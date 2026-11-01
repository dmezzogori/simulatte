"""Actual worker paths preserve observer invariance and production trace parity."""

import json
import subprocess
import sys

import pytest

from prototypes.native_engine.workloads import generate, save
from prototypes.native_engine_dynamic.trace_benchmark import equivalent


def test_trace_workers_preserve_results_decisions_and_real_trace_signatures(tmp_path):
    path = tmp_path / "input.json"
    save(generate(30, 0.95), path)
    rows = []
    for producer in ("independent", "feedback"):
        for mode in ("none", "raw", "full"):
            for backend in ("compiled", "reference", "tuned"):
                command = [
                    sys.executable,
                    "-m",
                    "prototypes.native_engine_dynamic.trace_benchmark",
                    "worker",
                    "--input",
                    str(path),
                    "--jobs",
                    "30",
                    "--producer",
                    producer,
                    "--mode",
                    mode,
                    "--backend",
                    backend,
                    "--probe",
                ]
                row = json.loads(subprocess.check_output(command, text=True))
                assert row["counters"]["completed"] == 30 and row["counters"]["callbacks"] == 0
                assert row["peak_rss_bytes"] > 0 and row["warmups"] == [] and len(row["samples"]) == 1
                assert (row["trace_signature"] is not None) == (mode == "full")
                assert row["source_bytes"] == (8 + 32 * row["counters"]["records"] if mode != "none" else 0)
                if mode == "full":
                    assert row["trace_signature"]["domain_events"] == 5 * 30 + 7 * row["operations"]
                    assert row["trace_signature"]["observer_events"] == 4 * 30 + 2
                    assert row["trace_bytes"] > 0
                rows.append(row)
    equivalent(rows)
    rows[-1]["trace_signature"]["state_sha256"] = "wrong"
    with pytest.raises(AssertionError):
        equivalent(rows)
