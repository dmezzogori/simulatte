from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.faulty_server import FaultyServer
from simulatte.job import ProductionJob
from simulatte.shopfloor import ShopFloor


def test_faulty_server_no_breakdown() -> None:
    """Processing completes before any breakdown occurs."""
    env = Environment()
    sf = ShopFloor(env=env)

    # Breakdown will occur at t=100, but job finishes at t=5
    server = FaultyServer(
        env=env,
        capacity=1,
        time_between_failures_distribution=lambda: 100.0,
        repair_time_distribution=lambda: 10.0,
        shopfloor=sf,
    )

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    sf.add(job)
    env.run(until=10)  # Run until job completes (breakdown_process runs forever)

    assert job.done
    assert server.breakdowns == 0
    assert server.breakdown_time == 0
    assert server.worked_time == pytest.approx(5.0)


def test_faulty_server_single_breakdown() -> None:
    """Processing interrupted by one breakdown, then resumed."""
    env = Environment()
    sf = ShopFloor(env=env)

    # Use a counter to ensure only one breakdown
    breakdown_count = [0]

    def tbf() -> float:
        breakdown_count[0] += 1
        if breakdown_count[0] == 1:
            return 2.0  # First breakdown at t=2
        return 1000.0  # No more breakdowns

    server = FaultyServer(
        env=env,
        capacity=1,
        time_between_failures_distribution=tbf,
        repair_time_distribution=lambda: 5.0,
        shopfloor=sf,
    )

    # Job needs 10 time units
    # Timeline: 0-2 processing, 2-7 repair, 7-15 remaining (8 units)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=50.0)
    sf.add(job)
    env.run(until=20)

    assert job.done
    assert server.breakdowns == 1
    assert server.breakdown_time == pytest.approx(5.0)
    assert server.worked_time == pytest.approx(10.0)


def test_faulty_server_breakdown_metrics() -> None:
    """Verify breakdown count and total breakdown time."""
    env = Environment()
    sf = ShopFloor(env=env)

    breakdown_count = 0

    def tbf_dist() -> float:
        nonlocal breakdown_count
        breakdown_count += 1
        return 1.0  # Breakdown every 1 time unit

    server = FaultyServer(
        env=env,
        capacity=1,
        time_between_failures_distribution=tbf_dist,
        repair_time_distribution=lambda: 0.5,  # Quick repair
        shopfloor=sf,
    )

    # Long job that will experience multiple breakdowns
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[3.0], due_date=50.0)
    sf.add(job)
    env.run(until=20)

    assert job.done
    assert server.breakdowns >= 1
    assert server.breakdown_time > 0


def test_faulty_server_retains_job_history() -> None:
    """Test with retain_job_history=True."""
    env = Environment()
    sf = ShopFloor(env=env)

    server = FaultyServer(
        env=env,
        capacity=1,
        time_between_failures_distribution=lambda: 100.0,
        repair_time_distribution=lambda: 1.0,
        shopfloor=sf,
        retain_job_history=True,
    )

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    sf.add(job)
    env.run(until=10)

    assert server._jobs is not None and job in server._jobs


def test_faulty_server_multiple_jobs() -> None:
    """Process multiple jobs, some with breakdowns."""
    env = Environment()
    sf = ShopFloor(env=env)

    call_count = 0

    def tbf_dist() -> float:
        nonlocal call_count
        call_count += 1
        # First breakdown at t=100 (no breakdown for first jobs)
        # Then regular breakdowns
        if call_count == 1:
            return 100.0
        return 50.0

    server = FaultyServer(
        env=env,
        capacity=1,
        time_between_failures_distribution=tbf_dist,
        repair_time_distribution=lambda: 5.0,
        shopfloor=sf,
    )

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=50.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10.0], due_date=100.0)

    sf.add(job1)
    sf.add(job2)
    env.run(until=50)

    assert job1.done
    assert job2.done
    assert server.worked_time == pytest.approx(20.0)
