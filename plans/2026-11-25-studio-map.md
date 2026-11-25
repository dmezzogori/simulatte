# The Studio scene drawn as a map — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** written on 2026-11-25 from the approved design and revised the same day after two independent reviews (one against the design, one against the code; the facts below marked "measured" come from the second). No task is implemented yet.

**Goal:** Studio draws an intralogistics run as a map to scale with a sprite per kind of element, an Overview panel and a readable click detail, all read from the recorded state at the cursor; the simulation records what that needs.

**Architecture:** Stage 1 works under `studio/`: the scene's projection grows a pure map model, drawing functions per kind draw it with PixiJS primitives, and a panel and a detail view are built from the same model. It is developed against frame fixtures (JSON) that already contain the state fields Stage 2 will record. Stage 2 adds those fields and the placement rule on the Python side, after Assignment 3 of the layout branch, and replaces the patched fixtures with recorded ones.

**Stack:** TypeScript, PixiJS 8, React 19, dockview, Vitest, Playwright under `studio/`; Python 3.11+ standard library; Zensical for the docs.

**Spec:** [`specs/2026-11-25-studio-map-design.md`](../specs/2026-11-25-studio-map-design.md). Section numbers and decision ids (`M1` to `M28`) refer to it. The agreed mockup is `specs/research/2026-11-25-studio-map-mockup.html`; where it and the spec differ, the spec wins. Signatures below are boundaries, not licence to weaken a contract stated in the spec.

**Follows:** `feature/layout-only-models`. Branch `feature/studio-map`. Nothing is pushed and no pull request is opened by this plan.

## Global constraints

- **Stage gate.** Tasks 11 to 15 do not start on `66a5a96`. They start after Assignment 3 of the layout branch is complete, on the commit the release coordinator names, after this branch is rebased on it. Its rulings are in `/Users/davide/Developer/simulatte/.superpowers/sdd/2026-11-23-layout-only-models/progress.md` under the heading "Assignment 3"; the controller pastes the rulings on wait reporting into the brief of Task 13 and confirms that spec §6.2 still holds.
- **Stage 1 writes only** under `studio/`, `tests/fixtures/traces/generate_map_frames.py` and `specs/research/2026-11-25-studio-map-acceptance.md`. No file under `src/`.
- The viewer never reduces events: everything the map, the panel and the detail show is a function of `frame.state`, `frame.layout` and `frame.time` (spec goal 6). `frame.crossedEvents` is read only by the existing decorative trails.
- A trace with a layout but without the new fields (`wait`, `route`, capacities, counters, `size`, `node_ids`) draws without error: a missing field means "not known". A projection without a map (no layout, no network, or the layout editor's `renderProjection`) is drawn, fitted and picked exactly as today; the only addition is the scale bar for a frame with a layout (spec §5.9). `studio/scripts/studio-smoke.mjs` exercises the editor's canvas only in CI, so no local gate catches a regression there.
- The public API of `@simulatte/scene` stays: `Scene`, `Projector`, `Glyph`, `Projection`, `equipment`, `motionProgress`, `registerRenderer`, `renderProjection`, `onEditorPointer`, `liveSceneCount`. Fields are added, none removed; new fields of `Projection` are optional.
- Offline player rules: one script, no `eval` (keep `import "pixi.js/unsafe-eval"` first), no remote asset or font, sprites drawn with primitives. Trace labels reach the DOM only through `textContent`.
- Clock-bound values are written imperatively; no React commit per frame.
- Build outputs go to `studio/.build/` (git-ignored and excluded from the asset fingerprint) or outside `studio/`; nothing else untracked may remain under `studio/` after a task, or CI packaging fails.
- `vite` does not resolve from the `studio/` root (measured). Run it as `pnpm --filter @simulatte/scene exec vite <cmd> --config <absolute path>`, with a config that is a plain object with an absolute `root`.
- TypeScript gates, from `studio/`: `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm test:browser`. Python gates: `uv run --all-extras pytest` (coverage >= 99 % branch), `uv run --all-extras ruff check src tests`, `uv run --all-extras ruff format --check`, `uv run --all-extras ty check src`. Read the output of each before reporting it.
- In Python every state change is an event emitted behind `if env.wants(Cls): env.emit(Cls(...))`; state fields are kept whether or not anything subscribes (M19); results do not depend on observers.
- Any task that changes a pinned digest, a layout hash, a trace fixture or a recorded JSON re-pins it in that task and shows that the task is the only cause. The frozen fixtures under `tests/fixtures/traces/frozen/` are never regenerated.
- Nothing about deadlines, venues or papers anywhere in the repository. Conventional commits; each task commits only its own files.

## Measured facts the tests rely on

The corridor example (`examples/intralogistics_traffic.py`), recorded with seed 19 as `tests/fixtures/traces/generate.py` does (`example_traffic.simtrace` is that run), at time 128.0:

- 13 nodes, 22 arcs, 15 strips; no arc has `passing`. One-way strips: `J1~W_N`, `W~W_N`, `W~W_S`, `J1~W_S`, `E_S~J2`, `E~E_S`, `E~E_N`, `E_N~J2`. Two-way: `J1~M1`, `C~M1`, `C~D`, `D~M2`, `J2~M2`, `C~S`, `D~S`.
- AGV-1 at C (35, 0), `TRAVELING_LOADED`, no motion, load `{crate: 1}`, order-0 West to East, heading 0. Its wait began at 121.5426 with node `D`, resources `[corridor:M2, C~D, D]`, blockers `[AGV-2]`. Its route is `[W, W_S, J1, M1, C, D, M2, J2, E_S, E]`.
- AGV-2 in motion M2 to D, at x = 57.53, y = 0, speed 1.5, heading pi, loaded, order-1.
- AGV-3 at W (-5, 0), `WAITING_LOAD`, heading -2.356, no route (it arrived at 103.757).
- Held strips: `C~M1`, `J1~M1`, `D~M2`, `J2~M2`, `W~W_N`. Held nodes: C, D, W, M1, M2. `C~D` is not held.
- East stock 49, West 48; order-3 (origin East) and order-4 (origin West) are `PENDING`. `Siding.parked` is empty. Waits so far 2, deadlocks 0, stuck 0.
- The vehicle type has `length=1.2` and no width, so Stage 2 records the footprint 1.2 x 0.6: lane width 0.84 m, threshold 22 / 1.2 px per metre.
- Around t = 1007 one vehicle waits, one moves and one is idle and parked.

The advanced example (`examples/intralogistics_advanced.py`, `Environment(seed=42)`, `main(plot=False)`): no `ReservationController`, so nothing is held; its type has no length (vehicles stay 1 x 1); at t = 22960 AGV-3 is `CHARGING` at CHRG, `slots_in_use` 1, `Parking.parked` is `[AGV-1, AGV-2, AGV-5]`. `examples/studio_recording.py` `record(path, "shop")` gives a production-only state (layout with `network: null`).

## Review focus

Conditions the spec implies and a person will meet; each has a test in the task named.

1. Seeking into the middle of a wait shows the wait, its blocker and the held floor without playing through its start (Task 3 on a frame; Task 15 on a recorded trace in the browser).
2. A blocker or a route node that is not in the frame: no line is drawn for it, the text names the id, nothing throws (Task 3).
3. A trace of this branch recorded before Stage 2 (layout, no new fields; facilities in the grid; vehicles 1 x 1 m): the map draws, the panel and the detail omit what they cannot know (Tasks 2, 3, 7, 8).
4. Many vehicles on one node or a small canvas: symbols fan out to distinct positions and the panel caps its lists (Tasks 3, 7; looked at by eye in Task 10).
5. Hostile and long labels (closing-script strings, 500 characters, right-to-left marks) in the map, the panel and the detail (Tasks 3, 5, 9).

## How visual results are verified

Tests assert the map model as data and compare four canvases with stored images (labels hidden, Linux Chromium, Task 8). They do not judge the look. Every task marked **visual** therefore ends with renders: the implementer runs `node studio/harness/render.mjs <out-dir>` with `<out-dir>` outside `studio/` (the brief names it), which writes one PNG per shot at device scale 2 with labels on, plus `mockup.png`, a render of the agreed mockup. The report lists the files. The reviewer opens each PNG and checks the task's **must see** and **must not see** lists, with `mockup.png` beside it. "As before" means byte-equal to the Task 1 render of the same shot (`cmp`), not a judgement by eye. The controller shows Davide the renders of Tasks 5, 6 and 15.

Shots of `render.mjs` (name: fixture, scale in px per metre, centre, selection): `corridor-symbol`: corridor-wait, 8, fit centre; `corridor-true`: corridor-wait, 30, (46, 2); `corridor-select-agv`: corridor-wait, 8, select AGV-1; `corridor-select-order`: corridor-wait, 8, select order-0; `corridor-select-facility`: corridor-wait, 8, select East; `idle-symbol`: corridor-idle, 8; `advanced-symbol`: advanced-charging, fit; `advanced-true`: advanced-charging, 30, CHRG; `two-fleets`: 8; `bare`: bare-layout, fit; `sprites-symbol`: sprites, 8; `sprites-true`: sprites, 30; `production`: fit; `no-layout`: fit.

---

## Stage 1 — `studio/` only

### Task 1 — Frame fixtures and the render harness

**Create:** `tests/fixtures/traces/generate_map_frames.py`; `studio/fixtures/map/{corridor-wait,corridor-idle,advanced-charging,two-fleets,bare-layout,production,no-layout,sprites}.json`; `studio/harness/{index.html,main.ts,vite.config.ts,render.mjs}`; `studio/packages/scene/test/fixtures.ts`, `test/fixtures.test.ts`.
**Interfaces (produces):** `export function loadFrame(name: string): PreparedFrame;` in `test/fixtures.ts`: reads `studio/fixtures/map/<name>.json` (`{time, cursor, state, initialState, layout}`) and fills `mode: "continuous"`, `catalog: {}`, `kpis: {}`, `diagnostics: []`, `crossedEvents: []`, `crossingsComplete: true`.

The generator records the examples as `tests/fixtures/traces/generate.py` `record_example` does (seed 19 for the corridor; the advanced example with its own seed and `main(plot=False)`), takes `Trace.state_at((t, large seq))` and `Trace.layout`, and writes JSON with sorted keys. With `--patch` (the default until Task 15) it adds what Stage 2 will record:

- vehicles: `wait` (from the last `traffic.wait_started` not followed by `wait_ended` for that vehicle: `{since, node, resources, blockers}`) and `route` (from the last `agv.routed`: `{nodes, destination, reason}`, null once the vehicle's `node` equals the destination with no motion), `battery_capacity`; corridor vehicles get the placement footprint `{width: 1.2, depth: 0.6}` and `size: [1.2, 0.6]` in their target; advanced vehicles are not resized;
- `parking_area.capacity`, `charging_station.slots`, fleet `waits`, `stuck_waits`, `deadlocks` counted from the events up to the cursor;
- facility targets get `node_ids`; facility placements get the position of spec §6.3, written as literals in the script with a comment giving the arithmetic.

Fixtures: `corridor-wait` (t = 128.0); `corridor-idle` (the first whole second in 1000..1015 with one vehicle waiting, one moving, one idle; the script finds and prints it); `advanced-charging` (t = 22960); `two-fleets` (corridor-wait with AGV-3's `fleet` set to `"shuttles"`); `bare-layout` (corridor-wait without the patch); `production` (`studio_recording.record(path, "shop")`, t = 60); `no-layout` (corridor-wait with `layout: null`); `sprites` (hand-built in the script, no simulation: a row of nodes joined by one-way, two-way and `passing` strips; one vehicle for each row of the spec §5.4 state table plus an unknown state and a `STRANDED` vehicle that also has a wait, one loaded, three vehicles on one parking node; a warehouse with three SKUs and two `PENDING` orders; two charging stations on one node; an entity of kind `conveyor`; one vehicle labelled `</script><img src=x onerror=alert(1)>`, one with a 500-character label, one with a right-to-left mark).

- [ ] The script asserts the measured facts above for `corridor-wait` and `advanced-charging` and fails otherwise.
- [ ] `fixtures.test.ts`: every fixture loads; all but `no-layout` have a layout; `corridor-wait` has `state["AGV-1"].wait.blockers` equal to `["AGV-2"]` and `bare-layout` has no `wait` key.
- [ ] Harness: `vite.config.ts` is a plain object with an absolute `root` and `build.outDir` `studio/.build/harness`. The page reads `?frame=&scale=&x=&y=&select=&labels=`, creates a `Scene` of 1200 x 500, renders the fixture, applies the camera (fit when `scale` is absent) and the selection, and sets `document.title = "ready"`. Unknown parameters are ignored, so later tasks add theirs.
- [ ] `render.mjs <out-dir>` builds and serves the harness, takes the shots listed above at device scale 2, and renders the mockup file inside a minimal HTML page to `mockup.png`. It refuses an `<out-dir>` under `studio/`.
- [ ] Run it; the shots show today's drawing. Keep this output: later tasks compare `production` and `no-layout` with it. `git status` shows nothing untracked under `studio/` except the committed sources. Commit `test(studio): frame fixtures and a render harness for the scene`.

### Task 2 — Map model: floor, sizes, level of detail

**Create:** `studio/packages/scene/src/map/{model.ts,floor.ts}`, `test/map-floor.test.ts`. **Modify:** `src/project.ts`, `src/index.ts`.
**Interfaces (produces):**

```ts
export type Detail = "symbol" | "true";
export interface Strip { id: string; a: Point; b: Point; width: number; oneWay: boolean; passing: boolean; held: boolean; }
export interface MapNode extends Point { id: string; held: boolean; }
export interface MapModel {
  time: number;
  laneWidth: number;                    // metres, nominal (spec §5.3)
  threshold: number;                    // px per metre where true size begins (spec §5.2)
  strips: readonly Strip[];
  nodes: readonly MapNode[];
  vehicles: readonly VehicleItem[];     // empty until Task 3
  facilities: readonly FacilityItem[];  // empty until Task 3
  waitLines: readonly WaitLine[];       // empty until Task 3
  orders: readonly OrderItem[];         // empty until Task 3
  fleets: readonly string[];            // distinct `fleet` values of the vehicles, sorted
  traffic: TrafficSummary;              // zeros and nulls until Task 3
}
export interface Projection { /* existing fields */ map?: MapModel | null; }
export const SYMBOL_PX = { vehicle: 22, facility: 36, strip: 10 } as const;
export function detailAt(model: MapModel, scale: number): Detail;
// on Scene:
get map(): MapModel | null;   // the map of the last projection
```

Task 2 declares the Task 3 item types as empty interfaces in `model.ts`; Task 3 fills them. `map` is null when the frame has no layout or the layout has no `network.graph.arcs`. A strip's id is its two node ids joined by `~`, smaller first by code-unit comparison (`<`), as `src/simulatte/intralogistics/network.py` does; never `localeCompare`. `held` is not part of the static-layer signature.

- [ ] Tests on `corridor-wait`: 13 nodes; 15 strips; the one-way set and the two-way set of the measured facts; `laneWidth` is 0.84 (to 1e-9); `threshold` is `22 / 1.2`; `detailAt(model, 18)` is `"symbol"`, `detailAt(model, 18.4)` is `"true"`. On `bare-layout`: `laneWidth` 1.4, `threshold` 22. With no vehicle in the state: `laneWidth` 1, `threshold` 22.
- [ ] Test on `sprites`: the `passing` strip is `2 * laneWidth` wide.
- [ ] Held (spec §5.5): on `corridor-wait` the held strips are exactly `{C~M1, J1~M1, D~M2, J2~M2, W~W_N}` and the held nodes exactly `{C, D, W, M1, M2}`; on `advanced-charging` nothing is held.
- [ ] `production` and `no-layout` give `map == null`, and for every fixture the glyph list (ids, positions, sizes) is unchanged: snapshot it before changing the code and compare.
- [ ] Implement; `pnpm --filter @simulatte/scene test`, `pnpm typecheck`. Commit `feat(studio): the map model of the floor`.

### Task 3 — Map model: vehicles, facilities, waits, goods

**Create:** `studio/packages/scene/src/map/{vehicles.ts,facilities.ts,overlay.ts}`, `test/map-items.test.ts`. **Modify:** `src/map/model.ts`, `src/project.ts`, `src/motion.ts`, `src/index.ts`.
**Interfaces (produces):**

```ts
export function motionSpeed(value: unknown, time: number): number | null; // motion.ts; null when it cannot be evaluated
export type Shown = "stranded" | "waiting" | "moving" | "working" | "idle" | "charging" | "resting" | "unknown";
export interface Wait { since: number; node: string; resources: readonly string[]; blockers: readonly string[]; }
export interface Route { nodes: readonly string[]; destination: string; reason: string; }
export interface VehicleItem extends Point {
  id: string; label: string; short: string;
  heading: number; length: number; width: number;
  shown: Shown; state: string; load: Readonly<Record<string, number>>; loaded: boolean;
  fleet: string | null; fleetIndex: number | null;   // index in MapModel.fleets; null with fewer than two fleets
  order: string | null; wait: Wait | null; route: Route | null;
  goal: string | null;      // label of the facility bound to route.destination, else that node id, else null
  holds: readonly string[]; // ids of nodes, segments and zones whose holder lists contain it
  speed: number | null; battery: number | null; batteryCapacity: number | null;
  offset: Point;            // pixels, fan-out at symbol level
  doing: string;            // see below
  line: string;             // map label at true-size level
}
export interface FacilityItem extends Point {
  id: string; kind: "warehouse" | "parking_area" | "charging_station"; label: string;
  width: number; depth: number; rotation: number; nodes: readonly string[];
  count: string; badge: number; offset: Point;
}
export interface WaitLine { agv: string; blocker: string; from: Point; to: Point; }
export interface OrderItem { id: string; sku: string; quantity: number; origin: string; destination: string; status: string; agv: string | null; picked: boolean; createdAt: number; }
export interface TrafficSummary { waiting: number; longestWait: number; waits: number | null; stuckWaits: number | null; deadlocks: number | null; }
export interface Overlay { holds: readonly string[]; route: readonly Point[]; waitLine: WaitLine | null; flow: readonly [Point, Point] | null; related: readonly string[]; }
export function overlayFor(model: MapModel, id: string | null): Overlay | null;
```

Rules the tests pin:

- `shown` is the first matching row of the spec §5.4 table (`resting` is its "travelling, at rest"); a state the table does not know is `"unknown"`.
- `doing` is: `stranded`; `waiting <n> s for <first blocker>` with `n = Math.floor(model.time - wait.since)`; `charging`; `loading` (`WAITING_LOAD`) or `unloading` (`WAITING_UNLOAD`); `moving`; `stopped` (resting); `idle`; for unknown, the recorded state in lower case.
- `line` is `<label> · to <goal>` when `shown` is `moving` and `goal` is known, otherwise `<label> · <doing>`.
- `short` is the label when it has at most four characters, otherwise the trailing digits of the id, otherwise `""`.
- Facility `nodes` are the `endpoint` values of the layout `bindings` whose key is `<id>:node`, `<id>:input_bays/<i>` or `<id>:output_bays/<i>`. `count`: a warehouse with one SKU shows its quantity as an integer string, with several `"<k> SKUs"`; parking `"<parked.length> / <capacity>"`; charging `"<slots_in_use> / <slots>"`; `""` when the capacity is not recorded. `badge` counts orders with `origin` equal to the id and status `PENDING`.
- `offset`: vehicles sharing a position, and facilities sharing a position, are spread along x by `SYMBOL_PX` of their kind plus 2 px, centred, in id order (natural order: `AGV-2` before `AGV-10`).
- `load` is `{}` when the recorded `load` is null.

- [ ] Tests on `corridor-wait`: AGV-1 `shown` `"waiting"`, `loaded`, `doing` `"waiting 6 s for AGV-2"`, `line` `"AGV-1 · waiting 6 s for AGV-2"`, `short` `"1"`, `goal` `"East"`, `holds` containing `C`, `C~M1`, `corridor:M1`; AGV-2 `"moving"`, x within 0.01 of 57.53, `speed` 1.5, heading pi, `line` `"AGV-2 · to West"`; AGV-3 `"working"`, `doing` `"loading"`; one `WaitLine` from (35, 0) to AGV-2's position; `traffic` equal to `{waiting: 1, longestWait: 6.457 (to 1e-3), waits: 2, stuckWaits: 0, deadlocks: 0}`; East `count` `"49"`, West `"48"`, both `badge` 1; Siding `count` `"0 / 3"`.
- [ ] Tests on `sprites`: one case per `Shown` value, including the stranded vehicle with a wait (stranded) and the unknown state; the three vehicles on the parking node have three distinct offsets; the two charging stations have distinct offsets; the warehouse `count` is `"3 SKUs"` and `badge` 2; the `conveyor` entity is in `projection.glyphs` and in no list of the map; the three hostile labels are in `label` unchanged.
- [ ] Tests on `bare-layout`: every vehicle has `wait` and `route` null, `shown` from its state and motion; Siding `count` `""`; the totals of `traffic` are null; nothing throws.
- [ ] Review focus 2: with AGV-2 deleted from the state, no `WaitLine`, and `doing` still names `AGV-2`; with a route node unknown to the layout, `Overlay.route` skips it.
- [ ] `overlayFor` on `corridor-wait`: for `AGV-1`, `route` starts at (35, 0) and ends at node E's position, `holds` as above, `waitLine` set, `flow` from West's position to East's; for `order-0` the same `flow` and `related` `["AGV-1"]`; for `East`, `related` holds the vehicles whose `route.destination` is one of its nodes; null for `null`, for an unknown id and for a server.
- [ ] 100 vehicles on one node get 100 distinct offsets (review focus 4).
- [ ] Implement; scene tests and typecheck. Commit `feat(studio): vehicles, facilities, waits and goods in the map model`.

### Task 4 — Drawing the floor, the layers and the scale bar (visual)

**Create:** `studio/packages/scene/src/draw/{floor.ts,node.ts,scale-bar.ts,palette.ts}`, `test/scale-bar.test.ts`. **Modify:** `src/index.ts`, `studio/harness/main.ts`.
**Interfaces (produces):** `scene.labels: boolean` (default true; false hides every text; harness parameter `labels=0`); `export function scaleBar(scale: number): { metres: number; px: number }`; `palette.ts` exports the colours of every element, including reserved entries `rack`, `staging`, `dock` that nothing uses yet (spec §5.4, goal 7).

- [ ] With `projection.map` present, `draw()` uses the layers of spec §5.1. The strips and chevrons are in the cached static layer, whose signature gains the strips' geometry (not `held`). A strip is drawn `max(width * scale, SYMBOL_PX.strip)` pixels wide at symbol level and `width * scale` at true-size level (M26). Chevrons as spec §5.3. Nodes are drawn only at true-size level or when selected.
- [ ] Glyphs of non-placeable kinds (orders, fleets) are not drawn, not labelled and not pickable when a map is present (M24). `registerRenderer` still wins for its kind.
- [ ] Vehicles and facilities keep today's glyph drawing in this task; Task 5 replaces it.
- [ ] `scaleBar`: the largest of 1, 2, 5 x 10^n metres whose length is at most 150 px. Tests: scale 2 gives 50 m (100 px); 11 gives 10 m; 30 gives 5 m (150 px); 400 gives 0.2 m. It is drawn bottom-left with its text whenever the frame has a layout, and never for `renderProjection`.
- [ ] `fit()` includes the strips' extent when a map is present. Without a map, `draw`, `fit` and `pick` take today's code path unchanged.
- [ ] `pnpm test`, `pnpm build`, `pnpm test:browser`.
- [ ] Renders. **Must see:** grey strips at least 10 px wide for every lane; chevrons on the eight one-way strips of the corridor; a scale bar in every shot with a layout; node circles with ids only in the `*-true` shots. **Must not see:** boxes for orders or the fleet near the origin in the corridor shots. `no-layout` is byte-equal to its Task 1 render; `production` differs from it only by the scale bar. Commit `feat(studio): the scene draws the floor as strips with a scale bar`.

### Task 5 — Drawing the sprites, labels and picking (visual)

**Create:** `studio/packages/scene/src/draw/{vehicle.ts,facility.ts}`, `test/pick.test.ts`. **Modify:** `src/index.ts`.

- [ ] Sprites as spec §5.4 and the mockup, colours from `palette.ts`: the vehicle body with a nose toward `heading`, the state colour and mark for each `Shown`, the load mark, the fleet stripe when `fleetIndex` is not null; the warehouse with a shelf pattern, a bay square on each of its `nodes`, a thin line from the bay to the footprint when they do not touch, the badge when it is above 0; the parking outline with "P"; the charging outline with a bolt. Every sprite has a light outline.
- [ ] Symbol level: sprites are `SYMBOL_PX` long, drawn at the true position plus `offset`. A facility symbol that would cover one of its own nodes is moved along the direction from that node to the facility's centre until its edge is 4 px clear of the bay square. True-size level: footprints, no offsets.
- [ ] Labels: at symbol level the facility's label and `count`, and the vehicle's `short`; at true-size level also `VehicleItem.line` above the vehicle. Labels are Pixi `Text` set from strings, as today.
- [ ] Picking with a map: at symbol level a vehicle or a facility is hit inside its drawn symbol (including `offset`); vehicles are tested before facilities; nodes are not pickable at symbol level. Tests through a function extracted for it: on `corridor-wait` at scale 8, the point 5 px from AGV-1's centre along its heading hits `AGV-1`; a point 30 px away hits nothing; a point inside East's symbol hits `East`.
- [ ] `pnpm test`, `pnpm build`, `pnpm test:browser`.
- [ ] Renders. **Must see:** in `corridor-symbol`, both warehouses beside their bays with name and stock and a badge, the parking outline at S with `0 / 3`, AGV-1 amber with pause bars, ring and load mark, AGV-2 blue with load mark pointing west, AGV-3 blue with the working mark at W, short labels 1, 2, 3; in `corridor-true`, vehicles 1.2 x 0.6 m with their one-line labels; in `idle-symbol`, a grey vehicle at the parking area; in `advanced-symbol`, a green vehicle with a bolt at the charging station and three vehicles fanned out at the parking area; in `two-fleets`, a stripe on every vehicle, AGV-3's in another colour; in `sprites-symbol` and `sprites-true`, one recognisable sprite per row of the spec §5.4 table, the two charging stations side by side, the three hostile labels as plain text; in `bare`, the map drawn with the facilities in the grid and long bay lines. **Must not see:** a bare box for any vehicle or facility; text other than names, counts and short labels in the symbol shots; a facility symbol covering its bay. `no-layout` and `production` as in Task 4. Commit `feat(studio): a sprite per kind of element`.

### Task 6 — Drawing traffic and the selection (visual)

**Create:** `studio/packages/scene/src/draw/{traffic.ts,overlay.ts}`. **Modify:** `src/index.ts`.

- [ ] Held strips and nodes get one translucent tint (M10). Each `WaitLine` is a dashed amber line (M11).
- [ ] `scene.select(id)` draws `overlayFor(scene.map, id)`: `holds` in a stronger tint (a zone id tints its member strips and nodes), `route` as a line along the strips, the wait line thicker, `flow` as a thin arrow, `related` outlined. An id without overlay keeps today's selection outline. `scene.focus(id)` on an order centres on its vehicle, else on its origin facility.
- [ ] `pnpm test`, `pnpm build`, `pnpm test:browser`.
- [ ] Renders. **Must see:** in `corridor-symbol`, the tint on J1–M1–C, on D–M2–J2 and on W–W_N with nodes C, D, W, M1, M2, and a dashed line from AGV-1 to AGV-2; in `corridor-select-agv`, AGV-1's route from C to E, its holds in the stronger tint and an arrow West to East; in `corridor-select-order`, that arrow and AGV-1 outlined; in `corridor-select-facility`, East and the vehicles bound for it outlined. **Must not see:** a tint on C–D; any tint in the `advanced-*` shots. Commit `feat(studio): held floor, wait lines and the selection overlay`.

### Task 7 — The Overview panel

**Create:** `studio/packages/scene/src/map/overview.ts`, `test/overview.test.ts`; `studio/apps/viewer/src/overview-panel.ts`. **Modify:** `studio/packages/scene/src/index.ts`, `studio/apps/viewer/src/{controller.ts,style.css}`, `studio/harness/main.ts`, `studio/browser/viewer.spec.ts`.
**Interfaces (produces):**

```ts
export interface OverviewLine { id: string; shown: Shown | null; text: string; }
export interface OverviewGroup { fleet: string; counts: Readonly<Record<Shown, number>>; lines: readonly OverviewLine[]; hidden: number; }
export interface Overview { vehicles: readonly OverviewGroup[]; goods: { counts: Readonly<Record<string, number>>; lines: readonly OverviewLine[]; hidden: number }; traffic: string; facilities: readonly OverviewLine[]; }
export function overview(model: MapModel, options: { filter: string; waitingOnly: boolean; cap?: number }): Overview; // cap 200
// overview-panel.ts
export function renderOverview(root: HTMLElement, value: Overview | null, onSelect: (id: string) => void): void;
// on Controller
select(id: string | null): void;   // the one place that sets the selection (scene, inspector, panel)
```

Texts: a vehicle line is `<label> · <"loaded, " + quantities, or "empty"><" → " + goal when known> · <doing>`, quantities as `<n> <sku>` joined by `, `; a goods line `<id> · <quantity> <sku> <origin> → <destination> · <"on " + agv, or agv + ", not picked", or "unassigned">`; `traffic` is `<waiting> waiting · longest <n> s` followed, when the totals are known, by ` · waits <w> · stuck <s> · deadlocks <d>`; a facility line `<label> · <sku> <qty>, ...` for a warehouse and `<label> · <count>` otherwise.

- [ ] Tests on `corridor-wait`: the first vehicle line is `AGV-1 · loaded, 1 crate → East · waiting 6 s for AGV-2`; `traffic` is `1 waiting · longest 6 s · waits 2 · stuck 0 · deadlocks 0`; on `bare-layout` it is `0 waiting · longest 0 s`. Order in a group: stranded, then waiting by longest wait, then natural id order. `waitingOnly` and `filter` (case-insensitive substring of the text) narrow the vehicle lines. 250 vehicles give 200 lines and `hidden` 50. `two-fleets` gives two groups. Goods: open orders (status not `DELIVERED`) oldest first by `createdAt`, capped, with counts by status. Facilities: one line each in id order.
- [ ] The panel: dock panel id `overview`, title "Overview", added in `resetLayout()` in the Inspector's group with `inactive: true`, and added the same way after a saved layout is restored without it. It calls `renderOverview(root, scene.map ? overview(...) : null, id => { this.select(id); scene.focus(id); })`. `renderOverview` writes text with `textContent` only and shows "No map for this trace" for null (M27). Under the scene, the existing legend line gains "Lane width is nominal." when a map is present (spec §5.3).
- [ ] Refresh: at most every 250 ms while playing; at once when the frame changes while paused and when the playback status changes (pause, end, buffering). `Playback.pause()` emits the same frame object, so the flush hangs on the status change, not on a new frame.
- [ ] `Controller.select` replaces the three inline closures that set the selection today; behaviour of the scene click, the entity list and Escape is unchanged (the existing specs pass).
- [ ] Harness: `?panel=1` mounts `renderOverview` beside the canvas. `studio/browser/viewer.spec.ts`: after opening `tests/fixtures/traces/generated/example_traffic.simtrace` the Overview tab exists and the Inspector is still the active tab. (The click and hostile-label checks of the panel are in Task 9's spec, which has the harness server.)
- [ ] Renders are unchanged from Task 6 (the panel is not in them). Commit `feat(studio): the Overview panel`.

### Task 8 — The click detail, in the viewer and the offline player

**Create:** `studio/packages/scene/src/map/{describe.ts,detail-view.ts}`, `test/describe.test.ts`. **Modify:** `studio/packages/scene/src/index.ts`, `studio/apps/viewer/src/controller.ts`, `studio/apps/player/src/{index.ts,player.css}`, `studio/harness/main.ts`.
**Interfaces (produces):**

```ts
export interface DetailLine { text: string; refs?: readonly string[]; } // refs: ids named in the line that can be selected
export function describe(model: MapModel, state: State, id: string): readonly DetailLine[] | null; // null for a kind it does not cover
export function renderDetail(root: HTMLElement, lines: readonly DetailLine[], onSelect: (id: string) => void): void; // textContent only; each ref a button
```

- [ ] Tests on `corridor-wait`: for `AGV-1` the lines of spec §5.7 in its order, among them `Waiting 6 s (since 121.5) to enter D`, `Needs corridor:M2, C~D, D` with those three refs, `Blocked by AGV-2` with that ref, `Route W → W_S → J1 → M1 → C → [D] → M2 → J2 → E_S → E` (the next node in brackets), `Size 1.2 × 0.6 m`, and a battery line with a percentage; for `order-0`, `East`, `Siding`, node `C`, segment `C~M1` and zone `corridor:M1`, at least the holder, the members and the bound nodes as refs; null for a server and for an unknown id. On `bare-layout` AGV-1 has no wait, route, size-from-type or percentage line and nothing throws.
- [ ] Viewer: when `describe` returns lines, the Inspector shows them through `renderDetail` first and today's raw fields below in a collapsed `<details>` (M14); a ref calls `Controller.select`. The lines are rebuilt on the same schedule as the Overview (250 ms while playing, flush on status change), not on every frame.
- [ ] Player: the selection shows `renderDetail` above its raw state view; a ref selects (M15).
- [ ] Harness: `?detail=<id>` mounts `renderDetail` for that id.
- [ ] `pnpm test`, `pnpm build`, `pnpm test:browser`; the offline HTML spec still passes. Commit `feat(studio): a readable detail for the selected element`.

### Task 9 — Browser specs of the map: screenshots, panel, detail, export

**Create:** `studio/browser/map.spec.ts`, `studio/browser/map.spec.ts-snapshots/*-chromium-linux.png`. **Modify:** `studio/playwright.config.ts`, `studio/browser/{viewer.spec.ts,export.spec.ts}`.

- [ ] `playwright.config.ts`: `webServer` becomes an array; the second entry builds the harness to `studio/.build/harness` and serves it on port 4174 (the command of the global constraints).
- [ ] Screenshots, `labels=0`: `corridor-symbol`, `corridor-true`, `corridor-select-agv`, `advanced-symbol`. When the project is chromium and `process.platform === "linux"`, compare the canvas with `toHaveScreenshot` (`maxDiffPixelRatio: 0.01`). Otherwise read the canvas pixels in the page and assert that the palette's strip colour, moving colour and waiting colour each cover at least 20 pixels.
- [ ] Baselines: made in `mcr.microsoft.com/playwright:v1.64.0-noble` with `--platform linux/amd64`, from a copy of the tree inside the container (the host `node_modules` holds macOS binaries): `pnpm install --frozen-lockfile`, `pnpm build`, `pnpm exec playwright test map.spec.ts --project chromium --update-snapshots`, then the PNGs copied back. The report gives the exact commands. Residual risk to state in the report: CI runs on the `ubuntu-latest` host, not in this container; a first CI run may need a refresh of the baselines.
- [ ] Panel and detail through the harness with `frame=sprites`: `?panel=1` shows the hostile label as text and no `img` element exists in the page; a click on a line marks it selected. `?detail=<the hostile vehicle>` likewise. With `frame=corridor-wait&detail=AGV-1`, a click on the ref `AGV-2` shows AGV-2's lines.
- [ ] `viewer.spec.ts` with `example_traffic.simtrace`: selecting `AGV-1` in the entity list shows a detail line starting with `AGV-1`; the offline HTML exported from that trace opens from `file://` with no network request and no page error.
- [ ] `export.spec.ts`: a PNG export of `example_traffic.simtrace` completes; the first image, decoded in the page from the stored zip as `compare.spec.ts` does, contains at least 20 pixels of the strip colour.
- [ ] All three browsers locally: `pnpm test:browser`. Commit `test(studio): browser specs of the map`.

### Task 10 — The map workloads of the bench

**Modify:** `studio/bench/{glyphs.py,vite.config.ts,run.mjs,README.md}`. **Create:** `specs/research/2026-11-25-studio-map-acceptance.md`.

- [ ] `glyphs.py` is rewritten on a declared network: `--workload fleet100` (100 vehicles on a `Lattice` of 400 nodes under `ReservationController`) and `--workload lanes1000` (1,000 vehicles on 50 `Lanes`). The bench's vite config gets an absolute `root` and the command of the global constraints; the README's commands are corrected and run as written.
- [ ] `run.mjs` gains `--shot <png>`: a screenshot of the scene at fit after the measurements.
- [ ] Run both workloads on Chromium; record open time, seek p50/p95 and scene p95 in the acceptance file with the commit and the machine.
- [ ] Gate: scene p95 below 16.7 ms on both. If not, stop and report with the profile; do not optimise beyond caching static geometry (spec §3, §8.4).
- [ ] Review focus 4 by eye on the `fleet100` shot: symbols distinguishable, no text beyond names, counts and short labels. Commit `test(studio): the map workloads of the bench`.

## Stage 2 — Python, after Assignment 3 (see the stage gate)

### Task 11 — Vehicle size and the bundle's new target fields

**Modify:** `src/simulatte/intralogistics/agv.py`; `src/simulatte/layout/{auto.py,types.py,bundle.py,identity.py,lifecycle.py}` as needed (the algorithm name is defined in `identity.py` and checked in `bundle.py`); `studio/packages/trace/src/layout.ts` (both checks of the algorithm name and the target key set); their tests; the generated fixtures.
**Interfaces (produces):** `AGVType.width: float | None = None`; `TargetMetadata.size: tuple[float, float] | None` (vehicles; the resolved length and width, so a type with `length=1.2` and no width gives `(1.2, 0.6)`), `TargetMetadata.node_ids: tuple[str, ...] | None` (warehouse, parking area, charging station; filled in Task 12); algorithm name `"layered-v2"` exactly when a target carries either field.

- [ ] Tests: `width` validated like `length`; `length=2.0, width=1.0` gives the footprint `{width: 2.0, depth: 1.0}`; length only gives depth half the length; no length gives 1 x 1 and no `size`; an explicit footprint wins.
- [ ] Tests: a production-only model and a model whose vehicles have no length keep `layered-v1` and their three hashes (compare with values pinned before the change); a sized vehicle gives `layered-v2`; `reresolve_presentation` of a saved `layered-v1` bundle gives the output it gave before.
- [ ] Both readers accept both names and the optional fields and reject any other name or key; the TypeScript tests use a regenerated fixture.
- [ ] Re-pin what moved, with the cause; all gates. Commit `feat(layout): a vehicle's size in its placement and in the bundle`.

### Task 12 — Facilities placed at their node

**Modify:** `src/simulatte/layout/auto.py`, the target registration of `warehouse.py`, `parking.py`, `charging.py`, tests under `tests/layout/`.

- [ ] Tests of spec §6.3 on hand cases, exact coordinates: a warehouse with one bay at the end of a lane (beyond the node, 1 m gap, half its extent); a warehouse with several bays (anchored on its first input bay, M28); a node whose neighbour vectors cancel (direction +y); a warehouse overlapping an explicit placement (moved by its own extent along the direction until clear); two warehouses on one node (id order); parking and charging centred on their node with a 3 x 3 m footprint, two on one node coinciding; a facility with no binding and a server keep the grid; an explicit position wins.
- [ ] Determinism: the same model under `PYTHONHASHSEED` 0 and 1 gives the same bundle.
- [ ] The four intralogistics examples: each warehouse's centre is within `half its extent + 1 m + 1e-9` of its first input bay along the rule's direction, unless the overlap rule moved it, which the test then names.
- [ ] Re-pin; gates. Commit `feat(layout): unplaced facilities are placed at the node they serve`.

### Task 13 — Waits and routes as vehicle state

**Modify:** `src/simulatte/intralogistics/{agv.py,reservation.py,travel.py,events.py}`, tests under `tests/intralogistics/`, fixtures.
**Interfaces (produces):** `agv` state fields `wait: {since, node, resources, blockers} | None` and `route: {nodes, destination, reason} | None` (spec §6.2).

- [ ] Before coding: from the rulings in the brief, write in the report how each `traffic.wait_started` / `wait_ended` emit site sets and clears `wait`, and which event clears `route` for each travel outcome (`ARRIVED`, `RETRY_FROM_CURRENT_POSITION`, `BATTERY_STRANDED`, `NO_ROUTE`, `GAVE_UP`, an interrupted travel).
- [ ] Tests: after `traffic.wait_started` the state has the event's values and `since == env.now`; if Assignment 3 made the simulation announce a wait again when its blockers change, the second announcement rewrites `resources` and `blockers` and keeps `since`; after `wait_ended` the field is null, for every `reason`; `Trace.state_at` at a cursor inside a wait returns it (review focus 1); `route` is set by `agv.routed`, replaced by a reroute, null after each outcome; an idle vehicle has both null.
- [ ] The snapshot of a bare run equals that of an observed run (`tests/intralogistics/test_invariance.py`, the random-world monitor) with the new fields.
- [ ] Cost: time a bare run of the corridor example (10 repetitions, median) and of one random world before and after; record both in the acceptance file. Above 2 % stop and report.
- [ ] Re-pin digests and fixtures with the cause; all gates and the `bench` job's scripts run locally. Commit `feat(intralogistics): a vehicle's wait and route are recorded as state`.

### Task 14 — Capacities and traffic counters

**Modify:** `parking.py`, `charging.py`, `agv.py`, `fleet.py`, `reservation.py`, `events.py`, tests.
**Interfaces (produces):** state fields `parking_area.capacity: int`, `charging_station.slots: int`, `agv.battery_capacity: float`, `fleet.waits`, `fleet.stuck_waits`, `fleet.deadlocks: int` (spec §6.4, §6.6).

- [ ] Tests: the constants are in the initial state and in no delta; each counter increments by a delta of its event, for the fleet of the vehicle; a deadlock of two vehicles of one fleet counts once, of two fleets once in each; bare and observed snapshots agree.
- [ ] Cost: the same timing as Task 13, before and after this task, recorded; above 2 % stop and report. The `bench` job's scripts run locally.
- [ ] Re-pin; gates. Commit `feat(intralogistics): capacities and traffic counters in the state`.

### Task 15 — Recorded fixtures replace the patched ones (visual)

**Modify:** `tests/fixtures/traces/generate_map_frames.py`, `studio/fixtures/map/*.json`, `studio/browser/map.spec.ts-snapshots/`, `studio/browser/viewer.spec.ts`, `studio/harness/render.mjs`.

- [ ] Remove `--patch` and its literals; regenerate every fixture from recorded state. `bare-layout` is produced by deleting the new fields in the script. `sprites` stays hand-built.
- [ ] Every Stage 1 test passes on the recorded fixtures. Where a literal moved (a time, a position, a count), the test is updated and the report lists each with its reason. A difference in the shape of `wait`, `route` or the targets is a defect of Tasks 11 to 14 or of Tasks 2 and 3 and is fixed there, not papered over in the fixture.
- [ ] Browser, review focus 1, in `viewer.spec.ts` with the regenerated `example_traffic.simtrace`: fill the timeline with 128 (inside AGV-1's wait, or the instant the generator now reports), select `AGV-1` in the entity list; the detail has a line starting with `Blocked by` and the Overview's traffic line starts with `1 waiting`.
- [ ] Acceptance 1: `render.mjs` gains shots of `intralogistics_simple` and `intralogistics_intermediate` at fit (the generator writes a frame of each); the report lists anything drawn as a bare box or away from its node.
- [ ] Bench again (acceptance 5): both workloads of Task 10 on traces recorded now; numbers appended to the acceptance file; the same gate.
- [ ] Renders of every shot; baselines regenerated by Task 9's recipe. **Must see / must not see:** the lists of Tasks 4, 5 and 6, now from recorded data. Commit `test(studio): the map's fixtures are recorded, not patched`.

### Task 16 — Documentation, amendments, acceptance record

**Modify:** `docs/` (Studio pages, intralogistics pages, the 0.18.0 migration notes), `docs/development/studio.md`, `CLAUDE.md`, `CONTRIBUTING.md` (the harness and its render command), the amended specs named in the design's header, `specs/research/2026-11-25-studio-map-acceptance.md`.

- [ ] Spec §7, item by item. The legend image in the docs is the `sprites-symbol` render. The docs name the shapes reserved for racks, staging areas and docks.
- [ ] One amendment note appended to each amended spec, naming the section and the decision id.
- [ ] The acceptance file lists, for each of the seven acceptance points of spec §10, the evidence and the commit; point 4 (Davide's review of the frames) is left open for the controller.
- [ ] `uv run --all-extras zensical build`; all gates. Commit `docs: the Studio map, the new state fields and the placement of facilities`.
