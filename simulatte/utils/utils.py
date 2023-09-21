from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from simpy import Process


def make_process(generator: Generator) -> Process:
    """
    Register a generator as a process in the environment.
    """

    from simulatte.environment import Environment

    return Environment().process(generator)


def as_process(f: Callable[..., Generator]) -> Callable[..., Process]:
    """
    Decorator to register a generator as a process in the environment.
    """

    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        return make_process(f(*args, **kwargs))

    return wrapper
