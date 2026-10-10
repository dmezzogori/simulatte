from simulatte import Environment, Layout
from simulatte.layout import LayoutError
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.layout import Footprint, Grid, Lattice, Point
from tests.layout.test_binding import line, system, vehicle
import pytest
from typing import Any


def test_stage_cannot_advance_and_failure_latches():
    env = Environment()
    layout = Layout(env)

    @layout.stage("bad", reads=())
    def bad(ctx):
        with pytest.raises(LayoutError):
            ctx.env.activate()

    with pytest.raises(LayoutError):
        layout.prepare()
    with pytest.raises(LayoutError):
        layout.prepare()
    assert env.now == 0 and env._initial_state is None


def test_layout_step_requires_activation_even_after_prepare():
    env = Environment()
    layout = Layout(env)
    env.timeout(1)
    layout.prepare()
    with pytest.raises(LayoutError, match="activate"):
        env.step()
    env.activate()
    env.step()
    assert env.now == 1


@pytest.mark.parametrize("action", ["activate", "run", "step", "prepare"])
def test_caught_reentrant_attempt_still_latches_failure(action):
    env = Environment()
    layout = line(env)

    @layout.stage("reenter", reads=())
    def reenter(ctx):
        with pytest.raises(LayoutError):
            getattr(layout if action == "prepare" else env, action)()

    with pytest.raises(LayoutError):
        layout.prepare()
    for retry in (layout.prepare, env.activate, env.run, env.step):
        with pytest.raises(LayoutError):
            retry()
    assert env._initial_state is None and env.now == 0


def test_prepare_runs_stages_once_without_initializers_commands_or_time_advance():
    env = Environment()
    layout = line(env)
    observed = []

    @layout.stage("create", reads=())
    def create(ctx):
        vehicle(ctx.env, layout.node("A"))

        def process():
            observed.append("process")
            yield env.timeout(1)

        env.process(process())
        observed.append("stage")

    env.on_activate(lambda: observed.append("initialize"))
    first = layout.prepare()
    assert layout.prepare() is first and observed == ["stage"] and env.now == 0
    env.activate()
    assert observed == ["stage", "initialize"]
    env.run()
    assert observed == ["stage", "initialize", "process"] and env.now == 1


@pytest.mark.parametrize("failure", ["missing", "binding", "collision", "custom_owner"])
def test_failed_preparation_leaves_initial_and_command_queue_untouched(failure):
    env = Environment()
    layout = line(env)
    fleet, _, origin, destination, *_ = system(env, layout)
    from tests.layout.test_binding import SKU_A

    order = fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination)
    fleet.submit(order)
    fleet.cancel(order)
    if failure == "missing":
        vehicle(env, layout.node("absent"))
    elif failure == "binding":

        def fail(ctx):
            raise ValueError("binder error")

        layout.on_bind("fail", fail)
    elif failure == "collision":
        vehicle(env, layout.node("A"), "A")
    else:
        layout.register_resource_owner(object(), owner=object(), graph=layout.graph(), binder="absent")
    before = list(env._commands)
    with pytest.raises(ValueError):
        env.activate()
    assert env._commands == before and env._initial_state is None and env.now == 0
    assert order.status.name == "PENDING_ACTIVATION"


def test_layout_submit_cancel_fifo_and_initial_placement():
    env = Environment(debug=True)
    layout = line(env)
    fleet, cart, origin, destination, *_ = system(env, layout, start="B")
    from tests.layout.test_binding import SKU_A

    order = fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination)
    fleet.submit(order)
    fleet.cancel(order)
    layout.prepare()
    assert order.status.name == "PENDING_ACTIVATION" and order.dispatched_at is None
    env.run()
    assert order.status.name == "CANCELLED" and order.dispatched_at == 0
    assert env.initial_state[cart.id]["node"] == "B"


@pytest.mark.parametrize("operation", ["timeout", "process", "entity", "declaration"])
def test_custom_binding_cannot_schedule_attach_or_mutate(operation):
    env = Environment()
    layout = line(env)

    def bind(ctx):
        if operation == "timeout":
            env.timeout(1)
        elif operation == "process":

            def task():
                yield env.timeout(1)

            env.process(task())
        elif operation == "entity":
            vehicle(env, None)
        else:
            layout.set_grid(Grid(1, 1))

    layout.on_bind("illegal", bind)
    with pytest.raises((LayoutError, RuntimeError)):
        layout.prepare()
    assert env._queue == [] and env._initial_state is None
    assert "schedule" not in env.__dict__
    with pytest.raises(LayoutError):
        layout.prepare()


def test_nested_scheduling_guards_restore_outer_block():
    env = Environment()
    with env._scheduling_blocked():
        before = env.schedule
        env._run_initializer(lambda: None)
        assert env.schedule == before
        with env._internal_scheduling():
            env.timeout(1)
        assert env.schedule == before
        with pytest.raises(RuntimeError):
            env.timeout(2)
    assert "schedule" not in env.__dict__


class Area(Entity, kind="layout_test_area"):
    state_schema = StateSchema({"value": FieldSpec("int")})

    def __init__(self, env, name=None):
        self.value = 1
        env.entities.attach(self, name=name)

    def snapshot(self):
        return {"value": self.value, "label": self.label}


def test_custom_targets_opt_in_stage_ownership_and_orphans_not_physical():
    env = Environment()
    excluded = Area(env, "unplaced")
    layout = Layout(env)
    hall = Area(env, "hall")
    layout.place(hall, footprint=Footprint(5, 4))
    layout.place("ghost", kind="layout_test_area", obstacle=True, position=Point(0, 0), footprint=Footprint(3, 3))
    values = []

    @layout.stage("equipment", reads=(layout.ref("placements", "hall", "footprint", "width"),))
    def equipment(ctx):
        values.append(ctx.physical(layout.ref("placements", "hall", "footprint", "width")))
        area = Area(env, "equipment")
        layout.place(area, position=Point(10, 10))

    result = layout.prepare()
    assert values == [5]
    assert excluded.id not in result.values.targets and "ghost" not in result.values.targets
    assert any(d.code == "orphan-placement" for d in result.diagnostics)
    assert layout.physical(layout.ref("placements", "hall", "footprint", "width")) == 5
    with pytest.raises(LayoutError, match="not covered"):
        layout.physical(layout.ref("placements", "hall", "position"))


def test_unmatched_code_target_cannot_survive_as_physical_final_dependency():
    env = Environment()
    layout = Layout(env)
    layout.place("ghost", kind="layout_test_area", footprint=Footprint(5, 4))
    layout.stage("read", reads=(layout.ref("placements", "ghost", "footprint"),))(lambda ctx: None)
    with pytest.raises(LayoutError, match="frozen"):
        layout.prepare()


@pytest.mark.parametrize("source", ["bind", "stage"])
def test_generated_id_physical_file_override_fails_after_actual_read(source):
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {"layout_test_area-0": {"kind": "layout_test_area", "footprint": {"width": 7, "depth": 4}}},
        },
    )
    area = Area(env)
    layout.place(area, footprint=Footprint(5, 4))
    read = layout.ref("placements", area.id, "footprint", "width")
    if source == "bind":
        layout.on_bind("consume", lambda ctx: ctx.physical(read), reads=(read,))
    else:
        layout.stage("consume", reads=(read,))(lambda ctx: ctx.physical(read))
    with pytest.raises(LayoutError, match="explicitly named"):
        layout.prepare()
    assert not env.entities.is_named(area.id)


def test_presentation_file_override_on_generated_id_remains_allowed_despite_other_physical_reads():
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {"layout_test_area-0": {"kind": "layout_test_area", "visual": {"label": "screen"}}},
        },
    )
    area = Area(env)
    layout.place(area, footprint=Footprint(5, 4))
    read = layout.ref("placements", area.id, "footprint", "width")
    layout.on_bind("consume", lambda ctx: ctx.physical(read), reads=(read,))
    result = layout.prepare()
    assert any(d.code == "generated-id-presentation" for d in result.diagnostics)


def test_bind_read_cannot_consume_auto_presentation_value():
    env = Environment()
    layout = Layout(env)
    area = Area(env, "hall")
    layout.place(area)
    read = layout.ref("placements", "hall", "position")
    layout.on_bind("consume", lambda ctx: ctx.physical(read), reads=(read,))
    with pytest.raises(LayoutError, match="auto value"):
        layout.prepare()


def test_graph_barrier_allows_later_nonphysical_targets_but_not_connectors():
    env = Environment()
    layout = Layout(env)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, 2, 0)))
    seen = []

    @layout.stage("late", reads=(layout.graph(),))
    def late(ctx):
        seen.append(ctx.graph(layout.graph()))
        area = Area(env, "later")
        layout.place(area, position=Point(20, 20))

    result = layout.prepare()
    assert seen == [result.network.graph]
    assert "later" in result.auto.targets


def test_explicit_graph_definition_targets_exist_before_nodebindings_and_coords_win():
    env = Environment()
    layout = line(env)
    layout.place("A", kind="node", position=Point(99, 99))
    cart = vehicle(env, layout.node("A"))
    layout.require_path(layout.node("A"), layout.node("B"))
    result = layout.prepare()
    assert result.auto.derived_positions["A"] == Point(0, 0)
    assert result.auto.derived_positions[cart.id] == Point(0, 0)
    assert result.required_paths[0].source == layout.node("A").address
    assert any(d.code == "node-position-ignored" for d in result.diagnostics)


def test_pending_declaration_can_be_read_before_its_later_attachment():
    env = Environment()
    layout = Layout(env)
    layout.place("hall", kind="layout_test_area", footprint=Footprint(5, 4))
    read = layout.ref("placements", "hall", "footprint", "width")

    @layout.stage("attach", reads=(read,))
    def attach(ctx):
        assert ctx.physical(read) == 5
        Area(env, "hall")

    assert layout.prepare().values.targets["hall"] == "layout_test_area"


def test_facade_file_roundtrip_load_once_flows_and_validation(tmp_path):
    import json
    from simulatte.layout import LayoutLimits, NetworkOverride
    from simulatte.server import Server

    env = Environment()
    server = Server(env=env, capacity=1, name="machine")
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps({"schema_version": "1.0", "placements": {"machine": {"kind": "server", "position": [4, 5, 0]}}})
    )
    layout = Layout(env, file=path)
    layout.flow([server, "future"])
    layout.place(server, rotation=90)
    layout.set_grid(Grid(1, 1))
    layout.set_network(Lattice((0, 0, 1, 0)))
    layout.override(NetworkOverride("first", {"kind": "row", "j": 0}, {"capacity": 2}))
    layout.override(NetworkOverride("second", {"kind": "column", "i": 1}, {"capacity": 3}))
    path.write_text('{"schema_version":"1.0"}')
    assert "machine" in layout.to_dict()["placements"]  # loaded once
    layout.save(path)
    layout.load(path)
    assert json.loads(path.read_text()) == layout.to_dict()
    result = layout.prepare()
    assert result.values.data["placements"]["machine"]["position"] == (4, 5, 0)
    assert result.auto.topology.flows == (("machine", "future"),)
    assert sorted(result.network.capacities.values()) == [2, 3]
    assert env.entities.is_named("machine") and not env.entities.is_named("absent")
    assert any(d.code == "unknown-flow-target" for d in result.diagnostics)
    with pytest.raises(LayoutError):
        layout.load(path)
    empty = Environment()
    with pytest.raises(LayoutError, match="max_placements"):
        Layout.from_dict(
            empty,
            {"schema_version": "1.0", "placements": {"a": {"kind": "server"}, "b": {"kind": "server"}}},
            limits=LayoutLimits(max_placements=1),
        )
    assert empty.layout is None


@pytest.mark.parametrize("change", ["existing_target", "obstacle_after_barrier", "network_after_barrier"])
def test_stage_freezes_and_late_network_edits(change):
    from simulatte.server import Server

    env = Environment()
    layout = line(env)
    server = Server(env=env, capacity=1, name="server")
    layout.place(server, position=Point(8, 8))

    @layout.stage("later", reads=(layout.graph(),))
    def later(ctx):
        if change == "existing_target":
            layout.place(server, visual={"label": "late"})
        elif change == "obstacle_after_barrier":
            later = Server(env=env, capacity=1, name="later")
            layout.place(later, obstacle=True, position=Point(0, 0), footprint=Footprint(1, 1))
        else:
            layout.set_network(Lattice((0, 0, 2, 2)))

    with pytest.raises(LayoutError, match="frozen|late-network-edit"):
        layout.prepare()


def test_facade_rejects_invalid_arguments_and_foreign_object_ownership():
    from simulatte.layout import Connector, Port

    env, other = Environment(), Environment()
    layout, foreign = Layout(env), Layout(other)
    own, alien = Area(env, "own"), Area(other, "alien")
    invalid: Any = None
    bad = [
        lambda: Layout(env),
        lambda: layout.place("missing"),
        lambda: layout.place(own, kind="server"),
        lambda: layout.place(own, colour="red"),
        lambda: layout.place(alien),
        lambda: layout.flow([alien]),
        lambda: layout.port(alien, "out"),
        lambda: layout.set_grid(invalid),
        lambda: layout.set_network(invalid),
        lambda: layout.override(invalid),
        lambda: layout.require_path(layout.node("x"), foreign.node("x")),
        lambda: layout.place(own, ports={"out": Port(Point(1, 0), Point(1, 0), Connector(foreign.node("x")))}),
    ]
    for operation in bad:
        with pytest.raises(LayoutError):
            operation()
    env2 = Environment()
    env2.activate()
    with pytest.raises(LayoutError):
        Layout(env2)


def test_graph_reference_without_network_fails_before_binding():
    from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

    env = Environment()
    layout = Layout(env)
    ResourceBasedTrafficManager(env=env, graph=layout.graph())
    with pytest.raises(LayoutError, match="declared network"):
        layout.prepare()


def test_port_binding_aliases_share_canonical_graph_node_and_capacity():
    from simulatte.layout import Connector, Port
    from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

    env = Environment()
    layout = line(env)
    area = Area(env, "dock")
    layout.place(
        area,
        position=Point(-1.5, 0),
        rotation=0,
        footprint=Footprint(1, 1),
        ports={"out": Port(Point(0.5, 0), Point(1, 0), Connector(layout.node("A"), approach_margin=1))},
    )
    ref = layout.port(area, "out")
    cart = vehicle(env, ref)
    manager = ResourceBasedTrafficManager(env=env, graph=layout.graph())
    result = layout.prepare()
    assert cart.current_node is result.network.node(ref.address)
    assert cart.current_node in manager._node_resources
    assert result.auto.derived_positions[cart.id] == Point(-1, 0)


def test_rejected_constructor_does_not_register_explicit_graph_use():
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

    env = Environment()
    with pytest.raises(ValueError):
        vehicle(env, Node("A", 0, 0), "")
    with pytest.raises(ValueError):
        ResourceBasedTrafficManager(env=env, graph=LayoutGraph([], []), node_capacity=0)
    assert Layout(env).prepare() is not None


def test_retired_targets_are_not_final_static_entities():
    env = Environment()
    layout = Layout(env)
    area = Area(env, "gone")
    layout.place(area, position=Point(1, 2))
    env.entities.retire(area)
    result = layout.prepare()
    assert "gone" not in result.values.targets
    assert any(item.code == "orphan-placement" for item in result.diagnostics)


@pytest.mark.parametrize("name", ["agv-2", "retired"])
def test_all_node_names_preflight_before_first_node_binding(name):
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.layout import Explicit

    env = Environment()
    layout = Layout(env)
    if name == "retired":
        area = Area(env, name)
        env.entities.retire(area)
    layout.set_network(Explicit(LayoutGraph([Node("A", 0, 0), Node(name, 1, 0)], [])))
    with pytest.raises(LayoutError, match="reserved|already used"):
        layout.prepare()
    assert not any(entity.kind == "node" for entity in env.entities.live())


@pytest.mark.parametrize("edit", ["position", "new_port", "connector"])
def test_d30_visual_port_edits_follow_selection_dependencies(edit):
    from simulatte.layout import Port

    patch = {"position": [2, 0, 0]} if edit != "connector" else {"connector": {}}
    port_name = "new" if edit == "new_port" else "visual"
    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {"layout_test_area-0": {"kind": "layout_test_area", "ports": {port_name: patch}}},
        },
    )
    area = Area(env)
    layout.place(
        area,
        position=Point(0, 0),
        rotation=0,
        footprint=Footprint(2, 2),
        ports={"visual": Port(Point(1, 0), Point(1, 0))},
    )
    if edit == "connector":
        layout.set_grid(Grid(1, 1))
        layout.set_network(Lattice((3, 0, 4, 0)))
    if edit == "position":
        result = layout.prepare()
        assert result.values.data["placements"][area.id]["ports"]["visual"]["position"] == (2, 0, 0)
        assert any(item.code == "generated-id-presentation" for item in result.diagnostics)
    else:
        with pytest.raises(LayoutError, match="explicitly named"):
            layout.prepare()


def test_d30_visual_port_coordinates_become_physical_when_binder_reads_them():
    from simulatte.layout import Port

    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {
                "layout_test_area-0": {"kind": "layout_test_area", "ports": {"visual": {"position": [2, 0, 0]}}}
            },
        },
    )
    area = Area(env)
    layout.place(area, ports={"visual": Port(Point(1, 0), Point(1, 0))})
    read = layout.ref("placements", area.id, "ports", "visual", "position")
    layout.on_bind("physical-port", lambda ctx: ctx.physical(read), reads=(read,))
    with pytest.raises(LayoutError, match="explicitly named"):
        layout.prepare()


def test_d30_selection_comparison_only_resolves_the_affected_target(monkeypatch):
    import simulatte.layout.layout as facade

    env = Environment()
    patches = {f"layout_test_area-{i}": {"kind": "layout_test_area", "visual": {"color": "blue"}} for i in range(20)}
    layout = Layout.from_dict(env, {"schema_version": "1.0", "placements": patches})
    for _ in patches:
        layout.place(Area(env), footprint=Footprint(1, 1))
    resolve = facade.resolve_layers
    finalizer_sizes = []

    def measured(code, file, *args, **kwargs):
        if layout._coordinator.closure.sealed:
            finalizer_sizes.append((len(kwargs["targets"]), sum(len(patch.get("placements", {})) for _, patch in code)))
        return resolve(code, file, *args, **kwargs)

    monkeypatch.setattr(facade, "resolve_layers", measured)
    result = layout.prepare()
    assert len(result.values.targets) == 20
    assert finalizer_sizes == [(1, 1)] * 20
