from __future__ import annotations


from simulatte.environment import Environment


def _require_env(env: Environment | None) -> Environment:
    """
    Ensure an environment is provided.

    Explicit wiring is preferred across the codebase; this helper keeps the error
    message consistent when an env is accidentally omitted.
    """

    if env is None:
        raise ValueError("Environment must be provided; no default environment is maintained.")
    return env


class EnvMixin:
    """Provide an environment reference; env must be passed explicitly."""

    def __init__(self, env: Environment | None) -> None:
        self.env = _require_env(env)
