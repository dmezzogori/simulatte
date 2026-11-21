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
            node.x = 1.0  # ty: ignore[invalid-assignment]

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

    def test_nodes_returns_tuple_in_insertion_order(self) -> None:
        nodes, graph = self._make_line_graph()
        result = graph.nodes
        assert isinstance(result, tuple)
        assert result == tuple(nodes)

    def test_nodes_keeps_first_occurrence_of_duplicates(self) -> None:
        a, b = Node(id="B", x=1.0, y=0.0), Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([a, b, a], [])
        assert graph.nodes == (a, b)

    def test_nodes_tuple_is_defensive_copy(self) -> None:
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


class TestArcLength:
    def test_arc_length_defaults_to_chord(self) -> None:
        arc = Arc(Node("A", 0.0, 0.0), Node("B", 3.0, 4.0))
        assert arc.length == 5.0
        assert arc.passing is False

    @pytest.mark.parametrize("length", [4.9, float("inf"), float("nan")])
    def test_arc_length_shorter_than_chord_raises(self, length: float) -> None:
        with pytest.raises(ValueError):
            Arc(Node("A", 0.0, 0.0), Node("B", 3.0, 4.0), length=length)

    def test_length_without_arc_raises(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0)
        with pytest.raises(ValueError):
            LayoutGraph([a, b], []).length(a, b)

    @pytest.mark.parametrize(("other_length", "other_passing"), [(7.0, False), (6.0, True)])
    def test_conflicting_arcs_between_same_nodes_raise(self, other_length: float, other_passing: bool) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0)
        forward = Arc(a, b, bidirectional=False, length=6.0)
        backward = Arc(b, a, bidirectional=False, length=other_length, passing=other_passing)
        with pytest.raises(ValueError):
            LayoutGraph([a, b], [forward, backward])
        with pytest.raises(ValueError):
            LayoutGraph._from_layout([a, b], [forward, backward])

    def test_agreeing_pair_is_accepted(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0)
        arcs = [Arc(a, b, bidirectional=False, length=6.0), Arc(b, a, bidirectional=False, length=6.0)]
        assert LayoutGraph._from_layout([a, b], arcs).length(b, a) == 6.0
        assert LayoutGraph([a, b], arcs).length(a, b) == 6.0

    def test_path_length_sums_arc_lengths(self) -> None:
        a, b, c = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), Node("C", 1.0, 1.0)
        graph = LayoutGraph([a, b, c], [Arc(a, b, length=2.0), Arc(b, c)])
        assert graph.path_length([a, b, c]) == 3.0
        assert graph.path_length([a]) == 0.0
        assert graph.distance(a, b) == 1.0


class TestSubdivide:
    def _graph(self) -> tuple[Node, Node, LayoutGraph]:
        a, b = Node("A", 0.0, 0.0), Node("B", 9.0, 0.0)
        return a, b, LayoutGraph([a, b], [Arc(a, b, speed_limit=1.0, length=10.0, passing=True)])

    def test_splits_long_arc(self) -> None:
        a, b, graph = self._graph()
        result = graph.subdivide(4.0)
        assert result.nodes[0] is a and result.nodes[1] is b
        assert [n.id for n in result.nodes] == ["A", "B", "A~B#1", "A~B#2"]
        assert [(n.x, n.y) for n in result.nodes[2:]] == [(3.0, 0.0), (6.0, 0.0)]
        n1, n2 = result.nodes[2:]
        for src, dst in [(a, n1), (n1, n2), (n2, b), (b, n2), (n2, n1), (n1, a)]:
            arc = result.arc_between(src, dst)
            assert arc is not None
            assert arc.length == pytest.approx(10 / 3)
            assert arc.speed_limit == 1.0 and arc.passing is True
        assert result.arc_between(a, b) is None

    def test_short_arc_untouched(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 4.0, 0.0)
        arc = Arc(a, b)
        result = LayoutGraph([a, b], [arc]).subdivide(4.0)
        assert result.nodes == (a, b)
        assert result.arc_between(a, b) is arc

    def test_one_way_pair_from_larger_id_shares_nodes(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 6.0, 0.0)
        graph = LayoutGraph([a, b], [Arc(b, a, bidirectional=False), Arc(a, b, bidirectional=False)])
        result = graph.subdivide(4.0)
        assert [n.id for n in result.nodes] == ["A", "B", "A~B#1"]
        mid = result.nodes[2]
        assert (mid.x, mid.y) == (3.0, 0.0)
        for src, dst in [(a, mid), (mid, b), (b, mid), (mid, a)]:
            assert result.arc_between(src, dst) is not None

    @pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf")])
    def test_invalid_max_length_raises(self, bad: float) -> None:
        with pytest.raises(ValueError):
            self._graph()[2].subdivide(bad)

    def test_layout_frozen_graph_raises(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0)
        frozen = LayoutGraph._from_layout([a, b], [Arc(a, b, bidirectional=False)])
        with pytest.raises(ValueError):
            frozen.subdivide(1.0)

    def test_arcs_with_endpoints_outside_nodes_are_kept(self) -> None:
        a, b, c = Node("A", 0.0, 0.0), Node("B", 6.0, 0.0), Node("C", 6.0, 6.0)
        graph = LayoutGraph([a], [Arc(a, b), Arc(b, c)])
        result = graph.subdivide(4.0)
        assert [n.id for n in result.nodes] == ["A", "A~B#1", "B~C#1"]
        ab, bc = result.nodes[1], result.nodes[2]
        for src, dst in [(a, ab), (ab, b), (b, ab), (ab, a), (b, bc), (bc, c), (c, bc), (bc, b)]:
            assert result.arc_between(src, dst) is not None

    def test_generated_nodes_follow_arc_order(self) -> None:
        a, b, c = Node("A", 0.0, 0.0), Node("B", 6.0, 0.0), Node("C", 6.0, 6.0)
        result = LayoutGraph([a, b, c], [Arc(b, c, length=8.0), Arc(a, b)]).subdivide(4.0)
        assert [n.id for n in result.nodes] == ["A", "B", "C", "B~C#1", "A~B#1"]
