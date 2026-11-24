# Intralogistics API Reference

Import everything from `simulatte.intralogistics`; the layout and its declarations (`Explicit`, `Lattice`, `Lanes`) come
from `simulatte.layout`.

Every environment has a layout, `env.layout`. A transport network is declared on it once, before activation, and
components take handles of it, never `Node` or `LayoutGraph` objects (a concrete one raises `LayoutError`, code
`foreign-handle`). Physical fields (`agv.current_node`, `fleet.graph`, `controller.graph`, bays, `station.node`,
`area.node`) raise `LayoutNotReady` until `env.layout.prepare()` or `env.activate()`; they hold canonical nodes, equal
to the declared ones and different objects (compare with `==`).

## Spatial Layer

### Declaring the network and handles

```python
layout = env.layout  # created on first read
layout.set_network(Explicit(graph, capacities={"N1": 2}, zones={"z": {"capacity": 1, "members": ["N1", ("N1", "N2")]}}))
# Lattice(...) and Lanes(...) are the other two declarations; a second call before preparation replaces the first.

layout.graph()  # GraphRef: FleetCoordinator(graph=), ReservationController(graph=)
layout.node("N1")  # NodeRef of an explicit node; layout.node_at(Address(...)) for generated networks
layout.port(entity, "out")  # PortRef of a named port
```

`Explicit(graph, *, clearance=0, blocked=None, capacity=None, capacities=None, block_length=None, zones=None)` keeps each
arc's direction, `speed_limit`, `passing` and `length`. Zone members are node ids or pairs of ids.

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
    speed_limit: float | None = None  # positive and finite when given
    length: float | None = None  # travelled length; None: the chord; never shorter than the chord
    passing: bool = False  # opposite traffic may pass on this arc (ReservationController)
```

### LayoutGraph

```python
class LayoutGraph:
    def __init__(self, nodes: Iterable[Node], arcs: Iterable[Arc]) -> None: ...

    @property
    def nodes(self) -> tuple[Node, ...]: ...  # insertion order
    def neighbors(self, node: Node) -> list[Node]: ...
    def arc_between(self, source: Node, target: Node) -> Arc | None: ...
    def distance(self, source: Node, target: Node) -> float: ...  # chord, requires arc
    def length(self, source: Node, target: Node) -> float: ...  # travelled arc length, requires arc
    def path_length(self, path) -> float: ...  # sum of arc lengths
    @staticmethod
    def path_distance(path: list[Node]) -> float: ...  # sum of chords
    def shortest_path(self, source: Node, target: Node) -> list[Node] | None: ...  # Dijkstra by arc length
    def subdivide(self, max_length: float) -> LayoutGraph: ...  # split long arcs; new nodes "<a>~<b>#<k>"
```

### Routing

```python
@dataclass(frozen=True)
class RouteRequest:
    agv: AGV | None
    origin: Node
    destination: Node
    load_weight: float = 0.0
    avoid: frozenset = frozenset()  # resources and nodes to avoid
    purpose: str = "travel"  # "travel", "make_way", "step_aside", "clear_node"


@dataclass(frozen=True)
class Route:
    nodes: tuple[Node, ...]  # a simple path
    cost: float  # in the router's own unit; compare only within one router


class Router(Protocol):
    def route(self, request: RouteRequest, view: TrafficView) -> Route | None: ...


ShortestDistanceRouter(algorithm="dijkstra")  # or "astar"; default router; cost = path length
FastestRouter()  # cost = travel time with the AGV's limits, speed limits, turn_time / corner_speed
CongestionAwareRouter(base=FastestRouter(), wait_weight=1.0, horizon=60.0)  # heuristic; base exactly one of the two
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
        self,
        *,
        env,
        name: str,
        input_bays: Sequence[NodeRef | PortRef],  # handles of the nodes where AGVs deliver
        output_bays: Sequence[NodeRef | PortRef],  # handles of the nodes where AGVs pick up
        n_slots: int,  # concurrent pick/put operations
        products: list[SKU],
        initial_inventory: dict[SKU, int] | None = None,
        pick_time: SamplerDescription | float | Callable[[SKU, int], float],  # streams "<name>/pick", "<name>/put"
        put_time: SamplerDescription
        | float
        | Callable[[SKU, int], float],  # number/description: managed; callable: opaque
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
    average_put_time: float  # property

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
    recharge_fn: Callable[[float, float], float] | None = None  # (current_level, target_level) -> time
    low_battery_threshold: float = 0.2  # fraction, triggers charging after mission
    critical_battery_threshold: float = 0.05  # fraction, triggers mid-trip charging
    load_time: SamplerDescription | float | Callable[[], float] = 0.0  # bound per AGV: "<agv id>/load"
    unload_time: SamplerDescription | float | Callable[[], float] = 0.0  # bound per AGV: "<agv id>/unload"
    length: float | None = None  # required by ReservationController
    min_gap: float = 0.0  # extent = length + min_gap
    corner_speed: Callable[[float], float] | None = None  # heading change (rad) -> speed cap at the node
    turn_time: Callable[[float], float] | None = None  # heading change (rad) -> wait at rest before leaving
    idle_power: float = 0.0  # energy per unit time outside CHARGING, accounted lazily
```

`depletion_fn`, `corner_speed`, `turn_time` and a profile's `limits` must be pure: they are also called for
arcs and turns that are never travelled.

Default depletion: `distance * 1.0`. Default recharge: `(target - current) * 1.0`.

### AGV

```python
class AGV:
    def __init__(
        self, *, env, agv_type: AGVType, agv_id: str | None = None, initial_node: NodeRef | PortRef | None = None
    ) -> None: ...

    agv_id: str
    agv_type: AGVType
    current_node: Node | None  # after preparation; assign a canonical node of the resolved network only
    current_load: dict[SKU, int] | None
    battery: Battery
    state: AGVState  # property

    heading: float | None  # direction of the last arc traversed; None after placement

    def can_carry(self, sku: SKU, quantity: int) -> bool: ...  # checks weight AND volume AND compatibility
    def battery_level_now(self) -> float: ...  # level with unaccounted idle power taken off; changes nothing
    def utilization(self) -> float: ...  # fraction of time in utilized states
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
        load_speed_factor_fn: Callable[[float], float] | None = None,  # load_weight -> speed_factor
    ) -> None: ...

    def travel_time(self, distance, load_weight=0.0, battery_level=1.0, speed_limit=None) -> float: ...
    def limits(self, load_weight=0.0, battery_level=1.0) -> Kinematics: ...  # v_max, accel, decel
```

A profile with `limits` moves in runs and passes nodes at speed; one without is traversed rest to rest.

Default battery degradation: `lambda level: level` (proportional). Default load factor: `lambda _: 1.0` (no effect).

### Battery

```python
class Battery:
    def __init__(
        self,
        capacity,
        initial_level=None,
        depletion_fn=None,
        recharge_fn=None,
        low_threshold=0.2,
        critical_threshold=0.05,
    ) -> None: ...

    capacity: float
    level: float
    level_pct: float  # property, 0-1
    is_low: bool  # property, level_pct <= low_threshold
    is_critical: bool  # property, level_pct <= critical_threshold

    def estimate_energy(self, distance, load_weight, speed) -> float: ...
    def deplete(self, distance, load_weight, speed) -> None: ...
    def recharge(self, amount) -> None: ...
    def recharge_time(self, target_pct=1.0) -> float: ...
```

## Facilities

### ChargingStation

```python
class ChargingStation:
    def __init__(
        self,
        *,
        env,
        name: str,
        node: NodeRef | PortRef,
        n_slots: int,
        recharge_time=None,  # number | description | (current_level, target_level) -> time; stream "<name>/recharge"
        supports_swap=False,
        swap_pool_size=0,
        swap_time=0.0,
        swap_recharge_time=0.0,
    ) -> None: ...

    node: Node  # after preparation, read-only
    total_recharges: int
    total_swaps: int
    total_occupied_time: float

    def recharge(self, agv, target_pct=1.0) -> ProcessGenerator: ...
    def swap(self, agv) -> ProcessGenerator: ...  # requires supports_swap=True
```

### ParkingArea

```python
class ParkingArea:
    def __init__(self, *, env, name: str, node: NodeRef | PortRef, capacity: int) -> None: ...

    node: Node  # after preparation, read-only
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
    PENDING_ACTIVATION = auto()  # submitted before the environment activated
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
    id: str = field(default=None, init=False)  # None until attached; then "order-<n>"
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

### FreeFlowController

Grants every claim at once; no entities, no traffic events. Default when `traffic_controller` is omitted from
`FleetCoordinator`. One instance serves the network of one environment.

### ReservationController

```python
class ReservationController:
    def __init__(
        self,
        *,
        env,
        graph: GraphRef,  # layout.graph()
        node_capacity: int = 1,  # fallback capacity; capacities and zones are declared with the network
        derive_corridors: bool = True,
        hold_back_dead_ends: bool = True,
        policy: TrafficPolicy | None = None,  # default StandardTrafficPolicy()
        priority_fn: Callable[[AGV], float] | None = None,  # higher first
    ) -> None: ...
```

Claims nodes, segments (`"<a>~<b>"`), zones and derived corridors (`"corridor:<node>"`) all or nothing. Node
capacity: declared on the network, else the facilities on the node, else `node_capacity`. Fleets register before activation;
every vehicle type needs `length`. Placing more AGVs on a node than its capacity raises `RuntimeError` at
activation. Emits `traffic.reserved`, `traffic.released`, `traffic.wait_started`, `traffic.wait_ended`,
`traffic.wait_stuck` and `traffic.deadlock`.

```python
# The value a declared zone resolves to (read it from `traffic.network.zones`); declare zones with the network.
Zone(id: str, members: tuple[Node | tuple[Node, Node], ...], capacity: int = 1, hold_back: bool = False)

StandardTrafficPolicy(*, reroute_after=30.0, max_detour=1.5, max_resolutions=8)


class TrafficPolicy(Protocol):
    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]: ...
    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]: ...
    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None: ...


# Reactions
KeepWaiting(review_after=None)
Reroute(avoid=frozenset(), max_cost_ratio=None)
StepAside(target=None)
GiveUp()
```

### TrafficKPIs

`TrafficKPIs(coordinator).attach(env)`: `blocked_time`, `waits`, `stuck_waits`, `deadlocks_resolved`,
`deadlocks_unresolved`, `reroutes`, `make_way_moves`, `requeued_orders`, keyed `"<fleet id>/<kpi>"`; empty under
`FreeFlowController`.

## FleetCoordinator

```python
class FleetCoordinator:
    def __init__(
        self,
        *,
        env,
        graph: GraphRef,  # layout.graph()
        fleet: list[AGV],
        warehouses: list[Warehouse],
        charging_stations: list[ChargingStation],
        parking_areas: list[ParkingArea] | None = None,
        traffic_controller: TrafficController | None = None,  # default: FreeFlowController()
        router: Router | None = None,  # default: ShortestDistanceRouter()
        dispatch_strategy: DispatchStrategy | None = None,  # default: NearestIdleStrategy
        repositioning_policy: RepositioningPolicy | None = None,  # default: StayInPlace
        load_recovery_strategy: LoadRecoveryStrategy | None = None,  # default: ReturnToOrigin
        clear_service_nodes: bool = True,  # idle AGVs move off bays and charger nodes under traffic control
        max_traffic_requeues: int = 3,  # per order, when an AGV gives up in traffic before pickup
        lookahead_margin: float = 0.0,  # claims held ahead beyond the braking distance
        default_metrics: bool = True,  # attach OrderEMACollector as self.metrics
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
        max_dispatch_retries: int = 10,
        pending_retry_delay: float = 1.0,
        name: str | None = None,  # entity id (default fleet-<n>)
        label: str | None = None,
    ) -> None: ...

    # Order management
    def create_order(self, *, sku, quantity, origin, destination, **kwargs) -> TransferOrder: ...
    # Attaches the order at once (id "order-<n>"); there is no id= argument.
    def submit(self, order) -> None: ...  # deferred until activation if called before env.run()/activate()
    def cancel(self, order) -> None: ...  # likewise; deferred orders report PENDING_ACTIVATION

    # Replenishment
    def add_replenishment_policy(self, policy, warehouse, check_interval=None) -> None: ...

    # Fleet info
    fleet_utilization: float  # property, average across fleet

    def fleet_time_allocation(self) -> dict[AGVState, float]: ...
    def agv_report(self) -> list[dict[str, object]]: ...  # battery_pct is the level now
    def best_bay(self, agv, bays, *, load_weight=0.0) -> Node: ...  # least router cost, first among equals
    def make_way(self, agv, target=None, *, concerned=()) -> bool: ...  # move an idle AGV out of the way

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
@dataclass(frozen=True)
class DispatchContext:
    graph: LayoutGraph
    router: Router
    view: TrafficView

    def cost(self, agv, node, *, load_weight=0.0) -> float: ...  # router cost; inf without a route


class NearestIdleStrategy:
    def select(self, order, fleet, context: DispatchContext) -> AGV | None: ...

    # Idle AGV with the least router cost to an output bay of the origin. Tie-breaks by agv_id.


class RoundRobinStrategy:
    def select(self, order, fleet, context: DispatchContext) -> AGV | None: ...

    # Cycles through idle AGVs via internal cursor.
```

### Repositioning

```python
class StayInPlace:
    def reposition(self, agv, context) -> Node | None: ...

    # Returns None (AGV stays).


class NearestParkingPolicy:
    def reposition(self, agv, context) -> Node | None: ...

    # Returns the node of the parking area with available capacity at the least context.cost
    # (first area among equal costs). RepositioningContext has graph, parking_areas,
    # charging_stations, fleet, router, view and cost(agv, node).
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

    ema_fulfillment_time: float | None  # created_at -> delivered_at
    ema_dispatch_delay: float | None  # created_at -> dispatched_at
    ema_travel_time_empty: float | None  # dispatched_at -> picked_at
    ema_travel_time_loaded: float | None  # picked_at -> delivered_at
    ema_late_orders: float | None  # fraction (0-1)
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
    inventory_ts: dict[str, list[tuple[float, dict[str, float]]]]  # warehouse id -> (t, {sku id: level})

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
    prefix: str = "",  # entity id prefix, to host several systems in one env
    layout=None,  # default env.layout
    nodes=None,  # role -> NodeRef | PortRef, required when the layout already has a network
    traffic_controller=None,
    router=None,
    agv_length: float | None = None,  # required by ReservationController
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, GraphRef]: ...
```

Builds on `env.layout`. With no declared network it declares a 5-node linear graph (WH_A → N1 → N2 → N3 → WH_B);
with one, `nodes` maps the roles `WH_A_OUT`, `N1`, `N2`, `N3` and `WH_B_IN` to handles. Two warehouses, a charging
station at N2, and *n_agvs* AGVs starting at N1, named `vehicle-<i>` (`<prefix>agv-<i>` with a prefix).
