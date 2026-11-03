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


@pytest.mark.parametrize("name,schema", [("bad name", "v1"), ("ok", ""), (None, "v1"), ("ok", "a" * 129)])
def test_backend_capability_identifiers_are_bounded(name, schema):
    with pytest.raises(ValueError):
        BackendCapabilities(name, schema)


@pytest.mark.parametrize("condition", ["closed", "active", "policy", "queue", "layout", "warmup", "type"])
def test_registration_rejects_unsupported_context_before_opening_resources(condition):
    from simulatte.stopping import FinitePopulation

    backend = FixedBackend()
    env = Environment(seed=0)
    env._execution_policy = Horizon(2)
    if condition == "closed":
        env.close()
    elif condition == "active":
        env.activate()
    elif condition == "policy":
        env._execution_policy = FinitePopulation(arrivals=1, safety_limit=2)
    elif condition == "queue":
        env.timeout(1)
    elif condition == "layout":
        Layout.from_dict(env, {"schema_version": "1.0", "placements": {}})
    elif condition == "warmup":
        env.configure_kpis(warmup=1)
    else:
        backend.capabilities = {"name": "not-capabilities"}
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        env.attach_execution_backend(backend)
    assert backend.opened == backend.closed == 0
    env.close()


def test_preparation_and_finalization_cannot_close_their_owner():
    class Premature(FixedBackend):
        def prepare(self, context):
            super().prepare(context)
            context.env.close()

    backend = Premature()
    with pytest.raises(RuntimeError, match="during preparation"):
        environment(backend)
    assert backend.closed == 1

    class Finalizing(FixedBackend):
        def finish(self):
            self.context.env.close()

    backend = Finalizing()
    env, _ = environment(backend)
    with pytest.raises(RuntimeError, match="during finalization"):
        execute_until(env, Horizon(2))
    env.close()
    assert backend.closed == 1


def test_capabilities_and_warmup_cannot_change_after_registration():
    backend = FixedBackend()
    env, _ = environment(backend)
    backend.capabilities = BackendCapabilities("changed", "v2")
    with pytest.raises(ValueError, match="capabilities"):
        execute_until(env, Horizon(2))
    env.close()
    backend = FixedBackend()
    env, _ = environment(backend)
    env.configure_kpis(warmup=1)
    with pytest.raises(ValueError, match="warm-up"):
        env.activate()
    env.close()


def test_failed_snapshot_close_invalidates_getters_and_still_releases_resources():
    class Broken(FixedBackend):
        def kpi_values(self):
            raise RuntimeError("snapshot unavailable")

    backend = Broken()
    env, _ = environment(backend)
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        env.close()
    assert backend.closed == 1
    binding = env._execution_backend
    for query in (binding.kpi_values, binding.finish, lambda: binding.advance(2)):
        with pytest.raises(RuntimeError):
            query()
    env.close()


def test_prepare_exception_is_preserved_even_if_cleanup_fails():
    class Broken(FixedBackend):
        def close(self):
            super().close()
            raise RuntimeError("close failed")

    backend = Broken(fail="prepare")
    with pytest.raises(RuntimeError, match="prepare failed"):
        environment(backend)
    assert backend.closed == 1


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
