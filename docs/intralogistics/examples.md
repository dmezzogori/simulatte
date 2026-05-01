# Intralogistics examples

Three runnable scripts in [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples) form a progressive learning path --- from a minimal setup to a full-shift warehouse operation.

| Example | Layout | AGVs | SKUs | Key features |
|---------|--------|------|------|--------------|
| [Simple](#simple) | 5 nodes (preset) | 2 | 1 | `build_simple_system`, text output |
| [Intermediate](#intermediate) | 10 nodes (manual) | 3 | 3 | Custom graph, dispatch strategy, parking, staggered orders, plots |
| [Advanced](#advanced) | 16 nodes (manual) | 5 | 5 | 3 warehouses, battery lifecycle, charging, replenishment, EMA metrics, 4 plots |

---

## Simple

**File:** [`examples/intralogistics_simple.py`](https://github.com/dmezzogori/simulatte/blob/main/examples/intralogistics_simple.py)

The minimal starting point. Uses `build_simple_system()` to create a 5-node linear graph with two warehouses and two AGVs. Submits four transfer orders and prints results.

```
[WH_A] -- [N1] -- [N2] -- [N3] -- [WH_B]
```

**What it demonstrates:**

- Using `build_simple_system()` for quick setup
- Creating orders with `coordinator.create_order()` and `coordinator.submit()`
- Reading results: order status, inventory levels, fleet utilization

**Run it:**

```bash
uv run python examples/intralogistics_simple.py
```

---

## Intermediate

**File:** [`examples/intralogistics_intermediate.py`](https://github.com/dmezzogori/simulatte/blob/main/examples/intralogistics_intermediate.py)

A manufacturing plant with Raw Materials and Finished Goods warehouses connected by a corridor with production floor branches. Builds everything manually --- no builder functions.

```
                    [P] Parking
                     |
[RM_IN]--[RM_OUT]--[C1]--[C2]--[C3]--[FG_IN]--[FG_OUT]
                     |                 |
                  [PROD_A]----------[PROD_B]
```

All corridor arcs are bidirectional. `PROD_A --> PROD_B` is one-way, providing a forward bypass when the main corridor is busy.

**What it demonstrates:**

- **Custom graph construction** --- manual `Node`, `Arc`, and `LayoutGraph` instead of a builder
- **Multiple SKUs** --- Steel Sheets (80 kg), Plastic Pellets (15 kg), Electronics (2 kg) with weight/volume capacity constraints
- **Dispatch strategy** --- `NearestIdleStrategy` selects the closest idle AGV for each order
- **Parking and repositioning** --- `ParkingArea` at node P with `NearestParkingPolicy` sending idle AGVs back to parking
- **Staggered order batches** --- three batches at t=0, t=30 min, and t=60 min to show queuing dynamics
- **Time-series plots** --- `DefaultIntralogisticsCollector` with `plot_fleet_utilization()` and `plot_pending_orders()`

**Key configuration:**

```python
# 3 AGVs with trapezoidal speed profile
speed_profile = TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=1.0)
agv_type = AGVType(
    name="standard",
    speed_profile=speed_profile,
    battery_capacity=200.0,
    weight_capacity=100.0,
    volume_capacity=3.0,
    ...
)

# 8 orders across 3 batches, all Raw Materials -> Finished Goods
batches = [
    (0,    [(steel, 1), (plastic, 2), (electronics, 3)]),
    (1800, [(steel, 1), (plastic, 3), (plastic, 2)]),
    (1800, [(electronics, 3), (steel, 1)]),
]
```

**Sample output:**

```
Manufacturing Plant Floor — Intermediate Example
Layout: 10 nodes, 10 arcs
Fleet: 3 AGVs
Simulation time: 7200s (120 min)
Orders: 8 submitted, 8 completed, 0 failed
Avg fulfillment time: 151.2s
```

**Run it:**

```bash
uv run python examples/intralogistics_intermediate.py
```

---

## Advanced

**File:** [`examples/intralogistics_advanced.py`](https://github.com/dmezzogori/simulatte/blob/main/examples/intralogistics_advanced.py)

A distribution hub with three warehouses --- Receiving (inbound), Bulk Storage (central), and Dispatch (outbound) --- running a full 8-hour shift. Goods flow inbound via automatic replenishment and outbound via continuous customer orders.

```
[RCV_IN]--[RCV_OUT]--[R1]--[R2]--[BULK_IN]
                             |
                       [CHRG]--[PARK]
                             |
          [BULK_OUT]--[B1]--[B2]--[B3]--[DSP_IN]--[DSP_OUT]
                             |
                           [B4]
                             |
                       [B5]--[B6]
```

Main corridors are bidirectional. The `B2 --> B4 --> B5 --> B6 --> B3` alternate route is one-way, giving the pathfinder a forward bypass option.

**What it demonstrates:**

- **3-warehouse topology** --- Receiving, Bulk Storage, and Dispatch with separate input/output bays
- **Battery lifecycle** --- `TrapezoidalProfile` with `battery_degradation_fn` (speed drops below 30% charge) and `load_speed_factor_fn` (heavier loads = slower)
- **Charging station** --- `ChargingStation` at CHRG with 2 slots; AGVs charge automatically when battery is low
- **Automatic replenishment** --- `ReorderPointPolicy` monitors Bulk Storage inventory and triggers transfers from Receiving when stock drops below thresholds
- **Round-robin dispatch** --- `RoundRobinStrategy` cycles through idle AGVs for balanced fleet utilization
- **Load recovery** --- `ReturnToOrigin` returns cargo to the origin warehouse if an AGV is interrupted
- **EMA metrics** --- `EMAOrderMetrics` tracks fulfillment time, dispatch delay, travel times (empty/loaded), and late order rate
- **4 time-series plots** --- fleet utilization, throughput, pending orders, and inventory levels over time

**Key configuration:**

```python
# 5 AGVs with battery degradation and load-dependent speed
speed_profile = TrapezoidalProfile(
    max_speed=2.0, acceleration=0.8, deceleration=1.0,
    battery_degradation_fn=lambda level: 1.0 if level >= 0.3 else 0.7 + level,
    load_speed_factor_fn=lambda weight: max(0.5, 1.0 - weight / 300),
)

# Automatic replenishment when Bulk Storage drops below thresholds
replenishment = ReorderPointPolicy(
    thresholds={pallet_a: 10, pallet_b: 10, ...},
    reorder_quantity={pallet_a: 1, pallet_b: 1, pallet_c: 5, ...},
)
coordinator.add_replenishment_policy(replenishment, bulk_storage)

# Continuous outbound orders at random intervals (5-10 min)
def outbound_order_stream(env, coordinator, ...):
    while True:
        yield env.timeout(rng.uniform(300, 600))
        ...
        coordinator.submit(order)
```

**Sample output:**

```
Multi-Warehouse Distribution Hub — Advanced Example
Layout: 16 nodes, 16 arcs
Fleet: 5 AGVs
Simulation time: 28800s (480 min)

Shift summary:
  Total orders: 62 (61 outbound, 1 replenishment)
  Completed: 62, Failed: 0
  Outbound:      61 completed, 0 failed
  Replenishment: 1 completed, 0 failed
  Avg outbound fulfillment time: 220.7s (3.7 min)
```

**Run it:**

```bash
uv run python examples/intralogistics_advanced.py
```

---

## Feature progression

| Concept | Simple | Intermediate | Advanced |
|---------|--------|--------------|---------|
| Graph construction | `build_simple_system()` | Manual `Node`/`Arc`/`LayoutGraph` | Manual, 16 nodes |
| Warehouses | 2 | 2 | 3 |
| SKUs | 1 | 3 (varied weight/volume) | 5 |
| Fleet size | 2 | 3 | 5 |
| Dispatch | Default | `NearestIdleStrategy` | `RoundRobinStrategy` |
| Parking | None | `ParkingArea` + `NearestParkingPolicy` | Same |
| Battery | Not a concern | Not a concern | Realistic drain + `ChargingStation` |
| Speed profile | Default | Basic `TrapezoidalProfile` | With battery/load degradation |
| Replenishment | None | None | `ReorderPointPolicy` (event-driven) |
| Load recovery | Default | Default | `ReturnToOrigin` |
| Order metrics | None | None | `EMAOrderMetrics` |
| Time-series | None | 2 plots | 4 plots |
| Order flow | All at once | Staggered batches | Continuous random arrivals |
| Due dates | None | None | Random due dates |
| Simulation duration | ~2 min | 2 hours | 8 hours (full shift) |
