from __future__ import annotations

import math
import operator
from collections.abc import Callable
from typing import Any

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

    def test_hash_is_the_hash_of_the_id(self) -> None:
        """Equal nodes have equal ids, so a hash of the id alone agrees with equality; it is cheaper than hashing the
        coordinates too, and graphs hash nodes on every dict access."""
        assert hash(Node(id="N1", x=0.0, y=0.0)) == hash("N1")
        assert hash(Node(id="N1", x=3.0, y=4.0)) == hash("N1")
        assert Node(id="N1", x=0.0, y=0.0) != Node(id="N1", x=3.0, y=4.0)


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

    @pytest.mark.parametrize("limit", [0.0, -1.0, float("nan"), float("inf")])
    def test_speed_limit_must_be_positive_and_finite(self, limit: float) -> None:
        with pytest.raises(ValueError, match=r"A->B speed_limit .* must be positive and finite"):
            Arc(Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), speed_limit=limit)


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

    @pytest.mark.parametrize("offset", [0.0, 5e6])
    def test_arc_length_below_the_chord_by_more_than_the_tolerance_raises(self, offset: float) -> None:
        a, b = Node("A", offset, offset), Node("B", offset + 3.0, offset + 4.0)
        for length in (5.0 - 1e-6, 5.0 * (1.0 - 1e-7)):
            with pytest.raises(ValueError, match="must be finite and not smaller than the chord"):
                Arc(a, b, length=length)

    def test_arc_length_tolerates_rounding_at_the_magnitude_of_large_coordinates(self) -> None:
        """Points generated at georeferenced coordinates carry the spacing of the floats there (9.3e-10 at 5e6), so
        a length computed along the way can fall that far under the chord of the points; near the origin the
        tolerance stays the relative 1e-9."""
        a, b = Node("A", 5e6, 5e6), Node("B", 5e6 + 0.09, 5e6)
        chord = math.hypot(b.x - a.x, b.y - a.y)
        short = chord - 2 * math.ulp(5e6)
        assert short < chord * (1.0 - 1e-9)
        assert Arc(a, b, length=short).length == short
        near = Node("A", 0.0, 0.0), Node("B", 0.09, 0.0)
        with pytest.raises(ValueError):
            Arc(*near, length=0.09 - 2 * math.ulp(5e6))

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


class TestLayoutBuiltGraphIsReadOnly:
    """A graph built by the layout resolves its adjacency into containers that refuse every change (spec §5.2), and
    its lookups cost what an explicit graph's do."""

    def _frozen(self) -> tuple[Node, Node, Node, LayoutGraph]:
        a, b, c = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0), Node("C", 0.0, 1.0)
        return (
            a,
            b,
            c,
            LayoutGraph._from_layout([a, b, c], [Arc(a, c, False, 2), Arc(a, b, False, 1), Arc(b, a, False, 3)]),
        )

    def test_every_mutation_of_the_nodes_and_the_adjacency_raises(self) -> None:
        a, b, c, graph = self._frozen()
        extra = Arc(c, a, False)
        containers: list[Any] = [graph._nodes, graph._adjacency, graph._adjacency[a], graph._adjacency[b]]
        mutations: list[Callable[[Any], object]] = [
            lambda d: operator.setitem(d, a, extra),
            lambda d: operator.delitem(d, a),
            lambda d: d.clear(),
            lambda d: d.__init__({c: extra}),
            lambda d: d.pop(a, None),
            lambda d: d.popitem(),
            lambda d: d.setdefault(c, extra),
            lambda d: d.update({c: extra}),
            lambda d: operator.ior(d, {c: extra}),
        ]
        snapshot = (dict(graph._nodes), {node: dict(edges) for node, edges in graph._adjacency.items()})
        for container in containers:
            for mutate in mutations:
                with pytest.raises(TypeError, match="read-only"):
                    mutate(container)
        assert (dict(graph._nodes), {node: dict(edges) for node, edges in graph._adjacency.items()}) == snapshot
        assert graph.nodes == (a, b, c)

    def test_the_plain_containers_the_lookups_read_agree_with_the_read_only_ones(self) -> None:
        a, b, c, graph = self._frozen()
        assert type(graph._lookup) is dict and all(type(edges) is dict for edges in graph._lookup.values())
        assert list(graph._lookup) == list(graph._adjacency) == list(graph._nodes) == [a, b, c]
        for node in graph._adjacency:
            assert list(graph._lookup[node].items()) == list(graph._adjacency[node].items())
            assert graph.neighbors(node) == list(graph._adjacency[node])
            for other in graph._nodes:
                assert graph.arc_between(node, other) is graph._adjacency[node].get(other)
        assert not any(plain is shown for plain, shown in zip(graph._lookup.values(), graph._adjacency.values()))
        explicit = LayoutGraph([a, b], [Arc(a, b)])
        assert explicit._lookup is explicit._adjacency  # a graph written by the user reads what it shows

    def test_the_public_surface_does_not_change_it(self) -> None:
        a, b, c, graph = self._frozen()
        with pytest.raises(AttributeError):
            graph.nodes = ()  # ty: ignore[invalid-assignment]  # nodes is a read-only property
        with pytest.raises(ValueError, match="cannot be subdivided"):
            graph.subdivide(0.5)
        graph.neighbors(a).append(c)  # a list copy
        assert graph.neighbors(a) == [c, b]

    def test_lookups_of_nodes_outside_the_graph_answer_without_inserting(self) -> None:
        a, b, c, graph = self._frozen()
        unknown = Node("unknown", 9.0, 9.0)
        before = list(graph._adjacency)
        assert graph.neighbors(unknown) == []
        assert graph.arc_between(unknown, a) is None
        assert graph.arc_between(a, unknown) is None
        assert graph.arc_between(c, a) is None  # a node of the graph without an arc that way
        assert graph.neighbors(c) == []
        assert list(graph._adjacency) == before and unknown not in graph._adjacency
        assert graph.arc_between(a, b) == Arc(a, b, False, 1)

    def test_a_graph_written_by_the_user_stays_mutable(self) -> None:
        a, b = Node("A", 0.0, 0.0), Node("B", 1.0, 0.0)
        graph = LayoutGraph([a, b], [Arc(a, b)])
        graph._adjacency[a].clear()  # as before: only layout-built graphs refuse
        assert graph.neighbors(a) == []
        assert graph.subdivide(0.5).nodes[:2] == (a, b)
