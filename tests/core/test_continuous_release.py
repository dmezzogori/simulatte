from __future__ import annotations

import math

import pytest

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.triggers import on_completion_trigger
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, ShopFloor, StandardWIPStrategy


def test_continuous_release_rejects_empty_norms() -> None:
    """ValueError for empty dict."""
    with pytest.raises(ValueError, match="wl_norm must not be empty"):
        ContinuousRelease(wl_norm={}, allowance_factor=2)


def test_continuous_release_rejects_non_positive_norm() -> None:
    """ValueError for norm=0.0."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        ContinuousRelease(wl_norm={server: 0.0}, allowance_factor=2)


def test_continuous_release_rejects_infinite_norm() -> None:
    """ValueError for norm=inf."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        ContinuousRelease(wl_norm={server: math.inf}, allowance_factor=2)


def test_continuous_release_validate_strategy_rejects_standard() -> None:
    """TypeError if StandardWIPStrategy."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server: 5.0}, allowance_factor=2)

    assert isinstance(sf.wip_strategy, StandardWIPStrategy)
    with pytest.raises(TypeError, match="ContinuousRelease requires CorrectedWIPStrategy"):
        cr.validate_strategy(sf)


def test_continuous_release_validate_strategy_rejects_missing_server() -> None:
    """ValueError if server has no norm."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    _server2 = Server(env=env, capacity=1, shopfloor=sf)  # noqa: F841 — must exist on shopfloor

    # Only server1 has a norm; _server2 is on the shopfloor but missing from wl_norm
    cr = ContinuousRelease(wl_norm={server1: 5.0}, allowance_factor=2)

    with pytest.raises(ValueError, match="missing norms"):
        cr.validate_strategy(sf)


def test_continuous_release_on_completion_releases_under_norm() -> None:
    """Released when WIP + contribution <= norm."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server: 100.0}, allowance_factor=2)
    env.process(on_completion_trigger(sf, psp, cr.on_completion_release))

    # Add a job to shopfloor that finishes quickly
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add candidate in PSP
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=20.0)
    psp.add(job2)

    # Run until job1 finishes (t=1), triggering on_completion_release
    env.run(until=2)

    # job2 should be released from PSP since WIP is under norm
    assert job2 not in psp
    assert job2 in sf.jobs or job2 in sf.jobs_done


def test_continuous_release_on_completion_blocks_over_norm() -> None:
    """NOT released when would exceed norm."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Very tight norm: 0.5 — adding any job with PT >= 1 would exceed it
    cr = ContinuousRelease(wl_norm={server: 0.5}, allowance_factor=2)
    env.process(on_completion_trigger(sf, psp, cr.on_completion_release))

    # Add a job to shopfloor that finishes quickly (PT=0.3 fits the norm)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[0.3], due_date=10.0)
    sf.add(job1)

    # Add candidate in PSP with PT=5.0 — contribution would be 5.0 > 0.5
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job2)

    # Run until job1 finishes
    env.run(until=1)

    # job2 should stay in PSP since adding it would exceed norm
    assert job2 in psp


def test_continuous_release_on_arrival_releases_to_idle_server() -> None:
    """Released on arrival when idle + fits."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server: 100.0}, allowance_factor=2)
    psp.on_arrival(cr.on_arrival_release)

    # Server is idle, norm is high → should release on arrival
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)

    assert job not in psp
    assert job in sf.jobs


def test_continuous_release_on_arrival_blocks_when_norms_violated() -> None:
    """Blocked even if idle when norms violated."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Very tight norm: PT=5.0 > norm=1.0
    cr = ContinuousRelease(wl_norm={server: 1.0}, allowance_factor=2)
    psp.on_arrival(cr.on_arrival_release)

    assert server.is_idle

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    # Should stay in PSP despite server being idle
    assert job in psp


def test_continuous_release_on_arrival_idempotent() -> None:
    """No crash if already released."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server: 100.0}, allowance_factor=2)

    # An earlier callback releases the job before ContinuousRelease's turn
    psp.on_arrival(lambda job, pool: pool.release(job))
    psp.on_arrival(cr.on_arrival_release)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)  # Should not raise

    assert job not in psp
    assert job in sf.jobs


def test_continuous_release_empty_system_bootstrap() -> None:
    """First job on empty shop released via arrival."""
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server: 100.0}, allowance_factor=2)
    psp.on_arrival(cr.on_arrival_release)

    # Empty shopfloor, add first job to PSP — should be released
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    assert job not in psp
    assert job in sf.jobs
    assert len(sf.jobs) == 1


def test_continuous_release_corrected_load_multi_server() -> None:
    """Verify PT/(i+1) formula for multi-op routing.

    Two servers: s1, s2. Norm = 3.0 per server.
    Job routing: s1(PT=4.0) -> s2(PT=6.0).
    Contribution: s1 = 4/1 = 4.0, s2 = 6/2 = 3.0.
    s1 contribution (4.0) > norm (3.0), so should NOT release.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    sf.set_wip_strategy(CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    cr = ContinuousRelease(wl_norm={server1: 3.0, server2: 3.0}, allowance_factor=2)
    psp.on_arrival(cr.on_arrival_release)

    assert server1.is_idle

    job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[4.0, 6.0], due_date=30.0)
    psp.add(job)

    # Should NOT be released: s1 contribution = 4.0 > norm = 3.0
    assert job in psp
