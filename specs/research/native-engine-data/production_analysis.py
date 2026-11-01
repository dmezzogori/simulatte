"""Audit direct-production measurements and separate thread CPU profiles."""

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
from prototypes.native_engine_dynamic.production_benchmark import (  # noqa:E402
    PHASES,
    cases,
    case_id,
    equivalent,
    libraries,
    source_hash,
    sources,
)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(rows, meta, *, current=True):
    assert len(rows) == len({r["case"] for r in rows}) == 24, "unique cases"
    assert {r["case"] for r in rows} == {case_id(c) for c in cases()}, "coverage"
    assert [r["case"] for r in rows] == meta["case_order"], "order"
    semantic, software, raw = {}, {}, {}
    for row in rows:
        assert row["case"] == case_id(row) and row["study"] == "direct-production"
        for key in ("source_sha256", "libraries", "input_sha256"):
            assert row[key] == meta[key], key
        assert software.setdefault(row["interpreter"], row["versions"]) == row["versions"]
        assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
        assert len(row["trace_sizes"]) == len(row["samples"]) + len(row["warmups"])
        assert row["jobs"] <= row["operations"] <= 10 * row["jobs"]
        assert row["peak_rss_bytes"] > 0 and row["counters"]["completed"] == row["jobs"]
        for t in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert set(t) == {*PHASES, "process_cpu"}
            assert all(math.isfinite(v) and v >= 0 for v in t.values()) and t["total"] > 0
            assert math.isclose(sum(t[k] for k in PHASES[:-1]), t["total"], abs_tol=1e-9), "timing sum"
        if row["mode"] == "none":
            assert row["trace_signature"] is row["trace_wire_sha256"] is row["kpis"] is None
            assert row["trace_bytes"] == row["source_bytes"] == 0 and not any(row["trace_sizes"])
        else:
            sig = row["trace_signature"]
            assert row["trace_bytes"] > 0 and all(n == row["trace_bytes"] for n in row["trace_sizes"])
            assert row["trace_wire_sha256"] and sig["kpis"] == row["kpis"]
            assert sig["series_values_sha256"] == row["series_values_sha256"]
            key = row["producer"], row["backend"]
            assert semantic.setdefault(key, sig["digest"]) == sig["digest"], "backend digest"
            records = 4 * row["jobs"] + 5 * row["operations"] if row["backend"] == "native" else 0
            assert row["source_bytes"] == (8 + 32 * records if records else 0)
            if records:
                assert raw.setdefault(row["producer"], row["source_bytes"]) == row["source_bytes"]
            if row["mode"] == "full":
                assert (
                    sig["domain_events"]
                    == (7 if row["backend"] == "production" else 5) * row["jobs"] + 7 * row["operations"]
                )
                assert sig["observer_events"] == 4 * row["jobs"] + (0 if row["backend"] == "production" else 2)
                assert sig["prelude_observer_events"] == (2 if row["backend"] == "production" else 0)
                assert sig["chunks"] > 1
            else:
                assert sig["domain_events"] == sig["observer_events"] == sig["chunks"] == 0
    equivalent(rows)
    if current:
        assert sources() == meta["files"] and source_hash() == meta["source_sha256"], "current source"
        assert libraries() == meta["libraries"], "current libraries"
        assert file_hash(REPO / meta["input"]) == meta["input_sha256"], "current input"


def negatives(rows, meta):
    rejected = []
    for field in (
        "case",
        "source_sha256",
        "libraries",
        "trajectory_sha256",
        "generated_sha256",
        "decisions_sha256",
        "kpis",
        "series_values_sha256",
        "samples",
        "trace_signature",
    ):
        bad = deepcopy(rows)
        target = next(r for r in bad if r["mode"] == "full")
        if field == "case":
            bad[-1] = deepcopy(bad[0])
        elif field == "samples":
            target[field][0]["total"] += 1
        elif field == "kpis":
            target[field]["shopfloor/total_flow"] += 100
        elif field == "trace_signature":
            target[field]["domain_events"] += 1
        elif field == "libraries":
            target[field]["dynamic"] = "corrupt"
        else:
            target[field] = "corrupt"
        try:
            audit(bad, meta, current=False)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    return rejected


def category(row):
    path, name = row["file"], row["name"]
    if name == "compress":
        return "compression"
    if "rich_recording.py" in path:
        return "bridge_journal"
    if path.endswith("studio_trace.py"):
        return "bridge_projection"
    if name in ("apply_deltas", "apply_op"):
        return "delta_replay"
    if path.endswith("digest.py"):
        return "semantic_digest"
    if path.endswith("_wire.py") or "msgpack" in path:
        return "wire_codec"
    if (
        path.endswith(("trace/sink.py", "trace/format.py"))
        or name in ("write", "read", "flush", "close")
        and path == ""
    ):
        return "file_io"
    if path.endswith("trace/writer.py"):
        return "trace_writer"
    if path.endswith(("events.py", "entities.py")):
        return "events_entities"
    if (
        "prototypes/native_engine_dynamic/compiled.py" in path
        or "prototypes/native_engine_dynamic/native.py" in path
        or "nd_scalar" in name
    ):
        return "native_binding"
    if "/simulatte/" in path or path.startswith("simpy/"):
        return "production_model_other"
    if name == "collect":
        return "garbage_collection"
    return "other"


def profile_audit(directory, rows, meta):
    profiles = []
    measured = {r["case"]: r for r in rows}
    covered = []
    for path in sorted((directory / "profiles").glob("*.json")):
        r = json.loads(path.read_text())
        assert (
            r["study"] == "direct-production-profile"
            and r["clock"] == "time.thread_time"
            and r["separate_thread_passes"]
        )
        for key in ("source_sha256", "libraries", "input_sha256"):
            assert r[key] == meta[key]
        case = dict(r, interpreter="CPython")
        covered.append(case_id(case))
        assert r["trajectory_sha256"] == measured[case_id(case)]["trajectory_sha256"]
        assert len(r["writers"]) == int(r["mode"] != "none")
        assert len(r["snapshots"]) == (
            measured[case_id(case)]["trace_signature"]["chunks"] if r["mode"] == "full" else 0
        )
        for span in r["spans"].values():
            assert span["calls"] > 0 and span["wall"] >= 0 and span["cpu"] >= 0
        for thread, stats in [("simulation", r["simulation"]), *[("writer", s) for s in r["writers"]]]:
            assert all(f["self_cpu"] >= 0 and f["cumulative_cpu"] >= 0 for f in stats["functions"])
            assert math.isclose(sum(f["self_cpu"] for f in stats["functions"]), stats["total_self_cpu"], abs_tol=1e-9)
            groups = {}
            for f in stats["functions"]:
                key = category(f)
                groups[key] = groups.get(key, 0) + f["self_cpu"]
            profiles.append(
                dict(
                    file=str(path.relative_to(directory)),
                    sha256=file_hash(path),
                    backend=r["backend"],
                    producer=r["producer"],
                    mode=r["mode"],
                    thread=thread,
                    total_self_cpu=stats["total_self_cpu"],
                    groups=groups,
                    top_functions=stats["functions"][:20],
                    spans=r["spans"],
                    snapshots=r["snapshots"],
                )
            )
    assert len(covered) == len(set(covered)) == 12, "profile coverage"
    assert set(covered) == {r["case"] for r in rows if r["interpreter"] == "CPython"}
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    rows = [json.loads(s) for s in (args.directory / "matrix.jsonl").read_text().splitlines()]
    meta = json.loads((args.directory / "metadata.json").read_text())
    audit(rows, meta)
    rejected = negatives(rows, meta)
    summary = []
    for row in rows:
        out = {
            k: row[k]
            for k in (
                "case",
                "interpreter",
                "producer",
                "backend",
                "mode",
                "jobs",
                "operations",
                "trace_bytes",
                "source_bytes",
                "peak_rss_bytes",
            )
        }
        out["chunks"] = row["trace_signature"]["chunks"] if row["trace_signature"] else 0
        for key in (*PHASES, "process_cpu"):
            values = [s[key] for s in row["samples"]]
            out[key] = statistics.median(values)
            out[f"{key}_min"], out[f"{key}_max"] = min(values), max(values)
            out[f"cold_{key}"] = row["cold_sample"][key]
        out["spread_percent"] = 100 * (out["total_max"] / out["total_min"] - 1)
        summary.append(out)
    with (args.directory / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)
    profiles = profile_audit(args.directory, rows, meta)
    (args.directory / "profile-summary.json").write_text(json.dumps(profiles, indent=2) + "\n")
    result = dict(
        cases=len(rows),
        timed_samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        exact_trajectory_generation_feedback_equivalence=True,
        kpi_tolerance_and_wip_series_equivalence=True,
        current_provenance=True,
        negative_checks_rejected=rejected,
        profile_threads=len(profiles),
        matrix_sha256=file_hash(args.directory / "matrix.jsonl"),
        metadata_sha256=file_hash(args.directory / "metadata.json"),
    )
    (args.directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
