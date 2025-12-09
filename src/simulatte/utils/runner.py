from __future__ import annotations

import random
from collections.abc import Callable
from multiprocessing import Pool
from typing import Self, TypeVar

import numpy as np

Result = TypeVar("Result")
Worker = TypeVar("Worker", bound=Callable[[tuple[int, int]], Result])


class Runner[Worker, Result]:
    """
    Utility class to run a function in parallel or sequentially.
    Useful for running multiple simulations in parallel.

    Attributes:
        n_jobs: The number of jobs to run.
        seeds: The seeds to use for random number generation.
        i: The index of the current job.
        results: The results of the jobs.

    Example:
        ::

            def builder(i: int, seed: int):
                from config import config
                from simulatte.simulation import Simulation

                simulation = Simulation(config=config, seed=seed)
                simulation.run(until=int(60 * 60 * 24 * 10), debug=False)
                return simulation.results

            def worker(args):
                i, seed = args
                return builder(i, seed)

            if __name__ == "__main__":
                from simulatte.utils import Runner

                runner = Runner(n_jobs=4, seed=42)
                runner(worker, parallel=True)
                print(runner.results)
    """

    def __init__(self, *, n_jobs: int = 1, seed: int | None = None) -> None:
        """
        Initialize the runner.
        If a seed is provided, it is used to generate the seeds for the jobs.

        Args:
            n_jobs: The number of jobs to run.
            seed: The seed to use for random number generation.
        """

        self.n_jobs = n_jobs
        self.seeds: list[int] = []
        self.i = 0
        self.results: list[Result] = []

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

    def __call__(self, worker: Worker, parallel=False) -> None:
        """
        Run the worker function in parallel or sequentially.

        Args:
            worker: The function to run.
            parallel: Whether to run the function in parallel or sequentially.
        """

        self.results = self._parallel(worker) if parallel else self._sequential(worker)

    def __iter__(self) -> Self:
        """
        Initialize the iterator, generating the seeds for the jobs.
        """

        self.seeds = [random.randint(1, 1000) for _ in range(self.n_jobs)]
        self.i = 0
        return self

    def __next__(self) -> tuple[int, int]:
        """
        Get the next job index and seed.
        """

        if self.i >= self.n_jobs:
            raise StopIteration
        i, seed = self.i, self.seeds[self.i]
        self.i += 1
        return i, seed

    def _parallel(self, worker: Worker) -> list[Result]:
        """
        Run the worker function in parallel.

        Args:
            worker: The function to run.
        """

        results: list[Result] = []
        with Pool(processes=4) as pool:
            multiple_results = pool.map_async(worker, self, callback=results.extend)
            multiple_results.wait()

        multiple_results.get()
        return results

    def _sequential(self, worker: Worker) -> list[Result]:
        """
        Run the worker function sequentially.

        Args:
            worker: The function to run.
        """

        return [worker(args) for args in self]
