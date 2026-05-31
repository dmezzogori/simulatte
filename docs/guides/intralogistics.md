# Intralogistics

Simulatte's intralogistics subpackage models warehouse-to-warehouse material transport using AGV (Automated Guided Vehicle) fleets. It provides a complete toolkit for building, running, and analyzing intralogistics simulations.

Import everything from a single namespace:

```python
from simulatte.intralogistics import (
    Node, Arc, LayoutGraph,          # spatial layout
    SKU, Warehouse,                  # products and storage
    AGV, AGVType, TrapezoidalProfile,# vehicle fleet
    FleetCoordinator, TransferOrder, # orchestration
    ChargingStation, ParkingArea,    # facilities
    NearestIdleStrategy,             # dispatch policies
    RoundRobinStrategy,
    NearestParkingPolicy,            # repositioning
    ReorderPointPolicy,              # replenishment
    ReturnToOrigin,                  # load recovery
    EMAOrderMetrics,                 # order-level metrics
    DefaultIntralogisticsCollector,  # time-series + plots
)
```

## Core concepts

**Layout** --- Define your facility as a directed graph of `Node` and `Arc` objects. Nodes have (x, y) coordinates; arcs can be one-way or bidirectional. `LayoutGraph` provides pathfinding via Dijkstra or A*.

**Warehouses** --- Each `Warehouse` has input/output bays (graph nodes), finite pick/put slots, and per-SKU inventory tracked as SimPy containers.

**AGV fleet** --- `AGV` instances are typed by `AGVType`, which bundles a `SpeedProfile`, battery parameters, and capacity limits. `TrapezoidalProfile` models acceleration, cruising, and deceleration with optional battery degradation and load speed factors.

**FleetCoordinator** --- The central orchestrator (analogous to `ShopFloor` for production). It manages the full mission lifecycle: dispatch, travel, pick, transit, deliver, reposition, and charge. Pluggable strategies control each decision point.

**Policies** --- Dispatch (`NearestIdleStrategy`, `RoundRobinStrategy`), repositioning (`NearestParkingPolicy`, `StayInPlace`), replenishment (`ReorderPointPolicy`), and load recovery (`ReturnToOrigin`, `ResumeDelivery`).

**Metrics** --- `EMAOrderMetrics` tracks exponential moving averages of fulfillment time, dispatch delay, and travel times. `DefaultIntralogisticsCollector` records time-series data and provides plot methods: `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`.

## Examples

Three runnable examples in the [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples) folder provide a progressive learning path:

- [Examples walkthrough](../examples/intralogistics.md): layout diagrams, feature progression, and annotated output for all three examples.

## Next

- [Examples walkthrough](../examples/intralogistics.md)
- [Basic Usage](../introduction/basic-usage.md) (production planning quick start)
- [Tutorials](../tutorials/index.md) (job-shop tutorials)
