from __future__ import annotations

import pytest
from typing import Any, ClassVar

from simulatte.entities import Entity, StateSchema
from simulatte.environment import Environment
from simulatte.server import Server
from simulatte.layout.errors import LayoutError
from simulatte.layout.layers import FileLayer
from simulatte.layout.lifecycle import PreparationCoordinator, PreparationState
from simulatte.layout.stages import StageSpec, ordered_stages


class StageHall(Entity, kind="area"):
    state_schema: ClassVar[StateSchema] = StateSchema({})


def hall(width=12):
    return {
        "schema_version": "1.0",
        "placements": {"hall": {"kind": "area", "footprint": {"width": width, "depth": 3}}},
    }


@pytest.mark.parametrize("width,count", [(12, 4), (6, 2)])
def test_stages_use_file_width_and_preserve_orphans(width, count):
    file = hall(width)
    file["placements"].update({f"machine-{i:02}": {"kind": "server", "rotation": 5} for i in range(4)})
    env = Environment()
    env.entities.attach(StageHall(), name="hall")
    engine = PreparationCoordinator(env, file_layer=FileLayer(file))
    engine.attach_target("hall", "area")
    engine.declare(hall())
    width_ref = engine.ref("placements", "hall", "footprint", "width")
    called = []

    @engine.stage("machines", reads=[width_ref])
    def machines(ctx):
        assert ctx.env is engine.env and ctx.layout is engine
        for i in range(int(ctx.physical(width_ref) / 3)):
            name = f"machine-{i:02}"
            machine = Server(env=ctx.env, capacity=1, name=name)
            engine.attach_target(machine.id, machine.kind)
            engine.declare({"placements": {name: {"kind": "server", "position": [i * 3, 0, 0]}}})
        called.append("machines")

    result = engine.prepare()
    assert engine.prepare() is result
    assert engine.state is PreparationState.FINALIZED
    assert called == ["machines"]
    assert [entity.id for entity in env.entities.live()] == ["hall", *(f"machine-{i:02}" for i in range(count))]
    assert env.now == 0
    assert len(result.data["placements"]) == count + 1
    assert sum(d.code == "orphan-placement" for d in result.diagnostics) == 4 - count


@pytest.mark.parametrize("after", [((), (), ()), (("c",), (), ()), (("declare",), ("a",), ("a",))])
def test_stable_order(after):
    stages = [StageSpec(name, lambda ctx: None, (), deps) for name, deps in zip("abc", after)]
    expected = ["b", "c", "a"] if after[0] == ("c",) else list("abc")
    assert [s.name for s in ordered_stages(stages)] == expected


@pytest.mark.parametrize(
    "names,deps", [(["a", "a"], [(), ()]), (["a"], [("missing",)]), (["a", "b"], [("b",), ("a",)])]
)
def test_invalid_topology_before_callbacks(names, deps):
    with pytest.raises(LayoutError):
        ordered_stages([StageSpec(n, lambda ctx: pytest.fail("callback"), (), d) for n, d in zip(names, deps)])


def test_whole_target_freeze_includes_absence():
    engine = PreparationCoordinator(object())
    engine.attach_target("hall", "area")
    engine.declare(hall())
    rotation = engine.ref("placements", "hall", "rotation")

    @engine.stage("read", reads=[rotation])
    def read(ctx):
        assert ctx.physical(rotation, default=0) == 0
        engine.declare({"placements": {"hall": {"kind": "area", "rotation": 0}}})

    with pytest.raises(LayoutError, match="frozen"):
        engine.prepare()
    assert engine.state is PreparationState.FAILED
    with pytest.raises(LayoutError, match="failed"):
        engine.prepare()


def test_barrier_once_and_bind_dependency():
    calls = []
    graph = object()
    engine = PreparationCoordinator(object(), network_builder=lambda values, closure: calls.append("graph") or graph)
    engine.declare({"network": {"type": "explicit", "nodes": [], "arcs": []}})
    graph_ref = engine.graph()

    @engine.stage("consumer", reads=[graph_ref])
    def consumer(ctx):
        assert ctx.graph(graph_ref) is graph
        calls.append("stage")
        engine.attach_target("warehouse", "warehouse")
        engine.declare({"placements": {"warehouse": {"kind": "warehouse", "visual": {"label": "new"}}}})

    label = engine.ref("placements", "warehouse", "visual", "label")
    engine.on_bind("custom", lambda ctx: calls.append(ctx.physical(label)), reads=[label])
    engine.prepare()
    assert calls == ["graph", "stage", "new"]
    assert engine.physical(label) == "new"
    with pytest.raises(LayoutError, match="covered"):
        engine.physical(engine.ref("placements", "warehouse", "position"), default=None)


@pytest.mark.parametrize("action", ["undeclared", "self", "future", "parent_self", "register", "reenter"])
def test_stage_access_and_lifecycle_failures_latch(action):
    engine = PreparationCoordinator(object())
    engine.attach_target("hall", "area")
    engine.declare(hall())
    width = engine.ref("placements", "hall", "footprint", "width")
    own = engine.ref("placements", "new", "rotation")
    saved = []
    reads = [own] if action == "future" else ([engine.ref("placements")] if action == "parent_self" else [])

    @engine.stage("bad", reads=reads)
    def bad(ctx):
        saved.append(ctx)
        if action == "register":
            engine.stage("later", reads=[])(lambda ctx: None)
        elif action == "reenter":
            # Catching reentry does not turn a failed attempt into successful preparation.
            with pytest.raises(LayoutError, match="reentrant"):
                engine.prepare()
            with pytest.raises(LayoutError, match="no longer active"):
                ctx.physical(width)
        elif action in ("self", "parent_self"):
            engine.attach_target("new", "server")
            ctx.physical(own, default=0)
        else:
            ctx.physical(width)

    with pytest.raises(LayoutError):
        engine.prepare()
    assert engine.active_context is None and engine.state is PreparationState.FAILED
    for ctx in saved:
        with pytest.raises(LayoutError, match="no longer active"):
            ctx.physical(width)


@pytest.mark.parametrize(
    "field,patch",
    [
        ("grid", {"dx": 1, "dy": 1}),
        ("network", {"type": "explicit", "nodes": [], "arcs": []}),
    ],
)
@pytest.mark.parametrize("previous_read", [False, True])
def test_later_global_declaration_allowed_only_if_absent_unread(field, patch, previous_read):
    engine = PreparationCoordinator(object())
    if previous_read:

        @engine.stage("read", reads=[engine.ref(field)])
        def read(ctx):
            assert ctx.physical(engine.ref(field), default=None) is None

    @engine.stage("add", reads=[])
    def add(ctx):
        engine.declare({field: patch})

    if previous_read:
        with pytest.raises(LayoutError, match="frozen"):
            engine.prepare()
    else:
        result = engine.prepare()
        assert result.data[field]
        assert any(q.descriptor["path"] == (field,) for q in engine.closure.queries)


@pytest.mark.parametrize("late", ["obstacle", "connector", "network", "grid", "overrides", "file_obstacle"])
def test_graph_producing_edits_after_barrier_rejected(late):
    file = {"schema_version": "1.0"}
    if late == "file_obstacle":
        file["placements"] = {"late": {"kind": "warehouse", "obstacle": True}}
    engine = PreparationCoordinator(object(), file_layer=FileLayer(file), network_builder=lambda v, c: object())

    @engine.stage("late", reads=[engine.graph()])
    def add(ctx):
        if late in ("obstacle", "file_obstacle", "connector"):
            engine.attach_target("late", "warehouse")
            patch = (
                {"kind": "warehouse", "obstacle": True}
                if late == "obstacle"
                else {
                    "kind": "warehouse",
                    "ports": {"p": {"position": [0, 0, 0], "approach": [1, 0, 0], "connector": {}}},
                }
            )
            engine.declare({"placements": {"late": patch}})
        else:
            engine.declare(
                {
                    late: {
                        "grid": {"dx": 1, "dy": 1},
                        "network": {"type": "explicit", "nodes": [], "arcs": []},
                        "overrides": [],
                    }[late]
                }
            )

    with pytest.raises(LayoutError, match="late-network-edit"):
        engine.prepare()


def test_network_selection_extends_negative_candidates_but_model_collection_does_not():
    def build(values, closure):
        closure.select(values, ("placements",), "obstacle")
        closure.select(values, ("placements",), "connector")
        return object()

    engine = PreparationCoordinator(object(), network_builder=build)

    @engine.stage("late", reads=[engine.graph()])
    def late(ctx):
        engine.attach_target("late", "warehouse")
        engine.declare({"placements": {"late": {"kind": "warehouse", "visual": {"label": "ok"}}}})

    result = engine.prepare()
    assert not engine.closure.changed(result)
    patch = FileLayer({"schema_version": "1.0", "placements": {"late": {"kind": "warehouse", "obstacle": True}}})
    assert ("placements",) in engine.closure.reevaluate(result.bundle.code_stages, patch)

    other = PreparationCoordinator(object())

    @other.stage("read", reads=[other.ref("placements")])
    def read(ctx):
        assert ctx.select(other.ref("placements"), "obstacle") == ()

    @other.stage("add", reads=[])
    def add(ctx):
        other.attach_target("new", "warehouse")
        other.declare({"placements": {"new": {"kind": "warehouse"}}})

    with pytest.raises(LayoutError, match="frozen"):
        other.prepare()


def test_single_target_read_allows_unrelated_target_and_granted_parent_covers_children():
    engine = PreparationCoordinator(object())
    engine.attach_target("hall", "area")
    engine.declare(hall())
    parent = engine.ref("placements", "hall", "footprint")

    @engine.stage("read", reads=[parent])
    def read(ctx):
        assert engine.physical(engine.ref(*parent.path, "width")) == 12
        engine.attach_target("new", "server")

    engine.prepare()
    assert engine.physical(engine.ref(*parent.path, "depth")) == 3


def test_unused_network_built_and_hooks_order_owner_node_binding():
    from simulatte.layout.refs import Address, NodeRef

    owner = object()
    calls = []
    node = object()
    ref = NodeRef(owner, Address({"kind": "explicit", "id": "n"}))
    result = object()

    def build(values, closure):
        calls.append("build")
        return {"graph": "graph"}

    def validate(engine):
        calls.append("validate")
        assert engine.state is PreparationState.VALIDATING
        return engine.values

    def bind(engine):
        calls.append("builtin-bind")
        engine.closure.bind("builtin", {"node": "n"})

    def finalize(engine):
        calls.append("finalize")
        assert engine.closure.sealed
        return result

    engine = PreparationCoordinator(
        object(),
        owner=owner,
        host="host",
        network_builder=build,
        graph_of=lambda network: network["graph"],
        node_resolver=lambda ref: node,
        node_identity=lambda node: {"id": "n", "position": [0, 0, 0]},
        validate=validate,
        bind=bind,
        finalize=finalize,
    )
    engine.declare({"network": {"type": "explicit", "nodes": [{"id": "n", "position": [0, 0, 0]}], "arcs": []}})

    def custom(ctx):
        assert ctx.layout == "host"
        assert ctx.node(ref) is node
        assert ctx.graph(engine.graph()) == "graph"
        calls.append("custom")

    engine.on_bind("custom", custom, reads=[engine.graph()])
    assert engine.prepare() is result and engine.prepare() is result
    assert calls == ["build", "validate", "builtin-bind", "custom", "finalize"]
    assert engine.stage_records() == {
        "stages": [],
        "binders": [{"name": "custom", "reads": [{"kind": "graph"}], "after": []}],
    }
    assert "callback" not in engine.stage_records()["binders"][0]
    assert len(engine.closure.to_dict()["bindings"]) == 2


@pytest.mark.parametrize("hook", ["network_builder", "validate", "bind", "finalize"])
def test_hook_failure_and_context_cleanup(hook):
    def fail(*args):
        raise RuntimeError("failed hook")

    hooks: dict[str, Any] = {hook: fail}
    engine = PreparationCoordinator(object(), **hooks)
    with pytest.raises(RuntimeError, match="failed hook"):
        engine.prepare()
    assert engine.state is PreparationState.FAILED and engine.active_context is None
    with pytest.raises(LayoutError, match="failed"):
        engine.prepare()


def test_declaration_and_finalization_guards_and_foreign_refs():
    engine = PreparationCoordinator(object())
    with pytest.raises(LayoutError, match="unavailable"):
        engine.physical(engine.ref("grid"))
    with pytest.raises(LayoutError, match="foreign"):
        engine.physical(PreparationCoordinator(object()).ref("grid"))
    engine.prepare()
    with pytest.raises(LayoutError, match="prohibited"):
        engine.attach_target("x", "server")
    with pytest.raises(LayoutError, match="prohibited"):
        engine.declare({})
    with pytest.raises(LayoutError, match="prohibited"):
        engine.on_bind("x", lambda ctx: None)


def test_prior_parent_read_survives_later_auto_fill_and_final_target_revalidation():
    from simulatte.layout.layers import resolve_layers

    def validate(engine):
        return resolve_layers(
            engine.values.bundle.code_stages,
            engine.file_layer,
            {"schema_version": "1.0", "placements": {"hall": {"kind": "area", "rotation": 0}}},
            targets=engine.values.targets,
            final=True,
        )

    engine = PreparationCoordinator(object(), validate=validate)
    engine.attach_target("hall", "area")
    engine.declare(hall())

    @engine.stage("parent", reads=[engine.ref("placements", "hall")])
    def parent(ctx):
        assert "rotation" not in ctx.physical(engine.ref("placements", "hall"))

    engine.prepare()
    assert engine.physical(engine.ref("placements", "hall", "rotation"), default=4) == 4
    assert not engine.closure.changed(engine.values)

    orphan = PreparationCoordinator(object())
    orphan.declare(hall())
    orphan.stage("read", reads=[orphan.ref("placements", "hall", "footprint")])(lambda ctx: None)
    with pytest.raises(LayoutError, match="frozen"):
        orphan.prepare()


def test_unused_bind_parent_read_rejects_auto_descendants():
    from simulatte.layout.layers import resolve_layers

    def validate(engine):
        return resolve_layers(
            engine.values.bundle.code_stages,
            engine.file_layer,
            {"schema_version": "1.0", "placements": {"hall": {"kind": "area", "rotation": 0}}},
            targets=engine.values.targets,
            final=True,
        )

    engine = PreparationCoordinator(object(), validate=validate)
    engine.attach_target("hall", "area")
    engine.declare(hall())
    engine.on_bind(
        "parent", lambda ctx: pytest.fail("must validate grants first"), reads=[engine.ref("placements", "hall")]
    )
    with pytest.raises(LayoutError, match="auto"):
        engine.prepare()


@pytest.mark.parametrize("hook", ["network_builder", "validate", "bind", "finalize"])
def test_reentry_cannot_be_swallowed_by_any_hook(hook):
    calls = []

    def attempt(*args):
        with pytest.raises(LayoutError, match="reentrant"):
            engine.prepare()
        calls.append("swallowed")

    hooks: dict[str, Any] = {hook: attempt}
    engine = PreparationCoordinator(object(), **hooks)
    with pytest.raises(LayoutError, match="failed"):
        engine.prepare()
    assert engine.state is PreparationState.FAILED and calls == ["swallowed"]


@pytest.mark.parametrize(
    "read_kind", ["foreign", "graph_undeclared", "node_undeclared", "node_no_resolver", "no_network"]
)
def test_binding_reference_validation(read_kind):
    from simulatte.layout.refs import Address, NodeRef

    engine = PreparationCoordinator(
        object(), network_builder=lambda v, c: None if read_kind == "no_network" else object()
    )
    ref = NodeRef(engine.owner, Address({"kind": "explicit", "id": "a"}))
    reads = [engine.graph()] if read_kind in ("node_no_resolver", "no_network") else []

    def bind(ctx):
        if read_kind == "foreign":
            ctx.graph(PreparationCoordinator(object()).graph())
        elif read_kind in ("graph_undeclared", "no_network"):
            ctx.graph(engine.graph())
        else:
            ctx.node(ref)

    engine.on_bind("bad", bind, reads=reads)
    with pytest.raises(LayoutError):
        engine.prepare()
    assert engine.state is PreparationState.FAILED


def test_registration_validation_is_early_and_duplicate_attachment_rejected():
    engine = PreparationCoordinator(object())
    with pytest.raises(LayoutError, match="stage name"):
        engine.stage("declare", reads=[])(lambda ctx: None)
    engine.stage("one", reads=[])(lambda ctx: None)
    with pytest.raises(LayoutError, match="duplicate stage"):
        engine.stage("one", reads=[])(lambda ctx: None)
    engine.on_bind("one", lambda ctx: None)
    with pytest.raises(LayoutError, match="duplicate binder"):
        engine.on_bind("one", lambda ctx: None)
    engine.attach_target("one", "server")
    with pytest.raises(LayoutError, match="already attached"):
        engine.attach_target("one", "server")
    other = PreparationCoordinator(object())
    other.stage("one", reads=[engine.ref("grid")])(lambda ctx: pytest.fail("foreign grant must fail before callback"))
    with pytest.raises(LayoutError, match="foreign"):
        other.prepare()


def test_target_provider_is_authoritative_and_unused_grants_are_physical():
    engine = PreparationCoordinator(object(), target_provider=lambda: {})
    engine.attach_target("hall", "area")
    engine.declare(hall())
    engine.stage("one", reads=[engine.ref("placements", "hall", "footprint", "width")])(lambda ctx: None)
    with pytest.raises(LayoutError, match="frozen"):
        engine.prepare()


def test_whole_placement_collection_prevents_unplaced_target_additions():
    engine = PreparationCoordinator(object())

    @engine.stage("read", reads=[engine.ref("placements")])
    def read(ctx):
        ctx.physical(engine.ref("placements"), default={})

    @engine.stage("add", reads=[])
    def add(ctx):
        engine.attach_target("new", "server")

    with pytest.raises(LayoutError, match="frozen"):
        engine.prepare()


def test_stage_grants_reject_other_reference_types():
    from simulatte.layout.refs import Address, NodeRef

    with pytest.raises(LayoutError, match="ValueRef or GraphRef"):
        invalid_reads: Any = (NodeRef(object(), Address({"kind": "explicit", "id": "a"})),)
        StageSpec("bad", lambda ctx: None, invalid_reads)
