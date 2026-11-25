# Studio map acceptance

Branch `feature/studio-map`, measured on `a67c5ce` with the bench changes of the commit `test(studio): the map workloads of the bench`; "after" also has the commit `perf(studio): the static part of the map model is computed once per layout`.

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
| Seek p50 / p95 | 222 / 936 ms | 109 / 470 ms | 520 / 857 ms | 518 / 1,076 ms |
| Scene p95 | 5.5 ms | 4.8 ms | 32.0 ms | 21.6 ms |
| of which projection p95 | 4.0 ms | 3.4 ms | 19.1 ms | 8.6 ms |
| of which the rest of the render p95 | 2.0 ms | 2.0 ms | 14.6 ms | 13.9 ms |
| Gate (scene p95 below 16.7 ms) | met | met | not met | not met |

The gate is met on `fleet100` and is not met on `lanes1000`, before and after the caching. The caching halved the projection of `lanes1000` (19.1 to 8.6 ms; `boundNodes` scanned the binding table once per facility, 100 facilities against about 1,100 bindings) and left the rest of the render, the drawing of the map and the glyphs, at about 14 ms: drawing alone is still below 16.7 ms and drawing plus projection is not. Nothing else was optimised. The seek figures of the runs vary a great deal between runs (see below).

### Where the seek time goes

Seek p50 / p95 of 0.1 to 1.1 s is far above the viewer's older 200 ms target. A first seek was split into the playback session's `prepare` (decoding the chunk and reconstructing the state) and the scene's `render` of the frame (`coldPrepare*` and `coldRender*` in the bench result).

| First seek, `lanes1000` | prepare p50 / p95 | render p50 / p95 |
| --- | ---: | ---: |
| each seek followed by a scene render, map on (three runs, after) | 495 / 1,053 ms; 1,269 / 3,169 ms; 141 / 243 ms | 22.5 / 25.6 ms |
| the same, network removed from the frame's layout (`nomap=1`) | 50 / 76 ms | 6.0 / 7.5 ms |
| no render at all (`norender=1`, three runs) | 28.6 / 56.2; 30.3 / 59.3; 32.1 / 60.8 ms | none |

| First seek, `fleet100` | prepare p50 / p95 | render p50 / p95 |
| --- | ---: | ---: |
| each seek followed by a scene render, map on (three runs, after) | 104 / 465 ms (median) | 5.1 / 6.6 ms |
| network removed (`nomap=1`) | 26 / 52 ms | 2.4 / 3.6 ms |
| no render at all (three runs) | 8.5 / 20.8; 9.9 / 22.5; 10.2 / 23.7 ms | none |

Decoding and reconstructing the state is not slow: 9 to 10 ms (p50) on `fleet100` and about 30 ms on `lanes1000` when nothing is drawn between the seeks, which is inside the 200 ms target. The scene's own `render` is 5 and 22 ms. The time appears when the scene draws a frame with the map between two seeks: the next `prepare` then takes 100 ms to 1.3 s, a different amount in every run of the same trace. With the map off it takes 26 and 50 ms. A CPU profile of the seeks of `lanes1000` accounts for the time as native time outside JavaScript (84 % of the profile is `(program)`, JavaScript decoding is about 1.9 s in total and garbage collection 0.9 s in about 80 s). A full collection after each render (`--js-flags=--expose-gc`, `gc=1`) changed nothing (prepare p50 131 ms with and 126 ms without, in that session), and waiting for the next animation frame after each render took 0.1 ms at the median. The mechanism is therefore not found; it is visible only after drawing the map, not in the decoding, and it is not the projection. It was not so before the map: the SP3 report has first seeks of 4.8 / 7.5 ms for 1,000 vehicles drawn as glyphs. The drawing with the map issues far more graphics work (strips, held floor, sprites) and the cost is paid by the next main-thread task rather than by the `render` call; this is a hypothesis to be tested, for example with a GPU timeline, not a measurement.

The bench page options that produced these figures are `seeks=N`, `nomap=1`, `norender=1`, `settle=1` and `gc=1`; the page and runner are in `studio/bench/`.
