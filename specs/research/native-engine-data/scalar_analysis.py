"""Audit the 64-case compiled scalar/feedback experiment and derive summaries."""

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
from prototypes.native_engine_dynamic.benchmark import source_hash, sources  # noqa:E402
from prototypes.native_engine_dynamic.scalar_benchmark import PHASES, cases, case_id, equivalent, libraries  # noqa:E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(rows, meta, *, current=True):
    assert len(rows) == len({r["case"] for r in rows}) == 64, "unique cases"
    assert {r["case"] for r in rows} == {case_id(c) for c in cases()}, "coverage"
    assert [r["case"] for r in rows] == meta["case_order"], "order"
    for r in rows:
        assert r["case"] == case_id(r)
        assert r["study"] == "scalar" and r["cap"] == 12 and r["policy"] == "spt" and r["load"] == 0.95
        assert r["source_sha256"] == meta["source_sha256"], "source"
        assert r["libraries"] == meta["libraries"], "libraries"
        assert r["input_sha256"] == meta["inputs"][str(r["jobs"])], "input"
        assert r["jobs"] <= r["operations"] <= 10 * r["jobs"]
        assert len(r["samples"]) == 5 and len(r["warmups"]) == (1 if r["interpreter"] == "CPython" else 7)
        assert r["peak_rss_bytes"] > 0
        if r["producer"] == "feedback":
            assert r["observations"] == r["jobs"] - 1
            assert sum(r["states"].values()) == r["observations"] and len(r["states"]) >= 2
            assert sum(r["choices"].values()) == r["jobs"] and r["choices"]["seed"] == 1
            assert set(r["choices"]) <= {"seed", "short", "long", "normal"}
            assert set(r["states"]) <= {"scheduled", "pool", "active", "finished"}
        else:
            assert r["observations"] == 0 and r["choices"] == r["states"] == {}
            assert r["operations"] == (27363 if r["jobs"] == 5000 else 274452)
        assert r["counters"]["completed"] == r["jobs"] and r["counters"]["callbacks"] == 0
        records = 3 * (r["jobs"] + r["operations"]) if r["mode"] == "record" else 0
        assert r["counters"]["records"] == records
        assert r["recording_bytes"] == (8 + 32 * records if records else 0)
        assert (r["recording_sha256"] is not None) == bool(records)
        for timing in [*r["samples"], *r["warmups"], r["cold_sample"]]:
            assert set(timing) == set(PHASES)
            assert all(math.isfinite(v) and v >= 0 for v in timing.values()) and timing["total"] > 0
            assert math.isclose(sum(timing[k] for k in PHASES[:-1]), timing["total"], abs_tol=1e-9), "timing sum"
    equivalent(rows)
    root = REPO / "prototypes/native_engine_dynamic"
    old = [json.loads(s) for s in (root / "results/matrix.jsonl").read_text().splitlines()]
    for r in rows:
        if r["producer"] == "independent":
            prior = [
                p
                for p in old
                if p["study"] == "dynamic"
                and p["jobs"] == r["jobs"]
                and p["cap"] == r["cap"]
                and p["mode"] == r["mode"]
            ]
            assert len(prior) == 6
            for p in prior:
                for field in ("input_sha256", "trajectory_sha256", "recording_sha256", "recording_bytes", "counters"):
                    assert r[field] == p[field], f"historical parity {field}"
    previous = json.loads((root / "registration-results/metadata.json").read_text())
    for key in ("static", "dynamic"):
        assert meta["libraries"][key] == previous["libraries"][key], "existing DSO changed"
    if current:
        assert source_hash() == meta["source_sha256"] and sources() == meta["files"], "current source"
        assert libraries() == meta["libraries"], "current libraries"
        for n, hashed in meta["inputs"].items():
            assert digest(REPO / f"prototypes/native_engine/.inputs/jobs-{n}-load-0.95.json") == hashed


def negative_checks(rows, meta):
    rejected = []
    for field in (
        "case",
        "source_sha256",
        "libraries",
        "trajectory_sha256",
        "generated_sha256",
        "decisions_sha256",
        "observations",
        "samples",
    ):
        bad = deepcopy(rows)
        if field == "case":
            bad[-1] = deepcopy(bad[0])
        elif field == "libraries":
            bad[0][field]["dynamic"] = "corrupt"
        elif field == "observations":
            bad[0][field] += 1
        elif field == "samples":
            bad[0][field][0]["total"] += 1
        else:
            bad[0][field] = "corrupt"
        try:
            audit(bad, meta, current=False)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    matrix, metadata = args.directory / "matrix.jsonl", args.directory / "metadata.json"
    rows = [json.loads(s) for s in matrix.read_text().splitlines()]
    meta = json.loads(metadata.read_text())
    audit(rows, meta)
    negative = negative_checks(rows, meta)
    summary = []
    for row in rows:
        r = {
            k: row[k]
            for k in (
                "case",
                "interpreter",
                "backend",
                "producer",
                "jobs",
                "operations",
                "mode",
                "observations",
                "peak_rss_bytes",
            )
        }
        for phase in PHASES:
            values = [s[phase] for s in row["samples"]]
            r[phase] = statistics.median(values)
            r[f"{phase}_min"], r[f"{phase}_max"] = min(values), max(values)
            r[f"cold_{phase}"] = row["cold_sample"][phase]
        r["spread_percent"] = 100 * (r["total_max"] / r["total_min"] - 1)
        summary.append(r)
    with (args.directory / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)
    verified = dict(
        cases=64,
        measured_samples=320,
        separate_cold_probes=64,
        exact_coverage=True,
        source_and_libraries_frozen=True,
        engine_DSO_unchanged=True,
        independent_historical_parity=True,
        feedback_decisions_and_generated_jobs_equivalent=True,
        negative_checks_rejected=negative,
        matrix_sha256=digest(matrix),
        metadata_sha256=digest(metadata),
        analysis_sha256=digest(__file__),
    )
    (args.directory / "verification.json").write_text(json.dumps(verified, indent=2) + "\n")
    by = {r["case"]: r for r in summary}
    print(
        "| Jobs | Producer | Mode | CP ctypes ms | CP compiled ms | PyPy ctypes ms | PyPy compiled ms | "
        "Best compact PyPy ms | CP compiled margin |"
    )
    print("|---:|---|---|---:|---:|---:|---:|---:|---:|")
    for n in (5000, 50000):
        for producer in ("independent", "feedback"):
            for mode in ("none", "record"):
                values = [
                    by[f"{i}-{producer}-{n}-{mode}-{b}"]["total"]
                    for i, b in (
                        ("CPython", "native"),
                        ("CPython", "compiled"),
                        ("PyPy", "native"),
                        ("PyPy", "compiled"),
                    )
                ]
                best = min(by[f"PyPy-{producer}-{n}-{mode}-{b}"]["total"] for b in ("reference", "tuned"))
                print(
                    f"| {n} | {producer} | {mode} | "
                    + " | ".join(f"{v * 1000:.2f}" for v in values)
                    + f" | {best * 1000:.2f} | {best / values[1]:.2f}x |"
                )


if __name__ == "__main__":
    main()
