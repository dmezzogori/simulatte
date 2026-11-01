"""Fresh scalar ctypes/C API/compact controls with independent or feedback jobs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, UTC
import gc
import hashlib
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

from prototypes.native_engine import benchmark as static
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read

from .benchmark import REPO, ROOT, binary_hashes, factory, source_hash, sources
from .build import BUILD
from .feedback import feed, validate_feed
from .validation import fingerprint, validate_recording, validate_result

PHASES = ("build", "feed", "run", "extract", "close", "total")


def libraries():
    extensions = {p.name: static.file_hash(p) for p in sorted(BUILD.glob("_nd_scalar.*.so"))}
    assert len(extensions) == 2, "build CPython and PyPy extensions first"
    return {**binary_hashes(), **extensions}


def cases():
    return [
        dict(interpreter=interpreter, producer=producer, jobs=jobs, mode=mode, backend=backend)
        for interpreter, producer, jobs, mode, backend in itertools.product(
            ("CPython", "PyPy"),
            ("independent", "feedback"),
            (5000, 50000),
            ("none", "record"),
            ("native", "compiled", "reference", "tuned"),
        )
    ]


def case_id(case):
    return "-".join(str(case[k]) for k in ("interpreter", "producer", "jobs", "mode", "backend"))


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def equivalent(rows):
    seen = {}
    for row in rows:
        key = row["producer"], row["input_sha256"], row["cap"]
        group = seen.setdefault(key, {})
        for field in ("trajectory_sha256", "recording_sha256", "generated_sha256", "decisions_sha256"):
            if row[field] is not None:
                assert group.setdefault(field, row[field]) == row[field], (key, field)
        for field in ("operations", "observations", "choices", "states", "counters"):
            # Observation modes differ only in recording counters.
            value = {k: v for k, v in row[field].items() if k != "records"} if field == "counters" else row[field]
            assert group.setdefault(field, value) == value, (key, field)


def sample(cls, templates, args, path):
    gc.collect()
    begin = time.perf_counter()
    stream = cls(
        templates.servers, wip_cap=12, policy="spt", mode=args.mode, trace_path=path if args.mode == "record" else None
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
    frozen, binaries, input_hash = source_hash(), libraries(), static.file_hash(args.input)
    templates = read(args.input)
    if args.backend == "compiled":
        from .compiled import CompiledStream
        from .compiled import extension

        extension()  # Imports stay outside lifecycle timing, just as ctypes CDLL loading does.
        cls = CompiledStream
    else:
        cls = factory("dynamic", args.backend)
        if args.backend == "native":
            from .native import _load_library

            _load_library()
    (ROOT / ".runs").mkdir(exist_ok=True)
    path = ROOT / ".runs" / f"scalar-{os.getpid()}.bin"
    samples, warmups, expected = [], [], None
    count = 0 if args.probe else args.warmups
    try:
        for index in range(count + (1 if args.probe else args.repeats)):
            result, jobs, decisions, timing, rss = sample(cls, templates, args, path)
            current = dict(
                trajectory_sha256=fingerprint(result),
                generated_sha256=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
                decisions_sha256=digest(decisions),
                recording_sha256=static.file_hash(path) if args.mode == "record" else None,
            )
            if expected is None:
                data = Workload(templates.servers, jobs)
                validate_result(data, result, wip_cap=12)
                if args.mode == "record":
                    validate_recording(data, result, path, wip_cap=12)
                if args.producer == "feedback":
                    validate_feed(templates, jobs, decisions, result)
                    assert jobs != templates.jobs
                    assert len({r[4] for r in decisions[1:]}) >= 2
                expected = current
                del data
            assert current == expected, "trajectory, workload, observation or record drift"
            counters = {k: result[k] for k in ("completed", "events", "callbacks", "records", "makespan")}
            operations = sum(len(j.route) for j in jobs)
            choices = dict(Counter(r[5] for r in decisions))
            states = dict(Counter(r[4] for r in decisions[1:]))
            (warmups if index < count else samples).append(timing)
            del result, jobs, decisions
        assert source_hash() == frozen and libraries() == binaries, "worker provenance changed"
        assert static.file_hash(args.input) == input_hash, "worker input changed"
        return dict(
            study="scalar",
            interpreter=platform.python_implementation(),
            python=sys.version,
            backend=args.backend,
            producer=args.producer,
            jobs=len(templates.jobs),
            operations=operations,
            mode=args.mode,
            cap=12,
            policy="spt",
            load=0.95,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=input_hash,
            **expected,
            counters=counters,
            choices=choices,
            states=states,
            observations=len(templates.jobs) - 1 if args.producer == "feedback" else 0,
            recording_bytes=path.stat().st_size if args.mode == "record" else 0,
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        path.unlink(missing_ok=True)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    paths = {n: REPO / f"prototypes/native_engine/.inputs/jobs-{n}-load-0.95.json" for n in (5000, 50000)}
    inputs = {str(n): static.file_hash(path) for n, path in paths.items()}
    order = cases()
    random.Random(20261102).shuffle(order)
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        inputs=inputs,
        platform=platform.platform(),
        host=platform.node(),
        seed=20261102,
        compiler=subprocess.check_output(["cc", "--version"], text=True),
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        case_order=[case_id(case) for case in order],
        command=" ".join(sys.argv),
        notes="Serial scalar per-job schedule, exact shared engine DSO, imports/DSO load outside timing.",
    )
    if sys.platform == "darwin":
        meta["linkage"] = {
            p.name: subprocess.check_output(["otool", "-L", str(p)], text=True) for p in BUILD.glob("_nd_scalar.*.so")
        }
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        executable = sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute())
        command = [
            executable,
            "-m",
            "prototypes.native_engine_dynamic.scalar_benchmark",
            "worker",
            "--input",
            str(paths[case["jobs"]]),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for k in ("backend", "producer", "mode"):
            command.extend([f"--{k}", case[k]])
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        probe = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
        for current in (row, probe):
            assert all(current[k] == v for k, v in case.items()), "case identity"
            assert current["source_sha256"] == frozen and current["libraries"] == binaries, "case provenance"
            assert current["input_sha256"] == inputs[str(case["jobs"])], "case input"
        equivalent([row, probe])
        assert row["counters"] == probe["counters"]
        assert source_hash() == frozen and libraries() == binaries, "matrix provenance changed"
        assert static.file_hash(paths[case["jobs"]]) == inputs[str(case["jobs"])], "matrix input changed"
        row.update(case=case_id(case), peak_rss_bytes=probe["peak_rss_bytes"], cold_sample=probe["samples"][0])
        rows.append(row)
        equivalent(rows)
        with (args.output / "matrix.jsonl").open("a") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"{len(rows)}/64 {row['case']}: {statistics.median(s['total'] for s in row['samples']):.6f}s", flush=True)
    assert source_hash() == frozen and libraries() == binaries, "final provenance changed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--backend", choices=("native", "compiled", "reference", "tuned"), required=True)
    run.add_argument("--producer", choices=("independent", "feedback"), required=True)
    run.add_argument("--mode", choices=("none", "record"), required=True)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--probe", action="store_true")
    run = sub.add_parser("matrix")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        if args.warmups < 0 or args.repeats < 1:
            parser.error("warmups must be nonnegative and repeats positive")
        print(json.dumps(worker(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
