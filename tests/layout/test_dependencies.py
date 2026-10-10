from __future__ import annotations

import json

import pytest

from simulatte.layout.dependencies import DependencyClosure
from simulatte.layout.errors import LayoutError
from simulatte.layout.layers import FileLayer, resolve_layers


def values(patch=None, auto=None):
    return resolve_layers(
        [("declare", {"schema_version": "1.0", **(patch or {})})],
        FileLayer({"schema_version": "1.0"}),
        auto,
        targets={"a": "server", "b": "server"},
    )


def test_negative_structural_order_and_serialization():
    closure = DependencyClosure()
    resolved = values(
        {
            "placements": {"a": {"kind": "server"}},
            "network": {
                "type": "explicit",
                "nodes": [{"id": "a", "position": [0, 0, 0]}, {"id": "b", "position": [1, 0, 0]}],
                "arcs": [],
            },
        }
    )
    assert closure.read(resolved, ("placements", "a", "rotation"), default=0) == 0
    closure.read(resolved, ("network", "nodes"))
    assert closure.select(resolved, ("placements",), "obstacle") == ()
    saved = json.loads(json.dumps(closure.to_dict()))
    restored = DependencyClosure.from_dict(saved)
    assert not restored.changed(resolved)
    changed = values(
        {
            "placements": {"a": {"kind": "server", "rotation": 0, "obstacle": True}},
            "network": {
                "type": "explicit",
                "nodes": [{"id": "b", "position": [1, 0, 0]}, {"id": "a", "position": [0, 0, 0]}],
                "arcs": [],
            },
        }
    )
    assert set(restored.changed(changed)) == {("placements", "a", "rotation"), ("network", "nodes"), ("placements",)}


def test_auto_and_mixed_descendants_rejected():
    resolved = values(
        {"placements": {"a": {"kind": "server"}}},
        {"schema_version": "1.0", "placements": {"a": {"kind": "server", "rotation": 0}}},
    )
    closure = DependencyClosure()
    with pytest.raises(LayoutError, match="auto"):
        closure.read(resolved, ("placements", "a"))
    with pytest.raises(LayoutError):
        closure.read(resolved, ("placements", "a", "rotation"))
    assert closure.read(resolved, ("placements", "a", "rotation"), default=4) == 4


def test_schema_defaults_and_equivalent_sources():
    code = values({"network": {"type": "explicit", "nodes": [], "arcs": []}})
    closure = DependencyClosure()
    assert closure.read(code, ("network", "clearance")) == 0
    file = resolve_layers(
        [],
        FileLayer({"schema_version": "1.0", "network": {"type": "explicit", "nodes": [], "arcs": [], "clearance": 0}}),
    )
    assert not closure.changed(file)


def test_membership_empty_and_present_maps_and_ordered_lists():
    closure = DependencyClosure()
    first = values({"placements": {}, "network": {"type": "explicit", "nodes": [], "arcs": []}})
    assert closure.record(first, ("placements",), kind="membership")["members"] == []
    assert closure.record(first, ("network", "nodes"), kind="membership")["members"] == []
    second = values(
        {
            "placements": {"a": {"kind": "server"}},
            "network": {"type": "explicit", "nodes": [{"id": "a", "position": [0, 0, 0]}], "arcs": []},
        }
    )
    assert set(closure.changed(second)) == {("placements",), ("network", "nodes")}


def test_sealed_parent_coverage_and_snapshot_reevaluation_before_auto():
    resolved = values({"placements": {"a": {"kind": "server"}}})
    closure = DependencyClosure()
    closure.read(resolved, ("placements", "a", "rotation"), default={"angle": [0]})
    frozen = closure.read(resolved, ("placements", "a", "rotation"), default={"angle": [0]})
    with pytest.raises(TypeError):
        frozen["angle"] = [2]
    closure.seal()
    assert closure.read(resolved, ("placements", "a", "rotation"), default=0) == 0
    with pytest.raises(LayoutError, match="covered"):
        closure.read(resolved, ("grid",), default=None)
    for action in (
        lambda: closure.record(resolved, ("grid",)),
        lambda: closure.bind("a", {}),
        lambda: closure.extend_network(resolved),
    ):
        with pytest.raises(LayoutError, match="sealed"):
            action()
    auto = {"schema_version": "1.0", "placements": {"a": {"kind": "server", "rotation": 0}}}
    later = resolve_layers(resolved.bundle.code_stages, resolved.bundle.file_layer, auto, targets=resolved.targets)
    assert not closure.changed(later)
    assert not closure.reevaluate(later.bundle.code_stages, later.bundle.file_layer)
    patch = FileLayer({"schema_version": "1.0", "placements": {"a": {"kind": "server", "rotation": 0}}})
    assert closure.reevaluate(later.bundle.code_stages, patch) == (("placements", "a", "rotation"),)


def test_unset_parent_absence_and_present_parent_membership():
    first = values({"placements": {"a": {"kind": "server", "rotation": {"$unset": True}}}})
    closure = DependencyClosure()
    assert closure.read(first, ("placements", "a", "rotation"), default=0) == 0
    closure.read(first, ("placements", "a"))
    second = values({"placements": {"a": {"kind": "server"}}})
    assert ("placements", "a", "rotation") in closure.changed(second)
    with pytest.raises(LayoutError, match="required"):
        DependencyClosure().read(first, ("placements", "a", "rotation"))


@pytest.mark.parametrize("value", [object(), float("nan"), "\ud800"])
def test_dependencies_must_remain_serializable(value):
    closure = DependencyClosure()
    with pytest.raises(LayoutError, match="serializable"):
        closure.read(values(), ("grid",), default=value)
    with pytest.raises(LayoutError, match="serializable"):
        closure.bind("bad", value)


def test_selection_predicate_flags_connector_membership_and_invalid_queries():
    closure = DependencyClosure()
    first = values(
        {"placements": {"a": {"kind": "server", "ports": {"p": {"position": [0, 0, 0], "approach": [1, 0, 0]}}}}}
    )
    assert closure.select(first, ("placements",), "connector") == ()
    second = values(
        {
            "placements": {
                "a": {"kind": "server", "ports": {"p": {"position": [0, 0, 0], "approach": [1, 0, 0], "connector": {}}}}
            }
        }
    )
    assert closure.changed(second) == (("placements",),)
    for kind, options in [("unknown", {}), ("selection", {"predicate": "unknown"})]:
        with pytest.raises(LayoutError, match="unknown"):
            closure.record(first, ("placements",), kind=kind, **options)
    with pytest.raises(LayoutError, match="mapping"):
        closure.select(first, ("placements", "a", "ports", "p", "position"), "obstacle")


def test_network_extension_never_changes_existing_predicates_or_selected_members():
    closure = DependencyClosure()
    first = values({"placements": {"a": {"kind": "server", "obstacle": False}}})
    with closure.network_queries():
        closure.select(first, ("placements",), "obstacle")
    for patch in (
        {"a": {"kind": "server", "obstacle": True}},
        {"a": {"kind": "server", "obstacle": False}, "b": {"kind": "server", "obstacle": True}},
        {},
    ):
        assert closure.changed(values({"placements": patch}), allow_network_extensions=True)
    extension = values({"placements": {"a": {"kind": "server", "obstacle": False}, "b": {"kind": "server"}}})
    assert not closure.changed(extension, allow_network_extensions=True)
    closure.extend_network(extension)
    assert not closure.changed(extension)
    closure.bind("endpoint", {"id": "a"})
    closure.bind("endpoint", {"id": "a"})
    with pytest.raises(LayoutError, match="conflicting"):
        closure.bind("endpoint", {"id": "b"})


def test_parent_queries_include_nested_unset_tombstones():
    first = values({"placements": {"a": {"kind": "server", "rotation": {"$unset": True}}}})
    closure = DependencyClosure()
    closure.read(first, ("placements", "a"))
    second = values({"placements": {"a": {"kind": "server"}}})
    assert closure.changed(second) == (("placements", "a"),)


def test_network_and_grid_absence_are_always_reuse_dependencies():
    from simulatte.layout.lifecycle import PreparationCoordinator

    engine = PreparationCoordinator(object())
    engine.prepare()
    for patch, path in (
        ({"network": {"type": "lattice", "bounds": [0, 0, 1, 1]}}, ("network",)),
        ({"grid": {"dx": 1, "dy": 1}}, ("grid",)),
    ):
        file = FileLayer({"schema_version": "1.0", **patch})
        assert engine.closure.reevaluate(engine.values.bundle.code_stages, file) == (path,)


def test_connector_selection_preserves_absent_vs_unset_ports():
    closure = DependencyClosure()
    first = values({"placements": {"a": {"kind": "server"}}})
    closure.select(first, ("placements",), "connector")
    second = values({"placements": {"a": {"kind": "server", "ports": {"p": {"$unset": True}}}}})
    assert closure.changed(second) == (("placements",),)


def test_physical_identity_omits_code_file_origin_and_replay_scope():
    code = values({"network": {"type": "explicit", "nodes": [], "arcs": []}})
    file = resolve_layers(
        [],
        FileLayer({"schema_version": "1.0", "network": {"type": "explicit", "nodes": [], "arcs": [], "clearance": 0}}),
    )
    left, right = DependencyClosure(), DependencyClosure()
    left.read(code, ("network", "clearance"))
    right.read(file, ("network", "clearance"))
    assert left.to_dict() != right.to_dict()
    assert left.physical_data() == right.physical_data()
    left.read(file, ("network", "clearance"))
    assert left.physical_data() == right.physical_data()
    left.seal()
    with pytest.raises(LayoutError, match="serializable"):
        left.read(code, ("network", "clearance"), default=object())
    # A covered absence still requires an explicit fallback on each actual read.
    absent = DependencyClosure()
    absent.read(code, ("grid",), default=None)
    absent.seal()
    with pytest.raises(LayoutError, match="required"):
        absent.read(code, ("grid",))
