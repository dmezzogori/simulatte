"""Tests for job type hierarchy."""

from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import (
    BaseJob,
    JobType,
    ProductionJob,
    TransportJob,
    WarehouseJob,
)
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_production_job_type() -> None:
    """ProductionJob should have PRODUCTION job type."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(
        env=env,
        sku="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert job.job_type == JobType.PRODUCTION
    assert isinstance(job, BaseJob)


def test_production_job_material_requirements() -> None:
    """ProductionJob should support material requirements per operation."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    materials = {
        0: {"steel": 2, "bolts": 10},
        1: {"paint": 1},
    }
    job = ProductionJob(
        env=env,
        sku="A",
        servers=[s1, s2],
        processing_times=[5, 3],
        due_date=20,
        material_requirements=materials,
    )

    assert job.get_materials_for_operation(0) == {"steel": 2, "bolts": 10}
    assert job.get_materials_for_operation(1) == {"paint": 1}
    assert job.get_materials_for_operation(2) == {}  # No requirements


def test_production_job_default_no_materials() -> None:
    """ProductionJob without material_requirements should have empty dict."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(
        env=env,
        sku="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert job.material_requirements == {}
    assert job.get_materials_for_operation(0) == {}


def test_transport_job_type() -> None:
    """TransportJob should have TRANSPORT job type."""
    env = Environment()
    sf = ShopFloor(env=env)
    origin = Server(env=env, capacity=1, shopfloor=sf)
    destination = Server(env=env, capacity=1, shopfloor=sf)

    job = TransportJob(
        env=env,
        origin=origin,
        destination=destination,
        cargo={"steel": 5},
    )

    assert job.job_type == JobType.TRANSPORT
    assert job.origin is origin
    assert job.destination is destination
    assert job.cargo == {"steel": 5}
    assert job.sku == "transport"
    assert isinstance(job, BaseJob)


def test_warehouse_job_pick() -> None:
    """WarehouseJob should support pick operations."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    job = WarehouseJob(
        env=env,
        warehouse=warehouse,
        product="steel",
        quantity=5,
        operation_type="pick",
        processing_time=2.0,
    )

    assert job.job_type == JobType.WAREHOUSE
    assert job.product == "steel"
    assert job.quantity == 5
    assert job.operation_type == "pick"
    assert job.sku == "warehouse_pick"
    assert job.processing_times == (2.0,)
    assert isinstance(job, BaseJob)


def test_warehouse_job_put() -> None:
    """WarehouseJob should support put operations."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    job = WarehouseJob(
        env=env,
        warehouse=warehouse,
        product="bolts",
        quantity=100,
        operation_type="put",
    )

    assert job.operation_type == "put"
    assert job.sku == "warehouse_put"


def test_warehouse_job_invalid_operation() -> None:
    """WarehouseJob should reject invalid operation types."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    with pytest.raises(ValueError, match="operation_type must be 'pick' or 'put'"):
        WarehouseJob(
            env=env,
            warehouse=warehouse,
            product="steel",
            quantity=5,
            operation_type="move",
        )


def test_base_job_is_abstract() -> None:
    """BaseJob should not be directly instantiable."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    with pytest.raises(TypeError, match="abstract"):
        BaseJob(  # type: ignore[abstract]
            env=env,
            job_type=JobType.PRODUCTION,
            sku="A",
            servers=[server],
            processing_times=[5],
            due_date=10,
        )


def test_job_repr() -> None:
    """Each job type should have appropriate repr."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    prod_job = ProductionJob(
        env=env,
        sku="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert "ProductionJob" in repr(prod_job)
    assert prod_job.id in repr(prod_job)

    transport_job = TransportJob(
        env=env,
        origin=server,
        destination=server,
        cargo={"x": 1},
    )
    assert "TransportJob" in repr(transport_job)
    assert "cargo" in repr(transport_job)

    warehouse_job = WarehouseJob(
        env=env,
        warehouse=server,
        product="y",
        quantity=2,
        operation_type="pick",
    )
    assert "WarehouseJob" in repr(warehouse_job)
    assert "pick" in repr(warehouse_job)
    assert "2x y" in repr(warehouse_job)


class TestJobProperties:
    """Tests for BaseJob property edge cases."""

    def test_makespan_not_finished(self) -> None:
        """makespan should use env.now - created_at when job not finished."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        # Advance time without finishing job
        env.run(until=10)
        assert job.makespan == 10.0

    def test_server_waiting_times_in_progress(self) -> None:
        """server_waiting_times when job is in progress (entry_at set, exit_at None)."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)

        # Start processing
        sf.add(job)
        env.run(until=5)  # Mid-processing

        # Job is in progress: entry_at set, exit_at is None
        waiting_times = job.server_waiting_times
        assert server in waiting_times
        # Should return env.now - entry_at since exit_at is None
        assert waiting_times[server] == pytest.approx(5.0)

    def test_server_waiting_times_not_entered(self) -> None:
        """server_waiting_times when job hasn't entered server (both None)."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1, 1], due_date=100)

        # Don't add to shopfloor - servers_entry_at will be None
        waiting_times = job.server_waiting_times
        # Both servers should return None since job hasn't entered them
        assert waiting_times[s1] is None
        assert waiting_times[s2] is None

    def test_total_queue_time_not_done_raises(self) -> None:
        """total_queue_time should raise when job not done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        with pytest.raises(ValueError, match="Job is not done"):
            _ = job.total_queue_time

    def test_total_queue_time_missing_timing_raises(self) -> None:
        """total_queue_time should raise when timing info is missing."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        # Mark as done but don't set timestamps properly
        job.done = True
        # servers_entry_at and servers_exit_at are None
        with pytest.raises(ValueError, match="missing timing information"):
            _ = job.total_queue_time

    def test_time_in_system_not_done_raises(self) -> None:
        """time_in_system should raise when job not done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        with pytest.raises(ValueError, match="Job is not done"):
            _ = job.time_in_system

    def test_late_when_done(self) -> None:
        """late should check finished_at vs due_date when done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[50], due_date=10)

        sf.add(job)
        env.run(until=60)

        assert job.done
        assert job.late  # finished at ~50, due at 10

    def test_late_when_not_done(self) -> None:
        """late should check env.now vs due_date when not done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=10)

        sf.add(job)
        env.run(until=15)

        assert not job.done
        assert job.late  # env.now (15) > due_date (10)

    def test_is_in_psp_false_after_release(self) -> None:
        """is_in_psp should be False after psp_exit_at is set."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        assert job.is_in_psp  # Before release
        job.psp_exit_at = env.now
        assert not job.is_in_psp  # After release

    def test_time_in_psp_not_released_raises(self) -> None:
        """time_in_psp should raise when job hasn't been released from PSP."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        with pytest.raises(ValueError, match="not been released from PSP"):
            _ = job.time_in_psp

    def test_next_server_in_psp(self) -> None:
        """next_server should return first server when job is in PSP."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5, 5], due_date=100)

        assert job.is_in_psp
        assert job.next_server is s1

    def test_next_server_in_shopfloor(self) -> None:
        """next_server should return next unvisited server when in shopfloor."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1, 1], due_date=100)

        sf.add(job)
        env.run(until=1.5)  # s1 done, s2 in progress

        assert job.psp_exit_at is not None  # Not in PSP
        # s1 has been entered, so next_server should be s2
        assert job.servers_entry_at[s1] is not None
        if job.servers_entry_at[s2] is None:
            assert job.next_server is s2
        else:
            # If s2 already entered, next_server should be None
            assert job.next_server is None

    def test_next_server_none_when_done(self) -> None:
        """next_server should return None when all servers visited."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=100)

        sf.add(job)
        env.run(until=2)

        assert job.done
        assert job.next_server is None

    def test_previous_server_none_when_not_started(self) -> None:
        """previous_server should return None when no servers have been exited."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        assert job.previous_server is None

    def test_previous_server_after_exiting(self) -> None:
        """previous_server should return last exited server."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1, 5], due_date=100)

        sf.add(job)
        env.run(until=2)  # s1 done, s2 in progress

        assert job.previous_server is s1

    def test_lateness_not_done_raises(self) -> None:
        """lateness should raise when job not done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        with pytest.raises(ValueError, match="Job is not done"):
            _ = job.lateness

    def test_is_finished_in_due_date_window_not_done_raises(self) -> None:
        """is_finished_in_due_date_window should raise when job not done."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        with pytest.raises(ValueError, match="Job is not done"):
            _ = job.is_finished_in_due_date_window()

    def test_planned_release_date(self) -> None:
        """planned_release_date should calculate based on processing times and allowance."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5, 3], due_date=100)

        # due_date - (sum of processing times + n_servers * allowance)
        # 100 - (8 + 2*2) = 100 - 12 = 88
        assert job.planned_release_date(allowance=2) == 88

    def test_virtual_lateness(self) -> None:
        """virtual_lateness should be env.now - due_date."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=50)

        env.run(until=60)
        assert job.virtual_lateness == 10.0  # 60 - 50

    def test_would_be_finished_in_due_date_window(self) -> None:
        """would_be_finished_in_due_date_window should check current time vs window."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=50)

        env.run(until=45)  # Within window [43, 57]
        assert job.would_be_finished_in_due_date_window(allowance=7)

        env.run(until=60)  # Outside window
        assert not job.would_be_finished_in_due_date_window(allowance=7)

    def test_virtual_tardy(self) -> None:
        """virtual_tardy should be True when virtual_lateness > 0."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=50)

        env.run(until=60)
        assert job.virtual_tardy

    def test_virtual_early(self) -> None:
        """virtual_early should be True when virtual_lateness < 0."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=50)

        env.run(until=40)
        assert job.virtual_early

    def test_virtual_in_window(self) -> None:
        """virtual_in_window should use default allowance of 7."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=50)

        env.run(until=50)
        assert job.virtual_in_window  # At exactly due_date

    def test_planned_slack_time(self) -> None:
        """planned_slack_time should be slack_time minus sum of processing times."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5, 3], due_date=50)

        # At t=0: slack_time = 50 - 0 = 50
        # planned_slack_time = 50 - (5 + 3) = 42
        assert job.planned_slack_time == 42.0
