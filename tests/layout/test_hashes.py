import hashlib
import json
from pathlib import Path

from simulatte import Environment, Layout
from simulatte.builders import build_immediate_release_system
from simulatte.scenario import Scenario
from simulatte.layout import LayoutBundle, ResolvedLayout
from simulatte.layout.identity import layout_hash
from tests.layout.test_binding import line, system
from tests.layout.test_traffic_fields import chord_only_square
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


PINNED_BUNDLES = {
    "valid-addresses": "9cb2bfb466803c33d5951d7b8e0f91c4f82aeb26a8c1e624be0b27c21614fb5c",
    "valid-empty": "de4be8c2bc9f2acaba156a6946fc5432ffa92a7e12edb2fb4373d6c48858e4d9",
    "valid-explicit": "c7b26ab69296592387323feca4273d6db544943f2e97eb54e0836eefe2b32794",
    "valid-grid": "07c6d076634a665cb053bc1f2aed8f6a8352cd89618be44d94fa5db8d09b8b48",
    "valid-lanes": "3d7eba3850f5324d82517107edc4543951132f28c989830dbb4e9baa3634bcbe",
    "valid-overrides": "72e0fac487b9da3d5e23690ebb6f8851815248b08b86dc7851b81c54fbec746d",
    "valid-placement": "1517b8fde4ee7d171205a83360d2b53c5389fb39c5406f6b92846094a58f28cd",
    "valid-traffic-explicit": "800f73ccd677366695fed4d85d0b25f463b61757ca3963ef8d88162a0ea515dd",
    "valid-traffic-lanes": "bf9cc8ca6895bffe656651c3c5ede98de1712b8dfd70cc1086d06dcd8ccde998",
    "valid-traffic-lattice": "1139feed0e7341f15bf2b1ecd7e2892ca93a9246f76538f36cb0eed1bb0ac36e",
    "valid-unset": "859fddbbb411ce36a75f8901f3cf37f524838c0cffa454fd544d4d4708efce9a",
    "code:line": "e9e85da59f7f582af9224c471407951165270e269511124e8b65c85478d3903f",
    "code:system": "f88b12dc44d37b8f2e4f032e731f4b4ebca2f000be76c2c25e4762485aa91331",
    "code:chord_only_square": "4978578e56733e34f5a86c9835bdb237715ce8979742b1f9f1c8ccbcbbf5a5bc",
    "code:shop10": "40c22e1a7d45de449c7270538333991b672075cacf2100fa53a62014f713aace",
}


def _pinned_model(name):
    if not name.startswith("code:"):
        path = Path(__file__).parents[1] / "fixtures" / "layout" / f"{name}.json"
        return Layout.from_dict(Environment(), json.loads(path.read_text()))
    if name == "code:line":
        return line(Environment())
    if name == "code:system":
        env = Environment()
        layout = line(env)
        system(env, layout)
        return layout
    if name == "code:chord_only_square":
        return chord_only_square()
    env = Environment(seed=3)
    layout = Layout(env)
    build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=10))
    return layout


@pytest.mark.parametrize("name", sorted(PINNED_BUNDLES))
def test_whole_bundles_are_those_of_the_commit_before_the_bundle_became_lazy(name):
    """The digests were read at 2946ebb, where preparation built and validated the bundle (spec §9)."""
    bundle = _pinned_model(name).prepare().bundle
    assert hashlib.sha256(json.dumps(bundle.to_dict(), sort_keys=True).encode()).hexdigest() == PINNED_BUNDLES[name]
