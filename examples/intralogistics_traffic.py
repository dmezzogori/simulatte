"""Traffic control on a two-way corridor with a passing place.

Two warehouses at the ends of a single-lane corridor exchange crates in both directions. Each warehouse sits on a
one-way loop, so a vehicle never meets another head-on at a bay. In the middle of the corridor a siding (``C - S - D``)
is the passing place, and its node ``S`` is the parking area::

                        S (parking, 3 slots)
                       / \\
    W loop -- J1 -- M1 -- C ----- D -- M2 -- J2 -- E loop

A ``ReservationController`` grants every move as a claim on nodes, lane segments and corridors, so two vehicles never
share a lane in opposite directions. ``FastestRouter`` ranks routes by travel time: the main lane ``C - D`` is faster
than the siding, whose speed limit is 1 m/s. ``TrafficKPIs`` counts the waits and deadlocks of the fleet.
"""

from __future__ import annotations

from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FastestRouter,
    FleetCoordinator,
    LayoutGraph,
    NearestIdleStrategy,
    NearestParkingPolicy,
    Node,
    OrderStatus,
    ParkingArea,
    ReservationController,
    SKU,
    TrafficKPIs,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.layout import Explicit


def order_stream(
    env: Environment, coordinator: FleetCoordinator, west: Warehouse, east: Warehouse, sku: SKU, orders: list
) -> ProcessGenerator:
    """Twelve orders, one every 30 s, alternating west to east and east to west."""
    for i in range(12):
        origin, destination = (west, east) if i % 2 == 0 else (east, west)
        order = coordinator.create_order(sku=sku, quantity=1, origin=origin, destination=destination)
        coordinator.submit(order)
        orders.append(order)
        yield env.timeout(30.0)


def main() -> None:
    with Environment() as env:
        # --- Layout: one-way loops at the warehouses, a two-way corridor with a siding in the middle ---
        w_north, west_bay, w_south, j1 = Node("W_N", 0, 5), Node("W", -5, 0), Node("W_S", 0, -5), Node("J1", 5, 0)
        m1, c, s, d, m2 = Node("M1", 20, 0), Node("C", 35, 0), Node("S", 45, 6), Node("D", 55, 0), Node("M2", 70, 0)
        j2, e_north, east_bay, e_south = Node("J2", 85, 0), Node("E_N", 90, 5), Node("E", 95, 0), Node("E_S", 90, -5)
        nodes = [w_north, west_bay, w_south, j1, m1, c, s, d, m2, j2, e_north, east_bay, e_south]
        arcs = [
            # one-way loops around the bays
            Arc(j1, w_north, bidirectional=False),
            Arc(w_north, west_bay, bidirectional=False),
            Arc(west_bay, w_south, bidirectional=False),
            Arc(w_south, j1, bidirectional=False),
            Arc(j2, e_south, bidirectional=False),
            Arc(e_south, east_bay, bidirectional=False),
            Arc(east_bay, e_north, bidirectional=False),
            Arc(e_north, j2, bidirectional=False),
            # the two-way corridor
            Arc(j1, m1),
            Arc(m1, c),
            Arc(c, d),
            Arc(d, m2),
            Arc(m2, j2),
            # the siding: the passing place, slower than the main lane
            Arc(c, s, speed_limit=1.0),
            Arc(s, d, speed_limit=1.0),
        ]
        graph = LayoutGraph(nodes, arcs)

        # The graph is the network of the environment's layout; everything below refers to it through handles.
        layout = env.layout
        layout.set_network(Explicit(graph))

        # --- Warehouses ---
        crate = SKU("crate", weight=20.0, volume=0.5)
        west = Warehouse(
            env=env,
            name="West",
            input_bays=[layout.node("W")],
            output_bays=[layout.node("W")],
            n_slots=1,
            products=[crate],
            initial_inventory={crate: 50},
            pick_time=20.0,
            put_time=15.0,
        )
        east = Warehouse(
            env=env,
            name="East",
            input_bays=[layout.node("E")],
            output_bays=[layout.node("E")],
            n_slots=1,
            products=[crate],
            initial_inventory={crate: 50},
            pick_time=20.0,
            put_time=15.0,
        )

        # --- Fleet: a vehicle length is required by the reservation controller ---
        tugger = AGVType(
            name="tugger",
            speed_profile=TrapezoidalProfile(
                max_speed=1.5,
                acceleration=0.5,
                deceleration=0.5,
                battery_degradation_fn=lambda level: 1.0,  # full speed at any battery level
            ),
            battery_capacity=1000.0,
            weight_capacity=100.0,
            volume_capacity=2.0,
            length=1.2,
            min_gap=0.3,
            load_time=5.0,
            unload_time=5.0,
        )
        agvs = [AGV(env=env, agv_type=tugger, agv_id=f"AGV-{i + 1}", initial_node=layout.node("S")) for i in range(3)]
        parking = ParkingArea(env=env, name="Siding", node=layout.node("S"), capacity=3)

        # --- Traffic control, routing and the coordinator ---
        traffic = ReservationController(env=env, graph=layout.graph())
        coordinator = FleetCoordinator(
            env=env,
            graph=layout.graph(),
            fleet=agvs,
            warehouses=[west, east],
            charging_stations=[],
            parking_areas=[parking],
            traffic_controller=traffic,
            router=FastestRouter(),
            dispatch_strategy=NearestIdleStrategy(),
            repositioning_policy=NearestParkingPolicy(),
            name="tuggers",
        )
        kpis = TrafficKPIs(coordinator).attach(env)

        orders: list = []
        env.process(order_stream(env, coordinator, west, east, crate, orders))
        env.run()  # ends when the last order is delivered and every AGV is parked

        # --- Output ---
        completed = sum(order.status is OrderStatus.COMPLETED for order in orders)
        values = kpis.scalars()

        def kpi(name: str) -> float:
            return values.get(f"tuggers/{name}", 0.0)

        print("Two-way corridor with a passing place")
        print(f"Layout: {len(graph.nodes)} nodes; fleet: {len(agvs)} AGVs")
        print(f"Completed orders: {completed}/{len(orders)} at t = {env.now:.1f} s")
        print()
        print("Traffic:")
        print(f"  Blocked time:   {kpi('blocked_time'):.1f} s over {kpi('waits'):.0f} waits")
        resolved, unresolved = kpi("deadlocks_resolved"), kpi("deadlocks_unresolved")
        print(f"  Deadlocks:      {resolved:.0f} resolved, {unresolved:.0f} unresolved")
        print(f"  Stuck waits:    {kpi('stuck_waits'):.0f}")
        print(f"  Reroutes:       {kpi('reroutes'):.0f}")
        print(f"  Make-way moves: {kpi('make_way_moves'):.0f}")
        print()
        for agv in coordinator.agv_report():
            print(f"{agv['agv_id']}: state={agv['state']}, node={agv['current_node']}")


if __name__ == "__main__":
    main()
