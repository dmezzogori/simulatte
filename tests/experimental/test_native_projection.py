"""Provider projections match the independent frozen binary parser and authority."""

import copy
import hashlib

import pytest

from simulatte import Environment
from simulatte._wire import canonical_pack, freeze
from simulatte.experimental.native import Job, NativeProduction, NativeStream
from simulatte.stopping import Horizon, execute_until
from prototypes.native_identity_contract.projection import native_material, pack


@pytest.fixture(autouse=True)
def provider():
    return pytest.importorskip("simulatte_native")


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 2])
def test_stream_projection_matches_original_state_and_stock(retention, policy, cap):
    with NativeStream(2, retention=retention, policy=policy, wip_cap=cap, warmup=2) as stream:
        for j in range(12):
            stream.add(Job(j * 0.5, (j % 2, 1 - j % 2), (0.5, 0.25), due=j + 1))
        for at in (0, 0.5, 1, 2, 2.25, 8):
            stream.advance(at)
            raw, snapshot = stream.state_bytes(), stream.snapshot()
            view = stream.state_projection()
            assert view["schema"] == "native-stream-projection-v1"
            assert view["trajectory"] == native_material(raw, snapshot)
            assert pack(view["snapshot"]) == pack(snapshot)
            assert view["native_sha256"] == hashlib.sha256(raw).hexdigest()
            assert view["native_bytes"] == len(raw)
            assert stream.state_bytes() == raw and stream.state_projection() == view
            view["snapshot"]["scalars"].clear()
            assert pack(stream.snapshot()) == pack(snapshot)
        stream.run()
        assert stream.state_projection()["native_sha256"] == hashlib.sha256(stream.state_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="closed"):
        stream.state_projection()


def attach(env, retention="bounded", inputs=None):
    env._execution_policy = Horizon(10, warmup=2)
    env.configure_kpis(warmup=2)
    backend = NativeProduction(1, retention=retention, input_state=inputs)
    env.attach_execution_backend(backend, scope="shopfloor")
    return backend


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_managed_projection_is_detached_and_preserves_complete_authority(retention):
    state = dict(seed=1, pressure=0, next_at=0.0, live=[dict(id=0)])
    with Environment() as env:
        backend = attach(env, retention, lambda: state)
        env.activate()
        original = backend.state_digest()
        view = backend.state_projection()
        assert view["schema"] == "native-production-projection-v1"
        assert view["authority"] == dict(schema=backend.capabilities.state_schema, digest=original)
        assert hashlib.sha256(canonical_pack(freeze(view["authority_preimage"]))).hexdigest() == original
        assert view["reporting"]["configuration"] == env._execution_policy.to_dict()
        assert view["scope"] == dict(backend="native-production", scope="shopfloor", time_unit=None)
        for field in ("seed", "pressure", "next_at"):
            state[field] += 1
            changed = backend.state_projection()
            assert changed["trajectory"] == view["trajectory"] and changed["reporting"] == view["reporting"]
            assert changed["authority"] != view["authority"]
            state[field] -= 1
        view["authority_preimage"]["inputs"]["live"].clear()
        view["reporting"]["catalog"].clear()
        assert state["live"] == [dict(id=0)]
        assert backend.state_digest() == original and backend.state_projection()["reporting"]["catalog"]


def test_lifecycle_and_synchronization_guards():
    with pytest.raises(RuntimeError):
        NativeProduction(1).state_projection()
    with Environment() as env:
        backend = attach(env)
        assert backend.state_projection()["authority_preimage"]["inputs"] is None
        backend.advance(1)
        with pytest.raises(RuntimeError, match="synchronized"):
            backend.state_projection()
        # Restore the test's clock without running managed execution.
        env._now = 1
    with Environment() as env:
        backend = attach(env)
        execute_until(env, env._execution_policy)
        with pytest.raises(RuntimeError, match="finalized"):
            backend.state_projection()
    with pytest.raises(RuntimeError, match="closed"):
        backend.state_projection()


@pytest.mark.parametrize("mutation", ["inputs", "add", "advance", "run", "close"])
def test_impure_callback_cannot_return_projection(mutation):
    state = dict(value=0)
    with Environment() as env:
        backend = attach(env)
        env.activate()

        def impure():
            if mutation == "inputs":
                state["value"] += 1
            elif mutation == "add":
                backend.add(Job(0, (0,), (1,), due=2))
            elif mutation == "advance":
                backend.advance(0)
            elif mutation == "run":
                backend._stream.run()
            else:
                backend.close()
            return state

        backend._input_state = impure
        with pytest.raises((RuntimeError, ValueError)):
            backend.state_projection()
        backend._input_state = None
        if mutation == "close":
            with pytest.raises(RuntimeError, match="finalized/closed"):
                env.close()


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_legacy_provider_has_actionable_hook_error(provider, monkeypatch, retention):
    with NativeStream(1, retention=retention) as stream:
        monkeypatch.delattr(provider, "project_state")
        with pytest.raises(ImportError, match="projection"):
            stream.state_projection()
        assert stream.snapshot()  # Existing stream operations still work.


@pytest.mark.parametrize("field", ["schema", "codec", "native", "snapshot", "extra", "clock", "scalar"])
@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_invalid_binding_projection_fails_closed(monkeypatch, field, retention):
    with NativeStream(1, retention=retention) as stream:
        name = "state_projection" if retention == "full" else "retention_state_projection"
        raw = getattr(stream._module, name)(stream._engine)
        bad = copy.deepcopy(raw)
        if field in ("schema", "codec"):
            bad[field] = "unknown-v99"
        elif field == "native":
            bad["native"] = b"bad"
        elif field == "snapshot":
            bad["snapshot"] = None
        elif field == "extra":
            bad["extra"] = 1
        elif field == "clock":
            bad["snapshot"]["window"] = (0.0, float("nan"), False)
        else:
            bad["snapshot"]["scalars"]["bad"] = float("inf")
        monkeypatch.setattr(stream._module, name, lambda _: bad)
        with pytest.raises((ValueError, RuntimeError)):
            stream.state_projection()


def test_provider_history_and_feature_guards(provider, monkeypatch):
    with NativeStream(1, retention="bounded") as stream:
        for history in (None, b"", bytearray(32)):
            with pytest.raises(ValueError):
                provider.project_state(stream._engine, retention="bounded", history=history)
        with pytest.raises(ValueError):
            provider.project_state(stream._engine, retention="unknown")
        monkeypatch.delattr(stream._module, "retention_state_projection")
        with pytest.raises(ImportError, match="projection"):
            stream.state_projection()


def test_provider_response_version_guard(provider, monkeypatch):
    with NativeStream(1) as stream:
        monkeypatch.setattr(provider, "project_state", lambda *a, **kw: dict(schema="unknown"))
        with pytest.raises(RuntimeError, match="projection"):
            stream.state_projection()
