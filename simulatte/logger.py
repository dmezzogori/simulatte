from __future__ import annotations

import sys

from loguru import logger
from simulatte.environment import Environment

# Initialize the logger
logger.remove()

fmt = "<green>{extra[now]: <8}</green> || <level>{message}</level>"

logger.add(
    sys.stderr,
    format=fmt,
    colorize=True,
    filter="simulatte",
    level="DEBUG",
)


# Patch the logger to add the current simulation time to the extra field
def now(record):
    seconds = Environment().now
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    record["extra"].update(now=f"{days:02}d {int(hours % 24):02d}:{int(minutes % 60):02d}:{(seconds % 60):02.2f}")


logger = logger.patch(now)

logger.debug("Logger initialized")
