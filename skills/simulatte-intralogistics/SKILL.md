---
name: simulatte-intralogistics
description: >
  Use when building, configuring, debugging, or running intralogistics
  simulations with Simulatte. Trigger whenever code imports from
  `simulatte.intralogistics`, references intralogistics classes
  (FleetCoordinator, AGV, Warehouse, LayoutGraph, TransferOrder,
  ChargingStation, ParkingArea), or the user asks about warehouse simulation,
  AGV fleet management, material transport, or warehouse layout design in a
  Python project. Also trigger when the user mentions dispatch strategies,
  replenishment policies, AGV battery management, traffic management,
  pathfinding, or order fulfillment in an intralogistics context.
---

# Simulatte Intralogistics Guide

The `simulatte.intralogistics` subpackage models warehouse-to-warehouse
material transport via AGV fleets. It is a standalone subsystem — no
dependency on the production-planning side (ShopFloor, ProductionJob, etc.).

Read `references/api-reference.md` (in this skill's directory) for exact
constructor signatures, parameter types, and method listings.

For production-planning simulations (job-shop, release policies, dispatching),
use the `simulatte:dev` skill instead.

## Understanding the request

| They want to...                              | Start here                        |
|----------------------------------------------|-----------------------------------|
| Get a minimal warehouse + AGV simulation     | `build_simple_system` (quick)     |
| Design a custom warehouse layout             | Manual composition (layout first) |
| Evaluate dispatch or repositioning strategies| Manual composition + metrics      |
| Add battery management and charging          | Fleet section + charging station  |
| Set up automatic replenishment               | Replenishment policy section      |
| Analyze fleet performance                    | Metrics and plots section         |

## Quick setup

`build_simple_system()` creates a 5-node linear graph, 2 warehouses, and
a fleet with sensible defaults. Good for smoke tests and prototyping.

```python
from simulatte.environment import Environment
from simulatte.intralogistics import build_simple_system, OrderStatus

with Environment() as env:
    coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env)
    order = coordinator.create_order(sku=sku, quantity=5, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    env.run(until=120.0)
```

## Manual composition

For custom layouts, follow this sequence:

1. **Define nodes** with (x, y) coordinates in meters
2. **Define arcs** between nodes (bidirectional or one-way)
3. **Create `LayoutGraph`**
4. **Define SKUs** with weight and volume
5. **Create warehouses** with input/output bays, inventory, pick/put times
6. **Create AGV type and fleet** with speed profile and battery config
7. **Create facilities** (parking areas, charging stations)
8. **Create `FleetCoordinator`** with policies and metrics
9. **Wire replenishment** if needed
10. **Start order processes** and run

```python
from simulatte.environment import Environment
from simulatte.intralogistics import (
    Node, Arc, LayoutGraph, SKU, Warehouse,
    AGV, AGVType, TrapezoidalProfile,
    FleetCoordinator, ParkingArea, ChargingStation,
    NearestIdleStrategy, NearestParkingPolicy,
    DefaultIntralogisticsCollector, EMAOrderMetrics,
)
```

See `references/api-reference.md` for all constructor signatures.

## Layout design

### Nodes and arcs

Nodes have string IDs and (x, y) coordinates. Distances are Euclidean.

```python
wh_in  = Node(id="WH_IN",  x=0,  y=0)
wh_out = Node(id="WH_OUT", x=20, y=0)
c1     = Node(id="C1",     x=40, y=0)

arcs = [
    Arc(wh_in, wh_out, bidirectional=True),
    Arc(wh_out, c1, bidirectional=True),
]
graph = LayoutGraph([wh_in, wh_out, c1], arcs)
```

### Layout rules

**All corridors must allow return trips.** If AGVs travel from warehouse A
to warehouse B, they must be able to return. Use bidirectional arcs for
main corridors. One-way arcs work for bypass routes (alternate paths AGVs
can take in one direction).

**Spread AGV starting positions.** Each AGV must start at a different node
when using `ResourceBasedTrafficManager` with `node_capacity=1`, or the
second AGV's placement blocks forever.

**Warehouse bays are graph nodes.** Each warehouse has `input_bays` and
`output_bays` — lists of nodes. AGVs travel to an output bay to pick and
to an input bay to deliver.

### ResourceBasedTrafficManager

The intent-based traffic system (`check_path`) flags ANY shared future
node between two AGV paths as a conflict. On layouts where all paths
share corridor nodes, this effectively serializes all AGV missions to one
at a time. Concurrent multi-AGV operation requires layouts with true
parallel routes.

**When to use it:** Complex layouts with multiple independent corridors
where concurrent AGVs genuinely need conflict avoidance.

**When to skip it:** Simple or linear layouts. Omit `traffic_manager` from
`FleetCoordinator` to use the default `FreeTrafficManager`.

## Fleet configuration

### Speed profile

`TrapezoidalProfile` models acceleration, cruising, and deceleration.
Optional functions degrade speed based on battery level and load weight.

```python
speed = TrapezoidalProfile(
    max_speed=2.0,        # m/s
    acceleration=0.8,     # m/s²
    deceleration=1.0,     # m/s²
    battery_degradation_fn=lambda level: 1.0 if level >= 0.3 else 0.7 + level,
    load_speed_factor_fn=lambda weight: max(0.5, 1.0 - weight / 300),
)
```

### AGV type

Bundles speed profile, battery config, and capacity limits.

```python
agv_type = AGVType(
    name="heavy-duty",
    speed_profile=speed,
    battery_capacity=100.0,
    weight_capacity=150.0,
    volume_capacity=1.5,
    depletion_fn=lambda distance, load_weight, speed: distance * 0.02 * (1.0 + load_weight / 200),
    low_battery_threshold=0.2,
    critical_battery_threshold=0.05,
    load_time_fn=lambda: 12.0,
    unload_time_fn=lambda: 10.0,
)
```

### Battery depletion sanity check

**Before running:** verify that an AGV at the farthest delivery point can
reach the nearest charging station with critical-level battery.

```
max_distance_to_charger × depletion_rate < battery_capacity × critical_threshold
```

If this fails, AGVs will strand. Lower the depletion rate or add a closer
charging station.

## Capacity checks

### Order quantities must fit AGV capacity

When generating random orders, cap quantities by BOTH weight AND volume:

```python
max_by_weight = int(agv_type.weight_capacity // sku.weight)
max_by_volume = int(agv_type.volume_capacity // sku.volume)
max_qty = min(max_by_weight, max_by_volume)
quantity = rng.randint(1, min(3, max(1, max_qty)))
```

Forgetting volume is a common mistake — orders that exceed capacity will
fail after `max_dispatch_retries` with no error message.

### Replenishment quantities must fit AGV capacity

`ReorderPointPolicy` reorder quantities are delivered in a single trip.
Set them to what one AGV can carry:

```python
reorder_qty = min(desired_qty, int(weight_cap // sku.weight), int(vol_cap // sku.volume))
```

## Policies

### Dispatch strategies

| Strategy             | Behavior                                      |
|----------------------|-----------------------------------------------|
| `NearestIdleStrategy`| Closest idle AGV by graph distance. Tie-breaks by `agv_id`. |
| `RoundRobinStrategy` | Cycles through idle AGVs. Ensures balanced utilization. |

**Fleet balance trap:** `NearestIdleStrategy` + `NearestParkingPolicy`
with a single parking area causes all AGVs to reposition to the same node.
Tie-breaking by `agv_id` means one AGV handles most orders. Fix: use
`RoundRobinStrategy`, or add multiple parking areas, or use `StayInPlace`.

### Repositioning

| Policy               | Behavior                                      |
|----------------------|-----------------------------------------------|
| `NearestParkingPolicy`| Send to nearest parking area with capacity.  |
| `StayInPlace`         | AGV stays where it delivered.                 |

### Replenishment

`ReorderPointPolicy` monitors a warehouse's inventory after each pick.
When stock drops below a threshold, it creates a `TransferOrder` sourced
from the warehouse with the highest stock of that SKU.

```python
policy = ReorderPointPolicy(thresholds={sku: 10}, reorder_quantity={sku: 5})
coordinator.add_replenishment_policy(policy, monitored_warehouse)
```

Event-driven by default (checked after each pick from the monitored
warehouse). Pass `check_interval=60.0` for periodic polling instead.

### Load recovery

| Strategy         | Behavior                                          |
|------------------|---------------------------------------------------|
| `ReturnToOrigin` | Return cargo to origin warehouse on failure.      |
| `ResumeDelivery` | Attempt re-delivery from current location.        |

## Metrics and plots

### Order-level metrics

`EMAOrderMetrics` tracks exponential moving averages:

- `ema_fulfillment_time`: created → delivered
- `ema_dispatch_delay`: created → dispatched
- `ema_travel_time_empty`: dispatched → picked
- `ema_travel_time_loaded`: picked → delivered
- `ema_late_orders`: fraction of late deliveries

### Time-series plots

`DefaultIntralogisticsCollector` records time-series data and provides:

```python
ts.plot_fleet_utilization()  # fleet-wide utilization over time
ts.plot_throughput()         # cumulative completed orders
ts.plot_pending_orders()     # queue depth over time
ts.plot_inventory()          # per-SKU inventory levels per warehouse
```

Pass as `time_series_collector=ts` to `FleetCoordinator`.

To include initial inventory in the plot, seed the collector before running:

```python
for wh in warehouses:
    ts.inventory_ts[wh] = [(0.0, {sku: float(c.level) for sku, c in wh.inventory.items()})]
```

## Orders and due dates

Create orders with `coordinator.create_order()`. All keyword arguments
forward to `TransferOrder`:

```python
order = coordinator.create_order(
    sku=sku, quantity=3, origin=wh_a, destination=wh_b,
    due_date=env.now + 1800.0,  # 30 minutes from now
    priority=1.0,
)
coordinator.submit(order)
```

`due_date` does NOT affect dispatch priority or ordering — it is only
used by `EMAOrderMetrics` to track `ema_late_orders` (fraction of orders
delivered after their due date). Custom priority-based dispatch is not
built in; use `priority` for application-level sorting.

## Tracking and debugging

### Which AGV handled which order?

After the simulation, inspect `order.assigned_agv`:

```python
for order in completed_orders:
    print(f"Order {order.id}: AGV={order.assigned_agv.agv_id}")
```

Or use lifecycle hooks for real-time tracking:

```python
coordinator.on_order_dispatched(lambda order, agv: print(f"Dispatched: {order.id} -> {agv.agv_id}"))
coordinator.on_delivery_complete(lambda order, agv: print(f"Delivered: {order.id} by {agv.agv_id}"))
```

### Debugging failed orders

Orders fail silently — check `order.status` after the run:

```python
failed = [o for o in all_orders if o.status is OrderStatus.FAILED]
for o in failed:
    print(f"FAILED: sku={o.sku.id}, qty={o.quantity}, "
          f"weight={o.sku.weight * o.quantity}, vol={o.sku.volume * o.quantity}")
```

Common causes of silent failure:
1. **No AGV can carry it** — weight or volume exceeds all AGVs' capacity.
   Check: `any(agv.can_carry(order.sku, order.quantity) for agv in fleet)`
2. **AGV stranded** — battery depleted mid-trip, no reachable charger.
   Look for `ERROR | FleetCoordinator | AGV-X STRANDED` in log output.
3. **No path** — destination unreachable from AGV's position (one-way layout).
   Look for `ERROR | FleetCoordinator | No path from X to Y` in log output.

## Battery management

Three battery-related mechanisms exist in `FleetCoordinator`:

1. **Automatic post-mission charging** — After each completed delivery,
   if `agv.battery.is_low` (below `low_battery_threshold`), the
   coordinator automatically navigates the AGV to the nearest charger.
   This is always active when `charging_stations` is non-empty.

2. **`on_low_battery` constructor callback** — If provided, this callback
   is invoked INSTEAD of the automatic charging behavior. If the callback
   returns a generator, the coordinator yields from it and skips its own
   charging logic. Use this to implement custom charging strategies.

3. **`on_battery_low` lifecycle hook** — A notification-only hook fired
   when any AGV's battery drops to low level. It does NOT affect charging
   behavior. Use it for logging or metrics.

In short: (1) is the default behavior, (2) replaces it, (3) observes it.

## Common pitfalls

**Volume capacity ignored in quantity generation.**
`AGV.can_carry()` checks both weight and volume. If you only cap order
quantity by weight, orders where `sku.volume × quantity > volume_capacity`
will silently fail dispatch. Always check both dimensions.

**Reorder quantities exceed AGV capacity.**
`ReorderPointPolicy` creates a single `TransferOrder` with the full
reorder quantity. If `sku.weight × reorder_qty > weight_capacity`, no AGV
can carry it and the order fails permanently.

**Battery depletion causes stranding.**
The `FleetCoordinator` charges AGVs proactively after missions when
battery is low. But mid-trip, if the AGV can't afford the next arc AND
can't reach a charger, it strands. Verify depletion rates against worst-
case distances to the nearest charger.

**One-way corridors with no return path.**
AGVs need to return after delivery. If all corridors are one-way in the
same direction, AGVs get trapped. Use bidirectional arcs for main
corridors; reserve one-way for bypass routes.

**Multiple AGVs at one node with `node_capacity=1`.**
`ResourceBasedTrafficManager._initial_placement()` acquires a node
resource for each AGV. With `node_capacity=1`, placing two AGVs at the
same node deadlocks the simulation.

**`NearestIdleStrategy` + single parking = fleet collapse.**
All AGVs reposition to the same parking node. Tie-breaking by `agv_id`
means one AGV monopolizes dispatch. Use `RoundRobinStrategy` for balanced
utilization, or spread AGVs across multiple parking areas.

**`ResourceBasedTrafficManager` serializes on shared paths.**
The intent system's `check_path` rejects paths that share ANY future node
with another AGV's planned path. On layouts without true parallel routes,
only one AGV moves at a time. For simple layouts, omit `traffic_manager`
(defaults to `FreeTrafficManager`).

## Examples

Three progressive examples in `examples/`:

- `intralogistics_simple.py`: 5-node preset, 2 AGVs, text output
- `intralogistics_intermediate.py`: 10-node custom layout, 3 AGVs, 3 SKUs, staggered batches, plots
- `intralogistics_advanced.py`: 16-node hub, 5 AGVs, 5 SKUs, 3 warehouses, battery, charging, replenishment, EMA metrics, 4 plots
