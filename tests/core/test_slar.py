from __future__ import annotations


from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.slar import Slar
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_slar_pst_priority_policy() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    pst = slar.pst_priority_policy(job, server)
    # slack_time = due_date - arrival_time - remaining_processing
    # At t=0: slack = 20 - 0 - 5 = 15
    # planned_slack_times with allowance=2: pst = slack - (processing + allowance per server)
    assert pst is not None


def test_slar_pst_value_none_returns_zero() -> None:
    """Test that _pst_value returns 0.0 when PST is None (server already visited)."""
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    # Job visits both servers
    job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[1.0, 1.0], due_date=20.0)

    # Before processing, PST for server1 should be a float
    pst_value_before = slar._pst_value(job, server1)
    assert isinstance(pst_value_before, float)
    assert pst_value_before != 0.0

    # Process job through server1
    sf.add(job)
    env.run(until=2)

    # After exiting server1, PST for server1 is None -> _pst_value returns 0.0
    pst_value_after = slar._pst_value(job, server1)
    assert pst_value_after == 0.0


def test_slar_pst_value_returns_float() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)

    pst_value = slar._pst_value(job, server)
    assert isinstance(pst_value, float)


def test_slar_release_when_server_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    # Start SLAR release trigger process
    env.process(slar.slar_release_triggers(sf, psp))

    # Add a job to shopfloor and process it
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add a candidate job to PSP (starts at same server)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job2)

    # Run until job1 finishes - this triggers job_processing_end
    env.run(until=2)

    # job2 should be released from PSP when server becomes empty
    assert job2 not in psp.jobs


def test_slar_release_when_queue_has_one() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(slar.slar_release_triggers(sf, psp))

    # Add two jobs to shopfloor - one processing, one waiting
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    sf.add(job1)
    sf.add(job2)

    # Add candidate job to PSP
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job3)

    # Run until job1 finishes - queue will have 1 job (job2)
    env.run(until=3)

    # job3 should be released because queue has only 1 job
    assert job3 not in psp.jobs


def test_slar_no_release_when_no_candidates() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(slar.slar_release_triggers(sf, psp))

    # Add job to server1
    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add candidate job to PSP that starts at server2 (different server)
    job2 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[1.0], due_date=20.0)
    psp.add(job2)

    # Run until job1 finishes
    env.run(until=2)

    # job2 should stay in PSP because it doesn't start at server1
    assert job2 in psp.jobs


def test_slar_selects_minimum_pst_job() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(slar.slar_release_triggers(sf, psp))

    # Add processing job
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Add two candidate jobs with different due dates (affects PST)
    job_urgent = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=5.0)
    job_relaxed = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    psp.add(job_urgent)
    psp.add(job_relaxed)

    env.run(until=2)

    # The urgent job (lower PST/more urgent) should be released first
    assert job_urgent not in psp.jobs


def test_slar_allowance_factor() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    slar1 = Slar(allowance_factor=1)
    slar2 = Slar(allowance_factor=5)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    pst1 = slar1.pst_priority_policy(job, server)
    pst2 = slar2.pst_priority_policy(job, server)

    # Different allowance factors should produce different PST values
    assert pst1 != pst2


def test_slar_negative_pst_release() -> None:
    """Test releasing negative PST job when all queued jobs have positive PST.

    This test covers the elif branch (line 116) where:
    - Queue has 2+ jobs (not empty, not has_one)
    - All queued jobs have positive PST (non-urgent)
    - An urgent job (negative PST) in PSP gets released
    """
    env = Environment()
    sf = ShopFloor(env=env)
    # Use capacity=2 so multiple jobs can queue while two are processing
    server = Server(env=env, capacity=2, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(slar.slar_release_triggers(sf, psp))

    # Add jobs that take a while to process (with far due dates = positive PST)
    processing_job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=1000.0)
    processing_job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=1000.0)
    sf.add(processing_job1)
    sf.add(processing_job2)

    # Add THREE jobs to queue with far due dates (positive PST)
    # When one processing job finishes, one queued job starts processing,
    # leaving 2 jobs in queue (triggers elif branch, not if branch)
    queued_job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    queued_job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    queued_job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(queued_job1)
    sf.add(queued_job2)
    sf.add(queued_job3)

    # Wait until queue has 3 jobs
    env.run(until=0.1)

    # Queue should have 3 jobs with positive PST (2 are processing)
    assert len(server.queue) == 3

    # Add candidate job to PSP with negative PST (urgent, past due)
    urgent_job = ProductionJob(
        env=env,
        sku="A",
        servers=[server],
        processing_times=[0.5],  # Short processing time (selected by min)
        due_date=env.now - 10.0,  # Already past due (negative PST)
    )
    psp.add(urgent_job)

    # Run until first job finishes - this triggers the release check
    # At t=5: processing_job1 finishes, queued_job1 starts processing
    # Queue now has 2 jobs (queued_job2, queued_job3) - all with positive PST
    # elif branch triggers, urgent_job gets released
    env.run(until=6)

    # The urgent job with negative PST should be released
    # (because queue has 2+ jobs, all with positive PST, and this one has negative)
    assert urgent_job not in list(psp.jobs)
