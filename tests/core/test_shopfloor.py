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
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
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
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[3], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[4], due_date=10)
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
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[5], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(10)

    env.run()

    assert job1.finished_at == pytest.approx(5)
    assert job2.finished_at == pytest.approx(5)
    assert env.now == pytest.approx(5)

    assert server.worked_time == pytest.approx(10)
    assert server.utilization_rate == pytest.approx(2.0)


def test_corrected_wip_strategy() -> None:
    from simulatte.shopfloor import CorrectedWIPStrategy

    env = Environment()
    shopfloor = ShopFloor(env=env, wip_strategy=CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server2 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server3 = Server(env=env, capacity=1, shopfloor=shopfloor)
    job1 = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[2, 3], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server2, server3], processing_times=[4, 5], due_date=10)
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
        sku="A",
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
        sku="A",
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

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=100)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[4.0], due_date=100)
    sf.add(job1)
    sf.add(job2)
    env.run()

    # job1 time_in_system = 2.0 (exit at t=2, enter at t=0)
    # job2 time_in_system = 6.0 (exit at t=6, enter at t=0)
    # average = (2 + 6) / 2 = 4.0
    assert sf.average_time_in_system == pytest.approx(4.0)


# =============================================================================
# Tests for new extensibility features
# =============================================================================


def test_before_operation_hook_adds_setup_time() -> None:
    """before_operation hook should inject time before processing."""
    from simulatte.typing import ProcessGenerator

    setup_times: list[float] = []

    def setup_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        setup_times.append(server.env.now)
        yield server.env.timeout(2.0)  # 2s setup time

    env = Environment()
    sf = ShopFloor(env=env, before_operation=setup_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    # Total time = setup (2) + processing (5) = 7
    assert job.finished_at == pytest.approx(7.0)
    assert len(setup_times) == 1
    assert setup_times[0] == pytest.approx(0.0)  # Hook ran at t=0


def test_after_operation_hook_executes_after_processing() -> None:
    """after_operation hook should run after processing completes."""
    from simulatte.typing import ProcessGenerator

    hook_times: list[float] = []

    def after_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        hook_times.append(server.env.now)
        yield server.env.timeout(1.0)  # 1s cleanup

    env = Environment()
    sf = ShopFloor(env=env, after_operation=after_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    # Hook runs after processing (at t=5), then cleanup takes 1s
    # But job.finished_at is set before the after hooks complete
    assert len(hook_times) == 1
    assert hook_times[0] == pytest.approx(5.0)  # Hook ran after processing


def test_multiple_hooks_execute_in_order() -> None:
    """Multiple hooks should execute in order."""
    from simulatte.shopfloor import OperationHook
    from simulatte.typing import ProcessGenerator

    execution_order: list[str] = []

    def hook1(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("hook1")
        return
        yield  # Make it a generator

    def hook2(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("hook2")
        return
        yield

    hooks: list[OperationHook] = [hook1, hook2]  # type: ignore[list-item]
    env = Environment()
    sf = ShopFloor(env=env, before_operation=hooks)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert execution_order == ["hook1", "hook2"]


def test_wip_strategy_corrected_via_constructor() -> None:
    """CorrectedWIPStrategy via constructor should work like deprecated flag."""
    from simulatte.shopfloor import CorrectedWIPStrategy

    env = Environment()
    sf = ShopFloor(env=env, wip_strategy=CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[2, 4], due_date=10)
    sf.add(job)

    # With corrected WIP: server1 gets 2/1=2, server2 gets 4/2=2
    assert sf.wip[server1] == pytest.approx(2.0)
    assert sf.wip[server2] == pytest.approx(2.0)

    env.run()
    assert job.done
    assert sf.wip[server1] == pytest.approx(0.0)
    assert sf.wip[server2] == pytest.approx(0.0)


def test_custom_metrics_collector() -> None:
    """Custom metrics collector should receive completed jobs."""

    class SimpleCollector:
        def __init__(self) -> None:
            self.jobs_recorded: list[ProductionJob] = []

        def record(self, job: ProductionJob) -> None:
            self.jobs_recorded.append(job)

    collector = SimpleCollector()
    env = Environment()
    sf = ShopFloor(env=env, metrics_collector=collector)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[3], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[4], due_date=20)
    sf.add(job1)
    sf.add(job2)
    env.run()

    assert len(collector.jobs_recorded) == 2
    assert collector.jobs_recorded[0] is job1
    assert collector.jobs_recorded[1] is job2


def test_no_metrics_collector() -> None:
    """ShopFloor with metrics_collector=None should work without recording metrics."""

    class NoOpCollector:
        """A collector that does nothing."""

        def record(self, job: ProductionJob) -> None:
            pass

    env = Environment()
    sf = ShopFloor(env=env, metrics_collector=NoOpCollector())
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert job.done
    assert job.finished_at == pytest.approx(5.0)


def test_on_job_finished_callback() -> None:
    """on_job_finished callback should be called when job completes."""
    finished_jobs: list[ProductionJob] = []

    def on_finished(job: ProductionJob) -> None:
        finished_jobs.append(job)

    env = Environment()
    sf = ShopFloor(env=env, on_job_finished=on_finished)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert len(finished_jobs) == 1
    assert finished_jobs[0] is job


def test_multiple_on_job_finished_callbacks() -> None:
    """Multiple on_job_finished callbacks should all be called."""
    callback1_count = [0]
    callback2_count = [0]

    def cb1(job: ProductionJob) -> None:
        callback1_count[0] += 1

    def cb2(job: ProductionJob) -> None:
        callback2_count[0] += 1

    env = Environment()
    sf = ShopFloor(env=env, on_job_finished=[cb1, cb2])
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert callback1_count[0] == 1
    assert callback2_count[0] == 1


def test_hooks_with_multi_server_routing() -> None:
    """Hooks should be called for each operation in multi-server routing."""
    from simulatte.typing import ProcessGenerator

    hook_calls: list[tuple[int, int]] = []  # (server_idx, op_index)

    def track_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        hook_calls.append((server._idx, op_index))
        return
        yield

    env = Environment()
    sf = ShopFloor(env=env, before_operation=track_hook)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    server3 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(
        env=env,
        sku="A",
        servers=[server1, server2, server3],
        processing_times=[2, 3, 4],
        due_date=20,
    )
    sf.add(job)
    env.run()

    assert len(hook_calls) == 3
    assert hook_calls[0] == (0, 0)  # server1, op 0
    assert hook_calls[1] == (1, 1)  # server2, op 1
    assert hook_calls[2] == (2, 2)  # server3, op 2
