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
from collections.abc import Callable, Iterator, Mapping, MutableMapping, MutableSequence, MutableSet
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeAlias, TypeVar, dataclass_transform

from simulatte._wire import FrozenMap, Wire, escape_key, exact_int, freeze, wire_equal

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
    "check_op_shape",
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


_ARITY: Mapping[str, int] = MappingProxyType(
    {"set": 4, "insert": 5, "remove": 4, "move": 5, "put": 5, "delete": 4, "create": 4, "retire": 2}
)


def apply_deltas(state: dict[str, dict[str, Any]], deltas: Deltas) -> None:
    """Apply `deltas` in order to `state`, a map from entity id to its field values.

    Field values stay wire values: list operations replace the tuple with an updated copy and map
    operations replace the :class:`FrozenMap`. ``create`` stores the entity kind under the reserved key
    ``"$kind"`` next to the initial fields. A ``set`` on a field the entity does not hold yet creates it (spec
    §6.2, ruling R12; the TypeScript reader does the same). ``remove`` and ``move`` find the first item whose
    canonical encoding equals the value's (:func:`~simulatte._wire.wire_equal`, ruling R31).

    Operation shapes are checked as the TypeScript reader checks them: each operation has its arity, entity ids,
    fields, keys and kinds are strings, indices integral numbers, ``create`` states maps, list operations need a
    list field and map operations a map field. Raises `KeyError` for unknown entities, for the other field
    operations on a missing field and for unknown map keys, `TypeError` for a wrong shape and `ValueError` for a
    duplicate ``create``, a ``remove``/``move`` of a missing value, a wrong arity or an unknown operation.
    """
    for op in deltas.ops:
        name = op[0]
        arity = _ARITY.get(name) if type(name) is str else None
        if arity is None:
            raise ValueError(f"unknown delta operation {name!r}")
        if len(op) != arity:
            raise ValueError(f"{name}: an operation of {arity} items, got {len(op)}")
        entity = op[1]
        if type(entity) is not str:  # inlined checks: the trace writer replays every operation
            raise TypeError(f"entity must be a string, got {type(entity).__name__}")
        if name == "create":
            if entity in state:
                raise ValueError(f"create: entity {entity!r} already exists")
            fields = dict(_map(op[3], "create state"))
            fields["$kind"] = _text(op[2], "kind")
            state[entity] = fields
            continue
        if name == "retire":
            del state[entity]
            continue
        fields = state[entity]
        field = op[2]
        if type(field) is not str:
            raise TypeError(f"field must be a string, got {type(field).__name__}")
        if name == "set":
            fields[field] = op[3]
        elif name == "insert":
            current = _list(fields[field], field)
            index = _index(op[3])
            fields[field] = (*current[:index], op[4], *current[index:])
        elif name == "remove":
            fields[field] = _without(_list(fields[field], field), op[3])
        elif name == "move":
            rest = _without(_list(fields[field], field), op[3])
            index = _index(op[4])
            fields[field] = (*rest[:index], op[3], *rest[index:])
        elif name == "put":
            fields[field] = FrozenMap({**_map(fields[field], field), _text(op[3], "key"): op[4]})
        else:  # delete
            updated = dict(_map(fields[field], field))
            del updated[_text(op[3], "key")]
            fields[field] = FrozenMap(updated)


_LIST_OPS = frozenset({"insert", "remove", "move"})
_MAP_OPS = frozenset({"put", "delete"})


def check_op_shape(op: Op, collection_of: Callable[[str, str], str | None] | None = None) -> None:
    """Check the shape of one delta operation with the rules :func:`apply_deltas` applies while replaying.

    The arity, string entity ids, fields, map keys and ``create`` kinds, integral indices and map ``create`` states.
    `collection_of`, when given, returns ``"list"``, ``"map"`` or ``"scalar"`` for an entity's field (None when it
    does not know): list operations then need a list field and map operations a map field. Raises `TypeError` or
    `ValueError` like :func:`apply_deltas`.
    """
    name = op[0]
    arity = _ARITY.get(name) if type(name) is str else None
    if arity is None:
        raise ValueError(f"unknown delta operation {name!r}")
    if len(op) != arity:
        raise ValueError(f"{name}: an operation of {arity} items, got {len(op)}")
    entity = _text(op[1], "entity")
    if name == "create":
        _text(op[2], "kind")
        _map(op[3], "create state")
        return
    if name == "retire":
        return
    field = _text(op[2], "field")
    if name == "insert":
        _index(op[3])
    elif name == "move":
        _index(op[4])
    elif name in _MAP_OPS:
        _text(op[3], "key")
    collection = None if collection_of is None or name == "set" else collection_of(entity, field)
    if collection is not None:
        if name in _LIST_OPS and collection != "list":
            raise TypeError(f"{name}: field {field!r} is not a list")
        if name in _MAP_OPS and collection != "map":
            raise TypeError(f"{name}: field {field!r} is not a map")


def _text(value: object, what: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{what} must be a string, got {type(value).__name__}")
    return value


def _list(value: object, field: str) -> tuple[Any, ...]:
    if type(value) is tuple or type(value) is list:
        return tuple(value)
    raise TypeError(f"field {field!r} is not a list")


def _map(value: object, what: str) -> Mapping[str, Any]:
    if type(value) is FrozenMap or type(value) is dict:
        return value  # ty: ignore[invalid-return-type]
    raise TypeError(f"{what} is not a map")


def _index(value: object) -> int:
    """An index: an integer or a float with an integral value (JavaScript cannot tell them apart)."""
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    raise TypeError(f"an index must be an integer, got {value!r}")


def _without(items: tuple[Any, ...], value: object) -> tuple[Any, ...]:
    index = _index_of(items, value)
    return items[:index] + items[index + 1 :]


def _index_of(items: tuple[Any, ...], value: object) -> int:
    """Index of the first item with the canonical encoding of `value` (ruling R31); `ValueError` when none has.

    ``True == 1``, ``1 == 1.0`` and ``0.0 == -0.0`` in Python, and NaN equals nothing, so ``tuple.index`` alone
    would find the wrong item or none. For a string, an integer, a boolean or a non-NaN float, canonical equality
    implies ``==``, so ``tuple.index`` finds every candidate in order and :func:`wire_equal` confirms it; other
    values are compared item by item.
    """
    t = type(value)
    if t is str or t is int or t is bool or (t is float and value == value):
        start = 0
        while True:
            index = items.index(value, start)  # ValueError when missing
            if wire_equal(items[index], value):
                return index
            start = index + 1
    for index, item in enumerate(items):
        if wire_equal(item, value):
            return index
    raise ValueError("remove or move of a value the list does not hold")


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
    collection_of: Callable[[str, str], str | None] | None = None,
) -> None:
    """Validate `event` against the catalog (debug mode).

    Checks that the type is registered, that each payload value matches its declared wire type and
    nullability, that payload values and delta operations are deep-immutable wire values (tuples and
    :class:`FrozenMap`, never lists, dicts or other mutable containers; global C1.2, ruling R30), that field
    operations target ``(kind, field)`` pairs declared in ``touches`` (skipped when `entity_kind` does not know
    the addressed entity), that every operation has the shape the trace writer's replay requires
    (:func:`check_op_shape`, with `collection_of` telling list, map and scalar fields apart), and that
    ``create``/``retire`` are carried only by ``entity.created``/``entity.retired``. `check_lifecycle` is called for
    each lifecycle operation so that the entity registry can check it against kind schemas and live entities.
    Errors name the event type.
    """
    cls = type(event)
    name = cls.__dict__.get("type_name")
    if name is None:
        raise TypeError(f"{cls.__name__} is not registered with @event_type")
    entry = CATALOG.get(name)
    for info in entry.fields:
        _check_value(name, info, getattr(event, info.name))
    touches = cls.touches
    ops = event.deltas.ops
    _check_immutable(f"{name}: the delta operations", ops)
    for op in ops:
        try:
            check_op_shape(op, collection_of)
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{name}: {exc}") from exc
        op_name = op[0]
        if op_name in FIELD_OPS:
            kind = entity_kind(op[1])
            if kind is not None and op[2] not in touches.get(kind, ()):
                raise ValueError(f"{name}: {op_name} on {kind}.{op[2]} is outside the declared touches")
        else:  # create or retire: check_op_shape rejected unknown operations
            owner = LIFECYCLE_OWNERS[op_name]
            if name != owner:
                raise ValueError(f"{name}: only {owner} may carry {op_name!r} entity operations")
            check_lifecycle(op)


def _check_value(event_name: str, info: FieldInfo, value: object) -> None:
    if value is None:
        if not info.nullable:
            raise TypeError(f"{event_name}.{info.name} is not nullable")
        return
    if not matches_wire_type(info.wire_type, value):
        raise TypeError(f"{event_name}.{info.name} expects {info.wire_type}, got {type(value).__name__}")
    _check_immutable(f"{event_name}.{info.name}", value)


def _check_immutable(where: str, value: object) -> None:
    """Raise unless `value` is a deep-immutable wire value (ruling R30).

    Scalars (subclasses of ``str``, ``int`` and ``float`` included), tuples and :class:`FrozenMap` with ``str`` keys
    are accepted; lists, dicts, sets and other mutable containers raise `TypeError`, as does anything that is not a
    wire value; integers outside +/-(2**53 - 1) raise `OverflowError`. Calls no user code (subclasses are read through
    the base type's methods).
    """
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        t = type(item)
        if item is None or t is str or t is bool or t is float:
            continue
        if issubclass(t, int):
            freeze(exact_int(item))  # ty: ignore[invalid-argument-type]  # OverflowError outside the safe range
        elif issubclass(t, (str, float)):
            continue
        elif issubclass(t, tuple):
            stack.extend(tuple.__getitem__(item, slice(None)))
        elif issubclass(t, FrozenMap):
            data: dict[Any, object] = item._data  # ty: ignore[unresolved-attribute]
            if not all(type(key) is str for key in data):
                raise TypeError(f"{where}: map keys must be str")
            stack.extend(data.values())
        elif issubclass(t, (MutableMapping, MutableSequence, MutableSet, bytearray)):
            raise TypeError(
                f"{where} holds a mutable {t.__name__}; event contents must be immutable (use a tuple or a FrozenMap)"
            )
        else:
            raise TypeError(f"{where} holds {t.__name__}, which is not a wire value")


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


class _Interest(dict[type, bool]):
    """Whether any subscriber takes each event class: filled on lookup, cleared when the subscriptions change.

    A dict subclass so that a cached answer costs one C-level lookup, without a Python frame: the emission guard
    :attr:`Environment.wants <simulatte.environment.Environment.wants>` is its bound ``__getitem__`` (D56).
    """

    __slots__ = ("_route",)

    def __init__(self, route: Callable[[type], tuple[Handler, ...]]) -> None:
        super().__init__()
        self._route = route

    def __missing__(self, cls: type) -> bool:
        wanted = self[cls] = bool(self._route(cls))
        return wanted


class EventBus:
    """Synchronous event delivery with nested emissions queued FIFO.

    `probe`, when given, is called before and after each handler and returns the number of scheduled SimPy
    events and the number of RNG draws; a change in either means the handler scheduled a SimPy event or
    drew from ``env.rng``, which raises `RuntimeError` (debug mode).
    """

    __slots__ = ("_delivering", "_interest", "_pending", "_probe", "_routes", "_subscriptions")

    def __init__(self, *, probe: Callable[[], tuple[int, int]] | None = None) -> None:
        self._subscriptions: list[Subscription] = []
        self._routes: dict[type, tuple[Handler, ...]] = {}
        self._interest = _Interest(self._route)
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
        self._interest.clear()
        return subscription

    def wants(self, cls: type[Event]) -> bool:
        """Whether any subscriber listens to events of type `cls`."""
        return self._interest[cls]

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
        self._interest.clear()


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
