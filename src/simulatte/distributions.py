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
            to ``1.0`` (the truncated 2-Erlang benchmark mean).

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
