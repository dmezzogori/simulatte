"""Equal stock-collector workloads: production, buffered views and live boundaries."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, UTC
import gc
import gzip
import importlib.util
import importlib.metadata
import io
from contextlib import redirect_stdout
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
import tempfile

from simulatte import Trace, Provenance
import simulatte.trace.writer as production_writer
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read
from prototypes.native_engine.benchmark import file_hash
from .compiled import CompiledStream, extension
from .feedback import feed, validate_feed
from .live_observation import LiveTraceStream, ProductionLiveStream, values
from .observation_gate import equivalent, canonical
from .production_benchmark import REPO, ROOT, PHASES, source_hash, sources
from .rich_recording import validate_records
from .scalar_benchmark import digest, libraries
from .trace_benchmark import normalized_wire
from .validation import fingerprint, validate_result

SEED = 20261102


def cases():
    return [
        dict(interpreter=i, producer=p, jobs=n, level=level, backend=b)
        for i, p, (n, level), b in itertools.product(
            ("CPython", "PyPy"),
            ("independent", "feedback"),
            ((5000, "kpi"), (5000, "full"), (20000, "full")),
            ("production", "buffered", "boundary"),
        )
    ]


def case_id(row):
    return "-".join(str(row[k]) for k in ("interpreter", "producer", "jobs", "level", "backend"))


def sample(templates, args, raw, path, provenance):
    gc.collect()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu = usage.ru_utime + usage.ru_stime
    begin = time.perf_counter()
    stream = (
        ProductionLiveStream(
            templates.servers,
            wip_cap=12,
            policy="spt",
            mode=args.level,
            trace_path=path if args.level != "none" else None,
            provenance=provenance,
        )
        if args.backend == "production"
        else LiveTraceStream(
            CompiledStream,
            templates.servers,
            wip_cap=12,
            policy="spt",
            level=args.level,
            trace_path=path if args.level != "none" else None,
            raw_path=raw,
            delivery=args.backend,
            provenance=provenance,
        )
    )
    built = time.perf_counter()
    try:
        jobs, decisions = feed(stream, templates, args.producer)
        fed = time.perf_counter()
        stream.run()
        ran = time.perf_counter()
        result, observed = stream.result(), values(stream)
        extracted = time.perf_counter()
    finally:
        stream.close()
    del stream
    gc.collect()
    end = time.perf_counter()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    points = (begin, built, fed, ran, extracted, end)
    timing = dict(zip(PHASES[:-1], (b - a for a, b in zip(points, points[1:]))))
    timing.update(total=end - begin, process_cpu=usage.ru_utime + usage.ru_stime - cpu)
    return result, observed, jobs, decisions, timing, usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def worker(args):
    assert Path(production_writer.__file__).resolve() == REPO / "src/simulatte/trace/writer.py"
    frozen, binaries = source_hash(), libraries()
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    assert len(templates.jobs) == args.jobs
    extension()  # Both paths exclude imports/DSO loading from measurement.
    provenance = Provenance(
        model="native-stock-observation-v1",
        source=frozen,
        inputs=file_hash(args.input),
        dependencies=file_hash(REPO / "uv.lock"),
    )
    raw, path = (ROOT / ".runs" / f"observation-{os.getpid()}{suffix}" for suffix in (".source", ".simtrace"))
    raw.parent.mkdir(exist_ok=True)
    warmups, samples, wires, sizes = [], [], [], []
    expected, signature = None, None
    warmup_count = 0 if args.probe else args.warmups
    try:
        for index in range(warmup_count + (1 if args.probe else args.repeats)):
            result, observed, jobs, decisions, timing, rss = sample(templates, args, raw, path, provenance)
            trace = Trace.open(path)
            wire, chunks = normalized_wire(path)
            current = dict(
                trajectory=fingerprint(result),
                generated=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
                decisions=digest(decisions),
                observations_sha256=digest(canonical(observed)),
                scalars=trace.kpis(),
                series_values_sha256=digest(
                    {k: [(cursor[0], value) for cursor, value in v] for k, v in trace.kpi_series().items()}
                ),
                trace_digest=trace.fingerprint.digest,
            )
            if expected is None:
                validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
                if args.producer == "feedback":
                    validate_feed(templates, jobs, decisions, result)
                trace.check()
                assert trace.verify() == (True if args.level == "full" else "not_verifiable")
                assert trace.outcome == "completed" and trace.manifest["complete"]
                equivalent(observed["scalars"], trace.kpis())
                assert observed["window"] == (0, result["makespan"], True)
                if args.backend != "production":
                    validate_records(raw, Workload(templates.servers, jobs), result, cap=12, policy="spt")
                if args.level == "full":
                    last = trace.state_at(trace.cursor_range[1])
                    assert not any(key.startswith("job-") for key in last)
                    assert last["shopfloor"]["jobs_in_system"] == 0 and last["psp"]["jobs"] == ()
                signature = dict(
                    chunks=chunks,
                    domain=sum(1 for _ in trace.events()) if args.level == "full" else 0,
                    observers=sum(1 for _ in trace.observer_events()) if args.level == "full" else 0,
                )
                payload = json.dumps(canonical(observed), separators=(",", ":")).encode()
                args.observations.parent.mkdir(parents=True, exist_ok=True)
                if args.observations.exists():
                    assert gzip.decompress(args.observations.read_bytes()) == payload, (
                        "observer export drift across delivery/level/probe"
                    )
                else:
                    args.observations.write_bytes(gzip.compress(payload, mtime=0))
                expected = current
            assert expected == current, "within-case observer or trajectory drift"
            wires.append(wire)
            sizes.append(path.stat().st_size)
            assert len(set(wires)) == len(set(sizes)) == 1
            counters = {k: result[k] for k in ("completed", "events", "callbacks", "records", "makespan")}
            operations = sum(len(j.route) for j in jobs)
            (warmups if index < warmup_count else samples).append(timing)
            del trace, observed, result, jobs, decisions
        assert source_hash() == frozen and libraries() == binaries
        return dict(
            interpreter=platform.python_implementation(),
            python=sys.version,
            versions={name: importlib.metadata.version(name) for name in ("simulatte", "simpy", "msgpack")},
            producer=args.producer,
            jobs=args.jobs,
            level=args.level,
            backend=args.backend,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=file_hash(args.input),
            **expected,
            observations_file=str(args.observations),
            observations_file_sha256=file_hash(args.observations),
            operations=operations,
            counters=counters,
            trace_signature=signature,
            trace_wire_sha256=wires[0],
            trace_bytes=sizes[0],
            source_bytes=raw.stat().st_size if raw.exists() else 0,
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        raw.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def check_groups(rows):
    groups, native = {}, {}
    for row in rows:
        key = row["producer"], row["jobs"]
        group = groups.setdefault(key, row)
        for field in ("trajectory", "generated", "decisions", "series_values_sha256", "operations"):
            assert row[field] == group[field], field
        equivalent(row["scalars"], group["scalars"])
        if row["backend"] != "production":
            key = row["interpreter"], row["producer"], row["jobs"], row["level"]
            prior = native.setdefault(key, row)
            for field in (
                "observations_sha256",
                "trace_digest",
                "trace_signature",
                "trace_wire_sha256",
                "trace_bytes",
                "source_bytes",
                "counters",
            ):
                assert row[field] == prior[field], field


def audit(directory):
    meta = json.loads((directory / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == source_hash() and meta["libraries"] == libraries()
    assert file_hash(REPO / meta["input"]) == meta["input_sha256"]
    assert meta["seed"] == SEED
    for path, value in meta["tests"].items():
        assert file_hash(REPO / path) == value
    current_c = (ROOT / "csrc/dynamic_engine.c").read_text()
    prior_c = subprocess.check_output(
        ["git", "show", "c69bcbe:prototypes/native_engine_dynamic/csrc/dynamic_engine.c"], cwd=REPO, text=True
    )
    start, end = current_c.index("int nd_flush(nd_engine *e)"), current_c.index("int nd_run(nd_engine *e)")
    assert current_c[:start] + current_c[end:] == prior_c, "calendar implementation changed"
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == [case_id(case) for case in order]
    rows = [json.loads(line) for line in (directory / "matrix.jsonl").read_text().splitlines()]
    assert [row["case"] for row in rows] == meta["case_order"]
    exports, group_values = {}, {}
    for row, case in zip(rows, order):
        assert all(row[k] == v for k, v in case.items())
        for field in ("source_sha256", "libraries", "input_sha256"):
            assert row[field] == meta[field]
        assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
        for timing in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert all(math.isfinite(timing[key]) and timing[key] >= 0 for key in (*PHASES, "process_cpu"))
            assert abs(sum(timing[k] for k in PHASES[:-1]) - timing["total"]) < 1e-8
        for key in row["cold_evidence"]:
            assert row[key] == row["cold_evidence"][key], key
        assert row["peak_rss_bytes"] > 0 and row["counters"]["completed"] == row["jobs"]
        path = REPO / row["observations_file"]
        assert file_hash(path) == row["observations_file_sha256"]
        if str(path) not in exports:
            exports[str(path)] = json.loads(gzip.decompress(path.read_bytes()))
        observed = exports[str(path)]
        assert digest(observed) == row["observations_sha256"]
        equivalent(observed["scalars"], row["scalars"])
        key = row["producer"], row["jobs"]
        equivalent(group_values.setdefault(key, observed), observed)
        if row["backend"] == "production":
            assert row["source_bytes"] == 0
        else:
            assert row["source_bytes"] == 8 + 32 * (4 * row["jobs"] + 5 * row["operations"])
        if row["level"] == "full":
            assert row["trace_signature"]["domain"] == 7 * (row["jobs"] + row["operations"])
            assert row["trace_signature"]["observers"] == 4 * row["jobs"] + (0 if row["backend"] == "production" else 2)
    check_groups(rows)
    return meta, rows


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    path = REPO / "prototypes/native_engine/.inputs/jobs-50000-load-0.95.json"
    order = cases()
    random.Random(SEED).shuffle(order)
    frozen, binaries = source_hash(), libraries()
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        input=str(path.relative_to(REPO)),
        input_sha256=file_hash(path),
        seed=SEED,
        platform=platform.platform(),
        case_order=[case_id(case) for case in order],
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        tests={
            str(p.relative_to(REPO)): file_hash(p)
            for p in (ROOT / "tests/test_live_observation.py", ROOT / "tests/native_sanitizer.c")
        },
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    env, rows = dict(os.environ, PYTHONHASHSEED="0"), []
    for case in order:
        name = "production" if case["backend"] == "production" else "native"
        observed = (
            args.output / "observations" / f"{case['interpreter']}-{case['producer']}-{case['jobs']}-{name}.json.gz"
        )
        command = [
            sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute()),
            "-m",
            "prototypes.native_engine_dynamic.observation_benchmark",
            "worker",
            "--input",
            str(path),
            "--observations",
            str(observed),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("producer", "jobs", "level", "backend"):
            command.extend([f"--{key}", str(case[key])])
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        cold = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
        keys = [k for k in row if k not in ("samples", "warmups", "peak_rss_bytes")]
        assert all(row[k] == cold[k] for k in keys)
        assert row["source_sha256"] == frozen and row["libraries"] == binaries and source_hash() == frozen
        assert all(row[key] == value for key, value in case.items()) and row["input_sha256"] == meta["input_sha256"]
        row.update(
            case=case_id(case),
            cold_sample=cold["samples"][0],
            peak_rss_bytes=cold["peak_rss_bytes"],
            cold_evidence={k: cold[k] for k in keys},
        )
        rows.append(row)
        check_groups(rows)
        with (args.output / "matrix.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(f"{len(rows)}/36 {row['case']} {statistics.median(s['total'] for s in row['samples']):.4f}s", flush=True)
    meta, rows = audit(args.output)
    summary = dict(
        source_sha256=frozen,
        cases=len(rows),
        samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        stock_series_and_scalars_equivalent=True,
        native_delivery_output_exact=True,
        evidence={name: file_hash(args.output / name) for name in ("metadata.json", "matrix.jsonl")},
    )
    (args.output / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


def audit_experiment(directory):
    from .semantics_gate import workloads

    frozen, binaries = source_hash(), libraries()
    gates = []
    expected_cases = list(
        itertools.product(
            [*workloads(), *[f"wide-{i}" for i in range(8)]],
            (1, 2, 12),
            ("fifo", "spt"),
            ("independent", "feedback"),
            ("kpi", "full"),
            (0, 2),
            (None, 4),
        )
    )
    for name, interpreter in (("cpython", "CPython"), ("pypy", "PyPy")):
        root = directory / f"gate-{name}"
        meta = json.loads((root / "metadata.json").read_text())
        verified = json.loads((root / "verification.json").read_text())
        assert (
            meta["source_sha256"] == frozen
            and meta["files"] == sources()
            and meta["libraries"] == binaries
            and meta["interpreter"] == interpreter
        )
        for path, value in verified["evidence"].items():
            assert file_hash(root / path) == value
        rows = [json.loads(line) for line in (root / "rows.jsonl").read_text().splitlines()]
        assert [
            (r["case"], r["cap"], r["policy"], r["producer"], r["level"], r["warmup"], r["horizon"]) for r in rows
        ] == expected_cases
        assert verified["configurations"] == len(rows) == 1536 and verified["model_runs"] == 3072
        for row in rows:
            assert row["final_sha256"][0] == row["final_sha256"][1]
            assert row["probe_sha256"][0] == row["probe_sha256"][1]
        gates.append(rows)
    assert gates[0] == gates[1]
    meta, rows = audit(directory / "scaling")
    # Verify all preceding measured sources and retained evidence from Git.
    baseline = ROOT / "semantics-results"
    previous = json.loads((baseline / "scaling/metadata.json").read_text())
    historical = {**previous["files"], **previous["gate_tests"]}
    tests = json.loads((baseline / "sweep-cpython/metadata.json").read_text())["tests"]
    historical.update(tests)
    historical.update({str(p.relative_to(REPO)): file_hash(p) for p in baseline.rglob("*") if p.is_file()})
    spec = importlib.util.spec_from_file_location(
        "production_analysis", REPO / "specs/research/native-engine-data/production_analysis.py"
    )
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    analysis.verify_committed_files(historical, "c69bcbe")
    # A regenerated default trace has exactly the old signature, wire and
    # source bytes. The opt-in views do not replace the buffered default.
    from .trace_fixture import generate

    with tempfile.TemporaryDirectory(prefix="native-default-regression-") as tmp:
        with redirect_stdout(io.StringIO()):
            generate(Path(tmp), name="control")
        expected = json.loads((ROOT / "fixtures/semantics.expected.json").read_text())
        actual = json.loads((Path(tmp) / "control.expected.json").read_text())
        assert actual["signature"] == expected["signature"]
        assert (Path(tmp) / "control.source").read_bytes() == (ROOT / "fixtures/semantics.source").read_bytes()
        assert normalized_wire(Path(tmp) / "control.simtrace") == normalized_wire(ROOT / "fixtures/semantics.simtrace")
    rejected = []
    for field in ("trajectory", "scalars", "trace_wire_sha256", "observations_sha256"):
        selected = [
            copy.deepcopy(r)
            for r in rows
            if r["jobs"] == 5000
            and r["interpreter"] == "CPython"
            and r["producer"] == "independent"
            and r["level"] == "full"
        ]
        next(r for r in selected if r["backend"] == "boundary")[field] = "corrupt"
        try:
            check_groups(selected)
        except (AssertionError, AttributeError):
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    summary = []
    for row in sorted(rows, key=case_id):
        times = [s["total"] for s in row["samples"]]
        summary.append(
            dict(
                case=row["case"],
                median_s=statistics.median(times),
                min_s=min(times),
                max_s=max(times),
                cold_s=row["cold_sample"]["total"],
                rss_mib=row["peak_rss_bytes"] / 2**20,
                trace_bytes=row["trace_bytes"],
                observations_bytes=(REPO / row["observations_file"]).stat().st_size,
            )
        )
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    result = dict(
        source_sha256=frozen,
        gate_cases_per_interpreter=1536,
        gate_model_runs_per_interpreter=3072,
        cross_interpreter_gate_exact=True,
        stock_series_and_scalars_equivalent=True,
        native_delivery_trace_exact=True,
        timing_cases=len(rows),
        timed_samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        default_buffered_trace_regression=True,
        scheduler_source_unchanged=True,
        historical_revision="c69bcbe",
        historical_verified_files=len(historical),
        negative_checks_rejected=rejected,
        evidence={
            str(p.relative_to(directory)): file_hash(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p != directory / "verification.json"
        },
    )
    (directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--input", type=Path, required=True)
    worker_parser.add_argument("--observations", type=Path, required=True)
    worker_parser.add_argument("--jobs", type=int, default=5000)
    worker_parser.add_argument("--producer", choices=("independent", "feedback"), required=True)
    worker_parser.add_argument("--level", choices=("kpi", "full"), required=True)
    worker_parser.add_argument("--backend", choices=("production", "buffered", "boundary"), required=True)
    worker_parser.add_argument("--warmups", type=int, default=1)
    worker_parser.add_argument("--repeats", type=int, default=5)
    worker_parser.add_argument("--probe", action="store_true")
    matrix_parser = sub.add_parser("matrix")
    matrix_parser.add_argument("--output", type=Path, required=True)
    matrix_parser.add_argument("--pypy", type=Path, required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--output", type=Path, required=True)
    experiment_parser = sub.add_parser("audit-experiment")
    experiment_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    elif args.command == "matrix":
        matrix(args)
    elif args.command == "audit-experiment":
        audit_experiment(args.output)
    else:
        _, rows = audit(args.output)
        print(json.dumps(dict(cases=len(rows), samples=sum(len(r["samples"]) for r in rows))))


if __name__ == "__main__":
    main()
