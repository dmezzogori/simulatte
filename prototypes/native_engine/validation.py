"""Independent conservation checks; no engine or observation implementation calls."""

from __future__ import annotations

import hashlib
import math
from array import array

from .model import flatten
from .recording import ARRIVAL, END, FINISH, QUEUE, START, read_records


def fingerprint(result):
    digest = hashlib.sha256()
    for key, code in [("starts", "d"), ("ends", "d"), ("finished", "d"), ("dispatch", "I")]:
        digest.update(key.encode())
        digest.update(array(code, result[key]).tobytes())
    return digest.hexdigest()


def validate_result(workload, result):
    offsets, servers, durations, owners = flatten(workload)
    count, jobs = len(servers), len(workload.jobs)
    starts, ends, finished = result["starts"], result["ends"], result["finished"]
    assert len(starts) == len(ends) == count and len(finished) == jobs
    assert sorted(result["dispatch"]) == list(range(count)), "operation conservation"
    assert result["completed"] == jobs, "job conservation"
    assert all(math.isfinite(t) and t >= 0 for values in (starts, ends, finished) for t in values)
    assert [starts[op] for op in result["dispatch"]] == sorted(starts), "dispatch time order"
    assert result["makespan"] == max(finished, default=0)
    available = [0.0] * workload.servers
    waits, busy = [], [[] for _ in range(workload.servers)]
    for op in result["dispatch"]:
        job, server = owners[op], servers[op]
        queued = workload.jobs[job].arrival if op == offsets[job] else ends[op - 1]
        assert starts[op] >= queued, "job precedence"
        assert starts[op] >= available[server], "resource capacity"
        assert ends[op] == starts[op] + durations[op] and ends[op] > starts[op], "duration"
        available[server] = ends[op]
        waits.append(starts[op] - queued)
        busy[server].append(durations[op])
    for job in range(jobs):
        assert finished[job] == ends[offsets[job + 1] - 1], "finish after last operation"
    if result["kpis"] is not None:
        kpis = result["kpis"]
        flow = math.fsum(finished[j] - source.arrival for j, source in enumerate(workload.jobs))
        expected = dict(
            total_flow=flow,
            wip_area=flow,
            total_wait=math.fsum(waits),
            total_tardiness=math.fsum(max(0, finished[j] - source.due) for j, source in enumerate(workload.jobs)),
        )
        for key, value in expected.items():
            assert math.isclose(kpis[key], value, rel_tol=2e-11, abs_tol=1e-8), key
        assert len(kpis["busy"]) == workload.servers
        for actual, values in zip(kpis["busy"], busy):
            assert math.isclose(actual, math.fsum(values), rel_tol=2e-11, abs_tol=1e-8)
        assert (1 <= kpis["max_wip"] <= jobs) if jobs else kpis["max_wip"] == 0
    assert result["callbacks"] in (0, count)
    assert result["records"] in (0, 2 * jobs + 3 * count)


def validate_recording(workload, result, path):
    offsets, servers, durations, owners = flatten(workload)
    job_state, op_state = [0] * len(workload.jobs), [0] * len(servers)
    occupied = [None] * workload.servers
    dispatch = []
    wip = maximum = records = 0
    for now, _, kind, job, op, server in read_records(path):
        records += 1
        assert 0 <= job < len(job_state), "job ID"
        if kind == ARRIVAL:
            assert job_state[job] == 0 and now == workload.jobs[job].arrival
            job_state[job] = 1
            wip += 1
            maximum = max(maximum, wip)
        elif kind == FINISH:
            assert job_state[job] == 1 and now == result["finished"][job]
            assert all(state == 3 for state in op_state[offsets[job] : offsets[job + 1]])
            job_state[job] = 2
            wip -= 1
        else:
            assert 0 <= op < len(op_state) and owners[op] == job and servers[op] == server
            assert job_state[job] == 1
            if kind == QUEUE:
                expected = workload.jobs[job].arrival if op == offsets[job] else result["ends"][op - 1]
                assert now == expected and op_state[op] == 0
                assert op == offsets[job] or op_state[op - 1] == 3
                op_state[op] = 1
            elif kind == START:
                assert op_state[op] == 1 and occupied[server] is None, "resource capacity/lifecycle"
                assert now == result["starts"][op]
                op_state[op], occupied[server] = 2, op
                dispatch.append(op)
            elif kind == END:
                assert op_state[op] == 2 and occupied[server] == op
                assert now == result["ends"][op] == result["starts"][op] + durations[op]
                op_state[op], occupied[server] = 3, None
    assert records == result["records"] == 2 * len(job_state) + 3 * len(op_state)
    assert all(s == 2 for s in job_state) and all(s == 3 for s in op_state)
    assert all(op is None for op in occupied) and wip == 0
    assert dispatch == result["dispatch"]
    assert maximum == result["kpis"]["max_wip"]


def compare_results(left, right):
    for key in ("starts", "ends", "finished", "dispatch", "makespan", "completed", "records"):
        assert left[key] == right[key], key
    assert (left["kpis"] is None) == (right["kpis"] is None)
    if left["kpis"] is not None:
        for key in left["kpis"]:
            a, b = left["kpis"][key], right["kpis"][key]
            pairs = zip(a, b) if isinstance(a, list) else [(a, b)]
            assert all(math.isclose(x, y, rel_tol=2e-11, abs_tol=1e-8) for x, y in pairs), key
