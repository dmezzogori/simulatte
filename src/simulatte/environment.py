from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import simpy

from simulatte.logger import EventHistoryBuffer, SimLogger

if TYPE_CHECKING:
    pass


class Environment(simpy.Environment):
    """
    Thin wrapper around ``simpy.Environment`` with integrated logging.

    Historically this was a global singleton; we now allow callers to
    instantiate environments explicitly so simulations can coexist in the
    same process.

    Each environment has its own logger that:
    - Automatically includes simulation time in log output
    - Supports JSON or text output format
    - Maintains an in-memory history buffer
    - Supports per-component filtering
    """

    def __init__(
        self,
        *,
        log_file: str | Path | None = None,
        log_format: Literal["text", "json"] = "text",
        log_history_size: int = 1000,
        log_db_path: str | Path | None = None,
    ) -> None:
        """Initialize the simulation environment.

        Args:
            log_file: Optional file path for log output (defaults to stderr)
            log_format: Output format ("text" or "json")
            log_history_size: Maximum number of events to keep in history buffer
            log_db_path: Optional SQLite database path for persistent event storage.
                         If provided, events are stored in both memory buffer and SQLite.
        """
        super().__init__()
        self._logger = SimLogger(
            env=self,
            log_file=log_file,
            log_format=log_format,
            history_size=log_history_size,
            db_path=log_db_path,
        )

    def step(self) -> None:
        """
        Process the next event in the queue.

        If user interrupts the simulation via KeyboardInterrupt
        raise a StopSimulation exception to gently pause the simulation.
        """

        try:
            super().step()
        except KeyboardInterrupt:  # pragma: no cover
            raise simpy.core.StopSimulation("KeyboardInterrupt")

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
