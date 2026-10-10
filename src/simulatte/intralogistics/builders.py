from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, overload

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
    from simulatte.layout import GraphRef, Layout, NodeRef, PortRef


@overload
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
    prefix: str = "",
    layout: Layout,
    nodes: Mapping[str, NodeRef | PortRef] | None = None,
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, GraphRef]: ...


@overload
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
    prefix: str = "",
    layout: Layout | None = None,
    nodes: Mapping[str, NodeRef | PortRef] | None = None,
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, LayoutGraph | GraphRef]: ...


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
    prefix: str = "",
    layout: Layout | None = None,
    nodes: Mapping[str, NodeRef | PortRef] | None = None,
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, LayoutGraph | GraphRef]:
    """Create a complete intralogistics system with sensible defaults.

    Builds a 5-node linear graph::

        WH_A_OUT(0,0) -- N1(5,0) -- N2(10,0) -- N3(15,0) -- WH_B_IN(20,0)

    with bidirectional arcs, two warehouses (A at the left end, B at the right
    end), a charging station at N2, and *n_agvs* AGVs starting at N1.

    Entities are named ``f"{prefix}{default}"``: nodes as above, warehouses ``WH-A`` and ``WH-B``, charging
    station ``CS-CENTER``, parking area ``PA-N1`` and coordinator ``fleet``. AGVs are named ``f"{prefix}agv-{i}"``
    when a prefix is given; without one their ids are generated (``agv-<n>``). A prefix keeps several systems
    apart in one environment.

    ``layout`` defaults to ``env.layout``. With a Layout attached, all consumers
    receive handles and the fifth return value is a ``GraphRef``. Physical getters
    become available after ``layout.prepare()`` or environment activation. Without
    a prefix, layout AGVs have explicit names ``vehicle-0``, ``vehicle-1``, etc.

    If no network is declared, layout mode declares the same five-node line.
    Otherwise ``nodes`` is required: its exact role keys are ``WH_A_OUT``, ``N1``,
    ``N2``, ``N3``, ``WH_B_IN`` (unprefixed), with node/port refs from this layout.
    Declare a union network and pass distinct mappings to compose independent lines;
    the builder never replaces an existing network. Partial file network patches
    may override the builder defaults. The traffic manager remains free-flowing.

    Layout warehouses default to ``obstacle=False``. Plain bay nodes have no owner
    clearance exemption: use a port connector for an obstructing warehouse.

    Returns:
        ``(coordinator, agvs, warehouse_a, warehouse_b, graph)``
    """
    layout = env.layout if layout is None else layout
    if layout is not None and layout.env is not env:
        from simulatte.layout import LayoutError

        raise LayoutError("builder layout belongs to another environment")
    if layout is None and nodes is not None:
        from simulatte.layout import LayoutError

        raise LayoutError("nodes mapping requires a layout")

    # -- Default products --
    if products is None:
        products = [SKU("A", 1.0, 0.1), SKU("B", 2.0, 0.2)]

    # -- Graph --
    graph: LayoutGraph | GraphRef
    bays_a: list[Node] | list[NodeRef | PortRef]
    bays_b: list[Node] | list[NodeRef | PortRef]
    if layout is None:
        graph = _simple_graph(prefix)
        wh_a_out, n1, n2, _, wh_b_in = graph.nodes
        bays_a, bays_b = [wh_a_out], [wh_b_in]
    else:
        graph, endpoints = _layout_graph(layout, nodes, prefix)
        wh_a_out, n1, n2, _, wh_b_in = endpoints
        bays_a, bays_b = [wh_a_out], [wh_b_in]

    # -- Default inventories --
    if initial_inventory_a is None:
        initial_inventory_a = {sku: 100 for sku in products}
    if initial_inventory_b is None:
        initial_inventory_b = {}

    # -- Warehouses --
    warehouse_a = Warehouse(
        env=env,
        name=f"{prefix}WH-A",
        input_bays=bays_a,
        output_bays=bays_a.copy(),
        n_slots=max(2, n_agvs),
        products=products,
        initial_inventory=initial_inventory_a,
        pick_time=1.0,
        put_time=1.0,
    )

    warehouse_b = Warehouse(
        env=env,
        name=f"{prefix}WH-B",
        input_bays=bays_b,
        output_bays=bays_b.copy(),
        n_slots=max(2, n_agvs),
        products=products,
        initial_inventory=initial_inventory_b,
        pick_time=1.0,
        put_time=1.0,
    )

    if layout is not None:
        layout.place(warehouse_a, obstacle=False)
        layout.place(warehouse_b, obstacle=False)

    # -- Charging station at N2 (center) --
    charging_station = ChargingStation(
        env=env,
        name=f"{prefix}CS-CENTER",
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
        load_time=1.0,
        unload_time=1.0,
    )

    # -- Fleet --
    agvs: list[AGV] = [
        AGV(
            env=env,
            agv_type=agv_type,
            agv_id=f"{prefix}agv-{i}" if prefix else (f"vehicle-{i}" if layout is not None else None),
            initial_node=n1,
        )
        for i in range(n_agvs)
    ]

    # -- Parking area at N1 --
    parking_area = ParkingArea(
        env=env,
        name=f"{prefix}PA-N1",
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
        name=f"{prefix}fleet",
    )

    return coordinator, agvs, warehouse_a, warehouse_b, graph


_ROLES = ("WH_A_OUT", "N1", "N2", "N3", "WH_B_IN")


def _simple_graph(prefix: str) -> LayoutGraph:
    wh_a_out = Node(id=f"{prefix}WH_A_OUT", x=0.0, y=0.0)
    n1 = Node(id=f"{prefix}N1", x=5.0, y=0.0)
    n2 = Node(id=f"{prefix}N2", x=10.0, y=0.0)
    n3 = Node(id=f"{prefix}N3", x=15.0, y=0.0)
    wh_b_in = Node(id=f"{prefix}WH_B_IN", x=20.0, y=0.0)

    arcs = [
        Arc(source=wh_a_out, target=n1, bidirectional=True),
        Arc(source=n1, target=n2, bidirectional=True),
        Arc(source=n2, target=n3, bidirectional=True),
        Arc(source=n3, target=wh_b_in, bidirectional=True),
    ]
    return LayoutGraph([wh_a_out, n1, n2, n3, wh_b_in], arcs)


def _layout_graph(
    layout: Layout, nodes: Mapping[str, NodeRef | PortRef] | None, prefix: str
) -> tuple[GraphRef, list[NodeRef | PortRef]]:
    from simulatte.layout import Explicit, LayoutError, NodeRef, PortRef

    # Inspect declarations only: graph reads would freeze/generate the network.
    # A partial file patch inherits defaults, but cannot erase an existing code
    # network and trick the builder into replacing it.
    coordinator = layout._coordinator
    declared = any("network" in patch for _, patch in coordinator._code) or (
        coordinator.values.at(("network", "type"), explicit=True).state == "present"
    )
    if declared:
        if nodes is None:
            raise LayoutError("existing network requires a nodes mapping for WH_A_OUT, N1, N2, N3, WH_B_IN")
    elif nodes is not None:
        raise LayoutError("nodes mapping requires an already declared network")
    endpoints: list[NodeRef | PortRef]
    if nodes is not None:
        if set(nodes) != set(_ROLES):
            raise LayoutError("nodes must have exactly the role keys WH_A_OUT, N1, N2, N3, WH_B_IN")
        for ref in nodes.values():
            layout._accept_reference(ref, (NodeRef, PortRef))
        endpoints = [nodes[role] for role in _ROLES]
    else:
        layout.set_network(Explicit(_simple_graph(prefix)))
        endpoints = [layout.node(f"{prefix}{role}") for role in _ROLES]
    return layout.graph(), endpoints
