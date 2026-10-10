import pytest

from simulatte.layout.dependencies import DependencyClosure
from simulatte.layout.errors import LayoutError
from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.network import resolve_network
from simulatte.layout.refs import Address
from simulatte.layout.types import LayoutLimits


def address(kind, **fields):
    return Address({"kind": kind, **fields})


def data(nodes=None, connector=None, placement=None, network=None, overrides=None, file=None):
    wh = {
        "kind": "warehouse",
        "position": [0, 0, 0],
        "rotation": 0,
        "footprint": {"width": 2, "depth": 2},
        "obstacle": True,
        "ports": {"out": {"position": [1, 0, 0], "approach": [1, 0, 0], "connector": connector or {}}},
    }
    wh.update(placement or {})
    patch = {
        "schema_version": "1.0",
        "network": {
            "type": "explicit",
            "nodes": nodes or [{"id": "A", "position": [3, 0, 0]}],
            "arcs": [],
            **(network or {}),
        },
        "placements": {"wh": wh},
    }
    if overrides is not None:
        patch["overrides"] = overrides
    return resolve_layers(
        (("declare", patch),), FileLayer({"schema_version": "1.0", **(file or {})}), targets={"wh": "warehouse"}
    )


def compile(values, **limits):
    return resolve_network(values, DependencyClosure(), limits=LayoutLimits(**limits))


def test_first_dyadic_gap_and_owner_exemption():
    result = compile(data(connector={"approach_margin": 0.125}, network={"clearance": 0.25}))
    port = result.node(address("port", entity="wh", port="out"))
    approach = result.node(address("approach", entity="wh", port="out"))
    assert (port.x, approach.x) == (1, 1.5)
    assert result.graph.shortest_path(port, result.node(address("explicit", id="A")))
    assert all(not e.blockers for e in result.definitions.edges.values())


@pytest.mark.parametrize("explicit", [False, True])
def test_approach_alias_and_no_outer_edge(explicit):
    connector = {"approach_margin": 0.125, "direction": "forward", "speed_limit": 2}
    if explicit:
        connector["target"] = {"kind": "explicit", "id": "A"}
    result = compile(data(nodes=[{"id": "A", "position": [1.125, 0, 0], "capacity": 3}], connector=connector))
    a = result.node(address("explicit", id="A"))
    assert result.node(address("approach", entity="wh", port="out")) is a
    assert len(result.graph.nodes) == 2 and len(result.definitions.edges) == 1
    assert result.capacities[a] == 3
    port = result.node(address("port", entity="wh", port="out"))
    assert result.graph.arc_between(port, a).speed_limit == 2
    assert result.graph.arc_between(a, port) is None
    result.validate(required_paths=((address("port", entity="wh", port="out"), address("explicit", id="A")),))
    with pytest.raises(LayoutError, match="unreachable"):
        result.validate(required_paths=((address("explicit", id="A"), address("port", entity="wh", port="out")),))


def test_rotated_owner_and_nonowner_inner_blocker():
    result = compile(data(nodes=[{"id": "A", "position": [0, 3, 0]}], placement={"rotation": 90}))
    assert result.node(address("port", entity="wh", port="out")).y == 1
    with pytest.raises(LayoutError, match="blocked"):
        compile(
            data(network={"blocked": {"other": {"position": [1.005, 0, 0], "width": 0.005, "depth": 1, "rotation": 0}}})
        )


def test_target_distance_ties_and_limit():
    result = compile(
        data(
            nodes=[{"id": "B", "position": [2, 1, 0]}, {"id": "A", "position": [2, -1, 0]}],
            connector={"approach_margin": 0.125},
        )
    )
    outer = result.definitions.edges[address("connector", entity="wh", port="out", segment="outer")]
    assert outer.target == address("explicit", id="A")
    compile(data(connector={"approach_margin": 0.125, "max_distance": 1.875}))
    with pytest.raises(LayoutError, match="candidate"):
        compile(data(connector={"approach_margin": 0.125, "max_distance": 1.874}))


@pytest.mark.parametrize(
    "position,approach",
    [([0, 0, 0], [1, 0, 0]), ([1, 0, 0], [-1, 0, 0]), ([1, 0, 0], [0, 0, 0]), ([1, 1, 0], [-1, -1, 0])],
)
def test_local_boundary_outward_validation(position, approach):
    with pytest.raises(LayoutError, match="boundary|outward|nonzero"):
        compile(data(placement={"ports": {"out": {"position": position, "approach": approach, "connector": {}}}}))


def test_post_rounding_retries_and_positive_zero_clearance_gap():
    result = compile(
        data(
            nodes=[{"id": "A", "position": [4, 0, 0]}],
            placement={
                "footprint": {"width": 4, "depth": 2},
                "ports": {"out": {"position": [2, 0, 0], "approach": [1, 0, 0], "connector": {}}},
            },
        )
    )
    # 2 + .01 rounds inward: its exact stored gap is less than .01.
    assert result.node(address("approach", entity="wh", port="out")).x == 2.02
    assert result.node(address("port", entity="wh", port="out")).x == 2


def test_precision_guards_and_step_exhaustion():
    with pytest.raises(LayoutError, match="2048 candidate"):
        compile(data(connector={"approach_margin": 5e-324}, network={"clearance": 1e308}))
    with pytest.raises(LayoutError, match="finite float64"):
        compile(data(connector={"approach_margin": 1e308}, network={"clearance": 1.7e308}))


@pytest.mark.parametrize("rotation", [0, 37, 180, 270])
def test_nonobstacle_owner_and_corner_rays(rotation):
    from simulatte.layout.geometry import transform
    from simulatte.layout.types import Point

    point = transform(Point(3, 3), Point(0, 0), rotation)
    result = compile(
        data(
            nodes=[{"id": "A", "position": [point.x, point.y, 0]}],
            placement={
                "rotation": rotation,
                "obstacle": False,
                "ports": {"out": {"position": [1, 1, 0], "approach": [1, -1, 0], "connector": {}}},
            },
        )
    )
    assert len(result.graph.nodes) == 3
    # A tangent at the corner remains on the boundary before leaving the footprint.
    result = compile(
        data(
            placement={"ports": {"out": {"position": [1, 1, 0], "approach": [-1, 0, 0], "connector": {}}}},
            nodes=[{"id": "A", "position": [-4, 1, 0]}],
        )
    )
    assert result.node(address("approach", entity="wh", port="out")).x < -1


@pytest.mark.parametrize(
    "target", [{"kind": "explicit", "id": "missing"}, {"kind": "port", "entity": "wh", "port": "out"}]
)
def test_target_must_be_existing_base_node(target):
    with pytest.raises(LayoutError, match="candidate"):
        compile(data(connector={"target": target}))


def test_outer_segment_has_no_owner_exemption_or_other_obstacle_exemption():
    with pytest.raises(LayoutError, match="placement:wh"):
        compile(data(nodes=[{"id": "A", "position": [-3, 0, 0]}]))
    with pytest.raises(LayoutError, match="blocked:wall"):
        compile(data(network={"blocked": {"wall": {"position": [2, 0, 0], "width": 0.1, "depth": 1, "rotation": 0}}}))
    with pytest.raises(LayoutError, match="disabled"):
        compile(data(nodes=[{"id": "A", "position": [3, 0, 0], "enabled": False}]))


def test_connectors_share_base_candidates_independent_of_port_order():
    ports = {
        name: {"position": [1, y, 0], "approach": [1, 0, 0], "connector": {}}
        for name, y in (("early", 0), ("late", 0.1))
    }
    first = compile(data(placement={"ports": ports}))
    second = compile(data(placement={"ports": dict(reversed(list(ports.items())))}))
    for result in (first, second):
        outer = [e for a, e in result.definitions.edges.items() if a.to_dict().get("segment") == "outer"]
        assert {e.target for e in outer} == {address("explicit", id="A")}
    assert first.definitions == second.definitions


@pytest.mark.parametrize(
    "direction,forward,backward", [("both", True, True), ("forward", True, False), ("backward", False, True)]
)
def test_connector_direction_speed_and_disabled(direction, forward, backward):
    result = compile(data(connector={"direction": direction, "speed_limit": 1.5}))
    port = result.node(address("port", entity="wh", port="out"))
    approach = result.node(address("approach", entity="wh", port="out"))
    assert (result.graph.arc_between(port, approach) is not None) == forward
    assert (result.graph.arc_between(approach, port) is not None) == backward
    assert all(e.speed_limit == 1.5 for e in result.definitions.edges.values())
    disabled = compile(data(connector={"enabled": False}))
    assert all(not disabled.graph.neighbors(n) for n in disabled.graph.nodes)
    assert len(disabled.definitions.edges) == 2


def test_connector_capacity_alias_order_and_absent_outer_override():
    result = compile(
        data(
            nodes=[{"id": "A", "position": [1.125, 0, 0], "capacity": 3}],
            connector={"approach_margin": 0.125},
            network={"capacity": 2},
            overrides=[
                {"id": "base", "target": {"kind": "explicit", "id": "A"}, "properties": {"capacity": 4}},
                {
                    "id": "alias",
                    "target": {"kind": "approach", "entity": "wh", "port": "out"},
                    "properties": {"capacity": 5},
                },
                {
                    "id": "outer",
                    "target": {"kind": "connector", "entity": "wh", "port": "out", "segment": "outer"},
                    "properties": {"speed_limit": 8},
                },
            ],
        )
    )
    assert result.capacities[result.node(address("explicit", id="A"))] == 5
    assert result.capacities[result.node(address("port", entity="wh", port="out"))] == 2
    assert [d.target for d in result.diagnostics if d.code == "stale-override"] == ["outer"]


@pytest.mark.parametrize(
    "limits,match", [({"max_nodes": 2}, "max_nodes"), ({"max_directed_arcs": 3}, "max_directed_arcs")]
)
def test_connector_limits(limits, match):
    with pytest.raises(LayoutError, match=match):
        compile(data(), **limits)


def test_missing_connector_physical_inputs_and_visual_ports_ignored():
    with pytest.raises(LayoutError, match="required input"):
        compile(
            data(
                file={
                    "placements": {
                        "wh": {
                            "kind": "warehouse",
                            "ports": {"out": {"connector": {"approach_margin": {"$unset": True}}}},
                        }
                    }
                }
            )
        )
    result = compile(data(placement={"ports": {"visual": {"position": [0, 0, 0]}}}))
    assert len(result.graph.nodes) == 1


def test_connector_inputs_tracked_without_visual_port_data():
    from simulatte.layout.connectors import _approach
    from simulatte.layout.geometry import Rectangle
    from simulatte.layout.types import Point

    view = data(
        placement={
            "ports": {
                "out": {"position": [1, 0, 0], "approach": [1, 0, 0], "connector": {}},
                "visual": {"position": [0, 0, 0]},
            }
        }
    )
    closure = DependencyClosure()
    resolve_network(view, closure, limits=LayoutLimits())
    assert closure.covers(("placements", "wh", "ports", "out", "connector", "approach_margin"))
    assert not closure.covers(("placements", "wh", "ports", "visual", "position"))
    changed = resolve_layers(
        view.bundle.code_stages,
        FileLayer(
            {
                "schema_version": "1.0",
                "placements": {"wh": {"kind": "warehouse", "ports": {"out": {"connector": {"max_distance": 100}}}}},
            }
        ),
        targets=view.targets,
    )
    assert closure.changed(changed)
    with pytest.raises(LayoutError, match="transformed approach vector is zero"):
        _approach(Point(1, 0), Point(0, 0), Rectangle.create("owner", Point(0, 0), 2, 2, 0), 0, 0.01)


def test_generated_ids_colliding_with_explicit_ids_are_rejected():
    collision = address("port", entity="wh", port="out").encoded_id
    with pytest.raises(LayoutError, match="node id collision"):
        compile(data(nodes=[{"id": collision, "position": [3, 0, 0]}]))


@pytest.mark.parametrize("field", ["enabled", "direction"])
def test_unset_connector_edge_property_requires_repair_of_every_segment(field):
    connector = {field: {"$unset": True}}
    with pytest.raises(LayoutError, match=f"placements/wh/ports/out/connector/{field}"):
        compile(data(connector=connector))
    records = [
        {
            "id": segment,
            "target": {"kind": "connector", "entity": "wh", "port": "out", "segment": segment},
            "properties": {field: "forward" if field == "direction" else True},
        }
        for segment in ("interior", "outer")
    ]
    with pytest.raises(LayoutError, match=f"connector/{field}"):
        compile(data(connector=connector, overrides=records[:1]))
    result = compile(data(connector=connector, overrides=records))
    assert len(result.graph.nodes) == 3
