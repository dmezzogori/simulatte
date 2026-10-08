from __future__ import annotations

import contextlib
import functools
import operator
import os
import random
import time
import types
from datetime import UTC, datetime
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, Literal, ParamSpec, Protocol, TypeVar, overload

import simpy
from simpy.core import StopSimulation

from simulatte._wire import FrozenMap, Wire
from simulatte.digest import Fingerprint, SemanticDigest
from simulatte.entities import EntityRegistry
from simulatte.events import DomainEvent, Event, EventBus, Op, validate_event
from simulatte.logger import EventHistoryBuffer, SimLogger
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
    from simulatte.trace.writer import TraceRecorder

SEED_LIMIT = 2**63
"""Seeds are integers in ``[0, SEED_LIMIT)``."""

InitialState = dict[str, dict[str, Wire]]
"""Canonical initial state: live entity states as wire values, sorted by id (see `EntityRegistry.snapshot`)."""


class Environment(simpy.Environment):
    """
    Thin wrapper around ``simpy.Environment`` with an event bus and integrated logging.

    Events are published with :meth:`emit` and delivered through :attr:`bus`; emitting sites guard event
    construction with :meth:`wants`. Components register themselves in :attr:`entities`. Randomness comes
    from the named streams of :meth:`rng`, derived from :attr:`seed`; components resolve their samplers
    with :meth:`bind`.

    Each environment has its own logger that:
    - Automatically includes simulation time in log output
    - Supports JSON or text output format
    - Maintains an in-memory history buffer
    - Supports per-component filtering
    """

    def __init__(
        self,
        *,
        seed: int | None = None,
        time_unit: str | None = None,
        provenance: Provenance | None = None,
        debug: bool = False,
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
            log_file: Optional file path for log output (defaults to stderr)
            log_format: Output format ("text" or "json")
            log_history_size: Maximum number of events to keep in history buffer
            log_db_path: Optional SQLite database path for persistent event storage.
                         If provided, events are stored in both memory buffer and SQLite.
        """
        if seed is None:
            seed = int.from_bytes(os.urandom(8), "big") >> 1
        else:
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
        self._requested_inputs: tuple[int, str | None, Provenance | None] | None = None
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
        self.entities = EntityRegistry(self)
        self._logger = SimLogger(
            env=self,
            log_file=log_file,
            log_format=log_format,
            history_size=log_history_size,
            db_path=log_db_path,
        )

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Stamp `event` and deliver it to the subscribers of its type.

        Stamps `t`, `seq` and, for domain events while the projection is active, `ordinal`. Raises
        `ValueError` for an instance that was already emitted or a non-domain event carrying deltas, and
        `RuntimeError` for a domain event emitted while subscribers are being called. Exceptions raised by
        subscribers propagate.
        """
        if event.seq != -1:
            raise ValueError(f"event already emitted (seq={event.seq}); emit a fresh instance")
        domain = isinstance(event, DomainEvent)
        if domain:
            if self.bus.delivering:
                raise RuntimeError("cannot emit a DomainEvent while subscribers are being called")
        elif event.deltas.ops:
            raise ValueError(f"{type(event).__name__} is not a DomainEvent and cannot carry deltas")
        if self._debug:
            validate_event(event, entity_kind=self._entity_kind, check_lifecycle=self._check_lifecycle_op)
        object.__setattr__(event, "t", self._now)
        object.__setattr__(event, "seq", self._seq)
        self._seq += 1
        if domain and self._projection_active:
            object.__setattr__(event, "ordinal", self._ordinal)
            self._ordinal += 1
        self.bus.publish(event)

    def wants(self, event_type: type[Event]) -> bool:
        """Whether any subscriber listens to `event_type` (guard event construction with it)."""
        return self.bus.wants(event_type)

    def _entity_kind(self, entity_id: str) -> str | None:
        """Kind of the live entity `entity_id`, or None when unknown (debug validation of touches)."""
        return self.entities.kind_of(entity_id)

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
        unknown kind and `TypeError` for a value of no accepted form.
        """
        callback, opaque = resolve_binding(value, kind=kind, stream=lambda: self.rng(stream))
        if opaque and owner not in self.opaque_sampler_owners:
            self.opaque_sampler_owners.append(owner)
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

        Raises `RuntimeError` before activation.
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

        Digests and trace recorders call this when they attach. Domain events get ordinals only once the
        projection is active, which happens after every listener received the initial state; the first domain
        event after activation has ordinal 0. Raises `RuntimeError` after activation.
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

        state = self.entities.snapshot()
        self._initial_state = state
        listeners = self._projection_listeners
        for listener in listeners:
            listener(state)
        if listeners:
            self._projection_active = True
        self._requested_inputs = (self._seed, self.time_unit, self._provenance)  # cheap; the manifest is built lazily
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
        """The digest (None unless :meth:`enable_digest` was called) and the KPI scalars of the run."""
        return Fingerprint(digest=None if self._digest is None else self._digest.hexdigest(), kpis={})

    def manifest(self) -> RunManifest:
        """The manifest of the run: the requested part, plus the final part once :meth:`run` was called."""
        requested = self._requested
        if requested is None:
            inputs = self._requested_inputs  # fixed at activation; before it, the current values
            seed, time_unit, provenance = (
                inputs if inputs is not None else (self._seed, self.time_unit, self._provenance)
            )
            requested = build_requested(seed=seed, time_unit=time_unit, provenance=provenance)
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
        """Close the trace recorders attached to this environment, then release logger resources.

        Every recorder is closed even if one raises; the exception propagates afterwards.
        """
        recorders, self._recorders = self._recorders, []
        with contextlib.ExitStack() as stack:
            stack.callback(self._logger.close)
            for recorder in reversed(recorders):
                stack.callback(recorder.close)

    def __enter__(self) -> Environment:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Logging convenience methods
    # -------------------------------------------------------------------------

    def debug(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log a debug message with simulation time context.

        Args:
            message: The log message
            component: Optional component class name for filtering (e.g., "Server")
            **extra: Additional structured data to include in the log
        """
        self._logger.debug(message, component=component, **extra)

    def info(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log an info message with simulation time context.

        Args:
            message: The log message
            component: Optional component class name for filtering (e.g., "Server")
            **extra: Additional structured data to include in the log
        """
        self._logger.info(message, component=component, **extra)

    def warning(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log a warning message with simulation time context.

        Args:
            message: The log message
            component: Optional component class name for filtering (e.g., "Server")
            **extra: Additional structured data to include in the log
        """
        self._logger.warning(message, component=component, **extra)

    def error(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log an error message with simulation time context.

        Args:
            message: The log message
            component: Optional component class name for filtering (e.g., "Server")
            **extra: Additional structured data to include in the log
        """
        self._logger.error(message, component=component, **extra)

    @property
    def log_history(self) -> EventHistoryBuffer:
        """Access the event history buffer.

        Returns:
            The EventHistoryBuffer containing recent log events.
            Use .query() to filter events by level, component, or time range.

        Example:
            >>> env.log_history.query(level="ERROR", since=100.0)
        """
        return self._logger.history

    @property
    def logger(self) -> SimLogger:
        """Access the underlying SimLogger for advanced configuration.

        Use this to enable/disable component-level filtering:
            >>> env.logger.disable_component("Server")
            >>> env.logger.enable_component("ShopFloor")
        """
        return self._logger


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
