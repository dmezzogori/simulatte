"""Cross-backend dynamic ownership, ordering and independent replay evidence."""

import random
from copy import deepcopy

import pytest

from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.reference import ReferenceStream


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 4])
@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_randomized_dynamic_parity_and_feeding_partitions(tmp_path, policy, cap, mode):
    from prototypes.native_engine_dynamic.tuned import TunedStream
    from prototypes.native_engine_dynamic.validation import validate_result, validate_recording

    rng = random.Random(9327)
    for trial in range(12):
        servers, now, jobs = rng.randint(1, 5), 0.0, []
        for _ in range(rng.randint(0, 45)):
            now += rng.choice([0, 0, 1, 2]) if trial % 2 else rng.random()
            route = tuple(rng.sample(range(servers), rng.randint(1, servers)))
            durations = tuple(rng.randint(1, 3) if trial % 2 else rng.uniform(0.1, 3) for _ in route)
            jobs.append(Job(now, route, durations, now + rng.randint(0, 15)))
        workload = Workload(servers, tuple(jobs))
        expected = recording = None
        for index, factory in enumerate((ReferenceStream, TunedStream, NativeStream)):
            for incremental in (False, True):
                path = tmp_path / f"{index}-{incremental}.bin" if mode == "record" else None
                stream = factory(servers, wip_cap=cap, policy=policy, mode=mode, trace_path=path)
                try:
                    for job in jobs:
                        if incremental:
                            stream.advance(job.arrival)
                        stream.add(job)
                    stream.run()
                    result = stream.result()
                    validate_result(workload, result, wip_cap=cap)
                    if path is not None:
                        validate_recording(workload, result, path, wip_cap=cap)
                    if expected is None:
                        expected = result
                    for key in (
                        "starts",
                        "ends",
                        "released",
                        "finished",
                        "dispatch",
                        "makespan",
                        "completed",
                        "records",
                    ):
                        assert result[key] == expected[key], (trial, key)
                    if mode != "none":
                        assert result["kpis"] == pytest.approx(expected["kpis"], rel=1e-12, abs=1e-10)
                finally:
                    stream.close()
                if path is not None:
                    data = path.read_bytes()
                    if recording is None:
                        recording = data
                    assert data == recording, trial


def test_independent_validator_rejects_bad_release_and_records(tmp_path):
    from prototypes.native_engine_dynamic.validation import validate_result, validate_recording

    data = Workload(1, (Job(1, (0,), (2,), 5),))
    path = tmp_path / "good.bin"
    stream = ReferenceStream(1, mode="record", trace_path=path)
    stream.add(data.jobs[0])
    stream.run()
    result = stream.result()
    stream.close()
    validate_result(data, result)
    validate_recording(data, result, path)
    broken = deepcopy(result)
    broken["released"][0] = 0
    with pytest.raises(AssertionError):
        validate_result(data, broken)
    bad = tmp_path / "bad.bin"
    bad.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        validate_recording(data, result, bad)


def test_release_policy_matches_actual_production_conwip():
    from simulatte import Environment
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.policies.conwip import ConWIP

    starts, finished, released, ids = [None] * 3, [None] * 3, [None] * 3, {}
    with Environment(seed=0) as env:

        def before(job, server, operation, duration):
            starts[ids[job.id]] = env.now

        def done(job):
            finished[ids[job.id]] = env.now

        shop = ShopFloor(env=env, default_metrics=False, on_before_operation=before, on_job_finished=done)
        server = Server(env=env, shopfloor=shop, capacity=1)
        pool = PreShopPool(env=env, shopfloor=shop)
        ConWIP(shopfloor=shop, psp=pool, wip_cap=1)
        original = pool.release

        def release(job):
            released[ids[job.id]] = env.now
            return original(job)

        pool.release = release
        for index, (duration, due) in enumerate(((3, 10), (2, 9), (1, 1))):
            job = ProductionJob(env=env, sku="fixture", servers=[server], processing_times=[duration], due_date=due)
            ids[job.id] = index
            pool.add(job)
        env.run()
    stream = NativeStream(1, wip_cap=1)
    try:
        for duration, due in ((3, 10), (2, 9), (1, 1)):
            stream.add(Job(0, (0,), (duration,), due))
        stream.run()
        result = stream.result()
        assert result["starts"] == starts == [0, 4, 3]
        assert result["released"] == released == [0, 4, 3]
        assert result["finished"] == finished == [3, 6, 4]
    finally:
        stream.close()


@pytest.mark.parametrize("check", ["trajectory", "recording"])
def test_independent_checks_reject_corrupt_system_peak(tmp_path, check):
    from prototypes.native_engine_dynamic.validation import validate_result, validate_recording

    data = Workload(1, (Job(0, (0,), (1,), 5),) * 3)
    path = tmp_path / "peak.bin"
    stream = ReferenceStream(1, mode="record", trace_path=path)
    try:
        for job in data.jobs:
            stream.add(job)
        stream.run()
        result = stream.result()
    finally:
        stream.close()
    assert result["kpis"]["max_wip"] == 3
    result["kpis"]["max_wip"] = 1
    with pytest.raises(AssertionError):
        if check == "trajectory":
            validate_result(data, result)
        else:
            validate_recording(data, result, path)
