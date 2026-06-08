"""Utility distributions and statistics for jobshop simulations.

This module provides probability distributions for job routing and service times,
as well as online statistics computation for simulation metrics.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.server import Server


@runtime_checkable
class Distribution(Protocol):
    """A callable random variate that also reports its analytical mean.

    Any object that is callable with no arguments returning a ``float`` and
    exposes a ``mean`` property satisfies this protocol. The ``Router`` only
    needs the callable side (the ``Sampler`` contract); ``Scenario`` reads
    ``.mean`` to derive the arrival rate for a target utilization.
    """

    def __call__(self) -> float: ...

    @property
    def mean(self) -> float: ...


def _erlang_cdf(shape: int, rate: float, x: float) -> float:
    """Regularized lower incomplete gamma P(shape, rate*x) for integer shape.

    Equivalent to the Erlang CDF ``1 - e^{-λx} Σ_{n=0}^{k-1} (λx)^n / n!``.
    Computed with elementary functions (no SciPy).
    """
    lam_x = rate * x
    total = 0.0
    term = 1.0  # (lam_x)^0 / 0!
    for n in range(shape):
        if n > 0:
            term *= lam_x / n
        total += term
    return 1.0 - math.exp(-lam_x) * total


@dataclass(frozen=True)
class Exponential:
    """Exponential distribution with rate ``rate`` (mean ``1/rate``)."""

    rate: float

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.expovariate(self.rate)

    @property
    def mean(self) -> float:
        return 1.0 / self.rate


@dataclass(frozen=True)
class Erlang:
    """Erlang (Gamma with integer ``shape``) distribution, mean ``shape/rate``."""

    rate: float
    shape: int = 2

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)
        if self.shape < 1:
            msg = f"shape must be >= 1, got {self.shape}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.gammavariate(self.shape, 1.0 / self.rate)

    @property
    def mean(self) -> float:
        return self.shape / self.rate


@dataclass(frozen=True)
class TruncatedErlang:
    """Erlang truncated to ``[0, max_value]`` by rejection resampling.

    The default ``shape=2`` reproduces the classic truncated 2-Erlang service
    process. ``mean`` is the TRUE conditional mean ``E[X | X <= max_value]``,
    strictly below the nominal ``shape/rate`` whenever ``max_value`` is finite.
    """

    rate: float
    shape: int = 2
    max_value: float = math.inf

    def __post_init__(self) -> None:
        if self.rate <= 0:
            msg = f"rate must be positive, got {self.rate}"
            raise ValueError(msg)
        if self.shape < 1:
            msg = f"shape must be >= 1, got {self.shape}"
            raise ValueError(msg)
        if self.max_value <= 0:
            msg = f"max_value must be positive, got {self.max_value}"
            raise ValueError(msg)

    def __call__(self) -> float:
        while True:
            sample = sum(random.expovariate(self.rate) for _ in range(self.shape))
            if sample <= self.max_value:
                return sample

    @property
    def mean(self) -> float:
        if math.isinf(self.max_value):
            return self.shape / self.rate
        numerator = _erlang_cdf(self.shape + 1, self.rate, self.max_value)
        denominator = _erlang_cdf(self.shape, self.rate, self.max_value)
        return (self.shape / self.rate) * numerator / denominator


@dataclass(frozen=True)
class LogNormal:
    """Lognormal distribution with underlying-normal params ``mu``/``sigma``."""

    mu: float
    sigma: float

    def __post_init__(self) -> None:
        if self.sigma <= 0:
            msg = f"sigma must be positive, got {self.sigma}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.lognormvariate(self.mu, self.sigma)

    @property
    def mean(self) -> float:
        return math.exp(self.mu + self.sigma**2 / 2.0)


@dataclass(frozen=True)
class Uniform:
    """Continuous uniform distribution on ``[low, high]``, mean ``(low+high)/2``."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            msg = f"low must be <= high, got low={self.low}, high={self.high}"
            raise ValueError(msg)

    def __call__(self) -> float:
        return random.uniform(self.low, self.high)

    @property
    def mean(self) -> float:
        return (self.low + self.high) / 2.0


@dataclass(frozen=True)
class Deterministic:
    """Degenerate distribution that always returns ``value``."""

    value: float

    def __call__(self) -> float:
        return self.value

    @property
    def mean(self) -> float:
        return self.value


def pure_job_shop_routing(servers: Sequence[Server]) -> Callable[[], Sequence[Server]]:
    """Create a Pure Job Shop (PJS) routing factory.

    The Pure Job Shop — also called a *randomly routed job shop* (Conway et al.,
    1967) — is the least directed of the standard workload-control benchmark
    shops: each order has a **random routing length** and a **random routing
    direction** (Oosterman, Land & Gaalman, 2000; Kasper, Land & Teunter, 2023).
    The factory draws a routing length ``k`` uniformly from ``[1, len(servers)]``
    and returns ``k`` distinct servers in random order (sampling *without*
    replacement, so re-entrant flow is prohibited).

    See ``general_flow_shop_routing`` (same length rule, but the subset is sorted
    into a directed flow) and ``pure_flow_shop_routing`` (fixed length, fully
    directed) for the directed counterparts on the directedness spectrum.

    Args:
        servers: The pool of servers to sample from. Its order defines the
            canonical machine index used by the directed sibling factories.

    Returns:
        A callable that, when invoked, returns a random subset of servers
        (between 1 and len(servers) inclusive) in random order, without
        replacement.

    Example:
        >>> routing = pure_job_shop_routing(servers)
        >>> routing()  # Returns e.g., [server_2, server_5, server_1]

    References:
        Oosterman, B., Land, M., & Gaalman, G. (2000). The influence of shop
        characteristics on workload control. *International Journal of
        Production Economics*, 68(1), 107-119.
        https://doi.org/10.1016/S0925-5273(99)00141-3
    """
    servers = tuple(servers)  # Freeze to prevent mutation issues

    def sample_servers() -> Sequence[Server]:
        k = random.randint(1, len(servers))
        return random.sample(servers, k=k)

    return sample_servers


def general_flow_shop_routing(servers: Sequence[Server]) -> Callable[[], Sequence[Server]]:
    """Create a General Flow Shop (GFS) routing factory.

    The General Flow Shop is the *directed* counterpart of the Pure Job Shop:
    each order has the same random routing length ``k ~ U[1, len(servers)]`` and
    the same equal per-machine inclusion probability, but the selected machines
    are **sorted into ascending index order** so that orders flow in a single
    direction with typical upstream and downstream stations (Oosterman, Land &
    Gaalman, 2000; Kasper, Land & Teunter, 2023). Machines are drawn *without*
    replacement, so re-entrant flow is prohibited.

    Args:
        servers: The pool of servers to sample from. Their order defines the
            canonical machine index along which routings are directed.

    Returns:
        A callable that, when invoked, returns a random subset of servers
        (between 1 and len(servers) inclusive), distinct and ordered by
        ascending canonical index.

    Example:
        >>> routing = general_flow_shop_routing(servers)
        >>> routing()  # Returns e.g., [server_1, server_4, server_5]

    References:
        Oosterman, B., Land, M., & Gaalman, G. (2000). The influence of shop
        characteristics on workload control. *International Journal of
        Production Economics*, 68(1), 107-119.
        https://doi.org/10.1016/S0925-5273(99)00141-3
    """
    servers = tuple(servers)  # Freeze to prevent mutation issues
    position = {server: index for index, server in enumerate(servers)}

    def directed_routing() -> Sequence[Server]:
        k = random.randint(1, len(servers))
        chosen = random.sample(servers, k=k)
        return sorted(chosen, key=position.__getitem__)

    return directed_routing


def pure_flow_shop_routing(servers: Sequence[Server]) -> Callable[[], Sequence[Server]]:
    """Create a Pure Flow Shop (PFS) routing factory.

    The Pure Flow Shop is the most directed benchmark shop: every order has a
    **fixed routing length equal to the number of machines** and visits *all*
    servers in the **same fixed (directed) sequence** (Oosterman, Land &
    Gaalman, 2000; Kasper, Land & Teunter, 2023). The routing is deterministic —
    every job shares the identical routing — so the factory ignores the RNG.

    Note:
        Because every order visits every machine, the mean routing length is
        ``len(servers)`` rather than ``(len(servers) + 1) / 2`` as for the pure
        job shop / general flow shop. Calibrate the arrival rate accordingly to
        hit a target utilization (see ``arrival_rate_for_utilization``).

    Args:
        servers: The servers visited, in the fixed order they are visited.

    Returns:
        A callable that, when invoked, returns all servers in their given order.

    Example:
        >>> routing = pure_flow_shop_routing(servers)
        >>> routing()  # Always returns every server, in order

    References:
        Oosterman, B., Land, M., & Gaalman, G. (2000). The influence of shop
        characteristics on workload control. *International Journal of
        Production Economics*, 68(1), 107-119.
        https://doi.org/10.1016/S0925-5273(99)00141-3
    """
    routing = tuple(servers)  # Freeze: identical directed routing for every job

    def fixed_routing() -> Sequence[Server]:
        return routing

    return fixed_routing


def arrival_rate_for_utilization(
    target_utilization: float,
    *,
    n_servers: int,
    mean_routing_length: float,
    mean_processing_time: float = 1.0,
) -> float:
    """Derive the Poisson arrival rate that yields a target shop utilization.

    Shop utilization couples to the mean routing length through
    ``rho = lambda * E[L] * E[p] / M``, where ``lambda`` is the order arrival
    rate, ``E[L]`` the mean routing length, ``E[p]`` the mean operation
    processing time, and ``M`` the number of machines. Inverting gives::

        lambda = rho * M / (E[L] * E[p])

    Because ``E[L]`` differs by shop type — ``M`` for a pure flow shop versus
    ``(M + 1) / 2`` for the pure job shop / general flow shop — the arrival rate
    is **not** portable across shop types: a pure flow shop reusing a job-shop
    arrival rate is driven unstable (``rho > 1``). Use this helper to recompute
    the rate whenever the shop type, machine count, or processing-time mean
    changes.

    Args:
        target_utilization: Desired steady-state utilization ``rho`` in (0, 1].
        n_servers: Number of machines ``M``.
        mean_routing_length: Mean number of operations per order ``E[L]``.
        mean_processing_time: Mean operation processing time ``E[p]``. Defaults
            to ``1.0`` (the nominal 2-Erlang mean; see ``TruncatedErlang``).

    Returns:
        The arrival rate ``lambda`` (orders per time unit). Its reciprocal is
        the mean inter-arrival time for an exponential arrival process.

    Example:
        >>> # Pure flow shop, M = 6, rho = 0.90, E[p] = 1.0 -> mean IAT 1.111
        >>> round(1 / arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=6), 3)
        1.111
        >>> # General flow shop / pure job shop, E[L] = 3.5 -> mean IAT 0.648
        >>> round(1 / arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=3.5), 3)
        0.648
    """
    return target_utilization * n_servers / (mean_routing_length * mean_processing_time)


def twk_due_date(allowance_factor: float) -> Callable[[Sequence[float]], float]:
    """Create a Total Work Content (TWK) due-date offset rule.

    The TWK procedure sets an order's due date proportional to its total work
    content: ``due_date = arrival_time + K * sum(p_ij)``, where ``K`` is the
    allowance factor and ``sum(p_ij)`` is the order's total processing time
    (Kasper, Land & Teunter, 2023). Larger, more work-intensive orders are
    granted proportionally more lead time than the flat-allowance rule.

    The returned callable matches the ``Router`` ``due_date_rule`` contract: it
    receives the job's sampled operation processing times and returns the
    due-date *offset* (the allowance added to the arrival time).

    Args:
        allowance_factor: The constant ``K`` multiplying total work content.

    Returns:
        A callable ``(processing_times) -> float`` returning ``K * sum(...)``.

    Example:
        >>> rule = twk_due_date(8.74)  # FOCUS pure-job-shop K (6 work centres)
        >>> rule([1.0, 2.0])           # offset for a 3.0 work-content order
        26.22

    References:
        Kasper, A., Land, M., & Teunter, R. (2023). Towards system state
        dispatching in high-variety manufacturing. *Omega*, 114, 102726.
        https://doi.org/10.1016/j.omega.2022.102726
    """

    def rule(processing_times: Sequence[float]) -> float:
        return allowance_factor * sum(processing_times)

    return rule


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
