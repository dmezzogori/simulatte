from __future__ import annotations

import random

from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVState,
    AGVType,
    Arc,
    ChargingStation,
    DefaultIntralogisticsCollector,
    EMAOrderMetrics,
    FleetCoordinator,
    LayoutGraph,
    NearestIdleStrategy,
    NearestParkingPolicy,
    Node,
    OrderStatus,
    ParkingArea,
    ReorderPointPolicy,
    ReturnToOrigin,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)


def fmt_time(value: float | None) -> str:
    return "-" if value is None else f"{value:7.1f}"


def fmt_ema(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def outbound_order_stream(
    env: Environment,
    coordinator: FleetCoordinator,
    bulk_storage: Warehouse,
    dispatch: Warehouse,
    skus: list[SKU],
    orders: list,
    rng: random.Random,
    weight_capacity: float,
    volume_capacity: float,
) -> ProcessGenerator:
    while True:
        yield env.timeout(rng.uniform(300, 600))
        sku = rng.choice(skus)
        max_by_weight = max(1, int(weight_capacity // sku.weight))
        max_by_volume = max(1, int(volume_capacity // sku.volume))
        max_qty = min(max_by_weight, max_by_volume)
        quantity = rng.randint(1, min(3, max_qty))
        due_date = env.now + rng.uniform(1800, 3600)
        order = coordinator.create_order(
            sku=sku,
            quantity=quantity,
            origin=bulk_storage,
            destination=dispatch,
            due_date=due_date,
        )
        orders.append(order)
        coordinator.submit(order)


def main() -> None:
    rng = random.Random(42)

    with Environment() as env:
        # --- Nodes (16) ---
        rcv_in = Node(id="RCV_IN", x=0, y=30)
        rcv_out = Node(id="RCV_OUT", x=20, y=30)
        r1 = Node(id="R1", x=50, y=30)
        r2 = Node(id="R2", x=80, y=30)
        bulk_in = Node(id="BULK_IN", x=110, y=30)
        chrg = Node(id="CHRG", x=80, y=15)
        park = Node(id="PARK", x=100, y=15)
        bulk_out = Node(id="BULK_OUT", x=20, y=0)
        b1 = Node(id="B1", x=50, y=0)
        b2 = Node(id="B2", x=80, y=0)
        b3 = Node(id="B3", x=110, y=0)
        dsp_in = Node(id="DSP_IN", x=140, y=0)
        dsp_out = Node(id="DSP_OUT", x=160, y=0)
        b4 = Node(id="B4", x=80, y=-20)
        b5 = Node(id="B5", x=100, y=-20)
        b6 = Node(id="B6", x=110, y=-20)

        all_nodes = [
            rcv_in,
            rcv_out,
            r1,
            r2,
            bulk_in,
            chrg,
            park,
            bulk_out,
            b1,
            b2,
            b3,
            dsp_in,
            dsp_out,
            b4,
            b5,
            b6,
        ]

        # --- Arcs (16) ---
        arcs = [
            # Warehouse bays (bidirectional, adjacent pairs)
            Arc(rcv_in, rcv_out, bidirectional=True),
            Arc(dsp_in, dsp_out, bidirectional=True),
            # Upper corridor (bidirectional)
            Arc(rcv_out, r1, bidirectional=True),
            Arc(r1, r2, bidirectional=True),
            Arc(r2, bulk_in, bidirectional=True),
            # Lower corridor (bidirectional)
            Arc(bulk_out, b1, bidirectional=True),
            Arc(b1, b2, bidirectional=True),
            Arc(b2, b3, bidirectional=True),
            Arc(b3, dsp_in, bidirectional=True),
            # Vertical connector
            Arc(r2, b2, bidirectional=True),
            # Charging / parking branch
            Arc(r2, chrg, bidirectional=True),
            Arc(chrg, park, bidirectional=True),
            # Alternate lower route (one-way forward bypass)
            Arc(b2, b4, bidirectional=False),
            Arc(b4, b5, bidirectional=False),
            Arc(b5, b6, bidirectional=False),
            Arc(b6, b3, bidirectional=False),
        ]

        graph = LayoutGraph(all_nodes, arcs)

        # --- SKUs (5) ---
        pallet_a = SKU(id="Pallet-A-Heavy", weight=120.0, volume=0.5)
        pallet_b = SKU(id="Pallet-B-Medium", weight=50.0, volume=0.8)
        pallet_c = SKU(id="Pallet-C-Light", weight=10.0, volume=0.3)
        pallet_d = SKU(id="Pallet-D-Bulky", weight=30.0, volume=1.2)
        pallet_e = SKU(id="Pallet-E-Small", weight=5.0, volume=0.1)
        skus = [pallet_a, pallet_b, pallet_c, pallet_d, pallet_e]

        # --- Warehouses (3) ---
        def rcv_pick_time(sku: SKU, qty: int) -> float:
            return 20.0 + qty * 3.0

        def rcv_put_time(sku: SKU, qty: int) -> float:
            return 10.0 + qty * 2.0

        def bulk_pick_time(sku: SKU, qty: int) -> float:
            return 25.0 + qty * 4.0

        def bulk_put_time(sku: SKU, qty: int) -> float:
            return 15.0 + qty * 3.0

        def dsp_pick_time(sku: SKU, qty: int) -> float:
            return 15.0 + qty * 2.0

        def dsp_put_time(sku: SKU, qty: int) -> float:
            return 10.0 + qty * 2.0

        receiving = Warehouse(
            env=env,
            name="Receiving",
            input_bays=[rcv_in],
            output_bays=[rcv_out],
            n_slots=3,
            products=skus,
            initial_inventory={sku: 200 for sku in skus},
            pick_time_fn=rcv_pick_time,
            put_time_fn=rcv_put_time,
        )

        bulk_storage = Warehouse(
            env=env,
            name="Bulk Storage",
            input_bays=[bulk_in],
            output_bays=[bulk_out],
            n_slots=4,
            products=skus,
            initial_inventory={sku: 30 for sku in skus},
            pick_time_fn=bulk_pick_time,
            put_time_fn=bulk_put_time,
        )

        dispatch = Warehouse(
            env=env,
            name="Dispatch",
            input_bays=[dsp_in],
            output_bays=[dsp_out],
            n_slots=3,
            products=skus,
            initial_inventory={sku: 0 for sku in skus},
            pick_time_fn=dsp_pick_time,
            put_time_fn=dsp_put_time,
        )

        # --- Fleet (5 AGVs) ---
        speed_profile = TrapezoidalProfile(
            max_speed=2.0,
            acceleration=0.8,
            deceleration=1.0,
            battery_degradation_fn=lambda level: 1.0 if level >= 0.3 else 0.7 + level,
            load_speed_factor_fn=lambda weight: max(0.5, 1.0 - weight / 300),
        )
        agv_type = AGVType(
            name="heavy-duty",
            speed_profile=speed_profile,
            battery_capacity=100.0,
            weight_capacity=150.0,
            volume_capacity=1.5,
            depletion_fn=lambda distance, load_weight, speed: distance * 0.02 * (1.0 + load_weight / 200),
            low_battery_threshold=0.2,
            critical_battery_threshold=0.05,
            load_time_fn=lambda: 12.0,
            unload_time_fn=lambda: 10.0,
        )
        starting_nodes = [park, bulk_out, b1, r1, b3]
        agvs = [
            AGV(env=env, agv_type=agv_type, agv_id=f"AGV-{i + 1}", initial_node=node)
            for i, node in enumerate(starting_nodes)
        ]

        # --- Parking & Charging ---
        parking_area = ParkingArea(env=env, name="Parking", node=park, capacity=3)
        charging_station = ChargingStation(env=env, name="Charger", node=chrg, n_slots=2)

        # --- Metrics ---
        order_metrics = EMAOrderMetrics(alpha=0.05)
        ts_collector = DefaultIntralogisticsCollector()

        # --- Coordinator ---
        coordinator = FleetCoordinator(
            env=env,
            graph=graph,
            fleet=agvs,
            warehouses=[receiving, bulk_storage, dispatch],
            charging_stations=[charging_station],
            parking_areas=[parking_area],
            dispatch_strategy=NearestIdleStrategy(),
            repositioning_policy=NearestParkingPolicy(),
            load_recovery_strategy=ReturnToOrigin(),
            order_metrics_collector=order_metrics,
            time_series_collector=ts_collector,
        )

        # --- Replenishment policy ---
        thresholds = {
            pallet_a: 10,
            pallet_b: 10,
            pallet_c: 10,
            pallet_d: 10,
            pallet_e: 10,
        }
        reorder_quantities = {
            pallet_a: 1,
            pallet_b: 1,
            pallet_c: 5,
            pallet_d: 1,
            pallet_e: 10,
        }
        replenishment = ReorderPointPolicy(
            thresholds=thresholds,
            reorder_quantity=reorder_quantities,
        )
        coordinator.add_replenishment_policy(replenishment, bulk_storage)

        # Record initial inventory
        initial_inv = {
            wh: {sku: wh.get_inventory_level(sku) for sku in skus} for wh in [receiving, bulk_storage, dispatch]
        }

        # Track all orders (outbound + replenishment) via hook
        all_orders: list = []
        coordinator.on_order_submitted(lambda order: all_orders.append(order))

        # Start outbound order stream
        outbound_orders: list = []
        env.process(
            outbound_order_stream(
                env,
                coordinator,
                bulk_storage,
                dispatch,
                skus,
                outbound_orders,
                rng,
                agv_type.weight_capacity,
                agv_type.volume_capacity,
            )
        )

        # Run simulation (8-hour shift = 28800 seconds)
        env.run(until=28800.0)

        # --- Classify orders ---
        replenishment_orders = [o for o in all_orders if o not in outbound_orders]
        completed = [o for o in all_orders if o.status is OrderStatus.COMPLETED]
        failed = [o for o in all_orders if o.status is OrderStatus.FAILED]

        completed_outbound = [o for o in outbound_orders if o.status is OrderStatus.COMPLETED]
        avg_fulfillment = 0.0
        if completed_outbound:
            avg_fulfillment = sum(
                o.delivered_at - o.created_at for o in completed_outbound if o.delivered_at is not None
            ) / len(completed_outbound)

        # --- Text output ---
        print("Multi-Warehouse Distribution Hub — Advanced Example")
        print(f"Layout: {len(graph.nodes)} nodes, {len(arcs)} arcs")
        print(f"Fleet: {len(agvs)} AGVs")
        print(f"Simulation time: {env.now:.0f}s ({env.now / 60:.0f} min)")
        print()

        print("Shift summary:")
        n_out = len(outbound_orders)
        n_repl = len(replenishment_orders)
        print(f"  Total orders: {len(all_orders)} ({n_out} outbound, {n_repl} replenishment)")
        print(f"  Completed: {len(completed)}, Failed: {len(failed)}")
        print(f"  Avg outbound fulfillment time: {avg_fulfillment:.1f}s ({avg_fulfillment / 60:.1f} min)")
        print()

        print("Warehouse inventory (start -> end):")
        for wh in [receiving, bulk_storage, dispatch]:
            print(f"  {wh.name}:")
            for sku in skus:
                start = initial_inv[wh][sku]
                end = wh.get_inventory_level(sku)
                print(f"    {sku.id:<20} {start:>5.0f} -> {end:>5.0f}")
        print()

        print("Fleet report:")
        for info in coordinator.agv_report():
            agv_obj = next(a for a in agvs if a.agv_id == info["agv_id"])
            charging_pct = agv_obj.state_percentage(AGVState.CHARGING)
            print(
                f"  {info['agv_id']}: utilization={info['utilization']:.1%},"
                f" charging={charging_pct:.1%},"
                f" battery={info['battery_pct']:.0%},"
                f" state={info['state']}"
            )
        print(f"  Fleet utilization: {coordinator.fleet_utilization:.1%}")
        print()

        print("EMA metrics:")
        print(f"  Fulfillment time: {fmt_ema(order_metrics.ema_fulfillment_time)}s")
        print(f"  Dispatch delay:   {fmt_ema(order_metrics.ema_dispatch_delay)}s")
        print(f"  Travel (empty):   {fmt_ema(order_metrics.ema_travel_time_empty)}s")
        print(f"  Travel (loaded):  {fmt_ema(order_metrics.ema_travel_time_loaded)}s")
        print(f"  Late order rate:  {fmt_ema(order_metrics.ema_late_orders)}")

        # --- Plots ---
        ts_collector.plot_fleet_utilization()
        ts_collector.plot_throughput()
        ts_collector.plot_pending_orders()
        ts_collector.plot_inventory()


if __name__ == "__main__":
    main()
