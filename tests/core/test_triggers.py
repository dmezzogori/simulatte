"""Tests for release trigger functions."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.triggers import on_arrival_trigger, on_completion_trigger, periodic_trigger
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_periodic_trigger_invokes_release_fn() -> None:
    """Periodic trigger should invoke release function at regular intervals."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    call_count = 0

    def release_fn(pool: PreShopPool) -> None:
        nonlocal call_count
        call_count += 1
        while not pool.empty:
            j = pool.remove()
            pool.shopfloor.add(j)

    psp.add(job)
    env.process(periodic_trigger(psp, 0.1, release_fn))
    env.run(until=0.3)

    assert call_count == 1  # Called once when job was present
    assert len(psp) == 0
    assert job in sf.jobs or job in sf.jobs_done


def test_periodic_trigger_skips_when_empty() -> None:
    """Periodic trigger should not invoke release function when PSP is empty."""
    env = Environment()
    sf = ShopFloor(env=env)
    psp = PreShopPool(env=env, shopfloor=sf)

    call_count = 0

    def release_fn(pool: PreShopPool) -> None:
        nonlocal call_count
        call_count += 1

    env.process(periodic_trigger(psp, 0.1, release_fn))
    env.run(until=0.5)

    assert call_count == 0  # Never called because PSP was always empty


def test_on_arrival_trigger_invokes_release_fn() -> None:
    """On-arrival trigger should invoke release function when job arrives."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    received_jobs: list[ProductionJob] = []

    def release_fn(j: ProductionJob, pool: PreShopPool) -> None:
        received_jobs.append(j)
        pool.remove(job=j)
        pool.shopfloor.add(j)

    env.process(on_arrival_trigger(psp, release_fn))
    # Prime the trigger so it is waiting
    env.run(until=0.0001)

    psp.add(job)
    env.run(until=0.1)

    assert received_jobs == [job]
    assert len(psp) == 0


def test_on_completion_trigger_invokes_release_fn() -> None:
    """On-completion trigger should invoke release function when job finishes processing."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[0.1], due_date=5)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[0.1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    triggered_jobs: list[ProductionJob] = []

    def release_fn(triggering_job: ProductionJob, pool: PreShopPool) -> None:
        triggered_jobs.append(triggering_job)
        # Release next job from PSP if available
        if not pool.empty:
            next_job = pool.remove()
            pool.shopfloor.add(next_job)

    env.process(on_completion_trigger(sf, psp, release_fn))

    # Add job2 to PSP, job1 goes directly to shopfloor
    psp.add(job2)
    sf.add(job1)
    env.run(until=0.5)

    # job1 should have triggered the release of job2
    assert job1 in triggered_jobs
    assert len(psp) == 0
