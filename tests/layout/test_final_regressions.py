"""Model-visible canonical values and final saved-layout boundary regressions."""

import math

import pytest

from simulatte import Environment, Layout, Trace, TraceRecorder
from simulatte._wire import escape_key
from simulatte.layout import LayoutBundle, Point, reresolve_presentation
from simulatte.server import Server
from tests.layout.test_binding import line, vehicle


def patch(**placement):
    return {"schema_version": "1.0", "placements": {"hall": {"kind": "server", **placement}}}


@pytest.mark.parametrize("parent", [False, True])
@pytest.mark.parametrize("phase", ["stage", "binder"])
def test_physical_numeric_and_map_values_match_fresh_and_saved_models(parent, phase):
    def make(rotation, zero, keys):
        env = Environment()
        ports = {key: {"position": [zero, 0, 0], "approach": [1, 0, 0]} for key in keys}
        source = patch(rotation=rotation, position=[zero, 0, 0], ports=ports)
        layout = Layout.from_dict(env, source)
        Server(env=env, capacity=1, name="hall")
        root = layout.ref("placements", "hall")
        refs = [layout.ref(*root.path, name) for name in ("rotation", "position", "ports")]
        observed = []

        def consume(ctx):
            if parent:
                value = ctx.physical(root)
                angle, position, names = (value[name] for name in ("rotation", "position", "ports"))
            else:
                angle, position, names = (ctx.physical(ref) for ref in refs)
            output = (angle, math.copysign(1, position[0]), tuple(names))
            observed.append(output)
            if phase == "stage" and ctx is not layout:
                Server(env=env, capacity=1, name="canonical" if output == expected else "different")
            assert type(angle) is float and all(type(n) is float for n in position)
            for port in names.values():
                assert tuple(port) == ("approach", "position")
                assert math.copysign(1, port["position"][0]) == 1
            with pytest.raises(TypeError):
                names["new"] = {}

        expected = (0.0, 1.0, tuple(sorted(keys, key=escape_key)))
        # A whole placement bind read needs every auto-filled field explicitly supplied.
        if parent and phase == "binder":
            layout.place(
                "hall", kind="server", footprint={"width": 1, "depth": 1, "height": 0}, obstacle=False, visual={}
            )
        if phase == "stage":
            layout.stage("model", reads=[root] if parent else refs)(consume)
        else:
            layout.on_bind("model", consume, reads=[root] if parent else refs)
        bundle = layout.prepare().bundle
        consume(layout)  # covered reads after finalization use exactly the same representation
        assert observed == [expected, expected]
        assert layout.to_dict()["placements"]["hall"]["rotation"] == rotation
        return bundle, source

    keys = ["~a", "é", "z", "a", "constructor", "__proto__"]
    original, _ = make(360, -0.0, keys)
    fresh, replacement = make(0.0, 0, keys[::-1])
    replay = reresolve_presentation(original, replacement)
    assert not replay.requires_rerun and replay.bundle is not None
    assert original.physical_hash == fresh.physical_hash == replay.bundle.physical_hash
    assert original["targets"] == fresh["targets"] == replay.bundle["targets"]
    if phase == "stage":
        assert "canonical" in original["targets"] and "different" not in original["targets"]


@pytest.mark.parametrize("phase", ["stage", "binder"])
def test_physical_default_containers_are_canonical_and_lists_keep_order(phase):
    env = Environment()
    layout = Layout(env)
    Server(env=env, capacity=1, name="hall")
    layout.place("hall", kind="server")
    ref = layout.ref("placements", "hall", "ports")
    fallback = {"z": {"position": [-0.0, 2, 0]}, "a": {"position": [3, 1, 0]}}
    observations = []

    def consume(ctx):
        ports = ctx.physical(ref, default=fallback)
        observations.append(tuple(ports))
        assert tuple(ports) == ("a", "z")
        assert ports["a"]["position"] == (3.0, 1.0, 0.0)
        assert type(ports["a"]["position"][0]) is float
        assert math.copysign(1, ports["z"]["position"][0]) == 1

    if phase == "stage":
        layout.stage("model", reads=[ref])(consume)
    else:
        layout.on_bind("model", consume, reads=[ref])
    bundle = layout.prepare().bundle
    consume(layout)
    assert observations == [("a", "z"), ("a", "z")]
    assert not reresolve_presentation(bundle, {"schema_version": "1.0"}).requires_rerun
    assert reresolve_presentation(bundle, patch(ports=fallback)).requires_rerun
    assert fallback["z"]["position"][0] == 0 and math.copysign(1, fallback["z"]["position"][0]) == -1


@pytest.mark.parametrize("author", ["code", "file"])
@pytest.mark.parametrize("magnitude", [10**16, 10**308])
def test_large_integer_geometry_matches_float_through_bundle_and_trace(tmp_path, author, magnitude):
    bundles = []
    for value in (magnitude, float(magnitude)):
        env = Environment(seed=11)
        source = patch(position=[value, 0, 0], rotation=360, visual={"label": {"$unset": True}})
        source["placements"]["orphan"] = {"kind": "server", "position": [value, 0, 0]}
        layout = Layout.from_dict(env, source if author == "file" else {"schema_version": "1.0"})
        Server(env=env, capacity=1, name="hall")
        if author == "code":
            for key, entry in source["placements"].items():
                layout.place(key, **entry)
        path = tmp_path / f"{author}-{type(value).__name__}.simtrace"
        TraceRecorder(env, path)
        bundle = layout.prepare().bundle
        copied = LayoutBundle.from_dict(bundle.to_dict())
        copied.check()
        assert copied == bundle
        layers = bundle["layers"]
        authored = layers["file"] if author == "file" else layers["code"][0][1]
        assert type(authored["placements"]["hall"]["position"][0]) is float
        assert authored["placements"]["hall"]["rotation"] == 360
        assert authored["placements"]["hall"]["visual"]["label"] == {"$unset": True}
        env.run()
        env.close()
        trace = Trace.open(path)
        trace.check()
        assert trace.layout == bundle
        bundles.append(bundle)
    assert bundles[0] == bundles[1]


@pytest.mark.parametrize("bound", [False, True])
def test_agv_effective_position_only_comes_from_bound_node(bound):
    env = Environment()
    layout = line(env) if bound else Layout(env)
    cart = vehicle(env, layout.node("A") if bound else None, "cart")
    layout.place(cart, position=Point(30, 40))
    original = layout.prepare().bundle
    replacement = {"schema_version": "1.0", "placements": {"cart": {"kind": "agv", "position": [60, 70, 0]}}}
    replay = reresolve_presentation(original, replacement)
    assert replay.bundle is not None and not replay.requires_rerun
    for bundle in (original, replay.bundle):
        effective = bundle["resolved"]["placements"]["cart"]
        if bound:
            assert effective["position"] == (0.0, 0.0, 0.0)
        else:
            assert "position" not in effective
            assert cart.current_node is None
    assert original["layers"]["code"][-1][1]["placements"]["cart"]["position"] == (30.0, 40.0, 0.0)
    assert replay.bundle["layers"]["file"]["placements"]["cart"]["position"] == (60.0, 70.0, 0.0)


@pytest.mark.parametrize("reversed_registration", [False, True])
def test_diagnostics_follow_execution_order_then_file_and_unstaged(reversed_registration):
    env = Environment()
    source = {"schema_version": "1.0", "placements": {"ghost-file": {"kind": "server"}}}
    layout = Layout.from_dict(env, source)
    layout.place("ghost-declare", kind="server")

    def zeta(ctx):
        layout.place("ghost-z", kind="server")

    def alpha(ctx):
        layout.place("ghost-a", kind="server")

    stages = [("zeta", zeta, []), ("alpha", alpha, ["zeta"])]
    for name, callback, after in stages[::-1] if reversed_registration else stages:
        layout.stage(name, reads=[], after=after)(callback)
    bundle = layout.prepare().bundle
    replay = reresolve_presentation(bundle, source)
    assert replay.bundle is not None
    for saved in (bundle, replay.bundle):
        assert [(d["stage"], d["target"]) for d in saved["diagnostics"]] == [
            ("declare", "ghost-declare"),
            ("zeta", "ghost-z"),
            ("alpha", "ghost-a"),
            (None, "ghost-file"),
        ]


@pytest.mark.parametrize("default", [False, True])
def test_known_bundle_materializes_path_addressed_large_geometry(default):
    env = Environment()
    layout = Layout(env)
    Server(env=env, capacity=1, name="hall")
    layout.place("hall", kind="server", **({} if default else {"footprint": {"width": 1e16}}))
    width = layout.ref("placements", "hall", "footprint", "width")
    layout.on_bind("read", lambda ctx: ctx.physical(width, default=1e16), reads=[width])
    bundle = layout.prepare().bundle
    encoded = bundle.to_dict()
    for query in encoded["dependencies"]["queries"]:
        if query["descriptor"]["path"] == list(width.path):
            if "default" in query["descriptor"]:
                query["descriptor"]["default"] = 10**16
            if query["observed"]["state"] == "present":
                query["observed"]["value"] = 10**16
    decoded = LayoutBundle.from_dict(encoded)
    decoded.check()
    assert decoded == bundle


def test_future_bundle_geometry_remains_opaque():
    bundle = LayoutBundle.from_dict({"schema_version": "9.0", "position": [-0.0, 1, 0], "rotation": 360})
    assert type(bundle["position"][1]) is int
    assert math.copysign(1, bundle["position"][0]) == -1
    assert type(bundle["rotation"]) is int and bundle["rotation"] == 360
