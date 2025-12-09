"""Utility distributions for routing and service times."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.jobshop.server.server import Server


def server_sampling(servers: Sequence[Server]) -> Callable[[], Sequence[Server]]:
    """Randomly sample a subset of servers."""

    def inner() -> Sequence[Server]:
        k = random.randint(1, len(servers))  # noqa: S311
        return random.sample(servers, k=k)

    return inner


def truncated_2erlang(lam: float = 2, max_value: float = 4.0) -> float:
    """Generate a sample from a truncated 2-Erlang distribution."""
    while True:
        sample = random.expovariate(lam) + random.expovariate(lam)
        if sample <= max_value:
            return sample


class RunningStats:
    """Compute running statistics using Welford's algorithm."""

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self) -> float:
        return self.M2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        return self.variance**0.5

    def z_norm(self, x: float) -> float:
        return (x - self.mean) / self.std if self.n > 1 and self.std > 0 else 0.0
