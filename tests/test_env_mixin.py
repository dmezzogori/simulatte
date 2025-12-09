"""Tests for EnvMixin."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.utils import EnvMixin


class TestEnvMixin:
    """Tests for EnvMixin environment access."""

    def test_env_mixin_requires_explicit_env(self) -> None:
        """EnvMixin should complain if env is omitted."""

        class MyClass(EnvMixin):
            def __init__(self) -> None:
                super().__init__(env=None)  # type: ignore[arg-type]

        try:
            MyClass()
        except ValueError as exc:
            assert "Environment must be provided" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("EnvMixin accepted a missing env")

    def test_env_mixin_uses_provided_env(self, env: Environment) -> None:
        """EnvMixin should keep the provided Environment."""

        class MyClass(EnvMixin):
            def __init__(self, env: Environment) -> None:
                super().__init__(env=env)

        obj = MyClass(env)

        assert obj.env is env

    def test_env_mixin_combined_with_identifiable(self, env: Environment) -> None:
        """EnvMixin should work with IdentifiableMixin."""
        from simulatte.utils import IdentifiableMixin

        class MyClass(IdentifiableMixin, EnvMixin):
            def __init__(self, env: Environment) -> None:
                IdentifiableMixin.__init__(self)
                EnvMixin.__init__(self, env=env)

        obj = MyClass(env)

        assert obj.id == 0
        assert obj.env is env

    def test_env_mixin_environment_is_functional(self, env: Environment) -> None:
        """Environment obtained via EnvMixin should be functional."""

        class MyClass(EnvMixin):
            def __init__(self, env: Environment) -> None:
                super().__init__(env=env)

        obj = MyClass(env)
        obj.env.run(until=10)

        assert obj.env.now == 10
