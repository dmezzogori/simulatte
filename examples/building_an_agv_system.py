"""Building an AGV system from scratch: layout, warehouses, fleet, and metrics.

Demonstrates how to assemble a complete intralogistics simulation using
simulatte's intralogistics modules step by step:

1. Define a layout graph (nodes + arcs)
2. Create warehouses with initial inventory
3. Configure an AGV fleet with a trapezoidal speed profile
4. Wire everything together with FleetCoordinator + dispatch strategy
5. Submit transfer orders and run the simulation
6. Read per-order metrics and fleet utilisation

Simplification note: a generous battery capacity (1000.0) is used and no
charging station is added, so the AGVs never need to recharge during the
run. This keeps the example focused on the core wiring steps; see the
advanced intralogistics example for battery lifecycle and charging.
"""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    DijkstraPlanner,
    FleetCoordinator,
    FreeTrafficManager,
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


def main() -> None:
    with Environment() as env:
        # ------------------------------------------------------------------ #
        # 1. Layout graph                                                     #
        # ------------------------------------------------------------------ #
        # Simple T-shaped layout:
        #
        #   STORE_OUT(0,0) -- C1(10,0) -- C2(20,0) -- LINE_IN(30,0)
        #                         |
        #                      PARK(10,10)
        #
        store_out = Node(id="STORE_OUT", x=0.0, y=0.0)
        c1 = Node(id="C1", x=10.0, y=0.0)
        c2 = Node(id="C2", x=20.0, y=0.0)
        line_in = Node(id="LINE_IN", x=30.0, y=0.0)
        park_node = Node(id="PARK", x=10.0, y=10.0)

        arcs = [
            Arc(source=store_out, target=c1, bidirectional=True),
            Arc(source=c1, target=c2, bidirectional=True),
            Arc(source=c2, target=line_in, bidirectional=True),
            Arc(source=c1, target=park_node, bidirectional=True),
        ]
        graph = LayoutGraph([store_out, c1, c2, line_in, park_node], arcs)

        # ------------------------------------------------------------------ #
        # 2. SKUs and warehouses                                              #
        # ------------------------------------------------------------------ #
        sku_a = SKU(id="ComponentA", weight=5.0, volume=0.2)
        sku_b = SKU(id="ComponentB", weight=10.0, volume=0.4)
        products = [sku_a, sku_b]

        storage = Warehouse(
            env=env,
            name="Storage",
            input_bays=[store_out],
            output_bays=[store_out],
            n_slots=3,
            products=products,
            initial_inventory={sku_a: 50, sku_b: 30},
            pick_time_fn=lambda sku, qty: 5.0 + qty * 2.0,
            put_time_fn=lambda sku, qty: 3.0 + qty * 1.0,
        )

        production_line = Warehouse(
            env=env,
            name="ProductionLine",
            input_bays=[line_in],
            output_bays=[line_in],
            n_slots=3,
            products=products,
            initial_inventory={},
            pick_time_fn=lambda sku, qty: 2.0,
            put_time_fn=lambda sku, qty: 2.0,
        )

        # ------------------------------------------------------------------ #
        # 3. AGV fleet                                                        #
        # ------------------------------------------------------------------ #
        speed_profile = TrapezoidalProfile(
            max_speed=1.5,
            acceleration=0.8,
            deceleration=0.8,
        )
        agv_type = AGVType(
            name="standard",
            speed_profile=speed_profile,
            battery_capacity=1000.0,
            weight_capacity=50.0,
            volume_capacity=2.0,
            load_time_fn=lambda: 5.0,
            unload_time_fn=lambda: 5.0,
        )
        # Two AGVs starting at C1
        agvs = [AGV(env=env, agv_type=agv_type, agv_id=f"agv-{i}", initial_node=c1) for i in range(2)]

        # ------------------------------------------------------------------ #
        # 4. Parking area and FleetCoordinator                               #
        # ------------------------------------------------------------------ #
        parking = ParkingArea(env=env, name="Parking", node=park_node, capacity=2)

        coordinator = FleetCoordinator(
            env=env,
            graph=graph,
            fleet=agvs,
            warehouses=[storage, production_line],
            charging_stations=[],
            parking_areas=[parking],
            traffic_manager=FreeTrafficManager(),
            path_planner=DijkstraPlanner(),
            dispatch_strategy=NearestIdleStrategy(),
            repositioning_policy=NearestParkingPolicy(),
        )

        # ------------------------------------------------------------------ #
        # 5. Submit transfer orders                                           #
        # ------------------------------------------------------------------ #
        orders = []
        transfer_requests = [
            (sku_a, 4),
            (sku_b, 2),
            (sku_a, 6),
            (sku_b, 3),
            (sku_a, 2),
        ]
        for sku, qty in transfer_requests:
            order = coordinator.create_order(
                sku=sku,
                quantity=qty,
                origin=storage,
                destination=production_line,
            )
            coordinator.submit(order)
            orders.append(order)

        # Record initial inventory
        inv_start = {
            storage: {s: storage.get_inventory_level(s) for s in products},
            production_line: {s: production_line.get_inventory_level(s) for s in products},
        }

        # ------------------------------------------------------------------ #
        # 6. Run and print metrics                                            #
        # ------------------------------------------------------------------ #
        env.run(until=300.0)

        completed = [o for o in orders if o.status is OrderStatus.COMPLETED]

        avg_fulfillment = 0.0
        if completed:
            avg_fulfillment = sum(o.delivered_at - o.created_at for o in completed) / len(completed)

        print("Building an AGV System — step-by-step example")
        print(f"Layout: {len(graph.nodes)} nodes, {len(arcs)} arcs")
        print(f"Fleet: {len(agvs)} AGVs")
        print(f"Orders: {len(orders)} submitted")
        print(f"Simulation time: {env.now:.1f}s")
        print()

        print(f"Completed orders: {len(completed)}/{len(orders)}")
        print(f"Avg fulfillment time: {avg_fulfillment:.1f}s")
        print()

        print("order sku          qty status      dispatch   pick deliver agv")
        for idx, order in enumerate(orders, start=1):
            agv_id = order.assigned_agv.agv_id if order.assigned_agv is not None else "-"
            print(
                f"{idx:>5} {order.sku.id:<14} {order.quantity:>3} {order.status.name:<11}"
                f" {fmt_time(order.dispatched_at)}"
                f" {fmt_time(order.picked_at)}"
                f" {fmt_time(order.delivered_at)} {agv_id}"
            )

        print()
        print("Inventory (start -> end):")
        for wh in [storage, production_line]:
            print(f"  {wh.name}:")
            for sku in products:
                start = inv_start[wh][sku]
                end = wh.get_inventory_level(sku)
                print(f"    {sku.id:<14} {start:>3.0f} -> {end:>3.0f}")

        print()
        print("Fleet report:")
        print(f"  Overall utilization: {coordinator.fleet_utilization:.1%}")
        for info in coordinator.agv_report():
            print(
                f"  {info['agv_id']}: state={info['state']},"
                f" battery={info['battery_pct']:.0%},"
                f" node={info['current_node']}"
            )


if __name__ == "__main__":
    main()
