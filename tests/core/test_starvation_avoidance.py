from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.starvation_avoidance import starvation_avoidance_process
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_starvation_avoidance_releases_when_server_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Start the starvation avoidance process
    env.process(starvation_avoidance_process(sf, psp))

    # Prime the process so it's waiting for new_job
    env.run(until=0.001)

    # Server is empty, so job should be released immediately
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(job)

    # Run to let the process react
    env.run(until=0.01)

    # Job should have been moved from PSP to shopfloor
    assert len(psp) == 0
    assert job in sf.jobs or job in sf.jobs_done


def test_starvation_avoidance_keeps_job_when_server_has_queue() -> None:
    """Job should stay in PSP when server already has jobs waiting."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Add two jobs to shopfloor - one processing, one waiting
    # This makes server.empty = False (there's a job in queue)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100.0], due_date=200.0)
    sf.add(job1)
    sf.add(job2)

    # Start the starvation avoidance process
    env.process(starvation_avoidance_process(sf, psp))

    # Run so jobs start processing - job1 is processing, job2 is waiting
    env.run(until=1)

    # Server queue should have job2 waiting (not empty)
    assert not server.empty

    # Add a new job to PSP
    new_job = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.0)
    psp.add(new_job)

    # Run a bit to let the process react
    env.run(until=2)

    # Job should stay in PSP since server has queue
    assert len(psp) == 1
    assert new_job in list(psp.jobs)


def test_starvation_avoidance_reacts_to_multiple_jobs() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Start starvation avoidance
    env.process(starvation_avoidance_process(sf, psp))
    env.run(until=0.001)

    # Add job for empty server1 - should be released
    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[10.0], due_date=100.0)
    psp.add(job1)
    env.run(until=0.1)

    assert job1 not in list(psp.jobs)

    # Add job for empty server2 - should be released
    job3 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[10.0], due_date=100.0)
    psp.add(job3)
    env.run(until=0.2)

    assert job3 not in list(psp.jobs)
