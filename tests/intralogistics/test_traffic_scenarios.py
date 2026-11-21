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
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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
    OrderStatusChanged,
    TrafficDeadlock,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.reservation import SegmentBinding, ZoneBinding
from simulatte.trace import TraceRecorder
from tests.intralogistics.test_idle_traffic import SKU_A, Mission, mission_world
from tests.intralogistics.test_travel import vehicle
from tests.intralogistics.traffic_support import (
    FullReplay,
    TrafficInvariants,
    build,
    loop_with_side_node,
    t_layout,
    two_junction_corridor,
)

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


def departures(m: Mission, agv: str) -> dict[str, float]:
    """The time at which `agv` started an arc from each node, for the nodes it left once."""
    started = m.events(AgvMoveStarted, agv=agv)
    assert len({e.from_node for e in started}) == len(started)
    return {e.from_node: float(e.t) for e in started}


def timeline(replay: FullReplay) -> list[tuple[str, float, str]]:
    """The arrivals and the order transitions of a run, as ``(kind, time, what)``."""
    arrivals = [("arrived", float(e.t), f"{e.agv}@{e.node}") for e in replay.of(AgvMoveEnded)]
    statuses = [("order", float(e.t), f"{e.order}:{e.status}") for e in replay.of(OrderStatusChanged)]
    return sorted(arrivals + statuses, key=lambda item: (item[1], item[0], item[2]))


# --- spec Appendix A ---------------------------------------------------------------------------------------------


def appendix_a(env: Environment, *, observed: bool = True) -> tuple[Mission, TrafficInvariants | None]:
    """The graph and fleet of Appendix A under ``ReservationController`` with ``length=0.0`` and its three orders, none
    submitted yet (see :func:`run_appendix_a`). With `observed` the invariants are attached."""
    graph, nodes = t_layout()
    controller = ReservationController(env=env, graph=graph)
    sku = SKU(id="A", weight=5.0, volume=0.2)
    storage = Warehouse(
        env=env,
        name="Storage",
        input_bays=[nodes["STORE_OUT"]],
        output_bays=[nodes["STORE_OUT"]],
        n_slots=3,
        products=[sku],
        initial_inventory={sku: 50},
        pick_time=5.0,
        put_time=3.0,
    )
    line = Warehouse(
        env=env,
        name="Line",
        input_bays=[nodes["LINE_IN"]],
        output_bays=[nodes["LINE_IN"]],
        n_slots=3,
        products=[sku],
        initial_inventory={},
        pick_time=2.0,
        put_time=2.0,
    )
    kind = AGVType(
        name="std",
        speed_profile=TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=0.8),
        battery_capacity=1000.0,
        weight_capacity=50.0,
        volume_capacity=2.0,
        load_time=5.0,
        unload_time=5.0,
        length=0.0,
    )
    vehicles = [AGV(env=env, agv_type=kind, initial_node=nodes[start]) for start in ("C1", "PARK")]  # agv-0, agv-1
    agvs = {agv.id: agv for agv in vehicles}
    fleet = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=vehicles,
        warehouses=[storage, line],
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
    )
    replay = FullReplay(env, from_snapshot=True)
    invariants = TrafficInvariants(env, controller) if observed else None
    m = Mission(env, fleet, nodes, agvs, {"Storage": storage, "Line": line}, {}, {}, replay)
    m.orders = [fleet.create_order(sku=sku, quantity=2, origin=storage, destination=line) for _ in range(3)]
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
        assert invariants is not None and invariants.checked > 0
        assert [o.status for o in m.orders] == [OrderStatus.COMPLETED] * 3
        assert not [e for e in m.events(OrderStatusChanged) if e.status == "FAILED"]
        assert not m.events(TrafficWaitStuck)
        assert m.orders[0].delivered_at == pytest.approx(51.52, abs=0.005)
        assert [(e.agvs, e.victim, e.action) for e in m.events(TrafficDeadlock)] == [
            (("agv-0", "agv-1"), "agv-0", "step_aside")
        ]


def test_appendix_a_scenario_2_completes_every_order_and_the_idle_agv_makes_way_to_park() -> None:
    with Environment(debug=True) as env:
        m, invariants = appendix_a(env)
        spots = Spots(m)
        run_appendix_a(2, m)
        assert invariants is not None and invariants.checked > 0
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


# --- spec §17: control ---------------------------------------------------------------------------------------------


def one_way_line() -> tuple[LayoutGraph, dict[str, Node]]:
    """A one-way line ``S -> N0 -> N1`` of 10 m and 30 m, split into blocks of at most 10 m: the nodes ``N0~N1#1`` and
    ``N0~N1#2`` appear between ``N0`` and ``N1``."""
    nodes = [Node("S", -10, 0), Node("N0", 0, 0), Node("N1", 30, 0)]
    graph = LayoutGraph(
        nodes, [Arc(nodes[0], nodes[1], bidirectional=False), Arc(nodes[1], nodes[2], bidirectional=False)]
    )
    graph = graph.subdivide(10.0)
    return graph, {node.id: node for node in graph.nodes}


def test_following_on_a_subdivided_one_way_line() -> None:
    """The second AGV waits for the first to leave the bay, then runs one block behind it; neither fails."""
    graph_nodes = one_way_line()
    nodes = graph_nodes[1]
    m, invariants = scenario(
        graph_nodes,
        {"A": "N0", "B": "S"},
        bays={"W0": "N0", "W1": "N1"},
        agv_type=vehicle(length=1.0),
        controller_options={"capacities": {nodes["N1"]: 2}},  # the end of the line takes both vehicles
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
        two_junction_corridor(),
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
    assert float(ended.t) == departures(m, "A")["J2"] == departures(m, "B")["x2"]


def test_a_swap_with_a_second_route_is_resolved_by_reroute() -> None:
    """``S1 -> S2`` and ``S2 -> S1`` over the corridor ``J1, L, J2``: A waits for S2 inside the corridor while B holds
    S2 and waits for the corridor. The victim takes the other chain."""
    m, invariants = scenario(loop_with_side_node(), {"A": "S1", "B": "S2"}, bays={"W1": "S1", "W2": "S2"})
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
    graph_nodes = build(
        [("J1", 0, 0), ("U", 10, 0), ("J2", 20, 0), ("S1", -10, 0), ("S2", 30, 0), ("X1", 0, 10), ("X2", 20, 10)],
        [("J1", "U"), ("U", "J2"), ("J1", "S1"), ("J2", "S2"), ("J1", "X1"), ("J2", "X2")],
    )
    m, invariants = scenario(graph_nodes, {"A": "S1", "B": "S2"}, bays={"W1": "S1", "W2": "S2"})
    m.order("W1", "W2")
    m.order("W2", "W1")
    m.env.run()
    assert_ran(m, invariants, orders=2)
    (deadlock,) = m.events(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), "A", "step_aside")
    (aside,) = [e for e in m.events(AgvRouted) if e.reason == "step_aside"]
    assert (aside.agv, aside.destination) == ("A", "X1")
    assert not [e for e in m.events(AgvRouted) if e.reason == "deadlock"]  # there is no other route to take


def stream_world() -> tuple[LayoutGraph, dict[str, Node]]:
    """A corridor ``J1, a, b, J2`` with four bay leaves on each junction and a parking leaf on each, for idle AGVs to
    step off to."""
    return build(
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
    )


def test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside() -> None:
    """Forward stream A1, A2 and a later A3; W comes the other way. W waits for A1 and A2, which are inside or ahead of
    it in the queue, but not for A3: A3 asks for the corridor while it is held in its own direction and is held
    back by the gate until W has gone through."""
    m, invariants = scenario(
        stream_world(),
        {"A1": "x1", "A2": "y1", "W": "x2", "A3": "z1"},
        bays={name.upper(): name for name in ("x1", "y1", "z1", "u1", "x2", "y2", "w2", "v2")},
        parking={"P1": ("p1", 4), "P2": ("p2", 4)},
    )
    m.order("X1", "Y2")  # A1
    m.order("Y1", "W2")  # A2
    m.order("X2", "U1")  # W, the other way
    m.order("Z1", "V2", at=20.0)  # A3, later: asks while A1 and A2 hold the corridor forward
    m.env.run()
    assert_ran(m, invariants, orders=4)
    assert not m.events(TrafficDeadlock)

    grants = [e.agv for e in m.events(TrafficReserved) if "corridor:a" in e.resources]
    assert grants == ["A1", "A2", "W", "A3"]  # A3 comes last although the corridor was held in its direction
    held_back = next(e for e in m.events(TrafficWaitStarted) if e.agv == "A3")
    assert float(held_back.t) == 21.0 and "W" in held_back.blockers
    entered = {e.agv: float(e.t) for e in m.events(TrafficReserved) if "corridor:a" in e.resources}
    left = {e.agv: float(e.t) for e in m.events(TrafficReleased) if "corridor:a" in e.resources}
    assert entered["W"] < entered["A3"]  # W goes first
    assert left["W"] <= entered["A3"]  # and A3 enters only when W has left the corridor
    assert entered["A3"] > float(held_back.t)  # A3 waited
    assert left["A1"] <= entered["W"] and left["A2"] <= entered["W"]  # W waited for the vehicles ahead only


def long_corridor() -> tuple[LayoutGraph, dict[str, Node]]:
    """A corridor ``J1, a, b, c, J2`` with three leaves on each junction and a parking leaf on each."""
    return build(
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
        long_corridor(),
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
    graph, nodes = two_junction_corridor()
    env = Environment(debug=True)
    controller = ReservationController(env=env, graph=graph)
    kind = vehicle(length=0.0)

    def bay(name: str, node: str) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[nodes[node]],
            output_bays=[nodes[node]],
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
            graph=graph,
            fleet=[AGV(env=env, agv_type=kind, agv_id="A", initial_node=nodes["x1"])],
            warehouses=[sites["X1"], sites["Y2"]],
            charging_stations=[],
            traffic_controller=controller,
            default_metrics=False,
            name="first",
        ),
        "second": FleetCoordinator(
            env=env,
            graph=graph,
            fleet=[AGV(env=env, agv_type=kind, agv_id="B", initial_node=nodes["x2"])],
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
            t_layout(),
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
            (graph, {node.id: node for node in graph.nodes}),
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


def test_free_flow_creates_no_segment_or_zone_entity_and_emits_no_traffic_event() -> None:
    m = mission_world(t_layout(), {"A": "C1", "B": "PARK"}, bays={"S": "STORE_OUT", "L": "LINE_IN"}, reserve=False)
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


@pytest.mark.parametrize("name", ["intralogistics_simple", "intralogistics_intermediate", "intralogistics_advanced"])
def test_the_examples_hold_the_traffic_invariants_under_a_reservation_controller(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with under_reservation() as pairs:
        namespace = runpy.run_path(str(ROOT / "examples" / f"{name}.py"), run_name="__headless__")
        namespace["main"](**({} if name == "intralogistics_simple" else {"plot": False}))
    capsys.readouterr()
    assert len(pairs) == 1
    replay, invariants = pairs[0]
    assert replay.events and invariants.checked > 0
    assert replay.of(TrafficReserved)


# --- spec §17: determinism ------------------------------------------------------------------------------------------


def appendix_digest(observers: bool, trace: Path | None = None) -> tuple[str, str]:
    """The digest and a summary of the final state of Appendix A scenario 1 (a deadlock resolved by a step aside)."""
    with Environment(debug=observers, log_level="DEBUG" if observers else "INFO") as env:
        env.enable_digest()
        if trace is not None:
            TraceRecorder(env, trace)
        m, _ = appendix_a(env, observed=observers)
        run_appendix_a(1, m)
        digest = env.fingerprint().digest
        assert digest is not None
        state = repr(
            (
                float(env.now),
                [(o.id, o.status.name, o.delivered_at) for o in m.orders],
                [(a.id, a.state.name, a.current_node.id if a.current_node else None) for a in m.agvs.values()],
            )
        )
        return digest, state


def test_the_digest_is_the_same_with_and_without_observers(tmp_path: Path) -> None:
    quiet = appendix_digest(False)
    observed = appendix_digest(True, tmp_path / "scenario-1.simtrace")
    assert quiet == observed


def test_the_digest_is_the_same_across_hash_seeds() -> None:
    expected = appendix_digest(False)[0]
    code = "from tests.intralogistics.test_traffic_scenarios import appendix_digest; print(appendix_digest(False)[0])"
    for seed in ("0", "1", "4242"):
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout.splitlines()[-1] == expected, seed
