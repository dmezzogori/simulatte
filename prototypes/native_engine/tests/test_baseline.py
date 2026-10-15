from __future__ import annotations

import random

import pytest

from prototypes.native_engine.baseline import SimulatteRunner
from prototypes.native_engine.model import Job, Workload, fifo_hook, spt_hook
from prototypes.native_engine.reference import ReferenceRunner
from prototypes.native_engine.native import NativeRunner


@pytest.mark.parametrize("policy,hook", [("fifo", None), ("spt", None), ("fifo", fifo_hook), ("spt", spt_hook)])
def test_actual_simulatte_matches_reference_with_ties(tmp_path, policy, hook):
    for seed in range(64):
        rng = random.Random(seed)
        jobs = []
        for _ in range(rng.randrange(1, 25)):
            route = tuple(rng.sample(range(4), rng.randint(1, 4)))
            jobs.append(Job(rng.randrange(8), route, tuple(rng.randint(1, 4) for _ in route), rng.randrange(30)))
        workload = Workload(4, tuple(sorted(jobs, key=lambda j: j.arrival)))
        outputs, records = [], []
        for backend in (ReferenceRunner, SimulatteRunner):
            path = tmp_path / f"{backend.__name__}.nevt"
            runner = backend(workload, policy=policy, mode="record", hook=hook, trace_path=path)
            runner.run()
            outputs.append(runner.result())
            runner.close()
            records.append(path.read_bytes())
        outputs[0].pop("events")
        outputs[1].pop("events")
        assert outputs[0] == outputs[1], seed
        assert records[0] == records[1], seed


@pytest.mark.parametrize("backend", [ReferenceRunner, SimulatteRunner])
def test_modes_and_callback_lifecycle(tmp_path, backend):
    workload = Workload(1, (Job(0, (0,), (1,), 2),))
    outcomes = []
    for mode in ("none", "kpi", "record"):
        runner = backend(workload, mode=mode, trace_path=tmp_path / "trace.nevt" if mode == "record" else None)
        runner.run()
        outcomes.append(runner.result()["starts"])
        runner.close()
    assert outcomes == [[0], [0], [0]]
    runner = backend(workload, hook=lambda *_: runner.close())
    with pytest.raises(RuntimeError):
        runner.run()
    runner.close()


def test_baseline_does_not_replace_current_processing_model():
    from simulatte import Environment
    from simulatte.shopfloor import ShopFloor
    from simulatte.server import Server

    runner = SimulatteRunner(Workload(1, ()))
    assert isinstance(runner.env, Environment)
    assert type(runner.shop).main is ShopFloor.main
    assert type(runner.servers[0]).process_job is Server.process_job
    runner.run()
    assert runner.result()["completed"] == 0
    runner.close()


def test_absolute_fractional_arrivals_do_not_round_or_split_batches(tmp_path):
    workload = Workload(2, (Job(0.2, (0,), (0.1,), 2), Job(0.9, (1,), (0.1,), 2), Job(0.9, (0,), (0.1,), 2)))
    results, traces = [], []
    for backend in (ReferenceRunner, SimulatteRunner, NativeRunner):
        path = tmp_path / backend.__name__
        runner = backend(workload, mode="record", trace_path=path)
        runner.run()
        result = runner.result()
        runner.close()
        result.pop("events")
        results.append(result)
        traces.append(path.read_bytes())
    assert results[0] == results[1] == results[2]
    assert traces[0] == traces[1] == traces[2]
