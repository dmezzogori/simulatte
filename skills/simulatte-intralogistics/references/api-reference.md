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
    def nodes(self) -> frozenset[Node]: ...
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
        pick_time_fn: Callable[[SKU, int], float],
        put_time_fn: Callable[[SKU, int], float],
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
    load_time_fn: Callable[[], float] = lambda: 0.0
    unload_time_fn: Callable[[], float] = lambda: 0.0
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
                 recharge_fn=None, supports_swap=False, swap_pool_size=0,
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
    id: str = field(default_factory=uuid4)
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
        order_metrics_collector: OrderMetricsCollector | None = None, # default: EMAOrderMetrics
        time_series_collector: IntralogisticsTimeSeriesCollector | None = None,
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
        max_dispatch_retries: int = 10,
        pending_retry_delay: float = 1.0,
    ) -> None: ...

    # Order management
    def create_order(self, *, sku, quantity, origin, destination, **kwargs) -> TransferOrder: ...
    def submit(self, order) -> None: ...
    def cancel(self, order) -> None: ...

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

### EMAOrderMetrics

```python
@dataclass
class EMAOrderMetrics:
    alpha: float = 0.01

    ema_fulfillment_time: float | None   # created_at -> delivered_at
    ema_dispatch_delay: float | None     # created_at -> dispatched_at
    ema_travel_time_empty: float | None  # dispatched_at -> picked_at
    ema_travel_time_loaded: float | None # picked_at -> delivered_at
    ema_late_orders: float | None        # fraction (0-1)

    def record(self, order: TransferOrder) -> None: ...
```

First observation initializes EMA directly (no bias toward 0).

### DefaultIntralogisticsCollector

```python
@dataclass
class DefaultIntralogisticsCollector:
    fleet_utilization_ts: list[tuple[float, float]]
    pending_orders_ts: list[tuple[float, int]]
    throughput_ts: list[tuple[float, int]]
    inventory_ts: dict[Warehouse, list[tuple[float, dict[SKU, float]]]]

    def plot_fleet_utilization(self) -> None: ...
    def plot_pending_orders(self) -> None: ...
    def plot_throughput(self) -> None: ...
    def plot_inventory(self) -> None: ...

    # Protocol methods (called by FleetCoordinator automatically):
    def on_order_submitted(self, coordinator, order) -> None: ...
    def on_order_dispatched(self, coordinator, order, agv) -> None: ...
    def on_pickup_complete(self, coordinator, order, agv) -> None: ...
    def on_delivery_complete(self, coordinator, order, agv) -> None: ...
    def on_agv_state_changed(self, coordinator, agv, old, new) -> None: ...
```

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
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, LayoutGraph]: ...
```

Creates a 5-node linear graph (WH_A → N1 → N2 → N3 → WH_B), two
warehouses, a charging station at N2, and *n_agvs* AGVs starting at N1.
