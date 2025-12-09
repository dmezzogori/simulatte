from __future__ import annotations

import sys

from loguru import logger as _logger

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


# Patch the logger to add the current simulation time to the extra field
def now(record):
    from simulatte.environment import Environment

    seconds = Environment().now
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    record["extra"].update(now=f"{days:02}d {int(hours % 24):02d}:{int(minutes % 60):02d}:{(seconds % 60):02.2f}")


logger = _logger.patch(now)

logger.debug("Logger initialized")
