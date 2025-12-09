from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

from simulatte.utils.env_mixin import get_default_env

if TYPE_CHECKING:
    from collections.abc import Generator

    from simpy import Event, Process


def as_process[ProcessReturn](f: Callable[..., Generator[Event, None, ProcessReturn]]) -> Callable[..., Process]:
    """
    Decorator to register a generator as a process in the environment.

    Uses the ``env`` attribute of the first argument when present, otherwise
    falls back to the module-level default environment. This keeps processes
    aligned with explicitly injected environments.
    """

    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        # If decorated function is a method, the first arg is ``self``.
        env = getattr(args[0], "env", None) if args else None
        if env is None:
            env = get_default_env()
        return env.process(f(*args, **kwargs))

    return wrapper
