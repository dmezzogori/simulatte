from __future__ import annotations

import math

import pytest

from prototypes.native_engine.model import Job, Workload, fifo_hook, spt_hook
from prototypes.native_engine.recording import read_records
from prototypes.native_engine.reference import ReferenceRunner


def tiny():
    return Workload(1, (Job(0, (0,), (3,), 10), Job(1, (0,), (2,), 10), Job(1, (0,), (1,), 10)))


@pytest.mark.parametrize(
    "policy,starts,ends,dispatch,flow,wait",
    [("fifo", [0, 3, 5], [3, 5, 6], [0, 1, 2], 12, 6), ("spt", [0, 4, 3], [3, 6, 4], [0, 2, 1], 11, 5)],
)
def test_hand_computed_queue_and_kpis(policy, starts, ends, dispatch, flow, wait):
    runner = ReferenceRunner(tiny(), policy=policy, mode="kpi")
    runner.run()
    result = runner.result()
    assert result["starts"] == starts
    assert result["ends"] == ends
    assert result["finished"] == ends
    assert result["dispatch"] == dispatch
    assert result["makespan"] == 6
    assert result["completed"] == 3
    assert result["kpis"] == dict(
        total_flow=flow, total_wait=wait, total_tardiness=0, busy=[6], wip_area=flow, max_wip=3
    )
    runner.close()


def test_simultaneous_cross_routing_preserves_release_order():
    workload = Workload(2, (Job(0, (0, 1), (2, 1), 10), Job(0, (1, 0), (2, 1), 10)))
    runner = ReferenceRunner(workload)
    runner.run()
    result = runner.result()
    assert result["starts"] == [0, 2, 0, 2]
    assert result["ends"] == [2, 3, 2, 3]
    assert result["dispatch"] == [0, 2, 3, 1]
    assert result["finished"] == [3, 3]
    runner.close()


@pytest.mark.parametrize("policy,hook", [("fifo", fifo_hook), ("spt", spt_hook)])
def test_recording_and_hook_preserve_trajectory(tmp_path, policy, hook):
    outputs = []
    files = []
    for use_hook in (None, hook):
        path = tmp_path / f"{len(files)}.nevt"
        runner = ReferenceRunner(tiny(), policy=policy, mode="record", hook=use_hook, trace_path=path)
        runner.run()
        outputs.append(runner.result())
        runner.close()
        files.append(path.read_bytes())
        records = list(read_records(path))
        assert len(records) == 15
        assert [r[1] for r in records] == list(range(15))
        assert [r[2] for r in records].count(3) == 3
    assert files[0] == files[1]
    assert outputs[0]["starts"] == outputs[1]["starts"]
    assert outputs[0]["callbacks"] == 0
    assert outputs[1]["callbacks"] == 3


def test_lifecycle_empty_and_detached_results():
    runner = ReferenceRunner(Workload(10, ()), mode="kpi")
    with pytest.raises(RuntimeError):
        runner.result()
    runner.run()
    assert runner.result()["completed"] == 0
    assert runner.result()["makespan"] == 0
    with pytest.raises(RuntimeError):
        runner.run()
    detached = runner.result()
    runner.close()
    runner.close()
    assert detached["starts"] == []
    with pytest.raises(RuntimeError):
        runner.result()


@pytest.mark.parametrize("answer", [True, -1, 999, 0.0, "0"])
def test_hook_rejects_nonmember_or_noninteger(answer):
    runner = ReferenceRunner(tiny(), hook=lambda *_: answer)
    with pytest.raises((ValueError, TypeError)):
        runner.run()
    runner.close()
    runner.close()


def test_hook_exception_preserved_and_close_possible():
    def fail(*_):
        raise LookupError("policy failed")

    runner = ReferenceRunner(tiny(), hook=fail)
    with pytest.raises(LookupError, match="policy failed"):
        runner.run()
    runner.close()


@pytest.mark.parametrize(
    "arrival,durations", [(-1, (1,)), (math.nan, (1,)), (0, (0,)), (0, (-1,)), (0, (math.inf,)), (True, (1,))]
)
def test_reject_invalid_time(arrival, durations):
    with pytest.raises((ValueError, TypeError)):
        Workload(1, (Job(arrival, (0,), durations, 10),))


def test_reject_malformed_routes_and_unsorted_arrivals():
    for job in (lambda: Job(0, (), (), 10), lambda: Job(0, (0, 0), (1, 1), 10), lambda: Job(0, (0,), (1, 2), 10)):
        with pytest.raises(ValueError):
            job()
    with pytest.raises(ValueError):
        Workload(1, (Job(0, (1,), (1,), 10),))
    with pytest.raises(ValueError):
        Workload(1, (Job(1, (0,), (1,), 10), Job(0, (0,), (1,), 10)))


def test_reject_nonadvancing_completion_time():
    runner = ReferenceRunner(Workload(1, (Job(1e20, (0,), (1,), 1e20),)))
    with pytest.raises(ValueError):
        runner.run()
    runner.close()
