from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpy import Process
    from typing import Generator, Callable


def make_process(generator: Generator) -> Process:
    from simulatte.environment import Environment

    return Environment().process(generator)


def as_process(f: Callable[..., Generator]) -> Callable[..., Process]:
    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        return make_process(f(*args, **kwargs))

    return wrapper
