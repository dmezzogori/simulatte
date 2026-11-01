"""Audit and summarize the binding-only, 24-case before/after study."""

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

from prototypes.native_engine.analyze import summaries  # noqa: E402
from prototypes.native_engine.benchmark import assert_equivalent, source_hash  # noqa: E402
from prototypes.native_engine.build import LIBRARY  # noqa: E402

PHASES = ("build", "run", "extract", "close", "total")
EXPECTED = {
    f"{interpreter}-{backend}-50000-0.95-spt-{mode}-{hook}"
    for interpreter, backend, mode, hook in itertools.product(
        ("cpython", "pypy"), ("reference", "tuned", "native"), ("none", "record"), ("builtin", "python")
    )
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_rows(path, metadata):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 24 and {r["case"] for r in rows} == EXPECTED, "case coverage"
    for row in rows:
        label = {"CPython": "cpython", "PyPy": "pypy"}[row["interpreter"]]
        assert row["case"] == (
            f"{label}-{row['backend']}-{row['jobs']}-{row['load']}-{row['policy']}-{row['mode']}-{row['hook']}"
        ), "case identity"
        assert row["source_sha256"] == metadata["source_sha256"], "source provenance"
        assert row["input_sha256"] == metadata["inputs"]["jobs-50000-load-0.95.json"], "input provenance"
        assert row["jobs"] == 50000 and row["operations"] == 274452, "workload size"
        assert len(row["samples"]) == 5, "sample count"
        assert len(row["warmup_totals"]) == (1 if label == "cpython" else 7), "warmup count"
        assert row["peak_rss_bytes"] > 0, "cold RSS probe"
        for sample in [*row["samples"], row["cold_sample"]]:
            assert set(sample) == set(PHASES), "timing fields"
            assert all(math.isfinite(value) and value >= 0 for value in sample.values()), "invalid timing"
            assert sample["total"] > 0, "empty lifecycle timing"
            assert math.isclose(sum(sample[p] for p in PHASES[:-1]), sample["total"], abs_tol=1e-9), "timing sum"
        counters = row["counters"]
        assert counters["completed"] == 50000, "completed jobs"
        assert counters["callbacks"] == (274452 if row["hook"] == "python" else 0), "callback count"
        records = 923356 if row["mode"] == "record" else 0
        assert counters["records"] == records, "record count"
        assert row["recording_bytes"] == (8 + 32 * records if records else 0), "record bytes"
        assert (row["recording_sha256"] is not None) == bool(records), "recording fingerprint"
    assert_equivalent(rows)
    return {row["case"]: row for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument(
        "--metadata", type=Path, default=REPO / "prototypes/native_engine/results/binding-metadata.json"
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    before, after = (
        audit_rows(path, metadata[phase]) for path, phase in ((args.before, "before"), (args.after, "after"))
    )
    assert_equivalent([*before.values(), *after.values()])
    for case in EXPECTED:
        for field in ("input_sha256", "trajectory_sha256", "recording_sha256", "recording_bytes", "counters", "python"):
            assert before[case][field] == after[case][field], (case, field)
    for key in (
        "library_sha256",
        "inputs",
        "compiler",
        "flags",
        "cpython",
        "pypy",
        "dependencies",
        "pypy_dependencies",
    ):
        assert metadata["before"][key] == metadata["after"][key], f"metadata mismatch: {key}"
    changed = [name for name, value in metadata["before"]["files"].items() if value != metadata["after"]["files"][name]]
    assert changed == ["prototypes/native_engine/native.py"], ("unexpected source changes", changed)
    for name, expected in metadata["after"]["files"].items():
        assert digest(REPO / name) == expected, f"source changed after measurement: {name}"
    for name, expected in metadata["after"]["inputs"].items():
        assert digest(REPO / "prototypes/native_engine/.inputs" / name) == expected, name
    assert digest(LIBRARY) == metadata["after"]["library_sha256"], "release library changed"
    assert source_hash() == metadata["after"]["source_sha256"], "measured source changed"

    summary = {
        phase: {r["case"]: r for r in summaries(path)}
        for phase, path in (("before", args.before), ("after", args.after))
    }
    output = []
    for case in sorted(EXPECTED):
        row = {key: after[case][key] for key in ("case", "interpreter", "backend", "mode", "hook")}
        for phase, raw in (("before", before), ("after", after)):
            for timing in PHASES:
                values = [sample[timing] for sample in raw[case]["samples"]]
                row[f"{phase}_{timing}"] = statistics.median(values)
                row[f"{phase}_{timing}_min"] = min(values)
                row[f"{phase}_{timing}_max"] = max(values)
                row[f"{phase}_cold_{timing}"] = raw[case]["cold_sample"][timing]
            row[f"{phase}_peak_rss_bytes"] = raw[case]["peak_rss_bytes"]
            row[f"{phase}_jobs_per_second"] = summary[phase][case]["jobs_per_second"]
            row[f"{phase}_operations_per_second"] = summary[phase][case]["operations_per_second"]
        row["before_after_speedup"] = row["before_total"] / row["after_total"]
        if row["interpreter"] == "CPython" and row["backend"] == "native":
            for phase in ("before", "after"):
                control = min(
                    (
                        summary[phase][f"pypy-{backend}-50000-0.95-spt-{row['mode']}-{row['hook']}"]
                        for backend in ("reference", "tuned")
                    ),
                    key=lambda value: value["total"],
                )
                row[f"{phase}_fastest_pypy_backend"] = control["backend"]
                row[f"{phase}_speedup_over_pypy"] = control["total"] / row[f"{phase}_total"]
        output.append(row)
    fields = list(dict.fromkeys(key for row in output for key in row))
    with args.csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    audit = dict(
        cases_per_phase=24,
        measured_samples=240,
        separate_cold_probes=48,
        exact_coverage=True,
        all_fingerprints_and_counters_match=True,
        unchanged_release_library=True,
        changed_measured_sources=changed,
        before_sha256=digest(args.before),
        after_sha256=digest(args.after),
        metadata_sha256=digest(args.metadata),
        analysis_sha256=digest(__file__),
        checks=metadata["checks"],
    )
    args.audit.write_text(json.dumps(audit, indent=2) + "\n")
    print("| CPython native | Hook | Before ms | After ms | Binding speedup | Vs fastest PyPy after |")
    print("|---|---|---:|---:|---:|---:|")
    for row in output:
        if "after_speedup_over_pypy" in row:
            print(
                f"| {row['mode']} | {row['hook']} | {row['before_total'] * 1000:.2f} | "
                f"{row['after_total'] * 1000:.2f} | {row['before_after_speedup']:.2f}x | "
                f"{row['after_speedup_over_pypy']:.2f}x |"
            )


if __name__ == "__main__":
    main()
