"""Static parallel slots: production oracle, retention and identity contracts."""

import hashlib
import random
import struct

import pytest

from simulatte import Environment
from simulatte.collectors import ShopFloorKPIs
from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
from simulatte.experimental.native import ExpiredHandleError, Job, NativeProduction, NativeStream
from simulatte.experimental.native_identity import compose_native_identity
from simulatte.job import ProductionJob
from simulatte.policies.conwip import ConWIP
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.stopping import Horizon


@pytest.mark.parametrize("capacities", [(), (1,), (1, 0), (1, -1), (1, True), (1, 1.5), (1, 2**32), (2**32 - 2, 2)])
@pytest.mark.parametrize("factory", [NativeStream, NativeProduction])
def test_invalid_capacity_before_optional_import(monkeypatch, capacities, factory):
    def forbidden(*args):
        raise AssertionError("loaded provider for invalid configuration")

    monkeypatch.setattr("simulatte.experimental.native.importlib.import_module", forbidden)
    with pytest.raises(ValueError, match="capacit"):
        factory(2, capacities=capacities)


class PythonPlant:
    """Independent SimPy model; timestamps and grants come from production actors."""

    def __init__(self, jobs, capacities, policy, cap, warmup):
        self.env = Environment(seed=0)
        self.env.configure_kpis(warmup=warmup)
        shop = ShopFloor(env=self.env, name="shopfloor", default_metrics=False, on_before_operation=self.start)
        psp = PreShopPool(env=self.env, shopfloor=shop)
        self.servers = [Server(env=self.env, capacity=n, shopfloor=shop) for n in capacities]
        if cap is None:
            psp.on_arrival(lambda job, pool: pool.release(job))
        else:
            ConWIP(shopfloor=shop, psp=psp, wip_cap=cap)
        self.collector = ShopFloorKPIs(shop).attach(self.env)
        self.actors, self.dispatch, self.indices, self.starts = [], [], {}, []
        self.jobs, self.cursor, self.psp = jobs, 0, psp
        self.priority = shortest_processing_time if policy == "spt" else first_come_first_served

    def start(self, job, server, op_index, processing_time):
        op = self.indices[job.id, server.id]
        self.starts[op] = self.env.now
        self.dispatch.append(op)

    def arrive(self, actor):
        self.psp.add(actor)
        if False:
            yield

    def advance(self, until):
        # Same external right-open admission boundary as ProductionStream's
        # frozen oracle: initialize all arrivals before normal events at t.
        while self.cursor < len(self.jobs) and self.jobs[self.cursor].arrival < until:
            job = self.jobs[self.cursor]
            if job.arrival > self.env.now:
                self.env.run(until=job.arrival)
            actor = ProductionJob(
                env=self.env,
                sku="capacity-oracle",
                servers=[self.servers[i] for i in job.route],
                processing_times=job.durations,
                due_date=job.due,
                priority_policy=self.priority,
            )
            offset = len(self.starts)
            self.starts.extend([None] * len(job.route))
            for i, server in enumerate(actor.routing):
                self.indices[actor.id, server.id] = offset + i
            self.actors.append(actor)
            self.cursor += 1
            self.env.process(self.arrive(actor))
        if until > self.env.now:
            self.env.run(until=until)
        return self.collector.scalars()

    def result(self):
        return dict(
            starts=self.starts,
            ends=[actor.servers_exit_at[server] for actor in self.actors for server in actor.routing],
            released=[actor.psp_exit_at for actor in self.actors],
            finished=[actor.finished_at for actor in self.actors],
            dispatch=self.dispatch,
        )


def workload(seed):
    rng = random.Random(seed)
    return tuple(
        Job(
            i // 4 * 0.5,
            tuple(rng.sample(range(3), 3)),
            tuple(rng.choice((0.25, 0.5, 1, 2)) for _ in range(3)),
            rng.randrange(2, 20),
        )
        for i in range(32)
    )


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 5])
@pytest.mark.parametrize("capacities", [(2, 1, 3), (2, 2, 2)])
def test_exact_production_trajectories_and_stock(seed, policy, cap, capacities):
    pytest.importorskip("simulatte_native")
    jobs = workload(seed)
    oracle = PythonPlant(jobs, capacities, policy, cap, 2)
    try:
        with (
            NativeStream(3, capacities=capacities, policy=policy, wip_cap=cap, warmup=2) as full,
            NativeStream(
                3, capacities=capacities, policy=policy, wip_cap=cap, warmup=2, retention="bounded"
            ) as bounded,
        ):
            handles = [(full.add(job), bounded.add(job)) for job in jobs]
            for at in (0.25, 0.5, 1, 2, 2.25, 4, 8, 20, 200):
                expected = oracle.advance(at)
                for stream in (full, bounded):
                    stream.advance(at)
                    assert stream.snapshot()["scalars"] == expected
                    raw = stream.state_bytes()
                    view = stream.state_projection()
                    assert view["native_sha256"] == hashlib.sha256(raw).hexdigest()
                    assert view["native_bytes"] == len(raw)
                    assert stream.state_bytes() == raw
                for a, b in handles:
                    state = full.inspect(a)
                    if state["state"] == "finished":
                        with pytest.raises(ExpiredHandleError):
                            bounded.inspect(b)
                    else:
                        assert bounded.inspect(b) == state
            full.run()
            bounded.run()
            for key, value in oracle.result().items():
                assert full.result()[key] == value, key
            assert bounded.snapshot() == full.snapshot()
            assert bounded.storage_stats()["retained_jobs"] == 0
    finally:
        oracle.env.close()


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_parallel_slots_boundary_and_utilization(retention):
    pytest.importorskip("simulatte_native")
    with NativeStream(1, capacities=(2,), wip_cap=None, retention=retention) as stream:
        handles = [stream.add(Job(0, (0,), (1,), 2)) for _ in range(3)]
        stream.advance(1)
        assert all(stream.inspect(h)["finished"] is None for h in handles)
        assert stream.snapshot()["scalars"]["shopfloor/utilization"] == 1
        stream.advance(2)
        assert stream.snapshot()["scalars"]["shopfloor/utilization"] == 0.75
        stream.run()
        assert stream.snapshot()["scalars"]["shopfloor/throughput"] == 1.5


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_unit_capacities_keep_legacy_commitments(retention):
    pytest.importorskip("simulatte_native")
    with (
        NativeStream(3, retention=retention) as default,
        NativeStream(3, capacities=(1, 1, 1), retention=retention) as explicit,
    ):
        for job in workload(0):
            default.add(job)
            explicit.add(job)
        for at in (0.5, 4, 200):
            default.advance(at)
            explicit.advance(at)
            assert default.state_bytes() == explicit.state_bytes()
            assert default.state_projection() == explicit.state_projection()
        assert default.state_bytes().startswith(b"NRST0001" if retention == "bounded" else b"NSTA0001")


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_capacity_commitments_and_detached_configuration(retention):
    pytest.importorskip("simulatte_native")
    results = []
    for capacities in ([2, 1], [1, 2], [1, 1]):
        with Environment() as env:
            env._execution_policy = Horizon(10)
            backend = NativeProduction(2, capacities=capacities, retention=retention)
            env.attach_execution_backend(backend)
            copied = tuple(capacities)
            capacities.clear()
            assert backend.capacities == copied
            view = backend.state_projection()
            assert view["authority"]["digest"] == backend.state_digest()
            identity = compose_native_identity(view, actions={})
            results.append((backend.capabilities.state_schema, identity.to_dict(), view["trajectory"]))
    assert results[0][0] == results[1][0] != results[2][0]
    assert results[0][1] != results[1][1]
    assert results[0][2] != results[1][2]
    assert results[0][2]["codec"].startswith("NBST0002" if retention == "bounded" else "NSTA0002")


def test_capacity_feature_guard_keeps_old_unit_provider_usable(monkeypatch):
    provider = pytest.importorskip("simulatte_native")
    monkeypatch.delattr(provider, "CAPACITY_VERSION", raising=False)
    with NativeStream(2, capacities=(1, 1)):
        pass
    with pytest.raises(ImportError, match="capacit"):
        NativeStream(2, capacities=(2, 1))


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("capacity", [300, 2**32 - 2])
def test_large_capacity_counts_and_serialization(retention, capacity):
    pytest.importorskip("simulatte_native")
    with NativeStream(1, capacities=(capacity,), wip_cap=None, retention=retention) as stream:
        for _ in range(300):
            stream.add(Job(0, (0,), (1,), 2))
        stream.advance(0.5)
        raw = stream.state_bytes()
        if retention == "bounded":
            # Wrapper is 8-byte magic + 32-byte history + 8-byte raw length.
            raw = raw[48:]
            assert raw[:8] == b"NBST0002"
            assert struct.unpack_from("<II", raw, 100) == (capacity, 300)
        else:
            assert raw[:8] == b"NSTA0002"
            assert struct.unpack_from("<I", raw, 108)[0] == capacity
            assert struct.unpack_from("<I", raw, 120)[0] == 300
        assert stream.snapshot()["scalars"]["shopfloor/utilization"] == 300 / capacity
        stream.run()
        assert stream.storage_stats()["completed"] == 300


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_low_level_configuration_boundary_and_validation(retention):
    provider = pytest.importorskip("simulatte_native")
    module = provider.load()
    configure = module.retention_configure_capacities if retention == "bounded" else module.configure_capacities
    for values in ([2, 1], (), (2, True), (2, 0), (2**32 - 2, 2)):
        with NativeStream(2, retention=retention) as stream:
            with pytest.raises((TypeError, ValueError)):
                configure(stream._engine, values)
            # Pure Python-boundary validation has not poisoned native ownership.
            assert stream.now == 0
    for kind in ("admission", "advance", "compacted"):
        with NativeStream(2, retention=retention) as stream:
            if kind != "advance":
                stream.add(Job(0, (0,), (1,), 2))
            stream.advance(2 if kind == "compacted" else 0.5 if kind == "advance" else 0)
            with pytest.raises(ValueError, match="capacity configuration"):
                configure(stream._engine, (2, 1))
            with pytest.raises(RuntimeError, match="failed"):
                stream.snapshot()


@pytest.mark.parametrize("policy", ["fifo", "spt"])
def test_repeated_dynamic_admission_bounds_retained_storage(policy):
    pytest.importorskip("simulatte_native")
    capacities = [3, 1, 2]
    with (
        NativeStream(3, capacities=capacities, wip_cap=5, policy=policy) as full,
        NativeStream(3, capacities=capacities, wip_cap=5, policy=policy, retention="bounded") as bounded,
    ):
        capacities.clear()
        samples = []
        for batch in range(128):
            for i in range(16):
                job = Job(batch * 32, (i % 3, (i + 1) % 3), (0.5, 0.25), batch * 32 + 6)
                full.add(job)
                bounded.add(job)
            for stream in (full, bounded):
                stream.advance(batch * 32 + 32)
            assert full.snapshot() == bounded.snapshot()
            assert bounded.capacities == (3, 1, 2)
            samples.append(bounded.storage_stats())
        assert all(s["retained_jobs"] == s["retained_operations"] == 0 for s in samples)
        assert len({s["native_bytes"] for s in samples[8:]}) == 1
        assert full.storage_stats()["native_bytes"] > samples[-1]["native_bytes"] * 10
        full.run()
        bounded.run()
        assert bounded.storage_stats()["completed"] == 2048
        assert bounded.snapshot() == full.snapshot()
