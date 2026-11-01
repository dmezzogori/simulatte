"""Audit the fixed 64-case registration study, including prior parity evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from prototypes.native_engine_dynamic.benchmark import binary_hashes, equivalent, source_hash, sources  # noqa:E402
from prototypes.native_engine_dynamic.registration_benchmark import PHASES, case_id, cases  # noqa:E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(rows, meta, *, check_current=True):
    expected = {case_id(case) for case in cases()}
    assert len(rows) == len({r["case"] for r in rows}) == 64, "unique case count"
    assert {r["case"] for r in rows} == expected, "case coverage"
    assert [r["case"] for r in rows] == meta["case_order"], "execution order"
    for row in rows:
        assert row["case"] == case_id(row), "case identity"
        assert row["study"] == "registration" and row["jobs"] == 50000 and row["operations"] == 274452
        assert row["cap"] == 12 and row["policy"] == "spt" and row["load"] == 0.95
        assert row["source_sha256"] == meta["source_sha256"], "source"
        assert row["libraries"] == meta["libraries"], "libraries"
        assert row["input_sha256"] == meta["input_sha256"], "input"
        assert len(row["samples"]) == 5
        assert len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
        assert row["peak_rss_bytes"] > 0
        windows = math.ceil(row["jobs"] / row["window"])
        assert row["advance_calls"] == (windows if row["schedule"] == "interleaved" else 0)
        assert row["registration_calls"] == (windows if row["method"] == "bulk" else row["jobs"])
        assert row["counters"]["completed"] == row["jobs"] and row["counters"]["callbacks"] == 0
        records = 3 * row["jobs"] + 3 * row["operations"] if row["mode"] == "record" else 0
        assert row["counters"]["records"] == records
        assert row["recording_bytes"] == (8 + 32 * records if records else 0)
        assert (row["recording_sha256"] is not None) == bool(records)
        for timing in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert set(timing) == set(PHASES)
            assert all(math.isfinite(value) and value >= 0 for value in timing.values())
            assert timing["total"] > 0
            assert math.isclose(sum(timing[key] for key in PHASES[:-1]), timing["total"], abs_tol=1e-9)
    equivalent(rows)
    # Compare exact trajectory and record hashes with the previous, independently
    # validated streamed dynamic study at 69010b7; historical timings are not reused.
    old = REPO / "prototypes/native_engine_dynamic/results/matrix.jsonl"
    prior = [json.loads(line) for line in old.read_text().splitlines()]
    for row in rows:
        matches = [
            r
            for r in prior
            if r["study"] == "dynamic"
            and r["jobs"] == row["jobs"]
            and r["cap"] == row["cap"]
            and r["mode"] == row["mode"]
        ]
        assert len(matches) == 6
        for previous in matches:
            for key in ("input_sha256", "trajectory_sha256", "recording_sha256", "recording_bytes", "counters"):
                assert row[key] == previous[key], f"prior evidence {key}"
    if check_current:
        assert source_hash() == meta["source_sha256"] and sources() == meta["files"], "current source"
        assert binary_hashes() == meta["libraries"], "current libraries"
        assert digest(REPO / meta["input"]) == meta["input_sha256"], "current input"


def negative_checks(rows, meta):
    passed = []
    for field in ("case", "source_sha256", "libraries", "trajectory_sha256", "registration_calls", "samples"):
        broken = deepcopy(rows)
        if field == "case":
            broken[-1] = deepcopy(broken[0])
        elif field == "libraries":
            broken[0][field]["dynamic"] = "corrupt"
        elif field == "registration_calls":
            broken[0][field] += 1
        elif field == "samples":
            broken[0][field][0]["total"] += 1
        else:
            broken[0][field] = "corrupt"
        try:
            audit(broken, meta, check_current=False)
        except AssertionError:
            passed.append(field)
        else:
            raise AssertionError(f"audit accepted corrupt {field}")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    matrix, metadata = args.directory / "matrix.jsonl", args.directory / "metadata.json"
    rows = [json.loads(line) for line in matrix.read_text().splitlines()]
    meta = json.loads(metadata.read_text())
    audit(rows, meta)
    negative = negative_checks(rows, meta)
    summary = []
    for row in rows:
        item = {
            key: row[key]
            for key in (
                "case",
                "interpreter",
                "schedule",
                "backend",
                "method",
                "window",
                "mode",
                "registration_calls",
                "advance_calls",
                "peak_rss_bytes",
            )
        }
        for phase in PHASES:
            values = [sample[phase] for sample in row["samples"]]
            item[phase] = statistics.median(values)
            item[f"{phase}_min"], item[f"{phase}_max"] = min(values), max(values)
            item[f"cold_{phase}"] = row["cold_sample"][phase]
        item["spread_percent"] = 100 * (item["total_max"] / item["total_min"] - 1)
        summary.append(item)
    with (args.directory / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)
    verification = dict(
        cases=64,
        measured_samples=320,
        separate_cold_probes=64,
        exact_coverage=True,
        source_and_libraries_frozen=True,
        prior_trajectory_and_record_parity=True,
        negative_checks_rejected=negative,
        matrix_sha256=digest(matrix),
        metadata_sha256=digest(metadata),
        analysis_sha256=digest(__file__),
    )
    (args.directory / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    by_case = {row["case"]: row for row in summary}
    print("| Interpreter | Mode | Scalar 1 ms | Scalar 32 ms | Bulk 32 ms | Scalar 256 ms | Bulk 256 ms |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for interpreter in ("CPython", "PyPy"):
        for mode in ("none", "record"):
            values = [
                by_case[f"{interpreter}-interleaved-native-{method}-{window}-{mode}"]["total"] * 1000
                for method, window in (("scalar", 1), ("scalar", 32), ("bulk", 32), ("scalar", 256), ("bulk", 256))
            ]
            print(f"| {interpreter} | {mode} | " + " | ".join(f"{value:.2f}" for value in values) + " |")
    print("\n| Interpreter | Mode | Eager scalar feed ms | Eager bulk 32 feed ms | Eager bulk 256 feed ms |")
    print("|---|---|---:|---:|---:|")
    for interpreter in ("CPython", "PyPy"):
        for mode in ("none", "record"):
            values = [
                by_case[f"{interpreter}-eager-native-{method}-{window}-{mode}"]["feed"] * 1000
                for method, window in (("scalar", 256), ("bulk", 32), ("bulk", 256))
            ]
            print(f"| {interpreter} | {mode} | " + " | ".join(f"{value:.2f}" for value in values) + " |")


if __name__ == "__main__":
    main()
