"""Layer precedence and provenance at the staged/presentation boundary."""

import pytest

from simulatte.layout.errors import LayoutError
from simulatte.layout.layers import FileLayer, LayerBundle, LayerSource, resolve_layers

UNSET = {"$unset": True}


def patch(**data):
    return {"schema_version": "1.0", **data}


def test_leaf_precedence_and_immutable_author_layers():
    code = patch(placements={"a": {"kind": "server", "position": [1.1, 2, 0], "visual": {"label": "A"}}})
    file = FileLayer(patch(placements={"a": {"kind": "server", "visual": {"color": "red"}}}))
    values = resolve_layers((("declare", code),), file)
    code["placements"]["a"]["position"][0] = 99
    assert values.data["placements"]["a"]["position"] == (1.1, 2, 0)
    assert dict(values.data["placements"]["a"]["visual"]) == {"label": "A", "color": "red"}
    assert values.at(("placements", "a", "position", "0")).source == LayerSource("code", "declare")
    assert values.at(("placements", "a", "visual", "color")).source == LayerSource("file")
    with pytest.raises(TypeError):
        values.data["placements"]["a"]["position"] = ()
    with pytest.raises(TypeError):
        file.data["placements"]["a"]["kind"] = "agv"
    assert isinstance(values.bundle, LayerBundle)
    assert values.bundle.code_stages[0][1]["placements"]["a"]["position"] == (1.1, 2, 0)


def test_unset_absence_and_required_use():
    code = patch(grid={"dx": 1, "dy": 2}, placements={"a": {"kind": "server", "visual": {"label": "A"}}})
    file = FileLayer(patch(grid={"dx": UNSET}, placements={"a": {"kind": "server", "visual": UNSET}}))
    values = resolve_layers((("declare", code),), file)
    assert "dx" not in values.data["grid"]
    assert values.at(("grid", "dx")).state == "unset"
    assert values.at(("placements", "a", "visual", "label")).state == "unset"
    assert values.at(("placements", "a", "position")).state == "absent"
    assert values.at(("grid", "origin")).schema_default
    assert values.require(("grid", "dy")) == 2
    with pytest.raises(LayoutError, match="grid/dx"):
        values.require(("grid", "dx"))
    with pytest.raises(LayoutError, match="position"):
        values.require(("placements", "a", "position"))


def test_auto_fills_missing_without_changing_explicit_view_or_unset():
    code = patch(placements={"a": {"kind": "server", "visual": {"label": "authored"}}})
    auto = patch(
        placements={
            "a": {"kind": "server", "position": [5, 6, 0], "rotation": 0, "visual": {"label": "auto", "color": "gray"}}
        }
    )
    file = FileLayer(patch(placements={"a": {"kind": "server", "rotation": UNSET}}))
    values = resolve_layers((("declare", code),), file, auto)
    assert values.at(("placements", "a", "position")).source == LayerSource("auto")
    assert values.at(("placements", "a", "position"), explicit=True).state == "absent"
    assert values.at(("placements", "a", "rotation")).state == "unset"
    assert values.at(("placements", "a", "visual", "label")).value == "authored"
    assert values.require(("placements", "a", "position")) == (5, 6, 0)
    with pytest.raises(LayoutError):
        values.require(("placements", "a", "position"), explicit=True)


def test_list_replacement_and_stage_order():
    one = patch(
        network={"type": "explicit", "nodes": [{"id": "a", "position": [0, 0, 0]}], "arcs": []},
        overrides=[{"id": "x", "target": {"kind": "explicit", "id": "a"}, "properties": {"enabled": False}}],
    )
    two = patch(network={"nodes": [{"id": "b", "position": [1, 0, 0]}]})
    file = FileLayer(patch(network={"nodes": []}, overrides=[]))
    values = resolve_layers((("declare", one), ("next", two)), file)
    assert values.data["network"]["nodes"] == ()
    assert values.data["overrides"] == ()
    assert values.at(("network", "nodes", "0")).state == "absent"
    assert values.at(("network", "type")).source == LayerSource("code", "declare")


def test_variant_switch_drops_old_fields_preserves_common_and_rejects_typeless_incompatible():
    code = patch(network={"type": "lattice", "bounds": [0, 0, 5, 5], "neighbors": 8, "clearance": 0.25})
    values = resolve_layers((("declare", code),), FileLayer(patch(network={"type": "lanes", "lanes": {}})))
    assert dict(values.data["network"]) == {"type": "lanes", "clearance": 0.25, "lanes": {}, "blocked": {}}
    assert values.at(("network", "bounds")).state == "absent"
    with pytest.raises(LayoutError):
        resolve_layers((("declare", code),), FileLayer(patch(network={"lanes": {}})))


def test_declared_connector_defaults_are_physical_sources_not_auto():
    code = patch(
        placements={
            "a": {"kind": "server", "ports": {"in": {"position": [1, 0, 0], "approach": [1, 0, 0], "connector": {}}}}
        }
    )
    values = resolve_layers((("declare", code),), FileLayer(patch()))
    leaf = values.at(("placements", "a", "ports", "in", "connector", "approach_margin"))
    assert leaf.value == 0.01
    assert leaf.schema_default
    assert leaf.source == LayerSource("code", "declare")
    assert values.at(("placements", "a", "rotation")).state == "absent"


def test_unset_inside_atomic_list_retains_provenance_and_can_be_required_later():
    values = resolve_layers(
        (),
        FileLayer(
            patch(network={"type": "explicit", "nodes": [{"id": "a", "position": UNSET, "enabled": UNSET}], "arcs": []})
        ),
    )
    assert "position" not in values.data["network"]["nodes"][0]
    leaf = values.at(("network", "nodes", "0", "position"))
    assert leaf.state == "unset"
    assert leaf.source is not None and leaf.source.kind == "file"
    assert values.at(("network", "nodes", "0", "enabled")).state == "unset"
    with pytest.raises(LayoutError, match="position"):
        values.require(("network", "nodes", "0", "position"))


def test_empty_auto_layer_cannot_introduce_physical_declarations():
    with pytest.raises(LayoutError, match="auto.*presentation"):
        resolve_layers((), FileLayer(patch()), patch(network={"type": "lattice"}))


def test_root_and_container_reads_include_effective_values():
    values = resolve_layers((), FileLayer(patch(grid={"dx": 1, "dy": 2})))
    assert values.require(())["grid"]["origin"] == (0, 0, 0)
    assert values.require(("grid",))["origin"] == (0, 0, 0)


def test_variant_switch_removes_incompatible_unset_tombstones():
    code = patch(network={"type": "lattice", "bounds": UNSET})
    values = resolve_layers((("declare", code),), FileLayer(patch(network={"type": "lanes", "lanes": {}})))
    assert values.at(("network", "bounds")).state == "absent"


def test_defaults_and_nested_map_unset_are_not_restored():
    code = patch(
        network={
            "type": "lanes",
            "lanes": {"x": {"vertices": [{"id": "v", "position": [1, 2, 0]}, {"id": "w", "position": [2, 2, 0]}]}},
            "blocked": {"b": {"position": [0, 0, 0], "width": 1, "depth": 1, "rotation": 0}},
        }
    )
    file = FileLayer(patch(network={"clearance": UNSET, "lanes": {"x": UNSET}, "blocked": {"b": UNSET}}))
    values = resolve_layers((("declare", code),), file)
    assert dict(values.data["network"]["lanes"]) == {}
    assert values.at(("network", "lanes", "x", "vertices")).state == "unset"
    assert values.at(("network", "clearance")).state == "unset"


def test_typeless_incompatibility_is_not_hidden_by_unset():
    with pytest.raises(LayoutError, match="incompatible"):
        resolve_layers(
            (("declare", patch(network={"type": "lanes", "lanes": {}})),), FileLayer(patch(network={"bounds": UNSET}))
        )


def test_explicit_directed_arc_defaults_preserve_independent_reverse_attributes():
    raw = patch(
        network={
            "type": "explicit",
            "nodes": [{"id": "a", "position": [0, 0, 0]}, {"id": "b", "position": [1, 0, 0]}],
            "arcs": [
                {"source": "a", "target": "b", "speed_limit": 2},
                {"source": "b", "target": "a", "speed_limit": 3},
            ],
        }
    )
    values = resolve_layers((("declare", raw),), FileLayer(patch()))
    assert [
        (arc["source"], arc["target"], arc["speed_limit"], arc["direction"]) for arc in values.data["network"]["arcs"]
    ] == [("a", "b", 2, "forward"), ("b", "a", 3, "forward")]
    assert values.at(("network", "arcs", "0", "direction")).schema_default
    assert values.at(("network", "nodes", "0", "enabled")).value is True


def test_override_list_unset_remains_available_to_graph_compiler():
    values = resolve_layers(
        (),
        FileLayer(
            patch(
                overrides=[
                    {
                        "id": "remove-speed",
                        "target": {"kind": "explicit_arc", "source": "a", "target": "b"},
                        "properties": {"speed_limit": UNSET, "enabled": False},
                    }
                ]
            )
        ),
    )
    properties = values.require(("overrides", "0", "properties"))
    assert dict(properties) == {"enabled": False}
    unset = values.at(("overrides", "0", "properties", "speed_limit"))
    assert unset.state == "unset" and unset.source == LayerSource("file")
    assert values.bundle.file_layer.to_dict()["overrides"][0]["properties"]["speed_limit"] == UNSET


def test_lane_defaults_and_visual_port_have_distinct_physical_defaults():
    values = resolve_layers(
        (),
        FileLayer(
            patch(
                network={
                    "type": "lanes",
                    "lanes": {
                        "a": {"vertices": [{"id": "a", "position": [0, 0, 0]}, {"id": "b", "position": [1, 0, 0]}]}
                    },
                },
                placements={"a": {"kind": "server", "ports": {"p": {"position": [0, 0, 0], "approach": [1, 0, 0]}}}},
            )
        ),
        targets={"a": "server"},
    )
    assert values.at(("network", "lanes", "a", "direction")).value == "both"
    assert values.at(("network", "lanes", "a", "direction")).schema_default
    assert values.at(("placements", "a", "ports", "p", "connector")).state == "absent"


def test_later_explicit_map_can_replace_unset_and_reset_removes_file_override():
    first = patch(placements={"a": {"kind": "server", "visual": UNSET}})
    next_patch = patch(placements={"a": {"kind": "server", "visual": {"color": "blue"}}})
    file = FileLayer(patch(placements={"a": {"kind": "server", "visual": {"color": "red"}}}))
    values = resolve_layers((("declare", first), ("next", next_patch)), file)
    assert values.at(("placements", "a", "visual", "color")).value == "red"
    raw = file.to_dict()
    del raw["placements"]["a"]["visual"]["color"]
    reset = resolve_layers(values.bundle.code_stages, FileLayer(raw))
    assert reset.at(("placements", "a", "visual", "color")).value == "blue"
    assert reset.at(("placements", "a", "visual", "color")).source == LayerSource("code", "next")
