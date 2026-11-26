# Intralogistics

For generated lattices, lanes, ports and handles, see the [layout guide](../guides/layout.md).

Simulatte's intralogistics subpackage models warehouse-to-warehouse material transport using AGV (Automated Guided Vehicle) fleets. It provides a complete toolkit for building, running, and analyzing intralogistics simulations.

Import everything from a single namespace:

```python
from simulatte.intralogistics import (
    Node,
    Arc,
    LayoutGraph,  # spatial layout
    SKU,
    Warehouse,  # products and storage
    AGV,
    AGVType,
    TrapezoidalProfile,  # vehicle fleet
    FleetCoordinator,
    TransferOrder,  # orchestration
    ChargingStation,
    ParkingArea,  # facilities
    FreeFlowController,  # traffic control (default)
    ReservationController,
    StandardTrafficPolicy,
    Zone,
    ShortestDistanceRouter,  # routing (default)
    FastestRouter,
    CongestionAwareRouter,
    NearestIdleStrategy,  # dispatch policies
    RoundRobinStrategy,
    NearestParkingPolicy,  # repositioning
    ReorderPointPolicy,  # replenishment
    ReturnToOrigin,  # load recovery
    OrderEMACollector,  # order-level metrics
    FleetTimeSeries,  # time-series + plots
    FleetKPIs,  # window-aware KPIs
    TrafficKPIs,  # waits, deadlocks, reroutes
)
```

`simulatte.intralogistics` loads its names on first use. Import the names you need, as above, or a submodule such as `simulatte.intralogistics.graph`. After a bare `import simulatte.intralogistics` its submodules are not yet attributes, and its event types are registered only when the first name is used.

## Core concepts

**Layout** --- Every environment has a layout, `env.layout`, and the transport network is declared on it with `env.layout.set_network(...)`: a `Lattice`, `Lanes`, or `Explicit(graph)` for a directed graph of `Node` and `Arc` objects that you write by hand. Components never take the graph or its nodes themselves: they take handles of the layout (`layout.graph()`, `layout.node(id)`, `layout.node_at(address)`, `layout.port(entity, name)`), which the layout resolves when it is prepared. A concrete `LayoutGraph` or `Node` raises `LayoutError`. After `env.layout.prepare()` or `env.activate()`, `agv.current_node`, `fleet.graph` and the other physical fields hold the resolved objects. Resolved nodes are equal to the ones you declared and are different objects: compare nodes with `==`. The resolved graph is not equal to the declared one, and its arcs are one-way: a two-way arc is two arcs with `bidirectional` false, so test for the way back with `fleet.graph.arc_between(v, u) is not None`. Before that, reading one raises `LayoutNotReady`. In an explicit graph, nodes have (x, y) coordinates; arcs can be one-way or bidirectional, carry an optional `speed_limit`, a travelled `length` (the chord between the endpoints when omitted; never shorter than it) and a `passing` flag (opposite traffic may pass on the arc). `LayoutGraph.length(u, v)` and `path_length(path)` use the travelled length; `distance` and `path_distance` keep the chord.

**Warehouses** --- Each `Warehouse` has input/output bays (layout node handles), finite pick/put slots, and per-SKU inventory tracked as SimPy containers.

**AGV fleet** --- `AGV` instances are typed by `AGVType`, which bundles a `SpeedProfile`, battery parameters, capacity limits, the vehicle's `length` and `min_gap`, and optional turning and idle-power models. `TrapezoidalProfile` models acceleration, cruising, and deceleration with optional battery degradation and load speed factors; it reports its kinematic limits, so its vehicles pass nodes at speed (see [Motion](#motion)). Without a `battery_degradation_fn` the speed factor equals the battery level, so maximum speed and acceleration fall in proportion to the charge (deceleration is unscaled); pass `lambda level: 1.0` for full speed at any level.

**FleetCoordinator** --- The central orchestrator (analogous to `ShopFloor` for production). It manages the full mission lifecycle: dispatch, travel, pick, transit, deliver, reposition, and charge. Pluggable strategies control each decision point. Its `traffic_controller` decides which vehicles may move (default `FreeFlowController`, see [Traffic control](#traffic-control)) and its `router` chooses their routes (default `ShortestDistanceRouter`, see [Routing](#routing)).

**Policies** --- Dispatch (`NearestIdleStrategy`, `RoundRobinStrategy`), repositioning (`NearestParkingPolicy`, `StayInPlace`), replenishment (`ReorderPointPolicy`), and load recovery (`ReturnToOrigin`, `ResumeDelivery`).

**Metrics** --- Collectors on the event bus, bound to a fleet coordinator and attached with `collector.attach(env)`. `OrderEMACollector` tracks exponential moving averages of fulfillment time, dispatch delay, travel times and late orders; every `FleetCoordinator` attaches one as `coordinator.metrics` unless built with `default_metrics=False`. `FleetTimeSeries(coordinator)` records time-series data (inventory keyed by warehouse id and SKU id) and provides plot methods: `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`. `FleetKPIs(coordinator)` computes window-aware KPIs, keyed `"<fleet id>/<kpi>"`. `TrafficKPIs(coordinator)` counts the fleet's traffic waits, deadlocks, reroutes and make-way moves (see [Traffic KPIs](#traffic-kpis)).

## Traffic control

### The two controllers

A traffic controller grants each AGV the right to move. A travel asks it for a claim before entering the arcs ahead; the coordinator also tells it where the AGVs start and when one becomes idle.

- `FreeFlowController` (the default) grants everything at once. Vehicles never wait for one another and can share nodes and lanes; it creates no entities and emits no traffic events. Use it when traffic is not what the study is about: travel times are those of a vehicle alone on the layout. One controller serves one environment: registering a fleet of another environment raises `ValueError`, and fleets of different environments each take their own controller, as they do by default.
- `ReservationController` enforces occupancy. An AGV moves only on resources it holds, so two vehicles never share a node beyond its capacity and never meet head-on on a lane. Use it when congestion, blocking and waiting are part of what you measure. It requires every vehicle type of its fleets to have a `length` (`register_fleet` raises `ValueError` naming the type otherwise).

```python
ReservationController(
    env=env,
    graph=env.layout.graph(),
    node_capacity=1,  # default capacity of a node
    derive_corridors=True,
    hold_back_dead_ends=True,
    policy=None,  # default StandardTrafficPolicy()
    priority_fn=None,  # agv -> float, higher first; default 0
)
```

Fleets register in their constructor and, on a reservation controller, must do so before the environment is activated (a later one raises `RuntimeError`; under `FreeFlowController` a fleet may be built after activation); the network is built by an activation initializer of the controller. A fleet on a reservation controller is passed as `FleetCoordinator(traffic_controller=controller)`. Several fleets share one controller, because an environment has one network. Assigning `AGV.current_node` directly after activation bypasses traffic.

### The traffic network

The controller derives its resources from the layout's network when the environment is activated. Node capacities and zones belong to the network, so they are declared with it (`capacity`, `capacities` and `zones` of `Explicit`, `capacity` and `zones` of `Lattice` and `Lanes`, the `capacity` of a network override; see the [layout guide](layout.md#traffic-fields-schema-11)); the controller has no arguments for them.

- **Nodes.** A node's capacity is the first of: the `capacity` of a network override of the node; the node's own capacity (`Explicit(capacities=)`, or the `capacity` of an explicit node in a layout file); the network's `capacity` (of `Explicit`, `Lattice` or `Lanes`); the sum of the capacities of the facilities on the node (a `ParkingArea`'s capacity, a `ChargingStation`'s slot count); `node_capacity`. A declared capacity smaller than the facility sum, the network's included, is logged as a warning at activation: `Explicit(graph, capacity=3)` gives capacity 3 to a node whose facilities sum to 5.
- **Segments.** One per pair of adjacent nodes, shared by both directions, with the arc's length. Its id is the two node ids in sorted order joined by `~` (`"C1~J"`). A segment is held with a direction. On a single-lane segment every holder has the same direction, and the extents of the holders (see below) must fit in its length. A vehicle that claims a single-lane segment again in the other direction while its body still lies on it (it reverses) holds it in both directions: no other vehicle is admitted onto the segment, in either direction, until the body as it arrived has left it (the vehicle starts its own leg over the segment, it has travelled far enough for its tail to clear the segment, which for a speed profile without kinematic limits counts at the arrival that ends that arc, or it comes to rest on a node of capacity above 1) or the segment is released. A vehicle waiting for the segment then names the reversing one as its blocker. On a segment whose arc has `passing=True` the test counts only the holders in the same direction, so opposite traffic may be on it at once.
- **Zones.** A zone, declared as `zones={id: {"capacity": 1, "hold_back": False, "members": [...]}}` with the network, is a set of nodes and node pairs (segments) of which at most `capacity` AGVs may hold any member. In `Explicit`, a member is a node id (`"J"`) or a pair of ids (`("X", "J")`), the segment between the two nodes; the controller's zones are `Zone(id, members, capacity, hold_back)` values over the resolved nodes, ordered by zone id, and the `zone` entity lists its members as nodes in id order, then segments. An AGV holds the zone while it holds any member. With `hold_back=True` the zone is claimed one leg before its first member, so that an AGV that cannot enter waits outside it.
- **Corridors.** Derived when `derive_corridors` is true: a maximal chain of at least two two-way, non-passing segments whose interior nodes have exactly two neighbours and capacity 1, between two **ports**, nodes with three or more neighbours or capacity above 1. Its id is `"corridor:<smallest interior node id>"`. Vehicles in a corridor all travel in the same direction: an AGV going the other way waits at the port until it is empty of oncoming traffic. A cycle with no port is not a corridor; the controller logs it once at activation.
- **Dead-end branches.** From every leaf (a node with one neighbour), whatever its capacity, the controller walks through nodes with two neighbours and capacity 1 to the first node with three or more neighbours or capacity above 1, the **root**. The walked nodes form a branch. An AGV that enters a branch claims everything down to the end of its route in it before it leaves the root, so a vehicle that cannot get in waits where it does not block the way out. `hold_back_dead_ends=False` turns the rule off. A line whose two ends are both leaves of capacity 1 has no passing place and no branch.

For example:

```
                PARK  parking area, capacity 2
                  |
    BAY ---- X ---- J ---- C1 ---- C2 ---- K ---- OUT
                                           capacity 2
```

Here `J~C1`, `C1~C2` and `C2~K` with the interior nodes `C1` and `C2` form `corridor:C1`, between the ports `J` (three neighbours) and `K` (capacity 2). `BAY` and `X` form a branch with root `J`; `PARK` and `OUT` are branches of one node, with roots `J` and `K`. For an AGV at `C2` travelling to `BAY`, the claim for the leg from `C1` to `J` also covers the legs to `X` and `BAY`, because `J` is the root of their branch: the AGV does not leave the corridor until the branch is free.

The same layout with two carts, the parking area, an explicit capacity on `K` and a zone that admits one vehicle at a time around `J`:

```python
from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FleetCoordinator,
    LayoutGraph,
    NearestParkingPolicy,
    Node,
    ParkingArea,
    ReservationController,
    SKU,
    TrafficKPIs,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.layout import Explicit

with Environment() as env:
    bay, x, j, park = Node("BAY", 0, 0), Node("X", 10, 0), Node("J", 20, 0), Node("PARK", 20, 10)
    c1, c2, k, out = Node("C1", 30, 0), Node("C2", 40, 0), Node("K", 50, 0), Node("OUT", 60, 0)
    graph = LayoutGraph(
        [bay, x, j, c1, c2, k, out, park],
        [Arc(bay, x), Arc(x, j), Arc(j, c1), Arc(c1, c2), Arc(c2, k), Arc(k, out), Arc(j, park)],
    )
    layout = env.layout
    layout.set_network(
        Explicit(
            graph,
            capacities={"K": 2},  # K gets capacity 2
            zones={"junction": {"capacity": 1, "members": ["J", ("X", "J"), ("J", "C1"), ("J", "PARK")]}},
        )
    )
    box = SKU("box", weight=1.0, volume=0.1)
    store = Warehouse(
        env=env,
        name="Store",
        input_bays=[layout.node("BAY")],
        output_bays=[layout.node("BAY")],
        n_slots=1,
        products=[box],
        initial_inventory={box: 10},
        pick_time=5.0,
        put_time=5.0,
    )
    line = Warehouse(
        env=env,
        name="Line",
        input_bays=[layout.node("OUT")],
        output_bays=[layout.node("OUT")],
        n_slots=1,
        products=[box],
        pick_time=5.0,
        put_time=5.0,
    )
    cart = AGVType(
        name="cart",
        speed_profile=TrapezoidalProfile(max_speed=1.5, acceleration=0.5, deceleration=0.5),
        battery_capacity=1000.0,
        weight_capacity=10.0,
        volume_capacity=1.0,
        length=1.2,  # required by ReservationController
        min_gap=0.3,  # extent 1.5
    )
    agvs = [AGV(env=env, agv_type=cart, initial_node=layout.node("PARK")) for _ in range(2)]
    parking = ParkingArea(env=env, name="Parking", node=layout.node("PARK"), capacity=2)  # PARK gets capacity 2
    traffic = ReservationController(env=env, graph=layout.graph())
    fleet = FleetCoordinator(
        env=env,
        graph=layout.graph(),
        fleet=agvs,
        warehouses=[store, line],
        charging_stations=[],
        parking_areas=[parking],
        traffic_controller=traffic,
        repositioning_policy=NearestParkingPolicy(),
    )
    kpis = TrafficKPIs(fleet).attach(env)
    for _ in range(3):
        fleet.submit(fleet.create_order(sku=box, quantity=1, origin=store, destination=line))
    env.run()
    print(kpis.scalars())
```

The resources of a run appear in the trace: each segment is a `segment` entity (fields `source`, `target`, `length`, `forward`, `backward`), each zone and corridor a `zone` entity (`rule`, `capacity`, `members`, `reserved_by`, `orientation`), and nodes record their holders in `reserved_by`. These entities exist only under `ReservationController`.

### Vehicle length

`AGVType.length` is the length of the vehicle and `min_gap` (default 0) the distance it keeps to the vehicle ahead; the **extent** is `length + min_gap`. As the vehicle travels, the controller keeps every node and segment under its body held: a node until the front is one extent past it, a segment until the front is one extent past its far node. A vehicle that stops keeps what its body covers. With a length of 0 and no gap, a node is released the instant the AGV leaves it and a segment the instant it reaches the far node; a positive length makes vehicles hold more and follow each other at a distance.

When an AGV comes to rest on a node of capacity above 1 (a bay with several slots, a charger, a parking area, a passing place), its whole trail is released: such a node has room for whole vehicles. The model does not track a rigid body through a reversal: the body is taken to be the last extent of the path travelled.

### Claims, waiting and release

For each leg `u -> v` of its route an AGV needs the segment in its direction, the node `v`, and every zone and corridor containing them, less what it already holds. The dead-end, corridor and hold-back rules above can extend a claim over several legs. A claim is granted all at once or not at all. Pending claims are queued by priority (`priority_fn`, higher first) and then by request order, and after every request, release and withdrawal, and when a reversing vehicle's body has left a segment that a pending claim needs, the controller grants every claim that has become admissible. A fairness rule stops a stream of vehicles in one direction from starving a vehicle waiting to go the other way through a corridor or segment.

An AGV requests ahead of itself (see [Look-ahead](#look-ahead)). When it reaches the last node it holds with its next claim still pending, it stops and waits: `traffic.wait_started` names the next node, the resources still needed and the vehicles that hold them. The wait ends with `traffic.wait_ended` and the reason `granted`, `rerouted`, `stepped_aside`, `gave_up` or `interrupted`. While it lasts, the AGV's `wait` state field holds the announcement (`since`, `node`, `resources`, `blockers`); `traffic.wait_ended` sets it back to null. In the same way the `route` field holds the route of the travel under way (`nodes`, `destination`, `reason`, from its last `agv.routed`) and is null once the travel ends. Both are kept whether or not anything observes the run. Resources are released as the tail of the vehicle clears them (`traffic.released`), and a granted resource is never taken back.

### The policy

While an AGV waits, the controller asks its `TrafficPolicy` what to do. The policy returns reactions, tried in order until one succeeds:

| Reaction | Succeeds when |
|---|---|
| `KeepWaiting(review_after=None)` | always; with a delay, the policy is asked again after it |
| `Reroute(avoid, max_cost_ratio=None)` | the fleet's router finds a route around `avoid` whose first claim is admissible now and, with a ratio, whose cost is at most that ratio times the cost of the route without `avoid` |
| `StepAside(target=None)` | the target, or the node of the standard search, can be reached with a first claim admissible now; the AGV then routes to its goal again |
| `GiveUp()` | the travel accepts to end (`GAVE_UP`); a travel with cargo on board never does |

`StandardTrafficPolicy(reroute_after=30.0, max_detour=1.5, max_resolutions=8)` is the default. When a wait starts it keeps waiting, or reroutes at once around a stranded blocker. At each review, every `reroute_after` time units, it tries a reroute around the blocked resources that costs at most `max_detour` times the remaining route, then keeps waiting. A travel stops being rerouted after `max_resolutions` reactions. A review is scheduled only while the network changes: a wait in a network where nothing moves gets at most one more review, so a run with a standing wait still ends.

A policy implements three methods: `on_blocked(wait, view)`, `on_deadlock(waits, view)` and `make_way_target(blocker, wait, view)`. `Wait` carries the AGV, its claim, the start time, the blockers (`Blocker` values with the AGV, its `Disposition` and the resources it blocks), the number of reviews, the remaining route, whether cargo is on board, the reactions staged for this travel (`resolutions`) and the priority. The `TrafficView` gives read-only access to the network: capacities, holders, pending claims, dispositions, routes and admissibility. The policy never moves anything itself. Subclassing the standard policy is the shortest way to change one decision:

```python
from simulatte.intralogistics import KeepWaiting, Reroute, StandardTrafficPolicy


class EagerReroute(StandardTrafficPolicy):
    """Look for a detour as soon as a wait starts, not after `reroute_after`."""

    def on_blocked(self, wait, view):
        if wait.reviews == 0 and wait.resolutions < self.max_resolutions:
            avoid = frozenset(resource for blocker in wait.blockers for resource in blocker.resources)
            return (Reroute(avoid, max_cost_ratio=self.max_detour), KeepWaiting(self.reroute_after))
        return super().on_blocked(wait, view)


traffic = ReservationController(env=env, graph=env.layout.graph(), policy=EagerReroute())
```

### Idle vehicles: make way, service nodes, parking

An idle AGV that stands in the way of a waiting one is asked to move: the controller takes the policy's `make_way_target`, or, when it returns None, the standard search, and calls `fleet.make_way(agv, target)`. The search looks outwards by router cost for the nearest node with a free slot that is not on the remaining route of the waiting vehicles, unless it would still have a free slot for each of them after the move; it prefers the cheapest parking area with a free slot, and avoids service nodes, roots of dead-end branches, nodes that other pending claims need and nodes inside a zone or corridor that is closed to the AGV now (a node with a free slot inside a full zone is no place to rest: the AGV would stop short of it, in the way). When nothing qualifies, a service node or root with room to spare is taken. The move is a short travel with purpose `make_way` (`agv.routed` with that reason). A target that the router cannot reach, or a parking area without a free slot, is refused: the AGV stays, even where the standard search would have found a node, and the wait can then be reported stuck. An AGV does not make way to, or start a make-way move from, a node it already made way from in the same chain of moves, which bounds a chain by the number of nodes; the chain ends when the AGV starts a mission or becomes idle where it blocks no wait. `TrafficController.idle(agv)` is how the controller is told that an AGV has come to rest without a travel where it stands, or cannot move: the coordinator calls it when the AGV becomes idle or strands, and the travel executor when a run stalls (a stalled AGV is not asked to make way; the waits it blocks are checked for the stuck condition at once). A stranded AGV is not asked to move; the waits it blocks are reviewed at once, so the policy can reroute around it (the standard policy does) and a wait that stays is reported stuck, even when the stranding released nothing.

**Service nodes** are the bays of the fleet's warehouses and the nodes of its charging stations. With `FleetCoordinator(clear_service_nodes=True)` (the default) and a controller that enforces occupancy, an AGV that would become idle on a service node, and that the repositioning policy sends nowhere, first moves off it to the standard-search node (`agv.routed` with reason `clear_node`). It stays when there is none, or when an order is pending.

**Parking.** When a repositioning, a service-node clearance or a make-way move targets the node of a parking area with a free slot, the coordinator takes a slot (`parking.entered`) at the decision and gives it back (`parking.left`) when the AGV next departs from that node or when the move ends without arriving: a move that turns to charge on the way gives the slot back as it turns, unless the charger is on the parking area's own node: there the AGV keeps the slot (it is given back if the AGV does not get there) and rests parked after the charge. A repositioning, a clearance or a make-way move that charges on its way and ends elsewhere than its target is not resumed. After the charge, unless an order is pending (then the AGV is idle where it charged and the pending-queue check runs), the AGV is asked once where to rest, as an AGV that ends a mission on the charger's node is: the repositioning policy is asked again and, where it names no node, the charger's node is cleared as any service node is. A trip of that decision that charges on its way again ends with the AGV idle where it charged. A full area is not a valid target: a custom repositioning policy that names one gets no move. This holds under both controllers. AGVs placed on a parking node at activation take no slot.

### Deadlocks and stuck waits

The controller looks for a deadlock each time an AGV starts to wait: a set of waiting AGVs none of which could proceed even if every vehicle outside the set released everything. Such a set is never a false positive. The policy's `on_deadlock` proposes reactions (the standard policy moves the least important member first: lowest priority, unloaded before loaded, latest to wait) and the controller stages the first that succeeds; `traffic.deadlock` reports the members, the victim and the action (`reroute`, `step_aside` or `give_up`). When no reaction succeeds, the action is `unresolved`: the controller logs an error once for that set and its members keep waiting; a member whose wait opened into the set gets no `on_blocked` call and no review while the set stands, unless a vehicle that blocks it strands, which reviews the waits it blocks. When a member stops waiting for another reason (its travel is interrupted, or a reaction at its own review moves it), the controller forgets the set and looks again at the waits left: it looks for a deadlock among them, which asks the policy again when one remains, and a member whose wait opened into the set and is in no deadlock now gets the `on_blocked` call and the reviews of an ordinary wait, from that instant.

A wait is **stuck** when every vehicle that blocks it is idle, stranded or stalled, or is itself waiting in a stuck wait (a cycle of such waits counts). This is not a deadlock: deadlock detection ignores vehicles that are not waiting, so two waiting AGVs that block each other only because an idle one holds a slot between them are not a deadlock. The controller emits `traffic.wait_stuck`, with the blockers and their dispositions, and logs a warning, once per wait: at a review that keeps the wait waiting, when a blocker comes to rest or stalls and does not move, and for the waits behind a wait just reported. A wait with no review scheduled (it sleeps, or its policy asks for no review) is checked whenever the controller is entered for something other than a grant or a release: when a wait opens, a claim is withdrawn, a review ends, or a vehicle comes to rest or stalls; a wait that a review already scheduled will reach is left to that review. A wait whose policy asks for no review that becomes stuck through a grant or a release (a blocker that moves on and frees what it held) is reported at the next of those occasions, not at that instant; a run cut by `until` in between ends without the report, and so does a run in which no such occasion follows: at natural quiescence that needs every vehicle still moving at that instant to end serving for ever (a pick waiting for stock). A stuck wait can still end if traffic changes. Neither report changes an order's status: standing waits leave their orders as they are, and `env.run()` without `until` still returns.

Under the standard policy, an AGV giving up a travel before pickup re-queues its order (reason `traffic_requeued`) at most `FleetCoordinator(max_traffic_requeues=3)` times per order.

### Designing a layout that avoids deadlocks

Standing deadlocks and stuck waits come from layouts with no place for one vehicle to let another by. Two vehicles that meet head-on on a single lane cannot pass, and an idle vehicle needs somewhere to go. In practice:

- **Give vehicles a place to pass.** A siding (two lanes between two junctions), or a lane authored with `passing=True` whose end nodes have room for two vehicles, lets opposite traffic pass. See the warning on capacity-defined passing places under [Known limits](#known-limits).
- **Give nodes room.** A capacity above 1 on junctions, bays and charger nodes lets a vehicle wait there without blocking the one that has to get past.
- **Give idle vehicles somewhere to go.** Parking areas off the main lanes are where make-way moves and repositioning send idle vehicles; without them, idle AGVs end up on lanes and in front of bays.
- **Use one-way loops** around bays and in busy areas, so that vehicles do not meet head-on at all.
- **Split long arcs into blocks.** A claim holds a whole segment, so on a long arc a follower waits until the leader has left it. `block_length` in the network declaration replaces every arc longer than it by equal pieces, with generated nodes named `"<a>~<b>#<k>"` (`a` and `b` the endpoint ids in sorted order, `k` counted from `a`; see the [layout guide](layout.md#traffic-fields-schema-11)). For a graph you write by hand, `LayoutGraph.subdivide(max_length)` does the same before you declare it with `Explicit`, with the same nodes and the same arc lengths to the float; it refuses a graph the layout built. With a profile that has kinematic limits, a straight line takes the same free-flow time however many nodes split it.

The [traffic example](../examples/intralogistics.md#traffic) runs a two-way corridor with one-way loops at the bays and a siding that doubles as the parking area.

### Known limits

- **Passing places defined by node capacity.** A vehicle that cannot enter a corridor or a dead-end branch waits one node before its port or root, also when the port or root has capacity above 1. Two vehicles that meet head-on at such a node therefore deadlock, and one steps aside, where they could have passed each other on the node. Authoring `passing` segments, or a siding, gives a passing place that works.
- **Make-way is not transitive.** An idle blocker whose only free neighbours are held by other idle vehicles stays, and the step-aside search excludes nodes ahead on the routes of the waits concerned even where a retreat exists. Small layouts without parking can therefore end with stuck waits and unresolved deadlocks. Parking areas, capacity above 1 and one-way loops avoid this.
- **The fairness rule lapses** when a vehicle that does not hold the corridor rests on a slot of the waiting vehicle's port.
- **Charging.** Two defects predate the traffic work and are unchanged: a vehicle whose battery is critical and whose charger is more than one arc away stops at the first node of each travel towards the charger and strands without charging; and when an arc of a mission cannot be afforded even with the level a full charge leaves on arrival at the node before it, the vehicle travels between the charger and that node without end, and `env.run()` without `until` does not return.
- **Late grants.** See [Look-ahead](#look-ahead).

## Motion

A vehicle whose speed profile has a `limits(load_weight, battery_level)` method (`TrapezoidalProfile` has one) moves in runs: from rest it accelerates, passes intermediate nodes at speed, and comes to rest only where it must. Its limits (`Kinematics(v_max, accel, decel)`) are sampled when it starts from rest and hold until it next stops; the speed is planned again at each node it reaches, and the profile of an arc is fixed when the vehicle enters it. Arc speed limits (`Arc.speed_limit`, positive and finite) cap the speed on their arc. Without kinematic limits a profile is traversed arc by arc, from rest to rest, with its `travel_time`. The `agv.move_started` motion of a run is a `piecewise` curve (phases of constant acceleration), which Studio plays back.

### Look-ahead

Under `ReservationController` a moving vehicle requests claims ahead until it holds the next arc plus its braking distance `v_max**2 / (2 * decel)` plus `FleetCoordinator(lookahead_margin=0.0)`. When every claim is granted at once, an AGV therefore has exactly the travel times it has under `FreeFlowController`. Holding the braking distance lowers the density of traffic on purpose; `lookahead_margin` adds to it.

Known limit: a claim granted late, after the vehicle has entered an arc whose profile already brakes for the end of its holdings, does not change that arc's profile. The vehicle finishes the arc as planned, at worst stopping at the node, and plans again there. The cost is at most one deceleration and acceleration per such node, and it shrinks with the block length.

### Turning

`AGVType.corner_speed` and `turn_time` take the heading change at a node in radians, in `[0, pi]`, measured between the chords of the arcs in and out. `corner_speed` caps the speed through the node (0 is a stop, infinity no cap; profiles with limits only). A positive `turn_time` stops the vehicle at the node and waits that long before it leaves. A vehicle's heading is that of the last arc it traversed (the `heading` field of its state) and is unknown after placement; an unknown heading turns for free. Both default to no effect. At rest the energy check for the next arc comes first, then the turn wait; a traffic wait counts only the time lost beyond the turn.

### Energy and idle power

Each arc depletes the battery on arrival by `depletion_fn(length, load_weight, average speed)`, with the travelled arc length. A run is planned only over arcs whose estimated energy fits the battery, and ends at the first node other than the goal where the estimated level turns critical, so that the critical-battery branch runs at rest there.

`AGVType.idle_power` (default 0) drains energy per unit time in every state except `CHARGING`. It is accounted lazily: the level is brought up to date at the events of the AGV that read or change the battery (`agv.battery_changed` when an accrual outside a move changes it), and no timer wakes an idle AGV. `agv.battery.level` is therefore the level as last accounted; `AGV.battery_level_now()` is the level now, without accounting anything. `agv_report()` reports the level now; custom dispatch strategies and `on_low_battery` callbacks that read the battery should use `battery_level_now()`. With a positive idle power the two controllers can end differently when idle power empties a battery during a run: under reservation the AGV comes to rest at the end of its claims and retries, under free flow it arrives with an empty battery, because the energy estimate of a run excludes idle power.

### Pure functions

The `limits` method of a speed profile, `AGVType.depletion_fn`, `corner_speed` and `turn_time` must be pure: the same arguments give the same result and nothing else changes. The planner, the energy estimates and `FastestRouter` call them for arcs and turns that are never travelled, and the executor reuses their results within a run.

## Routing

A router answers a `RouteRequest(agv, origin, destination, load_weight, avoid=frozenset(), purpose="travel")` with a `Route(nodes, cost)`, a simple path and its cost, or None. Every route assignment emits `agv.routed` with the nodes, the cost and a reason (`travel`, `blocked`, `deadlock`, `step_aside`, `make_way`, `clear_node`). Pass a router with `FleetCoordinator(router=...)`. A router's cost is in its own unit and is comparable only with costs of the same router.

- `ShortestDistanceRouter(algorithm="dijkstra")` (the default; `"astar"` for A*) minimises the summed arc length; ties break by node id.
- `FastestRouter()` minimises the time of the request's AGV: each arc costs `length / min(v_max, speed_limit)` with the limits for the request's load and the AGV's battery level now, or the profile's `travel_time` for a profile without limits. With a turning model, a node costs its `turn_time` plus the time lost slowing to the corner speed and regaining speed, and the search runs over (node, incoming arc) states. The cost omits the acceleration from rest at the start and the braking at the end. With turn costs the route is the least-cost walk cut to a simple path, which is not guaranteed to be the least-cost simple path. A request without an AGV is routed by length.
- `CongestionAwareRouter(base=FastestRouter(), wait_weight=1.0, horizon=60.0)` is a heuristic. It adds to each leg `wait_weight * w * exp(-t / horizon)`, where `t` is the cost accumulated when the search reaches the leg and `w` is the largest, over the leg's resources that their holders make inadmissible for the AGV now, of `(blocking holders + pending claims) * mean_hold`. It reads `view.blockers`, `view.pending` (which counts the requesting AGV's own queued claim too) and `view.mean_hold`, the exponential average of the observed hold times of a resource kept by `ReservationController`. A leg whose resource is held only by stranded AGVs is excluded. With `wait_weight=0`, or a request without an AGV, it is its base router exactly. `base` must be exactly a `ShortestDistanceRouter` or a `FastestRouter`; any other router, or a subclass, raises `TypeError`. The penalty is a snapshot of the traffic at the instant of the request and depends on the cost at which a leg is reached, which a shortest-path search does not model: the route is a good one, not the best one, and it can be worse than `FastestRouter`'s in some states. Under this router `StandardTrafficPolicy`'s `max_detour` compares costs that include penalties, so it no longer bounds the detour in time. Under `FreeFlowController` the view is empty and the router equals its base. On randomized stress layouts used to review it, it completed more orders with fewer deadlocks than `FastestRouter` under load; this is an observation on those layouts, not a guarantee.

### Route cost in decisions

The coordinator's decisions rank candidates by the router's cost of the travel that would follow, with one router call per candidate, asked with the load and purpose of that travel:

- `DispatchStrategy.select(order, fleet, context)` receives a `DispatchContext` with `graph`, `router`, `view` and `cost(agv, node, *, load_weight=0.0)` (`inf` when there is no route). `NearestIdleStrategy` picks the idle AGV with the least cost to its best output bay of the origin warehouse, ties by AGV id. A custom strategy receives the whole fleet, including AGVs that are making way (`TRAVELING_EMPTY` without an order); choose only `IDLE` AGVs.
- `RepositioningContext` carries `router`, `view` and `cost(agv, node)`. `NearestParkingPolicy` picks the area with a free slot at the least cost, the first in `parking_areas` among equal costs.
- `FleetCoordinator.best_bay(agv, bays, *, load_weight=0.0)` picks the bay of a mission, and the coordinator picks chargers the same way; the reachable-charger energy estimate is computed on the route the router gives, from its arc lengths and the battery level now. `Warehouse.nearest_input_bay` and `nearest_output_bay` remain as chord-distance helpers that the coordinator no longer calls.

Ties go to the first candidate of the sequence for bays, chargers and parking areas, and to the AGV id for dispatch. With the default router these decisions use the summed arc length; on a graph where every arc's length is its chord this is the length the coordinator always used. Under `CongestionAwareRouter` a ranking depends on the traffic at the instant of the decision.

Rankings cost one route search per candidate. On a 900-node grid with 10 idle AGVs and 3 output bays, one dispatch decision took about 40 ms with the default router, 95 ms with `FastestRouter`, 330 ms with `FastestRouter` and a turning model, and 600 ms with `CongestionAwareRouter`. On large layouts with frequent decisions this can dominate the run time.

The standard search of idle vehicles (see [Idle vehicles](#idle-vehicles-make-way-service-nodes-parking)) asks the router once per candidate node, so its cost grows with the layout: about 0.6 s per search on a 30 by 30 grid (900 nodes) with the default router. Under `ReservationController` it runs when an AGV clears a service node, makes way or steps aside; `FleetCoordinator(clear_service_nodes=False)` removes the first of the three.

## Traffic KPIs

`TrafficKPIs(coordinator)` subscribes to the traffic events of the fleet's AGVs and reports, keyed `"<fleet id>/<name>"`:

| KPI | Meaning |
|---|---|
| `blocked_time` | time spent in waits, clipped to the observation window; reported, possibly 0.0, once any wait has started |
| `waits` | waits started |
| `stuck_waits` | waits reported stuck |
| `deadlocks_resolved` | `traffic.deadlock` events with a victim |
| `deadlocks_unresolved` | `traffic.deadlock` events with action `unresolved` |
| `reroutes` | routes staged by traffic (`agv.routed` with reason `blocked` or `deadlock`) |
| `make_way_moves` | make-way moves of idle AGVs |
| `requeued_orders` | orders re-queued because their AGV gave up in traffic |

A KPI without observations is left out of `scalars()`, so a fleet under `FreeFlowController` reports none. Attach the collector before the run:

```python
kpis = TrafficKPIs(fleet).attach(env)
env.run()
print(kpis.scalars())
```

## Examples

Four runnable examples in the [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples) folder provide a progressive learning path:

- [Examples walkthrough](../examples/intralogistics.md): layout diagrams, feature progression, and annotated output for all four examples, including traffic control on a two-way corridor.

## Next

- [Examples walkthrough](../examples/intralogistics.md)
- [Basic Usage](../introduction/basic-usage.md) (production planning quick start)
- [Tutorials](../tutorials/index.md) (job-shop tutorials)
