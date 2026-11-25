# The Studio scene drawn as a map

- **Status:** approved by Davide on 2026-11-25. Nothing is implemented; approval is not verification evidence.
- **Baseline:** branch `feature/layout-only-models` at `66a5a96` (version 0.18.0 prepared, not released).
- **Branch:** `feature/studio-map`, from that baseline. Part of the 0.18.0 release.
- **Amends:** the [AGV traffic design](2026-11-21-agv-traffic-design.md) §1.3 (drawing reservations and routes in
  Studio is no longer a non-goal), §11.1 (the `agv`, `parking_area` and `charging_station` schemas gain fields),
  §11.2 (the sentence "It changes no state" of `traffic.wait_started`, `traffic.wait_ended` and `agv.routed`) and
  §13 (the scene no longer ignores `segment` and `zone` entities); the
  [SP3 viewer design](2026-10-11-sp3-viewer-design.md) §7 (scene layers and per-kind adapters) and §8 (the
  inspector); the [layout-only design](2026-11-23-layout-only-models-design.md) §5.5 (automatic placement of
  facilities). The last task of the plan appends an amendment note to each.
- **Decision ids:** `M1` to `M25`, recorded in §4 of this document only.
- **Builder check:** a reviewer read the first draft on 2026-11-25 as a builder would and checked it against
  the code; its eight corrections and nine questions are answered in the text (M18 to M25) and listed in §13.
- **Mockup:** [the page Davide agreed to](research/2026-11-25-studio-map-mockup.html) (an HTML fragment with
  inline SVG). Where it and §5 differ, §5 wins.

## 1. Outcome and boundaries

### 1.1 Intent

Davide opened two recorded intralogistics examples in Studio and could not read them. In his words he "had a bad
time understanding where the corridor was, the geometry of the environment the AGVs were moving in, and what they
were about doing"; the gap is "in recognizing the objects, their status, the flow of goods, being able to
visually distinguish each component at a glance, and the floor plan itself". The point is not visual appeal:
"the representation is too abstract".

He wants to watch the new traffic features in Studio before 0.18.0 is released, and later to build and run
simulations through an agent and look at their behaviour. The scene must also hold for the next release's
scenario: a transit point with 50 to 100 AGVs in three fleets, racks, staging areas and docks.

Shown three mockups, he chose the map: the floor plan to scale, read like a map; symbols of a fixed readable
size when zoomed out, true sizes when zoomed in.

### 1.2 Goals

1. The floor is drawn to scale: lanes as strips with a width, facilities at the place where they serve the
   network, a scale bar.
2. Every kind of element has its own sprite and can be told apart by eye. A vehicle shows its heading, its state
   and whether it is loaded.
3. Three levels of detail (M1): the map carries very little text; a panel says a bit more, one line per
   element; a click on an element shows everything about it.
4. Traffic is visible: which floor is held, which vehicle waits and for whom.
5. Goods are visible: what a vehicle carries, what a warehouse holds, where an order goes.
6. Every instant is correct, including after a seek: what the map and the panel show is a function of the
   recorded state at the cursor.
7. The sprite vocabulary has room for racks, staging areas and docks.
8. The viewer, the offline player, the export, the compare view and the layout editor keep working.

### 1.3 Non-goals

- Racks, staging areas and docks (0.19.0). Only their place in the vocabulary is reserved.
- A declared lane width in the layout (M5: later).
- Curved motion: a vehicle still moves on the straight line between two node positions.
- A panel in the offline player (M15).
- New sprites for production equipment (servers, pre-shop pool, jobs): they keep today's drawing, under the new
  zoom rule for labels.
- Interactive model editing beyond what Studio has; the performance of the standard search.

### 1.4 Anti-goals

- A view that is right only while playing forward. Nothing shown may depend on events the viewer happened to
  cross (goal 6).
- A second state implementation in the viewer: no reducer of event types in TypeScript (SP3 §4).
- Text on the map that grows with the fleet until it overlaps.
- State shown by colour alone: every state also has a mark or a shape (SP3 §7).
- A slower simulation for runs that do not record: the Python additions must cost nothing measurable when no
  subscriber wants the events.

### 1.5 Constraints

- The Studio rules already in force: deterministic scene state for a cursor; the export has its own scene and
  manual clock and never reads wall time; the offline player is one script under a CSP without `eval`, without
  remote assets or fonts; sprites are drawn with PixiJS primitives; trace labels are untrusted text; clock-bound
  values are written imperatively, not through React state.
- Browser matrix: Chromium, Firefox, Playwright WebKit; actual Safari by hand.
- Frame budget: p95 below 16.7 ms on the declared scene workload (SP3 §12).
- The layout file schema does not change (M4). The bundle written to a trace does, in its `targets` and its
  algorithm name, for the models §6.5 names.
- The Python additions touch files that Assignment 3 of the layout branch is changing (`reservation.py`,
  `travel.py`, `fleet.py`, pinned fingerprints). They are implemented after that assignment is complete, on the
  commit the release coordinator names (§9).

## 2. Evidence from the current code

Measured on 2026-11-24 and 2026-11-25 on `66a5a96`, from traces of `examples/intralogistics_traffic.py` and
`examples/intralogistics_advanced.py` recorded on this branch.

| Fact | Where |
|------|-------|
| Lanes are drawn as lines 2 px wide with an arrow; every placed entity is a rectangle, circle or diamond; labels appear only above 20 px per metre | `studio/packages/scene/src/index.ts` `draw`, `glyph` |
| A facility without an explicit position is packed in a grid near the origin. In the corridor example warehouse `East` is at (3, 3) and its bay node `E` at (95, 0); parking `Siding` at (9, 6), its node `S` at (45, 6) | `src/simulatte/layout/auto.py`; the trace's `resolved.placements` |
| The layout records which node a facility is bound to: `East:input_bays/0 -> E`, `Siding:node -> S`, `AGV-1:node -> S` | the trace's layout `bindings` |
| A vehicle's placement footprint is 1 x 1 m for every vehicle; `AGVType.length` and `min_gap` exist and are not written; there is no width | `layout/types.py` `placement_defaults`; `intralogistics/agv.py` |
| `network.graph.arcs` entries carry `source`, `target`, `speed_limit`, `passing` (traffic networks) and an optional `length`; `direction` and `enabled` are on `network.edges`. A disabled arc is absent from `graph.arcs`. Nothing carries a width; the only width-like value is the network `clearance` (0 by default) | `layout/bundle.py`; the trace's `network` |
| `agv` state: `node`, `state`, `battery`, `load`, `order`, `motion`, `fleet`, `heading`, `label` | `agv.py` `state_schema` |
| `order` state: `origin`, `destination`, `sku`, `quantity`, `status`, `agv`, `created_at`, `dispatched_at`, `picked_at`, `delivered_at`, `fleet` | the trace |
| Under `ReservationController`: `node.reserved_by`; `segment` `source`, `target`, `length`, `forward`, `backward`; `zone` `rule`, `capacity`, `members`, `reserved_by`, `orientation`. Corridors are zones named `corridor:<node>` | traffic design §11.1; the trace |
| `traffic.wait_started`, `traffic.wait_ended`, `traffic.wait_stuck`, `traffic.deadlock` and `agv.routed` carry no deltas. A waiting vehicle is in state `TRAVELING_*` with `motion: null` | `intralogistics/events.py`; the trace |
| A frame carries the events crossed since the previous frame (at most 256) and none after a seek | `studio/packages/playback/src/index.ts` `PreparedFrame` |
| Capacities are not in the state: `ParkingArea.capacity`, `ChargingStation.n_slots`, `AGVType.battery_capacity` | `parking.py`, `charging.py`, `agv.py` |
| `parking_area.parked` lists the holders of a slot; a slot is taken when the move to the area is decided, so the list includes vehicles still on their way. Several areas can share a node | `fleet.py`, `parking.py` |
| The totals of waits, stuck waits and deadlocks exist only as end-of-run scalars of `TrafficKPIs`, absent when there was no observation | `intralogistics/metrics.py` |
| The algorithm names are part of all three layout hashes; both readers accept only `layered-v1`; the automatic placement is a pure function of the saved `targets`, which hold `kind`, `explicitly_placed`, `graph_position` and, for a vehicle, `node_id`; the layout editor re-runs it from a saved bundle | `layout/identity.py`, `layout/bundle.py`, `layout/auto.py`, `studio/packages/trace/src/layout.ts` |
| Tests compare the entity snapshot of an observed run with that of a bare run, so every state field must be kept without subscribers | `tests/intralogistics/test_invariance.py`, `random_worlds.py` |
| The export has no camera: it fits the scene (at most 120 px per metre) on a background colour the user chooses | `studio/packages/export/src/media.ts`, `apps/viewer/src/export-dialog.tsx` |
| The bench generator declares no network and passes a concrete node, which this baseline rejects | `studio/bench/glyphs.py` |
| Orders and the fleet entity have no placement and are drawn as boxes in a tray at fixed coordinates near the origin | `scene/src/project.ts` |
| The scene skips entities of kind `segment` and `zone` | `scene/src/project.ts` `TRAFFIC_KINDS` |
| The viewer has four docked panels: Scene, Inspector, Recorded KPIs, Events. The offline player has a scene, a selection and a raw state view, no docking | `apps/viewer/src/controller.ts`, `apps/player/src/index.ts` |
| The layout editor draws through the same scene (`renderProjection`, `onEditorPointer`) | `apps/viewer/src/layout-canvas.tsx` |
| Studio has no screenshot comparison. The measured scene cost is 6.4 ms p95 for 1,000 vehicles | `studio/browser/`; SP3 acceptance report |

## 3. Approaches considered

How the scene is built:

1. **Extend the current scene (chosen, M16).** The projection becomes a pure map model; one drawing function per
   kind of element draws it with the PixiJS primitives used today; the floor is drawn once and cached. Every
   consumer of the scene keeps working through the same class. The frame time must be measured.
2. **Rebuild the scene around retained sprites.** Each element is a persistent object with a pre-rendered image
   that is moved, not redrawn. Fastest at thousands of vehicles; it rewrites the scene package and every
   consumer must be verified again, for speed that 50 to 100 vehicles do not need.
3. **A second map scene beside the old one.** Nothing existing can break; two scenes to maintain, and
   production models never get the better view.

Davide chose 1. If the measurement of §8.4 fails the budget, the cached floor layer and the per-kind drawing
functions are the units that move to retained objects.

The alternatives for the data the map needs are recorded with their decisions in §4 (M4, M6, M8).

## 4. Decisions

Davide's decisions are marked **D**; the others are calls made in this design, which he can overturn.

| Id | Decision |
|----|----------|
| M1 **D** | Three levels of detail. "On the map we should report very basic succint info"; "each element should be distinguishable by eye with different sprites"; "the panel should report a bit more infos"; "the highest detail level should be displayed when one clicks on an element". |
| M2 **D** | The sprites and the three levels as shown in the mockup of 2026-11-25 (§5.3 to §5.7 describe it): "looks good to me". |
| M3 **D** | In 0.18.0 a warehouse stays the model's stock with bays, drawn as one building. Racks with their own physicality are later work. |
| M4 **D** | Sizes are derived in 0.18.0. `AGVType` gains an optional `width`; a vehicle's length and width are written to the trace as its footprint; lanes are drawn as wide as the widest vehicle plus a margin, twice that where passing is allowed. The layout schema does not change and the simulation ignores the widths. |
| M5 **D** | A lane width declared in the layout comes later, with M4's rule as its default. |
| M6 **D** | Waits and routes are recorded as vehicle state by the simulation. Rejected: the viewer searching events backwards. |
| M7 | The blockers shown are those known when the wait started, because the simulation announces a wait once. Davide was told and did not object. |
| M8 **D** | A facility without an explicit position is placed by the layout beside the node it is bound to. Rejected: the viewer moving it. |
| M9 | Capacities the map needs are recorded as constant state fields (§6.4). |
| M10 | All held floor is tinted in one colour. A selection highlights what the selected vehicle holds, its route and its wait line. Agreed by Davide as a proposal ("all good"). |
| M11 | The line from a waiting vehicle to its first blocker is always drawn. Agreed as a proposal. |
| M12 | Goods on the map: the load mark on a vehicle, the stock count on a warehouse, a badge for orders waiting to leave it; a line from origin to destination for a selected order or loaded vehicle. Agreed as a proposal. |
| M13 | The panel groups vehicles by fleet and puts those that need attention first; it has a text filter and a "waiting only" switch. Agreed as a proposal. |
| M14 | The click detail replaces the content of today's Inspector for the selected element; the raw recorded fields stay reachable below it. Agreed as a proposal. |
| M15 | The offline player gets the map and the click detail, and no panel. Agreed as a proposal. |
| M16 **D** | The scene is extended in place (approach 1). |
| M17 | Traces without a layout keep today's fallback grid and today's drawing. Agreed as a proposal. |
| M18 | The totals of waits, stuck waits and deadlocks are recorded as counters in the state of the `fleet` entity, so the panel shows them at the cursor (§6.6). The end-of-run scalars cannot follow the cursor. |
| M19 | `wait` and `route` are live vehicle state, kept whether or not anything subscribes; their cost in a bare run is measured (§8.5). |
| M20 | The new inputs of the automatic placement are saved in the bundle's `targets`. The algorithm is named `layered-v2` exactly when a target uses one of them; every other bundle stays `layered-v1` with its hashes (§6.5). |
| M21 | A parking area and a charging station have a fixed default footprint centred on their node. Vehicles are drawn where they stand, not in slots. The parking count is the slots held. |
| M22 | The export has no camera choice: its level of detail follows the fitted scale. |
| M23 | The layout editor keeps today's glyphs. |
| M24 | Entities that have no placement by kind (orders, fleets) are not drawn on the map when the trace has a network. |
| M25 | The screenshot comparison runs on Linux Chromium in CI with labels hidden; the images Davide reviews are separate renders with labels. |

## 5. Design: the scene

### 5.1 Structure

`studio/packages/scene` keeps its public class `Scene` and its exports. Inside it:

- **Map model** (`project.ts`, split into files as the builder sees fit). `Projector.project(frame)` returns the
  projection of today plus the map model: floor strips, held resources, vehicles, facilities, wait lines, and
  the selection overlay's inputs. It is pure: a function of the frame and of nothing else. It reads state fields
  by name and never reduces events.
- **Drawing functions**, one per kind: floor, node, vehicle, warehouse, parking area, charging station, and the
  generic glyph of today for every other kind. Entities whose kind has no placement (orders, fleets) are in the
  map model for the panel and are not drawn when the trace has a network (M24). Each takes the graphics object, its element of the map model, the
  scale and the level of detail. `registerRenderer` keeps overriding the drawing of a kind.
- **Layers**, bottom to top: background grid; floor strips; held tint; facilities and ports; nodes; jobs and
  generic glyphs; vehicles; wait lines; selection overlay; labels; scale bar; decorative trails. The floor
  strips are static geometry redrawn only when the camera, the canvas size or the layout changes (the existing
  signature mechanism). Everything above is redrawn each frame.

The fields of `Glyph` and `Projection` that the layout editor, the compare view and the tests read stay. New data
is added beside them. The layout editor builds its own projection and passes it to `renderProjection`; such a
projection has no map model and is drawn with today's glyphs (M23).

Sprites carry a light outline so that they stay legible on any export background colour.

### 5.2 Level of detail

One number decides it: the scale in pixels per metre.

- **Symbol level**, below the threshold: vehicles and facilities are symbols of a fixed size in pixels, centred
  on their true position; nodes are hidden unless selected; the only text is facility names, their stock or
  occupancy count, and the vehicle's short label.
- **True-size level**, at or above the threshold: vehicles and facilities take their footprint; nodes are drawn
  with their id; a vehicle gains its one-line label (§5.4).

The threshold is the scale at which the true length of the longest vehicle equals the fixed symbol's length, so
the symbol never jumps in size at the switch. The symbol sizes, the label size and the colours are constants of
the scene package (§12).

When symbols of vehicles at the same position overlap (a parking area, a node with capacity above one), they are
fanned out by a fixed pixel offset in id order at symbol level; at true-size level they are drawn where they are.

The vehicle's short label at symbol level is its label when it has at most four characters, otherwise the
trailing digits of its id, otherwise nothing.

### 5.3 The floor

- A lane is a strip along each arc of `network.graph.arcs`, centred on the straight line between its two node
  positions. Arcs in both directions between the same two nodes are one strip; a strip with one arc only is
  one-way.
- Width (M4): `w = max(vehicle footprint depth over all vehicles in the initial state) + 2 * margin`, with
  `margin = 0.2 * that depth`. A strip whose arcs have `passing` true is `2 * w` wide. With no vehicle in the
  trace, `w` is 1 m. The map states in its legend that lane width is nominal.
- Direction: a one-way strip carries chevrons along its direction at a fixed pixel spacing; a strip travelled in
  both directions carries none at symbol level and one chevron pair at its ends at true-size level. A disabled
  arc is not drawn.
- A corridor (a zone whose id starts with `corridor:` and whose rule is `same_direction`) is not drawn as a
  separate shape; it shows when held (§5.5).
- A scale bar in a corner of the canvas shows a round length in metres for the current scale. It is part of the
  scene, so the export includes it.

### 5.4 Sprites

**Vehicle.** A rounded body with a pointed nose toward its heading: the direction of the arc under way when it
moves, else the recorded `heading`, else east. At true-size level the body is the footprint (`width` along the
heading is the vehicle's length, `depth` its width). The first row of the table that applies decides the shown
state.

| Shown state | Recorded condition | Colour | Mark |
|-------------|--------------------|--------|------|
| stranded | `STRANDED` | red | cross |
| waiting for traffic | `wait` not null (§6.2) | amber | pause bars and a dashed ring |
| moving | `motion` not null | blue | none |
| working | `WAITING_LOAD` or `WAITING_UNLOAD` | blue | a small square on the bay side |
| idle | `IDLE` | grey | none |
| charging | `CHARGING` | green | bolt |
| travelling, at rest | any other `TRAVELING_*` | blue | none |

A vehicle whose `load` is not empty carries the load mark, a brown square on its deck, in every state. A state
the table does not know is drawn grey with the state name in the click detail. A vehicle whose `fleet` differs
from the others carries a fleet stripe at its tail in one of a fixed set of fleet colours, in fleet id order;
with one fleet there is no stripe.

The one-line label at true-size level is `<label> · <what>`, where `<what>` is the first that applies: `waiting
<n> s for <first blocker>`; `charging`; `stranded`; `to <route destination>`; `idle`.

**Warehouse.** Its footprint rectangle with a shelf pattern, its name above, below it the stock: the quantity
when it holds one SKU, otherwise the number of SKUs. A bay is a small white square with the warehouse's outline
colour, on each node the warehouse is bound to (`<id>:input_bays/<i>`, `<id>:output_bays/<i>` in the layout
`bindings`), joined to the footprint by a thin line when they do not touch. The badge for waiting orders (M12)
is a count in a circle at the warehouse's corner: the orders whose `origin` is this warehouse and whose status
is `PENDING`.

**Parking area.** A dashed outline with a "P" around its node and the count `slots held / capacity`, where the
slots held are the length of the `parked` list (it includes vehicles on their way, M21). Vehicles are drawn where
they stand: those on the node are fanned out at symbol level (§5.2).

**Charging station.** A filled outline with a bolt and the count `slots_in_use / slots`.

**Node.** A small white circle at true-size level; a node bound to a facility is drawn as that facility's bay or
access point instead.

**Reserved for 0.19.0.** Rack: a narrow rectangle with cross bars. Staging area: a dotted outline with floor
positions. Dock: a three-sided bracket on the building edge. They are named here so that the colours and shapes
of 0.18.0 do not take them; nothing draws them yet.

**Everything else** (servers, pre-shop pool, jobs, unknown kinds) is drawn as today.

Colours and status are never the only signal: each state has a mark, and the panel and the click detail say the
state in words.

### 5.5 Traffic

- **Held floor (M10).** A node with a non-empty `reserved_by`, a segment with a non-empty `forward` or
  `backward`, and every member of a zone with a non-empty `reserved_by` are tinted with one translucent colour
  over the strip or the node. A segment id is `<a>~<b>`; its strip is the one between those two nodes. A zone's
  members are node ids and segment ids. Without `ReservationController` there are no such entities and nothing is
  tinted.
- **Wait line (M11).** For each vehicle with a wait, a dashed amber line from the vehicle to the position of its
  first blocker that exists in the frame.
- **Deadlocks and stuck waits** are counted in the panel (§5.6, §6.6) and are not drawn on the map. A cycle of wait lines is how a
  deadlock looks.
- **Selection.** With a vehicle selected: the floor it holds is tinted in a second, stronger colour; its route
  (§6.2) is drawn as a line along the strips from its position to its destination, the part already travelled
  omitted; its wait line is thicker; the origin-to-destination line of its order is drawn (M12). With an order
  selected: that line and the vehicle assigned to it are highlighted. With a facility selected: its bays and the
  vehicles bound for it (route destination among its nodes) are highlighted.

### 5.6 The panel (level 2)

A new docked panel, "Overview", in the viewer, placed with the Inspector. It is built from the same map model as
the scene, so it and the map cannot disagree. It is written imperatively at no more than 4 Hz while playing, and
flushed on pause, step and seek.

- **Vehicles**, grouped by fleet. Each group heading gives its counts: moving, waiting, working, idle, charging,
  stranded. One line per vehicle: the state mark's colour, the label, load and destination, and what it is doing:
  `AGV-1 · loaded, 1 crate → East · waiting 6 s for AGV-2`. Order inside a group (M13): stranded, then waiting by
  longest wait, then the rest by id. A text filter and a "waiting only" switch narrow the list. Groups render at
  most 200 lines and say how many are hidden.
- **Goods.** Counts of open orders by status, and one line per open order: quantity, SKU, origin, destination,
  vehicle, picked or not. At most 200 lines, oldest first.
- **Traffic.** The number of vehicles waiting now and the longest current wait; the waits, stuck waits and
  deadlocks so far, summed over the fleets' counters (§6.6). A trace without those counters shows only the first
  two.
- **Facilities.** One line each: stock per SKU for a warehouse, occupancy for parking and charging.

A click on a line selects the element and centres the map on it. The panel exists only when the trace has a
layout; in presentation mode it is hidden with the other panels.

### 5.7 The click detail (level 3)

Selecting an element, on the map, in the panel or in the Inspector's entity list, fills the Inspector with a
readable description first and the raw recorded fields below it, collapsed (M14).

- **Vehicle:** label, fleet, recorded state; load and order (SKU, quantity, origin, destination, picked at); the
  node it is at or the arc it is on, heading, current speed (evaluated from the recorded motion), battery as a
  value and, with a capacity, a percentage; its route with the next node marked; its wait: since when and for how
  long, the node it wants to enter, the resources it needs, its blockers; what it holds (the resources whose
  holder lists contain it); its size.
- **Order:** every field in a sentence each, and its vehicle.
- **Warehouse, parking, charging:** contents or occupancy, the nodes it is bound to, the vehicles at it, the
  open orders from and to it.
- **Node, segment, zone:** who holds it, who stands on it, capacity; for a zone its members and orientation.
  Segments and zones are selectable through the Inspector's list and through the vehicle detail, not by clicking
  the floor.

Every other kind keeps today's Inspector.

### 5.8 The offline player and the export

The player draws the same map through the same scene. Its selection shows the readable description of §5.7 above
its raw state view. It has no Overview panel (M15).

The video and PNG export draws the same map. The export fits the scene and has no camera choice, so its level of
detail is the one of the fitted scale (M22). No change to its dialog or its frame schedule.

### 5.9 Traces without a layout, and production-only models

A trace without a layout (written before 0.18.0) keeps the fallback grid and today's drawing; it has no map and
no Overview panel (M17). A trace with a layout and no network (a production-only model) is drawn as today, with
the scale bar added.

## 6. Design: what the simulation records

These are the additions on the Python side. All are implemented after Assignment 3 of the layout branch (§1.5).

### 6.1 Vehicle size (M4)

- `AGVType` gains `width: float | None = None`, validated like `length` (finite, not negative). It is
  descriptive: no component reads it.
- The layout target of a vehicle carries its type's size. The vehicle's default placement footprint becomes
  `width = length`, `depth = width` of the type (the layout's footprint `width` is the extent along the
  placement's x axis). When the type has no `length`, the footprint stays 1 x 1; when it has a length and no
  width, the depth is half the length. An explicit placement footprint for the vehicle wins.
- The scene uses the footprint for the true size and rotates it by the heading, not by the placement's rotation.
- The footprint of a vehicle enters the layout's hashes as any placement field does. The fingerprints and the
  layout hashes of models with vehicles that declare a length change; they are re-pinned once (§8.5).

### 6.2 Waits and routes (M6)

The `agv` state schema gains two nullable map fields.

- `wait`: `{"since": <time>, "node": <id>, "resources": [<ids>], "blockers": [<ids>]}`. Set by a delta of
  `traffic.wait_started` with that event's values, set to null by a delta of `traffic.wait_ended`. Nothing else
  writes it. The blockers are those of the announcement (M7).
- `route`: `{"nodes": [<ids>], "destination": <id>, "reason": <reason>}`. Set by a delta of `agv.routed`. Set to
  null by a delta of the event that ends the travel: the builder finds the single place in the travel executor
  where a travel ends, for every outcome, and attaches the delta to an event already emitted there; if no event
  is emitted for an outcome, the route is cleared by the next `agv.state_changed` of that vehicle. The position
  along the route is not recorded: the viewer finds the vehicle's node or arc in `nodes`.

Both are attributes of the vehicle, kept whether or not anything subscribes, and returned by `AGV.snapshot()`
(M19): the tests compare the snapshot of an observed run with that of a bare run. Today the wait's resources and
the route's node ids are computed only behind `env.wants`. The builder keeps the bare run cheap: the field may
hold the objects the controller already has (the waiting record, the route) and turn them into ids only when a
snapshot or a delta is built.

Both events stay as they are in payload and timing; only their deltas change. `traffic.wait_stuck` and
`traffic.deadlock` keep having none.

Assignment 3 changes when waits are reviewed and reported. This section is written against the events as they
are at `66a5a96`. The builder reads that assignment's rulings first; whatever it changes in when
`traffic.wait_started` and `traffic.wait_ended` are emitted, the rule stays "the field is set by one and cleared
by the other". If that assignment makes the simulation re-announce a wait when its blockers change, `wait` is
rewritten by each announcement with `since` kept, and M7's limit goes away.

Consequences: the semantic digest of every run with a traffic controller that reports waits, or with routed
travels, changes; pinned digests and generated fixtures are re-pinned once (§8.5). The frozen fixtures of
earlier trace versions are not regenerated. The sentence "It changes no state" in traffic design §11.2 is
amended for these three events.

### 6.3 Facility placement (M8)

In the automatic placement, a warehouse, parking area or charging station without an explicit position is placed
from its node bindings instead of being packed in the grid:

- Anchor: the position of its bound node, or the mean of the positions of its bound nodes.
- Direction: away from the network. It is the opposite of the mean of the unit vectors from the anchor to the
  neighbours of the bound nodes; when that mean is zero, the direction is +y.
- Position: the anchor moved along that direction by half the facility's extent in that direction plus a gap of
  1 m, so that the footprint does not cover the lane.
- A parking area and a charging station are centred on their node instead, with a default footprint of 3 m by
  3 m, the marked area around it (M21). They are not moved: two of them on one node coincide, and the scene
  draws their symbols side by side.
- A warehouse whose raw footprint overlaps the raw footprint of an explicit placement or of a warehouse placed
  before it is moved further along the direction in steps of its own extent until it does not. Nodes, parking
  areas and charging stations are not obstacles for this test. Warehouses are processed in id order.
- A facility with no node binding, and every production kind, keeps the grid.

The rule is deterministic and uses the same number handling as the rest of the automatic placement. §6.5 says
how it reaches the bundle. The layout-only design §5.5 is amended.

The default footprint of a warehouse stays 1 x 1 m. At symbol level it is drawn at the fixed symbol size, so an
undeclared size does not make it invisible.

### 6.4 Capacities (M9)

Constant fields, written once in the initial state and never changed, so they cost nothing per event:

- `parking_area.capacity` (int), `charging_station.slots` (int), `agv.battery_capacity` (float).

They enter the digest as part of the initial state.

### 6.5 The bundle (M20)

The automatic placement is a pure function of the bundle's saved `targets`, and the layout editor re-runs it from
a saved bundle. Its new inputs are therefore saved there:

- a vehicle target may carry `size`: `[length, width]`, present when its type declares a length;
- a warehouse, parking area or charging station target may carry `node_ids`: the ids of the nodes it is bound
  to, in binding order.

The algorithm name is `layered-v2` exactly when at least one target carries one of these fields, and
`layered-v1` otherwise, as a bundle is schema 1.1 exactly when it uses a 1.1 field. `layered-v2` is `layered-v1`
plus §6.1 and §6.3; one implementation serves both. So production-only models and every bundle saved by a
released version keep their name, their output and their three hashes, and stay re-resolvable in the editor.
Models with sized vehicles or node-bound facilities get new layout hashes, re-pinned once (§8.5).

Both readers (Python `layout/bundle.py`, TypeScript `studio/packages/trace/src/layout.ts`) accept both names and
the two optional target fields, in the same task as the writer. The bundle's `schema_version` does not change.

### 6.6 Traffic counters (M18)

The `fleet` state schema gains three integer fields, `waits`, `stuck_waits` and `deadlocks`, starting at 0 and
incremented by a delta of `traffic.wait_started`, `traffic.wait_stuck` and `traffic.deadlock`. A wait and a
stuck wait count for the fleet of their vehicle; a deadlock counts once for the fleet of each vehicle in it. They
are kept without subscribers, like every state field. `TrafficKPIs` is unchanged: it counts after the warm-up,
the counters count from the start.

## 7. Documentation

- `docs/`: the Studio pages describe the map, the sprites with a legend image, the three levels, the nominal lane
  width and how to declare a vehicle's size and a facility's position. The intralogistics pages document
  `AGVType.width`, the new state fields and the placement rule. The migration notes of 0.18.0 list the changed
  fingerprints and the automatic placement.
- `docs/development/studio.md`: the map model, the layers, the level of detail, the screenshot check.
- `CLAUDE.md`: the scene and the state fields, where it describes them.
- The amendment notes named in the header.

## 8. Verification

### 8.1 Map model

Unit tests of the projection, without a GPU: for fixed frames of a fixture trace, the strips with their widths,
the held resources, each vehicle's shown state, heading, load and label, the wait lines, the facility elements
and the selection overlay are asserted as data. Cases: a waiting vehicle; a held corridor; a passing arc; a
vehicle without length; two fleets; no `ReservationController`; a trace without a layout; a production-only
trace; unknown kinds and states; hostile labels.

### 8.2 Screenshots of fixed frames

A browser spec opens a fixture trace, seeks to fixed cursors, sets a fixed camera and canvas size, and compares
the scene canvas with a stored image under a tolerance, with labels and the scale bar's text hidden by a scene
option, because text rendering differs between machines (M25); the labels are asserted as data in §8.1. Frames: the corridor example with a wait, at symbol
level and at true-size level; a selection; the advanced example with a charging station. They run on Linux Chromium in CI, with stored images produced in the Playwright container of the pinned version; Firefox and WebKit run the same steps and assert no error and a non-empty canvas, because pixels are not
promised across browsers (global design C7.1). Separately, each visual task of the plan ends with
renders of the same frames with labels, made on the development machine and shown to the reviewer and to Davide;
no test compares these.

### 8.3 Panel, detail, player, export, editor

- Tests of the Overview panel and of the readable detail from fixed frames: the lines, the order, the filter,
  the caps; no React commit per frame while playing.
- The existing viewer, export, compare and layout-editor specs pass. The export spec gains a frame with the map.
  The offline HTML spec gains a selection.
- After a seek to an instant inside a wait, the map, the panel and the detail show that wait without playing
  through its start: this is the test of goal 6.

### 8.4 Performance

The bench generator of `studio/bench` is rewritten on a declared network (it does not run on this baseline). Two
workloads: 100 vehicles on a lattice of 400 nodes with reservations, and 1,000 vehicles on 50 lanes. Frame p95
stays below 16.7 ms for both; the numbers are recorded. The 6.4 ms of the SP3 report was measured without a
network and is not a baseline for them. If it fails, the plan stops and the fallback of §3 is brought to Davide.

### 8.5 Python

- Tests for each addition of §6: the fields are set and cleared at the right events; a seek inside a wait reads
  it from `Trace.state_at`; the placement rule on the examples and on hand cases (one bay, two bays, a dead end,
  an overlap, no binding); the footprint from the type.
- The observers-never-change-results tests and the random-world monitor stay green.
- Re-pins: each pinned digest, layout hash and generated fixture that moves is re-pinned in the task that moves
  it, with the cause shown to be that task only.
- The overhead gates of the `bench` CI job hold. A bare intralogistics run (no subscriber) is timed before and
  after §6.2 and §6.6 on the corridor example and on a random world; the difference is recorded, and above 2 % it
  is brought to Davide.
- The TypeScript reader's fixtures generated from Python are regenerated in the same task as the fields.

## 9. Order of work

1. Under `studio/` only, on the current base, against a fixture trace: the map model, the floor, the sprites, the
   level of detail, the held tint, the Overview panel, the click detail, the player, the screenshot check, the
   bench. The fixture is a trace of the corridor example recorded on the base, with the fields of §6 added by a
   small script kept with the fixture, so the viewer work does not wait for the Python work.
2. After Assignment 3 of the layout branch, on the commit the coordinator names: §6.1 to §6.6, each with its
   re-pins and, for §6.5, both readers; then the fixture is replaced by a recorded one and the screenshots are reviewed again.
3. Documentation and the amendment notes.

The branch is not pushed and has no pull request of its own; 0.18.0 reaches `main` as one pull request.

## 10. Acceptance

1. A fresh trace of each of the four intralogistics examples opens with every facility at its node, lanes as
   strips, vehicles with heading, state and load, and no element drawn as a bare box.
2. In the corridor example, at an instant reached by a seek into a wait, the waiting vehicle, its blocker and
   the held corridor can be named from the map alone; the panel and the click detail say the same.
3. The three levels hold what §5 says and nothing shown depends on crossed events.
4. The screenshot frames are stored and were reviewed by Davide.
5. The frame budget holds on both bench workloads.
6. The existing Studio specs, the Python suite and the CI gates pass after the re-pins.
7. The documentation and the amendment notes are written.

## 11. Risks

- **Assignment 3 changes the wait events more than expected.** Then §6.2 is revisited with its rulings before
  that task starts; the viewer side depends only on the `wait` field's shape.
- **Recording cost.** Two more deltas on events that already exist; measured by the bench gate, which was close
  to its limit on the layout branch.
- **The placement rule produces an ugly result on some network** (a facility bound to a node in the middle of a
  lattice). The overlap rule keeps it legal, an explicit position fixes it, and the examples are checked by eye.
- **Nominal lane width misleads** where real aisles are much wider than the vehicle. The legend says it is
  nominal; M5 replaces it.
- **Screenshot tests are brittle.** One browser, a tolerance, few frames.
- **Text at 100 vehicles.** The symbol level shows only short labels and the panel carries the words; the bench
  workload is also looked at by eye.

## 12. Left to the builder

- The exact pixel sizes, colours, line widths, tolerance of the screenshot comparison and the file split inside
  the scene package. The colours start from the mockup (blue moving, amber waiting, grey idle, green charging,
  red stranded, brown load) and must keep the state marks legible on the scene's light background.
- The names of the new TypeScript types and of the fixture script.
- The event that carries the route-clearing delta (§6.2).
- Whether the Overview panel is one file or several, and its markup.

## 13. Builder check

A reviewer read the first draft as a builder and checked it against the code at `66a5a96`.

Corrections (the draft contradicted the code):

1. The algorithm name is in all three layout hashes, so renaming it for every model would re-pin every layout
   hash. Now M20, §6.5: the new name only for bundles that use the new inputs.
2. Both readers reject any name but `layered-v1`, and the automatic placement needs its inputs saved in
   `targets`. Now §6.5, §1.5.
3. The export has no camera. Now M22, §5.8.
4. The traffic totals exist only as end-of-run scalars. Now M18, §6.6.
5. `parked` lists slot holders, including vehicles on their way. Now M21, §5.4.
6. The bench generator has no network and does not run on this baseline. Now §8.4.
7. State fields must be kept without subscribers. Now M19, §6.2, §8.5.
8. `graph.arcs` has no `direction` and no disabled arcs. Now §2, §5.3.

Questions, with where they are answered: bundle compatibility (M20); the mockup (header); the platform of the
screenshot baselines (M25, §8.2); the export camera (M22); totals at the cursor (M18); the layout editor (M23);
the open geometry of the placement rule (§6.3, M21); non-placeable kinds (M24); parking semantics (M21).

None was put to Davide before the handover: each is a call of this design, marked in §4 as not his, for him to
check while reading.
