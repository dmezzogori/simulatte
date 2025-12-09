from __future__ import annotations


from simulatte.environment import Environment

# Module-level default environment, replaceable for tests or multiple simulations
_DEFAULT_ENV: Environment | None = None


def set_default_env(env: Environment | None) -> None:
    """Set or clear the module-level default environment."""

    global _DEFAULT_ENV
    _DEFAULT_ENV = env


def get_default_env() -> Environment:
    """Return the current default environment, creating one if needed."""

    global _DEFAULT_ENV
    if _DEFAULT_ENV is None:
        _DEFAULT_ENV = Environment()
    return _DEFAULT_ENV


class EnvMixin:
    """Provide an environment reference, defaulting to the module-level env."""

    def __init__(self, env: Environment | None = None) -> None:
        self.env = env if env is not None else get_default_env()
