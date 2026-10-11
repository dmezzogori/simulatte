> Historical preview milestone. Superseded by [implementation acceptance evidence](2026-10-11-sp3-acceptance.md).

# SP3 development preview evidence — 2026-10-11

This is an implementation milestone, not the SP3 acceptance report. Work is
uncommitted on `feature/sp3-viewer`, based on main `632923d` / release 0.14.0.
Davide approved revision 2 and D66–D69 before implementation.

## Implemented in this milestone

- Python writes container 1.2. KPI declarations retain their original eleven
  fields; display rules are separate. Observer records have independent framing,
  phase, bounds and catalog epochs and do not enter domain replay or digest
  verification. Recorder diagnostics bypass the event bus, including when
  multiple recorders are attached.
- Python exposes observer history and series display metadata. Tests cover
  activation partition, late observer catalog entries, reentrant emission,
  multiple-recorder diagnostics, latency publication and malformed records.
- The TypeScript reader accepts byte sources and strict HTTP range snapshots,
  indexes observer/KPI metadata, merges bounded event pages, streams series and
  exposes sequential replay. Prepared-state reconstruction retains its own
  chunk reference across cache eviction. The cache has a conservative encoded
  size estimate (four times decoded wire bytes), a 128 MiB target, four-chunk
  maximum and two simultaneous chunk reads. This is not a measurement of JS heap.
- Playback separates activation, exact event and continuous positions; newer
  requests invalidate stale seek/step results. Sessions retain one active
  preparation and one replacement. Exact KPI values never interpolate from the
  future; continuous interpolation collapses duplicate times to the last sequence.
- The first Pixi scene uses recorded placements, footprint rotation, graph links,
  equipment occupancy, bounded queue glyphs and AGV motion. It supports
  fit/pan/zoom/picking and labels fallback layouts. Motion covers linear,
  trapezoidal/triangular and portable keyframes.
- The React/compiler shell contains dockview scene/inspector/KPI/event panels,
  presentation mode, file loading and imperative playback bindings. uPlot uses
  display data separately from recorded KPI values.
- Offline HTML embeds original trace bytes, a single-script player, dependency
  notices and a hashed script CSP. The 100 MiB source / 150 MiB output limits
  are enforced. Manual export schedules include an exact-end hold and reject
  unsafe timestamps or more than 100,000 frames. Video/ZIP writers are not yet
  implemented.

## Verification

Environment: eva, Apple M1 Pro, macOS 26, CPython 3.14.1, Node 24.13.0,
pnpm 11.5.1, Playwright 1.64.0.

| Check | Observed result |
| --- | --- |
| `uv run --all-extras pytest` | 2,211 passed; 99.20% branch-inclusive coverage |
| `uv run --all-extras ruff check .` | Passed |
| `uv run --all-extras ruff format --check .` | 330 files already formatted |
| `uv run --all-extras ty check` | Passed |
| `uv run --all-extras zensical build` | Passed, no issues |
| TypeScript unit suites | 267 reader/query, 5 playback, 2 motion/projection, 2 export tests passed |
| `pnpm --dir studio typecheck` | All six workspace packages/apps passed |
| `pnpm --dir studio build` | Player and viewer built; Vite reports a large main-bundle warning |
| Released 0.14 Python reader | Isolated `check()` and `verify()` passed on the new 1.2 reference fixture, including late catalogs and observer records |
| Chromium 156 / Playwright WebKit 27.2 | Six tests passed: production CSP viewer, offline file player and format 1.2 KPI/hostile-label checks in both engines |
| Firefox 157 / Playwright | Launch failed before page creation: “Could not find profile folder”; same failure with `TMPDIR=/tmp` |
| Actual Safari | Not tested; Playwright WebKit is not a substitute |

The new fixture lives in `tests/fixtures/traces/viewer/`. Its Python-derived
expected file checks merged observer/domain history, per-cursor replay states,
late catalogs, retirement, duplicate-time samples and missing values. Released
1.0/1.1 fixture bytes were preserved.

A preliminary, single un-warmed full-recording run on the existing 5,000-job,
10-server, utilization 0.90 workload took **3.3874 s**. The trace was 8,163,904
bytes with 37 chunks; the separate memory subprocess reported 141.36 MB peak.
Python cold seek p95 was 59.06 ms; warm seek p95 was 9.05 ms. This is exploratory
measurement only, with no comparative baseline or repeated noise estimate. It
is not a pass of the SP3 50,000-job reader, scene or recording-overhead gates.

## Remaining work

The full exit criteria of plan tasks 1–10 have not been met. In particular:

- Implement/test MP4/H.264, WebM and PNG ZIP64 sinks, OPFS/direct-file lifecycle,
  cancellation, quota failures, decoded output parity and near-100,000-frame
  memory/finalization experiments. Mediabunny and zip.js are pinned but unused.
- Implement the Python CLI, authenticated loopback server, bundled assets,
  source-fingerprinted build hook, wheel/sdist policy and installed-wheel smoke.
- Complete 50,000-job seek/open/heap/long-task/cancellation measurements,
  congested queues, 1,000 moving glyphs, observer-heavy recording benchmarks and
  near-limit HTML memory checks. Review metadata/index and sample working sets.
- Finish decorative transfers, ports/direction/status details and diagnostics,
  renderer-extension API, deterministic direct-seek scene parity and GPU checks.
- Complete inspector field paging/history cancellation, changed-entity/type/time
  controls, series selection, chart gap/extrema downsampling guarantees,
  preference persistence, structural-update throttling and React commit-count
  measurements. Current inspector limits the field count but does not page
  nested collection values. Retired-state retrieval is a first implementation.
- Resolve the local Firefox launch failure and test actual Safari. Add browser
  and installed-package CI gates, complete independent implementation review,
  and finish the acceptance matrix before any release or merge request.

## Primary documentation consulted

- [React compiler installation](https://react.dev/learn/react-compiler/installation)
  and local pinned plugin declarations for the Vite 5.x plugin's Babel option.
- [Pixi application](https://pixijs.com/8.x/guides/components/application) and local
  `pixi.js/unsafe-eval` source for manual ticking and CSP-safe code generation.
- [dockview](https://github.com/dockview/dockview), pinned package README/types;
  its current core API supplies imperative panel renderers.
- [uPlot](https://github.com/leeoniya/uPlot), pinned declarations for data/cursor
  updates.
- [Mediabunny writing media](https://mediabunny.dev/guide/writing-media-files),
  [media sources](https://mediabunny.dev/guide/media-sources), and
  [zip.js writer options](https://gildas-lormeau.github.io/zip.js/api/interfaces/ZipWriterConstructorOptions.html)
  were inspected for future sink work; feasibility has not yet been established.
