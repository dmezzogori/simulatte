"""Run the actual study worker and reject semantic mismatches."""

import json
import hashlib
import subprocess
import sys

import pytest

from prototypes.native_engine.workloads import generate, save
from prototypes.native_engine_dynamic.benchmark import equivalent


def test_actual_workers_keep_equivalent_work_and_cold_probe(tmp_path):
    path = tmp_path / "input.json"
    save(generate(20, 0.95), path)
    rows = []
    for study, variant, cap in [
        ("hook", "native-buffers", None),
        ("hook", "reference-buffers", None),
        ("dynamic", "native", 1),
        ("dynamic", "reference", 1),
        ("dynamic", "tuned", 1),
    ]:
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.benchmark",
            "worker",
            "--study",
            study,
            "--variant",
            variant,
            "--cap",
            str(cap),
            "--input",
            str(path),
            "--mode",
            "record",
            "--probe",
        ]
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["jobs"] == row["counters"]["completed"] == 20
        assert len(row["samples"]) == 1 and row["warmups"] == []
        assert row["peak_rss_bytes"] > 0
        from prototypes.native_engine.build import LIBRARY as STATIC_LIBRARY
        from prototypes.native_engine_dynamic.build import LIBRARY as DYNAMIC_LIBRARY

        assert row["libraries"] == {
            "static": hashlib.sha256(STATIC_LIBRARY.read_bytes()).hexdigest(),
            "dynamic": hashlib.sha256(DYNAMIC_LIBRARY.read_bytes()).hexdigest(),
        }
        assert all(value >= 0 for value in row["samples"][0].values())
        assert row["counters"]["callbacks"] == (row["operations"] if study == "hook" else 0)
        rows.append(row)
    equivalent(rows)
    rows[-1]["trajectory_sha256"] = "invalid"
    with pytest.raises(AssertionError):
        equivalent(rows)
