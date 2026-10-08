"""Semantic projection and digest (spec §9.1, §9.2).

The *semantic projection* of a run is what two runs must share to count as the same trajectory: the
initial state and every domain event with its deltas, without presentation fields (display-only payload
fields and display-only state fields such as ``label``), without ``seq`` and without observer events.
:func:`project_state` and :func:`project_event` encode the two kinds of item with the canonical MessagePack
of :mod:`simulatte._wire`; :class:`SemanticDigest` feeds them to a BLAKE2b hash.
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from simulatte._wire import Wire, canonical_pack
from simulatte.entities import KINDS
from simulatte.events import DomainEvent, Op

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment

__all__ = ["Fingerprint", "SemanticDigest", "project_event", "project_state"]

_BASE_FIELDS = frozenset({"t", "seq", "deltas", "ordinal"})
_PAYLOAD_FIELDS: dict[type, tuple[str, ...]] = {}
_U64 = struct.Struct(">Q")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Comparable summary of a run: the semantic digest (None when none is enabled) and the KPI scalars."""

    digest: str | None
    kpis: dict[str, float]


def project_state(state: Mapping[str, Mapping[str, Wire]]) -> bytes:
    """Canonical bytes of an activation snapshot without presentation fields, entities sorted by id.

    `state` maps entity ids to their field maps, each with the ``"$kind"`` entry (see
    :meth:`EntityRegistry.snapshot`); ``"$kind"`` is semantic and stays.
    """
    projected: Any = {}
    for entity_id in sorted(state):
        fields = state[entity_id]
        presentation = _presentation_of(str(fields["$kind"]))
        projected[entity_id] = {name: value for name, value in fields.items() if name not in presentation}
    return canonical_pack(projected)


def project_event(event: DomainEvent, kinds: MutableMapping[str, str] | None = None) -> bytes:
    """Canonical bytes of ``[ordinal, type, version, t, payload, deltas]`` for a domain event.

    Presentation payload fields are removed, and so are the delta operations on presentation state fields
    (inside a ``create`` the presentation fields are removed from the state). `seq` is not part of it.

    `kinds` maps live entity ids to their kind and resolves the kind of the entity a field operation
    addresses; it is updated by ``create`` and ``retire`` operations, in order. Without it, a field
    operation on an entity not created by the same event counts as presentation when the field is
    presentation in every registered kind that declares it.
    """
    cls = type(event)
    names = _PAYLOAD_FIELDS.get(cls)
    if names is None:
        skip = _BASE_FIELDS | cls.presentation_fields
        names = _PAYLOAD_FIELDS[cls] = tuple(f.name for f in dataclasses.fields(cls) if f.name not in skip)
    payload = {name: getattr(event, name) for name in names}
    resolved = {} if kinds is None else kinds
    ops: list[Any] = []
    for op in event.deltas.ops:
        kept = _project_op(op, resolved, strict=kinds is not None)
        if kept is not None:
            ops.append(kept)
    item: Any = (event.ordinal, cls.type_name, cls.type_version, float(event.t), payload, tuple(ops))
    return canonical_pack(item)


def _project_op(op: Op, kinds: MutableMapping[str, str], *, strict: bool) -> Op | None:
    name = op[0]
    if name == "create":
        kinds[op[1]] = op[2]
        presentation = _presentation_of(op[2])
        return (name, op[1], op[2], {k: v for k, v in op[3].items() if k not in presentation})
    if name == "retire":
        kinds.pop(op[1], None)
        return op
    kind = kinds.get(op[1])
    if kind is None:
        return None if not strict and _presentation_everywhere(op[2]) else op
    return None if op[2] in _presentation_of(kind) else op


def _presentation_of(kind: str) -> frozenset[str]:
    schema = KINDS.get(kind)
    return frozenset() if schema is None else schema.presentation


def _presentation_everywhere(field: str) -> bool:
    specs = [schema[field] for schema in KINDS.values() if field in schema]
    return bool(specs) and all(spec.presentation for spec in specs)


class SemanticDigest:
    """BLAKE2b-256 digest of the semantic projection of a run.

    It is fed with the initial-state projection at activation and then with the projection of every domain
    event, each item prefixed by its byte length as a big-endian u64. Create it with :meth:`attach`, before
    activation.
    """

    __slots__ = ("_hash", "_kinds", "_started")

    def __init__(self) -> None:
        self._hash = hashlib.blake2b(digest_size=32)
        self._kinds: dict[str, str] = {}
        self._started = False

    @classmethod
    def attach(cls, env: Environment) -> SemanticDigest:
        """Request the projection of `env` and subscribe a new digest to its domain events.

        Raises `RuntimeError` after activation: the initial state and the ordinals of earlier events are
        already fixed, so the digest could not cover the whole run.
        """
        if env.activated:
            raise RuntimeError(
                "cannot attach a SemanticDigest after activation: it must see the initial state and every domain "
                "event; attach it before the first env.run() or env.activate()"
            )
        digest = cls()
        env.request_projection(digest._on_initial_state)
        env.bus.subscribe(digest._on_event, "*")
        return digest

    def hexdigest(self) -> str:
        """Hex digest of everything fed so far."""
        return self._hash.hexdigest()

    def _feed(self, item: bytes) -> None:
        self._hash.update(_U64.pack(len(item)))
        self._hash.update(item)

    def _on_initial_state(self, state: Mapping[str, Mapping[str, Wire]]) -> None:
        self._kinds = {entity_id: str(fields["$kind"]) for entity_id, fields in state.items()}
        self._feed(project_state(state))
        self._started = True

    def _on_event(self, event: DomainEvent) -> None:
        if self._started:  # events of the prelude (before the snapshot) are not part of the projection
            self._feed(project_event(event, self._kinds))
