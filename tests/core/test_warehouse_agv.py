"""Tests for WarehouseStore and AGVServer."""

from __future__ import annotations

import pytest

from simulatte.agv_server import AGVServer
from simulatte.environment import Environment
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.warehouse_store import WarehouseStore


class TestWarehouseStore:
    """Tests for WarehouseStore functionality."""

    def test_warehouse_creation(self) -> None:
        """WarehouseStore should initialize with inventory containers."""
        env = Environment()
        sf = ShopFloor(env=env)
        warehouse = WarehouseStore(
            env=env,
            n_bays=2,
            products=["steel", "bolts"],
            initial_inventory={"steel": 100, "bolts": 500},
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
            shopfloor=sf,
        )

        assert warehouse.capacity == 2
        assert warehouse in sf.servers
        assert warehouse.get_inventory_level("steel") == 100
        assert warehouse.get_inventory_level("bolts") == 500
        assert warehouse.products == ["steel", "bolts"]

    def test_warehouse_pick_success(self) -> None:
        """Pick should succeed when inventory is available."""
        env = Environment()
        warehouse = WarehouseStore(
            env=env,
            n_bays=1,
            products=["steel"],
            initial_inventory={"steel": 10},
            pick_time_fn=lambda: 2.0,
            put_time_fn=lambda: 1.0,
        )

        def do_pick():
            yield from warehouse.pick_inventory("steel", 5)

        env.process(do_pick())
        env.run()

        assert warehouse.get_inventory_level("steel") == 5
        assert warehouse.total_picks == 1
        assert env.now == pytest.approx(2.0)
        assert warehouse.worked_time == pytest.approx(2.0)

    def test_warehouse_pick_blocks_on_insufficient_inventory(self) -> None:
        """Pick should block until inventory is available."""
        env = Environment()
        warehouse = WarehouseStore(
            env=env,
            n_bays=2,
            products=["steel"],
            initial_inventory={"steel": 0},
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
        )
        completed = []

        def try_pick():
            yield from warehouse.pick_inventory("steel", 5)
            completed.append("pick")

        def add_inventory():
            yield env.timeout(3.0)  # Wait, then put
            yield from warehouse.put_inventory("steel", 10)
            completed.append("put")

        env.process(try_pick())
        env.process(add_inventory())
        env.run()

        # Put completes first (at t=3.5), then pick can proceed (completes at t=4.5)
        assert completed == ["put", "pick"]
        assert warehouse.get_inventory_level("steel") == 5
        assert env.now == pytest.approx(4.5)

    def test_warehouse_put(self) -> None:
        """Put should add inventory."""
        env = Environment()
        warehouse = WarehouseStore(
            env=env,
            n_bays=1,
            products=["bolts"],
            initial_inventory={"bolts": 0},
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
        )

        def do_put():
            yield from warehouse.put_inventory("bolts", 100)

        env.process(do_put())
        env.run()

        assert warehouse.get_inventory_level("bolts") == 100
        assert warehouse.total_puts == 1
        assert env.now == pytest.approx(0.5)

    def test_warehouse_unknown_product_raises(self) -> None:
        """Operations on unknown products should raise KeyError."""
        env = Environment()
        warehouse = WarehouseStore(
            env=env,
            n_bays=1,
            products=["steel"],
            pick_time_fn=lambda: 1.0,
            put_time_fn=lambda: 0.5,
        )

        with pytest.raises(KeyError, match="Unknown product"):
            warehouse.get_inventory_level("unknown")

    def test_warehouse_metrics(self) -> None:
        """WarehouseStore should track pick/put metrics."""
        env = Environment()
        warehouse = WarehouseStore(
            env=env,
            n_bays=2,
            products=["a"],
            initial_inventory={"a": 100},
            pick_time_fn=lambda: 2.0,
            put_time_fn=lambda: 1.0,
        )

        def ops():
            yield from warehouse.pick_inventory("a", 10)
            yield from warehouse.pick_inventory("a", 10)
            yield from warehouse.put_inventory("a", 5)

        env.process(ops())
        env.run()

        assert warehouse.total_picks == 2
        assert warehouse.total_puts == 1
        assert warehouse.average_pick_time == pytest.approx(2.0)
        assert warehouse.average_put_time == pytest.approx(1.0)


class TestAGVServer:
    """Tests for AGVServer functionality."""

    def test_agv_creation(self) -> None:
        """AGVServer should initialize with capacity=1."""
        env = Environment()
        sf = ShopFloor(env=env)
        agv = AGVServer(
            env=env,
            travel_time_fn=lambda o, d: 5.0,
            shopfloor=sf,
            agv_id="agv-1",
        )

        assert agv.capacity == 1
        assert agv.agv_id == "agv-1"
        assert agv in sf.servers
        assert agv.trip_count == 0
        assert agv.current_location is None

    def test_agv_travel(self) -> None:
        """AGV should travel between locations with correct timing."""
        env = Environment()
        sf = ShopFloor(env=env)
        warehouse = Server(env=env, capacity=1, shopfloor=sf)
        workstation = Server(env=env, capacity=1, shopfloor=sf)
        agv = AGVServer(
            env=env,
            travel_time_fn=lambda o, d: 3.0,
            shopfloor=sf,
        )

        def do_travel():
            yield from agv.travel(warehouse, workstation)

        env.process(do_travel())
        env.run()

        assert agv.trip_count == 1
        assert agv.total_travel_time == pytest.approx(3.0)
        assert agv.current_location is workstation
        assert env.now == pytest.approx(3.0)
        assert agv.worked_time == pytest.approx(3.0)

    def test_agv_travel_to(self) -> None:
        """AGV should travel from current location."""
        env = Environment()
        sf = ShopFloor(env=env)
        loc_a = Server(env=env, capacity=1, shopfloor=sf)
        loc_b = Server(env=env, capacity=1, shopfloor=sf)

        def travel_fn(o: Server, d: Server) -> float:
            return 2.0

        agv = AGVServer(env=env, travel_time_fn=travel_fn, shopfloor=sf)
        agv.set_location(loc_a)

        def do_travel():
            yield from agv.travel_to(loc_b)

        env.process(do_travel())
        env.run()

        assert agv.current_location is loc_b
        assert agv.trip_count == 1

    def test_agv_travel_to_without_location_raises(self) -> None:
        """travel_to should raise if no current location."""
        env = Environment()
        loc = Server(env=env, capacity=1)
        agv = AGVServer(env=env, travel_time_fn=lambda o, d: 1.0)

        with pytest.raises(ValueError, match="no current location"):
            # Try to start the generator to trigger the error
            gen = agv.travel_to(loc)
            next(gen)

    def test_agv_metrics(self) -> None:
        """AGVServer should track travel metrics."""
        env = Environment()
        loc_a = Server(env=env, capacity=1)
        loc_b = Server(env=env, capacity=1)
        loc_c = Server(env=env, capacity=1)

        agv = AGVServer(env=env, travel_time_fn=lambda o, d: 2.5)

        def do_trips():
            yield from agv.travel(loc_a, loc_b)
            yield from agv.travel(loc_b, loc_c)
            yield from agv.travel(loc_c, loc_a)

        env.process(do_trips())
        env.run()

        assert agv.trip_count == 3
        assert agv.total_travel_time == pytest.approx(7.5)
        assert agv.average_travel_time == pytest.approx(2.5)
        assert agv.utilization_rate == pytest.approx(1.0)

    def test_agv_set_location(self) -> None:
        """set_location should set location without travel."""
        env = Environment()
        loc = Server(env=env, capacity=1)
        agv = AGVServer(env=env, travel_time_fn=lambda o, d: 10.0)

        agv.set_location(loc)

        assert agv.current_location is loc
        assert agv.trip_count == 0
        assert agv.total_travel_time == 0
        assert env.now == 0

    def test_agv_default_id(self) -> None:
        """AGV should generate default ID from index."""
        env = Environment()
        sf = ShopFloor(env=env)
        agv = AGVServer(env=env, travel_time_fn=lambda o, d: 1.0, shopfloor=sf)

        # ID includes the server index
        assert "agv-" in agv.agv_id
