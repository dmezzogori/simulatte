"""Equivalence gate for the actual production actors and the two recorder levels."""

import json
import platform
import subprocess
import sys

import pytest

from prototypes.native_engine.model import Workload, Job
from prototypes.native_engine.workloads import generate, save
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.feedback import feed, validate_feed
from prototypes.native_engine_dynamic.production import ProductionStream
from prototypes.native_engine_dynamic.production_benchmark import equivalent, validate_output
from prototypes.native_engine_dynamic.studio_trace import TraceStream
from prototypes.native_engine_dynamic.validation import fingerprint, validate_result


@pytest.mark.parametrize("producer", ["independent", "feedback"])
def test_production_trajectory_and_real_trace_levels(tmp_path, producer):
    templates = generate(100, 0.95)
    expected = None
    series = None
    for backend in ("native", "production"):
        for mode in ("none", "kpi", "full"):
            path = tmp_path / f"{backend}-{mode}.simtrace"
            if backend == "production":
                stream = ProductionStream(templates.servers, mode=mode, trace_path=path)
            elif mode == "none":
                stream = CompiledStream(templates.servers, wip_cap=12, policy="spt")
            else:
                stream = TraceStream(
                    CompiledStream,
                    templates.servers,
                    wip_cap=12,
                    policy="spt",
                    level=mode,
                    trace_path=path,
                    raw_path=tmp_path / "source",
                )
            try:
                jobs, decisions = feed(stream, templates, producer)
                stream.run()
                result = stream.result()
            finally:
                stream.close()
            validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
            if producer == "feedback":
                validate_feed(templates, jobs, decisions, result)
            actual = (fingerprint(result), jobs, decisions)
            expected = actual if expected is None else expected
            assert actual == expected
            if mode != "none":
                sig = validate_output(path, jobs, result, mode=mode, backend=backend)
                series = sig["series_values_sha256"] if series is None else series
                assert sig["series_values_sha256"] == series
                operations = sum(len(j.route) for j in jobs)
                if mode == "full":
                    assert sig["domain_events"] == 7 * len(jobs) + 7 * operations
                    assert sig["observer_events"] == 4 * len(jobs) + (0 if backend == "production" else 2)


def test_equal_time_arrivals_and_completion_boundaries(tmp_path):
    templates = Workload(1, (Job(0, (0,), (2,), 10), Job(0, (0,), (1,), 10), Job(2, (0,), (1,), 10)))
    rows = []
    for cls in (ProductionStream, CompiledStream):
        s = cls(1, wip_cap=1, policy="spt")
        a = s.add(templates.jobs[0])
        b = s.add(templates.jobs[1])
        assert s.inspect(a)["state"] == s.inspect(b)["state"] == "scheduled"
        s.advance(2)
        assert s.inspect(a)["state"] == "active" and s.inspect(b)["state"] == "pool"
        s.add(templates.jobs[2])
        s.run()
        rows.append(s.result())
        s.close()
    assert fingerprint(rows[0]) == fingerprint(rows[1])


def test_workers_and_equivalence_reject_changed_feedback(tmp_path):
    path = tmp_path / "input.json"
    save(generate(20, 0.95), path)
    rows = []
    for backend in ("production", "native"):
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.production_benchmark",
            "worker",
            "--input",
            str(path),
            "--jobs",
            "20",
            "--backend",
            backend,
            "--producer",
            "feedback",
            "--mode",
            "kpi",
            "--probe",
        ]
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["peak_rss_bytes"] > 0 and row["warmups"] == [] and len(row["samples"]) == 1
        assert row["trace_signature"]["chunks"] == 0
        assert row["samples"][0]["process_cpu"] > 0
        rows.append(row)
    equivalent(rows)
    rows[0]["decisions_sha256"] = "corrupt"
    with pytest.raises(AssertionError):
        equivalent(rows)


@pytest.mark.skipif(platform.python_implementation() != "CPython", reason="CPython CPU diagnostic")
def test_cpu_profile_covers_both_threads(tmp_path):
    path = tmp_path / "input.json"
    save(generate(10, 0.95), path)
    out = tmp_path / "profile.json"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.production_benchmark",
            "profile",
            "--input",
            str(path),
            "--jobs",
            "10",
            "--backend",
            "production",
            "--producer",
            "independent",
            "--mode",
            "full",
            "--output",
            str(out),
        ]
    )
    row = json.loads(out.read_text())
    assert row["clock"] == "time.thread_time" and len(row["writers"]) == 1
    assert row["writers"][0]["total_self_cpu"] > 0
    assert row["writers"][0]["total_self_cpu"] <= row["writer_pass_timing"]["process_cpu"] * 1.05
    assert row["simulation"]["total_self_cpu"] <= row["timing"]["process_cpu"] * 1.05
    assert row["spans"]["file_write"]["calls"] > 0 and row["spans"]["compress"]["calls"] > 0
    assert row["snapshots"] and all(s["bytes"] > 0 for s in row["snapshots"])
    assert any(r["name"] == "_write_batch" for r in row["writers"][0]["functions"])
    assert any(r["name"] == "main" and r["file"].endswith("shopfloor.py") for r in row["simulation"]["functions"])
