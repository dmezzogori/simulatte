from __future__ import annotations

from simulatte.environment import Environment
from simulatte.intralogistics import OrderStatus, SKU, build_simple_system


def fmt_time(value: float | None) -> str:
    return "-" if value is None else f"{value:6.1f}"


def main() -> None:
    with Environment() as env:
        coordinator, agvs, warehouse_a, warehouse_b, graph = build_simple_system(env)

        sku = SKU("A", weight=1.0, volume=0.1)
        quantities = [5, 8, 12, 6]

        initial_a = warehouse_a.get_inventory_level(sku)
        initial_b = warehouse_b.get_inventory_level(sku)

        orders = []
        for quantity in quantities:
            order = coordinator.create_order(
                sku=sku,
                quantity=quantity,
                origin=warehouse_a,
                destination=warehouse_b,
            )
            coordinator.submit(order)
            orders.append(order)

        env.run(until=120.0)

        completed = sum(order.status is OrderStatus.COMPLETED for order in orders)

        print("Simple intralogistics example")
        print(f"Layout nodes: {len(graph.nodes)}")
        print(f"AGVs: {len(agvs)}")
        print(f"Simulation time: {env.now:.1f}")
        print(f"Completed orders: {completed}/{len(orders)}")
        print()
        print(f"WH-A inventory: {initial_a:.0f} -> {warehouse_a.get_inventory_level(sku):.0f}")
        print(f"WH-B inventory: {initial_b:.0f} -> {warehouse_b.get_inventory_level(sku):.0f}")
        print()
        print("order qty status     dispatch   pick deliver agv")
        for idx, order in enumerate(orders, start=1):
            agv_id = order.assigned_agv.agv_id if order.assigned_agv is not None else "-"
            print(
                f"{idx:>5} {order.quantity:>3} {order.status.name:<10}"
                f" {fmt_time(order.dispatched_at)}"
                f" {fmt_time(order.picked_at)}"
                f" {fmt_time(order.delivered_at)} {agv_id}"
            )

        print()
        print(f"Fleet utilization: {coordinator.fleet_utilization:.1%}")
        for agv in coordinator.agv_report():
            print(
                f"{agv['agv_id']}: state={agv['state']}, battery={agv['battery_pct']:.1%}, node={agv['current_node']}"
            )


if __name__ == "__main__":
    main()
