from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import Corridor, Segment, Zone
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, Router, RouteRequest, ShortestDistanceRouter, shortest_path
from simulatte.intralogistics.traffic import FreeFlowController, TrafficView
from tests.intralogistics.traffic_support import build, two_junction_corridor

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
