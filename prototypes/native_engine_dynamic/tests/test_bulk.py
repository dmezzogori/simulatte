"""Bulk registration keeps scalar event ordering, ownership and error boundaries."""

import random

import pytest

from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.validation import validate_recording, validate_result


def item(at=0, *, route=(0,), duration=1):
    return Job(at, route, (duration,) * len(route), 10)


def test_bulk_validation_is_before_mutation_and_keeps_stream_usable():
    stream = NativeStream(1)
    first = stream.add(item())
    for values, error in [
        ([item()], TypeError),
        ((item(), object()), TypeError),
        ((item(), item(route=(1,))), ValueError),
        ((item(2), item(1)), ValueError),
    ]:
        with pytest.raises(error):
            stream.add_many(values)
    assert stream.add_many(()) == []
    handles = stream.add_many((item(), item(2)))
    assert [h.id for h in handles] == [1, 2]
    stream.advance(4)
    assert stream.inspect(first)["finished"] == 1
    with pytest.raises(ValueError):
        stream.add_many((item(3),))
    stream.run()
    assert stream.result()["completed"] == 3
    with pytest.raises(RuntimeError):
        stream.add_many(())
    stream.close()
    with pytest.raises(RuntimeError):
        stream.add_many((item(4),))


def test_bulk_growth_handles_mixed_scalar_and_incremental_lifetimes():
    stream, other = NativeStream(2, wip_cap=2), NativeStream(2)
    first = stream.add(item(duration=2))
    stream.advance(2)
    jobs = tuple(item(2, route=(0, 1)) for _ in range(513))
    handles = stream.add_many(jobs)
    del jobs
    assert [h.id for h in handles] == list(range(1, 514))
    assert stream.inspect(first)["state"] == "active"
    assert stream.inspect(handles[-1])["state"] == "scheduled"
    with pytest.raises(ValueError):
        other.inspect(handles[-1])
    stream.run()
    snapshot = stream.inspect(handles[-1])
    assert snapshot["state"] == "finished"
    stream.close()
    assert snapshot["finished"] > 2
    with pytest.raises(RuntimeError):
        stream.inspect(handles[0])
    other.close()


@pytest.mark.parametrize("field", ["_jobs", "_operations"])
def test_bulk_count_checks_before_lowering(field):
    stream = NativeStream(1)
    setattr(stream, field, 0xFFFFFFFE)
    with pytest.raises(ValueError):
        stream.add_many((item(),))
    setattr(stream, field, 0)
    assert stream.add_many((item(),))[0].id == 0
    stream.close()


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 4])
@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_bulk_matches_scalar_reference_with_window_lookahead(tmp_path, policy, cap, mode):
    rng, at, jobs = random.Random(381), 0.0, []
    for index in range(80):
        at += rng.choice((0, 0, 1, 2))
        route = tuple(rng.sample(range(3), rng.randint(1, 3)))
        jobs.append(Job(at, route, tuple(rng.choice((0.5, 1, 2)) for _ in route), at + index % 7))
    workload = Workload(3, tuple(jobs))
    expected = recording = None
    for factory, bulk, window in [(ReferenceStream, False, 1), (NativeStream, False, 1)] + [
        (NativeStream, True, n) for n in (1, 2, 7, 32, 256)
    ]:
        path = tmp_path / f"{factory.__name__}-{bulk}-{window}"
        stream = factory(3, wip_cap=cap, policy=policy, mode=mode, trace_path=path if mode == "record" else None)
        try:
            for begin in range(0, len(jobs), window):
                batch = tuple(jobs[begin : begin + window])
                stream.advance(batch[0].arrival)
                handles = stream.add_many(batch) if bulk else [stream.add(job) for job in batch]
                assert [h.id for h in handles] == list(range(begin, begin + len(batch)))
            stream.run()
            result = stream.result()
            validate_result(workload, result, wip_cap=cap)
            if expected is None:
                expected = result
            assert result == expected
            if mode == "record":
                validate_recording(workload, result, path, wip_cap=cap)
                data = path.read_bytes()
                recording = data if recording is None else recording
                assert data == recording
        finally:
            stream.close()
