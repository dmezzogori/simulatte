"""Installed retention integration: ownership, parity, schemas and failure guards."""

import hashlib
import random
from types import SimpleNamespace

import pytest

from simulatte import Environment
from simulatte.experimental.native import ExpiredHandleError, Job, NativeProduction, NativeStream
from simulatte.stopping import Horizon, execute_until
from tests.experimental.test_native import python_kpis


@pytest.fixture
def provider():
    pytest.importorskip("simulatte_native")


@pytest.mark.parametrize("value", [None, True, 1, "compact", object()])
def test_retention_validation_before_loading(value):
    with pytest.raises(ValueError, match="retention"):
        NativeStream(1, retention=value)
    with pytest.raises(ValueError, match="retention"):
        NativeProduction(1, retention=value)


def test_old_companion_has_actionable_api_error(monkeypatch):
    from simulatte.experimental import native

    monkeypatch.setattr(native.importlib, "import_module", lambda _: SimpleNamespace(API_VERSION=1))
    with pytest.raises(ImportError, match="provider API 2"):
        NativeStream(1)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 3])
def test_bounded_parity_with_default_full(provider, seed, policy, cap):
    rng = random.Random(seed)
    with (
        NativeStream(3, policy=policy, wip_cap=cap, warmup=2) as full,
        NativeStream(3, policy=policy, wip_cap=cap, warmup=2, retention="bounded") as bounded,
    ):
        handles = []
        for i in range(40):
            job = Job(
                i // 3 * 0.5,
                tuple(rng.sample(range(3), 3)),
                tuple(rng.choice((0.25, 0.5, 1.2)) for _ in range(3)),
                rng.randrange(5, 20),
            )
            handles.append((full.add(job), bounded.add(job)))
        for t in (0, 0.5, 1, 2, 4, 8, 15, 30):
            full.advance(t)
            bounded.advance(t)
            assert full.snapshot() == bounded.snapshot()
            assert full.storage_stats()["events"] == bounded.storage_stats()["events"]
            for a, b in handles:
                expected = full.inspect(a)
                if expected["state"] == "finished":
                    with pytest.raises(ExpiredHandleError):
                        bounded.inspect(b)
                else:
                    assert bounded.inspect(b) == expected
            before = bounded.state_bytes()
            assert before.startswith(b"NRST0001")
            bounded.snapshot()
            bounded.storage_stats()
            assert bounded.state_bytes() == before
        full.run()
        bounded.run()
        assert full.result()["completed"] == 40
        with pytest.raises(RuntimeError, match="trajectories"):
            bounded.result()
        assert bounded.snapshot() == full.snapshot()
        assert bounded.storage_stats()["retained_jobs"] == bounded.storage_stats()["retained_operations"] == 0
        assert full.state_bytes().startswith(b"NSTA0001")


def test_slot_reuse_live_and_foreign_handles(provider):
    with NativeStream(3, wip_cap=2, retention="bounded") as e, NativeStream(3, retention="bounded") as other:
        live = e.add(Job(0, (0, 1), (100, 1), 200))
        expired = e.add(Job(0, (2,), (1,), 10))
        e.advance(2)
        for i in range(2, 80):
            h = e.add(Job(i, (2,), (0.25,), 100))
            assert h.id > expired.id
            e.advance(i + 1)
            assert e.inspect(live)["state"] == "active"
            with pytest.raises(ExpiredHandleError):
                e.inspect(expired)
            with pytest.raises(ValueError, match="foreign"):
                other.inspect(h)
        assert e.storage_stats()["job_capacity"] == 16
        e.run()
    with pytest.raises(RuntimeError, match="closed"):
        e.storage_stats()


def test_bounded_partitions_preserve_past_commitment(provider):
    states = []
    for split in (False, True):
        with NativeStream(1, retention="bounded") as e:
            for i in range(100):
                e.add(Job(i * 2, (0,), (0.25,), i + 100))
                if split:
                    e.advance(i * 2 + 1)
            e.advance(200)
            e.run()
            states.append(e.state_bytes())
    assert states[0] == states[1]
    with NativeStream(1, retention="bounded") as e:
        e.add(Job(0, (0,), (1,), 200))
        e.run()
        a = hashlib.sha256(e.state_bytes()).hexdigest()
    with NativeStream(1, retention="bounded") as e:
        e.add(Job(0, (0,), (1,), 201))
        e.run()
        assert hashlib.sha256(e.state_bytes()).hexdigest() != a


@pytest.mark.parametrize("horizon,warmup", [(1, 0), (4, 0), (4, 2), (20, 2)])
def test_managed_bounded_ordinary_kpis_and_cached_final_state(provider, horizon, warmup):
    jobs = (Job(0, (0, 1), (2, 1), 2), Job(0, (1, 0), (1, 2), 3), Job(1, (0, 1), (0.5, 1), 4), Job(4, (1,), (2,), 6))
    backend = NativeProduction(2, jobs, policy="spt", wip_cap=2, retention="bounded")
    assert backend.capabilities.state_schema == "native-production-bounded-v1"
    with Environment(seed=0) as env:
        env._execution_policy = Horizon(horizon, warmup=warmup)
        env.configure_kpis(warmup=warmup)
        env.enable_digest()
        env.attach_execution_backend(backend)
        assert not backend._jobs
        assert backend.storage_stats()["admitted"] == len(jobs)
        execute_until(env, env._execution_policy)
        values, digest = backend.kpi_values(), backend.state_digest()
    assert backend.kpi_values() == values and backend.state_digest() == digest
    assert {"shopfloor/" + k: v for k, v in values.items()} == python_kpis(jobs, "spt", 2, horizon, warmup)


def test_failed_post_mutation_history_update_cannot_publish_state(provider, monkeypatch):
    class Broken:
        def update(self, data):
            raise RuntimeError("history update failed")

    e = NativeStream(1, retention="bounded")
    e.add(Job(0, (0,), (1,), 3))
    monkeypatch.setattr(e, "_history", Broken())
    with pytest.raises(RuntimeError, match="history update"):
        e.advance(2)
    with pytest.raises(RuntimeError, match="closed"):
        e.state_bytes()


def test_bounded_state_codec_guard_and_sealed_admission(provider, monkeypatch):
    backend = NativeProduction(1, retention="bounded", input_state=lambda: {})
    with Environment(seed=0) as env:
        env._execution_policy = Horizon(2)
        env.attach_execution_backend(backend)
        monkeypatch.setattr(backend._stream, "state_bytes", lambda: b"bad")
        with pytest.raises(RuntimeError, match="encoding"):
            backend.state_digest()
    with NativeStream(1, retention="bounded") as e:
        e.run()
        with pytest.raises(RuntimeError):
            e.add(Job(0, (0,), (1,), 3))
