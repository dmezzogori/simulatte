from __future__ import annotations

import math
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics import CongestionAwareRouter, FastestRouter
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.events import AgvRouted, TrafficWaitEnded
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.kinematics import Kinematics
from simulatte.intralogistics.network import Claim, ClaimItem, Corridor, Segment, Zone
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, Router, RouteRequest, ShortestDistanceRouter, shortest_path
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import Disposition, FreeFlowController, Reroute, TrafficView
from simulatte.intralogistics.travel import TravelOutcome
from tests.intralogistics.test_travel import Scripted, World, diamond, vehicle, world
from tests.intralogistics.traffic_support import (
    FullReplay,
    StillFleet,
    TrafficInvariants,
    build,
    fake_hooks,
    line,
    two_junction_corridor,
)

ROOT = Path(__file__).resolve().parents[2]

ALGORITHMS = ["dijkstra", "astar"]


def grid() -> tuple[dict[str, Node], LayoutGraph]:
    """A(0,0) -- B(1,0) over C(0,1) -- D(1,1)."""
    nodes = {
        "A": Node(id="A", x=0.0, y=0.0),
        "B": Node(id="B", x=1.0, y=0.0),
        "C": Node(id="C", x=0.0, y=1.0),
        "D": Node(id="D", x=1.0, y=1.0),
    }
    arcs = [
        Arc(source=nodes["A"], target=nodes["B"]),
        Arc(source=nodes["A"], target=nodes["C"]),
        Arc(source=nodes["B"], target=nodes["D"]),
        Arc(source=nodes["C"], target=nodes["D"]),
    ]
    return nodes, LayoutGraph(nodes.values(), arcs)


def free_view(graph: LayoutGraph) -> TrafficView:
    controller = FreeFlowController()
    controller.register_fleet(SimpleNamespace(graph=graph))  # type: ignore[arg-type]
    return controller.view


def reservation_view(graph: LayoutGraph, **options: Any) -> TrafficView:
    env = Environment(debug=True)
    for node in graph.nodes:
        env.entities.bind_node(node)
    controller = ReservationController(env=env, graph=graph, **options)
    controller.register_fleet(SimpleNamespace(fleet=(), parking_areas=(), charging_stations=(), graph=graph))  # type: ignore[arg-type]
    env.activate()
    return controller.view


def ask(algorithm: str, view: TrafficView, origin: Node, destination: Node, **fields: Any) -> Route | None:
    request = RouteRequest(agv=None, origin=origin, destination=destination, **fields)
    return ShortestDistanceRouter(algorithm).route(request, view)  # type: ignore[arg-type]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
class TestShortestDistanceRouter:
    def test_shortest_path_ties_by_node_id(self, algorithm: str) -> None:
        nodes, graph = grid()
        route = ask(algorithm, free_view(graph), nodes["A"], nodes["D"])
        assert route == Route((nodes["A"], nodes["B"], nodes["D"]), 2.0)

    def test_no_path(self, algorithm: str) -> None:
        graph, nodes = build([("A", 0, 0), ("B", 1, 0)], [])
        assert ask(algorithm, free_view(graph), nodes["A"], nodes["B"]) is None

    def test_same_node(self, algorithm: str) -> None:
        nodes, graph = grid()
        assert ask(algorithm, free_view(graph), nodes["A"], nodes["A"]) == Route((nodes["A"],), 0.0)

    def test_avoid_node(self, algorithm: str) -> None:
        nodes, graph = grid()
        route = ask(algorithm, free_view(graph), nodes["A"], nodes["D"], avoid=frozenset({nodes["B"]}))
        assert route is not None
        assert route.nodes == (nodes["A"], nodes["C"], nodes["D"])

    def test_avoid_makes_unreachable(self, algorithm: str) -> None:
        nodes, graph = grid()
        avoid = frozenset({nodes["B"], nodes["C"]})
        assert ask(algorithm, free_view(graph), nodes["A"], nodes["D"], avoid=avoid) is None

    def test_origin_is_never_excluded(self, algorithm: str) -> None:
        nodes, graph = grid()
        route = ask(algorithm, free_view(graph), nodes["A"], nodes["B"], avoid=frozenset({nodes["A"]}))
        assert route == Route((nodes["A"], nodes["B"]), 1.0)

    def test_stale_heap_entry(self, algorithm: str) -> None:
        a, b, c, d = (Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), Node("C", 1.1, 0.1), Node("D", 2.0, 0.0))
        arcs = [Arc(a, b), Arc(a, c), Arc(b, c), Arc(b, d), Arc(c, d)]
        graph = LayoutGraph([a, b, c, d], arcs)
        route = ask(algorithm, free_view(graph), a, d)
        assert route is not None
        assert route.nodes[0] == a
        assert route.nodes[-1] == d
        assert route.cost == pytest.approx(graph.path_length(route.nodes))

    def test_cost_uses_explicit_arc_length(self, algorithm: str) -> None:
        a, b, c = Node("A", 0.0, 0.0), Node("B", 10.0, 0.0), Node("C", 5.0, 5.0)
        arcs = [Arc(a, b, length=50.0), Arc(a, c), Arc(c, b)]
        graph = LayoutGraph([a, b, c], arcs)
        route = ask(algorithm, free_view(graph), a, b)
        assert route is not None
        assert route.nodes == (a, c, b)
        assert route.cost == pytest.approx(graph.path_length(route.nodes))

    def test_cost_is_the_path_length_of_the_route(self, algorithm: str) -> None:
        """The cost is ``graph.path_length`` (an fsum), not the running sum of the search: 0.1 + 0.2 + 0.3."""
        nodes = [Node(name, 0.05 * i, 0.0) for i, name in enumerate("ABCD")]
        arcs = [Arc(u, v, length=length) for u, v, length in zip(nodes[:-1], nodes[1:], (0.1, 0.2, 0.3), strict=True)]
        graph = LayoutGraph(nodes, arcs)
        route = ask(algorithm, free_view(graph), nodes[0], nodes[-1])
        assert route is not None
        assert 0.1 + 0.2 + 0.3 != 0.6
        assert route.cost == graph.path_length(route.nodes) == 0.6

    def test_avoid_segment(self, algorithm: str) -> None:
        nodes, graph = grid()
        view = reservation_view(graph)
        segment = view.resources(nodes["A"], nodes["B"])[0]
        assert isinstance(segment, Segment)
        route = ask(algorithm, view, nodes["A"], nodes["D"], avoid=frozenset({segment}))
        assert route is not None
        assert route.nodes == (nodes["A"], nodes["C"], nodes["D"])

    def test_avoid_zone(self, algorithm: str) -> None:
        nodes, graph = grid()
        zone = Zone("z", ((nodes["A"], nodes["B"]),))
        view = reservation_view(graph, zones=[zone])
        route = ask(algorithm, view, nodes["A"], nodes["D"], avoid=frozenset({zone}))
        assert route is not None
        assert route.nodes == (nodes["A"], nodes["C"], nodes["D"])

    def test_avoid_corridor(self, algorithm: str) -> None:
        graph, nodes = two_junction_corridor()
        view = reservation_view(graph)
        corridor = view.resources(nodes["a"], nodes["b"])[0]
        assert isinstance(corridor, Corridor)
        assert ask(algorithm, view, nodes["J1"], nodes["J2"]) is not None
        assert ask(algorithm, view, nodes["J1"], nodes["J2"], avoid=frozenset({corridor})) is None


class TestSearch:
    def test_router_protocol(self) -> None:
        assert isinstance(ShortestDistanceRouter(), Router)

    def test_allowed_excludes_legs(self) -> None:
        nodes, graph = grid()
        found = shortest_path(graph, nodes["A"], nodes["D"], allowed=lambda u, v: v != nodes["B"])
        assert found == ((nodes["A"], nodes["C"], nodes["D"]), 2.0)

    def test_request_defaults(self) -> None:
        nodes, _ = grid()
        request = RouteRequest(None, nodes["A"], nodes["B"])
        assert (request.load_weight, request.avoid, request.purpose) == (0.0, frozenset(), "travel")

    def test_graph_shortest_path_delegates(self) -> None:
        nodes, graph = grid()
        assert graph.shortest_path(nodes["A"], nodes["D"]) == [nodes["A"], nodes["B"], nodes["D"]]
        assert graph.shortest_path(nodes["A"], nodes["A"]) == [nodes["A"]]
        island = Node("ISLAND", 99.0, 99.0)
        assert LayoutGraph([nodes["A"], island], []).shortest_path(nodes["A"], island) is None


def test_unknown_algorithm_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown algorithm"):
        ShortestDistanceRouter("bfs")  # type: ignore[arg-type]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_improved_node_leaves_a_stale_entry(algorithm: str) -> None:
    a, b, c, d = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), Node("C", 2.0, 0.0), Node("D", 3.0, 0.0)
    arcs = [Arc(a, c, length=10.0), Arc(a, b, length=1.0), Arc(b, c, length=1.0), Arc(c, d, length=20.0)]
    graph = LayoutGraph([a, b, c, d], arcs)
    route = ask(algorithm, free_view(graph), a, d)
    assert route == Route((a, b, c, d), 22.0)


# --- FastestRouter (spec §10.2) ----------------------------------------------------------------------------------

RIGHT = math.pi / 2
SLOWING = 0.8333333333333334  # (1.5 - 0.5) ** 2 / (2 * 1.5) * (1 / 0.8 + 1 / 0.8) by hand; computed, one ulp below


def steady() -> TrapezoidalProfile:
    """``v_max=1.5``, ``accel=decel=0.8``, not slowed by a draining battery."""
    return TrapezoidalProfile(1.5, 0.8, 0.8, battery_degradation_fn=lambda level: 1.0)


class Plain:
    """A profile with only ``travel_time``: 2 m/s, below the arc's speed limit when it has one; no limits."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float, float, float | None]] = []

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        self.calls.append((distance, load_weight, battery_level, speed_limit))
        return distance / min(2.0, speed_limit if speed_limit is not None else 2.0)


class Limits:
    """A profile with fixed limits and ``travel_time`` of the rest-to-rest kind, like :class:`TrapezoidalProfile`."""

    def __init__(self, kin: Kinematics) -> None:
        self.kin = kin
        self.calls: list[tuple[float, float]] = []

    def limits(self, load_weight: float, battery_level: float) -> Kinematics:
        self.calls.append((load_weight, battery_level))
        return self.kin


class Turns:
    """A callable of the heading change that records its arguments and returns ``fn(angle)``."""

    def __init__(self, fn: Callable[[float], float]) -> None:
        self.fn = fn
        self.angles: list[float] = []

    def __call__(self, angle: float) -> float:
        self.angles.append(angle)
        return self.fn(angle)


def cart(profile: Any = None, **options: Any) -> AGVType:
    return AGVType(
        name="cart",
        speed_profile=profile if profile is not None else steady(),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        **options,
    )


@dataclass(eq=False)
class Vehicle:
    """The parts of an AGV that the routers read; hashable, as the view's ledger needs."""

    id: str
    agv_type: AGVType
    battery: Any
    heading: float | None

    def battery_level_now(self) -> float:
        return self.battery.level


def fastest(
    graph: LayoutGraph,
    origin: Node,
    destination: Node,
    agv_type: AGVType | None = None,
    *,
    level: float = 1.0,
    heading: float | None = None,
    view: TrafficView | None = None,
    router: Router | None = None,
    **fields: Any,
) -> Route | None:
    agv = Vehicle("V", agv_type or cart(), SimpleNamespace(level=level, capacity=1.0), heading)
    request = RouteRequest(agv=agv, origin=origin, destination=destination, **fields)  # type: ignore[arg-type]
    return (router or FastestRouter()).route(request, view or free_view(graph))


def arms() -> tuple[LayoutGraph, dict[str, Node]]:
    """``S`` to ``E`` through ``P`` (20 m, limited to 0.5) or through ``Q`` (30 m, no limit)."""
    nodes = {name: Node(name, x, y) for name, x, y in [("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0)]}
    s, p, q, e = (nodes[name] for name in "SPQE")
    arcs = [
        Arc(s, p, length=10.0, speed_limit=0.5),
        Arc(p, e, length=10.0, speed_limit=0.5),
        Arc(s, q, length=15.0),
        Arc(q, e, length=15.0),
    ]
    return LayoutGraph(nodes.values(), arcs), nodes


def corner(direct: float | None = None) -> tuple[LayoutGraph, dict[str, Node]]:
    """``A`` to ``C`` through ``B`` (two 10 m arcs at a right angle), and a direct arc of length `direct` if given."""
    nodes = {name: Node(name, x, y) for name, x, y in [("A", 0, 0), ("B", 10, 0), ("C", 10, 10)]}
    a, b, c = nodes["A"], nodes["B"], nodes["C"]
    arcs = [Arc(a, b), Arc(b, c)] + ([] if direct is None else [Arc(a, c, length=direct)])
    return LayoutGraph(nodes.values(), arcs), nodes


def octagon() -> tuple[LayoutGraph, tuple[Node, Node, Node]]:
    """The graph of ``test_a_route_is_never_a_loop`` and its nodes ``A``, ``X`` and ``Y``: a ring of eight gentle turns
    through ``A``, and ``X``, ``A``, ``Y`` at a sharp angle."""
    a, x, y = Node("A", 0.0, 0.0), Node("X", -10.0, 0.0), Node("Y", -7.0, -7.0)
    ring = [a]
    for k, degrees in enumerate((-45, 0, 45, 90, 135, 180, 225), start=1):
        ring.append(
            Node(
                f"V{k}",
                ring[-1].x + 10 * math.cos(math.radians(degrees)),
                ring[-1].y + 10 * math.sin(math.radians(degrees)),
            )
        )
    arcs = [Arc(u, v) for u, v in zip(ring, [*ring[1:], a])] + [Arc(x, a), Arc(a, y)]
    return LayoutGraph([a, x, y, *ring[1:]], arcs), (a, x, y)


class TestFastestRouter:
    def test_router_protocol_and_export(self) -> None:
        assert isinstance(FastestRouter(), Router)

    def test_the_fastest_route_is_not_the_shortest(self) -> None:
        graph, n = arms()
        view = free_view(graph)
        request = RouteRequest(None, n["S"], n["E"])
        assert ShortestDistanceRouter().route(request, view) == Route((n["S"], n["P"], n["E"]), 20.0)
        route = fastest(graph, n["S"], n["E"], view=view)
        assert route == Route((n["S"], n["Q"], n["E"]), 20.0)  # 30 m at 1.5 against 20 m at 0.5

    def test_a_profile_without_limits_is_asked_for_the_travel_time(self) -> None:
        graph, n = arms()
        profile = Plain()
        route = fastest(graph, n["S"], n["E"], cart(profile), level=0.5, load_weight=7.0)
        assert route == Route((n["S"], n["Q"], n["E"]), 15.0)  # 40 s at limit 0.5 against 2 * 7.5 s
        assert (10.0, 7.0, 0.5, 0.5) in profile.calls and (15.0, 7.0, 0.5, None) in profile.calls
        assert fastest(graph, n["S"], n["E"], cart(Plain())).cost == 15.0

    def test_load_and_battery_change_the_cost(self) -> None:
        graph, n = arms()
        profile = TrapezoidalProfile(1.5, 0.8, 0.8, load_speed_factor_fn=lambda load: 0.5 if load > 0 else 1.0)
        empty = fastest(graph, n["S"], n["E"], cart(profile))
        loaded = fastest(graph, n["S"], n["E"], cart(profile), load_weight=10.0)
        half = fastest(graph, n["S"], n["E"], cart(profile), level=0.5)
        assert (empty.cost, loaded.cost, half.cost) == (20.0, 40.0, 40.0)  # type: ignore[union-attr]

    def test_limits_are_sampled_once_per_request(self) -> None:
        graph, n = arms()
        profile = Limits(Kinematics(1.5, 0.8, 0.8))
        fastest(graph, n["S"], n["E"], cart(profile), level=0.25, load_weight=3.0)
        assert profile.calls == [(3.0, 0.25)]

    def test_same_node_and_no_path(self) -> None:
        graph, n = arms()
        assert fastest(graph, n["S"], n["S"]) == Route((n["S"],), 0.0)
        turning = cart(turn_time=lambda a: 1.0)
        assert fastest(graph, n["S"], n["S"], turning) == Route((n["S"],), 0.0)
        island = Node("I", 99.0, 99.0)
        both = LayoutGraph([Node("S", 0.0, 0.0), island], [])
        assert fastest(both, n["S"], island) is None
        assert fastest(both, n["S"], island, turning) is None

    def test_without_an_agv_the_cost_is_the_path_length(self) -> None:
        graph, n = arms()
        route = FastestRouter().route(RouteRequest(None, n["S"], n["E"]), free_view(graph))
        assert route == Route((n["S"], n["P"], n["E"]), 20.0)

    def test_a_stalled_vehicle_has_no_route(self) -> None:
        graph, n = arms()
        assert fastest(graph, n["S"], n["E"], cart(Limits(Kinematics(0.0, 0.8, 0.8)))) is None
        assert fastest(graph, n["S"], n["E"], cart(Limits(Kinematics(1.5, 0.0, 0.8)))) is None

    @pytest.mark.parametrize("turn_time", [None, lambda a: 0.0])
    def test_ties_break_by_node_id(self, turn_time: Any) -> None:
        nodes, graph = grid()
        route = fastest(graph, nodes["A"], nodes["D"], cart(turn_time=turn_time))
        assert route is not None and route.nodes == (nodes["A"], nodes["B"], nodes["D"])
        mirrored = LayoutGraph(
            [nodes["A"], nodes["B"], nodes["C"], nodes["D"]],
            [Arc(nodes["A"], nodes["C"]), Arc(nodes["C"], nodes["D"]), Arc(nodes["A"], nodes["B"])]
            + [Arc(nodes["B"], nodes["D"])],
        )
        route = fastest(mirrored, nodes["A"], nodes["D"], cart(turn_time=turn_time))
        assert route is not None and route.nodes == (nodes["A"], nodes["B"], nodes["D"])

    @pytest.mark.parametrize("turn_time", [None, lambda a: 0.0])
    def test_an_improved_node_leaves_a_stale_entry(self, turn_time: Any) -> None:
        a, b, c, d = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), Node("C", 2.0, 0.0), Node("D", 3.0, 0.0)
        arcs = [Arc(a, c, length=10.0), Arc(a, b, length=1.0), Arc(b, c, length=1.0), Arc(c, d, length=20.0)]
        route = fastest(LayoutGraph([a, b, c, d], arcs), a, d, cart(Plain(), turn_time=turn_time))
        assert route == Route((a, b, c, d), 11.0)

    @pytest.mark.parametrize("turn_time", [None, lambda a: 0.0])
    def test_avoid(self, turn_time: Any) -> None:
        nodes, graph = grid()
        agv_type = cart(turn_time=turn_time)
        route = fastest(graph, nodes["A"], nodes["D"], agv_type, avoid=frozenset({nodes["B"]}))
        assert route is not None and route.nodes == (nodes["A"], nodes["C"], nodes["D"])
        view = reservation_view(graph)
        segment = view.resources(nodes["A"], nodes["B"])[0]
        route = fastest(graph, nodes["A"], nodes["D"], agv_type, view=view, avoid=frozenset({segment}))
        assert route is not None and route.nodes == (nodes["A"], nodes["C"], nodes["D"])
        both = frozenset({nodes["B"], nodes["C"]})
        assert fastest(graph, nodes["A"], nodes["D"], agv_type, avoid=both) is None
        assert fastest(graph, nodes["A"], nodes["B"], agv_type, avoid=frozenset({nodes["A"]})) is not None

    def test_a_turn_adds_its_time(self) -> None:
        graph, n = corner()
        straight = fastest(graph, n["A"], n["C"], cart(Plain()))
        turned = fastest(graph, n["A"], n["C"], cart(Plain(), turn_time=lambda a: 4.0))
        assert straight is not None and turned is not None
        assert (straight.cost, turned.cost) == (10.0, 14.0)

    def test_a_longer_straight_route_wins_below_the_turn_time(self) -> None:
        agv_type = cart(Plain(), turn_time=lambda a: 4.0)
        near, far = corner(direct=26.0), corner(direct=30.0)  # 13.0 and 15.0 seconds, against 10.0 + 4.0
        assert [node.id for node in fastest(near[0], near[1]["A"], near[1]["C"], agv_type).nodes] == ["A", "C"]  # type: ignore[union-attr]
        assert [node.id for node in fastest(far[0], far[1]["A"], far[1]["C"], agv_type).nodes] == ["A", "B", "C"]  # type: ignore[union-attr]

    def test_a_turn_stops_a_vehicle_with_limits(self) -> None:
        graph, n = corner()
        plain = fastest(graph, n["A"], n["C"])
        turned = fastest(graph, n["A"], n["C"], cart(turn_time=lambda a: 4.0))
        assert plain is not None and turned is not None
        assert plain.cost == pytest.approx(2 * (10.0 / 1.5), rel=1e-12)
        # the turn wait and the time lost slowing from 1.5 to a stop and regaining the speed: u / 2 * (1 / a + 1 / d)
        assert turned.cost == 19.208333333333336
        assert turned.cost == pytest.approx(plain.cost + 4.0 + 1.5 / 2 * (1 / 0.8 + 1 / 0.8), rel=1e-12)

    def test_a_corner_speed_adds_the_time_lost_slowing(self) -> None:
        graph, n = corner()
        spy = Turns(lambda a: 0.5)
        plain = fastest(graph, n["A"], n["C"])
        capped = fastest(graph, n["A"], n["C"], cart(corner_speed=spy))
        assert plain is not None and capped is not None
        assert pytest.approx(RIGHT) in spy.angles
        assert capped.cost == 14.166666666666668
        assert capped.cost == pytest.approx(plain.cost + SLOWING, rel=1e-12)

    def test_a_corner_speed_that_does_not_bind_costs_nothing(self) -> None:
        graph, n = corner()
        plain = fastest(graph, n["A"], n["C"])
        for value in (1.5, 3.0, math.inf):
            capped = fastest(graph, n["A"], n["C"], cart(corner_speed=lambda a, v=value: v))
            assert capped is not None and plain is not None and capped.cost == plain.cost

    def test_the_cap_is_the_lower_of_the_two_arcs(self) -> None:
        nodes = {name: Node(name, x, y) for name, x, y in [("A", 0, 0), ("B", 10, 0), ("C", 10, 10)]}
        arcs = [Arc(nodes["A"], nodes["B"], speed_limit=0.5), Arc(nodes["B"], nodes["C"])]
        graph = LayoutGraph(nodes.values(), arcs)
        capped = fastest(graph, nodes["A"], nodes["C"], cart(corner_speed=lambda a: 0.25))
        plain = fastest(graph, nodes["A"], nodes["C"])
        assert capped is not None and plain is not None
        assert capped.cost == pytest.approx(plain.cost + (0.5 - 0.25) ** 2 / (2 * 0.5) * 2.5, rel=1e-12)

    def test_a_corner_speed_is_ignored_without_limits(self) -> None:
        graph, n = corner()
        spy = Turns(lambda a: 0.5)
        route = fastest(graph, n["A"], n["C"], cart(Plain(), corner_speed=spy))
        assert route is not None and route.cost == 10.0
        assert spy.angles == []

    def test_the_first_arc_turns_from_the_heading_at_rest(self) -> None:
        graph, n = corner()
        spy = Turns(lambda a: 3.0 if a > 0.1 else 0.0)
        agv_type = cart(Plain(), turn_time=spy)
        unknown = fastest(graph, n["A"], n["B"], agv_type)
        assert unknown is not None and unknown.cost == 5.0 and spy.angles == []  # an unknown heading turns for free
        for heading, cost in ((0.0, 5.0), (RIGHT, 8.0)):
            route = fastest(graph, n["A"], n["B"], agv_type, heading=heading)
            assert route is not None and route.cost == cost
        # at rest at the origin: no slowing term, only the turn time
        route = fastest(graph, n["A"], n["B"], cart(turn_time=lambda a: 3.0), heading=RIGHT)
        assert route is not None and route.cost == pytest.approx(10.0 / 1.5 + 3.0, rel=1e-12)

    def test_the_heading_decides_which_way_round_is_faster(self) -> None:
        a, b, c, d = Node("A", 0.0, 0.0), Node("B", 10.0, 0.0), Node("C", 0.0, 10.0), Node("D", 10.0, 10.0)
        graph = LayoutGraph([a, b, c, d], [Arc(a, b), Arc(a, c), Arc(b, d), Arc(c, d)])
        agv_type = cart(Plain(), turn_time=lambda angle: 3.0 if angle > 0.1 else 0.0)
        east = fastest(graph, a, d, agv_type, heading=0.0)
        north = fastest(graph, a, d, agv_type, heading=RIGHT)
        assert east is not None and east.nodes == (a, b, d) and east.cost == 13.0  # the corner at B costs 3.0
        assert north is not None and north.nodes == (a, c, d) and north.cost == 13.0  # the corner at C costs 3.0

    def test_an_arc_without_direction_leaves_the_heading_unknown(self) -> None:
        nodes = {name: Node(name, x, y) for name, x, y in [("A", 0, 0), ("B", 10, 0), ("Z", 10, 0), ("D", 10, 10)]}
        arcs = [Arc(nodes["A"], nodes["B"]), Arc(nodes["B"], nodes["Z"], length=1.0), Arc(nodes["Z"], nodes["D"])]
        graph = LayoutGraph(nodes.values(), arcs)
        spy = Turns(lambda a: 1.0)
        route = fastest(graph, nodes["A"], nodes["D"], cart(Plain(), turn_time=spy))
        assert route is not None and route.nodes == tuple(nodes.values())
        assert 0.0 in spy.angles
        assert route.cost == 11.5  # 10.5 and one turn, at B into the arc without direction; none at Z (heading unknown)

    def test_a_route_is_never_a_loop(self) -> None:
        """Eight gentle turns round an octagon through ``A`` cost less than the one sharp turn at ``A`` on the way from
        ``X`` to ``Y``: the search walks the octagon and the route is cut where it returns to ``A``."""
        graph, (a, x, y) = octagon()
        sharp = Turns(lambda angle: 100.0 if angle > 1.0 else 0.0)
        route = fastest(graph, x, y, cart(Plain(), turn_time=sharp))
        assert route is not None
        assert route.nodes == (x, a, y)  # not X, A, V1 ... V7, A, Y
        assert route.cost == pytest.approx(5.0 + math.hypot(7.0, 7.0) / 2 + 100.0, rel=1e-12)
        assert pytest.approx(math.radians(135)) in sharp.angles  # the direct route's turn was priced

    def test_a_reversal_is_never_priced(self) -> None:
        graph, n = corner()

        def at_pi(angle: float) -> float:
            return math.nan if angle > 3.1 else 0.0

        route = fastest(graph, n["A"], n["C"], cart(Plain(), turn_time=at_pi))
        assert route is not None and route.cost == 10.0
        route = fastest(graph, n["A"], n["C"], cart(corner_speed=lambda angle: math.nan if angle > 3.1 else math.inf))
        assert route is not None and route.cost == pytest.approx(2 * (10.0 / 1.5), rel=1e-12)

    def test_invalid_values_name_the_profile_or_the_type(self) -> None:
        graph, n = corner()

        def ask(agv_type: AGVType) -> None:
            fastest(graph, n["A"], n["C"], agv_type)

        for kin, problem in [
            (Kinematics(math.nan, 0.8, 0.8), "every value must be finite"),
            (Kinematics(1.5, math.inf, 0.8), "every value must be finite"),
            (Kinematics(1.5, 0.8, -0.1), "decel must not be negative"),
            (Kinematics(1.5, 0.8, 0.0), "decel must be positive"),
        ]:
            with pytest.raises(ValueError, match=f"Limits.limits\\(0.0, 1.0\\) returned .*: {problem}"):
                ask(cart(Limits(kin)))
        for value in (math.nan, -1.0, math.inf):
            profile = Plain()
            profile.travel_time = lambda *args, v=value, **kw: v  # type: ignore[method-assign]
            with pytest.raises(ValueError, match="Plain.travel_time.* returned .*finite and >= 0"):
                ask(cart(profile))
            with pytest.raises(ValueError, match="AGVType 'cart': turn_time.* returned .*finite and >= 0"):
                ask(cart(Plain(), turn_time=lambda a, v=value: v))
        for value in (math.nan, -1.0):
            with pytest.raises(ValueError, match="AGVType 'cart': corner_speed.* returned .*not NaN and >= 0"):
                ask(cart(corner_speed=lambda a, v=value: v))


def test_the_fastest_router_does_not_depend_on_the_hash_seed() -> None:
    script = "from tests.intralogistics.test_routing import tied_routes;print('ROUTES', tied_routes())"
    expected = tied_routes()
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
        assert completed.stdout.splitlines()[-1] == f"ROUTES {expected}", hash_seed


def tied_routes() -> list[tuple[str, ...]]:
    """Routes over a lattice full of equal-cost paths, with and without a turning model."""
    nodes = {f"{i}{j}": Node(f"{i}{j}", 10.0 * i, 10.0 * j) for i in range(4) for j in range(4)}
    arcs = [Arc(nodes[f"{i}{j}"], nodes[f"{i + 1}{j}"]) for i in range(3) for j in range(4)]
    arcs += [Arc(nodes[f"{i}{j}"], nodes[f"{i}{j + 1}"]) for i in range(4) for j in range(3)]
    graph = LayoutGraph(nodes.values(), arcs)
    found = []
    for turn_time in (None, lambda angle: 1.0 if angle > 0.1 else 0.0):
        route = fastest(graph, nodes["00"], nodes["33"], cart(Plain(), turn_time=turn_time))
        assert route is not None
        found.append(tuple(node.id for node in route.nodes))
    return found


# --- routers and the executor ------------------------------------------------------------------------------------


def test_avoid_can_make_destination_unreachable() -> None:
    """``FastestRouter`` answers None when `avoid` cuts every path, and a reroute without a route is not staged."""
    nodes, graph = grid()
    cut = frozenset({nodes["B"], nodes["C"]})
    assert fastest(graph, nodes["A"], nodes["D"], avoid=cut) is None

    policy = Scripted()
    w = world(
        diamond(),
        {"A": "S", "B": "P"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        router=FastestRouter(),
        policy=policy,
    )
    policy.blocked = (Reroute(frozenset({w.nodes["P"], w.nodes["Q"]})),)  # every way to E
    w.go("A", "E")
    w.env.run()
    assert policy.waits and w.outcomes == {}  # A still waits for P
    assert [e.nodes for e in w.of(AgvRouted)] == [("S", "P", "E")]
    assert not w.of(TrafficWaitEnded)
    assert w.agvs["A"].current_node == w.nodes["S"]


@pytest.mark.parametrize(
    "router", [FastestRouter(), CongestionAwareRouter(FastestRouter())], ids=["fastest", "congestion"]
)
def test_the_fastest_router_samples_the_limits_at_the_battery_level_now(router: Router) -> None:
    """An AGV with idle power 0.1, idle for 800 s: 100 accounted, 20 now. Its limits scale with the level
    (``TrapezoidalProfile`` at 2 m/s), so 20 m cost 20 / (2 * 0.2) = 50 s, not the 10 s of the level as last
    accounted; routing accounts nothing."""
    a, b = Node("A", 0.0, 0.0), Node("B", 20.0, 0.0)
    graph = LayoutGraph([a, b], [Arc(a, b)])
    env = Environment()
    kind = AGVType(
        name="drained",
        speed_profile=TrapezoidalProfile(2.0, 1.0, 1.0),
        battery_capacity=100.0,
        weight_capacity=10.0,
        volume_capacity=1.0,
        idle_power=0.1,
    )
    agv = AGV(env=env, agv_type=kind, initial_node=a)
    env.run(until=800.0)
    assert (agv.battery.level, agv.battery_level_now()) == (100.0, 20.0)
    route = router.route(RouteRequest(agv=agv, origin=a, destination=b), free_view(graph))
    assert route is not None and route.cost == pytest.approx(50.0, rel=1e-12)
    assert agv.battery.level == 100.0


def test_a_travel_follows_the_fastest_route() -> None:
    """On a real AGV: the 26 m direct arc takes 17.3 s, the corner 10 m + 10 m with a stop 19.2 s."""
    w = world(
        corner(direct=26.0), {"V": "A"}, reserve=False, router=FastestRouter(), agv_type=cart(turn_time=lambda a: 4.0)
    )
    w.go("V", "C")
    w.env.run()
    assert [(e.nodes, e.cost) for e in w.of(AgvRouted)] == [(("A", "C"), pytest.approx(26.0 / 1.5, rel=1e-12))]
    assert w.outcomes == {"V": TravelOutcome.ARRIVED}


@pytest.mark.parametrize(
    ("name", "ids", "problem"),
    [
        ("WrongStart", ("N1", "N2"), "it does not start at N0"),
        ("WrongEnd", ("N0", "N1"), "it does not end at N2"),
        ("NoArc", ("N0", "N2"), "there is no arc from N0 to N2"),
    ],
)
def test_executor_rejects_invalid_routes(name: str, ids: tuple[str, ...], problem: str) -> None:
    graph, nodes = line(3)
    route = Route(tuple(nodes[i] for i in ids), 1.0)
    router = type(name, (), {"route": lambda self, request, view: route})()
    w = world((graph, nodes), {"A": "N0"}, reserve=False, router=router)
    w.go("A", "N2")
    with pytest.raises(ValueError, match=f"{name} returned an invalid route from N0 to N2: {problem}"):
        w.env.run()


# --- CongestionAwareRouter ---------------------------------------------------------------------------------------


class Crowd:
    """The view of a graph with a scripted crowd: what ``blockers`` returns for a leg, the pending claims and the mean
    hold of a resource, and which holders are stranded. It counts the reads of the congestion state."""

    def __init__(
        self,
        graph: LayoutGraph,
        blocked: dict[tuple[str, str], tuple[tuple[Node, tuple[Any, ...]], ...]] | None = None,
        pending: dict[Node, int] | None = None,
        holds: dict[Node, float] | None = None,
        stranded: tuple[Any, ...] = (),
    ) -> None:
        self.free = free_view(graph)
        self.blocked = blocked or {}
        self.pending_claims = pending or {}
        self.holds = holds or {}
        self.stranded = stranded
        self.blockers_reads: Counter[tuple[str, str]] = Counter()
        self.pending_reads: Counter[Node] = Counter()
        self.mean_hold_reads: Counter[Node] = Counter()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.free, name)

    def blockers(self, agv: Any, u: Node, v: Node) -> tuple[tuple[Node, tuple[Any, ...]], ...]:
        self.blockers_reads[(u.id, v.id)] += 1
        return self.blocked.get((u.id, v.id), ())

    def pending(self, resource: Node) -> int:
        self.pending_reads[resource] += 1
        return self.pending_claims.get(resource, 0)

    def mean_hold(self, resource: Node) -> float:
        self.mean_hold_reads[resource] += 1
        return self.holds.get(resource, 0.0)

    def disposition(self, agv: Any) -> Disposition:
        return Disposition.STRANDED if agv in self.stranded else Disposition.MOVING


def congested(
    graph: LayoutGraph,
    origin: Node,
    destination: Node,
    view: Any,
    agv_type: AGVType | None = None,
    *,
    wait_weight: float = 1.0,
    horizon: float = 60.0,
    **fields: Any,
) -> Route | None:
    router = CongestionAwareRouter(wait_weight=wait_weight, horizon=horizon)
    return fastest(graph, origin, destination, agv_type, view=view, router=router, **fields)


def chain(*lengths: float) -> tuple[LayoutGraph, list[Node]]:
    """Nodes ``C0, C1, ...`` joined in a row by arcs of the given lengths (at 1.5 m/s, 1.5 times a length)."""
    nodes = [Node(f"C{i}", float(i), 0.0) for i in range(len(lengths) + 1)]
    arcs = [Arc(u, v, length=length) for u, v, length in zip(nodes, nodes[1:], lengths)]
    return LayoutGraph(nodes, arcs), nodes


def two_arms(q_arm: float) -> tuple[LayoutGraph, dict[str, Node]]:
    """``S`` to ``E`` through ``P`` (two 10 m arcs) or through ``Q`` (two arcs of `q_arm`), with ``K`` off ``S`` and an
    isolated ``Z``."""
    n = {name: Node(name, x, y) for name, x, y in [("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0)]}
    n |= {"K": Node("K", -10, 0), "Z": Node("Z", 50, 50)}
    arcs = [Arc(n["S"], n["P"]), Arc(n["P"], n["E"]), Arc(n["S"], n["K"])]
    arcs += [Arc(n["S"], n["Q"], length=q_arm), Arc(n["Q"], n["E"], length=q_arm)]
    return LayoutGraph(n.values(), arcs), n


def occupied_arm(q_arm: float, state: AGVState) -> tuple[World, Route | None]:
    """``V`` at ``S`` asks for a route to ``E`` while ``B`` is in `state` on ``P``, where a hold of 40 s was seen."""
    w = world(two_arms(q_arm), {"V": "S", "T": "K", "B": "Z"}, derive_corridors=False, hold_back_dead_ends=False)
    p = w.nodes["P"]
    w.controller.request(w.agvs["T"], Claim((ClaimItem(p),), 1), fake_hooks())
    w.env.run(until=40.0)
    w.controller.release(w.agvs["T"], [p])  # P was held for 40 s
    assert w.controller.request(w.agvs["B"], Claim((ClaimItem(p),), 1), fake_hooks()).granted
    w.agvs["B"]._state = state
    request = RouteRequest(w.agvs["V"], w.nodes["S"], w.nodes["E"])
    return w, CongestionAwareRouter().route(request, w.controller.view)


H1, H2 = SimpleNamespace(id="H1"), SimpleNamespace(id="H2")


class TestCongestionAwareRouter:
    def test_router_protocol_export_and_defaults(self) -> None:
        router = CongestionAwareRouter()
        assert isinstance(router, Router)
        assert isinstance(router.base, FastestRouter)
        assert (router.wait_weight, router.horizon) == (1.0, 60.0)
        base = ShortestDistanceRouter("astar")
        assert CongestionAwareRouter(base, wait_weight=0.5, horizon=10.0).base is base

    def test_arguments_are_validated(self) -> None:
        for horizon in (0.0, -1.0, math.nan):
            with pytest.raises(ValueError, match="horizon must be positive"):
                CongestionAwareRouter(horizon=horizon)
        for wait_weight in (-0.1, math.nan, math.inf):
            with pytest.raises(ValueError, match="wait_weight must be finite and >= 0"):
                CongestionAwareRouter(wait_weight=wait_weight)
        assert CongestionAwareRouter(horizon=math.inf).horizon == math.inf
        with pytest.raises(TypeError, match="base must be .*FastestRouter.*Scripted"):
            CongestionAwareRouter(Scripted())  # type: ignore[arg-type]

    def test_a_blocked_leg_costs_its_penalty_on_top_of_the_base_cost(self) -> None:
        graph, nodes = chain(10.0, 10.0)
        crowd = Crowd(graph, {("C0", "C1"): ((nodes[1], (H1,)),)}, {nodes[1]: 2}, {nodes[1]: 5.0})
        base = fastest(graph, nodes[0], nodes[2])
        route = congested(graph, nodes[0], nodes[2], crowd, wait_weight=2.0)
        assert base is not None and route is not None
        assert route.nodes == base.nodes
        assert route.cost == pytest.approx(base.cost + 2.0 * (1 + 2) * 5.0, rel=1e-12)  # (holders + pending) * hold

    def test_the_penalty_of_a_leg_falls_with_the_cost_at_which_it_is_reached(self) -> None:
        """A leg expanded at ``t = horizon`` costs ``1 / e`` of what it costs at ``t = 0``."""
        graph, nodes = chain(90.0, 10.0)  # 60 s to the second node at 1.5 m/s
        base = fastest(graph, nodes[0], nodes[2])
        assert base is not None
        penalties = []
        for blocked in (("C0", "C1"), ("C1", "C2")):
            crowd = Crowd(graph, {blocked: ((nodes[1], (H1,)),)}, holds={nodes[1]: 7.0})
            route = congested(graph, nodes[0], nodes[2], crowd, horizon=60.0)
            assert route is not None
            penalties.append(route.cost - base.cost)
        assert penalties[0] == pytest.approx(7.0, rel=1e-12)
        assert penalties[1] == pytest.approx(7.0 / math.e, rel=1e-12)
        assert penalties[1] == pytest.approx(penalties[0] / math.e, rel=1e-12)

    def test_the_largest_penalty_of_the_resources_of_the_leg_applies(self) -> None:
        graph, nodes = chain(10.0, 10.0, 10.0)
        pair = ((nodes[1], (H1,)), (nodes[2], (H1, H2)), (nodes[3], (H2,)))
        crowd = Crowd(
            graph,
            {("C0", "C1"): pair},
            {nodes[1]: 0, nodes[2]: 1, nodes[3]: 0},
            {nodes[1]: 10.0, nodes[2]: 4.0, nodes[3]: 8.0},
        )
        base = fastest(graph, nodes[0], nodes[3])
        route = congested(graph, nodes[0], nodes[3], crowd)
        assert base is not None and route is not None
        assert route.cost == pytest.approx(base.cost + 12.0, rel=1e-12)  # 1 * 10, (2 + 1) * 4, 1 * 8: the 12

    def test_a_penalty_makes_the_search_take_the_slower_arm(self) -> None:
        graph, n = arms()  # the fastest arm is S, Q, E (20 s); S, P, E takes 40 s
        legs = {("S", "Q"): ((n["Q"], (H1,)),)}
        for hold, arm, cost in ((30.0, "Q", 20.0 + 15.0), (50.0, "P", 40.0)):  # half the hold: 15 s or 25 s on Q's 20 s
            route = congested(graph, n["S"], n["E"], Crowd(graph, legs, holds={n["Q"]: hold}), wait_weight=0.5)
            assert route is not None and route.nodes[1] is n[arm]
            assert route.cost == pytest.approx(cost, rel=1e-12)

    def test_a_leg_held_to_capacity_by_stranded_agvs_is_excluded(self) -> None:
        graph, n = arms()
        legs = {("S", "Q"): ((n["Q"], (H1,)),)}
        crowd = Crowd(graph, legs, stranded=(H1,))
        route = congested(graph, n["S"], n["E"], crowd)
        assert route is not None and route.nodes[1] is n["P"] and route.cost == 40.0  # the base cost of that arm
        only = Crowd(graph, {**legs, ("S", "P"): ((n["P"], (H2,)),)}, stranded=(H1, H2))
        assert congested(graph, n["S"], n["E"], only) is None

    def test_a_resource_with_a_blocking_holder_that_is_not_stranded_is_penalised_only(self) -> None:
        graph, n = arms()
        legs = {("S", "Q"): ((n["Q"], (H1, H2)),)}
        crowd = Crowd(graph, legs, holds={n["Q"]: 1.0}, stranded=(H1,))
        route = congested(graph, n["S"], n["E"], crowd)
        assert route is not None and route.nodes[1] is n["Q"] and route.cost == pytest.approx(20.0 + 2.0, rel=1e-12)

    def test_a_stranded_leg_is_excluded_by_the_turning_search_too(self) -> None:
        graph, n = corner(direct=26.0)
        turns = cart(turn_time=lambda angle: 4.0)
        stranded = Crowd(graph, {("A", "C"): ((n["C"], (H1,)),)}, stranded=(H1,))
        route = congested(graph, n["A"], n["C"], stranded, agv_type=turns)
        assert route is not None and route.nodes == (n["A"], n["B"], n["C"])
        only, ids = corner()
        cut = Crowd(only, {("B", "C"): ((ids["C"], (H1,)),)}, stranded=(H1,))
        assert congested(only, ids["A"], ids["C"], cut, agv_type=turns) is None

    def test_the_turning_search_adds_the_penalty_to_the_cost(self) -> None:
        graph, n = corner(direct=26.0)
        turns = cart(turn_time=lambda angle: 4.0)  # the corner: 19.2 s; the direct arc: 26 / 1.5 = 17.3 s
        around = fastest(*corner()[:1], n["A"], n["C"], turns)
        assert around is not None and around.nodes == (n["A"], n["B"], n["C"])
        crowd = Crowd(graph, {("A", "C"): ((n["C"], (H1,)),)}, holds={n["C"]: 1.5})
        route = congested(graph, n["A"], n["C"], crowd, agv_type=turns)
        assert route is not None and route.nodes == (n["A"], n["C"])
        assert route.cost == pytest.approx(26.0 / 1.5 + 1.5, rel=1e-12)  # 18.8 s, still below the corner
        crowd = Crowd(graph, {("A", "C"): ((n["C"], (H1,)),)}, holds={n["C"]: 4.0})
        assert congested(graph, n["A"], n["C"], crowd, agv_type=turns) == around  # 21.3 s: the corner, unpenalised

    def test_a_route_cut_to_a_simple_path_keeps_the_penalties_of_its_legs(self) -> None:
        graph, (a, x, y) = octagon()
        turns = cart(Plain(), turn_time=lambda angle: 100.0 if angle > 1.0 else 0.0)
        base = fastest(graph, x, y, turns)
        crowd = Crowd(graph, {("X", "A"): ((a, (H1,)),)}, holds={a: 3.0})
        route = congested(graph, x, y, crowd, agv_type=turns)
        assert base is not None and route is not None
        assert route.nodes == base.nodes == (x, a, y)
        assert route.cost == pytest.approx(base.cost + 3.0, rel=1e-12)

    def test_the_congestion_state_is_read_once_per_leg(self) -> None:
        """On a lattice full of equal paths, with a turning model that visits every leg from several states."""
        nodes = {f"{i}{j}": Node(f"{i}{j}", 10.0 * i, 10.0 * j) for i in range(4) for j in range(4)}
        arcs = [Arc(nodes[f"{i}{j}"], nodes[f"{i + 1}{j}"]) for i in range(3) for j in range(4)]
        arcs += [Arc(nodes[f"{i}{j}"], nodes[f"{i}{j + 1}"]) for i in range(4) for j in range(3)]
        graph = LayoutGraph(nodes.values(), arcs)
        blocked = {(u.id, v.id): ((v, (H1,)),) for u, v in [(nodes["00"], nodes["10"]), (nodes["01"], nodes["02"])]} | {
            ("11", "12"): ((nodes["12"], (H1, H2)), (nodes["11"], (H2,)))
        }
        crowd = Crowd(graph, blocked, {nodes["12"]: 1}, {nodes["10"]: 3.0, nodes["02"]: 2.0, nodes["12"]: 1.0})
        turns = cart(Plain(), turn_time=lambda angle: 1.0 if angle > 0.1 else 0.0)
        route = congested(graph, nodes["00"], nodes["33"], crowd, agv_type=turns)
        assert route is not None
        assert set(crowd.blockers_reads.values()) == {1}  # every leg once, though it is expanded from several states
        assert sum(crowd.blockers_reads.values()) > len(blocked)  # and legs that are free were asked too
        pairs = sum(len(blocked[leg]) for leg in crowd.blockers_reads if leg in blocked)
        assert pairs >= 3  # the blocked legs were expanded
        assert sum(crowd.pending_reads.values()) == sum(crowd.mean_hold_reads.values()) == pairs

    def test_no_penalty_without_an_agv_or_with_a_zero_weight(self) -> None:
        graph, n = arms()
        legs = {("S", "Q"): ((n["Q"], (H1,)),), ("Q", "E"): ((n["E"], (H2,)),)}
        crowd = Crowd(graph, legs, holds={n["Q"]: 100.0, n["E"]: 100.0}, stranded=(H1,))
        base = fastest(graph, n["S"], n["E"])
        assert base is not None and base.nodes[1] is n["Q"]
        assert congested(graph, n["S"], n["E"], crowd, wait_weight=0.0) == base  # not even the stranded exclusion
        request = RouteRequest(None, n["S"], n["E"])
        assert CongestionAwareRouter().route(request, crowd) == FastestRouter().route(request, crowd)  # type: ignore[arg-type]
        assert not crowd.blockers_reads

    @pytest.mark.parametrize(
        "base",
        [FastestRouter(), ShortestDistanceRouter(), ShortestDistanceRouter("astar")],
        ids=["fastest", "dijkstra", "astar"],
    )
    @pytest.mark.parametrize("turning", [False, True])
    def test_congestion_router_equals_base_without_traffic(self, base: Router, turning: bool) -> None:
        """Whatever the view reports, as long as nothing is inadmissible the route and its cost are the base's."""
        nodes, graph = grid()
        agv_type = cart(turn_time=lambda angle: 2.0 if angle > 0.1 else 0.0) if turning else cart()
        views = [free_view(graph), reservation_view(graph), Crowd(graph)]
        for view in views:
            plain = fastest(graph, nodes["A"], nodes["D"], agv_type, view=view, router=base)
            wrapped = fastest(graph, nodes["A"], nodes["D"], agv_type, view=view, router=CongestionAwareRouter(base))
            assert plain is not None and wrapped == plain
        octa, (a, x, y) = octagon()
        sharp = cart(Plain(), turn_time=lambda angle: 100.0 if angle > 1.0 else 0.0)
        assert fastest(octa, x, y, sharp, router=CongestionAwareRouter(base)) == fastest(octa, x, y, sharp, router=base)
        assert a in fastest(octa, x, y, sharp, router=base).nodes  # type: ignore[union-attr]

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_a_shortest_distance_base_costs_lengths_and_penalties(self, algorithm: str) -> None:
        graph, n = arms()
        crowd = Crowd(graph, {("S", "P"): ((n["P"], (H1,)),)}, holds={n["P"]: 3.0})
        router = CongestionAwareRouter(ShortestDistanceRouter(algorithm), horizon=1000.0)
        route = fastest(graph, n["S"], n["E"], view=crowd, router=router)
        assert route is not None and route.nodes == (n["S"], n["P"], n["E"])
        assert route.cost == pytest.approx(20.0 + 3.0, rel=1e-12)
        heavy = Crowd(graph, {("S", "P"): ((n["P"], (H1,)),)}, holds={n["P"]: 30.0})
        route = fastest(graph, n["S"], n["E"], view=heavy, router=router)
        assert route is not None and route.nodes == (n["S"], n["Q"], n["E"])
        assert route.cost == graph.path_length(route.nodes)

    def test_avoid_still_excludes_legs(self) -> None:
        graph, n = arms()
        route = congested(graph, n["S"], n["E"], Crowd(graph), avoid=frozenset({n["Q"]}))
        assert route is not None and route.nodes[1] is n["P"]

    def test_the_arm_with_a_serving_agv_is_taken_only_when_the_other_costs_more_than_the_wait(self) -> None:
        """``P`` was held 40 s and a serving AGV holds it: the leg into ``P`` costs 40 at ``t = 0``."""
        w, route = occupied_arm(15.0, AGVState.WAITING_LOAD)  # the other arm takes 6.7 s longer
        nodes = w.nodes
        assert route is not None and route.nodes == (nodes["S"], nodes["Q"], nodes["E"])
        assert route.cost == pytest.approx(2 * 15.0 / 1.5, rel=1e-12)
        w, route = occupied_arm(50.0, AGVState.WAITING_LOAD)  # the other arm takes 53.3 s longer
        assert route is not None and route.nodes == (nodes["S"], nodes["P"], nodes["E"])
        assert route.cost == pytest.approx(2 * 10.0 / 1.5 + 40.0, rel=1e-12)
        assert w.controller.view.mean_hold(w.nodes["P"]) == 40.0

    def test_a_node_filled_by_a_stranded_agv_is_avoided_and_may_be_the_only_way(self) -> None:
        w, route = occupied_arm(50.0, AGVState.STRANDED)
        assert route is not None and route.nodes == (w.nodes["S"], w.nodes["Q"], w.nodes["E"])
        assert route.cost == pytest.approx(2 * 50.0 / 1.5, rel=1e-12)
        w = world(line(3), {"V": "N0", "B": "N1"}, derive_corridors=False, hold_back_dead_ends=False)
        request = RouteRequest(w.agvs["V"], w.nodes["N0"], w.nodes["N2"])
        assert CongestionAwareRouter().route(request, w.controller.view) is not None  # B is not stranded
        w.agvs["B"]._state = AGVState.STRANDED
        assert CongestionAwareRouter().route(request, w.controller.view) is None
        assert FastestRouter().route(request, w.controller.view) is not None


def congested_run(*, probe: bool = False, check: bool = False, router: Router | None = None) -> str:
    """The digest of a small run in which the congestion router meets traffic: three AGVs on the two-arm world. With
    `check` every event is replayed against the live state and the traffic invariants are asserted."""
    graph, nodes = two_arms(15.0)
    env = Environment(debug=True)
    env.enable_digest()
    replay = FullReplay(env) if check else None
    controller = ReservationController(env=env, graph=graph, derive_corridors=False, hold_back_dead_ends=False)
    vehicles = {
        name: AGV(env=env, agv_type=vehicle(), agv_id=name, initial_node=nodes[start])
        for name, start in {"A": "S", "B": "K", "C": "Q"}.items()
    }
    fleet = StillFleet(
        env=env,
        graph=graph,
        fleet=list(vehicles.values()),
        warehouses=[],
        charging_stations=[],
        traffic_controller=controller,
        router=router or CongestionAwareRouter(),
        default_metrics=False,
    )
    if check:
        TrafficInvariants(env, controller)
    env.activate()
    if probe:
        view = controller.view

        def read(event: Any) -> None:
            for u in graph.nodes:
                for v in graph.neighbors(u):
                    for resource, holders in view.blockers(vehicles["A"], u, v):
                        view.pending(resource), view.mean_hold(resource), [view.disposition(h) for h in holders]

        env.bus.subscribe(read, "*")
    w = World(env, fleet, nodes, vehicles, None, 0)  # type: ignore[arg-type]
    w.go("A", "E")
    w.go("B", "P", at=20.0)
    w.go("C", "K", at=20.0)
    env.run()
    assert set(w.outcomes) == {"A", "B", "C"}
    assert replay is None or replay.of(AgvRouted)
    digest = env.fingerprint().digest
    assert digest is not None
    return digest


def test_a_run_with_the_congestion_router_has_the_same_digest_across_hash_seeds() -> None:
    expected = congested_run()
    script = "from tests.intralogistics.test_routing import congested_run;print('DIGEST', congested_run())"
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


@pytest.mark.parametrize("router", [ShortestDistanceRouter(), FastestRouter()], ids=["shortest", "fastest"])
def test_reading_the_traffic_view_changes_no_result(router: Router) -> None:
    """``mean_hold`` and the other reads of the view are bookkeeping only: a run in which every event is followed by
    reads of the whole view has the digest of a run that never reads it."""
    assert congested_run(probe=True, router=router) == congested_run(router=router)


@pytest.mark.parametrize("router", [FastestRouter(), CongestionAwareRouter()], ids=["fastest", "congestion"])
def test_a_run_with_a_routing_router_replays_and_keeps_the_invariants(router: Router) -> None:
    """Replay equals the live state at every event and the traffic invariants hold under each new router, and the
    digest is that of the run without these observers."""
    assert congested_run(check=True, router=router) == congested_run(router=router)
