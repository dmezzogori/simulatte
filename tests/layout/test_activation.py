import sys
import threading

from simulatte import Environment, Layout, TraceRecorder
from simulatte.layout import LayoutError
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.layout import Footprint, Grid, Lattice, Point
from simulatte.layout import bundle as bundle_module
from simulatte.layout import identity, lifecycle
from simulatte.layout import layout as facade_module
from simulatte.layout.lifecycle import PreparationCoordinator
from simulatte.builders import build_immediate_release_system
from simulatte.job import ProductionJob
from simulatte.scenario import Scenario
from simulatte.server import Server
from tests.layout.test_binding import SKU_A, line, system, vehicle
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
    assert empty._layout is None


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
    from simulatte.intralogistics.reservation import ReservationController

    env = Environment()
    layout = Layout(env)
    ReservationController(env=env, graph=layout.graph())
    with pytest.raises(LayoutError, match="declared network"):
        layout.prepare()


def test_port_binding_aliases_share_canonical_graph_node_and_capacity():
    from simulatte.layout import Connector, Port
    from simulatte.intralogistics.reservation import ReservationController

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
    manager = ReservationController(env=env, graph=layout.graph())
    result = layout.prepare()
    assert cart.current_node is result.network.node(ref.address)
    assert cart.current_node in manager.graph.nodes
    assert result.auto.derived_positions[cart.id] == Point(-1, 0)


def test_rejected_constructors_leave_the_layout_usable():
    from simulatte.intralogistics.reservation import ReservationController

    env = Environment()
    with pytest.raises(ValueError, match="name"):
        vehicle(env, env.layout.node("A"), "")
    with pytest.raises(LayoutError):
        ReservationController(env=env, graph="not a graph")  # ty: ignore[invalid-argument-type]
    assert env.layout.prepare() is not None and env.entities.live() == ()


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


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("predicate", ["obstacle", "port_unset", "connector_unset"])
def test_d30_identical_file_predicate_restatements_still_require_names(named, predicate):
    patch: dict[str, Any] = {"obstacle": False}
    if predicate == "port_unset":
        patch = {"ports": {"visual": {"$unset": True}}}
    elif predicate == "connector_unset":
        patch = {"ports": {"visual": {"connector": {"$unset": True}}}}
    env = Environment()
    target = "hall" if named else "layout_test_area-0"
    layout = Layout.from_dict(
        env, {"schema_version": "1.0", "placements": {target: {"kind": "layout_test_area", **patch}}}
    )
    area = Area(env, "hall" if named else None)
    layout.place(area, **patch)
    if named:
        assert layout.prepare().values.targets[target] == "layout_test_area"
    else:
        with pytest.raises(LayoutError, match="explicitly named"):
            layout.prepare()


# --- cost of a layout (spec §9) ---------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["timeouts", "shop"])
def test_step_after_activation_reads_nothing_of_the_layout(monkeypatch, model):
    if model == "timeouts":
        env = Environment()
        layout = line(env)
        vehicle(env, layout.node("A"), "cart")
        for delay in range(1, 6):
            env.timeout(delay)
        steps = 5
    else:  # jobs arrive and are created during the steps (spec §5.5: they cost nothing)
        env = Environment(seed=1)
        layout = Layout(env)
        build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=4))
        steps = 2_000
    env.activate()
    jobs = env.entities._counters.get("job", 0)
    reads = []

    class Counted:
        """Count the reads of the coordinator's state; data descriptors take precedence over the instance field."""

        def __get__(self, coordinator, owner):
            reads.append(coordinator)
            return coordinator.__dict__["state"]

        def __set__(self, coordinator, value):
            coordinator.__dict__["state"] = value

    monkeypatch.setattr(PreparationCoordinator, "state", Counted(), raising=False)
    layout_modules = [
        name for name in sys.modules if name == "simulatte.layout" or name.startswith("simulatte.layout.")
    ]
    for name in layout_modules:
        monkeypatch.delitem(sys.modules, name)  # an import during the steps would load them again
    calls = []

    def profile(frame, event, arg):
        if event == "call" and "/simulatte/layout/" in frame.f_code.co_filename.replace("\\", "/"):
            calls.append(frame.f_code.co_name)

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        for _ in range(steps):
            env.step()
    finally:
        sys.setprofile(previous)
    if model == "timeouts":
        assert env.now == 5
    else:
        assert env.entities._counters["job"] > jobs + 10  # jobs were created during the steps
    assert calls == [] and reads == []
    assert not [name for name in layout_modules if name in sys.modules]
    assert layout._coordinator.state == "finalized" and reads == [layout._coordinator]  # the counter counts


@pytest.mark.parametrize("layout_first", [True, False])
def test_attaching_entities_resolves_the_layers_a_number_of_times_that_does_not_grow(monkeypatch, layout_first):
    calls = []
    for module in (lifecycle, facade_module):
        resolve = module.resolve_layers

        def counted(*args, resolve=resolve, **kwargs):
            calls.append(1)
            return resolve(*args, **kwargs)

        monkeypatch.setattr(module, "resolve_layers", counted)

    def counts(n):
        calls.clear()
        env = Environment()
        layout = Layout(env) if layout_first else None
        servers = [Server(env=env, capacity=1) for _ in range(n)]
        for server in servers:
            ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
        if layout is None:
            layout = Layout(env)
        attached = len(calls)
        result = layout.prepare()
        assert len(result.values.targets) == 2 * n
        return attached, len(calls) - attached

    assert counts(400) == counts(40)


@pytest.mark.unchecked_bundles
def test_an_unobserved_run_builds_no_bundle_and_a_read_builds_it_once(monkeypatch, tmp_path):
    built = []
    hashed = []
    build, hashes = bundle_module._build_bundle, identity.hashes

    def counted_build(resolved):
        built.append(resolved)
        return build(resolved)

    def counted_hashes(data):
        hashed.append(1)
        return hashes(data)

    monkeypatch.setattr(bundle_module, "_build_bundle", counted_build)
    monkeypatch.setattr(bundle_module, "hashes", counted_hashes)

    def model(env):
        layout = line(env)
        fleet, _, origin, destination, *_ = system(env, layout)
        fleet.submit(fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination))
        return layout

    env = Environment()
    layout = model(env)
    env.run(until=50)
    result = layout.prepare()
    assert built == [] and hashed == []
    manifest = env.manifest().requested["layout"]
    assert len(built) == 1 and built[0] is result and hashed == [1]
    assert manifest == result.bundle.manifest() and result.full_hash == result.bundle.full_hash
    assert len(built) == 1 and hashed == [1]

    recorded = Environment()
    TraceRecorder(recorded, tmp_path / "run.trace")
    model(recorded)
    built.clear()
    recorded.run(until=50)
    recorded.close()
    assert len(built) == 1 and built[0] is recorded.layout.prepare()


def test_a_bundle_read_late_describes_the_layout_as_prepared():
    def model():
        env = Environment(seed=3)
        layout = line(env)
        fleet, _, origin, destination, *_ = system(env, layout)
        for _ in range(3):
            fleet.submit(fleet.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination))
        Server(env=env, capacity=1, name="machine")
        return env, layout

    _, early_layout = model()
    early = early_layout.prepare().bundle.to_dict()
    late_env, late_layout = model()
    late_env.run()  # AGVs move, orders are created and finish, entities are created during the run
    late_layout.prepare().network  # the result is read, the bundle is not
    assert late_env.now > 0
    assert late_layout.prepare().bundle.to_dict() == early


def test_concurrent_first_reads_get_the_same_bundle():
    result = line(Environment()).prepare()
    barrier = threading.Barrier(4)
    seen = []

    def read():
        barrier.wait()
        seen.append(result.bundle)

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == 4 and all(bundle is result.bundle for bundle in seen)


@pytest.mark.unchecked_bundles
def test_model_errors_still_raise_at_prepare(monkeypatch):
    monkeypatch.setattr(bundle_module, "_build_bundle", lambda resolved: pytest.fail("bundle built"))
    env = Environment()
    layout = line(env)
    vehicle(env, layout.node("absent"), "cart")
    with pytest.raises(LayoutError, match="missing or disabled required node") as caught:
        layout.prepare()
    assert caught.value.diagnostics[0].code == "missing-handle"

    env = Environment()
    layout = Layout.from_dict(
        env,
        {
            "schema_version": "1.0",
            "placements": {"layout_test_area-0": {"kind": "layout_test_area", "obstacle": False}},
        },
    )
    layout.place(Area(env), obstacle=False)  # a physical file override of an auto-named entity
    with pytest.raises(LayoutError, match="explicitly named"):
        layout.prepare()

    env = Environment()
    layout = Layout(env)
    layout.place("ghost", kind="bogus", position=Point(0, 0))
    result = layout.prepare()  # an unknown kind is a diagnostic, available without the bundle
    assert [item.code for item in result.diagnostics] == ["orphan-placement"]


@pytest.mark.unchecked_bundles
@pytest.mark.parametrize("name", [5, ("a", "b"), "s\ud800", "a\0b"])
@pytest.mark.parametrize("kind", ["stage", "binder", "after"])
def test_an_invalid_stage_or_binder_name_raises_where_it_is_declared(monkeypatch, kind, name):
    """The names are saved in the bundle, which is built on first read: they are checked when declared (spec §9)."""
    monkeypatch.setattr(bundle_module, "_build_bundle", lambda resolved: pytest.fail("bundle built"))
    layout = Layout(Environment())
    with pytest.raises(LayoutError, match="invalid stage name"):
        if kind == "stage":
            layout.stage(name, reads=())(lambda ctx: None)
        elif kind == "binder":
            layout.on_bind(name, lambda ctx: None)
        else:
            layout.stage("valid", reads=(), after=(name,))(lambda ctx: None)
    layout.prepare()


@pytest.mark.parametrize(
    ("kind", "size", "views"),
    [("explicit", 4, 1), ("explicit", 40, 1), ("lattice", 4, 2), ("lattice", 40, 2), ("none", 4, 2), ("none", 40, 2)],
)
def test_preparing_checks_no_declared_patch_again_and_resolves_the_network_view_as_often_as_it_must(
    monkeypatch, kind, size, views
):
    """A patch is checked against the schema when it is declared (spec §9: validation of the declared inputs stays at
    declaration and preparation); the resolutions of a preparation take the checked patches as they are. The network
    view, which preparation asks for twice, is resolved once when the second ask has the layers and targets of the
    first (an explicit network) and twice otherwise: for a lattice the targets of the second ask include the nodes the
    first ask built, and a model without a network keeps nothing (`views` is how many times the view is resolved)."""
    from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
    from simulatte.layout import Explicit
    from simulatte.layout import layers as layers_module

    checks, resolutions = [], []
    parse = layers_module.parse_patch
    monkeypatch.setattr(layers_module, "parse_patch", lambda data, **k: checks.append(data) or parse(data, **k))
    for module in (lifecycle, facade_module):
        resolve = module.resolve_layers

        def counted(*args, resolve=resolve, **kwargs):
            resolutions.append((kwargs.get("final", False), len(args) > 2 and args[2] is not None))
            return resolve(*args, **kwargs)

        monkeypatch.setattr(module, "resolve_layers", counted)

    env = Environment()
    if kind == "explicit":
        path = [Node(f"n{i}", float(i), 0.0) for i in range(size)]
        env.layout.set_network(Explicit(LayoutGraph(path, [Arc(a, b) for a, b in zip(path, path[1:])])))
    elif kind == "lattice":
        env.layout.set_grid(Grid(1, 1))
        env.layout.set_network(Lattice((0, 0, size - 1, 0)))
    else:
        for _ in range(size):
            Server(env=env, capacity=1)
    assert bool([data for data in checks if "network" in data]) == (kind != "none")  # declaring checked its patch
    checks.clear()
    resolutions.clear()
    result = env.layout.prepare()
    assert not [data for data in checks if "network" in data]  # preparing checked it no more
    assert len(result.network.graph.nodes) == (0 if kind == "none" else size)
    # the layers with the final targets, the network view (once or twice) and the one with the auto layer
    assert resolutions.count((True, False)) == 1 + views
    assert resolutions.count((True, True)) == 1
