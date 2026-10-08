"""Entity identity, state schemas and lifecycle.

An *entity* is a component that the trace refers to by id: servers, jobs, pools, shop floors, routers and
user-defined kinds. A class becomes an entity kind by mixing in :class:`Entity` with a ``kind`` and a
:class:`StateSchema`::

    class Widget(Entity, kind="widget"):
        state_schema = StateSchema({"size": FieldSpec("float")})

        def __init__(self, env, *, name=None, label=None):
            self.size = 1.0
            env.entities.attach(self, name=name, label=label)

Every kind's schema also has the presentation field ``label``. Attachment assigns the id (the ``name`` when
given, otherwise ``f"{kind}-{n}"`` with a per-kind counter of the environment) and emits
:class:`EntityCreated`, the only event that carries a ``create`` delta. :meth:`EntityRegistry.retire` emits
:class:`EntityRetired` and drops the registry's strong reference.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from simulatte._wire import FrozenMap, Wire, freeze
from simulatte.events import Deltas, DomainEvent, Op, event_type, matches_wire_type

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment

__all__ = [
    "KINDS",
    "LABEL_FIELD",
    "Entity",
    "EntityCreated",
    "EntityRegistry",
    "EntityRetired",
    "FieldSpec",
    "StateSchema",
    "presentation_of",
]

_WIRE_TYPES = frozenset({"str", "int", "float", "bool", "array", "map", "any"})
_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


# ---------------------------------------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Declaration of one entity state field.

    `wire_type` is one of ``str``, ``int``, ``float``, ``bool``, ``array``, ``map`` or ``any``; for a
    collection field (`collection` ``"list"`` or ``"map"``) it is the type of the items. Presentation fields
    are excluded from the semantic projection.
    """

    wire_type: str
    nullable: bool = False
    collection: Literal["list", "map"] | None = None
    presentation: bool = False

    def __post_init__(self) -> None:
        if self.wire_type not in _WIRE_TYPES:
            raise ValueError(f"unknown wire type {self.wire_type!r}; expected one of {sorted(_WIRE_TYPES)}")
        if self.collection not in (None, "list", "map"):
            raise ValueError(f"collection must be None, 'list' or 'map', got {self.collection!r}")

    def accepts(self, value: object) -> bool:
        """Whether `value` (a wire value) matches this declaration (debug validation)."""
        if value is None:
            return self.nullable
        wire_type = self.wire_type
        if self.collection is None:
            return matches_wire_type(wire_type, value)
        if self.collection == "list":
            return isinstance(value, (tuple, list)) and all(matches_wire_type(wire_type, v) for v in value)
        return isinstance(value, Mapping) and all(matches_wire_type(wire_type, v) for v in value.values())


LABEL_FIELD = FieldSpec("str", presentation=True)
"""The ``label`` field every kind has: display only, excluded from the semantic projection."""


class StateSchema(Mapping[str, FieldSpec]):
    """The state fields of an entity kind, in declaration order.

    Field names are non-empty strings that do not start with ``$`` (``"$kind"`` is reserved in state maps).
    """

    __slots__ = ("_fields", "_presentation")

    def __init__(self, fields: Mapping[str, FieldSpec]) -> None:
        for name, spec in fields.items():
            if not isinstance(name, str) or not name or name.startswith("$"):
                raise ValueError(f"invalid state field name {name!r}: must be a non-empty str not starting with '$'")
            if not isinstance(spec, FieldSpec):
                raise TypeError(f"state field {name!r} must be declared with a FieldSpec, got {type(spec).__name__}")
        self._fields: dict[str, FieldSpec] = dict(fields)
        self._presentation = frozenset(name for name, spec in self._fields.items() if spec.presentation)

    def __getitem__(self, name: str) -> FieldSpec:
        return self._fields[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._fields)

    def __len__(self) -> int:
        return len(self._fields)

    def __repr__(self) -> str:
        return f"StateSchema({self._fields!r})"

    @property
    def presentation(self) -> frozenset[str]:
        """Names of the presentation fields."""
        return self._presentation

    def to_wire(self) -> FrozenMap:
        """The schema as stored in trace headers and catalog extensions: field name to its declaration."""
        return FrozenMap(
            {
                name: FrozenMap(
                    {
                        "type": spec.wire_type,
                        "nullable": spec.nullable,
                        "collection": spec.collection,
                        "presentation": spec.presentation,
                    }
                )
                for name, spec in self._fields.items()
            }
        )

    @classmethod
    def from_wire(cls, wire: Wire) -> StateSchema:
        """Invert :meth:`to_wire`. Raises `TypeError` or `ValueError` for a malformed schema."""
        if not isinstance(wire, Mapping):
            raise TypeError(f"a state schema is a map, got {type(wire).__name__}")
        fields: dict[str, FieldSpec] = {}
        for name, spec in wire.items():
            if not isinstance(spec, Mapping):
                raise TypeError(f"state field {name!r}: a field declaration is a map, got {type(spec).__name__}")
            nullable, presentation = spec["nullable"], spec["presentation"]
            if not isinstance(nullable, bool) or not isinstance(presentation, bool):
                raise TypeError(f"state field {name!r}: nullable and presentation must be booleans")
            fields[name] = FieldSpec(
                spec["type"], nullable, spec["collection"], presentation
            )  # FieldSpec checks type and collection
        return cls(fields)


# ---------------------------------------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------------------------------------

_KINDS: dict[str, StateSchema] = {}
KINDS: Mapping[str, StateSchema] = MappingProxyType(_KINDS)
"""Registered entity kinds and their state schemas (global, read-only view)."""

_PRESENTATION: dict[str, frozenset[str]] = {}
_NO_FIELDS: frozenset[str] = frozenset()


def presentation_of(kind: str) -> frozenset[str]:
    """Presentation state fields of the registered `kind` (empty for an unknown kind)."""
    return _PRESENTATION.get(kind, _NO_FIELDS)


_reserved: re.Pattern[str] | None = None


def _register_kind(kind: object, schema: StateSchema) -> None:
    global _reserved
    if not isinstance(kind, str) or not _KIND_PATTERN.fullmatch(kind):
        raise ValueError(f"invalid entity kind {kind!r}: use lowercase letters, digits and '_'")
    existing = _KINDS.get(kind)
    if existing is not None and existing != schema:
        raise ValueError(f"entity kind {kind!r} is already registered with a different state schema")
    _KINDS[kind] = schema
    _PRESENTATION[kind] = schema.presentation
    _reserved = None


def _reserved_pattern() -> re.Pattern[str]:
    global _reserved
    if _reserved is None:
        kinds = "|".join(re.escape(kind) for kind in sorted(_KINDS))
        _reserved = re.compile(rf"(?:{kinds})-\d+")
    return _reserved


class Entity:
    """Mixin for components with an identity in their environment.

    Subclasses declare ``kind=...`` in the class statement and a ``state_schema`` class attribute, and call
    ``env.entities.attach(self, ...)`` in their constructor once the state fields exist. Subclasses without
    ``kind`` inherit their parent's kind. :meth:`snapshot` returns the current state.
    """

    __slots__ = ()

    kind: ClassVar[str]
    state_schema: ClassVar[StateSchema]
    id: str
    label: str

    def __init_subclass__(cls, kind: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if kind is None:
            return
        schema = cls.__dict__.get("state_schema")
        if not isinstance(schema, StateSchema):
            raise TypeError(f"entity kind {kind!r} ({cls.__name__}) must define state_schema as a StateSchema")
        if "label" not in schema:
            schema = StateSchema({**schema, "label": LABEL_FIELD})
        elif schema["label"] != LABEL_FIELD:
            raise ValueError(f"entity kind {kind!r}: the label field must be {LABEL_FIELD!r}")
        _register_kind(kind, schema)
        cls.kind = kind
        cls.state_schema = schema

    def snapshot(self) -> dict[str, Any]:
        """Current state as a map from each schema field (presentation included) to its value.

        The default reads attributes named after the fields; override it when the state is derived.
        """
        return {name: getattr(self, name) for name in self.state_schema}


# ---------------------------------------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------------------------------------


@event_type("entity.created", presentation=frozenset({"label"}))
class EntityCreated(DomainEvent):
    """An entity was attached; the ``create`` delta carries its initial state."""

    entity: str
    kind: str
    label: str


@event_type("entity.retired")
class EntityRetired(DomainEvent):
    """An entity was retired; the ``retire`` delta removes it."""

    entity: str
    kind: str


# ---------------------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------------------


class EntityRegistry:
    """The entities of one environment: ids, live entities and lifecycle events.

    Ids are unique across kinds. Live entities are held strongly in attachment order; retired ones only
    weakly, and their ids are never reused.
    """

    __slots__ = ("_counters", "_env", "_live", "_names", "_retired", "_retiring")

    def __init__(self, env: Environment) -> None:
        self._env = env
        self._counters: dict[str, int] = {}
        self._live: dict[str, Entity] = {}
        self._names: set[str] = set()
        self._retired: weakref.WeakValueDictionary[str, Entity] = weakref.WeakValueDictionary()
        self._retiring: str | None = None

    def attach(self, obj: Entity, *, name: str | None = None, label: str | None = None) -> str:
        """Register `obj`, set its ``id`` and ``label`` and emit :class:`EntityCreated`; return the id.

        `name` becomes the id; without it the id is ``f"{kind}-{n}"``. Names must be non-empty, must not
        contain ``/`` or NUL and must not look like a generated id of any registered kind. Raises
        `ValueError` for invalid or duplicate ids and for an object that is already attached.
        """
        kind = getattr(type(obj), "kind", None)
        if kind is None:
            raise TypeError(f"{type(obj).__name__} is not an entity kind (subclass Entity with kind=...)")
        live = self._live
        current = getattr(obj, "id", None)
        if current is not None and live.get(current) is obj:
            raise ValueError(f"entity {current!r} is already attached")
        if name is None:
            n = self._counters.get(kind, 0)
            self._counters[kind] = n + 1
            entity_id = f"{kind}-{n}"
            if entity_id in self._names:
                raise ValueError(f"generated id {entity_id!r} is already used as a name")
        else:
            self._check_name(name)
            entity_id = name
            self._names.add(name)
        target: Any = obj  # the slots for id and label are declared by the concrete class
        target.id = entity_id
        target.label = label = entity_id if label is None else label
        live[entity_id] = obj
        env = self._env
        if env.wants(EntityCreated):
            env.emit(
                EntityCreated(
                    entity=entity_id,
                    kind=kind,
                    label=label,
                    deltas=Deltas.build().create(entity_id, kind, obj.snapshot()).done(),
                )
            )
        return entity_id

    def _check_name(self, name: object) -> None:
        if not isinstance(name, str):
            raise TypeError(f"entity name must be a str, got {type(name).__name__}")
        if not name or "/" in name or "\0" in name:
            raise ValueError(f"invalid entity name {name!r}: must be non-empty without '/' or NUL")
        if _reserved_pattern().fullmatch(name):
            raise ValueError(f"entity name {name!r} is reserved: it matches the generated id pattern '<kind>-<n>'")
        if name in self._names:
            raise ValueError(f"entity id {name!r} is already used in this environment")

    def retire(self, obj: Entity) -> None:
        """Drop `obj` from the live registry and emit :class:`EntityRetired`.

        Raises `ValueError` when `obj` is not a live entity of this environment.
        """
        entity_id = getattr(obj, "id", None)
        if entity_id is None or self._live.get(entity_id) is not obj:
            raise ValueError(f"{obj!r} is not live in this environment")
        del self._live[entity_id]
        self._retired[entity_id] = obj
        env = self._env
        if env.wants(EntityRetired):
            self._retiring = entity_id
            try:
                env.emit(EntityRetired(entity=entity_id, kind=obj.kind, deltas=Deltas.build().retire(entity_id).done()))
            finally:
                self._retiring = None

    def get(self, entity_id: str) -> Entity:
        """The entity with `entity_id`: live, or retired and still referenced elsewhere (`KeyError` otherwise)."""
        obj = self._live.get(entity_id)
        if obj is None:
            obj = self._retired[entity_id]
        return obj

    def live(self) -> tuple[Entity, ...]:
        """Live entities in attachment order."""
        return tuple(self._live.values())

    def kind_of(self, entity_id: str) -> str | None:
        """Kind of the live entity `entity_id`, or None."""
        obj = self._live.get(entity_id)
        return None if obj is None else obj.kind

    def snapshot(self, *, include_presentation: bool = True) -> dict[str, dict[str, Wire]]:
        """State of the live entities as wire values, sorted by id; each map has ``"$kind"`` first."""
        result: dict[str, dict[str, Wire]] = {}
        live = self._live
        for entity_id in sorted(live):
            obj = live[entity_id]
            schema = obj.state_schema
            fields: dict[str, Wire] = {"$kind": obj.kind}
            for name, value in obj.snapshot().items():
                if include_presentation or not schema[name].presentation:
                    fields[name] = freeze(value)
            result[entity_id] = fields
        return result

    def check_lifecycle_op(self, op: Op) -> None:
        """Validate a ``create`` against its kind's schema and a ``retire`` against the live entities."""
        if op[0] == "retire":
            if op[1] not in self._live and op[1] != self._retiring:
                raise ValueError(f"retire: entity {op[1]!r} is not live")
            return
        kind, state = op[2], op[3]
        schema = _KINDS.get(kind)
        if schema is None:
            raise ValueError(f"create: unknown entity kind {kind!r}")
        if not isinstance(state, Mapping):
            raise TypeError(f"create: state of {op[1]!r} must be a map, got {type(state).__name__}")
        if set(state) != set(schema):
            raise ValueError(
                f"create: state fields of {kind} {op[1]!r} are {sorted(state)}, the schema declares {sorted(schema)}"
            )
        for name, spec in schema.items():
            if not spec.accepts(state[name]):
                raise TypeError(f"create: {kind}.{name} of {op[1]!r} does not match {spec!r}: {state[name]!r}")
