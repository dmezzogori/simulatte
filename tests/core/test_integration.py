"""Integration tests for the complete material handling system."""

from __future__ import annotations

import pytest

from simulatte.builders import MaterialSystemBuilder
from simulatte.environment import Environment
from simulatte.job import ProductionJob


class TestMaterialSystemIntegration:
    """Integration tests for MaterialSystemBuilder and full simulations."""

    def test_material_system_builder(self) -> None:
        """MaterialSystemBuilder should create all components."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(env)

        assert len(servers) == 6
        assert len(agvs) == 3
        assert warehouse.capacity == 2
        assert coordinator.warehouse is warehouse
        assert len(coordinator.agvs) == 3

    def test_material_system_custom_config(self) -> None:
        """MaterialSystemBuilder should accept custom configuration."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=3,
            n_agvs=2,
            n_bays=4,
            products=["X", "Y"],
            initial_inventory={"X": 50, "Y": 100},
        )

        assert len(servers) == 3
        assert len(agvs) == 2
        assert warehouse.capacity == 4
        assert warehouse.products == ["X", "Y"]
        assert warehouse.get_inventory_level("X") == 50
        assert warehouse.get_inventory_level("Y") == 100

    def test_full_simulation_without_materials(self) -> None:
        """Jobs without material requirements should complete normally."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=2,
        )

        # Create a job with no material requirements
        job = ProductionJob(
            env=env,
            family="A",
            servers=[servers[0], servers[1]],
            processing_times=[3.0, 2.0],
            due_date=100,
        )

        # Add job directly - shopfloor.add() starts the processing
        shopfloor.add(job)
        env.run()

        assert job.done
        assert job.finished_at == pytest.approx(5.0)

    def test_full_simulation_with_materials(self) -> None:
        """Jobs with material requirements should wait for delivery."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=2,
            n_agvs=1,
            products=["steel"],
            initial_inventory={"steel": 100},
            pick_time=1.0,
            travel_time=2.0,
        )

        # Create a job that requires materials at operation 0
        job = ProductionJob(
            env=env,
            family="A",
            servers=[servers[0]],
            processing_times=[3.0],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        def run_job_with_materials():
            with servers[0].request(job=job) as req:
                yield req
                # Ensure materials before processing (FIFO blocking)
                yield from coordinator.ensure(job, servers[0], 0)
                yield env.process(servers[0].process_job(job, job.processing_times[0]))
            job.done = True
            job.finished_at = env.now

        env.process(run_job_with_materials())
        env.run()

        assert job.done
        # Time = pick (1.0) + travel (2.0) + processing (3.0) = 6.0
        assert job.finished_at == pytest.approx(6.0)
        assert warehouse.get_inventory_level("steel") == 95
        assert coordinator.total_deliveries == 1

    def test_multiple_jobs_fifo_ordering(self) -> None:
        """Multiple jobs should maintain FIFO order with material blocking."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=1,
            n_agvs=1,
            products=["steel"],
            initial_inventory={"steel": 100},
            pick_time=1.0,
            travel_time=2.0,
        )

        server = servers[0]
        completion_order = []

        # Job 1: requires materials
        job1 = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[1.0],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        # Job 2: no materials, arrives slightly later
        job2 = ProductionJob(
            env=env,
            family="B",
            servers=[server],
            processing_times=[1.0],
            due_date=100,
        )

        def process_job(job, needs_materials=False):
            with server.request(job=job) as req:
                yield req
                if needs_materials:
                    yield from coordinator.ensure(job, server, 0)
                yield env.process(server.process_job(job, job.processing_times[0]))
            completion_order.append(job.family)

        def start_job2():
            yield env.timeout(0.1)  # Arrive after job1
            yield from process_job(job2, needs_materials=False)

        env.process(process_job(job1, needs_materials=True))
        env.process(start_job2())
        env.run()

        # Job1 should complete first due to FIFO blocking
        assert completion_order == ["A", "B"]

    def test_warehouse_blocking_on_empty_inventory(self) -> None:
        """Jobs should block when warehouse inventory is depleted."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=1,
            n_agvs=1,
            products=["steel"],
            initial_inventory={"steel": 0},  # Empty!
            pick_time=1.0,
            travel_time=2.0,
        )

        server = servers[0]
        events = []

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[1.0],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        def process_job():
            with server.request(job=job) as req:
                yield req
                events.append(("started", env.now))
                yield from coordinator.ensure(job, server, 0)
                events.append(("materials_ready", env.now))
                yield env.process(server.process_job(job, job.processing_times[0]))
            events.append(("completed", env.now))

        def restock():
            yield env.timeout(5.0)
            yield from warehouse.put_inventory("steel", 10)
            events.append(("restocked", env.now))

        env.process(process_job())
        env.process(restock())
        env.run()

        # Restock at t=5.5, then pick (1.0) + travel (2.0) + process (1.0)
        assert events[0] == ("started", 0)
        assert events[1][0] == "restocked"
        assert events[2] == ("materials_ready", pytest.approx(8.5))
        assert events[3] == ("completed", pytest.approx(9.5))

    def test_agv_metrics_accumulate(self) -> None:
        """AGV metrics should accumulate across deliveries."""
        env = Environment()
        shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
            env,
            n_servers=2,
            n_agvs=1,
            products=["A", "B"],
            initial_inventory={"A": 100, "B": 100},
            pick_time=0.5,
            travel_time=1.0,
        )

        agv = agvs[0]

        # Two jobs with materials
        job1 = ProductionJob(
            env=env,
            family="X",
            servers=[servers[0]],
            processing_times=[1.0],
            due_date=100,
            material_requirements={0: {"A": 2}},
        )
        job2 = ProductionJob(
            env=env,
            family="Y",
            servers=[servers[1]],
            processing_times=[1.0],
            due_date=100,
            material_requirements={0: {"B": 3}},
        )

        def process(job, server):
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)
                yield env.process(server.process_job(job, job.processing_times[0]))

        env.process(process(job1, servers[0]))
        env.process(process(job2, servers[1]))
        env.run()

        assert agv.trip_count == 2
        assert agv.total_travel_time == pytest.approx(2.0)
