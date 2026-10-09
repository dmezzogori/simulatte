"""Entity identity, state schemas, the entity registry and lifecycle events."""

from __future__ import annotations

import gc
import platform
import weakref
from typing import Any, ClassVar

import pytest

from simulatte._wire import FrozenMap, freeze, pack, unpack
from simulatte.entities import (
    KINDS,
    LABEL_FIELD,
    Entity,
    EntityCreated,
    EntityRegistry,
    EntityRetired,
    FieldSpec,
    StateSchema,
    presentation_of,
)
from simulatte.environment import Environment
from simulatte.events import CATALOG, Deltas, Event, apply_deltas
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class Widget(Entity, kind="test_widget"):
    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "size": FieldSpec("float"),
            "parts": FieldSpec("str", collection="list"),
            "owner": FieldSpec("str", nullable=True),
        }
    )

    def __init__(self, env: Environment, *, name: str | None = None, label: str | None = None) -> None:
        self.size = 1.0
        self.parts = ["a", "b"]
        self.owner = None
        env.entities.attach(self, name=name, label=label)


@pytest.fixture
def env_debug() -> Environment:
    return Environment(debug=True)


def _record(env: Environment, types: Any = "**") -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, types)
    return seen


def _job(env: Environment, servers: list[Server], due_date: float = 10) -> ProductionJob:
    return ProductionJob(env=env, sku="A", servers=servers, processing_times=[2] * len(servers), due_date=due_date)


def _router(env: Environment, sf: ShopFloor, servers: list[Server], **kwargs: Any) -> Router:
    return Router(
        env=env,
        shopfloor=sf,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: 1.0,
        sku_distributions={"A": 1.0},
        sku_routings={"A": lambda: servers},
        sku_service_times={"A": dict.fromkeys(servers, lambda: 1.0)},
        due_date_offset_distribution={"A": lambda: 5.0},
        **kwargs,
    )


# --- ids -----------------------------------------------------------------------------------------------


def test_production_components_have_ids() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    lathe = Server(env=env, capacity=1, shopfloor=sf, name="lathe")
    unnamed = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = _router(env, sf, [lathe, unnamed])
    jobs = [_job(env, [lathe]), _job(env, [unnamed])]

    assert (sf.id, lathe.id, unnamed.id, psp.id, router.id) == ("shopfloor-0", "lathe", "server-0", "psp-0", "router-0")
    assert [job.id for job in jobs] == ["job-0", "job-1"]
    assert sf.servers == [lathe, unnamed]
    assert repr(lathe) == "Server(id='lathe')" and repr(jobs[0]) == "ProductionJob(id='job-0', sku='A')"
    assert env.entities.live() == (sf, lathe, unnamed, psp, router, *jobs)
    assert env.entities.get("lathe") is lathe and env.entities.get("job-1") is jobs[1]
    with pytest.raises(KeyError):
        env.entities.get("server-9")

    other = Environment()
    other_server = Server(env=other, capacity=1)
    assert other_server.id == "server-0" and _job(other, [other_server]).id == "job-0"


def test_generated_ids_per_kind() -> None:
    env = Environment()
    s0 = Server(env=env, capacity=1)
    j0 = _job(env, [s0])
    w0 = Widget(env)
    s1 = Server(env=env, capacity=1)
    j1 = _job(env, [s1])
    w1 = Widget(env, label="Second widget")
    assert [e.id for e in (s0, j0, w0, s1, j1, w1)] == [
        "server-0",
        "job-0",
        "test_widget-0",
        "server-1",
        "job-1",
        "test_widget-1",
    ]
    assert (w0.label, w1.label) == ("test_widget-0", "Second widget")
    assert {"server", "job", "psp", "shopfloor", "router", "test_widget"} <= set(KINDS)


def test_name_becomes_id_and_duplicates_raise() -> None:
    env = Environment()
    sf = ShopFloor(env=env, name="floor", label="Main floor")
    lathe = Server(env=env, capacity=1, shopfloor=sf, name="lathe", label="Lathe #1")
    assert (sf.id, sf.label, lathe.id, lathe.label) == ("floor", "Main floor", "lathe", "Lathe #1")
    psp = PreShopPool(env=env, shopfloor=sf, name="pool")
    router = _router(env, sf, [lathe], name="gen", label="Generator")
    assert (psp.id, psp.label, router.id, router.label) == ("pool", "pool", "gen", "Generator")

    with pytest.raises(ValueError, match="lathe"):
        Server(env=env, capacity=1, shopfloor=sf, name="lathe")
    assert sf.servers == [lathe]  # a rejected server is not registered on the shop floor
    with pytest.raises(ValueError, match="already"):
        PreShopPool(env=env, shopfloor=sf, name="floor")  # ids are unique across kinds
    with pytest.raises(ValueError, match="already has the id"):
        env.entities.attach(lathe)

    widget = Widget(env, name="gadget")
    env.entities.retire(widget)
    with pytest.raises(ValueError, match="gadget"):
        Widget(env, name="gadget")  # a retired id is never reused
    with pytest.raises(TypeError):
        Server(env=env, capacity=1, name=7)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="kind"):
        env.entities.attach(object())  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "name", ["server-0", "server-12", "job-3", "psp-0", "shopfloor-1", "router-0", "test_widget-4"]
)
def test_reserved_pattern_rejected(name: str) -> None:
    env = Environment()
    with pytest.raises(ValueError, match="reserved"):
        Server(env=env, capacity=1, name=name)
    for allowed in ("server-x", "server-", "my-server-0", "job_0", "Server-0"):
        Widget(env, name=allowed)


def test_late_kind_reserves_generated_ids_against_earlier_names() -> None:
    env = Environment()
    Server(env=env, capacity=1, name="test_late-0")

    class Late(Entity, kind="test_late"):
        state_schema: ClassVar[StateSchema] = StateSchema({})

    with pytest.raises(ValueError, match="test_late-0"):
        env.entities.attach(Late())


@pytest.mark.parametrize("name", ["a/b", "/", "a\0b", ""])
def test_slash_and_nul_rejected(name: str) -> None:
    env = Environment()
    with pytest.raises(ValueError):
        Server(env=env, capacity=1, name=name)
    with pytest.raises(ValueError):
        ShopFloor(env=env, name=name)


# --- lifecycle ------------------------------------------------------------------------------------------


def test_created_is_only_create_owner() -> None:
    env = Environment()
    assert not env.wants(EntityCreated)
    Server(env=env, capacity=1, name="unobserved")  # nothing listens: nothing is built

    seen = _record(env, "*")
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=2, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = _router(env, sf, [server])
    job = _job(env, [server], due_date=10)

    assert [type(e) for e in seen] == [EntityCreated] * 5
    for event, entity in zip(seen, (sf, server, psp, router, job), strict=True):
        assert isinstance(event, EntityCreated)
        assert (event.entity, event.kind, event.label) == (entity.id, entity.kind, entity.label)
        assert event.deltas.ops == (("create", entity.id, entity.kind, freeze(entity.snapshot())),)

    created_job: Any = seen[-1].deltas.ops[0][3]
    assert dict(created_job) == {
        "sku": "A",
        "routing": ("server-0",),
        "processing_times": (2.0,),
        "op_index": None,
        "location": None,
        "due_date": 10.0,
        "created_at": 0.0,
        "finished_at": None,
        "shopfloor": None,
        "label": "job-0",
    }
    assert all(type(created_job[f]) is float for f in ("due_date", "created_at"))
    assert dict(seen[1].deltas.ops[0][3]) == {
        "capacity": 2,
        "users": (),
        "queue": (),
        "worked_time": 0.0,
        "label": "server-0",
    }
    assert type(seen[1].deltas.ops[0][3]["worked_time"]) is float
    assert seen[2].deltas.ops[0][3]["shopfloor"] == "shopfloor-0"
    assert seen[3].deltas.ops[0][3]["shopfloor"] == "shopfloor-0"
    assert dict(seen[0].deltas.ops[0][3]) == {"wip": FrozenMap({}), "jobs_in_system": 0, "label": "shopfloor-0"}

    env.entities.retire(job)
    assert isinstance(seen[-1], EntityRetired)
    assert (seen[-1].entity, seen[-1].kind) == ("job-0", "job")
    assert seen[-1].deltas.ops == (("retire", "job-0"),)

    lifecycle = [op for e in seen for op in e.deltas.ops if op[0] in ("create", "retire")]
    assert [op[0] for op in lifecycle] == ["create"] * 5 + ["retire"]

    replayed: dict[str, dict[str, Any]] = {}
    for event in seen:
        apply_deltas(replayed, event.deltas)
    live = env.entities.snapshot()
    del live["unobserved"]
    assert replayed == live

    entry = CATALOG.get("entity.created")
    assert [(f.name, f.presentation) for f in entry.fields] == [("entity", False), ("kind", False), ("label", True)]
    assert [f.name for f in CATALOG.get("entity.retired").fields] == ["entity", "kind"]


def test_retire_drops_strong_reference() -> None:
    env = Environment()
    server = Server(env=env, capacity=1)
    job = _job(env, [server])
    ref = weakref.ref(job)

    env.entities.retire(job)
    assert job not in env.entities.live() and "job-0" not in env.entities.snapshot()
    assert env.entities.get("job-0") is job  # still reachable while user code holds it
    with pytest.raises(ValueError, match="not live"):
        env.entities.retire(job)

    del job
    gc.collect()
    assert ref() is None
    with pytest.raises(KeyError):
        env.entities.get("job-0")
    assert _job(env, [server]).id == "job-1"


def test_snapshot_excludes_presentation_when_asked() -> None:
    env = Environment()
    sf = ShopFloor(env=env, label="Floor")
    server = Server(env=env, capacity=1, shopfloor=sf, name="b-server")
    Widget(env, name="a-widget", label="A widget")
    sf.wip[server] = 3

    full: dict[str, Any] = env.entities.snapshot()
    assert list(full) == ["a-widget", "b-server", "shopfloor-0"]  # sorted by id
    assert full["a-widget"] == {
        "$kind": "test_widget",
        "size": 1.0,
        "parts": ("a", "b"),
        "owner": None,
        "label": "A widget",
    }
    assert full["shopfloor-0"] == {
        "$kind": "shopfloor",
        "wip": {"b-server": 3.0},
        "jobs_in_system": 0,
        "label": "Floor",
    }
    assert type(full["shopfloor-0"]["wip"]["b-server"]) is float

    semantic = env.entities.snapshot(include_presentation=False)
    assert semantic == {k: {f: v for f, v in fields.items() if f != "label"} for k, fields in full.items()}
    assert all("label" not in fields for fields in semantic.values())


# --- schemas and kinds ----------------------------------------------------------------------------------


def test_state_schema_and_field_spec_validation() -> None:
    schema = StateSchema({"a": FieldSpec("int"), "b": FieldSpec("float", collection="map", presentation=True)})
    assert list(schema) == ["a", "b"] and len(schema) == 2 and schema["a"] == FieldSpec("int")
    assert schema.presentation == frozenset({"b"})
    assert schema.presentation is schema.presentation  # computed once
    assert presentation_of("test_widget") == frozenset({"label"}) and presentation_of("no_such_kind") == frozenset()
    assert "StateSchema" in repr(schema)
    assert Widget.state_schema["label"] == FieldSpec("str", presentation=True)  # label is added to every kind

    with pytest.raises(ValueError, match=r"\$"):
        StateSchema({"$kind": FieldSpec("str")})
    with pytest.raises(ValueError):
        StateSchema({"": FieldSpec("str")})
    with pytest.raises(TypeError):
        StateSchema({"a": "int"})  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="wire type"):
        FieldSpec("decimal")
    with pytest.raises(ValueError, match="collection"):
        FieldSpec("int", collection="set")  # ty: ignore[invalid-argument-type]


def test_kind_registration_rules() -> None:
    schema = StateSchema({"x": FieldSpec("int")})

    class Same(Entity, kind="test_same"):
        state_schema: ClassVar[StateSchema] = schema

    class SameAgain(Entity, kind="test_same"):  # identical schema: allowed
        state_schema: ClassVar[StateSchema] = schema

    assert SameAgain.kind == Same.kind == "test_same"
    with pytest.raises(ValueError, match="different"):

        class Different(Entity, kind="test_same"):
            state_schema: ClassVar[StateSchema] = StateSchema({"y": FieldSpec("int")})

    with pytest.raises(TypeError, match="state_schema"):

        class Missing(Entity, kind="test_missing"):
            pass

    class ExplicitLabel(Entity, kind="test_explicit_label"):
        state_schema: ClassVar[StateSchema] = StateSchema({"label": LABEL_FIELD})

    assert list(ExplicitLabel.state_schema) == ["label"]
    with pytest.raises(ValueError, match="label"):

        class BadLabel(Entity, kind="test_bad_label"):
            state_schema: ClassVar[StateSchema] = StateSchema({"label": FieldSpec("int")})

    for bad in ("Bad", "a/b", "", "with-dash"):
        with pytest.raises(ValueError, match="kind"):

            class BadKind(Entity, kind=bad):
                state_schema: ClassVar[StateSchema] = schema

    class Sub(Same):  # subclasses inherit the kind
        pass

    env = Environment()
    sub = Sub()
    sub.x = 3  # ty: ignore[unresolved-attribute]
    assert env.entities.attach(sub) == "test_same-0"
    assert env.entities.snapshot()["test_same-0"] == {"$kind": "test_same", "x": 3, "label": "test_same-0"}


# --- debug validation of lifecycle operations (U4) ------------------------------------------------------


def test_debug_lifecycle_ops_for_dynamically_registered_kind(env_debug: Environment) -> None:
    seen = _record(env_debug)

    class Gizmo(Entity, kind="test_gizmo"):  # registered after the environment exists
        state_schema: ClassVar[StateSchema] = StateSchema(
            {
                "level": FieldSpec("float"),
                "tags": FieldSpec("str", collection="list"),
                "m": FieldSpec("int", collection="map"),
            }
        )

        def snapshot(self) -> dict[str, Any]:
            return {"level": 1.0, "tags": ["x"], "m": {"k": 1}, "label": self.label}

    gizmo = Gizmo()
    env_debug.entities.attach(gizmo)
    assert [type(e) for e in seen] == [EntityCreated]
    assert env_debug._entity_kind("test_gizmo-0") == "test_gizmo" and env_debug._entity_kind("nope") is None

    good = {"level": 2.0, "tags": ("y",), "m": {"a": 2}, "label": "g"}
    env_debug.emit(
        EntityCreated(
            entity="g2", kind="test_gizmo", label="g", deltas=Deltas.build().create("g2", "test_gizmo", good).done()
        )
    )

    bad_states: list[tuple[str, Any]] = [
        ("unknown_kind", good),
        ("test_gizmo", {k: v for k, v in good.items() if k != "tags"}),  # missing field
        ("test_gizmo", {**good, "extra": 1}),  # unknown field
        ("test_gizmo", {**good, "level": 2}),  # int for a float field (R6)
        ("test_gizmo", {**good, "level": None}),  # not nullable
        ("test_gizmo", {**good, "tags": "y"}),  # not a list
        ("test_gizmo", {**good, "tags": (1,)}),  # list item of the wrong type
        ("test_gizmo", {**good, "m": ("a",)}),  # not a map
        ("test_gizmo", {**good, "m": {"a": 1.5}}),  # map value of the wrong type
        ("test_gizmo", {**good, "label": 3}),
    ]
    for kind, state in bad_states:
        event = EntityCreated(entity="g3", kind=kind, label="g", deltas=Deltas.build().create("g3", kind, state).done())
        with pytest.raises((TypeError, ValueError)):
            env_debug.emit(event)
    with pytest.raises(TypeError):
        env_debug.emit(
            EntityCreated(
                entity="g3", kind="test_gizmo", label="g", deltas=Deltas((("create", "g3", "test_gizmo", ("x",)),))
            )
        )

    with pytest.raises(ValueError, match="live"):
        env_debug.emit(EntityRetired(entity="ghost", kind="test_gizmo", deltas=Deltas.build().retire("ghost").done()))
    env_debug.entities.retire(gizmo)
    with pytest.raises(ValueError, match="live"):
        env_debug.emit(EntityRetired(entity=gizmo.id, kind="test_gizmo", deltas=Deltas.build().retire(gizmo.id).done()))
    assert [type(e) for e in seen] == [EntityCreated, EntityCreated, EntityRetired]


def test_debug_validates_production_creations(env_debug: Environment) -> None:
    seen = _record(env_debug, "*")
    sf = ShopFloor(env=env_debug)
    server = Server(env=env_debug, capacity=1, shopfloor=sf)
    PreShopPool(env=env_debug, shopfloor=sf)
    _router(env_debug, sf, [server])
    job = _job(env_debug, [server], due_date=7)
    env_debug.entities.retire(job)
    assert len(seen) == 6


def test_state_schema_wire_roundtrip_and_validation() -> None:
    schema = StateSchema(
        {"a": FieldSpec("int", nullable=True), "b": FieldSpec("str", collection="list"), "label": LABEL_FIELD}
    )
    wire: Any = schema.to_wire()
    assert list(wire) == ["a", "b", "label"]
    assert wire["b"] == {"type": "str", "nullable": False, "collection": "list", "presentation": False}
    assert StateSchema.from_wire(wire) == schema
    assert StateSchema.from_wire(unpack(pack(wire))) == schema  # as read back from a trace

    with pytest.raises(TypeError, match="map"):
        StateSchema.from_wire(("a",))
    with pytest.raises(TypeError, match="declaration"):
        StateSchema.from_wire(FrozenMap({"a": "int"}))
    with pytest.raises(TypeError, match="booleans"):
        StateSchema.from_wire(FrozenMap({"a": FrozenMap({**wire["a"], "presentation": 1})}))
    with pytest.raises(ValueError, match="wire type"):
        StateSchema.from_wire(FrozenMap({"a": FrozenMap({**wire["a"], "type": "decimal"})}))
    with pytest.raises(KeyError):
        StateSchema.from_wire(FrozenMap({"a": FrozenMap({"type": "int"})}))


def test_an_entity_is_attached_once_live_retired_or_foreign() -> None:
    env = Environment()
    widget = Widget(env, name="w")
    with pytest.raises(ValueError, match="already has the id 'w'"):
        env.entities.attach(widget)  # live here
    env.entities.retire(widget)
    with pytest.raises(ValueError, match="already has the id 'w'"):
        env.entities.attach(widget, name="w2")  # retired here: its id would be silently rebound
    other = Environment()
    with pytest.raises(ValueError, match="already has the id 'w'"):
        other.entities.attach(widget)  # attached in another environment
    assert widget.id == "w"
    assert other.entities.live() == ()
    assert Widget(other).id == "test_widget-0"  # rejected attachments consume no generated id


class Slim(Entity, kind="test_slim"):
    """A slotted kind without ``__weakref__``."""

    __slots__ = ("id", "label", "x")
    state_schema: ClassVar[StateSchema] = StateSchema({"x": FieldSpec("int")})

    def __init__(self, env: Environment) -> None:
        self.x = 1
        env.entities.attach(self)


@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="every PyPy object supports weak references")
def test_retire_is_atomic_for_kinds_without_weak_references() -> None:
    env = Environment()
    seen = _record(env, (EntityRetired,))
    slim = Slim(env)
    with pytest.raises(TypeError, match="weak references"):
        env.entities.retire(slim)
    assert env.entities.live() == (slim,)  # the registry is unchanged
    assert env.entities.get(slim.id) is slim and env.entities.kind_of(slim.id) == "test_slim"
    assert seen == []


def test_job_construction_does_not_probe_a_missing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attaching a slotted job reads an initialized id slot instead of catching AttributeError."""
    env = Environment()
    server = Server(env=env, capacity=1)
    original = EntityRegistry.attach

    def attach(self: EntityRegistry, obj: Entity, **kwargs: Any) -> str:
        assert obj.id is None  # set by the job before attaching
        return original(self, obj, **kwargs)

    monkeypatch.setattr(EntityRegistry, "attach", attach)
    assert _job(env, [server]).id == "job-0"
