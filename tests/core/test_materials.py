"""Tests for MaterialCoordinator."""

from __future__ import annotations

import pytest

from simulatte.agv import AGV
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.materials import MaterialCoordinator
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.warehouse_store import WarehouseStore


def create_test_system(
    env: Environment,
    initial_inventory: dict[str, int] | None = None,
) -> tuple[ShopFloor, Server, WarehouseStore, AGV, MaterialCoordinator]:
    """Create a test system with warehouse, AGV, and coordinator."""
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    warehouse = WarehouseStore(
        env=env,
        n_bays=2,
        products=["steel", "bolts"],
        initial_inventory=initial_inventory or {"steel": 100, "bolts": 500},
        pick_time_fn=lambda: 1.0,
        put_time_fn=lambda: 0.5,
        shopfloor=sf,
    )
    agv = AGV(
        env=env,
        travel_time_fn=lambda o, d: 2.0,
        shopfloor=sf,
        agv_id="agv-1",
    )
    coordinator = MaterialCoordinator(
        env=env,
        warehouse=warehouse,
        agvs=[agv],
        shopfloor=sf,
    )
    return sf, server, warehouse, agv, coordinator


class TestMaterialCoordinator:
    """Tests for MaterialCoordinator functionality."""

    def test_coordinator_creation(self) -> None:
        """Coordinator should initialize with warehouse, AGVs, and shopfloor."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        assert coordinator.warehouse is warehouse
        assert coordinator.agvs == [agv]
        assert coordinator.shopfloor is sf
        assert coordinator.total_deliveries == 0

    def test_ensure_no_materials_returns_immediately(self) -> None:
        """ensure() should return immediately if no materials required."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=100,
            material_requirements={},  # No materials
        )

        def run_ensure():
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)

        env.process(run_ensure())
        env.run()

        assert env.now == pytest.approx(0)
        assert coordinator.total_deliveries == 0

    def test_ensure_delivers_materials(self) -> None:
        """ensure() should pick and transport materials."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        def run_ensure():
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)

        env.process(run_ensure())
        env.run()

        # Time = pick_time (1.0) + travel_time (2.0) = 3.0
        assert env.now == pytest.approx(3.0)
        assert warehouse.get_inventory_level("steel") == 95  # 100 - 5
        assert warehouse.total_picks == 1
        assert agv.trip_count == 1
        assert coordinator.total_deliveries == 1

    def test_ensure_multiple_materials(self) -> None:
        """ensure() should deliver multiple materials sequentially."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=100,
            material_requirements={0: {"steel": 5, "bolts": 10}},
        )

        def run_ensure():
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)

        env.process(run_ensure())
        env.run()

        # Two deliveries: (pick + travel) * 2 = (1 + 2) * 2 = 6.0
        assert env.now == pytest.approx(6.0)
        assert warehouse.get_inventory_level("steel") == 95
        assert warehouse.get_inventory_level("bolts") == 490
        assert warehouse.total_picks == 2
        assert agv.trip_count == 2
        assert coordinator.total_deliveries == 1  # One operation, multiple materials

    def test_fifo_blocking_holds_server(self) -> None:
        """Job should hold server while waiting for materials (FIFO blocking)."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        # Job 1 requires materials
        job1 = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[2],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        # Job 2 has no material requirements
        job2 = ProductionJob(
            env=env,
            family="B",
            servers=[server],
            processing_times=[1],
            due_date=100,
        )

        completion_order = []

        def process_job1():
            with server.request(job=job1) as req:
                yield req
                yield from coordinator.ensure(job1, server, 0)
                yield env.timeout(job1.processing_times[0])
                completion_order.append("job1")

        def process_job2():
            yield env.timeout(0.1)  # Arrive slightly later
            with server.request(job=job2) as req:
                yield req
                yield env.timeout(job2.processing_times[0])
                completion_order.append("job2")

        env.process(process_job1())
        env.process(process_job2())
        env.run()

        # Job1 blocks server during material delivery, job2 waits
        # Job1: material_time (3.0) + processing (2.0) = 5.0
        # Job2: waits for job1, then processing (1.0) = 6.0
        assert completion_order == ["job1", "job2"]
        assert env.now == pytest.approx(6.0)

    def test_ensure_blocks_on_insufficient_inventory(self) -> None:
        """ensure() should block until inventory is available."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(
            env,
            initial_inventory={"steel": 0, "bolts": 0},  # No inventory
        )

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        completed = []

        def process_job():
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)
                completed.append("delivered")

        def add_inventory():
            yield env.timeout(5.0)
            yield from warehouse.put_inventory("steel", 10)
            completed.append("restocked")

        env.process(process_job())
        env.process(add_inventory())
        env.run()

        # Restock at t=5.5 (put time), then pick and deliver
        # pick_time (1.0) + travel (2.0) = 8.5
        assert completed == ["restocked", "delivered"]
        assert warehouse.get_inventory_level("steel") == 5  # 10 - 5

    def test_coordinator_metrics(self) -> None:
        """Coordinator should track delivery metrics."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=100,
            material_requirements={0: {"steel": 5}},
        )

        def run_ensure():
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)

        env.process(run_ensure())
        env.run()

        assert coordinator.total_deliveries == 1
        assert coordinator.average_delivery_time == pytest.approx(3.0)

    def test_agv_selection_load_balances_across_agvs(self) -> None:
        """MaterialCoordinator should distribute work across available AGVs."""
        env = Environment()
        sf = ShopFloor(env=env)

        server_a = Server(env=env, capacity=1, shopfloor=sf)
        server_b = Server(env=env, capacity=1, shopfloor=sf)

        warehouse = WarehouseStore(
            env=env,
            n_bays=2,
            products=["steel"],
            initial_inventory={"steel": 100},
            pick_time_fn=lambda: 0.0,
            put_time_fn=lambda: 0.0,
            shopfloor=sf,
        )

        agv_1 = AGV(
            env=env,
            travel_time_fn=lambda o, d: 1.0,
            shopfloor=sf,
            agv_id="agv-1",
        )
        agv_2 = AGV(
            env=env,
            travel_time_fn=lambda o, d: 1.0,
            shopfloor=sf,
            agv_id="agv-2",
        )

        coordinator = MaterialCoordinator(
            env=env,
            warehouse=warehouse,
            agvs=[agv_1, agv_2],
            shopfloor=sf,
        )

        job1 = ProductionJob(
            env=env,
            family="A",
            servers=[server_a],
            processing_times=[0.0],
            due_date=100.0,
            material_requirements={0: {"steel": 1}},
        )
        job2 = ProductionJob(
            env=env,
            family="B",
            servers=[server_b],
            processing_times=[0.0],
            due_date=100.0,
            material_requirements={0: {"steel": 1}},
        )

        def run_ensure(job: ProductionJob, server: Server):
            with server.request(job=job) as req:
                yield req
                yield from coordinator.ensure(job, server, 0)

        env.process(run_ensure(job1, server_a))
        env.process(run_ensure(job2, server_b))
        env.run()

        assert agv_1.trip_count == 1
        assert agv_2.trip_count == 1

    def test_no_agvs_raises(self) -> None:
        """_select_agv should raise ValueError when no AGVs configured."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        warehouse = WarehouseStore(
            env=env,
            n_bays=1,
            products=["steel"],
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
            shopfloor=sf,
        )

        coordinator = MaterialCoordinator(
            env=env,
            warehouse=warehouse,
            agvs=[],  # No AGVs
            shopfloor=sf,
        )

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[1.0],
            due_date=100,
        )

        with pytest.raises(ValueError, match="no AGVs configured"):
            coordinator._select_agv(server, job)

    def test_average_delivery_time_no_deliveries(self) -> None:
        """average_delivery_time should return 0.0 when no deliveries made."""
        env = Environment()
        sf, server, warehouse, agv, coordinator = create_test_system(env)

        assert coordinator.average_delivery_time == 0.0

    def test_agv_selection_round_robin(self) -> None:
        """_select_agv should round-robin between AGVs with equal load."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        warehouse = WarehouseStore(
            env=env,
            n_bays=1,
            products=["steel"],
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
            shopfloor=sf,
        )

        agv1 = AGV(env=env, travel_time_fn=lambda o, d: 1.0, shopfloor=sf, agv_id="agv-1")
        agv2 = AGV(env=env, travel_time_fn=lambda o, d: 1.0, shopfloor=sf, agv_id="agv-2")

        coordinator = MaterialCoordinator(
            env=env,
            warehouse=warehouse,
            agvs=[agv1, agv2],
            shopfloor=sf,
        )

        job = ProductionJob(
            env=env,
            family="A",
            servers=[server],
            processing_times=[1.0],
            due_date=100,
        )

        # Select AGVs multiple times - should round-robin
        selected1 = coordinator._select_agv(server, job)
        selected2 = coordinator._select_agv(server, job)
        selected3 = coordinator._select_agv(server, job)

        assert selected1 is agv1
        assert selected2 is agv2
        assert selected3 is agv1
