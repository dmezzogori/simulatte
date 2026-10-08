"""Typed events, state deltas, the event catalog and the event bus.

Every event is a frozen, slotted, keyword-only dataclass. :class:`DomainEvent` subclasses describe the
trajectory of the simulation and may carry :class:`Deltas` (state changes of entities);
:class:`ObserverEvent` subclasses (logging, KPI samples, anything emitted by observers) never do.

Concrete event types are declared with :func:`event_type`, which turns the class into a dataclass and
registers it in the global :data:`CATALOG` with its payload fields, their wire types and nullability,
the presentation fields and the ``(kind, field)`` pairs its deltas may touch.

Delta operations are tuples of wire values that address ``(entity_id, field)``:

==========  ===================================================
``set``     ``("set", entity, field, value)``
``insert``  ``("insert", entity, field, index, value)``
``remove``  ``("remove", entity, field, value)``
``move``    ``("move", entity, field, value, index)``
``put``     ``("put", entity, field, key, value)``
``delete``  ``("delete", entity, field, key)``
``create``  ``("create", entity, kind, state)``
``retire``  ``("retire", entity)``
==========  ===================================================

Events are delivered by an :class:`EventBus`; :meth:`simulatte.environment.Environment.emit` stamps them
and enforces the emission rules.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeAlias, TypeVar, dataclass_transform

from simulatte._wire import FrozenMap, Wire, escape_key, freeze

__all__ = [
    "CATALOG",
    "Catalog",
    "CatalogEntry",
    "DeltaBuilder",
    "Deltas",
    "DomainEvent",
    "Event",
    "EventBus",
    "FieldInfo",
    "Handler",
    "KpiSample",
    "LogEvent",
    "ObserverEvent",
    "Op",
    "Subscription",
    "apply_deltas",
    "event_type",
    "matches_wire_type",
    "validate_event",
]

Op: TypeAlias = tuple[Any, ...]
"""A delta operation: a tuple of wire values whose first item is the operation name."""

Handler: TypeAlias = Callable[[Any], object]
"""A bus subscriber: called with each matching event."""

FIELD_OPS = frozenset({"set", "insert", "remove", "move", "put", "delete"})
LIFECYCLE_OWNERS: Mapping[str, str] = MappingProxyType({"create": "entity.created", "retire": "entity.retired"})
"""The only event type allowed to carry each lifecycle operation."""


# ---------------------------------------------------------------------------------------------------------
# Deltas
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Deltas:
    """An immutable sequence of delta operations (see the module docstring for their shapes)."""

    ops: tuple[Op, ...] = ()

    EMPTY: ClassVar[Deltas]

    @staticmethod
    def build() -> DeltaBuilder:
        """Start building a :class:`Deltas` value."""
        return DeltaBuilder()

    def __len__(self) -> int:
        return len(self.ops)

    def __iter__(self) -> Iterator[Op]:
        return iter(self.ops)


Deltas.EMPTY = Deltas()


class DeltaBuilder:
    """Accumulates delta operations; values are frozen into wire values as they are added."""

    __slots__ = ("_ops",)

    def __init__(self) -> None:
        self._ops: list[Op] = []

    def set(self, entity: str, field: str, value: object) -> DeltaBuilder:
        self._ops.append(("set", entity, field, freeze(value)))
        return self

    def insert(self, entity: str, field: str, index: int, value: object) -> DeltaBuilder:
        self._ops.append(("insert", entity, field, index, freeze(value)))
        return self

    def remove(self, entity: str, field: str, value: object) -> DeltaBuilder:
        self._ops.append(("remove", entity, field, freeze(value)))
        return self

    def move(self, entity: str, field: str, value: object, index: int) -> DeltaBuilder:
        """Move `value` so that it ends up at position `index` of the list."""
        self._ops.append(("move", entity, field, freeze(value), index))
        return self

    def put(self, entity: str, field: str, key: str, value: object) -> DeltaBuilder:
        self._ops.append(("put", entity, field, key, freeze(value)))
        return self

    def delete(self, entity: str, field: str, key: str) -> DeltaBuilder:
        self._ops.append(("delete", entity, field, key))
        return self

    def create(self, entity: str, kind: str, state: Mapping[str, object]) -> DeltaBuilder:
        self._ops.append(("create", entity, kind, freeze(state)))
        return self

    def retire(self, entity: str) -> DeltaBuilder:
        self._ops.append(("retire", entity))
        return self

    def done(self) -> Deltas:
        return Deltas(tuple(self._ops)) if self._ops else Deltas.EMPTY


def apply_deltas(state: dict[str, dict[str, Any]], deltas: Deltas) -> None:
    """Apply `deltas` in order to `state`, a map from entity id to its field values.

    Field values stay wire values: list operations replace the tuple with an updated copy and map
    operations replace the :class:`FrozenMap`. ``create`` stores the entity kind under the reserved key
    ``"$kind"`` next to the initial fields. Raises `KeyError` for unknown entities, fields or map keys
    and `ValueError` for a duplicate ``create``, a ``remove``/``move`` of a missing value or an unknown
    operation.
    """
    for op in deltas.ops:
        name = op[0]
        if name == "create":
            entity = op[1]
            if entity in state:
                raise ValueError(f"create: entity {entity!r} already exists")
            fields = dict(op[3])
            fields["$kind"] = op[2]
            state[entity] = fields
            continue
        if name == "retire":
            del state[op[1]]
            continue
        fields = state[op[1]]
        field = op[2]
        if name == "set":
            fields[field] = op[3]
        elif name == "insert":
            current = fields[field]
            index = op[3]
            fields[field] = (*current[:index], op[4], *current[index:])
        elif name == "remove":
            fields[field] = _without(fields[field], op[3])
        elif name == "move":
            rest = _without(fields[field], op[3])
            index = op[4]
            fields[field] = (*rest[:index], op[3], *rest[index:])
        elif name == "put":
            fields[field] = FrozenMap({**fields[field], op[3]: op[4]})
        elif name == "delete":
            updated = dict(fields[field])
            del updated[op[3]]
            fields[field] = FrozenMap(updated)
        else:
            raise ValueError(f"unknown delta operation {name!r}")


def _without(items: tuple[Any, ...], value: object) -> tuple[Any, ...]:
    index = items.index(value)  # ValueError when missing
    return items[:index] + items[index + 1 :]


# ---------------------------------------------------------------------------------------------------------
# Event classes
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base of all events. `t` and `seq` are stamped by :meth:`Environment.emit`."""

    t: float = float("nan")
    seq: int = -1
    deltas: Deltas = Deltas.EMPTY

    type_name: ClassVar[str]
    type_version: ClassVar[int]
    touches: ClassVar[Mapping[str, tuple[str, ...]]]
    presentation_fields: ClassVar[frozenset[str]]
    payload_fields: ClassVar[tuple[str, ...]]
    """Payload field names in declaration order (set by :func:`event_type`)."""
    semantic_fields: ClassVar[tuple[str, ...]]
    """Payload field names without the presentation ones, in canonical order: sorted by the UTF-8 bytes of the
    escaped name (set by :func:`event_type`)."""
    wire_payload: ClassVar[tuple[tuple[str, str], ...]]
    """``(escaped key, field name)`` of every payload field in canonical order, for the encoders."""
    wire_semantic: ClassVar[tuple[tuple[str, str], ...]]
    """``(escaped key, field name)`` of the :attr:`semantic_fields`, for the encoders."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent(Event):
    """An event that is part of the trajectory. `ordinal` is stamped while the projection is active."""

    ordinal: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ObserverEvent(Event):
    """An event emitted for observers (logging, KPI samples); it never carries deltas."""


_BASE_FIELDS = frozenset(f.name for f in dataclasses.fields(DomainEvent))


# ---------------------------------------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldInfo:
    """A payload field as declared in the catalog."""

    name: str
    wire_type: str  # "str", "int", "float", "bool", "array", "map" or "any"
    nullable: bool
    presentation: bool


@dataclass(frozen=True, slots=True, eq=True)
class CatalogEntry:
    """The catalog description of one event type."""

    name: str
    version: int
    category: Literal["domain", "observer"]
    fields: tuple[FieldInfo, ...]
    touches: Mapping[str, tuple[str, ...]]

    def to_wire(self) -> FrozenMap:
        return FrozenMap(
            {
                "name": self.name,
                "version": self.version,
                "category": self.category,
                "fields": tuple(
                    FrozenMap(
                        {"name": f.name, "type": f.wire_type, "nullable": f.nullable, "presentation": f.presentation}
                    )
                    for f in self.fields
                ),
                "touches": FrozenMap({kind: self.touches[kind] for kind in sorted(self.touches)}),
            }
        )

    @classmethod
    def from_wire(cls, wire: Wire) -> CatalogEntry:
        data: Any = wire
        return cls(
            name=data["name"],
            version=data["version"],
            category=data["category"],
            fields=tuple(FieldInfo(f["name"], f["type"], f["nullable"], f["presentation"]) for f in data["fields"]),
            touches=_freeze_touches(data["touches"]),
        )


class Catalog:
    """Registry of event types by name."""

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, CatalogEntry] = {}

    def register(self, entry: CatalogEntry) -> None:
        """Register `entry`; re-registering an identical definition is a no-op, a different one raises."""
        existing = self._entries.get(entry.name)
        if existing is not None and existing != entry:
            raise ValueError(f"event type {entry.name!r} is already registered with a different definition")
        self._entries[entry.name] = entry

    def get(self, name: str) -> CatalogEntry:
        return self._entries[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def to_wire(self) -> FrozenMap:
        return FrozenMap({name: self._entries[name].to_wire() for name in self.names()})

    def __contains__(self, name: object) -> bool:
        return name in self._entries


CATALOG = Catalog()
"""The global event catalog."""

_E = TypeVar("_E", bound="type[Event]")

_WIRE_TYPES = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "tuple": "array",
    "list": "array",
    "Sequence": "array",
    "FrozenMap": "map",
    "Mapping": "map",
    "dict": "map",
}


@dataclass_transform(frozen_default=True, kw_only_default=True)
def event_type(
    name: str,
    *,
    version: int = 1,
    touches: Mapping[str, tuple[str, ...]] | None = None,
    presentation: frozenset[str] = frozenset(),
) -> Callable[[_E], _E]:
    """Declare and register an event type.

    The decorated class (a subclass of :class:`DomainEvent` or :class:`ObserverEvent`) becomes a frozen,
    slotted, keyword-only dataclass. `touches` maps entity kinds to the state fields the type's deltas
    may change; `presentation` names payload fields excluded from the semantic projection.
    """
    if not name:
        raise ValueError("event type name must be a non-empty string")
    if version < 1:
        raise ValueError(f"event type version must be >= 1, got {version}")

    def register(cls: _E) -> _E:
        if not (isinstance(cls, type) and issubclass(cls, (DomainEvent, ObserverEvent))):
            raise TypeError(f"@event_type({name!r}) needs a DomainEvent or ObserverEvent subclass")
        if "__dataclass_fields__" not in cls.__dict__:
            cls = dataclass(frozen=True, slots=True, kw_only=True)(cls)
        payload = [f for f in dataclasses.fields(cls) if f.name not in _BASE_FIELDS]
        unknown = presentation - {f.name for f in payload}
        if unknown:
            raise ValueError(f"@event_type({name!r}): presentation names unknown fields {sorted(unknown)}")
        domain = issubclass(cls, DomainEvent)
        if touches and not domain:
            raise ValueError(f"@event_type({name!r}): observer events carry no deltas, so they cannot declare touches")
        entry = CatalogEntry(
            name=name,
            version=version,
            category="domain" if domain else "observer",
            fields=tuple(_field_info(f, presentation) for f in payload),
            touches=_freeze_touches(touches or {}),
        )
        CATALOG.register(entry)
        names = tuple(f.name for f in payload)
        canonical = sorted(names, key=escape_key)  # code point order of the escaped names = UTF-8 byte order
        cls.type_name = name
        cls.type_version = version
        cls.touches = entry.touches
        cls.presentation_fields = frozenset(presentation)
        cls.payload_fields = names
        cls.semantic_fields = tuple(n for n in canonical if n not in presentation)
        cls.wire_payload = tuple((escape_key(n), n) for n in canonical)
        cls.wire_semantic = tuple((escape_key(n), n) for n in cls.semantic_fields)
        return cls

    return register


def _freeze_touches(touches: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({kind: tuple(touches[kind]) for kind in sorted(touches)})


def _field_info(f: dataclasses.Field[Any], presentation: frozenset[str]) -> FieldInfo:
    annotation = f.type
    if isinstance(annotation, str):
        text = annotation
    elif isinstance(annotation, type):
        text = annotation.__name__
    else:
        text = str(annotation)
    parts = _split_union(text)
    nullable = any(p in ("None", "NoneType") for p in parts)
    others = [p for p in parts if p not in ("None", "NoneType")]
    wire_type = "any"
    if len(others) == 1:
        head = others[0].split("[", 1)[0].strip().rsplit(".", 1)[-1]
        wire_type = _WIRE_TYPES.get(head, "any")
    return FieldInfo(f.name, wire_type, nullable or wire_type == "any", f.name in presentation)


def _split_union(text: str) -> list[str]:
    """Split an annotation on top-level ``|``."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


# ---------------------------------------------------------------------------------------------------------
# Debug validation
# ---------------------------------------------------------------------------------------------------------


def validate_event(
    event: Event,
    *,
    entity_kind: Callable[[str], str | None],
    check_lifecycle: Callable[[Op], None],
) -> None:
    """Validate `event` against the catalog (debug mode).

    Checks that the type is registered, that each payload value matches its declared wire type and
    nullability, that field operations target ``(kind, field)`` pairs declared in ``touches`` (skipped
    when `entity_kind` does not know the addressed entity), and that ``create``/``retire`` are carried
    only by ``entity.created``/``entity.retired``. `check_lifecycle` is called for each lifecycle
    operation so that the entity registry can check it against kind schemas and live entities.
    """
    cls = type(event)
    name = cls.__dict__.get("type_name")
    if name is None:
        raise TypeError(f"{cls.__name__} is not registered with @event_type")
    entry = CATALOG.get(name)
    for info in entry.fields:
        _check_value(name, info, getattr(event, info.name))
    touches = cls.touches
    for op in event.deltas.ops:
        op_name = op[0]
        if op_name in FIELD_OPS:
            kind = entity_kind(op[1])
            if kind is not None and op[2] not in touches.get(kind, ()):
                raise ValueError(f"{name}: {op_name} on {kind}.{op[2]} is outside the declared touches")
        elif op_name in LIFECYCLE_OWNERS:
            owner = LIFECYCLE_OWNERS[op_name]
            if name != owner:
                raise ValueError(f"{name}: only {owner} may carry {op_name!r} entity operations")
            check_lifecycle(op)
        else:
            raise ValueError(f"{name}: unknown delta operation {op_name!r}")


def _check_value(event_name: str, info: FieldInfo, value: object) -> None:
    if value is None:
        if not info.nullable:
            raise TypeError(f"{event_name}.{info.name} is not nullable")
        return
    if not matches_wire_type(info.wire_type, value):
        raise TypeError(f"{event_name}.{info.name} expects {info.wire_type}, got {type(value).__name__}")
    freeze(value)  # TypeError / OverflowError for values outside the wire model


def matches_wire_type(wire_type: str, value: object) -> bool:
    """Whether non-null `value` has the declared `wire_type` (debug validation).

    ``float`` accepts only ``float`` instances, not ``int``: emitting sites coerce, so canonical bytes do not
    depend on incidental int/float types. ``any`` accepts everything.
    """
    if wire_type == "str":
        return isinstance(value, str)
    if wire_type == "bool":
        return isinstance(value, bool)
    if wire_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if wire_type == "float":
        return isinstance(value, float)
    if wire_type == "array":
        return isinstance(value, (tuple, list))
    if wire_type == "map":
        return isinstance(value, Mapping)
    return True


# ---------------------------------------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------------------------------------


class Subscription:
    """A handle returned by :meth:`EventBus.subscribe`."""

    __slots__ = ("_bus", "_classes", "active", "handler", "types")

    def __init__(
        self,
        bus: EventBus,
        handler: Handler,
        types: tuple[type[Event], ...] | Literal["*", "**"],
        classes: tuple[type[Event], ...],
    ) -> None:
        self._bus = bus
        self._classes = classes
        self.handler = handler
        self.types = types
        self.active = True

    def cancel(self) -> None:
        """Stop delivering events to the handler (idempotent; takes effect from the next event)."""
        if self.active:
            self.active = False
            self._bus._remove(self)


class EventBus:
    """Synchronous event delivery with nested emissions queued FIFO.

    `probe`, when given, is called before and after each handler and returns the number of scheduled SimPy
    events and the number of RNG draws; a change in either means the handler scheduled a SimPy event or
    drew from ``env.rng``, which raises `RuntimeError` (debug mode).
    """

    __slots__ = ("_delivering", "_pending", "_probe", "_routes", "_subscriptions")

    def __init__(self, *, probe: Callable[[], tuple[int, int]] | None = None) -> None:
        self._subscriptions: list[Subscription] = []
        self._routes: dict[type, tuple[Handler, ...]] = {}
        self._pending: deque[Event] = deque()
        self._delivering = False
        self._probe = probe

    @property
    def delivering(self) -> bool:
        """True while subscribers are being called."""
        return self._delivering

    def subscribe(self, handler: Handler, types: tuple[type[Event], ...] | Literal["*", "**"]) -> Subscription:
        """Deliver events to `handler` in subscription order.

        `types` is a tuple of event classes (subclasses match too), ``"*"`` for every domain event,
        including types registered later, or ``"**"`` for every event.
        """
        if not callable(handler):
            raise TypeError("handler must be callable")
        if types == "*":
            classes: tuple[type[Event], ...] = (DomainEvent,)
        elif types == "**":
            classes = (Event,)
        elif isinstance(types, tuple) and types:
            if not all(isinstance(c, type) and issubclass(c, Event) for c in types):
                raise TypeError("types must be Event subclasses")
            classes = types
        else:
            raise ValueError('types must be a non-empty tuple of event classes, "*" or "**"')
        subscription = Subscription(self, handler, types, classes)
        self._subscriptions.append(subscription)
        self._routes.clear()
        return subscription

    def wants(self, cls: type[Event]) -> bool:
        """Whether any subscriber listens to events of type `cls`."""
        handlers = self._routes.get(cls)
        if handlers is None:
            handlers = self._route(cls)
        return bool(handlers)

    def publish(self, event: Event) -> None:
        """Deliver `event`, or queue it if delivery is in progress.

        If a handler raises, the exception propagates, the queue is cleared and the bus stays usable.
        """
        if self._delivering:
            self._pending.append(event)
            return
        self._delivering = True
        try:
            self._dispatch(event)
            pending = self._pending
            while pending:
                self._dispatch(pending.popleft())
        finally:
            self._pending.clear()
            self._delivering = False

    def _dispatch(self, event: Event) -> None:
        cls = type(event)
        handlers = self._routes.get(cls)
        if handlers is None:
            handlers = self._route(cls)
        probe = self._probe
        if probe is None:
            for handler in handlers:
                handler(event)
            return
        for handler in handlers:
            scheduled, draws = probe()
            handler(event)
            scheduled_after, draws_after = probe()
            if scheduled_after != scheduled:
                raise RuntimeError(f"subscriber {handler!r} scheduled a SimPy event while handling {cls.__name__}")
            if draws_after != draws:
                raise RuntimeError(f"subscriber {handler!r} drew from env.rng while handling {cls.__name__}")

    def _route(self, cls: type) -> tuple[Handler, ...]:
        handlers = tuple(s.handler for s in self._subscriptions if issubclass(cls, s._classes))
        self._routes[cls] = handlers
        return handlers

    def _remove(self, subscription: Subscription) -> None:
        self._subscriptions.remove(subscription)
        self._routes.clear()


# ---------------------------------------------------------------------------------------------------------
# Observer events
# ---------------------------------------------------------------------------------------------------------


@event_type("log")
class LogEvent(ObserverEvent):
    """A log record."""

    level: str
    message: str
    component: str | None = None
    extra: FrozenMap = FrozenMap({})


@event_type("kpi.sample")
class KpiSample(ObserverEvent):
    """One sample of a KPI series; `scope` is the id of the owner entity."""

    kpi: str
    scope: str
    value: float
