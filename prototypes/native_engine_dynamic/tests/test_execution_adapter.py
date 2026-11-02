"""Restricted research integration with the real execution environment."""

import pytest

from simulatte import Environment, TraceRecorder, Trace
from simulatte.layout import Layout
from simulatte.stopping import Horizon, FinitePopulation, execute_until
from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine_dynamic.execution_adapter import ExecutionAdapter, Parameters


def run_slice(backend, policy, data, stopping, path=None):
    env = Environment(seed=42)
    env._execution_policy = stopping
    env.configure_kpis(warmup=stopping.warmup)
    recorder = TraceRecorder(env, path, level="kpi") if path else None
    layout = Layout.from_dict(env, {"schema_version": "1.0"})
    adapter = ExecutionAdapter(env, layout, Parameters(backend=backend, policy=policy, servers=data.servers), data)
    try:
        assert adapter.scalars()["scalars"] == {}
        execute_until(env, stopping)
        result = adapter.scalars()
        before = adapter.stream.now
        assert adapter.scalars() == result and adapter.stream.now == before
        states = adapter.states()
        assert len(states) == sum(job.arrival < stopping.until for job in data.jobs)
        if recorder:
            recorder.close(termination="horizon")
        env.close()
        assert adapter.closed and adapter.scalars() == result
        assert env.fingerprint().kpis == result["scalars"]
        return result, states
    finally:
        env.close()


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("stopping", [Horizon(0.5), Horizon(2), Horizon(3, warmup=1), Horizon(20, warmup=2)])
def test_partial_horizon_warmup_and_idle_tail(policy, stopping, tmp_path):
    data = Workload(2, (Job(0, (0, 1), (1, 2), 2), Job(0, (1,), (2,), 3), Job(3, (0,), (1,), 5)))
    expected = None
    for backend in ("production", "native"):
        path = tmp_path / f"{backend}.simtrace"
        result = run_slice(backend, policy, data, stopping, path)
        assert expected is None or result == expected
        expected = result
        trace = Trace.open(path)
        trace.check()
        assert trace.kpis() == result[0]["scalars"]
        assert trace.verify() == "not_verifiable"
        assert result[0]["window"] == (stopping.warmup, stopping.until, False)


def test_invalid_features_rejected_before_backend_construction(tmp_path, monkeypatch):
    from prototypes.native_engine_dynamic import execution_adapter as module

    def forbidden(*args, **kwargs):
        raise AssertionError("constructed backend before validation")

    monkeypatch.setitem(module.FACTORIES, "native", forbidden)
    for feature in ("full", "finite_population", "layout"):
        env = Environment(seed=0)
        env._execution_policy = (
            FinitePopulation(safety_limit=10, arrivals=1) if feature == "finite_population" else Horizon(5)
        )
        recorder = TraceRecorder(env, tmp_path / f"{feature}.simtrace", level="full") if feature == "full" else None
        layout = Layout.from_dict(env, {"schema_version": "1.0", **({"network": {}} if feature == "layout" else {})})
        try:
            with pytest.raises(ValueError, match="unsupported"):
                ExecutionAdapter(env, layout, Parameters(), Workload(10, ()))
        finally:
            if recorder:
                recorder.close(outcome="failed")
            env.close()


def test_close_without_running_releases_native_state():
    env = Environment(seed=0)
    env._execution_policy = Horizon(5)
    adapter = ExecutionAdapter(env, Layout.from_dict(env, {"schema_version": "1.0"}), Parameters(), Workload(10, ()))
    env.close()
    env.close()
    assert adapter.closed
    with pytest.raises(RuntimeError):
        adapter.stream.snapshot()


def test_managed_build_guard_and_ready_cancellation_release_native_state():
    env = Environment(seed=0)
    env._execution_policy = Horizon(5)
    layout = Layout.from_dict(env, {"schema_version": "1.0"})
    with env._building():
        adapter = ExecutionAdapter(env, layout, Parameters(), Workload(10, ()))
        with pytest.raises(RuntimeError, match="build"):
            env.step()

    def cancelled():
        from simulatte.execution.control import Cancelled

        raise Cancelled("test cancellation at ready")

    env._on_ready(cancelled)
    with pytest.raises(Exception, match="test cancellation"):
        execute_until(env, Horizon(5))
    env.close()
    assert adapter.closed and adapter.steps == 0
    with pytest.raises(RuntimeError):
        adapter.stream.snapshot()


def test_backend_error_releases_native_state(monkeypatch):
    env = Environment(seed=0)
    env._execution_policy = Horizon(5)
    adapter = ExecutionAdapter(env, Layout.from_dict(env, {"schema_version": "1.0"}), Parameters(), Workload(10, ()))

    def failed(until):
        raise RuntimeError("backend failure")

    monkeypatch.setattr(adapter.stream, "advance", failed)
    with pytest.raises(RuntimeError, match="backend failure"):
        execute_until(env, Horizon(5))
    env.close()
    assert adapter.closed and env._run_failed


def test_scalar_reads_never_advance_backend_and_reject_unsynchronized_clock():
    env = Environment(seed=0)
    env._execution_policy = Horizon(5)
    adapter = ExecutionAdapter(env, Layout.from_dict(env, {"schema_version": "1.0"}), Parameters(), Workload(10, ()))
    adapter.stream.advance(1)
    with pytest.raises(RuntimeError, match="synchronized"):
        adapter.scalars()
    with pytest.raises(RuntimeError):
        env.close()
    assert adapter.closed


def test_relocatable_assets_and_corruption_are_checked(tmp_path):
    import json
    import subprocess
    import sys
    from prototypes.native_engine_dynamic.execution_project import create_project

    original = create_project(tmp_path / "original")
    moved = tmp_path / "moved"
    original.rename(moved)
    script = (
        "from prototypes.native_engine_dynamic.execution_model import verify_assets; verify_assets(); "
        "from prototypes.native_engine_dynamic.native_kpis import NativeKPIStream; "
        "s=NativeKPIStream(1); s.run(); print(s.snapshot()); s.close()"
    )
    completed = subprocess.run([sys.executable, "-c", script], cwd=moved, capture_output=True, text=True, check=True)
    assert "scalars" in completed.stdout
    manifest = json.loads((moved / "native-assets.json").read_text())
    extension = moved / next(p for p in manifest["files"] if "_nd_scalar." in p)
    with extension.open("ab") as target:
        target.write(b"corruption-probe")
    failed = subprocess.run([sys.executable, "-c", script], cwd=moved, capture_output=True, text=True)
    assert failed.returncode != 0 and "integrity check failed" in failed.stderr


def test_result_artifacts_exclude_volatile_timings_and_reproduce_exactly(tmp_path):
    import json

    expected = None
    for index in range(2):
        root = tmp_path / str(index)
        root.mkdir()
        env = Environment(seed=0, artifacts_dir=root)
        env._execution_policy = Horizon(5)
        ExecutionAdapter(env, Layout.from_dict(env, {"schema_version": "1.0"}), Parameters(), Workload(10, ()))
        execute_until(env, Horizon(5))
        env.close()
        artifact = (root / "native-scalar-result.json").read_bytes()
        assert "times" not in json.loads(artifact)
        assert expected is None or expected == artifact
        expected = artifact
