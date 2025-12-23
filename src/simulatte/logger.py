from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from loguru import logger as _logger

if TYPE_CHECKING:
    from loguru import Record

# Initialize the logger
_logger.remove()

fmt = "<green>{extra[now]: <8}</green> || <level>{message}</level>"

_logger.add(
    sys.stderr,
    format=fmt,
    colorize=True,
    filter="simulatte",
    level="DEBUG",
)


def _format_sim_time(seconds: float) -> str:
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    return f"{days:02}d {int(hours % 24):02d}:{int(minutes % 60):02d}:{(seconds % 60):02.2f}"


def _patch_sim_time(record: Record) -> None:
    """Patch the log record with simulation time.

    This avoids any implicit Environment construction. Callers can either:
    - bind an env: `logger.bind(env=env).info(...)`
    - bind a timestamp: `logger.bind(now_seconds=env.now).info(...)`
    """
    extra: dict[Any, Any] = record["extra"]

    seconds: float | None = None

    env = extra.get("env")
    if env is not None:
        env_now = getattr(env, "now", None)
        if isinstance(env_now, int | float):
            seconds = float(env_now)

    if seconds is None:
        now_seconds = extra.get("now_seconds")
        if isinstance(now_seconds, int | float):
            seconds = float(now_seconds)

    extra["now"] = _format_sim_time(seconds) if seconds is not None else "--d --:--:--.--"
    record["extra"] = extra


logger = _logger.patch(_patch_sim_time)

logger.debug("Logger initialized")
