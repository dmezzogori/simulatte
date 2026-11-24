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
  replenishment policies, AGV battery management, traffic control,
  deadlocks, routing, or order fulfillment in an intralogistics context.
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
| Model congestion, blocking and deadlocks     | Traffic control section           |
| Route by travel time or around congestion    | Routing section                   |

## Quick setup

`build_simple_system()` declares a 5-node linear network on `env.layout`, and
builds 2 warehouses and a fleet with sensible defaults on handles of it. Good for
smoke tests and prototyping. Its AGVs are named `vehicle-<i>`.

```python
from simulatte.environment import Environment
from simulatte.intralogistics import build_simple_system, OrderStatus

with Environment(seed=1) as env:
    coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env)
    order = coordinator.create_order(sku=sku, quantity=5, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    env.run(until=120.0)
```

`create_order` attaches the order at once (it has its `order-<n>` id immediately).
`submit` and `cancel` called before the first `env.run()` / `env.activate()` are
deferred: the order reports `OrderStatus.PENDING_ACTIVATION` until activation, when the
queue drains at time 0. Entity names (warehouses, charging stations, parking areas, AGV
ids, graph node ids) share one namespace per environment and must not look like
`<kind>-<n>` (for example `agv-0` is reserved for generated AGV ids).

## Manual composition

Every environment has a layout, `env.layout`. For custom layouts, follow this sequence:

1. **Define nodes** with (x, y) coordinates in meters
2. **Define arcs** between nodes (bidirectional or one-way)
3. **Create `LayoutGraph`** and declare it with `env.layout.set_network(Explicit(graph))` before activation
4. **Define SKUs** with weight and volume
5. **Create warehouses** with input/output bays (handles `layout.node("ID")`), inventory, pick/put times
6. **Create AGV type and fleet** with speed profile and battery config
7. **Create facilities** (parking areas, charging stations)
8. **Create `FleetCoordinator`** with `graph=layout.graph()`, policies and metrics
9. **Wire replenishment** if needed
10. **Start order processes** and run

Components take handles of the layout, never `Node` or `LayoutGraph` objects: a concrete one raises `LayoutError`.

```python
from simulatte.environment import Environment
from simulatte.intralogistics import (
    Node,
    Arc,
    LayoutGraph,
    SKU,
    Warehouse,
    AGV,
    AGVType,
    TrapezoidalProfile,
    FleetCoordinator,
    ParkingArea,
    ChargingStation,
    NearestIdleStrategy,
    NearestParkingPolicy,
    OrderEMACollector,
    FleetTimeSeries,
    FleetKPIs,
    ReservationController,  # optional: traffic control
    FastestRouter,  # optional: routing by travel time
    TrafficKPIs,
)
from simulatte.layout import Explicit
```

See `references/api-reference.md` for all constructor signatures.

## Layout design

### Nodes and arcs

Nodes have string IDs and (x, y) coordinates. An arc's travelled length is the
distance between its nodes unless you pass `length=` (never shorter than that);
`speed_limit=` caps the speed on it and `passing=True` lets opposite traffic pass
on it under traffic control.

```python
wh_in = Node(id="WH_IN", x=0, y=0)
wh_out = Node(id="WH_OUT", x=20, y=0)
c1 = Node(id="C1", x=40, y=0)

arcs = [
    Arc(wh_in, wh_out, bidirectional=True),
    Arc(wh_out, c1, bidirectional=True),
]
graph = LayoutGraph([wh_in, wh_out, c1], arcs)

layout = env.layout
layout.set_network(Explicit(graph))  # once, before activation; a second call before preparation replaces the first
# Components take handles: layout.graph(), layout.node("WH_IN")
```

`Lattice(...)` and `Lanes(...)` are the other two declarations (see the layout guide). An arc between two nodes at the
same position is rejected: model a lift or a transfer point with a small offset and an explicit `length`.

### Layout rules

**All corridors must allow return trips.** If AGVs travel from warehouse A
to warehouse B, they must be able to return. Use bidirectional arcs for
main corridors. One-way arcs work for bypass routes (alternate paths AGVs
can take in one direction).

**Spread AGV starting positions.** Under `ReservationController` the AGVs
placed on one node must fit its capacity (1 by default; a parking area or
charging station on the node gives it its capacity), or activation raises
`RuntimeError`.

**Warehouse bays are graph nodes.** Each warehouse has `input_bays` and
`output_bays` — sequences of node handles (`layout.node("ID")`). AGVs travel to
an output bay to pick and to an input bay to deliver.

**Physical fields exist after preparation.** `agv.current_node`, `fleet.graph`,
`controller.graph`, bays, `station.node` and `area.node` raise `LayoutNotReady`
until `env.layout.prepare()` or `env.activate()`. They hold canonical nodes: equal
to the ones you declared and different objects, so compare with `==`, not `is`.
Declaring the network after activation raises `LayoutError` (also through
`build_simple_system`, or a component given a handle when no network was declared).
On a network declared before activation, an AGV, warehouse, parking area, charger
and a free-flow fleet with handles may be added later; a fleet on a
`ReservationController` must be built before activation (`RuntimeError`).
`env.step()` needs `env.activate()` first.

### Traffic control

`FleetCoordinator(traffic_controller=...)` decides whether AGVs can block
each other:

- `FreeFlowController` (default): every move is granted at once; AGVs share
  nodes and lanes and never wait. Use it when traffic is not studied.
- `ReservationController(env=, graph=layout.graph(), node_capacity=1,
  derive_corridors=True, hold_back_dead_ends=True, policy=None,
  priority_fn=None)`: AGVs claim nodes, lane segments, zones and derived
  corridors all or nothing, wait when they cannot, and deadlocks are
  detected and resolved by `StandardTrafficPolicy` (reroute, step aside,
  give up). Every `AGVType` needs a `length` (and optionally `min_gap`), or
  `register_fleet` raises `ValueError`. Build it before the coordinator and
  pass the same instance to every fleet: an environment has one network.
  Node capacities and zones are declared with the network
  (`Explicit(graph, capacities={"N1": 2}, zones={"z": {"capacity": 1,
  "members": ["N1", ("N1", "N2")]}})`), not on the controller.

Design the layout for it: two vehicles cannot pass on a single lane, so give
them passing places (a siding, or a `passing=True` arc whose end nodes have
room for two), capacity above 1 on junctions and bays, parking areas off the
main lanes, and one-way loops around bays. `block_length` in the network
declaration splits long arcs into blocks (`LayoutGraph.subdivide(max_length)`
does the same to a graph before `Explicit`). `TrafficKPIs(coordinator).attach(env)` counts
blocked time, waits, deadlocks (resolved and unresolved), stuck waits,
reroutes and make-way moves; `traffic.deadlock` and `traffic.wait_stuck`
events say what happened. A deadlock no reaction resolves stands
(`unresolved`), and the run still ends. See `examples/intralogistics_traffic.py`.

### Routing

`FleetCoordinator(router=...)`: `ShortestDistanceRouter()` (default; `"astar"`
for A*), `FastestRouter()` (travel time with the AGV's limits, speed limits
and turning model) or `CongestionAwareRouter(base=FastestRouter(),
wait_weight=1.0, horizon=60.0)` (a heuristic that penalises legs blocked
now). Costs are comparable only within one router. Dispatch, bays, chargers
and parking are ranked by the router's cost, one route search per candidate,
which is slow on large layouts with `FastestRouter` and turning, or with
`CongestionAwareRouter`.

## Fleet configuration

### Speed profile

`TrapezoidalProfile` models acceleration, cruising, and deceleration.
Optional functions degrade speed based on battery level and load weight.
By default the battery factor equals the battery level, so speed falls in proportion to the charge; pass `lambda level: 1.0` for full speed at any level.

```python
speed = TrapezoidalProfile(
    max_speed=2.0,  # m/s
    acceleration=0.8,  # m/s²
    deceleration=1.0,  # m/s²
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
    load_time=12.0,  # number, distribution (Uniform(8, 16)) or callable; bound to <agv id>/load
    unload_time=10.0,
)
```

Time parameters are *managed* when they are numbers or distribution descriptions
(`AGVType.load_time` / `unload_time`, `Warehouse(pick_time=, put_time=)`,
`ChargingStation(recharge_time=)`): they draw from named streams of `env.seed` and the run
stays reproducible. A plain callable (for example `pick_time=lambda sku, qty: 5 + qty`) is
accepted but *opaque*: it is recorded in `env.opaque_sampler_owners` and makes the run
manifest incomplete. The 0.12 names `load_time_fn`, `unload_time_fn`, `pick_time_fn`,
`put_time_fn` and `ChargingStation(recharge_fn=)` are gone.

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
quantity = env.rng("orders").randint(1, min(3, max(1, max_qty)))  # a named, seeded stream
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
| `NearestIdleStrategy`| Idle AGV with the least router cost to an output bay of the origin. Tie-breaks by `agv_id`. |
| `RoundRobinStrategy` | Cycles through idle AGVs. Ensures balanced utilization. |

**Fleet balance trap:** `NearestIdleStrategy` + `NearestParkingPolicy`
with a single parking area causes all AGVs to reposition to the same node.
Tie-breaking by `agv_id` means one AGV handles most orders. Fix: use
`RoundRobinStrategy`, or add multiple parking areas, or use `StayInPlace`.

### Repositioning

| Policy               | Behavior                                      |
|----------------------|-----------------------------------------------|
| `NearestParkingPolicy`| Send to the parking area with capacity at the least router cost. |
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

Every `FleetCoordinator` attaches an `OrderEMACollector` as `coordinator.metrics`
(`None` with `default_metrics=False`). It tracks exponential moving averages, each
`None` until the first delivery:

- `ema_fulfillment_time`: created → delivered
- `ema_dispatch_delay`: created → dispatched
- `ema_travel_time_empty`: dispatched → picked
- `ema_travel_time_loaded`: picked → delivered
- `ema_late_orders`: fraction of late deliveries

For another smoothing factor, build the coordinator with `default_metrics=False` and attach
`OrderEMACollector(coordinator, alpha=0.05).attach(env)`. `FleetKPIs(coordinator).attach(env)`
adds window-aware KPIs (fulfillment time, dispatch delay, travel times, late fraction,
throughput, time-weighted utilization and pending orders), keyed `"<fleet id>/<kpi>"`
and merged into `env.fingerprint().kpis`; set the warm-up with `env.configure_kpis(warmup=...)`.

### Time-series plots

`FleetTimeSeries(coordinator).attach(env)` (attach before the run) records time-series
data from the event bus and provides:

```python
ts.plot_fleet_utilization()  # fleet-wide utilization over time
ts.plot_throughput()  # cumulative completed orders
ts.plot_pending_orders()  # queue depth over time
ts.plot_inventory()  # per-SKU inventory levels per warehouse
```

`inventory_ts` is keyed by warehouse id with SKU-id keys. To include initial inventory in
the plot, seed it before running:

```python
for wh in warehouses:
    ts.inventory_ts[wh.id] = [(0.0, {sku.id: float(c.level) for sku, c in wh.inventory.items()})]
```

## Orders and due dates

Create orders with `coordinator.create_order()`. All keyword arguments
forward to `TransferOrder`:

```python
order = coordinator.create_order(
    sku=sku,
    quantity=3,
    origin=wh_a,
    destination=wh_b,
    due_date=env.now + 1800.0,  # 30 minutes from now
    priority=1.0,
)
coordinator.submit(order)
```

`due_date` does NOT affect dispatch priority or ordering — it is only
used by `OrderEMACollector` to track `ema_late_orders` (fraction of orders
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
    print(
        f"FAILED: sku={o.sku.id}, qty={o.quantity}, weight={o.sku.weight * o.quantity}, vol={o.sku.volume * o.quantity}"
    )
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

**Reading a physical field too early.**
`agv.current_node` right after construction raises `LayoutNotReady`; call
`env.layout.prepare()` first, or read it after the run starts.

**More than 100,000 entities before activation.** Entities (jobs and orders
included) created before activation are layout targets; past
`LayoutLimits.max_placements` preparation raises `LayoutError`. Use
`Layout(env, limits=...)` before anything reads `env.layout`.

**Multiple AGVs at one node under `ReservationController`.**
At activation the controller places each AGV and requires an immediate grant.
With a node capacity of 1, placing two AGVs at the same node raises
`RuntimeError`. Start them on a parking node with enough capacity, or spread them.

**Custom dispatch strategies take a context.**
`select(order, fleet, context)`: `context.graph`, `context.router`,
`context.view`, `context.cost(agv, node)`. The fleet passed includes AGVs that
are making way (`TRAVELING_EMPTY` without an order): choose only `IDLE` ones.
With `AGVType.idle_power`, read the battery with `agv.battery_level_now()`.

**`NearestIdleStrategy` + single parking = fleet collapse.**
All AGVs reposition to the same parking node. Tie-breaking by `agv_id`
means one AGV monopolizes dispatch. Use `RoundRobinStrategy` for balanced
utilization, or spread AGVs across multiple parking areas.

**Single-lane layouts under `ReservationController`.**
On a line or corridor without passing places, opposite traffic takes turns
and head-on meetings become deadlocks resolved by a step aside, or left
unresolved when no vehicle can move aside. Small layouts without parking can
end with stuck waits. Add sidings, capacity and parking (see Traffic control).

## Examples

Four progressive examples in `examples/`:

- `intralogistics_simple.py`: 5-node preset, 2 AGVs, text output
- `intralogistics_intermediate.py`: 10-node custom layout, 3 AGVs, 3 SKUs, staggered batches, plots
- `intralogistics_advanced.py`: 16-node hub, 5 AGVs, 5 SKUs, 3 warehouses, battery, charging, replenishment, EMA metrics (`OrderEMACollector`), 4 plots
- `intralogistics_traffic.py`: two-way corridor with a siding, 3 AGVs, `ReservationController`, `FastestRouter`, `TrafficKPIs`
