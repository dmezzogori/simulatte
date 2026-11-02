"""Production ordering parity and the remaining observation limits."""

import copy
import json
import platform
import subprocess
import sys

import pytest

from prototypes.native_engine_dynamic.production_benchmark import equivalent
from prototypes.native_engine_dynamic.scaling_benchmark import assert_row, cases, EVIDENCE_KEYS
from prototypes.native_engine_dynamic.semantics_gate import (
    BACKENDS,
    boundary,
    differences,
    event_observer,
    observers,
    run_case,
    workloads,
)
from prototypes.native_engine.workloads import generate, save


@pytest.mark.parametrize("mode", ["none", "kpi", "full"])
def test_four_job_production_equivalence(tmp_path, mode):
    rows = {
        backend: run_case(
            backend, workloads()["reduced"], tmp_path, cap=2, policy="spt", producer="independent", mode=mode
        )
        for backend in BACKENDS
    }
    assert rows["native"] == rows["direct"]
    assert rows["production"]["values"]["released"] == rows["direct"]["values"]["released"] == [5, 6, 9, 10]
    assert rows["production"]["values"]["finished"] == [9, 10, 11, 12]
    assert rows["direct"]["values"]["finished"] == [9, 10, 11, 12]
    assert differences(rows["production"], rows["direct"]) == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_strict_boundary_and_buffered_delivery(tmp_path, backend):
    result = boundary(backend, tmp_path)
    assert len(result["before_drain"]) == (1 if backend == "production" else 0)
    assert len(result["final"]) == 2


def test_payload_only_observer_preserves_results_and_wire(tmp_path):
    results = [event_observer(backend, tmp_path) for backend in BACKENDS]
    assert results[0] == results[1] == results[2]


def test_stock_collectors_require_live_actor_properties(tmp_path):
    rows = observers(tmp_path)
    assert len(rows) == 15
    assert all(row["status"] == "ok" for row in rows if row["backend"] == "production")
    assert all(
        row["status"] == "unsupported" and row["error"] == "AttributeError"
        for row in rows
        if row["backend"] != "production"
    )


def test_matrix_coverage_and_evidence_rejections(tmp_path):
    assert len(cases()) == 48 and len({tuple(sorted(row.items())) for row in cases()}) == 48
    path = tmp_path / "input.json"
    save(generate(12, 0.95), path)
    command = [
        sys.executable,
        "-m",
        "prototypes.native_engine_dynamic.production_benchmark",
        "worker",
        "--input",
        str(path),
        "--jobs",
        "12",
        "--backend",
        "direct",
        "--producer",
        "feedback",
        "--mode",
        "full",
    ]
    warmups = "1" if platform.python_implementation() == "CPython" else "7"
    row = json.loads(subprocess.check_output([*command, "--warmups", warmups], text=True))
    cold = json.loads(subprocess.check_output([*command, "--probe"], text=True))
    row.update(
        case="CPython-feedback-12-full-direct" if row["interpreter"] == "CPython" else "PyPy-feedback-12-full-direct",
        cold_sample=cold["samples"][0],
        peak_rss_bytes=cold["peak_rss_bytes"],
        cold_evidence={key: cold[key] for key in EVIDENCE_KEYS},
    )
    meta = {key: row[key] for key in ("source_sha256", "input_sha256", "libraries")}
    assert_row(row, meta)
    for key in ("source_sha256", "trace_bytes", "trajectory_sha256", "trace_wire_sha256", "counters"):
        bad = copy.deepcopy(row)
        bad[key] = "corrupt"
        with pytest.raises((AssertionError, TypeError)):
            assert_row(bad, meta)
    bad = copy.deepcopy(row)
    bad["samples"][0]["total"] += 1
    with pytest.raises(AssertionError):
        assert_row(bad, meta)
    bad = copy.deepcopy(row)
    bad["decisions_sha256"] = bad["cold_evidence"]["decisions_sha256"] = "corrupt"
    with pytest.raises(AssertionError):
        equivalent([row, bad])
