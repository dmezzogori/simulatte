"""Semantic projection and digest (spec §9.1, §9.2).

The *semantic projection* of a run is what two runs must share to count as the same trajectory: the
initial state and every domain event with its deltas, without presentation fields (display-only payload
fields and display-only state fields such as ``label``), without ``seq`` and without observer events.
:func:`project_state` and :func:`project_event` encode the two kinds of item with the canonical MessagePack
of :mod:`simulatte._wire`; :class:`DigestAccumulator` feeds them to a BLAKE2b hash, and :class:`SemanticDigest`
is the bus subscriber that does it during a run (a trace reader uses the accumulator to verify a recorded run).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from simulatte._wire import FrozenMap, Wire, canonical_pack, new_packer, prepared, prepared_op, wire_time
from simulatte.entities import KINDS
from simulatte.entities import presentation_of as _registered_presentation
from simulatte.events import DomainEvent, Op

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment

__all__ = [
    "DigestAccumulator",
    "Fingerprint",
    "SemanticDigest",
    "project_event",
    "project_event_parts",
    "project_state",
]

_U64 = struct.Struct(">Q")
_LIFECYCLE = frozenset({"create", "retire"})


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Comparable summary of a run: the semantic digest (None when none is enabled) and the KPI scalars.

    `kpis` maps ``"<scope id>/<kpi name>"`` to the scalar, so two systems sharing an environment never merge
    their results (spec §12.1). ``kpi.sample`` events are observer events: they never enter the digest.
    """

    digest: str | None
    kpis: dict[str, float]


def project_state(
    state: Mapping[str, Mapping[str, Wire]], presentation_of: Callable[[str], frozenset[str]] | None = None
) -> bytes:
    """Canonical bytes of an activation snapshot without presentation fields, entities sorted by id.

    `state` maps entity ids to their field maps, each with the ``"$kind"`` entry (see
    :meth:`EntityRegistry.snapshot`); ``"$kind"`` is semantic and stays. `presentation_of` returns the
    presentation fields of a kind; it defaults to the registered kinds (a trace reader passes the kinds
    stored in the trace).
    """
    presentation_of = _registered_presentation if presentation_of is None else presentation_of
    projected: Any = {}
    for entity_id in sorted(state):
        fields = state[entity_id]
        presentation = presentation_of(str(fields["$kind"]))
        projected[entity_id] = {name: value for name, value in fields.items() if name not in presentation}
    return canonical_pack(projected)


def project_event(event: DomainEvent, kinds: MutableMapping[str, str] | None = None) -> bytes:
    """Canonical bytes of ``[ordinal, type, version, t, payload, deltas]`` for a domain event.

    Presentation payload fields are removed, and so are the delta operations on presentation state fields
    (inside a ``create`` the presentation fields are removed from the state). `seq` is not part of it.

    `kinds` maps live entity ids to their kind and resolves the kind of the entity a field operation
    addresses; it is updated by ``create`` and ``retire`` operations, in order. Without it, a field
    operation on an entity not created by the same event counts as presentation when the field is
    presentation in every registered kind that declares it. Delegates to :func:`project_event_parts`.
    """
    cls = type(event)
    return project_event_parts(
        event.ordinal,  # ty: ignore[invalid-argument-type]  # None before activation; encoded as nil
        cls.type_name,
        cls.type_version,
        event.t,
        {name: getattr(event, name) for name in cls.payload_fields},
        event.deltas.ops,
        payload_presentation=cls.presentation_fields,
        kinds=kinds,
    )


def project_event_parts(
    ordinal: int,
    type_name: str,
    version: int,
    t: float,
    payload: Mapping[str, Wire],
    ops: tuple[Op, ...],
    *,
    payload_presentation: frozenset[str] = frozenset(),
    presentation_of: Callable[[str], frozenset[str]] | None = None,
    kinds: MutableMapping[str, str] | None = None,
) -> bytes:
    """:func:`project_event` for an event given as parts, such as one read back from a trace.

    `payload` holds every payload field; those in `payload_presentation` are removed. `presentation_of` returns
    the presentation state fields of a kind (default: the registered kinds) and `kinds` maps live entity ids to
    their kind, updated by ``create`` and ``retire`` operations as in :func:`project_event`; without `kinds`
    the kind of an entity not created by the same event is inferred as described there.
    """
    kept_payload = {name: value for name, value in payload.items() if name not in payload_presentation}
    projected, _ = _project_ops(
        ops,
        {} if kinds is None else kinds,
        _registered_presentation if presentation_of is None else presentation_of,
        strict=kinds is not None,
    )
    item: Any = (ordinal, type_name, version, wire_time(t), kept_payload, tuple(projected))
    return canonical_pack(item)


def _project_ops(
    ops: tuple[Op, ...],
    kinds: MutableMapping[str, str],
    presentation_of: Callable[[str], frozenset[str]],
    *,
    strict: bool,
) -> tuple[list[Op], bool]:
    """The projected operations, and whether they are the operations themselves (none removed or changed)."""
    projected: list[Op] = []
    unchanged = True
    for op in ops:
        kept = _project_op(op, kinds, presentation_of, strict=strict)
        if kept is not op:
            unchanged = False
        if kept is not None:
            projected.append(kept)
    return projected, unchanged


def _project_op(
    op: Op, kinds: MutableMapping[str, str], presentation_of: Callable[[str], frozenset[str]], *, strict: bool
) -> Op | None:
    name = op[0]
    if name == "create":
        kinds[op[1]] = op[2]
        presentation = presentation_of(op[2])
        state = op[3]
        if isinstance(state, FrozenMap):  # stays canonical when it is
            return (name, op[1], op[2], state.without(presentation))
        return (name, op[1], op[2], {k: v for k, v in state.items() if k not in presentation})
    if name == "retire":
        kinds.pop(op[1], None)
        return op
    kind = kinds.get(op[1])
    if kind is None:
        return None if not strict and _presentation_everywhere(op[2]) else op
    return None if op[2] in presentation_of(kind) else op


def _presentation_everywhere(field: str) -> bool:
    specs = [schema[field] for schema in KINDS.values() if field in schema]
    return bool(specs) and all(spec.presentation for spec in specs)


class DigestAccumulator:
    """BLAKE2b-256 of a semantic projection (spec §9.2), fed one item at a time.

    Every item is prefixed by its byte length as a big-endian u64. :attr:`kinds` maps the live entity ids to
    their kind: :meth:`feed_state` sets it from the ``"$kind"`` entries of the initial state, and every
    ``create`` and ``retire`` of the events fed after it updates it, in order. This rolled map is authoritative
    for the projection: whether a field operation is presentation is decided by the kind this map gives the
    addressed entity, never by a live registry or by kinds registered later. :class:`SemanticDigest` feeds it
    during a run and :meth:`simulatte.trace.Trace.verify` from a recorded trace, so both frame items the same
    way.
    """

    __slots__ = ("_hash", "kinds")

    def __init__(self) -> None:
        self._hash = hashlib.blake2b(digest_size=32)
        self.kinds: dict[str, str] = {}

    def feed_state(
        self,
        state: Mapping[str, Mapping[str, Wire]],
        presentation_of: Callable[[str], frozenset[str]] | None = None,
    ) -> None:
        """Feed the projection of the initial state (see :func:`project_state`) and reset :attr:`kinds` from it."""
        self.kinds = {entity_id: str(fields["$kind"]) for entity_id, fields in state.items()}
        self.feed(project_state(state, presentation_of))

    def feed_event_parts(
        self,
        ordinal: int,
        type_name: str,
        version: int,
        t: float,
        payload: Mapping[str, Wire],
        ops: tuple[Op, ...],
        *,
        payload_presentation: frozenset[str] = frozenset(),
        presentation_of: Callable[[str], frozenset[str]] | None = None,
    ) -> None:
        """Feed the projection of an event given as parts (see :func:`project_event_parts`), resolving and
        updating :attr:`kinds`."""
        self.feed(
            project_event_parts(
                ordinal,
                type_name,
                version,
                t,
                payload,
                ops,
                payload_presentation=payload_presentation,
                presentation_of=presentation_of,
                kinds=self.kinds,
            )
        )

    def feed(self, item: bytes) -> None:
        """Feed one encoded item, prefixed by its length."""
        update = self._hash.update
        update(_U64.pack(len(item)))
        update(item)

    def hexdigest(self) -> str:
        """Hex digest of everything fed so far."""
        return self._hash.hexdigest()


class SemanticDigest:
    """BLAKE2b-256 digest of the semantic projection of a run.

    It is fed with the initial-state projection at activation and then with the projection of every domain
    event (see :class:`DigestAccumulator`). Create it with :meth:`attach`, before activation.

    Events are encoded on a fast path that packs the precomputed canonical field order of each event class
    (:attr:`~simulatte.events.Event.wire_semantic`) and frozen values directly; it yields the bytes of
    :func:`project_event` with the rolled kind map. When an event has no presentation payload field and the
    projection keeps its operations unchanged, the encoded ``payload, deltas`` tail equals what a trace recorder
    stores for the event, and :meth:`shared_tail` hands it over so ``full`` recording encodes each event once.
    """

    __slots__ = ("_acc", "_heads", "_last", "_packer", "_share", "_started")

    def __init__(self) -> None:
        self._acc = DigestAccumulator()
        self._started = False
        self._packer = new_packer()  # simulation thread only
        self._heads: dict[type, int] = {}  # event class -> encoded length of [type, version] plus the array header
        self._share = False  # set by share_tails(): keep the tail of each projection for a recorder
        self._last: tuple[DomainEvent, bytes] | None = None

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
        return self._acc.hexdigest()

    def share_tails(self) -> None:
        """Keep the encoded ``payload, deltas`` tail of each event for :meth:`shared_tail` (trace recorders)."""
        self._share = True

    def shared_tail(self, event: DomainEvent) -> bytes | None:
        """The canonical ``payload, deltas`` tail of the projection of `event`, if a recorder can store it as is.

        It is available after :meth:`share_tails`, only for the event this digest projected last, when that event
        has no presentation payload field and its operations, a tuple, are already immutable wire values that the
        projection kept unchanged (so a recorder may keep them, ruling R30); otherwise None.
        """
        last = self._last
        if last is not None and last[0] is event:
            return last[1]
        return None

    def _on_initial_state(self, state: Mapping[str, Mapping[str, Wire]]) -> None:
        self._acc.feed_state(state)
        self._started = True

    def _on_event(self, event: DomainEvent) -> None:
        """Project `event` and feed it: :func:`project_event` with the rolled kind map, on a fused fast path."""
        if not self._started:  # events of the prelude (before the snapshot) are not part of the projection
            return
        cls = type(event)
        payload: dict[str, Any] = {}
        for key, name in cls.wire_semantic:
            value = getattr(event, name)
            t = type(value)
            payload[key] = value if t is str or (t is float and value == value) or value is None else prepared(value)
        kinds = self._acc.kinds
        ops: list[Any] = []
        event_ops = event.deltas.ops
        # Shared only when the recorder can keep the operations themselves: a tuple of tuples that are already
        # immutable wire values (prepared_op returns those unchanged), which nobody can change after emit (R30).
        unchanged = type(event_ops) is tuple
        for op in event_ops:
            if type(op) is not tuple:
                unchanged = False
            if op[0] in _LIFECYCLE:
                kept = _project_op(op, kinds, _registered_presentation, strict=True)
                ready = prepared_op(kept)  # ty: ignore[invalid-argument-type]  # lifecycle ops are always kept
                if ready is not op:
                    unchanged = False
                ops.append(ready)
                continue
            kind = kinds.get(op[1])
            if kind is not None and op[2] in _registered_presentation(kind):
                unchanged = False
                continue
            ready = prepared_op(op)
            if ready is not op:
                unchanged = False
            ops.append(ready)
        item = self._packer.pack((event.ordinal, cls.type_name, cls.type_version, wire_time(event.t), payload, ops))
        update = self._acc._hash.update
        update(_U64.pack(len(item)))
        update(item)
        if self._share:
            if unchanged and not cls.presentation_fields:
                self._last = (event, item[self._tail_offset(cls, event.ordinal) :])
            else:
                self._last = None

    def _tail_offset(self, cls: type[DomainEvent], ordinal: int | None) -> int:
        """Where ``payload`` starts in the encoded projection ``[ordinal, type, version, t, payload, deltas]``."""
        head = self._heads.get(cls)
        if head is None:
            pack = self._packer.pack
            head = self._heads[cls] = 1 + len(pack(cls.type_name)) + len(pack(cls.type_version))
        if ordinal is None or 0 <= ordinal < 0x80:
            size = 1
        else:
            size = len(self._packer.pack(ordinal))
        return head + size + 9  # t is always a float64
