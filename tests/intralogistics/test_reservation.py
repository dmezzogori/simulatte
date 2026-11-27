"""``ReservationController``: admission (spec §7.3), queue and gate (§7.4), release (§7.5), lifecycle (§7.1, §7.7).

Every test builds its world with :func:`world`, which runs :class:`FullReplay` and :class:`TrafficInvariants` over
every event. Tests drive the controller directly: an AGV "moves" by setting ``current_node`` to a node it holds.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from simulatte._wire import freeze
from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.events import Deltas
from simulatte.intralogistics.events import AgvMoveStarted, TrafficReleased, TrafficReserved
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
    declare,
    fake_hooks,
    line,
    loop_with_side_node,
    t_layout,
    two_junction_corridor,
)
from tests.intralogistics.traffic_support import World as Declared


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
    declared: Declared | None = None
    """The graph declared on the layout."""

    def seg(self, a: str, b: str) -> Segment:
        return self.controller.network.segment(self.nodes[a], self.nodes[b])

    def corridor(self) -> Corridor:
        (corridor,) = self.controller.network.corridors
        return corridor

    def zone(self, zone_id: str) -> Zone:
        """The zone `zone_id` of the network, declared with the graph."""
        (zone,) = [zone for zone in self.controller.network.zones if zone.id == zone_id]
        return zone

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
        return [resource.id for resource in self.controller.ledger.held(self.agvs[agv])]

    def holders(self, resource: Any) -> list[str]:
        return [agv.id for agv in self.controller.view.holders(self._resource(resource))]

    def events(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [(e.type_name, e.agv, e.resources) for e in self.replay.of(TrafficReserved, TrafficReleased)]


def world(
    shape: Callable[[Environment], Declared],
    agvs: dict[str, str | tuple[str, AGVType]],
    *,
    place: bool = True,
    **options: Any,
) -> World:
    """An activated world: `agvs` maps each AGV id to its start node id (or ``(node id, type)``), all placed. `shape`
    declares the graph on the environment's layout (a builder of ``traffic_support`` or of this file, its other
    arguments bound), with the capacities and zones of the network; `options` go to the ``ReservationController``.
    Every node is bound, as the layout binds them; ``World.nodes`` holds the canonical nodes."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    declared = shape(env)
    controller = ReservationController(env=env, graph=declared.graph, **options)
    vehicles: dict[str, AGV] = {}
    for name, start in agvs.items():
        node, agv_type = (start, cart()) if isinstance(start, str) else start
        vehicles[name] = AGV(env=env, agv_type=agv_type, agv_id=name, initial_node=declared.ref(node))
    graph = declared.canonical_graph()  # prepares the layout: the fleet's graph is the resolved one
    fleet = SimpleNamespace(fleet=tuple(vehicles.values()), parking_areas=(), charging_stations=(), graph=graph)
    controller.register_fleet(fleet)  # type: ignore[arg-type]
    invariants = TrafficInvariants(env, controller)
    if place:
        env.on_activate(lambda: [controller.place(agv, agv.current_node) for agv in vehicles.values()])
    env.activate()
    return World(env, controller, {node.id: node for node in graph.nodes}, vehicles, replay, invariants, declared)


def step(w: World, agv: str, route: str, index: int) -> None:
    """Leg `index` of `route` for an extent-0 vehicle: granted at once, the AGV arrives and frees what is behind."""
    nodes = route.split()
    here, there = nodes[index], nodes[index + 1]
    assert w.leg(agv, route, index).granted, (agv, route, index)
    w.move(agv, there)
    w.release(agv, here, w.seg(here, there))


def passing_line(env: Environment) -> Declared:
    """``N0, N1, N2`` with a passing arc between ``N1`` and ``N2``."""
    nodes = {name: Node(name, 10 * i, 0) for i, name in enumerate(("N0", "N1", "N2"))}
    arcs = [Arc(nodes["N0"], nodes["N1"]), Arc(nodes["N1"], nodes["N2"], passing=True)]
    return declare(env, LayoutGraph(nodes.values(), arcs))


# --- admission (spec §7.3) ---------------------------------------------------------------------------------


class TestAdmission:
    def test_full_node_pends_and_is_granted_on_release(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0"})
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
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0"})
        w.leg("B", "N0 N1")  # the segment is free, the node is not
        assert w.holders(w.seg("N0", "N1")) == []
        assert w.held("B") == ["N0"]
        assert w.replay.state["N0~N1"]["forward"] == ()

    def test_opposite_direction_on_a_single_lane_pends(self) -> None:
        w = world(lambda env: line(env, 4), {"A": "N1", "B": "N3"})
        assert w.ask("A", (w.seg("N1", "N2"), "forward")).granted
        waiting = w.ask("B", (w.seg("N1", "N2"), "backward"))
        assert not waiting.event.triggered
        w.release("A", w.seg("N1", "N2"))
        assert waiting.granted
        assert w.replay.state["N1~N2"]["backward"] == ("B",)

    def test_same_direction_admitted_while_extents_fit(self) -> None:
        w = world(lambda env: line(env, 2), {"A": "N0", "B": "N0", "C": "N0"}, place=False)
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
        w = world(
            lambda env: line(env, 2),
            {"L": ("N0", cart(length=15.0)), "M": ("N0", cart(length=1.0, min_gap=0.5))},
            place=False,
        )
        segment = w.seg("N0", "N1")
        assert w.ask("L", (segment, "forward")).granted
        assert not w.ask("M", (segment, "forward")).event.triggered

    def test_min_gap_counts_in_the_extent(self) -> None:
        long_gap = cart(length=4.0, min_gap=2.5)
        w = world(lambda env: line(env, 2), {"A": ("N0", long_gap), "B": ("N0", long_gap)}, place=False)
        segment = w.seg("N0", "N1")
        assert w.ask("A", (segment, "forward")).granted
        assert not w.ask("B", (segment, "forward")).event.triggered  # 6.5 + 6.5 > 10

    def test_passing_admits_both_directions(self) -> None:
        big = cart(length=8.0)
        agvs: dict[str, str | tuple[str, AGVType]] = {"A": ("N1", big), "B": ("N2", big), "C": ("N1", cart(4.0))}
        w = world(passing_line, agvs, place=False)
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
        zones = {"Z": {"members": ["N1", ("N2", "N3")], "capacity": 2}}
        w = world(lambda env: line(env, 4, zones=zones), {"A": "N0", "B": "N0", "C": "N0"}, place=False)
        zone = w.zone("Z")
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
        w = world(two_junction_corridor, {"A": "x1", "B": "x2", "C": "y1"})
        corridor = w.corridor()
        assert corridor.id == "corridor:a"
        assert w.ask("A", (corridor, "forward")).granted
        assert w.ask("C", (corridor, "forward")).granted
        assert not w.ask("B", (corridor, "backward")).event.triggered
        assert w.replay.state["corridor:a"]["orientation"] == "forward"
        assert w.replay.state["corridor:a"]["reserved_by"] == ("A", "C")

    def test_an_unoriented_holder_blocks_nobody(self) -> None:
        w = world(two_junction_corridor, {"P": "a", "X": "x2"})
        corridor = w.corridor()
        assert w.holders(corridor) == ["P"]
        assert w.ask("X", (corridor, "backward")).granted
        assert w.replay.state["corridor:a"]["orientation"] == "backward"

    def test_a_holder_sets_its_orientation_when_no_other_holder_opposes_it(self) -> None:
        w = world(two_junction_corridor, {"P": "a"})
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
        w = world(two_junction_corridor, {"P": "a", "X": "x2"})
        corridor = w.corridor()
        assert w.ask("X", (corridor, "backward")).granted
        assert not w.ask("P", (corridor, "forward")).event.triggered

    def test_reversal_with_a_same_orientation_co_holder_pends(self) -> None:
        w = world(two_junction_corridor, {"A": "x1", "C": "y1"})
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
    w = world(lambda env: line(env, 4), {"A": "N1", "B": "N3"})
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
        w = world(lambda env: line(env, 4), {"A": "N1"})
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
        w = world(lambda env: line(env, 4), {"A": "N1", "F": "N0"})
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
        w = world(lambda env: line(env, 4), {"A": "N1", "N": "N0"})
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted
        assert not w.ask("N", (segment, "forward")).event.triggered

    def test_on_a_passing_segment_the_reversal_needs_room_in_the_new_lane(self) -> None:
        agvs: dict[str, str | tuple[str, AGVType]] = {"A": "N1", "F": "N1", "B": "N2", "C": "N2"}
        w = world(passing_line, agvs, place=False)
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("F", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted  # F still forward: the backward lane is empty
        assert w.replay.state["N1~N2"]["forward"] == ("F",)
        assert w.replay.state["N1~N2"]["backward"] == ("A",)
        assert w.ask("B", (segment, "backward")).granted  # 4 + 4 fit
        assert w.ask("F", (segment, "backward")).granted is False  # 4 + 4 + 4 do not
        assert w.replay.state["N1~N2"]["forward"] == ("F",)


class TestBothDirections:
    """A single-lane segment claimed again in a new direction is held in both directions by its holder (spec §7.2,
    "Both directions"): no other claimant is admissible on it until ``cleared`` or a release ends the state."""

    @staticmethod
    def reversed_on(w: World, agv: str, a: str, b: str) -> Segment:
        """`agv` holds the segment ``a~b`` from ``a`` to ``b`` and claims it again from ``b`` to ``a``."""
        segment = w.seg(a, b)
        forward = "forward" if a < b else "backward"
        backward = "backward" if forward == "forward" else "forward"
        assert w.ask(agv, (segment, forward)).granted
        assert w.ask(agv, (segment, backward)).granted
        return segment

    @staticmethod
    def waiting(w: World, agv: str) -> Any:
        return w.controller._pending[w.agvs[agv]]

    def test_the_holder_counts_in_both_directions_for_every_other_claimant(self) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3", "C": "N0"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        ledger, a = w.controller.ledger, w.agvs["A"]
        assert ledger.both_directions(segment) == (a,)
        assert ledger.direction(segment, a) == "backward"
        assert w.replay.state["N1~N2"]["forward"] == () and w.replay.state["N1~N2"]["backward"] == ("A",)
        backward = Claim((ClaimItem(segment, "backward"),), 1)
        view = w.controller.view
        assert view.admissible(a, backward)  # its own holdings never block it
        assert not view.admissible(w.agvs["B"], backward)  # the extents 4 + 4 fit in 10: only the state refuses
        assert view.blockers(w.agvs["B"], w.nodes["N2"], w.nodes["N1"]) == ((segment, (a,)),)
        assert not w.ask("B", (segment, "backward")).event.triggered
        assert not w.ask("C", (segment, "forward")).event.triggered
        assert ledger.both_directions(w.nodes["N2"]) == ()

    def test_a_first_claim_a_passing_segment_and_a_corridor_start_no_state(self) -> None:
        w = world(lambda env: line(env, 4), {"A": "N1"})
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.controller.ledger.both_directions(segment) == ()
        w = world(passing_line, {"A": "N1", "B": "N2"}, place=False)
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        assert w.ask("A", (segment, "backward")).granted
        assert w.controller.ledger.both_directions(segment) == ()
        assert w.ask("B", (segment, "backward")).granted  # 4 + 4 fit in the backward lane
        w = world(two_junction_corridor, {"A": "J1"})
        corridor = w.corridor()
        assert w.ask("A", (corridor, "forward")).granted
        assert w.ask("A", (corridor, "backward")).granted
        assert w.controller.ledger.both_directions(corridor) == ()

    def test_cleared_ends_the_state_and_grants_the_wait_it_blocked_without_a_release(self) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        request = w.ask("B", (segment, "backward"))
        w.controller.stopped(request)
        before = len(w.replay.events)
        w.controller.cleared(w.agvs["A"], [w.nodes["N2"], segment])
        assert request.granted
        assert w.controller.ledger.both_directions(segment) == ()
        assert [(e.type_name, e.agv) for e in w.replay.events[before:]] == [
            ("traffic.wait_ended", "B"),
            ("traffic.reserved", "B"),
        ]
        assert w.holders(segment) == ["A", "B"]

    def test_cleared_of_resources_without_the_state_does_nothing(self) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        request = w.ask("B", (segment, "backward"))
        changes, before = w.controller._changes, len(w.replay.events)
        w.controller.cleared(w.agvs["B"], [segment, w.seg("N2", "N3")])  # B is not in the state
        w.controller.cleared(w.agvs["A"], [w.nodes["N2"], w.seg("N2", "N3")])
        assert not request.event.triggered
        assert w.controller.ledger.both_directions(segment) == (w.agvs["A"],)
        assert (w.controller._changes, len(w.replay.events)) == (changes, before)

    def test_a_release_ends_the_state(self) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        request = w.ask("B", (segment, "backward"))
        w.release("A", segment)
        assert w.controller.ledger.both_directions(segment) == ()
        assert request.granted

    def test_a_retain_that_drops_the_segment_ends_the_state_and_one_that_keeps_it_keeps_it(self) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        request = w.ask("B", (segment, "backward"))
        w.retain("A", "N2", segment)
        assert w.controller.ledger.both_directions(segment) == (w.agvs["A"],)
        assert not request.event.triggered
        w.retain("A", "N2")
        assert w.controller.ledger.both_directions(segment) == ()
        assert request.granted

    def test_an_end_that_blocked_no_pending_claim_moves_no_review_timer(self) -> None:
        """B waits for N3, held by D; the states of A and E block nothing. Their ends count for nothing (Ruling A3-e):
        B's review timer stays the same timer, and once its review found nothing changed it stays asleep."""
        w = world(lambda env: line(env, 6), {"A": "N2", "E": "N4", "D": "N3", "B": "N0"})
        first = self.reversed_on(w, "A", "N1", "N2")
        second = self.reversed_on(w, "E", "N4", "N5")
        request = w.ask("B", w.nodes["N3"])
        w.controller.stopped(request)
        waiting = self.waiting(w, "B")
        timer, changes = waiting.timer, w.controller._changes
        assert timer is not None
        w.env.run(until=10.0)
        w.controller.cleared(w.agvs["A"], [first])
        assert (waiting.timer, waiting.seen, w.controller._changes) == (timer, changes, changes)
        w.env.run(until=40.0)  # the review at 30 found nothing changed: asleep
        assert (waiting.timer, waiting.dormant, waiting.reviews) == (None, 30.0, 1)
        w.controller.cleared(w.agvs["E"], [second])
        assert (waiting.timer, waiting.dormant, w.controller._changes) == (None, 30.0, changes)
        assert w.controller.ledger.both_directions(second) == ()

    def test_a_wait_blocked_by_the_state_and_by_another_holder_is_woken_by_the_end_and_reviewed(self) -> None:
        """B needs ``N1~N2`` backward, refused by A's state, and N0, held by D. A is travelling (it turns): the review
        at 30 finds nothing changed and B sleeps, not stuck. At 40 A is idle again and its state ends: the end wakes
        the wait as a release would, the review at 70 finds B blocked by the idle D alone and reports it stuck."""
        w = world(lambda env: line(env, 5), {"A": "N2", "D": "N0", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        a = w.agvs["A"]
        a.transition_to(AGVState.TRAVELING_EMPTY)
        request = w.ask("B", (segment, "backward"), w.nodes["N0"])
        w.controller.stopped(request)
        (started,) = [e for e in w.replay.events if e.type_name == "traffic.wait_started"]
        assert started.blockers == ("A", "D")
        waiting = self.waiting(w, "B")
        w.env.run(until=40.0)
        assert (waiting.timer, waiting.dormant, waiting.stuck) == (None, 30.0, False)
        a.transition_to(AGVState.IDLE)
        w.controller.cleared(a, [segment])
        assert waiting.timer is not None and waiting.dormant is None
        w.env.run()
        assert w.env.now == 70.0
        (stuck,) = [e for e in w.replay.events if e.type_name == "traffic.wait_stuck"]
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (70.0, "B", {"D": "IDLE"})
        assert waiting.reviews == 2 and not request.event.triggered

    def test_a_waiting_holder_in_the_state_closes_a_deadlock(self) -> None:
        """A holds ``N1~N2`` in the state and waits for N3, held by B; B waits for ``N1~N2`` backward, which only the
        state refuses. Detection reads the state among the members: the two are deadlocked."""
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        mine = w.ask("A", w.nodes["N3"])
        w.controller.stopped(mine)
        theirs = w.ask("B", (segment, "backward"))
        w.controller.stopped(theirs)
        (deadlock,) = [e for e in w.replay.events if e.type_name == "traffic.deadlock"]
        assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), None, "unresolved")
        assert not mine.event.triggered and not theirs.event.triggered

    @pytest.mark.parametrize("disposition", ["IDLE", "STRANDED", "STALLED"])
    def test_a_wait_blocked_only_by_a_holder_at_rest_in_the_state_is_reported_stuck(self, disposition: str) -> None:
        w = world(lambda env: line(env, 5), {"A": "N2", "B": "N3"})
        segment = self.reversed_on(w, "A", "N1", "N2")
        a = w.agvs["A"]
        if disposition == "STRANDED":
            a.transition_to(AGVState.STRANDED)
        elif disposition == "STALLED":
            a.transition_to(AGVState.TRAVELING_EMPTY)
            motion = freeze({"from": "N2", "to": "N1", "t_start": 0.0, "t_end": math.inf, "stalled": True})
            a.motion = motion
            moved = Deltas.build().set("A", "motion", motion).done()
            w.env.emit(
                AgvMoveStarted(
                    agv="A", from_node="N2", to_node="N1", t_end=math.inf, motion=motion, loaded=False, deltas=moved
                )
            )
        request = w.ask("B", (segment, "backward"))
        w.controller.stopped(request)
        w.env.run()
        (stuck,) = [e for e in w.replay.events if e.type_name == "traffic.wait_stuck"]
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (30.0, "B", {"A": disposition})


def test_own_holdings_never_block_a_long_vehicle() -> None:
    w = world(lambda env: line(env, 4), {"A": ("N0", cart(length=12.0))})
    for index, there in enumerate(("N1", "N2", "N3")):
        assert w.leg("A", "N0 N1 N2 N3", index).granted
        w.move("A", there)
        if index == 1:  # extent 12 on arcs of 10: both segments behind the front are still held
            assert w.held("A") == ["N0", "N0~N1", "N1", "N1~N2", "N2"]
            w.release("A", "N0", w.seg("N0", "N1"))
    assert w.held("A") == ["N1", "N1~N2", "N2", "N2~N3", "N3"]


# --- order and gate (spec §7.4) ----------------------------------------------------------------------------


def star(env: Environment) -> Declared:
    """A hub ``H`` with the leaves ``L1 ... L5``."""
    names = [f"L{i}" for i in range(1, 6)]
    return build(env, [("H", 0, 0), *((name, 10 * i, 10) for i, name in enumerate(names))], [("H", n) for n in names])


class TestOrder:
    def test_priority_then_request_order(self) -> None:
        priorities = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 1.0}
        reads: list[str] = []

        def priority(agv: AGV) -> float:
            reads.append(agv.id)
            return priorities[agv.id]

        w = world(star, {"A": "H", "B": "L1", "C": "L2", "D": "L3"}, priority_fn=priority)
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
        w = world(lambda env: line(env, 2), {"A": "N0", "B": "N1"})
        w.ask("A", w.nodes["N1"])
        with pytest.raises(RuntimeError, match="A already has a pending"):
            w.ask("A", w.nodes["N1"])

    def test_withdraw_removes_the_claim_and_scans(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0", "C": "N2"})
        first = w.ask("B", w.nodes["N1"])
        w.controller.withdraw(first)
        assert w.controller.view.pending(w.nodes["N1"]) == 0
        w.controller.withdraw(first)  # not pending: nothing happens
        w.controller.withdraw(w.ask("B", w.nodes["N0"]))  # granted at once: nothing to withdraw
        assert w.held("B") == ["N0"]

    def test_a_grant_later_in_a_scan_can_open_the_gate_of_an_earlier_claim(self) -> None:
        # W (first) waits on the corridor and gates F; G (last) is granted J2, which then blocks W by an AGV that does
        # not hold the corridor: the gate opens and F is granted in the same scan.
        w = world(two_junction_corridor, {"H": "J1", "W": "x2", "F": "x1", "G": "y2"})
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
        w = world(two_junction_corridor, agvs)
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
        w = world(two_junction_corridor, {"H": "J1", "W": "x2", "F": "x1"})
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
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0", "C": "N2"})
        waiting = w.leg("B", "N0 N1")  # admissible on N0~N1, waits for N1
        assert not waiting.event.triggered
        assert w.ask("C", (w.seg("N0", "N1"), "forward")).granted


# --- release (spec §7.5) -----------------------------------------------------------------------------------


class TestRelease:
    def test_release_and_retain(self) -> None:
        w = world(lambda env: line(env, 4), {"A": "N0"})
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
        w = world(two_junction_corridor, {"A": "x1"})
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
        w = world(lambda env: line(env, 3), {"A": "N0", "B": "N2"})
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
        w = world(lambda env: line(env, 5, zones={"Z": {"members": ["N1", "N2"]}}), {"A": "N0"})
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
        w = world(two_junction_corridor, {"A": "J1", "B": "x1"})
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
        # B leaves onto the port J1 by a release: an exit record.
        step(w, "B", "x1 J1 a", 0)
        step(w, "B", "x1 J1 a", 1)
        assert w.leg("B", "a J1").granted  # back out
        w.move("B", "J1")
        w.release("B", "a", w.seg("J1", "a"))
        assert w.held("B") == ["corridor:a", "J1"]
        assert w.controller.ledger.exits(w.agvs["B"]) == {w.corridor(): w.nodes["J1"]}
        # Entering again deletes the record, so a cancelled entry drops the corridor.
        assert w.leg("B", "J1 a").granted
        assert w.controller.ledger.exits(w.agvs["B"]) == {}
        w.retain("B", "J1")
        assert w.held("B") == ["J1"]

    @pytest.mark.parametrize("end", ["moves_on", "travel_ends"])
    def test_the_exit_record_on_a_port_of_capacity_two(self, end: str) -> None:
        """spec §7.5: A leaves the corridor onto J2 (capacity 2) by the release of its last member, which creates an
        exit record; W, held back before J2 by the corridor A holds forward, keeps waiting while A waits on J2 for its
        next claim. The record goes with the port when A moves on (one event), or, while A keeps J2, by the ``retain``
        that ends A's travel there, which frees the corridor."""
        w = world(lambda env: two_junction_corridor(env, capacities={"J2": 2}), {"A": "J1", "B": "x1", "W": "x2"})
        nodes = w.nodes
        a = w.agvs["A"]
        step(w, "A", "J1 a b J2", 0)
        step(w, "A", "J1 a b J2", 1)
        waiter = w.leg("W", "x2 J2 b a J1")  # corridor, J2~x2, J2: the corridor is held forward by A
        assert not waiter.event.triggered
        step(w, "A", "J1 a b J2", 2)  # A leaves onto J2: an exit record
        assert w.events()[-1] == ("traffic.released", "A", ("b", "J2~b"))
        assert w.held("A") == ["corridor:a", "J2"]
        assert w.controller.ledger.exits(a) == {w.corridor(): nodes["J2"]}
        assert not waiter.event.triggered
        if end == "moves_on":
            assert w.ask("B", nodes["y2"]).granted  # y2 is taken: A waits on J2 for its next claim
            onward = w.leg("A", "J2 y2")
            assert not onward.event.triggered
            assert w.controller.ledger.exits(a) == {w.corridor(): nodes["J2"]}
            assert not waiter.event.triggered
            w.release("B", "y2")
            assert onward.granted
            w.move("A", "y2")
            w.release("A", "J2", w.seg("J2", "y2"))
            assert w.events()[-2:] == [
                ("traffic.released", "A", ("corridor:a", "J2", "J2~y2")),
                ("traffic.reserved", "W", ("corridor:a", "J2~x2", "J2")),
            ]
        else:
            w.retain("A", "J2")  # the travel ends on J2: the record goes, and the corridor with it
            assert w.held("A") == ["J2"]
            assert w.events()[-2:] == [
                ("traffic.released", "A", ("corridor:a",)),
                ("traffic.reserved", "W", ("corridor:a", "J2~x2", "J2")),
            ]
        assert w.controller.ledger.exits(a) == {}
        assert waiter.granted

    def test_retain_at_the_entry_port_drops_the_corridor(self) -> None:
        w = world(two_junction_corridor, {"A": "x1"})
        step(w, "A", "x1 J1 a b J2", 0)
        assert w.leg("A", "x1 J1 a b J2", 1).granted  # the first leg into the corridor
        w.retain("A", "J1")  # cancelled before departure: members go, the port stays, no exit record
        assert w.held("A") == ["J1"]
        assert w.events()[-1] == ("traffic.released", "A", ("corridor:a", "J1~a", "a"))
        assert w.controller.ledger.exits(w.agvs["A"]) == {}

    @pytest.mark.parametrize("variant", ["withdrawn", "granted"])
    def test_an_exit_record_goes_with_its_port_under_a_pending_claim(self, variant: str) -> None:
        w = world(loop_with_side_node, {"A": "J1", "X": "S1", "W": "S2"})
        a = w.agvs["A"]
        step(w, "A", "J1 U J2", 0)
        step(w, "A", "J1 U J2", 1)
        w.retain("A", "J2")
        assert w.held("A") == ["corridor:U", "J2"]
        assert w.ask("X", w.nodes["J1"]).granted  # X stands on J1
        w.move("X", "J1")
        w.release("X", "S1")
        assert w.leg("A", "J2 L J1 U", 0).granted
        look_ahead = w.leg("A", "J2 L J1 U", 1)  # names corridor:U, waits for J1
        assert not look_ahead.event.triggered
        w.move("A", "L")
        w.release("A", "J2", w.seg("J2", "L"))  # the port goes with its record; the pending claim keeps the corridor
        assert w.held("A") == ["corridor:U", "corridor:L", "L"]
        assert w.controller.ledger.exits(a) == {}
        if variant == "withdrawn":
            w.controller.withdraw(look_ahead)
            w.retain("A", "L")
            assert w.held("A") == ["corridor:L", "L"]
        else:
            assert w.ask("X", w.nodes["S1"]).granted
            w.move("X", "S1")
            w.release("X", "J1")
            assert look_ahead.granted
            w.move("A", "J1")
            w.retain("A", "J1")  # the travel ends before entering U
            assert w.held("A") == ["J1"]
        (corridor_u,) = [c for c in w.controller.network.corridors if c.id == "corridor:U"]
        assert w.holders(corridor_u) == []
        assert w.replay.state["corridor:U"]["orientation"] is None
        assert w.controller.ledger.exits(a) == {}
        assert w.leg("W", "S2 J2 U J1").granted

    def test_a_claim_is_determined_when_granted(self) -> None:
        w = world(lambda env: line(env, 5, zones={"Z": {"members": ["N0", "N2"]}}), {"A": "N0", "B": "N4", "C": "N3"})
        zone = w.zone("Z")
        assert w.ask("C", (w.seg("N1", "N2"), "backward")).granted
        assert w.leg("A", "N0 N1 N2 N3").granted
        w.move("A", "N1")
        pending = w.leg("A", "N0 N1 N2 N3", 1)  # Z, N1~N2, N2; Z held
        w.release("A", "N0", w.seg("N0", "N1"))
        w.release("A", zone)  # released on purpose while the claim waits
        assert w.ask("B", zone).granted
        w.release("C", w.seg("N1", "N2"))
        assert not pending.event.triggered  # it needs Z again, and B holds it
        w.release("B", zone)
        assert pending.granted
        assert w.events()[-1] == ("traffic.reserved", "A", ("Z", "N1~N2", "N2"))

    def test_a_corridor_goes_with_its_last_member_when_the_port_goes_too(self) -> None:
        w = world(two_junction_corridor, {"C": "x2"})
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
        w = world(lambda env: line(env, 5, zones={"Z": {"members": ["N0", "N2"]}}), {"A": "N0", "B": "N4", "C": "N3"})
        zone = w.zone("Z")
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
        w = world(lambda env: line(env, 3, zones={"Z": {"members": [("N0", "N1"), "N1"]}}), {"A": "N0"})
        assert w.leg("A", "N0 N1 N2").granted
        w.move("A", "N1")
        w.retain("A", "N1")
        assert w.held("A") == ["Z", "N1"]

    def test_a_hold_back_corridor_survives_until_entry(self) -> None:
        w = world(two_junction_corridor, {"A": "x1"})
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
        w = world(two_junction_corridor, {"A": "x1"})
        assert w.leg("A", "x1 J1 a b J2").granted
        w.move("A", "J1")
        w.release("A", "x1", w.seg("J1", "x1"))
        w.retain("A", "J1")  # the route changed before entry
        assert w.held("A") == ["J1"]
        assert w.events()[-1] == ("traffic.released", "A", ("corridor:a",))
        assert w.replay.state["corridor:a"]["orientation"] is None

    def test_hold_back_zone_is_claimed_a_leg_early(self) -> None:
        w = world(lambda env: line(env, 4, zones={"Z": {"members": ["N2"], "hold_back": True}}), {"A": "N0"})
        assert w.leg("A", "N0 N1 N2 N3").granted
        assert w.held("A") == ["N0", "Z", "N0~N1", "N1"]
        w.move("A", "N1")
        w.release("A", "N0", w.seg("N0", "N1"))
        assert w.held("A") == ["Z", "N1"]  # held ahead: no member yet


# --- lifecycle ---------------------------------------------------------------------------------------------


class TestLifecycle:
    def test_satisfies_the_protocols(self) -> None:
        w = world(lambda env: line(env, 2), {"A": "N0"})
        assert isinstance(w.controller, TrafficController)
        assert isinstance(w.controller.view, TrafficView)
        assert w.controller.controls_occupancy is True

    def test_place_reports_resource_and_capacity(self) -> None:
        w = world(
            lambda env: line(env, 4, zones={"Z": {"members": ["N2", "N3"]}}),
            {"A": "N1", "B": "N2", "C": "N1", "D": "N3"},
            place=False,
        )
        nodes = w.nodes
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
        w = world(two_junction_corridor, {"P": "a", "Q": "b"})
        assert w.held("P") == ["corridor:a", "a"]
        assert w.held("Q") == ["corridor:a", "b"]
        assert w.replay.state["corridor:a"]["orientation"] is None
        assert w.replay.state["corridor:a"]["reserved_by"] == ("P", "Q")
        assert w.events() == [
            ("traffic.reserved", "P", ("corridor:a", "a")),
            ("traffic.reserved", "Q", ("corridor:a", "b")),
        ]

    def test_entities_are_attached_at_activation_in_id_order(self) -> None:
        env = Environment(debug=True)
        declared = two_junction_corridor(env, zones={"B-zone": {"members": ["x1"]}})
        controller = ReservationController(env=env, graph=declared.graph)
        assert not [entity for entity in env.entities.live() if entity.kind in ("segment", "zone")]
        with pytest.raises(RuntimeError, match="activate"):
            controller.network
        env.activate()
        attached = [(entity.kind, entity.id) for entity in env.entities.live()]
        assert attached == [
            *(("node", node) for node in ("J1", "J2", "a", "b", "x1", "x2", "y1", "y2")),  # bound at preparation
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
        env = Environment()
        declared = line(env, 2)
        controller = ReservationController(env=env, graph=declared.graph)
        agv = AGV(env=env, agv_type=cart(), agv_id="A", initial_node=declared.ref("N0"))
        with pytest.raises(RuntimeError, match="activate"):
            controller.place(agv, declared.node("N0"))  # prepared, not activated

    def test_register_fleet_after_activation_raises(self) -> None:
        w = world(lambda env: line(env, 2), {"A": "N0"})
        with pytest.raises(RuntimeError, match="register_fleet"):
            w.controller.register_fleet(SimpleNamespace(fleet=(), parking_areas=(), charging_stations=()))  # type: ignore[arg-type]

    def test_a_vehicle_type_without_length_cannot_register(self) -> None:
        env = Environment()
        declared = line(env, 2)
        controller = ReservationController(env=env, graph=declared.graph)
        agv = AGV(env=env, agv_type=cart(length=None, name="tug"), agv_id="T", initial_node=declared.ref("N0"))
        with pytest.raises(ValueError, match="'tug' has no length"):
            controller.register_fleet(SimpleNamespace(fleet=(agv,), parking_areas=(), charging_stations=()))  # type: ignore[arg-type]
        assert controller._fleets == []

    def test_a_vehicle_without_length_has_no_extent(self) -> None:
        w = world(lambda env: line(env, 2), {"A": "N0"}, place=False)
        stray = AGV(env=w.env, agv_type=cart(length=None, name="tug"), agv_id="T", initial_node=w.env.layout.node("N1"))
        assert w.ask("A", (w.seg("N0", "N1"), "forward")).granted
        with pytest.raises(ValueError, match="'tug' has no length"):
            w.controller.request(stray, Claim((ClaimItem(w.seg("N0", "N1"), "forward"),), 1), fake_hooks())

    @pytest.mark.parametrize(
        ("length", "min_gap"), [(-1.0, 0.0), (math.inf, 0.0), (math.nan, 0.0), (1.0, -0.5), (1.0, math.inf)]
    )
    def test_vehicle_type_length_and_gap_are_validated(self, length: float, min_gap: float) -> None:
        with pytest.raises(ValueError, match="'cart'"):
            cart(length=length, min_gap=min_gap)

    def test_facility_capacities_and_warnings(self) -> None:
        env = Environment(debug=True)
        replay = FullReplay(env)
        declared = line(env, 4, capacities={"N2": 1})
        area = ParkingArea(env=env, name="P", node=declared.ref("N1"), capacity=2)
        station = ChargingStation(env=env, name="CS", node=declared.ref("N1"), n_slots=2)
        small = ParkingArea(env=env, name="Q", node=declared.ref("N2"), capacity=3)
        controller = ReservationController(env=env, graph=declared.graph)
        fleet = SimpleNamespace(fleet=(), parking_areas=(area, small), charging_stations=(station,))
        controller.register_fleet(fleet)  # type: ignore[arg-type]
        controller.register_fleet(SimpleNamespace(fleet=(), parking_areas=(area,), charging_stations=()))  # type: ignore[arg-type]
        TrafficInvariants(env, controller)
        warnings: list[Any] = []
        env.bus.subscribe(warnings.append, "**")
        env.activate()
        view = controller.view
        assert [view.capacity(declared.node(n)) for n in ("N0", "N1", "N2", "N3")] == [1, 4, 1, 1]  # P counted once
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
        controller = ReservationController(env=env, graph=layout.graph())
        with pytest.raises(LayoutNotReady):
            controller.graph
        prepared = layout.prepare()
        assert controller.graph is prepared.network.graph

    def test_install_layout_graph_takes_the_declared_capacities(self) -> None:
        """A declared capacity is the node's; a node without one falls back to the controller's default."""
        env = Environment()
        declared = line(env, 3, capacities={"N0": 3})
        controller = ReservationController(env=env, graph=declared.graph)
        env.activate()
        assert controller.graph is declared.canonical_graph()
        assert [controller.view.capacity(declared.node(n)) for n in ("N0", "N1")] == [3, 1]


# --- the view ----------------------------------------------------------------------------------------------


class TestView:
    def test_static_queries(self) -> None:
        nodes = {f"N{i}": Node(f"N{i}", 10 * i, 0) for i in range(3)}
        limited = LayoutGraph(
            nodes.values(), [Arc(nodes["N0"], nodes["N1"], speed_limit=1.5), Arc(nodes["N1"], nodes["N2"])]
        )
        w = world(lambda env: declare(env, limited), {"A": "N0"})
        view = w.controller.view
        n0, n1, n2 = w.nodes["N0"], w.nodes["N1"], w.nodes["N2"]
        assert w.declared is not None and view.graph is w.declared.canonical_graph()
        assert view.length(n0, n1) == 10.0
        assert view.speed_limit(n0, n1) == 1.5
        assert view.speed_limit(n1, n2) is None
        with pytest.raises(ValueError, match="not connected"):
            view.speed_limit(n0, n2)
        assert view.capacity(n1) == 1
        assert view.resources(n0, n1) == (w.seg("N0", "N1"), n1)
        assert view.mean_hold(n1) == 0.0

    def test_branch_root_is_the_root_of_a_dead_end_branch_only(self) -> None:
        w = world(t_layout, {"A": "C1"})  # C1 roots the three branches; the other nodes are members
        view = w.controller.view
        assert [node.id for node in w.nodes.values() if view.branch_root(node)] == ["C1"]
        w = world(t_layout, {"A": "C1"}, hold_back_dead_ends=False)
        off = w.controller.view
        assert not any(off.branch_root(node) for node in w.nodes.values())
        w = world(lambda env: line(env, 3), {"A": "N0"})  # a plain line has no branch
        view = w.controller.view
        assert not any(view.branch_root(node) for node in w.nodes.values())
        w = world(lambda env: line(env, 3, capacities={"N0": 2}), {"A": "N0"})
        roomy = w.controller.view  # rooted at N0
        assert [node.id for node in w.nodes.values() if roomy.branch_root(node)] == ["N0"]
        w = world(two_junction_corridor, {"A": "x1"})  # the junctions root the leaves
        view = w.controller.view
        assert [node.id for node in w.nodes.values() if view.branch_root(node)] == ["J1", "J2"]

    def test_dynamic_queries(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0"})
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
        assert view.disposition(a) is Disposition.SERVING  # travelling at rest between two arcs: turning
        a.motion = freeze({"from": "N1", "to": "N2", "t_start": 0.0, "t_end": 1.0})
        assert view.disposition(a) is Disposition.MOVING
        assert not view.admissible(b, request.claim)

    def test_admissible_ranks_a_new_claim_by_priority(self) -> None:
        priorities = {"H": 0.0, "W": 0.0, "F": 0.0}
        w = world(two_junction_corridor, {"H": "J1", "W": "x2", "F": "x1"}, priority_fn=lambda agv: priorities[agv.id])
        assert w.leg("H", "J1 a b J2").granted
        w.move("H", "a")
        w.release("H", "J1", w.seg("J1", "a"))
        w.leg("W", "x2 J2 b a J1")
        claim = w.controller.next_claim(w.agvs["F"], [w.nodes[n] for n in ("x1", "J1", "a")], 0)
        assert not w.controller.view.admissible(w.agvs["F"], claim)
        priorities["F"] = 1.0  # ahead of W: W is not an earlier claim
        assert w.controller.view.admissible(w.agvs["F"], claim)


class TestBlockers:
    def test_blockers_name_the_holders_that_make_a_resource_of_the_leg_inadmissible(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N1", "B": "N0"}, derive_corridors=False, hold_back_dead_ends=False)
        view = w.controller.view
        a, b = w.agvs["A"], w.agvs["B"]
        n0, n1, n2 = (w.nodes[name] for name in ("N0", "N1", "N2"))
        assert view.blockers(b, n0, n1) == ((n1, (a,)),)  # the segment is free, the node is not
        assert view.blockers(a, n1, n2) == ()
        assert view.blockers(a, n0, n1) == ()  # A holds the node itself
        assert w.leg("A", "N1 N2").granted
        assert view.blockers(b, n2, n1) == ((w.seg("N1", "N2"), (a,)), (n1, (a,)))  # in the leg's resource order

    def test_blockers_ignore_the_queue(self) -> None:
        w = world(
            lambda env: line(env, 3),
            {"A": "N1", "W": "N0", "F": "N2"},
            derive_corridors=False,
            hold_back_dead_ends=False,
        )
        n1, n2 = w.nodes["N1"], w.nodes["N2"]
        assert not w.ask("W", n1).event.triggered  # W queues for N1, which A holds: it is not a holder
        assert w.controller.view.blockers(w.agvs["F"], n2, n1) == ((n1, (w.agvs["A"],)),)


class TestMeanHold:
    def test_an_exponential_average_of_the_durations_of_the_holds(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N0"}, place=False, derive_corridors=False, hold_back_dead_ends=False)
        view, n1 = w.controller.view, w.nodes["N1"]
        assert view.mean_hold(n1) == 0.0
        for until, expected in ((10.0, 10.0), (30.0, 11.0), (60.0, 12.9)):  # holds of 10, 20 and 30
            assert w.ask("A", n1).granted
            w.env.run(until=until)
            w.release("A", n1)
            assert view.mean_hold(n1) == expected

    def test_an_unobserved_resource_reports_the_average_of_its_kind(self) -> None:
        w = world(lambda env: line(env, 4), {"A": "N0"}, place=False, derive_corridors=False, hold_back_dead_ends=False)
        view = w.controller.view
        n0, n1, n2, n3 = (w.nodes[name] for name in ("N0", "N1", "N2", "N3"))
        for node, duration in ((n0, 4.0), (n1, 9.0)):
            assert w.ask("A", node).granted
            w.env.run(until=w.env.now + duration)
            w.release("A", node)
        assert (view.mean_hold(n0), view.mean_hold(n1)) == (4.0, 9.0)
        assert view.mean_hold(n2) == view.mean_hold(n3) == 6.5
        assert view.mean_hold(w.seg("N0", "N1")) == 0.0  # no segment has an observation: 0.0, not the nodes' value
        segment = w.seg("N1", "N2")
        assert w.ask("A", (segment, "forward")).granted
        w.env.run(until=w.env.now + 3.0)
        w.release("A", segment)
        assert view.mean_hold(segment) == 3.0
        assert view.mean_hold(w.seg("N2", "N3")) == 3.0
        assert view.mean_hold(n2) == 6.5  # the kinds do not mix

    def test_zones_and_corridors_are_kinds_of_their_own(self) -> None:
        w = world(two_junction_corridor, {"A": "a"}, place=False)
        view = w.controller.view
        corridor, a = w.corridor(), w.nodes["a"]
        assert w.ask("A", (corridor, "forward")).granted
        w.env.run(until=7.0)
        w.release("A", corridor)
        assert view.mean_hold(corridor) == 7.0
        assert view.mean_hold(a) == 0.0
        z = world(
            lambda env: two_junction_corridor(env, zones={"Z": {"members": ["x1", "y1"], "capacity": 1}}),
            {"A": "a"},
            place=False,
        )
        zone = z.zone("Z")
        assert z.ask("A", zone).granted
        z.env.run(until=5.0)
        z.release("A", zone)
        assert z.controller.view.mean_hold(zone) == 5.0
        assert z.controller.view.mean_hold(z.corridor()) == 0.0

    def test_a_claim_in_a_new_direction_is_not_a_new_hold(self) -> None:
        w = world(lambda env: line(env, 3), {"A": "N0"}, place=False, derive_corridors=False, hold_back_dead_ends=False)
        segment = w.seg("N0", "N1")
        assert w.ask("A", (segment, "forward")).granted
        w.env.run(until=5.0)
        assert w.ask("A", (segment, "backward")).granted  # held the other way: claimed again
        w.env.run(until=12.0)
        w.release("A", segment)
        assert w.controller.view.mean_hold(segment) == 12.0

    def test_values_do_not_depend_on_what_observes_the_run(self) -> None:
        def run(subscribed: bool) -> list[float]:
            env = Environment()
            if subscribed:
                env.bus.subscribe(lambda event: None, "*")
            declared = line(env, 3)
            controller = ReservationController(
                env=env, graph=declared.graph, derive_corridors=False, hold_back_dead_ends=False
            )
            agv = AGV(env=env, agv_type=cart(), agv_id="A", initial_node=declared.ref("N0"))
            graph = declared.canonical_graph()  # prepares the layout
            controller.register_fleet(
                SimpleNamespace(fleet=(agv,), parking_areas=(), charging_stations=(), graph=graph)
            )  # type: ignore[arg-type]
            env.activate()
            nodes = {node.id: node for node in graph.nodes}
            seen = []
            for node, duration in ((nodes["N1"], 10.0), (nodes["N2"], 4.0), (nodes["N1"], 20.0), (nodes["N1"], 30.0)):
                controller.request(agv, Claim((ClaimItem(node),), 1), fake_hooks())
                env.run(until=env.now + duration)
                controller.release(agv, [node])
                seen += [controller.view.mean_hold(n) for n in nodes.values()]
            return seen

        bare = run(subscribed=False)
        assert run(subscribed=True) == bare
        assert bare[-2:] == pytest.approx([12.9, 4.0], rel=1e-12)  # N1 after holds of 10, 20 and 30; N2 after one of 4


class TestPendingIndex:
    def test_the_count_equals_the_scan_through_requests_grants_releases_and_withdrawals(self) -> None:
        """A randomized sequence over five AGVs and a line of five nodes: after every step ``pending`` is what the scan
        of the queue says, for every node and segment."""
        rng = random.Random(20261121)
        names = ["A", "B", "C", "D", "E"]
        w = world(
            lambda env: line(env, 5),
            dict.fromkeys(names, "N0"),
            place=False,
            derive_corridors=False,
            hold_back_dead_ends=False,
        )
        controller, view = w.controller, w.controller.view
        nodes = list(w.nodes.values())
        segments = [w.seg(f"N{i}", f"N{i + 1}") for i in range(4)]
        resources: list[Any] = [*nodes, *segments]
        requests: dict[str, ClaimRequest] = {}

        def scan(resource: Any) -> int:
            return sum(
                1
                for waiting in controller._queue
                if any(
                    item.resource == resource for item in controller.ledger.needed(waiting.agv, waiting.request.claim)
                )
            )

        queued = 0
        for _ in range(400):
            name = rng.choice(names)
            vehicle = w.agvs[name]
            held = controller.ledger.held(vehicle)
            action = rng.choice(["ask", "ask", "release", "withdraw"])
            if action == "ask" and vehicle not in controller._pending:
                items = tuple(
                    ClaimItem(r, rng.choice(["forward", "backward"]) if isinstance(r, Segment) else None)
                    for r in rng.sample(resources, rng.randint(1, 3))
                )
                requests[name] = controller.request(vehicle, Claim(items, 1), fake_hooks())
            elif action == "release" and held:
                controller.release(vehicle, rng.sample(held, rng.randint(1, len(held))))
            elif action == "withdraw" and vehicle in controller._pending:
                controller.withdraw(requests[name])
            queued += bool(controller._queue)
            for resource in resources:
                assert view.pending(resource) == scan(resource), (name, action, resource)
        assert queued > 50  # the sequence did queue claims


def test_route_claims_end_to_end_on_a_line() -> None:
    """Two AGVs on a line, one behind the other, each releasing behind it: the follower waits one node back."""
    w = world(lambda env: line(env, 4), {"A": "N1", "B": "N0"})
    route_a, route_b = "N1 N2 N3", "N0 N1 N2"
    behind = w.leg("B", route_b)
    assert not behind.event.triggered
    assert w.leg("A", route_a).granted
    w.move("A", "N2")
    w.release("A", "N1", w.seg("N1", "N2"))
    assert behind.granted
    assert w.held("B") == ["N0", "N0~N1", "N1"]
    assert w.replay.seeded == w.replay.state
