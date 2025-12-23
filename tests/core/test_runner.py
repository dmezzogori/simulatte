from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.runner import Runner
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:
    pass


class SimpleSystem:
    """A minimal system for testing Runner."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.sf = ShopFloor(env=env)
        self.server = Server(env=env, capacity=1, shopfloor=self.sf)
        self.final_time: float | None = None

    def record_time(self) -> None:
        self.final_time = self.env.now


def simple_builder(env: Environment) -> SimpleSystem:
    return SimpleSystem(env=env)


def extract_time(system: SimpleSystem) -> float:
    return system.env.now


def test_runner_sequential_single_seed() -> None:
    runner = Runner(
        builder=simple_builder,
        seeds=[42],
        parallel=False,
        extract_fn=extract_time,
    )

    results = runner.run(until=100.0)

    assert len(results) == 1
    assert results[0] == 100.0


def test_runner_sequential_multiple_seeds() -> None:
    runner = Runner(
        builder=simple_builder,
        seeds=[1, 2, 3],
        parallel=False,
        extract_fn=extract_time,
    )

    results = runner.run(until=50.0)

    assert len(results) == 3
    assert all(r == 50.0 for r in results)


def test_runner_extract_fn_called() -> None:
    call_count = 0

    def counting_extract(system: SimpleSystem) -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    runner = Runner(
        builder=simple_builder,
        seeds=[1, 2, 3],
        parallel=False,
        extract_fn=counting_extract,
    )

    results = runner.run(until=10.0)

    assert results == [1, 2, 3]
    assert call_count == 3


def test_runner_seed_affects_random_state() -> None:
    import random

    def random_builder(env: Environment) -> tuple[Environment, float]:
        # Capture a random value right after seed is set
        return (env, random.random())

    def extract_random(system: tuple[Environment, float]) -> float:
        return system[1]

    runner = Runner(
        builder=random_builder,
        seeds=[42, 42],  # Same seed twice
        parallel=False,
        extract_fn=extract_random,
    )

    results = runner.run(until=1.0)

    # Same seed should produce same random value
    assert results[0] == results[1]


def test_runner_different_seeds_different_results() -> None:
    import random

    def random_builder(env: Environment) -> tuple[Environment, float]:
        return (env, random.random())

    def extract_random(system: tuple[Environment, float]) -> float:
        return system[1]

    runner = Runner(
        builder=random_builder,
        seeds=[1, 2],  # Different seeds
        parallel=False,
        extract_fn=extract_random,
    )

    results = runner.run(until=1.0)

    # Different seeds should produce different random values
    assert results[0] != results[1]


def test_runner_parallel_smoke_test() -> None:
    """Basic smoke test for parallel execution."""
    runner = Runner(
        builder=simple_builder,
        seeds=[1, 2],
        parallel=True,
        extract_fn=extract_time,
        n_jobs=2,
    )

    results = runner.run(until=10.0)

    assert len(results) == 2
    assert all(r == 10.0 for r in results)


def test_runner_parallel_with_n_jobs_none() -> None:
    """Parallel execution with default n_jobs."""
    runner = Runner(
        builder=simple_builder,
        seeds=[1],
        parallel=True,
        extract_fn=extract_time,
        n_jobs=None,
    )

    results = runner.run(until=5.0)

    assert len(results) == 1
    assert results[0] == 5.0


def test_runner_empty_seeds() -> None:
    runner = Runner(
        builder=simple_builder,
        seeds=[],
        parallel=False,
        extract_fn=extract_time,
    )

    results = runner.run(until=10.0)

    assert results == []
