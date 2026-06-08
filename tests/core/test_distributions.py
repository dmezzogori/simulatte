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
    truncated_2erlang,
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


def test_truncated_2erlang_within_bounds() -> None:
    random.seed(42)
    for _ in range(100):
        sample = truncated_2erlang(lam=2, max_value=4.0)
        assert 0 <= sample <= 4.0


def test_truncated_2erlang_custom_max_value() -> None:
    random.seed(42)
    for _ in range(50):
        sample = truncated_2erlang(lam=2, max_value=1.0)
        assert 0 <= sample <= 1.0


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


def test_exponential_mean_and_sampling() -> None:
    d = Exponential(rate=2.0)
    assert d.mean == pytest.approx(0.5)
    assert _empirical_mean(d) == pytest.approx(0.5, rel=0.05)


def test_erlang_mean_and_sampling() -> None:
    d = Erlang(rate=2.0, shape=2)
    assert d.mean == pytest.approx(1.0)
    assert _empirical_mean(d) == pytest.approx(1.0, rel=0.05)


def test_deterministic_is_constant() -> None:
    d = Deterministic(value=3.0)
    assert d.mean == 3.0
    assert {d() for _ in range(10)} == {3.0}


def test_uniform_mean() -> None:
    d = Uniform(low=10.0, high=18.0)
    assert d.mean == pytest.approx(14.0)
    assert _empirical_mean(d) == pytest.approx(14.0, rel=0.05)


def test_lognormal_mean() -> None:
    d = LogNormal(mu=0.0, sigma=0.5)
    assert d.mean == pytest.approx(math.exp(0.125))
    assert _empirical_mean(d) == pytest.approx(math.exp(0.125), rel=0.05)


def test_truncated_erlang_respects_cap_and_true_mean() -> None:
    d = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)
    random.seed(42)
    samples = [d() for _ in range(2000)]
    assert all(0.0 <= s <= 4.0 for s in samples)
    assert d.mean < 1.0
    assert d.mean == pytest.approx(0.989232, abs=1e-4)
    assert _empirical_mean(d) == pytest.approx(d.mean, rel=0.03)


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
