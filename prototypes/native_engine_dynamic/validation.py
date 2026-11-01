"""Independent trajectory, cap/KPI and complete-record replay checks."""

from array import array
import hashlib
import math

from prototypes.native_engine.model import flatten

from .recording import ARRIVAL, END, FINISH, QUEUE, RELEASE, START, read_records


def fingerprint(result):
    digest = hashlib.sha256()
    for key, code in [("starts", "d"), ("ends", "d"), ("finished", "d"), ("released", "d"), ("dispatch", "I")]:
        digest.update(key.encode())
        digest.update(array(code, result[key]).tobytes())
    return digest.hexdigest()


def validate_result(workload, result, *, wip_cap=None):
    offsets, servers, durations, owners = flatten(workload)
    count, jobs = len(servers), len(workload.jobs)
    starts, ends, released, finished = (result[k] for k in ("starts", "ends", "released", "finished"))
    assert len(starts) == len(ends) == count
    assert len(released) == len(finished) == jobs
    assert all(math.isfinite(t) and t >= 0 for values in (starts, ends, released, finished) for t in values)
    assert sorted(result["dispatch"]) == list(range(count))
    assert result["completed"] == jobs and result["callbacks"] == 0
    assert result["makespan"] == max(finished, default=0)
    available, busy, waits = [0.0] * workload.servers, [[] for _ in range(workload.servers)], []
    for job, source in enumerate(workload.jobs):
        assert source.arrival <= released[job] <= finished[job]
        assert finished[job] == ends[offsets[job + 1] - 1]
    for op in result["dispatch"]:
        job, server = owners[op], servers[op]
        queued = released[job] if op == offsets[job] else ends[op - 1]
        assert starts[op] >= queued and starts[op] >= available[server]
        assert ends[op] == starts[op] + durations[op] and ends[op] > starts[op]
        waits.append(starts[op] - queued)
        available[server] = ends[op]
        busy[server].append(durations[op])
    floor = maximum = 0
    for _, delta in sorted([(t, 1) for t in released] + [(t, -1) for t in finished]):
        floor += delta
        assert floor >= 0
        maximum = max(maximum, floor)
        assert wip_cap is None or floor <= wip_cap
    assert floor == 0
    if result["kpis"] is not None:
        actual = result["kpis"]
        expected = dict(
            total_flow=math.fsum(finished[j] - s.arrival for j, s in enumerate(workload.jobs)),
            total_pool_wait=math.fsum(released[j] - s.arrival for j, s in enumerate(workload.jobs)),
            floor_wip_area=math.fsum(finished[j] - released[j] for j in range(jobs)),
            total_wait=math.fsum(waits),
            total_tardiness=math.fsum(max(0, finished[j] - s.due) for j, s in enumerate(workload.jobs)),
        )
        expected["wip_area"] = expected["total_flow"]
        for key, value in expected.items():
            assert math.isclose(actual[key], value, rel_tol=2e-11, abs_tol=1e-8), key
        assert len(actual["busy"]) == workload.servers
        for value, samples in zip(actual["busy"], busy):
            assert math.isclose(value, math.fsum(samples), rel_tol=2e-11, abs_tol=1e-8)
        assert maximum <= actual["max_floor_wip"] <= (wip_cap or jobs)
        system = peak = 0
        timeline = [(job.arrival, 0, 1) for job in workload.jobs] + [(at, 1, -1) for at in finished]
        for _, _, delta in sorted(timeline):
            system += delta
            peak = max(peak, system)
        assert system == 0 and actual["max_wip"] == peak


def validate_recording(workload, result, path, *, wip_cap=None):
    offsets, servers, _, owners = flatten(workload)
    states = [0] * len(workload.jobs)
    operations = [0] * len(servers)
    occupied = [None] * workload.servers
    floor, pool, dispatch = set(), set(), []
    previous = None
    count = maximum = system = system_peak = 0
    for now, _, kind, job, op, server in read_records(path):
        count += 1
        assert 0 <= job < len(states)
        if kind == ARRIVAL:
            assert states[job] == 0 and now == workload.jobs[job].arrival
            states[job] = 1
            pool.add(job)
            system += 1
            system_peak = max(system_peak, system)
        elif kind == RELEASE:
            assert states[job] == 1 and job in pool and now == result["released"][job]
            assert wip_cap is None or len(floor) < wip_cap
            if previous != (ARRIVAL, job):
                assert job == min(pool, key=lambda candidate: (workload.jobs[candidate].due, candidate)), "EDD release"
            pool.remove(job)
            floor.add(job)
            maximum = max(maximum, len(floor))
            states[job] = 2
        elif kind == FINISH:
            assert states[job] == 2 and job in floor and now == result["finished"][job]
            assert all(state == 3 for state in operations[offsets[job] : offsets[job + 1]])
            floor.remove(job)
            states[job] = 3
            system -= 1
        else:
            assert job in floor and states[job] == 2
            assert 0 <= op < len(operations) and owners[op] == job and servers[op] == server
            if kind == QUEUE:
                assert operations[op] == 0
                assert now == (result["released"][job] if op == offsets[job] else result["ends"][op - 1])
                operations[op] = 1
            elif kind == START:
                assert operations[op] == 1 and occupied[server] is None and now == result["starts"][op]
                operations[op] = 2
                occupied[server] = op
                dispatch.append(op)
            elif kind == END:
                assert operations[op] == 2 and occupied[server] == op and now == result["ends"][op]
                operations[op] = 3
                occupied[server] = None
        previous = (kind, job)
    assert all(state == 3 for state in states) and all(state == 3 for state in operations)
    assert not floor and not pool and all(op is None for op in occupied)
    assert dispatch == result["dispatch"]
    assert count == result["records"] == 3 * len(states) + 3 * len(operations)
    if result["kpis"] is not None:
        assert maximum == result["kpis"]["max_floor_wip"]
        assert system == 0 and system_peak == result["kpis"]["max_wip"]
