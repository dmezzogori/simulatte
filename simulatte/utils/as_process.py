from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Generator

    from simpy import Event, Process

_ProcessReturn = TypeVar("_ProcessReturn")


def as_process(f: Callable[..., Generator[Event, None, _ProcessReturn]]) -> Callable[..., Process]:
    """
    Decorator to register a generator as a process in the environment.
    """

    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        from simulatte.environment import Environment

        return Environment().process(f(*args, **kwargs))

    return wrapper
