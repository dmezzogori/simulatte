import json
import subprocess
import sys

import pytest

from prototypes.native_engine.benchmark import assert_equivalent
from prototypes.native_engine.workloads import generate, save


def test_subprocess_equivalent_work(tmp_path):
    path = tmp_path / "input.json"
    save(generate(20, 0.95), path)
    rows = []
    for backend in ("simulatte", "reference", "native"):
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "prototypes.native_engine.benchmark",
                "worker",
                "--input",
                str(path),
                "--backend",
                backend,
                "--mode",
                "record",
                "--hook",
                "python",
                "--warmups",
                "0",
                "--repeats",
                "1",
                "--probe",
            ],
            text=True,
        )
        row = json.loads(output)
        assert row["counters"]["callbacks"] == row["operations"]
        assert row["recording_bytes"] == 8 + 32 * (40 + 3 * row["operations"])
        assert row["peak_rss_bytes"] > 0
        assert all(value >= 0 for value in row["samples"][0].values())
        rows.append(row)
    assert_equivalent(rows)
    rows[-1]["trajectory_sha256"] = "wrong"
    with pytest.raises(AssertionError, match="equivalence failure"):
        assert_equivalent(rows)
