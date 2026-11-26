# Studio map acceptance

Branch `feature/studio-map`, measured on `ea0148b` with the bench changes of the commit `05fb053` (`test(studio): the map workloads of the bench`); "after" also has `ace19b6` (`perf(studio): the static part of the map model is computed once per layout`). The figures on the GPU-backed renderer (below) were measured again on `b2c9e2c`, the last commit of the fix wave of Stage 1, with the bench changes of `c5195ab` (`test(studio): the bench runs on a GPU-backed renderer and reports it`). These are the hashes after the branch was rebased onto `432ac64` (Assignment 3 of the layout branch); the measurements were made before the rebase, on commits with the same subjects.

Machine: bigboss, macOS 27.2.1, Apple M4 Pro (12 cores), 48 GB. CPython 3.14.3, Node 24.13.0, Playwright Chromium 156.0.8078.4, headless, viewport 1440 x 1000, scene 1280 x 720.

## The seven acceptance points of the design (§10)

Each point of §10 of [the design](../2026-11-25-studio-map-design.md), with the evidence and the commit that holds it. The branch has not been pushed, so no CI run exists; "passes" below means a local run on bigboss (CPython 3.14.3, Node 24.13.0, Playwright 1.64.0) unless it says otherwise. Evidence that is an implementer's or a reviewer's report, and not something in the repository, is marked **report**; those reports are in `.superpowers/sdd/2026-11-25-studio-map/`, which is not part of the repository, and the renders are in `.superpowers/sdd/2026-11-24-studio-map/renders/` (`renders/task-15/` for the last set).

| # | Point | State |
|---|-------|-------|
| 1 | Four examples open with facilities at their nodes, lanes as strips, vehicles with heading, state and load, nothing a bare box | Met on the renders and the fixtures; the look is a visual judgement |
| 2 | A seek into a wait in the corridor example shows the waiting vehicle, its blocker and the held corridor; panel and detail say the same | Met |
| 3 | The three levels hold what §5 says and nothing depends on crossed events | Met |
| 4 | The screenshot frames are stored and were reviewed by Davide | Open: Stage 1 reviewed, Stage 2 not yet shown |
| 5 | The frame budget holds on both bench workloads | Met on `fleet100`; not met on `lanes1000`, accepted by Davide as a known limit |
| 6 | The existing Studio specs, the Python suite and the CI gates pass after the re-pins | Passes locally at `7395d5f`; CI not run, x86_64 baselines unverified |
| 7 | The documentation and the amendment notes are written | Written in `c1c63a1` |

### 1. The four examples

- **Evidence.** `tests/fixtures/traces/generate_map_frames.py` records the four examples (corridor seed 19 at t = 128, advanced seed 42 at t = 22960, simple seed 0 at t = 12, intermediate seed 0 at t = 78), asserts the facts the tests rely on (`check_corridor`, `check_advanced`, `check_moving`), and writes `studio/fixtures/map/*.json`; `tests/core/test_map_frame_fixtures.py` regenerates them and compares. The placement of each facility at its node is pinned by `tests/layout/test_facility_placement.py` (positions on the corridor and advanced examples and on hand cases). The vehicle size is pinned by `tests/layout/test_vehicle_size.py`. The renders `corridor-symbol`, `corridor-true`, `advanced-symbol`, `advanced-true`, `simple-fit` and `intermediate-fit` show every facility beside its node, strips with chevrons, and vehicles with heading, state mark and load; the Task 15 report records that nothing is drawn as a bare box or away from its node (**report**; the controller also looked at `corridor-symbol`, `corridor-select-agv`, `intermediate-fit` and `bench-fleet100`).
- **Commits.** `0ccf5bb` and `a0914cc` (vehicle size and `layered-v2` in the bundle), `f06c377` and `055f442` (facilities placed at their nodes), `d68c2ab`, `0986991`, `d929f65` and `487a81d` (the recorded fields), `7395d5f` (recorded fixtures and the renders of the four examples).
- **What the renders show that is not a defect of the test.** In the simple example the two vehicles always occupy the same position, so at true size one hides the other; a warehouse whose output bay is far from its first input bay is joined to it by a long line (`advanced-symbol`: Bulk Storage crosses the charging station); in the 100-vehicle bench the warehouses placed at interior lattice nodes cover part of a lane and some of their labels overlap. These follow from §5.2 and §6.3 and are listed in the guide as limits.
- **Screenshot comparison.** Only the corridor and advanced examples are compared against stored images (point 4). The simple and intermediate shots are renders, which no test compares.

### 2. A seek into a wait

- **Evidence.** `studio/browser/viewer.spec.ts`, "a seek into a recorded wait shows it in the detail and the Overview without playing through its start": it opens `tests/fixtures/traces/generated/example_traffic.simtrace`, seeks to t = 128, selects AGV-1, and requires the detail to say `Blocked by AGV-2` and the Overview's Traffic section to say `1 waiting`; it passes on Chromium, Firefox and WebKit (Task 15). The Python reader gives the same state: `tests/intralogistics/test_wait_route_state.py` reads `wait` from `Trace.state_at` inside a wait and null after it. The map model is asserted as data in `studio/packages/scene/test/map-items.test.ts` (a waiting vehicle and its wait line) and `map-floor.test.ts` (the "held resources" cases: held nodes, segments and zones). The render `corridor-symbol` shows AGV-1 amber with its ring and a dashed line to AGV-2, and the held tint on J1-M1-C and D-M2-J2 and none on C-D. That the map alone lets a reader name the three is a visual judgement on that render, made by the controller and the implementer (**report**).
- **Commits.** `d68c2ab` (the `wait` and `route` state), `0986991`, `44fdfcb` and `e7865be` (held floor, wait lines), `399a901` and `a4afa8e` (panel and detail), `ea0148b` (browser specs), `7395d5f` (the seek spec on a recorded trace).

### 3. The three levels and crossed events

- **Evidence.** The level of detail is a function of the zoom (`detailAt`, threshold and symbol sizes), tested in `map-floor.test.ts` and `map-items.test.ts`, and picking at each level in `pick.test.ts`; the map's text, the panel and the detail are asserted as data in `map-items.test.ts` (labels, `doing`, short labels), `overview.test.ts` and `apps/viewer/test/overview-panel.test.ts` (lines, order, filter, caps, hostile labels) and `describe.test.ts` (the readable detail). The browser specs in `map.spec.ts` check the panel and the detail with hostile labels, refs, and in-place updates without a rebuild. Nothing the map shows reads crossed events: the model is built from the frame's `state`, `initialState` and `layout` only, and the harness and every fixture test give the frame no crossed events (`crossedEvents: []`); the only reader of `crossedEvents` in the scene is the decorative job trails of `project.ts`, which the map does not draw. The seek test of point 2 is the test of goal 6.
- **Commits.** `c1e9e7d`, `f90e92a` and `440cc59` (the map model), `41214e9` (floor, scale bar), `c30c58f` and `9d8edb5` (sprites), `44fdfcb` and `e7865be` (traffic), `399a901` (panel), `a4afa8e` and `cac20ea` (detail), `526a552` (one source for names, waits and stock), `b2c9e2c` (in-place update tests).

### 4. The screenshot frames

- **Stored.** Seven Linux Chromium baselines in `studio/browser/map.spec.ts-snapshots/` (`corridor-symbol`, `corridor-true`, `corridor-select-agv`, `advanced-symbol`, `no-layout`, `production`, `sprites-symbol`), compared with `maxDiffPixels: 300`, made in the Playwright 1.64.0 container on linux/arm64. Commits `4e2b2ad` (the first four) and `7671694` (the seven, after the fix wave). Task 15 compared them again without `--update-snapshots`: 13 passed, the seven shots compared and did not skip (**report**). CI on x86_64 is unverified.
- **Reviewed by Davide.** Open, for the controller to complete. Stage 1 renders were reviewed by Davide on 2026-11-25 ("the look is fine"). The Stage 2 renders (the recorded fixtures, the simple and intermediate examples, the 100-vehicle bench) have not yet been shown to him.

### 5. The frame budget

- **Evidence.** The sections "Bench (Stage 1)", "On the GPU-backed renderer" and "Bench (Stage 2, recorded fields)" below. On `fleet100` the scene p95 is 4.7 ms with the recorded fields (budget 16.7 ms): met. On `lanes1000` it is 21.9 ms: not met. Davide accepted the 1,000-vehicle frame time as a known limit for 0.18.0 on 2026-11-25 (option A), so the point holds for the 50 to 100 vehicle target and does not hold, and is not claimed to, for 1,000 vehicles. The recording cost of the Python additions is in "Stage 2: recording cost": at most +1.2 % on the corridor example (with subscribers), +0.9 % on a bare wait-heavy random world, below the 2 % limit of §8.5.
- **Commits.** `05fb053` (the workloads), `ace19b6` (the caching), `c5195ab` (the bench on a GPU-backed renderer), `7395d5f` (the Stage 2 figures); `d68c2ab`, `0986991`, `d929f65` and `487a81d` for the recording cost. The seek investigation is `.superpowers/sdd/2026-11-25-studio-map/task-10b-seeks-report.md` (**report**).

### 6. The existing specs, the Python suite and the CI gates

- **Evidence at `7395d5f`** (Task 15 report, **report**; the commands are the repository's): `uv run --all-extras pytest` 5309 passed, 1 skipped, 0 failed, coverage 99.21 % (the gate is 99 %); `ruff check` and `ruff format --check` clean; in `studio/`, `pnpm typecheck`, `pnpm test` (trace 351, playback 24, scene 207, export 15, viewer 86), `pnpm build` and `pnpm test:browser` (93 passed on Chromium, Firefox and WebKit). The bench job's overhead gate against `simulatte==0.12.0` was run locally at the Task 13 and Task 14 commits and passed (at Task 14: -24.9 % and -23.7 % against 0.12.0, +8.6 % and +6.7 % against the stripped copy, limit +12.5 %); "Stage 2: recording cost" has the numbers.
- **Re-pins**, each shown to be caused by its task only: `0ccf5bb` three (the `code:system` bundle digest, the `example_traffic` fixture and the map generator's bare-layout strip); `f06c377` one digest (`code:system`), three hashes of `simple()` and one regenerated trace fixture and one TypeScript test; `d68c2ab` fourteen run fingerprints and the `example_traffic` fixture (shown by recomputing the pins with the new fields stripped from the digest); `d929f65` the same fourteen again. No run fingerprint moved for the size or the placement, because the fingerprint does not include the layout.
- **Not covered.** No CI run: the branch is not pushed. The PyPy lane, the Windows and macOS `execution-native` lanes and actual Safari were not run for these changes. The screenshot baselines were made on arm64 and CI is x86_64.
- **Commits.** `0ccf5bb`, `a0914cc`, `f06c377`, `055f442`, `d68c2ab`, `0986991`, `d929f65`, `487a81d` and `7395d5f`.

### 7. The documentation and the amendment notes

- **Evidence.** `c1c63a1`: the guide `docs/guides/studio-map.md` with the legend image `docs/assets/studio-map-sprites.png`, `docs/development/studio.md`, `docs/guides/intralogistics.md`, `docs/guides/layout.md`, `docs/api/intralogistics.md`, `docs/api/layout.md`, the 0.18.0 entries of `CHANGELOG.md`, `CLAUDE.md`, `CONTRIBUTING.md`; section 14 and the corrected sentences of the design; one "Studio map amendment" note appended to each of the AGV traffic, SP3 viewer and layout-only designs. `uv run --all-extras zensical build` reports "No issues found".

## Bench (Stage 1)

The workloads are real fleets recorded through `studio/bench/glyphs.py`, on a network declared on the layout. The recordings are made before Stage 2 and do not contain its fields (wait, route, capacities, counters, sizes): the map draws them as a trace without those fields, facilities sit in the layout grid with a leader line to their bay, and vehicles are 1 x 1 m. The numbers are measured again after Stage 2.

`fleet100`: 100 vehicles on a 20 x 20 `Lattice` (5 m spacing, node capacity 2) under `ReservationController`, 12 warehouses on lattice nodes, one order every 2 s on average, 300 simulated seconds, no deadlock. At t = 280 about 33 vehicles move, about 25 wait or load and about 25 are idle, and part of the floor is held. `lanes1000`: 1,000 vehicles on 50 `Lanes` of 100 m with the default controller (`FreeFlowController`), 100 warehouses at the lane ends, one order per second per lane, 120 simulated seconds. Reservation control was not used there: it admits one vehicle per node and each lane carries 20 vehicles between its two end nodes, so the fleet could not move as a fleet.

Open is the time to open and index the recording. Seek is the first seek to each of 50 seeded pseudorandom times in an already-open recording (caches retained between times). Scene p95 is over 240 rendered positions after preparation, at 1280 x 720, as in the SP3 report; it times the scene's own `render` call, that is the work on the main thread, not what the graphics process does afterwards.

Each figure is the median of three runs (one browser process each); "before" is the scene without the caching of the static part of the map model, "after" with it. Open is 2.1 s on `fleet100` and 0.5 s on `lanes1000` in every run.

| Median of three runs | `fleet100` before | `fleet100` after | `lanes1000` before | `lanes1000` after |
| --- | ---: | ---: | ---: | ---: |
| Trace size | 4,218,142 bytes | same | 7,432,776 bytes | same |
| Entities at the end | 1,350 (100 vehicles, 12 warehouses, 400 nodes, 760 segments, 77 orders) | same | 2,150 (1,000 vehicles, 100 warehouses, 50 fleets, 100 nodes, 900 orders) | same |
| Open | 2,113 ms | 2,140 ms | 509 ms | 509 ms |
| Scene p95 | 5.5 ms | 4.8 ms | 32.0 ms | 21.6 ms |
| of which projection p95 | 4.0 ms | 3.4 ms | 19.1 ms | 8.6 ms |
| of which the rest of the render p95 | 2.0 ms | 2.0 ms | 14.6 ms | 13.9 ms |
| Gate (scene p95 below 16.7 ms) | met | met | not met | not met |

Davide accepted the 1,000-vehicle frame time for 0.18.0 on 2026-11-25 as a known limit; the 50 to 100 vehicle target is met.

These runs were made in headless Chromium without a GPU, so WebGL ran on SwiftShader (see "Where the seek time goes"). The scene p95 above times the scene's own `render` call on the main thread and is not affected by that; the seek figures are, and are replaced by those of the GPU-backed renderer below. What the first measurement saw for seek p50 / p95, in software: `fleet100` 222 / 936 ms before and 109 / 470 ms after, `lanes1000` 520 / 857 ms before and 518 / 1,076 ms after.

### On the GPU-backed renderer

Headless Chromium 156.0.8078.4 launched by `studio/bench/run.mjs` with `--use-angle=metal --enable-gpu --ignore-gpu-blocklist`; every run reported the WebGL renderer `ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)` (`softwareRendered` false). Three runs per workload, one browser process each, default page options (`?mode=performance`, 50 first seeks, 240 rendered positions); each figure is the median of the three runs, with the three runs in brackets where they spread. The machine's load average was 4 to 12 during the runs.

| Median of three runs | `fleet100` | `lanes1000` |
| --- | ---: | ---: |
| Open | 1,906 ms | 495 ms |
| Seek p50 / p95 | 15.1 / 29.4 ms (p95 28.3 to 29.7) | 53.4 / 81.2 ms (p95 76.4 to 83.6) |
| of which prepare p50 / p95 | 9.5 / 21.6 ms | 31.0 / 57.1 ms |
| of which render p50 / p95 | 5.0 / 7.6 ms | 22.1 / 30.2 ms |
| Scene p95 | 4.2 ms | 22.1 ms (21.6 to 22.3) |
| of which projection p95 | 2.8 ms | 9.5 ms |
| of which the rest of the render p95 | 1.6 ms | 14.4 ms |
| Gate (scene p95 below 16.7 ms) | met | not met |

The gate result is unchanged: met on `fleet100` and not met on `lanes1000`, whose scene p95 is CPU time on the main thread (projection about 9.5 ms and drawing about 14.4 ms). The seeks are inside the viewer's 200 ms target on both workloads.

The gate is met on `fleet100` and is not met on `lanes1000`, before and after the caching. The caching halved the projection of `lanes1000` (19.1 to 8.6 ms; `boundNodes` scanned the binding table once per facility, 100 facilities against about 1,100 bindings) and left the rest of the render, the drawing of the map and the glyphs, at about 14 ms: drawing alone is still below 16.7 ms and drawing plus projection is not. Nothing else was optimised.

### Where the seek time goes

The first measurement found first seeks of 0.1 to 1.1 s, with the time in the playback session's `prepare` only when the scene had drawn a map between two seeks, and could not find the mechanism. The cause is the way the bench ran, not the viewer (`.superpowers/sdd/2026-11-25-studio-map/task-10b-seeks-report.md`, verified with Chrome traces).

Headless Chromium without a GPU runs WebGL on SwiftShader, which rasterizes on the CPU, and composites in software. In that mode every commit of a WebGL canvas reads the whole frame back synchronously on the renderer's main thread (`ProxyMain::BeginMainFrame` > `Commit` > `LayerTreeHost::DoUpdateLayers` > `GLES2::ReadPixels` > `CommandBufferHelper::Finish`), waiting until SwiftShader has rasterized the frame's queued commands. `scene.render()` only queues them and returns in 5 to 22 ms; the commit comes at the next `BeginMainFrame`, which is reached when the next seek's `prepare` first yields to the event loop (a `Blob` read or the `DecompressionStream` of a chunk), so the rasterization of the previous frame was counted in that `prepare`. A map frame issues far more geometry than the glyphs of the SP3 report (strips, held floor, sprites, about 33,000 triangles on `fleet100` and 96,000 on `lanes1000`), so the wait was 100 ms to 1.3 s and varied with the machine's CPU load. Waiting for an animation frame after each render did not separate it, because animation frame callbacks run before the commit; waiting past the paint (an animation frame, then a task) does, and the bench's `settle=1` now does so.

With a GPU (headless with ANGLE on Metal, or headed) there is no readback on the main thread, and `prepare` after a map render equals `prepare` with nothing drawn: on `lanes1000` 31.0 ms p50 against 28.6 to 32.1 ms with no render at all in the first measurement, and with `settle=1` the wait past the paint was 0.2 / 0.3 ms p50 / p95. The figures above are therefore what a user with a GPU-accelerated browser sees. A browser without GPU acceleration (a blocklisted GPU, a virtual machine, a remote desktop) that falls back to SwiftShader WebGL would show the same wait as frame-to-frame stalls; this follows from the mechanism and was not measured. Safari and Firefox were not measured.

`studio/bench/run.mjs` now launches Chromium on macOS with those flags (`--headed` runs a visible window on any platform), and every result reports `webglRenderer` and `softwareRendered`. The bench page options are `seeks=N`, `nomap=1`, `norender=1`, `settle=1` and `gc=1`; the page and runner are in `studio/bench/`.

## Stage 2: recording cost

Task 13 makes a vehicle's traffic wait and route entity state (spec §6.2). Both are kept whether or not anything subscribes; the attributes hold the objects the controller and the travel executor already have and are turned into ids only when a snapshot or a delta is built. The bare run was timed on an Apple M4 Pro (macOS, CPython 3.14.3), at commit 055f442 (before) and on the working tree of the Task 13 commit (after), from the same process setup: before ran a copy of `src/simulatte` exported from 055f442 first on `PYTHONPATH`. Each workload ran 10 times after one warm-up, interleaved with the other, and the figure is the median of the 10; three such rounds alternated before and after.

| Workload | Before, medians of three rounds (ms) | After (ms) | Change of the mean of the medians |
|---|---|---|---|
| Corridor example (`examples/intralogistics_traffic.py`, `main()`, output discarded) | 24.5, 24.8, 24.5 | 24.8, 24.9, 25.0 | +1.2 % |
| Random world 272 (`world_spec(272)`, `ReservationController`, default router, no monitor, no digest; 22 waits, 37 routes) | 45.7, 45.7, 45.9 | 45.6, 45.9, 45.5 | -0.2 % |
| Random world 182 (the same, the wait-heavy world: 64 waits, 67 routes) | 68.1, 69.0, 69.7 | 69.0, 69.4, 70.3 | +0.9 % |

The two random worlds are bare runs: nothing subscribes to any event. The corridor example is not: it attaches `TrafficKPIs` and the coordinator's default `OrderEMACollector`, so its traffic and AGV events are wanted and their deltas are built. World 182 was timed after the review of Task 13, with the same method, against the same 055f442 copy (the working tree then also copied a route returned as a list). All changes are below the 2 % limit of §8.5. The bench job's intralogistics benchmark (`benchmarks/intralogistics.py --shifts 10`, the advanced example scaled up, mode `none`, three runs) gave 2.484 s before and 2.508 s after (+1.0 %, reported only), and its gate (mode `none` on `jobshop10-u90-5k` and `jobshop10-u95-5k` against released 0.12.0 and its stripped copy) passed locally.

Task 14 adds the capacities (spec §6.4: `parking_area.capacity`, `charging_station.slots`, `agv.battery_capacity`, constants written once in the initial state) and the fleet's traffic counters (§6.6: `waits`, `stuck_waits`, `deadlocks`). In a bare run a counted event costs one call that adds one to the counter of each distinct fleet of its vehicles; the delta is built only behind `env.wants`. Timed with the same method on the same machine, at commit 0986991 (before, a copy of its `src/simulatte` first on `PYTHONPATH`) and on the working tree of the Task 14 commit (after); the three workloads ran interleaved in each round.

| Workload | Before, medians of three rounds (ms) | After (ms) | Change of the mean of the medians |
|---|---|---|---|
| Corridor example (not bare: `TrafficKPIs` and `OrderEMACollector` subscribe) | 26.1, 26.1, 25.7 | 25.8, 25.8, 25.9 | -0.5 % |
| Random world 272 (bare; 22 waits, 4 stuck waits, 5 deadlocks) | 47.1, 47.5, 46.9 | 46.6, 47.2, 47.2 | -0.4 % |
| Random world 182 (bare; two fleets, 64 waits, 11 stuck waits, 25 deadlocks counted per fleet) | 70.7, 72.5, 70.6 | 71.1, 71.3, 71.3 | -0.1 % |

The changes are within run-to-run noise and below the 2 % limit. The bench job's intralogistics benchmark (`--shifts 10`, mode `none`, three runs) gave 2.548 s before and 2.542 s after (-0.3 %, reported only), and its gate passed locally (against released 0.12.0: -24.9 % and -23.7 %; against the stripped copy: +8.6 % and +6.7 %, limit +12.5 %).

## Bench (Stage 2, recorded fields)

Task 15 ran both workloads again on traces recorded at commit 487a81d with this branch's Python (`studio/bench/glyphs.py`, default seed and horizon), so the frames carry what Stage 2 records: at the end of `fleet100` 34 vehicles have a `wait` and 69 a `route`, its fleet counted 316 waits, 1 stuck wait and 30 deadlocks, its vehicles carry `size` (1.2 x 0.6 m) and its 12 warehouses `node_ids` and a position at their node; `lanes1000` has 750 routes, no waits (`FreeFlowController`), vehicles of 0.8 x 0.4 m and its 100 warehouses at their nodes. Same method as "On the GPU-backed renderer": headless Chromium 156.0.8078.4 from `studio/bench/run.mjs`, WebGL renderer `ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)` in every run (`softwareRendered` false), no browser error, three runs per workload, default page options, each figure the median of the three runs. The machine's load average was 3.3 to 4.0.

| Median of three runs | `fleet100` | `lanes1000` |
| --- | ---: | ---: |
| Open | 2,091 ms | 511 ms |
| Seek p50 / p95 | 17.0 / 32.6 ms | 55.2 / 83.4 ms (p95 81.0 to 97.7) |
| of which prepare p50 / p95 | 10.3 / 20.8 ms | 32.9 / 59.1 ms |
| of which render p50 / p95 | 5.7 / 8.4 ms | 22.3 / 29.9 ms |
| Scene p95 | 4.7 ms | 21.9 ms |
| of which projection p95 | 2.8 ms | 9.1 ms |
| of which the rest of the render p95 | 2.0 ms | 14.4 ms |
| Gate (scene p95 below 16.7 ms) | met | not met (known limit) |

The gate that matters, `fleet100`, is met: its scene p95 went from 4.2 to 4.7 ms with waits, wait lines, routes and the held floor drawn from recorded state. `lanes1000` stays above the budget, as in Stage 1, which the owner accepted as a known limit of the 1,000-vehicle workload; its projection and the rest of its render are unchanged within noise (9.5 and 14.4 ms before). The seeks stay inside the viewer's 200 ms target on both workloads.
