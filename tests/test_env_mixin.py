"""Tests for EnvMixin."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.utils import EnvMixin


class TestEnvMixin:
    """Tests for EnvMixin environment access."""

    def test_env_mixin_provides_environment(self) -> None:
        """EnvMixin should provide access to a default Environment."""

        class MyClass(EnvMixin):
            def __init__(self) -> None:
                super().__init__()

        obj = MyClass()

        assert hasattr(obj, "env")
        assert isinstance(obj.env, Environment)

    def test_env_mixin_returns_default_env(self) -> None:
        """EnvMixin.env should reuse the module-level default Environment."""

        class MyClass(EnvMixin):
            def __init__(self) -> None:
                super().__init__()

        obj1 = MyClass()
        obj2 = MyClass()

        assert obj1.env is obj2.env

    def test_env_mixin_combined_with_identifiable(self) -> None:
        """EnvMixin should work with IdentifiableMixin."""
        from simulatte.utils import IdentifiableMixin

        class MyClass(IdentifiableMixin, EnvMixin):
            def __init__(self) -> None:
                IdentifiableMixin.__init__(self)
                EnvMixin.__init__(self)

        obj = MyClass()

        assert obj.id == 0
        assert isinstance(obj.env, Environment)

    def test_env_mixin_environment_is_functional(self) -> None:
        """Environment obtained via EnvMixin should be functional."""

        class MyClass(EnvMixin):
            def __init__(self) -> None:
                super().__init__()

        obj = MyClass()
        obj.env.run(until=10)

        assert obj.env.now == 10
