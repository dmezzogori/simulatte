"""Audit the 80-case hook/dynamic study and summarize equivalent lifecycles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from prototypes.native_engine_dynamic.benchmark import equivalent, source_hash, sources, binary_hashes  # noqa:E402

PHASES = ("build", "feed", "run", "extract", "close", "total")
HOOKS = (
    "native-builtin",
    "native-tuple",
    "native-buffers",
    "reference-builtin",
    "reference-tuple",
    "reference-buffers",
    "tuned-builtin",
    "tuned-tuple",
)
EXPECTED = {
    f"hook-{interpreter}-{variant}-50000-{mode}-None"
    for interpreter, variant, mode in itertools.product(("cpython", "pypy"), HOOKS, ("none", "record"))
}
EXPECTED.update(
    f"dynamic-{interpreter}-{variant}-{jobs}-{mode}-{cap}"
    for interpreter, variant, jobs, mode, cap in itertools.product(
        ("cpython", "pypy"), ("reference", "tuned", "native"), (5000, 50000), ("none", "record"), (None, 12)
    )
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(rows, meta):
    assert len(rows) == len({r["case"] for r in rows}) == 80
    assert {r["case"] for r in rows} == EXPECTED, "case coverage"
    for row in rows:
        label = {"CPython": "cpython", "PyPy": "pypy"}[row["interpreter"]]
        assert row["case"] == f"{row['study']}-{label}-{row['variant']}-{row['jobs']}-{row['mode']}-{row['cap']}", (
            "case identity"
        )
        assert row["source_sha256"] == meta["source_sha256"], "source"
        assert row["libraries"] == meta["libraries"], "libraries"
        assert row["input_sha256"] == meta["inputs"][f"jobs-{row['jobs']}-load-0.95.json"], "input"
        assert row["policy"] == "spt" and row["load"] == 0.95
        assert row["operations"] == (27363 if row["jobs"] == 5000 else 274452)
        assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if label == "cpython" else 7)
        assert row["peak_rss_bytes"] > 0
        for sample in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert set(sample) == set(PHASES)
            assert all(math.isfinite(value) and value >= 0 for value in sample.values())
            assert sample["total"] > 0
            assert math.isclose(sum(sample[key] for key in PHASES[:-1]), sample["total"], abs_tol=1e-9)
        assert row["counters"]["completed"] == row["jobs"]
        callbacks = row["operations"] if row["study"] == "hook" and not row["variant"].endswith("builtin") else 0
        assert row["counters"]["callbacks"] == callbacks
        records = (
            ((2 if row["study"] == "hook" else 3) * row["jobs"] + 3 * row["operations"])
            if row["mode"] == "record"
            else 0
        )
        assert row["counters"]["records"] == records
        assert row["recording_bytes"] == (8 + 32 * records if records else 0)
        assert (row["recording_sha256"] is not None) == bool(records)
    equivalent(rows)
    assert source_hash() == meta["source_sha256"] and sources() == meta["files"], "current source changed"
    assert binary_hashes() == meta["libraries"], "current library changed"
    for name, expected in meta["inputs"].items():
        assert digest(REPO / "prototypes/native_engine/.inputs" / name) == expected, "current input changed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.matrix.read_text().splitlines()]
    meta = json.loads(args.metadata.read_text())
    audit(rows, meta)
    output = []
    for row in rows:
        summary = {
            key: row[key]
            for key in (
                "case",
                "study",
                "variant",
                "interpreter",
                "jobs",
                "operations",
                "mode",
                "cap",
                "peak_rss_bytes",
            )
        }
        for phase in PHASES:
            values = [sample[phase] for sample in row["samples"]]
            summary[phase] = statistics.median(values)
            summary[f"{phase}_min"], summary[f"{phase}_max"] = min(values), max(values)
            summary[f"cold_{phase}"] = row["cold_sample"][phase]
        summary["jobs_per_second"] = row["jobs"] / summary["total"]
        summary["operations_per_second"] = row["operations"] / summary["total"]
        summary["spread_percent"] = 100 * (summary["total_max"] / summary["total_min"] - 1)
        output.append(summary)
    with args.csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    audit_result = dict(
        cases=80,
        hook_cases=32,
        dynamic_cases=48,
        measured_samples=400,
        separate_cold_probes=80,
        exact_coverage=True,
        all_equivalent_fingerprints=True,
        source_and_libraries_frozen=True,
        input_hashes_match=True,
        matrix_sha256=digest(args.matrix),
        metadata_sha256=digest(args.metadata),
        analysis_sha256=digest(__file__),
        checks=meta["checks"],
    )
    args.audit.write_text(json.dumps(audit_result, indent=2) + "\n")
    by_case = {row["case"]: row for row in output}
    print("| Hook interface | Mode | Native tuple ms | Native buffers ms | Tuple / buffers | Best PyPy hook ms |")
    print("|---|---|---:|---:|---:|---:|")
    for interpreter, mode in itertools.product(("cpython", "pypy"), ("none", "record")):
        old = by_case[f"hook-{interpreter}-native-tuple-50000-{mode}-None"]
        new = by_case[f"hook-{interpreter}-native-buffers-50000-{mode}-None"]
        best = min(
            by_case[f"hook-pypy-{variant}-50000-{mode}-None"]["total"]
            for variant in ("reference-tuple", "reference-buffers", "tuned-tuple")
        )
        print(
            f"| {interpreter} | {mode} | {old['total'] * 1000:.2f} | {new['total'] * 1000:.2f} | "
            f"{old['total'] / new['total']:.2f}x | {best * 1000:.2f} |"
        )
    print("\n| Dynamic jobs | Cap | Mode | CPython native ms | PyPy native ms | Vs best CPython | Vs best PyPy |")
    print("|---:|---|---|---:|---:|---:|---:|")
    for jobs, cap, mode in itertools.product((5000, 50000), (None, 12), ("none", "record")):
        native = by_case[f"dynamic-cpython-native-{jobs}-{mode}-{cap}"]["total"]
        pypy = by_case[f"dynamic-pypy-native-{jobs}-{mode}-{cap}"]["total"]
        best = {
            interpreter: min(
                by_case[f"dynamic-{interpreter}-{variant}-{jobs}-{mode}-{cap}"]["total"]
                for variant in ("reference", "tuned")
            )
            for interpreter in ("cpython", "pypy")
        }
        print(
            f"| {jobs} | {cap} | {mode} | {native * 1000:.2f} | {pypy * 1000:.2f} | "
            f"{best['cpython'] / native:.2f}x | {best['pypy'] / native:.2f}x |"
        )


if __name__ == "__main__":
    main()
