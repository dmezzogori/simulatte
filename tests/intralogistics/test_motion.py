"""Motion descriptions and the movement events of AGVs (spec §6.4 ``agv.move_*``, §6.5)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveInterrupted, AgvMoveStarted
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.speed import LINEAR_MOTION, TrapezoidalProfile, describe_motion
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

from tests.intralogistics.test_entities import SKU_A, _agv_type, _line_graph, _system, _warehouses
from tests.intralogistics.test_fleet_events import FleetReplay


def _position(description: Any, t: float) -> tuple[float, float, float]:
    """Position and speed at time `t` along a trapezoidal description, integrated phase by phase, and the time at
    which the curve stops."""
    v_max, accel, decel, distance = (description[k] for k in ("v_max", "accel", "decel", "distance"))
    v_peak = min(v_max, math.sqrt(2 * distance * accel * decel / (accel + decel)))
    t_accel, t_decel = v_peak / accel, v_peak / decel
    d_accel, d_decel = v_peak**2 / (2 * accel), v_peak**2 / (2 * decel)
    t_cruise = (distance - d_accel - d_decel) / v_peak
    duration = t_accel + t_cruise + t_decel
    if t <= t_accel:
        return accel * t**2 / 2, accel * t, duration
    if t <= t_accel + t_cruise:
        return d_accel + v_peak * (t - t_accel), v_peak, duration
    s = t - t_accel - t_cruise
    return d_accel + v_peak * t_cruise + v_peak * s - decel * s**2 / 2, v_peak - decel * s, duration


@pytest.mark.parametrize(
    ("profile", "distance", "load_weight", "battery_level", "speed_limit"),
    [
        (TrapezoidalProfile(2.0, 1.0, 1.0), 10.0, 0.0, 1.0, None),  # cruise phase
        (TrapezoidalProfile(2.0, 1.0, 1.5), 1.0, 0.0, 1.0, None),  # triangular: v_max not reached
        (TrapezoidalProfile(3.0, 0.5, 2.0), 25.0, 0.0, 0.8, 1.2),  # battery-scaled, speed-limited
        (TrapezoidalProfile(3.0, 1.0, 1.0, load_speed_factor_fn=lambda w: 1 / (1 + w)), 12.0, 3.0, 1.0, None),
    ],
)
def test_trapezoidal_motion_integrates_to_travel_time(
    profile: TrapezoidalProfile, distance: float, load_weight: float, battery_level: float, speed_limit: float | None
) -> None:
    travel_time = profile.travel_time(distance, load_weight, battery_level, speed_limit)
    description = profile.motion(distance, load_weight, battery_level, speed_limit)
    assert description["curve"] == "trapezoidal" and description["distance"] == distance

    position, speed, duration = _position(description, travel_time)
    assert abs(duration - travel_time) <= 1e-9
    assert abs(position - distance) <= 1e-9
    assert abs(speed) <= 1e-9
    samples = [_position(description, travel_time * i / 100)[0] for i in range(101)]
    assert samples == sorted(samples)  # never moves backwards


def test_motion_reuses_the_factors_of_travel_time() -> None:
    calls: list[str] = []
    profile = TrapezoidalProfile(
        2.0,
        1.0,
        1.0,
        battery_degradation_fn=lambda level: calls.append("battery") or level,
        load_speed_factor_fn=lambda weight: calls.append("load") or 1.0,
    )
    profile.travel_time(10.0, 0.0, 0.5)
    assert calls == ["battery", "load"]
    assert profile.motion(10.0, 0.0, 0.5) == {
        "curve": "trapezoidal",
        "v_max": 1.0,
        "accel": 0.5,
        "decel": 1.0,
        "distance": 10.0,
    }
    assert calls == ["battery", "load"]  # same segment: no second call of the user functions
    profile.motion(10.0, 0.0, 0.25)  # other arguments: computed afresh
    assert calls == ["battery", "load", "battery", "load"]


def test_motion_edge_cases() -> None:
    profile = TrapezoidalProfile(2.0, 1.0, 1.0, battery_degradation_fn=lambda level: 0.0)
    assert profile.motion(0.0) == {"curve": "trapezoidal", "v_max": 2.0, "accel": 1.0, "decel": 1.0, "distance": 0.0}
    assert profile.travel_time(5.0) == math.inf  # a non-positive factor stalls the AGV
    assert profile.motion(5.0)["v_max"] == 0.0

    class Constant:
        def travel_time(self, distance: float, *args: object, **kwargs: object) -> float:
            return distance

        def motion(self, distance: float, *args: object) -> dict[str, object]:
            return {"curve": "constant", "speed": 1.0, "distance": distance}

    class Opaque:
        def travel_time(self, distance: float, *args: object, **kwargs: object) -> float:
            return distance

    assert describe_motion(Constant(), 3.0, 0.0, 1.0, None) == {"curve": "constant", "speed": 1.0, "distance": 3.0}
    assert describe_motion(Opaque(), 3.0, 0.0, 1.0, None) is LINEAR_MOTION


def test_move_events_carry_the_segment() -> None:
    env = Environment(debug=True)
    replay = FleetReplay(env)
    coordinator, (agv,), wh_a, wh_b, nodes = _system(env)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, (AgvMoveStarted, AgvMoveEnded))
    observed: list[Any] = []
    env.bus.subscribe(lambda event: observed.append(agv.snapshot()["motion"]), (AgvMoveStarted,))
    coordinator.submit(coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b))
    env.run()

    started, ended = seen[0], seen[1]
    assert isinstance(started, AgvMoveStarted) and isinstance(ended, AgvMoveEnded)
    assert (started.t, started.from_node, started.to_node, started.loaded) == (0, "C1", "OUT", False)
    assert started.t_end == 4.5
    assert started.motion == {"curve": "trapezoidal", "v_max": 2.0, "accel": 1.0, "decel": 1.0, "distance": 5.0}
    assert observed[0] == {"from": "C1", "to": "OUT", "t_start": 0.0, "t_end": 4.5, "description": started.motion}
    assert (ended.t, ended.node, ended.battery) == (4.5, "OUT", 995.0)
    assert ended.deltas.ops == (
        ("set", agv.id, "node", "OUT"),
        ("remove", "C1", "agvs", agv.id),
        ("insert", "OUT", "agvs", 0, agv.id),
        ("set", agv.id, "battery", 995.0),
        ("set", agv.id, "motion", None),
    )
    assert [e.loaded for e in seen if isinstance(e, AgvMoveStarted)] == [False, True, True, True]
    assert agv.motion is None and replay.events > 10


def test_move_started_after_enter_permission() -> None:
    """With capacity-1 nodes, an AGV starts its segment only once the traffic manager admitted it to the next node."""
    env = Environment(debug=True)
    FleetReplay(env)
    nodes, graph = _line_graph()
    out, c1, c2, end = nodes
    blocker, waiter = (AGV(env=env, agv_type=_agv_type(), initial_node=node) for node in (c1, out))
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=[blocker, waiter], warehouses=[], charging_stations=[], traffic_manager=traffic
    )
    started: list[tuple[float, str, str, str]] = []

    def on_started(event: AgvMoveStarted) -> None:
        agv = blocker if event.agv == blocker.id else waiter
        to_node = next(node for node in nodes if node.id == event.to_node)
        assert traffic._node_requests[(agv, to_node)].triggered  # admitted before the segment starts
        started.append((event.t, event.agv, event.from_node, event.to_node))

    env.bus.subscribe(on_started, (AgvMoveStarted,))
    env.activate()
    # The waiter plans first (no conflicting intents) and then waits for C1, which the blocker holds until it
    # reaches C2.
    env.process(coordinator._travel(waiter, out, c1, loaded=False))
    env.process(coordinator._travel(blocker, c1, end, loaded=False))
    env.run()

    assert started[0] == (0, blocker.id, "C1", "C2")
    assert sorted(started[1:]) == [(4.5, blocker.id, "C2", "IN"), (4.5, waiter.id, "OUT", "C1")]  # C1 freed at 4.5
    assert (waiter.current_node, blocker.current_node) == (c1, end)


def test_shared_profile_is_described_with_the_values_of_its_travel_time() -> None:
    """The description is taken when the travel time is computed, before waiting for the traffic manager, so another
    AGV using the same profile in between does not make the factor functions run again."""
    env = Environment(debug=True)
    calls = {"travel_time": 0, "factor": 0}

    class Counting(TrapezoidalProfile):
        def travel_time(self, *args: Any, **kwargs: Any) -> float:
            calls["travel_time"] += 1
            return super().travel_time(*args, **kwargs)

    def factor(level: float) -> float:
        calls["factor"] += 1
        return 1.0

    shared = AGVType(
        name="shared",
        speed_profile=Counting(2.0, 1.0, 1.0, battery_degradation_fn=factor),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
    )
    nodes, graph = _line_graph()
    out, c1, _, end = nodes
    blocker, waiter = (AGV(env=env, agv_type=shared, initial_node=node) for node in (c1, out))
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=[blocker, waiter], warehouses=[], charging_stations=[], traffic_manager=traffic
    )
    started: list[AgvMoveStarted] = []
    env.bus.subscribe(started.append, (AgvMoveStarted,))
    env.activate()
    env.process(coordinator._travel(waiter, out, c1, loaded=False))
    env.process(coordinator._travel(blocker, c1, end, loaded=False))
    env.run()

    assert len(started) == 3 and calls["factor"] == calls["travel_time"] == 3
    assert all(event.motion["v_max"] == 2.0 for event in started)


@pytest.mark.parametrize("cause", ["cancelled", "breakdown", None])
def test_interrupted_move_keeps_previous_node(cause: str | None) -> None:
    env = Environment(debug=True)
    replay = FleetReplay(env)
    coordinator, (agv,), wh_a, wh_b, nodes = _system(env)
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    interrupted: list[AgvMoveInterrupted] = []
    env.bus.subscribe(interrupted.append, (AgvMoveInterrupted,))
    coordinator.submit(order)
    env.run(until=2.0)  # half-way through C1 -> OUT
    assert agv.motion is not None and agv.motion["to"] == "OUT"

    if cause == "cancelled":
        coordinator.cancel(order)
    else:
        coordinator._active_missions[order.id].interrupt(cause)
    env.run(until=2.0 + 1e-9)

    first = interrupted[0]
    assert (first.t, first.agv, first.node, first.reason) == (2.0, agv.id, "C1", cause or "interrupted")
    assert first.deltas.ops == (("set", agv.id, "motion", None),)
    assert agv.current_node == nodes[1] and env.entities.bind_node(nodes[1]).agvs[0] == agv.id
    env.run()
    if cause == "cancelled":
        assert order.status is OrderStatus.CANCELLED and agv.state is AGVState.IDLE
    else:
        assert order.status is OrderStatus.COMPLETED  # re-queued and dispatched again
    assert replay.events > 5


def test_stalled_segment_ends_at_infinity() -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    nodes, graph = _line_graph()
    wh_a, wh_b = _warehouses(env, nodes)
    stalled = TrapezoidalProfile(2.0, 1.0, 1.0, load_speed_factor_fn=lambda weight: 0.0)
    agv_type = AGVType(
        name="stalled", speed_profile=stalled, battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0
    )
    agv = AGV(env=env, agv_type=agv_type, initial_node=nodes[1])
    coordinator = FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[wh_a, wh_b], charging_stations=[])
    started: list[AgvMoveStarted] = []
    env.bus.subscribe(started.append, (AgvMoveStarted,))
    coordinator.submit(coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b))
    env.run(until=100)

    assert started[0].t_end == math.inf
    assert agv.motion is not None
    assert (agv.motion["t_end"], agv.motion["stalled"]) == (math.inf, True)
