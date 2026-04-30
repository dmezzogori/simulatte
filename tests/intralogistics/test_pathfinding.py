from __future__ import annotations


from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.pathfinding import AStarPlanner, DijkstraPlanner


def _make_grid() -> tuple[dict[str, Node], LayoutGraph]:
    """2x2 grid: A(0,0) -- B(1,0)
     |          |
    C(0,1) -- D(1,1)"""
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


class TestDijkstraPlanner:
    def test_shortest_path(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"])
        assert path is not None
        assert path[0] == nodes["A"]
        assert path[-1] == nodes["D"]

    def test_no_path(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        planner = DijkstraPlanner()
        assert planner.plan(graph, n1, n2) is None

    def test_avoid_nodes(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"]])
        assert path is not None
        assert nodes["B"] not in path
        assert path == [nodes["A"], nodes["C"], nodes["D"]]

    def test_avoid_makes_unreachable(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"], nodes["C"]])
        assert path is None

    def test_same_node(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["A"])
        assert path == [nodes["A"]]


class TestAStarPlanner:
    def test_shortest_path(self) -> None:
        nodes, graph = _make_grid()
        planner = AStarPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"])
        assert path is not None
        assert path[0] == nodes["A"]
        assert path[-1] == nodes["D"]

    def test_avoid_nodes(self) -> None:
        nodes, graph = _make_grid()
        planner = AStarPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"]])
        assert path is not None
        assert nodes["B"] not in path

    def test_no_path(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        planner = AStarPlanner()
        assert planner.plan(graph, n1, n2) is None


class TestLayoutGraphShortestPath:
    def test_shortest_path_delegates_to_dijkstra(self) -> None:
        nodes, graph = _make_grid()
        path = graph.shortest_path(nodes["A"], nodes["D"])
        assert path is not None
        assert path == [nodes["A"], nodes["B"], nodes["D"]] or path == [nodes["A"], nodes["C"], nodes["D"]]

    def test_shortest_path_no_route(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        assert graph.shortest_path(n1, n2) is None

    def test_shortest_path_same_node(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        assert graph.shortest_path(n1, n1) == [n1]
