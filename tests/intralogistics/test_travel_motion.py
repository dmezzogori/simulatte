"""Runs of kinematic vehicles in the travel executor (spec §8.1, §9.2, §9.3).

A vehicle whose profile has ``limits`` keeps claims one arc plus its braking distance ahead, plans its speed from the
current node to the last node it holds and passes nodes at speed, so a lone AGV has the same travel time under both
controllers and however many nodes split a line. A profile without ``limits`` keeps the rest-to-rest motion of part A.
The energy horizon and the stop requests of spec §8.3 are pinned here for the simplest cases; ``test_stop_requests.py``
holds the rest.

Every world runs :class:`FullReplay`; worlds with a ``ReservationController`` also run :class:`TrafficInvariants`.
"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from simulatte import Layout
from simulatte._wire import freeze
from simulatte.environment import Environment
from simulatte.intralogistics import FreeFlowController, ReservationController, build_simple_system
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvMoveStarted,
    AgvRouted,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.kinematics import Kinematics, plan_run, rest_to_rest
from simulatte.intralogistics.speed import LINEAR_MOTION, TrapezoidalProfile, motion_fraction
from simulatte.intralogistics.travel import TravelOutcome
from tests.intralogistics.test_travel import ARC, Plain, World, steady, vehicle, world
from tests.intralogistics.traffic_support import FullReplay, TrafficInvariants, build, line, t_layout

KIN = Kinematics(1.5, 0.8, 0.8)
"""The limits of :func:`steady`."""

BRAKING = 1.5**2 / (2 * 0.8)
"""The braking distance of :data:`KIN`: 1.40625 m."""

UNIT = 2 * 1.5 / 0.8
"""One deceleration from ``v_max`` plus one acceleration back to it, at :data:`KIN`."""


def straight(pieces: int, length: float = 30.0) -> tuple[LayoutGraph, dict[str, Node]]:
    """A straight line of `length` metres from ``P0`` to ``P<pieces>`` in `pieces` equal arcs."""
    return build(
        [(f"P{i}", length * i / pieces, 0) for i in range(pieces + 1)],
        [(f"P{i}", f"P{i + 1}") for i in range(pieces)],
    )


def arrivals(w: World, agv: str = "A") -> list[tuple[str, float]]:
    return [(e.node, e.t) for e in w.of(AgvMoveEnded, agv=agv)]


def at_rest(description: Any) -> bool:
    """Whether a ``piecewise`` description ends at speed 0."""
    return description["v0"] + sum(duration * accel for duration, accel in description["phases"]) == pytest.approx(
        0.0, abs=1e-9
    )


def assert_valid_descriptions(w: World, lengths: dict[tuple[str, str], float]) -> int:
    """Every ``piecewise`` description of ``agv.move_started`` integrates to its arc length and to its duration
    (spec §11.3), as ``motion_fraction`` checks it with the event times; returns how many there were."""
    count = 0
    for e in w.of(AgvMoveStarted):
        description = e.motion
        if description["curve"] != "piecewise":
            continue
        count += 1
        duration = e.t_end - e.t
        assert motion_fraction(description, duration, duration, t_start=e.t) == 1.0, (e.t, description)
        assert motion_fraction(description, duration, 0.0, t_start=e.t) == 0.0
        covered, speed = 0.0, description["v0"]
        for span, accel in description["phases"]:
            covered += speed * span + 0.5 * accel * span * span
            speed += accel * span
            assert speed >= -1e-9
        length = lengths[(e.from_node, e.to_node)]
        assert description["distance"] == length
        assert abs(covered - length) <= max(1e-9, 1e-9 * length)
    return count


def lengths_of(w: World) -> dict[tuple[str, str], float]:
    graph = w.fleet.graph
    return {(u.id, v.id): graph.length(u, v) for u in graph.nodes for v in graph.neighbors(u)}


# --- free flow ---------------------------------------------------------------------------------------------------


def test_split_line_arrival_times() -> None:
    single = steady().travel_time(30.0)
    assert rest_to_rest(30.0, 1.5, KIN).duration == pytest.approx(single, rel=1e-12)
    for pieces in (1, 3, 30):
        w = world(straight(pieces), {"A": "P0"}, reserve=False)
        w.go("A", f"P{pieces}")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        assert len(w.of(AgvMoveEnded)) == pieces
        assert w.of(AgvMoveEnded)[-1].t == pytest.approx(single, rel=1e-9)


def test_the_t_layout_travel_takes_the_single_profile_time_and_passes_its_nodes_at_speed() -> None:
    w = world(t_layout(), {"A": "STORE_OUT"}, reserve=False)
    w.go("A", "LINE_IN")
    w.env.run()
    ended, started = w.of(AgvMoveEnded), w.of(AgvMoveStarted)
    assert ended[-1].t == pytest.approx(steady().travel_time(30.0), rel=1e-9)
    names = [(e.type_name, getattr(e, "node", None) or getattr(e, "from_node", None)) for e in w.events]
    for node in ("C1", "C2"):  # agv.move_ended at the node, then agv.move_started from it, at one instant
        i = names.index(("agv.move_ended", node))
        assert names[i + 1] == ("agv.move_started", node)
        assert w.events[i].t == w.events[i + 1].t
    assert [e.t for e in ended[:-1]] == [e.t for e in started[1:]]
    assert not at_rest(started[0].motion) and at_rest(started[-1].motion)
    assert assert_valid_descriptions(w, lengths_of(w)) == 3


@pytest.mark.parametrize("reserve", [False, True])
def test_descriptions_are_valid_at_a_large_simulation_time(reserve: bool) -> None:
    w = world(straight(30, 45.0), {"A": "P0"}, reserve=reserve)
    w.env.run(until=1e6)
    w.go("A", "P30", at=1e6 + 0.1)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert assert_valid_descriptions(w, lengths_of(w)) == 30


@pytest.mark.parametrize("reserve", [False, True])
def test_limits_are_sampled_per_run(reserve: bool) -> None:
    """The battery falls from 800 to 400 during the run, which halves the speed factor of the default profile; the
    arcs of the run keep the limits sampled at its start, and the next run from rest takes the new ones."""
    kind = AGVType(
        name="draining",
        speed_profile=TrapezoidalProfile(1.5, 0.8, 0.8),  # v_max and accel scale with the battery level
        battery_capacity=800.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        depletion_fn=lambda distance, load, speed: 10.0 * distance,
        length=0.0,
    )
    w = world(line(5), {"A": "N0"}, reserve=reserve, agv_type=kind)
    w.go("A", "N4")
    w.go("A", "N3", at=100.0)
    w.env.run()
    assert w.agvs["A"].battery.level == 300.0
    first, second = w.of(AgvMoveStarted)[:4], w.of(AgvMoveStarted)[4]
    planned = plan_run(0.0, [10.0] * 4, [1.5] * 4, [0.0, 1.5, 1.5, 1.5, 0.0], KIN)
    assert [e.motion for e in first] == [freeze(profile.description()) for profile in planned]
    halved = Kinematics(0.75, 0.4, 0.8)
    assert second.motion == freeze(rest_to_rest(10.0, 0.75, halved).description())


# --- reservation -------------------------------------------------------------------------------------------------


def builder_line_arrivals(reserve: bool) -> list[tuple[str, float]]:
    env = Environment()
    layout = Layout(env)
    replay = FullReplay(env)
    controller = ReservationController(env=env, graph=layout.graph()) if reserve else FreeFlowController()
    fleet, (agv,), _, _, _ = build_simple_system(env, n_agvs=1, traffic_controller=controller, agv_length=1.0)
    if reserve:
        TrafficInvariants(env, controller)
    env.activate()
    nodes = {node.id: node for node in fleet.graph.nodes}

    def trips() -> Any:
        for goal in ("WH_B_IN", "WH_A_OUT", "N3"):
            agv.transition_to(AGVState.TRAVELING_EMPTY)
            outcome = yield from fleet._executor.travel(agv, nodes[goal], loaded=False)
            assert outcome is TravelOutcome.ARRIVED
            agv.transition_to(AGVState.IDLE)

    env.run(env.process(trips()))
    return [(e.node, e.t) for e in replay.of(AgvMoveEnded)]


@pytest.mark.parametrize("layout", ["t_layout", "builder_line", "thirty_arcs"])
def test_lone_agv_matches_free_flow(layout: str) -> None:
    if layout == "builder_line":
        runs = [builder_line_arrivals(reserve) for reserve in (True, False)]
    else:
        graph_nodes, start, goals = {
            "t_layout": (t_layout, "STORE_OUT", ("LINE_IN", "PARK", "STORE_OUT")),
            "thirty_arcs": (lambda: straight(30), "P0", ("P30", "P0")),
        }[layout]
        runs = []
        for reserve in (True, False):
            w = world(graph_nodes(), {"A": start}, reserve=reserve, agv_type=vehicle(length=1.0))
            for i, goal in enumerate(goals):
                w.go("A", goal, at=200.0 * i)
            w.env.run()
            assert set(w.outcomes.values()) == {TravelOutcome.ARRIVED}
            runs.append(arrivals(w))
    assert len(runs[0]) >= 8
    assert runs[0] == runs[1]


BRAKING_1_59 = 1.59**2 / (2 * 1.14)
"""The braking distance at ``v_max=1.59``, ``decel=1.14``, where ``sqrt(2 * decel * distance)`` rounds below 1.59."""


def line_arrivals(v_max: float, accel: float, decel: float, block: float, arcs: int, reserve: bool) -> list[Any]:
    """The arrivals of a lone AGV over `arcs` straight arcs of length `block`, under one controller."""
    nodes = {f"E{i}": Node(f"E{i}", block * i, 0) for i in range(arcs + 1)}
    graph = LayoutGraph(nodes.values(), [Arc(nodes[f"E{i}"], nodes[f"E{i + 1}"], length=block) for i in range(arcs)])
    profile = TrapezoidalProfile(v_max, accel, decel, battery_degradation_fn=lambda level: 1.0)
    w = world((graph, nodes), {"A": "E0"}, reserve=reserve, agv_type=vehicle(profile=profile, battery=1e9))
    w.go("A", f"E{arcs}")
    w.env.run()
    return arrivals(w)


@pytest.mark.parametrize(
    "block",
    [
        BRAKING_1_59,
        math.nextafter(BRAKING_1_59, 0.0),
        math.nextafter(BRAKING_1_59, math.inf),
        0.5 * BRAKING_1_59,
        0.999999 * BRAKING_1_59,
        1.000001 * BRAKING_1_59,
        2.0 * BRAKING_1_59,
    ],
)
def test_a_horizon_at_the_braking_distance_keeps_the_free_flow_times_exactly(block: float) -> None:
    """Blocks as long as the braking distance at ``v_max=1.59``, ``decel=1.14`` and around it. Exactly at it, the
    horizon lands on the braking distance and ``sqrt(2 d L)`` rounds one unit below ``v_max``; the AGV then claims one
    block more, until the backward pass gives the cap at the end of the next arc, and its times equal free flow
    exactly (spec §9.3)."""
    assert math.sqrt(2 * 1.14 * BRAKING_1_59) < 1.59
    reserved = line_arrivals(1.59, 0.8, 1.14, block, 20, True)
    assert reserved == line_arrivals(1.59, 0.8, 1.14, block, 20, False)


@pytest.mark.parametrize(
    ("v_max", "decel", "block"), [(2.76, 0.6, 1.2695999999999998), (0.69, 0.87, 0.05472413793103448)]
)
def test_the_look_ahead_and_the_planner_judge_the_cap_alike(v_max: float, decel: float, block: float) -> None:
    """Blocks of a fifth of the braking distance where ``x * x`` and ``x ** 2`` round apart in the backward pass: the
    claim look-ahead uses the planner's own backward pass, so the lone AGV's times equal free flow exactly."""
    reserved = line_arrivals(v_max, 0.8, decel, block, 30, True)
    assert reserved == line_arrivals(v_max, 0.8, decel, block, 30, False)


def test_lone_agv_matches_free_flow_over_blocks_at_fractions_of_the_braking_distance() -> None:
    rng = random.Random(20261122)
    for case in range(240):
        v_max = round(rng.uniform(0.3, 4.0), rng.choice([1, 2, 3]))
        decel = round(rng.uniform(0.2, 3.0), rng.choice([1, 2]))
        block = v_max * v_max / (2 * decel) / rng.randint(1, 6)
        reserved = line_arrivals(v_max, 0.8, decel, block, 12, True)
        assert reserved == line_arrivals(v_max, 0.8, decel, block, 12, False), (case, v_max, decel, block)


@pytest.mark.parametrize("margin", [0.0, 5.0])
def test_the_claims_ahead_cover_the_next_arc_and_the_braking_distance(margin: float) -> None:
    """On a 30 m line of 1 m arcs, at every departure the AGV holds the next arc, then at least the braking distance
    plus the margin beyond it, and not one claim more than that needs; the arrival time does not depend on it."""
    w = world(straight(30), {"A": "P0"}, lookahead_margin=margin)
    held: list[tuple[int, int]] = []  # (node index, furthest node index held) at each departure
    agv = w.agvs["A"]

    def departure(event: AgvMoveStarted) -> None:
        furthest = max(int(r.id[1:]) for r in w.controller._held[agv] if isinstance(r, Node))
        held.append((int(event.from_node[1:]), furthest))

    w.env.bus.subscribe(departure, (AgvMoveStarted,))
    w.go("A", "P30")
    w.env.run()
    lookahead = BRAKING + margin
    assert len(held) == 30
    for index, furthest in held:
        if furthest < 30:
            assert furthest - (index + 1) >= lookahead  # 1 m arcs: node indices are metres
            assert furthest - 1 - (index + 1) < lookahead
    assert {furthest - index for index, furthest in held if furthest < 30} == {3 if margin == 0.0 else 8}
    assert w.of(AgvMoveEnded)[-1].t == pytest.approx(steady().travel_time(30.0), rel=1e-9)


def side_line() -> tuple[LayoutGraph, dict[str, Node]]:
    """A line ``N0 ... N4`` of 10 m arcs with the side node ``S`` 10 m off N3."""
    return build(
        [("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("N3", 30, 0), ("N4", 40, 0), ("S", 30, 10)],
        [("N0", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "N4"), ("N3", "S")],
    )


def leader_and_follower(leader_leaves: float) -> World:
    """F travels ``N0 -> N4`` while L stands on N3 until `leader_leaves`, then goes to S; one claim per leg."""
    w = world(side_line(), {"L": "N3", "F": "N0"}, hold_back_dead_ends=False)
    w.go("F", "N4")
    w.go("L", "S", at=leader_leaves)
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.ARRIVED, "L": TravelOutcome.ARRIVED}
    return w


def test_a_follower_comes_to_rest_at_the_last_node_it_holds_and_restarts_when_granted() -> None:
    w = leader_and_follower(60.0)
    ended = {e.node: e for e in w.of(AgvMoveEnded, agv="F")}
    started = {e.from_node: e for e in w.of(AgvMoveStarted, agv="F")}
    assert started["N1"].t == ended["N1"].t and not at_rest(started["N0"].motion)  # N1 is passed at speed
    assert at_rest(started["N1"].motion) and ended["N2"].t < 60.0  # it brakes for N2, the last node it holds
    (wait,) = w.of(TrafficWaitStarted, agv="F")
    assert (wait.t, wait.node, wait.blockers, wait.resources) == (ended["N2"].t, "N3", ("L",), ("N2~N3", "N3"))
    assert [(e.t, e.reason) for e in w.of(TrafficWaitEnded, agv="F")] == [(60.0, "granted")]
    assert started["N2"].t == 60.0 and started["N2"].motion["v0"] == 0.0


def test_grant_at_the_instant_of_arrival_is_used() -> None:
    """L leaves N3 exactly when F comes to rest on N2: the grant is taken on arrival, without a wait."""
    arrival = {e.node: e.t for e in leader_and_follower(1000.0).of(AgvMoveEnded, agv="F")}["N2"]
    w = leader_and_follower(arrival)
    assert not w.of(TrafficWaitStarted, agv="F")
    ended = {e.node: e for e in w.of(AgvMoveEnded, agv="F")}
    started = {e.from_node: e for e in w.of(AgvMoveStarted, agv="F")}
    assert ended["N2"].t == arrival == started["N2"].t
    assert started["N2"].motion["v0"] == 0.0


def stream_past_a_leader(leader_leaves: float) -> World:
    """F drives ``P00 -> P14`` over 1 m arcs while L stands on P10 until `leader_leaves`, then goes to the side node S;
    one claim per leg."""
    points = [(f"P{i:02d}", i, 0) for i in range(15)] + [("S", 10, 1)]
    links = [(f"P{i:02d}", f"P{i + 1:02d}") for i in range(14)] + [("P10", "S")]
    w = world(build(points, links), {"L": "P10", "F": "P00"}, hold_back_dead_ends=False)
    w.go("F", "P14")
    w.go("L", "S", at=leader_leaves)
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.ARRIVED, "L": TravelOutcome.ARRIVED}
    return w


def test_grant_at_the_instant_of_a_node_passed_at_speed_is_used() -> None:
    """L leaves P10 exactly when F passes P08 at speed, F's claim of ``P09 -> P10`` pending. The grant is made before
    F's arrival at P08 is processed, so F takes it there and plans past P09 instead of braking for it: it passes P09
    at speed and loses no more than when L leaves a microsecond earlier, while F is still on its way to P08."""
    passing = {e.node: e.t for e in stream_past_a_leader(1000.0).of(AgvMoveEnded, agv="F")}["P08"]
    alone = {e.node: e.t for e in stream_past_a_leader(1e-9).of(AgvMoveEnded, agv="F")}["P14"]
    w = stream_past_a_leader(passing)
    started = {e.from_node: e for e in w.of(AgvMoveStarted, agv="F")}
    assert started["P08"].t == passing and started["P08"].motion["v0"] > 0.0
    assert not at_rest(started["P08"].motion) and started["P09"].motion["v0"] > 0.0  # P09 is passed at speed
    assert not w.of(TrafficWaitStarted, agv="F")
    earlier = stream_past_a_leader(passing - 1e-6)
    delay = w.of(AgvMoveEnded, agv="F")[-1].t - alone
    assert delay <= earlier.of(AgvMoveEnded, agv="F")[-1].t - alone + 1e-6


def test_a_claim_granted_on_an_arc_that_already_brakes_ends_in_a_stop_at_its_node() -> None:
    """L leaves at 10, while F brakes on ``N1 -> N2`` for N2. F still stops there (the documented limit of spec
    §9.3), restarts at once without a wait, and loses at most one deceleration and one acceleration."""
    alone = world(side_line(), {"F": "N0"}, hold_back_dead_ends=False)
    alone.go("F", "N4")
    alone.env.run()
    free_time = alone.of(AgvMoveEnded)[-1].t
    w = leader_and_follower(10.0)
    ended = {e.node: e for e in w.of(AgvMoveEnded, agv="F")}
    started = {e.from_node: e for e in w.of(AgvMoveStarted, agv="F")}
    assert started["N1"].t < 10.0 < ended["N2"].t
    assert at_rest(started["N1"].motion)
    assert started["N2"].t == ended["N2"].t and started["N2"].motion["v0"] == 0.0
    assert not w.of(TrafficWaitStarted, agv="F")
    delay = ended["N4"].t - free_time
    assert 0.0 < delay <= UNIT


def test_a_moving_agv_blocked_by_an_idle_one_stops_at_its_horizon_and_the_idle_one_makes_way() -> None:
    """The pending claim of a moving AGV opens no wait, so the idle AGV it needs is not asked to move (and keeps
    its make-way memory) until the AGV comes to rest at its horizon and calls ``stopped``."""
    w = world(side_line(), {"A": "N0", "B": "N3"}, make_way=True)
    a, b = w.agvs["A"], w.agvs["B"]
    seen: list[tuple[str, bool, tuple[AGV, ...], int]] = []

    def departure(event: AgvMoveStarted) -> None:
        if event.agv == "A":
            routed = len(w.of(AgvRouted, agv="B"))
            seen.append((event.from_node, a in w.controller._pending, w.controller.view.waiting_on(b), routed))

    w.env.bus.subscribe(departure, (AgvMoveStarted,))
    w.go("A", "N4")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert seen[1] == ("N1", True, (), 0)  # moving with a pending claim that B blocks: no wait, no make-way move
    stop = {e.node: e.t for e in w.of(AgvMoveEnded, agv="A")}["N2"]
    (wait,) = w.of(TrafficWaitStarted, agv="A")
    assert (wait.t, wait.blockers) == (stop, ("B",))
    (moved,) = w.of(AgvRouted, agv="B")
    assert (moved.t, moved.reason, moved.destination) == (stop, "make_way", "S")


# --- stop requests and the energy horizon ------------------------------------------------------------------------


def test_an_interrupt_while_moving_takes_effect_at_the_first_node_where_the_run_can_rest() -> None:
    w = world(line(5), {"A": "N0"})
    process = w.go("A", "N4")
    w.interrupt(process, 3.0)
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    assert [e.resources for e in w.of(TrafficReserved, agv="A")] == [("N0~N1", "N1"), ("N1~N2", "N2")]
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert interrupted.node == "N2" and interrupted.t == w.of(AgvMoveEnded)[-1].t
    assert w.held("A") == ["N2"]


def test_an_interrupt_with_a_pending_claim_withdraws_it_at_rest_without_a_wait() -> None:
    w = world(line(6), {"L": "N3", "F": "N0"})
    process = w.go("F", "N4")
    w.interrupt(process, 10.0)  # on N1 -> N2, with the claim of N2 -> N3 pending
    w.env.run()
    assert w.caught == {"F": "cancelled"}
    assert w.agvs["F"].current_node == w.nodes["N2"] and w.held("F") == ["N2"]
    assert not w.controller._pending and not w.of(TrafficWaitStarted)


def critical_type(depletion: Any = None, threshold: float = 0.9, capacity: float = 100.0) -> AGVType:
    return AGVType(
        name="weak",
        speed_profile=steady(),
        battery_capacity=capacity,
        weight_capacity=100.0,
        volume_capacity=10.0,
        depletion_fn=depletion,
        critical_battery_threshold=threshold,
        length=0.0,
    )


@pytest.mark.parametrize("reserve", [False, True])
def test_the_plan_ends_where_the_estimated_battery_turns_critical(reserve: bool) -> None:
    """10 of 100 per arc and a critical threshold of 0.9: the estimate foresees a critical level at N1, so the run
    ends at rest there and the travel returns there, under both controllers (spec §9.5)."""
    w = world(line(5), {"A": "N0"}, reserve=reserve, agv_type=critical_type())
    w.go("A", "N4")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    assert w.agvs["A"].current_node == w.nodes["N1"]
    (started,) = w.of(AgvMoveStarted)
    assert at_rest(started.motion)
    assert w.of(AgvMoveEnded)[0].t == pytest.approx(steady().travel_time(10.0), rel=1e-12)


@pytest.mark.parametrize("reserve", [False, True])
def test_the_plan_ends_at_the_last_affordable_node(reserve: bool) -> None:
    kind = AGVType(
        name="small",
        speed_profile=steady(),
        battery_capacity=25.0,  # 10 per arc: two arcs
        weight_capacity=100.0,
        volume_capacity=10.0,
        length=0.0,
    )
    w = world(line(5), {"A": "N0"}, reserve=reserve, agv_type=kind)
    w.go("A", "N4")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert arrivals(w) == [("N1", w.of(AgvMoveStarted)[1].t), ("N2", pytest.approx(steady().travel_time(20.0)))]
    if reserve:
        assert [e.resources for e in w.of(TrafficReserved)] == [("N0~N1", "N1"), ("N1~N2", "N2")]


# --- stalls, zero-length arcs, profiles without limits ------------------------------------------------------------


class Limited:
    """A kinematic profile with fixed limits."""

    def __init__(self, limits: Kinematics) -> None:
        self._limits = limits

    def limits(self, load_weight: float, battery_level: float) -> Kinematics:
        return self._limits

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return math.inf


@pytest.mark.parametrize(("v_max", "accel"), [(0.0, 0.8), (1.5, 0.0), (-1.0, 0.8)])
def test_non_positive_limits_stall_the_run(v_max: float, accel: float) -> None:
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(profile=Limited(Kinematics(v_max, accel, 0.8))))
    process = w.go("A", "N2")
    w.interrupt(process, 5.0)
    w.env.run()
    assert w.env.now == 5.0  # no timer at infinity
    assert w.caught == {"A": "cancelled"}
    (started,) = w.of(AgvMoveStarted)
    assert started.t_end == math.inf and w.agvs["A"].motion is None
    assert [e.resources for e in w.of(TrafficReserved)] == [("N0~N1", "N1")]  # one arc ahead, no look-ahead
    assert [(e.t, e.resources) for e in w.of(TrafficReleased)] == [(5.0, ("N0~N1", "N1"))]
    (ended,) = w.of(AgvMoveEnded)
    assert (ended.t, ended.node) == (5.0, "N0")
    assert w.held("A") == ["N0"]


@pytest.mark.parametrize("reserve", [False, True])
def test_zero_length_arc_is_traversed_in_zero_time(reserve: bool) -> None:
    graph_nodes = build(
        [("N0", 0, 0), ("Z1", 10, 0), ("Z2", 10, 0), ("N3", 20, 0)], [("N0", "Z1"), ("Z1", "Z2"), ("Z2", "N3")]
    )
    w = world(graph_nodes, {"A": "N0"}, reserve=reserve)
    w.go("A", "N3")
    w.env.run()
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    zero = started["Z1"]
    assert dict(zero.motion) == {"curve": "linear"} and zero.t_end == zero.t
    assert {e.node: e.t for e in w.of(AgvMoveEnded)}["Z2"] == zero.t == started["Z2"].t
    assert w.of(AgvMoveEnded)[-1].t == pytest.approx(steady().travel_time(20.0), rel=1e-9)
    assert w.agvs["A"].heading == 0.0


class NoLimits:
    """The steady trapezoidal profile without ``limits``: travel time and description only."""

    def __init__(self) -> None:
        self._profile = steady()

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return self._profile.travel_time(distance, load_weight, battery_level, speed_limit)

    def motion(self, distance: float, load_weight: float, battery_level: float, speed_limit: float | None) -> Any:
        return self._profile.motion(distance, load_weight, battery_level, speed_limit)


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize("profile", ["plain", "no_limits"])
def test_a_profile_without_limits_keeps_the_rest_to_rest_timings(reserve: bool, profile: str) -> None:
    kind = vehicle(profile=Plain() if profile == "plain" else NoLimits())
    w = world(line(4), {"A": "N0"}, reserve=reserve, agv_type=kind)
    w.go("A", "N3")
    w.env.run()
    arc = 5.0 if profile == "plain" else ARC
    assert [e.t for e in w.of(AgvMoveEnded)] == [arc, arc + arc, arc + arc + arc]
    assert [e.t for e in w.of(AgvMoveStarted)] == [0.0, arc, arc + arc]
    if profile == "plain":
        assert all(dict(e.motion) == dict(LINEAR_MOTION) for e in w.of(AgvMoveStarted))
    else:
        assert {e.motion["curve"] for e in w.of(AgvMoveStarted)} == {"trapezoidal"}
    if reserve:  # one claim at a time, each at rest
        assert [(e.t, e.resources) for e in w.of(TrafficReserved)] == [
            (0.0, ("N0~N1", "N1")),
            (arc, ("N1~N2", "N2")),
            (arc + arc, ("N2~N3", "N3")),
        ]


# --- releases ------------------------------------------------------------------------------------------------------


def test_clear_points_follow_the_planned_profiles_and_a_roomy_node_passed_at_speed_keeps_them() -> None:
    graph, nodes = line(4)
    w = world((graph, nodes), {"A": "N0"}, agv_type=vehicle(length=2.0), capacities={nodes["N1"]: 2})
    w.go("A", "N3")
    w.env.run()
    profiles = plan_run(0.0, [10.0] * 3, [1.5] * 3, [0.0, 1.5, 1.5, 0.0], KIN)
    first = profiles[0]
    assert [(e.t, e.resources) for e in w.of(TrafficReleased)][:2] == [
        (first.time_at(2.0), ("N0",)),
        (first.duration + profiles[1].time_at(2.0), ("N0~N1", "N1")),  # not at the arrival on N1, passed at speed
    ]
    rest = world((graph, nodes), {"A": "N0"}, agv_type=vehicle(length=2.0), capacities={nodes["N1"]: 2})
    rest.go("A", "N1")
    rest.env.run()
    assert rest.held("A") == ["N1"]  # at rest on N1: the whole trail clears


def test_a_grant_taken_while_moving_keeps_the_trail_entries_it_lifts_held() -> None:
    """A, 3 m long on 1 m arcs, rests on N10 and holds its body back to N07. It reverses to N05 while B stands on N07,
    so its claim of ``N07~N08, N07`` is pending as it departs; B leaves at 100.5 and the grant arrives while A moves.
    The grant lifts ``N07~N08`` out of A's trail although its departure scheduled that entry's release: the entry lies
    ahead again and stays held. At the end A holds what its body overlaps, the last 3 m of its way to N05."""
    graph_nodes = build([(f"N{i:02d}", i, 0) for i in range(11)], [(f"N{i:02d}", f"N{i + 1:02d}") for i in range(10)])
    w = world(graph_nodes, {"A": "N02", "B": "N00"}, agv_type=vehicle(length=3.0))
    w.agvs["B"].agv_type = vehicle(length=0.0)
    w.go("A", "N10")
    w.go("B", "N07", at=50.0)
    w.go("A", "N05", at=100.0)
    w.go("B", "N00", at=100.5)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert w.agvs["A"].current_node == w.nodes["N05"]
    assert sorted(w.held("A")) == ["N05", "N05~N06", "N06", "N06~N07", "N07", "N07~N08"]
    granted = [e for e in w.of(TrafficReserved, agv="A") if e.t == 100.5]
    assert [e.resources for e in granted][:1] == [("N07~N08", "N07")]  # taken by the callback, while A moves


# --- heading and the coordinator --------------------------------------------------------------------------------


def test_heading_is_the_direction_of_the_last_arc_and_unknown_after_placement() -> None:
    w = world(t_layout(), {"A": "STORE_OUT"}, reserve=False)  # free flow: the AGV is then placed by hand
    agv = w.agvs["A"]
    assert agv.heading is None
    w.go("A", "C2")
    w.go("A", "PARK", at=100.0)
    w.env.run()
    assert agv.heading == pytest.approx(math.pi / 2)
    w.go("A", "STORE_OUT", at=200.0)
    w.env.run()
    assert agv.heading == pytest.approx(math.pi)
    assert [e.deltas.ops[-1] for e in w.of(AgvMoveEnded)][-1] == ("set", "A", "heading", agv.heading)
    agv.current_node = w.nodes["C1"]
    assert agv.heading is None and w.env.entities.snapshot()["A"]["heading"] is None


def test_the_lookahead_margin_is_a_coordinator_option() -> None:
    env = Environment()
    graph, nodes = line(2)
    agv = AGV(env=env, agv_type=vehicle(), initial_node=nodes["N0"])
    common: dict[str, Any] = {"env": env, "graph": graph, "warehouses": [], "charging_stations": []}
    assert FleetCoordinator(fleet=[agv], **common).lookahead_margin == 0.0
    other = AGV(env=env, agv_type=vehicle(), initial_node=nodes["N1"])
    assert FleetCoordinator(fleet=[other], lookahead_margin=2.5, **common).lookahead_margin == 2.5
    for bad in (-1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="lookahead_margin"):
            FleetCoordinator(fleet=[], lookahead_margin=bad, **common)
