from __future__ import annotations


from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.slar import Slar
from simulatte.policies.triggers import on_completion_trigger
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


def test_slar_release_when_server_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    # Start SLAR release trigger process
    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

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

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    # Add two jobs to shopfloor - one processing, one waiting
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    sf.add(job1)
    sf.add(job2)

    # Add candidate job to PSP
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job3)

    # At t=2 job1 finishes: queue has exactly 1 (job2) -> postponed release of job3
    # scheduled with a ~0.001 delay. Run past the delay.
    env.run(until=3)

    assert job3 not in psp.jobs


def test_slar_no_release_when_no_candidates() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

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

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

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
    """Urgent-in-PSP branch: release a negative-PST PSP job when all queued jobs are non-urgent.

    Covers the branch where:
    - Queue has 2+ jobs (not empty, not has_one)
    - All queued jobs have positive PST (non-urgent)
    - An urgent job (negative PST) in PSP gets released (min proc-time).
    """
    env = Environment()
    sf = ShopFloor(env=env)
    # Use capacity=2 so multiple jobs can queue while two are processing
    server = Server(env=env, capacity=2, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    # Two long-running jobs with far due dates (positive PST)
    processing_job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=1000.0)
    processing_job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=1000.0)
    sf.add(processing_job1)
    sf.add(processing_job2)

    # Three queued jobs with far due dates (positive PST).
    # When one processing job finishes, a queued job starts, leaving 2 in queue
    # -> triggers the urgent-in-PSP branch (non-empty queue, all non-urgent).
    queued_job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    queued_job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    queued_job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(queued_job1)
    sf.add(queued_job2)
    sf.add(queued_job3)

    # Wait until queue has 3 jobs
    env.run(until=0.1)
    assert len(server.queue) == 3

    # Candidate urgent PSP job (negative PST, short proc time)
    urgent_job = ProductionJob(
        env=env,
        sku="A",
        servers=[server],
        processing_times=[0.5],
        due_date=env.now - 10.0,  # already past due -> negative PST
    )
    psp.add(urgent_job)

    # At t=5 one processing job finishes. Queue now has 2 non-urgent jobs, so
    # the urgent-in-PSP branch releases urgent_job immediately.
    env.run(until=6)

    assert urgent_job not in list(psp.jobs)


def test_slar_no_release_when_urgent_job_in_queue() -> None:
    """Covers the branch where the queued mix contains at least one urgent job.

    When a queued job is already urgent and the queue length is >= 2, SLAR
    skips both the urgent-in-PSP branch (condition ``all positive`` is False)
    and the has_one starvation branch. No release happens.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    # Processing job, slow, non-urgent due date
    job_proc = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=1000.0)
    # Two queued jobs: one urgent (past due), one non-urgent
    job_urgent_q = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=-10.0)
    job_normal_q = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(job_proc)
    sf.add(job_urgent_q)
    sf.add(job_normal_q)

    # PSP candidate (non-urgent) that would be picked if any SLAR branch triggered
    psp_candidate = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    psp.add(psp_candidate)

    # Run past completion of job_proc at t=2. Queue at trigger moment has 2 jobs,
    # one urgent -> branch A skipped, branch B skipped (len != 1).
    env.run(until=3)

    assert psp_candidate in list(psp.jobs)


def test_slar_no_psp_candidate_for_postponed_release() -> None:
    """Covers the branch where queue has exactly 1 but PSP has no candidate.

    Branch B attempts starvation avoidance via postponed release but finds no
    PSP job starting at the triggering server, so nothing happens.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    server_b = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    # Two jobs on server_a (one processes, one queues) -> queue == 1 at completion.
    job1 = ProductionJob(env=env, sku="A", servers=[server_a], processing_times=[2.0], due_date=1000.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server_a], processing_times=[2.0], due_date=1000.0)
    sf.add(job1)
    sf.add(job2)

    # PSP candidate starts at a DIFFERENT server -> no release possible.
    other_psp = ProductionJob(env=env, sku="B", servers=[server_b], processing_times=[1.0], due_date=1000.0)
    psp.add(other_psp)

    env.run(until=3)

    assert other_psp in list(psp.jobs)


def test_slar_postponed_release_delay() -> None:
    """has_one-queue branch: PSP candidate is released via a postponed SimPy process.

    At the completion tick the PSP job is still in PSP; after the 0.001 delay
    it has been moved to the shopfloor.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    # Two jobs on server: one processing, one queued -> queue will have exactly 1
    # when job1 finishes.
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    sf.add(job1)
    sf.add(job2)

    # PSP candidate
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job3)

    # Stop between the completion event (t=2.0) and the postponed release
    # timeout (t=2.001). job3 should already be removed from PSP but not yet
    # added to the shopfloor.
    env.run(until=2.0005)
    assert job3 not in psp.jobs
    assert job3 not in sf.jobs

    # After the timeout elapses, the shopfloor has received job3.
    env.run(until=2.1)
    assert job3 in sf.jobs


def test_slar_pst_priority_orders_queue_correctly() -> None:
    """Jobs in the server queue must be ordered by PST when using SLAR's priority policy.

    This verifies the integration between Slar.pst_priority_policy (wired via the
    Router's priority_policies) and the ServerPriorityRequest, ensuring that jobs
    with lower PST (more urgent) are processed before jobs with higher PST.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    def pst_policy(job: ProductionJob, server: Server) -> float:
        return slar.pst_priority_policy(job, server) or 0.0

    # Blocker occupies the server so subsequent jobs queue up
    blocker = ProductionJob(
        env=env, sku="BLOCK", servers=[server], processing_times=[10.0], due_date=1000.0,
        priority_policy=pst_policy,
    )
    sf.add(blocker)
    env.run(until=0.01)

    # Add three jobs with different due dates (and thus different PSTs).
    # Lower due_date -> lower PST -> more urgent -> should process first.
    job_relaxed = ProductionJob(
        env=env, sku="RELAXED", servers=[server], processing_times=[1.0], due_date=50.0,
        priority_policy=pst_policy,
    )
    job_medium = ProductionJob(
        env=env, sku="MEDIUM", servers=[server], processing_times=[1.0], due_date=20.0,
        priority_policy=pst_policy,
    )
    job_urgent = ProductionJob(
        env=env, sku="URGENT", servers=[server], processing_times=[1.0], due_date=8.0,
        priority_policy=pst_policy,
    )
    # Add in non-PST order to verify priority sorting, not insertion order
    sf.add(job_relaxed)
    sf.add(job_medium)
    sf.add(job_urgent)

    env.run()

    # Verify processing order: urgent first, then medium, then relaxed
    assert job_urgent.servers_exit_at[server] < job_medium.servers_exit_at[server]
    assert job_medium.servers_exit_at[server] < job_relaxed.servers_exit_at[server]


def test_slar_pst_priority_discriminates_fractional_differences() -> None:
    """PST-based priority must distinguish jobs whose PST differs by less than 1.0.

    This is a regression test for the int() truncation bug: if priority values
    are truncated to int, jobs with PST=2.3 and PST=2.8 both become priority 2,
    losing the ordering guarantee.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    def pst_policy(job: ProductionJob, server: Server) -> float:
        return slar.pst_priority_policy(job, server) or 0.0

    blocker = ProductionJob(
        env=env, sku="BLOCK", servers=[server], processing_times=[10.0], due_date=1000.0,
        priority_policy=pst_policy,
    )
    sf.add(blocker)
    env.run(until=0.01)

    # Two jobs whose PSTs differ by less than 1.0 AND share the same integer part.
    # PST = (due - now) - (proc + allowance) = (due - 0.01) - (1.0 + 2.0) = due - 3.01
    # job_a: due=10.3 -> PST ≈ 7.29 -> int(7.29) = 7
    # job_b: due=10.7 -> PST ≈ 7.69 -> int(7.69) = 7
    # Both truncate to int priority 7, so int() truncation would make them FIFO.
    job_a = ProductionJob(
        env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.3,
        priority_policy=pst_policy,
    )
    job_b = ProductionJob(
        env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.7,
        priority_policy=pst_policy,
    )

    pst_a = slar.pst_priority_policy(job_a, server)
    pst_b = slar.pst_priority_policy(job_b, server)
    assert pst_a is not None and pst_b is not None
    assert pst_a < pst_b  # A is more urgent

    # Add in reverse PST order (B first) to ensure priority wins over insertion order
    sf.add(job_b)
    sf.add(job_a)

    env.run()

    # A (lower PST) must process before B despite being added second
    assert job_a.servers_exit_at[server] < job_b.servers_exit_at[server]


def test_slar_urgent_psp_job_processes_before_non_urgent_queued() -> None:
    """Branch A: an urgent PSP job released into the queue should process before
    a non-urgent queued job, thanks to PST-based priority ordering.

    When the trigger fires with queue=1 (non-urgent) and an urgent PSP job exists,
    Branch A releases the urgent job immediately. The priority policy ensures the
    urgent job gets the server first.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    slar = Slar(allowance_factor=2)

    def pst_policy(job: ProductionJob, server: Server) -> float:
        return slar.pst_priority_policy(job, server) or 0.0

    env.process(on_completion_trigger(sf, psp, slar.decide_next_job))

    job1 = ProductionJob(
        env=env, sku="J1", servers=[server], processing_times=[5.0], due_date=100.0,
        priority_policy=pst_policy,
    )
    job2 = ProductionJob(
        env=env, sku="J2", servers=[server], processing_times=[3.0], due_date=50.0,
        priority_policy=pst_policy,
    )
    sf.add(job1)
    sf.add(job2)

    # PSP candidate with a very tight due date (urgent, negative PST).
    # Branch A releases it immediately; priority ordering puts it ahead of job2.
    job3 = ProductionJob(
        env=env, sku="J3", servers=[server], processing_times=[2.0], due_date=3.0,
        priority_policy=pst_policy,
    )
    psp.add(job3)

    env.run()

    # Urgent job3 must process before non-urgent job2
    assert job3.servers_exit_at[server] < job2.servers_exit_at[server]
