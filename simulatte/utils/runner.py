from __future__ import annotations

import random
from multiprocessing import Pool


class Runner:
    def __init__(self, *, n_jobs: int = 1) -> None:
        self.n_jobs = n_jobs
        self.seeds: list[int] = []
        self.i = 0
        self.results = []

    def __call__(self, worker, parallel=False) -> None:
        self.results = self._parallel(worker) if parallel else self._sequential(worker)

    def __iter__(self):
        self.seeds = [random.randint(1, int(1000)) for _ in range(self.n_jobs)]
        self.i = 0
        return self

    def __next__(self):
        if self.i >= self.n_jobs:
            raise StopIteration
        i, seed = self.i, self.seeds[self.i]
        self.i += 1
        return i, seed

    def _parallel(self, worker):
        results = []
        with Pool(processes=4) as pool:
            multiple_results = pool.map_async(worker, self, callback=results.extend)
            multiple_results.wait()

        multiple_results.get()
        return results

    def _sequential(self, worker) -> list:
        return [worker(args) for args in self]
