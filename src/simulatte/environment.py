from __future__ import annotations

import contextlib
import functools
import math
import operator
import os
import random
import time
import types
from types import MappingProxyType
from datetime import UTC, datetime
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, Literal, ParamSpec, Protocol, TypeVar, cast, overload

import simpy
from simpy.core import StopSimulation

from simulatte._wire import FrozenMap, Wire, freeze
from simulatte.digest import Fingerprint, SemanticDigest
from simulatte.entities import KINDS, EntityRegistry
from simulatte.events import DomainEvent, Event, EventBus, LogEvent, Op, validate_event
from simulatte.logsinks import HistorySink, JsonSink, LogSink, SQLiteSink, TextSink
from simulatte.provenance import (
    Provenance,
    RunManifest,
    VolatileMetadata,
    build_final,
    build_requested,
    volatile_metadata,
)
from simulatte.rng import BindingKind, CountingRandom, DrawCounter, derive_seed, resolve_binding

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.kpi import Collector
    from simulatte.trace.writer import TraceRecorder

SEED_LIMIT = 2**63
"""Seeds are integers in ``[0, SEED_LIMIT)``."""
_UNBOUND = object()  # sentinel: a stream not bound yet

_EMITTABLE: set[type] = set()
"""Event classes :meth:`Environment.emit` has checked (registered, or not inheriting a registered type)."""

InitialState = Mapping[str, Mapping[str, Wire]]
"""Canonical initial state: live entity states as wire values, sorted by id (see `EntityRegistry.snapshot`).

A read-only view: neither the map nor the per-entity field maps can be modified."""


class Environment(simpy.Environment):
    """
    Thin wrapper around ``simpy.Environment`` with an event bus and integrated logging.

    Events are published with :meth:`emit` and delivered through :attr:`bus`; emitting sites guard event
    construction with :attr:`wants`. Components register themselves in :attr:`entities`. Randomness comes
    from the named streams of :meth:`rng`, derived from :attr:`seed`; components resolve their samplers
    with :meth:`bind`.

    :meth:`debug`, :meth:`info`, :meth:`warning` and :meth:`error` emit ``log`` events on the bus; the log sinks
    of :mod:`simulatte.logsinks` write them out. The ``log_*`` arguments attach the default sinks: text or JSON
    lines to stderr or `log_file`, the in-memory :attr:`log_history`, and an SQLite database (:attr:`log_db`). More
    sinks attach with ``sink.attach(env)``; :attr:`sinks` lists them and :meth:`close` closes them.
    """

    def __init__(
        self,
        *,
        seed: int | None = None,
        time_unit: str | None = None,
        provenance: Provenance | None = None,
        debug: bool = False,
        log_level: str = "INFO",
        log_file: str | Path | None = None,
        log_format: Literal["text", "json"] = "text",
        log_history_size: int = 1000,
        log_db_path: str | Path | None = None,
    ) -> None:
        """Initialize the simulation environment.

        Args:
            seed: Seed of every RNG stream, an integer in ``[0, 2**63)``. ``None`` draws one from
                  ``os.urandom``; read it back from :attr:`seed` to reproduce the run.
            time_unit: Optional name of the unit of simulated time (for example ``"minute"``), recorded in the
                       run manifest.
            provenance: Optional hashes of the model, source, inputs and dependencies, recorded in the run
                        manifest (see :class:`simulatte.provenance.Provenance`).
            debug: Validate emitted events against the catalog and the entity state schemas, and reject
                   subscribers that schedule SimPy events or draw from :meth:`rng`. Slower; meant for tests
                   and model development.
            log_level: Lowest level written by the default sinks (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` or
                       ``CRITICAL``), for this environment only. At ``DEBUG`` the text, JSON and SQLite sinks also
                       write every domain event, which then gets built at each emitting site.
            log_file: Optional file path for log output (defaults to stderr), opened once in append mode.
            log_format: Output format ("text" or "json").
            log_history_size: Maximum number of log records kept by :attr:`log_history`.
            log_db_path: Optional SQLite database path for persistent log storage (see :attr:`log_db`).
        """
        if seed is None:
            seed = int.from_bytes(os.urandom(8), "big") >> 1
        else:
            if isinstance(seed, bool):
                raise TypeError("seed must be an int, not a bool")
            seed = operator.index(seed)
            if not 0 <= seed < SEED_LIMIT:
                raise ValueError(f"seed must be in [0, 2**63), got {seed}")
        super().__init__()
        self._seed = seed
        self.time_unit = time_unit
        """Name of the unit of simulated time, or None."""
        self._provenance = provenance
        self._digest: SemanticDigest | None = None
        self._requested: FrozenMap | None = None
        self._requested_inputs: tuple[int, str | None, Provenance | None, float] | None = None
        self._warmup = 0.0
        self._collectors: list[Collector] = []  # attached KPI collectors, in attachment order
        self._stopping_policy: Wire | None = None
        self._wall_clock_start: str | None = None
        self._run_failed = False  # some run() raised
        self._interrupted = False  # some run() was stopped by KeyboardInterrupt (see step)
        self._recorders: list[TraceRecorder] = []  # closed by close()
        self._run_seconds = 0.0
        self._streams: dict[str, random.Random] = {}
        self._draws = DrawCounter()  # incremented only by the counting streams of debug mode
        self.opaque_sampler_owners: list[str] = []
        """Owners of the opaque samplers bound with :meth:`bind`, in order of first binding."""
        self._bound_streams: dict[str, object] = {}  # stream name -> bound value (debug mode only)
        self._debug = debug
        self._seq = 0
        self._ordinal = 0
        self._projection_active = False
        self._projection_listeners: list[Callable[[InitialState], None]] = []
        self._initializers: list[Callable[[], object]] = []
        self._commands: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []
        self._activation_started = False
        self._initializers_done = False  # set once the initializer loop of activate() finished
        self._activated = False
        self._initial_state: InitialState | None = None
        self.bus = EventBus(probe=self._probe if debug else None)
        self.wants: Callable[[type[Event]], bool] = self.bus._interest.__getitem__
        """``wants(event_type)``: whether any subscriber listens to `event_type` (guard event construction with it).

        The bound lookup of the bus's interest cache rather than a method, so that a check costs one C call and no
        Python frame while nobody listens (D56)."""
        self.entities = EntityRegistry(self)
        self._sinks: list[LogSink] = []
        if log_format not in ("text", "json"):
            raise ValueError(f"log_format must be 'text' or 'json', got {log_format!r}")
        stream_sink = JsonSink if log_format == "json" else TextSink
        stream_sink(log_file, level=log_level).attach(self)
        self._log_history = HistorySink(log_history_size, level=log_level)
        self._log_history.attach(self)
        self._log_db: SQLiteSink | None = None
        if log_db_path is not None:
            self._log_db = SQLiteSink(log_db_path, level=log_level)
            self._log_db.attach(self)

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Stamp `event` and deliver it to the subscribers of its type.

        Stamps `t`, `seq` and, for domain events while the projection is active, `ordinal`. Raises
        `ValueError` for an instance that was already emitted or a non-domain event carrying deltas,
        `RuntimeError` for a domain event emitted while subscribers are being called, and `TypeError` for an
        instance of a subclass of a registered event type that is not registered itself (it would be recorded
        under its parent's type with undeclared fields). Exceptions raised by subscribers propagate.
        """
        if event.seq != -1:
            raise ValueError(f"event already emitted (seq={event.seq}); emit a fresh instance")
        cls = type(event)
        if cls not in _EMITTABLE:
            if "type_name" not in cls.__dict__ and hasattr(cls, "type_name"):
                raise TypeError(
                    f"{cls.__name__} subclasses the event type {cls.type_name!r} without being registered; "
                    "decorate it with @event_type"
                )
            _EMITTABLE.add(cls)
        domain = isinstance(event, DomainEvent)
        if domain:
            if self.bus.delivering:
                raise RuntimeError("cannot emit a DomainEvent while subscribers are being called")
        elif event.deltas.ops:
            raise ValueError(f"{type(event).__name__} is not a DomainEvent and cannot carry deltas")
        if self._debug:
            validate_event(
                event,
                entity_kind=self._entity_kind,
                check_lifecycle=self._check_lifecycle_op,
                collection_of=self._field_collection,
            )
        object.__setattr__(event, "t", self._now)
        object.__setattr__(event, "seq", self._seq)
        self._seq += 1
        if domain and self._projection_active:
            object.__setattr__(event, "ordinal", self._ordinal)
            self._ordinal += 1
        self.bus.publish(event)

    @property
    def debug_mode(self) -> bool:
        """Whether the environment validates events and subscribers (the ``debug`` constructor argument)."""
        return self._debug

    def _entity_kind(self, entity_id: str) -> str | None:
        """Kind of the live entity `entity_id`, or None when unknown (debug validation of touches)."""
        return self.entities.kind_of(entity_id)

    def _field_collection(self, entity_id: str, field: str) -> str | None:
        """``"list"``, ``"map"`` or ``"scalar"`` for a declared field of a live entity; None otherwise (debug)."""
        kind = self.entities.kind_of(entity_id)
        schema = None if kind is None else KINDS.get(kind)
        if schema is None or field not in schema:
            return None
        spec = schema[field]
        if spec.collection is not None:
            return spec.collection
        if spec.wire_type == "array":  # a whole array or map value: the list or map operations replay on it
            return "list"
        if spec.wire_type == "map":
            return "map"
        return None if spec.wire_type == "any" else "scalar"

    def _check_lifecycle_op(self, op: Op) -> None:
        """Validate a ``create``/``retire`` operation (debug mode).

        ``create`` is checked against the schema of the kind it names, ``retire`` against the existence of
        the addressed live entity.
        """
        self.entities.check_lifecycle_op(op)

    def _probe(self) -> tuple[int, int]:
        """(scheduled SimPy events, RNG draws) for the debug delivery check of the bus."""
        return len(self._queue), self._draws.draws

    # -------------------------------------------------------------------------
    # Randomness
    # -------------------------------------------------------------------------

    @property
    def seed(self) -> int:
        """Seed of every RNG stream of this environment."""
        return self._seed

    def rng(self, name: str) -> random.Random:
        """The RNG stream `name`, created on first use and cached.

        Its seed is :func:`simulatte.rng.derive_seed` of :attr:`seed` and `name`, so streams are independent
        of each other and of their creation order. In debug mode the stream counts its draws, and drawing
        while subscribers are being called raises `RuntimeError`.
        """
        stream = self._streams.get(name)
        if stream is None:
            if not isinstance(name, str):
                raise TypeError(f"stream name must be a str, got {type(name).__name__}")
            seed = derive_seed(self._seed, name)
            stream = CountingRandom(seed, self._draws) if self._debug else random.Random(seed)
            self._streams[name] = stream
        return stream

    @overload
    def bind(self, value: object, *, kind: Literal["scalar"], stream: str, owner: str) -> Callable[[], float]: ...

    @overload
    def bind(
        self, value: object, *, kind: Literal["routing"], stream: str, owner: str
    ) -> Callable[[], Sequence[Any]]: ...

    @overload
    def bind(self, value: object, *, kind: Literal["contextual"], stream: str, owner: str) -> Callable[..., float]: ...

    def bind(self, value: object, *, kind: BindingKind, stream: str, owner: str) -> Callable[..., Any]:
        """Resolve `value` into the callback of a binding `kind` (spec §8.2).

        - ``scalar`` (``() -> float``): a description with ``sampler(rng)`` or a number.
        - ``routing`` (``() -> Sequence[Server]``): a routing description or a fixed sequence of servers.
        - ``contextual`` (``(*context) -> float``): a description or a number; the context is ignored.

        Descriptions draw from :meth:`rng` ``(stream)``. Any other callable is *opaque*: it is returned
        unchanged and `owner` is recorded in :attr:`opaque_sampler_owners`. Raises `ValueError` for an
        unknown kind and `TypeError` for a value of no accepted form. In debug mode binding a value to a
        stream already bound to a different value raises `ValueError`: the two samplers would share one
        stream and the result would depend on the order of their draws.
        """
        if self._debug:
            bound = self._bound_streams.get(stream, _UNBOUND)
            if bound is not _UNBOUND and bound is not value and bound != value:
                raise ValueError(f"stream {stream!r} is already bound to a different value ({owner!r})")
        callback, opaque = resolve_binding(value, kind=kind, stream=lambda: self.rng(stream))
        if opaque and owner not in self.opaque_sampler_owners:
            self.opaque_sampler_owners.append(owner)
        if self._debug:
            self._bound_streams.setdefault(stream, value)
        return callback

    # -------------------------------------------------------------------------
    # Preparation and activation
    # -------------------------------------------------------------------------

    @property
    def activated(self) -> bool:
        """Whether :meth:`activate` completed its initializers and captured the initial state."""
        return self._activated

    @property
    def initial_state(self) -> InitialState:
        """Canonical initial state captured at activation, after the initializers (spec §10).

        A read-only view, the same object the projection listeners receive. Raises `RuntimeError` before
        activation.
        """
        if self._initial_state is None:
            raise RuntimeError("the environment is not activated yet; the initial state is captured by activate()")
        return self._initial_state

    def on_activate(self, fn: Callable[[], object]) -> None:
        """Register `fn` as an activation initializer.

        Initializers run in registration order when the environment activates; one registered after the
        initializers of :meth:`activate` ran (for example by a projection listener, or after activation) runs
        immediately. While an initializer runs, scheduling any SimPy event raises `RuntimeError`, and simulated
        time must not advance.
        """
        if self._initializers_done:
            self._run_initializer(fn)
        else:
            self._initializers.append(fn)

    def request_projection(self, on_initial_state: Callable[[InitialState], None]) -> None:
        """Request the semantic projection; `on_initial_state` receives the initial state at activation.

        Digests and trace recorders call this when they attach; every listener receives the same read-only
        :attr:`initial_state`. Domain events get ordinals only once the projection is active, which happens after
        every listener received the initial state; the first domain event after activation has ordinal 0. Raises
        `RuntimeError` after activation.
        """
        if self._activated:
            raise RuntimeError("request_projection() must be called before activation")
        self._projection_listeners.append(on_initial_state)

    def activate(self) -> None:
        """End preparation and start the run (spec §10). Idempotent; :meth:`run` calls it on first use.

        The sequence is: run the initializers in registration order, capture :attr:`initial_state`, notify the
        projection listeners and activate the projection, then execute the queued deferrable commands in call
        order at the current time, before any scheduled event is processed. An exception from an initializer or
        a command propagates; commands after a failing one are dropped. Calling it again after a failed
        initializer, or from an initializer, raises `RuntimeError`.
        """
        if self._activated:
            return
        if self._activation_started:
            raise RuntimeError("activation is in progress or failed; the environment cannot be activated again")
        self._activation_started = True
        initializers = self._initializers
        index = 0
        while index < len(initializers):  # initializers may register further initializers
            self._run_initializer(initializers[index])
            index += 1
        self._initializers = []
        self._initializers_done = True  # later registrations (projection listeners included) run immediately

        snapshot = self.entities.snapshot()
        state = MappingProxyType({entity: MappingProxyType(fields) for entity, fields in snapshot.items()})
        self._initial_state = state
        listeners = self._projection_listeners
        for listener in listeners:
            listener(state)
        if listeners:
            self._projection_active = True
        # cheap; the manifest is built lazily
        self._requested_inputs = (self._seed, self.time_unit, self._provenance, self._warmup)
        self._activated = True

        commands, self._commands = self._commands, []
        for fn, args, kwargs in commands:
            fn(*args, **kwargs)

    def _run_initializer(self, fn: Callable[[], object]) -> None:
        """Call `fn` with scheduling blocked, and fail if it advanced simulated time."""
        now = self._now
        # Shadow the class method with an instance attribute only while the initializer runs, so the hot
        # scheduling path stays untouched otherwise.
        self.__dict__["schedule"] = self._schedule_blocked
        try:
            fn()
        finally:
            self.__dict__.pop("schedule", None)
        if self._now != now:
            raise RuntimeError(f"initializer {fn!r} advanced simulated time from {now} to {self._now}")

    def _schedule_blocked(self, event: simpy.Event, priority: int = 1, delay: float = 0) -> None:
        raise RuntimeError(
            "cannot schedule SimPy events while an activation initializer runs "
            "(initializers must not start processes, create timeouts or trigger events)"
        )

    @contextmanager
    def _internal_scheduling(self) -> Generator[None, None, None]:
        """Allow scheduling inside an initializer (internal; used by ``place_now`` for immediate grants)."""
        blocked = self.__dict__.pop("schedule", None)
        try:
            yield
        finally:
            if blocked is not None:
                self.__dict__["schedule"] = blocked

    def run(self, until: float | simpy.Event | None = None) -> Any:
        """Activate the environment on first use (see :meth:`activate`), then run the simulation.

        The stopping policy of the manifest is the one of the last call: a horizon for a numeric `until`,
        exhaustion for ``None``. If it raises, trace recorders report the run as ``failed``.
        """
        try:
            if not self._activated:
                self.activate()
            if until is None:
                self._stopping_policy = FrozenMap({"type": "exhaustion"})
            elif isinstance(until, simpy.Event):
                self._stopping_policy = FrozenMap({"type": "event"})
            else:
                self._stopping_policy = FrozenMap({"type": "horizon", "horizon": float(until)})
            if self._wall_clock_start is None:
                self._wall_clock_start = datetime.now(UTC).isoformat()
            started = time.perf_counter()
            try:
                return super().run(until)
            finally:
                self._run_seconds += time.perf_counter() - started
        except BaseException:
            self._run_failed = True
            raise

    # -------------------------------------------------------------------------
    # KPIs
    # -------------------------------------------------------------------------

    def configure_kpis(self, *, warmup: float = 0.0) -> None:
        """Set the warm-up of the KPI observation window (spec §12.2), recorded in the requested manifest.

        The window starts at `warmup`, a finite time ``>= 0``; see :func:`simulatte.kpi.observation_window`.
        Raises `RuntimeError` once activation started and `ValueError` for an invalid warm-up.
        """
        if self._activation_started:
            raise RuntimeError("configure_kpis() must be called before activation")
        value = float(warmup)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"warmup must be a finite time >= 0, got {warmup!r}")
        self._warmup = value

    @property
    def warmup(self) -> float:
        """The warm-up set with :meth:`configure_kpis` (default 0): the start of the KPI observation window."""
        return self._warmup

    @property
    def collectors(self) -> tuple[Collector, ...]:
        """The KPI collectors attached to this environment, in attachment order.

        Builders attach collectors when asked (for example ``collect_workload=True``); find them here by type and
        scope.
        """
        return tuple(self._collectors)

    # -------------------------------------------------------------------------
    # Digest and manifest
    # -------------------------------------------------------------------------

    def enable_digest(self) -> SemanticDigest:
        """Attach the semantic digest of the run and return it; later calls return the same digest.

        Raises `RuntimeError` after activation unless the digest was already enabled.
        """
        if self._digest is None:
            self._digest = SemanticDigest.attach(self)
        return self._digest

    def fingerprint(self) -> Fingerprint:
        """The digest (None unless :meth:`enable_digest` was called) and the KPI scalars of the run.

        The scalars are those of every attached :class:`~simulatte.kpi.Collector`, keyed
        ``"<scope id>/<kpi name>"`` and sorted by key.
        """
        kpis: dict[str, float] = {}
        for collector in self._collectors:
            kpis.update(collector.scalars())
        digest = None if self._digest is None else self._digest.hexdigest()
        return Fingerprint(digest=digest, kpis=dict(sorted(kpis.items())))

    def manifest(self) -> RunManifest:
        """The manifest of the run: the requested part, plus the final part once :meth:`run` was called."""
        requested = self._requested
        if requested is None:
            inputs = self._requested_inputs  # fixed at activation; before it, the current values
            seed, time_unit, provenance, warmup = (
                inputs if inputs is not None else (self._seed, self.time_unit, self._provenance, self._warmup)
            )
            requested = build_requested(seed=seed, time_unit=time_unit, provenance=provenance, warmup=warmup)
            if inputs is not None:
                self._requested = requested
        final = None
        if self._stopping_policy is not None:
            final = build_final(requested, self._stopping_policy, self.opaque_sampler_owners)
        return RunManifest(requested=requested, final=final)

    def volatile_metadata(self) -> VolatileMetadata:
        """Host, wall-clock start and wall-clock time spent in :meth:`run`; separate from the manifest."""
        return volatile_metadata(self._wall_clock_start, self._run_seconds)

    def step(self) -> None:
        """
        Process the next event in the queue.

        If user interrupts the simulation via KeyboardInterrupt
        raise a StopSimulation exception to gently pause the simulation; trace recorders then report the run
        as ``cancelled``.
        """

        try:
            super().step()
        except KeyboardInterrupt:
            self._interrupted = True
            raise StopSimulation("KeyboardInterrupt")

    def close(self) -> None:
        """Close the trace recorders attached to this environment, then its log sinks. Idempotent.

        Every recorder and sink is closed even if one raises; the exception propagates afterwards. A closed sink
        receives no further event; :attr:`log_history` keeps its records.
        """
        recorders, self._recorders = self._recorders, []
        with contextlib.ExitStack() as stack:
            for sink in reversed(self._sinks):
                stack.callback(sink.close)
            for recorder in reversed(recorders):
                stack.callback(recorder.close)

    def __enter__(self) -> Environment:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    def _log(self, level: str, message: str, component: str | None, extra: dict[str, Any]) -> None:
        if self.wants(LogEvent):
            # Debug mode records an immutable copy (R30), which also rejects values that are not wire values.
            frozen = cast("FrozenMap", freeze(extra)) if self._debug else FrozenMap(extra)
            self.emit(LogEvent(level=level, message=message, component=component, extra=frozen))

    def debug(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Emit a ``DEBUG`` :class:`~simulatte.events.LogEvent` at the current simulation time.

        Args:
            message: The log message
            component: Optional component name for filtering (e.g., "Server")
            **extra: Additional structured data to include in the record (wire values in debug mode)
        """
        self._log("DEBUG", message, component, extra)

    def info(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Emit an ``INFO`` :class:`~simulatte.events.LogEvent`; arguments as for :meth:`debug`."""
        self._log("INFO", message, component, extra)

    def warning(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Emit a ``WARNING`` :class:`~simulatte.events.LogEvent`; arguments as for :meth:`debug`."""
        self._log("WARNING", message, component, extra)

    def error(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Emit an ``ERROR`` :class:`~simulatte.events.LogEvent`; arguments as for :meth:`debug`."""
        self._log("ERROR", message, component, extra)

    @property
    def sinks(self) -> tuple[LogSink, ...]:
        """The log sinks attached to this environment, in attachment order (the default ones first)."""
        return tuple(self._sinks)

    @property
    def log_history(self) -> HistorySink:
        """The in-memory history of recent log records (``log_history_size`` of them).

        Example:
            >>> env.log_history.query(level="ERROR", since=100.0)
        """
        return self._log_history

    @property
    def log_db(self) -> SQLiteSink:
        """The SQLite sink created by ``log_db_path``, with :meth:`~simulatte.logsinks.SQLiteSink.query` and
        :meth:`~simulatte.logsinks.SQLiteSink.execute_sql`.

        Raises:
            RuntimeError: The environment was created without ``log_db_path``.
        """
        if self._log_db is None:
            raise RuntimeError("SQLite storage not enabled. Provide log_db_path to enable.")
        return self._log_db


# ---------------------------------------------------------------------------------------------------------
# Deferrable commands
# ---------------------------------------------------------------------------------------------------------


class _HasEnvironment(Protocol):
    @property
    def env(self) -> Environment: ...


_S = TypeVar("_S", bound=_HasEnvironment)
_P = ParamSpec("_P")
_R = TypeVar("_R")


def deferrable(method: Callable[Concatenate[_S, _P], _R]) -> Callable[Concatenate[_S, _P], _R | None]:
    """Make a component command deferrable until its environment (``self.env``) activates.

    Before activation a call appends ``(bound method, args, kwargs)`` to the environment's command queue, in
    call order across all components, and returns None; :meth:`Environment.activate` executes the queue.
    During and after activation the call runs immediately and returns the method's result.
    """

    @functools.wraps(method)
    def wrapper(self: _S, /, *args: _P.args, **kwargs: _P.kwargs) -> _R | None:
        env = self.env
        if env._activated:
            return method(self, *args, **kwargs)
        env._commands.append((types.MethodType(method, self), args, kwargs))
        return None

    return wrapper
