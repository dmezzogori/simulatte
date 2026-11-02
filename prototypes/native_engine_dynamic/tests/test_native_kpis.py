"""Native stock scalars against the real, unmodified production collector."""

import math
import random

import pytest

from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.native_kpis import NativeKPIStream, ProductionKPIStream


def compare(jobs, *, servers=3, policy="spt", cap=2, warmup=0, boundaries=()):
    native = NativeKPIStream(servers, policy=policy, wip_cap=cap, warmup=warmup)
    oracle = ProductionKPIStream(servers, policy=policy, wip_cap=cap, warmup=warmup)
    snapshots = []
    try:
        assert native.snapshot() == oracle.snapshot()
        by_time = {}
        for job in jobs:
            by_time.setdefault(job.arrival, []).append(job)
        for t in sorted(set(by_time) | set(boundaries)):
            native.advance(t)
            oracle.advance(t)
            assert native.snapshot() == oracle.snapshot(), t
            for job in by_time.get(t, ()):
                native.add(job)
                oracle.add(job)
            first = native.snapshot()
            assert first == native.snapshot() == oracle.snapshot(), t
            snapshots.append(first)
        native.run()
        oracle.run()
        assert native.snapshot() == oracle.snapshot()
        a, b = native.result(), oracle.result()
        for key in ("starts", "ends", "dispatch", "released", "finished", "completed", "makespan"):
            assert a[key] == b[key], key
        assert a["records"] == a["callbacks"] == 0 and a["kpis"] is None
        assert native.snapshot() == oracle.snapshot()
        return snapshots, native.snapshot()
    finally:
        native.close()
        oracle.close()


@pytest.mark.parametrize("warmup", [0, 1, 2, 3, 10])
def test_partial_windows_cohorts_idle_tail(warmup):
    snapshots, final = compare(
        [Job(0, (0,), (2,), 1), Job(0, (0,), (1,), 4)],
        servers=1,
        cap=1,
        warmup=warmup,
        boundaries=(0.5, 1, 2, 2.5, 3, 4, 8),
    )
    assert final["window"] == (float(warmup), max(8.0, warmup), warmup <= 8)
    if warmup == 0:
        assert snapshots[1]["scalars"] == {
            "shopfloor/throughput": 0.0,
            "shopfloor/utilization": 1.0,
            "shopfloor/jobs_in_system": 1.0,
        }
        assert final["scalars"]["shopfloor/makespan"] == 2.5
        assert final["scalars"]["shopfloor/jobs_in_system"] == 3 / 8


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("cap", [1, 2, 6])
@pytest.mark.parametrize("warmup", [0, 1.5, 3, 20])
@pytest.mark.parametrize("continuous", [False, True])
def test_ties_and_continuous_times(policy, cap, warmup, continuous):
    rng = random.Random(812)
    jobs = []
    at = 0.0
    for _ in range(40):
        at += rng.random() / 3 if continuous else rng.choice((0, 0, 0.5))
        n = rng.randrange(1, 4)
        route = tuple(rng.sample(range(3), n))
        durations = tuple(rng.random() + 0.1 if continuous else rng.choice((0.5, 1, 2)) for _ in route)
        jobs.append(Job(at, route, durations, rng.choice((-1, 2, 10))))
    compare(jobs, policy=policy, cap=cap, warmup=warmup, boundaries=(1, 2, 4, 8, 12))


def test_empty_and_zero_length_completion_window():
    assert compare([], warmup=2, boundaries=(1,))[1] == {"window": (2.0, 2.0, False), "scalars": {}}
    _, final = compare([Job(0, (0,), (2,), 3)], servers=1, warmup=2)
    assert len(final["scalars"]) == 5 and final["window"] == (2.0, 2.0, True)


def test_nonfinite_scalar_preserves_production_collector_behavior():
    _, final = compare([Job(0, (0,), (1e308,), -1e308)], servers=1)
    assert final["scalars"]["shopfloor/lateness"] == math.inf


def test_snapshot_detached_and_lifecycle():
    stream = NativeKPIStream(1)
    first = stream.snapshot()
    first["scalars"]["corrupt"] = 1
    assert stream.snapshot()["scalars"] == {}
    stream.close()
    stream.close()
    with pytest.raises(RuntimeError):
        stream.snapshot()


@pytest.mark.parametrize("warmup", [-1, math.inf, math.nan, True, "1"])
def test_invalid_warmup(warmup):
    with pytest.raises((TypeError, ValueError)):
        NativeKPIStream(1, warmup=warmup)


def test_exact_sum_rounding_growth_and_special_values():
    import ctypes as ct
    from simulatte.kpi import _ExactSum
    from prototypes.native_engine_dynamic.build import LIBRARY

    class Sum(ct.Structure):
        _fields_ = [
            ("p", ct.POINTER(ct.c_double)),
            ("n", ct.c_size_t),
            ("capacity", ct.c_size_t),
            ("special", ct.c_double),
        ]

    lib = ct.CDLL(str(LIBRARY))
    lib.nd_sum_add.argtypes = [ct.POINTER(Sum), ct.c_double]
    lib.nd_sum_add.restype = ct.c_int
    lib.nd_sum_value.argtypes = [ct.POINTER(Sum)]
    lib.nd_sum_value.restype = ct.c_double
    lib.nd_sum_destroy.argtypes = [ct.POINTER(Sum)]
    rng = random.Random(912)
    sequences = [
        [1e-16, 1, 1e16],
        [1e16, 1, -1e16],
        [1e308, 1e308, -1e308],
        [math.inf, -math.inf],
        [math.nan, 1],
        [-0.0, 0.0],
        [2.0 ** (-54 * i) for i in range(20)],
        [rng.uniform(-1, 1) * 2.0 ** rng.randrange(-500, 500) for _ in range(1000)],
    ]
    for terms in sequences:
        native, oracle = Sum(), _ExactSum()
        try:
            for term in terms:
                assert lib.nd_sum_add(ct.byref(native), term) == 0
                oracle.add(term)
                actual, expected = lib.nd_sum_value(ct.byref(native)), oracle.value()
                assert actual == expected or math.isnan(actual) and math.isnan(expected)
        finally:
            lib.nd_sum_destroy(ct.byref(native))


def test_polling_does_not_change_final_values():
    jobs = [Job(0, (0, 1), (0.1, 0.3), 0.2), Job(0, (1, 0), (0.2, 0.7), 2)]
    assert compare(jobs, servers=2, boundaries=tuple(i / 100 for i in range(1, 90)))[1] == compare(jobs, servers=2)[1]


def test_no_python_publisher_or_journal(monkeypatch):
    from simulatte import Environment, TraceRecorder
    from prototypes.native_engine_dynamic import live_observation

    def forbidden(*args, **kwargs):
        raise AssertionError("native scalars invoked Python observation machinery")

    monkeypatch.setattr(Environment, "__init__", forbidden)
    monkeypatch.setattr(TraceRecorder, "__init__", forbidden)
    monkeypatch.setattr(live_observation, "LiveTraceStream", forbidden)
    stream = NativeKPIStream(1)
    try:
        stream.add(Job(0, (0,), (1,), 0))
        stream.run()
        assert len(stream.snapshot()["scalars"]) == 8
        assert stream.result()["records"] == 0
    finally:
        stream.close()


def test_failed_engine_rejects_snapshot_and_optional_configuration():
    from prototypes.native_engine_dynamic.compiled import CompiledStream

    stream = CompiledStream(1)
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            stream._module.stock_kpis(stream._engine)
        stream.add(Job(0, (0,), (1,), 0))
        with pytest.raises(ValueError):
            stream._module.enable_stock_kpis(stream._engine, 0)
        with pytest.raises(RuntimeError):
            stream._module.stock_kpis(stream._engine)
    finally:
        stream.close()
