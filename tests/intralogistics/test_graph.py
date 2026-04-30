from __future__ import annotations

import pytest

from simulatte.intralogistics.graph import Arc, LayoutGraph, Node


class TestNode:
    def test_creation(self) -> None:
        node = Node(id="N1", x=0.0, y=0.0)
        assert node.id == "N1"
        assert node.x == 0.0
        assert node.y == 0.0

    def test_frozen(self) -> None:
        node = Node(id="N1", x=0.0, y=0.0)
        with pytest.raises(AttributeError):
            node.x = 1.0  # type: ignore[misc]

    def test_hashable(self) -> None:
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N1", x=0.0, y=0.0)
        assert n1 == n2
        assert len({n1, n2}) == 1


class TestArc:
    def test_bidirectional_default(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        arc = Arc(source=n1, target=n2)
        assert arc.bidirectional is True
        assert arc.speed_limit is None

    def test_unidirectional(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        arc = Arc(source=n1, target=n2, bidirectional=False)
        assert arc.bidirectional is False

    def test_speed_limit(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        arc = Arc(source=n1, target=n2, speed_limit=2.5)
        assert arc.speed_limit == 2.5


class TestLayoutGraph:
    def _make_line_graph(self) -> tuple[list[Node], LayoutGraph]:
        nodes = [Node(id=f"N{i}", x=float(i), y=0.0) for i in range(4)]
        arcs = [Arc(source=nodes[i], target=nodes[i + 1]) for i in range(3)]
        return nodes, LayoutGraph(nodes, arcs)

    def test_neighbors_bidirectional(self) -> None:
        nodes, graph = self._make_line_graph()
        assert set(graph.neighbors(nodes[1])) == {nodes[0], nodes[2]}

    def test_neighbors_unidirectional(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2, bidirectional=False)])
        assert graph.neighbors(n1) == [n2]
        assert graph.neighbors(n2) == []

    def test_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        assert graph.distance(n1, n2) == pytest.approx(5.0)

    def test_distance_not_connected_raises(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        with pytest.raises(ValueError, match="not connected"):
            graph.distance(n1, n2)

    def test_arc_between(self) -> None:
        nodes, graph = self._make_line_graph()
        arc = graph.arc_between(nodes[0], nodes[1])
        assert arc is not None
        assert arc.source == nodes[0]
        assert arc.target == nodes[1]

    def test_arc_between_bidirectional_reverse(self) -> None:
        nodes, graph = self._make_line_graph()
        arc = graph.arc_between(nodes[1], nodes[0])
        assert arc is not None

    def test_arc_between_none(self) -> None:
        nodes, graph = self._make_line_graph()
        assert graph.arc_between(nodes[0], nodes[2]) is None

    def test_nodes_returns_frozenset_of_all_nodes(self) -> None:
        nodes, graph = self._make_line_graph()
        result = graph.nodes
        assert isinstance(result, frozenset)
        assert result == frozenset(nodes)

    def test_nodes_frozenset_is_defensive_copy(self) -> None:
        nodes, graph = self._make_line_graph()
        result1 = graph.nodes
        result2 = graph.nodes
        assert result1 == result2
        assert result1 is not graph._nodes


class TestPathDistance:
    def test_single_arc_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        assert LayoutGraph.path_distance([n1, n2]) == pytest.approx(5.0)

    def test_multi_arc_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=0.0)
        n3 = Node(id="C", x=3.0, y=4.0)
        assert LayoutGraph.path_distance([n1, n2, n3]) == pytest.approx(7.0)

    def test_single_node_returns_zero(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        assert LayoutGraph.path_distance([n1]) == 0.0

    def test_empty_path_returns_zero(self) -> None:
        assert LayoutGraph.path_distance([]) == 0.0
