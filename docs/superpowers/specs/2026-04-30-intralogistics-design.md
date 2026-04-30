# Intralogistics Subsystem Design

**Date:** 2026-04-30
**Status:** Approved
**Scope:** Standalone intralogistics simulation with warehouses, AGV fleet, grid-based spatial layout, and transfer order orchestration.

## 1. Goals

Build a self-contained intralogistics simulation subsystem within simulatte that supports:

- Grid-based spatial layouts with configurable node-arc graph topology
- Heterogeneous AGV fleets with capacity, battery, speed profiles, and SKU compatibility
- Warehouses as inventory sources/sinks with input/output bays on the graph
- Transfer order and replenishment-driven SKU movement between warehouses
- Traffic management with deadlock prevention
- Charging/swapping stations for AGV battery management
- OEE-style AGV metrics and system-level observability

The subsystem must be usable standalone (intralogistics-only simulations) without depending on the production-oriented core (`Server`, `ShopFloor`, `ProductionJob`). It shares only `Environment` and logging from core simulatte.

## 2. Package Structure

```
src/simulatte/
├── intralogistics/
│   ├── __init__.py              # Public API exports
│   ├── graph.py                 # Node, Arc, LayoutGraph
│   ├── pathfinding.py           # PathPlanner protocol, DijkstraPlanner, AStarPlanner
│   ├── traffic.py               # TrafficManager protocol, ResourceBasedTrafficManager, FreeTrafficManager
│   ├── sku.py                   # SKU dataclass
│   ├── agv.py                   # AGV, AGVType, AGVState enum
│   ├── speed.py                 # SpeedProfile protocol, TrapezoidalProfile
│   ├── battery.py               # Battery, built-in depletion/recharge functions
│   ├── warehouse.py             # Warehouse (enhanced black box with bay nodes)
│   ├── charging.py              # ChargingStation (recharge + swap)
│   ├── parking.py               # ParkingArea
│   ├── order.py                 # TransferOrder
│   ├── policies.py              # DispatchStrategy, ReplenishmentPolicy, RepositioningPolicy + built-in implementations
│   ├── coordinator.py           # FleetCoordinator (orchestration)
│   ├── metrics.py               # OrderMetricsCollector, IntralogisticsTimeSeriesCollector + built-in implementations
│   ├── hooks.py                 # MissionHook and lifecycle event protocols
│   └── builders.py              # Convenience factory functions
```

### Dependency Rules

- `simulatte.intralogistics` imports only `simulatte.environment` and `simulatte.logger`.
- No dependency on `Server`, `ShopFloor`, `ProductionJob`, or `simulatte.policies`.
- A future combined production+intralogistics simulation would import both subsystems and bridge them at the user level.

### Cleanup of `simulatte.experimental`

The following modules are removed from `simulatte.experimental` (no backward compatibility, no deprecation warnings — all APIs are explicitly unstable):

- `agv.py` — replaced by `intralogistics/agv.py`
- `warehouse.py` — replaced by `intralogistics/warehouse.py`
- `materials.py` (MaterialCoordinator) — replaced by `intralogistics/coordinator.py`
- `builders.py` (MaterialSystemBuilder) — replaced by `intralogistics/builders.py`
- `job.py` (TransportJob, WarehouseJob) — replaced by `intralogistics/order.py`
- `typing.py` (MaterialSystem) — removed

`simulatte.experimental` retains only `gymnasium.py` (SimulatteEnv). Its `__init__.py` is updated to export only `SimulatteEnv`.

Tests under `tests/experimental/` are removed except `test_gymnasium.py`. New tests go under `tests/intralogistics/`.

## 3. Spatial Layer

### Node

Pure location in the graph. No reference to facilities — facilities reference their nodes, not the reverse.

```python
@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float
```

Frozen dataclass: hashable, usable as dict keys and in sets.

### Arc

Directional or bidirectional connection between two nodes.

```python
@dataclass(frozen=True)
class Arc:
    source: Node
    target: Node
    bidirectional: bool = True
    speed_limit: float | None = None
```

- **Distance** is computed from node coordinates: `sqrt((x2-x1)² + (y2-y1)²)`.
- **`speed_limit`** caps AGV max speed on this arc (models narrow aisles, turns, slow zones).
- A bidirectional arc registers traversal in both directions internally.

### LayoutGraph

Immutable graph built at simulation setup.

```python
class LayoutGraph:
    def __init__(self, nodes: Iterable[Node], arcs: Iterable[Arc]) -> None: ...

    def neighbors(self, node: Node) -> list[Node]: ...
    def arc_between(self, source: Node, target: Node) -> Arc | None: ...
    def distance(self, source: Node, target: Node) -> float: ...
    def shortest_path(self, source: Node, target: Node) -> list[Node]: ...
```

`shortest_path` uses the default `PathPlanner` (Dijkstra). The graph stores adjacency internally and is not modified during simulation.

### PathPlanner (Protocol)

```python
class PathPlanner(Protocol):
    def plan(self, graph: LayoutGraph, origin: Node, destination: Node) -> list[Node]: ...
```

Built-in implementations:

- **`DijkstraPlanner`** — shortest path by distance. Default.
- **`AStarPlanner`** — A* with Euclidean heuristic. Faster for large grids.

The `LayoutGraph` holds a default planner (Dijkstra). The `FleetCoordinator` can use a different planner (e.g., congestion-aware) independently.

## 4. Traffic Layer

### TrafficManager (Protocol)

Controls AGV movement through the graph. Separates traffic control from movement physics and path planning.

```python
class TrafficManager(Protocol):
    def reserve(self, agv: AGV, path: list[Node]) -> ProcessGenerator:
        """Register intent and check for conflicts. May block or trigger reroute."""
        ...

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:
        """Acquire the node. Blocks if at capacity. Timeout triggers resolution."""
        ...

    def leave_node(self, agv: AGV, node: Node) -> None:
        """Release the node and update intent registry."""
        ...

    def cancel(self, agv: AGV) -> None:
        """Cancel the AGV's current path intent (for rerouting)."""
        ...
```

### Movement Loop (owned by FleetCoordinator)

```
1. PathPlanner computes route: [N1, N2, N3, N4]
2. TrafficManager.reserve(agv, path)       — register intent, check conflicts
3. For each hop (N1→N2, N2→N3, N3→N4):
   a. TrafficManager.enter_node(agv, next) — acquire next node
   b. SpeedProfile computes travel_time    — physics
   c. yield env.timeout(travel_time)       — SimPy movement
   d. Battery.deplete(distance, weight, speed)
   e. TrafficManager.leave_node(agv, prev) — release previous node
4. AGV arrives at destination
```

### Strategy: Hop-by-Hop with Intent Registration + Runtime Resolution

The AGV holds **at most 2 nodes** during a transition (current + next), and **exactly 1** when stationary.

**`reserve()` semantics:** Registers the AGV's intended path in a shared registry (a plain dict, not SimPy resources). Checks for conflicts against other registered paths. If two AGVs' paths would cause a head-on conflict on a shared segment, `reserve()` resolves proactively: delay one AGV's departure, or signal the coordinator to re-plan via the `PathPlanner`.

**`enter_node()` semantics:** Requests the next node's SimPy resource. If the node is at capacity, blocks. If blocked longer than a configurable timeout, triggers deadlock resolution — the AGV releases its current node and the coordinator re-plans the route.

**`cancel()` semantics:** Removes the AGV's current intent from the registry. Used when the coordinator decides to reroute an AGV.

### Built-in Implementations

**`FreeTrafficManager`** — all methods are no-ops. No constraints. For simulations where congestion is not the focus.

**`ResourceBasedTrafficManager`** — each node is a `simpy.Resource` with configurable capacity (default 1). Implements intent registration, conflict detection, and timeout-based deadlock resolution. Constructor accepts `node_capacity: int = 1` and `deadlock_timeout: float`.

### Future: FullTrafficController

A future implementation can do time-windowed reservations on top of the same protocol — same `TrafficManager` interface, richer `reserve()` logic. No changes needed to the AGV, FleetCoordinator, or graph.

## 5. SKU Model

```python
@dataclass(frozen=True)
class SKU:
    id: str
    weight: float                          # kg per unit
    volume: float                          # m³ per unit
    attributes: tuple[tuple[str, Any], ...] = ()
```

- `weight` and `volume` are first-class for AGV capacity checks.
- `attributes` stores domain-specific properties (fragility, temperature class, hazmat flag, etc.) as an immutable tuple of key-value pairs. A convenience `get_attribute(key)` method provides dict-like lookup. This keeps SKU frozen and hashable.
- Frozen and hashable: usable as dict keys.
- AGV/SKU compatibility is determined by `AGVType.compatibility_fn(sku) -> bool`.

## 6. AGV Layer

### AGVState

```python
class AGVState(Enum):
    IDLE = auto()
    TRAVELING_EMPTY = auto()
    WAITING_LOAD = auto()
    TRAVELING_LOADED = auto()
    WAITING_UNLOAD = auto()
    CHARGING = auto()
```

Mutually exclusive and exhaustive. At any simulation time, an AGV is in exactly one state. Time is accumulated per-state for OEE reporting.

**Derived metric:** `% utilized = (TRAVELING_EMPTY + WAITING_LOAD + TRAVELING_LOADED + WAITING_UNLOAD) / total_time`. This is everything except IDLE and CHARGING.

**Edge case — traveling to charger:** counts as TRAVELING_EMPTY since the AGV is physically traveling empty. The `attributes` on the state transition (or a flag on the AGV) can tag it as "charging-related travel" for developers who want to split it out.

### SpeedProfile (Protocol)

```python
class SpeedProfile(Protocol):
    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float: ...
```

### TrapezoidalProfile (built-in)

```python
class TrapezoidalProfile:
    def __init__(
        self,
        max_speed: float,                    # m/s
        acceleration: float,                  # m/s²
        deceleration: float,                  # m/s²
        battery_degradation_fn: Callable[[float], float] | None = None,
        load_speed_factor_fn: Callable[[float], float] | None = None,
    ) -> None: ...
```

Computes trapezoidal velocity profile: accelerate → cruise at max_speed → decelerate. If the arc is too short to reach max speed, the profile becomes triangular (accelerate → decelerate directly).

- `battery_degradation_fn(battery_level) -> speed_factor`: scales max_speed and acceleration. Default: linear (100% battery = 100% speed, 0% battery = 0% speed).
- `load_speed_factor_fn(load_weight) -> speed_factor`: scales max_speed based on carried weight. Default: no effect (factor = 1.0).
- `speed_limit` from the arc caps the effective max_speed.

### Battery

```python
class Battery:
    def __init__(
        self,
        capacity: float,
        initial_level: float | None = None,
        depletion_fn: Callable[[float, float, float], float] | None = None,
        recharge_fn: Callable[[float, float], float] | None = None,
        low_threshold: float = 0.2,
        critical_threshold: float = 0.05,
    ) -> None: ...
```

- `capacity`: total energy (abstract units).
- `initial_level`: defaults to `capacity` (fully charged).
- `depletion_fn(distance, load_weight, speed) -> energy_consumed`: default is linear on distance.
- `recharge_fn(current_level, target_level) -> recharge_time`: default is linear rate.
- `low_threshold` / `critical_threshold`: percentages (0.0–1.0) triggering low-battery and critical-battery behaviors.

Properties and methods:

- `level: float` — current energy level.
- `level_pct: float` — `level / capacity` (0.0–1.0).
- `is_low: bool` — `level_pct <= low_threshold`.
- `is_critical: bool` — `level_pct <= critical_threshold`.
- `deplete(distance, load_weight, speed) -> None` — reduces level.
- `recharge_time(target_pct=1.0) -> float` — computes time to recharge.

### AGVType

Configuration template shared by multiple AGVs of the same type.

```python
@dataclass
class AGVType:
    name: str
    speed_profile: SpeedProfile
    battery_capacity: float
    weight_capacity: float                   # max kg
    volume_capacity: float                   # max m³
    compatibility_fn: Callable[[SKU], bool] = lambda sku: True
    depletion_fn: Callable[[float, float, float], float] | None = None
    recharge_fn: Callable[[float, float], float] | None = None
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    load_time_fn: Callable[[], float] = lambda: 0.0
    unload_time_fn: Callable[[], float] = lambda: 0.0
```

### AGV

**Does not extend `Server`.** The AGV is its own entity — not a SimPy resource that jobs request. Assignment is managed by the `FleetCoordinator` through `DispatchStrategy`.

```python
class AGV:
    def __init__(
        self,
        *,
        env: Environment,
        agv_type: AGVType,
        agv_id: str | None = None,
        initial_node: Node | None = None,
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
    ) -> None: ...
```

Key attributes:

- `agv_id: str` — auto-generated if not provided.
- `agv_type: AGVType` — configuration reference.
- `battery: Battery` — own instance, created from `AGVType` config.
- `state: AGVState` — current state (read-only property).
- `current_node: Node | None` — current position on the graph.
- `current_load: dict[SKU, int] | None` — what's being carried.

Methods:

- `can_carry(sku, quantity) -> bool` — checks weight, volume, and compatibility.
- `utilization() -> float` — `(total - idle - charging) / total`.
- `state_percentage(state) -> float` — time in a given state / total time.
- `state_durations: dict[AGVState, float]` — accumulated time per state.
- `_transition_to(new_state) -> None` — called by FleetCoordinator, accumulates time in previous state.

## 7. Facility Layer

### Warehouse

**Does not extend `Server`.** Standalone facility placed on the graph via bay nodes. Uses `simpy.Resource` internally for slot capacity.

```python
class Warehouse:
    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        input_bays: list[Node],
        output_bays: list[Node],
        n_slots: int,
        products: list[SKU],
        initial_inventory: dict[SKU, int] | None = None,
        pick_time_fn: Callable[[SKU, int], float],
        put_time_fn: Callable[[SKU, int], float],
    ) -> None: ...
```

- `input_bays` / `output_bays`: graph nodes where AGVs interact with this warehouse. An AGV delivering TO this warehouse navigates to an input bay. An AGV picking up FROM this warehouse navigates to an output bay.
- `n_slots`: concurrent pick/put operations (SimPy resource capacity).
- `pick_time_fn(sku, quantity) -> float` and `put_time_fn(sku, quantity) -> float`: time depends on what and how much.
- Inventory is `simpy.Container` per SKU. `pick()` blocks if insufficient stock (natural backpressure for stockouts).

Methods:

- `pick(sku, quantity) -> ProcessGenerator` — acquires slot, waits for inventory, simulates pick time.
- `put(sku, quantity) -> ProcessGenerator` — acquires slot, simulates put time, adds to inventory.
- `get_inventory_level(sku) -> float` — current stock level.
- `nearest_input_bay(from_node, graph) -> Node` — closest input bay by graph distance.
- `nearest_output_bay(from_node, graph) -> Node` — closest output bay by graph distance.

Metrics: `total_picks`, `total_puts`, `average_pick_time`, `average_put_time`.

### ChargingStation

```python
class ChargingStation:
    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        n_slots: int,
        recharge_fn: Callable[[float, float], float] | None = None,
        supports_swap: bool = False,
        swap_pool_size: int = 0,
        swap_time: float = 0.0,
        swap_recharge_time: float = 0.0,
    ) -> None: ...
```

- `node`: location on the graph.
- `n_slots`: concurrent charging (SimPy resource capacity).
- `recharge_fn(current_level, target_level) -> time`: overrides AGV battery's recharge_fn (models fast vs. slow chargers).
- Swap support: `swap_pool_size` pre-charged batteries, `swap_time` for the swap operation, `swap_recharge_time` for depleted batteries to recharge and re-enter the pool.

Methods:

- `recharge(agv, target_pct=1.0) -> ProcessGenerator` — acquires slot, simulates recharge time, updates battery, releases slot.
- `swap(agv) -> ProcessGenerator` — acquires slot, if pool has a battery: near-instant swap; if pool empty: waits for next battery to finish recharging.

Metrics: `total_recharges`, `total_swaps`, `total_occupied_time`.

### ParkingArea

```python
class ParkingArea:
    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        capacity: int,
    ) -> None: ...
```

- `node`: location on the graph.
- `capacity`: max AGVs (SimPy resource).

Methods:

- `enter(agv) -> ProcessGenerator` — blocks if full.
- `leave(agv) -> None` — releases slot.

## 8. Order & Dispatch Layer

### TransferOrder

The unit of work in the intralogistics system.

```python
@dataclass
class TransferOrder:
    id: str                            # auto-generated UUID
    sku: SKU
    quantity: int
    origin: Warehouse
    destination: Warehouse
    created_at: float                  # env.now at creation
    due_date: float | None = None
    priority: float = 0.0             # lower = higher priority

    # Lifecycle timestamps (set by FleetCoordinator)
    dispatched_at: float | None = None
    picked_at: float | None = None
    delivered_at: float | None = None
    assigned_agv: AGV | None = None
```

### DispatchStrategy (Protocol)

```python
class DispatchStrategy(Protocol):
    def select(
        self,
        order: TransferOrder,
        fleet: list[AGV],
        graph: LayoutGraph,
    ) -> AGV | None: ...
```

Returns `None` if no compatible AGV is available (order enters pending queue).

Built-in implementations:

- **`NearestIdleStrategy`** — selects the idle AGV closest to the origin warehouse's output bay. Filters by SKU compatibility and capacity. Ties broken by AGV ID.
- **`RoundRobinStrategy`** — cycles through compatible idle AGVs.

### ReplenishmentPolicy (Protocol)

```python
class ReplenishmentPolicy(Protocol):
    def check(
        self,
        warehouse: Warehouse,
        all_warehouses: list[Warehouse],
    ) -> list[TransferOrder]: ...
```

Returns transfer orders to submit, or an empty list.

Built-in implementations:

- **`ReorderPointPolicy`** — when a SKU's inventory drops below a configured threshold, creates a transfer order to pull from the warehouse with the highest stock of that SKU. Constructor takes `thresholds: dict[SKU, int]` and `reorder_quantity: dict[SKU, int]`.

Registration on `FleetCoordinator`:

```python
def add_replenishment_policy(
    self,
    policy: ReplenishmentPolicy,
    warehouse: Warehouse,
    check_interval: float | None = None,
) -> None: ...
```

If `check_interval` is set, the policy is checked periodically (SimPy process with timeout loop). Otherwise, it is checked after every pick from the monitored warehouse.

### RepositioningPolicy (Protocol)

```python
class RepositioningPolicy(Protocol):
    def reposition(
        self,
        agv: AGV,
        graph: LayoutGraph,
    ) -> Node | None: ...
```

Returns a target node, or `None` to stay in place.

Built-in implementations:

- **`StayInPlace`** — returns `None`. Default.
- **`NearestParkingPolicy`** — sends AGV to the nearest `ParkingArea` with available capacity.

## 9. Orchestration: FleetCoordinator

Central orchestrator for the intralogistics subsystem. Analogous to `ShopFloor` for production simulations.

```python
class FleetCoordinator:
    def __init__(
        self,
        *,
        env: Environment,
        graph: LayoutGraph,
        fleet: list[AGV],
        warehouses: list[Warehouse],
        charging_stations: list[ChargingStation],
        parking_areas: list[ParkingArea] | None = None,
        traffic_manager: TrafficManager | None = None,
        path_planner: PathPlanner | None = None,
        dispatch_strategy: DispatchStrategy | None = None,
        repositioning_policy: RepositioningPolicy | None = None,
        order_metrics_collector: OrderMetricsCollector | None = None,
        time_series_collector: IntralogisticsTimeSeriesCollector | None = None,
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
    ) -> None: ...
```

Defaults:
- `traffic_manager`: `FreeTrafficManager()`
- `path_planner`: `DijkstraPlanner()`
- `dispatch_strategy`: `NearestIdleStrategy()`
- `repositioning_policy`: `StayInPlace()`
- `order_metrics_collector`: `EMAOrderMetrics()`
- `on_low_battery`: built-in behavior (divert to nearest charging station)

### Submitting Orders

```python
def submit(self, order: TransferOrder) -> None: ...
```

Dispatches an AGV via `DispatchStrategy.select()`. If no AGV is available, the order enters a pending queue. When an AGV becomes idle, the coordinator checks the pending queue.

### Mission Lifecycle

Each submitted order spawns a SimPy process:

1. **DISPATCH** — `DispatchStrategy.select()` picks an AGV. AGV transitions to `TRAVELING_EMPTY`.
2. **TRAVEL EMPTY** — AGV navigates to origin warehouse's nearest output bay. `PathPlanner` computes route. `TrafficManager` controls hop-by-hop movement. `SpeedProfile` computes travel time per arc. `Battery` depletes per arc.
3. **LOAD** — AGV transitions to `WAITING_LOAD`. `Warehouse.pick(sku, quantity)` blocks if no inventory. `AGVType.load_time_fn()` simulates physical loading. AGV transitions to `TRAVELING_LOADED`.
4. **TRAVEL LOADED** — AGV navigates to destination warehouse's nearest input bay. Same mechanics as step 2.
5. **UNLOAD** — AGV transitions to `WAITING_UNLOAD`. `Warehouse.put(sku, quantity)`. `AGVType.unload_time_fn()` simulates physical unloading.
6. **POST-MISSION** — Battery check: if low, divert to nearest `ChargingStation` (AGV transitions to `CHARGING`). Otherwise, `RepositioningPolicy.reposition()` decides next position. AGV transitions to `IDLE`.

### Battery Management During Missions

After each arc traversal, the coordinator checks the AGV's battery:

- **`is_low`**: flag is set to charge after current mission completes (step 6 diverts to charging instead of repositioning).
- **`is_critical`**: mission is interrupted immediately. The AGV diverts to the nearest charging station. The order is re-queued for another AGV.
- Developer can override via `on_low_battery` callback on the `FleetCoordinator` or on individual `AGV` instances.

### Lifecycle Hooks

```python
def on_order_submitted(self, callback: Callable[[TransferOrder], None]) -> None: ...
def on_order_dispatched(self, callback: Callable[[TransferOrder, AGV], None]) -> None: ...
def on_pickup_complete(self, callback: Callable[[TransferOrder, AGV], None]) -> None: ...
def on_delivery_complete(self, callback: Callable[[TransferOrder, AGV], None]) -> None: ...
def on_battery_low(self, callback: Callable[[AGV], None]) -> None: ...
def on_charging_started(self, callback: Callable[[AGV, ChargingStation], None]) -> None: ...
def on_charging_complete(self, callback: Callable[[AGV, ChargingStation], None]) -> None: ...
def on_agv_idle(self, callback: Callable[[AGV], None]) -> None: ...
```

### Fleet-Level Convenience

```python
@property
def fleet_utilization(self) -> float: ...
def fleet_state_breakdown(self) -> dict[AGVState, float]: ...
def agv_report(self) -> list[dict]: ...
```

## 10. Metrics & Observability

### OrderMetricsCollector (Protocol)

```python
class OrderMetricsCollector(Protocol):
    def record(self, order: TransferOrder) -> None: ...
```

Built-in: **`EMAOrderMetrics`** — tracks EMA of:
- `ema_fulfillment_time` — order created to delivered
- `ema_dispatch_delay` — order created to AGV dispatched
- `ema_travel_time_empty` — dispatch to pickup
- `ema_travel_time_loaded` — pickup to delivery
- `ema_late_orders` — proportion delivered after due_date

### IntralogisticsTimeSeriesCollector (Protocol)

```python
class IntralogisticsTimeSeriesCollector(Protocol):
    def on_order_submitted(self, coordinator: FleetCoordinator, order: TransferOrder) -> None: ...
    def on_order_dispatched(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None: ...
    def on_delivery_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None: ...
    def on_agv_state_changed(self, coordinator: FleetCoordinator, agv: AGV, old: AGVState, new: AGVState) -> None: ...
```

Built-in: **`DefaultIntralogisticsCollector`** — records:
- `fleet_utilization_ts: list[tuple[float, float]]` — average fleet utilization over time
- `pending_orders_ts: list[tuple[float, int]]` — order queue depth over time
- `throughput_ts: list[tuple[float, int]]` — cumulative completed orders
- `inventory_ts: dict[Warehouse, list[tuple[float, dict[SKU, float]]]]` — per-warehouse inventory levels

Each with matplotlib plot methods, same style as `DefaultTimeSeriesCollector` in `shopfloor.py`.

### AGV OEE Metrics

Built into the `AGV` class (section 6). The AGV tracks `state_durations: dict[AGVState, float]` and provides `utilization()` and `state_percentage(state)`.

### Environment Logging

All components use `env.debug()` / `env.info()` with component tags:
- `component="FleetCoordinator"`
- `component="AGV"`
- `component="Warehouse"`
- `component="TrafficManager"`
- `component="ChargingStation"`

The existing `env.logger.disable_component()` / `enable_component()` filtering works without modification.

## 11. Out of Scope / Future Work

The following are explicitly deferred:

- **Full traffic controller** (option C) — time-windowed reservations, intersection management. The `TrafficManager` protocol is designed to accommodate this without changes to AGV or FleetCoordinator.
- **Structured warehouse internals** — zones, aisles, storage location modeling. The current enhanced black-box model can be extended later.
- **Multi-stop routes** — an AGV picking from multiple locations in one trip. The current model is single-origin-single-destination. Multi-stop is a vehicle routing problem that adds significant complexity.
- **AGV-to-AGV handoffs** — transferring cargo between AGVs mid-route.
- **Dynamic graph modification** — adding/removing nodes and arcs during simulation. The graph is immutable after setup.
- **Production integration bridge** — wiring intralogistics into the ShopFloor/MaterialCoordinator pattern. Can be built later as a user-level bridge without changes to either subsystem.
