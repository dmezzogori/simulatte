from dataclasses import FrozenInstanceError
import math
import struct

import pytest

from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.handles import JobHandle
from prototypes.native_engine_dynamic.native import NativeStream


def job(arrival=0, duration=1, due=10, route=(0,)):
    return Job(arrival, route, (duration,) * len(route), due)


@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_hand_edd(mode, tmp_path):
    path = tmp_path / "trace"
    stream = NativeStream(1, wip_cap=1, mode=mode, trace_path=path if mode == "record" else None)
    handles = [stream.add(job(duration=d, due=due)) for d, due in [(3, 10), (2, 9), (1, 1)]]
    assert stream.inspect(handles[0])["state"] == "scheduled"
    stream.advance(1)
    assert [stream.inspect(h)["state"] for h in handles] == ["active", "pool", "pool"]
    stream.run()
    result = stream.result()
    assert result["released"] == result["starts"] == [0, 4, 3]
    assert result["finished"] == [3, 6, 4]
    assert result["dispatch"] == [0, 2, 1]
    if mode != "none":
        assert result["kpis"]["total_pool_wait"] == 7
        assert result["kpis"]["floor_wip_area"] == 6
        assert result["kpis"]["wip_area"] == 13
        assert result["kpis"]["max_floor_wip"] == 1
    if mode == "record":
        data = path.read_bytes()
        assert data[:8] == b"NDYN0001"
        rows = list(struct.iter_unpack("<dQIIII", data[8:]))
        assert result["records"] == len(rows) == 18
        assert [r[3] for r in rows if r[2] == 6] == [0, 2, 1]
    stream.close()


def test_incremental_ties_and_drain():
    stream = NativeStream(1, wip_cap=1)
    first = stream.add(job(duration=2))
    stream.advance(2)
    assert stream.inspect(first)["finished"] is None
    second = stream.add(job(arrival=2))
    stream.advance(4)
    assert stream.inspect(second)["finished"] == 3
    third = stream.add(job(arrival=4))
    stream.run()
    assert stream.inspect(third)["finished"] == 5
    assert stream.result()["released"] == [0, 2, 4]
    stream.close()


def test_empty_and_never_run():
    stream = NativeStream(2)
    stream.run()
    assert stream.result()["completed"] == 0
    assert stream.result()["starts"] == []
    stream.close()
    stream.close()
    NativeStream(1).close()


def test_growth_handles_and_detached_results():
    stream, other = NativeStream(2), NativeStream(1)
    handles = [stream.add(job(arrival=i / 10, route=(0, 1))) for i in range(257)]
    assert [h.id for h in handles] == list(range(257))
    with pytest.raises(FrozenInstanceError):
        handles[0].id = 8
    with pytest.raises(ValueError):
        other.inspect(handles[0])
    with pytest.raises((TypeError, ValueError)):
        stream.inspect(0)
    with pytest.raises(ValueError):
        stream.inspect(JobHandle(handles[0]._owner, 999))
    stream.run()
    snapshot = stream.inspect(handles[0])
    snapshot["finished"] = -1
    result = stream.result()
    result["starts"][0] = -1
    assert stream.result()["starts"][0] == 0
    assert stream.inspect(handles[0])["finished"] == 2
    stream.close()
    with pytest.raises(RuntimeError):
        stream.inspect(handles[0])
    assert result["finished"][0] == 2
    other.close()


@pytest.mark.parametrize("servers", [0, -1, 0xFFFFFFFF, True, 1.5])
def test_invalid_servers(servers):
    with pytest.raises((TypeError, ValueError)):
        NativeStream(servers)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(wip_cap=0),
        dict(wip_cap=True),
        dict(wip_cap=0xFFFFFFFF),
        dict(policy="x"),
        dict(mode="x"),
        dict(mode="record"),
        dict(trace_path="x"),
    ],
)
def test_invalid_options(kwargs):
    with pytest.raises((TypeError, ValueError)):
        NativeStream(1, **kwargs)


def test_invalid_jobs_and_times():
    stream = NativeStream(1)

    class SubJob(Job):
        pass

    for value in [object(), SubJob(0, (0,), (1,), 1)]:
        with pytest.raises(TypeError):
            stream.add(value)
    with pytest.raises(ValueError):
        stream.add(job(route=(1,)))
    stream.add(job(arrival=2))
    with pytest.raises(ValueError):
        stream.add(job(arrival=1))
    for time in [-1, math.inf, math.nan, True]:
        with pytest.raises((TypeError, ValueError)):
            stream.advance(time)
    stream.advance(3)
    with pytest.raises(ValueError):
        stream.add(job(arrival=2))
    with pytest.raises(ValueError):
        stream.advance(2)
    stream.run()
    for action in [stream.run, lambda: stream.advance(4), lambda: stream.add(job(arrival=4))]:
        with pytest.raises(RuntimeError):
            action()
    stream.close()


@pytest.mark.parametrize("arrival,duration", [(1e308, 1e308), (1e300, 1)])
def test_event_time_failure(arrival, duration):
    stream = NativeStream(1)
    handle = stream.add(job(arrival=arrival, duration=duration))
    with pytest.raises(ValueError):
        stream.run()
    for action in [stream.result, stream.run, lambda: stream.inspect(handle), lambda: stream.add(job(arrival=arrival))]:
        with pytest.raises(RuntimeError):
            action()
    stream.close()


def test_unavailable_and_closed():
    stream = NativeStream(1)
    with pytest.raises(RuntimeError):
        stream.result()
    stream.close()
    for action in [stream.result, stream.run, lambda: stream.advance(0), lambda: stream.add(job())]:
        with pytest.raises(RuntimeError):
            action()


@pytest.mark.parametrize("policy,dispatch,starts", [("fifo", [0, 1, 2], [0, 5, 8]), ("spt", [0, 2, 1], [0, 6, 5])])
def test_unlimited_dispatch(policy, dispatch, starts):
    stream = NativeStream(1, policy=policy, mode="kpi")
    for duration in [5, 3, 1]:
        stream.add(job(duration=duration))
    stream.run()
    result = stream.result()
    assert result["released"] == [0, 0, 0]
    assert result["starts"] == starts
    assert result["dispatch"] == dispatch
    assert result["kpis"]["max_floor_wip"] == 3
    assert result["kpis"]["floor_wip_area"] == result["kpis"]["total_flow"]
    stream.close()


def test_live_growth_and_equal_time_registration(tmp_path):
    jobs = [job(arrival=0, duration=2)] + [job(arrival=2, duration=1, due=i % 7) for i in range(200)]
    results, records = [], []
    for incremental in [False, True]:
        path = tmp_path / str(incremental)
        stream = NativeStream(1, wip_cap=2, policy="spt", mode="record", trace_path=path)
        first = stream.add(jobs[0])
        if incremental:
            stream.advance(2)
            assert stream.inspect(first)["state"] == "active"
        for index, item in enumerate(jobs[1:]):
            stream.add(item)
            if incremental and index % 11 == 0:
                stream.advance(2)
        assert stream.inspect(first)["id"] == 0
        stream.run()
        results.append(stream.result())
        records.append(path.read_bytes())
        stream.close()
    assert results[0] == results[1]
    assert records[0] == records[1]


def test_operation_and_job_count_limits_before_lowering():
    stream = NativeStream(1)
    stream._jobs = 0xFFFFFFFE
    with pytest.raises(ValueError):
        stream.add(job())
    stream._jobs = 0
    stream._operations = 0xFFFFFFFE
    with pytest.raises(ValueError):
        stream.add(job())
    stream._operations = 0
    stream.add(job())
    stream.run()
    stream.close()


def test_record_open_error(tmp_path):
    with pytest.raises(OSError):
        NativeStream(1, mode="record", trace_path=tmp_path)
    for path in ["", "bad\x00path"]:
        with pytest.raises(ValueError):
            NativeStream(1, mode="record", trace_path=path)


def test_record_write_error(tmp_path):
    import subprocess
    import sys
    import textwrap

    pytest.importorskip("resource")
    script = textwrap.dedent("""\
        import resource
        import signal
        import sys
        from prototypes.native_engine.model import Job
        from prototypes.native_engine_dynamic.native import NativeStream

        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1, 1))
        stream = NativeStream(1, mode="record", trace_path=sys.argv[1])
        stream.add(Job(0, (0,), (1,), 1))
        try:
            stream.run()
        except OSError:
            pass
        else:
            raise AssertionError("record flush should fail under file size limit")
        try:
            stream.result()
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed stream exposed successful result")
        stream.close()
        stream.close()
    """)
    subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path / "limited")], check=True, capture_output=True)


def test_close_scheduled_pool_and_active():
    stream = NativeStream(1, wip_cap=1)
    stream.add(job())
    stream.add(job())
    stream.add(job(arrival=10))
    stream.advance(0.5)
    stream.close()
    stream.close()


@pytest.mark.parametrize("has_job,expected", [(True, 2.0), (False, 0.0)])
def test_makespan_ignores_idle_advance(has_job, expected):
    stream = NativeStream(1)
    if has_job:
        stream.add(job(duration=2))
    stream.advance(100)
    stream.run()
    assert stream.now == 100
    assert stream.result()["makespan"] == expected
    stream.close()
