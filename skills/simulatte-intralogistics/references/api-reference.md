# Intralogistics API Reference

Import everything from `simulatte.intralogistics`.

## Spatial Layer

### Node

```python
@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float
```

### Arc

```python
@dataclass(frozen=True)
class Arc:
    source: Node
    target: Node
    bidirectional: bool = True
    speed_limit: float | None = None
```

### LayoutGraph

```python
class LayoutGraph:
    def __init__(self, nodes: Iterable[Node], arcs: Iterable[Arc]) -> None: ...

    @property
    def nodes(self) -> tuple[Node, ...]: ...   # insertion order
    def neighbors(self, node: Node) -> list[Node]: ...
    def arc_between(self, source: Node, target: Node) -> Arc | None: ...
    def distance(self, source: Node, target: Node) -> float: ...  # Euclidean, requires arc
    @staticmethod
    def path_distance(path: list[Node]) -> float: ...              # sum of segments
    def shortest_path(self, source: Node, target: Node) -> list[Node] | None: ...  # Dijkstra
```

### Pathfinding

```python
class DijkstraPlanner:
    def plan(self, graph, origin, destination, avoid=None) -> list[Node] | None: ...

class AStarPlanner:
    def plan(self, graph, origin, destination, avoid=None) -> list[Node] | None: ...
```

## Products

### SKU

```python
@dataclass(frozen=True)
class SKU:
    id: str
    weight: float
    volume: float
    attributes: tuple[tuple[str, Any], ...] = ()

    def get_attribute(self, key: str, default=None) -> Any: ...
```

## Warehouses

### Warehouse

```python
class Warehouse:
    def __init__(
        self, *, env, name: str,
        input_bays: list[Node],    # nodes where AGVs deliver
        output_bays: list[Node],   # nodes where AGVs pick up
        n_slots: int,              # concurrent pick/put operations
        products: list[SKU],
        initial_inventory: dict[SKU, int] | None = None,
        pick_time: SamplerDescription | float | Callable[[SKU, int], float],  # streams "<name>/pick", "<name>/put"
        put_time: SamplerDescription | float | Callable[[SKU, int], float],   # number/description: managed; callable: opaque
    ) -> None: ...

    def get_inventory_level(self, sku: SKU) -> float: ...
    def pick(self, sku, quantity, *, on_committed=None) -> ProcessGenerator: ...  # waits for inventory
    def put(self, sku, quantity) -> ProcessGenerator: ...
    def nearest_input_bay(self, from_node, graph) -> Node: ...
    def nearest_output_bay(self, from_node, graph) -> Node: ...

    # Metrics
    total_picks: int
    total_puts: int
    average_pick_time: float  # property
    average_put_time: float   # property

    # Inventory access (for seeding time-series)
    inventory: dict[SKU, simpy.Container]
```

## Vehicles

### AGVState

```python
class AGVState(Enum):
    IDLE = auto()
    TRAVELING_EMPTY = auto()
    WAITING_LOAD = auto()
    TRAVELING_LOADED = auto()
    WAITING_UNLOAD = auto()
    CHARGING = auto()
    STRANDED = auto()
```

### AGVType

```python
@dataclass(frozen=True)
class AGVType:
    name: str
    speed_profile: SpeedProfile
    battery_capacity: float
    weight_capacity: float
    volume_capacity: float
    compatibility_fn: Callable[[Any], bool] = lambda sku: True
    depletion_fn: Callable[[float, float, float], float] | None = None  # (distance, load_weight, speed) -> energy
    recharge_fn: Callable[[float, float], float] | None = None          # (current_level, target_level) -> time
    low_battery_threshold: float = 0.2       # fraction, triggers charging after mission
    critical_battery_threshold: float = 0.05 # fraction, triggers mid-trip charging
    load_time: SamplerDescription | float | Callable[[], float] = 0.0    # bound per AGV: "<agv id>/load"
    unload_time: SamplerDescription | float | Callable[[], float] = 0.0  # bound per AGV: "<agv id>/unload"
```

Default depletion: `distance * 1.0`. Default recharge: `(target - current) * 1.0`.

### AGV

```python
class AGV:
    def __init__(self, *, env, agv_type: AGVType, agv_id: str | None = None, initial_node: Node | None = None) -> None: ...

    agv_id: str
    agv_type: AGVType
    current_node: Node | None
    current_load: dict[SKU, int] | None
    battery: Battery
    state: AGVState  # property

    def can_carry(self, sku: SKU, quantity: int) -> bool: ...  # checks weight AND volume AND compatibility
    def utilization(self) -> float: ...          # fraction of time in utilized states
    def state_percentage(self, state) -> float: ...
    def time_allocation(self) -> dict[AGVState, float]: ...
```

### TrapezoidalProfile

```python
class TrapezoidalProfile:
    def __init__(
        self,
        max_speed: float,
        acceleration: float,
        deceleration: float,
        battery_degradation_fn: Callable[[float], float] | None = None,  # battery_pct -> speed_factor
        load_speed_factor_fn: Callable[[float], float] | None = None,    # load_weight -> speed_factor
    ) -> None: ...

    def travel_time(self, distance, load_weight=0.0, battery_level=1.0, speed_limit=None) -> float: ...
```

Default battery degradation: `lambda level: level` (proportional). Default load factor: `lambda _: 1.0` (no effect).

### Battery

```python
class Battery:
    def __init__(self, capacity, initial_level=None, depletion_fn=None, recharge_fn=None,
                 low_threshold=0.2, critical_threshold=0.05) -> None: ...

    capacity: float
    level: float
    level_pct: float    # property, 0-1
    is_low: bool        # property, level_pct <= low_threshold
    is_critical: bool   # property, level_pct <= critical_threshold

    def estimate_energy(self, distance, load_weight, speed) -> float: ...
    def deplete(self, distance, load_weight, speed) -> None: ...
    def recharge(self, amount) -> None: ...
    def recharge_time(self, target_pct=1.0) -> float: ...
```

## Facilities

### ChargingStation

```python
class ChargingStation:
    def __init__(self, *, env, name: str, node: Node, n_slots: int,
                 recharge_time=None,  # number | description | (current_level, target_level) -> time; stream "<name>/recharge"
                 supports_swap=False, swap_pool_size=0,
                 swap_time=0.0, swap_recharge_time=0.0) -> None: ...

    node: Node
    total_recharges: int
    total_swaps: int
    total_occupied_time: float

    def recharge(self, agv, target_pct=1.0) -> ProcessGenerator: ...
    def swap(self, agv) -> ProcessGenerator: ...  # requires supports_swap=True
```

### ParkingArea

```python
class ParkingArea:
    def __init__(self, *, env, name: str, node: Node, capacity: int) -> None: ...

    node: Node
    available_capacity: int  # property

    def enter(self, agv) -> ProcessGenerator: ...
    def leave(self, agv) -> None: ...
```

## Orders

### OrderStatus

```python
class OrderStatus(Enum):
    PENDING = auto()
    DISPATCHED = auto()
    PICKING = auto()
    IN_TRANSIT = auto()
    DELIVERING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    PENDING_ACTIVATION = auto()   # submitted before the environment activated
```

### TransferOrder

```python
@dataclass
class TransferOrder:
    sku: SKU
    quantity: int
    origin: Warehouse
    destination: Warehouse
    created_at: float
    id: str = field(default=None, init=False)   # None until attached; then "order-<n>"
    due_date: float | None = None
    priority: float = 0.0
    status: OrderStatus = OrderStatus.PENDING

    # Set by FleetCoordinator during mission
    dispatched_at: float | None = None
    picked_at: float | None = None
    delivered_at: float | None = None
    assigned_agv: AGV | None = None
```

## Traffic

### ResourceBasedTrafficManager

```python
class ResourceBasedTrafficManager:
    def __init__(self, *, graph, env, node_capacity: int = 1,
                 deadlock_timeout: float = 30.0, priority_fn=None) -> None: ...
```

Creates a `simpy.Resource` per node. `check_path` rejects paths sharing
future nodes with other AGVs' intents — this effectively serializes
traffic on shared paths. Use only with layouts that have true parallel routes.

`place_now(agv, node)` (renamed from `place`) reserves the node for an AGV at activation and
requires an immediate grant; a conflict raises. `TrafficManager` is a runtime-checkable protocol.

### FreeTrafficManager

No-op. All paths are feasible, no resource acquisition. Default when
`traffic_manager` is omitted from `FleetCoordinator`.

## FleetCoordinator

```python
class FleetCoordinator:
    def __init__(
        self, *, env, graph: LayoutGraph, fleet: list[AGV],
        warehouses: list[Warehouse],
        charging_stations: list[ChargingStation],
        parking_areas: list[ParkingArea] | None = None,
        traffic_manager: TrafficManager | None = None,       # default: FreeTrafficManager
        path_planner: PathPlanner | None = None,              # default: DijkstraPlanner
        dispatch_strategy: DispatchStrategy | None = None,    # default: NearestIdleStrategy
        repositioning_policy: RepositioningPolicy | None = None,  # default: StayInPlace
        load_recovery_strategy: LoadRecoveryStrategy | None = None, # default: ReturnToOrigin
        default_metrics: bool = True,                         # attach OrderEMACollector as self.metrics
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
        max_dispatch_retries: int = 10,
        pending_retry_delay: float = 1.0,
        name: str | None = None,                              # entity id (default fleet-<n>)
        label: str | None = None,
    ) -> None: ...

    # Order management
    def create_order(self, *, sku, quantity, origin, destination, **kwargs) -> TransferOrder: ...
    # Attaches the order at once (id "order-<n>"); there is no id= argument.
    def submit(self, order) -> None: ...   # deferred until activation if called before env.run()/activate()
    def cancel(self, order) -> None: ...   # likewise; deferred orders report PENDING_ACTIVATION

    # Replenishment
    def add_replenishment_policy(self, policy, warehouse, check_interval=None) -> None: ...

    # Fleet info
    fleet_utilization: float                          # property, average across fleet
    def fleet_time_allocation(self) -> dict[AGVState, float]: ...
    def agv_report(self) -> list[dict[str, object]]: ...

    # Lifecycle hooks
    def on_order_submitted(self, callback) -> None: ...
    def on_order_dispatched(self, callback) -> None: ...
    def on_pickup_complete(self, callback) -> None: ...
    def on_delivery_complete(self, callback) -> None: ...
    def on_battery_low(self, callback) -> None: ...
    def on_charging_started(self, callback) -> None: ...
    def on_charging_complete(self, callback) -> None: ...
    def on_agv_idle(self, callback) -> None: ...
    def on_cargo_dropped(self, callback) -> None: ...
```

## Policies

### Dispatch

```python
class NearestIdleStrategy:
    def select(self, order, fleet, graph) -> AGV | None: ...
    # Closest idle AGV by graph path distance. Tie-breaks by agv_id.

class RoundRobinStrategy:
    def select(self, order, fleet, graph) -> AGV | None: ...
    # Cycles through idle AGVs via internal cursor.
```

### Repositioning

```python
class StayInPlace:
    def reposition(self, agv, context) -> Node | None: ...
    # Returns None (AGV stays).

class NearestParkingPolicy:
    def reposition(self, agv, context) -> Node | None: ...
    # Returns nearest parking area node with available capacity.
```

### Replenishment

```python
class ReorderPointPolicy:
    def __init__(self, thresholds: dict[SKU, int], reorder_quantity: dict[SKU, int]) -> None: ...
    def check(self, warehouse, all_warehouses, in_transit_orders) -> list[TransferOrder]: ...
    # Returns orders for SKUs below threshold (adjusted for in-transit).
    # Source: warehouse with highest stock (excluding monitored one).
```

### Load Recovery

```python
class ReturnToOrigin:
    def recover(self, order, agv, coordinator) -> ProcessGenerator: ...
    # Sets order to PENDING, coordinator returns cargo physically.

class ResumeDelivery:
    def recover(self, order, agv, coordinator) -> ProcessGenerator: ...
    # Sets order to IN_TRANSIT, coordinator re-attempts delivery.
```

## Metrics

Collectors replace the 0.12 `OrderMetricsCollector`, `EMAOrderMetrics`,
`IntralogisticsTimeSeriesCollector` and `DefaultIntralogisticsCollector`. Each is bound to a
fleet coordinator and attached with `collector.attach(env)` (before the run); `env.collectors`
lists them.

### OrderEMACollector

```python
class OrderEMACollector(Collector):
    def __init__(self, fleet: FleetCoordinator, alpha: float = 0.01) -> None: ...

    ema_fulfillment_time: float | None   # created_at -> delivered_at
    ema_dispatch_delay: float | None     # created_at -> dispatched_at
    ema_travel_time_empty: float | None  # dispatched_at -> picked_at
    ema_travel_time_loaded: float | None # picked_at -> delivered_at
    ema_late_orders: float | None        # fraction (0-1)
```

First observation initializes EMA directly (no bias toward 0). Every coordinator attaches
one as `coordinator.metrics` unless `default_metrics=False`. It declares no KPIs, so it is not
part of the fingerprint.

### FleetTimeSeries

```python
class FleetTimeSeries(Collector):
    def __init__(self, fleet: FleetCoordinator) -> None: ...

    fleet_utilization_ts: list[tuple[float, float]]
    pending_orders_ts: list[tuple[float, int]]
    throughput_ts: list[tuple[float, int]]
    inventory_ts: dict[str, list[tuple[float, dict[str, float]]]]   # warehouse id -> (t, {sku id: level})

    def plot_fleet_utilization(self) -> None: ...
    def plot_pending_orders(self) -> None: ...
    def plot_throughput(self) -> None: ...
    def plot_inventory(self) -> None: ...
```

Built from `agv.state_changed`, `order.status_changed` and `fleet.pending_changed` events.

### FleetKPIs

`FleetKPIs(fleet)` computes window-aware KPIs (warm-up from `env.configure_kpis`): order means
`fulfillment_time`, `dispatch_delay`, `travel_time_empty`, `travel_time_loaded`, `late_fraction`,
`throughput`, and the time-weighted `utilization` and `pending_orders`. Keys are
`"<fleet id>/<kpi>"`; they appear in `env.fingerprint().kpis`.

## Builder

### build_simple_system

```python
def build_simple_system(
    env,
    n_agvs: int = 2,
    agv_max_speed: float = 2.0,
    agv_acceleration: float = 1.0,
    agv_battery_capacity: float = 100.0,
    agv_weight_capacity: float = 500.0,
    agv_volume_capacity: float = 10.0,
    products: list[SKU] | None = None,
    initial_inventory_a: dict[SKU, int] | None = None,
    initial_inventory_b: dict[SKU, int] | None = None,
    prefix: str = "",                    # entity id prefix, to host several systems in one env
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, LayoutGraph]: ...
```

Creates a 5-node linear graph (WH_A → N1 → N2 → N3 → WH_B), two
warehouses, a charging station at N2, and *n_agvs* AGVs starting at N1.
