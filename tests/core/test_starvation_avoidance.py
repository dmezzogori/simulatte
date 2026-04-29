from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_starvation_avoidance_releases_when_server_idle() -> None:
    """Job should be released immediately when its first server is idle."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.on_arrival(starvation_avoidance)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)

    assert len(psp) == 0
    assert job in sf.jobs or job in sf.jobs_done


def test_starvation_avoidance_keeps_job_when_server_busy() -> None:
    """Job should stay in PSP when its first server is processing."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Occupy the server
    blocker = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    sf.add(blocker)
    env.run(until=0.1)

    psp.on_arrival(starvation_avoidance)

    new_job = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(new_job)

    assert new_job in psp


def test_starvation_avoidance_keeps_job_when_server_has_queue() -> None:
    """Job should stay in PSP when server has jobs in queue."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Occupy the server and fill queue
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    sf.add(job1)
    sf.add(job2)
    env.run(until=0.1)

    assert not server.empty

    psp.on_arrival(starvation_avoidance)

    new_job = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(new_job)

    assert new_job in psp


def test_starvation_avoidance_reacts_to_multiple_jobs() -> None:
    """Multiple arrivals for different idle servers should all be released."""
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp.on_arrival(starvation_avoidance)

    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[10.0], due_date=100.0)
    psp.add(job1)
    assert job1 not in psp

    job2 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[10.0], due_date=100.0)
    psp.add(job2)
    assert job2 not in psp


def test_starvation_avoidance_coexists_with_other_arrival_callbacks() -> None:
    """Starvation avoidance should work alongside other on_arrival callbacks."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    arrivals_seen: list[ProductionJob] = []
    psp.on_arrival(lambda job, pool: arrivals_seen.append(job))
    psp.on_arrival(starvation_avoidance)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    # Both callbacks fired: the logger saw the arrival, starvation avoidance released it
    assert arrivals_seen == [job]
    assert job not in psp
    assert job in sf.jobs


def test_starvation_avoidance_tolerates_earlier_callback_releasing_job() -> None:
    """If an earlier on_arrival callback already released the job, starvation avoidance should not error."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # An earlier callback releases the job before starvation avoidance runs
    psp.on_arrival(lambda job, pool: pool.release(job))
    psp.on_arrival(starvation_avoidance)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)  # Should not raise ValueError

    assert job not in psp
    assert job in sf.jobs
