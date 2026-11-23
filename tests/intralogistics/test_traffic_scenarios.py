"""Acceptance scenarios of traffic control, part A (spec §17 and Appendix A).

Each test runs a complete world through ``FleetCoordinator`` with real orders under ``ReservationController`` and
asserts what the spec says about it. Every world runs :class:`FullReplay` (replayed deltas equal the live state of every
entity after every event) and :class:`TrafficInvariants` (node, segment, zone and corridor holdings, spec §17) and
``assert_ran`` checks that the invariants were evaluated. What the unit tests of ``test_travel``, ``test_deadlock``,
``test_idle_traffic``, ``test_traffic_termination`` and ``tests/layout/test_traffic_fields`` already pin is not
repeated; the acceptance record ``specs/research/2026-11-21-agv-traffic-acceptance.md`` names the test that covers
each item of §17.
"""

from __future__ import annotations

import contextlib
import inspect
import math
import os
import re
import runpy
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import simulatte.environment
import simulatte.intralogistics as intralogistics
import simulatte.intralogistics.builders as builders
from simulatte import Layout
from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FleetCoordinator,
    FreeFlowController,
    LayoutGraph,
    Node,
    OrderStatus,
    ReservationController,
    SKU,
    TrapezoidalProfile,
    Warehouse,
    build_simple_system,
)
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveStarted,
    AgvRouted,
    OrderAssigned,
    OrderStatusChanged,
    TrafficDeadlock,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.reservation import SegmentBinding, ZoneBinding
from simulatte.intralogistics.travel import TravelOutcome
from simulatte.trace import Trace, TraceRecorder
from tests.intralogistics.test_idle_traffic import SKU_A, Mission, mission_world
from tests.intralogistics.test_travel import steady, vehicle, world
from tests.intralogistics.test_travel_motion import straight
from tests.intralogistics.traffic_support import (
    FullReplay,
    TrafficInvariants,
    build,
    declare,
    loop_with_side_node,
    t_layout,
    two_junction_corridor,
)
from tests.intralogistics.traffic_support import World as Declared

ROOT = Path(__file__).resolve().parents[2]


# --- support -----------------------------------------------------------------------------------------------------


class Spots:
    """Where each AGV physically stood when each of its waits started: ``(event, node id)`` in order."""

    def __init__(self, m: Mission) -> None:
        self.m = m
        self.seen: list[tuple[TrafficWaitStarted, str]] = []
        m.env.bus.subscribe(self, (TrafficWaitStarted,))

    def __call__(self, event: TrafficWaitStarted) -> None:
        node = self.m.agvs[event.agv].current_node
        assert node is not None
        self.seen.append((event, node.id))

    def first(self, agv: str) -> tuple[TrafficWaitStarted, str]:
        return next(item for item in self.seen if item[0].agv == agv)


def scenario(*args: Any, **kwargs: Any) -> tuple[Mission, TrafficInvariants]:
    """``mission_world`` plus a checked :class:`TrafficInvariants` of this file's own."""
    m = mission_world(*args, **kwargs)
    return m, TrafficInvariants(m.env, m.controller)


def assert_ran(m: Mission, invariants: TrafficInvariants, *, orders: int) -> None:
    """The run finished by itself with `orders` orders, all completed; nothing failed, deadlocked for good or stuck."""
    assert invariants.checked > 0 and m.replay.events
    assert len(m.orders) == orders
    assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * orders
    assert not m.events(TrafficWaitStuck)
    assert all(e.action != "unresolved" for e in m.events(TrafficDeadlock))


def departures(m: Mission, agv: str, *, until: float = float("inf")) -> dict[str, float]:
    """The time at which `agv` started an arc from each node, for the nodes it left once, before `until`."""
    started = [e for e in m.events(AgvMoveStarted, agv=agv) if e.t < until]
    assert len({e.from_node for e in started}) == len(started)
    return {e.from_node: float(e.t) for e in started}


def timeline(replay: FullReplay) -> list[tuple[str, float, str]]:
    """The arrivals and the order transitions of a run, as ``(kind, time, what)``."""
    arrivals = [("arrived", float(e.t), f"{e.agv}@{e.node}") for e in replay.of(AgvMoveEnded)]
    statuses = [("order", float(e.t), f"{e.order}:{e.status}") for e in replay.of(OrderStatusChanged)]
    return sorted(arrivals + statuses, key=lambda item: (item[1], item[0], item[2]))


# --- spec Appendix A ---------------------------------------------------------------------------------------------


def appendix_a(env: Environment, *, observed: bool = True, **type_options: Any) -> tuple[Mission, TrafficInvariants]:
    """The graph and fleet of Appendix A under ``ReservationController`` with ``length=0.0`` and its three orders, none
    submitted yet (see :func:`run_appendix_a`). The replay and the invariants are attached unless `observed` is false,
    in which case ``Mission.replay`` and the invariants are ``None`` and the world subscribes to nothing. `type_options`
    replace or add ``AGVType`` fields (the appendix has none)."""
    declared = t_layout(env)
    controller = ReservationController(env=env, graph=declared.graph)
    sku = SKU(id="A", weight=5.0, volume=0.2)
    storage = Warehouse(
        env=env,
        name="Storage",
        input_bays=[declared.ref("STORE_OUT")],
        output_bays=[declared.ref("STORE_OUT")],
        n_slots=3,
        products=[sku],
        initial_inventory={sku: 50},
        pick_time=5.0,
        put_time=3.0,
    )
    line = Warehouse(
        env=env,
        name="Line",
        input_bays=[declared.ref("LINE_IN")],
        output_bays=[declared.ref("LINE_IN")],
        n_slots=3,
        products=[sku],
        initial_inventory={},
        pick_time=2.0,
        put_time=2.0,
    )
    kind = AGVType(
        **{
            "name": "std",
            "speed_profile": TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=0.8),
            "battery_capacity": 1000.0,
            "weight_capacity": 50.0,
            "volume_capacity": 2.0,
            "load_time": 5.0,
            "unload_time": 5.0,
            "length": 0.0,
            **type_options,
        }
    )
    vehicles = [
        AGV(env=env, agv_type=kind, initial_node=declared.ref(start)) for start in ("C1", "PARK")
    ]  # agv-0, agv-1
    agvs = {agv.id: agv for agv in vehicles}
    fleet = FleetCoordinator(
        env=env,
        graph=declared.graph,
        fleet=vehicles,
        warehouses=[storage, line],
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
    )
    replay = FullReplay(env, from_snapshot=True) if observed else cast(FullReplay, None)
    invariants = TrafficInvariants(env, controller) if observed else cast(TrafficInvariants, None)
    orders = [fleet.create_order(sku=sku, quantity=2, origin=storage, destination=line) for _ in range(3)]
    nodes = {node.id: node for node in declared.canonical_graph().nodes}  # prepares the layout, after the orders
    m = Mission(env, fleet, nodes, agvs, {"Storage": storage, "Line": line}, {}, {}, replay)
    m.orders = orders
    return m, invariants


def run_appendix_a(number: int, m: Mission) -> None:
    """Submit the orders of scenario `number` as the appendix says and run to its horizon: scenario 1 submits all three
    before activation and runs until 400, scenario 2 submits at 0, 9 and 1509 and runs until 3000."""
    first, second, third = m.orders
    if number == 1:
        for order in m.orders:
            m.fleet.submit(order)
        m.env.run(until=400.0)
    else:
        m.at(0.0, lambda: m.fleet.submit(first))
        m.at(9.0, lambda: m.fleet.submit(second))
        m.at(1509.0, lambda: m.fleet.submit(third))
        m.env.run(until=3000.0)


def test_appendix_a_scenario_1_completes_every_order() -> None:
    """Baseline: ``order-0`` completes at 51.52 and the other two fail at 0. Here the second AGV waits for the first
    and, when they meet head-on on the dead-end branch, the standard policy steps one aside."""
    with Environment(debug=True) as env:
        m, invariants = appendix_a(env)
        run_appendix_a(1, m)
        assert invariants.checked > 0
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        assert not [e for e in m.events(OrderStatusChanged) if e.status == "FAILED"]
        assert not m.events(TrafficWaitStuck)
        assert m.orders[0].delivered_at == pytest.approx(47.61, abs=0.005)  # 51.52 when every arc ended at rest
        assert [(e.agvs, e.victim, e.action) for e in m.events(TrafficDeadlock)] == [
            (("agv-0", "agv-1"), "agv-0", "step_aside")
        ]


def test_appendix_a_scenario_2_completes_every_order_and_the_idle_agv_makes_way_to_park() -> None:
    with Environment(debug=True) as env:
        m, invariants = appendix_a(env)
        spots = Spots(m)
        run_appendix_a(2, m)
        assert invariants.checked > 0
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        assert not [e for e in m.events(OrderStatusChanged) if e.status == "FAILED"]
        assert not m.events(TrafficWaitStuck) and not m.events(TrafficDeadlock)

        # The later AGV waits at PARK under the dead-end rule while agv-0 picks, not at C1: it stands on PARK and asks
        # for the way out through C1.
        event, node = spots.first("agv-1")
        assert (float(event.t), node, event.node, event.blockers) == (9.0, "PARK", "C1", ("agv-0",))

        # Each idle AGV left at C2 after a delivery makes way to PARK when the other needs C2.
        made_way = [(e.agv, e.origin, e.destination) for e in m.events(AgvRouted) if e.reason == "make_way"]
        assert made_way == [("agv-0", "C2", "PARK"), ("agv-1", "C2", "PARK")]


# --- spec Appendix A with corner caps, turn waits and idle power (part B) -------------------------------------------

CORNER_CAP = 0.6
"""The speed cap of a corner (every turn above 0.1 rad) in the part B variant of Appendix A, in m/s."""

TURN_WAIT = 3.0
"""The wait of a reversal (a heading change above 2 rad) in the part B variant of Appendix A, in seconds."""

MOTION_OPTIONS: dict[str, Any] = {
    "speed_profile": TrapezoidalProfile(
        max_speed=1.5, acceleration=0.8, deceleration=0.8, battery_degradation_fn=lambda level: 1.0
    ),
    "corner_speed": lambda angle: CORNER_CAP if angle > 0.1 else math.inf,
    "turn_time": lambda angle: TURN_WAIT if angle > 2.0 else 0.0,
    "idle_power": 0.01,
}
"""The part B variant: the appendix profile without the battery slowing the vehicle (so that the speed through a node is
the hand-computable 1.5 m/s), right-angle corners at 0.6 m/s, a 3 s wait for a reversal and idle power."""


def heading_change(first: AgvMoveStarted, second: AgvMoveStarted, nodes: dict[str, Node]) -> float:
    """The heading change at the node where `first` ends and `second` starts, from the chords (spec §9.4)."""

    def heading(event: AgvMoveStarted) -> float:
        a, b = nodes[event.from_node], nodes[event.to_node]
        return math.atan2(b.y - a.y, b.x - a.x)

    delta = abs(heading(second) - heading(first)) % (2 * math.pi)
    return min(delta, 2 * math.pi - delta)


def node_events(m: Mission) -> list[tuple[AgvMoveStarted, AgvMoveStarted, float]]:
    """For every node an AGV left after arriving over an arc: the arc it came over, the arc it took, and the time it
    spent at the node (0 when it passed the node at speed)."""
    arrived: dict[str, AgvMoveEnded] = {}
    entered: dict[str, AgvMoveStarted] = {}
    found = []
    for event in m.replay.events:
        if isinstance(event, AgvMoveEnded):
            arrived[event.agv] = event
        elif isinstance(event, AgvMoveStarted):
            end, last = arrived.get(event.agv), entered.get(event.agv)
            if end is not None and last is not None and end.node == event.from_node == last.to_node:
                found.append((last, event, float(event.t) - float(end.t)))
            entered[event.agv] = event
    return found


@pytest.mark.parametrize("number", [1, 2])
def test_appendix_a_with_corner_caps_turn_waits_and_idle_power_completes_every_order(number: int) -> None:
    """Part B: through-node motion with a corner cap, a turn wait and idle power changes when things happen, not what
    the spec expects. Every order completes, none fails, no deadlock stays unresolved, no wait is stuck; the invariants
    and the replay hold. Hand-computed: an AGV that passes a node straight leaves it at 1.5 m/s at the instant it
    arrived, one that takes a right-angle corner without stopping leaves at 0.6 m/s, and a reversal waits at least
    3 s."""
    with Environment(debug=True) as env:
        m, invariants = appendix_a(env, **MOTION_OPTIONS)
        spots = Spots(m)
        run_appendix_a(number, m)
        assert invariants.checked > 0
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        assert not [e for e in m.events(OrderStatusChanged) if e.status == "FAILED"]
        assert not m.events(TrafficWaitStuck)
        assert all(e.action != "unresolved" for e in m.events(TrafficDeadlock))

        if number == 1:  # the second AGV waits for the first and, head-on on the dead-end branch, one steps aside
            assert [(e.agvs, e.action) for e in m.events(TrafficDeadlock)] == [(("agv-0", "agv-1"), "step_aside")]
        else:  # agv-1 waits at PARK under the dead-end rule while agv-0 picks; each idle AGV left at C2 makes way
            event, node = spots.first("agv-1")
            assert (float(event.t), node, event.node, event.blockers) == (9.0, "PARK", "C1", ("agv-0",))
            assert [(e.agv, e.origin, e.destination) for e in m.events(AgvRouted) if e.reason == "make_way"] == [
                ("agv-0", "C2", "PARK"),
                ("agv-1", "C2", "PARK"),
            ]

        straight_passes, corner_passes, reversals = [], [], []
        for before, after, spent in node_events(m):
            angle = heading_change(before, after, m.nodes)
            exit_speed = before.motion["v0"] + sum(duration * accel for duration, accel in before.motion["phases"])
            if spent == 0.0 and exit_speed > 1e-9:  # passed at speed
                assert angle < 2.0  # a reversal is never taken at speed
                (corner_passes if angle > 0.1 else straight_passes).append(after.motion["v0"])
            elif angle > 2.0:
                assert spent >= TURN_WAIT - 1e-9
                reversals.append(spent)
        assert straight_passes and corner_passes and reversals
        assert straight_passes == pytest.approx([1.5] * len(straight_passes), abs=1e-9)
        assert corner_passes == pytest.approx([CORNER_CAP] * len(corner_passes), abs=1e-9)

        if number == 1:
            # The PARK reversal of agv-0 costs exactly the turn wait. Scenario 2 does not discriminate: its reversals
            # sit on service stops and long traffic waits, which are longer than the turn wait anyway.
            assert any(abs(spent - TURN_WAIT) < 1e-9 for spent in reversals)

        # Idle power: the same world with idle_power 0 takes the same times (the estimate ignores idle power) and each
        # AGV ends with exactly idle_power times the time accounted so far less (lazily, up to its last event).
        delivered = [o.delivered_at for o in m.orders]
        levels = {agv_id: agv.battery.level for agv_id, agv in m.agvs.items()}
        accounted = {agv_id: float(agv._accrued_at) for agv_id, agv in m.agvs.items()}
    with Environment(debug=True) as env:
        free, _ = appendix_a(env, **{**MOTION_OPTIONS, "idle_power": 0.0})
        run_appendix_a(number, free)
        assert [o.delivered_at for o in free.orders] == delivered
        for agv_id, agv in free.agvs.items():
            assert accounted[agv_id] > 0.0
            assert levels[agv_id] == pytest.approx(
                agv.battery.level - MOTION_OPTIONS["idle_power"] * accounted[agv_id], abs=1e-9
            )


def run_motion_world(*, observed: bool, trace: Path | None = None) -> tuple[str, dict[str, Any]]:
    """Scenario 1 of the part B variant of Appendix A with the digest on; `observed` adds the replay and the invariants
    and, when `trace` is given, a full trace. Returns the digest and the final entity snapshot."""
    with Environment(debug=observed) as env:
        env.enable_digest()
        if trace is not None:
            TraceRecorder(env, trace)
        m, _ = appendix_a(env, observed=observed, **MOTION_OPTIONS)
        run_appendix_a(1, m)
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        digest = env.fingerprint().digest
        assert digest is not None
        return digest, env.entities.snapshot()


def test_the_motion_scenario_is_the_same_with_and_without_observers(tmp_path: Path) -> None:
    """Spec §17, purity: the replay, the invariants and a full trace change neither the digest nor the final state."""
    bare = run_motion_world(observed=False)
    observed = run_motion_world(observed=True, trace=tmp_path / "motion.simtrace")
    assert bare == observed
    trace = Trace.open(tmp_path / "motion.simtrace")
    assert trace.verify() is True
    assert trace.fingerprint is not None and trace.fingerprint.digest == bare[0]


def test_the_motion_scenario_digest_is_the_same_across_hash_seeds() -> None:
    expected = run_motion_world(observed=False)[0]
    script = (
        "from tests.intralogistics.test_traffic_scenarios import run_motion_world;"
        "print('DIGEST', run_motion_world(observed=False)[0])"
    )
    for hash_seed in ("0", "1", "123"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.splitlines()[-1] == f"DIGEST {expected}", hash_seed


# --- spec §17: control ---------------------------------------------------------------------------------------------


def one_way_line(env: Environment, **network: Any) -> Declared:
    """A one-way line ``S -> N0 -> N1`` of 10 m and 30 m, split into blocks of at most 10 m: the nodes ``N0~N1#1`` and
    ``N0~N1#2`` appear between ``N0`` and ``N1``."""
    nodes = [Node("S", -10, 0), Node("N0", 0, 0), Node("N1", 30, 0)]
    graph = LayoutGraph(
        nodes, [Arc(nodes[0], nodes[1], bidirectional=False), Arc(nodes[1], nodes[2], bidirectional=False)]
    )
    return declare(env, graph.subdivide(10.0), **network)


def test_following_on_a_subdivided_one_way_line() -> None:
    """The second AGV waits for the first to leave the bay, then runs one block behind it; neither fails."""
    m, invariants = scenario(
        lambda env: one_way_line(env, capacities={"N1": 2}),  # the end of the line takes both vehicles
        {"A": "N0", "B": "S"},
        bays={"W0": "N0", "W1": "N1"},
        agv_type=vehicle(length=1.0),
    )
    m.order("W0", "W1")
    m.order("W0", "W1")
    m.env.run()
    assert_ran(m, invariants, orders=2)
    assert not m.events(TrafficDeadlock)

    (wait,) = m.events(TrafficWaitStarted)
    assert (wait.agv, wait.node, wait.blockers) == ("B", "N0", ("A",))  # B queues for the bay A picks at
    a, b = departures(m, "A"), departures(m, "B")
    chain = ["N0", "N0~N1#1", "N0~N1#2"]
    assert list(a) == chain and list(b) == ["S", *chain]
    for here, ahead in zip(chain, chain[1:], strict=False):
        # B leaves a node only once A has left the next one: one block of separation, never less.
        assert b[here] >= a[ahead]
    assert b["N0"] > a["N0"]


def test_head_on_in_a_corridor_the_second_agv_waits_before_its_port() -> None:
    m, invariants = scenario(
        two_junction_corridor,
        {"A": "x1", "B": "x2"},
        bays={"X1": "x1", "X2": "x2", "Y1": "y1", "Y2": "y2"},
    )
    spots = Spots(m)
    m.order("X1", "Y2")  # A: x1, J1, a, b, J2, y2
    m.order("X2", "Y1")  # B: x2, J2, b, a, J1, y1
    m.env.run()
    assert_ran(m, invariants, orders=2)
    assert not m.events(TrafficDeadlock)

    # B never entered its port J2: it stood on x2 asking for the corridor, J2~x2 and J2, blocked by A alone.
    (event, node) = spots.first("B")
    assert (node, event.node, event.blockers, event.resources) == ("x2", "J2", ("A",), ("corridor:a", "J2~x2", "J2"))
    assert not [e for e, _ in spots.seen if e.agv == "A"]  # A never waits
    # B goes as soon as A has left the far port J2, the corridor's exit, and not before.
    (ended,) = m.events(TrafficWaitEnded, agv="B")
    assert ended.reason == "granted"
    # (A clears the bay y2 after its delivery through J2 and b: only the order travels count)
    delivered = float(m.orders[0].delivered_at or 0.0)
    assert float(ended.t) == departures(m, "A", until=delivered)["J2"] == departures(m, "B", until=delivered)["x2"]


def test_a_swap_with_a_second_route_is_resolved_by_reroute() -> None:
    """``S1 -> S2`` and ``S2 -> S1`` over the corridor ``J1, L, J2``: A waits for S2 inside the corridor while B holds
    S2 and waits for the corridor. The victim takes the other chain."""
    m, invariants = scenario(loop_with_side_node, {"A": "S1", "B": "S2"}, bays={"W1": "S1", "W2": "S2"})
    m.order("W1", "W2")
    m.order("W2", "W1")
    m.env.run()
    assert_ran(m, invariants, orders=2)
    (deadlock,) = m.events(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), "B", "reroute")
    routes = {(e.agv, e.reason): e.nodes for e in m.events(AgvRouted) if e.agv == "B"}
    assert routes[("B", "travel")] == ("S2", "J2", "L", "J1", "S1")
    assert routes[("B", "deadlock")] == ("S2", "J2", "U", "J1", "S1")  # the other chain


def test_a_swap_without_another_route_is_resolved_by_step_aside() -> None:
    """The same swap on a graph whose only connection is the corridor ``J1, U, J2``: the victim steps aside onto a side
    leaf of the junction and drives on when the corridor is free."""
    m, invariants = scenario(
        lambda env: build(
            env,
            [("J1", 0, 0), ("U", 10, 0), ("J2", 20, 0), ("S1", -10, 0), ("S2", 30, 0), ("X1", 0, 10), ("X2", 20, 10)],
            [("J1", "U"), ("U", "J2"), ("J1", "S1"), ("J2", "S2"), ("J1", "X1"), ("J2", "X2")],
        ),
        {"A": "S1", "B": "S2"},
        bays={"W1": "S1", "W2": "S2"},
    )
    m.order("W1", "W2")
    m.order("W2", "W1")
    m.env.run()
    assert_ran(m, invariants, orders=2)
    (deadlock,) = m.events(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), "A", "step_aside")
    (aside,) = [e for e in m.events(AgvRouted) if e.reason == "step_aside"]
    assert (aside.agv, aside.destination) == ("A", "X1")
    assert not [e for e in m.events(AgvRouted) if e.reason == "deadlock"]  # there is no other route to take


def stream_world(env: Environment, **network: Any) -> Declared:
    """A corridor ``J1, a, b, J2`` with four bay leaves on each junction and a parking leaf on each, for idle AGVs to
    step off to."""
    return build(
        env,
        [
            ("J1", 0, 0),
            ("a", 10, 0),
            ("b", 20, 0),
            ("J2", 30, 0),
            ("x1", -10, 10),
            ("y1", -10, -10),
            ("z1", -20, 0),
            ("u1", -10, 20),
            ("p1", -30, 10),
            ("x2", 40, 10),
            ("y2", 40, -10),
            ("w2", 50, 0),
            ("v2", 40, 20),
            ("p2", 50, 20),
        ],
        [
            ("J1", "a"),
            ("a", "b"),
            ("b", "J2"),
            ("J1", "x1"),
            ("J1", "y1"),
            ("J1", "z1"),
            ("J1", "u1"),
            ("J1", "p1"),
            ("J2", "x2"),
            ("J2", "y2"),
            ("J2", "w2"),
            ("J2", "v2"),
            ("J2", "p2"),
        ],
        **network,
    )


def stream(port: int, *, clear_service_nodes: bool) -> tuple[Mission, TrafficInvariants]:
    """The fairness scenario on :func:`stream_world` with the port J2 of capacity `port`: forward stream A1 (x1 to y2)
    and A2 (y1 to w2), W the other way (x2 to u1), and A3 (z1 to v2) at 20, after the corridor is held forward."""
    m, invariants = scenario(
        lambda env: stream_world(env, capacities={"J2": port}),
        {"A1": "x1", "A2": "y1", "W": "x2", "A3": "z1"},
        bays={name.upper(): name for name in ("x1", "y1", "z1", "u1", "x2", "y2", "w2", "v2")},
        parking={"P1": ("p1", 4), "P2": ("p2", 4)},
        clear_service_nodes=clear_service_nodes,
    )
    m.order("X1", "Y2")  # A1
    m.order("Y1", "W2")  # A2
    m.order("X2", "U1")  # W, the other way
    m.order("Z1", "V2", at=20.0)  # A3, later: asks while A1 and A2 hold the corridor forward
    return m, invariants


def corridor_grants(m: Mission) -> list[str]:
    return [e.agv for e in m.events(TrafficReserved) if "corridor:a" in e.resources]


@pytest.mark.parametrize("port", [1, 2, 3], ids=["port-1", "port-2", "port-3"])
def test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside(port: int) -> None:
    """Forward stream A1, A2 and a later A3; W comes the other way. W waits for A1 and A2, which are inside or ahead of
    it in the queue, but not for A3: A3 asks for the corridor while it is held in its own direction and is held
    back by the gate until W has gone through. Whatever the capacity of the port at W's end, a stream vehicle that
    passes it keeps the corridor until it leaves the port (spec §7.5), so the stream vehicles that fill the port while
    W waits still hold the corridor, the gate of §7.4 applies, and the grants come in the same order.

    The AGVs do not clear service nodes here. With clearing on, A1 leaves its delivery bay y2 for the parking area
    through J2 while W still waits, and takes a slot of J2 without holding the corridor: a third party, for which the
    gate does not apply (see the next test). This test is about the stream vehicles only."""
    m, invariants = stream(port, clear_service_nodes=False)
    spots = Spots(m)
    m.env.run()
    assert_ran(m, invariants, orders=4)
    assert not m.events(TrafficDeadlock)

    assert corridor_grants(m) == ["A1", "A2", "W", "A3"]  # A3 comes last although the corridor was held its way
    held_back = next(e for e in m.events(TrafficWaitStarted) if e.agv == "A3")
    assert float(held_back.t) == 21.0 and "W" in held_back.blockers
    entered = {e.agv: float(e.t) for e in m.events(TrafficReserved) if "corridor:a" in e.resources}
    left = {e.agv: float(e.t) for e in m.events(TrafficReleased) if "corridor:a" in e.resources}
    assert entered["W"] < entered["A3"]  # W goes first
    assert left["W"] <= entered["A3"]  # and A3 enters only when W has left the corridor
    assert entered["A3"] > float(held_back.t)  # A3 waited
    assert left["A1"] <= entered["W"] and left["A2"] <= entered["W"]  # W waited for the vehicles ahead only
    assert spots.first("W")[1] == "x2"


def test_known_lapse_a_vehicle_outside_the_corridor_on_the_waiters_port_lets_the_stream_pass_first() -> None:
    """The known lapse of the fairness gate (spec §7.4), pinned so that a change of it is noticed. In the scenario
    above at port capacity 2 with service-node clearing on, A1 delivers at the bay y2 and its clearance travel to the
    parking area p2 passes J2 at 42.731, when A2's claim ahead holds the other slot. W is then blocked on J2 by A1,
    which holds no corridor, so the gate does not hold A3 back and A3 enters before W."""
    m, invariants = stream(2, clear_service_nodes=True)
    m.env.run()
    assert_ran(m, invariants, orders=4)
    assert corridor_grants(m) == ["A1", "A2", "A3", "W"]
    (granted,) = [e for e in m.events(TrafficReserved) if e.agv == "A3" and "corridor:a" in e.resources]
    assert float(granted.t) == pytest.approx(42.731, abs=1e-3)
    assert ("clear_node", "y2", "p2") in m.routed("A1")


def passing_place(env: Environment, kind: str, **network: Any) -> Declared:
    """``x1 ... a, P, b ... x2`` with P of capacity 2 (set by the test): two corridors ``J1, a, P`` and ``P, b, J2``
    between junctions with two leaves each (``corridors``), or the line ``x1, a, P, b, x2`` of two dead ends rooted at
    P (``dead_ends``)."""
    if kind == "dead_ends":
        return build(
            env,
            [("x1", 0, 0), ("a", 10, 0), ("P", 20, 0), ("b", 30, 0), ("x2", 40, 0)],
            [("x1", "a"), ("a", "P"), ("P", "b"), ("b", "x2")],
            **network,
        )
    return build(
        env,
        [("J1", 0, 0), ("a", 10, 0), ("P", 20, 0), ("b", 30, 0), ("J2", 40, 0)]
        + [("x1", -10, 10), ("y1", -10, -10), ("x2", 50, 10), ("y2", 50, -10)],
        [("J1", "a"), ("a", "P"), ("P", "b"), ("b", "J2"), ("J1", "x1"), ("J1", "y1"), ("J2", "x2"), ("J2", "y2")],
        **network,
    )


@pytest.mark.parametrize("kind", ["corridors", "dead_ends"])
def test_two_agvs_head_on_at_a_node_of_capacity_two_complete_through_one_resolved_deadlock(kind: str) -> None:
    """spec §7.2, §18: the hold-back treats a port or root of capacity above 1 like any other, so two AGVs that meet
    head-on each stop one node before P; the deadlock is resolved by one step aside and both orders complete."""
    m, invariants = scenario(
        lambda env: passing_place(env, kind, capacities={"P": 2}),
        {"A": "x1", "B": "x2"},
        bays={"WA": "x1", "WB": "x2"},
    )
    m.order("WA", "WB")  # A
    m.order("WB", "WA")  # B
    m.env.run()
    assert_ran(m, invariants, orders=2)
    assert [e.action for e in m.events(TrafficDeadlock)] == ["step_aside"]


def test_a_vehicle_does_not_wait_on_a_roomy_port_where_a_resting_agv_holds_the_other_slot() -> None:
    """The reviewer's plug: S1 rests on the port J2 (capacity 2); W carries from x2 into the corridor while S2, inside
    it, carries out through J2. Had W waited on J2, it would take the slot S2 needs, and neither would move with nothing
    reported. W holds back before the port instead, and both orders complete."""
    m, invariants = scenario(
        lambda env: two_junction_corridor(env, capacities={"J2": 2}),
        {"S1": "J2", "W": "x2", "S2": "x1"},
        bays={"WA": "x1", "WB": "x2", "WC": "y2"},
    )
    m.order("WA", "WC", at=1.0)  # S2, through the corridor and J2
    m.order("WB", "WA", at=1.0)  # W, the other way
    m.env.run()
    assert_ran(m, invariants, orders=2)


def test_a_swap_past_a_resting_agv_on_a_roomy_root_is_a_reported_deadlock() -> None:
    """The reviewer's second plug: ``x1, a, P, b, x2`` with an AGV parked on the parking area of capacity 2 on P, and
    two AGVs swapping ends. There is no way past: the deadlock is reported unresolved, once, and the run returns."""
    m, invariants = scenario(
        lambda env: passing_place(env, "dead_ends"),
        {"I": "P", "A": "x1", "B": "x2"},
        bays={"WA": "x1", "WB": "x2"},
        parking={"PK": ("P", 2)},
    )
    m.order("WA", "WB", at=1.0)
    m.order("WB", "WA", at=1.0)
    m.env.run()
    assert invariants.checked > 0
    assert m.env.peek() == float("inf")
    assert [(e.agvs, e.action) for e in m.events(TrafficDeadlock)][-1:] == [(("A", "B"), "unresolved")]
    assert len([e for e in m.events(TrafficDeadlock) if e.action == "unresolved"]) == 1
    assert [o.status for o in m.orders] == [OrderStatus.IN_TRANSIT] * 2


def test_an_agv_parked_on_a_roomy_port_is_dispatched_through_the_empty_corridor_at_once() -> None:
    """I1 and I2 came through the corridor to the parking area of capacity 2 on the port J2, whose other neighbours are
    bays. I1 is then dispatched back through the empty corridor: neither keeps an exit record on J2 (spec §7.5), so
    I1's claims are granted at once and the order completes."""
    m, invariants = scenario(
        two_junction_corridor,
        {"I1": "x1", "I2": "y1"},
        bays={"A": "x1", "D": "y1", "B": "x2", "C": "y2"},
        parking={"P": ("J2", 2)},
    )
    nodes = m.nodes
    m.at(1.0, lambda: m.fleet.make_way(m.agvs["I1"], nodes["J2"]))
    m.at(60.0, lambda: m.fleet.make_way(m.agvs["I2"], nodes["J2"]))
    m.order("D", "A", at=200.0)
    m.env.run()
    assert_ran(m, invariants, orders=1)
    assert m.routed("I1")[:2] == [("make_way", "x1", "J2"), ("travel", "J2", "y1")]
    assert not m.events(TrafficWaitStarted)


def test_a_standstill_behind_waiting_blockers_on_a_roomy_port_is_reported_stuck() -> None:
    """The part A review's third plug: I1 and I2 rest on the port J2 (capacity 2), whose other neighbours are bays.
    S2 carries from x1 through the corridor towards y2 when I1 is dispatched from J2 back through it: I1 waits on J2
    for the corridor, S2 waits before J2 for a slot held by I1 and by I2, which has nowhere to go. It is not a deadlock
    (I2 is not waiting), but both waits are stuck (spec §7.9): each is reported once, the run returns and no order
    fails."""
    m, invariants = scenario(
        lambda env: two_junction_corridor(env, capacities={"J2": 2}),
        {"I1": "J2", "I2": "J2", "S2": "x1"},
        bays={"WA": "x1", "WB": "x2", "WC": "y2", "WD": "y1"},
    )
    m.order("WA", "WC", at=1.0)  # S2: x1 to y2 through the corridor
    m.order("WD", "WA", at=3.0)  # I1: J2 to y1 through the corridor
    m.env.run()
    assert invariants.checked > 0
    assert m.env.peek() == float("inf")
    assert not m.events(TrafficDeadlock)
    assert [(e.agv, e.blockers) for e in m.events(TrafficWaitStarted)] == [("I1", ("S2",)), ("S2", ("I1", "I2"))]
    assert [(e.agv, dict(e.blockers)) for e in m.events(TrafficWaitStuck)] == [
        ("I1", {"S2": "WAITING"}),
        ("S2", {"I1": "WAITING", "I2": "IDLE"}),
    ]
    assert [o.status for o in m.orders] == [OrderStatus.IN_TRANSIT, OrderStatus.DISPATCHED]


def long_corridor(env: Environment) -> Declared:
    """A corridor ``J1, a, b, c, J2`` with three leaves on each junction and a parking leaf on each."""
    return build(
        env,
        [
            ("J1", 0, 0),
            ("a", 10, 0),
            ("b", 20, 0),
            ("c", 30, 0),
            ("J2", 40, 0),
            ("x1", -10, 10),
            ("y1", -10, -10),
            ("z1", -10, 0),
            ("p1", 0, 20),
            ("x2", 50, 10),
            ("y2", 50, -10),
            ("z2", 50, 0),
            ("p2", 40, 20),
        ],
        [
            ("J1", "a"),
            ("a", "b"),
            ("b", "c"),
            ("c", "J2"),
            ("J1", "x1"),
            ("J1", "y1"),
            ("J1", "z1"),
            ("J1", "p1"),
            ("J2", "x2"),
            ("J2", "y2"),
            ("J2", "z2"),
            ("J2", "p2"),
        ],
    )


class Waivers(ReservationController):
    """Notes each detection in which the claims still waiting form a cycle only through fairness gates: the set of
    deadlocked waiters computed without gates is empty and the one computed with gates is not (spec §7.8). Reads the
    controller's queue, as ``TrafficInvariants`` reads its holdings."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.waived: list[tuple[float, tuple[str, ...]]] = []

    def _detect(self) -> Any:
        waiters = [waiting for waiting in self._queue if waiting.stopped]
        if not self._reduce(waiters, gates=False):
            gated = self._reduce(waiters, gates=True)
            if gated:
                self.waived.append((float(self._env.now), tuple(sorted(waiting.agv.id for waiting in gated))))
        return super()._detect()


def test_a_gate_that_would_complete_a_cycle_is_waived() -> None:
    """Three AGVs meet at the far junction ``J2`` of a corridor: A1 and A2 carry to ``y2``, where A0 stands and wants to
    go the other way. At 18.08 their waiting claims form a cycle that exists only through a fairness gate: no
    ``traffic.deadlock`` is reported, the gate is waived and A0 is granted at once. The deadlock reported later, when
    a cycle stands without gates, is a real one and is resolved by a step aside."""
    sites = ["x1", "y1", "z1", "x2", "y2", "z2", "a", "b", "c"]
    m, invariants = scenario(
        long_corridor,
        {"A0": "y2", "A1": "b", "A2": "z2"},
        bays={f"B{site}": site for site in sites},
        parking={"P1": ("p1", 4), "P2": ("p2", 4)},
        controller_type=Waivers,
    )
    m.order("Bb", "By2")  # A1
    m.order("Bc", "By2")  # A2
    m.order("By2", "By1")  # A0
    m.env.run()
    assert_ran(m, invariants, orders=3)

    controller = m.controller
    assert isinstance(controller, Waivers)
    ((when, members), *_) = controller.waived
    assert members == ("A0", "A1", "A2")
    # At that instant no deadlock is reported and the claim that gated the others is granted.
    assert not [e for e in m.events(TrafficDeadlock) if float(e.t) == when]
    assert [(e.agv, e.reason) for e in m.events(TrafficWaitEnded) if float(e.t) == when] == [("A0", "granted")]
    # The one deadlock of the run comes later, without gates, and is resolved.
    assert [(e.agvs, e.victim, e.action) for e in m.events(TrafficDeadlock)] == [
        (("A0", "A1", "A2"), "A0", "step_aside")
    ]
    assert all(float(e.t) > when for e in m.events(TrafficDeadlock))


def test_a_reroute_asked_of_an_agv_on_its_goal_during_a_step_aside_does_not_raise() -> None:
    """Found by these scenarios: A1 carries ``c -> b``, is sent aside along ``c, b, a, J1, z1``, stands on ``b``, its
    goal, and is asked for a reroute. It has arrived (spec §7.9): the run goes on and every order completes."""
    sites = ["x1", "y1", "z1", "x2", "y2", "z2", "a", "b", "c"]
    m, invariants = scenario(
        long_corridor,
        {"A0": "a", "A1": "c", "A2": "b", "A3": "x1"},
        bays={f"B{site}": site for site in sites},
        parking={"P1": ("p1", 4), "P2": ("p2", 4)},
    )
    m.order("Ba", "Bb")
    m.order("Ba", "Bx1")
    m.order("Bb", "Ba")
    m.env.run(until=3000.0)
    assert invariants.checked > 0
    assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
    assert not [e for e in m.events(OrderStatusChanged) if e.status == "FAILED"]
    assert all(e.action != "unresolved" for e in m.events(TrafficDeadlock))
    arrived = [e for e in m.events(AgvRouted) if e.agv == "A1" and e.nodes == ("b",)]
    assert [(e.origin, e.destination, e.cost, e.reason) for e in arrived] == [("b", "b", 0.0, "blocked")]
    assert {agv.id: (agv.state.name, agv.current_node.id) for agv in m.agvs.values()} == {
        "A0": ("IDLE", "p1"),
        "A1": ("IDLE", "p1"),
        "A2": ("IDLE", "x1"),
        "A3": ("IDLE", "p1"),
    }


def test_the_default_builder_completes_twenty_transfer_orders_with_no_deadlock_and_no_stuck_wait() -> None:
    """``build_simple_system`` with its two AGVs: its parking node N1 and charging node N2 have capacity 2 and are the
    passing places. The orders go from A to B, so each AGV charges when its battery asks for it."""
    env = Environment(debug=True)
    layout = Layout(env)
    replay = FullReplay(env)
    controller = ReservationController(env=env, graph=layout.graph())
    invariants = TrafficInvariants(env, controller)
    fleet, _, origin, destination, _ = build_simple_system(env, traffic_controller=controller, agv_length=1.0)
    sku = next(iter(origin.inventory))
    orders = [fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination) for _ in range(20)]
    for order in orders:
        fleet.submit(order)
    env.run()
    assert [o.status for o in orders] == [OrderStatus.COMPLETED] * 20
    assert not replay.of(TrafficDeadlock, TrafficWaitStuck)
    assert replay.of(TrafficWaitStarted) and invariants.checked > 0  # it did queue: the line is shared


def test_the_default_builder_with_orders_in_both_directions() -> None:
    """Head-on meetings on the builder's line, with a battery that never runs low so that only traffic is tested: the
    AGVs pass each other on N1 and N2 and no wait outlasts them."""
    env = Environment(debug=True)
    layout = Layout(env)
    replay = FullReplay(env)
    controller = ReservationController(env=env, graph=layout.graph())
    invariants = TrafficInvariants(env, controller)
    sku = SKU("A", 1.0, 0.1)
    fleet, _, a, b, _ = build_simple_system(
        env,
        traffic_controller=controller,
        agv_length=1.0,
        agv_battery_capacity=1.0e5,
        products=[sku],
        initial_inventory_a={sku: 100},
        initial_inventory_b={sku: 100},
    )
    orders = []
    for index in range(20):
        origin, destination = (a, b) if index % 2 == 0 else (b, a)
        orders.append(fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination))
        fleet.submit(orders[-1])
    env.run()
    assert [o.status for o in orders] == [OrderStatus.COMPLETED] * 20
    assert not replay.of(TrafficDeadlock, TrafficWaitStuck)
    assert replay.of(TrafficWaitStarted) and invariants.checked > 0


def test_two_fleets_share_a_controller() -> None:
    """Two fleets on one graph and one ``ReservationController`` meet head-on in a corridor: the second waits for the
    first, whatever fleet it belongs to."""
    env = Environment(debug=True)
    declared = two_junction_corridor(env)
    controller = ReservationController(env=env, graph=declared.graph)
    kind = vehicle(length=0.0)

    def bay(name: str, node: str) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[declared.ref(node)],
            output_bays=[declared.ref(node)],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: 10},
            pick_time=1.0,
            put_time=1.0,
        )

    sites = {name: bay(name, node) for name, node in (("X1", "x1"), ("Y2", "y2"), ("X2", "x2"), ("Y1", "y1"))}
    fleets = {
        "first": FleetCoordinator(
            env=env,
            graph=declared.graph,
            fleet=[AGV(env=env, agv_type=kind, agv_id="A", initial_node=declared.ref("x1"))],
            warehouses=[sites["X1"], sites["Y2"]],
            charging_stations=[],
            traffic_controller=controller,
            default_metrics=False,
            name="first",
        ),
        "second": FleetCoordinator(
            env=env,
            graph=declared.graph,
            fleet=[AGV(env=env, agv_type=kind, agv_id="B", initial_node=declared.ref("x2"))],
            warehouses=[sites["X2"], sites["Y1"]],
            charging_stations=[],
            traffic_controller=controller,
            default_metrics=False,
            name="second",
        ),
    }
    replay = FullReplay(env, from_snapshot=True)
    invariants = TrafficInvariants(env, controller)
    orders = [
        fleets["first"].create_order(sku=SKU_A, quantity=1, origin=sites["X1"], destination=sites["Y2"]),
        fleets["second"].create_order(sku=SKU_A, quantity=1, origin=sites["X2"], destination=sites["Y1"]),
    ]
    for fleet, order in zip(fleets.values(), orders, strict=True):
        fleet.submit(order)
    env.run()
    assert [o.status for o in orders] == [OrderStatus.COMPLETED] * 2
    assert not replay.of(TrafficDeadlock, TrafficWaitStuck)
    (wait,) = replay.of(TrafficWaitStarted)
    assert (wait.agv, wait.node, wait.blockers) == ("B", "J2", ("A",))  # across fleets, as within one
    assert invariants.checked > 0


# --- spec §17: a lone AGV -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0.0, 1.0])
def test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_the_appendix_layout(length: float) -> None:
    profile = TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=0.8)
    runs = []
    for reserve in (True, False):
        m = mission_world(
            t_layout,
            {"A": "C1"},
            bays={"S": "STORE_OUT", "L": "LINE_IN", "P": "PARK"},
            reserve=reserve,
            agv_type=vehicle(length=length, profile=profile),
            clear_service_nodes=False,  # the one difference by design: an idle AGV leaves a bay only under reservation
        )
        m.order("S", "L")
        m.order("L", "P", at=200.0)
        m.order("P", "S", at=400.0)
        m.env.run()
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        runs.append(timeline(m.replay))
    assert len(runs[0]) > 20
    assert runs[0] == runs[1]


@pytest.mark.parametrize("pieces", [1, 2, 10])
def test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_a_line_split_into_arcs(pieces: int) -> None:
    runs = []
    for reserve in (True, False):
        ends = [Node("L0", 0, 0), Node("L1", 100, 0)]
        graph = LayoutGraph(ends, [Arc(ends[0], ends[1])]).subdivide(100.0 / pieces)
        assert len(graph.nodes) == pieces + 1
        m = mission_world(
            lambda env: declare(env, graph),
            {"A": "L0"},
            bays={"W0": "L0", "W1": "L1"},
            reserve=reserve,
            agv_type=vehicle(length=1.0),
            clear_service_nodes=False,
        )
        m.order("W0", "W1")
        m.order("W1", "W0", at=500.0)
        m.env.run()
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 2
        assert len(m.events(AgvMoveEnded)) == 2 * pieces
        runs.append(timeline(m.replay))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize("pieces", [1, 2, 10])
def test_a_straight_line_split_into_arcs_takes_the_single_arc_time_under_both_controllers(
    pieces: int, reserve: bool
) -> None:
    """Spec §17 (motion): 100 m at 1.5 m/s with 0.8 m/s^2 takes 100 / 1.5 + 1.5 / 0.8 = 68.541666... s as one arc and as
    2 or 10 arcs, to 1e-9 relative, with the nodes passed at speed."""
    expected = 100.0 / 1.5 + 1.5 / 0.8
    assert steady().travel_time(100.0) == pytest.approx(expected, rel=1e-12)
    w = world(lambda env: straight(env, pieces, 100.0), {"A": "P0"}, reserve=reserve)
    w.go("A", f"P{pieces}")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    ended = w.of(AgvMoveEnded)
    assert len(ended) == pieces
    assert ended[-1].t == pytest.approx(expected, rel=1e-9)
    assert [e.t for e in ended[:-1]] == [e.t for e in w.of(AgvMoveStarted)[1:]]  # passed at speed, not stopped


def test_free_flow_creates_no_segment_or_zone_entity_and_emits_no_traffic_event() -> None:
    m = mission_world(t_layout, {"A": "C1", "B": "PARK"}, bays={"S": "STORE_OUT", "L": "LINE_IN"}, reserve=False)
    m.order("S", "L")
    m.order("S", "L")
    m.env.run()
    assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 2
    assert not [e for e in m.env.entities.live() if isinstance(e, (SegmentBinding, ZoneBinding))]
    assert not [e for e in m.replay.events if e.type_name.startswith("traffic.")]


def test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_the_builder_line() -> None:
    runs = []
    for reserve in (True, False):
        env = Environment()
        layout = Layout(env)
        replay = FullReplay(env)
        controller = ReservationController(env=env, graph=layout.graph()) if reserve else FreeFlowController()
        fleet, _, a, b, _ = build_simple_system(env, n_agvs=1, traffic_controller=controller, agv_length=1.0)
        fleet.clear_service_nodes = False  # an idle AGV leaves a bay only under reservation, by design
        sku = next(iter(a.inventory))
        orders = [
            fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination)
            for origin, destination in ((a, b), (a, b), (b, a))
        ]
        for order in orders:
            fleet.submit(order)
        env.run()
        assert [o.status for o in orders] == [OrderStatus.COMPLETED] * 3
        runs.append(timeline(replay))
    assert len(runs[0]) > 20
    assert runs[0] == runs[1]


# --- spec §17: the examples ---------------------------------------------------------------------------------------


@contextlib.contextmanager
def under_reservation() -> Iterator[list[tuple[FullReplay, TrafficInvariants]]]:
    """Run the examples of ``examples/`` with every coordinator on its own ``ReservationController`` (vehicle length 0)
    with a full replay and the invariants attached; yields the pairs, one per coordinator."""
    pairs: list[tuple[FullReplay, TrafficInvariants]] = []

    class Reserved(FleetCoordinator):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            controller = ReservationController(env=kwargs["env"], graph=kwargs["graph"])
            super().__init__(*args, **{**kwargs, "traffic_controller": controller})
            pairs.append((FullReplay(self.env, from_snapshot=True), TrafficInvariants(self.env, controller)))

    def with_length(*args: Any, **kwargs: Any) -> AGVType:
        if kwargs.get("length") is None:
            kwargs["length"] = 0.0
        return AGVType(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        for module in (intralogistics, builders):
            patch.setattr(module, "FleetCoordinator", Reserved)
            patch.setattr(module, "AGVType", with_length)
        yield pairs


def run_example(name: str, capsys: pytest.CaptureFixture[str]) -> str:
    """Run the example `name` headless and return what it printed."""
    namespace = runpy.run_path(str(ROOT / "examples" / f"{name}.py"), run_name="__headless__")
    namespace["main"](**({} if name == "intralogistics_simple" else {"plot": False}))
    return capsys.readouterr().out


COMPLETED = {
    "intralogistics_intermediate": r"Orders: \d+ submitted, (\d+) completed",
    "intralogistics_advanced": r"Outbound: +(\d+) completed",
}
"""How each example prints its completed orders (the outbound ones for the advanced example)."""


@pytest.mark.parametrize("name", ["intralogistics_simple", "intralogistics_intermediate", "intralogistics_advanced"])
def test_the_examples_hold_the_traffic_invariants_under_a_reservation_controller(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec §17: the invariants and the replay hold; no deadlock is left unresolved and no stuck wait is still open at
    the end; the intermediate and advanced examples complete as many orders as under free flow."""
    with under_reservation() as pairs:
        reserved = run_example(name, capsys)
    assert len(pairs) == 1
    replay, invariants = pairs[0]
    assert replay.events and invariants.checked > 0
    assert replay.of(TrafficReserved)
    assert not [e for e in replay.of(TrafficDeadlock) if e.action == "unresolved"]
    events = replay.events
    for index, stuck in enumerate(events):
        if isinstance(stuck, TrafficWaitStuck):
            assert any(isinstance(e, TrafficWaitEnded) and e.agv == stuck.agv for e in events[index:]), stuck
    if name in COMPLETED:
        free = run_example(name, capsys)  # the same example under free flow
        counts = []
        for text in (reserved, free):
            match = re.search(COMPLETED[name], text)
            assert match is not None, text
            counts.append(int(match.group(1)))
        assert counts[0] == counts[1] > 0


# --- spec §10.4: with the default router, route cost decides as path length did -----------------------------------


def path_length_rankings(patch: pytest.MonkeyPatch) -> None:
    """Put back, through `patch`, the rankings of the end of part B: the dispatch of ``NearestIdleStrategy``, the
    choice of ``NearestParkingPolicy``, the bays and both charger searches rank by ``graph.path_distance`` of
    ``graph.shortest_path`` (the chords), the bays through the warehouse's chord helper."""
    from simulatte.intralogistics.policies import NearestIdleStrategy, NearestParkingPolicy
    from simulatte.intralogistics.speed import KinematicProfile
    from simulatte.intralogistics.travel import estimate_arc_energy

    def distance(graph: LayoutGraph, origin: Node, destination: Node) -> float:
        path = graph.shortest_path(origin, destination)
        return math.inf if path is None else graph.path_distance(path)

    def select(self: Any, order: Any, fleet: Any, context: Any) -> AGV | None:
        graph = context.graph
        candidates = [
            agv
            for agv in fleet
            if agv.state.name == "IDLE" and agv.current_node is not None and agv.can_carry(order.sku, order.quantity)
        ]
        if not candidates:
            return None

        def key(agv: AGV) -> tuple[float, str]:
            assert agv.current_node is not None
            bay = order.origin.nearest_output_bay(agv.current_node, graph)
            return (distance(graph, agv.current_node, bay), agv.agv_id)

        best = min(candidates, key=key)
        return None if key(best)[0] == math.inf else best

    def reposition(self: Any, agv: AGV, context: Any) -> Node | None:
        available = [area for area in context.parking_areas if area.available_capacity > 0]
        if not available or agv.current_node is None:
            return None
        current = agv.current_node
        return min(available, key=lambda area: distance(context.graph, current, area.node)).node

    def best_bay(self: FleetCoordinator, agv: AGV, bays: Any, *, load_weight: float = 0.0) -> Node:
        assert agv.current_node is not None
        return Warehouse._nearest_bay(agv.current_node, bays, self.graph)

    def nearest_charger(self: FleetCoordinator, agv: AGV) -> Any:
        if not self.charging_stations or agv.current_node is None:
            return None
        current = agv.current_node
        best = min(self.charging_stations, key=lambda cs: distance(self.graph, current, cs.node))
        return best if distance(self.graph, current, best.node) < math.inf else None

    def reachable_charger(self: FleetCoordinator, agv: AGV) -> Any:
        if not self.charging_stations or agv.current_node is None:
            return None
        reachable = []
        for cs in self.charging_stations:
            path = self.graph.shortest_path(agv.current_node, cs.node)
            if path is None:
                continue
            total = self.graph.path_distance(path)
            profile = agv.agv_type.speed_profile
            if isinstance(profile, KinematicProfile):
                kin = profile.limits(0.0, agv.battery.level_pct)
                needed = math.fsum(
                    estimate_arc_energy(
                        agv.battery,
                        kin,
                        self.graph.length(u, v),
                        0.0,
                        cast(Arc, self.graph.arc_between(u, v)).speed_limit,
                    )
                    for u, v in zip(path, path[1:])
                )
            else:
                time = profile.travel_time(total, 0.0, agv.battery.level_pct)
                needed = agv.battery.estimate_energy(total, 0.0, total / time if time > 0 else 0.0)
            if agv.battery.level >= needed:
                reachable.append((total, cs))
        return min(reachable, key=lambda item: item[0])[1] if reachable else None

    patch.setattr(NearestIdleStrategy, "select", select)
    patch.setattr(NearestParkingPolicy, "reposition", reposition)
    patch.setattr(FleetCoordinator, "best_bay", best_bay)
    patch.setattr(FleetCoordinator, "_find_nearest_charger", nearest_charger)
    patch.setattr(FleetCoordinator, "_find_reachable_charger", reachable_charger)


def decisions(name: str, capsys: pytest.CaptureFixture[str]) -> list[tuple[Any, ...]]:
    """The ``order.assigned`` and ``agv.routed`` events of the example or Appendix A scenario `name`, in order."""
    seen: list[tuple[Any, ...]] = []

    def record(env: Environment) -> None:
        def handler(event: Any) -> None:
            if isinstance(event, OrderAssigned):
                seen.append((float(env.now), "assigned", event.order, event.agv))
            else:
                seen.append((float(env.now), "routed", event.agv, event.nodes, event.cost, event.reason))

        env.bus.subscribe(handler, (OrderAssigned, AgvRouted))

    if name.startswith("appendix_a_"):
        with Environment() as env:
            record(env)
            m, _ = appendix_a(env, observed=False)
            run_appendix_a(int(name[-1]), m)
        return seen

    class Recorded(Environment):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            record(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(simulatte.environment, "Environment", Recorded)
        main = runpy.run_path(str(ROOT / "examples" / f"{name}.py"), run_name="__headless__")["main"]
        main(**({"plot": False} if "plot" in inspect.signature(main).parameters else {}))
    capsys.readouterr()
    return seen


@pytest.mark.parametrize(
    "name",
    [
        "intralogistics_simple",
        "intralogistics_intermediate",
        "intralogistics_advanced",
        "building_an_agv_system",
        "appendix_a_1",
        "appendix_a_2",
    ],
)
def test_with_the_default_router_dispatch_bays_chargers_and_parking_decide_as_path_length_did(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec §10.4 and §17 (routing): the examples and Appendix A assign the same orders to the same AGVs at the same
    times, and route the same travels, as a run whose rankings are those of the end of part B."""
    ranked = decisions(name, capsys)
    with pytest.MonkeyPatch.context() as patch:
        path_length_rankings(patch)
        reference = decisions(name, capsys)
    assigned = [item for item in ranked if item[1] == "assigned"]
    assert assigned and assigned == [item for item in reference if item[1] == "assigned"]
    assert ranked == reference
