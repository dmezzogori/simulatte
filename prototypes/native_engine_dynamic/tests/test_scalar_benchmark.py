"""Run actual fresh workers and check feedback evidence and cold accounting."""

import json
import subprocess
import sys

import pytest

from prototypes.native_engine.workloads import generate, save
from prototypes.native_engine_dynamic.scalar_benchmark import equivalent


def test_actual_scalar_workers_keep_producer_decisions_and_trajectories(tmp_path):
    path = tmp_path / "input.json"
    save(generate(80, 0.95), path)
    rows = []
    for producer in ("independent", "feedback"):
        for backend in ("reference", "tuned", "native", "compiled"):
            command = [
                sys.executable,
                "-m",
                "prototypes.native_engine_dynamic.scalar_benchmark",
                "worker",
                "--input",
                str(path),
                "--producer",
                producer,
                "--backend",
                backend,
                "--mode",
                "record",
                "--probe",
            ]
            row = json.loads(subprocess.check_output(command, text=True))
            assert row["counters"]["completed"] == 80 and row["counters"]["callbacks"] == 0
            assert row["observations"] == (79 if producer == "feedback" else 0)
            assert row["peak_rss_bytes"] > 0 and len(row["libraries"]) == 4
            assert len(row["samples"]) == 1 and row["warmups"] == []
            rows.append(row)
    equivalent(rows)
    rows[-1]["decisions_sha256"] = "bad"
    with pytest.raises(AssertionError):
        equivalent(rows)
