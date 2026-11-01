"""Focused native boundary and lifecycle tests (the common suite checks parity)."""

import gc
import math
import os
import random
import struct
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from prototypes.native_engine.native import NativeRunner
from prototypes.native_engine.model import Job, Workload


def workload(rows=(), servers=1):
    jobs = tuple(SimpleNamespace(arrival=a, route=tuple(r), durations=tuple(d), due=due) for a, r, d, due in rows)
    return SimpleNamespace(servers=servers, jobs=jobs, operation_count=sum(len(j.route) for j in jobs))


def sample():
    return workload([(0, [0], [3], 2), (1, [0], [2], 4), (1, [0], [1], 6)])


@pytest.mark.parametrize(
    "policy,starts,dispatch",
    [
        ("fifo", [0.0, 3.0, 5.0], [0, 1, 2]),
        ("spt", [0.0, 4.0, 3.0], [0, 2, 1]),
    ],
)
def test_hand_derived(policy, starts, dispatch):
    runner = NativeRunner(sample(), policy=policy, mode="kpi")
    runner.run()
    result = runner.result()
    assert result["starts"] == starts
    assert result["dispatch"] == dispatch
    assert result["makespan"] == 6
    assert result["completed"] == 3
    assert result["callbacks"] == result["records"] == 0
    assert result["kpis"]["busy"] == [6.0]
    assert result["kpis"]["total_flow"] == (12 if policy == "fifo" else 11)
    assert result["kpis"]["total_wait"] == (6 if policy == "fifo" else 5)
    assert result["kpis"]["total_tardiness"] == (2 if policy == "fifo" else 3)
    assert result["kpis"]["wip_area"] == result["kpis"]["total_flow"]
    assert result["kpis"]["max_wip"] == 3
    result["starts"][0] = 999
    assert runner.result()["starts"][0] == 0
    with pytest.raises(RuntimeError):
        runner.run()
    runner.close()
    runner.close()
    with pytest.raises(RuntimeError):
        runner.result()


@pytest.mark.parametrize("typed", [False, True])
def test_empty_and_lifecycle(typed):
    runner = NativeRunner(Workload(1, ()) if typed else workload())
    with pytest.raises(RuntimeError):
        runner.result()
    runner.run()
    result = runner.result()
    assert result["starts"] == result["ends"] == result["finished"] == []
    assert result["dispatch"] == []
    assert result["makespan"] == result["completed"] == 0
    assert result["kpis"] is None
    runner.close()
    never_run = NativeRunner(workload())
    never_run.close()
    with pytest.raises(RuntimeError):
        never_run.run()


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
def test_typed_and_checked_workloads_match(tmp_path, policy, mode):
    class DerivedWorkload(Workload):
        pass

    class DerivedJob(Job):
        pass

    data = sample()
    jobs = tuple(Job(**vars(job)) for job in data.jobs)
    variants = (
        data,
        Workload(1, jobs),
        DerivedWorkload(1, jobs),
        Workload(1, tuple(DerivedJob(**vars(job)) for job in data.jobs)),
    )
    results, recordings = [], []
    for index, variant in enumerate(variants):
        path = tmp_path / f"{index}.bin" if mode == "record" else None
        runner = NativeRunner(variant, policy=policy, mode=mode, trace_path=path)
        try:
            runner.run()
            results.append(runner.result())
        finally:
            runner.close()
        if path is not None:
            recordings.append(path.read_bytes())
    assert all(result == results[0] for result in results)
    assert results[0]["starts"] == ([0.0, 3.0, 5.0] if policy == "fifo" else [0.0, 4.0, 3.0])
    assert all(recording == recordings[0] for recording in recordings)


def test_input_storage_is_owned_after_construction():
    data = Workload(1, (Job(0, (0,), (3,), 2),))
    runner = NativeRunner(data)
    del data
    gc.collect()
    try:
        runner.run()
        result = runner.result()
        assert result["starts"] == [0.0]
        assert result["ends"] == result["finished"] == [3.0]
        assert result["dispatch"] == [0]
    finally:
        runner.close()


@pytest.mark.parametrize("mode", ["none", "kpi", "record"])
@pytest.mark.parametrize("empty", [False, True])
def test_result_lists_survive_close_and_are_independent(tmp_path, mode, empty):
    data = Workload(1, () if empty else (Job(0, (0,), (3,), 2),))
    runner = NativeRunner(data, mode=mode, trace_path=tmp_path / "result.bin" if mode == "record" else None)
    try:
        runner.run()
        first, second, expected = runner.result(), runner.result(), runner.result()
        for key in ("starts", "ends", "finished", "dispatch"):
            assert type(first[key]) is list
            first[key][:] = [999]
        if mode != "none":
            assert type(first["kpis"]["busy"]) is list
            first["kpis"]["busy"][:] = [999]
        assert second == expected == runner.result()
    finally:
        runner.close()
    del runner
    gc.collect()
    assert second == expected
    assert second["starts"] == ([] if empty else [0.0])
    assert second["ends"] == second["finished"] == ([] if empty else [3.0])


def test_hook_and_record(tmp_path):
    calls = []

    def hook(now, server, candidates):
        assert isinstance(candidates, tuple)
        assert all(isinstance(c, tuple) for c in candidates)
        calls.append((now, server, candidates))
        return min(candidates, key=lambda c: c[1])[0]

    path = tmp_path / "events.bin"
    runner = NativeRunner(sample(), mode="record", hook=hook, trace_path=path)
    runner.run()
    result = runner.result()
    assert result["starts"] == [0.0, 4.0, 3.0]
    assert result["callbacks"] == len(calls) == 3
    assert calls[1] == (3.0, 0, ((1, 2.0), (2, 1.0)))
    data = path.read_bytes()  # successful run must flush, before close
    assert data[:8] == b"NEVT0001"
    records = list(struct.iter_unpack("<dQIIII", data[8:]))
    assert result["records"] == len(records) == 15
    assert [r[1] for r in records] == list(range(15))
    assert [r[0] for r in records] == sorted(r[0] for r in records)
    runner.close()


@pytest.mark.parametrize("selection", [True, False, 1.0, None, -1, 999, 2**100])
def test_invalid_hook_result(selection):
    runner = NativeRunner(sample(), hook=lambda *args: selection)
    with pytest.raises((TypeError, ValueError)):
        runner.run()
    with pytest.raises(RuntimeError):
        runner.result()
    with pytest.raises(RuntimeError):
        runner.run()
    runner.close()


def test_hook_exception_preserved():
    error = LookupError("hook failure")

    def hook(*args):
        raise error

    runner = NativeRunner(sample(), hook=hook)
    with pytest.raises(LookupError) as caught:
        runner.run()
    assert caught.value is error
    runner.close()


@pytest.mark.parametrize(
    "rows,servers",
    [
        ([], 0),
        ([], True),
        ([], 0xFFFFFFFF),
        ([], 2**40),
        ([(True, [0], [1], 0)], 1),
        ([(0, [0], [True], 0)], 1),
        ([(0, [0], [1], True)], 1),
        ([(0, [-1], [1], 0)], 1),
        ([(0, [0xFFFFFFFF], [1], 0)], 1),
        ([(0, [2**40], [1], 0)], 1),
        ([(math.nan, [0], [1], 0)], 1),
        ([(0, [], [], 0)], 1),
        ([(0, [1], [1], 0)], 1),
        ([(0, [0], [0], 0)], 1),
        ([(0, [0], [math.inf], 0)], 1),
        ([(0, [0], [1], math.inf)], 1),
        ([(0, [0, 0], [1, 1], 0)], 1),
        ([(2, [0], [1], 0), (1, [0], [1], 0)], 1),
        ([(0, [0], [], 0)], 1),
        ([(0, [True], [1], 0)], 2),
    ],
)
def test_invalid_input(rows, servers):
    with pytest.raises((TypeError, ValueError)):
        NativeRunner(workload(rows, servers))


@pytest.mark.parametrize("kwargs", [dict(policy="bad"), dict(mode="bad"), dict(hook=1), dict(mode="record")])
def test_invalid_options(kwargs):
    with pytest.raises((TypeError, ValueError)):
        NativeRunner(sample(), **kwargs)


@pytest.mark.parametrize("arrival,duration", [(1e308, 1e308), (1e308, 1.0)])
def test_scheduling_overflow(arrival, duration):
    runner = NativeRunner(workload([(arrival, [0], [duration], 0)]))
    with pytest.raises(ValueError):
        runner.run()
    runner.close()


@pytest.mark.parametrize("method", ["close", "run", "result"])
def test_reentrant_lifecycle_rejected(method):
    runner = None

    def hook(*args):
        getattr(runner, method)()
        return 0

    runner = NativeRunner(sample(), hook=hook)
    with pytest.raises(RuntimeError):
        runner.run()
    with pytest.raises(RuntimeError):
        runner.result()
    runner.close()
    runner.close()


def test_nested_independent_runner_from_hook():
    nested = []

    def hook(now, server, candidates):
        inner = NativeRunner(sample(), mode="kpi", hook=lambda t, s, choices: min(choices, key=lambda c: c[1])[0])
        try:
            inner.run()
            nested.append(inner.result())
        finally:
            inner.close()
        return candidates[0][0]

    outer = NativeRunner(sample(), hook=hook)
    try:
        outer.run()
        result = outer.result()
        assert result["starts"] == [0.0, 3.0, 5.0]
        assert result["callbacks"] == len(nested) == 3
        assert all(r["starts"] == [0.0, 4.0, 3.0] and r["callbacks"] == 3 for r in nested)
    finally:
        outer.close()


@pytest.mark.skipif(os.name != "posix", reason="RLIMIT_FSIZE requires POSIX")
@pytest.mark.parametrize("jobs,limit", [(1, 16), (1000, 1024)], ids=["final-flush", "buffer-write"])
def test_record_write_failure_in_subprocess(tmp_path, jobs, limit):
    resource = pytest.importorskip("resource")
    if not hasattr(resource, "RLIMIT_FSIZE"):
        pytest.skip("RLIMIT_FSIZE is unavailable")
    script = textwrap.dedent("""
        import resource
        import signal
        import sys
        from prototypes.native_engine.model import Job, Workload
        from prototypes.native_engine.native import NativeRunner

        if not hasattr(signal, 'SIGXFSZ'):
            sys.exit(77)
        try:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            limit = int(sys.argv[3])
            if hard != resource.RLIM_INFINITY:
                limit = min(limit, hard)
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
        except (OSError, ValueError):
            sys.exit(77)
        data = Workload(1, (Job(0, (0,), (1,), 2000),) * int(sys.argv[2]))
        runner = NativeRunner(data, mode='record', trace_path=sys.argv[1])
        try:
            try:
                runner.run()
            except OSError as error:
                assert 'record' in str(error)
            else:
                raise AssertionError('record write failure was not reported')
            for method in (runner.result, runner.run):
                try:
                    method()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('failed runner remained usable')
        finally:
            runner.close()
            runner.close()
        try:
            runner.result()
        except RuntimeError:
            pass
        else:
            raise AssertionError('closed runner remained usable')
    """)
    path = tmp_path / "limited.bin"
    process = subprocess.run(
        [sys.executable, "-c", script, str(path), str(jobs), str(limit)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode == 77:
        pytest.skip("process cannot apply RLIMIT_FSIZE with SIGXFSZ ignored")
    assert process.returncode == 0, process.stdout + process.stderr
    assert path.stat().st_size <= limit


def test_index_protocol_hook():
    class Index:
        def __init__(self, value):
            self.value = value

        def __index__(self):
            return self.value

    runner = NativeRunner(sample(), hook=lambda now, server, candidates: Index(candidates[0][0]))
    runner.run()
    assert runner.result()["dispatch"] == [0, 1, 2]
    runner.close()


def test_randomized_reference_and_binary_parity(tmp_path):
    from prototypes.native_engine.model import Job, Workload, fifo_hook, spt_hook
    from prototypes.native_engine.reference import ReferenceRunner

    rng = random.Random(1827)
    native_path, reference_path = tmp_path / "native.bin", tmp_path / "reference.bin"
    for trial in range(200):
        servers = rng.randint(1, 7)
        jobs, now = [], 0.0
        for _ in range(rng.randint(0, 70)):
            now += rng.choice([0, 0, 1, 2]) if trial % 2 else rng.random()
            route = tuple(rng.sample(range(servers), rng.randint(1, servers)))
            durations = tuple(rng.randint(1, 4) if trial % 2 else rng.uniform(0.01, 4) for _ in route)
            jobs.append(Job(now, route, durations, now + rng.randint(0, 20)))
        data = Workload(servers, tuple(jobs))
        for policy, hook in (("fifo", fifo_hook), ("spt", spt_hook)):
            kwargs = dict(policy=policy, mode="record", hook=hook if trial % 3 else None)
            native = NativeRunner(data, trace_path=native_path, **kwargs)
            reference = ReferenceRunner(data, trace_path=reference_path, **kwargs)
            try:
                native.run()
                reference.run()
                actual, expected = native.result(), reference.result()
                for key in ("starts", "ends", "finished", "dispatch", "makespan", "completed", "callbacks", "records"):
                    assert actual[key] == expected[key], (trial, policy, key)
                for key, value in expected["kpis"].items():
                    assert actual["kpis"][key] == pytest.approx(value, rel=1e-12, abs=1e-10)
            finally:
                native.close()
                reference.close()
            assert native_path.read_bytes() == reference_path.read_bytes(), (trial, policy)


def test_record_buffer_boundary_and_modes(tmp_path):
    data = workload([(0.0, [0], [1.0], 2000.0) for _ in range(1000)])
    results = []
    for mode in ("none", "kpi", "record"):
        runner = NativeRunner(data, mode=mode, trace_path=tmp_path / "large.bin" if mode == "record" else None)
        runner.run()
        results.append(runner.result())
        runner.close()
    for key in ("starts", "ends", "dispatch", "finished", "completed", "makespan"):
        assert results[0][key] == results[1][key] == results[2][key]
    assert results[1]["kpis"] == results[2]["kpis"]
    assert len((tmp_path / "large.bin").read_bytes()) == 8 + 5000 * 32


def test_bad_paths_and_metadata(tmp_path):
    with pytest.raises(OSError):
        NativeRunner(sample(), mode="record", trace_path=tmp_path / "missing" / "trace.bin")
    with pytest.raises(ValueError):
        NativeRunner(sample(), trace_path=tmp_path / "ignored.bin")
    data = sample()
    data.operation_count += 1
    with pytest.raises(ValueError):
        NativeRunner(data)


@pytest.mark.parametrize("count", [-1, 0xFFFFFFFF, 2**40, True])
def test_invalid_operation_count(count):
    data = sample()
    data.operation_count = count
    with pytest.raises((TypeError, ValueError)):
        NativeRunner(data)


@pytest.mark.parametrize(
    "field,reported,actual", [("jobs", 2, 1), ("jobs", 0, 1), ("durations", 1, 0), ("durations", 1, 2)]
)
def test_inconsistent_container_lengths_rejected_before_c(monkeypatch, field, reported, actual):
    class Miscounted(list):
        def __len__(self):
            return reported

    def unsafe_library_load():
        raise AssertionError("inconsistent input must be rejected before calling C")

    # Do not enter C with undersized buffers, even in a regression test.
    monkeypatch.setattr("prototypes.native_engine.native._load_library", unsafe_library_load)
    data = workload([(0, [0], [1], 0)])
    if field == "jobs":
        data.jobs = Miscounted(data.jobs)
    else:
        data.jobs[0].durations = Miscounted([1] * actual)
    with pytest.raises(ValueError, match="lengths"):
        NativeRunner(data)


def test_kpi_overflow_does_not_change_trajectory(tmp_path):
    data = workload([(0, [0], [1e308], -1e308), (0, [1], [1e308], -1e308)], servers=2)
    results = []
    for mode in ("none", "kpi", "record"):
        runner = NativeRunner(data, mode=mode, trace_path=tmp_path / "huge.bin" if mode == "record" else None)
        runner.run()
        results.append(runner.result())
        runner.close()
    assert all(r["starts"] == [0.0, 0.0] and r["ends"] == [1e308, 1e308] for r in results)
    for result in results[1:]:
        assert math.isinf(result["kpis"]["total_flow"])
        assert math.isinf(result["kpis"]["total_tardiness"])
        assert math.isinf(result["kpis"]["wip_area"])
