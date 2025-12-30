"""Utility distributions and statistics for jobshop simulations.

This module provides probability distributions for job routing and service times,
as well as online statistics computation for simulation metrics.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.server import Server


def server_sampling(servers: Sequence[Server]) -> Callable[[], Sequence[Server]]:
    """Create a factory that randomly samples subsets of servers for job routing.

    This is useful for simulating variable job routings in job-shop scheduling,
    where each job may visit a random subset of available servers.

    Args:
        servers: The pool of servers to sample from.

    Returns:
        A callable that, when invoked, returns a random subset of servers
        (between 1 and len(servers) inclusive) without replacement.

    Example:
        >>> sampler = server_sampling(servers)
        >>> routing = sampler()  # Returns e.g., [server_2, server_5, server_1]
    """
    servers = tuple(servers)  # Freeze to prevent mutation issues

    def sample_servers() -> Sequence[Server]:
        k = random.randint(1, len(servers))  # noqa: S311
        return random.sample(servers, k=k)

    return sample_servers


def truncated_2erlang(lam: float = 2, max_value: float = 4.0) -> float:
    """Generate a sample from a truncated 2-Erlang (Gamma(2, 1/λ)) distribution.

    The 2-Erlang distribution models service times as the sum of two exponential
    phases, producing less variable times than pure exponential. Truncation
    ensures samples don't exceed a maximum value.

    Args:
        lam: Rate parameter (λ) for each exponential phase. Higher values
            produce smaller samples on average (mean = 2/λ).
        max_value: Maximum allowed sample value. Samples exceeding this
            are rejected and redrawn.

    Returns:
        A random sample from the truncated distribution, guaranteed to
        be in the range [0, max_value].

    Example:
        >>> service_time = truncated_2erlang(lam=2.0, max_value=4.0)
        >>> 0 <= service_time <= 4.0
        True
    """
    while True:
        sample = random.expovariate(lam) + random.expovariate(lam)
        if sample <= max_value:
            return sample


class RunningStats:
    """Compute running mean, variance, and standard deviation using Welford's algorithm.

    Welford's algorithm maintains numerical stability by avoiding catastrophic
    cancellation that occurs when computing variance via the naive formula
    (sum of squares minus squared sum). This is especially important for
    simulations with many observations or values of similar magnitude.

    Attributes:
        n: Number of observations added.
        mean: Current running mean of all observations.
        M2: Sum of squared differences from the mean (internal state).

    Example:
        >>> stats = RunningStats()
        >>> for value in [2.0, 4.0, 6.0]:
        ...     stats.update(value)
        >>> stats.mean
        4.0
        >>> stats.std
        2.0
    """

    def __init__(self) -> None:
        """Initialize statistics counters to zero."""
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x: float) -> None:
        """Add a new observation and update running statistics.

        Args:
            x: The new value to incorporate into the statistics.
        """
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self) -> float:
        """Sample variance using Bessel's correction (n-1 denominator)."""
        return self.M2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        """Sample standard deviation (square root of variance)."""
        return self.variance**0.5

    def z_norm(self, x: float) -> float:
        """Compute the z-score (standard score) for a given value.

        Args:
            x: The value to normalize.

        Returns:
            The z-score (x - mean) / std, or 0.0 if insufficient data
            or zero standard deviation.
        """
        return (x - self.mean) / self.std if self.n > 1 and self.std > 0 else 0.0
