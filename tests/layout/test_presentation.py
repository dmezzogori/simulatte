from simulatte import Environment, Layout
from simulatte.layout import reresolve_presentation
from simulatte.server import Server
import pytest
from simulatte.layout import LayoutError, Point
from tests.layout.test_hashes import prepared
from tests.layout.test_binding import line, vehicle


def test_saved_presentation_and_network_presence():
    env = Environment()
    layout = Layout(env)
    Server(env=env, capacity=1, name="machine")
    bundle = layout.prepare().bundle
    result = reresolve_presentation(
        bundle,
        {
            "schema_version": "1.0",
            "placements": {"machine": {"kind": "server", "position": [8, 2, 0], "visual": {"color": "red"}}},
        },
    )
    assert not result.requires_rerun
    assert result.bundle is not None
    assert result.bundle.physical_hash == bundle.physical_hash
    assert result.bundle.full_hash != bundle.full_hash
    result = reresolve_presentation(
        bundle, {"schema_version": "1.0", "network": {"type": "explicit", "nodes": [], "arcs": []}}
    )
    assert result.requires_rerun and result.bundle is None
    assert ("network",) in result.changed_paths


@pytest.mark.parametrize("leaf,value", [("position", [9, 1, 0]), ("visual", {"color": "red"})])
def test_first_presentation_patch_on_unnamed_target_at_prepare_and_reuse(leaf, value):
    env = Environment()
    server = Server(env=env, capacity=1)
    patch = {"schema_version": "1.0", "placements": {server.id: {"kind": "server", leaf: value}}}
    layout = Layout.from_dict(env, patch)
    bundle = layout.prepare().bundle
    assert any(d["code"] == "generated-id-presentation" for d in bundle["diagnostics"])
    assert not reresolve_presentation(bundle, patch).requires_rerun


@pytest.mark.parametrize(
    "patch",
    [
        {"footprint": {"width": 3}},
        {"obstacle": True},
        {"ports": {"entry": {"position": [0, 0, 0], "connector": {}}}},
    ],
)
def test_physical_changes_require_run(patch):
    _, result = prepared(read=True)
    edit = reresolve_presentation(
        result.bundle, {"schema_version": "1.0", "placements": {"machine": {"kind": "server", **patch}}}
    )
    assert edit.requires_rerun and edit.bundle is None and edit.changed_paths
    assert edit.original_physical_hash == result.physical_hash


def test_stage_absence_and_frozen_prefix_before_auto(monkeypatch):
    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1, name="machine")
    layout.place(machine)
    ref = layout.ref("placements", "machine", "position")
    calls = []

    @layout.stage("observe", reads=[ref])
    def observe(ctx):
        calls.append(ctx.physical(ref, default=[0, 0, 0]))
        later = Server(env=env, capacity=1, name="later")
        layout.place(later, position=Point(10, 10))

    bundle = layout.prepare().bundle
    assert calls == [(0, 0, 0)]

    def forbidden(*args, **kwargs):
        raise AssertionError("callback/RNG/network compilation executed")

    monkeypatch.setattr("simulatte.layout.network.resolve_network", forbidden)
    monkeypatch.setattr(env, "rng", forbidden)
    edit = reresolve_presentation(
        bundle, {"schema_version": "1.0", "placements": {"later": {"kind": "server", "visual": {"color": "red"}}}}
    )
    assert edit.bundle is not None and edit.bundle.physical_hash == bundle.physical_hash
    edit = reresolve_presentation(
        bundle, {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "position": [0, 0, 0]}}}
    )
    assert edit.requires_rerun and ("placements", "machine", "position") in edit.changed_paths
    assert calls == [(0, 0, 0)]


def test_numeric_rotation_comparison_and_fresh_file_replacement():
    env = Environment()
    layout = Layout.from_dict(
        env, {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"color": "red"}}}}
    )
    machine = Server(env=env, capacity=1, name="machine")
    layout.place(machine, rotation=360)
    ref = layout.ref("placements", "machine", "rotation")
    layout.on_bind("rotation", lambda ctx: ctx.physical(ref), reads=[ref])
    bundle = layout.prepare().bundle
    edit = reresolve_presentation(
        bundle, {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "rotation": -0.0}}}
    )
    assert not edit.requires_rerun and edit.bundle is not None
    assert "color" not in edit.bundle["resolved"]["placements"]["machine"]["visual"]
    assert edit.bundle.physical_hash == bundle.physical_hash


def test_node_and_agv_derived_positions_remain_authoritative():
    env = Environment()
    layout = line(env)
    vehicle(env, layout.node("A"), "cart")
    bundle = layout.prepare().bundle
    edit = reresolve_presentation(
        bundle,
        {
            "schema_version": "1.0",
            "placements": {
                "A": {"kind": "node", "position": [20, 20, 0]},
                "cart": {"kind": "agv", "position": [30, 30, 0]},
            },
        },
    )
    assert edit.bundle is not None and not edit.requires_rerun
    assert edit.bundle["resolved"]["placements"]["A"]["position"] == (0.0, 0.0, 0.0)
    assert edit.bundle["resolved"]["placements"]["cart"]["position"] == (0.0, 0.0, 0.0)
    assert edit.bundle.physical_hash == bundle.physical_hash
    with pytest.raises(LayoutError):
        reresolve_presentation(bundle, {"schema_version": "1.0", "unknown": 1})


def test_saved_same_value_physical_override_requires_explicit_name():
    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1)
    layout.place(machine, footprint={"width": 2})
    ref = layout.ref("placements", machine.id, "footprint", "width")
    layout.on_bind("width", lambda ctx: ctx.physical(ref), reads=[ref])
    bundle = layout.prepare().bundle
    patch = {"schema_version": "1.0", "placements": {machine.id: {"kind": "server", "footprint": {"width": 2.0}}}}
    with pytest.raises(LayoutError, match="explicitly named"):
        reresolve_presentation(bundle, patch)


def test_saved_generated_id_warning_added_then_removed():
    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1)
    bundle = layout.prepare().bundle
    edit = reresolve_presentation(
        bundle, {"schema_version": "1.0", "placements": {machine.id: {"kind": "server", "visual": {"color": "red"}}}}
    )
    assert edit.bundle is not None
    assert [d["target"] for d in edit.bundle["diagnostics"] if d["code"] == "generated-id-presentation"] == [machine.id]
    reset = reresolve_presentation(edit.bundle, {"schema_version": "1.0"})
    assert reset.bundle is not None
    assert not any(d["code"] == "generated-id-presentation" for d in reset.bundle["diagnostics"])


@pytest.mark.parametrize(
    "placement",
    [
        {"footprint": {"width": 3}},
        {"obstacle": False},
        {"obstacle": True},
        {"ports": {"visual": {"$unset": True}}},
    ],
)
def test_saved_unnamed_physical_eligibility_precedes_change_result(placement):
    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1)
    layout.place(machine, footprint={"width": 2}, obstacle=False, ports={"visual": {"position": [0, 0, 0]}})
    ref = layout.ref("placements", machine.id, "footprint", "width")
    layout.on_bind("width", lambda ctx: ctx.physical(ref), reads=[ref])
    bundle = layout.prepare().bundle
    with pytest.raises(LayoutError, match="explicitly named"):
        reresolve_presentation(
            bundle, {"schema_version": "1.0", "placements": {machine.id: {"kind": "server", **placement}}}
        )


def test_saved_unnamed_visual_port_coordinate_change_remains_eligible():
    env = Environment()
    layout = Layout(env)
    machine = Server(env=env, capacity=1)
    layout.place(machine, ports={"visual": {"position": [0, 0, 0]}})
    bundle = layout.prepare().bundle
    edit = reresolve_presentation(
        bundle,
        {
            "schema_version": "1.0",
            "placements": {machine.id: {"kind": "server", "ports": {"visual": {"position": [1, 0, 0]}}}},
        },
    )
    assert not edit.requires_rerun and edit.bundle is not None
    assert edit.bundle.physical_hash == bundle.physical_hash
    assert any(d["code"] == "generated-id-presentation" for d in edit.bundle["diagnostics"])
