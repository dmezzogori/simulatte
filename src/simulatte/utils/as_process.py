from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

from simulatte.utils.env_mixin import _require_env

if TYPE_CHECKING:
    from collections.abc import Generator

    from simpy import Event, Process


def as_process[ProcessReturn](f: Callable[..., Generator[Event, None, ProcessReturn]]) -> Callable[..., Process]:
    """
    Decorator to register a generator as a process in the environment.

    Uses the ``env`` attribute of the first argument when present, otherwise
    expects an ``env`` keyword argument. No implicit default environment is kept.
    """

    @wraps(f)
    def wrapper(*args, **kwargs) -> Process:
        # If decorated function is a method, the first arg is ``self``.
        env = getattr(args[0], "env", None) if args else None

        # Allow callers of free functions to pass env explicitly
        explicit_env = kwargs.pop("env", None)
        if explicit_env is not None:
            env = explicit_env

        env = _require_env(env)
        return env.process(f(*args, **kwargs))

    return wrapper
