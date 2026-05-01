# Intralogistics Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new example scripts (`intralogistics_intermediate.py`, `intralogistics_advanced.py`) to the `examples/` folder, plus a prerequisite library enhancement (`plot_inventory()` on `DefaultIntralogisticsCollector`).

**Architecture:** Three independent deliverables: (1) a small library addition to `metrics.py` with its test, (2) intermediate example with its test, (3) advanced example with its test. Each produces a working commit. The examples are single-file scripts that construct simulatte intralogistics systems manually (no builder functions), run simulations, and print text + matplotlib plots.

**Tech Stack:** simulatte (SimPy-based), matplotlib, pytest, stdlib `random` for reproducible RNG in the advanced example.

**Spec:** `docs/superpowers/specs/2026-05-01-intralogistics-examples-design.md`

---

### Task 1: Add `plot_inventory()` to `DefaultIntralogisticsCollector`

**Files:**
- Modify: `src/simulatte/intralogistics/metrics.py`
- Modify: `tests/intralogistics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/intralogistics/test_metrics.py`:

```python
class TestDefaultCollectorPlotInventory:
    """DefaultIntralogisticsCollector.plot_inventory renders without error."""

    def test_plot_inventory_with_data(self, monkeypatch) -> None:
        import matplotlib.pyplot

        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

        c = DefaultIntralogisticsCollector()
        sku_a = SKU(id="A", weight=1.0, volume=0.1)
        sku_b = SKU(id="B", weight=2.0, volume=0.2)
        wh = MagicMock()
        wh.name = "WH-Test"
        c.inventory_ts[wh] = [
            (0.0, {sku_a: 100.0, sku_b: 50.0}),
            (10.0, {sku_a: 90.0, sku_b: 45.0}),
            (20.0, {sku_a: 80.0, sku_b: 40.0}),
        ]
        c.plot_inventory()

    def test_plot_inventory_empty_data(self, monkeypatch) -> None:
        import matplotlib.pyplot

        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

        c = DefaultIntralogisticsCollector()
        c.plot_inventory()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_metrics.py::TestDefaultCollectorPlotInventory -v`
Expected: FAIL with `AttributeError: 'DefaultIntralogisticsCollector' object has no attribute 'plot_inventory'`

- [ ] **Step 3: Implement `plot_inventory()`**

Add to `DefaultIntralogisticsCollector` class in `src/simulatte/intralogistics/metrics.py`, after the `plot_throughput` method:

```python
def plot_inventory(self) -> None:  # pragma: no cover
    import matplotlib.pyplot as plt

    if not self.inventory_ts:
        return
    for warehouse, snapshots in self.inventory_ts.items():
        if not snapshots:
            continue
        all_skus = {sku for _, inv in snapshots for sku in inv}
        for sku in sorted(all_skus, key=lambda s: s.id):
            times = [t for t, inv in snapshots if sku in inv]
            levels = [inv[sku] for t, inv in snapshots if sku in inv]
            plt.step(times, levels, where="post", label=f"{warehouse.name} / {sku.id}")
    plt.xlabel("Time")
    plt.ylabel("Inventory Level")
    plt.title("Inventory Levels Over Time")
    plt.legend()
    plt.show()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_metrics.py::TestDefaultCollectorPlotInventory -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/metrics.py tests/intralogistics/test_metrics.py
git commit -m "feat(intralogistics): add plot_inventory() to DefaultIntralogisticsCollector"
```

---

### Task 2: Intermediate example — Manufacturing Plant Floor

**Files:**
- Create: `examples/intralogistics_intermediate.py`
- Modify: `tests/intralogistics/test_examples.py`

- [ ] **Step 1: Write the example script**

Create `examples/intralogistics_intermediate.py`:

```python
from __future__ import annotations

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
    ResourceBasedTrafficManager,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)

from simpy.events import ProcessGenerator


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
        pick_time_fn = lambda sku, qty: 15.0 + qty * 5.0
        put_time_fn = lambda sku, qty: 10.0 + qty * 3.0

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
            max_speed=1.5, acceleration=0.8, deceleration=1.0,
        )
        agv_type = AGVType(
            name="standard",
            speed_profile=speed_profile,
            battery_capacity=200.0,
            weight_capacity=100.0,
            volume_capacity=1.0,
            load_time_fn=lambda: 10.0,
            unload_time_fn=lambda: 8.0,
        )
        starting_nodes = [rm_out, c2, p]
        agvs = [
            AGV(env=env, agv_type=agv_type, agv_id=f"AGV-{i+1}", initial_node=node)
            for i, node in enumerate(starting_nodes)
        ]

        # --- Parking ---
        parking = ParkingArea(env=env, name="Parking", node=p, capacity=3)

        # --- Traffic ---
        traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

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
            traffic_manager=traffic,
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
            avg_fulfillment = (
                sum(o.delivered_at - o.created_at for o in completed_orders if o.delivered_at is not None)
                / len(completed_orders)
            )

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
```

- [ ] **Step 2: Run the example to verify it works**

Run: `uv run python examples/intralogistics_intermediate.py`
Expected: Text output with header "Manufacturing Plant Floor", order table, fleet report, warehouse inventory. Two matplotlib plot windows open. Close them to finish. Verify all 8 orders show COMPLETED status. If any show FAILED, debug the layout/capacity.

- [ ] **Step 3: Write the test**

Add to `tests/intralogistics/test_examples.py`:

```python
def test_intralogistics_intermediate_example_runs(monkeypatch, capsys) -> None:
    import matplotlib.pyplot

    monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

    example = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_intermediate.py"
    runpy.run_path(str(example), run_name="__main__")

    captured = capsys.readouterr()
    assert "Manufacturing Plant Floor" in captured.out
    assert "10 nodes" in captured.out
    assert "3 AGVs" in captured.out
    assert "8 submitted" in captured.out
    assert "0 failed" in captured.out
    assert "Fleet report:" in captured.out
    assert "Warehouse inventory:" in captured.out
    assert "Steel Sheets" in captured.out
    assert "Plastic Pellets" in captured.out
    assert "Electronics" in captured.out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_examples.py::test_intralogistics_intermediate_example_runs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add examples/intralogistics_intermediate.py tests/intralogistics/test_examples.py
git commit -m "feat(examples): add intermediate intralogistics example"
```

---

### Task 3: Advanced example — Multi-Warehouse Distribution Hub

**Files:**
- Create: `examples/intralogistics_advanced.py`
- Modify: `tests/intralogistics/test_examples.py`

- [ ] **Step 1: Write the example script**

Create `examples/intralogistics_advanced.py`:

```python
from __future__ import annotations

import random

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
    ResourceBasedTrafficManager,
    ReturnToOrigin,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)

from simpy.events import ProcessGenerator


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
) -> ProcessGenerator:
    while True:
        yield env.timeout(rng.uniform(300, 600))
        sku = rng.choice(skus)
        max_qty = max(1, int(weight_capacity // sku.weight))
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
            rcv_in, rcv_out, r1, r2, bulk_in,
            chrg, park,
            bulk_out, b1, b2, b3, dsp_in, dsp_out,
            b4, b5, b6,
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
        receiving = Warehouse(
            env=env,
            name="Receiving",
            input_bays=[rcv_in],
            output_bays=[rcv_out],
            n_slots=3,
            products=skus,
            initial_inventory={sku: 200 for sku in skus},
            pick_time_fn=lambda sku, qty: 20.0 + qty * 3.0,
            put_time_fn=lambda sku, qty: 10.0 + qty * 2.0,
        )

        bulk_storage = Warehouse(
            env=env,
            name="Bulk Storage",
            input_bays=[bulk_in],
            output_bays=[bulk_out],
            n_slots=4,
            products=skus,
            initial_inventory={sku: 30 for sku in skus},
            pick_time_fn=lambda sku, qty: 25.0 + qty * 4.0,
            put_time_fn=lambda sku, qty: 15.0 + qty * 3.0,
        )

        dispatch = Warehouse(
            env=env,
            name="Dispatch",
            input_bays=[dsp_in],
            output_bays=[dsp_out],
            n_slots=3,
            products=skus,
            initial_inventory={sku: 0 for sku in skus},
            pick_time_fn=lambda sku, qty: 15.0 + qty * 2.0,
            put_time_fn=lambda sku, qty: 10.0 + qty * 2.0,
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
            depletion_fn=lambda distance, load_weight, speed: distance * 0.05 * (1.0 + load_weight / 200),
            low_battery_threshold=0.2,
            critical_battery_threshold=0.05,
            load_time_fn=lambda: 12.0,
            unload_time_fn=lambda: 10.0,
        )
        starting_nodes = [park, bulk_out, b1, r1, b3]
        agvs = [
            AGV(env=env, agv_type=agv_type, agv_id=f"AGV-{i+1}", initial_node=node)
            for i, node in enumerate(starting_nodes)
        ]

        # --- Parking & Charging ---
        parking_area = ParkingArea(env=env, name="Parking", node=park, capacity=3)
        charging_station = ChargingStation(env=env, name="Charger", node=chrg, n_slots=2)

        # --- Traffic ---
        traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

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
            traffic_manager=traffic,
            dispatch_strategy=NearestIdleStrategy(),
            repositioning_policy=NearestParkingPolicy(),
            load_recovery_strategy=ReturnToOrigin(),
            order_metrics_collector=order_metrics,
            time_series_collector=ts_collector,
        )

        # --- Replenishment policy ---
        thresholds = {
            pallet_a: 5, pallet_b: 8, pallet_c: 10, pallet_d: 5, pallet_e: 15,
        }
        reorder_quantities = {
            pallet_a: 10, pallet_b: 15, pallet_c: 20, pallet_d: 10, pallet_e: 25,
        }
        replenishment = ReorderPointPolicy(
            thresholds=thresholds, reorder_quantity=reorder_quantities,
        )
        coordinator.add_replenishment_policy(replenishment, bulk_storage)

        # Record initial inventory
        initial_inv = {
            wh: {sku: wh.get_inventory_level(sku) for sku in skus}
            for wh in [receiving, bulk_storage, dispatch]
        }

        # Track all orders (outbound + replenishment) via hook
        all_orders: list = []
        coordinator.on_order_submitted(lambda order: all_orders.append(order))

        # Start outbound order stream
        outbound_orders: list = []
        env.process(
            outbound_order_stream(
                env, coordinator, bulk_storage, dispatch, skus,
                outbound_orders, rng, agv_type.weight_capacity,
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
            avg_fulfillment = (
                sum(o.delivered_at - o.created_at for o in completed_outbound if o.delivered_at is not None)
                / len(completed_outbound)
            )

        # --- Text output ---
        print("Multi-Warehouse Distribution Hub — Advanced Example")
        print(f"Layout: {len(graph.nodes)} nodes, {len(arcs)} arcs")
        print(f"Fleet: {len(agvs)} AGVs")
        print(f"Simulation time: {env.now:.0f}s ({env.now / 60:.0f} min)")
        print()

        print("Shift summary:")
        print(f"  Total orders: {len(all_orders)} ({len(outbound_orders)} outbound, {len(replenishment_orders)} replenishment)")
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
```

- [ ] **Step 2: Run the example to verify it works**

Run: `uv run python examples/intralogistics_advanced.py`
Expected: Text output with header "Multi-Warehouse Distribution Hub", shift summary, warehouse inventory, fleet report, EMA metrics. Four matplotlib windows open. Close them to finish. Verify reasonable numbers: outbound orders > 40, replenishment orders > 0, failed orders low (ideally 0). If replenishment is not triggering, verify `ReorderPointPolicy` thresholds vs. initial inventory.

- [ ] **Step 3: Write the test**

Add to `tests/intralogistics/test_examples.py`:

```python
def test_intralogistics_advanced_example_runs(monkeypatch, capsys) -> None:
    import matplotlib.pyplot

    monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)

    example = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_advanced.py"
    runpy.run_path(str(example), run_name="__main__")

    captured = capsys.readouterr()
    assert "Multi-Warehouse Distribution Hub" in captured.out
    assert "16 nodes" in captured.out
    assert "5 AGVs" in captured.out
    assert "Shift summary:" in captured.out
    assert "Warehouse inventory" in captured.out
    assert "Fleet report:" in captured.out
    assert "EMA metrics:" in captured.out
    assert "Receiving:" in captured.out
    assert "Bulk Storage:" in captured.out
    assert "Dispatch:" in captured.out
    assert "replenishment" in captured.out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_examples.py::test_intralogistics_advanced_example_runs -v`
Expected: PASS

- [ ] **Step 5: Run the full example test suite**

Run: `uv run pytest tests/intralogistics/test_examples.py -v`
Expected: 3 passed (simple + intermediate + advanced)

- [ ] **Step 6: Commit**

```bash
git add examples/intralogistics_advanced.py tests/intralogistics/test_examples.py
git commit -m "feat(examples): add advanced intralogistics example"
```
