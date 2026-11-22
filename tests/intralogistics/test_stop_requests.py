"""Stop requests while moving, and the energy horizon (spec §8.3, §9.5).

An interrupt received while a kinematic vehicle moves, or a battery found critical at a node it passes at speed,
stops it at the first node of its horizon where it can be at rest given its speed and deceleration. The arc being
traversed keeps its profile, so the vehicle passes the end of that arc at the speed planned for it. At rest the
executor withdraws its pending request, releases what lies ahead, emits ``agv.move_interrupted`` and raises the
interrupt, or returns ``RETRY_FROM_CURRENT_POSITION``. The plan covers only the arcs whose estimate, at the arc cap,
the battery affords.

The arcs are 10 m at ``Kinematics(1.5, 0.8, 0.8)``: a braking distance of 1.40625 m, so a run that passes a node at
1.5 m/s rests at the next node.

Every world runs :class:`FullReplay`; worlds with a ``ReservationController`` also run :class:`TrafficInvariants`.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvMoveStarted,
    AgvStranded,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitStarted,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.kinematics import Kinematics
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeFlowController, TrafficView
from simulatte.intralogistics.travel import TravelOutcome
from simulatte.intralogistics.warehouse import Warehouse
from tests.intralogistics.test_idle_traffic import Via
from tests.intralogistics.test_travel import Plain, World, run_of, steady, vehicle, world
from tests.intralogistics.test_travel_motion import arrivals, at_rest, side_line
from tests.intralogistics.traffic_support import FullReplay, StillFleet, TrafficInvariants, build, line

CASES = [(False, 0.0), (True, 0.0), (True, 25.0)]
"""``(reserve, lookahead_margin)``: free flow; reservation with the braking distance; reservation holding 25 m more."""


def interrupt_after(w: World, process: Any, first: float, second: float, fired: list[float]) -> None:
    """Interrupt `process` `second` after `first`, in two timers so that the second one is created at `first`."""

    def later() -> Any:
        yield w.env.timeout(first)
        yield w.env.timeout(second)
        fired.append(w.env.now)
        process.interrupt("cancelled")

    w.env.process(later())


# --- interrupts ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("reserve", "margin"), CASES)
def test_an_interrupt_on_a_cruising_arc_passes_its_end_at_speed_and_stops_at_the_end_of_the_next(
    reserve: bool, margin: float
) -> None:
    """Cancelled at 10.0 on arc 2 of 6, which was entered at 1.5 m/s: the arc keeps its profile, N2 is passed at speed,
    and the AGV rests at N3, wherever the horizon lies (spec §8.3)."""
    w = world(line(7), {"A": "N0"}, reserve=reserve, lookahead_margin=margin)
    process = w.go("A", "N6")
    w.interrupt(process, 10.0)
    w.env.run()
    assert w.caught == {"A": "cancelled"} and not w.outcomes
    durations = [arc.duration for arc in run_of(3)]
    now, times = 0.0, []
    for duration in durations:
        now += duration
        times.append(now)
    assert [node for node, _ in arrivals(w)] == ["N1", "N2", "N3"]
    assert [t for _, t in arrivals(w)] == pytest.approx(times, rel=1e-12)
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    assert started["N2"].motion["v0"] == 1.5 and at_rest(started["N2"].motion)  # N2 passed at speed, N3 at rest
    assert w.agvs["A"].current_node == w.nodes["N3"] and w.agvs["A"].motion is None
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.reason, interrupted.t) == ("N3", "cancelled", arrivals(w)[-1][1])
    assert not interrupted.deltas
    assert w.agvs["A"].battery.level == 970.0  # the three arcs covered, nothing more


@pytest.mark.parametrize(("margin", "farthest"), [(10.0, "N4"), (25.0, "N5")])
def test_an_interrupt_releases_every_claim_ahead_at_the_stop(margin: float, farthest: str) -> None:
    """The look-ahead margin makes the AGV hold claims beyond N3; the stop at N3 releases them at that instant."""
    w = world(line(7), {"A": "N0"}, lookahead_margin=margin)
    process = w.go("A", "N6")
    w.interrupt(process, 10.0)
    w.env.run()
    stop = arrivals(w)[-1][1]
    reserved = {resource for e in w.of(TrafficReserved) for resource in e.resources}
    released = {resource for e in w.of(TrafficReleased) for resource in e.resources}
    assert farthest in reserved
    assert released | {"N3"} == reserved | {"N0"}  # all but the node of the stop, and the start node
    ahead = {"N3~N4", "N4", "N4~N5", "N5", "N5~N6", "N6"}
    assert {e.t for e in w.of(TrafficReleased) if ahead & set(e.resources)} == {stop}
    assert w.held("A") == ["N3"] and not w.controller._pending and not w.of(TrafficWaitStarted)


def test_an_interrupt_stops_at_the_first_node_a_braking_distance_on_with_short_arcs() -> None:
    """With 1 m arcs the claims reach the next arc and the braking distance beyond it (three nodes past the arc being
    traversed), but the stop request truncates the plan at the first node 1.40625 m on from the end of that arc: the
    second one."""
    graph = build([(f"P{i}", float(i), 0.0) for i in range(31)], [(f"P{i}", f"P{i + 1}") for i in range(30)])
    w = world(graph, {"A": "P0"})
    process = w.go("A", "P30")
    w.interrupt(process, 6.0)
    w.env.run()
    (interrupted,) = w.of(AgvMoveInterrupted)
    reached = sum(1 for _, t in arrivals(w) if t <= 6.0)  # the AGV is on P<reached>, on the arc to P<reached + 1>
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    assert started[f"P{reached}"].motion["v0"] == 1.5 and f"P{reached + 1}" in started  # cruising, and passes it
    assert interrupted.node == f"P{reached + 3}"
    assert at_rest(started[f"P{reached + 2}"].motion) and w.held("A") == [interrupted.node]


def test_an_interrupt_on_an_arc_that_ends_at_rest_stops_at_its_end() -> None:
    """F brakes for N2 because L stands on N3. Cancelled on that arc with the claim of the next arc pending, F rests at
    N2 as planned, withdraws the claim and does not wait."""
    w = world(side_line(), {"L": "N3", "F": "N0"}, hold_back_dead_ends=False)
    process = w.go("F", "N4")
    w.interrupt(process, 10.0)
    w.env.run()
    assert w.caught == {"F": "cancelled"}
    assert [node for node, _ in arrivals(w, "F")] == ["N1", "N2"]
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.t) == ("N2", arrivals(w, "F")[-1][1])
    assert w.held("F") == ["N2"] and not w.controller._pending and not w.of(TrafficWaitStarted)


@pytest.mark.parametrize("reserve", [False, True])
def test_an_interrupt_on_a_profile_without_limits_stops_at_the_end_of_the_arc(reserve: bool) -> None:
    w = world(line(5), {"A": "N0"}, reserve=reserve, agv_type=vehicle(profile=Plain()))
    process = w.go("A", "N4")
    w.interrupt(process, 2.0)
    w.env.run()
    assert arrivals(w) == [("N1", 5.0)]
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.t) == ("N1", 5.0)
    assert w.agvs["A"].battery.level == 990.0


def test_a_second_interrupt_before_rest_is_dropped() -> None:
    """The first interrupt fixes the stop (N3); the second, received while the AGV still moves, changes nothing, and
    neither does a third one received after N2 was passed. Only the first cause is raised and reported."""
    w = world(line(7), {"A": "N0"})
    process = w.go("A", "N6")
    w.interrupt(process, 10.0, "first")
    w.interrupt(process, 12.0, "second")
    w.interrupt(process, 16.0, "third")  # on N2 -> N3, the arc that brakes
    w.env.run()
    assert w.caught == {"A": "first"}
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.reason) == ("N3", "first")
    assert [node for node, _ in arrivals(w)] == ["N1", "N2", "N3"]


@pytest.mark.parametrize(
    ("first", "stop"),
    [
        (True, "N3"),  # the interrupt sorts before the arrival at N2: it counts as received before it
        (False, "N4"),  # the arrival at N2 sorts first: the arc N2 -> N3 was entered with its profile fixed
    ],
)
def test_interrupt_at_the_instant_of_arrival(first: bool, stop: str) -> None:
    """An interrupt that falls on the instant the AGV reaches N2, which it passes at speed. Event order at one instant
    decides: an interrupt delivered before the arrival is a stop request received while moving, so the plan from N2
    already ends at N3; delivered after it, the arc N2 -> N3 has been entered with a profile that passes N3 at speed.
    Either way the AGV rests at the first node where it can, and the order is deterministic."""
    reference = world(line(7), {"A": "N0"})
    reference.go("A", "N6")
    reference.env.run()
    t1, t2 = (t for node, t in arrivals(reference) if node in ("N1", "N2"))
    w = world(line(7), {"A": "N0"})
    process = w.go("A", "N6")
    fired: list[float] = []
    if first:
        w.interrupt(process, t2)
        fired.append(t2)
    else:
        interrupt_after(w, process, t1, t2 - t1, fired)
    w.env.run()
    assert fired == [t2]
    assert w.caught == {"A": "cancelled"}
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert interrupted.node == stop
    assert arrivals(w)[1] == ("N2", t2)
    assert w.held("A") == [stop]


def test_an_interrupt_at_the_instant_of_arrival_at_rest() -> None:
    """The arrival at N2 (rest to rest) and the interrupt share the instant 10.0: the interrupt, delivered first,
    takes effect at N2 at that instant. Delivered after the arrival, the AGV has left and rests at N3."""
    outcomes = {}
    for first in (True, False):
        w = world(line(5), {"A": "N0"}, agv_type=vehicle(profile=Plain()))
        process = w.go("A", "N4")
        fired: list[float] = []
        if first:
            w.interrupt(process, 10.0)
            fired.append(10.0)
        else:
            interrupt_after(w, process, 5.0, 5.0, fired)
        w.env.run()
        (interrupted,) = w.of(AgvMoveInterrupted)
        outcomes[first] = (interrupted.node, interrupted.t)
    assert outcomes == {True: ("N2", 10.0), False: ("N3", 15.0)}


@pytest.mark.parametrize("reserve", [False, True])
def test_a_stop_request_rests_at_the_first_node_a_braking_distance_beyond_the_end_of_the_arc(reserve: bool) -> None:
    """Seeded sweep: a lone AGV on a straight line of random arc lengths and limits, interrupted at a random time. The
    stop node is computed apart from the executor: the arc being traversed ends at ``ref`` at the speed its profile
    says, and the AGV rests at the first node at least ``v ** 2 / (2 * decel)`` beyond ``ref``, or at ``ref`` when that
    speed is 0. No plan is refused (``plan_run`` raises when the AGV cannot stop within the plan), and every arc
    entered after the arc being traversed is covered by the plan to that node."""
    rng = random.Random(20260521)
    for case in range(80):
        count = rng.randint(3, 12)
        arcs = [rng.choice([0.4, 1.0, 2.5, 7.0, 12.0]) * rng.uniform(0.9, 1.1) for _ in range(count)]
        xs = [sum(arcs[:i]) for i in range(count + 1)]
        v_max, accel, decel = rng.uniform(0.5, 3.0), rng.uniform(0.3, 2.0), rng.uniform(0.3, 2.0)
        profile = TrapezoidalProfile(v_max, accel, decel, battery_degradation_fn=lambda level: 1.0)
        graph = build([(f"P{i}", x, 0.0) for i, x in enumerate(xs)], [(f"P{i}", f"P{i + 1}") for i in range(count)])
        margin = rng.choice([0.0, 0.0, 6.0])

        def run(interrupt: float | None, graph: Any = graph, profile: Any = profile, margin: float = margin) -> World:
            w = world(
                graph,
                {"A": "P0"},
                reserve=reserve,
                agv_type=vehicle(profile=profile),
                lookahead_margin=margin if reserve else 0.0,
            )
            process = w.go("A", f"P{count}")
            if interrupt is not None:
                w.interrupt(process, interrupt)
            w.env.run()
            return w

        reference = run(None)
        total = arrivals(reference)[-1][1]
        at = rng.uniform(0.0, total * 0.97)
        w = run(at)
        assert w.caught == {"A": "cancelled"}, case
        (interrupted,) = w.of(AgvMoveInterrupted)
        moving = [e for e in w.of(AgvMoveStarted) if e.t <= at < e.t_end][0]
        description = moving.motion
        speed = max(0.0, description["v0"] + sum(duration * a for duration, a in description["phases"]))
        ref = int(moving.to_node[1:])
        stop = ref
        if speed > 1e-9:
            while xs[stop] - xs[ref] < speed**2 / (2 * decel) * (1 - 1e-9) and stop < count:
                stop += 1
        assert interrupted.node == f"P{stop}", (case, at, speed)
        started = {e.from_node: e for e in w.of(AgvMoveStarted)}
        assert at_rest(started[f"P{stop - 1}"].motion) or stop == ref  # the plan ends at rest at the stop node


# --- the mission recovers as before -------------------------------------------------------------------------------


def mission_world(
    reserve: bool, *, loaded: bool
) -> tuple[Environment, FleetCoordinator, Any, Warehouse, AGV, FullReplay, SKU]:
    """Six 10 m arcs, a warehouse at each end; the order goes from ``N0`` to ``N6`` and the AGV starts on ``N0`` (to
    carry cargo) or on ``N6`` (to drive empty to the origin)."""
    graph, nodes = line(7)
    env = Environment(debug=True)
    replay = FullReplay(env)
    sku = SKU(id="SKU-A", weight=5.0, volume=0.1)
    origin = Warehouse(
        env=env,
        name="WH-ORIGIN",
        input_bays=[nodes["N0"]],
        output_bays=[nodes["N0"]],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: 100},
        pick_time=1.0,
        put_time=1.0,
    )
    destination = Warehouse(
        env=env,
        name="WH-DEST",
        input_bays=[nodes["N6"]],
        output_bays=[nodes["N6"]],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: 0},
        pick_time=1.0,
        put_time=1.0,
    )
    kind = AGVType(
        name="cart",
        speed_profile=steady(),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time=1.0,
        unload_time=1.0,
        length=0.0,
    )
    agv = AGV(env=env, agv_type=kind, agv_id="A", initial_node=nodes["N0" if loaded else "N6"])
    controller: Any = ReservationController(env=env, graph=graph) if reserve else FreeFlowController()
    fleet = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=[agv],
        warehouses=[origin, destination],
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
    )
    if reserve:
        TrafficInvariants(env, controller)
    order = fleet.create_order(sku=sku, quantity=10, origin=origin, destination=destination)
    fleet.submit(order)
    return env, fleet, order, origin, agv, replay, sku


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize("loaded", [True, False])
def test_a_cancelled_mission_recovers_after_a_stop_at_the_first_feasible_node(reserve: bool, loaded: bool) -> None:
    """The order is cancelled on arc 2 of the six, at cruising speed. The AGV rests at N3 (from N0) or N3 (from N6),
    returns the cargo it carries to the origin, and ends idle with the order ``CANCELLED``."""
    env, fleet, order, origin, agv, replay, sku = mission_world(reserve, loaded=loaded)
    t0 = 2.0 if loaded else 0.0  # the AGV that starts at the origin picks (1) and loads (1) first

    def cancel() -> Any:
        yield env.timeout(t0 + 12.0)
        fleet.cancel(order)

    env.process(cancel())
    env.run()
    moves = [e for e in replay.events if isinstance(e, AgvMoveStarted)]
    assert moves[0].t == t0 and moves[0].loaded is loaded
    (interrupted,) = [e for e in replay.events if isinstance(e, AgvMoveInterrupted)]
    assert (interrupted.node, interrupted.reason) == ("N3", "cancelled")
    assert interrupted.t == pytest.approx(t0 + 7.604166666666667 + 6.666666666666667 + 7.604166666666667, rel=1e-12)
    assert order.status is OrderStatus.CANCELLED and agv.state is AGVState.IDLE and agv.current_load is None
    assert origin.get_inventory_level(sku) == 100  # the cargo is back (or was never taken)
    assert agv.battery.level < 1000.0


# --- the battery --------------------------------------------------------------------------------------------------


def weak(depletion: Any = None, *, capacity: float = 1000.0, threshold: float = 0.05, profile: Any = None) -> AGVType:
    return AGVType(
        name="weak",
        speed_profile=steady() if profile is None else profile,
        battery_capacity=capacity,
        weight_capacity=100.0,
        volume_capacity=10.0,
        depletion_fn=depletion,
        critical_battery_threshold=threshold,
        length=0.0,
    )


@pytest.mark.parametrize("reserve", [False, True])
def test_a_level_critical_on_arrival_stops_the_agv_at_the_first_feasible_node(reserve: bool) -> None:
    """The depletion is 5 per metre below 1.4 m/s and 1 above. The estimate, at the arc cap 1.5, is 10 per arc and
    foresees a critical level (0.96) only at N4. The first arc, from rest, averages 1.3 m/s and costs 50: the AGV
    arrives at N1 critical, passes it at speed and rests at N2, a braking distance on, where the travel returns
    (spec §8.3), under both controllers."""
    kind = weak(lambda distance, load, speed: distance * (1.0 if speed >= 1.4 else 5.0), threshold=0.96)
    # Under reservation the claims reach N4, where the estimate turns critical: only a stop request ends the plan at N2.
    w = world(line(6), {"A": "N0"}, reserve=reserve, agv_type=kind, lookahead_margin=25.0)
    w.go("A", "N5")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    assert w.agvs["A"].current_node == w.nodes["N2"]
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    assert not at_rest(started["N0"].motion) and started["N1"].motion["v0"] == 1.5  # N1 is passed at speed
    assert at_rest(started["N1"].motion)  # and the arc to N2 brakes
    ended = {e.node: e for e in w.of(AgvMoveEnded)}
    assert [e.battery for e in ended.values()] == [950.0, 900.0] and ended["N1"].t == started["N1"].t
    assert not w.of(AgvMoveInterrupted)
    if reserve:
        assert w.held("A") == ["N2"] and not w.controller._pending
        assert {r for e in w.of(TrafficReserved) for r in e.resources} >= {"N4"}  # claims reached past the stop node
        assert not [e for e in w.of(TrafficReserved) if e.t > ended["N1"].t]  # none asked for after the stop request


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(("interrupt_at", "cause"), [(3.0, "early"), (10.0, "late")])
def test_a_battery_stop_and_an_interrupt_share_the_first_stop_node(
    reserve: bool, interrupt_at: float, cause: str
) -> None:
    """With the depletion of the test above the level turns critical at N1 against the estimate. An interrupt received
    before (3.0, on the first arc) or after (10.0, on the second) that finds the stop already fixed at N2: the first
    request keeps its node, and at rest the interrupt, not the retry, reaches the mission."""
    kind = weak(lambda distance, load, speed: distance * (1.0 if speed >= 1.4 else 5.0), threshold=0.96)
    w = world(line(6), {"A": "N0"}, reserve=reserve, agv_type=kind, lookahead_margin=25.0)
    process = w.go("A", "N5")
    w.interrupt(process, interrupt_at, cause)
    w.env.run()
    assert w.caught == {"A": cause} and not w.outcomes
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.reason) == ("N2", cause)
    assert [node for node, _ in arrivals(w)] == ["N1", "N2"]


@pytest.mark.parametrize("reserve", [False, True])
def test_a_critical_level_found_at_the_goal_arrives(reserve: bool) -> None:
    """A critical level on arrival at the goal does not change the outcome (spec §8.2): the travel arrives."""
    kind = weak(lambda distance, load, speed: distance * 60.0, threshold=0.5)
    w = world(line(2), {"A": "N0"}, reserve=reserve, agv_type=kind)
    w.go("A", "N1")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED} and w.agvs["A"].battery.is_critical


def charger_world(
    capacity: float,
    station: str | None,
    *,
    depletion: Any = None,
    reserve: bool = True,
    graph_nodes: tuple[LayoutGraph, dict[str, Node]] | None = None,
    router: Any = None,
    start: str = "N0",
) -> World:
    """The line ``N0 ... N4`` of 10 m arcs (or `graph_nodes`) with a charging station on `station` (none: no one)."""
    graph, nodes = graph_nodes or line(5)
    env = Environment(debug=True)
    replay = FullReplay(env)
    controller: Any = ReservationController(env=env, graph=graph) if reserve else FreeFlowController()
    agv = AGV(env=env, agv_type=weak(depletion, capacity=capacity), agv_id="A", initial_node=nodes[start])
    stations = [] if station is None else [ChargingStation(env=env, name="CS", node=nodes[station], n_slots=1)]
    fleet = StillFleet(
        env=env,
        graph=graph,
        fleet=[agv],
        warehouses=[],
        charging_stations=stations,
        traffic_controller=controller,
        router=router,
        default_metrics=False,
    )
    if reserve:
        TrafficInvariants(env, controller)
    env.activate()
    return World(env, fleet, nodes, {"A": agv}, replay, len(replay.events))


def test_energy_for_two_and_a_half_arcs_ends_the_plan_at_the_second_node_and_the_charger_takes_over() -> None:
    w = charger_world(25.0, "N2")
    w.go("A", "N4")
    w.env.run()
    assert [node for node, _ in arrivals(w)] == ["N1", "N2"]  # no third arc is started
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    assert w.agvs["A"].current_node == w.nodes["N2"] and w.agvs["A"].battery.level > 5.0  # charged there
    assert not w.of(AgvStranded)
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    assert at_rest(started["N1"].motion)  # the plan ended at rest at N2


def test_energy_for_two_and_a_half_arcs_strands_the_agv_when_no_charger_is_reachable() -> None:
    w = charger_world(25.0, None)
    w.go("A", "N4")
    w.env.run()
    assert [node for node, _ in arrivals(w)] == ["N1", "N2"]
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    (stranded,) = w.of(AgvStranded)
    assert (stranded.node, stranded.reason, stranded.t) == ("N2", "no_reachable_charger", arrivals(w)[-1][1])
    assert w.agvs["A"].battery.level == pytest.approx(5.0)


def speed_cost(distance: float, load: float, speed: float) -> float:
    return distance * speed


def limited_line(limits: dict[int, float]) -> tuple[LayoutGraph, dict[str, Node]]:
    """``N0 ... N5`` of 10 m arcs; arc `i` (from ``N<i>``) has the speed limit ``limits[i]`` when given."""
    nodes = {f"N{i}": Node(f"N{i}", 10.0 * i, 0.0) for i in range(6)}
    arcs = [Arc(nodes[f"N{i}"], nodes[f"N{i + 1}"], speed_limit=limits.get(i)) for i in range(5)]
    return LayoutGraph(nodes.values(), arcs), nodes


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(("limits", "last"), [({}, "N2"), ({2: 0.5}, "N3"), ({1: 0.25}, "N3")])
def test_the_energy_horizon_uses_the_arc_cap(reserve: bool, limits: dict[int, float], last: str) -> None:
    """The depletion is the speed times the distance. At the arc cap 1.5 an arc costs 15 and a battery of 40 affords
    two (the old estimate, at the average speed of one arc from rest, 1.17, would have afforded three). A limit of
    0.5 on the third arc makes it cost 5, so the plan reaches N3; a limit of 0.25 on the second makes it cost 2.5, and
    the plan reaches N3 too (15 + 2.5 + 15 = 32.5)."""
    w = world(limited_line(limits), {"A": "N0"}, reserve=reserve, agv_type=weak(speed_cost, capacity=40.0))
    w.go("A", "N5")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert w.agvs["A"].current_node == w.nodes[last]
    (stranded,) = w.of(AgvStranded)
    assert stranded.node == last and stranded.reason == "no_reachable_charger"


@pytest.mark.parametrize("reserve", [False, True])
def test_the_energy_check_at_rest_uses_the_arc_cap(reserve: bool) -> None:
    """A battery of 14 would take one arc at its average speed from rest (1.17 m/s, 11.7) but not at the arc cap (15):
    the AGV does not depart."""
    w = world(line(3), {"A": "N0"}, reserve=reserve, agv_type=weak(speed_cost, capacity=14.0))
    w.go("A", "N2")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert not w.of(AgvMoveStarted)
    (stranded,) = w.of(AgvStranded)
    assert (stranded.node, stranded.t) == ("N0", 0.0)


class Stalled:
    """Limits with a negative top speed: the vehicle cannot move."""

    def limits(self, load_weight: float, battery_level: float) -> Kinematics:
        return Kinematics(-1.0, 0.8, 0.8)

    def travel_time(self, distance: float, *args: Any, **kwargs: Any) -> float:
        return math.inf


@pytest.mark.parametrize(("capacity", "stalls"), [(1.2, True), (0.8, False)])
def test_a_vehicle_that_cannot_move_estimates_its_energy_at_speed_zero(capacity: float, stalls: bool) -> None:
    """The depletion is ``0.05 * distance * (2 + speed)``: 1.0 for an arc at speed 0, 0.5 at the speed -1 of the
    limits. A stalled run is estimated at speed 0 (``max(cap, 0)``), so with 1.2 the check at rest passes and the
    vehicle stalls on its first arc until interrupted, and with 0.8 it is stranded at once."""
    kind = weak(lambda distance, load, speed: 0.05 * distance * (2.0 + speed), capacity=capacity, profile=Stalled())
    w = world(line(3), {"A": "N0"}, agv_type=kind)
    process = w.go("A", "N2")
    if stalls:
        w.interrupt(process, 5.0)
    w.env.run()
    if stalls:
        assert w.caught == {"A": "cancelled"} and not w.of(AgvStranded)
        (started,) = w.of(AgvMoveStarted)
        assert started.t_end == math.inf
    else:
        assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED} and not w.of(AgvMoveStarted)


class Fixed:
    """Limits fixed at `kin`, whatever the load and the battery level."""

    def __init__(self, kin: Kinematics) -> None:
        self.kin = kin

    def limits(self, load_weight: float, battery_level: float) -> Kinematics:
        return self.kin

    def travel_time(self, distance: float, *args: Any, **kwargs: Any) -> float:
        return distance


NAN, INF = math.nan, math.inf


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(
    ("kin", "problem"),
    [
        (Kinematics(NAN, 0.8, 0.8), "finite"),
        (Kinematics(1.5, NAN, 0.8), "finite"),
        (Kinematics(1.5, 0.8, NAN), "finite"),
        (Kinematics(INF, 0.8, 0.8), "finite"),
        (Kinematics(1.5, INF, 0.8), "finite"),
        (Kinematics(1.5, 0.8, INF), "finite"),
        (Kinematics(1.5, 0.8, -0.8), "negative"),
        (Kinematics(0.0, 0.8, -0.8), "negative"),
        (Kinematics(1.5, 0.8, 0.0), "a stop cannot be planned"),
    ],
)
def test_invalid_sampled_limits_raise_naming_the_profile_and_the_values(
    reserve: bool, kin: Kinematics, problem: str
) -> None:
    w = world(line(3), {"A": "N0"}, reserve=reserve, agv_type=vehicle(profile=Fixed(kin)))
    w.go("A", "N2")
    with pytest.raises(ValueError, match=rf"Fixed\.limits\(.*\) returned {re.escape(repr(kin))}: .*{problem}"):
        w.env.run()
    assert not w.of(AgvMoveStarted)


@pytest.mark.parametrize(
    "kin",
    [Kinematics(0.0, 0.8, 0.8), Kinematics(1.5, 0.0, 0.8), Kinematics(1.5, -0.8, 0.8), Kinematics(0.0, 0.8, 0.0)],
)
def test_limits_with_a_top_speed_or_acceleration_not_positive_stall_as_before(kin: Kinematics) -> None:
    """A non-positive ``v_max`` or ``accel`` is a vehicle that cannot move (spec §9.1): the run stalls, whatever the
    deceleration, until an interrupt."""
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(profile=Fixed(kin)))
    w.interrupt(w.go("A", "N2"), 5.0)
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    (started,) = w.of(AgvMoveStarted)
    assert started.t_end == math.inf


# --- fix round 1: one estimate for the check at rest, the horizon and charger reachability --------------------------


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(("capacity", "charges"), [(14.0, False), (20.0, True)])
def test_the_charger_is_reachable_by_the_same_estimate_as_the_check_at_rest(
    reserve: bool, capacity: float, charges: bool
) -> None:
    """The depletion is the speed times the distance, so the estimate at the cap 1.5 is 15 per arc. With 14 not even the
    arc to the charger on N1 is affordable: the AGV is stranded at once (no ``RecursionError`` from a diversion that
    cannot leave). With 20 the plan ends at N1, where the charger takes over."""
    w = charger_world(capacity, "N1", depletion=speed_cost, reserve=reserve)
    w.go("A", "N4")
    w.env.run()
    if charges:
        assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
        assert w.agvs["A"].current_node == w.nodes["N1"] and w.agvs["A"].battery.level == capacity  # recharged
        assert not w.of(AgvStranded)
    else:
        assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED} and not w.of(AgvMoveStarted)
        (stranded,) = w.of(AgvStranded)
        assert (stranded.node, stranded.reason) == ("N0", "no_reachable_charger")


class Detours(Via):
    """:class:`Via`, except that the first request of each pair in `first` gets the shortest path: the route a charger
    ranking estimates is not the one its diversion then travels, as with a router whose answer changes between the
    two requests."""

    def __init__(
        self, nodes: dict[str, Node], fixed: dict[tuple[str, str], tuple[str, ...]], first: set[tuple[str, str]]
    ) -> None:
        super().__init__(nodes, fixed)
        self.first = first

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        key = (request.origin.id, request.destination.id)
        if key in self.first:
            self.first.discard(key)
            return ShortestDistanceRouter.route(self, request, view)
        return super().route(request, view)


@pytest.mark.parametrize("reserve", [False, True])
def test_a_diversion_whose_first_arc_is_not_affordable_strands_without_recursing(reserve: bool) -> None:
    """The charger on N1 is reachable on the route the router first gives (the arc N0 N1 has a speed limit of 0.5, so
    it costs 5), but the router then sends the diversion through X, whose first arc costs 14.1 against a battery of 13,
    as it sends the travel to N4. The diversion cannot leave N0: it strands the AGV there, once, instead of diverting
    again."""
    nodes = {n: Node(n, x, y) for n, x, y in [("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("X", 5, 8)]}
    arcs = [Arc(nodes["N0"], nodes["N1"], speed_limit=0.5), Arc(nodes["N1"], nodes["N2"])]
    arcs += [Arc(nodes["N0"], nodes["X"]), Arc(nodes["X"], nodes["N1"])]
    graph = LayoutGraph(nodes.values(), arcs)
    router = Detours(
        nodes, {("N0", "N2"): ("N0", "X", "N1", "N2"), ("N0", "N1"): ("N0", "X", "N1")}, first={("N0", "N1")}
    )
    w = charger_world(13.0, "N1", depletion=speed_cost, reserve=reserve, graph_nodes=(graph, nodes), router=router)
    w.go("A", "N2")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert w.agvs["A"].current_node == nodes["N0"]
    (stranded,) = w.of(AgvStranded)
    assert (stranded.node, stranded.reason) == ("N0", "no_reachable_charger")
    assert not w.of(AgvMoveStarted)


# --- fix round 1: the trail clears with the controller's on a roomy node --------------------------------------------


def long_vehicle_line(roomy: str | None) -> World:
    """``N0 ... N5`` of 10 m arcs with the vehicle on N3; it is 100 m long, longer than the line, so on a node of
    capacity 1 its body overlaps everything it has driven over; `roomy` names a node of capacity 2."""
    graph, nodes = line(6)
    capacities = {} if roomy is None else {nodes[roomy]: 2}
    return world((graph, nodes), {"A": "N3"}, agv_type=vehicle(length=100.0), capacities=capacities)


def holds(w: World) -> list[str]:
    return sorted(w.held("A"))


def test_an_agv_stopped_on_a_roomy_node_holds_only_that_node() -> None:
    """Driven N3 to N0, then towards N5 and cancelled 1 s in, the AGV rests at N2. On a node of capacity 1 its body,
    100 m long, overlaps all it has driven over (control, below). On N2 of capacity 2 the trail clears (§6.6): it
    overlaps N2 only, and the claims it held ahead (N3 and the segment) are released with the rest."""
    results = {}
    for roomy in (None, "N2"):
        w = long_vehicle_line(roomy)
        w.go("A", "N0")
        w.env.run()
        everything = holds(w)
        assert everything == ["N0", "N0~N1", "N1", "N1~N2", "N2", "N2~N3", "N3"]
        process = w.go("A", "N5", at=w.env.now)
        w.interrupt(process, 1.0)
        w.env.run()
        assert w.agvs["A"].current_node == w.nodes["N2"] and w.caught == {"A": "cancelled"}
        results[roomy] = holds(w)
        if roomy:
            assert not w.fleet._executor._trail(w.agvs["A"]).entries and not w.controller._pending
    assert results[None] == everything
    assert results["N2"] == ["N2"]


def test_a_later_travel_over_the_old_trail_holds_nothing_ahead_of_it() -> None:
    """After an ordinary arrival on N4 (capacity 2) the AGV holds N4 only and its trail is empty. Driving back towards
    N0 and cancelled 1 s in, it rests at N2 and holds what its 100 m body overlaps behind it on this trip: N2 to N4.
    Nothing lies ahead of it (N0 to N1~N2)."""
    graph, nodes = line(8)
    w = world((graph, nodes), {"A": "N5"}, agv_type=vehicle(length=100.0), capacities={nodes["N4"]: 2})
    agv = w.agvs["A"]
    w.go("A", "N0")
    w.env.run()
    w.go("A", "N4", at=w.env.now)
    w.env.run()
    assert holds(w) == ["N4"] and not w.fleet._executor._trail(agv).entries
    process = w.go("A", "N0", at=w.env.now)
    w.interrupt(process, 1.0)
    w.env.run()
    assert agv.current_node == nodes["N2"] and w.caught == {"A": "cancelled"}
    assert holds(w) == ["N2", "N2~N3", "N3", "N3~N4", "N4"]


# --- fix round 1: the energy horizon at node events -----------------------------------------------------------------


def falls_with_speed(distance: float, load: float, speed: float) -> float:
    """5 per metre below 1.4 m/s, 1 above: the estimate at the cap 1.5 (10 per arc) under-predicts the first arc."""
    return distance * (1.0 if speed >= 1.4 else 5.0)


@pytest.mark.parametrize("reserve", [False, True])
def test_the_energy_horizon_shortens_the_plan_at_a_node_event_under_both_controllers(reserve: bool) -> None:
    """N0 to N10 with a battery of 100. The first arc costs 50, not 10: from N1 the estimate affords five arcs and turns
    critical at N6. Free flow and reservation alike come to rest at N6, never passing it at speed."""
    w = world(line(11), {"A": "N0"}, reserve=reserve, agv_type=weak(falls_with_speed, capacity=100.0))
    w.go("A", "N10")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    assert w.agvs["A"].current_node == w.nodes["N6"]
    started = {e.from_node: e for e in w.of(AgvMoveStarted)}
    assert at_rest(started["N5"].motion) and "N6" in {e.node for e in w.of(AgvMoveEnded)}
    assert not [e for e in w.of(AgvMoveStarted) if e.from_node == "N6"]


@pytest.mark.parametrize("reserve", [False, True])
def test_a_charger_on_the_node_where_the_estimate_turns_critical_is_not_passed_at_speed(reserve: bool) -> None:
    graph_nodes = line(11)
    w = charger_world(100.0, "N6", depletion=falls_with_speed, reserve=reserve, graph_nodes=graph_nodes)
    w.go("A", "N10")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    assert w.agvs["A"].current_node == w.nodes["N6"] and w.agvs["A"].battery.level == 0.0
    assert at_rest({e.from_node: e for e in w.of(AgvMoveStarted)}["N5"].motion)


def test_a_lone_agv_has_the_same_arrivals_under_both_controllers_with_a_falling_depletion() -> None:
    runs = []
    for reserve in (False, True):
        w = world(line(11), {"A": "N0"}, reserve=reserve, agv_type=weak(falls_with_speed, capacity=100.0))
        w.go("A", "N10")
        w.env.run()
        runs.append(arrivals(w))
    assert runs[0] == runs[1] and runs[0][-1][0] == "N6"


# --- fix round 2: stop requests combine, nested diversions, the cost of the node-event check ------------------------


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize(("at", "stop", "when"), [(10.0, "N3", 21.875), (20.0, "N4", 28.5416666666666)])
def test_an_interrupt_after_an_energy_stop_request_takes_effect_at_its_own_first_feasible_node(
    reserve: bool, at: float, stop: str, when: float
) -> None:
    """The falling depletion makes the AGV ask, at N1, to stop at the affordable end N6. An interrupt received later,
    on N1 -> N2 (10.0) or N2 -> N3 (20.0), moves the stop nearer: the first node a braking distance beyond the end
    of the arc being traversed, as without the energy request, and the same under both controllers."""
    w = world(line(11), {"A": "N0"}, reserve=reserve, agv_type=weak(falls_with_speed, capacity=100.0))
    process = w.go("A", "N10")
    w.interrupt(process, at)
    w.env.run()
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.t) == (stop, pytest.approx(when, rel=1e-12))
    assert w.held("A") == [stop] if reserve else True


@pytest.mark.parametrize("reserve", [False, True])
def test_an_energy_request_after_an_interrupt_never_moves_the_stop_farther(reserve: bool) -> None:
    """Cancelled at 3.0 on the first arc, the AGV is to rest at N2. At N1 the energy horizon asks for N6, which is
    farther: the stop stays at N2."""
    w = world(line(11), {"A": "N0"}, reserve=reserve, agv_type=weak(falls_with_speed, capacity=100.0))
    process = w.go("A", "N10")
    w.interrupt(process, 3.0)
    w.env.run()
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert interrupted.node == "N2" and [node for node, _ in arrivals(w)] == ["N1", "N2"]


def trace_336_world(reserve: bool) -> World:
    """Twelve arcs of 1, 10, 1, 10, 1, 2, 10, 1, 2, 2, 1, 2 m (a limit of 1.794 on the fifth), limits (2.98, 0.8,
    1.275), depletion ``3 d / (0.5 + v)`` that falls with speed, battery 34.4, threshold 0: after each arc the level
    is lower than the estimate foresaw and the affordable end comes nearer."""
    lengths = [1.0, 10.0, 1.0, 10.0, 1.0, 2.0, 10.0, 1.0, 2.0, 2.0, 1.0, 2.0]
    limits: list[float | None] = [None] * len(lengths)
    limits[4] = 1.794
    nodes: dict[str, Node] = {}
    x = 0.0
    for i in range(len(lengths) + 1):
        nodes[f"E{i:03d}"] = Node(f"E{i:03d}", x, 0.0)
        x += lengths[i] if i < len(lengths) else 0.0
    names = list(nodes)
    arcs = [
        Arc(nodes[names[i]], nodes[names[i + 1]], length=lengths[i], speed_limit=limits[i]) for i in range(len(lengths))
    ]
    profile = TrapezoidalProfile(2.9815443942111157, 0.8, 1.2749757037707088, battery_degradation_fn=lambda level: 1.0)
    kind = weak(lambda d, load, speed: d * 3.0 / (0.5 + speed), capacity=34.4, threshold=0.0, profile=profile)
    return world(
        (LayoutGraph(nodes.values(), arcs), nodes),
        {"A": "E000"},
        reserve=reserve,
        agv_type=kind,
        lookahead_margin=5.0 if reserve else 0.0,
    )


@pytest.mark.parametrize("reserve", [False, True])
def test_an_affordable_end_that_comes_nearer_at_a_later_node_event_is_honoured(reserve: bool) -> None:
    """The affordable end moves from E008 to E007 while the AGV runs. Free flow rests at E007, as reservation does."""
    w = trace_336_world(reserve)
    w.go("A", "E012")
    w.env.run()
    assert w.agvs["A"].current_node == w.nodes["E007"]
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}


def energy_run(
    lengths: list[float],
    limits: list[float | None],
    kin: tuple[float, float, float],
    kind_kw: dict[str, Any],
    reserve: bool,
    margin: float,
) -> tuple[Any, ...]:
    nodes: dict[str, Node] = {}
    x = 0.0
    for i in range(len(lengths) + 1):
        nodes[f"E{i:03d}"] = Node(f"E{i:03d}", x, 0.0)
        x += lengths[i] if i < len(lengths) else 0.0
    names = list(nodes)
    arcs = [
        Arc(nodes[names[i]], nodes[names[i + 1]], length=lengths[i], speed_limit=limits[i]) for i in range(len(lengths))
    ]
    profile = TrapezoidalProfile(*kin, battery_degradation_fn=lambda level: 1.0)
    w = world(
        (LayoutGraph(nodes.values(), arcs), nodes),
        {"A": names[0]},
        reserve=reserve,
        agv_type=weak(profile=profile, **kind_kw),
        lookahead_margin=margin,
    )
    w.go("A", names[-1])
    w.env.run()
    ended = [(e.node, e.t, e.battery) for e in w.of(AgvMoveEnded)]
    return ended, [(e.node, e.reason) for e in w.of(AgvStranded)], dict(w.outcomes), w


FALLING = {
    "steps": lambda d, load, v: d * (1.0 if v >= 1.2 else 4.0),
    "smooth": lambda d, load, v: d * 3.0 / (0.5 + v),
    "default": None,
}


def test_a_lone_agv_ends_alike_under_both_controllers_for_a_depletion_that_falls_with_speed() -> None:
    """Seeded sweep of random lines, limits and batteries: the arrivals, levels, outcome and rest node are the same
    under free flow and reservation."""
    rng = random.Random(2)  # seed 2 differs under free flow from trial 52 when an energy request is not renewed
    for trial in range(260):
        kin = (
            rng.choice([1.5, 2.0, rng.uniform(0.6, 3.0)]),
            rng.choice([0.8, rng.uniform(0.3, 2.0)]),
            rng.choice([0.8, rng.uniform(0.3, 2.0)]),
        )
        count = rng.randint(3, 14)
        lengths = [rng.choice([10.0, 2.0, 1.0, rng.uniform(0.5, 12.0)]) for _ in range(count)]
        limits = [rng.choice([None, None, None, rng.uniform(0.3, 2 * kin[0])]) for _ in range(count)]
        name = rng.choice(list(FALLING))
        capacity = rng.choice([0.4, 0.8, 1.5, 3.0]) * sum(lengths) * (2.0 if name != "default" else 1.0)
        kind_kw = {"depletion": FALLING[name], "capacity": capacity, "threshold": rng.choice([0.05, 0.05, 0.3, 0.0])}
        margin = rng.choice([0.0, 0.0, 5.0])
        free = energy_run(lengths, limits, kin, kind_kw, False, margin)
        reserved = energy_run(lengths, limits, kin, kind_kw, True, margin)
        assert free[:3] == reserved[:3], (trial, name)
        assert free[3].agvs["A"].current_node.id == reserved[3].agvs["A"].current_node.id


@pytest.mark.parametrize("reserve", [False, True])
def test_a_nested_diversion_from_a_second_node_strands_without_raising(reserve: bool) -> None:
    """S to G costs 30 against a battery of 25: the AGV diverts to the charger on C, reachable on the route the router
    first gives, which then sends the diversion over a detour: at Y the diversion again lacks the energy and diverts
    from Y, where the route through Z is too long. The guard of the first diversion survives the second: the travel
    returns ``BATTERY_STRANDED`` at Y."""
    nodes = {n: Node(n, x, y) for n, x, y in [("S", 0, 0), ("G", 30, 0), ("Y", 0, 10), ("C", 0, 20), ("Z", 3, 19)]}
    arcs = [
        Arc(nodes["S"], nodes["G"]),
        Arc(nodes["S"], nodes["Y"]),
        Arc(nodes["Y"], nodes["C"]),
        Arc(nodes["Y"], nodes["Z"], length=20.0),
        Arc(nodes["Z"], nodes["C"]),
    ]
    graph = LayoutGraph(nodes.values(), arcs)
    router = Detours(
        nodes, {("S", "C"): ("S", "Y", "Z", "C"), ("Y", "C"): ("Y", "Z", "C")}, first={("S", "C"), ("Y", "C")}
    )
    w = charger_world(25.0, "C", reserve=reserve, graph_nodes=(graph, nodes), router=router, start="S")
    w.go("A", "G")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert w.agvs["A"].current_node == nodes["Y"]
    assert [(e.node, e.reason) for e in w.of(AgvStranded)] == [("Y", "no_reachable_charger")]


def test_the_energy_check_at_node_events_ignores_a_level_as_expected(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default depletion the level at every node is the one the plan expected, bit for bit, so a route of 800
    arcs under free flow scans the affordable end no more often than a route of 30."""
    from simulatte.intralogistics.travel import _Trip

    counts = {}
    for arcs in (30, 800):
        calls = []
        original = _Trip._affordable_end
        monkeypatch.setattr(
            _Trip,
            "_affordable_end",
            lambda self, limit, *args, calls=calls: (calls.append(limit), original(self, limit, *args))[1],
        )
        graph, nodes = build(
            [(f"P{i}", 1.0 * i, 0.0) for i in range(arcs + 1)], [(f"P{i}", f"P{i + 1}") for i in range(arcs)]
        )
        w = world((graph, nodes), {"A": "P0"}, reserve=False, agv_type=weak(capacity=1e9))
        w.go("A", f"P{arcs}")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        counts[arcs] = len(calls)
        monkeypatch.undo()
    assert counts[800] == counts[30] <= 3


@pytest.mark.parametrize(
    "depletion",
    [lambda d, load, v: d * (1.0 if v >= 1.2 else 4.0), lambda d, load, v: d * 3.0 / (0.5 + v)],
    ids=["step", "smooth"],
)
def test_after_a_deficit_that_does_not_grow_the_energy_horizon_is_not_scanned_again(
    monkeypatch: pytest.MonkeyPatch, depletion: Any
) -> None:
    """A depletion that falls with speed costs more than the estimate at the arc cap on the slow arcs from rest, and the
    estimate on the arcs passed at the cap. After the first scan, which finds the plan affordable, the level never falls
    below the levels that scan carried, so a route of 800 arcs under free flow scans no more often than one of 30."""
    from simulatte.intralogistics.travel import _Trip

    counts = {}
    for arcs in (30, 800):
        calls: list[int] = []
        original = _Trip._affordable_end
        monkeypatch.setattr(
            _Trip, "_affordable_end", lambda self, *args, calls=calls: (calls.append(1), original(self, *args))[1]
        )
        graph, nodes = build(
            [(f"P{i}", 1.0 * i, 0.0) for i in range(arcs + 1)], [(f"P{i}", f"P{i + 1}") for i in range(arcs)]
        )
        w = world((graph, nodes), {"A": "P0"}, reserve=False, agv_type=weak(depletion, capacity=1e9))
        w.go("A", f"P{arcs}")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        assert w.of(AgvMoveEnded)[0].battery < 1e9 - 1.0  # the first arc cost more than its estimate of 1
        counts[arcs] = len(calls)
        monkeypatch.undo()
    assert counts[800] == counts[30] <= 4


def test_float_ties_in_the_energy_horizon_change_nothing_for_the_default_depletion() -> None:
    """Arcs that are not exact in binary and a battery sized for exactly k arcs, or to reach the threshold at a node:
    the two controllers end alike, and the AGV rests at an interior node only where it cannot go on (the level is
    critical, or the next arc is not affordable)."""
    rng = random.Random(7)
    for trial in range(150):
        length = rng.choice([0.1, 0.3, 0.7, 1.1, 2.3])
        count = rng.randint(4, 30)
        k = rng.randint(2, count)
        capacity = length * k * rng.choice([1.0, 1.0, 1.0, 1 + 1e-15, 1 - 1e-15]) / rng.choice([1.0, 0.95, 0.5])
        kind_kw = {"capacity": capacity, "threshold": rng.choice([0.05, 0.0, 0.5])}
        runs = [
            energy_run([length] * count, [None] * count, (1.5, 0.8, 0.8), kind_kw, reserve, 0.0)
            for reserve in (False, True)
        ]
        assert runs[0][:3] == runs[1][:3], trial
        ended, _, _, w = runs[0]
        starts = {e.from_node: e for e in w.of(AgvMoveStarted)}
        for node, _, level in ended[:-1]:
            if node in starts and starts[node].motion["v0"] != 0.0:
                continue  # passed at speed
            critical = level / capacity <= kind_kw["threshold"] * (1 + 1e-9) + 1e-9
            assert critical or level < length * (1 + 1e-9), (trial, node, level)
