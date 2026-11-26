# Studio map acceptance

Branch `feature/studio-map`, measured on `a67c5ce` with the bench changes of the commit `test(studio): the map workloads of the bench`; "after" also has the commit `perf(studio): the static part of the map model is computed once per layout`. The figures on the GPU-backed renderer (below) were measured again on `def9c3f`, the fix wave of Stage 1, with the bench changes of the commit `test(studio): the bench runs on a GPU-backed renderer and reports it`.

Machine: bigboss, macOS 27.2.1, Apple M4 Pro (12 cores), 48 GB. CPython 3.14.3, Node 24.13.0, Playwright Chromium 156.0.8078.4, headless, viewport 1440 x 1000, scene 1280 x 720.

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

The gate is met on `fleet100` and is not met on `lanes1000`, before and after the caching. The caching halved the projection of `lanes1000` (19.1 to 8.6 ms; `boundNodes` scanned the binding table once per facility, 100 facilities against about 1,100 bindings) and left the rest of the render, the drawing of the map and the glyphs, at about 14 ms: drawing alone is still below 16.7 ms and drawing plus projection is not. Nothing else was optimised. The seek figures of the runs vary a great deal between runs (see below).

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
