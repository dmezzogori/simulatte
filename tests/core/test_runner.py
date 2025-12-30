from __future__ import annotations

from pathlib import Path
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


def random_value_builder(env: Environment) -> tuple[Environment, float]:
    import random

    return (env, random.random())


def extract_random_value(system: tuple[Environment, float]) -> float:
    return system[1]


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


def test_runner_parallel_preserves_seed_order() -> None:
    import random

    seeds = [1, 2, 3, 4]
    runner = Runner(
        builder=random_value_builder,
        seeds=seeds,
        parallel=True,
        extract_fn=extract_random_value,
        n_jobs=2,
    )

    results = runner.run(until=1.0)

    assert results == [random.Random(seed).random() for seed in seeds]


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


# =============================================================================
# Tests for logging integration
# =============================================================================


def test_runner_log_dir_creates_files(tmp_path: Path) -> None:
    """Runner should create per-simulation log files when log_dir is specified."""

    from simulatte.logger import SimLogger

    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")

        def logging_builder(env: Environment) -> SimpleSystem:
            system = SimpleSystem(env=env)
            env.info("Simulation started")
            return system

        runner = Runner(
            builder=logging_builder,
            seeds=[1, 2, 3],
            parallel=False,
            extract_fn=extract_time,
            log_dir=tmp_path,
        )

        runner.run(until=10.0)

        # Check that 3 log files were created
        log_files = list(tmp_path.glob("sim_*.log"))
        assert len(log_files) == 3

        # Check naming convention
        expected_files = [
            tmp_path / "sim_0000_seed_1.log",
            tmp_path / "sim_0001_seed_2.log",
            tmp_path / "sim_0002_seed_3.log",
        ]
        for expected in expected_files:
            assert expected.exists()
    finally:
        SimLogger.set_level(original_level)


def test_runner_log_format_json(tmp_path: Path) -> None:
    """Runner should support JSON log format."""
    import json

    from simulatte.logger import SimLogger

    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")

        def logging_builder(env: Environment) -> SimpleSystem:
            system = SimpleSystem(env=env)
            env.info("Simulation started", component="Builder")
            return system

        runner = Runner(
            builder=logging_builder,
            seeds=[42],
            parallel=False,
            extract_fn=extract_time,
            log_dir=tmp_path,
            log_format="json",
        )

        runner.run(until=10.0)

        log_file = tmp_path / "sim_0000_seed_42.log"
        assert log_file.exists()

        content = log_file.read_text().strip()
        data = json.loads(content)
        assert data["level"] == "INFO"
        assert data["message"] == "Simulation started"
        assert data["component"] == "Builder"
    finally:
        SimLogger.set_level(original_level)


def test_runner_without_log_dir() -> None:
    """Runner should work without log_dir (logs to stderr)."""
    runner = Runner(
        builder=simple_builder,
        seeds=[1],
        parallel=False,
        extract_fn=extract_time,
        log_dir=None,
    )

    results = runner.run(until=10.0)
    assert len(results) == 1
    assert results[0] == 10.0


def test_runner_log_dir_creates_directory(tmp_path: Path) -> None:
    """Runner should create log_dir if it doesn't exist."""

    from simulatte.logger import SimLogger

    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")

        def logging_builder(env: Environment) -> SimpleSystem:
            system = SimpleSystem(env=env)
            env.info("Simulation started")
            return system

        nested_dir = tmp_path / "nested" / "logs"
        assert not nested_dir.exists()

        runner = Runner(
            builder=logging_builder,
            seeds=[1],
            parallel=False,
            extract_fn=extract_time,
            log_dir=nested_dir,
        )

        runner.run(until=10.0)

        assert nested_dir.exists()
        assert (nested_dir / "sim_0000_seed_1.log").exists()
    finally:
        SimLogger.set_level(original_level)
