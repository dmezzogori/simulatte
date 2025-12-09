"""Tests for the Singleton metaclass."""

from __future__ import annotations

from simulatte.utils.singleton import Singleton


class TestSingleton:
    """Tests for Singleton metaclass behavior."""

    def test_singleton_returns_same_instance(self) -> None:
        """A singleton class should always return the same instance."""

        class MySingleton(metaclass=Singleton):
            def __init__(self, value: int = 0) -> None:
                self.value = value

        instance1 = MySingleton(value=1)
        instance2 = MySingleton(value=2)

        assert instance1 is instance2
        assert instance1.value == 1  # First value preserved

    def test_singleton_clear_allows_new_instance(self) -> None:
        """After clear(), a new instance should be created."""

        class MySingleton(metaclass=Singleton):
            def __init__(self, value: int = 0) -> None:
                self.value = value

        instance1 = MySingleton(value=1)
        Singleton.clear()
        instance2 = MySingleton(value=2)

        assert instance1 is not instance2
        assert instance2.value == 2

    def test_multiple_singleton_classes_independent(self) -> None:
        """Different singleton classes should have independent instances."""

        class SingletonA(metaclass=Singleton):
            pass

        class SingletonB(metaclass=Singleton):
            pass

        instance_a = SingletonA()
        instance_b = SingletonB()

        assert instance_a is not instance_b
        assert type(instance_a) is SingletonA
        assert type(instance_b) is SingletonB

    def test_singleton_with_args_and_kwargs(self) -> None:
        """Singleton should properly handle constructor arguments."""

        class MySingleton(metaclass=Singleton):
            def __init__(self, a: int, b: str, c: float = 1.0) -> None:
                self.a = a
                self.b = b
                self.c = c

        instance = MySingleton(1, "test", c=2.5)

        assert instance.a == 1
        assert instance.b == "test"
        assert instance.c == 2.5

    def test_clear_removes_all_instances(self) -> None:
        """Singleton.clear() should remove all singleton instances."""

        class SingletonA(metaclass=Singleton):
            pass

        class SingletonB(metaclass=Singleton):
            pass

        SingletonA()
        SingletonB()

        assert len(Singleton._instances) >= 2
        Singleton.clear()
        assert len(Singleton._instances) == 0
