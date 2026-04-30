from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.pathfinding import DijkstraPlanner
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeTrafficManager
from simulatte.intralogistics.warehouse import Warehouse

if TYPE_CHECKING:
    from simulatte.environment import Environment


def build_simple_system(
    env: Environment,
    *,
    n_agvs: int = 2,
    agv_max_speed: float = 2.0,
    agv_acceleration: float = 1.0,
    agv_battery_capacity: float = 100.0,
    agv_weight_capacity: float = 500.0,
    agv_volume_capacity: float = 10.0,
    products: list[SKU] | None = None,
    initial_inventory_a: dict[SKU, int] | None = None,
    initial_inventory_b: dict[SKU, int] | None = None,
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, LayoutGraph]:
    """Create a complete intralogistics system with sensible defaults.

    Builds a 5-node linear graph::

        WH_A_OUT(0,0) -- N1(5,0) -- N2(10,0) -- N3(15,0) -- WH_B_IN(20,0)

    with bidirectional arcs, two warehouses (A at the left end, B at the right
    end), a charging station at N2, and *n_agvs* AGVs starting at N1.

    Returns:
        ``(coordinator, agvs, warehouse_a, warehouse_b, graph)``
    """
    # -- Default products --
    if products is None:
        products = [SKU("A", 1.0, 0.1), SKU("B", 2.0, 0.2)]

    # -- Graph --
    wh_a_out = Node(id="WH_A_OUT", x=0.0, y=0.0)
    n1 = Node(id="N1", x=5.0, y=0.0)
    n2 = Node(id="N2", x=10.0, y=0.0)
    n3 = Node(id="N3", x=15.0, y=0.0)
    wh_b_in = Node(id="WH_B_IN", x=20.0, y=0.0)

    arcs = [
        Arc(source=wh_a_out, target=n1, bidirectional=True),
        Arc(source=n1, target=n2, bidirectional=True),
        Arc(source=n2, target=n3, bidirectional=True),
        Arc(source=n3, target=wh_b_in, bidirectional=True),
    ]
    graph = LayoutGraph([wh_a_out, n1, n2, n3, wh_b_in], arcs)

    # -- Default inventories --
    if initial_inventory_a is None:
        initial_inventory_a = {sku: 100 for sku in products}
    if initial_inventory_b is None:
        initial_inventory_b = {}

    # -- Warehouses --
    warehouse_a = Warehouse(
        env=env,
        name="WH-A",
        input_bays=[wh_a_out],
        output_bays=[wh_a_out],
        n_slots=max(2, n_agvs),
        products=products,
        initial_inventory=initial_inventory_a,
        pick_time_fn=lambda s, q: 1.0,
        put_time_fn=lambda s, q: 1.0,
    )

    warehouse_b = Warehouse(
        env=env,
        name="WH-B",
        input_bays=[wh_b_in],
        output_bays=[wh_b_in],
        n_slots=max(2, n_agvs),
        products=products,
        initial_inventory=initial_inventory_b,
        pick_time_fn=lambda s, q: 1.0,
        put_time_fn=lambda s, q: 1.0,
    )

    # -- Charging station at N2 (center) --
    charging_station = ChargingStation(
        env=env,
        name="CS-CENTER",
        node=n2,
        n_slots=max(1, n_agvs),
    )

    # -- Speed profile and AGV type --
    speed_profile = TrapezoidalProfile(
        max_speed=agv_max_speed,
        acceleration=agv_acceleration,
        deceleration=agv_acceleration,
    )

    agv_type = AGVType(
        name="standard",
        speed_profile=speed_profile,
        battery_capacity=agv_battery_capacity,
        weight_capacity=agv_weight_capacity,
        volume_capacity=agv_volume_capacity,
        load_time_fn=lambda: 1.0,
        unload_time_fn=lambda: 1.0,
    )

    # -- Fleet --
    agvs: list[AGV] = [AGV(env=env, agv_type=agv_type, agv_id=f"agv-{i}", initial_node=n1) for i in range(n_agvs)]

    # -- Parking area at N1 --
    parking_area = ParkingArea(
        env=env,
        name="PA-N1",
        node=n1,
        capacity=n_agvs,
    )

    # -- Coordinator --
    coordinator = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=agvs,
        warehouses=[warehouse_a, warehouse_b],
        charging_stations=[charging_station],
        parking_areas=[parking_area],
        traffic_manager=FreeTrafficManager(),
        path_planner=DijkstraPlanner(),
    )

    return coordinator, agvs, warehouse_a, warehouse_b, graph
