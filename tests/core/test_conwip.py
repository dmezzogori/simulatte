from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.conwip import ConWIP
from simulatte.policies.triggers import on_completion_trigger
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_conwip_rejects_zero_cap() -> None:
    """ValueError for wip_cap=0."""
    with pytest.raises(ValueError, match="wip_cap must be >= 1"):
        ConWIP(wip_cap=0)


def test_conwip_rejects_negative_cap() -> None:
    """ValueError for wip_cap=-3."""
    with pytest.raises(ValueError, match="wip_cap must be >= 1"):
        ConWIP(wip_cap=-3)


def test_conwip_on_completion_releases_under_cap() -> None:
    """Job released when WIP < cap after completion."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=2)
    env.process(on_completion_trigger(sf, psp, conwip.on_completion_release))

    # Add one job to the shopfloor (will process and finish, dropping WIP to 0)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add a candidate in PSP
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=15.0)
    psp.add(job2)

    # Run until job1 finishes (t=1), triggering on_completion_release
    env.run(until=2)

    # job2 should be released from PSP since WIP dropped below cap
    assert job2 not in psp
    assert job2 in sf.jobs or job2 in sf.jobs_done


def test_conwip_on_completion_blocks_at_cap() -> None:
    """Job NOT released when WIP >= cap."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=1)
    env.process(on_completion_trigger(sf, psp, conwip.on_completion_release))

    # Two long jobs: job1 processes first, job2 waits in queue. len(sf.jobs) = 2.
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
    sf.add(job1)
    sf.add(job2)

    # PSP candidate
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    psp.add(job3)

    # When job1 finishes at t=5, sf.jobs has job2 still. len(sf.jobs) = 1 == wip_cap.
    # So job3 should NOT be released (WIP at cap).
    env.run(until=6)

    assert job3 in psp


def test_conwip_on_completion_releases_multiple() -> None:
    """Multiple jobs released in one event if room."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=3)
    env.process(on_completion_trigger(sf, psp, conwip.on_completion_release))

    # One job on shopfloor that finishes quickly
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Three candidates in PSP
    job_a = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=20.0)
    job_b = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=25.0)
    job_c = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=30.0)
    psp.add(job_a)
    psp.add(job_b)
    psp.add(job_c)

    # At t=1 job1 finishes: sf.jobs drops from 1 to 0. Cap=3 → room for 3 releases.
    env.run(until=2)

    # All three should be released
    assert job_a not in psp
    assert job_b not in psp
    assert job_c not in psp


def test_conwip_on_completion_selects_by_edd() -> None:
    """Earliest due date job selected first."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=1)
    env.process(on_completion_trigger(sf, psp, conwip.on_completion_release))

    # One job on shopfloor that finishes at t=1
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Two PSP candidates
    job_late = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=50.0)
    job_early = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=5.0)
    psp.add(job_late)
    psp.add(job_early)

    # After job1 finishes: sf.jobs=0. Cap=1 → release exactly one.
    # job_early (dd=5) has earlier due date → selected first.
    env.run(until=2)

    assert job_early not in psp
    assert job_late in psp


def test_conwip_on_arrival_releases_under_cap() -> None:
    """Job released on arrival when under cap."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=2)
    psp.on_arrival(conwip.on_arrival_release)

    # Shopfloor empty → WIP=0 < cap=2
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)

    # Job should be immediately released
    assert job not in psp
    assert job in sf.jobs


def test_conwip_on_arrival_blocks_at_cap() -> None:
    """Job NOT released on arrival when at cap."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=1)
    psp.on_arrival(conwip.on_arrival_release)

    # Fill shopfloor to cap
    blocker = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    sf.add(blocker)
    env.run(until=0.1)

    # Now add a job to PSP — should stay since WIP=1 == cap
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)

    assert job in psp


def test_conwip_on_arrival_idempotent_if_already_released() -> None:
    """No crash if job already released by another callback."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=5)

    # An earlier callback releases the job before ConWIP's turn
    psp.on_arrival(lambda job, pool: pool.release(job))
    psp.on_arrival(conwip.on_arrival_release)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)  # Should not raise

    assert job not in psp
    assert job in sf.jobs


def test_conwip_empty_system_bootstrap() -> None:
    """First job on empty shopfloor is released via arrival."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    conwip = ConWIP(wip_cap=3)
    psp.on_arrival(conwip.on_arrival_release)

    # Empty shopfloor, add first job to PSP — should be released
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    assert job not in psp
    assert job in sf.jobs
    assert len(sf.jobs) == 1
