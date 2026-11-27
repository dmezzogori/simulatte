"""Run the five-node builder through the layout's deferred handle binding.

Run with: uv run --all-extras python examples/layout_intralogistics.py
intralogistics_simple.py builds the same system and never mentions the layout; this example reads env.layout first
and prepares it before the run.
"""

from __future__ import annotations

from simulatte import Environment
from simulatte.intralogistics import FleetKPIs, OrderStatus, build_simple_system


def main() -> None:
    with Environment(seed=17) as env:
        layout = env.layout
        fleet, agvs, origin, destination, graph_ref = build_simple_system(env)
        assert graph_ref == layout.graph()
        kpis = FleetKPIs(fleet).attach(env)
        sku = next(iter(origin.inventory))
        orders = []
        for quantity in (5, 8, 12, 6):
            order = fleet.create_order(sku=sku, quantity=quantity, origin=origin, destination=destination)
            fleet.submit(order)
            orders.append(order)

        # Constructors and submissions above use handles. Preparation resolves all
        # endpoints without advancing time or dispatching queued orders.
        layout.prepare()
        print("Layout-backed intralogistics example")
        print(f"Prepared at time: {env.now}")
        print(f"Layout nodes: {len(fleet.graph.nodes)}")
        print(f"AGVs: {', '.join(agv.id for agv in agvs)}")
        print(f"Destination: {destination.input_bays[0].id}")
        env.run(until=120)
        completed = sum(order.status is OrderStatus.COMPLETED for order in orders)
        print(f"Completed orders: {completed}/{len(orders)}")
        print(f"WH-A inventory: {origin.get_inventory_level(sku):.0f}")
        print(f"WH-B inventory: {destination.get_inventory_level(sku):.0f}")
        print(f"Fulfillment time: {kpis.scalars()['fleet/fulfillment_time']:.2f}")


if __name__ == "__main__":
    main()
