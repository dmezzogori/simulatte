"""Exercise the actual producer-window worker, including the cold probe."""

import json
import subprocess
import sys

from prototypes.native_engine.workloads import generate, save
from prototypes.native_engine_dynamic.benchmark import equivalent


def test_registration_workers_preserve_results_and_records(tmp_path):
    path = tmp_path / "input.json"
    save(generate(40, 0.95), path)
    rows = []
    for schedule, backend, method, window in (
        ("interleaved", "reference", "scalar", 1),
        ("interleaved", "tuned", "scalar", 7),
        ("interleaved", "native", "scalar", 7),
        ("interleaved", "native", "bulk", 7),
        ("eager", "native", "bulk", 256),
    ):
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.registration_benchmark",
            "worker",
            "--input",
            str(path),
            "--schedule",
            schedule,
            "--backend",
            backend,
            "--method",
            method,
            "--window",
            str(window),
            "--mode",
            "record",
            "--probe",
        ]
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["counters"]["completed"] == 40 and row["counters"]["callbacks"] == 0
        assert len(row["samples"]) == 1 and row["warmups"] == []
        assert row["peak_rss_bytes"] > 0
        assert row["advance_calls"] == ((40 + window - 1) // window if schedule == "interleaved" else 0)
        assert row["registration_calls"] == ((40 + window - 1) // window if method == "bulk" else 40)
        assert row["recording_bytes"] == 8 + 32 * row["counters"]["records"]
        rows.append(row)
    equivalent(rows)
