from __future__ import annotations

from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    DefaultIntralogisticsCollector,
    FleetCoordinator,
    LayoutGraph,
    NearestIdleStrategy,
    NearestParkingPolicy,
    Node,
    OrderStatus,
    ParkingArea,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)


def fmt_time(value: float | None) -> str:
    return "-" if value is None else f"{value:7.1f}"


def order_batches(
    env: Environment,
    coordinator: FleetCoordinator,
    raw_materials: Warehouse,
    finished_goods: Warehouse,
    skus: list[SKU],
    orders: list,
) -> ProcessGenerator:
    steel, plastic, electronics = skus
    batches: list[tuple[float, list[tuple[SKU, int]]]] = [
        (0, [(steel, 1), (plastic, 2), (electronics, 3)]),
        (1800, [(steel, 1), (plastic, 3), (plastic, 2)]),
        (1800, [(electronics, 3), (steel, 1)]),
    ]
    for delay, batch in batches:
        if delay > 0:
            yield env.timeout(delay)
        for sku, quantity in batch:
            order = coordinator.create_order(
                sku=sku,
                quantity=quantity,
                origin=raw_materials,
                destination=finished_goods,
            )
            orders.append(order)
            coordinator.submit(order)


def main() -> None:
    with Environment() as env:
        # --- Nodes ---
        rm_in = Node(id="RM_IN", x=0, y=0)
        rm_out = Node(id="RM_OUT", x=20, y=0)
        c1 = Node(id="C1", x=40, y=0)
        c2 = Node(id="C2", x=60, y=0)
        c3 = Node(id="C3", x=80, y=0)
        fg_in = Node(id="FG_IN", x=100, y=0)
        fg_out = Node(id="FG_OUT", x=120, y=0)
        prod_a = Node(id="PROD_A", x=40, y=-25)
        prod_b = Node(id="PROD_B", x=80, y=-25)
        p = Node(id="P", x=40, y=25)

        all_nodes = [rm_in, rm_out, c1, c2, c3, fg_in, fg_out, prod_a, prod_b, p]

        # --- Arcs ---
        arcs = [
            Arc(rm_in, rm_out, bidirectional=True),
            Arc(fg_in, fg_out, bidirectional=True),
            Arc(rm_out, c1, bidirectional=True),
            Arc(c1, c2, bidirectional=True),
            Arc(c2, c3, bidirectional=True),
            Arc(c3, fg_in, bidirectional=True),
            Arc(c1, p, bidirectional=True),
            Arc(c1, prod_a, bidirectional=True),
            Arc(c3, prod_b, bidirectional=True),
            Arc(prod_a, prod_b, bidirectional=False),
        ]

        graph = LayoutGraph(all_nodes, arcs)

        # --- SKUs ---
        steel = SKU(id="Steel Sheets", weight=80.0, volume=0.3)
        plastic = SKU(id="Plastic Pellets", weight=15.0, volume=0.8)
        electronics = SKU(id="Electronics", weight=2.0, volume=0.5)
        skus = [steel, plastic, electronics]

        # --- Warehouses ---
        def pick_time_fn(sku: SKU, qty: int) -> float:
            return 15.0 + qty * 5.0

        def put_time_fn(sku: SKU, qty: int) -> float:
            return 10.0 + qty * 3.0

        raw_materials = Warehouse(
            env=env,
            name="Raw Materials",
            input_bays=[rm_in],
            output_bays=[rm_out],
            n_slots=2,
            products=skus,
            initial_inventory={sku: 20 for sku in skus},
            pick_time_fn=pick_time_fn,
            put_time_fn=put_time_fn,
        )

        finished_goods = Warehouse(
            env=env,
            name="Finished Goods",
            input_bays=[fg_in],
            output_bays=[fg_out],
            n_slots=2,
            products=skus,
            initial_inventory={sku: 0 for sku in skus},
            pick_time_fn=pick_time_fn,
            put_time_fn=put_time_fn,
        )

        # --- Fleet ---
        speed_profile = TrapezoidalProfile(
            max_speed=1.5,
            acceleration=0.8,
            deceleration=1.0,
        )
        agv_type = AGVType(
            name="standard",
            speed_profile=speed_profile,
            battery_capacity=200.0,
            weight_capacity=100.0,
            volume_capacity=3.0,
            depletion_fn=lambda distance, load_weight, speed: distance * 0.01,
            load_time_fn=lambda: 10.0,
            unload_time_fn=lambda: 8.0,
        )
        starting_nodes = [rm_out, c2, p]
        agvs = [
            AGV(env=env, agv_type=agv_type, agv_id=f"AGV-{i + 1}", initial_node=node)
            for i, node in enumerate(starting_nodes)
        ]

        # --- Parking ---
        parking = ParkingArea(env=env, name="Parking", node=p, capacity=3)

        # --- Metrics ---
        ts_collector = DefaultIntralogisticsCollector()

        # --- Coordinator ---
        coordinator = FleetCoordinator(
            env=env,
            graph=graph,
            fleet=agvs,
            warehouses=[raw_materials, finished_goods],
            charging_stations=[],
            parking_areas=[parking],
            dispatch_strategy=NearestIdleStrategy(),
            repositioning_policy=NearestParkingPolicy(),
            time_series_collector=ts_collector,
        )

        # Record initial inventory
        initial_rm = {sku: raw_materials.get_inventory_level(sku) for sku in skus}
        initial_fg = {sku: finished_goods.get_inventory_level(sku) for sku in skus}

        # Start order generation
        orders: list = []
        env.process(order_batches(env, coordinator, raw_materials, finished_goods, skus, orders))

        # Run simulation (120 minutes)
        env.run(until=7200.0)

        # --- Text output ---
        completed_orders = [o for o in orders if o.status is OrderStatus.COMPLETED]
        failed = sum(1 for o in orders if o.status is OrderStatus.FAILED)
        avg_fulfillment = 0.0
        if completed_orders:
            avg_fulfillment = sum(
                o.delivered_at - o.created_at for o in completed_orders if o.delivered_at is not None
            ) / len(completed_orders)

        print("Manufacturing Plant Floor — Intermediate Example")
        print(f"Layout: {len(graph.nodes)} nodes, {len(arcs)} arcs")
        print(f"Fleet: {len(agvs)} AGVs")
        print(f"Simulation time: {env.now:.0f}s ({env.now / 60:.0f} min)")
        print(f"Orders: {len(orders)} submitted, {len(completed_orders)} completed, {failed} failed")
        print(f"Avg fulfillment time: {avg_fulfillment:.1f}s")
        print()

        print("order sku                   qty status     dispatch    pick deliver agv")
        for idx, order in enumerate(orders, start=1):
            agv_id = order.assigned_agv.agv_id if order.assigned_agv is not None else "-"
            print(
                f"{idx:>5} {order.sku.id:<22} {order.quantity:>3} {order.status.name:<10}"
                f" {fmt_time(order.dispatched_at)}"
                f" {fmt_time(order.picked_at)}"
                f" {fmt_time(order.delivered_at)} {agv_id}"
            )

        print()
        print("Fleet report:")
        for info in coordinator.agv_report():
            print(
                f"  {info['agv_id']}: utilization={info['utilization']:.1%},"
                f" state={info['state']}, node={info['current_node']}"
            )

        print()
        print("Warehouse inventory:")
        for sku in skus:
            rm_now = raw_materials.get_inventory_level(sku)
            fg_now = finished_goods.get_inventory_level(sku)
            print(
                f"  {sku.id:<22} RM: {initial_rm[sku]:>3.0f} -> {rm_now:>3.0f}"
                f"  FG: {initial_fg[sku]:>3.0f} -> {fg_now:>3.0f}"
            )

        # --- Plots ---
        ts_collector.plot_fleet_utilization()
        ts_collector.plot_pending_orders()


if __name__ == "__main__":
    main()
