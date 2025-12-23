"""Runner to execute multiple simulations with different seeds."""

from __future__ import annotations

import multiprocessing
import random
from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor
from simulatte.utils.singleton import Singleton

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.typing import Builder


class Runner[S, T]:
    """Manage repeated simulations with configurable builder and seeds."""

    def __init__(
        self,
        *,
        builder: Builder[S],
        seeds: Sequence[int],
        parallel: bool = False,
        extract_fn: Callable[[S], T],
        n_jobs: int | None = None,
    ) -> None:
        self.builder = builder
        self.seeds = seeds
        self.parallel = parallel
        self.extract_fn = extract_fn
        self.n_jobs = n_jobs

    def _run_single(self, seed_until: tuple[float, float]) -> T:
        Singleton.clear()
        ShopFloor.servers = []
        env = Environment()
        seed, until = seed_until
        random.seed(seed)
        system = self.builder()
        env.run(until=until)
        return self.extract_fn(system)

    def run(self, until: float) -> list[T]:
        if not self.parallel:
            return [self._run_single((seed, until)) for seed in self.seeds]
        with multiprocessing.get_context("spawn").Pool(
            processes=self.n_jobs,
        ) as pool:
            return pool.map(
                self._run_single,
                [(seed, until) for seed in self.seeds],
            )
