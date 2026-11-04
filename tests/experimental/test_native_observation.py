"""Packaged native KPI observations are bounded and leave results unchanged."""

import math

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.events import KpiSample
from simulatte.experimental.native import Job, NativeObservation, NativeProduction
from simulatte.experimental.native_identity import compose_native_identity
from simulatte.stopping import Horizon, execute_until


@pytest.mark.parametrize("interval", [0, -1, True, float("inf"), float("nan"), "1"])
def test_invalid_interval(interval):
    with pytest.raises(ValueError, match="interval"):
        NativeObservation(interval=interval)


@pytest.mark.parametrize("limit", [0, 1, True, 4097, 2.5])
def test_invalid_limit(limit):
    with pytest.raises(ValueError, match="max_samples"):
        NativeObservation(interval=1, max_samples=limit)


@pytest.mark.parametrize(
    "interval,limit,expected",
    [(1, 1000, (2, 3, 4, 5, 6, 7, 8, 9, 10)), (0.5, 5, (2, 4, 6, 8, 10)), (20, 5, (2, 10)), (3, 5, (2, 5, 8, 10))],
)
def test_schedule_covers_whole_window(interval, limit, expected):
    plan = NativeObservation(interval=interval, max_samples=limit)
    assert plan.times(Horizon(10, warmup=2)) == expected


def test_extreme_floating_point_schedule_stays_finite_distinct_and_bounded():
    for policy in (Horizon(1e308, warmup=1e307), Horizon(math.nextafter(1e308, math.inf), warmup=1e308)):
        times = NativeObservation(interval=1e-300, max_samples=4096).times(policy)
        assert 2 <= len(times) <= 4096
        assert times[0] == policy.warmup and times[-1] == policy.until
        assert all(math.isfinite(v) for v in times)
        assert all(a < b for a, b in zip(times, times[1:]))


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_cadence_and_recording_do_not_change_native_or_python_results(tmp_path, retention, dynamic):
    pytest.importorskip("simulatte_native")
    outputs = []
    for i, (observation, record) in enumerate(
        (
            (None, False),
            (None, True),
            (NativeObservation(interval=0.25), True),
            (NativeObservation(interval=0.25, max_samples=5), True),
        )
    ):
        jobs = tuple(Job(j // 3 * 0.5, (j % 3, (j + 1) % 3), (1, 0.5), 4 + j) for j in range(24))
        cursor, seen = [0], []
        with Environment(seed=0) as env:
            policy = Horizon(20, warmup=2)
            env._execution_policy = policy
            env.configure_kpis(warmup=2)
            path = tmp_path / f"{i}.simtrace"
            recorder = TraceRecorder(env, path, level="kpi") if record else None
            backend = NativeProduction(
                3,
                () if dynamic else jobs,
                capacities=(2, 1, 3),
                retention=retention,
                observation=observation,
                input_state=(lambda: dict(cursor=cursor[0])) if dynamic else None,
            )
            env.attach_execution_backend(backend)
            env.enable_digest()
            if dynamic:

                def source():
                    for j, job in enumerate(jobs):
                        yield env.timeout(job.arrival - env.now)
                        backend.add(job)
                        cursor[0] = j + 1

                env.process(source())
            if observation:
                env.bus.subscribe(lambda event: seen.append((event.t, event.kpi, event.value)), (KpiSample,))
            execute_until(env, policy)
            result = (env.fingerprint(), backend.state_digest(), cursor[0])
            outputs.append(result)
            if recorder:
                recorder.close()
                trace = Trace.open(path)
                assert trace.fingerprint == env.fingerprint()
                if observation:
                    assert sorted({at for at, _, _ in seen}) == list(observation.times(policy))
                    assert len(seen) == len(observation.times(policy)) * 8
                    assert trace.kpi_declarations["shopfloor/utilization"]["kind"] == ("scalar", "series")
                    assert [cursor[0] for cursor, _ in trace.kpi_series()["shopfloor/utilization"]] == list(
                        observation.times(policy)
                    )
    assert all(output == outputs[0] for output in outputs)


def test_observation_disabled_without_subscriber():
    pytest.importorskip("simulatte_native")
    with Environment() as env:
        env._execution_policy = Horizon(10)
        backend = NativeProduction(1, [Job(0, (0,), (1,), 2)], observation=NativeObservation(interval=0.1))
        env.attach_execution_backend(backend)
        original, calls = backend.kpi_values, []

        def values():
            calls.append(env.now)
            return original()

        backend.kpi_values = values
        execute_until(env, env._execution_policy)
        assert calls and all(at == 10 for at in calls)


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_reporting_identity_binds_cadence_while_native_authority_stays_the_same(retention):
    pytest.importorskip("simulatte_native")
    values = []
    for observation in (None, NativeObservation(interval=1), NativeObservation(interval=2)):
        with Environment() as env:
            env._execution_policy = Horizon(10)
            backend = NativeProduction(2, capacities=(2, 1), retention=retention, observation=observation)
            env.attach_execution_backend(backend)
            view = backend.state_projection()
            if observation:
                assert view["reporting"]["configuration"]["observation"] == observation.to_dict(env._execution_policy)
            else:
                assert "observation" not in view["reporting"]["configuration"]
            values.append(compose_native_identity(view, actions={}).to_dict())
    assert all(value["authority"] == values[0]["authority"] for value in values)
    assert all(value["trajectory_control"] == values[0]["trajectory_control"] for value in values)
    assert len({value["reporting"]["digest"] for value in values}) == 3
    assert len({value["reproduction_state"]["digest"] for value in values}) == 3
