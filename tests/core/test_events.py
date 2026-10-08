"""Tests for typed events, deltas, the event catalog and the event bus."""

from __future__ import annotations

import math
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest

from simulatte import events
from simulatte._wire import FrozenMap, pack, unpack
from simulatte.environment import Environment
from simulatte.events import (
    CATALOG,
    Catalog,
    CatalogEntry,
    Deltas,
    DomainEvent,
    Event,
    EventBus,
    KpiSample,
    LogEvent,
    ObserverEvent,
    apply_deltas,
    event_type,
)


@event_type("test.ping", touches={"server": ("queue", "users")})
class Ping(DomainEvent):
    n: int = 0


@event_type("test.pong")
class Pong(ObserverEvent):
    note: str = ""


@event_type(
    "test.rich", version=2, touches={"job": ("location",), "server": ("queue",)}, presentation=frozenset({"label"})
)
class Rich(DomainEvent):
    job: str
    label: str | None = None
    weight: float = 0.0
    route: tuple[str, ...] = ()
    info: FrozenMap = FrozenMap({})
    flag: bool = False
    anything: Any = None


@pytest.fixture
def env_debug() -> Environment:
    return Environment(debug=True)


def _record(env: Environment, types: Any = "**") -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, types)
    return seen


# --- emission ------------------------------------------------------------------------------------------


def test_emit_stamps_time_and_seq(env: Environment) -> None:
    seen = _record(env)
    first, second = Ping(n=1), Pong(note="x")
    assert math.isnan(first.t) and first.seq == -1 and first.ordinal is None

    env.emit(first)
    env.emit(second)
    assert [(e.t, e.seq) for e in seen] == [(0, 0), (0, 1)]

    def proc() -> Generator[Any, Any, None]:
        yield env.timeout(5)
        env.emit(Ping(n=2))

    env.process(proc())
    env.run()
    assert seen[-1].t == 5 and seen[-1].seq == 2
    assert all(isinstance(e, DomainEvent) and e.ordinal is None for e in seen if isinstance(e, Ping))


def test_ordinal_stamped_only_while_projection_active(env: Environment) -> None:
    seen = _record(env)
    env.emit(Ping())
    env._projection_active = True
    env.emit(Ping())
    env.emit(Pong())
    env.emit(Ping())
    assert [getattr(e, "ordinal", "obs") for e in seen] == [None, 0, "obs", 1]
    assert [e.seq for e in seen] == [0, 1, 2, 3]


def test_emit_without_subscribers_still_stamps(env: Environment) -> None:
    event = Ping()
    env.emit(event)
    assert event.seq == 0 and event.t == 0


def test_wants_tracks_subscriptions(env: Environment) -> None:
    assert not env.wants(Ping) and not env.wants(Pong)

    sub = env.bus.subscribe(lambda e: None, (Ping,))
    assert env.wants(Ping) and not env.wants(Pong) and not env.wants(Rich)
    sub.cancel()
    assert not env.wants(Ping)
    sub.cancel()  # idempotent
    assert not env.wants(Ping)

    star = env.bus.subscribe(lambda e: None, "*")
    assert env.wants(Ping) and env.wants(Rich) and not env.wants(Pong) and not env.wants(LogEvent)
    everything = env.bus.subscribe(lambda e: None, "**")
    assert env.wants(Pong) and env.wants(LogEvent) and env.wants(KpiSample)
    everything.cancel()
    assert not env.wants(Pong) and env.wants(Ping)
    star.cancel()
    assert not env.wants(Ping)

    base = env.bus.subscribe(lambda e: None, (ObserverEvent,))
    assert env.wants(Pong) and env.wants(LogEvent) and not env.wants(Ping)
    base.cancel()


def test_subscribe_rejects_bad_arguments(env: Environment) -> None:
    bad: list[Any] = ["x", (), (int,), [Ping], ("*",)]
    for bad_types in bad:
        with pytest.raises((TypeError, ValueError)):
            env.bus.subscribe(lambda e: None, bad_types)
    not_callable: Any = 3
    with pytest.raises(TypeError):
        env.bus.subscribe(not_callable, "*")


def test_star_covers_types_registered_later(env: Environment) -> None:
    seen = _record(env, "*")

    @event_type("test.registered_later")
    class Later(DomainEvent):
        value: int

    assert env.wants(Later)
    env.emit(Later(value=7))
    assert len(seen) == 1 and isinstance(seen[0], Later)


def test_delivery_in_subscription_order(env: Environment) -> None:
    order: list[str] = []
    env.bus.subscribe(lambda e: order.append("a"), "*")
    env.bus.subscribe(lambda e: order.append("b"), (Ping,))
    env.bus.subscribe(lambda e: order.append("c"), "**")
    env.emit(Ping())
    assert order == ["a", "b", "c"]


def test_nested_observer_emits_fifo(env: Environment) -> None:
    def on_ping(event: Ping) -> None:
        env.emit(Pong(note="from ping"))

    env.bus.subscribe(on_ping, (Ping,))
    seen = _record(env)
    env.emit(Ping())
    assert [type(e).__name__ for e in seen] == ["Ping", "Pong"]
    assert seen[1].seq > seen[0].seq
    assert not env.bus.delivering


def test_subscriber_exception_propagates_and_bus_recovers(env: Environment) -> None:
    calls = {"n": 0}

    def explode(event: Ping) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            env.emit(Pong(note="queued before the failure"))
            raise RuntimeError("boom")

    env.bus.subscribe(explode, (Ping,))
    seen = _record(env)
    with pytest.raises(RuntimeError, match="boom"):
        env.emit(Ping())
    assert seen == []  # nested queue cleared, later subscribers not reached
    assert not env.bus.delivering

    env.emit(Ping(n=2))
    assert [type(e).__name__ for e in seen] == ["Ping"]


def test_domain_event_from_subscriber_rejected(env: Environment) -> None:
    def inject(event: Pong) -> None:
        env.emit(Ping())

    env.bus.subscribe(inject, (Pong,))
    with pytest.raises(RuntimeError, match="DomainEvent"):
        env.emit(Pong())
    env.emit(Ping())  # allowed outside delivery


def test_observer_event_with_deltas_rejected(env: Environment) -> None:
    seen = _record(env)
    event = Pong(deltas=Deltas.build().set("s", "queue", ()).done())
    with pytest.raises(ValueError, match="deltas"):
        env.emit(event)
    assert seen == [] and event.seq == -1


def test_reemitted_instance_rejected_and_first_unchanged(env: Environment) -> None:
    seen = _record(env)
    event = Ping(n=1)
    env.emit(event)
    env.run(until=3)
    with pytest.raises(ValueError, match="already emitted"):
        env.emit(event)
    assert (event.t, event.seq) == (0, 0)
    assert seen == [event]


def test_events_are_frozen() -> None:
    event: Any = Ping(n=1)
    with pytest.raises(AttributeError):
        event.n = 2


# --- deltas --------------------------------------------------------------------------------------------


def test_delta_builder_freezes_values_and_ops_are_tuples() -> None:
    deltas = (
        Deltas.build()
        .set("j", "routing", ["a", "b"])
        .insert("s", "queue", 0, "j")
        .remove("s", "queue", "j")
        .move("s", "queue", "j", 1)
        .put("f", "wip", "s", 1.5)
        .delete("f", "wip", "s")
        .create("j", "job", {"routing": ["a"], "meta": {"k": 1}})
        .retire("j")
        .done()
    )
    assert deltas.ops == (
        ("set", "j", "routing", ("a", "b")),
        ("insert", "s", "queue", 0, "j"),
        ("remove", "s", "queue", "j"),
        ("move", "s", "queue", "j", 1),
        ("put", "f", "wip", "s", 1.5),
        ("delete", "f", "wip", "s"),
        ("create", "j", "job", FrozenMap({"routing": ("a",), "meta": FrozenMap({"k": 1})})),
        ("retire", "j"),
    )
    assert len(deltas) == 8 and list(deltas) == list(deltas.ops) and deltas
    assert hash(deltas) == hash(Deltas(deltas.ops))
    assert Deltas.build().done() is Deltas.EMPTY and not Deltas.EMPTY
    with pytest.raises(TypeError):
        Deltas.build().set("j", "x", object())
    frozen: Any = deltas
    with pytest.raises(AttributeError):
        frozen.ops = ()
    assert unpack(pack(deltas.ops)) == deltas.ops


def test_apply_deltas_all_ops() -> None:
    state: dict[str, dict[str, Any]] = {}
    apply_deltas(
        state,
        Deltas.build()
        .create("s1", "server", {"queue": [], "wip": {}, "worked_time": 0.0})
        .create("j9", "job", {"location": None})
        .insert("s1", "queue", 0, "j1")
        .insert("s1", "queue", 1, "j2")
        .insert("s1", "queue", 0, "j0")
        .move("s1", "queue", "j2", 0)
        .remove("s1", "queue", "j0")
        .put("s1", "wip", "a", 1.5)
        .put("s1", "wip", "b", 2)
        .put("s1", "wip", "b", 3)
        .delete("s1", "wip", "a")
        .set("s1", "worked_time", 3.0)
        .set("j9", "location", "done")
        .done(),
    )
    assert state == {
        "s1": {"$kind": "server", "queue": ("j2", "j1"), "wip": {"b": 3}, "worked_time": 3.0},
        "j9": {"$kind": "job", "location": "done"},
    }
    assert isinstance(state["s1"]["queue"], tuple) and isinstance(state["s1"]["wip"], FrozenMap)

    apply_deltas(state, Deltas.build().retire("j9").done())
    assert set(state) == {"s1"}
    apply_deltas(state, Deltas.EMPTY)
    assert set(state) == {"s1"}


@pytest.mark.parametrize(
    ("ops", "error"),
    [
        ((("create", "s1", "server", FrozenMap({})),), ValueError),
        ((("retire", "missing"),), KeyError),
        ((("set", "missing", "f", 1),), KeyError),
        ((("remove", "s1", "queue", "nope"),), ValueError),
        ((("delete", "s1", "wip", "nope"),), KeyError),
        ((("explode", "s1", "queue"),), ValueError),
    ],
)
def test_apply_deltas_rejects_inconsistent_ops(ops: Any, error: type[Exception]) -> None:
    state: dict[str, dict[str, Any]] = {"s1": {"queue": ("j1",), "wip": FrozenMap({})}}
    with pytest.raises(error):
        apply_deltas(state, Deltas(ops))


# --- catalog -------------------------------------------------------------------------------------------


def test_event_type_sets_class_attributes() -> None:
    assert Rich.type_name == "test.rich" and Rich.type_version == 2
    assert dict(Rich.touches) == {"job": ("location",), "server": ("queue",)}
    assert Rich.presentation_fields == frozenset({"label"})
    assert Pong.touches == {} and Pong.presentation_fields == frozenset()
    assert {"log", "kpi.sample", "test.ping"} <= set(CATALOG.names())
    assert list(CATALOG.names()) == sorted(CATALOG.names())
    assert "test.ping" in CATALOG and "test.nope" not in CATALOG
    with pytest.raises(KeyError):
        CATALOG.get("test.nope")

    log = LogEvent(level="INFO", message="hi")
    assert log.component is None and log.extra == {}
    assert KpiSample(kpi="wip", scope="shopfloor", value=1.0).value == 1.0


def test_catalog_entry_describes_fields() -> None:
    entry = CATALOG.get("test.rich")
    assert entry.category == "domain" and CATALOG.get("test.pong").category == "observer"
    assert [(f.name, f.wire_type, f.nullable, f.presentation) for f in entry.fields] == [
        ("job", "str", False, False),
        ("label", "str", True, True),
        ("weight", "float", False, False),
        ("route", "array", False, False),
        ("info", "map", False, False),
        ("flag", "bool", False, False),
        ("anything", "any", True, False),
    ]
    assert [(f.name, f.wire_type) for f in CATALOG.get("test.ping").fields] == [("n", "int")]


def test_duplicate_type_name_different_fields_raises() -> None:
    @event_type("test.dup")
    class First(DomainEvent):
        a: int

    @event_type("test.dup")
    class Same(DomainEvent):  # an identical definition (e.g. a module reload) is accepted
        a: int

    with pytest.raises(ValueError, match="test.dup"):

        @event_type("test.dup")
        class Other(DomainEvent):
            b: int

    with pytest.raises(ValueError, match="test.dup"):

        @event_type("test.dup", version=2)
        class Newer(DomainEvent):
            a: int

    assert [f.name for f in CATALOG.get("test.dup").fields] == ["a"]


def test_event_type_rejects_bad_declarations() -> None:
    with pytest.raises(TypeError):

        @event_type("test.bad_base")
        class NotAnEvent:
            a: int

    with pytest.raises(TypeError):

        @event_type("test.bad_bare")
        class Bare(Event):
            a: int

    with pytest.raises(ValueError, match="presentation"):

        @event_type("test.bad_presentation", presentation=frozenset({"missing"}))
        class BadPresentation(DomainEvent):
            a: int

    with pytest.raises(ValueError, match="touches"):

        @event_type("test.bad_touches", touches={"server": ("queue",)})
        class ObserverTouches(ObserverEvent):
            a: int

    with pytest.raises(ValueError):

        @event_type("", version=1)
        class NoName(DomainEvent):
            a: int

    with pytest.raises(ValueError):

        @event_type("test.bad_version", version=0)
        class BadVersion(DomainEvent):
            a: int

    for name in ("test.bad_base", "test.bad_bare", "test.bad_presentation", "test.bad_touches", "test.bad_version"):
        assert name not in CATALOG


def test_event_type_accepts_dataclasses_and_runtime_annotations() -> None:
    @event_type("test.explicit_dataclass")
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Explicit(DomainEvent):
        a: int

    assert Explicit(a=1).a == 1 and Explicit.type_name == "test.explicit_dataclass"

    # A class whose annotations are runtime objects (a module without `from __future__ import annotations`).
    runtime = type("Runtime", (ObserverEvent,), {"__annotations__": {"a": int, "b": int | None, "c": int | str}})
    event_type("test.runtime_annotations")(runtime)
    fields = CATALOG.get("test.runtime_annotations").fields
    assert [(f.name, f.wire_type, f.nullable) for f in fields] == [
        ("a", "int", False),
        ("b", "int", True),
        ("c", "any", True),
    ]


def test_catalog_wire_roundtrip_includes_touches() -> None:
    wire: Any = unpack(pack(CATALOG.to_wire()))
    assert set(wire) == set(CATALOG.names())
    rich = wire["test.rich"]
    assert rich["touches"] == {"job": ("location",), "server": ("queue",)}
    assert rich["version"] == 2 and rich["category"] == "domain"
    assert [(f["name"], f["presentation"]) for f in rich["fields"]][:2] == [("job", False), ("label", True)]
    for name in CATALOG.names():
        assert CatalogEntry.from_wire(wire[name]) == CATALOG.get(name)


# --- debug mode ----------------------------------------------------------------------------------------


def test_debug_rejects_deltas_outside_touches(env_debug: Environment, monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = {"s1": "server", "j1": "job"}
    monkeypatch.setattr(env_debug, "_entity_kind", kinds.get)
    seen = _record(env_debug)

    env_debug.emit(Ping(deltas=Deltas.build().insert("s1", "queue", 0, "j1").remove("s1", "users", "j0").done()))
    env_debug.emit(Ping(deltas=Deltas.build().set("unknown-entity", "anything", 1).done()))  # kind unknown: skipped
    assert len(seen) == 2

    outside_field = Ping(deltas=Deltas.build().set("s1", "worked_time", 1.0).done())
    with pytest.raises(ValueError, match="touches"):
        env_debug.emit(outside_field)
    with pytest.raises(ValueError, match="touches"):
        env_debug.emit(Ping(deltas=Deltas.build().set("j1", "location", "x").done()))
    with pytest.raises(ValueError, match="unknown delta operation"):
        env_debug.emit(Ping(deltas=Deltas((("explode", "s1", "queue"),))))
    assert len(seen) == 2 and outside_field.seq == -1


def test_debug_lifecycle_ops_only_on_lifecycle_events(env_debug: Environment, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stand-ins for the lifecycle types, registered in an isolated catalog so they never clash with the real ones.
    isolated = Catalog()
    isolated.register(CATALOG.get("test.ping"))
    monkeypatch.setattr(events, "CATALOG", isolated)
    monkeypatch.setattr(env_debug, "_check_lifecycle_op", lambda op: None)  # owner check only (schemas: test_entities)

    @event_type("entity.created")
    class _Created(DomainEvent):
        entity: str

    @event_type("entity.retired")
    class _Retired(DomainEvent):
        entity: str

    seen = _record(env_debug)
    env_debug.emit(_Created(entity="j1", deltas=Deltas.build().create("j1", "job", {}).done()))
    env_debug.emit(_Retired(entity="j1", deltas=Deltas.build().retire("j1").done()))
    assert len(seen) == 2

    for event in (
        Ping(deltas=Deltas.build().create("j2", "job", {}).done()),
        Ping(deltas=Deltas.build().retire("j1").done()),
        _Created(entity="j1", deltas=Deltas.build().retire("j1").done()),
        _Retired(entity="j1", deltas=Deltas.build().create("j1", "job", {}).done()),
    ):
        with pytest.raises(ValueError, match="entity"):
            env_debug.emit(event)
    assert len(seen) == 2


def test_debug_validates_payload(env_debug: Environment) -> None:
    env_debug.emit(Rich(job="j", label=None, weight=1.0, route=("a",), info=FrozenMap({"k": 1}), anything=[1, 2]))
    bad: list[Any] = [
        Rich(job=None),  # ty: ignore[invalid-argument-type]
        Rich(job="j", weight="heavy"),  # ty: ignore[invalid-argument-type]
        Rich(job="j", weight=True),
        Rich(job="j", weight=1),  # float fields take only floats
        Rich(job="j", flag=1),  # ty: ignore[invalid-argument-type]
        Rich(job="j", route="ab"),  # ty: ignore[invalid-argument-type]
        Rich(job="j", route=(object(),)),  # ty: ignore[invalid-argument-type]
        Rich(job="j", info=("k",)),  # ty: ignore[invalid-argument-type]
        Rich(job="j", anything=object()),
        Ping(n=2**60),
    ]
    for event in bad:
        with pytest.raises((TypeError, OverflowError)):
            env_debug.emit(event)


def test_debug_rejects_unregistered_types(env_debug: Environment) -> None:
    class Unregistered(Ping):
        pass

    with pytest.raises(TypeError, match="event_type"):
        env_debug.emit(Unregistered())


def test_debug_rejects_subscriber_scheduling(env_debug: Environment, env: Environment) -> None:
    def schedules(event: Event) -> None:
        env_debug.timeout(1)

    env_debug.bus.subscribe(schedules, (Ping,))
    with pytest.raises(RuntimeError, match="scheduled"):
        env_debug.emit(Ping())
    assert not env_debug.bus.delivering

    env.bus.subscribe(lambda e: env.timeout(1), (Ping,))
    env.emit(Ping())  # not checked outside debug mode


def test_standalone_bus_has_no_probe() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append, "**")
    event = Pong()
    bus.publish(event)
    assert seen == [event] and bus.wants(Pong)
