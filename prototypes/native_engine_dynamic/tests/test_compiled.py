"""The compiled scalar boundary preserves the stream contract and detached ownership."""

import gc
import math
from threading import Event, Thread

import pytest

from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.compiled import extension
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.validation import validate_recording, validate_result


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [None, 1, 4])
@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_compiled_scalar_parity_growth_ties_and_detached_lifetimes(tmp_path, policy, cap, mode):
    jobs = tuple(Job(i // 8, (i % 3, (i + 1) % 3), (0.5 + i % 3, 1), i % 7) for i in range(257))
    data = Workload(3, jobs)
    expected = recorded = None
    for cls in (ReferenceStream, CompiledStream):
        path = tmp_path / cls.__name__
        stream = cls(3, wip_cap=cap, policy=policy, mode=mode, trace_path=path if mode == "record" else None)
        handles = []
        for job in jobs:
            stream.advance(job.arrival)
            handles.append(stream.add(job))
        assert handles[0].id == 0 and handles[-1].id == 256
        stream.run()
        result = stream.result()
        validate_result(data, result, wip_cap=cap)
        if expected is None:
            expected = stream.result()
        assert result == expected
        snapshot = stream.inspect(handles[-1])
        if mode == "record":
            validate_recording(data, result, path, wip_cap=cap)
            content = path.read_bytes()
            recorded = content if recorded is None else recorded
            assert content == recorded
        again = stream.result()
        result["starts"][0] = -100
        assert again["starts"][0] == 0
        assert snapshot["state"] == "finished"
        stream.close()
        stream.close()
        assert again["completed"] == 257
        with pytest.raises(RuntimeError):
            stream.inspect(handles[0])


def test_compiled_validation_lifecycle_and_foreign_handles():
    stream, other = CompiledStream(1), CompiledStream(1)
    job = Job(0, (0,), (1,), 1)
    for bad in (object(), None):
        with pytest.raises(TypeError):
            stream.add(bad)
    with pytest.raises(ValueError):
        stream.add(Job(0, (1,), (1,), 1))
    with pytest.raises(RuntimeError):
        stream.result()
    handle = stream.add(job)
    with pytest.raises(ValueError):
        other.inspect(handle)
    for until in (True, math.inf, math.nan, -1):
        with pytest.raises((TypeError, ValueError)):
            stream.advance(until)
    stream.advance(1)
    assert stream.inspect(handle)["finished"] is None
    with pytest.raises(ValueError):
        stream.add(job)
    stream.advance(3)
    assert stream.now == 3 and stream.inspect(handle)["finished"] == 1
    stream.add(Job(3, (0,), (1,), 1))
    stream.run()
    assert stream.result()["makespan"] == 4
    for action in (stream.run, lambda: stream.add(job), lambda: stream.advance(4)):
        with pytest.raises(RuntimeError):
            action()
    stream.close()
    other.close()


@pytest.mark.parametrize(
    "options",
    [
        dict(servers=True),
        dict(servers=0),
        dict(servers=0xFFFFFFFF),
        dict(servers=1, wip_cap=0),
        dict(servers=1, policy="bad"),
        dict(servers=1, mode="record"),
        dict(servers=1, trace_path="x"),
    ],
)
def test_compiled_invalid_options(options):
    with pytest.raises((TypeError, ValueError)):
        CompiledStream(**options)


def test_compiled_native_failure_and_record_error(tmp_path):
    with pytest.raises(OSError):
        CompiledStream(1, mode="record", trace_path=tmp_path)
    for path in ("", "a\x00b"):
        with pytest.raises(ValueError):
            CompiledStream(1, mode="record", trace_path=path)
    stream = CompiledStream(1)
    handle = stream.add(Job(1e300, (0,), (1,), 1))
    with pytest.raises(ValueError):
        stream.run()
    for action in (stream.result, lambda: stream.inspect(handle), lambda: stream.add(Job(1e300, (0,), (1,), 1))):
        with pytest.raises(RuntimeError):
            action()
    stream.close()


def test_compiled_empty_destructor_and_serialized_threads():
    empty = CompiledStream(2)
    empty.advance(10)
    empty.run()
    assert empty.result()["makespan"] == 0
    empty.close()
    stream = CompiledStream(1)
    failures = []

    def add_jobs():
        try:
            for _ in range(100):
                stream.add(Job(0, (0,), (1,), 1))
        except BaseException as error:
            failures.append(error)

    threads = [Thread(target=add_jobs) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    stream.run()
    assert stream.result()["completed"] == 300
    stream = None
    for _ in range(10):
        CompiledStream(1).add(Job(0, (0,), (1,), 1))
    gc.collect()


def test_raw_boundary_rejects_bad_capsules_lengths_counts_and_closed_calls():
    raw = extension()
    with pytest.raises((TypeError, ValueError)):
        raw.now(object())
    for servers in (0, True, 0xFFFFFFFF, 2**65):
        with pytest.raises((TypeError, ValueError, OverflowError)):
            raw.create(servers, 0, 0, 0, None)
    engine = raw.create(2, 0, 0, 0, None)
    for routes, durations in [
        ([], (1,)),
        ((0,), ()),
        ((0, 0), (1, 1)),
        ((2,), (1,)),
        ((True,), (1,)),
        ((0,), (math.inf,)),
        ((0,), (object(),)),
    ]:
        with pytest.raises((TypeError, ValueError)):
            raw.add(engine, 0, 1, routes, durations)
    assert raw.add(engine, 0, 1, (0,), (1,)) == 0
    with pytest.raises(ValueError):
        raw.inspect(engine, 0xFFFFFFFE)
    raw.run(engine)
    assert raw.result(engine)["completed"] == 1
    raw.close(engine)
    raw.close(engine)
    for action in (
        lambda: raw.now(engine),
        lambda: raw.result(engine),
        lambda: raw.run(engine),
        lambda: raw.add(engine, 0, 1, (0,), (1,)),
    ):
        with pytest.raises(RuntimeError):
            action()


def test_sanitized_boundary_allocation_failures():
    raw = extension()
    if not hasattr(raw, "_fail_allocation"):
        pytest.skip("allocation hooks exist only in the sanitizer build")
    raw._fail_allocation(1)
    with pytest.raises(MemoryError):
        raw.create(1, 0, 0, 0, None)
    raw._fail_allocation(0)
    engine = raw.create(1, 0, 0, 0, None)
    for nth in (1, 2):
        raw._fail_allocation(nth)
        with pytest.raises(MemoryError):
            raw.add(engine, 0, 1, (0,), (1,))
    raw._fail_allocation(0)
    assert raw.add(engine, 0, 1, (0,), (1,)) == 0
    raw.run(engine)
    assert raw.result(engine)["completed"] == 1
    raw.close(engine)


def test_raw_native_run_releases_gil_and_rejects_concurrent_close():
    raw = extension()
    engine = raw.create(1, 0, 0, 0, None)
    for _ in range(20000):
        raw.add(engine, 0, 1, (0,), (1,))
    started, errors = Event(), []

    def run():
        started.set()
        try:
            raw.run(engine)
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=run)
    thread.start()
    started.wait()
    try:
        with pytest.raises(RuntimeError, match="busy"):
            raw.close(engine)
    finally:
        thread.join()
    assert errors == []
    assert raw.result(engine)["completed"] == 20000
    raw.close(engine)
