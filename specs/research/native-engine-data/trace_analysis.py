"""Audit native production trace compatibility, source invariance and pipeline cost."""

from __future__ import annotations

import argparse
from copy import deepcopy
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
from prototypes.native_engine_dynamic.trace_benchmark import (  # noqa:E402
    PHASES,
    cases,
    case_id,
    equivalent,
    libraries,
    source_hash,
    sources,
)
from prototypes.native_engine.workloads import read  # noqa:E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(rows, meta, *, current=True):
    assert len(rows) == len({r["case"] for r in rows}) == 72, "unique cases"
    assert {r["case"] for r in rows} == {case_id(r) for r in cases()}, "case coverage"
    assert [r["case"] for r in rows] == meta["case_order"], "execution order"
    templates = read(REPO / meta["input"])
    software = {}
    for r in rows:
        assert r["case"] == case_id(r) and r["study"] == "studio-trace"
        assert r["source_sha256"] == meta["source_sha256"], "source"
        assert r["libraries"] == meta["libraries"], "libraries"
        assert r["input_sha256"] == meta["input_sha256"], "input"
        assert r["cap"] == 12 and r["policy"] == "spt" and r["template_load"] == 0.95
        assert r["jobs"] <= r["operations"] <= 10 * r["jobs"]
        if r["producer"] == "independent":
            assert r["operations"] == sum(len(j.route) for j in templates.jobs[: r["jobs"]])
        assert len(r["samples"]) == 5 and len(r["warmups"]) == (1 if r["interpreter"] == "CPython" else 7)
        assert len(r["trace_sizes"]) == len(r["samples"]) + len(r["warmups"])
        assert software.setdefault(r["interpreter"], r["versions"]) == r["versions"], "software versions"
        assert r["peak_rss_bytes"] > 0
        assert r["counters"]["completed"] == r["jobs"] and r["counters"]["callbacks"] == 0
        records = 0 if r["mode"] == "none" else 4 * r["jobs"] + 5 * r["operations"]
        assert r["counters"]["records"] == records
        assert r["source_bytes"] == (8 + 32 * records if records else 0)
        assert (r["source_records_sha256"] is not None) == bool(records)
        if r["mode"] == "full":
            sig = r["trace_signature"]
            assert sig["domain_events"] == 5 * r["jobs"] + 7 * r["operations"]
            assert sig["observer_events"] == 4 * r["jobs"] + 2
            assert sig["chunks"] > 1 and all(r["trace_sizes"])
            assert r["trace_wire_sha256"] is not None and r["trace_bytes"] > 0
        else:
            assert r["trace_signature"] is r["trace_wire_sha256"] is None and r["trace_bytes"] == 0
            assert not any(r["trace_sizes"])
        for timing in [*r["samples"], *r["warmups"], r["cold_sample"]]:
            assert set(timing) == set(PHASES)
            assert all(math.isfinite(v) and v >= 0 for v in timing.values()) and timing["total"] > 0
            assert math.isclose(sum(timing[k] for k in PHASES[:-1]), timing["total"], abs_tol=1e-9), "timing sum"
    equivalent(rows)
    if current:
        assert source_hash() == meta["source_sha256"] and sources() == meta["files"], "current source"
        assert libraries() == meta["libraries"], "current libraries"
        assert digest(REPO / meta["input"]) == meta["input_sha256"], "current input"


def negatives(rows, meta):
    rejected = []
    for field in (
        "case",
        "source_sha256",
        "libraries",
        "trajectory_sha256",
        "generated_sha256",
        "decisions_sha256",
        "source_records_sha256",
        "trace_signature",
        "samples",
    ):
        bad = deepcopy(rows)
        if field == "case":
            bad[-1] = deepcopy(bad[0])
        elif field == "libraries":
            bad[0][field]["dynamic"] = "corrupt"
        elif field == "trace_signature":
            next(r for r in bad if r["mode"] == "full")[field]["state_sha256"] = "corrupt"
        elif field == "samples":
            bad[0][field][0]["total"] += 1
        elif field == "source_records_sha256":
            next(r for r in bad if r["mode"] == "raw")[field] = "corrupt"
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
    rejected = negatives(rows, meta)
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
                "trace_bytes",
                "source_bytes",
                "peak_rss_bytes",
            )
        }
        r["chunks"] = row["trace_signature"]["chunks"] if row["trace_signature"] else 0
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
        cases=72,
        measured_samples=360,
        separate_cold_probes=72,
        exact_coverage=True,
        observer_invariance=True,
        full_trace_semantic_event_state_kpi_parity=True,
        source_and_libraries_frozen=True,
        negative_checks_rejected=rejected,
        matrix_sha256=digest(matrix),
        metadata_sha256=digest(metadata),
        analysis_sha256=digest(__file__),
    )
    (args.directory / "verification.json").write_text(json.dumps(verified, indent=2) + "\n")
    by = {r["case"]: r for r in summary}
    print(
        "| Jobs | Producer | Mode | CP native ms | Best compact CP ms | Best compact PyPy ms | "
        "Native / best PyPy throughput |"
    )
    print("|---:|---|---|---:|---:|---:|---:|")
    for n, producer, mode in itertools.product((500, 5000), ("independent", "feedback"), ("none", "raw", "full")):
        native = by[f"CPython-{producer}-{n}-{mode}-compiled"]["total"]
        best = {
            i: min(by[f"{i}-{producer}-{n}-{mode}-{b}"]["total"] for b in ("reference", "tuned"))
            for i in ("CPython", "PyPy")
        }
        print(
            f"| {n} | {producer} | {mode} | {native * 1000:.2f} | {best['CPython'] * 1000:.2f} | "
            f"{best['PyPy'] * 1000:.2f} | {best['PyPy'] / native:.2f}x |"
        )


if __name__ == "__main__":
    main()
