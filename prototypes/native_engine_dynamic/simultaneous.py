"""Deterministic, quantized differential sweep against unmodified production."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path
import platform
import random
import statistics

from prototypes.native_engine.model import Job, Workload
from .compiled import CompiledStream
from .feedback import feed, validate_feed
from .native import NativeStream
from .production import ProductionStream
from .production_benchmark import REPO, ROOT, source_hash, sources
from .reference import ReferenceStream
from .scalar_benchmark import digest, libraries
from .tuned import TunedStream
from .validation import fingerprint, validate_result

FACTORIES = (ReferenceStream, TunedStream, NativeStream, CompiledStream)


def quantized(seed, jobs=48):
    rng = random.Random(20261102 + seed)
    servers, at, rows = rng.choice((1, 2, 3, 5, 8)), 0.0, []
    for _ in range(jobs):
        at += 0 if seed % 4 == 0 else rng.choice((0, 0, 0.25, 0.5, 1))
        route = tuple(rng.sample(range(servers), rng.randint(1, servers)))
        durations = tuple(1 if seed % 3 == 0 else rng.choice((0.25, 0.5, 1, 1, 2)) for _ in route)
        due = 10 if seed % 4 == 0 else at + rng.choice((0, 2, 10, 10, 20))
        rows.append(Job(at, route, durations, due))
    return Workload(servers, tuple(rows))


def execute(cls, templates, *, cap, policy, producer):
    stream = cls(templates.servers, wip_cap=cap, policy=policy)
    try:
        jobs, decisions = feed(stream, templates, producer)
        stream.run()
        result = stream.result()
        validate_result(Workload(templates.servers, jobs), result, wip_cap=cap)
        if producer == "feedback":
            validate_feed(templates, jobs, decisions, result)
        return fingerprint(result), jobs, decisions, result
    finally:
        stream.close()


def check(templates, *, cap, policy, producer):
    # Production ConWIP requires an integer. A cap of N cannot bind this N-job
    # run, so it is also an oracle for the native uncapped path.
    expected = execute(
        ProductionStream, templates, cap=cap or max(1, len(templates.jobs)), policy=policy, producer=producer
    )
    counters, same = {}, None
    for cls in FACTORIES:
        actual = execute(cls, templates, cap=cap, policy=policy, producer=producer)
        assert actual[:3] == expected[:3], (cls.__name__, cap, policy, producer)
        assert same is None or actual[3] == same, "compact/C model result mismatch"
        same = actual[3]
        counters[cls.__name__] = actual[3]["events"]
    return dict(
        trajectory=expected[0],
        generated=digest([(j.arrival, j.route, j.durations, j.due) for j in expected[1]]),
        decisions=digest(
            [
                (
                    probe,
                    *(None if value is None else float(value) for value in (arrival, released, finished)),
                    state,
                    choice,
                )
                for probe, arrival, released, finished, state, choice in expected[2]
            ]
        ),
        operations=len(expected[3]["starts"]),
        events=counters,
        completed=expected[3]["completed"],
        makespan=expected[3]["makespan"],
    )


def sweep(output, seeds):
    output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    from prototypes.native_engine.benchmark import file_hash

    metadata = dict(
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        interpreter=platform.python_implementation(),
        seeds=seeds,
        jobs=48,
        caps=[None, 1, 2, 4, 12],
        policies=["fifo", "spt"],
        producers=["independent", "feedback"],
        tests={
            str(p.relative_to(REPO)): file_hash(p)
            for p in (
                ROOT / "tests/test_simultaneous.py",
                ROOT / "tests/test_semantics_gate.py",
                ROOT / "tests/native_sanitizer.c",
            )
        },
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    count = 0
    with (output / "rows.jsonl").open("w") as target:
        for seed, cap, policy, producer in itertools.product(
            range(seeds), metadata["caps"], metadata["policies"], metadata["producers"]
        ):
            templates = quantized(seed)
            row = check(templates, cap=cap, policy=policy, producer=producer)
            target.write(
                json.dumps(dict(seed=seed, servers=templates.servers, cap=cap, policy=policy, producer=producer, **row))
                + "\n"
            )
            count += 1
    assert source_hash() == frozen and libraries() == binaries
    result = dict(
        source_sha256=frozen,
        configurations=count,
        model_runs=count * 5,
        mismatches=0,
        files={name: file_hash(output / name) for name in ("metadata.json", "rows.jsonl")},
    )
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def certify_targeted(data):
    """Require trajectory/dispatch/series parity; isolate the known export bug."""
    from .semantics_gate import workloads, differences

    expected = list(
        itertools.product(
            workloads(), (1, 2, 12), ("fifo", "spt"), ("independent", "feedback"), ("none", "kpi", "full")
        )
    )
    assert [(r["case"], r["cap"], r["policy"], r["producer"], r["mode"]) for r in data["rows"]] == expected
    artifacts = 0
    for row in data["rows"]:
        actual, production = row["results"]["direct"], row["results"]["production"]
        assert actual == row["results"]["native"]
        changed = differences(production, actual)
        assert changed == row["differences"]
        if changed:
            assert changed == ["kpi:max_floor_wip"]
            assert (row["case"], row["cap"], row["producer"]) == ("boundary", 12, "independent")
            assert row["mode"] in ("kpi", "full")
            assert production["kpis"]["max_floor_wip"] == 2 and actual["kpis"]["max_floor_wip"] == 3
            artifacts += 1
        if row["mode"] != "none":
            assert actual["series_peaks"] == production["series_peaks"]
            assert actual["kpis"]["max_floor_wip"] == production["series_peaks"]["shopfloor/floor_wip"]
    assert artifacts == 4
    assert data["summary"]["changed_times"] == 0
    assert data["summary"]["difference_counts"] == {"kpi:max_floor_wip": 4}
    return dict(comparisons=len(expected), scheduling_mismatches=0, known_export_artifacts=artifacts)


def audit_experiment(directory):
    """Audit the new evidence and verify the previous measured checkout."""
    from prototypes.native_engine.benchmark import file_hash
    from .scaling_benchmark import audit

    frozen, binaries = source_hash(), libraries()
    targeted = [json.loads((directory / f"targeted-{name}.json").read_text()) for name in ("cpython", "pypy")]
    certificates = [certify_targeted(data) for data in targeted]
    for data in targeted:
        assert data["source_sha256"] == frozen and data["libraries"] == binaries
    for key in ("rows", "boundaries", "event_observers", "summary", "reduced_time_10_events"):
        assert targeted[0][key] == targeted[1][key]
    sweeps = []
    for name, interpreter in (("cpython", "CPython"), ("pypy", "PyPy")):
        root = directory / f"sweep-{name}"
        meta = json.loads((root / "metadata.json").read_text())
        verification = json.loads((root / "verification.json").read_text())
        assert meta["source_sha256"] == frozen and meta["libraries"] == binaries and meta["files"] == sources()
        assert meta["interpreter"] == interpreter and meta["seeds"] == 128 and meta["jobs"] == 48
        assert (
            meta["caps"] == [None, 1, 2, 4, 12]
            and meta["policies"] == ["fifo", "spt"]
            and meta["producers"] == ["independent", "feedback"]
        )
        for path, value in meta["tests"].items():
            assert file_hash(REPO / path) == value
        for path, value in verification["files"].items():
            assert file_hash(root / path) == value
        rows = [json.loads(line) for line in (root / "rows.jsonl").read_text().splitlines()]
        expected = list(itertools.product(range(128), meta["caps"], meta["policies"], meta["producers"]))
        assert [(r["seed"], r["cap"], r["policy"], r["producer"]) for r in rows] == expected
        assert verification["configurations"] == len(rows) == 2560 and verification["model_runs"] == 12800
        assert verification["mismatches"] == 0 and verification["source_sha256"] == frozen
        for row in rows:
            assert row["completed"] == 48 and row["operations"] >= 48
            assert set(row["events"]) == {cls.__name__ for cls in FACTORIES}
            assert len(set(row["events"].values())) == 1
        sweeps.append(rows)
    assert sweeps[0] == sweeps[1]
    meta, rows, parity = audit(directory / "scaling")
    baseline = ROOT / "gate-results/scaling"
    old_meta, old_rows, _ = audit(baseline, current=False)
    # The existing verifier reads historical Git blobs, not mutable worktree
    # files. Keep all preceding raw evidence untouched.
    spec = importlib.util.spec_from_file_location(
        "production_analysis", REPO / "specs/research/native-engine-data/production_analysis.py"
    )
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    historical = {**old_meta["files"], **old_meta["gate_tests"]}
    historical.update(
        {str(p.relative_to(REPO)): file_hash(p) for p in (ROOT / "gate-results").rglob("*") if p.is_file()}
    )
    analysis.verify_committed_files(historical, "31bf33e")
    assert meta["input_sha256"] == old_meta["input_sha256"] and meta["writer"] == old_meta["writer"]
    assert binaries.keys() == old_meta["libraries"].keys()
    changed_libraries = [key for key in binaries if binaries[key] != old_meta["libraries"][key]]
    assert len(changed_libraries) == 1 and "dynamic" in changed_libraries[0]
    old_by_case = {row["case"]: row for row in old_rows}
    comparisons = []
    for row in rows:
        old = old_by_case[row["case"]]
        for key in (
            "trajectory_sha256",
            "generated_sha256",
            "decisions_sha256",
            "kpis",
            "series_values_sha256",
            "operations",
            "versions",
            "python",
        ):
            assert row[key] == old[key], (row["case"], key)
        for key in ("completed", "callbacks", "records", "makespan"):
            assert row["counters"][key] == old["counters"][key]
        med = statistics.median(sample["total"] for sample in row["samples"])
        old_med = statistics.median(sample["total"] for sample in old["samples"])
        comparisons.append(
            dict(
                case=row["case"],
                previous_s=old_med,
                current_s=med,
                current_over_previous=med / old_med,
                previous_events=old["counters"]["events"],
                current_events=row["counters"]["events"],
                changed_record_fields=[
                    key
                    for key in ("trace_signature", "trace_wire_sha256", "source_bytes", "trace_bytes")
                    if row[key] != old[key]
                ],
            )
        )
    (directory / "comparison.json").write_text(json.dumps(comparisons, indent=2) + "\n")
    result = dict(
        source_sha256=frozen,
        targeted=certificates,
        sweep_configurations_per_interpreter=2560,
        sweep_model_runs_per_interpreter=12800,
        cross_interpreter_sweep_exact=True,
        scaling_cases=len(rows),
        scaling_samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        aligned_parity_probes=len(parity),
        historical_revision="31bf33e",
        historical_verified_files=len(historical),
        changed_libraries=changed_libraries,
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=128)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("seeds must be positive")
    audit_experiment(args.output) if args.audit else sweep(args.output, args.seeds)


if __name__ == "__main__":
    main()
