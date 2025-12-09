"""Tests for the as_process decorator."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from simulatte.environment import Environment
from simulatte.utils import as_process

if TYPE_CHECKING:
    from collections.abc import Generator


class TestAsProcess:
    """Tests for the as_process decorator."""

    def test_as_process_returns_simpy_process(self, env: Environment) -> None:
        """Decorated function should return a SimPy Process."""

        @as_process
        def my_generator() -> Generator[simpy.Event]:
            yield env.timeout(1)

        result = my_generator(env=env)

        assert isinstance(result, simpy.Process)

    def test_as_process_executes_generator(self, env: Environment) -> None:
        """Decorated function should execute the generator."""
        results = []

        @as_process
        def my_generator() -> Generator[simpy.Event]:
            results.append("started")
            yield env.timeout(1)
            results.append("completed")

        my_generator(env=env)
        env.run()

        assert results == ["started", "completed"]

    def test_as_process_preserves_function_name(self, env: Environment) -> None:
        """Decorator should preserve the original function name."""

        @as_process
        def my_special_process() -> Generator[simpy.Event]:
            yield env.timeout(1)

        my_special_process(env=env)

        assert my_special_process.__name__ == "my_special_process"

    def test_as_process_with_arguments(self, env: Environment) -> None:
        """Decorated function should accept arguments."""
        results = []

        @as_process
        def my_generator(value: int, multiplier: int = 2) -> Generator[simpy.Event, None, int]:
            yield env.timeout(1)
            result = value * multiplier
            results.append(result)
            return result

        my_generator(5, multiplier=3, env=env)
        env.run()

        assert results == [15]

    def test_as_process_with_self(self, env: Environment) -> None:
        """Decorated method should work as instance method."""
        results = []

        class MyClass:
            def __init__(self, value: int) -> None:
                self.value = value
                self.env = env

            @as_process
            def my_method(self) -> Generator[simpy.Event]:
                yield env.timeout(1)
                results.append(self.value)

        obj = MyClass(42)
        obj.my_method()
        env.run()

        assert results == [42]

    def test_as_process_multiple_calls(self, env: Environment) -> None:
        """Multiple calls to decorated function should create multiple processes."""
        results = []

        @as_process
        def my_generator(value: int) -> Generator[simpy.Event]:
            yield env.timeout(value)
            results.append(value)

        my_generator(3, env=env)
        my_generator(1, env=env)
        my_generator(2, env=env)
        env.run()

        # Results should be in order of timeout completion
        assert results == [1, 2, 3]
