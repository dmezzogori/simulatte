from __future__ import annotations

import pytest

from simulatte.agv import AGV
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.materials import MaterialCoordinator
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.warehouse_store import WarehouseStore


def test_single_job_processing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, family="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)

    assert job in sf.jobs
    assert not sf.jobs_done
    assert sf.wip[server] == pytest.approx(5)

    env.run()

    assert job.done
    assert job.psp_exit_at == pytest.approx(0)
    assert job.finished_at == pytest.approx(5)
    assert job in sf.jobs_done
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(5)
    assert server.utilization_rate == pytest.approx(1.0)
    assert server.idle_time == pytest.approx(0.0)


def test_multiple_jobs_sequential_processing_and_queue() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, family="A", servers=[server], processing_times=[3], due_date=10)
    job2 = ProductionJob(env=env, family="B", servers=[server], processing_times=[4], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(7)
    assert server.count == 0

    env.run()

    assert job1.done
    assert job2.done
    assert job1.finished_at == pytest.approx(3)
    assert job2.finished_at == pytest.approx(7)
    assert sf.jobs_done == [job1, job2]
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(7)
    assert server.average_queue_length == (1 * 3 + 0 * 4) / 7
    assert server.utilization_rate == 1


def test_parallel_processing_with_capacity() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=2, shopfloor=sf)
    job1 = ProductionJob(env=env, family="A", servers=[server], processing_times=[5], due_date=10)
    job2 = ProductionJob(env=env, family="B", servers=[server], processing_times=[5], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(10)

    env.run()

    assert job1.finished_at == pytest.approx(5)
    assert job2.finished_at == pytest.approx(5)
    assert env.now == pytest.approx(5)

    assert server.worked_time == pytest.approx(10)
    assert server.utilization_rate == pytest.approx(2.0)


def test_enable_corrected_wip() -> None:
    env = Environment()
    shopfloor = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server2 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server3 = Server(env=env, capacity=1, shopfloor=shopfloor)
    shopfloor.enable_corrected_wip = True
    job1 = ProductionJob(env=env, family="A", servers=[server1, server2], processing_times=[2, 3], due_date=10)
    job2 = ProductionJob(env=env, family="B", servers=[server2, server3], processing_times=[4, 5], due_date=10)
    shopfloor.add(job1)
    shopfloor.add(job2)

    assert shopfloor.wip[server1] == 2
    assert shopfloor.wip[server2] == 5.5
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job1.current_server == server2
    assert job1.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 7
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job2.current_server == server3
    assert job2.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 3
    assert shopfloor.wip[server3] == 5

    env.run(until=shopfloor.job_processing_end)
    assert job1.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 5

    env.run()
    assert job2.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 0


def test_automatic_material_handling_via_shopfloor() -> None:
    """ShopFloor with material_coordinator should handle materials automatically."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    warehouse = WarehouseStore(
        env=env,
        n_bays=1,
        products=["steel"],
        initial_inventory={"steel": 100},
        pick_time_fn=lambda: 1.0,
        put_time_fn=lambda: 0.5,
        shopfloor=sf,
    )

    agv = AGV(
        env=env,
        travel_time_fn=lambda o, d: 2.0,
        shopfloor=sf,
    )

    coordinator = MaterialCoordinator(
        env=env,
        warehouse=warehouse,
        agvs=[agv],
        shopfloor=sf,
    )

    # Wire coordinator to shopfloor
    sf.material_coordinator = coordinator

    # Create job with material requirements
    job = ProductionJob(
        env=env,
        family="A",
        servers=[server],
        processing_times=[3.0],
        due_date=100,
        material_requirements={0: {"steel": 5}},
    )

    # Add job via shopfloor - materials should be handled automatically
    sf.add(job)
    env.run()

    assert job.done
    # Time = pick (1.0) + travel (2.0) + processing (3.0) = 6.0
    assert job.finished_at == pytest.approx(6.0)
    assert warehouse.get_inventory_level("steel") == 95
    assert coordinator.total_deliveries == 1
    assert agv.trip_count == 1


def test_shopfloor_without_coordinator_works() -> None:
    """ShopFloor without material_coordinator should work normally."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    # Job with material requirements but no coordinator configured
    job = ProductionJob(
        env=env,
        family="A",
        servers=[server],
        processing_times=[5.0],
        due_date=100,
        material_requirements={0: {"steel": 5}},  # Will be ignored
    )

    sf.add(job)
    env.run()

    assert job.done
    # No material handling, just processing time
    assert job.finished_at == pytest.approx(5.0)


def test_average_time_in_system_no_jobs_done() -> None:
    """average_time_in_system should return 0.0 when no jobs are done."""
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)

    assert sf.average_time_in_system == 0.0


def test_average_time_in_system_with_jobs() -> None:
    """average_time_in_system should calculate correctly when jobs are done."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job1 = ProductionJob(env=env, family="A", servers=[server], processing_times=[2.0], due_date=100)
    job2 = ProductionJob(env=env, family="A", servers=[server], processing_times=[4.0], due_date=100)
    sf.add(job1)
    sf.add(job2)
    env.run()

    # job1 time_in_system = 2.0 (exit at t=2, enter at t=0)
    # job2 time_in_system = 6.0 (exit at t=6, enter at t=0)
    # average = (2 + 6) / 2 = 4.0
    assert sf.average_time_in_system == pytest.approx(4.0)


def test_update_hourly_throughput_snapshot() -> None:
    """Throughput snapshot should update after time window passes."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    # Add and process a job quickly
    job1 = ProductionJob(env=env, family="A", servers=[server], processing_times=[1], due_date=100)
    sf.add(job1)
    env.run(until=2)

    assert job1.done

    # Advance time past the 60-second window
    env.run(until=65)

    # Process another job - this should trigger the throughput update
    job2 = ProductionJob(env=env, family="A", servers=[server], processing_times=[1], due_date=200)
    sf.add(job2)
    env.run(until=70)

    # The hourly throughput snapshot should have been updated
    # We just check that the code ran without error
    assert sf.last_throughput_snapshot_time > 0
