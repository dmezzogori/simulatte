"""Per-environment structured logging with history buffer and component filtering."""

from __future__ import annotations

import json
import sys
import uuid
import weakref
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from collections.abc import Iterator

from loguru import logger as _logger

if TYPE_CHECKING:
    from simulatte.environment import Environment


def _patch_loguru_default_sink() -> None:
    """Avoid double-logging per-environment records to Loguru's default sink.

    Loguru installs a default stderr sink on import. Since SimLogger registers
    its own sink (stderr or file) per Environment, keeping Loguru's default sink
    enabled would duplicate output. We only patch the default sink when Loguru
    still appears to be in its pristine, unconfigured state.
    """
    try:
        core = getattr(_logger, "_core", None)
        handlers = getattr(core, "handlers", None)
        if not isinstance(handlers, dict) or len(handlers) != 1:
            return

        handler0 = handlers.get(0)
        if handler0 is None:
            return

        sink = getattr(handler0, "_sink", None)
        stream = getattr(sink, "_stream", None)
        if stream is not sys.stderr:
            return

        level_no = getattr(handler0, "_levelno", 10)  # Default is DEBUG.
        _logger.remove(0)
        _logger.add(
            sys.stderr,
            level=level_no,
            filter=lambda r: r["extra"].get("env_id") is None,
        )
    except Exception:
        # Never fail import-time due to Loguru internals changing.
        return


_patch_loguru_default_sink()


def _format_sim_time(seconds: float) -> str:
    """Format simulation time as 'DDd HH:MM:SS.MS'."""
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    return f"{days:02}d {int(hours % 24):02d}:{int(minutes % 60):02d}:{(seconds % 60):02.2f}"


@dataclass(frozen=True, slots=True)
class LogEvent:
    """Immutable record of a logged event."""

    timestamp: float
    level: str
    message: str
    component: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class EventHistoryBuffer:
    """Fixed-size ring buffer for log events."""

    __slots__ = ("_buffer",)

    def __init__(self, max_size: int = 1000) -> None:
        self._buffer: deque[LogEvent] = deque(maxlen=max_size)

    def append(self, event: LogEvent) -> None:
        """Add an event to the buffer."""
        self._buffer.append(event)

    def __iter__(self) -> Iterator[LogEvent]:
        yield from self._buffer

    def __len__(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        """Clear all events from the buffer."""
        self._buffer.clear()

    def query(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[LogEvent]:
        """Query history with optional filters.

        Args:
            level: Filter by log level (e.g., "INFO", "ERROR")
            component: Filter by component name (e.g., "Server")
            since: Include events with timestamp >= since
            until: Include events with timestamp <= until

        Returns:
            List of matching LogEvent objects
        """
        results = list(self._buffer)
        if level:
            level_upper = level.upper()
            results = [e for e in results if e.level == level_upper]
        if component:
            results = [e for e in results if e.component == component]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        if until is not None:
            results = [e for e in results if e.timestamp <= until]
        return results


# Level name to numeric priority mapping (matching loguru)
_LEVEL_PRIORITY = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class SimLogger:
    """Per-environment logger with structured output and history buffer.

    Each Environment instance has its own SimLogger, which:
    - Automatically includes simulation time in log output
    - Supports JSON or text output format
    - Maintains an in-memory history buffer
    - Supports per-component filtering
    """

    _global_level: ClassVar[str] = "INFO"

    __slots__ = (
        "_env",
        "_log_file",
        "_log_format",
        "_history",
        "_component_filters",
        "_handler_id",
        "_env_id",
        "_finalizer",
    )

    def __init__(
        self,
        *,
        env: Environment,
        log_file: str | Path | None = None,
        log_format: Literal["text", "json"] = "text",
        history_size: int = 1000,
    ) -> None:
        """Initialize a per-environment logger.

        Args:
            env: The simulation environment this logger is attached to
            log_file: Optional file path for log output (defaults to stderr)
            log_format: Output format ("text" or "json")
            history_size: Maximum number of events to keep in history buffer
        """
        self._env = env
        self._log_file = Path(log_file) if log_file else None
        self._log_format = log_format
        self._history = EventHistoryBuffer(max_size=history_size)
        self._component_filters: dict[str, bool] = {}
        self._env_id = uuid.uuid4().hex
        self._handler_id: int | None = None
        self._setup_handler()
        handler_id = self._handler_id
        if handler_id is None:  # pragma: no cover
            self._finalizer = weakref.finalize(env, lambda: None)
        else:
            self._finalizer = weakref.finalize(env, _finalize_handler, handler_id)

    @classmethod
    def set_level(cls, level: str) -> None:
        """Set global log level for all environments.

        Args:
            level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        cls._global_level = level.upper()

    @classmethod
    def get_level(cls) -> str:
        """Get current global log level."""
        return cls._global_level

    def enable_component(self, component: str) -> None:
        """Enable logging for a specific component type.

        Args:
            component: Component class name (e.g., "Server", "ProductionJob")
        """
        self._component_filters[component] = True

    def disable_component(self, component: str) -> None:
        """Disable logging for a specific component type.

        Args:
            component: Component class name (e.g., "Server", "ProductionJob")
        """
        self._component_filters[component] = False

    def _should_log(self, component: str | None) -> bool:
        """Check if logging is enabled for this component."""
        if not self._component_filters:
            return True  # No filters = log everything
        if component is None:
            return True  # No component specified = log
        return self._component_filters.get(component, True)

    def _should_log_level(self, level: str) -> bool:
        """Check if the given level should be logged based on global level."""
        level_priority = _LEVEL_PRIORITY.get(level.upper(), 0)
        global_priority = _LEVEL_PRIORITY.get(self._global_level, 0)
        return level_priority >= global_priority

    def _format_message(self, record: dict[str, Any]) -> str:
        """Format a log record for output."""
        extra = record["extra"]
        sim_time = extra.get("sim_time", 0)
        sim_time_formatted = _format_sim_time(float(sim_time))
        component = extra.get("component") or "-"
        level_name = record["level"].name
        message = record["message"]

        if self._log_format == "json":
            data = {
                "sim_time": sim_time,
                "sim_time_formatted": sim_time_formatted,
                "wall_time": datetime.now(UTC).isoformat(),
                "level": level_name,
                "message": message,
                "component": component if component != "-" else None,
                "extra": {k: v for k, v in extra.items() if k not in ("sim_time", "component", "env_id")},
            }
            return json.dumps(data)
        else:
            return f"{sim_time_formatted} | {level_name:<8} | {component:<12} | {message}"

    def _make_sink(self) -> Any:
        """Create a custom sink that handles formatting."""
        log_file = self._log_file

        def sink(message: Any) -> None:
            record = message.record
            formatted = self._format_message(record)
            if log_file is not None:
                with open(log_file, "a") as f:
                    f.write(formatted + "\n")
            else:
                sys.stderr.write(formatted + "\n")
                sys.stderr.flush()

        return sink

    def _setup_handler(self) -> None:
        """Configure loguru handler for this environment."""
        self._handler_id = _logger.add(
            self._make_sink(),
            format="{message}",  # We handle formatting in the sink
            level="TRACE",  # Accept all levels, we filter ourselves
            filter=lambda r: r["extra"].get("env_id") == self._env_id,
            enqueue=False,  # Our sink handles output directly
        )

    def _log(
        self,
        level: str,
        message: str,
        *,
        component: str | None = None,
        **extra: Any,
    ) -> None:
        """Internal logging method."""
        if not self._should_log(component):
            return
        if not self._should_log_level(level):
            return

        sim_time = self._env.now

        # Add to history
        event = LogEvent(
            timestamp=sim_time,
            level=level.upper(),
            message=message,
            component=component,
            extra=dict(extra) if extra else {},
        )
        self._history.append(event)

        # Log via loguru
        _logger.bind(
            sim_time=sim_time,
            component=component,
            env_id=self._env_id,
            **extra,
        ).log(level, message)

    def debug(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log a debug message."""
        self._log("DEBUG", message, component=component, **extra)

    def info(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log an info message."""
        self._log("INFO", message, component=component, **extra)

    def warning(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log a warning message."""
        self._log("WARNING", message, component=component, **extra)

    def error(self, message: str, *, component: str | None = None, **extra: Any) -> None:
        """Log an error message."""
        self._log("ERROR", message, component=component, **extra)

    @property
    def history(self) -> EventHistoryBuffer:
        """Access the event history buffer."""
        return self._history

    def close(self) -> None:
        """Clean up loguru handler.

        Should be called when the environment is no longer needed,
        especially important for multiprocessing scenarios.
        """
        if getattr(self, "_finalizer", None) is not None and self._finalizer.alive:
            self._finalizer.detach()
        if self._handler_id is not None:
            try:
                _logger.remove(self._handler_id)
            except ValueError:
                pass  # Handler already removed
            self._handler_id = None


def _finalize_handler(handler_id: int) -> None:
    """Remove a Loguru handler from a weakref finalizer callback."""
    try:
        _logger.remove(handler_id)
    except Exception:
        # Be resilient: handler may already be removed, or Loguru may be torn down.
        return
