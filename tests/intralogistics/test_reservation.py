"""``ReservationController``: admission (spec §7.3), queue and gate (§7.4), release (§7.5), lifecycle (§7.1, §7.7).

Every test builds its world with :func:`world`, which runs :class:`FullReplay` and :class:`TrafficInvariants` over
every event. Tests drive the controller directly: an AGV "moves" by setting ``current_node`` to a node it holds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import TrafficReleased, TrafficReserved
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import Claim, ClaimItem, Corridor, Segment, Zone
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.reservation import ReservationController, SegmentBinding, ZoneBinding
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import (
    ClaimRequest,
    Disposition,
    Granted,
    TrafficController,
    TrafficView,
)
from tests.intralogistics.traffic_support import (
    FullReplay,
    TrafficInvariants,
    build,
    fake_hooks,
    line,
    two_junction_corridor,
)


def cart(length: float | None = 4.0, min_gap: float = 0.0, name: str = "cart") -> AGVType:
    return AGVType(
        name=name,
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        length=length,
        min_gap=min_gap,
    )


@dataclass
class World:
    env: Environment
    controller: ReservationController
    nodes: dict[str, Node]
    agvs: dict[str, AGV]
    replay: FullReplay
    invariants: TrafficInvariants

    def seg(self, a: str, b: str) -> Segment:
        return self.controller.network.segment(self.nodes[a], self.nodes[b])

    def corridor(self) -> Corridor:
        (corridor,) = self.controller.network.corridors
        return corridor

    def ask(self, agv: str, *items: tuple[Any, str | None] | Any) -> ClaimRequest:
        """Request a hand-made claim: each item a resource, or a ``(resource, direction)`` pair."""
        claim_items = tuple(ClaimItem(*item) if isinstance(item, tuple) else ClaimItem(item) for item in items)
        return self.controller.request(self.agvs[agv], Claim(claim_items, 1), fake_hooks())

    def leg(self, agv: str, route: str, index: int = 0) -> ClaimRequest:
        """Request the claim of leg `index` of the route of node ids `route` (space separated)."""
        path = [self.nodes[name] for name in route.split()]
        vehicle = self.agvs[agv]
        return self.controller.request(vehicle, self.controller.next_claim(vehicle, path, index), fake_hooks(path))

    def move(self, agv: str, node: str) -> None:
        self.agvs[agv].current_node = self.nodes[node]

    def release(self, agv: str, *resources: Any) -> None:
        self.controller.release(self.agvs[agv], [self._resource(r) for r in resources])

    def retain(self, agv: str, *resources: Any) -> None:
        self.controller.retain(self.agvs[agv], [self._resource(r) for r in resources])

    def _resource(self, resource: Any) -> Any:
        return self.nodes[resource] if isinstance(resource, str) else resource

    def held(self, agv: str) -> list[str]:
        return [resource.id for resource in self.controller._held.get(self.agvs[agv], {})]

    def holders(self, resource: Any) -> list[str]:
        return [agv.id for agv in self.controller.view.holders(self._resource(resource))]

    def events(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [(e.type_name, e.agv, e.resources) for e in self.replay.of(TrafficReserved, TrafficReleased)]


def world(
    graph_nodes: tuple[LayoutGraph, dict[str, Node]],
    agvs: dict[str, str | tuple[str, AGVType]],
    *,
    bind: bool = True,
    place: bool = True,
    **options: Any,
) -> World:
    """An activated world: `agvs` maps each AGV id to its start node id (or ``(node id, type)``), all placed."""
    graph, nodes = graph_nodes
    env = Environment(debug=True)
    replay = FullReplay(env)
    if bind:
        for node in graph.nodes:
            env.entities.bind_node(node)
    controller = ReservationController(env=env, graph=graph, **options)
    vehicles: dict[str, AGV] = {}
    for name, start in agvs.items():
        node, agv_type = (start, cart()) if isinstance(start, str) else start
        vehicles[name] = AGV(env=env, agv_type=agv_type, agv_id=name, initial_node=nodes[node])
    fleet = SimpleNamespace(fleet=tuple(vehicles.values()), parking_areas=(), charging_stations=(), graph=graph)
    controller.register_fleet(fleet)  # type: ignore[arg-type]
    invariants = TrafficInvariants(env, controller)
    if place:
        env.on_activate(lambda: [controller.place(agv, agv.current_node) for agv in vehicles.values()])
    env.activate()
    return World(env, controller, nodes, vehicles, replay, invariants)


def step(w: World, agv: str, route: str, index: int) -> None:
    """Leg `index` of `route` for an extent-0 vehicle: granted at once, the AGV arrives and frees what is behind."""
    nodes = route.split()
    here, there = nodes[index], nodes[index + 1]
    assert w.leg(agv, route, index).granted, (agv, route, index)
    w.move(agv, there)
    w.release(agv, here, w.seg(here, there))


def passing_line() -> tuple[LayoutGraph, dict[str, Node]]:
    """``N0, N1, N2`` with a passing arc between ``N1`` and ``N2``."""
    nodes = {name: Node(name, 10 * i, 0) for i, name in enumerate(("N0", "N1", "N2"))}
    arcs = [Arc(nodes["N0"], nodes["N1"]), Arc(nodes["N1"], nodes["N2"], passing=True)]
    return LayoutGraph(nodes.values(), arcs), nodes


# --- admission (spec §7.3) ---------------------------------------------------------------------------------


class TestAdmission:
    def test_full_node_pends_and_is_granted_on_release(self) -> None:
        w = world(line(3), {"A": "N1", "B": "N0"})
        waiting = w.leg("B", "N0 N1")
        assert not waiting.event.triggered
        assert w.controller.view.pending(w.nodes["N1"]) == 1
        moving = w.leg("A", "N1 N2")
        assert moving.granted
        w.move("A", "N2")
        w.release("A", "N1")
        assert waiting.granted
        assert waiting.event.value == Granted()
        assert w.holders("N1") == ["B"]
        assert w.controller.view.pending(w.nodes["N1"]) == 0
        assert w.invariants.checked > 0

    def test_all_or_nothing(self) -> None:
        w = world(line(3), {"A": "N1", "B": "N0"})
        w.leg("B", "N0 N1")  # the segment is free, the node is not
        assert w.holders(w.seg("N0", "N1")) == []
        assert w.held("B") == ["N0"]
        assert w.replay.state["N0~N1"]["forward"] == ()

    def test_opposite_direction_on_a_single_lane_pends(self) -> None:
        w = world(line(4), {"A": "N1", "B": "N3"})
        assert w.ask("A", (w.seg("N1", "N2"), "forward")).granted
        waiting = w.ask("B", (w.seg("N1", "N2"), "backward"))
        assert not waiting.event.triggered
        w.release("A", w.seg("N1", "N2"))
        assert waiting.granted
        assert w.replay.state["N1~N2"]["backward"] == ("B",)

    def test_same_direction_admitted_while_extents_fit(self) -> None:
        w = world(line(2), {"A": "N0", "B": "N0", "C": "N0"}, place=False)
        segment = w.seg("N0", "N1")
        assert segment.length == 10
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("B", (segment, "forward")).granted  # 4 + 4 <= 10
        third = w.ask("C", (segment, "forward"))  # 4 + 4 + 4 > 10
        assert not third.event.triggered
        assert w.replay.state["N0~N1"]["forward"] == ("A", "B")
        w.release("A", segment)
        assert third.granted

    def test_empty_segment_admits_one_whatever_its_extent(self) -> None:
        w = world(line(2), {"L": ("N0", cart(length=15.0)), "M": ("N0", cart(length=1.0, min_gap=0.5))}, place=False)
        segment = w.seg("N0", "N1")
        assert w.ask("L", (segment, "forward")).granted
        assert not w.ask("M", (segment, "forward")).event.triggered

    def test_min_gap_counts_in_the_extent(self) -> None:
        long_gap = cart(length=4.0, min_gap=2.5)
        w = world(line(2), {"A": ("N0", long_gap), "B": ("N0", long_gap)}, place=False)
        segment = w.seg("N0", "N1")
        assert w.ask("A", (segment, "forward")).granted
        assert not w.ask("B", (segment, "forward")).event.triggered  # 6.5 + 6.5 > 10

    def test_passing_admits_both_directions(self) -> None:
        big = cart(length=8.0)
        agvs: dict[str, str | tuple[str, AGVType]] = {"A": ("N1", big), "B": ("N2", big), "C": ("N1", cart(4.0))}
        w = world(passing_line(), agvs, place=False)
        segment = w.seg("N1", "N2")
        assert segment.passing
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("B", (segment, "backward")).granted  # an empty direction admits one
        third = w.ask("C", (segment, "forward"))  # 8 + 4 > 10 among the forward holders
        assert not third.event.triggered
        w.release("B", segment)
        assert not third.event.triggered  # the other direction never counted
        w.release("A", segment)
        assert third.granted

    def test_zone_capacity(self) -> None:
        graph, nodes = line(4)
        zone = Zone("Z", (nodes["N1"], (nodes["N2"], nodes["N3"])), capacity=2)
        w = world((graph, nodes), {"A": "N0", "B": "N0", "C": "N0"}, place=False, zones=[zone])
        assert w.ask("A", zone).granted
        assert w.ask("B", zone).granted
        third = w.ask("C", zone)
        assert not third.event.triggered
        assert w.replay.state["Z"]["reserved_by"] == ("A", "B")
        assert w.replay.state["Z"]["members"] == ("N1", "N2~N3")
        w.release("B", zone)
        assert third.granted


class TestCorridorAdmission:
    def test_opposite_orientation_pends_and_same_is_admitted(self) -> None:
        w = world(two_junction_corridor(), {"A": "x1", "B": "x2", "C": "y1"})
        corridor = w.corridor()
        assert corridor.id == "corridor:a"
        assert w.ask("A", (corridor, "forward")).granted
        assert w.ask("C", (corridor, "forward")).granted
        assert not w.ask("B", (corridor, "backward")).event.triggered
        assert w.replay.state["corridor:a"]["orientation"] == "forward"
        assert w.replay.state["corridor:a"]["reserved_by"] == ("A", "C")

    def test_an_unoriented_holder_blocks_nobody(self) -> None:
        w = world(two_junction_corridor(), {"P": "a", "X": "x2"})
        corridor = w.corridor()
        assert w.holders(corridor) == ["P"]
        assert w.ask("X", (corridor, "backward")).granted
        assert w.replay.state["corridor:a"]["orientation"] == "backward"

    def test_a_holder_sets_its_orientation_when_no_other_holder_opposes_it(self) -> None:
        w = world(two_junction_corridor(), {"P": "a"})
        corridor = w.corridor()
        request = w.ask("P", (corridor, "forward"))  # held without orientation: claimed again
        assert request.granted
        reserved = w.replay.of(TrafficReserved)[-1]
        assert reserved.resources == ("corridor:a",)
        assert reserved.deltas.ops == (("set", "corridor:a", "orientation", "forward"),)
        assert w.replay.state["corridor:a"]["reserved_by"] == ("P",)
        assert w.ask("P", (corridor, "forward")).granted  # same orientation: nothing left to claim
        assert len(w.replay.of(TrafficReserved)) == 2  # the placement and the orientation

    def test_setting_an_orientation_against_another_holder_pends(self) -> None:
        w = world(two_junction_corridor(), {"P": "a", "X": "x2"})
        corridor = w.corridor()
        assert w.ask("X", (corridor, "backward")).granted
        assert not w.ask("P", (corridor, "forward")).event.triggered

    def test_reversal_with_a_same_orientation_co_holder_pends(self) -> None:
        w = world(two_junction_corridor(), {"A": "x1", "C": "y1"})
        corridor = w.corridor()
        assert w.ask("A", (corridor, "forward")).granted
        assert w.ask("C", (corridor, "forward")).granted
        reversal = w.ask("A", (corridor, "backward"))
        assert not reversal.event.triggered
        w.release("C", corridor)
        assert reversal.granted
        assert w.replay.state["corridor:a"]["orientation"] == "backward"
        assert w.replay.of(TrafficReserved)[-1].deltas.ops == (("set", "corridor:a", "orientation", "backward"),)


def test_own_holdings_never_block() -> None:
    w = world(line(4), {"A": "N1", "B": "N3"})
    segment = w.seg("N1", "N2")
    assert w.ask("A", (segment, "forward")).granted
    backward = Claim((ClaimItem(segment, "backward"),), 1)
    assert w.controller.view.admissible(w.agvs["A"], backward)
    assert not w.controller.view.admissible(w.agvs["B"], backward)
    assert w.controller.request(w.agvs["A"], backward, fake_hooks()).granted
    assert w.holders(segment) == ["A"]


class TestSegmentReversal:
    """A leg over a segment the AGV still holds in the other direction claims it again (spec §7.2)."""

    def test_alone_the_reversal_is_granted_at_once(self) -> None:
        w = world(line(4), {"A": "N1"})
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted
        assert w.replay.state["N1~N2"]["forward"] == ()
        assert w.replay.state["N1~N2"]["backward"] == ("A",)
        reserved = w.replay.of(TrafficReserved)[-1]
        assert reserved.resources == ("N1~N2",)
        assert reserved.deltas.ops == (("remove", "N1~N2", "forward", "A"), ("insert", "N1~N2", "backward", 0, "A"))
        assert w.replay.seeded == w.replay.state
        w.release("A", segment)
        assert w.replay.state["N1~N2"]["backward"] == ()

    def test_a_same_direction_follower_holds_the_reversal_back(self) -> None:
        w = world(line(4), {"A": "N1", "F": "N0"})
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("F", (segment, "forward")).granted  # extents 4 + 4 fit in 10
        reversal = w.ask("A", (segment, "backward"))
        assert not reversal.event.triggered
        assert w.replay.state["N1~N2"]["forward"] == ("A", "F")
        w.release("F", segment)
        assert reversal.granted
        assert w.replay.state["N1~N2"]["forward"] == ()
        assert w.replay.state["N1~N2"]["backward"] == ("A",)

    def test_after_a_reversal_the_reverser_counts_in_its_new_direction(self) -> None:
        w = world(line(4), {"A": "N1", "N": "N0"})
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted
        assert not w.ask("N", (segment, "forward")).event.triggered

    def test_on_a_passing_segment_the_reversal_needs_room_in_the_new_lane(self) -> None:
        agvs: dict[str, str | tuple[str, AGVType]] = {"A": "N1", "F": "N1", "B": "N2", "C": "N2"}
        w = world(passing_line(), agvs, place=False)
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("F", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted  # F still forward: the backward lane is empty
        assert w.replay.state["N1~N2"]["forward"] == ("F",)
        assert w.replay.state["N1~N2"]["backward"] == ("A",)
        assert w.ask("B", (segment, "backward")).granted  # 4 + 4 fit
        assert w.ask("F", (segment, "backward")).granted is False  # 4 + 4 + 4 do not
        assert w.replay.state["N1~N2"]["forward"] == ("F",)


def test_own_holdings_never_block_a_long_vehicle() -> None:
    w = world(line(4), {"A": ("N0", cart(length=12.0))})
    for index, there in enumerate(("N1", "N2", "N3")):
        assert w.leg("A", "N0 N1 N2 N3", index).granted
        w.move("A", there)
        if index == 1:  # extent 12 on arcs of 10: both segments behind the front are still held
            assert w.held("A") == ["N0", "N0~N1", "N1", "N1~N2", "N2"]
            w.release("A", "N0", w.seg("N0", "N1"))
    assert w.held("A") == ["N1", "N1~N2", "N2", "N2~N3", "N3"]


# --- order and gate (spec §7.4) ----------------------------------------------------------------------------


def star() -> tuple[LayoutGraph, dict[str, Node]]:
    """A hub ``H`` with the leaves ``L1 ... L5``."""
    names = [f"L{i}" for i in range(1, 6)]
    return build([("H", 0, 0), *((name, 10 * i, 10) for i, name in enumerate(names))], [("H", n) for n in names])


class TestOrder:
    def test_priority_then_request_order(self) -> None:
        priorities = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 1.0}
        reads: list[str] = []

        def priority(agv: AGV) -> float:
            reads.append(agv.id)
            return priorities[agv.id]

        w = world(star(), {"A": "H", "B": "L1", "C": "L2", "D": "L3"}, priority_fn=priority)
        hub = w.nodes["H"]
        requests = {name: w.ask(name, hub) for name in ("B", "C", "D")}
        assert reads == ["B", "C", "D"]
        priorities["C"] = 5.0  # read once, at the request
        assert not any(request.event.triggered for request in requests.values())
        assert w.ask("A", w.nodes["L5"]).granted
        w.move("A", "L5")
        w.release("A", "H")
        assert [name for name, request in requests.items() if request.granted] == ["D"]
        w.release("D", "H")
        w.release("B", "H")
        assert all(request.granted for request in requests.values())
        hub_grants = [e.agv for e in w.replay.of(TrafficReserved) if e.resources == ("H",)]
        assert hub_grants == ["A", "D", "B", "C"]
        assert reads == ["B", "C", "D", "A"]

    def test_request_while_pending_raises(self) -> None:
        w = world(line(2), {"A": "N0", "B": "N1"})
        w.ask("A", w.nodes["N1"])
        with pytest.raises(RuntimeError, match="A already has a pending"):
            w.ask("A", w.nodes["N1"])

    def test_withdraw_removes_the_claim_and_scans(self) -> None:
        w = world(line(3), {"A": "N1", "B": "N0", "C": "N2"})
        first = w.ask("B", w.nodes["N1"])
        w.controller.withdraw(first)
        assert w.controller.view.pending(w.nodes["N1"]) == 0
        w.controller.withdraw(first)  # not pending: nothing happens
        w.controller.withdraw(w.ask("B", w.nodes["N0"]))  # granted at once: nothing to withdraw
        assert w.held("B") == ["N0"]

    def test_a_grant_later_in_a_scan_can_open_the_gate_of_an_earlier_claim(self) -> None:
        # W (first) waits on the corridor and gates F; G (last) is granted J2, which then blocks W by an AGV that does
        # not hold the corridor: the gate opens and F is granted in the same scan.
        w = world(two_junction_corridor(), {"H": "J1", "W": "x2", "F": "x1", "G": "y2"})
        assert w.leg("H", "J1 a b J2").granted
        w.move("H", "a")
        w.release("H", "J1", w.seg("J1", "a"))
        waiter = w.leg("W", "x2 J2 b a J1")
        follower = w.leg("F", "x1 J1 a")
        assert not waiter.event.triggered
        assert not follower.event.triggered  # gated by W
        assert w.leg("G", "y2 J2").granted
        assert follower.granted
        assert not waiter.event.triggered
        grants = [e.agv for e in w.replay.of(TrafficReserved)][-2:]
        assert grants == ["G", "F"]


class TestGate:
    def _waiting(self, *extra: tuple[str, str]) -> World:
        """H holds the corridor forward from ``a``; W waits at ``x2`` to enter it backward."""
        agvs = {"H": "J1", "W": "x2", "F": "x1", **dict(extra)}
        w = world(two_junction_corridor(), agvs)
        assert w.leg("H", "J1 a b J2").granted
        w.move("H", "a")
        w.release("H", "J1", w.seg("J1", "a"))
        return w

    def test_holds_a_later_same_direction_claim(self) -> None:
        w = self._waiting()
        waiter = w.leg("W", "x2 J2 b a J1")
        assert not waiter.event.triggered  # inadmissible on the corridor only
        follower_claim = w.controller.next_claim(w.agvs["F"], [w.nodes[n] for n in ("x1", "J1", "a")], 0)
        assert [item.resource.id for item in follower_claim.items] == ["corridor:a", "J1~x1", "J1"]
        assert not w.controller.view.admissible(w.agvs["F"], follower_claim)
        follower = w.controller.request(w.agvs["F"], follower_claim, fake_hooks())
        assert not follower.event.triggered
        w.controller.withdraw(waiter)
        assert follower.granted

    def test_a_yielding_claim_does_not_gate(self) -> None:
        w = self._waiting()
        w.leg("W", "x2 J2 b a J1")
        w.controller._pending[w.agvs["W"]].yielding = True
        assert w.leg("F", "x1 J1 a").granted

    def test_a_stream_does_not_reopen_the_gate_at_the_waiters_port(self) -> None:
        w = world(two_junction_corridor(), {"H": "J1", "W": "x2", "F": "x1"})
        step(w, "H", "J1 a b J2", 0)
        waiter = w.leg("W", "x2 J2 b a J1")
        follower = w.leg("F", "x1 J1 a b J2")
        assert not waiter.event.triggered
        assert not follower.event.triggered  # gated by W
        step(w, "H", "J1 a b J2", 1)
        step(w, "H", "J1 a b J2", 2)  # H left the corridor onto W's port: it keeps the corridor
        assert w.held("H") == ["corridor:a", "J2"]
        assert not follower.event.triggered
        step(w, "H", "J2 y2", 0)  # H releases the port, and the corridor with it
        assert w.events()[-2] == ("traffic.released", "H", ("corridor:a", "J2", "J2~y2"))
        assert waiter.granted
        assert not follower.event.triggered
        for index in range(4):
            step(w, "W", "x2 J2 b a J1", index)
            assert not follower.event.triggered
        assert w.held("W") == ["corridor:a", "J1"]
        step(w, "W", "J1 y1", 0)
        assert follower.granted

    def test_a_holder_reversing_is_not_gated_by_a_waiter_it_blocks(self) -> None:
        w = self._waiting()
        waiter = w.leg("W", "x2 J2 b a J1")
        assert not waiter.event.triggered
        reversal = w.ask("H", (w.corridor(), "backward"))
        assert reversal.granted
        assert waiter.granted  # now both travel backward

    def test_does_not_hold_when_blocked_by_an_unrelated_agv(self) -> None:
        w = self._waiting(("U", "J2"))
        waiter = w.leg("W", "x2 J2 b a J1")
        assert not waiter.event.triggered
        assert w.leg("F", "x1 J1 a").granted

    def test_an_earlier_claim_admissible_on_the_resource_does_not_gate(self) -> None:
        w = world(line(3), {"A": "N1", "B": "N0", "C": "N2"})
        waiting = w.leg("B", "N0 N1")  # admissible on N0~N1, waits for N1
        assert not waiting.event.triggered
        assert w.ask("C", (w.seg("N0", "N1"), "forward")).granted


# --- release (spec §7.5) -----------------------------------------------------------------------------------


class TestRelease:
    def test_release_and_retain(self) -> None:
        w = world(line(4), {"A": "N0"})
        assert w.leg("A", "N0 N1 N2 N3").granted
        w.move("A", "N1")
        before = len(w.replay.events)
        w.release("A", "N3", "N2")  # not held: nothing happens
        assert len(w.replay.events) == before
        assert w.leg("A", "N0 N1 N2 N3", 1).granted
        w.move("A", "N2")
        w.release("A", w.seg("N0", "N1"), "N0", "N3")
        assert w.held("A") == ["N1", "N1~N2", "N2"]
        w.retain("A", "N2")
        assert w.held("A") == ["N2"]
        assert w.events()[-2:] == [
            ("traffic.released", "A", ("N0", "N0~N1")),  # in holding order
            ("traffic.released", "A", ("N1", "N1~N2")),
        ]
        w.retain("A", "N2")  # nothing to free: no event
        assert w.events()[-1] == ("traffic.released", "A", ("N1", "N1~N2"))

    def test_events_carry_every_resource_and_delta(self) -> None:
        w = world(two_junction_corridor(), {"A": "x1"})
        assert w.leg("A", "x1 J1 a b J2").granted
        reserved = w.replay.of(TrafficReserved)[-1]
        assert reserved.resources == ("corridor:a", "J1~x1", "J1")
        assert reserved.deltas.ops == (
            ("insert", "corridor:a", "reserved_by", 0, "A"),
            ("set", "corridor:a", "orientation", "forward"),
            ("insert", "J1~x1", "backward", 0, "A"),
            ("insert", "J1", "reserved_by", 0, "A"),
        )
        w.move("A", "J1")
        w.retain("A", "J1")
        released = w.replay.of(TrafficReleased)[-1]
        assert released.resources == ("x1", "corridor:a", "J1~x1")
        assert released.deltas.ops == (
            ("remove", "x1", "reserved_by", "A"),
            ("remove", "corridor:a", "reserved_by", "A"),
            ("set", "corridor:a", "orientation", None),
            ("remove", "J1~x1", "backward", "A"),
        )
        assert w.replay.seeded == w.replay.state

    def test_one_event_per_grant_and_per_call(self) -> None:
        w = world(line(3), {"A": "N0", "B": "N2"})
        assert w.leg("A", "N0 N1 N2").granted
        w.move("A", "N1")
        w.release("A", "N0", w.seg("N0", "N1"))
        assert w.events() == [
            ("traffic.reserved", "A", ("N0",)),
            ("traffic.reserved", "B", ("N2",)),
            ("traffic.reserved", "A", ("N0~N1", "N1")),
            ("traffic.released", "A", ("N0", "N0~N1")),
        ]

    def test_a_zone_goes_with_its_last_member(self) -> None:
        graph, nodes = line(5)
        zone = Zone("Z", (nodes["N1"], nodes["N2"]))
        w = world((graph, nodes), {"A": "N0"}, zones=[zone])
        route = "N0 N1 N2 N3 N4"
        for index in range(3):
            assert w.leg("A", route, index).granted
            here, there = route.split()[index : index + 2]
            w.move("A", there)
            w.release("A", here, w.seg(here, there))
            if index < 2:
                assert "Z" in w.held("A")
        assert w.events()[-1] == ("traffic.released", "A", ("Z", "N2", "N2~N3"))
        assert w.held("A") == ["N3"]
        assert w.replay.state["Z"]["reserved_by"] == ()

    def test_a_corridor_left_through_a_port_goes_with_the_port(self) -> None:
        w = world(two_junction_corridor(), {"A": "J1", "B": "x1"})
        for index in range(3):
            step(w, "A", "J1 a b J2", index)
        assert w.held("A") == ["corridor:a", "J2"]
        w.retain("A", "J2")  # the travel ends on the port: the corridor stays
        assert w.held("A") == ["corridor:a", "J2"]
        assert w.leg("A", "J2 y2").granted
        w.move("A", "y2")
        w.release("A", "J2")
        assert w.events()[-1] == ("traffic.released", "A", ("corridor:a", "J2"))
        assert w.replay.state["corridor:a"]["reserved_by"] == ()
        # B ends a travel on the port J1 through retain: the corridor is left onto the port.
        assert w.leg("B", "x1 J1 a b").granted
        step(w, "B", "x1 J1 a b", 0)
        step(w, "B", "x1 J1 a b", 1)
        assert w.leg("B", "a J1", 0).granted  # back out
        w.move("B", "J1")
        w.retain("B", "J1")
        assert w.held("B") == ["corridor:a", "J1"]
        assert w.leg("B", "J1 a").granted  # entering again: held through a member, no longer through the port
        w.move("B", "a")
        w.release("B", "J1")
        assert w.held("B") == ["corridor:a", "J1~a", "a"]

    def test_a_corridor_goes_with_its_last_member_when_the_port_goes_too(self) -> None:
        w = world(two_junction_corridor(), {"C": "x2"})
        step(w, "C", "x2 J2 b", 0)
        step(w, "C", "x2 J2 b", 1)
        assert w.leg("C", "b J2").granted  # back out, the corridor turned forward
        w.move("C", "J2")
        assert w.leg("C", "J2 y2").granted
        w.move("C", "y2")
        w.release("C", "b", w.seg("b", "J2"), "J2")
        assert w.events()[-1] == ("traffic.released", "C", ("corridor:a", "b", "J2~b", "J2"))
        assert w.held("C") == ["J2~y2", "y2"]

    @pytest.mark.parametrize("how", ["release", "retain"])
    def test_a_pending_claim_keeps_its_zone(self, how: str) -> None:
        graph, nodes = line(5)
        zone = Zone("Z", (nodes["N0"], nodes["N2"]))
        w = world((graph, nodes), {"A": "N0", "B": "N4", "C": "N3"}, zones=[zone])
        assert w.held("A") == ["Z", "N0"]
        assert w.ask("C", (w.seg("N1", "N2"), "backward")).granted
        assert w.leg("A", "N0 N1 N2 N3").granted
        w.move("A", "N1")
        look_ahead = w.leg("A", "N0 N1 N2 N3", 1)  # Z, N1~N2, N2: waits for the segment
        assert not look_ahead.event.triggered
        if how == "release":
            w.release("A", "N0", w.seg("N0", "N1"))
        else:
            w.retain("A", "N1")
        assert w.held("A") == ["Z", "N1"]  # the pending claim names Z
        other = w.ask("B", zone)
        assert not other.event.triggered
        w.release("C", w.seg("N1", "N2"))
        assert look_ahead.granted
        assert w.held("A") == ["Z", "N1", "N1~N2", "N2"]
        assert w.replay.state["Z"]["reserved_by"] == ("A",)
        assert not other.event.triggered

    def test_retain_keeps_a_zone_with_a_kept_member(self) -> None:
        graph, nodes = line(3)
        zone = Zone("Z", ((nodes["N0"], nodes["N1"]), nodes["N1"]))
        w = world((graph, nodes), {"A": "N0"}, zones=[zone])
        assert w.leg("A", "N0 N1 N2").granted
        w.move("A", "N1")
        w.retain("A", "N1")
        assert w.held("A") == ["Z", "N1"]

    def test_a_hold_back_corridor_survives_until_entry(self) -> None:
        w = world(two_junction_corridor(), {"A": "x1"})
        assert w.leg("A", "x1 J1 a b J2").granted  # the corridor ahead of entry
        w.move("A", "J1")
        w.release("A", "x1", w.seg("J1", "x1"))
        assert w.held("A") == ["corridor:a", "J1"]
        assert w.leg("A", "x1 J1 a b J2", 1).granted  # entry: a member is taken
        w.move("A", "a")
        w.release("A", "J1")
        assert w.held("A") == ["corridor:a", "J1~a", "a"]
        w.release("A", w.seg("J1", "a"))
        assert w.held("A") == ["corridor:a", "a"]

    def test_retain_drops_a_corridor_held_ahead(self) -> None:
        w = world(two_junction_corridor(), {"A": "x1"})
        assert w.leg("A", "x1 J1 a b J2").granted
        w.move("A", "J1")
        w.release("A", "x1", w.seg("J1", "x1"))
        w.retain("A", "J1")  # the route changed before entry
        assert w.held("A") == ["J1"]
        assert w.events()[-1] == ("traffic.released", "A", ("corridor:a",))
        assert w.replay.state["corridor:a"]["orientation"] is None

    def test_hold_back_zone_is_claimed_a_leg_early(self) -> None:
        graph, nodes = line(4)
        zone = Zone("Z", (nodes["N2"],), hold_back=True)
        w = world((graph, nodes), {"A": "N0"}, zones=[zone])
        assert w.leg("A", "N0 N1 N2 N3").granted
        assert w.held("A") == ["N0", "Z", "N0~N1", "N1"]
        w.move("A", "N1")
        w.release("A", "N0", w.seg("N0", "N1"))
        assert w.held("A") == ["Z", "N1"]  # held ahead: no member yet


# --- lifecycle ---------------------------------------------------------------------------------------------


class TestLifecycle:
    def test_satisfies_the_protocols(self) -> None:
        w = world(line(2), {"A": "N0"})
        assert isinstance(w.controller, TrafficController)
        assert isinstance(w.controller.view, TrafficView)
        assert w.controller.controls_occupancy is True

    def test_place_reports_resource_and_capacity(self) -> None:
        graph, nodes = line(4)
        zone = Zone("Z", (nodes["N2"], nodes["N3"]))
        w = world((graph, nodes), {"A": "N1", "B": "N2", "C": "N1", "D": "N3"}, place=False, zones=[zone])
        w.controller.place(w.agvs["A"], nodes["N1"])
        with pytest.raises(RuntimeError, match=r"cannot place C at node N1: N1 .*\(capacity 1\)"):
            w.controller.place(w.agvs["C"], nodes["N1"])
        w.controller.place(w.agvs["B"], nodes["N2"])
        with pytest.raises(RuntimeError, match=r"cannot place D at node N3: Z .*\(capacity 1\)"):
            w.controller.place(w.agvs["D"], nodes["N3"])
        assert w.holders("N3") == []
        w.controller.place(w.agvs["A"], nodes["N1"])  # placed again: nothing new to hold
        assert w.held("A") == ["N1"]

    def test_placed_inside_a_corridor_holds_it_without_orientation(self) -> None:
        w = world(two_junction_corridor(), {"P": "a", "Q": "b"})
        assert w.held("P") == ["corridor:a", "a"]
        assert w.held("Q") == ["corridor:a", "b"]
        assert w.replay.state["corridor:a"]["orientation"] is None
        assert w.replay.state["corridor:a"]["reserved_by"] == ("P", "Q")
        assert w.events() == [
            ("traffic.reserved", "P", ("corridor:a", "a")),
            ("traffic.reserved", "Q", ("corridor:a", "b")),
        ]

    def test_entities_are_attached_at_activation_in_id_order(self) -> None:
        graph, nodes = two_junction_corridor()
        zone = Zone("B-zone", (nodes["x1"],))
        env = Environment(debug=True)
        controller = ReservationController(env=env, graph=graph, zones=[zone])
        assert not [entity for entity in env.entities.live() if entity.kind in ("segment", "zone")]
        with pytest.raises(RuntimeError, match="activate"):
            controller.network
        env.activate()
        attached = [(entity.kind, entity.id) for entity in env.entities.live()]
        assert attached == [
            ("segment", "J1~a"),
            ("segment", "J1~x1"),
            ("segment", "J1~y1"),
            ("segment", "J2~b"),
            ("segment", "J2~x2"),
            ("segment", "J2~y2"),
            ("segment", "a~b"),
            ("zone", "B-zone"),
            ("zone", "corridor:a"),
        ]
        segment = env.entities.get("J1~a")
        assert isinstance(segment, SegmentBinding)
        assert segment.snapshot() == {
            "source": "J1",
            "target": "a",
            "length": 10.0,
            "forward": [],
            "backward": [],
            "label": "J1~a",
        }
        corridor = env.entities.get("corridor:a")
        assert isinstance(corridor, ZoneBinding)
        assert corridor.snapshot() == {
            "rule": "same_direction",
            "capacity": None,
            "members": ["J1~a", "a", "a~b", "b", "J2~b"],
            "reserved_by": [],
            "orientation": None,
            "label": "corridor:a",
        }
        assert env.entities.get("B-zone").snapshot()["rule"] == "exclusive"

    def test_place_before_activation_raises(self) -> None:
        graph, nodes = line(2)
        env = Environment()
        controller = ReservationController(env=env, graph=graph)
        agv = AGV(env=env, agv_type=cart(), agv_id="A", initial_node=nodes["N0"])
        with pytest.raises(RuntimeError, match="activate"):
            controller.place(agv, nodes["N0"])

    def test_register_fleet_after_activation_raises(self) -> None:
        w = world(line(2), {"A": "N0"})
        with pytest.raises(RuntimeError, match="register_fleet"):
            w.controller.register_fleet(SimpleNamespace(fleet=(), parking_areas=(), charging_stations=()))  # type: ignore[arg-type]

    def test_a_vehicle_type_without_length_cannot_register(self) -> None:
        graph, nodes = line(2)
        env = Environment()
        controller = ReservationController(env=env, graph=graph)
        agv = AGV(env=env, agv_type=cart(length=None, name="tug"), agv_id="T", initial_node=nodes["N0"])
        with pytest.raises(ValueError, match="'tug' has no length"):
            controller.register_fleet(SimpleNamespace(fleet=(agv,), parking_areas=(), charging_stations=()))  # type: ignore[arg-type]
        assert controller._fleets == []

    def test_a_vehicle_without_length_has_no_extent(self) -> None:
        w = world(line(2), {"A": "N0"}, place=False)
        stray = AGV(env=w.env, agv_type=cart(length=None, name="tug"), agv_id="T", initial_node=w.nodes["N1"])
        assert w.ask("A", (w.seg("N0", "N1"), "forward")).granted
        with pytest.raises(ValueError, match="'tug' has no length"):
            w.controller.request(stray, Claim((ClaimItem(w.seg("N0", "N1"), "forward"),), 1), fake_hooks())

    def test_a_node_without_binding_records_nothing(self) -> None:
        w = world(line(3), {"A": "N0"}, bind=False)
        assert w.held("A") == ["N0"]
        assert w.replay.of(TrafficReserved) == []
        assert w.leg("A", "N0 N1 N2").granted
        w.move("A", "N1")
        w.release("A", "N0")  # freed, but nothing to record
        assert w.holders("N0") == []
        w.release("A", w.seg("N0", "N1"))
        assert w.events() == [
            ("traffic.reserved", "A", ("N0~N1",)),
            ("traffic.released", "A", ("N0~N1",)),
        ]
        assert w.holders("N1") == ["A"]

    @pytest.mark.parametrize(
        ("length", "min_gap"), [(-1.0, 0.0), (math.inf, 0.0), (math.nan, 0.0), (1.0, -0.5), (1.0, math.inf)]
    )
    def test_vehicle_type_length_and_gap_are_validated(self, length: float, min_gap: float) -> None:
        with pytest.raises(ValueError, match="'cart'"):
            cart(length=length, min_gap=min_gap)

    def test_facility_capacities_and_warnings(self) -> None:
        graph, nodes = line(4)
        env = Environment(debug=True)
        replay = FullReplay(env)
        area = ParkingArea(env=env, name="P", node=nodes["N1"], capacity=2)
        station = ChargingStation(env=env, name="CS", node=nodes["N1"], n_slots=2)
        small = ParkingArea(env=env, name="Q", node=nodes["N2"], capacity=3)
        controller = ReservationController(env=env, graph=graph, capacities={nodes["N2"]: 1})
        fleet = SimpleNamespace(fleet=(), parking_areas=(area, small), charging_stations=(station,))
        controller.register_fleet(fleet)  # type: ignore[arg-type]
        controller.register_fleet(SimpleNamespace(fleet=(), parking_areas=(area,), charging_stations=()))  # type: ignore[arg-type]
        TrafficInvariants(env, controller)
        warnings: list[Any] = []
        env.bus.subscribe(warnings.append, "**")
        env.activate()
        view = controller.view
        assert [view.capacity(nodes[n]) for n in ("N0", "N1", "N2", "N3")] == [1, 4, 1, 1]  # P counted once
        logged = [(e.message, e.component) for e in warnings if e.type_name == "log"]
        assert logged == [("node N2: capacity 1 is below the capacity 3 of its facilities", "ReservationController")]
        assert replay.events

    def test_layout_graph_is_installed_at_preparation(self) -> None:
        from simulatte import Layout
        from simulatte.layout import Explicit, LayoutNotReady

        env = Environment()
        layout = Layout(env)
        a, b = Node("A", 0, 0), Node("B", 2, 0)
        layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b)])))
        controller = ReservationController(env=env, graph=layout.graph(), capacities={})
        with pytest.raises(LayoutNotReady):
            controller.graph
        prepared = layout.prepare()
        assert controller.graph is prepared.network.graph

    def test_install_layout_graph_takes_explicit_capacities(self) -> None:
        graph, nodes = line(3)
        env = Environment()
        controller = ReservationController(env=env, graph=line(2)[0], capacities={nodes["N2"]: 4})
        network = SimpleNamespace(graph=graph, capacities={nodes["N0"]: 3, nodes["N1"]: None, nodes["N2"]: 2})
        controller._install_layout_graph(network)  # type: ignore[arg-type]
        env.activate()
        assert controller.graph is graph
        assert [controller.view.capacity(nodes[n]) for n in ("N0", "N1", "N2")] == [3, 1, 4]


# --- the view ----------------------------------------------------------------------------------------------


class TestView:
    def test_static_queries(self) -> None:
        graph, nodes = line(3)
        limited = LayoutGraph(
            graph.nodes, [Arc(nodes["N0"], nodes["N1"], speed_limit=1.5), Arc(nodes["N1"], nodes["N2"])]
        )
        w = world((limited, nodes), {"A": "N0"})
        view = w.controller.view
        n0, n1, n2 = nodes["N0"], nodes["N1"], nodes["N2"]
        assert view.graph is limited
        assert view.length(n0, n1) == 10.0
        assert view.speed_limit(n0, n1) == 1.5
        assert view.speed_limit(n1, n2) is None
        with pytest.raises(ValueError, match="not connected"):
            view.speed_limit(n0, n2)
        assert view.capacity(n1) == 1
        assert view.resources(n0, n1) == (w.seg("N0", "N1"), n1)
        assert view.mean_hold(n1) == 0.0

    def test_dynamic_queries(self) -> None:
        w = world(line(3), {"A": "N1", "B": "N0"})
        view = w.controller.view
        a, b = w.agvs["A"], w.agvs["B"]
        assert view.holders(w.nodes["N1"]) == (a,)
        assert view.holders(w.nodes["N2"]) == ()
        assert view.route_of(b) == ()
        request = w.leg("B", "N0 N1 N2")
        assert view.route_of(b) == (w.nodes["N0"], w.nodes["N1"], w.nodes["N2"])
        assert view.pending(w.seg("N0", "N1")) == 1
        assert view.disposition(b) is Disposition.IDLE
        w.controller.stopped(request)
        assert view.disposition(b) is Disposition.WAITING
        w.controller.stopped(ClaimRequest(a, request.claim, request.hooks, w.env.event()))  # not pending: ignored
        a._state = AGVState.TRAVELING_EMPTY
        assert view.disposition(a) is Disposition.MOVING
        assert not view.admissible(b, request.claim)

    def test_admissible_ranks_a_new_claim_by_priority(self) -> None:
        priorities = {"H": 0.0, "W": 0.0, "F": 0.0}
        w = world(
            two_junction_corridor(), {"H": "J1", "W": "x2", "F": "x1"}, priority_fn=lambda agv: priorities[agv.id]
        )
        assert w.leg("H", "J1 a b J2").granted
        w.move("H", "a")
        w.release("H", "J1", w.seg("J1", "a"))
        w.leg("W", "x2 J2 b a J1")
        claim = w.controller.next_claim(w.agvs["F"], [w.nodes[n] for n in ("x1", "J1", "a")], 0)
        assert not w.controller.view.admissible(w.agvs["F"], claim)
        priorities["F"] = 1.0  # ahead of W: W is not an earlier claim
        assert w.controller.view.admissible(w.agvs["F"], claim)


def test_route_claims_end_to_end_on_a_line() -> None:
    """Two AGVs on a line, one behind the other, each releasing behind it: the follower waits one node back."""
    w = world(line(4), {"A": "N1", "B": "N0"})
    route_a, route_b = "N1 N2 N3", "N0 N1 N2"
    behind = w.leg("B", route_b)
    assert not behind.event.triggered
    assert w.leg("A", route_a).granted
    w.move("A", "N2")
    w.release("A", "N1", w.seg("N1", "N2"))
    assert behind.granted
    assert w.held("B") == ["N0", "N0~N1", "N1"]
    assert w.replay.seeded == w.replay.state
