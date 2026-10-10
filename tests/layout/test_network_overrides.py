from typing import Any, cast

import pytest

from simulatte.layout.dependencies import DependencyClosure
from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.network import resolve_network
from simulatte.layout.refs import Address
from simulatte.layout.types import LayoutLimits


def resolve(patch, file=None, closure=None):
    values = resolve_layers(
        (("declare", {"schema_version": "1.0", **patch}),), FileLayer({"schema_version": "1.0", **(file or {})})
    )
    return resolve_network(values, closure or DependencyClosure(), limits=LayoutLimits())


def override(target, **properties):
    return {"id": "edit", "target": target, "properties": properties}


def test_ordered_per_property_and_tombstone():
    result = resolve(
        {
            "grid": {"dx": 1, "dy": 1},
            "network": {"type": "lattice", "bounds": [0, 0, 1, 1], "speed_limit": 2, "capacity": 3},
            "overrides": [
                override({"kind": "row", "j": 0}, capacity=5, speed_limit=4),
                override({"kind": "column", "i": 0}, capacity=7),
                override({"kind": "row", "j": 0}, speed_limit={"$unset": True}),
            ],
        }
    )
    a = result.node(Address({"kind": "lattice", "i": 0, "j": 0}))
    b = result.node(Address({"kind": "lattice", "i": 1, "j": 0}))
    assert result.capacities[a] == 7 and result.capacities[b] == 5
    assert result.graph.arc_between(a, b).speed_limit is None


def test_absent_graph_closure_and_missing_handle():
    closure = DependencyClosure()
    result = resolve({}, closure=closure)
    assert not result.graph.nodes
    assert any(q.descriptor["kind"] == "presence" and q.descriptor["path"] == ("network",) for q in closure.queries)
    with pytest.raises(ValueError, match="missing"):
        result.node(Address({"kind": "explicit", "id": "missing"}))


def line(**attributes):
    return {
        "network": {
            "type": "explicit",
            "nodes": [{"id": "A", "position": [0, 0, 0]}, {"id": "B", "position": [2, 0, 0]}],
            "arcs": [{"source": "A", "target": "B", **attributes}],
        }
    }


def test_file_list_replacement_and_closed_rectangle_endpoint_selection():
    patch = line(speed_limit=2)
    patch["overrides"] = [override({"kind": "explicit", "id": "A"}, enabled=False)]
    result = resolve(
        patch, {"overrides": [override({"kind": "rectangle", "bounds": [0, 0, 1, 0]}, capacity=4, speed_limit=3)]}
    )
    a, b = result.graph.nodes
    assert result.capacities[a] == 4 and result.capacities[b] is None
    assert result.graph.arc_between(a, b).speed_limit == 2
    result = resolve(patch, {"overrides": [override({"kind": "rectangle", "bounds": [0, 0, 2, 0]}, speed_limit=3)]})
    assert result.graph.arc_between(*result.graph.nodes).speed_limit == 3
    with pytest.raises(ValueError, match="ordered"):
        resolve(patch, {"overrides": [override({"kind": "rectangle", "bounds": [2, 0, 0, 0]}, enabled=False)]})


def test_stale_empty_and_wrong_addresses_are_preserved_as_warnings():
    patch = line()
    patch["overrides"] = [
        override({"kind": "row", "j": 999}, enabled=False),
        override({"kind": "explicit", "id": "gone"}, capacity=2),
        override({"kind": "lane_segment", "lane": "gone", "source": "a", "target": "b"}, enabled=False),
    ]
    result = resolve(patch)
    assert len(result.diagnostics) == 3
    assert all(d.code == "stale-override" for d in result.diagnostics)


def test_disabled_node_removes_incident_traversals_and_required_handle_fails():
    patch = line()
    patch["overrides"] = [override({"kind": "explicit", "id": "A"}, enabled=False)]
    result = resolve(patch)
    assert [n.id for n in result.graph.nodes] == ["B"]
    assert len(result.definitions.nodes) == 2 and len(result.definitions.edges) == 1
    assert not any(result.definitions.adjacency.values())
    with pytest.raises(ValueError, match="disabled"):
        result.validate(required_nodes=[Address({"kind": "explicit", "id": "A"})])


def test_reenable_blocked_nodes_and_edges_cannot_bypass_geometry():
    patch = line(enabled=False)
    patch["network"]["blocked"] = {"wall": {"position": [1, 0, 0], "width": 0.1, "depth": 1, "rotation": 0}}
    assert len(resolve(patch).graph.nodes) == 2
    patch["overrides"] = [override({"kind": "explicit_arc", "source": "A", "target": "B"}, enabled=True)]
    with pytest.raises(ValueError, match="enabled edge.*blockers"):
        resolve(patch)
    patch["network"]["blocked"]["wall"]["position"] = [0, 0, 0]
    patch["network"]["nodes"][0]["enabled"] = False
    patch["overrides"] = []
    assert len(resolve(patch).graph.nodes) == 1
    patch["overrides"] = [override({"kind": "explicit", "id": "A"}, enabled=True)]
    with pytest.raises(ValueError, match="enabled node.*blockers"):
        resolve(patch)


def test_required_paths_components_and_collision_validation():
    result = resolve(line())
    a, b = (Address({"kind": "explicit", "id": id}) for id in ("A", "B"))
    assert result.validate(required_nodes=[a], required_paths=[(a, b), (a, a)])[0].path == (a, b)
    assert len(result.weak_components) == 1 and len(result.strong_components) == 2
    with pytest.raises(ValueError, match="unreachable"):
        result.validate(required_paths=[(b, a)])
    with pytest.raises(ValueError, match="collision"):
        result.validate(existing_entity_ids=["A"])
    patch = line(enabled=False)
    result = resolve(patch)
    assert len(result.weak_components) == len(result.strong_components) == 2
    assert result.diagnostics[0].code == "disconnected-network"
    with pytest.raises(TypeError):
        result.capacities[result.graph.nodes[0]] = 3


@pytest.mark.parametrize(
    "field,target",
    [
        ("enabled", {"kind": "explicit", "id": "A"}),
        ("enabled", {"kind": "explicit_arc", "source": "A", "target": "B"}),
        ("direction", {"kind": "explicit_arc", "source": "A", "target": "B"}),
    ],
)
def test_required_unset_fails_unless_later_override_repairs(field, target):
    patch = line()
    patch["overrides"] = [override(target, **{field: {"$unset": True}})]
    with pytest.raises(ValueError, match=f"overrides/0/properties/{field}"):
        resolve(patch)
    patch["overrides"].append(override(target, **{field: "backward" if field == "direction" else True}))
    result = resolve(patch)
    assert len(result.graph.nodes) == 2


def test_optional_capacity_unset_and_disabled_direction_unset():
    patch = line(enabled=False)
    patch["network"]["nodes"][0]["capacity"] = 3
    patch["overrides"] = [
        override({"kind": "explicit", "id": "A"}, capacity={"$unset": True}),
        override({"kind": "explicit_arc", "source": "A", "target": "B"}, direction={"$unset": True}),
    ]
    result = resolve(patch)
    assert result.capacities[result.graph.nodes[0]] is None
    assert not result.graph.neighbors(result.graph.nodes[0])


def lanes():
    return {
        "network": {
            "type": "lanes",
            "lanes": {
                "h": {"vertices": [{"id": "a", "position": [-1, 0, 0]}, {"id": "b", "position": [1, 0, 0]}]},
                "v": {"vertices": [{"id": "a", "position": [0, -1, 0]}, {"id": "b", "position": [0, 1, 0]}]},
            },
        }
    }


def test_lane_selectors_parent_children_and_no_crossing_leakage():
    patch = lanes()
    parent = {"kind": "lane_segment", "lane": "h", "source": "a", "target": "b"}
    patch["overrides"] = [
        override({"kind": "lane", "id": "h"}, capacity=2, speed_limit=3),
        override(parent, direction="forward", speed_limit=4),
    ]
    result = resolve(patch)
    children = result.definitions.lineage[Address(parent)]
    assert len(children) == 2
    assert all(
        result.definitions.edges[a].direction == "forward" and result.definitions.edges[a].speed_limit == 4
        for a in children
    )
    assert all(e.speed_limit is None for a, e in result.definitions.edges.items() if a not in children)
    # Insert a vertex; original ordered source segment no longer exists.
    patch["network"]["lanes"]["h"]["vertices"].insert(1, {"id": "middle", "position": [-0.5, 0, 0]})
    changed = resolve(patch)
    assert any(d.code == "stale-override" and d.path == ("overrides", "1") for d in changed.diagnostics)


def test_endpoint_alias_overrides_shared_capacity():
    patch = lanes()
    patch["network"]["lanes"]["v"]["vertices"][0]["position"] = [0, 0, 0]
    alias = Address({"kind": "lane_vertex", "lane": "v", "vertex": "a"})
    patch["overrides"] = [override(alias.to_dict(), capacity=9)]
    result = resolve(patch)
    canonical = result.aliases[alias]
    assert canonical != alias
    assert result.capacities[result.node(alias)] == 9


def test_overlapping_directed_definitions_deduplicate_or_reject_conflict():
    patch = line(speed_limit=2)
    patch["network"]["arcs"].append({"source": "B", "target": "A", "speed_limit": 2})
    patch["overrides"] = [override({"kind": "explicit_arc", "source": "A", "target": "B"}, direction="both")]
    result = resolve(patch)
    assert sum(len(result.graph.neighbors(n)) for n in result.graph.nodes) == 2
    patch["network"]["arcs"][1]["speed_limit"] = 3
    with pytest.raises(ValueError, match="conflicting effective"):
        resolve(patch)


def test_physical_closure_ignores_presentation_but_freezes_network_negative_predicates():
    from copy import deepcopy

    patch = line()
    patch["placements"] = {
        "visual": {
            "kind": "server",
            "position": [10, 10, 0],
            "rotation": 0,
            "footprint": {"width": 2, "depth": 2},
            "obstacle": False,
            "ports": {"p": {"position": [1, 0, 0], "approach": [1, 0, 0]}},
        }
    }

    def values(p):
        return resolve_layers((("declare", {"schema_version": "1.0", **p}),), FileLayer({"schema_version": "1.0"}))

    original = values(patch)
    closure = DependencyClosure()
    with closure.network_queries():
        resolve_network(original, closure, limits=LayoutLimits())
    presentation = deepcopy(patch)
    presentation["placements"]["visual"]["position"] = [20, 20, 0]
    assert not closure.changed(values(presentation))
    assert not closure.covers(("placements", "visual", "position"))
    for field, value in (("obstacle", True), ("ports", {"p": {"connector": {}}})):
        changed = deepcopy(presentation)
        changed["placements"]["visual"][field] = value
        assert ("placements",) in closure.changed(values(changed))
    extended = deepcopy(presentation)
    extended["placements"]["later"] = {"kind": "server", "position": [100, 100, 0]}
    assert not closure.changed(values(extended), allow_network_extensions=True)
    closure.extend_network(values(extended))
    extended["placements"]["later"]["obstacle"] = True
    assert ("placements",) in closure.changed(values(extended))


def test_structural_network_inputs_and_lane_order_are_frozen():
    from copy import deepcopy

    for patch, mutate in [
        (line(), lambda p: p["network"]["nodes"].reverse()),
        (lanes(), lambda p: p["network"]["lanes"]["h"]["vertices"].reverse()),
        (
            {"grid": {"dx": 1, "dy": 1}, "network": {"type": "lattice", "bounds": [0, 0, 1, 1]}},
            lambda p: p["grid"].update(dx=2),
        ),
        (
            line(),
            lambda p: p["network"].update(
                blocked={"wall": {"position": [100, 100, 0], "width": 1, "depth": 1, "rotation": 0}}
            ),
        ),
    ]:
        closure = DependencyClosure()
        resolve(patch, closure=closure)
        changed = deepcopy(patch)
        mutate(changed)
        view = resolve_layers(
            (("declare", {"schema_version": "1.0", **changed}),), FileLayer({"schema_version": "1.0"})
        )
        assert closure.changed(view)
        assert closure.reevaluate(view.bundle.code_stages, view.bundle.file_layer)


def test_stale_property_payload_is_not_physical_but_targets_and_order_are():
    from copy import deepcopy

    patch = line()
    patch["overrides"] = [
        override({"kind": "explicit", "id": "missing"}, capacity=2),
        override({"kind": "explicit", "id": "A"}, capacity=3),
    ]
    closure = DependencyClosure()
    resolve(patch, closure=closure)
    changed = deepcopy(patch)
    changed["overrides"][0]["properties"]["capacity"] = 99
    other = DependencyClosure()
    resolve(changed, closure=other)
    assert closure.physical_data() == other.physical_data()

    def view():
        return resolve_layers(
            (("declare", {"schema_version": "1.0", **changed}),), FileLayer({"schema_version": "1.0"})
        )

    assert not closure.changed(view())
    changed["overrides"][0]["target"]["id"] = "B"
    assert closure.changed(view())
    changed["overrides"].reverse()
    assert closure.changed(view())


def test_auto_geometry_is_never_physical_and_obstacle_transform_is_tracked():
    patch: dict[str, Any] = {
        **line(),
        "placements": {
            "wall": {"kind": "warehouse", "obstacle": True, "rotation": 0, "footprint": {"width": 1, "depth": 1}}
        },
    }
    view = resolve_layers(
        (("declare", {"schema_version": "1.0", **patch}),),
        FileLayer({"schema_version": "1.0"}),
        {"schema_version": "1.0", "placements": {"wall": {"kind": "warehouse", "position": [20, 20, 0]}}},
    )
    with pytest.raises(ValueError, match="auto value cannot be physical"):
        resolve_network(view, DependencyClosure(), limits=LayoutLimits())
    cast(Any, patch)["placements"]["wall"]["position"] = [20, 20, 0]
    closure = DependencyClosure()
    resolve(patch, closure=closure)
    assert closure.covers(("placements", "wall", "footprint", "width"))
    patch["placements"]["wall"]["rotation"] = 90
    changed = resolve_layers((("declare", {"schema_version": "1.0", **patch}),), FileLayer({"schema_version": "1.0"}))
    assert ("placements", "wall", "rotation") in closure.changed(changed)


def test_final_arc_limit_after_override_direction_expansion():
    patch = line()
    patch["overrides"] = [override({"kind": "explicit_arc", "source": "A", "target": "B"}, direction="both")]
    view = resolve_layers((("declare", {"schema_version": "1.0", **patch}),), FileLayer({"schema_version": "1.0"}))
    with pytest.raises(ValueError, match="max_directed_arcs"):
        resolve_network(view, DependencyClosure(), limits=LayoutLimits(max_directed_arcs=1))


@pytest.mark.parametrize("field", ["clearance", "neighbors"])
def test_unset_required_generator_inputs_do_not_restore_defaults(field):
    patch = {
        "grid": {"dx": 1, "dy": 1},
        "network": {"type": "lattice", "bounds": [0, 0, 1, 0], field: {"$unset": True}},
    }
    with pytest.raises(ValueError, match=f"network/{field}"):
        resolve(patch)


@pytest.mark.parametrize("field", ["enabled", "direction"])
@pytest.mark.parametrize("variant", ["lattice", "lanes"])
def test_unset_generator_edge_property_can_be_repaired_by_override(field, variant):
    patch: dict[str, Any] = (
        {"grid": {"dx": 1, "dy": 1}, "network": {"type": "lattice", "bounds": [0, 0, 1, 0], field: {"$unset": True}}}
        if variant == "lattice"
        else lanes()
    )
    if variant == "lanes":
        patch["network"]["lanes"]["h"][field] = {"$unset": True}
    with pytest.raises(ValueError, match=f"network/.*{field}"):
        resolve(patch)
    selector = {"kind": "row", "j": 0} if variant == "lattice" else {"kind": "lane", "id": "h"}
    patch["overrides"] = [override(selector, **{field: "forward" if field == "direction" else True})]
    result = resolve(patch)
    assert any(result.graph.neighbors(n) for n in result.graph.nodes)


def test_unset_explicit_candidate_state_and_raw_direction_fail_at_input():
    patch = line()
    patch["network"]["nodes"][0]["enabled"] = {"$unset": True}
    with pytest.raises(ValueError, match="network/nodes/0/enabled"):
        resolve(patch)
    patch = line(direction={"$unset": True})
    with pytest.raises(ValueError, match="network/arcs/0/direction"):
        resolve(patch)
    patch = line(enabled={"$unset": True})
    with pytest.raises(ValueError, match="network/arcs/0/enabled"):
        resolve(patch)
    patch["overrides"] = [override({"kind": "explicit_arc", "source": "A", "target": "B"}, enabled=True)]
    assert resolve(patch).graph.arc_between(*resolve(patch).graph.nodes) is not None
