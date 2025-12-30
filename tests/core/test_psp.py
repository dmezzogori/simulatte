from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_psp_add_remove_sets_exit_time() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    assert len(psp) == 0
    psp.add(job)
    assert len(psp) == 1

    removed = psp.remove()
    assert removed is job
    assert job.psp_exit_at == env.now


def test_psp_signal_new_job_triggers_event() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    events = []

    def listener():
        while True:
            j = yield psp.new_job
            events.append(j)

    env.process(listener())
    # Prime the listener so it is waiting on the current new_job event
    env.run(until=0.0001)
    psp.add(job)
    env.run(until=0.1)

    assert events == [job]


def test_psp_contains() -> None:
    """PSP __contains__ should return True for jobs in the pool."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.add(job1)
    assert job1 in psp
    assert job2 not in psp


def test_psp_getitem() -> None:
    """PSP __getitem__ should return job by index."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.add(job1)
    psp.add(job2)
    assert psp[0] is job1
    assert psp[1] is job2


def test_psp_empty() -> None:
    """PSP empty property should reflect pool state."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    assert psp.empty
    psp.add(job)
    assert not psp.empty


def test_psp_remove_specific_job() -> None:
    """PSP remove with specific job should remove that job."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.add(job1)
    psp.add(job2)

    removed = psp.remove(job=job2)
    assert removed is job2
    assert job2 not in psp
    assert job1 in psp


def test_psp_remove_specific_job_not_found() -> None:
    """PSP remove with job not in pool should raise ValueError."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1], due_date=5)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.add(job1)

    with pytest.raises(ValueError, match="not found"):
        psp.remove(job=job2)
