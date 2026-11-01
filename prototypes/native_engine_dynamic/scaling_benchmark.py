"""Frozen-runtime scaling gate, reusing the production timing worker unchanged."""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, UTC
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys

from prototypes.native_engine import benchmark as static
from .production_benchmark import REPO, ROOT, PHASES, case_id, equivalent, source_hash, sources
from .scalar_benchmark import libraries

SEED = 20261107
EVIDENCE_KEYS = (
    "source_sha256",
    "input_sha256",
    "libraries",
    "versions",
    "interpreter",
    "python",
    "producer",
    "jobs",
    "mode",
    "backend",
    "trajectory_sha256",
    "generated_sha256",
    "decisions_sha256",
    "operations",
    "kpis",
    "series_values_sha256",
    "trace_signature",
    "trace_wire_sha256",
    "counters",
    "trace_bytes",
    "source_bytes",
)


def cases():
    return [
        dict(interpreter=i, producer=p, jobs=n, mode=m, backend=b)
        for i, p, n, m, b in itertools.product(
            ("CPython", "PyPy"),
            ("independent", "feedback"),
            (5000, 20000),
            ("none", "kpi", "full"),
            ("production", "direct"),
        )
    ]


def assert_row(row, meta):
    assert row["source_sha256"] == meta["source_sha256"]
    assert row["input_sha256"] == meta["input_sha256"] and row["libraries"] == meta["libraries"]
    assert row["case"] == case_id(row)
    assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
    assert len(row["trace_sizes"]) == len(row["samples"]) + len(row["warmups"])
    assert all(size == row["trace_bytes"] for size in row["trace_sizes"])
    assert row["peak_rss_bytes"] > 0
    for key in EVIDENCE_KEYS:
        assert row[key] == row["cold_evidence"][key], f"cold {key}"
    assert row["counters"]["completed"] == row["jobs"] and row["counters"]["callbacks"] == 0
    for timing in [*row["samples"], *row["warmups"], row["cold_sample"]]:
        assert all(math.isfinite(timing[k]) and timing[k] >= 0 for k in (*PHASES, "process_cpu"))
        assert timing["process_cpu"] > 0
        assert math.isclose(sum(timing[k] for k in PHASES[:-1]), timing["total"], rel_tol=1e-10, abs_tol=1e-9)
    recorded = row["mode"] != "none"
    assert (row["trace_signature"] is not None) == recorded
    assert (row["trace_wire_sha256"] is not None) == recorded
    assert (row["trace_bytes"] > 0) == recorded
    expected_source = (
        8 + 32 * (4 * row["jobs"] + 5 * row["operations"]) if recorded and row["backend"] == "direct" else 0
    )
    assert row["source_bytes"] == expected_source
    if row["mode"] == "full":
        assert row["trace_signature"]["domain_events"] == 7 * (row["jobs"] + row["operations"])
        assert row["trace_signature"]["observer_events"] == 4 * row["jobs"] + (2 if row["backend"] == "direct" else 0)


def audit(directory, *, current=True):
    meta = json.loads((directory / "metadata.json").read_text())
    rows = [json.loads(line) for line in (directory / "matrix.jsonl").read_text().splitlines()]
    expected = cases()
    random.Random(SEED).shuffle(expected)
    assert meta["seed"] == SEED and meta["case_order"] == [case_id(c) for c in expected]
    assert [r["case"] for r in rows] == meta["case_order"]
    if current:
        assert (
            sources() == meta["files"] and source_hash() == meta["source_sha256"] and libraries() == meta["libraries"]
        )
        assert static.file_hash(REPO / meta["input"]) == meta["input_sha256"]
        for path, hashed in meta["gate_tests"].items():
            assert static.file_hash(REPO / path) == hashed
    for row, case in zip(rows, expected):
        assert all(row[k] == value for k, value in case.items())
        assert_row(row, meta)
    equivalent(rows)
    parity = [json.loads(line) for line in (directory / "parity.jsonl").read_text().splitlines()]
    expected_parity = {
        (i, p, m) for i, p, m in itertools.product(("CPython", "PyPy"), ("independent", "feedback"), ("kpi", "full"))
    }
    assert {(r["interpreter"], r["producer"], r["mode"]) for r in parity} == expected_parity and len(parity) == 8
    for row in parity:
        assert row["backend"] == "native" and row["jobs"] == 20000
        assert row["source_sha256"] == meta["source_sha256"] and row["libraries"] == meta["libraries"]
        assert row["input_sha256"] == meta["input_sha256"] and row["warmups"] == [] and len(row["samples"]) == 1
        direct = next(
            r
            for r in rows
            if r["backend"] == "direct" and all(r[k] == row[k] for k in ("jobs", "interpreter", "producer", "mode"))
        )
        equivalent([row, direct])
        for key in ("counters", "source_bytes", "trace_bytes", "trace_wire_sha256", "trace_signature"):
            assert row[key] == direct[key], key
    return meta, rows, parity


def summarize(directory):
    meta, rows, parity = audit(directory)
    output = []
    for row in sorted(rows, key=case_id):
        times = [s["total"] for s in row["samples"]]
        output.append(
            dict(
                case=row["case"],
                interpreter=row["interpreter"],
                producer=row["producer"],
                jobs=row["jobs"],
                mode=row["mode"],
                backend=row["backend"],
                median_s=statistics.median(times),
                minimum_s=min(times),
                maximum_s=max(times),
                cold_s=row["cold_sample"]["total"],
                peak_rss_mib=row["peak_rss_bytes"] / 2**20,
                trace_bytes=row["trace_bytes"],
                source_bytes=row["source_bytes"],
                operations=row["operations"],
            )
        )
    with (directory / "summary.csv").open("w") as target:
        writer = csv.DictWriter(target, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    # Audit must reject corrupt evidence, beyond merely checking coverage.
    rejected = []
    for key in ("source_sha256", "trajectory_sha256", "trace_wire_sha256", "counters", "trace_bytes"):
        bad = copy.deepcopy(next(r for r in rows if r["backend"] == "direct" and r["mode"] == "full"))
        bad[key] = "corrupt"
        try:
            assert_row(bad, meta)
        except (AssertionError, TypeError):
            rejected.append(key)
        else:
            raise AssertionError(f"accepted corrupt {key}")
    result = dict(
        source_sha256=meta["source_sha256"],
        cases=len(rows),
        measured_samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        aligned_parity_probes=len(parity),
        corruption_rejected=rejected,
        evidence={
            name: static.file_hash(directory / name)
            for name in ("metadata.json", "matrix.jsonl", "parity.jsonl", "summary.csv")
        },
    )
    (directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    path = REPO / "prototypes/native_engine/.inputs/jobs-50000-load-0.95.json"
    frozen, binaries = source_hash(), libraries()
    order = cases()
    random.Random(SEED).shuffle(order)
    test_path = ROOT / "tests/test_semantics_gate.py"
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        seed=SEED,
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        input=str(path.relative_to(REPO)),
        input_sha256=static.file_hash(path),
        platform=platform.platform(),
        host=platform.node(),
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        case_order=[case_id(c) for c in order],
        command=" ".join(sys.argv),
        gate_tests={str(test_path.relative_to(REPO)): static.file_hash(test_path)},
        writer=dict(
            max_events=10000, max_bytes=1 << 20, max_pending_bytes=64 << 20, max_latency_s="infinity", fsync=False
        ),
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    env = dict(os.environ, PYTHONHASHSEED="0")

    def worker(case, probe=False):
        executable = sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute())
        command = [
            executable,
            "-m",
            "prototypes.native_engine_dynamic.production_benchmark",
            "worker",
            "--input",
            str(path),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("producer", "mode", "backend", "jobs"):
            command.extend([f"--{key}", str(case[key])])
        if probe:
            command.append("--probe")
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        assert all(row[k] == value for k, value in case.items())
        assert (
            row["source_sha256"] == frozen
            and row["libraries"] == binaries
            and row["input_sha256"] == meta["input_sha256"]
        )
        assert source_hash() == frozen and libraries() == binaries
        return row

    rows = []
    for case in order:
        row, cold = worker(case), worker(case, probe=True)
        row.update(
            case=case_id(case),
            cold_sample=cold["samples"][0],
            peak_rss_bytes=cold["peak_rss_bytes"],
            cold_evidence={key: cold[key] for key in EVIDENCE_KEYS},
        )
        assert_row(row, meta)
        rows.append(row)
        equivalent(rows)
        with (args.output / "matrix.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(f"{len(rows)}/48 {row['case']} {statistics.median(s['total'] for s in row['samples']):.4f}s", flush=True)
    for i, p, m in itertools.product(("CPython", "PyPy"), ("independent", "feedback"), ("kpi", "full")):
        row = worker(dict(interpreter=i, producer=p, mode=m, jobs=20000, backend="native"), probe=True)
        with (args.output / "parity.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(f"aligned parity {i} {p} {m}", flush=True)
    summarize(args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("matrix")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    run = sub.add_parser("audit")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    matrix(args) if args.command == "matrix" else summarize(args.output)


if __name__ == "__main__":
    main()
