"""Runner to execute multiple simulations with different seeds."""

from __future__ import annotations

import multiprocessing
import random
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tqdm import tqdm

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.typing import Builder


class Runner[S, T]:
    """Manage repeated simulations with configurable builder and seeds.

    The builder callable should accept an `env: Environment` parameter.

    Supports per-simulation log files when running in parallel, enabling
    separate log output for each simulation instance.
    """

    def __init__(
        self,
        *,
        builder: Builder[S],
        seeds: Sequence[int],
        parallel: bool = False,
        progress: bool | None = None,
        extract_fn: Callable[[S], T],
        n_jobs: int | None = None,
        log_dir: Path | None = None,
        log_format: Literal["text", "json"] = "text",
    ) -> None:
        """Initialize the runner.

        Args:
            builder: Callable that accepts env: Environment and returns a system
            seeds: Sequence of random seeds for each simulation run
            parallel: Whether to run simulations in parallel using multiprocessing
            progress: Whether to show a progress bar (None = auto, based on stderr TTY)
            extract_fn: Function to extract results from the system after simulation
            n_jobs: Number of parallel workers (defaults to CPU count)
            log_dir: Optional directory for per-simulation log files.
                     Each simulation will create a file named sim_XXXX_seed_YYYY.log
            log_format: Log output format ("text" or "json")
        """
        self.builder = builder
        self.seeds = seeds
        self.parallel = parallel
        self.progress = progress
        self.extract_fn = extract_fn
        self.n_jobs = n_jobs
        self.log_dir = log_dir
        self.log_format = log_format

    def _run_single(self, args: tuple[int, int, float]) -> tuple[int, T]:
        """Run a single simulation.

        Args:
            args: Tuple of (run_id, seed, until)

        Returns:
            Tuple of (run_id, extracted result)
        """
        run_id, seed, until = args
        random.seed(seed)

        # Determine log file path
        log_file = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self.log_dir / f"sim_{run_id:04d}_seed_{seed}.log"

        with Environment(
            log_file=log_file,
            log_format=self.log_format,
        ) as env:
            system = self.builder(env=env)
            env.run(until=until)
            return run_id, self.extract_fn(system)

    def run(self, until: float) -> list[T]:
        """Run all simulations.

        Args:
            until: Simulation end time

        Returns:
            List of extracted results, one per seed
        """
        args = [(i, seed, until) for i, seed in enumerate(self.seeds)]
        progress_enabled = self.progress if self.progress is not None else sys.stderr.isatty()

        if not self.parallel:
            results: list[T | None] = [None] * len(args)
            for run_id, result in tqdm(
                map(self._run_single, args),
                total=len(args),
                disable=not progress_enabled,
                desc="Simulations",
                unit="run",
            ):
                results[run_id] = result

            ordered_results: list[T] = []
            for result in results:
                if result is None:  # pragma: no cover
                    raise RuntimeError("Missing simulation result. This should not happen.")
                ordered_results.append(result)
            return ordered_results

        with multiprocessing.get_context("spawn").Pool(
            processes=self.n_jobs,
        ) as pool:
            results: list[T | None] = [None] * len(args)
            chunksize = max(1, len(args) // ((self.n_jobs or multiprocessing.cpu_count()) * 4))
            for run_id, result in tqdm(
                pool.imap_unordered(self._run_single, args, chunksize=chunksize),
                total=len(args),
                disable=not progress_enabled,
                desc="Simulations",
                unit="run",
            ):
                results[run_id] = result

            ordered_results: list[T] = []
            for result in results:
                if result is None:  # pragma: no cover
                    raise RuntimeError("Missing simulation result. This should not happen.")
                ordered_results.append(result)
            return ordered_results
