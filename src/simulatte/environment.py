from __future__ import annotations

import operator
import os
import random
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, overload

import simpy
from simpy.core import StopSimulation

from simulatte.entities import EntityRegistry
from simulatte.events import DomainEvent, Event, EventBus, Op, validate_event
from simulatte.logger import EventHistoryBuffer, SimLogger
from simulatte.rng import BindingKind, CountingRandom, DrawCounter, derive_seed, resolve_binding

SEED_LIMIT = 2**63
"""Seeds are integers in ``[0, SEED_LIMIT)``."""


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
        self._streams: dict[str, random.Random] = {}
        self._draws = DrawCounter()  # incremented only by the counting streams of debug mode
        self.opaque_sampler_owners: list[str] = []
        """Owners of the opaque samplers bound with :meth:`bind`, in order of first binding."""
        self._debug = debug
        self._seq = 0
        self._ordinal = 0
        self._projection_active = False
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

    def step(self) -> None:
        """
        Process the next event in the queue.

        If user interrupts the simulation via KeyboardInterrupt
        raise a StopSimulation exception to gently pause the simulation.
        """

        try:
            super().step()
        except KeyboardInterrupt:  # pragma: no cover
            raise StopSimulation("KeyboardInterrupt")

    def close(self) -> None:
        """Release logger resources associated with this environment."""
        self._logger.close()

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
