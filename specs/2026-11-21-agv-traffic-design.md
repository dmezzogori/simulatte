# AGV traffic control, motion and routing

- **Status:** revision 3, approved by Davide on 2026-11-21 with the answers recorded in §20. Revision 2
  incorporated a builder check of revision 1 (§21). Nothing is implemented; approval is not verification
  evidence.
- **Baseline:** Simulatte 0.17.0, `main` at `ea1292c6c5c1d8f2abc18a20f610da107cc8863c`.
- **Branch:** `feature/agv-traffic`, from `main`. One pull request and one release for all three parts.
- **Supersedes:** decision D61 and [SP2 §7](2026-10-10-sp2-layout-design.md#7-traffic-semantics-approved-a1--d61)
  (node-only traffic, distance-only routing, no per-arc resources).
- **Amends:** [SP1 §5.2, §6.4, §6.5](2026-10-08-sp1-events-trace-design.md) (state schemas, event catalog, motion
  description) and [SP2 §6, §8.1, §10](2026-10-10-sp2-layout-design.md) (edge properties, schema version, traffic
  owner validation). The amendment paragraphs are appended to those documents by the last task of the routing
  plan.
- **Plans:** [control](../plans/2026-11-21-agv-traffic-a-control.md),
  [motion](../plans/2026-11-21-agv-traffic-b-motion.md),
  [routing and release](../plans/2026-11-21-agv-traffic-c-routing.md), executed in that order.
- **Decision ids:** `T1`–`T24` are recorded in §4 of this document only. There is no separate log; D61 gets a
  one-line note in [studio-decisions.md](studio-decisions.md) saying this design supersedes it.

## 1. Outcome and boundaries

### 1.1 Intent

Today the only congestion-aware traffic manager turns ordinary congestion into failed orders or permanent
gridlock, and the default manager has no vehicle interaction at all. Waiting, queueing and blocking between AGVs
therefore cannot be studied with Simulatte. This design replaces node reservation with claim-based control over
nodes, segments and zones, plans speed across nodes instead of per hop, and makes routing time-based and aware
of traffic. Route choice, traffic control and the reaction to a conflict become three separate, replaceable
parts.

Davide listed nine limitations of the current implementation and asked for all of them to be fixed, with higher
importance on 1, 5, 6, 7 and 8.

### 1.2 Goals

Each goal names the limitation it removes.

| # | Limitation today | Goal |
|---|---|---|
| 1 | Any shared future node between two paths is a conflict, with no notion of time; the outcome is a reroute or a failed order | Conflicts are settled in time: an AGV waits for the resources it needs and proceeds when they are free |
| 2 | Deadlock is inferred from a 30 s timeout and never resolved | Deadlock is detected exactly from the wait relation, at the moment it forms, and resolved by a replaceable policy |
| 3 | Idle AGVs keep their node forever; `ParkingArea.enter` is never called | Idle AGVs leave service nodes, make way when they block a claim, and parking capacity is real |
| 4 | A travel failure is terminal and not retried | Congestion never fails an order; only an unreachable destination does |
| 5 | Only nodes are resources; no segment or zone control, no vehicle length, no turning | Nodes, segments and zones are resources; vehicles have a length; turning costs time |
| 6 | Each hop is a stop-to-stop trapezoid; distance is the chord between node coordinates | Speed is carried through nodes; free-flow travel time does not depend on how a line is split into nodes; arcs can carry a length |
| 7 | Planners minimise distance and see traffic only through an `avoid` list | A router sees the network and the traffic state and minimises time; time-based and congestion-aware routers are built in |
| 8 | The reaction to a conflict is hard-coded in `FleetCoordinator._travel`, which probes the manager with `getattr` | `_travel` only executes; `Router`, `TrafficController` and `TrafficPolicy` are protocols with no private probing |
| 9 | An interrupted AGV snaps back to the node it left; waiting and partial hops cost no energy | An interrupt takes effect at the next node where the AGV can stop; energy follows distance covered, plus an optional idle power |

### 1.3 Non-goals

- AGVs waiting between nodes, and several AGVs accumulating on one arc. An AGV stops only at nodes. Following on
  a long arc is obtained by splitting it into blocks (§6.1, §12), which §9 makes free in travel time.
- Replanning speed in the middle of an arc when a claim is granted there (§9.3 states the resulting limit).
- Curved lane geometry in the layout package. `Arc.length` is the hook for it.
- A space-time (time-window) reservation planner. The router interface must allow one later (§10.1).
- Turn restrictions at junctions, lane width, two-dimensional footprints and swept-area collision checks.
- A proof of deadlock freedom on arbitrary graphs.
- A new `AGVState` for blocked AGVs. Blocked time is observable through events and a collector (§11.4).
- Occupancy-aware charger choice, changes to dispatch order, and fixes to `ReorderPointPolicy`.
- Drawing reservations, zones or routes in Studio. The trace carries them; Studio only has to keep working (§13).

### 1.4 Anti-goals

- A run that hangs silently, or that fails or drops an order because of traffic.
- A timeout used as a deadlock detector.
- Results that change with `PYTHONHASHSEED`, with the presence of observers, or with the order of set iteration.
- Free-flow travel times that depend on node discretisation.
- A coordinator that reaches into a controller, router or policy through attributes outside its protocol.

### 1.5 Constraints

- Python >= 3.11, CPython 3.11–3.15 and PyPy 3.11 lanes, no new runtime dependency, the existing 99% branch
  coverage gate, and the observer-invariance requirement (`tests/core/test_invariance.py`,
  `tests/intralogistics/test_invariance.py`).
- Every state change is a typed event behind `if env.wants(Cls)`, and replaying deltas reproduces live state at
  every event (SP1 §6). Event construction calls no user code.
- All APIs are unstable (repository `CLAUDE.md`). Old names are removed without aliases; the migration is
  documented (§14).
- Without a `Layout`, explicit `LayoutGraph` scripts keep working with the renamed arguments. Layout mode keeps
  the single-graph, single-resource-owner rule of SP2 §10.
- The default stays free traffic and shortest-distance routing. Travel times still change for every model,
  because of §9.

## 2. Evidence from the current code

| Existing implementation | Consequence |
|---|---|
| `traffic.py:200-213`: `check_path` compares future nodes of registered intents; `fleet.py:851-890` reroutes around the conflict or returns `MISSION_FAILED` | Removed. Waiting on resources replaces path checks (§7) |
| `fleet.py:1059-1116`: `_enter_with_timeout` waits `deadlock_timeout`, reroutes or backs off three times, then gives up; the AGV keeps its node while backing off | Removed. Detection uses the wait relation (§7.8); thresholds belong to the policy (§7.9) |
| `fleet.py:893`, `fleet.py:1111`: `getattr(manager, "deadlock_timeout")`, `getattr(manager, "priority")` | Removed. The protocol is complete (§7.1) |
| `fleet.py:897-969`: per hop, `math.hypot` distance, `travel_time`, `enter_node`, `timeout`, `leave_node`; both nodes are held for the whole hop | Replaced by the executor (§8) and the speed plan (§9); release follows the vehicle's tail (§6.6) |
| `fleet.py:974-979`, `events.py:161-170`: an interrupt mid-hop clears `motion`; the AGV stays at the node it left; no energy is charged | Replaced by stop requests (§8.3) |
| `fleet.py:609-612`, `650-655`: `MISSION_FAILED` sets `FAILED` with `travel_failed`; nothing re-queues | Only `NO_ROUTE` fails; traffic re-queues (§8.4) |
| `fleet.py:677-695`, `policies.py:118-122`: the default `StayInPlace` leaves the AGV on the delivery bay; `parking.py:37`: the coordinator never calls `enter`/`leave` | §7.10 |
| `speed.py:84-108`: `TrapezoidalProfile.travel_time` always starts and ends at rest | §9.1 adds `limits`; the plan spans arcs |
| `pathfinding.py:52`, `95`: edge weight is the chord length; `speed_limit` is ignored | §10 |
| `graph.py:25-30`: `Arc(source, target, bidirectional, speed_limit)`; no length, no lane count | §6.1 |
| `layout/network.py:633-645`: a `both` edge becomes two unrelated directed `Arc`s; every polyline vertex is a node; crossings become nodes | Segments are keyed by the unordered node pair (§6.3); no lane identity is needed at run time |
| `layout/layout.py:318-331`: `_validate_fleet` compares `type(manager)` with the two built-in classes | Validation by protocol (§12) |
| `layout/schema/layout-v1.schema.json`: `schema_version` is the constant `"1.0"`, `additionalProperties: false` everywhere; `studio/packages/trace/src/layout-schema.ts` carries a copy | New fields need schema 1.1 on both sides (§12) |
| `studio/packages/scene/src/motion.ts`: `trapezoidal` is validated by recomputing a rest-to-rest duration; `project.ts:122-141` interpolates on the straight line between the two node positions; entities without a position are drawn in an "unplaced" row | A new curve is needed for nonzero entry and exit speed; new entity kinds must be skipped (§11.3, §13) |
| `tests/intralogistics/test_builders.py:312-326` pins a digest for `build_simple_system`; `tests/fixtures/traces/generated/*.expected.json` are generated goldens | Re-pinned in parts A and B, with the reason in the PR (§17) |

Two scenarios were run on the baseline with `ResourceBasedTrafficManager` on the five-node T layout of
`examples/building_an_agv_system.py` (`STORE_OUT–C1–C2–LINE_IN`, `PARK` off `C1`). With two AGVs and three
simultaneous orders, two orders fail at t=0 with "No alternative path". With one AGV picking at `STORE_OUT` and a
second dispatched nine seconds later, both orders fail after about 300 s, both AGVs stay `IDLE` holding
`STORE_OUT` and `C1`, and a third order submitted 1,500 s later fails the same way. Both are acceptance
scenarios in §17; Appendix A gives their exact setup.

## 3. Approaches considered

1. **Patch the node manager.** Return a delay instead of a conflict and add cycle detection. Cheap, and it
   addresses limitations 1–4, but it leaves 5, 6, 7 and 8, which are the ones ranked highest.
2. **Claim-ahead control on a resource graph, with three protocols (chosen).** AGVs claim nodes, segments and
   zones ahead of them and wait at nodes when a claim is not granted. It mirrors block control in real AGV
   systems, produces waiting and queueing, stays event-driven and deterministic, and gives the motion and routing
   work a state to build on.
3. **Space-time reservation planning.** Every route is planned conflict-free in time. It solves 1 and 7
   together, but random pick, put, load and charging durations invalidate the planned windows continuously, so
   it needs a replanning layer on top, and parked vehicles need special handling. It remains possible later
   behind the `Router` and `TrafficView` interfaces.

Davide chose approach 2 on 2026-11-21.

## 4. Decisions

Author's calls unless marked. Each is justified in the section named.

- **T1.** An AGV stops only at nodes. Segments admit by direction and by length, but a leg is claimed together
  with its end node (§6.3, §7.2).
- **T2.** Both directions between two nodes share one segment. `Arc.passing` (default false) says whether
  opposite traffic may pass on it (§6.1).
- **T3.** Resources behind a vehicle are released when its tail clears them. `AGVType.length` and `min_gap`
  define the tail. `length` has no default and is required with `ReservationController` (Davide, §20); 0.0 is
  allowed and means a point vehicle (§6.6).
- **T4.** Dead-end branches are entered with one atomic claim from the node before the root to the stop inside.
  No zone is derived for them (§6.5, §7.2).
- **T5.** Single-lane two-way chains between junctions are derived as corridors with a same-direction token,
  claimed one leg early (§6.4, §7.2).
- **T6.** Authored zones are exclusive with a capacity. Corridors are derived only (§6.4).
- **T7.** Claims are all-or-nothing and granted in priority, then request order, with a fairness gate on
  resources that have a direction or a length budget (§7.4).
- **T8.** Granted claims are never revoked by the controller (§7.5).
- **T9.** Deadlock detection is the standard reduction over stopped, waiting AGVs; it has no false positives
  (§7.8).
- **T10.** The traffic policy is a collaborator of `ReservationController`, shared by all fleets that share the
  controller (§7.9).
- **T11.** An unresolvable deadlock is reported and left standing; it fails no order (§7.8, §8.4).
- **T12.** Service nodes (bays, charging nodes) are cleared after use when the controller controls occupancy
  (§7.10).
- **T13.** A parking slot is taken when the repositioning decision is made and released at departure (§7.10).
- **T14.** Kinematic limits are sampled when an AGV starts from rest and hold until it next stops (§9.2).
- **T15.** Speed is replanned at node events only (§9.3).
- **T16.** A new motion curve `piecewise` replaces the idea of extending `trapezoidal` (§11.3).
- **T17.** `Router` replaces `PathPlanner`; the default is `ShortestDistanceRouter`, so default decisions do not
  change (§10).
- **T18.** `DispatchStrategy.select` and `RepositioningContext` receive the router and the view (§10.4).
- **T19.** Layout schema 1.1 adds `passing`, `block_length` and `zones` (§12).
- **T20.** No container format change in the trace; new kinds and events use the catalog (§11.5).
- **T21.** Free traffic remains the default controller (approved by Davide).
- **T22.** Mid-arc replanning, curved lanes and a time-window router are out of this round (approved by Davide).
- **T23.** Traffic schedules no timer while the traffic state does not change, and resolutions and re-queues are
  bounded, so a run with a standing deadlock ends when its event queue drains (§7.9, §8.4).
- **T24.** An AGV with cargo on board never gives up a travel; without cargo, giving up re-queues the order a
  bounded number of times (§8.2).

## 5. Vocabulary

- **Leg:** the traversal of one arc `u → v`.
- **Segment:** the physical lane piece between two adjacent nodes, shared by both directions.
- **Zone:** a named set of nodes and segments with an exclusive capacity. **Corridor:** a derived chain with a
  same-direction rule.
- **Resource:** a node, a segment, a zone or a corridor.
- **Claim:** the set of resources an AGV needs for one or more consecutive legs, granted all at once.
- **Horizon:** the last node up to which an AGV holds granted claims.
- **Run:** the motion of an AGV from rest to its next rest.
- **Extent:** `AGVType.length + AGVType.min_gap`.
- **Disposition** of an AGV, as traffic sees it: `moving`, `waiting` (at rest with a pending claim), `serving`
  (loading, unloading, charging, turning), `idle`, `stranded`.

## 6. Traffic network

### 6.1 Graph additions

`Arc` gains two trailing fields, so positional construction is unchanged:

```python
@dataclass(frozen=True)
class Arc:
    source: Node
    target: Node
    bidirectional: bool = True
    speed_limit: float | None = None
    length: float | None = None  # None: the chord between the endpoints
    passing: bool = False  # opposite traffic may pass on this arc
```

`length` must be finite and not smaller than the chord (relative tolerance 1e-9); otherwise `ValueError`.
`LayoutGraph.length(source, target)` returns the effective length and raises `ValueError` when no arc exists.
`LayoutGraph.path_length(path)` sums it. `distance` and `path_distance` keep their chord meaning. Two arcs
between the same pair of nodes must agree on `length` and `passing`, or construction raises.

`LayoutGraph.subdivide(max_length)` returns a new graph in which every arc longer than `max_length` is replaced
by `ceil(length / max_length)` equal pieces, without the piece that float noise in the quotient would add (the
quotient is reduced by a relative 1e-9 before the ceiling, as the layout's `block_length` does). Original `Node`
instances are kept. New nodes lie at equal fractions
of the chord, are shared by both directions, and get deterministic ids derived from the two endpoint ids in
sorted order and the piece index. Pieces inherit `bidirectional`, `speed_limit` and `passing`, and split `length`
equally: each piece is the parent's `length` divided by the number of pieces. It is not available on a layout-frozen graph (§12 covers layouts).

### 6.2 Nodes

A node's capacity is the first of: an explicit capacity (layout node capacity, or the controller's
`capacities` mapping); the sum of the capacities of the facilities on the node (`ParkingArea` capacity,
`ChargingStation` slot count); the controller's `node_capacity` (default 1). If an explicit capacity is smaller
than the facility sum, the controller logs a warning at activation.

### 6.3 Segments

There is one segment per unordered pair of adjacent nodes. Its directions are the traversals the graph allows.
Its length is the arc length. Its entity id is the two node ids in sorted order joined by `~`.

A segment is claimed with a direction. T1: although a segment may admit several vehicles (§7.3), a leg's claim
always contains the end node as well, so a second vehicle can be on the same segment only when the end node has
capacity for it.

### 6.4 Zones and corridors

`Zone(id, members, capacity=1, hold_back=False)` is authored. `members` are nodes and node pairs (segments). At
most `capacity` AGVs hold the zone. An AGV holds a zone while it holds any member. With `hold_back`, the zone is
also claimed one leg early (§7.2).

Corridors are derived at finalization (§7.7) when `derive_corridors` is true (the default). A corridor is a
maximal chain of at least two segments such that:

- every segment is traversable in both directions and has `passing == False`;
- every interior node has exactly two neighbours in the whole graph, ignoring direction, and capacity 1;
- both end nodes (the **ports**) are places where vehicles can pass each other: three or more neighbours, or
  capacity above 1.

Its members are its segments and interior nodes; the ports are not members. A chain that leaves a port and returns
to the same port is a corridor too, with that port at both ends. A corridor has two orientations, `forward` from the
port with the smaller id (for a chain back to its own port, the direction whose first interior node has the smaller
id). Its id is deterministic from its member ids. Chains that end in a
leaf are dead-end branches (§6.5), not corridors. A cycle with no port is not derived; the controller logs it
once at activation.

### 6.5 Dead-end branches

A dead-end branch is found from every node with exactly one neighbour (a leaf), whatever its capacity: walk
away from the leaf through nodes with exactly two neighbours and capacity 1 until the first node with three or
more neighbours or capacity above 1, the **root**. A leaf with capacity above 1 ends a walk as a root too. The
branch is the starting leaf, the walked nodes and the segments between them and to the root. The root is not a
member. If the walk reaches a leaf of capacity 1, the component is a plain line with no passing place and has no
branch. On a line whose two end leaves both have capacity above 1, each interior node belongs to two branches,
one per direction; a leg enters the branch in which it moves away from the root. A leaf is never a corridor
port. Branches carry no resource of their own; they
only shape claims (§7.2). `hold_back_dead_ends` (default true) turns the rule off.

### 6.6 Vehicle extent and release

`AGVType` gains `length: float | None = None` and `min_gap: float = 0.0`. A fleet whose AGVs have a type without
a length cannot register with `ReservationController`: `register_fleet` raises `ValueError` naming the type.
`FreeFlowController` does not need it. The extent is `length + min_gap`. The executor keeps an odometer per
travel and a trail of held resources with the odometer value at which the tail clears each:

- a node, when the front is one extent past it;
- a segment, when the front is one extent past its far node.

A resource is released when the odometer reaches its clear point. If the AGV stops first, it stays held. With
extent 0 a node is released at the instant the AGV leaves it and a segment at the instant the AGV reaches its
far node. A zone or corridor is released by the controller when the AGV holds none of its members and has no
pending entry (§7.5).

A node of capacity above 1 is a place with room for whole vehicles (a bay with several slots, a charger, a
parking area, a passing place): when an AGV comes to rest on one, every entry of its trail clears, and the
executor releases them with the resources that clear on arrival. Without this rule two vehicles with a positive
extent that rest on such a node each keep the segment they arrived on in its arrival direction, and neither can
leave back over it. An AGV that passes such a node without stopping keeps its ordinary clear points.

The trail follows the path travelled: the body is taken to be the last extent of that path. The model does not
track a rigid body through a reversal, so what a reversing vehicle longer than its last arcs would sweep or
overhang is not held.

The time at which the odometer reaches a value inside the current arc is exact for kinematic profiles (§9.1).
For other profiles, a clear point at the arc start is released at departure and every other clear point is
released at the next arrival.

## 7. Claims and the controller

### 7.1 Protocol

```python
class TrafficController(Protocol):
    controls_occupancy: bool
    view: TrafficView

    def register_fleet(self, fleet: FleetCoordinator) -> None: ...
    def place(self, agv: AGV, node: Node) -> None: ...
    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim: ...
    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest: ...
    def stopped(self, request: ClaimRequest) -> None: ...
    def idle(self, agv: AGV) -> None: ...
    def withdraw(self, request: ClaimRequest) -> None: ...
    def release(self, agv: AGV, resources: Collection[Resource]) -> None: ...
    def retain(self, agv: AGV, resources: Collection[Resource]) -> None: ...
```

- `place` records the initial position at activation. It raises `RuntimeError` when the node, or a zone that
  contains it, is not admissible. An AGV placed inside a corridor holds it without an orientation.
- `next_claim` returns the claim that starts at leg `index` of `route`: its resources and the number of legs it
  covers (`Claim.legs >= 1`).
- `request` returns a `ClaimRequest` whose `event` succeeds with `Granted`, or with a directive that the
  controller has already staged through `hooks` (§7.9). An AGV has at most one pending request.
- `stopped` tells the controller that the AGV is now at rest and waiting on the request. The wait, its events,
  deadlock detection and policy calls start here. The executor calls it at once when a request made at rest is
  not granted immediately.
- `idle` tells the controller that the AGV has just come to rest without a travel where it stands: it became `IDLE`
  or `STRANDED`. The coordinator calls it on every transition to `IDLE` and to `STRANDED`. `ReservationController`
  uses it for the waits the AGV blocks (§7.10); `FreeFlowController` does nothing.
- `withdraw` cancels a pending request. `release` frees specific resources. `retain` frees everything the AGV
  holds except the given resources.
- `WaitHooks` carries what the controller and the policy may ask of the waiting travel: `route` (remaining
  nodes), `loaded`, `resolutions` (the reactions staged for the AGV since it last arrived at the goal it travels to,
  §7.9), and the callables `try_reroute(avoid, max_cost_ratio)`,
  `try_step_aside(target)` and `give_up()`, each returning whether the action was staged.

Nothing else on a controller is read by the coordinator.

### 7.2 Building a claim

For the leg `u → v` of a route, the base claim is the segment `{u, v}` in that direction, the node `v`, and
every zone or corridor that contains the segment or `v`. Resources the AGV already holds are left out. Three
rules extend it:

- **Dead ends (T4).** If the leg enters a dead-end branch, or the leg's end node is the root of a branch that the
  next leg enters, the claim also covers every following leg down to the route's final node. A route that enters
  a branch ends inside it, because routers return simple paths. When the route starts at the root or inside the
  branch and goes deeper, the claim covers all remaining legs. Legs that leave a branch are claimed one at a
  time.
- **Corridors (T5).** If the next leg enters a corridor from one of its ports, the claim for the leg into that
  port also contains the corridor with the orientation of travel. When the route starts at the port, the first
  leg carries it. A leg inside a corridor that the AGV already holds with a different orientation, or with none,
  contains the corridor with the leg's orientation. Likewise a leg over a segment the AGV still holds in the
  other direction (it reverses while its body is on the segment) contains the segment with the new direction.
  These are the two cases in which a held resource is claimed again; a grant then changes the recorded
  direction.
- **Zones with `hold_back`.** The claim for the leg before the first leg that takes a member also contains the
  zone.

The purpose of all three is the same: an AGV that cannot get in must wait where it does not block the way out.

### 7.3 Admission

A resource is admissible for claimant `a` given the other holders `H` (the claimant's own holdings never block
it):

- node: `len(H) < capacity`;
- segment, direction `d`, single lane: every holder has direction `d`, and `H` is empty or the extents of `H`
  plus `a`'s extent do not exceed the length. With `passing`, the same test over the holders with direction `d`
  only;
- zone: `len(H) < capacity`;
- corridor, orientation `o`: no other holder has the opposite orientation. Holders without an orientation do
  not count. The same test applies to an AGV that already holds the corridor and sets or reverses its own
  orientation. An AGV keeps its orientation while it stays inside, including when it becomes idle there.

A claim is admissible when all its resources are.

### 7.4 Queueing and grants

Pending claims are ordered by priority (higher first, from the controller's `priority_fn(agv)`, default 0) and
then by a request counter. After every request, release, withdrawal or waiver the controller scans the queue in
order and grants every claim that is admissible and not gated.

**Fairness gate (T7).** A claim is gated on a segment or corridor `r` when an earlier, non-yielding pending claim
names `r`, is inadmissible on `r`, and each of its other resources is either admissible or blocked only by AGVs
that hold `r`. The gate stops a stream in one direction from starving a waiter in the other: the stream's
vehicles leave through the waiter's port and hold it on their last leg, which must not reopen the gate; for that
reason a vehicle keeps the corridor until it releases the port it left through (§7.5). A claim is never gated on
a resource its own AGV already holds. Gates never cause a reported deadlock; §7.8 waives the ones that would.

A grant records the holdings, emits one `traffic.reserved` (§11.2) and succeeds the request's event.

### 7.5 Release

Releases come from the executor (`release` at clear points, `retain` when a travel ends or its route changes).
The controller releases a zone or corridor itself when the AGV's last member is released, with three
exceptions. A zone or corridor held ahead of entry under a hold-back rule stays; `retain` drops those. A zone or
corridor named by the AGV's pending claim stays, because the pending claim was built on holding it; for the same
reason the resources of a pending claim that the AGV already holds are determined when the claim is granted, not
when it is requested. A corridor that the AGV left onto one of its ports stays for as long as the AGV holds that
port, and is released in the same event as the port. A port of capacity above 1 is often a parking or
charging node where the vehicle that left comes to rest; a record kept for as long as it stays would hold the
corridor oriented and block every opposite entrant. So on such a port the record lasts while the vehicle passes
or waits there for its next claim, and is dropped, with the corridor if the AGV holds none of its members, when
its travel ends on the port (the `retain` of a travel that ends or changes route at rest there). Only a `release` at a clear point counts as leaving: when
`retain` drops the last members (a travel that ends before entering), the corridor goes with them. The executor
therefore releases what clears on arrival before it calls `retain` at the end of a travel. T8: the controller never takes a granted resource
back. Every release emits one `traffic.released` and triggers a scan.

### 7.6 `FreeFlowController`

`controls_occupancy` is false. `next_claim` returns an empty claim that covers the rest of the route. `request`
returns an already granted request. It creates no entities and emits no events. It is the default. It serves
one graph, the one its view exposes to routers: registering a fleet on a different graph raises `ValueError`.
Fleets on different graphs each take their own controller, as they do by default.

### 7.7 `ReservationController`

```python
ReservationController(
    *, env, graph,                      # LayoutGraph or GraphRef
    node_capacity=1, capacities=None,   # Mapping[Node, int]
    zones=(), derive_corridors=True, hold_back_dead_ends=True,
    policy=None,                        # default StandardTrafficPolicy()
    priority_fn=None,
)
```

Fleets register in their constructor. The network (capacities, segments, zones, corridors, branches) is finalized
by an activation initializer that the controller registers at construction, so it runs before any fleet places
its AGVs. Registering a fleet after finalization raises. Finalization attaches one `segment` entity per segment
and one `zone` entity per zone and corridor, in id order. Node bindings are still created by the coordinator. A
node without a binding records no reservation and emits none, as today.

### 7.8 Deadlock detection

Let `W` be the AGVs that are at rest with a pending claim. Detection runs when an AGV joins `W`:

1. `S = W`.
2. Remove from `S` any AGV whose claim would be admissible if only the holdings of the other members of `S`
   existed. Gates are ignored.
3. Repeat until nothing is removed. The remainder `D` is deadlocked.

Holdings of AGVs outside `W` are ignored, so `D` is never a false positive: its members cannot proceed even if
everyone else releases everything. A real deadlock is found when its last member stops.

The reduction then runs a second time with gates applied, among the claims of `S`. Every gating claim that
belongs to that larger remainder is marked yielding, which removes its gate until it is granted, and the queue
is rescanned. This produces no policy call and no event. Only `D`, computed without gates, goes to the policy.

When `D` is not empty the controller asks the policy for attempts (§7.9) and stages the first that succeeds,
then emits `traffic.deadlock` with the members, the victim and the action. Removing one member does not always
break the deadlock (the victim may have been waiting behind a cycle it is not part of), so after the staged
attempt and the rescan the reduction runs again, and the resolution repeats for the new `D`, with one
`traffic.deadlock` per victim, until `D` is empty or no attempt succeeds. Each round removes one member from
`W`, so the rounds are bounded by its size. If no attempt succeeds it emits `traffic.deadlock` with action
`unresolved` and logs an error, once per distinct set, and the members keep waiting (T11). Any later release
rescans as usual. The AGV whose stop started the detection gets its `on_blocked` call (§7.9) when its wait is
still open and it is not a member of a set left unresolved.

### 7.9 Policy

```python
class TrafficPolicy(Protocol):
    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]: ...
    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]: ...
    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None: ...
```

`Wait` holds the AGV, its claim, the start time, the blockers (each with its disposition and the resources it
blocks), the number of reviews, the priority read when the request was made, and the values the `WaitHooks`
callables `route`, `loaded` and `resolutions` return at the instant (not the hooks themselves). `loaded` means cargo on board
(`agv.current_load`), not the `loaded` argument of the travel: a charging diversion travels unloaded in name
while carrying cargo. `resolutions` counts the reactions staged for the AGV since it last arrived at the goal it
is travelling to. Reactions are frozen values:

- `KeepWaiting(review_after=None)`: always succeeds; with a delay, `on_blocked` is called again after it, subject
  to the rule on reviews below;
- `Reroute(avoid, max_cost_ratio=None)`: succeeds when the fleet's router finds a route that avoids the given
  resources, whose first claim is admissible now, and, when a ratio is given, whose cost is at most that ratio
  times the cost of the router's route to the goal without `avoid`. An AGV that stands on its goal when asked (it can be there in
  the middle of a step-aside excursion that passes through it) has arrived: the reroute succeeds with the
  one-node route and the travel ends with `ARRIVED`;
- `StepAside(target=None)`: succeeds when the target, or the node found by the standard search below, can be
  reached with a first claim that is admissible now;
- `GiveUp()`: succeeds when the travel's `give_up()` hook accepts (§8.2); the travel then ends with `GAVE_UP`.

The controller tries the returned reactions in order. `on_blocked` is called when a wait starts and at each
review. The policy never moves anything itself.

**Reviews (T23).** The first review of a wait is scheduled `review_after` after the wait opens. After a review,
the next is due `review_after` later if a grant or release happened anywhere since the previous review;
otherwise no timer is scheduled: the wait sleeps until the next grant or release and the delay starts then. A
blocker that strands also ends the sleep: the wait is reviewed at that instant (§7.10), since a stranding may release
nothing. The
grant or release that wakes a sleeping wait is consumed by the wake-up: the review it schedules is followed by
another only if something changed after the wake-up, so one change gives one review. A network in which nothing
moves therefore schedules at most one review per wait. A wait that ends takes its review timer with it: the
timer is removed from the event queue (`Environment.unschedule`), so it neither fires nor advances the clock.

**Stuck waits.** When a review ends in `KeepWaiting` and every blocker of the wait is idle, stranded or stalled,
or is itself waiting in a wait that is stuck by the same rule (a cycle of such waits counts), the controller
emits `traffic.wait_stuck` and logs a warning, once per wait. It is a report, not a deadlock: deadlock detection
(§7.8) ignores the holdings of AGVs that are not waiting, so two waiting vehicles that block each other only
because a resting one holds a slot between them are not a deadlock, and without this rule nothing would report
them.

**Standard search.** From a node, search outwards by router cost over legs, the first claim admissible now, for
the cheapest node that has free capacity and does not lie ahead on the remaining route of an AGV in the waits
concerned, unless the node would still have a free slot for each of them after the move. Ties break by node id.
For an AGV that will be idle on arrival (§7.10), the node of the cheapest parking area with a free slot is taken
first; failing that, the node must not be a service node. For such an AGV a node that any pending claim names is
not taken either, unless it would still have a free slot for each of those claims after the move: an idle AGV
moved out of one wait's way must not land in another's, or two waits could send it back and forth without end.
The search asks the router with the purpose and the load weight of the move it is for, so that the search and
the move agree. Three more rules apply to an AGV that will be idle on arrival. The root of a dead-end
branch (§6.5) is not taken unless it is the node of a parking area: an idle AGV on the root stops every vehicle
that must hold back for the branch, and the node where they hold back is often its only way out. Members of a
branch stay valid targets, since on a layout made of dead ends a leaf is the natural place to rest. A
candidate is not taken when a segment, zone or corridor of the last leg of its route is named by a pending
claim, nor when a zone or corridor of that leg is inadmissible for the AGV now: a node inside a full zone is no free
rest, though its own capacity has room, and the AGV would stop short of it and block the way. And a node the AGV has already made way from in the current chain of make-way moves is not taken
(§7.10). A make-way move has one fallback: when no node satisfies these rules, a service node or a root is taken
if its capacity is above 1 and it would still have a free slot, after the move, for each AGV in the waits
concerned and for each pending claim that names it. For this fallback the segment of the last leg may be
named by a pending claim (zones and corridors still may not): the AGV comes to rest on a node of capacity above
1, where its trail clears (§6.6), so it does not keep that segment. On a line whose only passing place is a charging node with
two slots, this is where an idle AGV in a bay steps out so that the next one can come in.

`StandardTrafficPolicy(*, reroute_after=30.0, max_detour=1.5, max_resolutions=8)` (keyword-only):

- `on_blocked`: with a stranded blocker, `Reroute(avoid=its resources)` then `KeepWaiting(reroute_after)`. On the
  first call otherwise, `KeepWaiting(reroute_after)`. At a review, `Reroute(avoid=blocked resources,
  max_cost_ratio=max_detour)` then `KeepWaiting(reroute_after)`. A `Reroute` is offered only while the wait's
  `resolutions` is below `max_resolutions`; after that the answer is `KeepWaiting(reroute_after)` alone, so a
  travel between standing blockers is not rerouted back and forth without end.
- `on_deadlock`: order the members by priority (lowest first), unloaded before loaded, latest wait first, id.
  First, `GiveUp` for each unloaded member whose `resolutions` reached `max_resolutions`. Then, for the members
  below that bound, `Reroute(avoid=the resources its claim is blocked on)` in order, then `StepAside()` in
  order.
- `make_way_target`: `None`, which selects the standard search.

A member at the bound is no longer moved by the standard policy. If nobody else can act, the deadlock is
reported as unresolved and stands (§7.8). This bounds the work a tight layout can cause.

### 7.10 Idle AGVs, service nodes, parking

- **Make way.** When a wait starts or is reviewed, or when an AGV becomes `IDLE`, each idle blocker of a wait is
  asked to move: the controller takes `make_way_target`, or the standard search, and calls
  `blocker.fleet.make_way(blocker, target, concerned=...)` with the AGVs of the waits it makes way for. The coordinator runs it as a short travel (`TRAVELING_EMPTY`, then
  `IDLE`, then the pending-queue check and the `on_agv_idle` hooks). One make-way move per AGV at a time. If no
  target exists the blocker stays and the waiter's `on_blocked` reviews continue. The idle case comes through
  `idle(agv)` (§7.1): the controller asks the AGV to make way once, for the waits it blocks taken in request
  order, and when the AGV does not move it checks those waits for the stuck condition (§7.9) at once, since a
  sleeping wait has no review left to report it. `make_way` refuses a target the fleet's router cannot
  reach, and an AGV whose make-way move ended on the node it started from is not asked again at the same
  instant, so a move that fails at once cannot repeat without time passing. An AGV does not make way to a node
  it has already made way from in the same chain of moves, which bounds a chain by the number of nodes. The
  chain ends, and the memory is cleared, when the AGV starts a mission or becomes idle on a node where it blocks
  no wait. Nor does it start a make-way move from a node it already made way from in the chain: a move that is cut
  short before it leaves (a deadlock resolved by giving up, which a travel without an order always accepts)
  would otherwise be started again at every review, each attempt being itself the traffic change that keeps
  the reviews coming.
- **Service nodes (T12).** A service node is a bay of one of the fleet's warehouses or the node of one of its
  charging stations. `FleetCoordinator(clear_service_nodes=True)`: when the controller controls occupancy, an AGV
  that would become idle on a service node, and for which the repositioning policy returned no target, first
  travels to the standard-search target. If there is none it stays. An AGV does not clear while the pending
  queue holds an order: it becomes `IDLE` where it stands and the pending-queue check runs.
- **Parking (T13).** When the target of a repositioning, a service-node clearance or a make-way move is the node
  of a parking area with a free slot, the coordinator calls `area.enter(agv)` at the decision, which is granted
  at once, and `area.leave(agv)` when the AGV next departs from that node or when the travel does not arrive. A
  full area is not a valid target. This applies with both controllers. AGVs placed on a parking node at
  activation take no slot.
- **Stranded and serving blockers** are left to `on_blocked`. When an AGV strands, the coordinator calls `idle(agv)`
  (§7.1) and each open wait the AGV blocks gets one review at that instant, as after a release: the policy can reroute
  around the stranded blocker, and a wait that keeps waiting is reported stuck by the rule of §7.9. A stranded AGV is
  not asked to make way, a review timer the wait had is dropped, the review schedules a new one only by the usual rule
  (T23), and nothing else is scheduled. Charging and stranding themselves do not change (§8.4).

Assigning `AGV.current_node` directly after activation still bypasses traffic, as today.

## 8. Travel execution

### 8.1 The executor

`FleetCoordinator._travel` becomes `intralogistics/travel.py`. For `travel(agv, goal, loaded)`:

1. If the AGV is at the goal, return `ARRIVED`. Ask the router for a route; with none, return `NO_ROUTE`. Emit
   `agv.routed`.
2. **Claim.** While there is no pending request, the route has legs beyond the horizon, and the horizon is
   nearer than the end of the next arc plus the look-ahead (§9.3): take `next_claim` and `request` it. A grant
   extends the horizon by `claim.legs` and repeats this step. Grants that arrive while the AGV is moving run this
   step from the request's callback, without waking the travel process.
3. **Wait.** If the AGV is at the horizon with a pending request, it is at rest (§9.2). Call `stopped`, then wait
   for the request's event. `Granted` returns to step 2. A directive replaces the route (reroute, step aside) or
   ends the travel (`GAVE_UP`); after a step-aside move the AGV routes to the goal again.
4. **Plan.** Plan the speeds from the current node to the horizon (§9.2), after applying stop requests (§8.3)
   and the energy horizon (§9.5). If the AGV is at rest and cannot enter the next arc for lack of energy, run the
   existing charging-or-stranded branch.
5. **Turn.** At rest with a heading change and a positive `turn_time`, wait that long.
6. **Traverse** the next arc: set `agv.motion`, emit `agv.move_started`, wait to each clear point and `release`
   there, wait to the end of the arc.
7. **Arrive:** deplete the battery, relocate the AGV, emit `agv.move_ended`, advance. At the goal the speed is
   0: `retain` the resources the body overlaps and return `ARRIVED`. Otherwise return to step 2.

Whenever a travel ends or its route changes, the executor withdraws any pending request and calls `retain` with
the resources the body overlaps. With `FreeFlowController` the whole route is one horizon and the plan is
computed once per travel.

### 8.2 Outcomes

| Outcome | Cause | Mission |
|---|---|---|
| `ARRIVED` | at the goal | continues |
| `RETRY_FROM_CURRENT_POSITION` | battery critical on arrival at a node other than the goal, or a charging diversion ended | as today |
| `BATTERY_STRANDED` | no energy for the next arc and no reachable charger | as today |
| `NO_ROUTE` | the router found no route | `FAILED` with `travel_failed`, cargo returned first, as today's `MISSION_FAILED` |
| `GAVE_UP` | a `GiveUp` directive accepted by the travel's `give_up()` hook | see below |

`_TravelOutcome.MISSION_FAILED` and `_EnterOutcome` are removed.

**Giving up (T24).** The coordinator's `give_up()` hook decides by what the travel is for:

- cargo on board, whatever the travel (delivery, cargo return, a charging diversion after pickup): refused;
- an order mission before pickup, including a charging diversion inside it: accepted while the order has been
  re-queued for traffic fewer than `FleetCoordinator(max_traffic_requeues=3)` times, refused afterwards. The
  order goes back to `PENDING` with reason `traffic_requeued`, is unassigned and appended to the pending queue;
  the AGV becomes `IDLE` where it stands, and §7.10 applies. On this path the controller is told first
  (`idle`, §7.1), so that the AGV can make way before the pending queue is checked, and the pending-queue check
  of that instant does not offer the order back to the AGV that gave it up: otherwise the nearest idle AGV,
  which is usually the same one, would take the order again at once, re-enter the same wait and spend every
  re-queue without time passing;
- a travel with no order (repositioning, make way, clearing a service node, charging after a mission):
  accepted; the travel ends and the AGV becomes `IDLE` where it stands.

### 8.3 Interrupts and stop requests

An interrupt, a critical battery found on arrival at a node other than the goal, and the energy horizon are
stop requests. A critical battery found on arrival at the goal does not change the outcome: the travel returns
`ARRIVED` and the energy check before the next arc handles it. A critical level read at a node passed at speed
is a stop request whatever level the plan expected there. A stop request
truncates the plan to the first node of the horizon at which the AGV can be at rest given its speed and
deceleration. For an AGV at rest, or waiting, that is the current node and the request takes effect at once.
When the AGV is at rest the executor withdraws its pending request, calls `retain`, and then:

- for an interrupt, emits `agv.move_interrupted` with the node and the cause, and raises the interrupt in the
  mission. Further interrupts received before rest are dropped; the flags their senders set still apply;
- for the battery, returns `RETRY_FROM_CURRENT_POSITION` or runs the stranded branch.

The AGV is therefore always at a node, at rest, with energy charged for the arcs it covered, when the mission
reacts.

An arc whose travel time is not finite (a stalled vehicle, §9.1) is never left. Nothing is released at its
departure, the AGV counts as at rest at the departure node, and a stop request takes effect at once: the
executor emits `agv.move_ended` at the departure node, which clears the motion with no change of position or
battery, and then proceeds as above.

### 8.4 Failure semantics

Traffic produces no `FAILED` order and no dropped cargo. Stranding, cancellation and `NO_ROUTE` behave as today.
A standing deadlock or a stuck wait leaves its orders in their current status, with `traffic.deadlock` or
`traffic.wait_stuck` in the event stream.

**Termination (T23).** `env.run()` without `until` must still return in those states, as it does today, where
many tests and examples rely on it. Traffic contributes no perpetual timer (§7.9), and resolutions and re-queues
are bounded (§7.9, §8.2). The one remaining poller is `FleetCoordinator._pending_retry_loop`, which today ticks
for as long as any order is pending. With the built-in dispatch strategies it must tick only while a pending
order is unserviceable (so that such orders still exhaust their retries); otherwise it stops, and the existing
pending-queue check on every transition to `IDLE` restarts dispatch. Custom strategies keep the polling loop.

## 9. Motion

### 9.1 Kinematic interface

`SpeedProfile` is unchanged. A profile may also implement

```python
def limits(self, load_weight: float, battery_level: float) -> Kinematics   # v_max, accel, decel
```

`TrapezoidalProfile` does, with the values `_effective` computes today before the speed limit. A non-positive
`v_max` or `accel` means the AGV cannot move: the run stalls as today (`t_end = +inf`, `stalled`). A profile
without `limits` is traversed arc by arc, rest to rest, with `travel_time` and `motion` as today.

`intralogistics/kinematics.py` is a pure module: arc profiles as lists of constant-acceleration phases, position
and speed at a time, the time at a position, and the planner of §9.2.

### 9.2 Speed plan

T14: `Kinematics` is sampled when the AGV starts from rest and holds for the run. For the run's nodes `k..m`
(current node to horizon), with arc lengths `L_j`, arc caps `u_j = min(v_max, speed_limit_j)`, acceleration `a`
and deceleration `d`:

- node caps: `c_k` is the current speed, `c_m = 0`, and for interior nodes
  `c_j = min(u_{j-1}, u_j, corner_j)`, with `c_j = 0` where the AGV must turn at rest (§9.4);
- backward pass: `b_m = 0`, `b_j = min(c_j, sqrt(b_{j+1}^2 + 2 d L_j))`;
- forward pass: `f_k = c_k`, `f_{j+1} = min(b_{j+1}, sqrt(f_j^2 + 2 a L_j))`;
- on arc `j` the AGV accelerates from `f_j` to the peak
  `p = min(u_j, sqrt((2 a d L_j + d f_j^2 + a f_{j+1}^2) / (a + d)))`, cruises, and decelerates to `f_{j+1}`.
  Phases of zero duration are omitted.

The plan always ends at rest at the horizon. Because horizons only grow (T8) and the limits are fixed for the
run, the current speed never exceeds what the backward pass allows. The plan is recomputed at each node arrival
with the horizon held then (T15); the profile of an arc is fixed when the AGV enters it.

Consequence for goal 6: with no corner cap, a straight line has the same free-flow time however many nodes split
it.

### 9.3 Look-ahead

The look-ahead distance is the braking distance `v_max^2 / (2 d)` plus
`FleetCoordinator(lookahead_margin=0.0)`; it is 0 for profiles without `limits`. Step 2 of §8.1 requests claims,
at every node event, until the horizon lies at least that far beyond the end of the arc the AGV is about to
enter. The profile of that arc is then never shaped by the stop at the horizon: the backward pass of §9.2 gives
a cap of at least `v_max` at the arc's end.

Requirement: when every claim is granted at once, an AGV under `ReservationController` has exactly the travel
time it has under `FreeFlowController`. Goal 6 and the discretisation anti-goal hold for both controllers.

An AGV therefore holds, ahead of itself, the next arc and its braking distance. With rest-to-rest profiles it
holds one claim ahead.

Known limit (T15, T22): a claim that is granted late, after the AGV entered an arc whose profile already brakes
for the horizon, does not change that arc's profile. The AGV finishes the arc as planned, at worst stopping at
the node, and replans there. The cost is at most one deceleration and acceleration per such node, and it shrinks
with the block length.

### 9.4 Turning

`AGVType` gains `corner_speed: Callable[[float], float] | None = None` and
`turn_time: Callable[[float], float] | None = None`. Both take the heading change at a node in radians, computed
from the chords of the incoming and outgoing arcs. `corner_speed` caps the speed through the node. A positive
`turn_time` forces a stop at the node and adds that wait before departure. An AGV's heading is that of the last
arc it traversed and is unknown after placement; an unknown heading turns for free. Both default to no effect.
A heading change below 1e-9 rad is taken as 0, so that rounding in the coordinates of collinear nodes does not
turn a straight continuation into a turn. At rest at a node the order is: the energy check for the next arc
(§8.1), then the turn wait, then the kinematic limits of the new run. The claim for the next arc is requested on
arrival and stays pending during the turn wait; the AGV reports a traffic wait (`stopped`) only if the claim is
still not granted when the turn is done, so a traffic wait counts the time lost beyond the turn.

### 9.5 Energy

- Each arc depletes the battery on arrival by `depletion(length, load_weight, average speed)`, as today with the
  arc length in place of the chord.
- **Energy horizon.** The plan covers only arcs whose cumulative estimate `estimate_energy(length, load_weight,
  u_j)` fits the battery level. A truncated plan stops at the last affordable node, where the existing branch
  runs. The plan also ends at the first node other than the goal at which the estimated level is critical, so
  that the AGV is at rest at the node where the critical-battery branch runs and does not pass a charger at
  speed with a battery it knows will be critical.
- **Idle power.** `AGVType.idle_power: float = 0.0`, energy per unit time in every state except `CHARGING`. It
  accrues lazily: the level is brought up to date at each event of the AGV that reads or changes the battery,
  and `agv.battery_changed` is emitted when an accrual outside a move changes it. No timer wakes an idle AGV to
  charge; the next dispatch or travel finds the level.

## 10. Routing

### 10.1 Protocol and view

```python
@dataclass(frozen=True)
class RouteRequest:
    agv: AGV | None  # None for a request that is not made for a vehicle
    origin: Node
    destination: Node
    load_weight: float = 0.0
    avoid: frozenset[Resource] = frozenset()
    purpose: str = "travel"


@dataclass(frozen=True)
class Route:
    nodes: tuple[Node, ...]  # a simple path from origin to destination
    cost: float  # the router's own unit; comparable only within one router


class Router(Protocol):
    def route(self, request: RouteRequest, view: TrafficView) -> Route | None: ...
```

`TrafficView` is read-only: `graph`, `length(u, v)`, `speed_limit(u, v)`, `capacity(node)`, `branch_root(node)` (whether the node is the root of a dead-end branch, §6.5; false when
`hold_back_dead_ends` is off),
`resources(u, v)` (what a leg needs), `holders(resource)`, `pending(resource)` (count of pending claims that still
need it, given the current holdings), `disposition(agv)`, `route_of(agv)`, `waiting_on(agv)` (the AGVs whose waits a resource held by `agv`
blocks), `admissible(agv, claim)`, `blockers(agv, u, v)` (for each resource of the leg from `u` to `v` that the
holders make inadmissible for `agv` now, whatever the queue and the gate say, the holders that block it, in the
leg's resource order) and `mean_hold(resource)`. With
`FreeFlowController` the dynamic members report an empty network. A space-time router can later be built on the
same request and a view extended with reservation times; nothing here depends on that.

### 10.2 Built-in routers

- `ShortestDistanceRouter(algorithm="dijkstra")` (or `"astar"`): cost is `path_length`; tie-breaking by node id
  as today. Default (T17).
- `FastestRouter()`: arc cost `length / min(v_max, speed_limit)` with `limits` for the request's load and the
  AGV's battery level now (idle power accrued, §9.5), or `travel_time` for profiles without `limits`. Node cost: `turn_time` plus the time lost
  slowing to `corner_speed` and regaining speed; where `turn_time` is positive the vehicle stops, so the corner speed
  is 0 there, and a profile without `limits` is at rest at every node, so its turns cost `turn_time` only. With a
  turning model the search state is the node and the incoming arc. A request without an AGV is routed by path
  length.
- `CongestionAwareRouter(base=FastestRouter(), *, wait_weight=1.0, horizon=60.0)` (`wait_weight` and `horizon`
  keyword-only; `base` must be exactly a `ShortestDistanceRouter` or a `FastestRouter`, any other router or a subclass
  raises `TypeError`): adds to each leg
  `wait_weight * w * exp(-t / horizon)`, where `t` is the cost accumulated when the leg is expanded and `w` is the
  largest, over the leg's resources that are not admissible now, of `(blocking holders + pending claims) *
  mean_hold(resource)`, read from `view.blockers`, `view.pending` and `view.mean_hold`. A resource held to
  capacity by stranded AGVs (every one of its blocking holders is stranded) is avoided. Without an AGV in the
  request no penalty applies, and with `wait_weight=0` the router is its base router exactly (no penalty and no
  exclusion). It is a heuristic and does not claim an optimum.

`mean_hold` is an exponential average (factor 0.1) of observed hold durations per resource, maintained by
`ReservationController` whether or not anything observes it.

### 10.3 Rerouting

Reroutes happen only through `Reroute` reactions (§7.9): at a blocked review, in deadlock resolution and for
stranded blockers. Every route assignment emits `agv.routed` with its reason.

### 10.4 Users of route cost

`DispatchStrategy.select(order, fleet, context)` receives a `DispatchContext` with `graph`, `router`, `view` and
`cost(agv, node)`. `NearestIdleStrategy` minimises the cost to the best output bay. `RepositioningContext` gains
`router` and `view`; `NearestParkingPolicy` uses cost. The coordinator chooses bays and chargers by cost, and the
reachable-charger energy estimate uses the route's arc lengths. A ranking asks the router with the purpose and
the load of the travel it ranks for, and keeps the tie rules of before this work: the first candidate of the
sequence among equal costs for bays, chargers and parking areas, the AGV id for dispatch. With the default router
every one of these decisions equals today's, except on a graph with an arc whose explicit `length` exceeds its
chord: the old rankings summed chords there, and the route cost sums arc lengths, as the executor does.

## 11. Events, state and trace

### 11.1 State schemas

| Kind | Id | State fields |
|---|---|---|
| `node` | `Node.id` | unchanged: `x`, `y`, `agvs`, `reserved_by` |
| `segment` | sorted node ids joined by `~` | `source`, `target`, `length`, `forward` (agv ids, grant order), `backward` |
| `zone` | zone id, or the derived corridor id | `rule` (`exclusive`, `same_direction`), `capacity` (null for corridors), `members` (ids), `reserved_by` (agv ids, grant order), `orientation` (`forward`, `backward` or null) |

`agv` gains `heading` (radians, the direction of the last arc traversed, null after placement); `motion` keeps
`{from, to, t_start, t_end, description}`. `segment` and `zone` entities exist
only with `ReservationController`.

### 11.2 Events

| Type | Change |
|---|---|
| `traffic.reserved` | payload `agv`, `resources` (ids); one event per grant or placement, with the deltas of every resource |
| `traffic.released` | payload `agv`, `resources`; one event per release call |
| `traffic.wait_started` | emitted by `stopped`; payload `agv`, `node` (next node), `resources`, `blockers` (agv ids), `reason` = `occupied` |
| `traffic.wait_ended` | `reason` in `granted`, `rerouted`, `stepped_aside`, `gave_up`, `interrupted` |
| `traffic.wait_stuck` (new) | `agv`, `blockers` (agv ids with their dispositions); once per wait (§7.9); no deltas |
| `traffic.deadlock` (new) | `agvs` (sorted ids), `victim` (id or null), `action` in `reroute`, `step_aside`, `give_up`, `unresolved`; no deltas |
| `agv.routed` (new) | `agv`, `origin`, `destination`, `nodes`, `cost`, `reason` in `travel`, `blocked`, `deadlock`, `step_aside`, `make_way`, `clear_node`; no deltas |
| `agv.move_started` | unchanged payload; `motion` may be `piecewise` |
| `agv.move_interrupted` | emitted at the node where an interrupted AGV came to rest; `agv`, `node`, `reason`; no deltas |
| `agv.battery_changed` | also emitted for idle-power accrual outside a move |
| `order.status_changed` | new reason `traffic_requeued`; `travel_failed` now means no route |
| `parking.entered`, `parking.left` | now emitted in normal operation (§7.10) |

No `traffic.reserved` or `traffic.released` is emitted when nothing is recorded: a grant or a release whose resources
have no entity (a node without a binding) changes no state and emits nothing. Removed wait reasons: `node_occupied`,
`path_delay`, `deadlock_backoff`, `cancelled`, `elapsed`. A pending
look-ahead request of a moving AGV is not a wait and emits nothing.

### 11.3 Motion curve `piecewise` (T16)

```json
{"curve": "piecewise", "v0": 0.8, "phases": [[1.25, 0.8], [3.1, 0.0], [0.6, -0.8]], "distance": 6.4}
```

Each phase is `[duration, acceleration]`. It is valid when there is at least one phase, every duration is
positive and finite, the durations sum to `t_end - t_start` within the tolerance already used for `keyframes`,
the speed never goes below zero beyond 1e-9, and the integrated distance equals `distance` within
`max(1e-9, 1e-9 * distance)`. The position fraction is the integrated distance over `distance`. An invalid
description falls back to linear with the approximate flag, as other curves do. Python and TypeScript share
generated fixtures. `trapezoidal` stays valid and is still what a profile's own `motion` may return.

### 11.4 Collector

`TrafficKPIs(fleet)` in `intralogistics/metrics.py` subscribes to the traffic events and declares,
per fleet: blocked time (sum of closed waits, clipped to the observation window), number of waits, stuck waits,
deadlocks resolved and unresolved (the action taken is in each `traffic.deadlock` event), reroutes, make-way moves
and re-queued orders. Declarations follow SP1 §12.

### 11.5 Trace format (T20)

No container, record or digest-algorithm change. New kinds and event types go through the catalog. Old traces
stay readable. Semantic digests of intralogistics runs change with this work.

## 12. Layout integration

- **Schema 1.1 (T19).** Readers accept `"1.0"` and `"1.1"`. A 1.0 file with a 1.1 field is an error that names
  the field. `save` writes 1.1 only when a 1.1 field is present. New fields, all physical inputs in the
  dependency closure and the physical hash:
  - `passing` (boolean) on lanes, explicit arcs, connectors, the lattice network and edge overrides;
  - `network.block_length` (positive number): after overrides, every enabled edge longer than it is split into
    `ceil(length / block_length)` equal pieces. Generated nodes get a new address kind derived from the parent
    edge address and the piece index, deterministic ids, and the network default capacity. Pieces inherit the
    parent edge's effective properties. Overrides cannot target a piece or a generated node. `max_nodes` and
    `max_directed_arcs` apply after splitting;
  - `network.zones`: a map of zone id to `{capacity, hold_back, members}`, where `members` is a list of the
    element addresses and selectors that overrides already accept. A member that matches nothing is a stale
    diagnostic, like a stale override. Members are matched against the network the overrides see, before
    splitting; a matched edge then stands for all its pieces and the generated nodes between them, so a zone
    covers the same stretch with and without `block_length`.
- **Resolution.** `resolve_network` sets `Arc.passing`, leaves `Arc.length` unset (lanes are straight) except on the
  pieces of `block_length`, which carry the parent's length divided by the number of pieces, and counts the pieces
  with the rule of `LayoutGraph.subdivide` (§6.1), so that a layout and the subdivided graph of the same nodes and
  arcs are the same network to the float; it adds
  `ResolvedNetwork.zones` as concrete nodes and node pairs.
- **Binding.** The built-in controller registers as the layout's resource owner as today and reads the graph,
  capacities and zones at bind. `_validate_fleet` accepts a controller with `controls_occupancy == False`
  without registration, requires the registered owner for `ReservationController`, and requires
  `register_resource_owner` for any other controller. It no longer compares classes.
- **Bundle.** The trace's layout bundle carries the resolved zones and generated nodes. A bundle is 1.1 exactly when
  its layers use a 1.1 field; a presentation re-resolution whose layers would change the bundle's version
  requires a rerun. The TypeScript schema
  copy and its tests follow.
- `build_simple_system` gains `traffic_controller=None`, `router=None` and `agv_length=None`, passed through.

## 13. Studio

- `studio/packages/scene/src/motion.ts` evaluates `piecewise` (§11.3), with tests against the shared fixtures.
- The scene projection skips `segment` and `zone` entities; they are neither placed nor listed as unplaced.
- `studio/packages/trace/src/layout-schema.ts` accepts schema 1.1.
- Nothing else changes. Position is still interpolated on the straight line between the two node positions.

## 14. Public surface and migration

New modules in `intralogistics/`: `kinematics.py`, `network.py` (resources, zones, derivation, claims),
`reservation.py` (`ReservationController` and its entities), `traffic_policy.py` (`StandardTrafficPolicy`),
`routing.py` and `travel.py`. `traffic.py` holds the protocols, the value types and `FreeFlowController`.
`pathfinding.py` is removed.

| Removed | Replacement |
|---|---|
| `TrafficManager`, `PathCheckResult` | `TrafficController`, `Claim`, `ClaimRequest` |
| `FreeTrafficManager` | `FreeFlowController` |
| `ResourceBasedTrafficManager(graph=, env=, node_capacity=, deadlock_timeout=, priority_fn=)` | `ReservationController(...)` (§7.7); `deadlock_timeout` has no equivalent, see `StandardTrafficPolicy.reroute_after` |
| `PathPlanner`, `DijkstraPlanner`, `AStarPlanner` | `Router`, `ShortestDistanceRouter(algorithm=...)` |
| `FleetCoordinator(traffic_manager=, path_planner=)` | `FleetCoordinator(traffic_controller=, router=, clear_service_nodes=, lookahead_margin=)` |
| `DispatchStrategy.select(order, fleet, graph)` | `select(order, fleet, context)` |

Added: `Arc.length`, `Arc.passing`, `LayoutGraph.length`, `path_length`, `subdivide`, `Zone`, `TrafficPolicy`,
`StandardTrafficPolicy`, the reactions, `TrafficView`, `Route`, `RouteRequest`, `FastestRouter`,
`CongestionAwareRouter`, `Kinematics`, `TrafficKPIs`, `FleetCoordinator.make_way`, `FleetCoordinator.best_bay`,
`DispatchContext`, the `RepositioningContext` fields `router` and `view` and its `cost`, and the `AGVType` fields
`length`, `min_gap`, `corner_speed`, `turn_time`, `idle_power`.

Docs to update: `docs/guides/intralogistics.md`, `docs/guides/layout.md` (the "Traffic reserves nodes only"
paragraph), `docs/tutorials/building-an-agv-system.md`, `docs/tutorials/logging.md` (event table),
`docs/api/intralogistics.md`, `docs/api/layout.md`, `docs/examples/intralogistics.md`, `docs/ai-skill.md`,
`docs/development/changelog.md` with a migration section, the three intralogistics examples, and the
architecture section of `CLAUDE.md`.

## 15. Determinism, purity, performance

- Every ordering is explicit: queues by priority and counter, entity creation by id, searches tie-broken by
  node id. No set or dict of nodes is iterated for a decision without a defined order.
- No randomness is added. Review timers and `mean_hold` exist whether or not anything observes them.
- Cost per leg is proportional to the resources of the claim. A scan pass is linear in pending claims, of which there
  is at most one per AGV, and at worst quadratic when gates apply. Detection runs only when an AGV starts
  waiting; its reduction is quadratic in waiting AGVs and its gate pass cubic at worst. Measured on a line of
  80 waiters, the last `stopped` call takes about 11 ms and a scan 0.1 ms. A stuck report propagates along the chain
  of waits behind it and recomputes the blockers of every wait it reports, which is cubic in the length of the chain,
  once per chain.
- With `FreeFlowController` the executor makes no controller calls per arc beyond the first claim.
- `benchmarks/intralogistics.py` is run before and after each part and reported; it stays ungated.

## 16. Delivery

Three parts, each its own plan, built in order on one branch and released together. Every task leaves the
test suite green; user documentation is written once, at the end.

**Part A, control (goals 1, 2, 3, 4, 5, 8).**

- A1: `kinematics.py` (profile evaluation only), `Arc.length`, `Arc.passing`, `LayoutGraph` additions.
- A2: network derivation and `ReservationController`: claims, admission, queue, gate, release, detection.
- A3: `Router` with `ShortestDistanceRouter`; the executor with rest-to-rest arcs; policy, make-way, service
  nodes, parking, outcomes, the pending-loop change of §8.4, events, `TrafficKPIs`; removal of the old API;
  docs. Interrupts already follow §8.3, which for rest-to-rest arcs means the AGV finishes its arc. `Reroute`
  with `max_cost_ratio` is enforced from this part on, with path-length cost.
- A4: layout schema 1.1, binding, TypeScript schema, scene skip.

**Part B, motion (goals 6, 9).** The planner, `limits`, look-ahead, turning, the energy horizon, idle power,
`piecewise` in Python and TypeScript.

**Part C, routing (goal 7).** `FastestRouter`, `CongestionAwareRouter`, `mean_hold`, cost users.

Part A alone gives waiting and queueing instead of failures. Parts B and C do not depend on each other.

## 17. Acceptance

Tests extend `tests/intralogistics`, `tests/layout` and the TypeScript suites.

- **Invariants.** A test helper replays any trace and asserts at every event: node holders within capacity; a
  single-lane segment never holds both directions; zone holders within capacity; a corridor never holds both
  orientations; an AGV at rest holds its node; an AGV that holds a member of a zone or corridor holds the zone
  or corridor. It runs over every scenario below and the three examples.
- **Baseline scenarios (Appendix A)** with `ReservationController`: all orders complete, none is `FAILED`, and
  in the second scenario the later AGV waits at `PARK`, not at `C1`.
- **Following.** Two AGVs on a subdivided one-way line: the second follows one block behind; neither fails.
- **Head-on.** A two-way corridor between two junctions: the second AGV waits before its port. A two-node line
  with two AGVs swapping: `traffic.deadlock` with `unresolved` once, no `FAILED` order, and `env.run()` without
  `until` returns.
- **Stuck wait.** A three-node line with a bay at each end, capacity 1 everywhere and two AGVs: one idle with no
  node to move to, one loaded and waiting. `traffic.wait_stuck` once, no timer left, `env.run()` returns.
- **Default builder.** `build_simple_system` with its two AGVs and a `ReservationController` completes a batch
  of transfer orders with no deadlock and no stuck wait: its parking node and charging node have capacity 2 and
  act as passing places.
- **Lone AGV.** One AGV under `ReservationController` has the same arrival times as under `FreeFlowController`,
  on the examples and on a line split into 1, 2 and 10 arcs.
- **Giving up.** An AGV carrying cargo on a charging diversion is never re-queued; an unloaded mission is
  re-queued at most `max_traffic_requeues` times.
- **Resolution.** A loop with a side node: a swap deadlock is resolved by reroute; a variant without an
  alternative route is resolved by step aside.
- **Fairness.** A stream in one direction does not delay an opposite waiter beyond the vehicles already inside.
  A gate that would complete a cycle is waived.
- **Idle and parking.** An idle AGV on a through node makes way; a bay is cleared after delivery; a parking area
  of capacity 2 never lists three AGVs; a full area is not chosen.
- **Capacity above one,** vehicle extent longer than a block, `passing` segments, authored zones with and without
  `hold_back`.
- **Vehicle length.** A fleet whose `AGVType` has no `length` cannot register with `ReservationController`.
- **Motion.** A straight line split into 1, 2 and 10 arcs has equal free-flow time within 1e-9 relative; speed
  limit changes, corner caps and turn waits match hand-computed values; `piecewise` descriptions integrate to
  the arc length and duration in both languages; a profile without `limits` reproduces today's hop times.
- **Stop requests.** A cancelled order: the AGV stops at the next feasible node, energy matches the arcs covered,
  the mission recovers as today. Critical battery during a run.
- **Routing.** `FastestRouter` prefers a longer, faster route; `CongestionAwareRouter` avoids a queue when the
  detour is cheaper; with the default router, dispatch and bay choices equal the baseline on the examples.
- **Replay and purity.** Replay equals live state for all scenarios with the new kinds; digests are identical
  across `PYTHONHASHSEED` values and with and without observers; CPython and PyPy agree.
- **Layout.** Schema 1.0 files still load; 1.1 fields round-trip; `block_length` and zones change the physical
  hash; an explicit-graph model and its layout twin give the same trajectories.
- **Free flow.** `FreeFlowController` creates no `segment` or `zone` entity and emits no traffic event.

The pinned digest in `tests/intralogistics/test_builders.py` and the generated golden traces are regenerated in
parts A and B; each PR states why they changed.

## 18. Risks

- **Size.** Part A is large. A2 is testable without the coordinator, which limits the risk.
- **Tight layouts.** A layout with no place to step aside produces standing deadlocks. That is reported, not
  hidden, but users may read it as a defect. The docs must say how to add passing places and parking. Two
  limits make this more likely on small layouts without parking: make-way is not transitive (an idle blocker
  whose only free neighbours are held by other idle AGVs stays), and the step-aside search excludes nodes ahead
  on the routes of the waits concerned even where a retreat exists.
- **Passing places defined by capacity.** The hold-back rules of §7.2 treat a port or a root of capacity above 1
  like any other: a vehicle that cannot enter the corridor or branch behind it waits one node before it. Two
  vehicles that meet head-on at such a node therefore deadlock and one steps aside, where they could have
  passed each other on the node. Letting a vehicle wait on the node itself is unsafe as a plain rule: with one
  slot taken by a resting AGV, the waiting vehicle takes the slot the oncoming one needs, and neither moves. A
  sound rule admits the waiting vehicle only while a slot remains for each oncoming one; it is not part of this
  release. Authoring `passing` segments, or a siding, gives a passing place that works today.
- **Look-ahead holds resources.** A fast AGV reserves its braking distance, which lowers density. This is the
  intended physical meaning, and `lookahead_margin` tunes it.
- **Heuristic router.** `CongestionAwareRouter` can choose worse routes than `FastestRouter` in some states. It
  is opt-in.

## 19. Left to the builder

Internal data structures; the exact id format of derived corridors and generated nodes, within the stated
constraints; the initial value of `mean_hold`; the file split of tests; the order of same-instant events,
provided replay equals live state and the order is deterministic; KPI declaration details; the wording of
diagnostics; whether `Wait` and `WaitHooks` are one object or two.

## 20. Resolved questions

Davide answered on 2026-11-21:

- **Base and sequencing.** Branch from `main` now; one release for all three parts.
- **Vehicle length.** Required with `ReservationController` (T3).
- **`clear_service_nodes`.** Default true (T12).
- **Decision log.** A separate log or none, at the author's judgment: none. §4 is the record.

## 21. Review record

A fresh reviewer read revision 1 as a builder on 2026-11-21 and returned seven questions. Revision 2 answers
them as the author's calls:

1. When the next claim is requested: the horizon must reach the look-ahead beyond the end of the next arc, and a
   lone AGV must match free-flow time (§8.1, §9.3).
2. The fairness gate lapsed whenever a stream vehicle held the waiter's port: the gate now ignores blockers that
   hold the gated resource, and only the set computed without gates goes to the policy (§7.4, §7.8).
3. Permanent blocks that are not deadlocks, and runs without `until`: `traffic.wait_stuck`, the rule on reviews
   and the pending-loop change (§7.9, §8.4, T23). The case the reviewer found on the default builder's line is
   also prevented: capacity above 1 now ends a dead-end branch and a corridor (§6.4, §6.5), and the standard
   search prefers parking (§7.9).
4. What "unloaded" means for giving up, and travels that serve no order (§7.9, §8.2, T24).
5. Corridor orientation for an AGV already inside (§7.2, §7.3).
6. The baseline scenarios could not be rebuilt from the text (Appendix A).
7. Whether the reroute cost ratio applies in part A: it does (§16).

## Appendix A. Baseline scenarios

Both use the graph of `examples/building_an_agv_system.py`: nodes `STORE_OUT (0, 0)`, `C1 (10, 0)`,
`C2 (20, 0)`, `LINE_IN (30, 0)`, `PARK (10, 10)`; two-way arcs `STORE_OUT–C1`, `C1–C2`, `C2–LINE_IN`, `C1–PARK`;
node capacity 1; no parking area and no charging station.

- One SKU, weight 5.0, volume 0.2.
- Warehouse `Storage`: input and output bay `STORE_OUT`, 3 slots, 50 units, `pick_time=5.0`, `put_time=3.0`.
- Warehouse `Line`: input and output bay `LINE_IN`, 3 slots, empty, `pick_time=2.0`, `put_time=2.0`.
- `AGVType`: `TrapezoidalProfile(max_speed=1.5, acceleration=0.8, deceleration=0.8)`, battery 1000.0, weight
  capacity 50.0, volume capacity 2.0, `load_time=5.0`, `unload_time=5.0`, `length=0.0`.
- Two AGVs: `agv-0` at `C1`, `agv-1` at `PARK`. Default dispatch, repositioning and load-recovery policies.
- Every order moves 2 units from `Storage` to `Line`.

Scenario 1: three orders submitted before activation; run until 400. On the baseline `order-0` completes at
51.52 and the other two fail at 0.

Scenario 2: `order-0` at 0, `order-1` at 9, `order-2` at 1509; run until 3000. On the baseline all three fail
(at 321.54, 317.54 and 1822.00) and the AGVs end `IDLE` at `STORE_OUT` and `C1`.

Expected with `ReservationController` and the standard policy: every order completes. In scenario 2, `agv-1`
waits at `PARK` under the dead-end rule while `agv-0` picks, and each idle AGV left at `C2` after a delivery
makes way to `PARK` when the other needs `C2`.
