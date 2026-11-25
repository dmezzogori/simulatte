# The Studio scene drawn as a map — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** written on 2026-11-25 from the approved design. No task is implemented yet.

**Goal:** Studio draws an intralogistics run as a map to scale with a sprite per kind of element, an Overview panel and a readable click detail, all read from the recorded state at the cursor; the simulation records what that needs.

**Architecture:** Stage 1 works only under `studio/`: the scene's projection grows a pure map model, drawing functions per kind draw it with PixiJS primitives, and a panel and a detail view are built from the same model. It is developed against frame fixtures (JSON) that already contain the new state fields. Stage 2 adds those fields and the placement rule on the Python side, after Assignment 3 of the layout branch, and replaces the patched fixtures with recorded ones.

**Stack:** TypeScript, PixiJS 8, React 19, dockview, Vitest, Playwright under `studio/`; Python 3.11+ standard library; Zensical for the docs.

**Spec:** [`specs/2026-11-25-studio-map-design.md`](../specs/2026-11-25-studio-map-design.md). Section numbers and decision ids (`M1` to `M25`) refer to it. The agreed mockup is `specs/research/2026-11-25-studio-map-mockup.html`; where it and the spec differ, the spec wins. Signatures below are boundaries, not licence to weaken a contract stated in the spec.

**Follows:** `feature/layout-only-models`. Branch `feature/studio-map`. Nothing is pushed and no pull request is opened by this plan.

## Global constraints

- **Stage gate.** Tasks 9 to 13 do not start on `66a5a96`. They start after Assignment 3 of the layout branch is complete, on the commit the release coordinator names, after this branch is rebased on it. Before Task 11 the implementer reads that assignment's rulings on wait reporting (`progress.md` of the layout work, heading "Assignment 3") and the controller confirms that spec §6.2 still holds.
- The viewer never reduces events: everything the map, the panel and the detail show is a function of `frame.state` and `frame.layout` (spec goal 6). `frame.crossedEvents` is read only by the existing decorative trails.
- A trace with a layout but without the new fields (`wait`, `route`, capacities, counters, `size`) must draw without error: a missing field means "not known", never a crash. A trace without a layout keeps today's drawing exactly (M17).
- The public API of `@simulatte/scene` that other packages use stays: `Scene`, `Projector`, `Glyph`, `Projection`, `equipment`, `motionProgress`, `registerRenderer`, `renderProjection`, `onEditorPointer`, `liveSceneCount`. Fields are added, none removed.
- Offline player rules: one script, no `eval` (keep `import "pixi.js/unsafe-eval"` first), no remote asset or font, sprites drawn with primitives. Trace labels are text, never markup.
- Clock-bound values are written imperatively; no React commit per frame.
- TypeScript gates, run from `studio/`: `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm test:browser`. Python gates: `uv run --all-extras pytest` (coverage >= 99 % branch), `uv run --all-extras ruff check src tests`, `uv run --all-extras ruff format --check`, `uv run --all-extras ty check src`. Read the output of each before reporting it.
- Every state change in Python is an event emitted behind `if env.wants(Cls): env.emit(Cls(...))`; state fields are kept whether or not anything subscribes (M19); results do not depend on observers.
- Any task that changes a pinned digest, a layout hash, a trace fixture or a recorded JSON re-pins it in that task and shows that the task is the only cause. The frozen fixtures under `tests/fixtures/traces/frozen/` are never regenerated.
- The layout file schema does not change (M4). The bundle changes only as spec §6.5 says.
- Nothing about deadlines, venues or papers anywhere in the repository. Conventional commits; each task commits only its own files.

## Review focus

Conditions the spec implies and a person will meet; each has a test in the task named.

1. Seeking into the middle of a wait shows the wait, its blocker and the held floor without playing through its start (Task 3 on a frame; Task 13 on a recorded trace through the browser).
2. A blocker or a route node that is not in the frame (retired entity, id from another fleet's graph): no line is drawn for it, the text names the id, nothing throws (Task 3).
3. A trace of this branch recorded before stage 2 (layout, no new fields; facilities in the grid; vehicles 1 x 1 m): the map draws, the panel omits what it cannot know (Tasks 2, 3, 6).
4. One hundred vehicles on a small canvas: no label overlaps another vehicle's symbol at symbol level, the panel caps its lists (Tasks 3, 6, 8).
5. Hostile and long labels (closing-script strings, 500 characters, right-to-left marks) in the map, the panel and the detail of the offline player (Tasks 3, 7).

## How visual results are verified

Tests assert the map model as data and compare a few canvases with stored images (labels hidden, Linux Chromium, Task 8). They do not judge the look. So every task marked **visual** ends with renders: the implementer runs `node studio/harness/render.mjs <out-dir>`, which writes one PNG per fixture frame and detail level with labels on, lists the files in the report, and states for each what must be seen. The reviewer opens each PNG and checks the task's "must see" list against the mockup. The controller shows Davide the renders of Tasks 4, 5 and 13.

---

## Stage 1 — `studio/` only

### Task 1 — Frame fixtures and the render harness

**Create:** `tests/fixtures/traces/generate_map_frames.py`, `studio/fixtures/map/{corridor-wait,corridor-moving,advanced-charging,two-fleets,bare-layout,production}.json`, `studio/harness/{index.html,main.ts,vite.config.ts,render.mjs}`, `studio/packages/scene/test/fixtures.ts`.
**Interfaces (produces):**

```ts
// studio/packages/scene/test/fixtures.ts
export function loadFrame(name: string): PreparedFrame; // reads studio/fixtures/map/<name>.json
```

A fixture file is `{"time", "cursor", "state", "initialState", "layout"}` as the playback package would prepare it. `generate_map_frames.py` runs `examples/intralogistics_traffic.py` and `examples/intralogistics_advanced.py` with a `TraceRecorder`, takes `Trace.state_at` at fixed cursors and `Trace.layout`, and writes JSON. `--patch` (the default until Task 13) adds what stage 2 will record, from the trace's own events: `wait` and `route` on vehicles, `battery_capacity`, `capacity`, `slots`, fleet counters, the vehicle footprint 2.0 x 1.0 and `size` in its target, `node_ids` in facility targets, and facility positions by spec §6.3 computed by hand in the script for these two examples. `bare-layout` is `corridor-wait` without the patch (review focus 3). `two-fleets` is `corridor-wait` with one vehicle's `fleet` renamed. `production` is a state from `examples/studio_recording.py`.

- [ ] `corridor-wait` is the frame at time 128.0 of the corridor example with the default seed (AGV-1 has waited at C for AGV-2 since 121.54 s; AGV-2 is moving). The script asserts both facts and fails otherwise; if the example changed after the rebase, the implementer picks the nearest instant with one waiting and one moving vehicle and reports it, and the literal values in Tasks 3 and 6 follow.
- [ ] The harness page takes `?frame=<name>&scale=<px per m>&x=&y=&select=<id>&labels=0|1`, creates a `Scene` of 1200 x 500, calls `scene.render(frame)`, sets the camera and sets `document.title = "ready"`. `render.mjs` builds it with its vite config, serves it, and screenshots the canvas for every fixture at symbol level (fit) and true-size level (30 px/m on the first waiting or first vehicle).
- [ ] `loadFrame` test: every fixture loads and has a layout except none; `corridor-wait` has a vehicle with `wait.blockers` of length 1.
- [ ] Run `render.mjs`; the renders show today's drawing (thin lanes, boxes). Commit `test(studio): frame fixtures and a render harness for the scene`.

### Task 2 — Map model: floor, sizes, level of detail

**Create:** `studio/packages/scene/src/map/{model.ts,floor.ts,detail.ts}`, `studio/packages/scene/test/map-floor.test.ts`. **Modify:** `studio/packages/scene/src/project.ts`, `src/index.ts` (exports).
**Interfaces (produces):**

```ts
export type Detail = "symbol" | "true";
export interface Strip { id: string; a: Point; b: Point; width: number; oneWay: boolean; passing: boolean; held: boolean; }
export interface MapNode extends Point { id: string; facility: string | null; held: boolean; }
export interface MapModel {
  laneWidth: number;            // metres, nominal (spec §5.3)
  threshold: number;            // px per metre where true size begins (spec §5.2)
  strips: readonly Strip[];
  nodes: readonly MapNode[];
  vehicles: readonly VehicleItem[];   // Task 3
  facilities: readonly FacilityItem[]; // Task 3
  waitLines: readonly WaitLine[];      // Task 3
  orders: readonly OrderItem[];        // Task 3
  fleets: readonly string[];
  traffic: TrafficSummary;             // Task 3
}
export interface Projection { /* existing fields */ map: MapModel | null; }
export const SYMBOL_PX = { vehicle: 22, facility: 36 } as const;
export function detailAt(model: MapModel, scale: number): Detail;
```

`map` is null when the frame has no layout or the layout has no `network.graph.arcs` (spec §5.9). Strip id is the two node ids sorted and joined by `~`, the id of the segment entity.

- [ ] Tests on `corridor-wait`: 13 nodes; one strip per unordered node pair; the loop strips are `oneWay`, the corridor strips are not; `laneWidth === 1.4` (vehicle depth 1.0, margin 0.2 each side); a strip whose arcs have `passing` is `2 * laneWidth` wide; `threshold === 22 / 2.0`; `detailAt(model, 10.9) === "symbol"`, `detailAt(model, 11) === "true"`.
- [ ] Tests: `bare-layout` gives `laneWidth === 1.4` from the 1 x 1 footprint; a frame with no vehicle gives `laneWidth === 1`; `production` and a frame without layout give `map === null` and the same glyph list as before this task (snapshot the ids and positions before changing the code).
- [ ] Held: `Strip.held` when the segment entity of that id has a non-empty `forward` or `backward`, or a zone with a non-empty `reserved_by` lists it in `members`; `MapNode.held` likewise from `reserved_by` and zone members. Test on `corridor-wait`: the strips of the corridor AGV-2 holds are held, the others not; a frame without segment entities has nothing held.
- [ ] Implement. `pnpm --filter @simulatte/scene test`, `pnpm typecheck`. Commit `feat(studio): the map model of the floor`.

### Task 3 — Map model: vehicles, facilities, waits, goods

**Create:** `studio/packages/scene/src/map/{vehicles.ts,facilities.ts,overlay.ts}`, `test/map-items.test.ts`. **Modify:** `map/model.ts`, `project.ts`.
**Interfaces (produces):**

```ts
export type Shown = "stranded" | "waiting" | "moving" | "working" | "idle" | "charging" | "resting" | "unknown";
export interface Wait { since: number; node: string; resources: readonly string[]; blockers: readonly string[]; }
export interface Route { nodes: readonly string[]; destination: string; reason: string; }
export interface VehicleItem extends Point {
  id: string; label: string; short: string;        // short: spec §5.2
  heading: number;                                  // radians
  length: number; width: number;                    // metres
  shown: Shown; state: string; loaded: boolean; load: Readonly<Record<string, number>>;
  fleet: string | null; fleetIndex: number | null;  // null with one fleet
  order: string | null; wait: Wait | null; route: Route | null;
  holds: readonly string[];                         // resource ids whose holder lists contain it
  speed: number | null; battery: number | null; batteryCapacity: number | null;
  offset: Point;                                    // pixels, fan-out at symbol level
  line: string;                                     // one-line label, spec §5.4
}
export interface FacilityItem extends Point {
  id: string; kind: "warehouse" | "parking_area" | "charging_station"; label: string;
  width: number; depth: number; rotation: number; nodes: readonly string[];
  count: string;      // "49", "3 SKUs", "1 / 3", "2 / 4"; "" when not known
  badge: number;      // pending orders leaving a warehouse
}
export interface WaitLine { agv: string; blocker: string; from: Point; to: Point; }
export interface OrderItem { id: string; sku: string; quantity: number; origin: string; destination: string; status: string; agv: string | null; picked: boolean; createdAt: number; }
export interface TrafficSummary { waiting: number; longestWait: number; waits: number | null; stuckWaits: number | null; deadlocks: number | null; }
export interface Overlay { holds: readonly string[]; route: readonly Point[]; waitLine: WaitLine | null; flow: readonly [Point, Point] | null; related: readonly string[]; }
export function overlayFor(model: MapModel, id: string | null): Overlay | null;
```

`resting` is "travelling, at rest" of the spec's table; the table's order decides `shown`. Facility `nodes` come from the layout `bindings` keys `<id>:input_bays/<i>`, `<id>:output_bays/<i>`, `<id>:node`.

- [ ] Tests on `corridor-wait`: AGV-1 `shown === "waiting"`, `loaded`, `line === "AGV-1 · waiting 6 s for AGV-2"`, `short === "1"`; AGV-2 `"moving"` with heading pi and a position strictly between its motion's two nodes; the parked vehicle `"idle"`; one `WaitLine` from AGV-1 to AGV-2's position. Each row of the spec's state table has a case, including a `STRANDED` vehicle that also has a wait (shown stranded) and an unknown state (`"unknown"`).
- [ ] Tests: facilities at their patched positions with `nodes` from the bindings; `count` for one SKU, several SKUs, parking `slots held / capacity`, charging; `badge` counts `PENDING` orders by origin; on `bare-layout` `count` of the parking area is `""` and nothing throws.
- [ ] Tests, review focus: a blocker id absent from the state gives no `WaitLine` and a `line` that still names it; a route node absent from the layout is skipped in `Overlay.route`; labels of 500 characters, `</script>` and right-to-left marks pass through unchanged as strings; 100 vehicles on one node get 100 distinct `offset`s.
- [ ] `overlayFor`: a vehicle (its holds, its route from its position to the destination with the travelled part omitted, its wait line, its order's origin-to-destination points), an order, a facility (`related` = vehicles whose route destination is one of its nodes), `null` for no selection or an id not in the model.
- [ ] `TrafficSummary`: `waiting` and `longestWait` from the vehicles; the three totals summed over fleet entities' `waits`, `stuck_waits`, `deadlocks`, `null` when no fleet has the field.
- [ ] Implement; scene tests and typecheck. Commit `feat(studio): vehicles, facilities, waits and goods in the map model`.

### Task 4 — Drawing the floor and the sprites (visual)

**Create:** `studio/packages/scene/src/draw/{floor.ts,vehicle.ts,facility.ts,node.ts,scale-bar.ts,palette.ts}`. **Modify:** `studio/packages/scene/src/index.ts`.
**Interfaces (produces):** `scene.labels: boolean` (default true; false hides every text, for Task 8); `scene.detail: Detail | null`; drawing functions `(g: Graphics, item, scale: number, detail: Detail) => void`.

- [ ] With `projection.map` present, `draw()` uses the layers of spec §5.1; without it, the code path of today, unchanged. The floor strips and chevrons go in the cached static layer (extend the signature with the map's strips). Entities of non-placeable kinds are not drawn when a map is present (M24); `registerRenderer` still wins for its kind; `renderProjection` (the layout editor) passes no map and is unchanged (M23).
- [ ] Sprites as spec §5.4 and the mockup: vehicle body with nose, state colour and mark, load mark, fleet stripe; warehouse with shelf pattern, bay squares on its nodes and the link line, badge; parking outline with "P"; charging outline with bolt. Symbol level uses `SYMBOL_PX` and `VehicleItem.offset`; true-size level uses footprints and draws nodes with ids. Every sprite has a light outline (M22's background). Labels: names and counts at symbol level, `VehicleItem.line` at true-size level.
- [ ] Scale bar: bottom-left, a round length (1, 2, 5 x 10^n m) between 60 and 150 px wide, with its text. It is drawn whenever the frame has a layout, map or not (spec §5.9).
- [ ] `fit()` includes the strips' extent. Picking uses the drawn size: at symbol level a vehicle or facility is hit within its symbol.
- [ ] Unit tests where there is logic without a GPU: the scale bar's length for scales 2, 11, 30, 400; picking at symbol level hits a vehicle 8 px from its centre and misses at 20 px.
- [ ] Existing specs pass: `pnpm test`, `pnpm build`, `pnpm test:browser` (viewer, export, compare; the layout editor's canvas test).
- [ ] Renders. **Must see:** lanes as grey strips with chevrons on the loops; both warehouses beside their bays with name and stock; the parking outline at S with its count; AGV-1 amber with pause bars and load; AGV-2 blue pointing west; at 30 px/m vehicles 2 x 1 m with the one-line label and node ids; `production` and the no-layout case exactly as before. Commit `feat(studio): the scene draws the floor and a sprite per kind`.

### Task 5 — Drawing traffic and the selection (visual)

**Create:** `studio/packages/scene/src/draw/{traffic.ts,overlay.ts}`. **Modify:** `src/index.ts`.

- [ ] Held tint over held strips and nodes, one colour (M10). Wait lines, dashed amber, from `model.waitLines` (M11).
- [ ] `scene.select(id)` draws `overlayFor(model, id)`: the holds in a stronger tint, the route as a line along the strips, the wait line thicker, the flow line origin to destination, `related` outlined (spec §5.5). Selecting an id without overlay keeps today's outline.
- [ ] Unit test: with AGV-1 selected on `corridor-wait`, the overlay's route starts at AGV-1's position and ends at node E.
- [ ] Renders with `select=AGV-1`, `select=<an order>`, `select=East`. **Must see:** the corridor ahead of AGV-2 tinted; a dashed line from AGV-1 to AGV-2; with AGV-1 selected its route to East and node C in the stronger tint; nothing tinted on `advanced-charging` if that example runs without reservation control. Commit `feat(studio): held floor, wait lines and the selection overlay`.

### Task 6 — The Overview panel

**Create:** `studio/packages/scene/src/map/overview.ts`, `studio/packages/scene/test/overview.test.ts`, `studio/apps/viewer/src/overview-panel.ts`, `studio/apps/viewer/test/overview-panel.test.ts`. **Modify:** `studio/apps/viewer/src/controller.ts`, `style.css`.
**Interfaces (produces):**

```ts
export interface OverviewLine { id: string; shown: Shown | null; text: string; }
export interface OverviewGroup { fleet: string; counts: Readonly<Record<Shown, number>>; lines: readonly OverviewLine[]; hidden: number; }
export interface Overview { vehicles: readonly OverviewGroup[]; goods: { counts: Readonly<Record<string, number>>; lines: readonly OverviewLine[]; hidden: number }; traffic: string; facilities: readonly OverviewLine[]; }
export function overview(model: MapModel, time: number, options: { filter: string; waitingOnly: boolean; cap?: number }): Overview; // cap 200
```

- [ ] Tests of `overview` on `corridor-wait`: the vehicle line `AGV-1 · loaded, 1 crate → East · waiting 6 s for AGV-2`; order within a group is stranded, waiting by longest wait, then id (natural order: AGV-2 before AGV-10); `waitingOnly` and `filter` (case-insensitive substring of the line) narrow it; 250 vehicles give 200 lines and `hidden === 50`; `two-fleets` gives two groups; the traffic string with counters and, on `bare-layout`, without them.
- [ ] The panel is a dock panel `overview`, title "Overview", added in `resetLayout()` as a tab beside the Inspector and registered in `#panels`; a saved layout without it still loads. It renders `overview(...)` with DOM text nodes only, at most every 250 ms while playing and at once on pause, step and seek (hook where `displayFrame` flushes the other adapters). A click on a line calls the controller's selection and `scene.focus(id)`. It shows "No map for this trace" when `projection.map` is null. Hidden in presentation mode with the others.
- [ ] Panel test (jsdom, as the neighbouring viewer tests): lines rendered from a fixture; a click selects; a label containing `<img onerror>` appears as text.
- [ ] Browser check in `studio/browser/viewer.spec.ts`: the panel exists after opening `tests/fixtures/traces/generated/example_traffic.simtrace`. Commit `feat(studio): the Overview panel`.

### Task 7 — The click detail, in the viewer and the offline player

**Create:** `studio/packages/scene/src/map/describe.ts`, `test/describe.test.ts`. **Modify:** `studio/apps/viewer/src/controller.ts` (`updateInspector`), `studio/apps/player/src/index.ts`, `player.css`.
**Interfaces (produces):** `export function describe(model: MapModel, state: State, id: string, time: number): readonly string[] | null;` — the readable lines of spec §5.7, or null for a kind it does not cover.

- [ ] Tests: the lines for AGV-1 on `corridor-wait` (state, load and order, node and heading, speed 0, battery with percentage, route with the next node marked, the wait with since, duration, node, resources and blockers, holds, size); for an order, a warehouse, the parking area, a node, a segment and a zone; `null` for a server; on `bare-layout` the lines that need missing fields are absent.
- [ ] Viewer: the Inspector shows `describe(...)` first and today's raw fields in a collapsed `<details>` below (M14); numerics refresh at the Inspector's existing 15 Hz.
- [ ] Player: the selection shows `describe(...)` above its raw state view (M15); no panel.
- [ ] Browser: in `viewer.spec.ts`, selecting a vehicle through the entity list shows a line starting with its label; the offline HTML spec selects a vehicle and finds the same line, with no network request and no page error.
- [ ] Review focus 5: a hostile label in the fixture state appears as text in both. Commit `feat(studio): a readable detail for the selected element`.

### Task 8 — Screenshot comparison, export frame, bench

**Create:** `studio/browser/map.spec.ts`, `studio/browser/map.spec.ts-snapshots/` (Linux Chromium images). **Modify:** `studio/playwright.config.ts` (a second web server for the harness), `studio/browser/export.spec.ts`, `studio/bench/glyphs.py`, `studio/bench/README.md`, `.github/workflows/ci.yml` only if the harness needs a build step.

- [ ] `map.spec.ts` opens the harness with `labels=0` for: `corridor-wait` fit; `corridor-wait` at 30 px/m; `corridor-wait` with `select=AGV-1`; `advanced-charging` fit. On Chromium it compares the canvas with `toHaveScreenshot` (`maxDiffPixelRatio: 0.01`); on Firefox and WebKit it asserts no page error and that the canvas is not one flat colour. Baselines are produced in the Playwright container whose tag equals the pinned `@playwright/test` version; the report gives the command used.
- [ ] `export.spec.ts`: a PNG export of `example_traffic.simtrace` completes and its first image is not one flat colour.
- [ ] `glyphs.py` is rewritten on a declared network (`Lattice` or `Lanes`): `--workload fleet100` (100 vehicles, 400 nodes, `ReservationController`) and `--workload lanes1000` (1,000 vehicles on 50 lanes). Run both through `studio/bench/run.mjs` on Chromium; record open time, seek p50/p95 and scene p95 in `specs/research/2026-11-25-studio-map-acceptance.md`.
- [ ] Gate: scene p95 below 16.7 ms on both. If not, stop and report to the controller with the profile; do not optimise beyond caching static geometry (spec §3, §8.4).
- [ ] Render `fleet100` at fit and check review focus 4 by eye. Commit `test(studio): screenshots of fixed frames and the map workloads of the bench`.

## Stage 2 — Python, after Assignment 3 (see the stage gate)

### Task 9 — Vehicle size and the bundle's new target fields

**Modify:** `src/simulatte/intralogistics/agv.py`, `src/simulatte/layout/{auto.py,types.py,bundle.py,lifecycle.py}` as needed, `studio/packages/trace/src/layout.ts`, their tests, `tests/fixtures/traces/generate.py` outputs.
**Interfaces (produces):** `AGVType.width: float | None = None`; `TargetMetadata.size: tuple[float, float] | None` (vehicles), `TargetMetadata.node_ids: tuple[str, ...] | None` (warehouse, parking area, charging station; filled in Task 10); algorithm name `"layered-v2"` exactly when a target carries either field.

- [ ] Tests: `width` validated like `length`; a vehicle whose type has `length=2.0, width=1.0` gets the footprint `{width: 2.0, depth: 1.0}`; length only gives depth 1.0 (half); no length gives 1 x 1 and no `size`; an explicit footprint wins.
- [ ] Tests: a production-only model and a model whose vehicles have no length keep `layered-v1` and their three hashes byte for byte (compare with values pinned before the change); a sized vehicle gives `layered-v2`; `reresolve_presentation` of a saved `layered-v1` bundle gives the same output as before.
- [ ] Both readers accept both names and the optional fields and reject any other name or key; the TypeScript tests use a regenerated fixture.
- [ ] Re-pin what moved, with the cause; full Python and TypeScript gates. Commit `feat(layout): a vehicle's size in its placement and in the bundle`.

### Task 10 — Facilities placed at their node

**Modify:** `src/simulatte/layout/auto.py`, the facility constructors' target registration (`warehouse.py`, `parking.py`, `charging.py` or `layout/lifecycle.py`), tests under `tests/layout/`.

- [ ] Tests of spec §6.3 on hand cases, exact coordinates: one bay at the end of a lane (the warehouse lies beyond the node, 1 m gap, half its extent); two bays (anchor at their mean); a node whose neighbour vectors cancel (direction +y); a warehouse overlapping an explicit placement (moved by its own extent along the direction until clear); two warehouses on one node (id order); parking and charging centred on their node with a 3 x 3 m footprint, two on one node coinciding; a facility with no binding and a server keep the grid; an explicit position wins.
- [ ] Determinism: the same model under `PYTHONHASHSEED` 0 and 1 gives the same bundle.
- [ ] The four intralogistics examples: assert each facility's centre is within 6 m of one of its nodes.
- [ ] Re-pin; gates. Commit `feat(layout): unplaced facilities are placed at the node they serve`.

### Task 11 — Waits and routes as vehicle state

**Modify:** `src/simulatte/intralogistics/{agv.py,reservation.py,travel.py,events.py}`, tests under `tests/intralogistics/`, fixtures.
**Interfaces (produces):** `agv` state fields `wait: {since, node, resources, blockers} | None` and `route: {nodes, destination, reason} | None` (spec §6.2).

- [ ] Before coding: read the Assignment 3 rulings on wait reporting; write in the report how each `traffic.wait_started` / `wait_ended` emit site sets and clears `wait`, and which event clears `route` for each travel outcome (`ARRIVED`, `RETRY_FROM_CURRENT_POSITION`, `BATTERY_STRANDED`, `NO_ROUTE`, `GAVE_UP`, an interrupted travel).
- [ ] Tests: after `traffic.wait_started` the vehicle's state has the event's values and `since == env.now`; after `wait_ended` it is null, for every `reason`; `Trace.state_at` at a cursor inside a wait returns it (review focus 1); `route` is set by `agv.routed`, replaced by a reroute, null after each outcome; an idle vehicle has both null.
- [ ] The snapshot of a bare run equals that of an observed run (`tests/intralogistics/test_invariance.py`, the random-world monitor) with the new fields.
- [ ] Cost: time a bare run of the corridor example (10 repetitions, median) and of one random world before and after; record both in the acceptance file. Above 2 % stop and report.
- [ ] Re-pin digests and fixtures with the cause; gates including the `bench` job's scripts run locally. Commit `feat(intralogistics): a vehicle's wait and route are recorded as state`.

### Task 12 — Capacities and traffic counters

**Modify:** `parking.py`, `charging.py`, `agv.py`, `fleet.py`, `reservation.py`, `events.py`, tests.
**Interfaces (produces):** state fields `parking_area.capacity: int`, `charging_station.slots: int`, `agv.battery_capacity: float`, `fleet.waits`, `fleet.stuck_waits`, `fleet.deadlocks: int` (spec §6.4, §6.6).

- [ ] Tests: the constants are in the initial state and never appear in a delta; each counter increments by a delta of its event, for the fleet of the vehicle; a deadlock of two vehicles of one fleet counts once, of two fleets once in each; bare and observed snapshots agree.
- [ ] Re-pin; gates. Commit `feat(intralogistics): capacities and traffic counters in the state`.

### Task 13 — Recorded fixtures replace the patched ones (visual)

**Modify:** `tests/fixtures/traces/generate_map_frames.py`, `studio/fixtures/map/*.json`, `studio/browser/map.spec.ts-snapshots/`, `studio/browser/viewer.spec.ts`.

- [ ] Remove `--patch` and the hand-computed positions; regenerate every fixture from recorded state. `bare-layout` becomes a hand-stripped copy (fields deleted in the script) and stays.
- [ ] Every Stage 1 test passes on the recorded fixtures. Where a value moved (a position, a wait duration), the test is updated and the report lists each with the reason; a difference in shape of `wait`, `route` or the targets is a defect of Task 9 to 12 or of Task 2, 3 and is fixed there.
- [ ] Browser, review focus 1: open a fresh trace of the corridor example, seek by the timeline to inside a wait, select the waiting vehicle through the entity list; the detail names its blocker and the Overview's traffic line says 1 waiting.
- [ ] Acceptance 1: record the four intralogistics examples, render each at fit, and list in the report anything drawn as a bare box.
- [ ] Renders of every frame, baselines regenerated. **Must see:** the same as Tasks 4 and 5, now from recorded data. Commit `test(studio): the map's fixtures are recorded, not patched`.

### Task 14 — Documentation, amendments, acceptance record

**Modify:** `docs/` (Studio pages, intralogistics pages, the 0.18.0 migration notes), `docs/development/studio.md`, `CLAUDE.md`, `CONTRIBUTING.md` if the harness adds a command, the amended specs named in the design's header, `specs/research/2026-11-25-studio-map-acceptance.md`.

- [ ] Spec §7, item by item. The legend image in the docs is a render from the harness.
- [ ] One amendment note appended to each amended spec, naming the section and the decision id.
- [ ] The acceptance file lists, for each of the seven acceptance points of spec §10, the evidence and the commit; point 4 (Davide's review of the frames) is left open for the controller.
- [ ] `uv run --all-extras zensical build`; all gates. Commit `docs: the Studio map, the new state fields and the placement of facilities`.
