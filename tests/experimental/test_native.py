"""Optional native API: validated inputs, ownership and managed state contracts."""

from dataclasses import FrozenInstanceError
import importlib

import pytest

from simulatte.experimental.native import Job, NativeProduction, NativeStream
from simulatte import Environment, TraceRecorder
from simulatte.stopping import Horizon, execute_until


@pytest.mark.parametrize(
    "changes",
    [
        dict(arrival=-1),
        dict(due=float("nan")),
        dict(route=(0, 0)),
        dict(durations=(0,)),
        dict(route=(True,)),
        dict(durations=(True,)),
    ],
)
def test_invalid_jobs(changes):
    values = dict(arrival=0, route=(0,), durations=(1,), due=10)
    with pytest.raises((ValueError, TypeError)):
        Job(**(values | changes))


def test_job_is_detached_and_immutable():
    route, times = [0, 1], [2, 3]
    job = Job(0, route, times, 10)
    route.clear()
    times.clear()
    assert job.route == (0, 1) and job.durations == (2, 3)
    with pytest.raises(FrozenInstanceError):
        job.due = 1


@pytest.fixture
def native():
    pytest.importorskip("simulatte_native")


def test_direct_example_and_handle_ownership(native):
    with NativeStream(servers=2, wip_cap=2, policy="spt") as engine:
        with pytest.raises(TypeError, match="validated"):
            engine.add(object())
        first = engine.add(Job(0, (0, 1), (2, 3), 10))
        engine.add(Job(1, (1, 0), (1, 2), 10))
        with NativeStream(servers=2) as other:
            with pytest.raises(ValueError, match="foreign"):
                other.inspect(first)
        engine.run()
        assert engine.result()["completed"] == 2
        snapshot = engine.snapshot()
        assert engine.inspect(first)["finished"] == 5
        assert snapshot["scalars"]["shopfloor/utilization"] == 0.8
        assert snapshot["scalars"]["shopfloor/throughput"] == 0.4
        with pytest.raises(RuntimeError):
            engine.add(Job(6, (0,), (1,), 10))
    engine.close()
    assert snapshot["scalars"]["shopfloor/jobs_in_system"] == 1.6
    with pytest.raises(RuntimeError, match="closed"):
        engine.snapshot()


def test_advance_is_right_open_and_queries_do_not_advance(native):
    with NativeStream(servers=1, warmup=0.5) as engine:
        handle = engine.add(Job(0, (0,), (1,), 10))
        engine.advance(1)
        before = engine.state_bytes()
        assert engine.inspect(handle)["finished"] is None
        assert engine.snapshot() == engine.snapshot()
        assert engine.state_bytes() == before and engine.now == 1
        engine.advance(2)
        assert engine.inspect(handle)["finished"] == 1


def test_managed_dynamic_admission_requires_input_state():
    backend = NativeProduction(servers=1)
    with pytest.raises(RuntimeError, match="prepared"):
        backend.add(Job(0, (0,), (1,), 10))
    with pytest.raises(RuntimeError):
        backend.state_digest()
    backend.close()
    backend.close()


@pytest.mark.parametrize("changes", [dict(servers=True), dict(servers=0), dict(policy="edd"), dict(wip_cap=0)])
def test_invalid_stream_configuration_before_loading(changes):
    with pytest.raises((TypeError, ValueError)):
        NativeStream(**(dict(servers=1) | changes))


@pytest.mark.parametrize(
    "value", [True, float("inf"), 10**10000, object(), -1], ids=["bool", "infinity", "overflow", "object", "negative"]
)
def test_invalid_warmup_before_optional_import(value):
    with pytest.raises(ValueError):
        NativeStream(1, warmup=value)


@pytest.mark.parametrize("missing", ["simulatte_native", "inner_dependency"])
def test_missing_provider_explains_install_without_hiding_import_errors(monkeypatch, missing):
    original = importlib.import_module

    def absent(name):
        if name == "simulatte_native":
            raise ModuleNotFoundError(name=missing)
        return original(name)

    monkeypatch.setattr(importlib, "import_module", absent)
    with pytest.raises(ImportError, match="simulatte\\[native\\]" if missing == "simulatte_native" else None) as caught:
        NativeStream(1)
    if missing != "simulatte_native":
        assert caught.value.name == missing


@pytest.mark.parametrize(
    "jobs,callback",
    [
        ([object()], None),
        ([Job(0, (2,), (1,), 1)], None),
        ([Job(1, (0,), (1,), 1), Job(0, (0,), (1,), 1)], None),
        ([], 1),
    ],
)
def test_managed_input_validation(jobs, callback):
    with pytest.raises((TypeError, ValueError)):
        NativeProduction(1, jobs, input_state=callback)


def managed(backend, *, horizon=10, warmup=0, path=None):
    env = Environment(seed=0)
    env._execution_policy = Horizon(horizon, warmup=warmup)
    env.configure_kpis(warmup=warmup)
    recorder = TraceRecorder(env, path, level="kpi") if path else None
    env.attach_execution_backend(backend)
    return env, recorder


def python_kpis(jobs, policy, cap, until, warmup):
    from simulatte.collectors import ShopFloorKPIs
    from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
    from simulatte.job import ProductionJob
    from simulatte.policies.conwip import ConWIP
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor

    env = Environment(seed=0)
    env.configure_kpis(warmup=warmup)
    shop = ShopFloor(env=env, name="shopfloor", default_metrics=False)
    psp = PreShopPool(env=env, shopfloor=shop)
    servers = [Server(env=env, capacity=1, shopfloor=shop) for _ in range(2)]
    ConWIP(shopfloor=shop, psp=psp, wip_cap=cap)
    collector = ShopFloorKPIs(shop).attach(env)

    def arrivals():
        for job in jobs:
            yield env.timeout(job.arrival - env.now)
            actor = ProductionJob(
                env=env,
                sku="native-oracle",
                servers=[servers[i] for i in job.route],
                processing_times=job.durations,
                due_date=job.due,
                priority_policy=shortest_processing_time if policy == "spt" else first_come_first_served,
            )
            psp.add(actor)

    env.process(arrivals())
    env.run(until=until)
    result = collector.scalars()
    env.close()
    return result


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [1, 2])
@pytest.mark.parametrize("horizon,warmup", [(1, 0), (4, 0), (4, 2), (20, 2)])
def test_packaged_backend_exactly_matches_production_kpis(native, policy, cap, horizon, warmup):
    jobs = (Job(0, (0, 1), (2, 1), 2), Job(0, (1, 0), (1, 2), 3), Job(1, (0, 1), (0.5, 1), 4), Job(4, (1,), (2,), 6))
    backend = NativeProduction(2, jobs, policy=policy, wip_cap=cap)
    env, _ = managed(backend, horizon=horizon, warmup=warmup)
    try:
        initial = backend.state_digest()
        assert initial == backend.state_digest()
        execute_until(env, env._execution_policy)
        values = {"shopfloor/" + name: value for name, value in backend.kpi_values().items()}
        final = backend.state_digest()
        with pytest.raises(RuntimeError):
            backend.advance(horizon)
    finally:
        env.close()
    assert backend.state_digest() == final and final != initial
    assert values == python_kpis(jobs, policy, cap, horizon, warmup)


def test_future_python_state_changes_fingerprint_with_equal_current_kpis(native, tmp_path):
    fingerprints = []
    for index in (1, 2):
        backend = NativeProduction(1, input_state=lambda: {"future": index})
        env, recorder = managed(backend, horizon=2, path=tmp_path / str(index))
        assert backend.kpi_values() == backend.kpi_values()
        execute_until(env, env._execution_policy)
        fingerprints.append(env.fingerprint())
        recorder.close(termination="horizon")
        env.close()
        env.close()
    assert fingerprints[0].kpis == fingerprints[1].kpis
    assert fingerprints[0].digest != fingerprints[1].digest


def test_dynamic_handles_and_boundary_synchronization(native):
    pending = [Job(0, (0,), (1,), 2), Job(1, (0,), (1,), 3)]
    backend = NativeProduction(1, input_state=lambda: {"remaining": len(pending)})
    env, _ = managed(backend, horizon=3)
    observed = []

    def arrivals():
        while pending:
            job = pending.pop(0)
            yield env.timeout(job.arrival - env.now)
            handle = backend.add(job)
            observed.append(backend.inspect(handle)["state"])

    env.process(arrivals())
    execute_until(env, env._execution_policy)
    assert observed == ["scheduled", "scheduled"]
    env.close()
    other = NativeProduction(1)
    env, _ = managed(other)
    with pytest.raises(ValueError, match="input_state"):
        other.add(Job(0, (0,), (1,), 2))
    other._stream.advance(1)
    with pytest.raises(RuntimeError, match="synchronized"):
        other.kpi_values()
    with pytest.raises(RuntimeError, match="synchronized"):
        env.close()


def test_partial_stock_preparation_failure_closes_capsule(native, monkeypatch):
    import simulatte_native

    raw = simulatte_native.load()
    close, closed = raw.close, []

    def record(engine):
        closed.append(engine)
        close(engine)

    def fail(*_):
        raise RuntimeError("stock preparation failed")

    monkeypatch.setattr(raw, "close", record)
    monkeypatch.setattr(raw, "enable_stock_kpis", fail)
    with pytest.raises(RuntimeError, match="stock preparation"):
        NativeStream(1)
    assert len(closed) == 1


def test_state_encoding_and_single_use_guards(native, monkeypatch):
    backend = NativeProduction(1)
    env, _ = managed(backend)
    with pytest.raises(RuntimeError, match="single-use"):
        backend.prepare(backend._context)
    monkeypatch.setattr(backend._stream, "state_bytes", lambda: b"unknown-state-codec")
    with pytest.raises(RuntimeError, match="encoding"):
        backend.state_digest()
    env.close()
