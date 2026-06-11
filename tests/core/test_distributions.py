from __future__ import annotations

import math
import random
import statistics

import pytest

from simulatte.distributions import (
    Deterministic,
    Distribution,
    Erlang,
    Exponential,
    LogNormal,
    RunningStats,
    TruncatedErlang,
    Uniform,
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
    twk_due_date,
)
from simulatte.environment import Environment
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_pure_job_shop_routing_returns_subset() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(5)]

    random.seed(42)
    routing = pure_job_shop_routing(servers)
    result = routing()

    assert 1 <= len(result) <= len(servers)
    assert all(s in servers for s in result)


def test_pure_job_shop_routing_different_results_with_different_seeds() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(10)]

    routing = pure_job_shop_routing(servers)

    random.seed(1)
    assert routing() == [servers[9], servers[1], servers[4]]

    random.seed(2)
    assert routing() == [servers[1]]


def test_pure_flow_shop_routing_visits_all_servers_in_fixed_order() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(6)]

    routing = pure_flow_shop_routing(servers)

    # Every job visits ALL servers in the same fixed (directed) sequence,
    # independent of the RNG state.
    random.seed(1)
    first = routing()
    random.seed(999)
    second = routing()

    assert list(first) == servers
    assert list(second) == servers
    assert len(first) == len(servers)


def test_general_flow_shop_routing_is_a_directed_subset() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(6)]

    routing = general_flow_shop_routing(servers)

    random.seed(42)
    for _ in range(100):
        result = list(routing())
        # Random routing length U[1, M].
        assert 1 <= len(result) <= len(servers)
        # Distinct servers, drawn without replacement (no re-entrant flow).
        assert len(set(result)) == len(result)
        assert all(s in servers for s in result)
        # Directed: servers appear in ascending canonical (index) order.
        indices = [servers.index(s) for s in result]
        assert indices == sorted(indices)


def test_general_flow_shop_routing_varies_length_across_seeds() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(6)]

    routing = general_flow_shop_routing(servers)

    # Same underlying draw as the pure job shop, but sorted into a flow:
    # seed 1 over 6 servers must yield a strictly ascending index sequence.
    random.seed(1)
    result = list(routing())
    indices = [servers.index(s) for s in result]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)


def test_arrival_rate_for_utilization_matches_benchmark_constants() -> None:
    # General flow shop / pure job shop: E[L] = (M+1)/2 = 3.5 -> mean IAT 0.648.
    lam_job = arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=3.5)
    assert 1 / lam_job == pytest.approx(0.648, abs=1e-3)

    # Pure flow shop: E[L] = M = 6 -> mean IAT 1.111 (the utilization "trap").
    lam_flow = arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=6.0)
    assert 1 / lam_flow == pytest.approx(1.111, abs=1e-3)

    # A pure flow shop carries ~1.7x the arrival rate of the job shop at fixed rho.
    assert lam_flow < lam_job


def test_twk_due_date_scales_with_total_work_content() -> None:
    rule = twk_due_date(2.0)
    # Allowance = K * sum(processing_times).
    assert rule([1.0, 2.0, 3.0]) == pytest.approx(12.0)
    # Empty routing -> zero work content -> zero allowance.
    assert rule(()) == pytest.approx(0.0)


def test_arrival_rate_for_utilization_respects_processing_time_mean() -> None:
    # Halving the mean processing time doubles the sustainable arrival rate.
    base = arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=3.5, mean_processing_time=1.0)
    faster = arrival_rate_for_utilization(0.9, n_servers=6, mean_routing_length=3.5, mean_processing_time=0.5)
    assert faster == pytest.approx(2 * base)


def test_running_stats_empty() -> None:
    stats = RunningStats()
    assert stats.n == 0
    assert stats.mean == 0.0
    assert stats.variance == 0.0
    assert stats.std == 0.0
    assert stats.z_norm(5.0) == 0.0


def test_running_stats_single_value() -> None:
    stats = RunningStats()
    stats.update(10.0)

    assert stats.n == 1
    assert stats.mean == 10.0
    assert stats.variance == 0.0  # undefined with n=1, returns 0
    assert stats.std == 0.0
    assert stats.z_norm(10.0) == 0.0  # std is 0, so z_norm returns 0


def test_running_stats_multiple_values() -> None:
    stats = RunningStats()
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    for v in values:
        stats.update(v)

    assert stats.n == 8
    assert stats.mean == pytest.approx(5.0)
    # Sample variance = sum((x - mean)^2) / (n-1) = 32 / 7 ≈ 4.571
    assert stats.variance == pytest.approx(4.571428571, rel=1e-3)
    assert stats.std == pytest.approx(2.138, rel=1e-2)


def test_running_stats_z_norm() -> None:
    stats = RunningStats()
    # Add values with known mean=5, std=2
    for v in [3.0, 5.0, 7.0]:
        stats.update(v)

    # mean = 5, variance = 4, std = 2
    assert stats.mean == pytest.approx(5.0)
    assert stats.std == pytest.approx(2.0)

    # z_norm(7) = (7 - 5) / 2 = 1.0
    assert stats.z_norm(7.0) == pytest.approx(1.0)
    # z_norm(3) = (3 - 5) / 2 = -1.0
    assert stats.z_norm(3.0) == pytest.approx(-1.0)
    # z_norm(5) = (5 - 5) / 2 = 0.0
    assert stats.z_norm(5.0) == pytest.approx(0.0)


def _empirical_mean(dist: Distribution, n: int = 50_000) -> float:
    random.seed(7)
    return statistics.fmean(dist() for _ in range(n))


def _empirical_variance(dist: Distribution, n: int = 200_000) -> float:
    """Seeded sample variance (Bessel-corrected) of ``n`` draws.

    Variance estimates converge ~sqrt(2) slower than the mean for the same
    relative tolerance, hence the larger default ``n`` than ``_empirical_mean``.
    """
    random.seed(7)
    return statistics.variance([dist() for _ in range(n)])


def _empirical_log_variance(dist: Distribution, n: int = 200_000) -> float:
    """Sample variance of ``log(X)`` — used for the lognormal, whose raw sample
    variance converges slowly (it is itself heavy-tailed). The log of a lognormal
    is exactly normal, so this estimator converges as fast as a Gaussian variance.
    """
    random.seed(7)
    return statistics.variance([math.log(dist()) for _ in range(n)])


# Reference values for the truncated 2-Erlang (rate=2, shape=2, max_value=4),
# computed INDEPENDENTLY of the production ``_erlang_cdf`` via fine-grained
# trapezoidal integration of the Erlang pdf f(x)=rate^k x^(k-1) e^(-rate x)/(k-1)!:
#   mean = ∫_0^T x f(x) dx / ∫_0^T f(x) dx                       = 0.98923269
#   var  = ∫_0^T x^2 f(x) dx / ∫_0^T f(x) dx  -  mean^2          = 0.46219847
# (T=4, N=2e6 panels; reproduced in the task's derivation notes). Truncation at
# T=4 removes only ~0.30% of the mass, so these sit just below the nominal
# (mean 1.0, var 0.5) of the untruncated 2-Erlang.
_TRUNC_DEFAULT_MEAN = 0.98923269
_TRUNC_DEFAULT_VAR = 0.46219847


def test_exponential_mean_and_sampling() -> None:
    d = Exponential(rate=2.0)
    assert d.mean == pytest.approx(0.5)
    assert _empirical_mean(d) == pytest.approx(0.5, rel=0.05)


def test_exponential_variance() -> None:
    d = Exponential(rate=2.0)
    # Var = 1/rate^2 = 0.25. Seeded empirical var converges to within ~0.03% at
    # n=200k, so rel=0.04 is comfortable headroom while still rejecting any
    # variate whose CV is off by more than a few percent.
    assert _empirical_variance(d) == pytest.approx(1.0 / 2.0**2, rel=0.04)


def test_erlang_mean_and_sampling() -> None:
    d = Erlang(rate=2.0, shape=2)
    assert d.mean == pytest.approx(1.0)
    assert _empirical_mean(d) == pytest.approx(1.0, rel=0.05)


def test_erlang_variance_distinguishes_wrong_cv() -> None:
    d = Erlang(rate=2.0, shape=2)
    # Var = shape/rate^2 = 0.5 (CV = 1/sqrt(2) = 0.707). The named Gap-A mutant
    # — Erlang sampled as expovariate(1.0) — has the RIGHT mean 1.0 but CV 1.0,
    # i.e. var 1.0. Discrimination margin: correct empirical var lands at ~0.497
    # (rel err 0.6%) vs the mutant's ~1.0 (rel err 100%). rel=0.04 passes the
    # correct sampler with ~6x slack and fails the mutant by ~25x.
    assert _empirical_variance(d) == pytest.approx(2.0 / 2.0**2, rel=0.04)


def test_deterministic_is_constant() -> None:
    d = Deterministic(value=3.0)
    assert d.mean == 3.0
    assert {d() for _ in range(10)} == {3.0}


def test_deterministic_has_zero_variance() -> None:
    d = Deterministic(value=3.0)
    # Degenerate distribution: every draw is identical, so variance is exactly 0.
    assert _empirical_variance(d, n=1_000) == 0.0


def test_uniform_mean() -> None:
    d = Uniform(low=10.0, high=18.0)
    assert d.mean == pytest.approx(14.0)
    assert _empirical_mean(d) == pytest.approx(14.0, rel=0.05)


def test_uniform_variance_distinguishes_constant_impostor() -> None:
    d = Uniform(low=10.0, high=18.0)
    # Var = (high-low)^2/12 = 64/12 = 5.333. The named Gap-A mutant — Uniform
    # returning the constant midpoint 14 — has the RIGHT mean but variance 0.
    # Correct empirical var lands at ~5.341 (rel err 0.14%); the impostor's 0 is
    # a 100% miss. rel=0.03 passes correct decisively and rejects the impostor.
    assert _empirical_variance(d) == pytest.approx((18.0 - 10.0) ** 2 / 12.0, rel=0.03)


def test_lognormal_mean() -> None:
    d = LogNormal(mu=0.0, sigma=0.5)
    assert d.mean == pytest.approx(math.exp(0.125))
    assert _empirical_mean(d) == pytest.approx(math.exp(0.125), rel=0.05)


def test_lognormal_variance_on_log_samples() -> None:
    d = LogNormal(mu=0.0, sigma=0.5)
    # Asserting on the variance of log(X) rather than X: the raw lognormal
    # sample variance is heavy-tailed and converges slowly, whereas log(X) is
    # exactly N(mu, sigma^2), so its sample variance converges at Gaussian
    # speed. Reference is sigma^2 = 0.25 (independent of any production code).
    # Empirical lands at ~0.2501 (rel err 0.04%), so rel=0.03 is ample.
    assert _empirical_log_variance(d) == pytest.approx(0.5**2, rel=0.03)


def test_truncated_erlang_respects_cap_and_true_mean() -> None:
    d = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)
    random.seed(42)
    samples = [d() for _ in range(2000)]
    assert all(0.0 <= s <= 4.0 for s in samples)
    assert d.mean < 1.0
    # Reference mean from independent numerical integration (see module header),
    # NOT from d.mean / _erlang_cdf.
    assert d.mean == pytest.approx(_TRUNC_DEFAULT_MEAN, abs=1e-4)
    assert _empirical_mean(d) == pytest.approx(d.mean, rel=0.03)


def test_truncated_erlang_default_variance() -> None:
    # The workload-control benchmark shops are *defined* by the squared CV of
    # this truncated 2-Erlang service process, so the variance (not just the
    # mean) must be pinned. Reference var = 0.46219847 from independent
    # trapezoidal integration of x^2 f(x) (see module header) — NOT from any
    # production helper. Empirical lands at ~0.46262 (rel err 0.09%) at n=150k.
    d = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)
    assert _empirical_variance(d, n=150_000) == pytest.approx(_TRUNC_DEFAULT_VAR, rel=0.03)


def test_truncated_erlang_shape1_matches_truncated_exponential_closed_form() -> None:
    # shape=1 exercises the mean formula's (shape+1) numerator edge. For an
    # exponential truncated to [0, T], the conditional mean has the closed form
    #   E[X | X <= T] = 1/lam - T e^{-lam T} / (1 - e^{-lam T})
    # written out here directly (independent of _erlang_cdf).
    rate, t = 2.0, 1.5
    reference = 1.0 / rate - t * math.exp(-rate * t) / (1.0 - math.exp(-rate * t))
    d = TruncatedErlang(rate=rate, shape=1, max_value=t)
    assert d.mean == pytest.approx(reference, abs=1e-6)
    # Tie the sampler to the formula: empirical mean of real draws matches .mean.
    assert _empirical_mean(d, n=80_000) == pytest.approx(d.mean, rel=0.02)


def test_truncated_erlang_higher_shape_moderate_truncation() -> None:
    # shape=3 with truncation at T=2 (removes ~24% of mass — well beyond the
    # ~0.3% of the default, where formula errors actually surface). Reference is
    # a plain fine-grained trapezoidal integration of the Erlang pdf, written
    # out below; it does NOT touch the production _erlang_cdf.
    rate, shape, t = 2.0, 3, 2.0

    def erlang_pdf(x: float) -> float:
        return (rate**shape) * (x ** (shape - 1)) * math.exp(-rate * x) / math.factorial(shape - 1)

    panels = 200_000
    h = t / panels
    weighted = 0.0  # ∫ x f(x) dx
    mass = 0.0  # ∫ f(x) dx
    for i in range(panels + 1):
        x = i * h
        w = 0.5 if i in (0, panels) else 1.0  # trapezoid end weights
        weighted += w * x * erlang_pdf(x)
        mass += w * erlang_pdf(x)
    reference = weighted / mass

    d = TruncatedErlang(rate=rate, shape=shape, max_value=t)
    assert d.mean == pytest.approx(reference, rel=1e-4)
    assert _empirical_mean(d, n=80_000) == pytest.approx(d.mean, rel=0.02)


def test_truncated_erlang_tight_truncation() -> None:
    # Tight truncation: rate=2, shape=2, max_value=1.0 removes ~41% of the mass
    # (acceptance ~0.59), the regime where _erlang_cdf rounding would bite.
    rate, shape, t = 2.0, 2, 1.0

    def erlang_pdf(x: float) -> float:
        return (rate**shape) * (x ** (shape - 1)) * math.exp(-rate * x) / math.factorial(shape - 1)

    panels = 200_000
    h = t / panels
    weighted = 0.0
    mass = 0.0
    for i in range(panels + 1):
        x = i * h
        w = 0.5 if i in (0, panels) else 1.0
        weighted += w * x * erlang_pdf(x)
        mass += w * erlang_pdf(x)
    reference_mean = weighted / mass  # ~0.54432

    d = TruncatedErlang(rate=rate, shape=shape, max_value=t)
    # Analytic .mean vs independent integration.
    assert d.mean == pytest.approx(reference_mean, rel=1e-4)

    # Sampling still terminates (acceptance ~0.59, no hang) and respects the
    # support bound, and its empirical mean matches .mean.
    random.seed(7)
    samples = [d() for _ in range(80_000)]
    assert all(0.0 <= s <= t for s in samples)
    assert statistics.fmean(samples) == pytest.approx(d.mean, rel=0.02)


def test_untruncated_erlang_mean_equals_nominal() -> None:
    assert TruncatedErlang(rate=2.0, shape=2, max_value=math.inf).mean == pytest.approx(1.0)


def test_distribution_validation() -> None:
    for bad in (
        lambda: Exponential(rate=0.0),
        lambda: Erlang(rate=0.0),
        lambda: Erlang(rate=2.0, shape=0),
        lambda: TruncatedErlang(rate=0.0),
        lambda: TruncatedErlang(rate=2.0, shape=0),
        lambda: TruncatedErlang(rate=2.0, shape=2, max_value=0.0),
        lambda: LogNormal(mu=0.0, sigma=0.0),
        lambda: Uniform(low=5.0, high=1.0),
    ):
        with pytest.raises(ValueError):
            bad()
