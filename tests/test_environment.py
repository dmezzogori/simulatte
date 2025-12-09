"""Tests for the simulation Environment."""

from __future__ import annotations

import simpy

from simulatte.environment import Environment
from simulatte.utils.singleton import Singleton


class TestEnvironment:
    """Tests for the singleton simulation Environment."""

    def test_environment_is_singleton(self) -> None:
        """Environment should be a singleton."""
        env1 = Environment()
        env2 = Environment()

        assert env1 is env2

    def test_environment_is_simpy_environment(self) -> None:
        """Environment should inherit from simpy.Environment."""
        env = Environment()

        assert isinstance(env, simpy.Environment)

    def test_environment_initial_time_is_zero(self) -> None:
        """New environment should start at time 0."""
        env = Environment()

        assert env.now == 0

    def test_environment_step_advances_time(self) -> None:
        """step() should process events and advance time."""
        env = Environment()

        # Schedule an event
        def process(env: simpy.Environment):
            yield env.timeout(10)

        env.process(process(env))
        # First step starts the process, second step processes the timeout
        env.step()
        env.step()

        assert env.now == 10

    def test_environment_process_registration(self) -> None:
        """Environment should be able to register processes."""
        env = Environment()
        results = []

        def my_process(env: simpy.Environment):
            results.append("started")
            yield env.timeout(5)
            results.append("completed")

        env.process(my_process(env))
        env.run()

        assert results == ["started", "completed"]

    def test_new_environment_after_clear(self) -> None:
        """After Singleton.clear(), a new Environment instance should be created."""
        env1 = Environment()
        env1.run(until=100)

        Singleton.clear()
        env2 = Environment()

        assert env1 is not env2
        assert env2.now == 0

    def test_environment_run_until(self) -> None:
        """Environment should be able to run until a specific time."""
        env = Environment()
        env.run(until=50)

        assert env.now == 50

    def test_environment_multiple_processes(self) -> None:
        """Environment should handle multiple concurrent processes."""
        env = Environment()
        results = []

        def process_a(env: simpy.Environment):
            yield env.timeout(5)
            results.append("A")

        def process_b(env: simpy.Environment):
            yield env.timeout(3)
            results.append("B")

        env.process(process_a(env))
        env.process(process_b(env))
        env.run()

        # B finishes first (timeout 3), then A (timeout 5)
        assert results == ["B", "A"]
