"""Capacities and traffic counters as entity state (studio map spec §6.4, §6.6).

The constants ``parking_area.capacity``, ``charging_station.slots`` and ``agv.battery_capacity`` are written once, in
the initial state, and never in a delta. The ``fleet`` counters ``waits``, ``stuck_waits`` and ``deadlocks`` start at 0
and are set, one more each time, by a delta of ``traffic.wait_started``, ``traffic.wait_stuck`` and
``traffic.deadlock``: a wait and a stuck wait count for the fleet of their vehicle, a deadlock once for each distinct
fleet with a vehicle in it, and a vehicle without a fleet counts nowhere. :class:`Tally` checks every counter delta of a
run against that rule; every world here also runs a full replay, which compares the replayed deltas with the live
entities after every event.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import TrafficDeadlock, TrafficWaitStarted, TrafficWaitStuck
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import KeepWaiting
from tests.intralogistics.random_worlds import build_world, world_spec
from tests.intralogistics.test_deadlock import Scripted
from tests.intralogistics.test_deadlock import world as stub_world
from tests.intralogistics.traffic_support import (
    FullReplay,
    StillFleet,
    TrafficInvariants,
    line,
    t_layout,
    vehicle,
    world,
)
from tests.intralogistics.traffic_support import World as Declared

CONSTANTS = {"agv": "battery_capacity", "parking_area": "capacity", "charging_station": "slots"}
COUNTERS = {TrafficWaitStarted: "waits", TrafficWaitStuck: "stuck_waits", TrafficDeadlock: "deadlocks"}


class Tally:
    """Checks, after every event, that only the three traffic events set a fleet counter, each by one, on exactly the
    distinct fleets of its vehicles, and that no delta writes a constant; counts the events per fleet."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.counted: dict[str, Counter[str]] = {counter: Counter() for counter in COUNTERS.values()}
        self.events: list[DomainEvent] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        self.events.append(event)
        state = self.env.entities.snapshot()
        counter = COUNTERS.get(type(event))
        for op in event.deltas.ops:
            if op[0] in {"create", "retire"}:  # a create's initial state holds the constants and zero counters
                continue
            kind = state.get(op[1], {}).get("$kind")
            assert op[2] != CONSTANTS.get(kind), event
            if kind == "fleet" and op[2] in self.counted:
                assert op[2] == counter, event
        if counter is None:
            return
        agvs = event.agvs if isinstance(event, TrafficDeadlock) else (event.agv,)
        fleets = list(dict.fromkeys(state[agv]["fleet"] for agv in agvs if state[agv]["fleet"] is not None))
        for fleet in fleets:
            self.counted[counter][fleet] += 1
        expected = [("set", fleet, counter, self.counted[counter][fleet]) for fleet in fleets]
        assert [op for op in event.deltas.ops if op[2] == counter] == expected, event
        for fleet in fleets:
            assert state[fleet][counter] == self.counted[counter][fleet]

    def of(self, *types: type[DomainEvent]) -> list[Any]:
        return [event for event in self.events if isinstance(event, types)]

    def counters(self, fleet: str) -> tuple[int, int, int]:
        """``(waits, stuck_waits, deadlocks)`` of `fleet` in the live state."""
        fields = self.env.entities.snapshot()[fleet]
        return fields["waits"], fields["stuck_waits"], fields["deadlocks"]


# --- the constants -------------------------------------------------------------------------------------------------


def facilities_world() -> tuple[Environment, FleetCoordinator]:
    """A line of three nodes with a parking area of 2 on N0, a charging station of 3 slots on N2 and an AGV whose type
    declares the integer battery capacity 500."""
    env = Environment(debug=True)
    FullReplay(env)
    declared = line(env, 3)
    area = ParkingArea(env=env, name="Lot", node=declared.ref("N0"), capacity=2)
    station = ChargingStation(env=env, name="Charger", node=declared.ref("N2"), n_slots=3)
    agv = AGV(env=env, agv_type=vehicle(length=1.0, battery=500), agv_id="A", initial_node=declared.ref("N1"))
    fleet = StillFleet(
        env=env,
        graph=declared.graph,
        fleet=[agv],
        warehouses=[],
        charging_stations=[station],
        parking_areas=[area],
        default_metrics=False,
        name="fleet",
    )
    env.activate()
    return env, fleet


def test_the_constants_and_the_counters_are_in_the_schemas_the_snapshots_and_the_initial_state() -> None:
    env, _ = facilities_world()
    classes = {"agv": AGV, "parking_area": ParkingArea, "charging_station": ChargingStation}
    for kind, field in CONSTANTS.items():
        spec = classes[kind].state_schema[field]
        assert (spec.wire_type, spec.nullable) == ("float" if kind == "agv" else "int", False)
    for counter in COUNTERS.values():
        spec = FleetCoordinator.state_schema[counter]
        assert (spec.wire_type, spec.nullable) == ("int", False)
    initial = env.initial_state
    assert initial is not None
    assert initial["Lot"]["capacity"] == 2 and type(initial["Lot"]["capacity"]) is int
    assert initial["Charger"]["slots"] == 3 and type(initial["Charger"]["slots"]) is int
    assert initial["A"]["battery_capacity"] == 500.0 and type(initial["A"]["battery_capacity"]) is float
    assert (initial["fleet"]["waits"], initial["fleet"]["stuck_waits"], initial["fleet"]["deadlocks"]) == (0, 0, 0)
    live = env.entities.snapshot()
    assert (live["Lot"]["capacity"], live["Charger"]["slots"], live["A"]["battery_capacity"]) == (2, 3, 500.0)


@pytest.mark.parametrize("seed", [182, 272])
def test_a_random_world_writes_no_constant_in_a_delta_and_counts_every_traffic_event(seed: int) -> None:
    """182 has two fleets, two chargers and two parking areas; 272 one fleet and one charger. Both have waits, stuck
    waits and deadlocks. The constants of the initial state are those of the objects; the final counters are the events
    counted per fleet."""
    spec = world_spec(seed)
    env = Environment(seed=spec.seed, log_level="CRITICAL")
    built = build_world(env, spec)
    FullReplay(env, from_snapshot=True)
    tally = Tally(env)
    env.run()
    initial = env.initial_state
    assert initial is not None
    for agv in built.agvs.values():
        assert initial[agv.id]["battery_capacity"] == agv.agv_type.battery_capacity
    for station in built.chargers:
        assert initial[station.id]["slots"] == station.n_slots
    for area in built.parking:
        assert initial[area.id]["capacity"] == area.capacity
    assert (len(built.fleets), len(built.chargers), len(built.parking)) == ((2, 2, 2) if seed == 182 else (1, 1, 0))
    for fleet in built.fleets:
        assert initial[fleet.id]["waits"] == initial[fleet.id]["stuck_waits"] == initial[fleet.id]["deadlocks"] == 0
        assert tally.counters(fleet.id) == tuple(tally.counted[counter][fleet.id] for counter in COUNTERS.values())
    assert tally.of(TrafficWaitStarted) and tally.of(TrafficWaitStuck) and tally.of(TrafficDeadlock)


# --- the counters --------------------------------------------------------------------------------------------------


def test_a_wait_counts_for_the_fleet_of_its_vehicle() -> None:
    w = world(lambda env: line(env, 4), {"L": "N1", "F": "N0"}, agv_type=vehicle(length=2.0))
    tally = Tally(w.env)
    w.go("F", "N2")
    w.go("L", "N3")
    w.env.run()
    fleet = w.fleet.id
    ((started),) = tally.of(TrafficWaitStarted)
    assert [op[:3] for op in started.deltas.ops] == [("set", "F", "wait"), ("set", fleet, "waits")]
    assert started.deltas.ops[1] == ("set", fleet, "waits", 1)
    assert tally.counters(fleet) == (1, 0, 0)


def test_a_stuck_wait_counts_for_the_fleet_of_its_vehicle() -> None:
    """L rests on N1 and its fleet does not move it: F's wait is reported stuck at its first review."""
    w = world(lambda env: line(env, 4), {"L": "N1", "F": "N0"}, agv_type=vehicle(length=2.0))
    tally = Tally(w.env)
    w.go("F", "N2")
    w.env.run(until=100.0)
    ((stuck),) = tally.of(TrafficWaitStuck)
    assert stuck.deltas.ops == (("set", w.fleet.id, "stuck_waits", 1),)
    assert tally.counters(w.fleet.id) == (1, 1, 0)


def _unresolved(env: Environment) -> Declared:
    return line(env, 2)


SWAPS: dict[str, tuple[Callable[[Environment], Declared], dict[str, str], dict[str, str], str]] = {
    # A and B swap the two nodes of a line: nothing can move out of the way
    "unresolved": (_unresolved, {"A": "N0", "B": "N1"}, {"A": "N1", "B": "N0"}, "unresolved"),
    # A and B cross on the T layout: B steps aside into PARK
    "resolved": (t_layout, {"A": "STORE_OUT", "B": "LINE_IN"}, {"A": "LINE_IN", "B": "STORE_OUT"}, "step_aside"),
}


@pytest.mark.parametrize("case", list(SWAPS))
def test_a_deadlock_of_two_vehicles_of_one_fleet_counts_once(case: str) -> None:
    shape, starts, goals, action = SWAPS[case]
    w = world(shape, starts, agv_type=vehicle(length=2.0))
    tally = Tally(w.env)
    for agv, goal in goals.items():
        w.go(agv, goal)
    w.env.run(until=500.0)
    deadlock, *_ = tally.of(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.action) == (("A", "B"), action)
    assert deadlock.deltas.ops == (("set", w.fleet.id, "deadlocks", 1),)
    assert tally.counters(w.fleet.id)[2] == len(tally.of(TrafficDeadlock)) == 1


def two_fleets(
    shape: Callable[[Environment], Declared], starts: dict[str, str]
) -> tuple[Environment, dict[str, AGV], dict[str, FleetCoordinator], dict[str, Any]]:
    """`shape` with one ``ReservationController`` shared by the fleets ``one`` (the first AGV of `starts`) and
    ``two`` (the others), whose idle AGVs never make way."""
    env = Environment(debug=True)
    FullReplay(env)
    declared = shape(env)
    controller = ReservationController(env=env, graph=declared.graph)
    agvs = {
        name: AGV(env=env, agv_type=vehicle(length=2.0), agv_id=name, initial_node=declared.ref(start))
        for name, start in starts.items()
    }
    first, *others = agvs.values()
    fleets = {
        name: StillFleet(
            env=env,
            graph=declared.graph,
            fleet=members,
            warehouses=[],
            charging_stations=[],
            traffic_controller=controller,
            default_metrics=False,
            name=name,
        )
        for name, members in (("one", [first]), ("two", others))
    }
    TrafficInvariants(env, controller)
    env.activate()
    nodes = {node.id: node for node in declared.canonical_graph().nodes}
    return env, agvs, fleets, nodes


def go(env: Environment, fleet: FleetCoordinator, agv: AGV, goal: Any) -> None:
    def mission() -> Any:
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        yield from fleet._executor.travel(agv, goal, loaded=False)
        agv.transition_to(AGVState.IDLE)

    env.process(mission())


@pytest.mark.parametrize("case", list(SWAPS))
def test_a_deadlock_of_vehicles_of_two_fleets_counts_once_in_each(case: str) -> None:
    shape, starts, goals, action = SWAPS[case]
    env, agvs, fleets, nodes = two_fleets(shape, starts)
    tally = Tally(env)
    go(env, fleets["one"], agvs["A"], nodes[goals["A"]])
    go(env, fleets["two"], agvs["B"], nodes[goals["B"]])
    env.run(until=500.0)
    deadlock, *_ = tally.of(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.action) == (("A", "B"), action)
    assert deadlock.deltas.ops == (("set", "one", "deadlocks", 1), ("set", "two", "deadlocks", 1))
    assert tally.counters("one")[2] == tally.counters("two")[2] == 1


def test_three_vehicles_of_two_fleets_in_a_deadlock_count_once_in_each() -> None:
    """A three-vehicle cycle on the controller's own stub fleets: A and C belong to F1, B to F2."""
    w = stub_world({"A": ("N0", "F1"), "B": ("N1", "F2"), "C": ("N2", "F1")}, policy=Scripted())
    tally = Tally(w.env)
    w.wait("A", "N1")
    w.wait("B", "N2")
    w.wait("C", "N0")
    (deadlock,) = tally.of(TrafficDeadlock)
    assert deadlock.agvs == ("A", "B", "C")
    assert deadlock.deltas.ops == (("set", "F1", "deadlocks", 1), ("set", "F2", "deadlocks", 1))
    assert (tally.counters("F1"), tally.counters("F2")) == ((2, 0, 1), (1, 0, 1))


def test_a_vehicle_without_a_fleet_counts_nowhere() -> None:
    """A controller can be driven for a vehicle that no fleet took (its ``fleet`` is None): its wait, its stuck wait and
    its share of a deadlock carry no counter delta. B has no fleet; A belongs to F1."""
    w = stub_world({"A": ("N1", "F1"), "B": "N0", "C": "N3"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
    assert w.agvs["B"].fleet is None
    tally = Tally(w.env)
    w.fleets["F1"].accept = False
    w.wait("B", "N1")
    w.env.run(until=40.0)
    (started,) = tally.of(TrafficWaitStarted)
    assert [op[:3] for op in started.deltas.ops] == [("set", "B", "wait")]
    (stuck,) = tally.of(TrafficWaitStuck)
    assert stuck.agv == "B" and stuck.deltas.ops == ()
    w.wait("A", "N0")  # A waits for B's node while B waits for A's: a deadlock of A (F1) and B (no fleet)
    deadlock, *_ = tally.of(TrafficDeadlock)
    assert deadlock.agvs == ("A", "B")
    assert deadlock.deltas.ops == (("set", "F1", "deadlocks", 1),)
    assert tally.counters("F1") == (1, 0, 1)


# --- kept whether or not anything subscribes -----------------------------------------------------------------------


def random_run(seed: int, *, events: list[DomainEvent] | None = None, until: float | None = None) -> Environment:
    """Random world `seed`; with `events`, a ``"*"`` subscriber appends every event to it, otherwise nothing
    subscribes. The run stops at `until`."""
    spec = world_spec(seed)
    env = Environment(seed=spec.seed, log_level="CRITICAL")
    if events is not None:
        env.bus.subscribe(events.append, "*")
    build_world(env, spec)
    assert env.wants(TrafficWaitStarted) is (events is not None)
    env.run(until=until)
    return env


def test_the_counters_are_kept_without_subscribers() -> None:
    """Random world 272, with no subscriber and with a ``"*"`` subscriber, stopped one second after its first deadlock
    (a resolved one) and run to its end: every entity's snapshot is the same at both instants. At the end the run has
    counted waits, stuck waits, resolved and unresolved deadlocks, so each emit site that counts is reached."""
    events: list[DomainEvent] = []
    end = random_run(272, events=events).now
    deadlocks = [event for event in events if isinstance(event, TrafficDeadlock)]
    assert {event.action == "unresolved" for event in deadlocks} == {True, False}
    at = deadlocks[0].t + 1.0
    assert at < end
    for until in (at, None):
        bare, observed = random_run(272, until=until), random_run(272, events=[], until=until)
        snapshot = bare.entities.snapshot()
        assert snapshot == observed.entities.snapshot()
        (fleet,) = [fields for fields in snapshot.values() if fields["$kind"] == "fleet"]
        assert fleet["waits"] > 0 and fleet["deadlocks"] > 0
    assert (fleet["waits"], fleet["stuck_waits"], fleet["deadlocks"]) == (22, 4, 5)


def test_the_facility_constants_keep_their_constructed_values() -> None:
    """A later change of the SimPy resources' capacity does not reach the state: the snapshot holds the value the
    initial state recorded."""
    env, fleet = facilities_world()
    area, station = fleet.parking_areas[0], fleet.charging_stations[0]
    area._resource._capacity = 5
    station._slots._capacity = 7
    assert area.snapshot()["capacity"] == 2
    assert station.snapshot()["slots"] == 3
