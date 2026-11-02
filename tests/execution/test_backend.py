"""Opt-in backend lifecycle, projection fingerprints and resource ownership."""

import hashlib

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.execution.backend import BackendCapabilities, ExecutionBackend
from simulatte.layout import Layout
from simulatte.stopping import Horizon, execute_until
from simulatte.kpi import KPI


class FixedBackend(ExecutionBackend):
    capabilities = BackendCapabilities("test-engine", "test-engine-state-v1")
    kpis = (KPI("constant", unit="jobs"),)

    def __init__(self, hidden=0, *, fail=None):
        self.hidden, self.fail = hidden, fail
        self.opened = self.advances = self.finished = self.closed = 0

    def prepare(self, context):
        self.context = context
        self.opened += 1
        if self.fail == "prepare":
            raise RuntimeError("prepare failed")

    def advance(self, until):
        self.advances += 1
        if self.fail == "advance":
            raise RuntimeError("advance failed")

    def kpi_values(self):
        return {"constant": 1.0}

    def state_digest(self):
        return hashlib.sha256(str(self.hidden).encode()).hexdigest()

    def finish(self):
        self.finished += 1
        if self.fail == "finish":
            raise RuntimeError("finish failed")

    def close(self):
        self.closed += 1


def environment(backend, *, path=None):
    env = Environment(seed=0)
    env._execution_policy = Horizon(2)
    Layout.from_dict(env, {"schema_version": "1.0"})
    recorder = TraceRecorder(env, path, level="kpi") if path else None
    env.attach_execution_backend(backend, scope="plant")
    return env, recorder


def test_state_hash_changes_normal_fingerprint_with_equal_kpis(tmp_path):
    results = []
    for hidden in (1, 2):
        path = tmp_path / f"{hidden}.simtrace"
        backend = FixedBackend(hidden)
        env, recorder = environment(backend, path=path)
        assert "step" not in env.__dict__ and "close" not in env.__dict__
        execute_until(env, Horizon(2))
        assert backend.finished == backend.advances == 1
        fingerprint = env.fingerprint()
        recorder.close(termination="horizon")
        env.close()
        env.close()
        assert backend.closed == 1
        assert env.fingerprint() == fingerprint
        trace = Trace.open(path)
        trace.check()
        assert trace.fingerprint == fingerprint
        assert trace.verify() == "not_verifiable"
        results.append(fingerprint)
    assert results[0].kpis == results[1].kpis == {"plant/constant": 1.0}
    assert results[0].digest != results[1].digest


@pytest.mark.parametrize("failure", ["prepare", "advance", "finish"])
def test_failure_always_closes_backend_once(failure):
    backend = FixedBackend(fail=failure)
    env = Environment(seed=0)
    env._execution_policy = Horizon(2)
    try:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            env.attach_execution_backend(backend)
            execute_until(env, Horizon(2))
    finally:
        env.close()
        env.close()
    assert backend.closed == 1


def test_validation_precedes_prepare_and_no_ordinary_run(tmp_path):
    backend = FixedBackend()
    env = Environment(seed=0)
    env._execution_policy = Horizon(2)
    recorder = TraceRecorder(env, tmp_path / "full.simtrace", level="full")
    with pytest.raises(ValueError, match="unsupported"):
        env.attach_execution_backend(backend)
    assert backend.opened == backend.closed == 0
    recorder.close(outcome="failed")
    env.close()
    env, _ = environment(backend)
    try:
        with pytest.raises(RuntimeError, match="managed"):
            env.run(until=2)
        with pytest.raises(ValueError, match="stopping"):
            execute_until(env, Horizon(3))
    finally:
        env.close()


def test_ready_cancellation_does_not_advance_backend():
    backend = FixedBackend()
    env, _ = environment(backend)

    def cancel():
        raise RuntimeError("ready cancelled")

    env._on_ready(cancel)
    try:
        with pytest.raises(RuntimeError, match="ready cancelled"):
            execute_until(env, Horizon(2))
    finally:
        env.close()
    assert backend.advances == backend.finished == 0 and backend.closed == 1


def test_final_state_event_covers_change_after_identical_initial_state(tmp_path):
    initial, final = [], []
    for value in (1, 2):

        class Changing(FixedBackend):
            def advance(self, until):
                super().advance(until)
                self.hidden = value

        backend = Changing(0)
        env, recorder = environment(backend, path=tmp_path / str(value))
        # Initial capture and final capture must both enter the projection.
        env.request_projection(lambda state: initial.append(env.fingerprint().digest))
        execute_until(env, Horizon(2))
        final.append(env.fingerprint().digest)
        env.close()
    assert initial[0] == initial[1] and final[0] != final[1]


def test_recorder_failure_still_closes_backend_once():
    backend = FixedBackend()
    env, _ = environment(backend)

    class BrokenRecorder:
        def close(self):
            raise OSError("recorder failed")

    env._recorders.append(BrokenRecorder())
    with pytest.raises(OSError, match="recorder failed"):
        env.close()
    env.close()
    assert backend.closed == 1


def test_invalid_hash_and_duplicate_registration_are_rejected():
    backend = FixedBackend()
    env, _ = environment(backend)
    try:
        with pytest.raises(RuntimeError):
            env.attach_execution_backend(FixedBackend())
        backend.state_digest = lambda: "bad-hash"
        with pytest.raises(ValueError, match="SHA-256"):
            execute_until(env, Horizon(2))
    finally:
        env.close()
    assert backend.closed == 1


def test_backend_instance_cannot_be_shared_or_reused_after_close():
    backend = FixedBackend()
    first, _ = environment(backend)
    second = Environment(seed=0)
    second._execution_policy = Horizon(2)
    try:
        with pytest.raises(RuntimeError, match="single-use"):
            second.attach_execution_backend(backend)
        assert backend.opened == 1
    finally:
        first.close()
    with pytest.raises(RuntimeError, match="single-use"):
        second.attach_execution_backend(backend)
    second.close()
    assert backend.closed == 1
