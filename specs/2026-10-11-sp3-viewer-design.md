# SP3: trace viewer

- **Status:** implementation and acceptance complete; [acceptance evidence](research/2026-10-11-sp3-acceptance.md) records automated checks and Davide’s passing manual actual-Safari checks. Approved design revision 2, 2026-10-11, corrected after [review 1](reviews/2026-10-11-sp3-review-1.md) and [review 2](reviews/2026-10-11-sp3-review-2.md). Davide approved revision 2, including A1–A3, libraries, limits and OPFS retention, on 2026-10-11 (“Approved”). Approval records decisions, not implementation evidence.
- **Release:** 0.15, following released 0.14.0 (base commit `632923d`).
- **Authority:** [global design](2026-10-08-studio-global-design.md), especially C1, C2, C7 and §§6–9; [decisions](studio-decisions.md), especially D9, D13–D14, D16, D35, D63.
- **Plan:** [implementation plan](../plans/2026-10-11-sp3-viewer.md).

## 1. Outcome and scope

`simulatte view run.simtrace` opens a read-only, local player. A recorded production shop or AGV fleet can be animated, inspected at an exact event cursor, and shared as offline HTML or video without executing model code. Client presentation comes first, visual debugging second, statistics third.

SP3 includes the existing TypeScript reader's completion, framework-independent PixiJS scene and playback runtime, React shell, Python static server/CLI, exports, packaging, documentation and browser evidence. It excludes model loading/execution, parameter editing, layout editing/re-resolution, watching, websocket streaming, run stores, experiment comparison and remote workers. Those remain SP4/SP5. The scene/clock interfaces allow later streaming and comparison without implementing them now.

## 2. Shipped foundation and gaps

Inspected at `632923d`, rather than inferred from the roadmap:

| Foundation | SP3 work |
|---|---|
| `studio/packages/trace`: strict wire decoding, CRC/decompression limits, chunk cache, `prepare`/`stateAt`, layout-v1 validation | Public event/catalog/prelude/KPI/manifest access, bounded indexed queries and sequential replay |
| Trace 1.0/1.1, layout schema 1.0; Python writer and two readers | Preserve old traces; add only the reviewed optional extensions below |
| Resolved layout contains placements, network, topology, aliases and diagnostics | Consume the resolved result; do not reimplement Python layout resolution |
| Recorded AGV motion has endpoint ids, start/end, constant/linear/trapezoidal descriptions | Pure interpolation and validation; keyframe convention below |
| Python KPI records contain samples `[seq,t,key,value]`, scalars and declarations | Display them without deriving simulation KPIs in JavaScript |
| Full writer subscribes to `"*"` (domain only); KPI samples have a separate subscription | Address the inspect-only observer gap explicitly (A2) |
| `KPI` has estimand metadata but no interpolation or missing-value fields | Add declared display semantics (A1) |
| No CLI, studio extra/server, frontend assets or browser harness | Introduce minimal serving and build/package checks |

`Trace.stateAt` currently copies a chunk snapshot and replays its prefix on every call. It is a correctness oracle, not the intended 60 Hz path. The existing four-chunk cache bounds count, not decoded bytes; the viewer needs an explicit working-set budget.

## 3. Decisions and proposed amendments

The approved global contracts remain authoritative. A1–A3 were accepted on 2026-10-11; global/SP1 contracts and decisions D66–D69 record them.

### A1. KPI display semantics and optional trace extension

Add Python `KPI` keyword fields `interpolation: Literal["previous", "linear", "none"] = "previous"` and `missing: Literal["gap", "hold"] = "gap"`. Keep existing `kpi-declarations-v1` records at their exact existing field set: both current readers validate it strictly. Publish the new fields separately in `KPI.series_display`, a map from the existing scoped key to `{interpolation, missing}`, under optional feature `kpi-display-v1`. Each key is declared once, alongside or after its KPI declaration and before its first sample where possible; late attachment retains the current publication rules. Writers emit container **1.2**; major 1, layout 1.0 and layout-v1 remain unchanged. Old readers ignore the extra KPI member. New readers support 1.0 and 1.1.

Older series without display metadata use documented compatibility rules: previous sample, gaps for non-finite samples, no value before the first sample. Mark these as assumed display rules in metadata. No estimand is inferred from a series name. Existing `dataclasses.asdict(kpi)` writer code must explicitly split the old declaration fields from the new display fields.

This refines C1.8's already-required declared interpolation and missing-value rules; it does not alter Python KPI arithmetic. Verify identical numeric values/digests with and without the extension.

### A2. Full traces include inspect-only observer events

Record observer events through a separate optional **OBSERVERS record (type 9)** under feature `observer-events-v1`. Keep domain CHUNK/PRELUDE records, their index and the footer's domain cursor semantics unchanged. This matters: the released Python verifier digests every CHUNK event and would falsely reject observer-containing chunks. Older readers skip type 9 and must still pass `check()`/`verify()` on new traces; decoding alone is not sufficient compatibility evidence.

An OBSERVERS payload is an uncompressed wire map `{phase, first, last, epoch, events}`. Phase is `prelude` or `simulation`; each event uses `[seq, null, type, t, payload, []]`. First/last are its actual event cursors; epoch identifies catalog definitions already published before that record. Require nonempty, ordered events, matching bounds, null ordinal, empty deltas and catalog category `observer`. Never apply these records to state or digest. Close buffers on the existing count/byte/latency limits; apply the same queue/backpressure/record-size safeguards as other records. A complete CRC-valid record commits itself, like KPI records; an incomplete trailing record is ignored under existing tail rules. Corruption before later valid records is an error. Metadata scanning indexes offsets/bounds/phase/epoch without retaining all observer payloads.

The full recorder receives `"**"` through a dispatcher: domain events use the existing domain path; observer events use their own encoder and never access `DomainEvent.ordinal` or its digest-tail optimization. Include logs, custom observers and `kpi.sample`. The dedicated KPI stream remains authoritative for values; the event log merges domain chunks and OBSERVERS by `(t,seq)` and never duplicates sample entries from KPI records. Kpi-level recording is unchanged. Flush prelude observer buffers before INITIAL and mark subsequent observer records `simulation`; preparation events are inspectable separately and never stepped/replayed. Full history bounds include every committed simulation observer event even when its seq exceeds the legacy domain footer cursor. Replay state at an observer position is the state after the latest eligible domain cursor, or INITIAL if none; an observer never creates a new state transition. New adjacency/page APIs use merged history bounds while legacy `stateAt`/`cursorRange` retain their domain contract. Without the optional feature, label history **domain-event history only**.

Recorder-internal warnings/errors are delivered through a dedicated recorder diagnostic channel, not `env.warning`/the recorded event bus. Existing configured log sinks may render them through a direct diagnostic method, but no recorder re-observes them and no observer derived-event chain starts from them. Do not exclude logs by the user-controlled component string. This coverage exception prevents recursion when an oversized-event warning itself exceeds the limit and applies across multiple recorders. Record end-of-run diagnostic summaries outside semantic data if needed. Test tiny `max_event_bytes`, oversize logs, multiple recorders, backpressure and close/failure paths.

This closes C1.2/C7.3's inspect-only gap without changing domain projection/ordinal or old-reader verification. Tests cover reentrant derived events, catalog growth, activation partition, merged end bounds, truncation, observer-only runs and replay equality. Remeasure full-recording cost/file size against existing budgets; a failure requires an explicit decision, never a silent budget increase.

### A3. Portable keyframe convention

Complete C1.2's keyframe option with a data-only motion description:
`{curve: "keyframes", keyframes: [[elapsed, fraction], ...], accuracy: meters}`.
Elapsed time is measured from `t_start` in simulation units; fraction is progress along the recorded endpoint segment. Require finite, strictly increasing elapsed values, monotone fractions in [0,1], first `[0,0]`, last fraction 1, at least two points, nonnegative finite accuracy, and a finite positive segment duration. The final keyframe elapsed time is the profile duration. Its agreement with recorded `t_end-t_start` is checked within eight float64 ULPs of the larger absolute endpoint time (minimum absolute tolerance 1e-12 simulation units), accounting for endpoint addition/subtraction rounding; map elapsed proportionally to the recorded duration within that tolerance. Larger disagreement is invalid/approximate, not silently retimed. Interpolate linearly between adjacent keyframes. This defines a new optional description variant inside the existing motion map, not a change to motion timing, topology or trace framing. Curved spatial paths remain sequences of graph segments. Unknown curves fall back to linear with an explicit approximate diagnostic.

### Other accepted SP3 decisions

| Choice | Proposal and reason |
|---|---|
| Scene/UI | PixiJS 8 WebGL2; React 19 with React Compiler; Vite. These implement D13/D14, not reopened framework choices. Pin actual versions in the lockfile during implementation. |
| Charts | uPlot, updated through its imperative API; one scoped series per chart initially, compatible units may be overlaid. Canvas plotting matches clock-bound updates. |
| Docking | dockview-react, a default Scene / Inspector / KPIs / Events layout, reset-layout action. Disable popout windows in SP3 to retain one document and clock. |
| Video muxing | Mediabunny, ordinary MP4 with `fastStart: false`, position-aware streaming targets and explicit codec probing. Maximum 100,000 video frames (PNG reduced to 10,000 by D70 after measurement); metadata is bounded by this limit, not constant in frame count. No in-memory whole-video target. Include dependency notices and exact source/version references in distributed assets. |
| PNG archive | zip.js with streaming ZIP64, sequential uncompressed entries and no worker pool. Validate the pinned version's buffering and central-directory behavior in Task 1. |
| Icons | Bundled small vector equipment glyphs drawn with Pixi primitives; a small bundled UI icon set, with licenses. No external URLs, arbitrary SVG, user scripts or remotely loaded fonts. |
| Offline HTML | Warn at 25 MiB source trace; refuse above 100 MiB source trace or 150 MiB final HTML. These are product limits to validate, not measured browser limits. Offer local view/video when exceeded. |
| Visual direction | Approved by Davide on 2026-10-11: light background, clear equipment footprints, muted status colors, presentation mode hiding inspector/charts. |

Primary documentation checked on 2026-10-11: [Pixi application](https://pixijs.com/8.x/guides/components/application), [React Compiler installation](https://react.dev/learn/react-compiler/installation), [uPlot](https://github.com/leeoniya/uPlot), [dockview React quickstart](https://dockview.dev/docs/overview/quickstart/?framework=react), [Mediabunny output targets](https://mediabunny.dev/guide/writing-media-files), [StreamTarget](https://mediabunny.dev/api/StreamTarget), [codec probing](https://developer.mozilla.org/en-US/docs/Web/API/VideoEncoder/isConfigSupported_static), [OPFS](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system). These establish available APIs; they do not prove export support on a particular browser/device. Mediabunny currently declares MPL-2.0 in its [package metadata](https://github.com/Vanilagy/mediabunny/blob/main/package.json); do not describe the whole dependency bundle as MIT.

Additional review sources: [Pixi CSP-safe extension](https://github.com/pixijs/pixijs/blob/v8.14.3/src/unsafe-eval/init.ts), [MP4 output modes](https://mediabunny.dev/guide/output-formats), [zip.js streaming/ZIP64](https://gildas-lormeau.github.io/zip.js/), and [ZIP writer options](https://gildas-lormeau.github.io/zip.js/api/interfaces/ZipWriterConstructorOptions.html). Recheck these against the versions pinned by Task 1.

## 4. Package boundaries

| Path | Responsibility |
|---|---|
| `studio/packages/trace` | Wire/container validation, sources, catalogs, replay, event and KPI queries; no React/Pixi/DOM dependency |
| `studio/packages/playback` | Cursor resolution, one clock, prepared frames, incremental state, stale-request fencing; no React/Pixi dependency |
| `studio/packages/scene` | Pure view projection and motion math; Pixi scene lifecycle, picking and camera; no React dependency |
| `studio/packages/export` | Manual frame driver, codecs/muxer/sinks, offline HTML assembly; no React dependency |
| `studio/apps/viewer` | React/dockview shell, imperative chart/inspector adapters, accessible controls |
| `studio/apps/player` | Minimal offline entrypoint reusing trace/playback/scene; controls without docking/exporter |
| `src/simulatte/cli.py`, `__main__.py` | Stdlib CLI dispatch and optional-extra error messages |
| `src/simulatte/studio` | Loopback static viewer app and prebuilt assets |

The standalone player is bundled as one script with no runtime imports or worker URL dependencies. The normal viewer may split modules. No second simulation-state implementation is permitted: scene adapters read generic replay state, never reduce event types themselves.

Both renderer entrypoints load Pixi's CSP-safe `pixi.js/unsafe-eval` extension before initialization. Despite its name, that extension replaces dynamic function generation so the application can keep eval forbidden. Preserve its side effects in the bundle. Task 1 must render labels, equipment and the chosen batching path under the exact production CSP, and performance measurements must use that same path.

## 5. Trace access and memory

Keep `openTrace(Blob | ArrayBuffer)` and current `prepare`/`stateAt` behavior. Add an exported random-access source interface with `size`, `read(offset,length,signal?)` and explicit lifetime/identity. Blob sources use slices; HTTP sources request byte ranges. The viewer opens a browser-selected file locally without upload. The served trace has an opaque route, not a user-supplied filesystem path.

Expose immutable, typed views for:

- requested manifest (HEADER merged with INITIAL), final manifest separately, fingerprint, recording level, activation cursor, outcome, truncation and observable time range;
- catalog by epoch, including definitions introduced between chunks; lazy prelude records in a separate preparation log;
- bounded forward/backward event pages and nearest previous/next recorded event cursor across chunks; filter queries may scan but are cancellable and report progress;
- KPI declarations/display metadata, scalar records and sample pages, ordered by `(t,seq)`, with scoped keys preserved;
- resolved layout bundle (opaque for unsupported schema versions) and structured layout diagnostics.

Index framing/KPI locations/catalog epochs in a cancellable metadata pass; do not inflate all chunks at open. Never retain every decoded event/sample during indexing. Existing readers' corruption/truncation rules stay intact, including verifying committed chunk references and ignoring only an incomplete trailing record/chunk. HTTP requests validate 206/Content-Range and file identity; a changed file invalidates the reader with a reopen message. SP3 uses a fixed file snapshot, not polling a growing file. A missing footer means **unfinished or truncated**, never confidently “running.” No INITIAL means metadata/prelude only, no animation.

Measure longest main-thread task and cancellation latency during metadata scanning, cold decode and congested replay, not only seek/frame percentiles. The shipped parser's maximum chunk may cause a long synchronous task. Task 1/3 must choose a worker-backed preparation path for the served viewer or bounded cooperative decode/replay if the provisional 50 ms long-task / 200 ms cancellation targets fail. Offline mode must preserve its no-runtime-worker-URL promise; a separately bundled/inlined worker and corresponding CSP would need an explicit spec revision, otherwise use a cooperative path or a measured smaller supported working envelope. A cancellation flag does not interrupt an already-running synchronous decode.

Working-set defaults: 128 MiB estimated decoded chunk/sample cache, at most four chunks, at most two concurrent chunk reads, and one preparation request plus its replacement. A single valid record/chunk that exceeds this cache budget is processed alone up to the existing parser limits; display the oversized-working-set diagnostic. Parsed object memory is measured separately because encoded-byte accounting is only an estimate. Arrays/metadata outside this cache need their own bounds; do not claim constant memory for arbitrary indexes. Event/inspector pages cap at 200 items, retained search pages at 1,000; chart display data cap at 4,000 points per series and eight active series. Downsampling is display-only, preserves gaps and extrema, and never feeds KPI values or exports of numeric data.

Sequential replay uses an owned state plus an event position; it applies each delta once while moving forward. A backwards seek or jump outside the prepared range rebuilds from the appropriate snapshot. Preparing a frame returns a stable, immutable view or an exclusively owned frame that cannot be changed by a later preparation. Pin prepared chunk/state dependencies through render, independent of cache eviction. Keep `stateAt` as the reference oracle in replay equivalence tests.

## 6. Time, cursors and preparation

The playback position contains both `time` and the latest applied event `cursor`. Between events, time advances continuously for motion while state stays at the preceding event. At a normal time seek, apply all recorded events at or before the target time, including all seq values at that time. Exact event stepping instead uses precisely the selected `(t,seq)` and displays its post-event state, even among same-time events. Do not pass a synthetic infinite seq into integer-only cursor APIs; resolve the last eligible real cursor first. Activation is a separate initial position, before ordinary time-zero events.

Play after an exact step consumes remaining same-time events in order before advancing time. Previous/next step covers recorded simulation events, including empty-delta events; prelude is inspected separately and never stepped as simulation. “Previous” from activation and “next” at the end are disabled. KPI-only files allow time/KPI inspection but no entity animation or event stepping, and explicitly say so; their timeline comes from recorded sample/footer information, not the current full-only `cursorRange` behavior.

Valid wire times may be infinite. The timeline/interactive clock use only the finite recorded range (finite activation/events/samples/footer times); no finite range means no play/seek slider. A finite prefix with an infinite footer ends automatic playback at its last finite time and shows an unbounded-time diagnostic; it never scales a slider against infinity. Exact stepping to a non-finite event remains available as a **static inspection position**: show the actual ±∞ cursor and post-event state, suppress motion interpolation/trails, and disable play until a finite position is selected. Previous/next still follows event order. Non-finite times are not corruption or silently clamped; event/prelude inspection retains them. Export requires a finite interval contained in the available finite range. Test infinite activation/footer/event, only an initial finite state, and finite-prefix/infinite-tail traces.

Only `PlaybackClock` advances time. Interactive mode owns one requestAnimationFrame loop. Manual mode accepts explicit frame positions and registers no ticker. Pixi auto-start/shared tickers are disabled. Hidden tabs pause advancement and reset the wall-clock anchor on return, avoiding a catch-up jump. Playback rate is finite positive simulated units per wall-clock second, labelled using manifest `time_unit` (or “simulation units”).

Preparation is asynchronous; commit/render is synchronous. Every seek/load has a monotonically increasing generation. A late result can warm its source cache but cannot replace state, selected entity, chart cursor or time from a newer generation. Pause/seek/unload invalidates pending advancement. If data is not ready, freeze the displayed time/state together and show buffering; resume from a fresh wall-clock anchor. Do not drop state changes to catch up. Cancel, decode failure, source replacement and disposal have explicit transitions; errors retain the last valid frame with a visible message.

Render uses `PreparedFrame { time, cursor, state, layout, catalog, kpis, diagnostics }` plus camera/theme. Time and cursor must be consistent with the prepared interval. A deterministic pure projection maps this to entity glyphs. Comparison can later prepare two sessions then commit both to one manual clock; SP3 does not implement comparison UI.

## 7. Scene and interaction

World coordinates remain `(x,y,z)` meters; map y-up to screen y-down at the camera transform. Respect footprint width/depth and rotation about z; labels remain upright. Fit camera, pan, wheel/trackpad zoom about pointer, select, clear selection, and focus selected entity are available through buttons and keyboard as well as pointer gestures. One resize observer updates the canvas; destruction releases textures, listeners, subscriptions and pending work.

Layers: background/grid; network and direction arrows; placed equipment/ports; jobs/queues and AGVs; selection/status; labels; decorative trails. Geometry is batched and reused between frames, visibility is culled, and labels/queue glyphs reduce at low zoom. Queue summaries are bounded (first 20 visible jobs plus count); inspection retains exact recorded queue order. Status is expressed by text/shape as well as color. No metric such as utilization is inferred from a fill color and presented as a KPI.

Built-in adapters:

| Entity | Recorded source and presentation |
|---|---|
| Server | footprint; `users`, `queue`, `capacity`; occupancy/status and bounded queue markers |
| PSP | footprint and ordered `jobs` |
| Job | `location`, routing/op index; destination position at transfer cursor, never a fictitious in-flight state |
| Warehouse | footprint/ports; recorded per-SKU inventory and slots |
| AGV | active `motion`, else recorded `node`; state/load/battery badges |
| Node, charging, parking | resolved geometry and recorded state; node details normally hidden until selected |
| Shopfloor, fleet, router, orders, unknown kinds | inspector entries; generic labelled glyph when placeable, otherwise a deterministic unplaced tray |

Job location vocabulary is the shipped one: `psp:<id>`, `queue:<id>`, `server:<id>`, `transit`, `done`, or null. Resolve only the first three to physical equipment. A `transit` state is shown in the unplaced/in-transfer tray, `done` in completion status until retirement, and null as unlocated. Release and destination-arrival can be separate same-time cursors; do not anticipate a later destination. There is no assumed `job.transferred` event. A decorative transfer begins only when a location delta establishes a new physical destination; queue-to-service within one equipment is not an inter-equipment trail. Use the last recorded physical location as its source when available. If a direct seek lacks that bounded historical context, omit the trail; never infer future state. Test PSP release → transit → queue → grant at equal times and across chunks.

Use only stored resolved layout geometry. For traces without layout, node entity x/y positions remain usable; other static entities go in a deterministic id-sorted grid labelled **viewer fallback layout**. This is presentation-only and is not a port of SP2's physical resolver. Unknown layout versions retain metadata/inspection and use the same fallback with a warning. New transient entities do not rearrange static equipment. A missing placement/reference puts the entity in the unplaced tray rather than at invented physical coordinates.

Known visual shape/icon names use a closed bundled registry; unknown names use the generic glyph. Treat colors as validated color values and every label as text. Never interpret trace fields as markup, CSS rules, URL assets or executable plugins. A consumer may register trusted renderer code through the scene API; a trace may not.

### Motion

Position derives from the current replay state's motion, not the last event the UI happened to see. Resolve endpoint ids against recorded graph/node coordinates. Clamp elapsed time/progress. Constant/linear descriptions use elapsed/duration; trapezoidal uses the recorded distance/effective speed, acceleration and deceleration, with triangular peak speed when necessary. Compute travelled-distance fraction, then interpolate endpoint coordinates. Keyframes follow A3. Validate finite parameters, nonnegative distance and positive finite moving-profile rates before evaluation. A stalled/infinite-end motion stays at the source, visibly stalled. A zero-duration segment is at its destination after the corresponding transition. Interruption/reroute clears/supersedes the plan: render the state actually recorded, including jumps back to a node. No smoothing across a discontinuity. Invalid/unknown descriptions use an approximate linear fallback only when finite timing/endpoints make it meaningful; otherwise show unlocated/stalled diagnostics.

### Decorative transfers

At a zero-duration job transfer, the selectable job is already at its recorded destination. The source-to-destination trail is dashed, nonselectable and labelled decoration in the legend. Interactive trails last 200 ms of active playback wall time and are cleared on seeks. Export trails last six frames, derived from event crossings and frame index; reproducible manual export never reads wall time. Trails are bounded to 256 and never affect state, KPI panels or picking. Same-time event stepping may show the selected transfer's trail without inventing elapsed simulation time.

## 8. Shell, inspection and KPI panels

Default layout: scene dominant; inspector on the right; KPIs/events in lower tabs; global timeline below. Presentation mode hides docking chrome, inspector and charts, leaving the scene and compact controls that can auto-hide. Export defaults to this clean scene with time/legend overlays, not a screenshot of the DOM. Persist only viewer preferences/layout locally, never trace bytes or filesystem paths.

React handles discrete selection, pause/play, trace load, seek completion, panel configuration and errors. Timeline text/thumb, clock-bound inspector values, KPI values and chart cursors are imperative ref-owned updates. A normal 10-second playback interval must not trigger React commits proportional to frame count. Charts may refresh at 10 Hz and numeric inspector fields at 15 Hz; scene/timeline use the animation frame. Tests measure updates and final values, not just the presence of memoization.

Pause, exact step, buffering entry, seek completion and end-of-run synchronously flush every visible clock-bound adapter to the committed frame. Imperative adapters also own bounded structural row/status updates (field changes, queues, creation/retirement), so these do not force React commits for each simulation event. Selection by the user remains discrete React state. Test immediate pause after retirement/creation and sparse sample updates.

Inspector shows kind/id/label, exact cursor/time, paged state fields, motion diagnostics and catalog information. Selected retired entities remain selected as **retired at this cursor**; a cancellable historical query retrieves the last pre-retirement state labelled with its own cursor. Never insert retired state into the live scene. Event log supports time/type and **changed entity** (delta target id) filters and paged payload/delta inspection, including unknown types and empty deltas. The shipped catalog has no entity-reference field semantics, so SP3 does not promise a complete related-entity filter for empty-delta/custom events. Offer clearly separate payload text search, not inferred references. A future catalog extension can add richer filtering. Preparation log is a separate tab.

KPI identity is the full scoped key. Display unit, estimand/cohort/window/censoring metadata when present; distinguish absent metadata from an empty result. At exact event cursors, samples with a greater `(t,seq)` are not visible. At continuous time positions, use all samples up to that time. Duplicate-time samples are ordered by seq. Final scalars are a separate **End-of-run results** section including outcome, never substituted for playback values.

Exact-event tiles never look ahead: `previous` and `linear` show the most recent eligible recorded sample, labelled recorded (with its timestamp); `none` shows only an eligible sample at the cursor's time, including one earlier in seq at that same time. Select the greatest eligible seq among duplicate-time samples. At activation, no simulation sample is eligible. In continuous-time mode, collapse duplicate times to the greatest seq: `previous` holds the latest sample, `none` displays only at a sample time, and `linear` interpolates between adjacent distinct-time brackets with an explicit **interpolated** label. A right bracket can lie in the future only in this labelled continuous mode, never in exact-event inspection. No extrapolation before the first sample; after the last, previous holds while continuous linear/none return no value except at the final sample time.

With `gap`, a non-finite sample breaks the value/curve until a later finite sample; linear never bridges a non-finite bracket. With `hold`, substitute the most recent preceding finite value at a missing sample's timestamp (no substitute before the first finite sample), then apply the selected interpolation rule. Non-finite final scalars display `NaN`, `+∞` or `−∞`, not zero. Charts may draw the full series independently of tile eligibility; downsampling preserves gaps.

| Example (linear unless stated) | Exact event | Continuous time |
|---|---|---|
| Samples `(0,1)=0`, `(10,20)=10`, inspect time 5 | Recorded 0 | Interpolated 5 |
| Time 10 samples seq 20=10 and seq 22=12; cursor `(10,21)` | Recorded 10 | At time 10: recorded 12 |
| Before first sample | Missing | Missing |
| Latest eligible sample is non-finite, gap | Missing | Missing across that gap |
| Same data, hold, earlier finite value 4 | Recorded 4 | Use held 4 as bracket, label interpolation if between times |
| Time 11 after last sample at 10 | Latest recorded value | Linear/none missing; previous holds |
| None, sample `(10,20)=10`, event `(10,21)` | Recorded 10 | At time 10: last duplicate-time value |

Keyboard controls have visible labels/help, avoid taking shortcuts while text fields are focused, and work without canvas picking through the entity list. Playback buttons expose accessible names, focus states and disabled states. Respect reduced-motion preferences for decorative UI/trails; simulated motion remains explicit playback.

## 9. Video and PNG export

An export gets its own playback session/manual clock and scene. Interactive session position/camera is unchanged. User chooses finite start/end times, playback rate, 24/30/60 fps, resolution (default 1920×1080, even dimensions for H.264), background and overlays. No audio in SP3. Reject invalid or excessive canvas dimensions through capability checks before allocating; default limit 3840×2160.

Define `N = ceil((end-start)*fps/rate) + 1`, including the exact-end frame; a zero-length interval has N=1. Require finite arithmetic, safe integer N in [1,100000], and finite strictly increasing simulation frame times when N>1 before opening a destination. Frames before the final one sample `start + i*rate/fps`; the last samples exactly end. Every frame has a nominal 1/fps duration, so the exact-end frame is held for one frame and total video duration is N/fps, including rounding to the frame grid. Timestamp i is `round(i*1000000/fps)` microseconds; duration is the difference to timestamp i+1. Validate safe, strictly increasing integer timestamps. Reject a rate/interval below float precision rather than exporting duplicate times. Use this same schedule and terminal hold in the PNG timing manifest and decoded-video tests.

Probe `VideoEncoder.isConfigSupported` with the actual dimensions/framerate/codec configuration. Prefer MP4/H.264, then WebM/VP9, then WebM/VP8, then PNG frames. Codec availability is independent of sink availability and file:// player support. Offer only tested combinations; unsupported configs show the concrete reason and available fallback. Offline HTML is a read-only player and need not host video export.

Use streaming muxer output with backpressure and ordinary MP4 `fastStart: false` (not whole-file buffering or unverified fragmentation). Allow at most four outstanding encoded-frame submissions; close every VideoFrame after submission/settlement as required by the API. Apply positioned writes in order, including overwrites; the OPFS worker acknowledges each write before the producer exceeds a two-message/8 MiB transport queue. Split large writes without losing offsets. Await source backpressure as well as sink writes, flush/finalize before success, and never collect all media chunks/frames in an array. The 100,000-frame cap bounds per-frame muxer/index metadata; Task 1 must measure and set a metadata/finalization allowance within a provisional 256 MiB export working-set budget (excluding the already-loaded trace and driver/GPU allocations). Lower the cap or revise the proposal if that cannot be met. Browser-total memory is reported separately. Test near-cap exports and inspect pinned muxer internals; a short feasibility clip is insufficient.

Prefer direct File System Access writes after a user gesture. Otherwise write to a unique OPFS file (worker sync handle or supported writable API), close it, obtain a File, and offer a blob-URL download without converting it to an ArrayBuffer. Show completed staged exports with Download and Remove temporary copy actions; do not equate a click with download completion. Keep the file/URL until explicit removal or session shutdown after browser-tested handoff. Do not remove during an active export. Current-origin startup may offer removal of abandoned viewer-owned staging files, but **a new random port cannot clean another origin's OPFS**. Normal shutdown makes best-effort cleanup; crashes may leave data. Document site-data cleanup for the old 127.0.0.1:port origin and the limitation that a later service reusing that origin can access residual files. This is an explicit proposed retention/privacy limitation of the globally required OPFS path, not a cross-port cleanup guarantee. Test handoff/removal races, repeated exports and restart on another port. Storage estimates are advisory; catch quota/write failures at every stage. Stop and clean incomplete artifacts on cancellation or failure. A user-chosen partial file is reported as incomplete if it cannot be removed safely.

PNG fallback is a sequence with numbered frame names and a compact JSON timing manifest in one streaming ZIP64 archive. Use zip.js with sequential uncompressed PNG entries, no parallel buffering/workers, and at most one frame PNG retained. The manifest stores the schedule formula/parameters and final-frame rule, not an unbounded per-frame object list. Maximum 10,000 frames plus one manifest bounds central-directory metadata (D70, lowered under the provisional-budget rule above); include it in the working-set measurement. Test more than 65,535 entries and offsets beyond 4 GiB with generated/sparse data and an independent extractor. It uses the same direct-file/OPFS sinks. It is a codec fallback, not a promise to export without disk APIs: when neither sink exists, allow a deliberately bounded small download (maximum 64 MiB output), checked incrementally; otherwise refuse with a local-browser alternative. No unbounded memory fallback.

Cancellation stops further preparation/encoding, drains or aborts outstanding writes, closes encoders/handles and removes owned partial OPFS files. Inject quota failure, codec error, frame preparation failure, late cancellation and finalization failure. Memory acceptance is measured from the supported browser, not inferred from a queue setting.

Chromium and actual Safari MP4 exports are release requirements. Playwright WebKit tests the WebKit path but is not evidence that Safari's platform H.264 encoder works. Record browser version, OS, device, codec config, sink, output size, decoded frame count/timestamps, peak memory method and cancellation outcome. Test current Firefox too; document the observed codec/sink fallback rather than promising MP4. If Safari fails the required sink/codec path, SP3 is not complete until fixed or D35 is explicitly amended.

## 10. Offline HTML

Embed the original trace as base64 data, a versioned player bundle, CSS and bundled assets in one HTML file. Data is never executable: base64 contains no raw trace strings; decoded labels enter text nodes/canvas only. Compute exact export size before final download and enforce §3 limits, including base64 expansion. Do not inline a decoded JSON trace, strip metadata or silently remove events.

Use a build-generated hash-based CSP for the trusted inline script/style; `default-src 'none'`, `connect-src 'none'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'none'`, only required `data:` image/font sources, and no inline event handlers/eval. No CDN, dynamic imports, remote assets, service worker or fetch-based data loading. Bundle licenses. The player reads embedded bytes through the same decoder and offers play/pause, rate, seek/step, fit/zoom, selection and basic metadata/KPI values. No dock manager, server API, editable controls or export codecs in this bundle.

Test a saved file opened with file:// and networking disabled in Chromium, Firefox and WebKit, plus actual Safari before release. Check zero network attempts, adversarial labels including closing-script strings, Unicode, non-finite values, 1.0/1.1 compatibility, maximum-size fixtures and frame/state equivalence with local view. HTML limits are accepted only after these measurements; failing the proposed limit lowers it explicitly with a documented decision.

## 11. Local server and CLI

`simulatte view TRACE [--port PORT] [--no-browser]` and `python -m simulatte view TRACE`. Default port 0; bind only `127.0.0.1`. Validate a readable regular file; never import/execute it. Expose only packaged assets, bootstrap and one configured trace route with HEAD/single-byte-range GET support. No directory serving, uploads, arbitrary path query, model loader or websocket. Reject file replacement/modification during the session and ask to reopen; a static snapshot may be taken if robust identity cannot otherwise be guaranteed.

Minimal `[studio]` adds Starlette and an ASGI server; file watching waits until SP4. Core-only CLI help works without importing either. Missing extras produce the installation command. `all` includes `studio`. CPython is the supported server runtime; core/PyPy behavior remains unchanged.

Apply the global loopback trust boundary to trace confidentiality: exact Host+port validation, no CORS, origin checks when present, one-time CLI bootstrap token exchanged for an instance-specific HttpOnly SameSite=Strict cookie. Assets needed before authentication contain no trace data. Protect trace bytes with an origin-scoped per-instance secret delivered only at bootstrap and supplied as a custom header; cookie alone never discloses it. Bootstrap is a **200 HTML response**, with nonce CSP, no-referrer and `Cache-Control: no-store`; it stores the secret in sessionStorage, verifies the storage operation, then uses `location.replace` to the token-free app. Do not rely on execution of an HTTP 3xx body. Failure gives terminal renewal instructions without revealing credentials. Credential/trace responses are not cached.

Tokens/secrets are redacted from application and access logs; print a token-free address and open the one-time URL directly in browser. `--no-browser` needs a one-time link explicitly printed to the interactive terminal as a credential handoff, not server logging; do not save it in reports. An interactive terminal command `link` prints a fresh single-use link while serving (and invalidates unused links); without a TTY the user restarts with `--no-browser`. Reload works with cookie+sessionStorage; a fresh link supports another tab or recovery after failed browser launch/storage. A wrong/expired token gets a nonrevealing error. SP4 will extend this mechanism to privileged operations.

Static app CSP permits only bundled same-origin scripts/assets and same-origin trace fetches; no external assets, inline event handlers or dynamic execution from traces. Requests from other loopback services with only cookies cannot read the trace. Test DNS-rebinding Hosts, absent/foreign Origin, cross-origin fetch, reused tokens, range errors, encoded traversal, symlinks and multiple instances. Error responses do not leak absolute paths or credentials. Ctrl-C stops the server and closes file handles; failed browser launch leaves the server usable with explicit terminal recovery.

## 12. Builds, tests and release acceptance

Build assets into `src/simulatte/studio/static/` with a build manifest including package version, asset hashes and a sorted fingerprint of relevant frontend inputs/lockfile/build configuration. Wheel and sdist include the assets, manifest and dependency notices. A Python Hatch build hook validates those hashes; checkout release builds also compare the source fingerprint, while sdists have no `studio/` inputs and validate their packaged manifest/assets/version only. Include the hook in the sdist. A hash check alone is not a claim of cryptographic provenance.

Node/pnpm are contributor/release tools only: an sdist-to-wheel build must work with Node absent and network disabled, with Python build requirements pre-provisioned. CI builds frontend assets before assembling the sdist. Editable installs may bootstrap core development without assets; `view` then fails with the explicit frontend build command if assets are missing/stale. Release wheel/sdist builds are strict and refuse missing/stale assets. This preserves the existing `uv sync` setup order. Test clean-checkout editable setup, stale frontend input, tampered asset, isolated wheel install and offline sdist build separately. Existing core import footprint and optional-dependency behavior get smoke tests.

Acceptance is evidence, not a claim implied by this specification:

1. Python/TS fixture parity: exact state for incremental replay vs seek, every chunk boundary, same-time steps, activation, retirement, empty/custom/observer events, catalog growth, 1.0/1.1/1.2, kpi-only, no-layout, invalid/unknown layout and incomplete files.
2. Motion unit tests: constant/trapezoid/triangle/keyframes, endpoints, stalled/infinite/invalid timing, interruptions and direct seek into active motion. Scene snapshots test geometry/state; GPU pixels use tolerant visual checks.
3. UI/browser tests: load/select/seek/step, rapid superseding seeks, disposal, keyboard navigation, presentation mode, imperative update counts, recorded KPI semantics and hostile strings.
4. Export tests: independent manual clock; decoded video timing/count and end frame; MP4 actual Safari/Chromium; Firefox fallback; direct-file and OPFS sinks, quota and cancellation; PNG ZIP bounds; offline HTML under file:// with no network.
5. Python server tests: authentication/ranges/lifetime/security; fresh wheel install and wheel-from-sdist start `view` and browser-load a fixture. Core-only and CPython/PyPy existing checks remain green. No weakening of 99% Python branch-coverage gate.
6. Performance: 10-server 50,000-job full trace, congested queues, and an AGV workload with 1,000 moving glyphs. Measure open/index time, seek p50/p95 (target <200 ms end-to-end on 2021 laptop class), frame p95 (target <16.7 ms for the declared scene workload), memory, cache occupancy and React commits. Include actual model, trace hash, browser/device and warm/cold definitions. Targets are provisional until measured; existing recording budgets are not relaxed.
7. Documentation: quick start, recording for viewing, time/cursor/motion semantics, incomplete/KPI-only traces, export browser matrix/limits, optional install, offline sharing/privacy, contributor build commands and release migration notes.

## 13. Review and next gate

D9 requires two adversarial reviews, corrections, then Davide's go-ahead or another review. Both revision-1 reviews are complete; their findings are addressed below. Both reviewers subsequently verified the revision-2 document corrections in bounded rechecks and reported no unresolved issue within those rechecks (see each report's addendum). This is document verification, not implementation/browser evidence. No production implementation begins merely because the visual direction was approved.

| Finding | Revision-2 disposition |
|---|---|
| Review 1 R1: old verifier hashes observers | Accepted: separate optional OBSERVERS records; domain chunks unchanged; released-reader verify test (§3 A2) |
| Review 1 R2: recursive recorder warnings | Accepted: dedicated non-bus recorder diagnostic path, multiple-recorder/tiny-threshold tests (A2) |
| Review 1 R3: exact KPI lookahead | Accepted: exact/continuous policies and truth table (§8) |
| Review 1 R4: infinite event/footer times | Accepted: finite clock range plus static exact inspection for infinities (§6) |
| Review 1 S1/S2/S3 | Accepted: actual location vocabulary, changed-entity filter limitation, long-task/cancellation evidence (§§5,7,8) |
| Review 2 R2-1: metadata/write bounds | Accepted: universal frame/timestamp limits, MP4 mode, position-aware bounded sink queues and measured metadata budget (§9) |
| Review 2 R2-2: Pixi/CSP | Accepted: CSP-safe extension in both entrypoints and exact-policy feasibility test (§4) |
| Review 2 R2-3: OPFS origins/retention | Accepted: explicit session cleanup/retention, no cross-port recovery promise, residual-storage limitation and tests (§9) |
| Review 2 R2-4: ZIP64 | Accepted: zip.js streaming ZIP64 and >65,535-entry/>4-GiB independent-reader tests (§9) |
| Review 2 R2-5/R2-6/R2-7/R2-8 | Accepted: source/editable/sdist build policy, boundary flushes, executable bootstrap/renewal, exact frame duration (§§8–12) |

Davide approved A1–A3, the libraries/export limits and the explicitly documented OPFS retention limitation on 2026-10-11. Browser/performance targets are implementation feasibility gates, not claimed results. The authoritative global/SP1 documents and decision log have been amended accordingly.
