from dataclasses import FrozenInstanceError
from fractions import Fraction
from collections.abc import Mapping
import math

import pytest

from simulatte.layout import Connector, Explicit, Footprint, Grid, Lanes, Lattice, LayoutLimits, Point, Port
from simulatte.layout.types import placement_defaults, normalize_rotation
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node


def test_snap_exact_ties_and_adjacent_float():
    grid = Grid(1, 1)
    assert grid.snap(Point(0.5, -0.5)) == Point(1, 0)
    assert grid.snap(Point(0.49999999999999994, 0)) == Point(0, 0)
    grid = Grid(0.3, 0.7, Point(0.1, -0.2))
    point = Point(0.55, -0.55)

    def snap(value, origin, spacing):
        q = (Fraction(value) - Fraction(origin)) / Fraction(spacing)
        index = q.numerator // q.denominator
        index += q - index >= Fraction(1, 2)
        return float(Fraction(origin) + index * Fraction(spacing))

    assert grid.snap(point) == Point(snap(point.x, 0.1, 0.3), snap(point.y, -0.2, 0.7))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Grid(0, 1),
        lambda: Grid(1, -1),
        lambda: Point(0, 0, 1),
        lambda: Point(True, 0),
        lambda: Point(float("inf"), 0),
        lambda: Point(float("nan"), 0),
        lambda: Footprint(0, 1),
        lambda: Footprint(1, -1),
        lambda: Footprint(1, 1, -1),
        lambda: Connector(approach_margin=0),
        lambda: Connector(approach_margin=-1),
        lambda: Connector(max_distance=0),
        lambda: Connector(speed_limit=0),
        lambda: Port(Point(0, 0), Point(0, 0)),
        lambda: Lattice((1, 0, 0, 1)),
        lambda: Lattice((0, 0, 1, 1), neighbors=True),  # ty: ignore[invalid-argument-type]  # Exercise invalid runtime inputs.
        lambda: LayoutLimits(max_nodes=True),
    ],
)
def test_invalid_values(factory):
    with pytest.raises((ValueError, TypeError)):
        factory()


def test_defaults_normalization_and_immutability():
    assert normalize_rotation(-90) == 270
    assert math.copysign(1, Point(-0.0, 0).x) == 1
    assert Connector().approach_margin == 0.01
    assert Connector().direction == "both"
    assert LayoutLimits().max_file_bytes == 16 * 1024 * 1024
    for kind in ("server", "psp", "warehouse", "charging_station", "parking_area"):
        assert placement_defaults(kind).auto_position
        assert placement_defaults(kind).footprint == Footprint(1, 1)
    for kind in ("agv", "node"):
        assert not placement_defaults(kind).auto_position
    assert placement_defaults("node").footprint == Footprint(0.1, 0.1)
    for kind in ("shopfloor", "router", "fleet", "job", "order"):
        assert not placement_defaults(kind).placeable
        assert placement_defaults(kind).footprint is None
    assert placement_defaults("custom").placeable
    assert not placement_defaults("custom").auto_position
    with pytest.raises(FrozenInstanceError):
        Point(0, 0).x = 2  # ty: ignore[invalid-assignment]  # Frozen assignment must fail.


def test_networks_copy_caller_data_and_effective_graph():
    lanes = {"road": {"vertices": [{"id": "a", "position": [0, 0, 0]}, {"id": "b", "position": [1, 0, 0]}]}}
    spec = Lanes(lanes)
    lanes["road"]["vertices"][0]["position"][0] = 99
    assert spec.lanes["road"]["vertices"][0]["position"][0] == 0
    a, b = Node("a", 0, 0), Node("b", 1, 0)
    graph = LayoutGraph([a, b], [Arc(a, b, speed_limit=1), Arc(a, b, False, 2)])
    explicit = Explicit(graph)
    assert [(edge.source, edge.target, edge.speed_limit) for edge in explicit.arcs] == [("a", "b", 2), ("b", "a", 1)]
    graph._adjacency[a].clear()
    assert len(explicit.arcs) == 2


def test_snapshot_does_not_mutate_graph_even_for_isolated_nodes():
    node = Node("a", 0, 0)
    graph = LayoutGraph([node], [])
    before = dict(graph._adjacency)
    assert Explicit(graph).nodes[0].position == Point(0, 0)
    assert dict(graph._adjacency) == before


def test_connector_margin_cannot_be_none():
    with pytest.raises(ValueError):
        Connector(approach_margin=None)  # ty: ignore[invalid-argument-type]  # Exercise invalid runtime inputs.


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Point(10**309, 0),
        lambda: Grid(1, 1, origin=(0, 0)),  # ty: ignore[invalid-argument-type]  # Exercise invalid runtime inputs.
        lambda: Connector(target="a"),  # ty: ignore[invalid-argument-type]  # Invalid runtime input.
        lambda: Connector(enabled=1),  # ty: ignore[invalid-argument-type]  # Invalid runtime input.
        lambda: Connector(direction="invalid"),  # ty: ignore[invalid-argument-type]  # Exercise invalid runtime inputs.
        lambda: Port((0, 0), Point(1, 0)),  # ty: ignore[invalid-argument-type]  # Invalid runtime input.
        lambda: Port(Point(0, 0), Point(1, 0), connector={}),  # ty: ignore[invalid-argument-type]  # Exercise invalid runtime inputs.
        lambda: Lattice((0, 0, 1, 1), capacity=0),
        lambda: Lanes({}, capacity=0),
        lambda: Explicit(LayoutGraph([Node("a", 0, 0), Node("a", 1, 1)], [])),
        lambda: Explicit(LayoutGraph([Node("a", 0, 0)], [Arc(Node("a", 0, 0), Node("b", 1, 0))])),
        lambda: Explicit(LayoutGraph([Node("a", 0, 0)], [Arc(Node("b", 1, 0), Node("a", 0, 0), False)])),
        lambda: Explicit(LayoutGraph([Node("a", 0, 0), Node("b", 0, 0)], [Arc(Node("a", 0, 0), Node("b", 0, 0))])),
        lambda: Explicit(LayoutGraph([], []), capacity=0),
    ],
)
def test_definition_errors(factory):
    with pytest.raises(ValueError):
        factory()


def test_network_values_overrides_and_defaults_are_immutable():
    from simulatte.layout import Address, NetworkOverride

    lattice = Lattice(
        (0, 0, 1, 1),
        capacity=2,
        speed_limit=3,
        blocked={"wall": {"position": [0, 0, 0], "width": 1, "depth": 1, "rotation": 0}},
    )
    assert lattice.bounds == (0.0, 0.0, 1.0, 1.0)
    assert lattice.clearance == 0
    with pytest.raises(TypeError):
        lattice.blocked["wall"]["width"] = 2
    props = {"capacity": 2}
    target = {"kind": "row", "j": -1}
    override = NetworkOverride("o", target, props)
    target["j"] = 10
    props["capacity"] = 8
    assert override.properties["capacity"] == 2
    assert isinstance(override.target, Mapping)
    assert override.target["j"] == -1
    by_address = NetworkOverride("node", Address({"kind": "explicit", "id": "a"}), {})
    assert isinstance(by_address.target, Mapping)
    assert by_address.target["id"] == "a"
    defaults = placement_defaults("server")
    assert defaults.rotation == 0 and defaults.obstacle is False
    assert defaults.ports == defaults.visual == {}
    assert Lanes({}, capacity=2).capacity == 2
    assert Explicit(LayoutGraph([], []), capacity=2).capacity == 2
    a, b = Node("a", 0, 0), Node("b", 1, 0)
    assert Explicit(LayoutGraph([a, b], [Arc(a, b)])).arcs[0].speed_limit is None


def test_snap_overflow_is_a_layout_error():
    with pytest.raises(ValueError, match="finite float64"):
        Grid(1.1e308, 1).snap(Point(1.79e308, 0))


def test_visual_and_connected_ports():
    from simulatte.layout import Address, NodeRef

    visual = Port(Point(1, 0), Point(2, 0))
    assert visual.connector is None
    target = NodeRef(object(), Address({"kind": "explicit", "id": "a"}))
    connector = Connector(target, max_distance=3, speed_limit=2)
    connected = Port(Point(1, 0), Point(1, 0), connector)
    assert connected.connector is not None
    assert connected.connector.target is target


def test_custom_kinds_enter_auto_layout_only_after_explicit_placement():
    assert not placement_defaults("custom").auto_position
    assert placement_defaults("custom", explicitly_placed=True).auto_position
    assert not placement_defaults("agv", explicitly_placed=True).auto_position


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Lattice((0, 0, 1, 1), enabled={"$unset": True}),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Lattice((0, 0, 1, 1), capacity={"$unset": True}),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Lattice((0, 0, 1, 1), speed_limit={"$unset": True}),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Lanes({}, capacity={"$unset": True}),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Explicit(LayoutGraph([], []), capacity={"$unset": True}),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Lanes({"a": {"vertices": {"$unset": True}}}),
        lambda: Lanes({"a": {}}),
        lambda: Lattice((0, 0, 1, 1), blocked={"a": {"width": {"$unset": True}}}),
        lambda: Lattice((0, 0, 1, 1), blocked={"a": {"width": 1}}),
        lambda: Lanes({}, capacity=1.5),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
        lambda: Lattice((0, 0, 1, 1), capacity=1.0),  # ty: ignore[invalid-argument-type]  # Reject patch values at code boundary.
    ],
)
def test_complete_code_definitions_reject_patch_sentinels_and_missing_inputs(factory):
    with pytest.raises(ValueError):
        factory()


def test_code_overrides_remain_patches_and_copy_unset_leaves():
    from simulatte.layout import NetworkOverride

    props = {"speed_limit": {"$unset": True}}
    override = NetworkOverride("o", {"kind": "lane", "id": "a"}, props)
    props["speed_limit"]["$unset"] = False
    assert override.properties["speed_limit"]["$unset"] is True
    with pytest.raises(TypeError):
        override.properties["speed_limit"]["$unset"] = False


@pytest.mark.parametrize("value", [2**53, 2**53 + 1, 10**308])
def test_geometry_accepts_large_finite_integer_notation(value):
    expected = float(value)
    assert Point(value, -value) == Point(expected, -expected)
    assert Grid(value, 1).dx == expected
    assert Footprint(value, value, value) == Footprint(expected, expected, expected)
    assert Connector(approach_margin=value, max_distance=value, speed_limit=value) == Connector(
        approach_margin=expected, max_distance=expected, speed_limit=expected
    )


@pytest.mark.parametrize("factory", [lambda: LayoutLimits(max_nodes=2**53), lambda: Lanes({}, capacity=2**53)])
def test_counts_keep_safe_integer_bounds(factory):
    with pytest.raises(ValueError, match="safe integer"):
        factory()


@pytest.mark.parametrize("axis", ["x", "y"])
def test_explicit_rejects_edges_collapsed_by_float64_normalization(axis):
    from simulatte.layout import LayoutError

    a = Node("a", 2**53 if axis == "x" else 0, 2**53 if axis == "y" else 0)
    b = Node("b", 2**53 + 1 if axis == "x" else 0, 2**53 + 1 if axis == "y" else 0)
    graph = LayoutGraph([a, b], [Arc(a, b, bidirectional=False)])
    assert (a.x, a.y) != (b.x, b.y)
    assert Point(a.x, a.y) == Point(b.x, b.y)
    with pytest.raises(LayoutError, match="zero-length.*float64"):
        Explicit(graph)
    assert graph.nodes == (a, b)
    assert graph.neighbors(a) == [b]


@pytest.mark.parametrize("axis", ["x", "y"])
def test_explicit_keeps_large_edges_with_distinct_stored_positions(axis):
    a = Node("a", 2**53 if axis == "x" else 0, 2**53 if axis == "y" else 0)
    b = Node("b", 2**53 + 2 if axis == "x" else 0, 2**53 + 2 if axis == "y" else 0)
    snapshot = Explicit(LayoutGraph([a, b], [Arc(a, b, bidirectional=False)]))
    assert snapshot.nodes[0].position != snapshot.nodes[1].position
    assert [(arc.source, arc.target) for arc in snapshot.arcs] == [("a", "b")]


def test_explicit_snapshot_endpoint_membership_is_linear(monkeypatch):
    nodes = [Node(f"n{i}", i, 0) for i in range(100)]
    graph = LayoutGraph(nodes, [Arc(a, b) for a, b in zip(nodes, nodes[1:])])
    equality_calls = 0
    original = Node.__eq__

    def counted(self, other):
        nonlocal equality_calls
        equality_calls += 1
        return original(self, other)

    monkeypatch.setattr(Node, "__eq__", counted)
    snapshot = Explicit(graph)
    assert len(snapshot.nodes) == 100 and len(snapshot.arcs) == 198
    assert equality_calls < 1000
