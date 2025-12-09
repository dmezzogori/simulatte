from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from simpy import Event, Process


def as_process[ProcessReturn](f: Callable[..., Generator[Event, None, ProcessReturn]]) -> Callable[..., Process]:
    """
    Decorator to register a generator as a process in the environment.
    """

    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        from simulatte.environment import Environment

        return Environment().process(f(*args, **kwargs))

    return wrapper
