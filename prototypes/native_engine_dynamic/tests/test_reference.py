"""Hand-derived streamed release and lifecycle semantics, independent of C."""

import pytest

from prototypes.native_engine.model import Job


def factory():
    from prototypes.native_engine_dynamic.reference import ReferenceStream

    return ReferenceStream


@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_cap_one_releases_by_edd_on_completion(tmp_path, mode):
    stream = factory()(1, wip_cap=1, mode=mode, trace_path=tmp_path / "edd.bin" if mode == "record" else None)
    try:
        for duration, due in ((3, 10), (2, 9), (1, 1)):
            stream.add(Job(0, (0,), (duration,), due))
        stream.run()
        result = stream.result()
        assert result["released"] == result["starts"] == [0.0, 4.0, 3.0]
        assert result["finished"] == [3.0, 6.0, 4.0]
        assert result["dispatch"] == [0, 2, 1]
        if mode != "none":
            assert result["kpis"]["total_flow"] == result["kpis"]["wip_area"] == 13
            assert result["kpis"]["total_pool_wait"] == 7
            assert result["kpis"]["floor_wip_area"] == 6
            assert result["kpis"]["max_floor_wip"] == 1
    finally:
        stream.close()


def test_create_while_active_and_after_temporary_drain():
    stream = factory()(1, wip_cap=1)
    try:
        first = stream.add(Job(0, (0,), (2,), 5))
        stream.advance(1)
        assert stream.inspect(first)["state"] == "active"
        second = stream.add(Job(1, (0,), (1,), 4))
        stream.advance(4)
        assert stream.inspect(first)["finished"] == 2
        assert stream.inspect(second)["finished"] == 3
        third = stream.add(Job(4, (0,), (1,), 8))
        stream.run()
        assert stream.inspect(third)["finished"] == 5
        assert stream.result()["starts"] == [0.0, 2.0, 4.0]
    finally:
        stream.close()
    with pytest.raises(RuntimeError):
        stream.inspect(first)


def test_arrival_priority_and_feeding_partition_match(tmp_path):
    jobs = (Job(0, (0,), (2,), 10), Job(1, (0,), (1,), 9), Job(2, (0,), (1,), 1))
    results, records = [], []
    for incremental in (False, True):
        path = tmp_path / f"{incremental}.bin"
        stream = factory()(1, wip_cap=1, mode="record", trace_path=path)
        try:
            for job in jobs:
                if incremental:
                    stream.advance(job.arrival)
                stream.add(job)
            stream.run()
            results.append(stream.result())
        finally:
            stream.close()
        records.append(path.read_bytes())
    assert results[0] == results[1]
    assert records[0] == records[1]
    assert results[0]["released"] == [0.0, 3.0, 2.0]


def test_empty_and_foreign_handles():
    a, b = factory()(1), factory()(1)
    try:
        handle = b.add(Job(0, (0,), (1,), 2))
        with pytest.raises(ValueError):
            a.inspect(handle)
        a.run()
        assert a.result()["starts"] == a.result()["released"] == []
        with pytest.raises(RuntimeError):
            a.add(Job(0, (0,), (1,), 2))
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("empty", [False, True])
def test_makespan_is_completion_time_after_advance_beyond_drain(empty):
    stream = factory()(1)
    try:
        if not empty:
            stream.add(Job(0, (0,), (2,), 5))
        stream.advance(100)
        stream.run()
        assert stream.now == 100
        assert stream.result()["makespan"] == (0 if empty else 2)
    finally:
        stream.close()


@pytest.mark.parametrize("identity", [True, 0.0])
def test_inspect_rejects_noninteger_handle_id(identity):
    from prototypes.native_engine_dynamic.handles import JobHandle

    stream = factory()(1)
    try:
        stream.add(Job(0, (0,), (1,), 2))
        with pytest.raises(TypeError):
            stream.inspect(JobHandle(stream._owner, identity))
    finally:
        stream.close()
