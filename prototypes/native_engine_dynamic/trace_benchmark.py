"""Full production trace gate, including the rich journal and projection bridge."""

from __future__ import annotations

import argparse
from datetime import datetime, UTC
import gc
import hashlib
import importlib.metadata
import itertools
import json
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
import zlib

from simulatte import Trace, Provenance
import simulatte.trace.writer as production_writer
from simulatte.trace.format import PREAMBLE, RECORD_HEADER, TRAILER, RecordType
from prototypes.native_engine import benchmark as static
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read

from .benchmark import REPO, ROOT, sources as prototype_sources
from .compiled import CompiledStream, extension
from .feedback import feed, validate_feed
from .reference import ReferenceStream
from .tuned import TunedStream
from .rich_recording import validate_records
from .scalar_benchmark import digest, libraries, PHASES
from .studio_trace import TraceStream, validate_trace
from .validation import fingerprint, validate_result

FACTORIES = {"compiled": CompiledStream, "reference": ReferenceStream, "tuned": TunedStream}


def sources():
    compatibility = []
    for package in ("trace", "playback", "scene"):
        compatibility.extend((REPO / "studio/packages" / package / "src").rglob("*.ts"))
    compatibility.extend((ROOT / "fixtures").glob("*"))
    compatibility.extend(
        (
            REPO / "studio/browser/native-prototype.spec.ts",
            REPO / "studio/packages/playback/test/native-prototype.test.ts",
        )
    )
    return {
        **prototype_sources(),
        **{str(p.relative_to(REPO)): static.file_hash(p) for p in sorted(compatibility)},
        **{str(p.relative_to(REPO)): static.file_hash(p) for p in sorted((REPO / "src/simulatte").rglob("*.py"))},
        **{
            str(p.relative_to(REPO)): static.file_hash(p)
            for p in (REPO / "uv.lock", REPO / "pyproject.toml", REPO / "studio/pnpm-lock.yaml")
        },
    }


def source_hash():
    return digest(sources())


def cases():
    return [
        dict(interpreter=i, producer=p, jobs=n, mode=m, backend=b)
        for i, p, n, m, b in itertools.product(
            ("CPython", "PyPy"), ("independent", "feedback"), (500, 5000), ("none", "raw", "full"), FACTORIES
        )
    ]


def case_id(row):
    return "-".join(str(row[k]) for k in ("interpreter", "producer", "jobs", "mode", "backend"))


def normalized_wire(path):
    """Check every CRC and hash deterministic wire records; metadata/offsets vary."""
    hashed, chunks = hashlib.sha256(), 0
    size = path.stat().st_size
    with path.open("rb") as stream:
        assert stream.read(PREAMBLE.size)[:8] == b"SIMTRACE"
        while stream.tell() < size - TRAILER.size:
            data = stream.read(RECORD_HEADER.size)
            assert len(data) == RECORD_HEADER.size
            length, kind, crc = RECORD_HEADER.unpack(data)
            assert length <= size - stream.tell() - TRAILER.size
            payload = stream.read(length)
            assert zlib.crc32(payload) == crc, "trace CRC"
            if kind not in (RecordType.HEADER, RecordType.FOOTER, RecordType.INDEX):
                hashed.update(bytes([kind]))
                hashed.update(length.to_bytes(8, "big"))
                hashed.update(payload)
            chunks += kind == RecordType.CHUNK
        assert stream.read(TRAILER.size)[8:] == b"SIMTEND\x00"
    return hashed.hexdigest(), chunks


def equivalent(rows):
    groups = {}
    for r in rows:
        g = groups.setdefault((r["producer"], r["jobs"], r["input_sha256"]), {})
        for key in (
            "trajectory_sha256",
            "generated_sha256",
            "decisions_sha256",
            "source_records_sha256",
            "trace_signature",
        ):
            if r[key] is not None:
                assert g.setdefault(key, r[key]) == r[key], (r["case"] if "case" in r else "probe", key)
        for key in ("operations", "completed", "events", "makespan"):
            value = r[key] if key == "operations" else r["counters"][key]
            assert g.setdefault(key, value) == value, key


def sample(cls, templates, args, raw, path, provenance):
    gc.collect()
    begin = time.perf_counter()
    stream = (
        TraceStream(
            cls,
            templates.servers,
            raw_path=raw,
            trace_path=path,
            policy="spt",
            wip_cap=12,
            provenance=provenance,
            parameters={"producer": args.producer, "template_prefix_jobs": args.jobs, "policy": "spt", "cap": 12},
        )
        if args.mode == "full"
        else cls(
            templates.servers,
            wip_cap=12,
            policy="spt",
            mode="studio" if args.mode == "raw" else "none",
            trace_path=raw if args.mode == "raw" else None,
        )
    )
    built = time.perf_counter()
    try:
        jobs, decisions = feed(stream, templates, args.producer)
        fed = time.perf_counter()
        stream.run()
        ran = time.perf_counter()
        result = stream.result()
        extracted = time.perf_counter()
    finally:
        stream.close()
    del stream
    gc.collect()
    closed = time.perf_counter()
    points = (begin, built, fed, ran, extracted, closed)
    timing = dict(zip(PHASES[:-1], (b - a for a, b in zip(points, points[1:]))))
    timing["total"] = closed - begin
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return result, jobs, decisions, timing, rss if sys.platform == "darwin" else rss * 1024


def worker(args):
    assert Path(production_writer.__file__).resolve() == REPO / "src/simulatte/trace/writer.py", "wrong source checkout"
    frozen, binaries, input_hash = source_hash(), libraries(), static.file_hash(args.input)
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    assert len(templates.jobs) == args.jobs
    if args.backend == "compiled":
        extension()
    cls = FACTORIES[args.backend]
    provenance = Provenance(
        model=digest({"contract": "native-trace-v1", "producer": args.producer}),
        source=frozen,
        inputs=input_hash,
        dependencies=static.file_hash(REPO / "uv.lock"),
    )
    (ROOT / ".runs").mkdir(exist_ok=True)
    raw, path = (ROOT / ".runs" / f"trace-{os.getpid()}{suffix}" for suffix in (".source", ".simtrace"))
    samples, warmups, sizes, wire_hashes = [], [], [], []
    count, expected, signature = 0 if args.probe else args.warmups, None, None
    try:
        for index in range(count + (1 if args.probe else args.repeats)):
            result, jobs, decisions, timing, rss = sample(cls, templates, args, raw, path, provenance)
            current = dict(
                trajectory_sha256=fingerprint(result),
                generated_sha256=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
                decisions_sha256=digest(decisions),
                source_records_sha256=static.file_hash(raw) if args.mode != "none" else None,
            )
            if args.mode == "full":
                wire, chunks = normalized_wire(path)
                opened = Trace.open(path)
                assert opened.outcome == "completed" and not opened.truncated and opened.manifest["complete"]
                current["stored_digest"] = opened.fingerprint.digest
                current["stored_kpis"] = opened.kpis()
                wire_hashes.append(wire)
                assert wire == wire_hashes[0], "encoded wire drift"
                del opened
            if expected is None:
                workload = Workload(templates.servers, jobs)
                validate_result(workload, result, wip_cap=12)
                if args.producer == "feedback":
                    validate_feed(templates, jobs, decisions, result)
                if args.mode != "none":
                    validate_records(raw, workload, result, cap=12, policy="spt")
                if args.mode == "full":
                    signature = validate_trace(path, workload, result, wip_cap=12)
                    assert signature["digest"] == current["stored_digest"] and signature["chunks"] == chunks
                expected = current
                del workload
            assert current == expected, "trajectory, decisions, raw records or stored trace drift"
            counters = {k: result[k] for k in ("completed", "events", "callbacks", "records", "makespan")}
            operations = sum(len(j.route) for j in jobs)
            (warmups if index < count else samples).append(timing)
            sizes.append(path.stat().st_size if args.mode == "full" else 0)
            del result, jobs, decisions
        assert source_hash() == frozen and libraries() == binaries, "worker provenance changed"
        assert static.file_hash(args.input) == input_hash, "worker input changed"
        return dict(
            study="studio-trace",
            interpreter=platform.python_implementation(),
            python=sys.version,
            producer=args.producer,
            backend=args.backend,
            jobs=args.jobs,
            operations=operations,
            mode=args.mode,
            cap=12,
            policy="spt",
            template_load=0.95,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=input_hash,
            **{
                k: expected[k]
                for k in ("trajectory_sha256", "generated_sha256", "decisions_sha256", "source_records_sha256")
            },
            trace_signature=signature,
            trace_wire_sha256=wire_hashes[0] if wire_hashes else None,
            trace_bytes=sizes[-1],
            trace_sizes=sizes,
            counters=counters,
            source_bytes=raw.stat().st_size if args.mode != "none" else 0,
            versions={
                **{name: importlib.metadata.version(name) for name in ("simulatte", "simpy", "msgpack")},
                "zlib": zlib.ZLIB_RUNTIME_VERSION,
            },
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        raw.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    path = REPO / "prototypes/native_engine/.inputs/jobs-5000-load-0.95.json"
    input_hash = static.file_hash(path)
    order = cases()
    random.Random(20261103).shuffle(order)
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        input_sha256=input_hash,
        input=str(path.relative_to(REPO)),
        seed=20261103,
        platform=platform.platform(),
        host=platform.node(),
        case_order=[case_id(c) for c in order],
        command=" ".join(sys.argv),
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        writer={
            "max_events": 10000,
            "max_bytes": 1 << 20,
            "max_pending_bytes": 64 << 20,
            "max_latency_s": "infinity",
            "compression": "production zlib",
            "fsync": False,
        },
        notes=(
            "Full close includes all writer shutdown. Journal I/O and projection clock are included; "
            "no model SimPy processes."
        ),
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        executable = sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute())
        command = [
            executable,
            "-m",
            "prototypes.native_engine_dynamic.trace_benchmark",
            "worker",
            "--input",
            str(path),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("producer", "jobs", "mode", "backend"):
            command.extend([f"--{key}", str(case[key])])
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, env=env, cwd=REPO, text=True))
        probe = json.loads(subprocess.check_output([*command, "--probe"], env=env, cwd=REPO, text=True))
        for current in (row, probe):
            assert all(current[k] == v for k, v in case.items())
            assert current["source_sha256"] == frozen and current["libraries"] == binaries, "case provenance"
            assert current["input_sha256"] == input_hash, "case input"
        equivalent([row, probe])
        assert row["counters"] == probe["counters"]
        assert row["trace_wire_sha256"] == probe["trace_wire_sha256"], "cold encoded trace drift"
        assert source_hash() == frozen and libraries() == binaries, "matrix provenance changed"
        assert static.file_hash(path) == input_hash
        row.update(case=case_id(case), peak_rss_bytes=probe["peak_rss_bytes"], cold_sample=probe["samples"][0])
        rows.append(row)
        equivalent(rows)
        with (args.output / "matrix.jsonl").open("a") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"{len(rows)}/72 {row['case']}: {statistics.median(s['total'] for s in row['samples']):.6f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--jobs", type=int, required=True)
    run.add_argument("--mode", choices=("none", "raw", "full"), required=True)
    run.add_argument("--backend", choices=FACTORIES, required=True)
    run.add_argument("--producer", choices=("independent", "feedback"), required=True)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--probe", action="store_true")
    run = sub.add_parser("matrix")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        if args.jobs < 1 or args.warmups < 0 or args.repeats < 1:
            parser.error("jobs/repeats must be positive, warmups nonnegative")
        print(json.dumps(worker(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
