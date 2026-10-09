"""Reading AGV utilization getters must not change AGV accounting (spec §13, observer purity)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import simpy

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.speed import TrapezoidalProfile

# Non-dyadic durations so that float accumulation differs when intervals are split.
_SCHEDULE: list[tuple[AGVState, float]] = [
    (AGVState.TRAVELING_EMPTY, 0.3),
    (AGVState.WAITING_LOAD, 0.7),
    (AGVState.TRAVELING_LOADED, 1.3),
    (AGVState.WAITING_UNLOAD, 0.1),
    (AGVState.CHARGING, 2.9),
    (AGVState.IDLE, 0.4),
]


def _build(env: Environment, profile: TrapezoidalProfile) -> AGV:
    agv_type = AGVType(
        name="test",
        speed_profile=profile,
        battery_capacity=100.0,
        weight_capacity=500.0,
        volume_capacity=2.0,
    )
    return AGV(env=env, agv_type=agv_type)


def _driver(env: Environment, agv: AGV) -> Generator[simpy.Event, None, None]:
    for _ in range(20):
        for state, duration in _SCHEDULE:
            agv.transition_to(state)
            yield env.timeout(duration)


def _reader(env: Environment, agv: AGV) -> Generator[simpy.Event, None, None]:
    for _ in range(1000):
        yield env.timeout(0.1)
        agv.utilization()
        agv.state_percentage(AGVState.CHARGING)
        agv.time_allocation()


def _results(agv: AGV) -> tuple[float, dict[AGVState, float], dict[AGVState, float]]:
    return (
        agv.utilization(),
        {s: agv.state_percentage(s) for s in AGVState},
        agv.time_allocation(),
    )


@pytest.mark.parametrize("stop", [2.5, 50.05, 100.05])
def test_reading_utilization_does_not_change_state(simple_speed_profile: TrapezoidalProfile, stop: float) -> None:
    # (a) Reading the getters mid-run leaves state_durations and _state_entered_at untouched.
    env = Environment()
    agv = _build(env, simple_speed_profile)
    env.process(_driver(env, agv))
    env.run(until=stop)

    durations_before = dict(agv.state_durations)
    entered_before = agv._state_entered_at
    agv.utilization()
    agv.state_percentage(AGVState.TRAVELING_EMPTY)
    agv.time_allocation()
    assert agv.state_durations == durations_before
    assert agv._state_entered_at == entered_before

    # (b) A run read 1000 times at intermediate times matches an identical run read only at the end.
    # The reader reads every 0.1 time units (1000 reads, the last near t=100). Stopping at `stop` with
    # stop > 100 (e.g. 100.05) leaves the reader's reads inside the run, so it covers all mid-run reads.
    # Smaller stops only see the reads that happened before them.
    env_read = Environment()
    agv_read = _build(env_read, simple_speed_profile)
    env_read.process(_driver(env_read, agv_read))
    env_read.process(_reader(env_read, agv_read))
    env_read.run(until=stop)

    env_quiet = Environment()
    agv_quiet = _build(env_quiet, simple_speed_profile)
    env_quiet.process(_driver(env_quiet, agv_quiet))
    env_quiet.run(until=stop)

    assert agv_read.state_durations == agv_quiet.state_durations
    assert agv_read._state_entered_at == agv_quiet._state_entered_at
    assert _results(agv_read) == _results(agv_quiet)
