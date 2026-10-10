"""Log sinks: write an environment's ``log`` events, and at ``DEBUG`` its domain events, somewhere (spec §7.2).

:meth:`Environment.debug <simulatte.environment.Environment.debug>`, ``info``, ``warning`` and ``error`` emit
:class:`~simulatte.events.LogEvent` observer events on the environment's bus; a sink subscribes to them when it is
attached with :meth:`LogSink.attach` and keeps those at or above its `level` whose component passes its filters.

- :class:`TextSink` writes one formatted line per record to a file or a text stream.
- :class:`JsonSink` writes one JSON object per line.
- :class:`SQLiteSink` inserts one row per record into an SQLite database and queries it back.
- :class:`HistorySink` keeps the latest records in memory (``env.log_history``).

At ``DEBUG``, a text, JSON or SQLite sink created with ``render_domain=True`` also subscribes to every domain event
(``"*"``) and writes it as a ``DEBUG`` record; its component is the namespace of the event type (``job`` for
``job.queued``). Otherwise sinks listen to ``log`` events only, so domain events are not built for them.

Each sink attaches to one environment; :meth:`Environment.close <simulatte.environment.Environment.close>` closes
the sinks attached to it.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import uuid
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self, TextIO

from simulatte._wire import FrozenMap, wire_float
from simulatte.events import DomainEvent, LogEvent, Subscription

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment

__all__ = ["LEVELS", "HistorySink", "JsonSink", "LogSink", "SQLiteSink", "TextSink"]

LEVELS: Mapping[str, int] = MappingProxyType({"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50})
"""Level names and their priorities; a sink keeps the records at or above its level."""

_DEBUG = LEVELS["DEBUG"]


def _level_priority(level: str) -> int:
    """Priority of the level name `level` (case-insensitive); `ValueError` for an unknown name."""
    priority = LEVELS.get(level.upper()) if isinstance(level, str) else None
    if priority is None:
        raise ValueError(f"unknown log level {level!r}; expected one of {', '.join(LEVELS)}")
    return priority


def _format_sim_time(seconds: float) -> str:
    """Format simulation time as 'DDd HH:MM:SS.MS'."""
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    return f"{days:02}d {int(hours % 24):02d}:{int(minutes % 60):02d}:{(seconds % 60):02.2f}"


def _namespace(event: DomainEvent) -> str:
    """Component of a domain event: the namespace of its type name (``job`` for ``job.queued``)."""
    return event.type_name.partition(".")[0]


def _payload(event: DomainEvent) -> dict[str, Any]:
    return {name: getattr(event, name) for name in event.payload_fields}


def _render(event: DomainEvent) -> str:
    """One-line text of a domain event: its type name followed by ``field=value`` pairs."""
    fields = " ".join(f"{name}={getattr(event, name)!r}" for name in event.payload_fields)
    return f"{event.type_name} {fields}" if fields else event.type_name


def _json_default(value: object) -> Any:
    """JSON fallback: mappings (``FrozenMap``) become objects, anything else its ``str``."""
    if isinstance(value, Mapping):
        return dict(value)
    return str(value)


def _dumps(value: object) -> str:
    return json.dumps(value, default=_json_default)


class LogSink:
    """Base of the log sinks: level and component filters, attachment and closing.

    Args:
        level: Lowest level kept (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` or ``CRITICAL``, case-insensitive).
        components: When given, only records of these components are kept.
        exclude: Records of these components are dropped.

    Records without a component always pass the component filters.
    """

    _render_domain = False

    def __init__(
        self,
        *,
        level: str = "INFO",
        components: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> None:
        self._threshold = _level_priority(level)
        self._level = level.upper()
        self._components: set[str] | None = None if components is None else set(components)
        self._excluded: set[str] = set(exclude)
        self._env_id: str | None = None
        self._subscriptions: list[Subscription] = []
        self._closed = False

    @property
    def level(self) -> str:
        """Lowest level kept, upper-case."""
        return self._level

    @property
    def env_id(self) -> str | None:
        """Identifier of the attached environment (a fresh UUID hex string per attachment), None before."""
        return self._env_id

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` was called."""
        return self._closed

    def attach(self, env: Environment) -> Self:
        """Subscribe to the ``log`` events of `env` (and, see the module docstring, its domain events); return self.

        The sink is added to ``env.sinks`` and closed by ``env.close()``. Raises `RuntimeError` if the sink is
        already attached or closed.
        """
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")
        if self._env_id is not None:
            raise RuntimeError(f"{type(self).__name__} is already attached to an environment")
        self._open()
        self._env_id = uuid.uuid4().hex
        subscription = env.bus.subscribe(self._on_log, (LogEvent,))
        subscription._log_threshold = self._threshold
        self._subscriptions.append(subscription)
        if self._render_domain and self._threshold <= _DEBUG:
            self._subscriptions.append(env.bus.subscribe(self._on_domain, "*"))
        env._sinks.append(self)
        return self

    def enable_component(self, component: str) -> None:
        """Keep the records of `component` again (and add it to the `components` allow-list, if one was given)."""
        self._excluded.discard(component)
        if self._components is not None:
            self._components.add(component)

    def disable_component(self, component: str) -> None:
        """Drop the records of `component`."""
        self._excluded.add(component)

    def close(self) -> None:
        """Stop receiving events and release the sink's resources. Idempotent."""
        if self._closed:
            return
        self._closed = True
        subscriptions, self._subscriptions = self._subscriptions, []
        for subscription in subscriptions:
            subscription.cancel()
        self._release()

    def _accepts(self, component: str | None) -> bool:
        if component is None:
            return True
        if component in self._excluded:
            return False
        return self._components is None or component in self._components

    def _on_log(self, event: LogEvent) -> None:
        if LEVELS.get(event.level, 0) >= self._threshold and self._accepts(event.component):
            self._write_log(event)

    def _on_domain(self, event: DomainEvent) -> None:
        if self._accepts(_namespace(event)):
            self._write_domain(event)

    def _open(self) -> None:
        """Acquire resources when attaching (files, connections)."""

    def _release(self) -> None:
        """Release what :meth:`_open` acquired."""

    def _write_log(self, event: LogEvent) -> None:  # pragma: no cover - every concrete sink overrides it
        raise NotImplementedError

    def _write_domain(self, event: DomainEvent) -> None:  # pragma: no cover - only rendering sinks subscribe
        raise NotImplementedError


class _StreamSink(LogSink):
    """A sink writing one line per record to a file it opens once, or to a text stream it does not own."""

    def __init__(
        self,
        target: str | Path | TextIO | None,
        *,
        level: str = "INFO",
        components: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
        render_domain: bool = True,
    ) -> None:
        super().__init__(level=level, components=components, exclude=exclude)
        self._render_domain = render_domain
        self._path: Path | None = None
        self._stream: TextIO | None = None
        if isinstance(target, (str, Path)):
            self._path = Path(target)
        else:
            self._stream = target
        self._file: TextIO | None = None

    def _open(self) -> None:
        if self._path is not None:
            # Line-buffered, so every record reaches the file even if the environment is never closed.
            self._file = open(self._path, "a", encoding="utf-8", buffering=1)

    def _release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _write(self, line: str) -> None:
        stream = self._file or self._stream or sys.stderr
        stream.write(line + "\n")
        stream.flush()


class TextSink(_StreamSink):
    """Write each record as ``time | LEVEL | component | message``.

    Args:
        target: File path (opened once in append mode, closed by :meth:`close`), a text stream (written to, not
            closed), or None for the current ``sys.stderr``.
        level: Lowest level kept.
        components: When given, only records of these components are kept.
        exclude: Records of these components are dropped.
        render_domain: At ``DEBUG``, also write every domain event (type name and payload).
    """

    def _write_log(self, event: LogEvent) -> None:
        component = event.component or "-"
        self._write(f"{_format_sim_time(wire_float(event.t))} | {event.level:<8} | {component:<12} | {event.message}")

    def _write_domain(self, event: DomainEvent) -> None:
        self._write(
            f"{_format_sim_time(wire_float(event.t))} | {'DEBUG':<8} | {_namespace(event):<12} | {_render(event)}"
        )


class JsonSink(_StreamSink):
    """Write each record as one JSON object per line.

    A ``log`` record has ``sim_time``, ``sim_time_formatted``, ``wall_time``, ``seq``, ``kind`` (``"log"``),
    ``type`` (``"log"``), ``level``, ``message``, ``component`` and ``extra``. A domain event (``DEBUG`` with
    `render_domain`) has ``kind`` ``"domain"``, its event type, level ``DEBUG``, the namespace of its type as
    ``component``, its one-line rendering as ``message`` and its payload as ``data``. Arguments as for
    :class:`TextSink`.
    """

    def _write_log(self, event: LogEvent) -> None:
        record = self._base(event, "log", event.type_name, event.level, event.message, event.component)
        record["extra"] = dict(event.extra)
        self._write(_dumps(record))

    def _write_domain(self, event: DomainEvent) -> None:
        record = self._base(event, "domain", event.type_name, "DEBUG", _render(event), _namespace(event))
        record["data"] = _payload(event)
        self._write(_dumps(record))

    @staticmethod
    def _base(
        event: LogEvent | DomainEvent, kind: str, type_name: str, level: str, message: str, component: str | None
    ) -> dict[str, Any]:
        return {
            "sim_time": event.t,
            "sim_time_formatted": _format_sim_time(wire_float(event.t)),
            "wall_time": datetime.now(UTC).isoformat(),
            "seq": event.seq,
            "kind": kind,
            "type": type_name,
            "level": level,
            "message": message,
            "component": component,
        }


class SQLiteSink(LogSink):
    """Insert each record as a row of the ``events`` table of an SQLite database.

    The table has the columns ``env_id, seq, t, kind, type, level, component, message, data_json``: ``kind`` is
    ``"log"`` for a ``log`` event (``data_json`` holds its ``extra``) or ``"domain"`` for a domain event written at
    ``DEBUG`` with `render_domain` (``data_json`` holds its payload, ``component`` the namespace of its type).
    Several environments, each with its own sink, can share one database file; :attr:`env_id` tells their rows
    apart. Every row is committed when inserted.

    Args:
        path: Database file, created if missing.
        level: Lowest level kept.
        components: When given, only records of these components are kept.
        exclude: Records of these components are dropped.
        render_domain: At ``DEBUG``, also store every domain event.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS events (
            env_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            t REAL NOT NULL,
            kind TEXT NOT NULL,
            type TEXT NOT NULL,
            level TEXT NOT NULL,
            component TEXT,
            message TEXT NOT NULL,
            data_json TEXT NOT NULL,
            PRIMARY KEY (env_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_events_t ON events(env_id, t);
        CREATE INDEX IF NOT EXISTS idx_events_level ON events(env_id, level);
        CREATE INDEX IF NOT EXISTS idx_events_component ON events(env_id, component);
    """

    def __init__(
        self,
        path: str | Path,
        *,
        level: str = "INFO",
        components: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
        render_domain: bool = True,
    ) -> None:
        super().__init__(level=level, components=components, exclude=exclude)
        self._path = Path(path)
        self._render_domain = render_domain
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        """The database file."""
        return self._path

    def _open(self) -> None:
        # Autocommit: every insert is its own transaction, as with the per-insert commits of the old store.
        conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # PRAGMA statements return a row; consume it so the statement is finalized (PyPy's sqlite3 requires it).
        conn.execute("PRAGMA journal_mode=WAL").fetchall()
        conn.execute("PRAGMA synchronous=NORMAL").fetchall()
        conn.execute("PRAGMA busy_timeout=5000").fetchall()
        conn.executescript(self._SCHEMA)
        self._conn = conn

    def _release(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    def _insert(self, row: tuple[Any, ...]) -> None:
        with self._lock:
            conn = self._require_open()
            conn.execute(
                "INSERT INTO events (env_id, seq, t, kind, type, level, component, message, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            ).close()

    def _write_log(self, event: LogEvent) -> None:
        extra = _dumps(dict(event.extra))
        row = (self._env_id, event.seq, event.t, "log", event.type_name, event.level, event.component, event.message)
        self._insert((*row, extra))

    def _write_domain(self, event: DomainEvent) -> None:
        data = _dumps(_payload(event))
        row = (self._env_id, event.seq, event.t, "domain", event.type_name, "DEBUG", _namespace(event), _render(event))
        self._insert((*row, data))

    def _require_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("the SQLite sink is not attached or already closed")
        return self._conn

    def query(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[LogEvent]:
        """The ``log`` records of the attached environment, ordered by time and emission.

        Args:
            level: Keep this level only (case-insensitive).
            component: Keep this component only.
            since: Keep records with ``t >= since``.
            until: Keep records with ``t <= until``.
            limit: Maximum number of records returned.
            offset: Number of records skipped.

        Returns:
            :class:`~simulatte.events.LogEvent` instances with their original ``t`` and ``seq``.

        Raises:
            RuntimeError: The sink is not attached or already closed.
        """
        sql = "SELECT seq, t, level, message, component, data_json FROM events WHERE env_id = ? AND kind = 'log'"
        params: list[Any] = [self._env_id]
        if level is not None:
            sql += " AND level = ?"
            params.append(level.upper())
        if component is not None:
            sql += " AND component = ?"
            params.append(component)
        if since is not None:
            sql += " AND t >= ?"
            params.append(since)
        if until is not None:
            sql += " AND t <= ?"
            params.append(until)
        sql += " ORDER BY t, seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        if offset > 0:
            if limit is None:
                sql += " LIMIT -1"
            sql += " OFFSET ?"
            params.append(offset)
        rows = self.execute_sql(sql, tuple(params))
        return [
            LogEvent(
                t=row["t"],
                seq=row["seq"],
                level=row["level"],
                message=row["message"],
                component=row["component"],
                extra=FrozenMap(json.loads(row["data_json"])),
            )
            for row in rows
        ]

    def execute_sql(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Execute `sql` with `params` on the database and return the rows (``sqlite3.Row``, indexable by name).

        Raises:
            RuntimeError: The sink is not attached or already closed.
        """
        with self._lock:
            cursor = self._require_open().execute(sql, params)
            try:
                return cursor.fetchall()
            finally:
                cursor.close()


class HistorySink(LogSink):
    """Keep the latest `maxlen` ``log`` records in memory, oldest first (``env.log_history``).

    Iterating yields :class:`~simulatte.events.LogEvent` instances. After :meth:`close` the records stay
    available, and no new record is kept.

    Args:
        maxlen: Number of records kept; older ones are dropped.
        level: Lowest level kept.
        components: When given, only records of these components are kept.
        exclude: Records of these components are dropped.
    """

    def __init__(
        self,
        maxlen: int = 1000,
        *,
        level: str = "INFO",
        components: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> None:
        super().__init__(level=level, components=components, exclude=exclude)
        self._buffer: deque[LogEvent] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int | None:
        """Number of records kept."""
        return self._buffer.maxlen

    def _write_log(self, event: LogEvent) -> None:
        self._buffer.append(event)

    def __iter__(self) -> Iterator[LogEvent]:
        return iter(tuple(self._buffer))

    def __len__(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        """Drop every record."""
        self._buffer.clear()

    def query(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[LogEvent]:
        """The kept records matching every given filter, oldest first.

        Args:
            level: Keep this level only (case-insensitive), e.g. ``"ERROR"``.
            component: Keep this component only, e.g. ``"FleetCoordinator"``.
            since: Keep records with ``t >= since``.
            until: Keep records with ``t <= until``.
        """
        level_upper = level.upper() if level else None
        return [
            e
            for e in self._buffer
            if (level_upper is None or e.level == level_upper)
            and (not component or e.component == component)
            and (since is None or e.t >= since)
            and (until is None or e.t <= until)
        ]
