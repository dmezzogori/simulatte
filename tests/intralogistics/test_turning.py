"""Turning at nodes (spec §9.4): corner speed and turn time.

``AGVType.corner_speed`` caps the speed through a node and ``AGVType.turn_time`` makes the vehicle stop there and wait
before it leaves. Both receive the heading change in radians, in ``[0, pi]``, computed from the chords of the incoming
and outgoing arcs; the heading of an AGV is that of the last arc it traversed and is unknown after placement, which
turns for free. A turn wait is a wait at rest on the node; it is not a traffic wait.

The L-shaped route is two 10 m arcs at a right angle, driven at ``Kinematics(1.5, 0.8, 0.8)``.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvMoveStarted,
    AgvRouted,
    AgvStranded,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
)
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import Disposition, Reroute
from simulatte.intralogistics.travel import TravelOutcome
from tests.intralogistics.test_travel import ARC, Plain, Scripted, World, vehicle, world
from tests.intralogistics.traffic_support import FullReplay, StillFleet, TrafficInvariants, build
from tests.intralogistics.traffic_support import World as Declared

RIGHT = math.pi / 2


class Spy:
    """A callable of the heading change that records its arguments and returns ``fn(angle)``."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.angles: list[float] = []

    def __call__(self, angle: float) -> float:
        self.angles.append(angle)
        return self.fn(angle)


def proportional(angle: float) -> float:
    """2 s for a right angle, in proportion to the angle."""
    return 2.0 * angle / RIGHT


def turning(**options: Any) -> AGVType:
    return replace(vehicle(), **options)


def l_shape(env: Environment) -> Declared:
    """``N0`` to ``N1`` eastwards and ``N1`` to ``N2`` northwards, 10 m each."""
    return build(env, [("N0", 0, 0), ("N1", 10, 0), ("N2", 10, 10)], [("N0", "N1"), ("N1", "N2")])


def arc_times(w: World, agv: str = "A") -> list[tuple[str, float]]:
    return [(e.node, e.t) for e in w.of(AgvMoveEnded, agv=agv)]


def run_l(agv_type: AGVType, *, reserve: bool) -> World:
    w = world(l_shape, {"A": "N0"}, reserve=reserve, agv_type=agv_type)
    w.go("A", "N2")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    return w


# --- corner speed ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reserve", [False, True])
def test_a_corner_speed_caps_the_speed_through_the_corner(reserve: bool) -> None:
    spy = Spy(lambda angle: 0.5)
    w = run_l(turning(corner_speed=spy), reserve=reserve)
    first, second = w.of(AgvMoveStarted)
    # 8.020833333333334 by hand; the computed value is one unit in the last place below
    assert [e.t for e in w.of(AgvMoveEnded)] == [8.020833333333332, 16.041666666666664]
    assert second.t == first.t_end == pytest.approx(8.020833333333334, rel=1e-12)  # passed without a wait
    assert second.motion["v0"] == 0.5  # the speed at the corner
    assert second.t_end - second.t == 8.020833333333332
    assert spy.angles == [pytest.approx(RIGHT)]
    assert not w.of(TrafficWaitStarted)


def test_a_corner_speed_of_zero_stops_at_the_corner_and_infinity_is_no_cap() -> None:
    stop = run_l(turning(corner_speed=lambda angle: 0.0), reserve=True)
    first, second = stop.of(AgvMoveStarted)
    assert second.t == first.t_end == ARC and second.motion["v0"] == 0.0  # at rest, no wait
    assert stop.of(AgvMoveEnded)[-1].t == pytest.approx(2 * ARC, rel=1e-12)

    free = run_l(turning(corner_speed=lambda angle: math.inf), reserve=True)
    plain = run_l(vehicle(), reserve=True)
    assert [e.t for e in free.of(AgvMoveEnded)] == [e.t for e in plain.of(AgvMoveEnded)]
    assert free.of(AgvMoveStarted)[1].motion["v0"] == plain.of(AgvMoveStarted)[1].motion["v0"] > 0.5


def test_a_corner_speed_has_no_effect_without_limits() -> None:
    w = run_l(turning(speed_profile=Plain(), corner_speed=lambda angle: 0.5), reserve=False)
    assert arc_times(w) == [("N1", 5.0), ("N2", 10.0)]


# --- turn time ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reserve", [False, True])
def test_a_turn_time_stops_at_the_corner_waits_and_leaves_from_rest(reserve: bool) -> None:
    spy = Spy(proportional)
    w = run_l(turning(turn_time=spy), reserve=reserve)
    first, second = w.of(AgvMoveStarted)
    ended = w.of(AgvMoveEnded)
    durations = (first.t_end - first.t, second.t - ended[0].t, ended[1].t - second.t)
    assert durations == (ARC, 2.0, ARC)  # 8.541666666666666 by hand, one unit in the last place below
    assert durations == pytest.approx((8.541666666666666, 2.0, 8.541666666666666), rel=1e-12)
    assert ended[0].t == first.t_end and second.motion["v0"] == 0.0  # at rest at the corner
    assert spy.angles == [pytest.approx(RIGHT)]
    assert not w.of(TrafficWaitStarted)  # a turn wait is not a traffic wait
    assert [e.type_name for e in w.events if e.type_name.startswith("agv.move")] == [
        "agv.move_started",
        "agv.move_ended",
        "agv.move_started",
        "agv.move_ended",
    ]


def test_a_turn_time_applies_to_a_profile_without_limits() -> None:
    w = run_l(turning(speed_profile=Plain(), turn_time=proportional), reserve=True)
    assert arc_times(w) == [("N1", 5.0), ("N2", 12.0)]
    assert w.of(AgvMoveStarted)[1].t == 7.0


def test_a_turn_wait_holds_the_claims_ahead_and_a_stop_request_takes_effect_at_once() -> None:
    w = run_l(turning(turn_time=proportional), reserve=True)
    assert not w.of(AgvMoveInterrupted)

    w = world(l_shape, {"A": "N0"}, reserve=True, agv_type=turning(turn_time=proportional))
    process = w.go("A", "N2")
    w.interrupt(process, 10.0)  # the wait lasts from ARC to ARC + 2
    w.env.run(until=9.0)
    assert set(w.held("A")) >= {"N1", "N1~N2", "N2"}  # the claims ahead are held as at any rest
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    (interrupted,) = w.of(AgvMoveInterrupted, agv="A")
    assert (interrupted.node, interrupted.t) == ("N1", 10.0)
    assert w.agvs["A"].current_node == w.nodes["N1"] and w.agvs["A"].motion is None
    assert w.held("A") == ["N1"]
    assert [(e.t, e.resources) for e in w.of(TrafficReleased, agv="A")][-1] == (10.0, ("N1~N2", "N2"))
    assert len(w.of(AgvMoveStarted)) == 1


# --- the heading change ------------------------------------------------------------------------------------------


def test_a_straight_continuation_costs_nothing() -> None:
    points, links = [("P0", 0, 0), ("P1", 10, 0), ("P2", 20, 0)], [("P0", "P1"), ("P1", "P2")]
    spy = Spy(proportional)
    cap = Spy(lambda angle: math.inf if angle == 0 else 0.5)
    w = world(lambda env: build(env, points, links), {"A": "P0"}, agv_type=turning(turn_time=spy, corner_speed=cap))
    w.go("A", "P2")
    w.env.run()
    free = world(lambda env: build(env, points, links), {"A": "P0"})
    free.go("A", "P2")
    free.env.run()
    assert [e.t for e in w.of(AgvMoveEnded)] == [e.t for e in free.of(AgvMoveEnded)]
    assert spy.angles == [0.0] and cap.angles == [0.0]


def test_the_first_arc_after_placement_turns_for_free_and_reversing_is_a_turn_of_pi() -> None:
    spy = Spy(proportional)
    w = world(
        lambda env: build(env, [("N0", 0, 0), ("N1", 10, 0)], [("N0", "N1")]),
        {"A": "N0"},
        agv_type=turning(turn_time=spy),
    )
    assert w.agvs["A"].heading is None
    w.go("A", "N1")
    w.env.run()
    assert spy.angles == []  # the heading was unknown: no turn, the callable was not asked
    assert [e.t for e in w.of(AgvMoveEnded)] == [ARC]
    w.go("A", "N0", at=20.0)
    w.env.run()
    assert spy.angles == [pytest.approx(math.pi)]
    back = w.of(AgvMoveStarted)[1]
    assert back.t == 24.0  # departs after a wait of 2 * pi / (pi / 2) = 4
    assert w.of(AgvMoveEnded)[1].t == pytest.approx(24.0 + ARC, rel=1e-12)


def test_a_turn_at_the_start_of_a_travel_from_rest_waits_before_departure() -> None:
    spy = Spy(proportional)
    w = world(l_shape, {"A": "N0"}, agv_type=turning(turn_time=spy))
    w.go("A", "N1")
    w.env.run()
    w.go("A", "N2", at=20.0)  # the heading is east; the arc to N2 goes north
    w.env.run()
    assert spy.angles == [pytest.approx(RIGHT)]
    assert w.of(AgvMoveStarted)[1].t == 22.0
    assert w.of(AgvMoveEnded)[1].t == pytest.approx(22.0 + ARC, rel=1e-12)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (math.radians(170), math.radians(-170), math.radians(20)),  # across the cut of atan2
        (math.radians(-170), math.radians(170), math.radians(20)),
        (math.radians(10), math.radians(100), math.radians(90)),
        (math.radians(100), math.radians(10), math.radians(90)),
        (math.radians(-90), math.radians(90), math.pi),
    ],
)
def test_the_heading_change_is_the_wrapped_absolute_difference(first: float, second: float, expected: float) -> None:
    x, y = 10 * math.cos(first), 10 * math.sin(first)
    points = [("P", 0, 0), ("Q", x, y), ("R", x + 10 * math.cos(second), y + 10 * math.sin(second))]
    spy = Spy(lambda angle: 0.0)
    w = world(lambda env: build(env, points, [("P", "Q"), ("Q", "R")]), {"A": "P"}, agv_type=turning(turn_time=spy))
    w.go("A", "R")
    w.env.run()
    assert spy.angles == [pytest.approx(expected, abs=1e-9)]
    assert 0.0 <= spy.angles[0] <= math.pi


def test_an_arc_whose_chord_has_no_direction_leaves_the_heading_unchanged() -> None:
    """``N1`` and ``N2`` coincide: the turn at ``N1`` into the zero-length arc is 0, and the turn at ``N2`` is measured
    from the last known heading, east."""
    # Explicit mode, built here by hand: the layout rejects an arc between two nodes at one position (spec L16), and
    # this test goes with that support.
    nodes = {name: Node(name, x, y) for name, x, y in (("N0", 0, 0), ("N1", 10, 0), ("N2", 10, 0), ("N3", 10, 10))}
    graph = LayoutGraph(
        nodes.values(), [Arc(nodes[a], nodes[b]) for a, b in (("N0", "N1"), ("N1", "N2"), ("N2", "N3"))]
    )

    def explicit_world(start: str, agv_type: AGVType) -> World:
        env = Environment(debug=True)
        replay = FullReplay(env)
        controller = ReservationController(env=env, graph=graph)
        agv = AGV(env=env, agv_type=agv_type, agv_id="A", initial_node=nodes[start])
        fleet = StillFleet(
            env=env,
            graph=graph,
            fleet=[agv],
            warehouses=[],
            charging_stations=[],
            traffic_controller=controller,
            default_metrics=False,
        )
        TrafficInvariants(env, controller)
        env.activate()
        return World(env, fleet, nodes, {"A": agv}, replay, len(replay.events))

    spy = Spy(lambda angle: 0.0)
    w = explicit_world("N0", turning(turn_time=spy))
    w.go("A", "N3")
    w.env.run()
    assert spy.angles == [0.0, pytest.approx(RIGHT)]
    assert w.agvs["A"].heading == pytest.approx(RIGHT)

    # a vehicle that has never had a direction turns for free across it and after it
    spy = Spy(lambda angle: 0.0)
    w = explicit_world("N1", turning(turn_time=spy))
    w.go("A", "N3")
    w.env.run()
    assert spy.angles == []


# --- values ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("turn_time", -1.0),
        ("turn_time", math.nan),
        ("turn_time", math.inf),
        ("corner_speed", -0.5),
        ("corner_speed", math.nan),
    ],
)
def test_invalid_turning_values_raise(option: str, value: float) -> None:
    w = world(l_shape, {"A": "N0"}, agv_type=turning(**{option: lambda angle: value}))
    w.go("A", "N2")
    with pytest.raises(ValueError, match=rf"AGVType 'cart': {option}\(1\.5707963\d*\) returned"):
        w.env.run()


# --- both controllers --------------------------------------------------------------------------------------------


def staircase(env: Environment, lengths: list[float]) -> Declared:
    """Arcs of the given lengths alternating east and north, from ``S0``."""
    points, x, y = [("S0", 0.0, 0.0)], 0.0, 0.0
    for i, length in enumerate(lengths):
        x, y = (x + length, y) if i % 2 == 0 else (x, y + length)
        points.append((f"S{i + 1}", x, y))
    return build(env, points, [(f"S{i}", f"S{i + 1}") for i in range(len(lengths))])


@pytest.mark.parametrize(
    "lengths",
    [[10.0] * 6, [1.0] * 10, [3.0, 0.7, 12.0, 1.1, 1.1, 6.0, 2.5], [0.3] * 12],
)
@pytest.mark.parametrize("option", ["corner_speed", "turn_time", "both"])
def test_a_lone_vehicle_has_the_same_times_under_both_controllers(lengths: list[float], option: str) -> None:
    options: dict[str, Any] = {}
    if option in ("corner_speed", "both"):
        options["corner_speed"] = lambda angle: 0.45 + 0.1 * angle
    if option in ("turn_time", "both"):
        options["turn_time"] = lambda angle: 1.5 * angle / RIGHT
    times = []
    for reserve in (False, True):
        w = world(lambda env: staircase(env, lengths), {"A": "S0"}, reserve=reserve, agv_type=turning(**options))
        w.go("A", f"S{len(lengths)}")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        times.append(arc_times(w) + [(e.to_node, e.t) for e in w.of(AgvMoveStarted)])
    assert times[0] == times[1]


def test_seeded_lone_vehicles_with_turning_match_free_flow_exactly() -> None:
    """Blocks of a fraction of the braking distance and corners that cap the speed at, below and above what the
    backward pass gives: the look-ahead and the planner judge the corner caps alike, so the times are equal."""
    rng = random.Random(20260222)
    for _ in range(120):
        v_max, decel = rng.uniform(0.5, 4.0), rng.uniform(0.3, 3.0)
        braking = v_max**2 / (2 * decel)
        lengths = [braking / rng.choice([0.5, 1, 1.5, 2, 3, 5]) for _ in range(rng.randrange(4, 12))]
        cap, wait = rng.uniform(0.0, 1.2) * v_max, rng.choice([0.0, 0.0, 0.5])
        kind = turning(
            speed_profile=TrapezoidalProfile(v_max, 0.8, decel, battery_degradation_fn=lambda level: 1.0),
            corner_speed=lambda angle: cap * (1 + angle / RIGHT),
            turn_time=lambda angle: wait * angle / RIGHT,
        )
        times = []
        for reserve in (False, True):
            w = world(lambda env: staircase(env, lengths), {"A": "S0"}, reserve=reserve, agv_type=kind)
            w.go("A", f"S{len(lengths)}")
            w.env.run()
            times.append([(e.node, e.t) for e in w.of(AgvMoveEnded)])
        assert times[0] == times[1], (v_max, decel, lengths, cap, wait)


def test_the_look_ahead_counts_a_corner_cap_among_the_caps_the_backward_pass_reaches() -> None:
    """The corner cap at S3 lies just below ``v_max``. By the time the AGV passes S2 the claims reach S3 and the
    backward pass over them gives that cap exactly, so no claim beyond S3 is needed yet. Judging the node by the arc
    caps alone, the pass is one unit in the last place short of ``v_max`` there and asks for one claim too many, at
    the departure from S1 instead of at the arrival at S2. The times equal free flow's either way (§9.3); what the
    look-ahead decides is when the claim is asked for."""
    lengths = [1.565600948907193, 1.5656009489071945, 1.5656009489071945, 3.131201897814386, 0.5218669829690653]
    kind = turning(
        speed_profile=TrapezoidalProfile(1.59, 0.8, 0.8073896486089382, battery_degradation_fn=lambda level: 1.0),
        corner_speed=lambda angle: 1.5899841000000001,
    )
    worlds = {}
    for reserve in (False, True):
        w = worlds[reserve] = world(lambda env: staircase(env, lengths), {"A": "S0"}, reserve=reserve, agv_type=kind)
        w.go("A", f"S{len(lengths)}")
        w.env.run()
    assert arc_times(worlds[True]) == arc_times(worlds[False])
    w = worlds[True]
    second = arc_times(w)[1][1]
    (claim,) = [e for e in w.of(TrafficReserved) if e.resources == ("S3~S4", "S4")]
    assert claim.t == second


@pytest.mark.parametrize("reserve", [False, True])
def test_a_turn_wait_reports_serving_and_a_run_reports_moving(reserve: bool) -> None:
    w = world(l_shape, {"A": "N0"}, reserve=reserve, agv_type=turning(turn_time=proportional))
    a, view = w.agvs["A"], w.controller.view
    w.go("A", "N2")
    w.env.run(until=4.0)
    assert a.motion is not None and view.disposition(a) is Disposition.MOVING
    w.env.run(until=9.0)  # the turn wait lasts from ARC to ARC + 2
    assert a.motion is None and view.disposition(a) is Disposition.SERVING
    w.env.run(until=12.0)
    assert view.disposition(a) is Disposition.MOVING
    w.env.run()
    assert view.disposition(a) is Disposition.IDLE


def test_a_reroute_after_a_turn_wait_turns_again_towards_the_new_first_arc() -> None:
    """A new direction is a new turn: ``A``, heading north, turns to east at ``S`` (2 s), is blocked by ``B`` on ``P``,
    is sent round through ``Q`` and turns from north to north-east (1 s) before it leaves. Its heading has not changed
    by waiting, so the second turn is measured from north again."""
    policy = Scripted()
    w = world(
        lambda env: build(
            env,
            [("K", 0, -10), ("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0)],
            [("K", "S"), ("S", "P"), ("P", "E"), ("S", "Q"), ("Q", "E")],
        ),
        {"A": "K", "B": "P"},
        agv_type=turning(turn_time=proportional),
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
    )
    policy.blocked = (Reroute(frozenset({w.nodes["P"]})),)
    w.go("A", "S")
    w.env.run()
    w.go("A", "E", at=20.0)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert [e.nodes for e in w.of(AgvRouted)] == [("K", "S"), ("S", "P", "E"), ("S", "Q", "E")]
    (wait,) = w.of(TrafficWaitEnded, agv="A")
    assert wait.t == 22.0  # the first turn wait came before the claim wait
    assert [e.t for e in w.of(AgvMoveStarted, agv="A")][1] == 23.0


# --- fix round 1: the tolerance of the heading change, and the order of energy check, turn and claim wait ----------


def noisy_line(env: Environment) -> Declared:
    """Six collinear nodes whose coordinates carry rounding: the computed heading changes are 5.6e-17 and 1.1e-16."""
    return build(env, [(f"P{i}", 7.1 * i, 0.3 * 7.1 * i) for i in range(6)], [(f"P{i}", f"P{i + 1}") for i in range(5)])


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(
    "option", ["turn_time", "corner_speed", "positive_turn_time"], ids=["proportional", "corner", "any_angle"]
)
def test_rounding_in_the_coordinates_does_not_turn_a_straight_continuation(reserve: bool, option: str) -> None:
    spy = Spy(
        {
            "turn_time": proportional,
            "corner_speed": lambda angle: 0.5 if angle > 0 else math.inf,
            "positive_turn_time": lambda angle: 1.0 if angle > 0 else 0.0,
        }[option]
    )
    kind = turning(**{"corner_speed" if option == "corner_speed" else "turn_time": spy})
    results = []
    for agv_type in (kind, vehicle()):
        w = world(noisy_line, {"A": "P0"}, reserve=reserve, agv_type=agv_type)
        w.go("A", "P5")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        results.append(([(e.node, e.t) for e in w.of(AgvMoveEnded)], [e.motion["v0"] for e in w.of(AgvMoveStarted)]))
    assert results[0] == results[1]  # exactly the line without turning, passed at speed
    assert all(v0 > 0 for v0 in results[0][1][1:])
    assert spy.angles and set(spy.angles) == {0.0}


def charger_world(turn: bool) -> World:
    """The L route with a charging station on ``N1``; the vehicle holds 11 units and an arc takes 10."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    declared = l_shape(env)
    controller = ReservationController(env=env, graph=declared.graph)
    agv_type = turning(turn_time=proportional if turn else None, battery_capacity=11.0)
    agv = AGV(env=env, agv_type=agv_type, agv_id="A", initial_node=declared.ref("N0"))
    station = ChargingStation(env=env, name="CS", node=declared.ref("N1"), n_slots=1)
    fleet = StillFleet(
        env=env,
        graph=declared.graph,
        fleet=[agv],
        warehouses=[],
        charging_stations=[station],
        traffic_controller=controller,
        default_metrics=False,
    )
    TrafficInvariants(env, controller)
    env.activate()
    nodes = {node.id: node for node in declared.canonical_graph().nodes}
    return World(env, fleet, nodes, {"A": agv}, replay, len(replay.events))


def test_the_energy_check_comes_before_the_turn_wait_and_strands_at_once() -> None:
    """The vehicle arrives at N1 with 1 unit left. The travel to N2 turns 90 degrees (2 s) and the arc needs 10: the
    check comes first, so ``agv.stranded`` is emitted when the travel starts, not after the turn."""
    w = world(l_shape, {"A": "N0"}, agv_type=turning(turn_time=proportional, battery_capacity=11.0))
    w.go("A", "N1")
    w.env.run()
    assert w.agvs["A"].battery.level == 1.0
    w.go("A", "N2", at=20.0)
    w.env.run()
    assert w.outcomes["A"] is TravelOutcome.BATTERY_STRANDED
    (stranded,) = w.of(AgvStranded)
    assert (stranded.node, stranded.t) == ("N1", 20.0)


def test_a_charging_diversion_does_not_serve_the_turn_towards_the_abandoned_arc() -> None:
    ends = []
    for turn in (True, False):
        w = charger_world(turn)
        w.go("A", "N1")
        w.env.run()
        w.go("A", "N2", at=20.0)
        w.env.run()
        assert w.outcomes["A"] is TravelOutcome.RETRY_FROM_CURRENT_POSITION
        assert w.agvs["A"].battery.level > 1.0  # charged
        ends.append(w.env.now)
    assert ends[0] == ends[1]  # the turn (2 s) was not served before the diversion


def blocked_corner(leaves: float) -> World:
    """The L route of :func:`l_shape` extended to ``N3``; ``A`` turns at ``N1`` from 8.54 to 10.54 while ``B`` stands on
    ``N2`` and leaves for ``N3`` at `leaves`."""
    w = world(
        lambda env: build(
            env,
            [("N0", 0, 0), ("N1", 10, 0), ("N2", 10, 10), ("N3", 10, 20)],
            [("N0", "N1"), ("N1", "N2"), ("N2", "N3")],
        ),
        {"A": "N0", "B": "N2"},
        agv_type=turning(turn_time=proportional),
    )
    w.go("A", "N2")
    w.go("B", "N3", at=leaves)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    return w


def test_a_claim_granted_during_the_turn_wait_gives_no_traffic_wait() -> None:
    w = blocked_corner(leaves=9.0)
    assert not w.of(TrafficWaitStarted, agv="A")
    assert w.of(AgvMoveStarted, agv="A")[1].t == pytest.approx(ARC + 2.0, rel=1e-12)  # the turn, nothing more


def test_a_claim_still_pending_after_the_turn_waits_from_the_end_of_the_turn() -> None:
    w = blocked_corner(leaves=15.0)
    (started,) = w.of(TrafficWaitStarted, agv="A")
    assert started.t == pytest.approx(ARC + 2.0, rel=1e-12)  # not at the arrival
    (ended,) = w.of(TrafficWaitEnded, agv="A")
    assert ended.t == 15.0 and w.of(AgvMoveStarted, agv="A")[1].t == 15.0


def test_the_claim_is_requested_before_the_turn_wait_at_the_start_of_a_travel() -> None:
    """``A`` has stopped at N1 heading east and sets out for N2 at 20: a turn of 2 s. ``B`` stands on N2 and leaves at
    21, during the turn. The claim was asked for at 20, so it is granted at 21 and the vehicle leaves at 22 with no
    traffic wait."""
    w = world(
        lambda env: build(
            env,
            [("N0", 0, 0), ("N1", 10, 0), ("N2", 10, 10), ("N3", 10, 20)],
            [("N0", "N1"), ("N1", "N2"), ("N2", "N3")],
        ),
        {"A": "N0", "B": "N2"},
        agv_type=turning(turn_time=proportional),
    )
    w.go("A", "N1")
    w.env.run()
    w.go("A", "N2", at=20.0)
    w.go("B", "N3", at=21.0)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert [e.t for e in w.of(TrafficReserved, agv="A")][-1] == 21.0
    assert w.of(AgvMoveStarted, agv="A")[1].t == 22.0
    assert not w.of(TrafficWaitStarted, agv="A")
