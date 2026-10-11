# SP3 implementation and acceptance evidence

This supersedes the partial [preview report](2026-10-11-sp3-preview-evidence.md).
Work is on `feature/sp3-viewer`, based on released 0.14.0 (`632923d`). The package
version remains 0.14.0 during development; no 0.15 release, merge or PR is claimed.
The approved [SP3 design](../2026-10-11-sp3-viewer-design.md) remains authoritative.

**Status:** SP3 implementation and acceptance are complete. Automated
Chromium/WebKit/Firefox checks are complemented by Davide’s manual actual-Safari
checks, including his final confirmation that all remaining checks pass.
WebDriver session creation still times out; no automated Safari pass is claimed.

## Implemented scope

- Trace 1.2 optional observer records, KPI display declarations and portable motion
  keyframes, retaining 1.0/1.1 reading and released-reader verification semantics.
- Indexed event/sample access, immutable incremental replay, bounded read/cache
  working sets, continuous and exact-event clocks, cancellation and visibility handling.
- A framework-independent Pixi scene, recorded geometry/motion, selection/camera,
  queue markers, status badges and bounded decorative transfer crossings.
- React/compiler shell with docked panels, paged fields/history, KPI series and
  recorded/interpolated sample timestamps, entity schemas (including late kinds),
  event catalog metadata, retired-state inspection and presentation.
- Offline HTML with hashed script/style CSP, complete original bytes and no network.
- Manual MP4/WebM/PNG export, positioned streaming sinks, OPFS ownership/cleanup,
  bounded memory fallback, progress, cancellation and encoder/context error handling.
- Authenticated loopback-only `simulatte view`, optional dependencies, packaged
  assets, source/hash validation, source-race rollback, CI and recording examples.

## Automated checks

On eva, macOS 26, M1 Pro, 16 GB, CPython 3.14.1, Node 24, pnpm 11.5.1:

| Check | Evidence |
| --- | --- |
| `uv run --all-extras pytest` | 2,232 passed; branch coverage 99.15%, unchanged 99% gate |
| Ruff lint / format; `ty check src` | Pass |
| `zensical build --clean`; internal link checker | Pass; 54 pages checked |
| TypeScript typecheck and tests | Pass: trace 274, playback 21, scene 16, export 14 (325 total) |
| Production player/viewer build | Pass; React Compiler enabled |
| Chromium 156.0.8078.4 | 10 browser tests pass |
| Playwright WebKit 27.2 | 10 browser tests pass, persistent storage profile for OPFS |
| Firefox 157, Ubuntu 24.04 on strangelove | 10 browser tests pass in an isolated copied workspace |

Browser tests cover restrictive CSP, exact stepping, seek/presentation, hostile
labels, offline `file://` playback, 10 seconds without frame-proportional React
commits, preparation history remaining inspect-only, retired selection, sample
timestamps, MP4/WebM/PNG OPFS download/removal, cancellation and viewer-position
independence. MP4 tests use the default 1920 × 1080 dimensions; independent FFmpeg
decoding of all four Chromium/WebKit frames reports no errors. The cancel test delays real PNG conversion to prevent a fast browser
from completing before the cancel gesture; output and storage remain real.

Unit regressions also cover same-time stepping, hidden-tab behavior, cursor/KPI
cancellation, bounded indexed seek fallback, replay/crossing parity, transfer
lifetime/queue placement, direct positioned writes, quota errors, worker failure,
late finalization cancellation, cleanup failure and graphics-context loss. Seven
deferred-I/O regressions verify prompt replay cancellation, shared-reader isolation,
removal of canceled queued reads/replays, atomic state commits and replacement after
two stalled HTTP payload reads.

Mac Firefox could not launch its automation profile (`Could not find profile
folder`); Linux Firefox is the verified environment. Actual Safari 27.0.1 was
unavailable to WebDriver because Remote Automation was disabled. An earlier native
capability probe exposed file-handle cloning failure; the OPFS worker now opens its
unique file by name. Davide then explicitly deferred Safari testing. No successful
actual-Safari claim follows from that fix.

## Package and server evidence

Both wheel and sdist contain CLI, `__main__`, server package, frontend, manifest
and dependency notices. The wheel has 15 static files. The sdist includes the
Hatch hook and excludes the root Node workspace, without excluding the Python
`simulatte.studio` package.

Four isolated virtual environments verified core-only install/help, `[studio]`
install, offline build dependencies and installation of the rebuilt wheel. With
Hatchling 1.32.4 preprovisioned, the extracted sdist built under
`PATH=/usr/bin:/bin` and a macOS sandbox denying all network. The driver verified
that Node was absent and network sockets failed with `PermissionError`. The
rebuilt wheel was byte-identical to its source wheel. Final artifact SHA-256:

- Wheel: `8bad981d83cd4ac628759ccf671d850895c0fe1765b405d3d6f127f2f4ac74ca`.
- Sdist: `eb0829b3bb76e5b46c021457a16c9ea13dac83a616676ff218cea0b4e8731943`.

 Temporary-copy changes to
frontend inputs and packaged HTML were rejected. Source changes during asset
copy/publication preserve the previous valid assets. The source fingerprint includes
the imported Python layout JSON schema, with JS/Python parity and stale-schema
regressions.

A browser opened the installed CLI from outside the checkout, consumed the
one-time bootstrap, loaded ranged trace bytes, stepped an event, reloaded with
session credentials and exported HTML. No page errors occurred. Credentials were
kept in process memory, not reports. `studio/scripts/installed-smoke.mjs` runs this
flow against a freshly installed wheel in CI. Server tests cover exact Host/Origin,
per-instance cookie plus header secret, reused bootstrap, ranges/ETag, changed
files, symlink/FIFO rejection, path traversal, binary portable reads and shutdown.

`examples/studio_recording.py` generated both production and fleet recordings;
`Trace.check()` and `verify()` passed for each.

## Reference workloads and performance

Measurements are single local runs, not calibrated CI distributions. “First seek”
means the first seek to each of 50 seeded pseudorandom times in an already-open
recording; cache contents are retained between times. “Repeated” means the
immediate identical seek. These are not 50 fresh-browser cold starts. Scene p95
uses 240 rendered positions after preparation, at 1280 × 720.

| Workload | Trace bytes / SHA-256 |
| --- | --- |
| 50,000 jobs, 10 servers, u95, seed 1 | 83,828,844 / `c6a77e4936e4f0a713f18032da93955852563945a080f6e673e5b4591521b6f6` |
| 1,000 moving AGVs, 50 lanes | 657,444 / `4cbf2253e486a16a745d8c139670069db19b0b2d4f31de721a5007a2b4dc5935` |
| Near-limit HTML, 12,500 observer logs | 103,272,908 / `c89b53e7fb4e5ac6e9faf6999c40e1e998a0f25683318b185638b609aac29dfa` |

The AGV fixture (`studio/bench/glyphs.py`) isolates renderer cost with actual AGV
entities and recorded motion. It is not a fleet throughput benchmark.

| Chromium on eva | 50k job shop | 1,000 AGVs |
| --- | ---: | ---: |
| Open/index | 231.5 ms | 58.2 ms |
| First seek p50 / p95 | 40.6 / 90.9 ms | 4.8 / 7.5 ms |
| Repeated seek p50 / p95 | 0.7 / 3.0 ms | 4.8 / 6.6 ms |
| Scene render p95 | 6.6 ms | 6.4 ms |
| Final estimated decoded cache | 4 chunks / 14,234,128 bytes | 0 chunks |
| Final used JS heap | 133,838,050 bytes | 12,538,765 bytes |
| Peak renderer RSS | 541,376,512 bytes | 371,507,200 bytes |
| Peak browser-process total RSS | 1,013,383,168 bytes | 741,031,936 bytes |
| Longest observed main-thread task, including startup | 482 ms | 1,378 ms |

RSS is sampled every 100 ms using CDP process IDs and macOS `ps`; renderer RSS
includes engine/native allocations, total also includes browser/GPU processes.
It is not the 128 MiB estimated decoded-cache budget. Startup/graphics work can
still create long tasks despite steady-state targets passing. Shared chunk reads have per-caller cancellation: the last departing consumer
aborts the underlying read, and canceled queued work is removed. The two-read cap
and four-chunk cache remain enforced. A custom ByteSource that ignores its abort
signal occupies its physical slot until settling; synchronous bounded chunk decode
cannot be preempted. Built-in HTTP cancellation releases stalled read slots.

The initial forward-seek implementation traversed every skipped event (first-seek
p95 10,843.5 ms). A 400-event scan cap with indexed fallback brought p95 below the
200 ms target. Crossed decorative events retain at most the latest 256, with
incomplete-history metadata preventing invented source locations.

Python full recording: 34.4101 s versus 7.4742 s without recording (4.604×),
1.677 kB/job, 395.85 MB peak process RSS versus 168.26 MB baseline. Cold seek
p50/p95 48.32/57.89 ms; warm 4.61/8.87 ms. These exploratory single-run results
fit the existing recording budgets; no budget was relaxed or calibrated from them.

## Export evidence and limits

Video remains capped at 100,000 frames. PNG is capped at 10,000 under D70, the
approved design's explicit permission to lower provisional limits after memory
measurement. Dimensions remain at most 3840 × 2160, with concrete codec probing;
this does not promise every codec/resolution combination on every platform.

Near-cap exports use 320 × 180; dimensions and frame-count stress are separate
checks. The final export pipeline produced these artifacts with no browser errors:

| Chromium on eva | H.264 baseline MP4 | PNG ZIP64 |
| --- | ---: | ---: |
| Frames | 99,999 | 10,000 |
| Output bytes | 31,448,801 | 76,704,661 |
| Duration of export | 87.264 s | 775.446 s |
| Peak used JS heap | 61,443,219 bytes | 50,862,746 bytes |
| Blank-page renderer RSS baseline | 74,268,672 bytes | 74,334,208 bytes |
| Peak renderer RSS | 338,903,040 bytes | 297,435,136 bytes |
| Increment over baseline | 252.38 MiB | 212.77 MiB |
| Peak browser-process total RSS | 616,497,152 bytes | 626,589,696 bytes |

Full-lifetime RSS sampling was every 100 ms, so transient peaks between samples are
not captured. Renderer growth includes engine/native allocations and is an
approximation of the incremental export allowance, not an isolated allocation
counter. Both measured increments fit the provisional 256 MiB allowance.

FFmpeg decoded all 99,999 MP4 frames without errors; ffprobe reports constrained
baseline H.264, 320 × 180 and 3333.3 seconds. Python verified all 10,001 ZIP entries,
CRCs, final PNG signature and the 10,000-frame timing manifest. ZIP SHA-256:
`7ef57a6e7baca3efc8eb0c14aadf3e42297993dbcfec5e7617db1a8665825946`.
These long exports preceded the final shared-read cancellation and entity-schema
inspection changes; final browser media checks passed after those changes.

A separate format stress test (`studio/bench/zip64.mjs`) produced 100,001 entries
and 4,311,568,608 bytes, including a >4 GiB entry. Python `zipfile.testzip()` passed.
Node peak RSS was 1.61 GB, so this is ZIP64 compatibility evidence, not a valid
100,000-PNG memory promise. A 10,000-entry Chromium directory probe used at most
95,970,737 bytes of JS heap. Archives and videos stream through acknowledged
positioned writes; no complete media byte array is retained by the exporter.

Small Chromium/WebKit/Firefox outputs were saved independently of the viewer.
Firefox VP9 and PNG decode/extract normally. Firefox H.264 high-profile output
produced reordering warnings in FFmpeg; selecting baseline profile removed the
timestamp warnings. Its encoder metadata still triggers SPS parser warnings in
FFmpeg, although all four frames decode with the expected presentation timestamps.
WebM is the verified clean Firefox alternative; do not promise universal MP4
interoperability based solely on `isConfigSupported`.

## Offline HTML

A 103,272,908-byte valid recording generated a 138,396,390-byte HTML file in
1.92 seconds. This fixture contains 12,500 log events and zero entities: its
canvas is intentionally empty, and advancing sequence counters validate event
playback only. Use the fleet example for visible motion and scene testing. With networking disabled, it opened and stepped an event in:

| Browser | Open to prepared frame | Non-file network requests / page errors |
| --- | ---: | --- |
| Chromium 156, eva | 2,050 ms | 0 / 0 |
| WebKit 27.2, eva | 3,898 ms | 0 / 0 |
| Firefox 157, strangelove | 3,478 ms | 0 / 0 |

Chromium used 109,979,194 bytes of JS heap after opening. This is a post-open
measurement, not peak browser RSS. The 100 MiB source / 150 MiB HTML limits remain
conservative product caps; actual Safari manual results are recorded below. Hash CSP
covers scripts and styles; trace text remains data and no network fallback exists.

## Manual Safari acceptance and completion

Davide confirmed that the large offline HTML opens and advances sequence counters
in Chrome and Safari. The observer-only fixture has no entities, so its empty
canvas is expected. He also confirmed standalone HTML playback and visible motion in
`~/Downloads/simulatte-sp3/fleet-demo.html`, supplied a screenshot, and subsequently
reported successful MP4 output during the Safari manual acceptance discussion.
These are accepted manual results; WebDriver automation is not required to count
them as evidence. Inspection of that trace confirms two AGVs following identical
routes/timing: their glyphs and labels overlap. This is a presentation limitation,
not evidence that the second vehicle was omitted. Better demo staging and clearer
co-location indicators are follow-up polish, not completed work.

Actual Safari's automation setting was observed enabled. Two WebDriver session
attempts timed out connecting to Safari, before any application test ran. This
supersedes the earlier user-requested deferral; no browser gate has been waived.

Davide subsequently confirmed “all checks pass” after the explicit checklist:

- Cancel a running export, retain a responsive viewer and start another export.
- Download an export, remove its temporary copy without error, and open the download.
- Disable networking, reload the standalone fleet HTML, seek forward/backward and
  resume playback.

Together with his prior MP4 output and normal/large HTML playback confirmations,
this closes the remaining actual-Safari acceptance checklist. These results are
user-reported manual evidence on eva, not instrumented Safari measurements. No
new Safari frame-count, memory or zero-request telemetry is claimed.

SP3 is complete. Overlapping-label polish and WebDriver troubleshooting remain
optional follow-ups. Maintainer commit/PR/merge/release actions are separate;
none has been performed.

## Reproduction and durable measurements

`studio/bench/README.md` documents the local browser harness, renderer fixture,
near-cap export commands, measurement methods and independent artifact checks.
Small machine-readable results are retained in
[`sp3-acceptance-data/`](sp3-acceptance-data/). Large generated recordings and media
are intentionally not added to the repository. The benchmark JSON keys
`coldSeekP50`/`coldSeekP95` mean the first-seek method defined above, not fresh
browser processes.
