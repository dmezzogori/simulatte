"""Retention must be observational: compare actual unchanged packaged execution."""

from concurrent.futures import ThreadPoolExecutor
import random

import pytest

from simulatte.experimental.native import Job, NativeStream
from prototypes.native_engine_streaming.stream import ExpiredHandleError, Stream


def jobs(seed, n=45):
    rng = random.Random(seed)
    return [
        Job(
            i // 3 * 0.5,
            tuple(rng.sample(range(3), 3)),
            tuple(rng.choice((0.25, 0.5, 1.2)) for _ in range(3)),
            rng.choice((3, 5, 20)),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 3])
@pytest.mark.parametrize("warmup", [0, 2.5])
def test_exact_packaged_kpis_and_retention_independent_state(seed, policy, cap, warmup):
    with (
        Stream(3, policy=policy, wip_cap=cap, warmup=warmup) as full,
        Stream(3, policy=policy, wip_cap=cap, warmup=warmup, retention="bounded") as bounded,
        NativeStream(3, policy=policy, wip_cap=cap, warmup=warmup) as packaged,
    ):
        handles = [(full.add(j), bounded.add(j), packaged.add(j)) for j in jobs(seed)]
        for t in (0, 0.25, 1, 2.5, 3, 8, 12, 20):
            for stream in (full, bounded, packaged):
                stream.advance(t)
            assert full.snapshot() == bounded.snapshot() == packaged.snapshot()
            assert full.state_bytes() == bounded.state_bytes()
            assert full.history_digest == bounded.history_digest
            for a, b, c in handles:
                expected = packaged.inspect(c)
                assert full.inspect(a) == expected
                if expected["state"] == "finished":
                    with pytest.raises(ExpiredHandleError):
                        bounded.inspect(b)
                else:
                    assert bounded.inspect(b) == expected
        for stream in (full, bounded, packaged):
            stream.run()
        assert full.snapshot() == bounded.snapshot() == packaged.snapshot()
        assert full.state_bytes() == bounded.state_bytes()
        assert bounded.stats()["retained_jobs"] == bounded.stats()["retained_operations"] == 0
        assert bounded.stats()["admitted"] == bounded.stats()["completed"] == 45


def test_long_live_job_and_waiting_pool_survive_repeated_slot_reuse():
    with Stream(3, wip_cap=2, retention="bounded") as stream:
        old = stream.add(Job(0, (0, 1), (100, 1), 200))
        first = stream.add(Job(0, (2,), (1,), 10))
        stream.advance(2)
        assert stream.inspect(old)["state"] == "active"
        with pytest.raises(ExpiredHandleError):
            stream.inspect(first)
        for t in range(2, 60):
            new = stream.add(Job(t, (2,), (0.25,), 100))
            assert new.id > first.id
            stream.advance(t + 1)
            assert stream.inspect(old)["state"] == "active"
            with pytest.raises(ExpiredHandleError):
                stream.inspect(first)
        assert stream.stats()["job_capacity"] == 16
        stream.run()
        with pytest.raises(ExpiredHandleError):
            stream.inspect(old)


def test_queries_do_not_mutate_and_past_inputs_remain_committed():
    digests = []
    for due in (5, 6):
        with Stream(1, retention="bounded") as stream:
            stream.add(Job(0, (0,), (1,), due))
            stream.advance(2)
            initial = stream.state_bytes()
            for _ in range(3):
                stream.snapshot()
                stream.stats()
                assert stream.state_bytes() == initial
            assert stream.stats()["retained_jobs"] == 0
            digests.append(stream.state_digest())
    assert digests[0] != digests[1]


def test_advancement_partition_and_refill_invariance():
    outputs = []
    for retention, split in (("full", False), ("bounded", False), ("bounded", True)):
        with Stream(3, retention=retention) as stream:
            for batch in range(5):
                for j in jobs(batch, 9):
                    stream.add(Job(j.arrival + batch * 10, j.route, j.durations, j.due))
                if split:
                    for t in (1, 2, 5, 7):
                        stream.advance(batch * 10 + t)
                stream.advance((batch + 1) * 10)
            stream.run()
            outputs.append((stream.snapshot(), stream.state_bytes(), stream.history_digest))
    assert outputs[0] == outputs[1] == outputs[2]


def test_controller_state_is_explicit_and_foreign_or_closed_handles_fail():
    cursor = 0
    with Stream(1, retention="bounded", input_state=lambda: dict(cursor=cursor)) as stream, Stream(1) as other:
        handle = stream.add(Job(0, (0,), (1,), 10))
        with pytest.raises(ValueError, match="Foreign"):
            other.inspect(handle)
        a = stream.state_digest()
        cursor += 1
        assert stream.state_digest() != a
        stream.run()
        with pytest.raises(ExpiredHandleError):
            stream.inspect(handle)
    stream.close()
    with pytest.raises(RuntimeError):
        stream.inspect(handle)


def test_empty_stream_drains_and_independent_engines_share_no_state():
    def run(i):
        with Stream(1, retention="bounded") as stream:
            stream.advance(2)
            assert stream.snapshot()["scalars"]["shopfloor/throughput"] == 0
            stream.add(Job(2, (0,), (i + 1,), 20))
            stream.run()
            return stream.snapshot()["scalars"]["shopfloor/makespan"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run, range(12))) == list(range(1, 13))


@pytest.mark.parametrize("kwargs", [dict(retention="none"), dict(warmup=-1), dict(input_state=0)])
def test_invalid_configuration(kwargs):
    with pytest.raises((ValueError, TypeError)):
        Stream(1, **kwargs)


@pytest.mark.parametrize("seed", range(6))
def test_lazy_input_with_irregular_windows_and_varying_routes(seed):
    from prototypes.native_engine_streaming.producer import Producer

    producer = Producer(seed=seed, spacing=0.75)
    rng = random.Random(seed)
    with (
        Stream(3, wip_cap=3, input_state=producer.state) as full,
        Stream(3, wip_cap=3, retention="bounded", input_state=producer.state) as bounded,
    ):
        for _ in range(40):
            for _ in range(rng.randrange(1, 20)):
                job = producer.next()
                full.add(job)
                bounded.add(job)
            for stream in (full, bounded):
                stream.advance(producer.cursor * producer.spacing)
            assert full.snapshot() == bounded.snapshot()
            assert full.state_bytes() == bounded.state_bytes()
        full.run()
        bounded.run()
        assert full.state_bytes() == bounded.state_bytes()


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("horizon,warmup", [(1, 0), (4, 0), (4, 2), (20, 2)])
def test_compaction_matches_ordinary_production_collectors(retention, horizon, warmup):
    from tests.experimental.test_native import python_kpis

    work = (Job(0, (0, 1), (2, 1), 2), Job(0, (1, 0), (1, 2), 3), Job(1, (0, 1), (0.5, 1), 4), Job(4, (1,), (2,), 6))
    with Stream(2, wip_cap=2, policy="spt", warmup=warmup, retention=retention) as stream:
        for job in work:
            stream.add(job)
        for t in (0.25, 0.5, 1, 2, 3, 4):
            if t < horizon:
                stream.advance(t)
        stream.advance(horizon)
        assert stream.snapshot()["scalars"] == python_kpis(work, "spt", 2, horizon, warmup)


def test_managed_environment_uses_compact_controller_and_reproduces():
    from simulatte import Environment
    from simulatte.stopping import Horizon, execute_until
    from prototypes.native_engine_streaming.backend import StreamingProduction
    from prototypes.native_engine_streaming.producer import Producer

    outputs = []
    for retention in ("full", "bounded", "bounded"):
        producer = Producer(seed=47)
        backend = StreamingProduction(3, input_state=producer.state, retention=retention, wip_cap=3)
        with Environment(seed=0) as env:
            env.enable_digest()
            env._execution_policy = Horizon(300, warmup=10)
            env.configure_kpis(warmup=10)
            env.attach_execution_backend(backend, scope="shopfloor")

            def controller():
                while producer.cursor < 200:
                    for _ in range(8):
                        backend.add(producer.next())
                    yield env.timeout(producer.cursor * producer.spacing - env.now)

            env.process(controller())
            execute_until(env, env._execution_policy)
            assert len(env.entities.live()) == len(env.collectors) == 1
            assert len(backend._jobs) == 0
            stats = backend._stream.stats()
            assert stats["retained_jobs"] <= (200 if retention == "full" else 8)
            assert env.fingerprint().digest is not None
            outputs.append((backend.kpi_values(), backend.state_digest(), env.fingerprint()))
    assert outputs[0] == outputs[1] == outputs[2]
