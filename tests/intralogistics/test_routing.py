from __future__ import annotations

import math
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics import FastestRouter
from simulatte.intralogistics.agv import AGVType
from simulatte.intralogistics.events import AgvRouted, TrafficWaitEnded
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.kinematics import Kinematics
from simulatte.intralogistics.network import Corridor, Segment, Zone
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, Router, RouteRequest, ShortestDistanceRouter, shortest_path
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeFlowController, Reroute, TrafficView
from simulatte.intralogistics.travel import TravelOutcome
from tests.intralogistics.test_travel import Scripted, diamond, world
from tests.intralogistics.traffic_support import build, line, two_junction_corridor

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


def fastest(
    graph: LayoutGraph,
    origin: Node,
    destination: Node,
    agv_type: AGVType | None = None,
    *,
    level: float = 1.0,
    heading: float | None = None,
    view: TrafficView | None = None,
    **fields: Any,
) -> Route | None:
    agv = SimpleNamespace(agv_type=agv_type or cart(), battery=SimpleNamespace(level_pct=level), heading=heading)
    request = RouteRequest(agv=agv, origin=origin, destination=destination, **fields)  # type: ignore[arg-type]
    return FastestRouter().route(request, view or free_view(graph))


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
        graph = LayoutGraph([a, x, y, *ring[1:]], arcs)
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
