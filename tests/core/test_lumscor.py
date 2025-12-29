from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.lumscor import LumsCor, lumscor_starvation_trigger
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, ShopFloor, StandardWIPStrategy


def test_lumscor_sets_corrected_wip_strategy() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    assert isinstance(sf.wip_strategy, StandardWIPStrategy)

    LumsCor(shopfloor=sf, wl_norm={server: 10.0}, allowance_factor=2)

    assert isinstance(sf.wip_strategy, CorrectedWIPStrategy)


def test_lumscor_release_condition_always_true() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    policy = LumsCor(shopfloor=sf, wl_norm={server: 10.0}, allowance_factor=2)

    assert policy.release_condition(psp, sf) is True


def test_lumscor_release_under_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # High workload norm allows releases
    policy = LumsCor(shopfloor=sf, wl_norm={server: 100.0}, allowance_factor=2)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    policy.release(psp, sf)

    # Job should be released since WIP is well under norm
    assert job not in psp.jobs
    assert job in sf.jobs


def test_lumscor_release_respects_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Very low workload norm blocks releases
    policy = LumsCor(shopfloor=sf, wl_norm={server: 0.1}, allowance_factor=2)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)

    policy.release(psp, sf)

    # Job should stay in PSP since adding it would exceed norm
    assert job in psp.jobs


def test_lumscor_release_order_by_planned_release_date() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    policy = LumsCor(shopfloor=sf, wl_norm={server: 100.0}, allowance_factor=2)

    # Add jobs with different due dates
    job_late = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    job_early = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)

    psp.add(job_late)
    psp.add(job_early)

    policy.release(psp, sf)

    # Both should be released since norm is high
    assert job_early not in psp.jobs
    assert job_late not in psp.jobs


def test_lumscor_starvation_trigger_releases_when_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Set allowance_factor via LumsCor (class attribute)
    LumsCor(shopfloor=sf, wl_norm={server: 100.0}, allowance_factor=2)

    # Start starvation trigger process
    env.process(lumscor_starvation_trigger(sf, psp))

    # Add a job to shopfloor
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add candidate job to PSP
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job2)

    # Run until job1 finishes
    env.run(until=2)

    # job2 should be released when server becomes empty
    assert job2 not in psp.jobs


def test_lumscor_starvation_trigger_when_queue_has_one() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    LumsCor(shopfloor=sf, wl_norm={server: 100.0}, allowance_factor=2)
    env.process(lumscor_starvation_trigger(sf, psp))

    # Add two jobs to shopfloor
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    sf.add(job1)
    sf.add(job2)

    # Add candidate to PSP
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=25.0)
    psp.add(job3)

    # Run until job1 finishes
    env.run(until=3)

    # job3 should be released when queue has only 1 job
    assert job3 not in psp.jobs


def test_lumscor_starvation_no_release_when_no_candidates() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    LumsCor(shopfloor=sf, wl_norm={server1: 100.0, server2: 100.0}, allowance_factor=2)
    env.process(lumscor_starvation_trigger(sf, psp))

    # Add job to server1
    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add candidate to PSP that starts at server2
    job2 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[1.0], due_date=20.0)
    psp.add(job2)

    env.run(until=2)

    # job2 should stay in PSP (starts at different server)
    assert job2 in psp.jobs


def test_lumscor_starvation_selects_by_planned_release_date() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    LumsCor(shopfloor=sf, wl_norm={server: 100.0}, allowance_factor=2)
    env.process(lumscor_starvation_trigger(sf, psp))

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add two candidates with different due dates
    job_urgent = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=5.0)
    job_relaxed = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    psp.add(job_urgent)
    psp.add(job_relaxed)

    env.run(until=2)

    # The urgent job (earlier planned release date) should be selected
    assert job_urgent not in psp.jobs
