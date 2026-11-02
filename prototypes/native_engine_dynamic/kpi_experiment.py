"""Native scalar-only contract gate and paired CPython timing experiment."""

import argparse
import copy
from datetime import datetime, UTC
import gc
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

from prototypes.native_engine.benchmark import file_hash
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read
from simulatte.collectors import ShopFloorKPIs
from .feedback import feed, validate_feed
from .native_kpis import NativeKPIStream, ProductionKPIStream
from .production_benchmark import REPO, ROOT, sources as prior_sources
from .scalar_benchmark import digest, libraries
from .simultaneous import quantized
from .validation import fingerprint, validate_result

FACTORIES = {"native": NativeKPIStream, "production": ProductionKPIStream}
PHASES = ("construction", "simulation", "scalar_export", "cleanup")
INPUT = REPO / "prototypes/native_engine/.inputs/jobs-50000-load-0.95.json"
SEED = 20261102


def sources():
    return {
        **prior_sources(),
        **{str((ROOT / name).relative_to(REPO)): file_hash(ROOT / name) for name in ("README.md", "CONTRACT.md")},
        **{str(p.relative_to(REPO)): file_hash(p) for p in sorted((ROOT / "tests").glob("*")) if p.is_file()},
        str(REPO.joinpath("specs/research/2026-11-02-native-kpi-plan.md").relative_to(REPO)): file_hash(
            REPO / "specs/research/2026-11-02-native-kpi-plan.md"
        ),
    }


def metadata():
    files = sources()
    return dict(
        created=datetime.now(UTC).isoformat(),
        files=files,
        source_sha256=digest(files),
        libraries=libraries(),
        input=str(INPUT.relative_to(REPO)),
        input_sha256=file_hash(INPUT),
        python=sys.version,
        interpreter=platform.python_implementation(),
        platform=platform.platform(),
        revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        outputs=[k.name for k in ShopFloorKPIs.kpis],
        scope="eight stock scalars only; no trace, history, EMA or custom observers",
        timing="sum of construction, feed+run, scalar snapshot, close+drop+gc; trajectory export/validation excluded",
    )


def evidence(jobs, decisions, result):
    return dict(
        trajectory=fingerprint(result),
        generated=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
        decisions=digest(
            [
                (probe, *(None if x is None else float(x) for x in (arrival, released, finished)), state, choice)
                for probe, arrival, released, finished, state, choice in decisions
            ]
        ),
        completed=result["completed"],
        operations=len(result["starts"]),
        records=result["records"],
        callbacks=result["callbacks"],
    )


class Tap:
    def __init__(self, stream):
        self.stream, self.snapshots = stream, []

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def advance(self, until):
        self.stream.advance(until)
        snapshot = self.stream.snapshot()
        assert snapshot == self.stream.snapshot()
        self.snapshots.append(snapshot)


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for seed, cap, policy, producer, warmup in itertools.product(
        range(12),
        (1, 2, 12),
        ("fifo", "spt"),
        ("independent", "feedback"),
        (0, 1.5, 1000),
    ):
        templates = quantized(seed)
        reference = None
        for backend in ("production", "native"):
            stream = FACTORIES[backend](templates.servers, wip_cap=cap, policy=policy, warmup=warmup)
            try:
                tapped = Tap(stream)
                jobs, decisions = feed(tapped, templates, producer)
                tapped.advance(stream.now + 0.125)
                stream.run()
                result, snapshot = stream.result(), stream.snapshot()
                validate_result(Workload(templates.servers, jobs), result, wip_cap=cap)
                if producer == "feedback":
                    validate_feed(templates, jobs, decisions, result)
                observed = dict(
                    **evidence(jobs, decisions, result), snapshot=snapshot, boundaries=digest(tapped.snapshots)
                )
                assert reference is None or observed == reference, (
                    seed,
                    cap,
                    policy,
                    producer,
                    warmup,
                    [k for k in observed if reference[k] != observed[k]],
                )
                reference = observed
            finally:
                stream.close()
        rows.append(dict(seed=seed, cap=cap, policy=policy, producer=producer, warmup=warmup, **reference))
    (output / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
    assert meta["files"] == sources() and meta["libraries"] == libraries()
    cert = dict(
        configurations=len(rows),
        model_runs=2 * len(rows),
        exact_scalars_and_boundaries=True,
        source_sha256=meta["source_sha256"],
        rows_sha256=file_hash(output / "rows.json"),
    )
    (output / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")
    print(json.dumps(cert), flush=True)


class Poll:
    """Read all eight current outputs without retaining an observation history."""

    def __init__(self, stream):
        self.stream, self.count = stream, 0

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def advance(self, until):
        self.stream.advance(until)
        self.stream.snapshot()
        self.count += 1


def sample(templates, backend, producer, warmup, polling):
    timings, cpu = {}, 0.0
    gc.collect()
    wall, process = time.perf_counter(), time.process_time()
    stream = FACTORIES[backend](templates.servers, wip_cap=12, policy="spt", warmup=warmup)
    timings["construction"] = time.perf_counter() - wall
    cpu += time.process_time() - process
    try:
        wall, process = time.perf_counter(), time.process_time()
        polled = Poll(stream) if polling == "boundary" else stream
        jobs, decisions = feed(polled, templates, producer)
        stream.run()
        timings["simulation"] = time.perf_counter() - wall
        cpu += time.process_time() - process
        wall, process = time.perf_counter(), time.process_time()
        snapshot = stream.snapshot()
        timings["scalar_export"] = time.perf_counter() - wall
        cpu += time.process_time() - process
        # Both adapters already maintain trajectory arrays. Copy/verify them
        # outside timing; the requested output is just the eight scalars.
        result = stream.result()
        validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
        if producer == "feedback":
            validate_feed(templates, jobs, decisions, result)
        observed = dict(
            **evidence(jobs, decisions, result),
            snapshot=snapshot,
            snapshot_calls=(polled.count if polling == "boundary" else 0) + 1,
        )
        assert len(snapshot["scalars"]) == 8
        assert observed["records"] == observed["callbacks"] == 0
        if backend == "native":
            assert result["kpis"] is None and not hasattr(stream, "env")
        else:
            assert stream.recorder is None and tuple(stream.env.collectors) == (stream.stock,)
    finally:
        wall, process = time.perf_counter(), time.process_time()
        stream.close()
        polled = stream = None
        gc.collect()
        timings["cleanup"] = time.perf_counter() - wall
        cpu += time.process_time() - process
    timings.update(total=sum(timings.values()), process_cpu=cpu)
    return timings, observed


def worker(args):
    templates = read(INPUT)
    templates = Workload(templates.servers, templates.jobs[: args.jobs])
    meta = metadata()
    samples, warmups, expected = [], [], None
    for i in range(args.warmups + args.repeats):
        timing, observed = sample(templates, args.backend, args.producer, args.warmup, args.polling)
        assert expected is None or expected == observed
        expected = observed
        (warmups if i < args.warmups else samples).append(timing)
    return dict(
        backend=args.backend,
        producer=args.producer,
        jobs=args.jobs,
        warmup=args.warmup,
        polling=args.polling,
        samples=samples,
        warmups=warmups,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        source_sha256=meta["source_sha256"],
        libraries=meta["libraries"],
        input_sha256=meta["input_sha256"],
        **expected,
    )


def cases():
    return [
        dict(backend=b, producer=p, jobs=n, warmup=w, polling=q)
        for b, p, n, w, q in itertools.product(
            FACTORIES,
            ("independent", "feedback"),
            (5000, 20000),
            (0, 500),
            ("final", "boundary"),
        )
    ]


def check_rows(rows):
    groups = {}
    trajectories, snapshots = {}, {}
    for row in rows:
        key = row["producer"], row["jobs"], row["warmup"], row["polling"]
        observed = {
            k: row[k]
            for k in (
                "trajectory",
                "generated",
                "decisions",
                "completed",
                "operations",
                "records",
                "callbacks",
                "snapshot",
                "snapshot_calls",
            )
        }
        assert groups.setdefault(key, observed) == observed, key
        physical = {k: observed[k] for k in observed if k not in ("snapshot", "snapshot_calls")}
        base = row["producer"], row["jobs"]
        assert trajectories.setdefault(base, physical) == physical, base
        scalar_key = (*base, row["warmup"])
        assert snapshots.setdefault(scalar_key, row["snapshot"]) == row["snapshot"], scalar_key


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    assert meta["interpreter"] == "CPython"
    order = cases()
    random.Random(SEED).shuffle(order)
    meta.update(seed=SEED, cases=order)
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        command = [sys.executable, "-m", "prototypes.native_engine_dynamic.kpi_experiment", "worker"]
        for key, value in case.items():
            command.extend(["--" + key, str(value)])
        row = json.loads(
            subprocess.check_output(command, cwd=REPO, text=True, env=dict(os.environ, PYTHONHASHSEED="0"))
        )
        rows.append(row)
        check_rows(rows)
        with (output / "matrix.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(
            f"{len(rows)}/{len(order)} {case} {statistics.median(t['total'] for t in row['samples']):.4f}s", flush=True
        )
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(sources())
    assert meta["libraries"] == libraries() and meta["input_sha256"] == file_hash(INPUT)
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["cases"] == order
    rows = [json.loads(line) for line in (output / "matrix.jsonl").read_text().splitlines()]
    assert len(rows) == len(order)
    for row, case in zip(rows, order):
        assert all(row[k] == v for k, v in case.items())
        assert all(row[k] == meta[k] for k in ("source_sha256", "libraries", "input_sha256"))
        assert len(row["samples"]) == 5 and len(row["warmups"]) == 1
        for timing in row["samples"] + row["warmups"]:
            assert all(math.isfinite(t) and t >= 0 for t in timing.values())
            assert abs(timing["total"] - sum(timing[k] for k in PHASES)) < 1e-9
        assert row["records"] == row["callbacks"] == 0 and row["completed"] == row["jobs"]
        assert len(row["snapshot"]["scalars"]) == 8 and row["peak_rss_bytes"] > 0
        assert row["snapshot_calls"] == (row["jobs"] + 1 if row["polling"] == "boundary" else 1)
    check_rows(rows)
    summary = []
    for row in rows:
        if row["backend"] != "native":
            continue
        baseline = next(
            r
            for r in rows
            if r["backend"] == "production" and all(r[k] == row[k] for k in ("producer", "jobs", "warmup", "polling"))
        )
        native = statistics.median(t["total"] for t in row["samples"])
        production = statistics.median(t["total"] for t in baseline["samples"])
        summary.append(
            dict(
                producer=row["producer"],
                jobs=row["jobs"],
                warmup=row["warmup"],
                polling=row["polling"],
                native_s=native,
                production_s=production,
                speedup=production / native,
            )
        )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    cert = dict(
        cases=len(rows),
        samples=5 * len(rows),
        exact_outputs=True,
        source_sha256=meta["source_sha256"],
        evidence={n: file_hash(output / n) for n in ("metadata.json", "matrix.jsonl", "summary.json")},
    )
    (output / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")
    print(json.dumps(cert), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    frozen, binaries = digest(sources()), libraries()
    meta = json.loads((output / "gate-cpython/metadata.json").read_text())
    cert = json.loads((output / "gate-cpython/verification.json").read_text())
    assert meta["files"] == sources() and meta["libraries"] == binaries and meta["source_sha256"] == frozen
    assert cert["source_sha256"] == frozen and cert["configurations"] == 432 and cert["model_runs"] == 864
    assert cert["rows_sha256"] == file_hash(output / "gate-cpython/rows.json")
    rows = json.loads((output / "gate-cpython/rows.json").read_text())
    expected = list(
        itertools.product(range(12), (1, 2, 12), ("fifo", "spt"), ("independent", "feedback"), (0, 1.5, 1000))
    )
    assert [(r["seed"], r["cap"], r["policy"], r["producer"], r["warmup"]) for r in rows] == expected
    assert all(r["records"] == r["callbacks"] == 0 and r["completed"] == 48 for r in rows)
    from .trace_fixture import generate
    from .observation_fixture import generate as generate_observed
    from .trace_benchmark import normalized_wire
    from contextlib import redirect_stdout
    import io
    import tempfile

    with tempfile.TemporaryDirectory(prefix="native-kpi-regression-") as tmp:
        with redirect_stdout(io.StringIO()):
            generate(Path(tmp), name="control")
            generate_observed(Path(tmp))
        for current, previous in (("control", "semantics"), ("observed", "observed")):
            assert (Path(tmp) / f"{current}.source").read_bytes() == (ROOT / f"fixtures/{previous}.source").read_bytes()
            assert normalized_wire(Path(tmp) / f"{current}.simtrace") == normalized_wire(
                ROOT / f"fixtures/{previous}.simtrace"
            )
    # Production code and previous evidence remain the committed control.
    prior = json.loads((ROOT / "interest-results/scaling/metadata.json").read_text())
    unchanged = {p: h for p, h in prior["files"].items() if p.startswith("src/")}
    unchanged.update(
        {str(p.relative_to(REPO)): file_hash(p) for p in (ROOT / "interest-results").rglob("*") if p.is_file()}
    )
    for path, value in unchanged.items():
        assert file_hash(REPO / path) == value
        import hashlib

        assert (
            hashlib.sha256(subprocess.check_output(["git", "show", f"4193642:{path}"], cwd=REPO)).hexdigest() == value
        )
    timings = [json.loads(line) for line in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("trajectory", "generated", "decisions", "snapshot", "snapshot_calls"):
        broken = copy.deepcopy(timings)
        broken[0][field] = "corrupt"
        try:
            check_rows(broken)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    result = dict(
        source_sha256=frozen,
        gate_configurations=432,
        gate_model_runs=864,
        timing_cases=len(timings),
        timed_samples=sum(len(r["samples"]) for r in timings),
        outputs_exact=True,
        recorded_fixtures_preserved=True,
        historical_files=len(unchanged),
        negative_checks_rejected=rejected,
        evidence={
            str(p.relative_to(output)): file_hash(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p != output / "verification.json"
        },
    )
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--backend", choices=FACTORIES, required=True)
    worker_parser.add_argument("--producer", choices=("independent", "feedback"), required=True)
    worker_parser.add_argument("--jobs", type=int, required=True)
    worker_parser.add_argument("--polling", choices=("final", "boundary"), default="final")
    worker_parser.add_argument("--warmup", type=float, default=0)
    worker_parser.add_argument("--warmups", type=int, default=1)
    worker_parser.add_argument("--repeats", type=int, default=5)
    for name in ("gate", "matrix", "audit", "audit-experiment"):
        sub.add_parser(name).add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    else:
        {"gate": gate, "matrix": matrix, "audit": audit, "audit-experiment": audit_experiment}[args.command](
            args.output
        )


if __name__ == "__main__":
    main()
