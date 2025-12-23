from __future__ import annotations

import random

import pytest

from simulatte.distributions import RunningStats, server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_server_sampling_returns_subset() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(5)]

    random.seed(42)
    sampler = server_sampling(servers)
    result = sampler()

    assert 1 <= len(result) <= len(servers)
    assert all(s in servers for s in result)


def test_server_sampling_different_results_with_different_seeds() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(10)]

    sampler = server_sampling(servers)

    random.seed(1)
    assert sampler() == [servers[9], servers[1], servers[4]]

    random.seed(2)
    assert sampler() == [servers[1]]


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
