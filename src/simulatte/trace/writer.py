"""Trace writer: :class:`TraceRecorder` (spec §11.2).

The recorder writes one trace file per environment. Two threads share the work:

- The **simulation thread** encodes each domain event as ``[seq, ordinal, type, t, payload, deltas]`` and
  appends it to the open buffer. Payload maps and deltas use the canonical encoding; when the semantic digest
  already encoded an event's ``payload, deltas`` tail (no presentation field involved), that tail is reused, so
  each event is encoded once. It seals the buffer itself when the event-count, byte or simulated-time
  limit of :class:`ChunkLimits` is reached. Before activation the buffer becomes a ``PRELUDE`` record, after
  it a ``CHUNK``.
- ``kpi.sample`` events are encoded as ``[seq, t, "<scope id>/<kpi>", value]`` and published in ``KPI``
  records of ``{"samples": [...]}`` after the initial state, each time the event-count or byte limit is reached
  and when the recorder closes; the scalars follow in a last ``KPI`` record ``{"scalars": {...}}``.
- The **writer thread** seals the open buffer when it has been open longer than the latency limit, whatever
  the simulation does, and serves the single FIFO publication queue: it writes every record of the file
  (header, prelude, initial state, catalog extensions, each chunk followed by its committing index, KPI,
  footer) in queue order. It builds each chunk's start snapshot from its own replay state (the initial state
  plus the deltas of earlier chunks), never from live simulation objects.

Pending queued bytes are bounded by ``max_pending_bytes``: the simulation thread blocks until the writer
drains, and a single batch larger than the bound waits until the queue is empty. The first block is logged as a
warning; when there were more, :meth:`TraceRecorder.close` logs their count and total blocked time. An
exception in the writer thread is latched and re-raised in the simulation thread at its next append and by
:meth:`close`; the trace then ends without a footer.
"""

from __future__ import annotations

import dataclasses
import math
import threading
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import msgpack

from simulatte._wire import FrozenMap, freeze, new_packer, pack, prepared, prepared_op, wire_time
from simulatte.entities import KINDS
from simulatte.events import CATALOG, Deltas, DomainEvent, KpiSample, Op, Subscription, apply_deltas
from simulatte.trace.format import (
    OPTIONAL_FEATURES,
    REQUIRED_FEATURES,
    RecordType,
    write_preamble,
    write_record,
    write_trailer,
)

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment, InitialState

__all__ = ["ChunkLimits", "Outcome", "TraceRecorder"]

Outcome = Literal["completed", "cancelled", "failed"]
"""How a recorded run ended (stored in the footer)."""

ACTIVATION_MANIFEST_FIELDS: frozenset[str] = frozenset({"parameters", "time_unit", "warmup"})
"""Requested-manifest fields fixed only at activation: stored in ``INITIAL``, the rest in ``HEADER`` (U1)."""

_OUTCOMES = frozenset({"completed", "cancelled", "failed"})
_ENTRY_HEADER = b"\x96"  # MessagePack fixarray of 6: [seq, ordinal, type, t, payload, deltas]


@dataclass(frozen=True, slots=True)
class ChunkLimits:
    """When the open buffer of events is sealed into a record.

    A buffer is sealed when it holds `max_events` events or `max_bytes` encoded bytes (a single larger event
    makes a chunk of its own), when it has been open for `max_latency_s` seconds of the recorder clock, or,
    when `max_sim_window` is set, before an event whose simulated time is that far from the buffer's first
    event. An event above `max_event_bytes` is recorded with a warning, or raises in debug mode.
    """

    max_events: int = 10_000
    max_bytes: int = 1 << 20
    max_latency_s: float = 1.0
    max_event_bytes: int = 256 << 10
    max_sim_window: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_events", "max_bytes", "max_event_bytes"):
            if getattr(self, name) < 1:
                raise ValueError(f"ChunkLimits.{name} must be >= 1, got {getattr(self, name)}")
        if not self.max_latency_s > 0:
            raise ValueError(f"ChunkLimits.max_latency_s must be > 0, got {self.max_latency_s}")
        if self.max_sim_window is not None and not self.max_sim_window > 0:
            raise ValueError(f"ChunkLimits.max_sim_window must be > 0 or None, got {self.max_sim_window}")

    def to_wire(self) -> FrozenMap:
        return FrozenMap(
            {
                "max_events": self.max_events,
                "max_bytes": self.max_bytes,
                "max_latency_s": float(self.max_latency_s),
                "max_event_bytes": self.max_event_bytes,
                "max_sim_window": None if self.max_sim_window is None else float(self.max_sim_window),
            }
        )


@dataclass(slots=True)
class _Batch:
    """A sealed buffer: encoded events and, for chunks, their deltas for the writer's replay state."""

    rtype: RecordType
    entries: list[bytes]
    deltas: list[Deltas]
    first: tuple[float, int]
    last: tuple[float, int]
    epoch: int
    size: int


def _frozen_op(op: Any) -> Op:
    """An immutable copy of a delta operation, equal to its canonical encoding."""
    return cast("Op", freeze(op))


# Queue items: (kind, pending size, data). Kinds: "header", "record", "ext", "initial", "batch", "footer".
_Item = tuple[str, int, Any]


class TraceRecorder:
    """Record the run of `env` to a trace file at `path` (spec §11).

    Attach it before activation (it raises `RuntimeError` afterwards). It enables the semantic digest of
    `env`, writes the header immediately, records prelude events and, at activation, the initial state. At
    ``level="full"`` it records every domain event in chunks; at ``level="kpi"`` only the header, the initial
    state, KPI records and the footer. At both levels it records ``kpi.sample`` events (the KPI series) in
    ``KPI`` records. Several :meth:`Environment.run` calls continue the same trace.

    :meth:`close` (also called by :meth:`Environment.close`) seals the open chunk, writes the remaining KPI
    samples, the KPI scalars and the footer, and waits until everything is on disk. `clock` is the time source
    of the latency limit and of the blocked time reported when backpressure stops the simulation.
    """

    def __init__(
        self,
        env: Environment,
        path: str | Path,
        *,
        level: Literal["full", "kpi"] = "full",
        chunk_limits: ChunkLimits | None = None,
        max_pending_bytes: int = 64 << 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if env.activated:
            raise RuntimeError(
                "cannot attach a TraceRecorder after activation: the trace must hold the initial state and every "
                "domain event; create it before the first env.run() or env.activate()"
            )
        if level not in ("full", "kpi"):
            raise ValueError(f"level must be 'full' or 'kpi', got {level!r}")
        if max_pending_bytes < 1:
            raise ValueError(f"max_pending_bytes must be >= 1, got {max_pending_bytes}")
        limits = ChunkLimits() if chunk_limits is None else chunk_limits
        self._env = env
        self._level = level
        self._limits = limits
        self._max_pending = max_pending_bytes
        self._clock = clock

        # Catalog known to readers so far (simulation thread).
        self._known_types: set[str] = set(CATALOG.names())
        self._known_kinds: set[str] = set(KINDS)
        self._next_epoch = 1
        requested = env.manifest().requested
        header: Any = {
            "features": {"required": REQUIRED_FEATURES, "optional": OPTIONAL_FEATURES},
            "catalog": CATALOG.to_wire(),
            "kinds": {kind: KINDS[kind].to_wire() for kind in sorted(self._known_kinds)},
            "manifest": {k: v for k, v in requested.items() if k not in ACTIVATION_MANIFEST_FIELDS},
            "level": level,
            "chunk_limits": limits.to_wire(),
            "volatile": dataclasses.asdict(env.volatile_metadata()),
        }
        header_payload = pack(header)

        # Shared state, guarded by _cond.
        self._cond = threading.Condition()
        self._queue: deque[_Item] = deque()
        self._pending = 0
        self._peak_pending = 0
        self._producer_waiting = False
        self._stop = False
        self._error: BaseException | None = None
        self._epoch = 0
        self._chunks_written = 0
        self._buf_type = RecordType.PRELUDE
        self._entries: list[bytes] = []
        self._deltas: list[Deltas] = []
        self._buf_bytes = 0
        self._first: tuple[float, int] = (0.0, -1)
        self._last: tuple[float, int] = (0.0, -1)
        self._opened_at = 0.0

        self._samples: list[bytes] = []
        self._sample_bytes = 0
        self._samples_opened_at = 0.0
        self._samples_ready = False  # publish only after INITIAL has been queued

        # Simulation thread only.
        self._closed = False
        self._known_collectors = 0
        self._last_seq = -1
        self._last_t = 0.0  # time of the last recorded chunk event (or of activation)
        self._subscription: Subscription | None = None
        self._event_packer = new_packer()
        self._blocked_count = 0  # backpressure episodes: the first is logged, all are summarized at close()
        self._blocked_total = 0.0

        # Writer thread only.
        self._offset = 0
        self._index: list[FrozenMap] = []
        self._epochs: list[int] = []
        self._replay: dict[str, dict[str, Any]] = {}
        self._packer = msgpack.Packer()

        self._file = open(path, "wb")  # closed by close() once the writer thread has finished
        self._thread = threading.Thread(target=self._run, name="simulatte-trace-writer", daemon=True)
        self._thread.start()
        self._enqueue("header", len(header_payload), header_payload)

        self._digest = env.enable_digest()  # subscribed before this recorder, so it projects each event first
        self._digest.share_tails()
        env.request_projection(self._on_initial_state)
        if level == "full":
            self._subscription = env.bus.subscribe(self._on_event, "*")
        self._sample_subscription = env.bus.subscribe(self._on_sample, (KpiSample,))
        env._recorders.append(self)

    # -------------------------------------------------------------------------
    # Simulation thread
    # -------------------------------------------------------------------------

    def _on_event(self, event: DomainEvent) -> None:
        error = self._error  # read without the lock: a writer error set meanwhile is raised by the next event
        if error is not None:
            raise error
        cls = type(event)
        name = cls.type_name
        deltas = event.deltas
        t = event.t
        if type(t) is not float or t != t:
            t = wire_time(t)
        seq = event.seq
        pack_event = self._event_packer.pack
        tail = self._digest.shared_tail(event)
        if tail is None:
            payload = {key: prepared(getattr(event, n)) for key, n in cls.wire_payload}
            ops = deltas.ops
            prepared_ops = tuple(map(prepared_op, ops))
            tail = pack_event((payload, prepared_ops))[1:]
            if type(ops) is not tuple or any(
                p is not op or type(op) is not tuple for p, op in zip(prepared_ops, ops, strict=True)
            ):
                # Not all immutable already: replay an immutable copy of what was just encoded, never the caller's
                # objects, which may change before the writer thread applies them (R30).
                deltas = Deltas(tuple(map(_frozen_op, ops)))
        entry = _ENTRY_HEADER + pack_event((seq, event.ordinal, name, t))[1:] + tail
        size = len(entry)
        limits = self._limits
        if size > limits.max_event_bytes:
            self._oversized(name, seq, size)

        # An exception while waiting for backpressure (for example KeyboardInterrupt) still queues the item
        # (see _enqueue); the event is then recorded too, so the trace stays consistent, and it is re-raised.
        interrupted: BaseException | None = None
        try:
            if name not in self._known_types:
                self._extend_catalog(types=(name,))
            if name == "entity.created":
                new_kinds = {op[2] for op in deltas.ops if op[0] == "create" and op[2] not in self._known_kinds}
                if new_kinds:
                    self._extend_catalog(kinds=new_kinds)
            window = limits.max_sim_window
            # Read without the lock; benign, since the seal below re-checks the buffer under it.
            if self._entries and (
                self._buf_bytes + size > limits.max_bytes or (window is not None and t - self._first[0] >= window)
            ):
                # Publish the full buffer before opening the next one, so the writer never sees them out of order.
                with self._cond:
                    batch = self._seal_locked() if self._entries else None
                if batch is not None:  # pragma: no branch - None only if the writer sealed it first (latency)
                    self._enqueue("batch", batch.size, batch)
        except BaseException as exc:
            interrupted = exc
        sealed = None
        with self._cond:
            entries = self._entries
            if not entries:
                self._first = (t, seq)
                self._opened_at = self._clock()
                self._cond.notify_all()  # the writer starts timing the latency limit
            entries.append(entry)
            self._deltas.append(deltas)
            self._buf_bytes += size
            self._last = (t, seq)
            if len(entries) >= limits.max_events or self._buf_bytes >= limits.max_bytes:
                sealed = self._seal_locked()
        if self._buf_type is RecordType.CHUNK:
            self._last_seq = seq
            self._last_t = t
        if sealed is not None:
            self._enqueue("batch", sealed.size, sealed)
        if interrupted is not None:
            raise interrupted

    def _on_sample(self, event: KpiSample) -> None:
        """Buffer a KPI sample; publish on the count, byte or wall-clock latency limit."""
        error = self._error  # read without the lock, as in _on_event
        if error is not None:
            raise error
        if self._samples_ready:
            self._sync_kpi_declarations()
        entry = pack((event.seq, wire_time(event.t), f"{event.scope}/{event.kpi}", event.value))
        with self._cond:
            if not self._samples:
                self._samples_opened_at = self._clock()
                self._cond.notify_all()
            self._samples.append(entry)
            self._sample_bytes += len(entry)
            limits = self._limits
            flush = self._samples_ready and (
                len(self._samples) >= limits.max_events or self._sample_bytes >= limits.max_bytes
            )
        if flush:
            self._flush_samples()

    def _sync_kpi_declarations(self) -> None:
        collectors = self._env._collectors
        if self._known_collectors == len(collectors):
            return
        declarations: Any = {
            f"{collector._scope_id}/{name}": dataclasses.asdict(kpi)
            for collector in collectors[self._known_collectors :]
            for name, kpi in collector._declared.items()
        }
        self._known_collectors = len(collectors)
        if declarations:
            record: Any = {"declarations": declarations}
            payload = pack(record)
            self._enqueue("record", len(payload), (RecordType.KPI, payload))

    def _seal_samples_locked(self) -> bytes:
        samples, self._samples = self._samples, []
        self._sample_bytes = 0
        # Each thread needs its own MessagePack packer.
        return b"".join([b"\x81", pack("samples"), new_packer().pack_array_header(len(samples)), *samples])

    def _flush_samples(self) -> None:
        """Publish the buffered KPI samples as a ``KPI`` record ``{"samples": [...]}``."""
        with self._cond:
            if not self._samples:
                return
            payload = self._seal_samples_locked()
        self._enqueue("record", len(payload), (RecordType.KPI, payload))

    def _oversized(self, name: str, seq: int, size: int) -> None:
        message = (
            f"event {name} (seq {seq}) encodes to {size} bytes, above ChunkLimits.max_event_bytes="
            f"{self._limits.max_event_bytes}"
        )
        if self._env.debug_mode:
            raise ValueError(message)
        self._env.warning(f"{message}; recorded anyway", component="TraceRecorder")

    def _extend_catalog(self, *, types: Iterable[str] = (), kinds: Iterable[str] = ()) -> None:
        """Enqueue a ``CATALOG_EXT`` record for event types or kinds absent from the header."""
        new_types = sorted(types)
        new_kinds = sorted(kinds)
        epoch = self._next_epoch
        self._next_epoch += 1
        self._known_types.update(new_types)
        self._known_kinds.update(new_kinds)
        ext: Any = {
            "epoch": epoch,
            "types": {name: CATALOG.get(name).to_wire() for name in new_types},
            "kinds": {kind: KINDS[kind].to_wire() for kind in new_kinds},
        }
        payload = pack(ext)
        self._enqueue("ext", len(payload), (epoch, payload))

    def _on_initial_state(self, state: InitialState) -> None:
        """Activation: publish the prelude, then the ``INITIAL`` record; later events go to chunks."""
        if self._closed:
            return
        with self._cond:
            batch = self._seal_locked() if self._entries else None
            self._buf_type = RecordType.CHUNK
        if batch is not None:
            self._enqueue("batch", batch.size, batch)
        new_kinds = {str(fields["$kind"]) for fields in state.values()} - self._known_kinds
        if new_kinds:
            self._extend_catalog(kinds=new_kinds)
        env = self._env
        requested = env.manifest().requested
        replay = {entity: dict(fields) for entity, fields in state.items()}  # plain dicts of the frozen snapshot
        self._last_t = activation = wire_time(env.now)
        initial: Any = {
            "cursor": (activation, -1),
            "state": replay,
            "manifest": {k: v for k, v in requested.items() if k in ACTIVATION_MANIFEST_FIELDS},
        }
        payload = pack(initial)
        self._enqueue("initial", len(payload), (payload, replay))
        self._sync_kpi_declarations()
        with self._cond:
            self._samples_ready = True
            self._cond.notify_all()

    def _seal_locked(self) -> _Batch:
        """Take the open buffer as a batch (the caller holds the lock and checked it is not empty)."""
        batch = _Batch(
            rtype=self._buf_type,
            entries=self._entries,
            deltas=self._deltas,
            first=self._first,
            last=self._last,
            epoch=self._epoch,
            size=self._buf_bytes,
        )
        self._entries = []
        self._deltas = []
        self._buf_bytes = 0
        return batch

    def _enqueue(self, kind: str, size: int, data: Any) -> None:
        """Append an item to the publication queue, blocking while pending bytes would exceed the bound.

        An item that does not fit even in an empty queue is admitted once the queue has drained. Raises the
        latched writer exception. Any other exception raised while waiting (for example KeyboardInterrupt)
        is re-raised after the item was queued anyway, overshooting the bound once, so that what the
        simulation thread already committed to (sealed events, catalog extensions) reaches the file (R10).
        """
        cond = self._cond
        blocked_since: float | None = None
        with cond:
            try:
                while True:
                    error = self._error
                    if error is not None:
                        raise error
                    pending = self._pending
                    if pending == 0 or pending + size <= self._max_pending:
                        break
                    if blocked_since is None:
                        blocked_since = self._clock()
                    self._producer_waiting = True
                    cond.notify_all()
                    cond.wait()
            except BaseException as exc:
                self._producer_waiting = False
                if exc is not self._error:
                    self._admit_locked(kind, size, data)
                raise
            self._producer_waiting = False
            self._admit_locked(kind, size, data)
        if blocked_since is not None:
            blocked = self._clock() - blocked_since
            self._blocked_count += 1
            self._blocked_total += blocked
            if self._blocked_count == 1:  # later episodes are summarized by close(), not logged one by one
                self._env.warning(
                    f"trace writer backpressure: the simulation blocked for {blocked:.3f} s "
                    f"(max_pending_bytes={self._max_pending}); further blocks are summarized when the recorder "
                    "closes",
                    component="TraceRecorder",
                    blocked_s=blocked,
                )

    def _admit_locked(self, kind: str, size: int, data: Any) -> None:
        self._queue.append((kind, size, data))
        self._pending = pending = self._pending + size
        if pending > self._peak_pending:
            self._peak_pending = pending
        if kind == "ext":
            self._epoch = data[0]
        self._cond.notify_all()

    def close(self, outcome: Outcome | None = None) -> None:
        """Seal the open chunk, write the KPI scalars and the footer, and wait until all is on disk.

        `outcome` defaults to ``"failed"`` if :meth:`Environment.run` raised, ``"cancelled"`` if a run was
        interrupted, else ``"completed"``. Raises the latched writer exception, if any; the trace then has no
        footer. Repeated calls do nothing.
        """
        if outcome is not None and outcome not in _OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_OUTCOMES)}, got {outcome!r}")
        if self._closed:
            return
        self._closed = True
        if self._subscription is not None:
            self._subscription.cancel()
        self._sample_subscription.cancel()
        env = self._env
        cond = self._cond
        try:
            with cond:
                batch = self._seal_locked() if self._entries else None
                cond.notify_all()
            if batch is not None:
                self._enqueue("batch", batch.size, batch)
            if self._samples:
                self._flush_samples()
            self._sync_kpi_declarations()
            fingerprint = env.fingerprint()
            kpis: Any = dict(fingerprint.kpis)
            if kpis:
                payload = pack(FrozenMap({"scalars": kpis}))
                self._enqueue("record", len(payload), (RecordType.KPI, payload))
            if outcome is None:
                outcome = "failed" if env._run_failed else "cancelled" if env._interrupted else "completed"
            try:
                end = wire_time(env.now)
            except (TypeError, ValueError):  # the run failed on its time (R35): end at the last recorded event
                end = self._last_t
            footer = {
                "outcome": outcome,
                "cursor": (end, self._last_seq),
                "manifest": env.manifest().final,
                "fingerprint": {"digest": fingerprint.digest, "kpis": kpis},
                "volatile": dataclasses.asdict(env.volatile_metadata()),
            }
            self._enqueue("footer", 0, footer)
            if self._blocked_count > 1:
                self._env.warning(
                    f"trace writer backpressure: the simulation blocked {self._blocked_count} times for "
                    f"{self._blocked_total:.3f} s in total (max_pending_bytes={self._max_pending})",
                    component="TraceRecorder",
                    blocked_count=self._blocked_count,
                    blocked_s=self._blocked_total,
                )
        finally:
            with cond:
                self._stop = True
                cond.notify_all()
            self._thread.join()
            self._file.close()
        error = self._error
        if error is not None:
            raise error

    # -------------------------------------------------------------------------
    # Writer thread
    # -------------------------------------------------------------------------

    def _run(self) -> None:
        cond = self._cond
        try:
            while True:
                with cond:
                    item = self._next_item_locked()
                if item is None:
                    return
                kind, size, data = item
                done = self._write_item(kind, data)
                with cond:
                    self._pending -= size
                    if kind == "batch" and data.rtype is RecordType.CHUNK:
                        self._chunks_written += 1
                    cond.notify_all()
                if done:
                    return
        except BaseException as exc:  # latched for the simulation thread
            with cond:
                self._error = exc
                cond.notify_all()

    def _next_item_locked(self) -> _Item | None:
        """The next item to write, sealing the open buffer on latency while the queue is empty."""
        cond = self._cond
        latency = self._limits.max_latency_s
        while True:
            if self._queue:
                return self._queue.popleft()
            if self._stop:
                return None
            timeout = None
            if self._entries:
                remaining = self._opened_at + latency - self._clock()
                if remaining <= 0:
                    # The queue is empty, so admitting the batch respects both backpressure rules.
                    batch = self._seal_locked()
                    assert self._pending == 0
                    self._pending = batch.size
                    self._peak_pending = max(self._peak_pending, batch.size)
                    return ("batch", batch.size, batch)
                timeout = remaining if math.isfinite(remaining) else None
            if self._samples_ready and self._samples:
                remaining = self._samples_opened_at + latency - self._clock()
                if remaining <= 0:
                    payload = self._seal_samples_locked()
                    self._pending = len(payload)
                    self._peak_pending = max(self._peak_pending, len(payload))
                    return ("record", len(payload), (RecordType.KPI, payload))
                if math.isfinite(remaining):
                    timeout = remaining if timeout is None else min(timeout, remaining)
            cond.wait(timeout)

    def _write_item(self, kind: str, data: Any) -> bool:
        """Write one queue item; return True after the footer."""
        f = self._file
        if kind == "batch":
            self._write_batch(data)
        elif kind == "record":
            rtype, payload = data
            self._offset += write_record(f, rtype, payload)
        elif kind == "ext":
            self._epochs.append(self._offset)
            self._offset += write_record(f, RecordType.CATALOG_EXT, data[1])
        elif kind == "initial":
            payload, self._replay = data
            self._offset += write_record(f, RecordType.INITIAL, payload)
        elif kind == "header":
            self._offset += write_preamble(f)
            self._epochs.append(self._offset)
            self._offset += write_record(f, RecordType.HEADER, data)
        else:  # footer
            footer: Any = {**data, "index": tuple(self._index), "epochs": tuple(self._epochs)}
            offset = self._offset
            self._offset += write_record(f, RecordType.FOOTER, pack(footer))
            self._offset += write_trailer(f, offset)
            f.flush()
            return True
        f.flush()
        return False

    def _write_batch(self, batch: _Batch) -> None:
        f = self._file
        first: Any = batch.first
        last: Any = batch.last
        if batch.rtype is RecordType.PRELUDE:
            body = self._frame([("first", pack(first)), ("last", pack(last))], batch.entries)
            self._offset += write_record(f, RecordType.PRELUDE, body)
            return
        replay: Any = self._replay  # plain dicts of wire values
        body = self._frame(
            [
                ("first", pack(first)),
                ("last", pack(last)),
                ("t_start", pack(first[0])),
                ("t_end", pack(last[0])),
                ("epoch", pack(batch.epoch)),
                ("snapshot", pack(replay)),
            ],
            batch.entries,
        )
        offset = self._offset
        length = write_record(f, RecordType.CHUNK, zlib.compress(body))
        self._offset += length
        entry = FrozenMap(
            {
                "offset": offset,
                "length": length,
                "first": first,
                "last": last,
                "t_start": first[0],
                "t_end": last[0],
                "epoch": batch.epoch,
            }
        )
        self._offset += write_record(f, RecordType.INDEX, pack(entry))
        self._index.append(entry)
        for deltas in batch.deltas:
            apply_deltas(replay, deltas)

    def _frame(self, fields: list[tuple[str, bytes]], entries: list[bytes]) -> bytes:
        """A MessagePack map of pre-encoded `fields` plus ``"events"``, the array of pre-encoded `entries`."""
        packer = self._packer
        parts = [packer.pack_map_header(len(fields) + 1)]
        for key, value in fields:
            parts.append(pack(key))
            parts.append(value)
        parts.append(pack("events"))
        parts.append(packer.pack_array_header(len(entries)))
        parts.extend(entries)
        return b"".join(parts)
