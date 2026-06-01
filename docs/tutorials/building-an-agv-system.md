# Building an AGV System

This tutorial assembles a complete intralogistics simulation from scratch — layout, warehouses, fleet, coordinator, and metrics — step by step. By the end you will have a running script that dispatches five transfer orders across two AGVs and reports per-order timings and fleet utilisation.

> See the runnable [intralogistics examples](../examples/intralogistics.md) for end-to-end systems with plots.

For quick setups consider `build_simple_system()` from the [Intralogistics examples](../examples/intralogistics.md); this tutorial builds everything manually so you can see how the pieces fit together.

See also: [Intralogistics guide](../guides/intralogistics.md) · [Intralogistics API](../api/intralogistics.md) · [Examples gallery](../examples/index.md)

---

## 1. Define the layout graph

The layout is a T-shaped corridor with a parking spur:

```
STORE_OUT(0,0) -- C1(10,0) -- C2(20,0) -- LINE_IN(30,0)
                      |
                   PARK(10,10)
```

```python
from simulatte.intralogistics import Arc, LayoutGraph, Node

store_out = Node(id="STORE_OUT", x=0.0, y=0.0)
c1        = Node(id="C1",        x=10.0, y=0.0)
c2        = Node(id="C2",        x=20.0, y=0.0)
line_in   = Node(id="LINE_IN",   x=30.0, y=0.0)
park_node = Node(id="PARK",      x=10.0, y=10.0)

arcs = [
    Arc(source=store_out, target=c1,      bidirectional=True),
    Arc(source=c1,        target=c2,      bidirectional=True),
    Arc(source=c2,        target=line_in, bidirectional=True),
    Arc(source=c1,        target=park_node, bidirectional=True),
]
graph = LayoutGraph([store_out, c1, c2, line_in, park_node], arcs)
```

`LayoutGraph` builds an adjacency structure that the path planner (Dijkstra or A*) uses to route AGVs.

---

## 2. SKUs and warehouses

Define your SKUs first, then create warehouses with initial inventory. `pick_time_fn` and `put_time_fn` control how long a warehouse slot is occupied during pick/put operations.

```python
from simulatte.intralogistics import SKU, Warehouse

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
```

---

## 3. AGV fleet

An `AGVType` bundles the speed profile, battery, capacity, and load/unload time functions. All AGVs of the same type share these settings.

```python
from simulatte.intralogistics import AGV, AGVType, TrapezoidalProfile

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
# Two AGVs starting at the corridor junction C1
agvs = [
    AGV(env=env, agv_type=agv_type, agv_id=f"agv-{i}", initial_node=c1)
    for i in range(2)
]
```

`TrapezoidalProfile` models acceleration and deceleration; travel time between nodes is computed from the actual arc distance and the profile.

---

## 4. Wire FleetCoordinator and policies

`FleetCoordinator` is the central dispatcher. Pass it the graph, fleet, warehouses, and optional infrastructure (charging stations, parking areas). The `dispatch_strategy` and `repositioning_policy` are pluggable.

```python
from simulatte.intralogistics import (
    DijkstraPlanner,
    FleetCoordinator,
    FreeTrafficManager,
    NearestIdleStrategy,
    NearestParkingPolicy,
    ParkingArea,
)

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
```

- `NearestIdleStrategy` selects the idle AGV closest to the order's origin warehouse.
- `NearestParkingPolicy` sends idle AGVs to the closest parking area to keep the main corridor clear.
- `FreeTrafficManager` imposes no node-capacity limits; swap in `ResourceBasedTrafficManager` for congestion control.
- `DijkstraPlanner` computes shortest paths; `AStarPlanner` is available for larger graphs.

---

## 5. Dispatch transfer orders

Create orders with `coordinator.create_order()` and submit them with `coordinator.submit()`. The coordinator dispatches them to AGVs as capacity allows.

```python
orders = []
for sku, qty in [(sku_a, 4), (sku_b, 2), (sku_a, 6), (sku_b, 3), (sku_a, 2)]:
    order = coordinator.create_order(
        sku=sku,
        quantity=qty,
        origin=storage,
        destination=production_line,
    )
    coordinator.submit(order)
    orders.append(order)

env.run(until=300.0)
```

---

## 6. Read metrics

After the simulation, inspect per-order timing attributes and use `coordinator.agv_report()` for fleet-level data.

```python
from simulatte.intralogistics import OrderStatus

completed = [o for o in orders if o.status is OrderStatus.COMPLETED]
avg_fulfillment = sum(o.delivered_at - o.created_at for o in completed) / len(completed)

print(f"Completed: {len(completed)}/{len(orders)}")
print(f"Avg fulfillment time: {avg_fulfillment:.1f}s")
print(f"Fleet utilization: {coordinator.fleet_utilization:.1%}")
for info in coordinator.agv_report():
    print(f"  {info['agv_id']}: state={info['state']}, battery={info['battery_pct']:.0%}")
```

---

## Full script

The complete runnable script is at [`examples/building_an_agv_system.py`](https://github.com/dmezzogori/simulatte/blob/main/examples/building_an_agv_system.py).

**Run it:**

```bash
uv run python examples/building_an_agv_system.py
```

## Output

```text
Building an AGV System — step-by-step example
Layout: 5 nodes, 4 arcs
Fleet: 2 AGVs
Orders: 5 submitted
Simulation time: 300.0s

Completed orders: 5/5
Avg fulfillment time: 135.8s

order sku          qty status      dispatch   pick deliver agv
    1 ComponentA       4 COMPLETED       0.0    21.5    59.5 agv-0
    2 ComponentB       2 COMPLETED       0.0    17.5    55.5 agv-1
    3 ComponentA       6 COMPLETED      82.1   117.1   156.7 agv-1
    4 ComponentB       3 COMPLETED      86.1   115.1   154.7 agv-0
    5 ComponentA       2 COMPLETED     182.9   211.1   252.7 agv-0

Inventory (start -> end):
  Storage:
    ComponentA      50 ->  38
    ComponentB      30 ->  25
  ProductionLine:
    ComponentA       0 ->  12
    ComponentB       0 ->   5

Fleet report:
  Overall utilization: 78.0%
  agv-0: state=IDLE, battery=77%, node=PARK
  agv-1: state=IDLE, battery=85%, node=PARK
```

## What the output shows

All five orders complete within the 300-second window. The two AGVs handle the first two orders in parallel (both dispatched at t=0), then pick up the next pair once they return (~t=82–86 s), and agv-0 takes the fifth order alone. Average fulfillment time of 135.8 s reflects pick time (which scales with quantity), travel across 30 m of corridor, and unload time at the production line. Both AGVs park at the PARK node when idle, keeping the main corridor free. Fleet utilisation is 78 %, leaving headroom to absorb additional orders without queuing.

---

## Next steps

This example deliberately uses a generous `battery_capacity` (1000.0) and omits a charging station so the fleet never needs to recharge — keeping the focus on the core wiring. To model a realistic battery lifecycle:

- Add a `ChargingStation` and lower `battery_capacity` to see automatic recharging in action (see the [advanced intralogistics example](../examples/intralogistics.md#advanced))
- Attach a `DefaultIntralogisticsCollector` to record time-series and call `plot_fleet_utilization()` for a visual breakdown
- Swap `NearestIdleStrategy` for `RoundRobinStrategy` to compare fleet balance
