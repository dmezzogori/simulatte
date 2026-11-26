"""A vehicle's traffic wait and route as entity state (studio map spec §6.2).

``wait`` is set by ``traffic.wait_started`` with that event's values and cleared by ``traffic.wait_ended``, whatever
its reason; ``route`` is set by ``agv.routed`` and cleared by the event that ends the travel: ``agv.move_ended`` at the
route's last node, ``agv.move_interrupted``, otherwise the vehicle's next ``agv.state_changed``. Every world here runs
:class:`~tests.intralogistics.traffic_support.FullReplay`, which compares the replayed deltas with the live entities
after every event; :class:`Watch` reads the two fields after each event that names a vehicle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvRouted,
    AgvStateChanged,
    TrafficWaitEnded,
    TrafficWaitStarted,
)
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import GiveUp, Reroute, StepAside
from simulatte.intralogistics.travel import TravelOutcome
from simulatte.trace import ChunkLimits, Trace, TraceRecorder
from tests.intralogistics.test_stop_requests import charger_world, detour_world, weak
from tests.intralogistics.test_travel import Scripted, diamond, stalls_after_its_turn
from tests.intralogistics.traffic_support import StillFleet, build, declare, line, t_layout, vehicle, world
from tests.intralogistics.traffic_support import TravelWorld as World

WAITED = {"blockers": ["L"], "node": "N1", "resources": ["N0~N1", "N1"], "since": 0.0}
"""F's wait on the line when L, 2 m long, rests on N1."""


def plain(value: Any) -> Any:
    """`value` with maps as dicts and tuples as lists."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


class Watch:
    """The ``wait`` and ``route`` of the vehicle an event names, read from the live entities after the event."""

    def __init__(self, w: World) -> None:
        self.w = w
        self.seen: list[tuple[DomainEvent, Any, Any]] = []
        w.env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        agv = getattr(event, "agv", None)
        state = self.w.env.entities.snapshot().get(agv) if isinstance(agv, str) else None
        if state is not None and state["$kind"] == "agv":
            self.seen.append((event, plain(state["wait"]), plain(state["route"])))

    def of(self, agv: str, *types: type[DomainEvent]) -> list[tuple[Any, Any, Any]]:
        return [entry for entry in self.seen if entry[0].agv == agv and (not types or isinstance(entry[0], types))]

    def check(self) -> None:
        """``wait`` holds each announcement's values, ``since`` its time, until its ``traffic.wait_ended``; ``route``
        is each ``agv.routed``'s route; no other event sets either field, and only the events that end a travel set
        ``route`` to null."""
        for event, wait, route in self.seen:
            if isinstance(event, TrafficWaitStarted):
                assert wait == {
                    "blockers": list(event.blockers),
                    "node": event.node,
                    "resources": list(event.resources),
                    "since": event.t,
                }
            elif isinstance(event, TrafficWaitEnded):
                assert wait is None
            elif isinstance(event, AgvRouted):
                assert route == {"destination": event.destination, "nodes": list(event.nodes), "reason": event.reason}
        for event in self.w.events:
            for op in event.deltas.ops:
                if op[0] == "set" and op[2] == "wait":
                    assert isinstance(event, TrafficWaitStarted if op[3] is not None else TrafficWaitEnded), event
                elif op[0] == "set" and op[2] == "route":
                    ends = (AgvMoveEnded, AgvMoveInterrupted, AgvStateChanged)
                    assert isinstance(event, AgvRouted if op[3] is not None else ends), event


def cleared_by_the_next_state_change(watch: Watch, agv: str, state: str) -> None:
    """One ``agv.state_changed`` of `agv`, to `state`, clears the route with its deltas; the route was set before it."""
    seen = watch.of(agv)
    clearing = [
        i
        for i, (event, _, _) in enumerate(seen)
        if isinstance(event, AgvStateChanged) and ("set", agv, "route", None) in event.deltas.ops
    ]
    assert len(clearing) == 1
    event, _, route = seen[clearing[0]]
    assert route is None and event.state == state
    assert seen[clearing[0] - 1][2] is not None
    assert watch.w.agvs[agv].route is None


# --- idle vehicles -------------------------------------------------------------------------------------------------


def test_an_idle_vehicle_has_neither_a_wait_nor_a_route() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0"})
    agv = w.agvs["A"]
    assert AGV.state_schema["wait"].nullable and AGV.state_schema["route"].nullable
    assert agv.wait is None and agv.route is None
    assert (agv.snapshot()["wait"], agv.snapshot()["route"]) == (None, None)
    initial = w.env.initial_state
    assert initial is not None and (initial["A"]["wait"], initial["A"]["route"]) == (None, None)


# --- waits ---------------------------------------------------------------------------------------------------------


def test_a_wait_holds_its_announcement_until_it_is_granted() -> None:
    w = world(lambda env: line(env, 4), {"L": "N1", "F": "N0"}, agv_type=vehicle(length=2.0))
    watch = Watch(w)
    w.go("F", "N2")
    w.go("L", "N3")
    w.env.run()
    ((started, wait, _),) = watch.of("F", TrafficWaitStarted)
    assert wait == WAITED
    assert [op[:3] for op in started.deltas.ops] == [("set", "F", "wait")]
    ((ended, wait, _),) = watch.of("F", TrafficWaitEnded)
    assert ended.reason == "granted" and wait is None
    assert ended.deltas.ops == (("set", "F", "wait", None),)
    assert w.agvs["F"].wait is None
    watch.check()


def test_since_is_the_time_of_the_announcement_and_the_attribute_holds_it_between_events() -> None:
    w = world(lambda env: line(env, 3), {"F": "N0", "L": "N1"})
    watch = Watch(w)
    w.go("F", "N2", at=7.5)
    w.env.run(until=20.0)
    ((_, wait, _),) = watch.of("F", TrafficWaitStarted)
    assert wait["since"] == 7.5
    assert plain(w.agvs["F"].wait) == wait
    watch.check()


def _granted(w: World) -> None:
    w.go("F", "N2")
    w.go("L", "N3")


def _interrupted(w: World) -> None:
    w.interrupt(w.go("F", "N2"), 5.0)


def _gave_up(w: World) -> None:
    w.go("F", "N2")


def _rerouted(w: World) -> None:
    w.controller._policy.blocked = (Reroute(frozenset({w.nodes["P"]})),)
    w.go("F", "E")


def _stepped_aside(w: World) -> None:
    w.go("B", "LINE_IN")
    w.go("F", "STORE_OUT")


ENDS: dict[str, tuple[Callable[[], World], Callable[[World], None]]] = {
    "granted": (lambda: world(lambda env: line(env, 4), {"L": "N1", "F": "N0"}), _granted),
    "interrupted": (lambda: world(lambda env: line(env, 3), {"F": "N0", "L": "N1"}), _interrupted),
    "gave_up": (
        lambda: world(lambda env: line(env, 3), {"F": "N0", "L": "N1"}, policy=Scripted(blocked=(GiveUp(),))),
        _gave_up,
    ),
    "rerouted": (
        lambda: world(
            diamond, {"F": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=Scripted()
        ),
        _rerouted,
    ),
    "stepped_aside": (
        lambda: world(t_layout, {"F": "C1", "B": "STORE_OUT"}, policy=Scripted(deadlock=("F", StepAside()))),
        _stepped_aside,
    ),
}


@pytest.mark.parametrize("reason", sorted(ENDS))
def test_every_end_of_a_wait_clears_it(reason: str) -> None:
    make, start = ENDS[reason]
    w = make()
    watch = Watch(w)
    start(w)
    w.env.run()
    (first, *_) = watch.of("F", TrafficWaitStarted)
    assert first[1] is not None
    ends = watch.of("F", TrafficWaitEnded)
    assert ends[0][0].reason == reason
    assert all(wait is None and event.deltas.ops == (("set", "F", "wait", None),) for event, wait, _ in ends)
    assert w.agvs["F"].wait is None
    watch.check()


def test_the_look_ahead_claim_a_stalled_run_withdraws_announced_no_wait() -> None:
    """Traffic spec §8.3: the claim a stalled run had asked for ahead is no wait; withdrawing it emits nothing and
    ``wait`` stays null. The stalled travel is under way: its route stays."""
    w, _ = stalls_after_its_turn({"A": "N0", "C": "N3"}, "N3")
    watch = Watch(w)
    w.env.run(until=1000.0)
    assert w.agvs["A"].motion["stalled"] and w.controller._queue == []
    assert w.of(TrafficWaitStarted) == [] and w.agvs["A"].wait is None
    assert plain(w.agvs["A"].route)["destination"] == "N3"
    watch.check()


def test_a_seek_inside_a_wait_reads_it_from_the_trace(tmp_path: Path) -> None:
    """The cursor lies in a chunk that starts after the wait's announcement: the reader gets the wait from the chunk's
    checkpoint, not by playing through ``traffic.wait_started``."""
    path = tmp_path / "wait.simtrace"
    with Environment(seed=3) as env:
        TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=2, max_bytes=1 << 30, max_latency_s=3600.0))
        declared = line(env, 4)
        agvs = {
            name: AGV(env=env, agv_type=vehicle(length=2.0), agv_id=name, initial_node=declared.ref(start))
            for name, start in (("L", "N1"), ("F", "N0"))
        }
        fleet = StillFleet(
            env=env,
            graph=declared.graph,
            fleet=list(agvs.values()),
            warehouses=[],
            charging_stations=[],
            traffic_controller=ReservationController(env=env, graph=declared.graph),
            default_metrics=False,
        )
        env.activate()
        nodes = {node.id: node for node in declared.canonical_graph().nodes}

        def go(name: str, goal: str, at: float) -> Any:
            yield env.timeout(at)
            agvs[name].transition_to(AGVState.TRAVELING_EMPTY)
            yield from fleet._executor.travel(agvs[name], nodes[goal], loaded=False)
            agvs[name].transition_to(AGVState.IDLE)

        env.process(go("F", "N2", 0.0))
        env.process(go("L", "N3", 50.0))
        env.run()
    trace = Trace.open(path)
    started = next(e for e in trace.events() if e.type == "traffic.wait_started")
    cursor = (51.0, 10**9)
    chunk = next(i for i in reversed(trace.index) if i.first <= cursor)
    assert chunk.first > (started.t, started.seq)
    state = trace.state_at(cursor)
    assert plain(state["F"]["wait"]) == WAITED
    assert plain(state["F"]["route"]) == {"destination": "N2", "nodes": ["N0", "N1", "N2"], "reason": "travel"}
    assert trace.state_at((60.0, 10**9))["F"]["wait"] is None


# --- routes --------------------------------------------------------------------------------------------------------


def test_the_route_is_set_by_agv_routed_and_cleared_on_arrival_at_its_last_node() -> None:
    w = world(t_layout, {"A": "STORE_OUT"}, reserve=False)
    watch = Watch(w)
    w.go("A", "LINE_IN")
    w.env.run()
    ((routed, _, route),) = watch.of("A", AgvRouted)
    assert route == {"destination": "LINE_IN", "nodes": ["STORE_OUT", "C1", "C2", "LINE_IN"], "reason": "travel"}
    assert [op[:3] for op in routed.deltas.ops] == [("set", "A", "route")]
    moves = watch.of("A", AgvMoveEnded)
    assert [entry[2] is None for entry in moves] == [False, False, True]
    assert moves[-1][0].deltas.ops[-1] == ("set", "A", "route", None)
    assert w.outcomes == {"A": TravelOutcome.ARRIVED} and w.agvs["A"].route is None
    watch.check()


def test_a_reroute_replaces_the_route() -> None:
    policy = Scripted()
    w = world(diamond, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    policy.blocked = (Reroute(frozenset({w.nodes["P"]})),)
    watch = Watch(w)
    w.go("A", "E")
    w.env.run()
    assert [route for _, _, route in watch.of("A", AgvRouted)] == [
        {"destination": "E", "nodes": ["S", "P", "E"], "reason": "travel"},
        {"destination": "E", "nodes": ["S", "Q", "E"], "reason": "blocked"},
    ]
    assert w.outcomes == {"A": TravelOutcome.ARRIVED} and w.agvs["A"].route is None
    watch.check()


def test_a_step_aside_route_ends_at_its_target_and_the_travel_routes_again() -> None:
    w = world(t_layout, {"A": "C1", "B": "STORE_OUT"}, policy=Scripted(deadlock=("A", StepAside())))
    watch = Watch(w)
    w.go("B", "LINE_IN")
    w.go("A", "STORE_OUT")
    w.env.run()
    assert [route["reason"] for _, _, route in watch.of("A", AgvRouted)] == ["travel", "step_aside", "travel"]
    park = next(entry for entry in watch.of("A", AgvMoveEnded) if entry[0].node == "PARK")
    assert park[2] is None  # the step aside's route ends at its target
    assert w.outcomes["A"] is TravelOutcome.ARRIVED and w.agvs["A"].route is None
    watch.check()


def test_no_route_back_after_a_step_aside_leaves_the_route_null() -> None:
    """The step aside to D ends at its target, which clears its route; from D, a dead end, there is no route back."""
    policy = Scripted()
    nodes = {name: Node(name, x, y) for name, x, y in [("C1", 10, 0), ("S", 0, 0), ("D", 10, 10), ("T", 20, 0)]}
    graph = LayoutGraph(
        nodes.values(),
        [Arc(nodes["S"], nodes["C1"]), Arc(nodes["C1"], nodes["T"]), Arc(nodes["C1"], nodes["D"], bidirectional=False)],
    )
    w = world(
        lambda env: declare(env, graph),
        {"A": "C1", "B": "S"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
    )
    policy.deadlock = ("A", StepAside(w.nodes["D"]))
    watch = Watch(w)
    w.go("B", "T")
    w.go("A", "S")
    w.env.run()
    assert w.outcomes == {"B": TravelOutcome.ARRIVED, "A": TravelOutcome.NO_ROUTE}
    assert [route["reason"] for _, _, route in watch.of("A", AgvRouted)] == ["travel", "step_aside"]
    assert watch.of("A", AgvMoveEnded)[-1][2] is None and w.agvs["A"].route is None
    watch.check()


def test_an_interrupt_clears_the_route_with_agv_move_interrupted() -> None:
    w = world(lambda env: line(env, 4), {"A": "N0"})
    watch = Watch(w)
    w.interrupt(w.go("A", "N3"), 3.0)
    w.env.run()
    ((interrupted, _, route),) = watch.of("A", AgvMoveInterrupted)
    assert interrupted.deltas.ops == (("set", "A", "route", None),) and route is None
    assert watch.of("A", AgvMoveEnded)[-1][2] is not None  # N2 is not the route's last node
    watch.check()


def test_an_interrupt_while_waiting_clears_both_fields() -> None:
    w = world(lambda env: line(env, 3), {"F": "N0", "L": "N1"})
    watch = Watch(w)
    w.interrupt(w.go("F", "N2"), 5.0)
    w.env.run()
    assert [entry[1:] for entry in watch.of("F", TrafficWaitEnded, AgvMoveInterrupted)] == [
        (None, {"destination": "N2", "nodes": ["N0", "N1", "N2"], "reason": "travel"}),
        (None, None),
    ]
    watch.check()


@pytest.mark.parametrize("reserve", [False, True])
def test_a_retry_from_the_current_position_clears_the_route_at_the_next_state_change(reserve: bool) -> None:
    kind = weak(lambda distance, load, speed: distance * (1.0 if speed >= 1.4 else 5.0), threshold=0.96)
    w = world(lambda env: line(env, 6), {"A": "N0"}, reserve=reserve, agv_type=kind, lookahead_margin=25.0)
    watch = Watch(w)
    w.go("A", "N5")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    cleared_by_the_next_state_change(watch, "A", "IDLE")
    watch.check()


def test_a_stranding_clears_the_route_at_the_turn() -> None:
    w = charger_world(25.0, None)
    watch = Watch(w)
    w.go("A", "N4")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    cleared_by_the_next_state_change(watch, "A", "STRANDED")
    watch.check()


def test_the_turn_to_a_charger_clears_the_route_of_a_trip_that_is_not_resumed() -> None:
    """Ruling A3-m: a trip without an order that turns to charge is not resumed; its route goes at the turn, with the
    state change to ``CHARGING`` (the charger is on the node where the energy ran short)."""
    w = charger_world(25.0, "N2")
    watch = Watch(w)
    w.go("A", "N4")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
    cleared_by_the_next_state_change(watch, "A", "CHARGING")
    watch.check()


@pytest.mark.parametrize("reserve", [False, True])
def test_a_diversion_to_a_charger_elsewhere_has_its_own_route(reserve: bool) -> None:
    """S to G costs 30 against a battery of 25: the travel turns at S; the state change of the diversion clears its
    route, the diversion's ``agv.routed`` sets its own, which ends at the charger; then the AGV strands there."""
    w = charger_world(25.0, "C", reserve=reserve, shape=detour_world, start="S")
    watch = Watch(w)
    w.go("A", "G")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}  # charged, a full battery still falls short of 30
    assert [route["destination"] for _, _, route in watch.of("A", AgvRouted)] == ["G", "C"]
    cleared_by_the_next_state_change(watch, "A", "TRAVELING_EMPTY")
    assert watch.of("A", AgvMoveEnded)[-1][2] is None and w.agvs["A"].route is None
    watch.check()


def test_giving_up_clears_the_route_at_the_next_state_change() -> None:
    w = world(lambda env: line(env, 3), {"F": "N0", "L": "N1"}, policy=Scripted(blocked=(GiveUp(),)))
    watch = Watch(w)
    w.go("F", "N2")
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.GAVE_UP}
    cleared_by_the_next_state_change(watch, "F", "IDLE")
    watch.check()


def test_no_route_leaves_the_route_null() -> None:
    w = world(lambda env: build(env, [("A", 0, 0), ("B", 10, 0)], []), {"V": "A"}, reserve=False)
    watch = Watch(w)
    w.go("V", "B")
    w.env.run()
    assert w.outcomes == {"V": TravelOutcome.NO_ROUTE}
    assert all(route is None for _, _, route in watch.of("V"))
    watch.check()


def test_free_flow_announces_no_wait_and_records_routes() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0", "B": "N2"}, reserve=False)
    watch = Watch(w)
    w.go("A", "N2")
    w.go("B", "N0")
    w.env.run()
    assert all(wait is None for _, wait, _ in watch.seen)
    assert {route["destination"] for _, _, route in watch.seen if route is not None} == {"N0", "N2"}
    assert w.agvs["A"].route is None and w.agvs["B"].route is None
    watch.check()
