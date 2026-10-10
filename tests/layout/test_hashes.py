from simulatte import Environment, Layout
from simulatte.layout import LayoutBundle, ResolvedLayout
from simulatte.layout.identity import layout_hash
from tests.layout.test_binding import line
import dataclasses
import pytest
from simulatte.layout import LayoutError, Point, NetworkOverride
from simulatte.layout.identity import hashes, normalized_query
from simulatte.server import Server


def test_canonical_geometry_and_order():
    assert layout_hash("full", {"position": [1, -0.0, 0], "rotation": 360}) == layout_hash(
        "full", {"rotation": 0.0, "position": [1.0, 0, 0.0]}
    )
    assert layout_hash("full", {"a": [1, 2]}) != layout_hash("full", {"a": [2, 1]})


def test_final_bundle_roundtrip():
    result = line(Environment()).prepare()
    assert isinstance(result, ResolvedLayout)
    bundle = LayoutBundle.from_dict(result.bundle.to_dict())
    assert bundle.full_hash == result.full_hash
    bundle.check()
    assert bundle["network"]["nodes"]
    assert Layout(Environment()).prepare().bundle["network"] is None


def prepared(patch=None, *, read=False):
    env = Environment(seed=5)
    layout = Layout.from_dict(env, patch or {"schema_version": "1.0"})
    server = Server(env=env, capacity=1, name="machine")
    layout.place(server, position=Point(1, 0), rotation=0, footprint={"width": 2})
    if read:
        ref = layout.ref("placements", "machine", "footprint", "width")
        layout.on_bind("width", lambda ctx: ctx.physical(ref), reads=[ref])
    return env, layout.prepare()


def test_explicit_code_file_equal_and_layer_provenance_distinct():
    env, original = prepared(read=True)
    changed_env, same = prepared(
        {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "footprint": {"width": 2.0}}}}, read=True
    )
    assert original.physical_hash == same.physical_hash
    assert original.full_hash == same.full_hash
    assert original.layers_hash != same.layers_hash
    env.enable_digest()
    changed_env.enable_digest()
    env.run()
    changed_env.run()
    assert env.fingerprint() == changed_env.fingerprint()


def test_orphan_and_stale_properties_only_change_layers():
    _, original = prepared()
    _, changed = prepared(
        {"schema_version": "1.0", "placements": {"ghost": {"kind": "server", "position": [99, 0, 0]}}}
    )
    assert original.full_hash == changed.full_hash and original.physical_hash == changed.physical_hash
    assert original.layers_hash != changed.layers_hash
    results = []
    for enabled in (False, True):
        layout = line(Environment())
        layout.override(
            NetworkOverride(id="stale", target={"kind": "explicit", "id": "missing"}, properties={"enabled": enabled})
        )
        results.append(layout.prepare())
    a, b = results
    assert a.full_hash == b.full_hash and a.physical_hash == b.physical_hash and a.layers_hash != b.layers_hash


def test_immutable_bundle_and_result_and_copied_data():
    _, result = prepared()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "bundle", None)
    with pytest.raises(TypeError):
        result.bundle["resolved"]["placements"]["machine"]["position"][0] = 2
    copied = result.bundle.to_dict()
    copied["resolved"]["placements"]["machine"]["position"][0] = 90
    assert result.bundle["resolved"]["placements"]["machine"]["position"][0] == 1
    assert len(result.bundle) == len(dict(result.bundle))
    assert result.bundle.reusable and result.bundle.schema_version == "1.0"


def test_unknown_version_inspection_but_no_reuse():
    from simulatte.layout import reresolve_presentation

    opaque = LayoutBundle.from_dict({"schema_version": "9.0", "future": {"data": [1]}})
    assert not opaque.reusable and opaque["future"]["data"] == (1,)
    with pytest.raises(LayoutError, match="unsupported"):
        reresolve_presentation(opaque, {"schema_version": "1.0"})


def test_eligibility_and_descriptor_identity_and_domain_separation():
    descriptor = {"kind": "value", "path": ["placements", "x", "rotation"], "default": 360}
    a = normalized_query(descriptor, {"value": -0.0, "eligible": True})
    b = normalized_query({**descriptor, "default": 0}, {"value": 720, "eligible": True})
    assert a == b
    assert layout_hash("physical", a) != layout_hash("physical", {**a, "observed": {"value": 0.0, "eligible": False}})
    assert layout_hash("full", a) != layout_hash("physical", a)
    for value in (float("nan"), float("inf")):
        with pytest.raises(LayoutError, match="finite"):
            layout_hash("full", {"position": [value, 0, 0]})
    assert layout_hash("full", {"capacity": 1.0}) == layout_hash("full", {"capacity": 1})
    with pytest.raises(LayoutError, match="integers"):
        layout_hash("full", {"capacity": 1.5})


def test_unused_declared_network_and_disabled_metadata():
    from simulatte.layout import Explicit
    from simulatte.intralogistics.graph import LayoutGraph, Node

    empty = Layout(Environment()).prepare().bundle
    layout = Layout(Environment())
    layout.set_network(Explicit(LayoutGraph([Node("unused", 2, 3)], [])))
    layout.override(
        NetworkOverride(id="disable", target={"kind": "explicit", "id": "unused"}, properties={"enabled": False})
    )
    declared = layout.prepare().bundle
    assert declared["network"]["nodes"][0]["enabled"] is False
    assert declared["network"]["graph"]["nodes"] == ()
    assert declared.physical_hash != empty.physical_hash
    assert hashes(declared)["full_hash"] == declared.full_hash


def test_disabled_edge_tombstones_and_obstacle_geometry_metadata():
    from simulatte.layout import Grid, Lattice

    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1, name="obstacle")
    layout.place(machine, position=Point(10, 10), rotation=0, footprint={"width": 2, "depth": 2}, obstacle=True)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, 1, 0)))
    layout.override(
        NetworkOverride(
            id="disable",
            target={
                "kind": "edge",
                "source": {"kind": "lattice", "i": 0, "j": 0},
                "target": {"kind": "lattice", "i": 1, "j": 0},
            },
            properties={"enabled": False, "direction": {"$unset": True}},
        )
    )
    bundle = layout.prepare().bundle
    assert bundle["network"]["edges"][0]["direction"] is None
    assert bundle["network"]["obstacles"][0]["vertices"][0] == (9.0, 9.0, 0.0)
    bundle.check()
