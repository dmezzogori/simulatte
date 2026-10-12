"""Explicit finite populations stop at drain, independently of housekeeping events."""

from __future__ import annotations

import importlib

import pytest

from simulatte import Environment, FinitePopulation, Horizon, Trace, TraceRecorder
from simulatte.kpi import observation_window


def api():
    module = importlib.import_module("simulatte.stopping")
    assert hasattr(module, "Population"), "Population driver is missing"
    return module


def test_count_population_drains_before_housekeeping(tmp_path):
    m = api()
    policy = FinitePopulation(arrivals=2, safety_limit=20)
    env = Environment(seed=1)
    pop = m.Population(env, policy)
    recorder = TraceRecorder(env, tmp_path / "run.simtrace")

    def arrivals():
        assert pop.admit("a")
        yield env.timeout(1)
        assert pop.admit("b")
        assert not pop.admit("c")
        yield env.timeout(2)
        pop.complete("a")
        pop.complete("b")

    def timer():
        while True:
            yield env.timeout(1)

    env.process(arrivals())
    env.process(timer())
    result = m.execute_until(env, policy)
    assert result.actual_end == 3
    assert result.reason == "drained"
    assert env.now == 3
    assert not pop.open and pop.closed.triggered
    assert observation_window(env).end == 3 and observation_window(env).closed
    final = env.manifest().final
    assert final is not None
    assert final["stopping_policy"] == policy.to_dict()
    recorder.close()
    trace = Trace.open(tmp_path / "run.simtrace")
    trace.check()
    assert trace.verify() is True
    env.close()


def test_time_cutoff_rejects_arrival_at_boundary():
    m = api()
    policy = FinitePopulation(cutoff=2, safety_limit=5)
    with Environment(seed=1) as env:
        pop = m.Population(env, policy)
        seen = []

        def arrival():
            pop.pending("scheduled")
            yield env.timeout(2)
            seen.append(pop.admit("boundary"))
            pop.unpending("scheduled")

        env.process(arrival())
        result = m.execute_until(env, policy)
        assert seen == [False]
        assert result.actual_end == 2


@pytest.mark.parametrize(
    "policy", [FinitePopulation(arrivals=0, safety_limit=4), FinitePopulation(cutoff=0, safety_limit=4)]
)
def test_empty_population_ends_at_zero(policy):
    m = api()
    with Environment(seed=1) as env:
        m.Population(env, policy)
        result = m.execute_until(env, policy)
        assert result.actual_end == 0
        assert observation_window(env).length == 0


def test_safety_boundary_completion_succeeds():
    m = api()
    policy = FinitePopulation(arrivals=1, safety_limit=3)
    with Environment(seed=1) as env:
        pop = m.Population(env, policy)

        def job():
            pop.admit("job")
            yield env.timeout(3)
            pop.complete("job")

        env.process(job())
        assert m.execute_until(env, policy).actual_end == 3


@pytest.mark.parametrize(
    "mode,code", [("never_admitted", "population_incomplete"), ("active", "safety_limit"), ("pending", "safety_limit")]
)
def test_incomplete_population_is_not_success(mode, code):
    m = api()
    policy = FinitePopulation(arrivals=1, safety_limit=3)
    with Environment(seed=1) as env:
        pop = m.Population(env, policy)
        if mode != "never_admitted":
            pop.admit("a")
            if mode == "pending":
                pop.complete("a")
                pop.pending("later")
            env.timeout(20)
        with pytest.raises(m.StoppingError, match=code):
            m.execute_until(env, policy)
        assert env._run_failed


def test_duplicate_population_members_and_completion():
    m = api()
    policy = FinitePopulation(arrivals=2, safety_limit=3)
    with Environment(seed=1) as env:
        pop = m.Population(env, policy)
        pop.admit("a")
        with pytest.raises(ValueError):
            pop.admit("a")
        pop.complete("a")
        with pytest.raises(ValueError):
            pop.complete("a")
        with pytest.raises(ValueError):
            pop.complete("unknown")
        pop.pending("later")
        with pytest.raises(ValueError):
            pop.pending("later")
        pop.unpending("later")
        with pytest.raises(ValueError):
            pop.unpending("later")
        with pytest.raises(ValueError):
            m.Population(env, policy)


def test_horizon_driver_matches_plain_run_half_open_and_parameters():
    m = api()
    states = []
    for managed in (False, True):
        with Environment(seed=1, parameters={"rate": 1.0}) as env:
            env.enable_digest()
            seen = []

            def proc():
                yield env.timeout(2)
                seen.append(env.now)

            env.process(proc())
            if managed:
                m.execute_until(env, Horizon(2, warmup=1))
            else:
                env.configure_kpis(warmup=1)
                env.run(until=2)
            assert seen == []
            window = observation_window(env)
            assert (window.start, window.end, window.closed) == (1, 2, False)
            assert env.manifest().requested["parameters"] == {"rate": 1.0}
            states.append(env.fingerprint())
    assert states[0] == states[1]


def test_driver_control_cancels_nonadvancing_work():
    m = api()
    with Environment(seed=1) as env:
        checks = []

        def control():
            checks.append(1)
            if len(checks) == 5:
                raise TimeoutError("deadline")

        def loop():
            while True:
                yield env.timeout(0)

        env.process(loop())
        with pytest.raises(TimeoutError):
            m.execute_until(env, Horizon(10), control)
        assert env._run_failed
