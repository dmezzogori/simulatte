# Changelog

All notable changes to Simulatte are documented here.

## 0.18.0 — 2026-11-22

AGV traffic control, motion and routing for the intralogistics subpackage, and a layout for every model. Travel times
and semantic digests of intralogistics models change with this release, and models are built through the layout only;
see "Changed", "Removed" and "Migration".

### Added

- `ReservationController`: AGVs move on claims over nodes, lane segments, zones and corridors, granted all or
  nothing, in priority and request order, with a fairness rule for opposite directions. Corridors and dead-end
  branches are derived from the graph; vehicles hold what their body covers, from `AGVType.length` and `min_gap`.
  Segments, zones and corridors are `segment` and `zone` entities in traces.
- Waits, deadlocks and their resolution: the `TrafficPolicy` protocol, `StandardTrafficPolicy(reroute_after=30.0,
  max_detour=1.5, max_resolutions=8)` and the reactions `KeepWaiting`, `Reroute`, `StepAside` and `GiveUp`.
  Deadlocks are detected when an AGV starts to wait, and again among the waits left when a member of an unresolved
  deadlock stops waiting (a member then in no deadlock gets `on_blocked` and its reviews from that instant), and are
  reported with `traffic.deadlock`; waits that nothing can end are reported with `traffic.wait_stuck`. An AGV that gives up a travel before pickup re-queues its order
  (`FleetCoordinator(max_traffic_requeues=3)`, reason `traffic_requeued`).
- Idle AGVs make way for waiting ones (`FleetCoordinator.make_way`, `TrafficController.idle`), move off bays and
  charger nodes (`FleetCoordinator(clear_service_nodes=True)`) and take parking slots when they are sent to a parking
  area. The search for a place to rest skips a node inside a zone or corridor that is closed to the AGV now, so idle
  AGVs do not make way to such a node and around a loop without end. The coordinator also calls
  `TrafficController.idle` when an AGV strands, and the waits it blocks are reviewed at once: the standard policy
  reroutes around it, and a wait that stays is reported stuck even when the stranding released nothing.
- `ChargingStation.n_slots`, `ParkingArea.capacity` and `FleetCoordinator.service_nodes` (the bays of the fleet's
  warehouses and the nodes of its charging stations).
- `Zone(id, members, capacity=1, hold_back=False)` for authored zones; the protocol values `Claim`, `ClaimItem`,
  `ClaimRequest`, `Segment`, `Corridor`, `WaitHooks`, `Wait`, `Blocker`, `Disposition`, `Granted`, `Rerouted`,
  `SteppedAside` and `GaveUp`; `TrafficView` with `branch_root`, `blockers` and `mean_hold` (an exponential average
  of the observed hold times of each resource).
- Motion through nodes: a speed profile with `limits(load_weight, battery_level) -> Kinematics` (as
  `TrapezoidalProfile` has) moves in runs and passes nodes at speed, with claims held one arc plus the braking
  distance ahead (`FleetCoordinator(lookahead_margin=0.0)`). The motion curve `piecewise` describes such runs in
  traces and Studio.
- `AGVType.corner_speed` and `turn_time` (a turning model from the heading change at a node), `AGVType.idle_power`
  (energy drained per unit time outside charging, accounted lazily) and `AGV.battery_level_now()`. The AGV state has
  a `heading` field.
- Routers: the `Router` protocol with `RouteRequest` and `Route`, `ShortestDistanceRouter(algorithm=...)` (the
  default), `FastestRouter` (by travel time) and `CongestionAwareRouter` (a heuristic that penalises busy legs).
  Every route assignment emits `agv.routed`.
- `DispatchContext` (`graph`, `router`, `view`, `cost`), the `RepositioningContext` fields `router` and `view` and its
  `cost`, and `FleetCoordinator.best_bay`.
- `Arc.length` (the travelled length, never shorter than the chord) and `Arc.passing` (opposite traffic may pass);
  `LayoutGraph.length`, `path_length` and `subdivide(max_length)`. `subdivide` and the layout's `block_length` split
  an arc into the same number of pieces (without the piece that float noise in the quotient would add) of the same
  length (the parent's length over the number of pieces), so a subdivided graph and its layout twin are the same
  network to the float.
- `TrafficKPIs`: blocked time, waits, stuck waits, resolved and unresolved deadlocks, reroutes, make-way moves and
  re-queued orders of a fleet.
- Layout schema 1.1: `passing`, `network.block_length` (with the address kinds `block_node` and `block_edge`) and
  `network.zones`. Readers accept 1.0 and 1.1.
- `Environment.unschedule(event)` removes a scheduled event from the queue.
- `Explicit(graph, capacities={"N1": 2})` gives single nodes of an explicit graph a capacity, by node id. In
  `Explicit(zones=...)` a member may be a node id or a pair of ids, the segment between the two nodes. Explicit arcs
  keep their `length` (schema 1.1 reads an optional `length` on explicit arcs; see "Changed").
- `build_simple_system(traffic_controller=None, router=None, agv_length=None)`.
- The example `examples/intralogistics_traffic.py` and a traffic section in the intralogistics guide.

### Changed

- **Travel times and digests of intralogistics models change.** A vehicle whose profile reports kinematic limits
  (every `TrapezoidalProfile`) passes intermediate nodes at speed instead of stopping at each, so travel times over
  more than one arc are shorter under both controllers. A profile without `limits` keeps its rest-to-rest timings.
  The durations of a run's arcs are correctly rounded sums (`math.fsum`), so event times are the same floats on
  CPython 3.11, PyPy and CPython 3.12 and later.
  The new `agv.routed` event and the `heading` field enter semantic digests. Recorded results of intralogistics runs
  are not comparable with earlier releases.
- An interrupt received while an AGV moves takes effect at the first node where it can come to rest, with the energy
  of the arcs covered charged; one received while it waits for a claim takes effect at once.
- A critical battery found on arrival at the goal no longer interrupts the travel: it returns `ARRIVED`, and the
  energy check before the next arc handles the battery. An AGV that arrives at its charger with a critical battery
  therefore charges; before, the travel returned a retry and the charge was skipped.
- `agv.move_interrupted` names the node where the AGV came to rest (before, the node it had left), carries no deltas
  (`agv.move_ended` already cleared the motion), and is also emitted for an interrupt received while the AGV waits for
  a claim.
- A run is planned only over arcs whose estimated energy fits the battery and ends at the first node, other than the
  goal, where the estimated level turns critical, so an AGV does not pass a charger at speed with a battery it knows
  will be critical.
- Dispatch, bays, chargers and parking areas are ranked by the fleet's router's cost of the travel that follows. With
  the default router a ranking gives the earlier decision for the same fleet state, except on a graph with an arc
  whose explicit `length` exceeds its chord: the rankings now sum arc lengths where they summed chords. A run as a
  whole can still differ, because the fleet states differ (see the parking slots below). The reachable-charger estimate uses
  the route the router gives and the battery level now. `Warehouse.nearest_input_bay` and `nearest_output_bay` keep
  the chord ranking and are no longer used by the coordinator.
- `DispatchStrategy.select(order, fleet, graph)` is now `select(order, fleet, context)`, and
  the context of `RepositioningPolicy.reposition(agv, context)` gains `router`, `view` and `cost`; `RepositioningContext`
  is frozen. Custom dispatch
  strategies must be updated (see "Migration"). A custom dispatch strategy receives the whole fleet,
  including AGVs that are making way.
- The coordinator takes a parking slot when it sends an AGV to a parking area and gives it back when the AGV leaves,
  under both controllers: `parking.entered`, `parking.left` and the areas' `parked` state now appear in free-flow
  traces and digests. Under free flow `NearestParkingPolicy` therefore sees areas fill and sends AGVs elsewhere, or
  nowhere, so repositioning targets and the dispatches after them change. A custom repositioning policy that names a
  full area gets no move.
- `order.status_changed` with reason `travel_failed` now means that the router found no route.
- `traffic.reserved` and `traffic.released` carry `resources` (entity ids) instead of `node`; `traffic.wait_started`
  carries `resources`, `blockers` and the reason `occupied`; `traffic.wait_ended` has the reasons `granted`,
  `rerouted`, `stepped_aside`, `gave_up` and `interrupted`; `agv.battery_changed` is also emitted for idle power
  accounted outside a move.
- A `FreeFlowController` serves the network of one environment: registering a fleet of another environment raises
  `ValueError`, and fleets of different environments each take their own controller, as they do by default.
- `Arc.speed_limit`, when given, must be positive and finite (`ValueError` at construction). The kinematic limits a
  run samples are validated when the run starts: a value that is not finite, a negative `decel`, or a `decel` of 0
  for a vehicle that can move raises `ValueError` naming the profile.
- A layout controller is validated by the layout's record of registered resource owners, not by its class.
- The free-flow benchmark (`benchmarks/intralogistics.py`, see `benchmarks/intralogistics-baseline.md`) runs slower
  than in the previous release, because planning runs through nodes costs more per arc than one travel time per arc.
- **Every model is built through its layout.** Every environment has exactly one layout. `env.layout` returns it and
  creates it on first read; activation creates one if nothing did. `Layout(env, file=..., limits=...)` and
  `Layout.from_dict` stay as the way to give the layout a file layer or limits, and must come before the first read of
  `env.layout` and before activation. A script of servers, a shop floor or a production builder needs no layout code,
  and its ids, event order and semantic digest do not change.
- **Traces always carry the layout.** The INITIAL record and the manifest of every trace written by this version carry
  the resolved layout, production-only models included; `env.manifest()` before activation reports
  `{"schema_version": "1.0", "status": "unresolved"}` as its `layout`. `Trace.layout` is `None` only for traces of
  earlier versions, which stay readable by the Python reader, the TypeScript reader and Studio; Studio draws them on
  its fallback grid.
- **The transport network is declared on the layout.** `env.layout.set_network(Explicit(graph) | Lattice(...) |
  Lanes(...))` is the only way to give a model a network. Before preparation a second call replaces the first (the
  last declaration wins, so several regions are declared as their union in one call); after preparation it raises
  `LayoutError`. An environment has one network and at most one controller that controls occupancy; fleets share
  them.
- **Components take handles.** `FleetCoordinator(graph=)`, `ReservationController(graph=)`, `Warehouse(input_bays=,
  output_bays=)`, `AGV(initial_node=)`, `ChargingStation(node=)`, `ParkingArea(node=)` and the `nodes=` mapping of
  `build_simple_system` accept only `layout.graph()`, `layout.node(id)`, `layout.node_at(address)` and
  `layout.port(entity, name)`. A concrete `LayoutGraph` or `Node` raises `LayoutError` (code `foreign-handle`) with a
  message that names what to write.
- **Physical fields exist after preparation.** `agv.current_node`, `fleet.graph`, `controller.graph`, a warehouse's
  bays, `station.node` and `area.node` raise `LayoutNotReady` before `env.layout.prepare()` or `env.activate()`, and
  are read-only afterwards (bays are tuples). The objects are the canonical nodes and graph of the resolved network.
  The nodes are equal to the ones you declared and are different objects, so compare nodes with `==`. The graph is not
  equal to the one you declared, and its arcs are one-way: an arc declared `bidirectional=True` (the default) is two
  arcs of the resolved graph, `arc_between(u, v)` and `arc_between(v, u)`, each with `bidirectional` false and not equal
  to the declared arc; test for the way back with `graph.arc_between(v, u) is not None`. `agv.current_node = node`
  takes a canonical node of the resolved network only. `repr()` of a component never raises.
- **The layout is finalized at activation.** Declaring the transport network after `env.activate()` or the first
  `env.run()` raises `LayoutError`. That includes `build_simple_system(env)`, which declares one, and a component given
  a node handle or `layout.graph()` (a first fleet, for one) when no network was declared before activation. On a
  network declared before activation, an `AGV`, `Warehouse`, `ParkingArea` or `ChargingStation` with handles may still
  be built later and resolves at once, and so may a fleet on the default `FreeFlowController`. A `ReservationController`
  built after preparation raises `LayoutError`, and a fleet on a `ReservationController` built after activation raises
  `RuntimeError`: build it before activation. `env.on_activate(env.step)` now raises `LayoutError` where it raised
  `RuntimeError`.
- `Explicit` keeps `Arc.length`: a length that differs from the chord of the arc's nodes is recorded (schema 1.1 reads
  an optional `length` on explicit arcs, which enters the physical hash), so an arc longer than its chord keeps its
  length, and a graph from `LayoutGraph.subdivide` keeps the lengths of its pieces. With `block_length` the pieces of an
  arc are its recorded length over their count, and a split that would put two block nodes at one position raises.
  Bundles of explicit networks whose lengths are all chords do not change.
- Node capacities and zones are declared with the network: `ReservationController(capacities=, zones=)` are gone, and
  `node_capacity=` stays as the fallback capacity. The zones of a controller are ordered by zone id, and the `members`
  of a `zone` entity list nodes first in id order, then segments.
- `build_simple_system(env)` uses `env.layout` and returns a `GraphRef` as its fifth value. Its AGVs are named
  `vehicle-<i>` (`<prefix>agv-<i>` with a prefix); without a prefix the builder used to leave generated `agv-<n>` ids
  when no layout was in use. Digests of builder models change because of these ids. An `AGV` constructed directly keeps its
  generated `agv-<n>` id unless `agv_id` is given.
- An arc between two nodes at the same position cannot be declared. A lift or a transfer point is modelled with a small
  offset between its nodes and an explicit `length` on the arc. The offset gives the arc a direction, where an arc of
  length 0 left the heading unchanged: with `corner_speed` or `turn_time` on the vehicle type, put the offset along the
  direction of travel, so that the lift adds no turn (an offset across the lane or against it costs a turn).
- Entities created before activation, jobs and orders included, are layout targets with an automatic placement. More
  than `LayoutLimits.max_placements` (100,000) of them raise `LayoutError` at preparation, naming the limit and
  `Layout(env, limits=...)`. The INITIAL record of a recorded run is limited by the 64 MiB of
  `TraceRecorder(max_initial_bytes=)`: 99,990 jobs of two operations on ten servers take about 35 MiB of it (the trace
  file is about 37 MB, or about 63 MB when the recorder is attached before the jobs are created). Entities created
  during the run are not targets.
- A model pays nothing per event for its layout. Activation of a production model takes about 0.5 ms at 10 servers and
  about 50 ms at 1,600 (timings on a development machine). `ResolvedLayout.bundle` and the hashes are built when
  first read: a recorded run builds them once, at INITIAL, and a run that nobody records and whose manifest nobody
  reads never builds them. A model error still raises at preparation.
- A stage or binder name must be a nonempty string without NUL or a lone surrogate; any other name raises
  `LayoutError` where it is declared.
- `env.step()` on an environment that was not activated raises and names `env.activate()`, for every environment.
- `simulatte.intralogistics` loads its names on first use: after a bare `import simulatte.intralogistics` its
  submodules are not attributes (import the submodule or the name), and its event types are registered when the first
  name is used. `import simulatte` still imports neither the layout nor the intralogistics graph.

### Fixed

- `env.run()` without `until` ends at the last finite event when an AGV is stalled (an arc with an infinite travel
  time); no timer is left at infinity.
- A run with a serviceable backlog and no AGV that can become idle now ends: the retry loop of the pending queue
  ticks only while a pending order is unserviceable with the built-in dispatch strategies. A run whose last event
  was a retry tick ends earlier, and the time of a `retries_exhausted` failure can move by up to
  `pending_retry_delay`, because the loop restarts when it is needed again.
- An order cancelled at the instant of its dispatch to an AGV already on its origin stays cancelled.

### Removed

- The node-reservation traffic managers, the path planners and the module `intralogistics/pathfinding.py`, without
  aliases; see the table under "Migration".
- The wait reasons `node_occupied`, `path_delay`, `deadlock_backoff`, `cancelled` and `elapsed`.
- Models built with concrete objects, without aliases: a `LayoutGraph` or a `Node` passed to a component, mutable bay
  lists, `ReservationController(capacities=, zones=)`, a `build_simple_system` that returns a `LayoutGraph`, a second
  network in one environment, and `env.layout is None` as a test for a layout (every environment has one).

### Migration

| Removed | Replacement |
|---|---|
| `TrafficManager`, `PathCheckResult` | `TrafficController`, `Claim`, `ClaimRequest` |
| `FreeTrafficManager` | `FreeFlowController` |
| `ResourceBasedTrafficManager(graph=, env=, node_capacity=, deadlock_timeout=, priority_fn=)` | `ReservationController(graph=, env=, node_capacity=, priority_fn=)` (keyword-only; these four carry over); `deadlock_timeout` has no equivalent, see `StandardTrafficPolicy.reroute_after` |
| `PathPlanner` | `Router` |
| `DijkstraPlanner`, `AStarPlanner` | `ShortestDistanceRouter(algorithm="dijkstra")` (the default), `ShortestDistanceRouter(algorithm="astar")` |
| `FleetCoordinator(traffic_manager=, path_planner=)` | `FleetCoordinator(traffic_controller=, router=, clear_service_nodes=, lookahead_margin=)` |
| `DispatchStrategy.select(order, fleet, graph)` | `select(order, fleet, context)` |

`graph=` and every node argument of these replacements take handles of `env.layout`; see the second table below.

- `AGVType.length` is required with `ReservationController`: `register_fleet` raises `ValueError` naming a vehicle
  type without one. `FreeFlowController`, the default, does not need it. With a length of 0 (and no `min_gap`) a node is
  released the instant the AGV leaves it.
- A custom dispatch strategy reads the graph from `context.graph` and can rank AGVs with `context.cost(agv, node)`. A
  custom repositioning policy can use `context.router`, `context.view` and `context.cost(agv, node)`. Code that
  constructs a `RepositioningContext` must pass `router` and `view`; the context is frozen.
- Code that read `agv.battery.level` with idle power should read `agv.battery_level_now()` for the level now.
- Results, digests and recorded fixtures of intralogistics models change (see "Changed"); re-record them. Layout
  files of schema 1.0 load unchanged, and old traces stay readable.

Models built with concrete objects no longer run. Build them through the layout:

| Before | After |
|---|---|
| `graph = LayoutGraph(nodes, arcs)` passed to components | `from simulatte.layout import Explicit`, then `env.layout.set_network(Explicit(LayoutGraph(nodes, arcs)))`, then `graph = env.layout.graph()`. This `graph` is a handle for components (`graph=`), not a readable graph: `graph.nodes` and `len(graph.nodes)` raise `LayoutNotReady`. Read the resolved graph as `fleet.graph` (or `env.layout.prepare().network.graph`) after `env.layout.prepare()` or `env.activate()` |
| `Warehouse(input_bays=[node])`, `AGV(initial_node=node)`, `ChargingStation(node=node)`, `ParkingArea(node=node)` | `env.layout.node("id")` in place of the node; for a `Node` you already hold, `env.layout.node(node.id)` |
| Reading `agv.current_node` or `fleet.graph` right after construction | Read it after `env.layout.prepare()` or `env.activate()` |
| `agv.current_node is my_node` | `agv.current_node == my_node` |
| `agv.current_node = my_node`, with a `Node` the script built | The canonical node of that id: `env.layout.prepare().network.node(env.layout.node("N1").address)`, or the node of id `"N1"` in `fleet.graph.nodes` |
| `fleet.graph.arc_between(u, v).bidirectional`, or `arc == my_arc`, to find a two-way arc | `fleet.graph.arc_between(v, u) is not None`: resolved arcs are one-way, and a two-way arc is two of them |
| `Explicit(graph, block_length=1.0, capacities={"a~b#1": 2})`, a capacity on a node the block length generates (it raises: `capacities` names declared nodes) | `Explicit(graph.subdivide(1.0), capacities={"a~b#1": 2})`, which declares the same network (the same node ids, positions and arc lengths) with the block nodes as declared nodes |
| A custom traffic controller with `controls_occupancy = True` given to a fleet without registration | Before preparation, `layout.register_resource_owner(controller, owner=..., graph=layout.graph(), binder="name")` and `layout.on_bind("name", callback, reads=...)` |
| `wh.input_bays.append(node)` | Declare the bays at construction; endpoints are frozen |
| Two graphs in one environment | One declared network with both regions, in one `set_network` call |
| `ReservationController(capacities={node: 2}, zones=[Zone(...)])` | `Explicit(graph, capacities={"N1": 2}, zones={"z": {"capacity": 1, "members": ["N1", ("N1", "N2")]}})` (a member is a node id or a pair of ids), or the `capacity` and `zones` of `Lattice` and `Lanes`; `node_capacity=` stays |
| An arc between two nodes at the same position | A small offset between the nodes and an explicit `length` on the arc; with `corner_speed` or `turn_time`, the offset along the direction of travel |
| `env.step()` on a fresh environment | `env.activate()` first |
| Declaring the transport network after `env.activate()` or the first `env.run()` (also through a builder, or a first fleet when no network was declared) | Declare the network on `env.layout` before activation. On a network declared before, an AGV, warehouse, parking area or charger with handles, and a fleet on the default `FreeFlowController`, may still be built later; a fleet on a `ReservationController` must be built before activation (`RuntimeError`) |
| `env.layout is None` or `getattr(env, "layout", None)` as a check | Every environment has a layout; reading `env.layout` creates it |
| `graph` returned by `build_simple_system(env)` used as a `LayoutGraph` | It is a `GraphRef`; read `fleet.graph` after preparation |
| `Layout(env)` to give a production model a layout | `env.layout`, or nothing: activation creates it. Keep `Layout(env, file=..., limits=...)` for a file layer or limits |

The statements that go with the table:

- Traces and manifests always carry the layout, so a trace of a model that never mentioned one now has it.
- AGVs of `build_simple_system` are named `vehicle-<i>` (`<prefix>agv-<i>` with a prefix).
- Declaring a second network before preparation replaces the first; after preparation it raises. Declare the union.
- A lift or a transfer point is a small offset between two nodes and an explicit `length` on their arc. With
  `corner_speed` or `turn_time`, the offset lies along the direction of travel, so that the lift adds no turn.
- More than 100,000 entities created before activation (jobs and orders count) need `Layout(env, limits=...)`, with a
  larger `max_placements`.
- A concrete `LayoutGraph` or `Node` given to a component raises `LayoutError` with the code `foreign-handle`; the
  message says what to write.
- A zone entity lists its members as nodes in id order, then segments; the zones of a controller are ordered by id.
- Every entity id becomes a layout id at activation, also in a model that nothing records: an entity name with a lone
  surrogate (not valid UTF-8) now raises `LayoutError: topology ids must be nonempty UTF-8 strings` at activation,
  where a production model used to run unless it was recorded. Name entities with valid UTF-8 strings.
- `Explicit` refuses what a `LayoutGraph` accepts and a network cannot hold: a node with a coordinate that is not
  finite (`LayoutError: x must be finite`) and an arc from a node to itself (`zero-length`). Give nodes finite
  coordinates and leave such arcs out.

The two-node model of the [layout guide](docs/guides/layout.md), before:

```python
from simulatte import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FleetCoordinator,
    LayoutGraph,
    Node,
    ReservationController,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)

env = Environment(seed=7)
box = SKU("box", weight=1.0, volume=0.1)
cart = AGVType(
    name="cart",
    speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
    battery_capacity=100.0,
    weight_capacity=10.0,
    volume_capacity=1.0,
    length=1.0,
)

a, b = Node("a", 0, 0), Node("b", 20, 0)
graph = LayoutGraph([a, b], [Arc(a, b, bidirectional=True)])
store = Warehouse(
    env=env,
    name="store",
    input_bays=[a],
    output_bays=[a],
    n_slots=1,
    products=[box],
    initial_inventory={box: 5},
    pick_time=2.0,
    put_time=2.0,
)
line = Warehouse(
    env=env,
    name="line",
    input_bays=[b],
    output_bays=[b],
    n_slots=1,
    products=[box],
    pick_time=2.0,
    put_time=2.0,
)
agv = AGV(env=env, agv_type=cart, initial_node=a)
traffic = ReservationController(env=env, graph=graph)
fleet = FleetCoordinator(
    env=env,
    graph=graph,
    fleet=[agv],
    warehouses=[store, line],
    charging_stations=[],
    parking_areas=[],
    traffic_controller=traffic,
)
assert agv.current_node is a
print(len(graph.nodes))
```

and after:

```python
from simulatte import Environment
from simulatte.intralogistics import (
    AGV,
    AGVType,
    Arc,
    FleetCoordinator,
    LayoutGraph,
    Node,
    ReservationController,
    SKU,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.layout import Explicit

env = Environment(seed=7)
box = SKU("box", weight=1.0, volume=0.1)
cart = AGVType(
    name="cart",
    speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
    battery_capacity=100.0,
    weight_capacity=10.0,
    volume_capacity=1.0,
    length=1.0,
)

a, b = Node("a", 0, 0), Node("b", 20, 0)
layout = env.layout
layout.set_network(Explicit(LayoutGraph([a, b], [Arc(a, b, bidirectional=True)])))
store = Warehouse(
    env=env,
    name="store",
    input_bays=[layout.node("a")],
    output_bays=[layout.node("a")],
    n_slots=1,
    products=[box],
    initial_inventory={box: 5},
    pick_time=2.0,
    put_time=2.0,
)
line = Warehouse(
    env=env,
    name="line",
    input_bays=[layout.node("b")],
    output_bays=[layout.node("b")],
    n_slots=1,
    products=[box],
    pick_time=2.0,
    put_time=2.0,
)
agv = AGV(env=env, agv_type=cart, initial_node=layout.node("a"))
traffic = ReservationController(env=env, graph=layout.graph())
fleet = FleetCoordinator(
    env=env,
    graph=layout.graph(),
    fleet=[agv],
    warehouses=[store, line],
    charging_stations=[],
    parking_areas=[],
    traffic_controller=traffic,
)
layout.prepare()
assert agv.current_node == a
print(len(fleet.graph.nodes))  # the resolved graph; `layout.graph()` is only a handle
```

## 0.17.0 — 2026-10-13

- Added experiments: `*.experiment.json` files (schema `experiment-v1`), the
  `simulatte.experiments` Python API (`Experiment`, `Grid`, `Explicit`, `run`) and
  `simulatte experiment run|status|results|cancel|resume|delete`. A grid or explicit list of
  configurations, optional layout variants and N replications run as ordinary managed executions;
  replication i uses seed `base_seed + i` in every configuration for common random numbers, and the
  experiment type (steady state, terminating horizon, terminating finite population) follows the
  stopping policy.
- Added replication-level statistics in pure Python: Student t intervals with `ok`, `degenerate`
  and `insufficient` states, pointwise labelling or Bonferroni adjustment, and paired differences by
  replication index against a baseline with missing pairs listed. Reports come from
  `simulatte experiment results` and the Python `ExperimentReport`.
- Added `simulatte replay REQUEST_ID`, which reruns a KPI-only replication at `full` level, on the
  original host unless `--worker` names another, and labels it `verified` or `unverified` with the first differing check (digest, KPI scalar, runtime,
  dependencies or layout).
- Added Studio panels for experiments, results and charts: definition form, start, cancel, resume
  and delete, a configuration by KPI table with baseline and Bonferroni controls, overlaid series
  from up to eight recordings and a Welch warm-up plot. Warm-up stays user-set.
- Added side-by-side compare playback of two full recordings on one clock, with a "Replay at full
  level" action and a verified or unverified replay badge.
- Added remote workers over SSH: user-level `workers.toml` (no credentials accepted),
  `simulatte workers list|check|setup|prune`, projects provisioned from `uv.lock` into immutable
  cached environments, hash-verified staging with a 64 MiB default bundle cap, and uv installed
  only by a confirmed `workers setup --install-uv`. Hosts must be POSIX; Windows is never a remote
  host and SSH from a Windows coordinator is untested.
- Added `simulatte store prune [--dry-run]` and `simulatte experiment delete`; nothing is deleted
  automatically.
- Experiments run on several local slots; the default is `min(4, max(1, cpu_count - 1))`, chosen from
  a throughput measurement, with `--local-slots` to override it. A lost replication is dispatched
  again at most twice, by another slot when the worker could not be reached, and that slot backs
  off from 15 s up to 5 minutes; a failed one is never retried; Ctrl-C pauses a run with exit 130 and an
  unchanged file returns or resumes the existing run.
- Execution protocol 1.1 adds staging and provisioning messages. The run store moves to schema 2
  (slots, experiments, replications and replays).

### Changed

- A runtime that changed after preparation now fails the attempt with `runtime_changed` (`failed`)
  instead of `validation_failed`, for local and remote runs.

### Fixed

- A `[tool.simulatte.bundle] include` pattern ending in `**` (for example `packages/**`) now captures
  the files below it on Python 3.11 and 3.12, where it previously matched directories only.

### Migration

- Stores migrate automatically; delete `.simulatte/` to reset. A store written by a newer version
  is still refused.
- Remote runs need a uv project with `uv.lock` that pins `simulatte` at the coordinator's
  major.minor, path dependencies inside the project root, and uv on the host. Private indexes warn:
  the host needs its own credentials.
- `*.experiment.json` files and layout files listed in `[tool.simulatte.bundle] layouts` are no longer
  part of the captured model source.

## 0.16.0 — 2026-10-12

- Added typed `@model` metadata, constraints and explicit horizon/finite-population
  stopping, preserving ordinary script calls and the existing `Runner`.
- Added `simulatte run` and `simulatte studio`: immutable source/declared-input
  capture, fresh local execution processes, bounded durable output, cancellation,
  revision fencing, result verification and captured-request reproduction.
- Studio adds parameter forms, complete layout editing and undo/redo, live
  playback/progress/KPIs, committed-run history, conflict recovery and pinned
  presentation exports. Physical/source edits rerun; presentation-only edits
  update the view association.
- Trace container 1.3 adds optional execution provenance. Existing trace reading,
  verification, `simulatte view` and offline/media exports remain supported.

### Migration

- Core-only installs support `run`; install `[studio]` for the interactive server.
- Managed models use frozen dataclass parameters and finish build before running
  simulation events. Declare data inputs and non-Git source membership explicitly;
  external editable imports/startup hooks are not executed in managed children.
- Finite models explicitly track pending/admitted/completed work through
  `Population`; an empty event queue alone is not completion.
- Managed output belongs under `env.artifacts_dir`. The local `.simulatte` store
  has one coordinator owner; close Studio before headless execution in that project.

## 0.15.0 — 2026-10-11

- Added Studio: `simulatte view` with authenticated loopback serving, recorded-state playback, a Pixi
  scene, docked inspector, event history, KPI charts and presentation mode. Install with `[studio]`.
- Added standalone offline HTML, streamed MP4/WebM and PNG ZIP64 exports, bounded temporary-file
  storage and cancellable manual export timelines. Actual Safari export and offline-player paths passed manual acceptance.
- Trace container 1.2 adds optional separate observer records and KPI series display rules while
  retaining 1.0/1.1 reading and the existing domain replay/digest contract.
- Recorder diagnostics bypass the event bus to prevent recursive recording, including with multiple
  recorders attached to one environment.

### Migration

- Install `simulatte[studio]` to use `simulatte view`; core simulation use does not require the extra.
- New recordings use trace container 1.2. Studio reads 1.0/1.1/1.2; older readers skip optional
  observer/display metadata while preserving domain replay and verification.
- KPI display defaults remain previous-sample interpolation with gaps. Set `interpolation` and
  `missing` on KPI declarations to choose other display rules; numeric KPI calculations are unchanged.
- Offline HTML embeds the complete recording. Video exports are capped at 100,000 frames and PNG
  sequences at 10,000; temporary export storage is scoped to the serving origin.

## 0.14.0 — 2026-10-11

Simulatte now prepares deterministic layouts and transport networks separately from simulation execution.
The headless layout subsystem needs no extra runtime dependencies. See the
[layout guide](docs/guides/layout.md) for runnable examples and migration guidance.

### Added

- `Layout`: layered code/file placement, deterministic static auto layout, explicit/lattice/lane transport
  networks, ports/connectors, logical handles, staged physical reads and frozen dependency tracking.
- Immutable saved layout bundles, full/physical/layers hashes and presentation-only re-resolution without
  rerunning model code. JSON Schema 1.0 ships in the core package; no new runtime dependency.
- Structured `LayoutError.diagnostics` with stable categories, tuple paths and available stage/target context.
- [Runnable layout guide](docs/guides/layout.md), API reference and migration examples.

### Changed

- Traces now write container 1.1 with optional `INITIAL.layout`; Python and TypeScript readers retain 1.0
  compatibility. Full/KPI levels store the same layout; Python checks its hashes. INITIAL has a separate
  64 MiB encoded limit (`max_initial_bytes`), paired with the reader's `max_record` limit.
- In layout mode, intralogistics constructors accept owner-scoped handles, physical getters raise
  `LayoutNotReady` before binding, and static endpoints share one frozen canonical graph. The simple builder
  returns a `GraphRef` and accepts a complete role mapping for an existing network. Explicit `LayoutGraph`
  scripts retain their existing behavior. Traffic continues to reserve nodes only.

## 0.13.0 — 2026-10-10

Simulatte now records everything that happens in a run as typed events on a per-environment bus. Logging, KPI
collectors, the semantic digest and the new trace recorder are all subscribers of that bus. This release changes
many APIs and, because RNG streams are now derived from the seed and entity names, **seeded results differ from
0.12**. Read [Migrating from 0.12](#migrating-from-012) before upgrading.

### Added

- Event bus: `env.bus.subscribe(handler, types)` (event classes, `"*"` for domain events, `"**"` for everything),
  `env.emit`, `env.wants`. Typed `DomainEvent`s with state deltas and `ObserverEvent`s (`log`, `kpi.sample`),
  registered with `@event_type` in a catalog, in `simulatte.events`. Every component now publishes its transitions:
  servers, pre-shop pool, shop floor, release policies (`policy.decision`), fleet, orders, AGVs, traffic,
  warehouses, charging stations and parking areas. A server credits its own `worked_time` with
  `server.work_credited` (from `Server.process_job`), so a server used without a `ShopFloor` replays like one inside
  it.
- Entities (`simulatte.entities`): every component has a stable id (`job-<n>`, `server-<n>` or `name=`, `agv-<n>`,
  `order-<n>`, ...) and a declared state schema. `env.entities` is the registry; `name=` and `label=` arguments on
  `Server`, `ShopFloor`, `PreShopPool`, `Router`, `FleetCoordinator`, `Warehouse`, `ChargingStation` and
  `ParkingArea`; `NodeBinding` entities for graph nodes. Jobs and orders retire from the live registry when they
  finish.
- Builder `prefix`: every `build_*_system`, `build_simple_system`, `Scenario.build_floor` and
  `Scenario.build_router` take `prefix: str = ""`, so several systems can share one environment.
- Seeds and RNG streams: `Environment(seed=...)` (an integer in `[0, 2**63)`, or drawn from `os.urandom` and
  readable as `env.seed`), `env.rng(name)` and `env.bind(value, kind=..., stream=..., owner=...)` with three binding
  kinds: `scalar` (`() -> float`), `routing` (`() -> Sequence[Server]`) and `contextual` (`(*context) -> float`, for
  example `(sku, qty)` for warehouse picks). Distributions and routings are descriptions with `sampler(rng)`;
  `env.opaque_sampler_owners` lists callables the library cannot reproduce.
- Activation (`env.activate()`, `env.on_activate(fn)`, `env.activated`, `env.initial_state`):
  `env.run()` activates on first use. `FleetCoordinator.submit` and `cancel` called before activation are
  deferred and the order reports `OrderStatus.PENDING_ACTIVATION`.
- Semantic digest and manifest: `env.enable_digest()`, `env.fingerprint()` (digest plus KPI scalars),
  `env.manifest()`, `Provenance` and `RunManifest` (`simulatte.provenance`). The digest is identical across observer
  configurations, `PYTHONHASHSEED` values and, for the tested workloads, CPython and PyPy. A run stopped with
  `run(until=<simpy.Event>)` makes the manifest incomplete, because the stop cannot be reproduced from it.
- Traces: `TraceRecorder(env, path, level="full" | "kpi", chunk_limits=...)` writes a seekable, checksummed trace
  file; `Trace.open(path)` reads it (`state_at`, `events`, `kpis`, `kpi_series`, `manifest`, `fingerprint`,
  `check`, `verify`) and tolerates incomplete tails. Format 1.0, MessagePack with zlib-compressed chunks. Replay
  compares values by their canonical encoding in both readers: `True` is not `1`, `1` is not `1.0`, `-0.0` is not
  `0.0`, and NaN equals NaN, so a NaN in a state or a delta round-trips.
- `studio/`: a pnpm workspace with `@simulatte/trace`, a TypeScript reader that replays the same states as the Python
  reader (new `trace-ts` CI job). It is not part of the Python package.
- KPI framework (`simulatte.kpi`): `KPI` declarations, the `Collector` base class bound to an owner entity,
  `env.configure_kpis(warmup=...)`, window-aware aggregation, `env.collectors`.
- Built-in collectors: `simulatte.collectors` (`EMACollector`, `ShopFloorTimeSeries`, `CurrentWorkloadCollector`,
  `ServerTimeSeries`, `ShopFloorKPIs`) and, in `simulatte.intralogistics`, `OrderEMACollector`, `FleetTimeSeries`
  and `FleetKPIs`. `ShopFloor(default_metrics=True)` and `FleetCoordinator(default_metrics=True)` attach the EMA
  collector as `.metrics`.
- Log sinks (`simulatte.logsinks`): `TextSink`, `JsonSink`, `SQLiteSink`, `HistorySink`, `LogSink`.
  `Environment(log_level=...)`, `env.sinks`, `env.log_db` and `Runner(log_level=...)`. At `DEBUG` the sinks also
  render domain events.
- `Environment(debug=True)` validates events against the catalog and the entity state schemas, rejects subscribers
  that schedule events or draw random numbers, rejects two different values bound to one RNG stream, requires
  wire-valued `extra` in log calls (recorded as an immutable copy), accepts only `float` (not `int`) in float payload
  and state fields, rejects lists, dicts, sets and other mutable containers anywhere in event payloads and deltas
  (use tuples and `FrozenMap`), and checks the shape of each delta operation (arity, string ids and fields, integral
  indices, list operations on list fields, map operations on map fields), naming the event. Independently of debug mode, the trace recorder keeps an immutable copy of delta
  values, so changing a list after emitting it no longer changes the recorded trace.
- Intralogistics: `AGV.sample_load_time()` / `sample_unload_time()`; `SpeedProfile.motion(...)` describes AGV motion
  for traces; `OrderStatus.PENDING_ACTIVATION`.
- Top-level exports: `from simulatte import Environment, Runner, Provenance, TraceRecorder, Trace, KPI, Collector,
  Event, DomainEvent, ObserverEvent`. Component classes stay importable from their modules.
- Benchmarks (`benchmarks/`) and a `bench` CI job that gates the no-subscriber overhead against
  `simulatte==0.12.0`. Modes: `none` (no subscribers, gated), `default` (the default EMA collector), `digest` and
  `full` (ratios against `none`).
- Docs: the [Events, Traces & KPIs guide](docs/guides/events-and-traces.md) and an Events & Traces API page.

### Changed

- **Breaking:** plotting requires `pip install "simulatte[plot]"`; the experimental Gymnasium wrapper requires
  `pip install "simulatte[rl]"`. Use `simulatte[all]` for both. Core installs no longer pull in matplotlib,
  gymnasium or their NumPy dependency. Missing extras raise an error with the relevant install command.
- Python 3.15 final is supported and its CI test lane is required. Linting, type checks and the primary
  coverage upload remain on Python 3.14; the Python 3.11 language floor is unchanged.

- Production builders accept `default_metrics=False`; `FleetCoordinator` accepts `ema_alpha`.
- `Runner` is imported lazily from the top-level package. API reference pages filter private members.
- Traces record KPI declarations (the optional `kpi-declarations-v1` feature); Python exposes `kpi_declarations`
  and TypeScript `kpiDeclarations`. Older traces remain readable. The TypeScript reader uses its own strict
  MessagePack subset decoder and exposes metadata through read-only getters.
- CI covers CPython 3.11. Benchmarks cover INFO logging, warm seeks, TypeScript decoding/replay, Runner throughput
  and intralogistics comparisons with 0.12.0; observer-invariance tests also compare SimPy step counts.


- **Breaking:** logging is rebuilt on the bus and `loguru` is no longer a dependency; `msgpack` is a new
  dependency. `simulatte.logger` is removed (see Removed). `Environment(log_level=...)` is per environment.
  `log_format` accepts only `"text"` and `"json"`. History records are `simulatte.events.LogEvent` (`.timestamp` is
  now `.t`, plus `.seq`; `.extra` is a read-only map). JSON lines gain `seq`, `kind` and `type`. The SQLite table
  `log_events(id, env_id, timestamp, level, message, component, extra, wall_time)` is now
  `events(env_id, seq, t, kind, type, level, component, message, data_json)`; use `rowid` for insertion order.
  The production components (`Server`, `ShopFloor`, `PreShopPool`, `Router`) and the facilities (`Warehouse`,
  `TrafficManager`, `ChargingStation`, `ParkingArea`) no longer write DEBUG log messages; subscribe to their events
  instead. Fleet warnings and errors stay `log` events.
- Event construction calls no user code (observers cannot change results through it) for the value types it
  supports: `bool`, `int`, `float`, `str`, `None`, tuples and maps of them, subclasses of `int`, `float` and `str`
  (read through the base type, so a subclass's own `__float__` is never called), and, as numbers, `Fraction`,
  `Decimal` and NumPy scalar numbers other than `timedelta64` and complex scalars. Anything else (user classes, NumPy
  arrays, `MappingProxyType`, user mappings) is not convertible: a priority is recorded as null, another number as
  `NaN`, and debug mode rejects it. Overrides of `Entity.snapshot()` must be pure: it runs only when observed. Values derived only for an
  event (`job.finished`'s `makespan`, `lateness`, `total_queue_time`) are computed from converted floats, not
  through model properties. Digests and traces read the simulation time the same way and never record a NaN time: a
  time of another type raises `TypeError`, a NaN time `ValueError`, at the first recorded event. Wire values are
  made of exact built-in types (an `IntEnum` or `StrEnum` in a delta becomes `int` or `str`).
- **Breaking:** seeding. `Runner` creates `Environment(seed=seed)` and no longer calls `random.seed(seed)`, so model
  code that draws from the global `random` module loses its reproducibility; draw from `env.rng(name)`.
  `Distribution.__call__` is removed: distributions are descriptions with `sampler(rng)`, bound to named streams
  (`<router>/interarrival`, `<router>/service/<sku>/<server>`, `<agv id>/load`, `<warehouse>/pick`, ...). Plain
  callables are still accepted where a distribution was, but are opaque (they make the manifest incomplete).
  `Environment(seed=...)` raises `ValueError` outside `[0, 2**63)` and `TypeError` for a `bool`. Because stream names
  derive from entity names, every seeded result changes: the numbers in the docs and the gallery were regenerated.
- **Breaking:** identity. Job ids are `job-<n>` (a per-environment counter) instead of UUIDs; AGV ids default to
  `agv-<n>` and `agv-<n>` is reserved (`AGV(agv_id="agv-0")` raises); order ids are `order-<n>` and `TransferOrder.id`
  is `None` until the order is attached. Entity names share one namespace per environment (graph node ids,
  warehouse, charging station and parking area names, and component names included), must not contain `/` or NUL,
  must not match `^<kind>-\d+$` for a registered kind, and duplicates raise. An entity is attached once.
- **Breaking:** builders and `Scenario.build_floor` / `Scenario.build_router` now name their entities (`wc-<i>`,
  `shopfloor`, `router`, `psp`), so building a second system in the same environment without `prefix=` raises a
  duplicate-id error. The `scenario` default of the builders is `None` (a fresh `Scenario()`) instead of a shared
  instance.
- **Breaking:** collectors. `ShopFloor(metrics_collector=...)`, `collect_time_series`, `time_series_collector`,
  `Server(collect_time_series=...)` and `FleetCoordinator(order_metrics_collector=..., time_series_collector=...)` are
  replaced by `default_metrics=` and bus collectors attached with `collector.attach(env)`; see the table below.
  Builder flags are kept and attach the new collectors: every `build_*_system` and `Scenario.build_floor` take `collect_workload`; `collect_time_series` exists only on `build_immediate_release_system` and `Scenario.build_floor`. `inventory_ts`
  is keyed by warehouse id with SKU-id keys instead of `Warehouse` and `SKU` objects.
- `Router` binds its distributions, routings and service times at construction: changing the arguments afterwards has
  no effect.
- **Breaking:** `TrafficManager.place` is renamed `place_now` (a runtime-checkable protocol). It reserves the node
  during activation, requires an immediate grant and raises on conflict instead of waiting, so two AGVs on one node of
  capacity 1 now raise.
- **Breaking:** `FleetCoordinator.create_order` attaches the order at once and no longer accepts `id=`. `submit` and
  `cancel` before activation are deferred until activation (queued in call order, run at time 0).
- **Breaking:** `LayoutGraph.nodes` returns a tuple in insertion order instead of a `frozenset`.
  `ShopFloor.jobs` is an insertion-ordered `dict[ProductionJob, None]` instead of a set (`shopfloor.jobs[job] = None`
  and `del shopfloor.jobs[job]` replace `.add` and `.remove`). `Server._idx` is removed.
- **Breaking:** intralogistics time parameters are managed bindings: `AGVType.load_time_fn` / `unload_time_fn`
  become `load_time` / `unload_time`, `Warehouse(pick_time_fn=, put_time_fn=)` become `pick_time=` / `put_time=`,
  `ChargingStation(recharge_fn=)` becomes `recharge_time=`. Each takes a number, a distribution or a callable (opaque).
  `warehouse.pick_time_fn` and `put_time_fn` attributes are gone. (`AGVType.recharge_fn` and `Battery(recharge_fn=)`
  are unchanged.)
- **Breaking:** `ProductionJob.planned_release_date` (and the values derived from it) is always a `float`; before, an
  integer routing time and allowance gave an `int`.
- Numeric changes (D58): sums of floating-point values that feed events, dispatching priorities, arrival rates, due
  dates, planned release dates, samplers, fleet paths and loads, or KPIs use `math.fsum`. Results can differ from 0.12
  in the last bit; CPython (all versions) and PyPy now agree for the tested workloads.
- `psp.remove(job=..., reason=...)`: release policies pass `released` or `postponed`; the job's location and the
  `psp.exited` event record it.
- `job.queued` carries `queue_length`, counted when the job joins and including the job itself (see Fixed).
- Log and KPI events consume the global event sequence number, so the `seq` values of domain events in a trace can have
  gaps. The digest excludes `seq`, and readers must not assume contiguity.
- Driving the simulation manually with `env.step()` requires `env.activate()` first; `env.run()` activates by itself.
- Three behaviors of the fleet collectors differ from the old ones: the destination inventory snapshot of
  `FleetTimeSeries` is taken when the order reaches `COMPLETED`, before the user delivery hooks run; orders are counted
  by the fleet that created them (an order created on fleet A and submitted to fleet B counts for A); and a direct
  `AGV.transition_to` call now adds a utilization point.
- `ShopFloorTimeSeries`, `ServerTimeSeries` and `CurrentWorkloadCollector` behave as the old collectors except:
  `CurrentWorkloadCollector` subtracts work when an operation completes, so a yielding after-operation hook that holds
  the server does not count finished work as remaining (this corrects the old series); `ServerTimeSeries.qt` has one
  point per time and records cancelled requests; `ut` starts at the collector's creation time. The EMAs are not part of
  `env.fingerprint()`; `ShopFloorKPIs` and `FleetKPIs` scalars are. `EMACollector.ema_tardy_jobs` counts late jobs outside
  the due-date window, while the `tardy_fraction` KPI counts every job with positive lateness.
- The advanced intralogistics example draws from `Environment(seed=42)` streams instead of a private
  `random.Random(42)`; its printed output changed (65 orders, average outbound fulfillment 219.6 s).
- Docs prose that claimed more than the regenerated numbers support was reworded in the benchmark shops, dispatching
  (stateless, focus), release (WIP, workload, triggers) and release-policy comparison pages.

### Fixed

- Concurrent parking requests for one AGV share a reservation. Interrupted pending parking requests cancel
  their wait and release any just-granted slot when no other caller is waiting. A duplicate admission cannot
  repark an AGV after it has left.

- Fleet recovery now tolerates repeated interrupts, requeues orders after `ReturnToOrigin`, preserves terminal
  order status on cancellation, and completes mission cleanup before idle hooks can dispatch another mission.
- Saturated fleets skip unnecessary pending-order scans; built-in dispatch strategies reuse the idle fleet view.
- Interrupted charging, battery swaps and node waits release or cancel resources; duplicate parking entries retain
  their original slot.
- Environment setup closes already-open sinks on failure; `close()` suppresses later emissions from finalizers.
  Filtered log levels no longer construct events or consume sequence numbers unless a bus subscriber wants them.
- Empty deltas report `ValueError`; KPI samples flush on the trace latency limit as well as size/count and close.
- Digest framing tests no longer infer entity kinds from unrelated globally registered test types.


- `job.queued`'s queue length (previously the logged `queue_length` of `Server.request`) was one too high: SimPy appends
  a request to the queue before the logging call ran. It now counts the waiting requests including the newcomer.
- Observer purity: `AGV.utilization()`, `state_percentage()` and `time_allocation()` no longer write to the AGV, and
  `TrafficManager.check_path` no longer logs, so reading them cannot change a run.
- Iteration order: `LayoutGraph` nodes, `check_path` conflict nodes and `ShopFloor.jobs` have a defined order, so runs
  are identical across `PYTHONHASHSEED` values.

### Removed

- `simulatte.logger` and loguru: `SimLogger`, the old `LogEvent`, `EventHistoryBuffer`, `SQLiteEventStore`,
  `env.logger`, `SimLogger.set_level` / `get_level`. Use `Environment(log_level=)`, the sinks (`env.sinks`,
  `env.log_history`, `env.log_db`) and `sink.enable_component` / `disable_component`.
- From `simulatte.shopfloor`: `MetricsCollector`, `EMAMetricsCollector`, `TimeSeriesCollector`,
  `DefaultTimeSeriesCollector`, `CurrentWorkLoadCollector`, `ShopFloor.metrics_collector`, `set_metrics_collector`,
  `time_series_collector`, `set_time_series_collector`. `Server(collect_time_series=)`; `Server.plot_qt` / `plot_ut` moved to `ServerTimeSeries`.
- From `simulatte.intralogistics`: `OrderMetricsCollector`, `EMAOrderMetrics`, `IntralogisticsTimeSeriesCollector`,
  `DefaultIntralogisticsCollector`, `FleetCoordinator(order_metrics_collector=, time_series_collector=)`.
- `FleetCoordinator.create_order(id=)`, `TrafficManager.place`, `Distribution.__call__`, the `*_time_fn` parameters
  listed above.

### Known limitations

- On the stock PyPy 8.0.0 / Python 3.11.16 Linux x86-64 runtime used by CI, digest, KPI trace and full trace
  recording exceed the accepted performance targets. This is accepted for 0.13.0 as a documented limitation;
  the targets remain unchanged. The tested trajectories and digests still match. See
  [Running on PyPy](docs/running-on-pypy.md#013-recording-limitation-on-pypy-800) and
  [follow-up #59](https://github.com/dmezzogori/simulatte/issues/59).

### Migrating from 0.12

**Seeds and randomness.** Pass a seed to the environment; draw from named streams.

```python
# 0.12
random.seed(42)
env = Environment()
service = lambda: random.expovariate(0.5)

# 0.13
env = Environment(seed=42)
service = Exponential(rate=0.5)  # managed: reproducible, recorded in the manifest
rng = env.rng("my-stream")  # for your own draws
```

**Logging.**

```python
# 0.12
env.logger.disable_component("Server")
rows = env.logger.query_sql(level="ERROR", component="Server")
raw = env.logger.execute_sql("SELECT ...")
if env.logger.db_enabled:
    ...
db_env_id = env.logger.env_id
SimLogger.set_level("DEBUG")

# 0.13
for sink in env.sinks:
    sink.disable_component("Server")
rows = env.log_db.query(level="ERROR", component="Server")  # Environment(log_db_path=...); returns LogEvents
raw = env.log_db.execute_sql("SELECT ...")
try:  # db_enabled: env.log_db raises RuntimeError without log_db_path
    env.log_db
except RuntimeError:
    ...
db_env_id = env.log_db.env_id
env = Environment(log_level="DEBUG")  # per environment
```

For built-in components, replace message-substring checks by subscribing to event classes (`JobQueued`,
`JobFinished`, ...) with `env.bus.subscribe`.

**Collectors.**

| 0.12 | 0.13 |
|---|---|
| `ShopFloor(metrics_collector=None)` | `ShopFloor(default_metrics=False)` |
| `shopfloor.metrics_collector.ema_makespan` | `shopfloor.metrics.ema_makespan` |
| `ShopFloor(collect_time_series=True)`, `shopfloor.time_series_collector.plot_wip()` | `ts = ShopFloorTimeSeries(shopfloor).attach(env)`, `ts.plot_wip()` |
| `Server(collect_time_series=True)`, `server.plot_qt()` | `ServerTimeSeries(server).attach(env).plot_qt()` |
| custom `MetricsCollector.record(job)` / `TimeSeriesCollector` | subclass `simulatte.kpi.Collector`, subscribe to `JobFinished`, `OperationCompleted`, ... |
| `FleetCoordinator(order_metrics_collector=EMAOrderMetrics(alpha=0.05))` | `default_metrics=False` and `OrderEMACollector(coordinator, alpha=0.05).attach(env)` |
| `FleetCoordinator(time_series_collector=DefaultIntralogisticsCollector())` | `FleetTimeSeries(coordinator).attach(env)` |

**Two systems in one environment.**

```python
# 0.12: ids were generated, two builders could share an environment
build_lumscor_system(env=env, ...)
build_slar_system(env=env, ...)

# 0.13: entity ids are unique; give each system a prefix
build_lumscor_system(env=env, prefix="a.", ...)
build_slar_system(env=env, prefix="b.", ...)
```

**Intralogistics.**

```python
# 0.12
AGVType(..., load_time_fn=lambda: 5.0)
Warehouse(..., pick_time_fn=lambda sku, qty: 2.0, put_time_fn=lambda sku, qty: 2.0)
ChargingStation(..., recharge_fn=fast)
order = coordinator.create_order(id="o-1", ...)
traffic.place(agv, node)
graph.nodes            # frozenset

# 0.13
AGVType(..., load_time=5.0)             # number, Uniform(...), or a callable (opaque)
Warehouse(..., pick_time=2.0, put_time=2.0)
ChargingStation(..., recharge_time=fast)
order = coordinator.create_order(...)   # order.id == "order-0", assigned at once
traffic.place_now(agv, node)
graph.nodes            # tuple, insertion order
```

Orders submitted before the first `env.run()` report `OrderStatus.PENDING_ACTIVATION` until activation. Name AGVs
without `agv-<n>` ids (or let the library generate them), and avoid node ids that equal another entity's name.

**Job and shop-floor details.** Replace `shopfloor.jobs.add(job)` / `.remove(job)` by item assignment and `del`;
read job ids as `job-<n>` strings; expect `planned_release_date` to be a `float`.

**New tooling you may want.** Record a run with `TraceRecorder(env, "run.simtrace")` before the first `env.run()` and
`env.close()` (or `with Environment(...) as env:`), then `Trace.open("run.simtrace")`. Compare runs with
`env.enable_digest()` and `env.fingerprint()`. See the [guide](docs/guides/events-and-traces.md).

## 0.12.0 — 2026-06-11

### Added

- `Scenario` value object: shop type (PJS/GFS/PFS), preset configurations, and
  derived arrival rate, with `build_floor`/`build_router` assembly as the
  de-duplicated core.
- Benchmark shop environments (PJS/GFS/PFS) accessible via Scenario presets.
- `SkuFamily` value object with per-family SKU mix and pluggable
  distributions/arrival process.
- `Distribution` protocol with built-in variates; `TruncatedErlang` distribution.
- `build_*_system` builders now return the wired policy.

### Changed

- **Breaking:** builder signatures are now keyword-only (`*, env, scenario`).
- **Breaking:** renamed the `Distribution` type alias to `Sampler`.
- **Breaking:** removed `truncated_2erlang` in favor of `TruncatedErlang`.
- Policies (`LumsCor`, `Slar`, `ConWIP`, `ContinuousRelease`, `slar_limit`) now
  self-wire in `__init__` and accept a scalar norm.
- `build_router` validates server count and documents the `arrival_process`
  mean=1/rate contract.
- Shared workload-norm validation and corrected-load fit check across policies.

### Fixed

- `logger`: defer finalizer handler removal to avoid loguru lock self-deadlock.
- examples: run all release-trigger systems at the Scenario-derived arrival rate.

### Docs

- Align installation page Python floor with `requires-python` (>=3.11).
- Numerous tutorial/example/api-reference updates for the Scenario refactor.

### CI

- Add `pytest-timeout` guard; force Agg backend in the Pyodide smoke test.

## 0.11.0 — 2026-06-04

### New

- Three new builder functions in `simulatte.builders`:
  `build_conwip_system`, `build_continuous_release_system`,
  `build_starvation_avoidance_system` — one-call setup for ConWIP,
  workload-controlled continuous release, and starvation-avoidance policies.
- PyPy 3.11 compatibility: simulatte now runs under
  [PyPy 3.11](https://pypy.org/) with identical results to CPython for a given
  seed. The CI matrix includes a PyPy 3.11 lane.
- In-browser runnable code blocks on simulatte.dev: examples tagged `# run`
  execute via Pyodide directly in the browser, with plot output rendered inline.

### Changed

- `requires-python` floor lowered from `>=3.12` to `>=3.11`, enabling use on
  CPython 3.11 and PyPy 3.11.
- `import simulatte` no longer eagerly imports `matplotlib`; the import is
  deferred to first use, reducing startup time.
- `specs/` and `plans/` directories excluded from the sdist.

### Fixed

- SQLite logger now finalizes cursors before committing, fixing a correctness
  issue on PyPy where deferred cursor cleanup caused errors.

### Documentation

- Documentation completely restructured into a layered, tab-first information
  architecture: Introduction, Guides, Tutorials, Examples, API Reference,
  Development. All example sections are now runnable in-browser.

## 0.10.0 — 2026-05-31

### New

- **Four dispatching rules** added to `simulatte.dispatching_rules`, extending
  the catalog with three new scheduling families:

  - **`work_in_next_queue`** (`dispatching_rules.work_content`) — Work In Next
    Queue (WINQ): orders by the total processing time queued at a job's next
    machine, feeding soon-to-starve downstream stations (queue-only; a job on
    its last operation → 0). Blackstone, Phillips & Hogg (1982, *IJPR* 20(1),
    27–45).
  - **`apparent_tardiness_cost(lookahead, *, avg_processing=None, weight=None)`**
    (`dispatching_rules.tardiness_cost`) — Apparent Tardiness Cost (ATC):
    `(w/p)·exp(−max(0, d−p−t)/(k·p̄))`. The average processing time `p̄`
    defaults to live computation from the server's queue, with an optional
    fixed override. Vepsäläinen & Morton (1987, *Management Science* 33(8),
    1035–1047).
  - **`cost_over_time(lookahead, *, weight=None)`**
    (`dispatching_rules.tardiness_cost`) — Cost Over Time (COVERT):
    `max(0, 1−max(0, slack)/(k·RPT))/p`, reducing to WSPT when tardy. Carroll
    (1965); job-shop form Russell, Dar-El & Taylor (1987, *IJPR* 25(10)).
  - **`raghu_rajendran(*, utilization=None)`** (`dispatching_rules.composite`) —
    Raghu & Rajendran (RR): `exp(u)·p + (s/RPT)·exp(−u)·p + WINQ`, a
    minimum-index composite weighted by the current machine's utilization (live
    by default, fixed override accepted); the raw slack `s` may be negative.
    Raghu & Rajendran (1993, *IJPE* 32(3), 301–313).

  ATC and COVERT return a negated cost index (higher cost → served first); WINQ
  and RR return their index directly. All are `(job, server) → float` callables
  usable as `Router(priority_policies=…)` or `ProductionJob(priority_policy=…)`,
  grouped into the new `dispatching_rules.work_content`,
  `dispatching_rules.tardiness_cost`, and `dispatching_rules.composite` modules.

## 0.9.0 — 2026-05-29

### New

- **`Draco` release policy** (`simulatte.policies.Draco`) — non-hierarchical WIP
  control merging release, authorization, and dispatching into a single
  per-server decision on each job completion. Implements Kasper, Land &
  Teunter (2023, *IJPE* 257, 108768) in full-DRACO configuration
  (`w^R=0.25, w^A=0.25, w^D=0.5`). `Draco.__init__` uses the
  active-construction pattern: pass `shopfloor`, `router`, and optionally
  `psp` and it self-wires all hooks on construction (mirroring `Slar`).
  Builder: `build_draco_system()`.

- **`Focus` dispatching rule** (`simulatte.dispatching_rules.Focus`) — five
  composable scheduling mechanisms (SPT, starvation avoidance, slack,
  pacing, WIP-balance entropy) unified into a single `(job, server) →
  float` priority callable. Implements Thürer, Land & Stevenson (2023,
  *Omega* 114, 102726). Builder: `build_focus_system()`.

### Changed

- **`Draco` constructor** is active (like `Slar`): pass `shopfloor`,
  `router`, and optionally `psp`, and hook registration happens on
  construction. No manual wiring required.

## 0.8.0 — 2026-05-28

### New

- **Dispatching-rule catalog** in `simulatte.dispatching_rules`. Adds six stateless rules — `shortest_processing_time`, `earliest_due_date`, `operational_due_date` (Land, Stevenson & Thürer, 2014), `modified_operational_due_date` (Baker & Kanet, 1983), `critical_ratio` (Berry & Rao, 1975) and `first_come_first_served` — plus a parameterized factory `slack_per_remaining_operation(allowance)` (Kanet, 1982) alongside the existing `planned_slack_time`. All are `(job, server) → float` callables usable as `Router(priority_policies=…)` or `ProductionJob(priority_policy=…)`. Rules are grouped by scheduling family across `dispatching_rules.processing`, `dispatching_rules.due_date`, and `dispatching_rules.slack`.
- **`BaseJob.unfinished_routing`** — property returning the servers whose operations have not yet completed (servers not yet exited), including the in-progress one. Used by `critical_ratio` and `slack_per_remaining_operation` to compute remaining processing time and the remaining-operation count.

### Changed (breaking)

- `builders.spt_priority_policy` removed; use `simulatte.dispatching_rules.shortest_processing_time` instead.
- `BaseJob.planned_slack_time` property removed; use `BaseJob.planned_slack_time_at(server, allowance=…)` or the `simulatte.dispatching_rules.planned_slack_time(allowance)` rule factory instead.

## 0.7.0 — 2026-05-26

### New

- **`SlarLimit` release policy** — SLAR with per-server workload-norm upper bounds, preventing superfluous load even when SLAR's base rule would allow a release. Derived from Thürer & Stevenson (2021), *Int. J. Production Economics*, 231, 107881. Available via `simulatte.policies.SlarLimit` and `build_slar_limit_system()`.
- **`simulatte.dispatching_rules` package** — new home for dispatching-rule callables. Introduces `planned_slack_time(allowance)`, a factory producing `(job, server) → float` PST priority callbacks (Land & Gaalman, 1998).

### Changed (breaking)

- **`Slar` constructor** now accepts `shopfloor`, `psp`, and optional `router` and self-registers its completion trigger and starvation-avoidance hook on construction. Previous pattern of `Slar(allowance_factor=k)` + manual `env.process(on_completion_trigger(…))` no longer works as-is; replace with `Slar(shopfloor=sf, psp=psp, router=router, allowance_factor=k)`.
- `Slar.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.
- `LumsCor.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.

### Documentation

- Auto-generated API reference page (`/reference`) powered by mkdocstrings.
- New tutorial section covering SLAR-Limit usage and dispatching rules.
- Updated builder comparison table in the release-control tutorial.

## 0.6.1 — 2026-05-26

### Fixed
- **Dynamic priorities in server queues.** `Server.sort_queue` now re-evaluates `job.priority_policy(job, server)` for every queued request before sorting, so priorities computed from `env.now`, external state, or runtime policy reassignment take effect immediately. Previously, `req.key` was frozen at request-construction time and never refreshed.
- **Automatic refresh at every dispatch decision.** `Server._trigger_put` is now overridden to call `sort_queue` automatically on both the new-arrival and release dispatch paths. Dynamic priority changes no longer require an explicit `sort_queue()` call from user code.

### Documentation
- Added a *Dynamic priorities* tutorial section to the release-control-and-dispatching guide, covering time-dependent policies, runtime policy reassignment, mutable external state, the `priority_policy` purity contract, and cost model.
- Updated docstrings on `Server`, `ServerPriorityRequest`, and `Server.sort_queue` to describe the snapshot-vs-live distinction between `req.priority` and `req.key`, and the mechanism of the automatic refresh.

## 0.6.0 — 2026-05-10

### Added
- **Intralogistics subsystem** (`simulatte.intralogistics`): full warehouse-to-warehouse material transport via AGV fleets, including:
  - `LayoutGraph` with `Node`/`Arc` and Dijkstra/A* pathfinding
  - `Warehouse` with per-SKU inventory, finite pick/put slots, and deadlock-safe operations
  - `AGV` with state machine, trapezoidal speed profiles, and battery lifecycle
  - `FleetCoordinator` orchestrating dispatch, travel, pick, deliver, reposition, and charge
  - `TrafficManager` protocol with `FreeTrafficManager` and `ResourceBasedTrafficManager` (node capacity enforcement and deadlock resolution)
  - `ChargingStation` (recharge and swap) and `ParkingArea`
  - Pluggable policies: `NearestIdleStrategy`, `RoundRobinStrategy`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`
  - `EMAOrderMetrics` and `DefaultIntralogisticsCollector` with `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`
  - `build_simple_system()` builder for quick setup
- **ConWIP release policy** (`simulatte.policies.conwip`): Constant Work-In-Process release with shop-wide job count cap and EDD selection
- **ContinuousRelease policy** (`simulatte.policies.continuous_release`): workload-controlled continuous release using corrected aggregate load norms
- Three progressive intralogistics examples: simple, intermediate (manufacturing plant floor), and advanced (multi-warehouse distribution hub)
- AI coding agent skill for intralogistics (`simulatte-intralogistics`)
- Intralogistics documentation section with overview and examples walkthrough

### Removed
- Experimental AGV, Warehouse, and MaterialCoordinator modules (`simulatte.experimental.agv`, `simulatte.experimental.warehouse`, `simulatte.experimental.materials`, `simulatte.experimental.builders`, `simulatte.experimental.job`, `simulatte.experimental.typing`) — replaced by the `simulatte.intralogistics` subsystem

### Fixed
- Fleet pending-queue starvation: retry counter now increments even when capable AGVs exist but none are idle
- Default `pending_retry_delay` changed from 0.001 to 1.0 for realistic retry behavior
- `DefaultIntralogisticsCollector` no longer accesses private `_pending_queue` (uses `pending_count` property)

## 0.5.0 — 2026-04-29

- Add `SimulatteEnv` Gymnasium wrapper for RL integration (`simulatte.experimental.gymnasium`)
- Improve web documentation structure and content
- Bump `actions/deploy-pages` from 4 to 5

## 0.4.0 — 2026-04-29

- Add `is_idle` and `current_jobs` properties to `Server`
- Add `release()`, `jobs_starting_at()`, and `on_arrival()` callback to `PSP`
- Support sync callbacks in `OperationHook` protocol
- Add post-init hook registration: `on_before_operation()` / `on_after_operation()`
- Add `on_processing_end()` callback to `ShopFloor`
- Add `Dispatcher` protocol and `attach_dispatcher()` for one-call hook wiring
- Rewrite `starvation_avoidance` as a `psp.on_arrival()` callback
- Bump CI dependencies (actions/download-artifact, upload-artifact, upload-pages-artifact, configure-pages, codecov)

## 0.3.0 — 2026-04-28

- feat: add simulatte:dev agent skill for Claude Code integration
- fix: SLAR policy refactor and type safety improvements (#4)
