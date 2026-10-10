from typing import Any, cast

import pytest

from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.network import generate_base_network
from simulatte.layout.refs import Address
from simulatte.layout.types import LayoutLimits


def values(network, grid=None, placements=None):
    data = {"schema_version": "1.0", "network": network}
    if grid is not None:
        data["grid"] = grid
    if placements is not None:
        data["placements"] = placements
    return resolve_layers(
        (("declaration", data),),
        FileLayer({"schema_version": "1.0"}),
        targets={k: v["kind"] for k, v in (placements or {}).items()},
    )


def test_closed_lattice_and_direction():
    network = generate_base_network(
        values({"type": "lattice", "bounds": [-1, -1, 1, 1]}, {"dx": 1, "dy": 1}), limits=LayoutLimits()
    )
    assert len(network.nodes) == 9
    assert len(network.edges) == 12
    assert sum(map(len, network.adjacency.values())) == 24
    assert next(iter(network.nodes)) == Address({"kind": "lattice", "i": -1, "j": -1})
    with pytest.raises(TypeError):
        cast(Any, network.nodes)[None] = None


def test_lattice_limits_and_corner_cutting():
    data = values(
        {
            "type": "lattice",
            "bounds": [0, 0, 1, 1],
            "neighbors": 8,
            "blocked": {"b": {"position": [1, 0, 0], "width": 0.1, "depth": 0.1, "rotation": 0}},
        },
        {"dx": 1, "dy": 1},
    )
    network = generate_base_network(data, limits=LayoutLimits())
    assert len(network.nodes) == 3
    assert len(network.edges) == 2
    with pytest.raises(ValueError, match="max_nodes"):
        generate_base_network(data, limits=LayoutLimits(max_nodes=3))


@pytest.mark.parametrize("direction,expected", [("both", 12), ("forward", 6), ("backward", 6)])
def test_eight_neighbors_direction_and_disabled_definitions(direction, expected):
    data = values(
        {
            "type": "lattice",
            "bounds": [0, 0, 1, 1],
            "neighbors": 8,
            "direction": direction,
            "capacity": 3,
            "speed_limit": 2,
        },
        {"dx": 1, "dy": 1},
    )
    network = generate_base_network(data, limits=LayoutLimits())
    assert len(network.edges) == 6
    assert sum(map(len, network.adjacency.values())) == expected
    assert all(n.capacity == 3 for n in network.nodes.values())
    assert all(e.speed_limit == 2 for e in network.edges.values())
    disabled = generate_base_network(
        values({"type": "lattice", "bounds": [0, 0, 1, 1], "enabled": False}, {"dx": 1, "dy": 1}), limits=LayoutLimits()
    )
    assert len(disabled.edges) == 4
    assert not any(disabled.adjacency.values())


def test_empty_lattice_bounds_and_missing_inputs():
    empty = generate_base_network(
        values({"type": "lattice", "bounds": [0, 0.1, 1e100, 0.2]}, {"dx": 1, "dy": 1}), limits=LayoutLimits()
    )
    assert not empty.nodes
    absent = resolve_layers((), FileLayer({"schema_version": "1.0"}))
    assert not generate_base_network(absent, limits=LayoutLimits()).nodes
    for network, grid in [
        ({"type": "lattice"}, {"dx": 1, "dy": 1}),
        ({"type": "lattice", "bounds": [0, 0, 1, 1]}, None),
    ]:
        with pytest.raises(ValueError, match="required input"):
            generate_base_network(values(network, grid), limits=LayoutLimits())
    with pytest.raises(ValueError, match="ordered"):
        generate_base_network(
            values({"type": "lattice", "bounds": [1, 1, 0, 0]}, {"dx": 1, "dy": 1}), limits=LayoutLimits()
        )


def test_arc_limit_before_enumeration():
    with pytest.raises(ValueError, match="max_directed_arcs"):
        generate_base_network(
            values({"type": "lattice", "bounds": [0, 0, 1, 1]}, {"dx": 1, "dy": 1}),
            limits=LayoutLimits(max_directed_arcs=3),
        )


def test_obstacle_segments_pruned_and_physical_placement_required():
    obstacle = {
        "kind": "warehouse",
        "position": [0.5, 0, 0],
        "rotation": 0,
        "footprint": {"width": 0.1, "depth": 0.1},
        "obstacle": True,
    }
    result = generate_base_network(
        values(
            {"type": "lattice", "bounds": [0, 0, 1, 0]},
            {"dx": 1, "dy": 1},
            {"wh": obstacle, "visual": {"kind": "server"}},
        ),
        limits=LayoutLimits(),
    )
    assert len(result.nodes) == 2 and not result.edges
    assert result.obstacles[0].owner == "wh"
    for missing in ("position", "rotation", "footprint"):
        with pytest.raises(ValueError, match="required input"):
            generate_base_network(
                values(
                    {"type": "lattice", "bounds": [0, 0, 1, 0]},
                    {"dx": 1, "dy": 1},
                    {"wh": {k: v for k, v in obstacle.items() if k != missing}},
                ),
                limits=LayoutLimits(),
            )
    with pytest.raises(ValueError, match="AGVs"):
        generate_base_network(
            values({"type": "explicit", "nodes": [], "arcs": []}, placements={"a": {**obstacle, "kind": "agv"}}),
            limits=LayoutLimits(),
        )


def test_explicit_effective_snapshot_and_disabled_blocker_metadata():
    from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
    from simulatte.layout.types import Explicit

    a, b = Node("A", 0, 0), Node("B", 2, 0)
    graph = LayoutGraph([a, a, b], [Arc(a, b, speed_limit=1), Arc(a, b, bidirectional=False, speed_limit=2)])
    snapshot = Explicit(graph)
    assert [n.id for n in snapshot.nodes] == ["A", "B"]
    assert [(a.source, a.target, a.speed_limit) for a in snapshot.arcs] == [("A", "B", 2), ("B", "A", 1)]
    nodes = [{"id": n.id, "position": [n.position.x, n.position.y, 0]} for n in snapshot.nodes]
    arcs = [{"source": a.source, "target": a.target, "speed_limit": a.speed_limit} for a in snapshot.arcs]
    result = generate_base_network(values({"type": "explicit", "nodes": nodes, "arcs": arcs}), limits=LayoutLimits())
    assert [(e.source.to_dict()["id"], e.target.to_dict()["id"], e.speed_limit) for e in result.edges.values()] == [
        ("A", "B", 2),
        ("B", "A", 1),
    ]
    reverse = graph.arc_between(b, a)
    assert reverse is not None
    assert graph.nodes == (a, b) and reverse.speed_limit == 1
    data = {
        "type": "explicit",
        "nodes": [{**nodes[0], "enabled": False}, nodes[1]],
        "arcs": [{"source": "A", "target": "B", "enabled": False}],
        "blocked": {"wall": {"position": [0, 0, 0], "width": 1, "depth": 1, "rotation": 0}},
    }
    disabled = generate_base_network(values(data), limits=LayoutLimits())
    assert not any(disabled.adjacency.values())
    assert next(iter(disabled.nodes.values())).blockers == ("blocked:wall",)
    assert next(iter(disabled.edges.values())).blockers == ("blocked:wall",)


@pytest.mark.parametrize(
    "nodes,arcs,message",
    [
        ([{"id": "A", "position": [0, 0, 0]}, {"id": "A", "position": [1, 0, 0]}], [], "duplicate"),
        ([{"id": "A", "position": [0, 0, 0]}], [{"source": "A", "target": "B"}], "foreign"),
        (
            [{"id": "A", "position": [0, 0, 0]}, {"id": "B", "position": [0, 0, 0]}],
            [{"source": "A", "target": "B"}],
            "zero-length",
        ),
        (
            [{"id": "A", "position": [0, 0, 0]}, {"id": "B", "position": [1, 0, 0]}],
            [{"source": "A", "target": "B", "speed_limit": 1}, {"source": "A", "target": "B", "speed_limit": 2}],
            "conflicting",
        ),
    ],
)
def test_explicit_raw_validation(nodes, arcs, message):
    with pytest.raises(ValueError, match=message):
        generate_base_network(values({"type": "explicit", "nodes": nodes, "arcs": arcs}), limits=LayoutLimits())


def test_explicit_direction_deduplication_and_limits():
    nodes = [{"id": "A", "position": [0, 0, 0]}, {"id": "B", "position": [1, 0, 0]}]
    arcs = [
        {"source": "A", "target": "B", "direction": "both"},
        {"source": "A", "target": "B", "direction": "backward"},
    ]
    result = generate_base_network(values({"type": "explicit", "nodes": nodes, "arcs": arcs}), limits=LayoutLimits())
    assert len(result.edges) == 2
    assert sum(map(len, result.adjacency.values())) == 2
    for limits, message in [
        (LayoutLimits(max_nodes=1), "max_nodes"),
        (LayoutLimits(max_directed_arcs=1), "max_directed_arcs"),
    ]:
        with pytest.raises(ValueError, match=message):
            generate_base_network(values({"type": "explicit", "nodes": nodes, "arcs": arcs}), limits=limits)


def test_frozen_graph_is_canonical_readonly_and_preserves_neighbor_order():
    from simulatte.intralogistics.graph import Arc, LayoutGraph, Node

    a, b, c = Node("A", 0, 0), Node("B", 1, 0), Node("C", 0, 1)
    graph = LayoutGraph._from_layout([a, b, c], [Arc(a, c, False, 2), Arc(a, b, False, 1), Arc(b, a, False, 3)])
    assert graph.nodes == (a, b, c)
    assert graph.neighbors(a) == [c, b]
    assert graph.arc_between(b, a) == Arc(b, a, False, 3)
    unknown = Node("unknown", 9, 9)
    before = tuple(graph._adjacency)
    assert graph.neighbors(unknown) == [] and graph.arc_between(unknown, a) is None
    assert tuple(graph._adjacency) == before
    with pytest.raises(TypeError):
        graph._adjacency[a][b] = Arc(a, b)
    with pytest.raises(TypeError):
        graph._nodes[a] = None
    for nodes, arcs, message in [
        ([a, a], [], "duplicate node"),
        ([a, b], [Arc(Node("A", 0, 0), b, False)], "canonical"),
        ([a], [Arc(a, b, False)], "canonical"),
        ([a, b], [Arc(a, b)], "directed arcs"),
        ([a, b], [Arc(a, b, False), Arc(a, b, False)], "duplicate directed"),
    ]:
        with pytest.raises(ValueError, match=message):
            LayoutGraph._from_layout(nodes, arcs)
