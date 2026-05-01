# Intralogistics Examples Design

Two new examples for the `examples/` folder, targeting the intralogistics subpackage.
Each is a single self-contained script producing text output and matplotlib plots.
They serve both onboarding (progressive learning path from the existing simple example)
and documentation (embedded in the simulatte.dev website).

---

## Example 1: Intermediate — Manufacturing Plant Floor

**File:** `examples/intralogistics_intermediate.py`

### Scenario

A manufacturing plant with two warehouses (Raw Materials and Finished Goods) connected
by a production floor corridor. AGVs transport goods from Raw Materials to Finished
Goods. PROD_A and PROD_B are production-area nodes that add routing interest to the
graph but are not order endpoints — all orders flow Raw Materials → Finished Goods.

### Layout (10 nodes)

```
                    [P] Parking
                     |
[RM_IN]--[RM_OUT]--[C1]--[C2]--[C3]--[FG_IN]--[FG_OUT]
                     |           |
                  [PROD_A]----[PROD_B]
```

- RM_IN / RM_OUT: Raw Materials warehouse input/output bays.
- FG_IN / FG_OUT: Finished Goods warehouse input/output bays.
- C1, C2, C3: Central corridor nodes. All corridor arcs are bidirectional; traffic
  management with `node_capacity=1` creates congestion (two AGVs can't occupy the
  same node, so they wait or reroute).
- PROD_A, PROD_B: Production floor nodes branching off the corridor.
  Connected by a one-way arc PROD_A → PROD_B, creating a forward-only alternate
  route C1 → PROD_A → PROD_B → C3 that AGVs can use as a bypass when the main
  corridor is congested. These are not warehouse endpoints.
- P: Parking area for idle AGVs.

Node coordinates (meters, approximate):

| Node    | x   | y   |
|---------|-----|-----|
| RM_IN   | 0   | 0   |
| RM_OUT  | 20  | 0   |
| C1      | 40  | 0   |
| C2      | 60  | 0   |
| C3      | 80  | 0   |
| FG_IN   | 100 | 0   |
| FG_OUT  | 120 | 0   |
| PROD_A  | 40  | -25 |
| PROD_B  | 80  | -25 |
| P       | 40  | 25  |

### SKUs

| SKU                   | Weight (kg) | Volume (m3) |
|-----------------------|-------------|-------------|
| Steel Sheets          | 80.0        | 0.3         |
| Plastic Pellets       | 15.0        | 0.8         |
| Electronic Components | 2.0         | 0.5         |

These are chosen so that weight and volume capacities matter for dispatch decisions.

### Fleet

3 AGVs:
- AGVType with `TrapezoidalProfile`: max_speed=1.5 m/s, acceleration=0.8 m/s²,
  deceleration=1.0 m/s². No battery degradation functions (battery lifecycle is
  reserved for the advanced example).
- Battery capacity: 200 units (high enough that battery is not a concern in the
  intermediate scenario — we want to focus on traffic and dispatch).
- Weight capacity: 100 kg, volume capacity: 1.0 m3.
- Load time: 10s, unload time: 8s.
- Starting positions: AGV-1 at RM_OUT, AGV-2 at C2, AGV-3 at P (spread across
  different nodes to avoid placement deadlock with `node_capacity=1`).

### Traffic Management

`ResourceBasedTrafficManager` with `node_capacity=1` on corridor nodes (C1, C2, C3).
This forces AGVs to wait or reroute when the corridor is congested, demonstrating
traffic management clearly.

### Policies

- Dispatch: `NearestIdleStrategy`.
- Repositioning: `NearestParkingPolicy` — idle AGVs return to parking area P.

### Order Flow

Three batches of orders submitted at staggered times to show queuing dynamics:

| Batch | Time (min) | Orders | SKUs                                         |
|-------|------------|--------|----------------------------------------------|
| 1     | 0          | 3      | 1x Steel Sheets, 1x Plastic Pellets, 1x Electronics |
| 2     | 30         | 3      | 1x Steel Sheets, 2x Plastic Pellets          |
| 3     | 60         | 2      | 1x Electronics, 1x Steel Sheets               |

All orders: Raw Materials → Finished Goods. Quantities per order: 1-3 units.

This is implemented as a SimPy generator process:

```python
def order_batches(env, coordinator, raw_materials, finished_goods, skus):
    """Submit orders in staggered batches."""
    batches = [
        (0, [...]),    # batch 1
        (30, [...]),   # batch 2
        (60, [...]),   # batch 3
    ]
    for delay, batch_orders in batches:
        yield env.timeout(delay * 60)  # convert minutes to seconds
        for sku, quantity in batch_orders:
            order = coordinator.create_order(
                sku=sku, quantity=quantity,
                origin=raw_materials, destination=finished_goods,
            )
            coordinator.submit(order)
```

### Warehouse Configuration

- Raw Materials: 2 pick/put slots, initial inventory of 20 per SKU.
  Input bay: RM_IN, output bay: RM_OUT.
  Pick time: `lambda sku, qty: 15 + qty * 5` seconds (heavier items take longer
  implicitly through more units). Put time: `lambda sku, qty: 10 + qty * 3`.
- Finished Goods: 2 pick/put slots, initial inventory of 0.
  Input bay: FG_IN, output bay: FG_OUT.
  Same pick/put time functions.

### Simulation Duration

120 minutes (7200 seconds). This gives enough time for all three batches to complete
and AGVs to return to parking.

### Output

**Text:**
- Header with scenario description.
- Summary: total orders, completed, avg fulfillment time.
- Order table: index, SKU, quantity, status, dispatched_at, picked_at, delivered_at,
  assigned AGV.
- Fleet report: per-AGV utilization and current state.
- Warehouse inventory delta: RM start → end, FG start → end.

**Plots (2 figures):**
- `plot_fleet_utilization()` via `DefaultIntralogisticsCollector`.
- `plot_pending_orders()` via `DefaultIntralogisticsCollector`.

### Features Demonstrated (progressive from simple example)

| Concept              | Simple Example | Intermediate Example |
|----------------------|----------------|----------------------|
| Graph construction   | `build_simple_system()` | Manual `Node`/`Arc`/`LayoutGraph` |
| Traffic management   | None (free)    | `ResourceBasedTrafficManager` |
| Dispatch strategy    | Default        | Explicit `NearestIdleStrategy` |
| Parking              | None           | `ParkingArea` + `NearestParkingPolicy` |
| SKUs                 | 1              | 3 with different weights/volumes |
| Order pattern        | All at once    | Staggered batches |
| Output               | Text only      | Text + plots |

---

## Example 2: Advanced — Multi-Warehouse Distribution Hub

**File:** `examples/intralogistics_advanced.py`

### Scenario

A distribution hub with three warehouses — Receiving (inbound dock), Bulk Storage
(central hub), and Dispatch (outbound shipping). Goods flow inbound from Receiving to
Bulk Storage via replenishment, then outbound from Bulk Storage to Dispatch via
customer orders. The system runs continuously over an 8-hour shift, managing battery
life, automatic replenishment, and producing operational analytics.

### Layout (16 nodes)

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

- RCV_IN / RCV_OUT: Receiving warehouse bays.
- BULK_IN / BULK_OUT: Bulk Storage warehouse bays.
- DSP_IN / DSP_OUT: Dispatch warehouse bays.
- R1, R2: Upper corridor (Receiving → Bulk).
- B1–B6: Lower network (Bulk → Dispatch) with an alternate route through
  B4 → B5 → B6 → B3, giving the pathfinder a choice.
- CHRG: Charging station node (2 slots).
- PARK: Parking area (3 capacity).

Node coordinates (meters, approximate):

| Node     | x   | y   |
|----------|-----|-----|
| RCV_IN   | 0   | 30  |
| RCV_OUT  | 20  | 30  |
| R1       | 50  | 30  |
| R2       | 80  | 30  |
| BULK_IN  | 110 | 30  |
| CHRG     | 80  | 15  |
| PARK     | 100 | 15  |
| BULK_OUT | 20  | 0   |
| B1       | 50  | 0   |
| B2       | 80  | 0   |
| B3       | 110 | 0   |
| DSP_IN   | 140 | 0   |
| DSP_OUT  | 160 | 0   |
| B4       | 80  | -20 |
| B5       | 100 | -20 |
| B6       | 110 | -20 |

Arc directionality:
- Upper corridor (RCV_OUT ↔ R1 ↔ R2 ↔ BULK_IN): bidirectional.
- Lower main corridor (BULK_OUT ↔ B1 ↔ B2 ↔ B3 ↔ DSP_IN): bidirectional.
- Alternate route (B2 → B4, B4 → B5, B5 → B6, B6 → B3): one-way forward bypass.
- Branches to CHRG, PARK: bidirectional from R2 and CHRG respectively.
- Warehouse bays: bidirectional (IN ↔ OUT for adjacent pairs RCV and DSP).
- Vertical connector R2 ↔ B2 (bidirectional, links upper and lower corridors).
- Traffic management with `node_capacity=1` creates congestion on bidirectional arcs.

### SKUs

| SKU              | Weight (kg) | Volume (m3) |
|------------------|-------------|-------------|
| Pallet A (Heavy) | 120.0       | 0.5         |
| Pallet B (Medium)| 50.0        | 0.8         |
| Pallet C (Light) | 10.0        | 0.3         |
| Pallet D (Bulky) | 30.0        | 1.2         |
| Pallet E (Small) | 5.0         | 0.1         |

### Fleet

5 AGVs with `TrapezoidalProfile`:
- max_speed: 2.0 m/s, acceleration: 0.8 m/s², deceleration: 1.0 m/s².
- `battery_degradation_fn`: When battery < 30%, max speed scales linearly down to
  70% at 0% charge. `lambda level: 1.0 if level >= 0.3 else 0.7 + level`.
- `load_speed_factor_fn`: Max speed reduces linearly with load weight up to 150 kg.
  `lambda weight: max(0.5, 1.0 - weight / 300)`.
- Battery capacity: 100 units.
- Depletion function: `lambda distance, load_weight, speed: distance * 0.05 * (1.0 + load_weight / 200)`.
  This yields ~6 energy per 120m empty trip and ~9 per loaded trip with 100kg,
  so the battery lasts ~7 orders before charging.
- Low battery threshold: 20%, critical: 5%.
- Weight capacity: 150 kg, volume capacity: 1.5 m3.
- Load time: 12s, unload time: 10s.
- Starting positions: AGV-1 at PARK, AGV-2 at BULK_OUT, AGV-3 at B1, AGV-4 at R1,
  AGV-5 at B3 (spread to avoid placement deadlock with `node_capacity=1`).

### Traffic Management

`ResourceBasedTrafficManager` with `node_capacity=1`.

### Charging Station

`ChargingStation` at CHRG node with 2 slots.
Recharge function: default linear.
No battery swap (swap is not demonstrated).

### Policies

- Dispatch: `NearestIdleStrategy`.
- Repositioning: `NearestParkingPolicy`.
- Load recovery: `ReturnToOrigin`.
- Replenishment: `ReorderPointPolicy` on Bulk Storage, wired via
  `coordinator.add_replenishment_policy(policy, bulk_storage)` (event-driven, no
  interval — triggers after every pick from Bulk Storage).

Replenishment thresholds and reorder quantities:

| SKU     | Reorder Point | Reorder Quantity |
|---------|---------------|------------------|
| Pallet A| 5             | 10               |
| Pallet B| 8             | 15               |
| Pallet C| 10            | 20               |
| Pallet D| 5             | 10               |
| Pallet E| 15            | 25               |

### Warehouse Configuration

- **Receiving**: 3 pick/put slots, large initial inventory (200 per SKU —
  effectively unlimited supply for an 8-hour shift).
  Input bay: RCV_IN, output bay: RCV_OUT.
  Pick time: `lambda sku, qty: 20 + qty * 3`, put time: `lambda sku, qty: 10 + qty * 2`.

- **Bulk Storage**: 4 pick/put slots, moderate initial inventory (30 per SKU —
  enough to start operations but will need replenishment).
  Input bay: BULK_IN, output bay: BULK_OUT.
  Pick time: `lambda sku, qty: 25 + qty * 4`, put time: `lambda sku, qty: 15 + qty * 3`.

- **Dispatch**: 3 pick/put slots, initial inventory of 0.
  Input bay: DSP_IN, output bay: DSP_OUT.
  Pick time: `lambda sku, qty: 15 + qty * 2`, put time: `lambda sku, qty: 10 + qty * 2`.

### Order Flow

Two concurrent SimPy generator processes:

**1. Outbound orders** (Bulk Storage → Dispatch):

```python
def outbound_order_stream(env, coordinator, bulk_storage, dispatch, skus, rng):
    """Continuous outbound orders at random intervals."""
    while True:
        yield env.timeout(rng.uniform(300, 600))  # every 5-10 minutes
        sku = rng.choice(skus)
        max_qty = int(weight_capacity // sku.weight)
        quantity = rng.integers(1, min(4, max_qty + 1))
        due_date = env.now + rng.uniform(1800, 3600)  # 30-60 min from now
        order = coordinator.create_order(
            sku=sku, quantity=quantity,
            origin=bulk_storage, destination=dispatch,
            due_date=due_date,
        )
        coordinator.submit(order)
```

**2. Replenishment orders** (Receiving → Bulk Storage):
Triggered automatically by `ReorderPointPolicy` when Bulk Storage inventory drops
below thresholds. No separate process needed — the policy is event-driven.

### Simulation Duration

480 minutes (28,800 seconds) — one full 8-hour shift.

Expected dynamics:
- ~50-100 outbound orders over the shift.
- Multiple replenishment cycles as Bulk Storage depletes.
- 2-3 charging cycles per AGV (battery lasts ~2 hours under load).
- Traffic contention peaks during replenishment + outbound overlaps.

### Output

**Text:**
- Shift summary: total orders submitted, completed, failed, average fulfillment time.
- Per-warehouse inventory delta (start → end).
- Fleet report: per-AGV utilization, number of charging cycles (approximated from
  `AGVState.CHARGING` duration), current state.
- Replenishment summary: number of replenishment orders triggered, completed.
- EMA metrics: fulfillment time, dispatch delay, travel times (empty/loaded), late
  order rate.

**Plots (3 figures):**
1. `plot_fleet_utilization()` via `DefaultIntralogisticsCollector`.
2. `plot_throughput()` via `DefaultIntralogisticsCollector`.
3. `plot_pending_orders()` via `DefaultIntralogisticsCollector`.

Note: `plot_inventory()` does not exist on `DefaultIntralogisticsCollector`. The
collector does track `inventory_ts` data. Two options:
- **(Preferred) Add `plot_inventory()` to `DefaultIntralogisticsCollector`** as a
  prerequisite task before implementing the advanced example. This benefits the
  library, not just the example.
- Write custom matplotlib code in the example using the raw `inventory_ts` data.

### Features Demonstrated (progressive from intermediate example)

| Concept              | Intermediate Example     | Advanced Example              |
|----------------------|--------------------------|-------------------------------|
| Fleet size           | 3 AGVs                   | 5 AGVs                       |
| Battery              | High capacity, no concern | Realistic drain + charging   |
| Speed profile        | Basic trapezoidal        | With battery/load degradation |
| Warehouses           | 2                        | 3                             |
| Charging             | None                     | `ChargingStation` (2 slots)   |
| Replenishment        | None                     | `ReorderPointPolicy` (event-driven) |
| Load recovery        | Default                  | Explicit `ReturnToOrigin`     |
| Order metrics        | None                     | `EMAOrderMetrics`             |
| Time-series          | `DefaultIntralogisticsCollector` | Same, with more plots  |
| Order flow           | Batched                  | Continuous random arrivals    |
| Due dates            | None                     | Random due dates              |
| Pathfinding choice   | Linear                   | Alternate routes (B2→B4→B5→B6→B3) |
| SKUs                 | 3                        | 5                             |
| Simulation duration  | 2 hours                  | 8 hours (full shift)          |

---

## Naming Convention

The three examples form a progression:
- `intralogistics_simple.py` (existing, unchanged)
- `intralogistics_intermediate.py` (new)
- `intralogistics_advanced.py` (new)

## Prerequisites

Before implementing the advanced example, `DefaultIntralogisticsCollector` should gain
a `plot_inventory()` method. This is a small library enhancement (follows the pattern
of the existing plot methods) and should be implemented first.

## Realistic Timing Sanity Check

**Intermediate**: Corridor is ~100m end-to-end (RM_OUT to FG_IN). At 1.5 m/s, transit
takes ~67s. Add pick (~20s) + put (~13s) + load/unload (18s) ≈ 2 minutes per order.
With 3 AGVs and 8 total orders across 3 batches, all complete well within 120 minutes.
Traffic waits on the corridor add realistic delays.

**Advanced**: Bulk Storage to Dispatch is ~120m via main corridor. At 2.0 m/s (loaded,
degraded), transit takes ~80-100s. Add pick (~30s) + put (~14s) + load/unload (22s)
≈ 2.5-3 minutes per order. With 5 AGVs and ~80 orders over 8 hours, the system is
moderately loaded. Battery depletion of `distance * 0.05 * (1 + weight/200)` means an
AGV carrying 100kg over 120m uses ~9 energy units. With 100 capacity, the battery
lasts ~7 loaded orders before hitting the 20% low-battery threshold and seeking a
charger. Each AGV handles ~16 orders over the shift, yielding 2-3 charge cycles.
