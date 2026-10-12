# Simulatte Studio: decision log

Decisions for the work described in [`2026-10-08-studio-global-design.md`](2026-10-08-studio-global-design.md). Each entry records what was decided, why, and what was rejected. A later session must not reopen a decision without new evidence; when one is reversed, mark it superseded and add a new entry.

Format: **D<n>. Title** (date, source). Decision. Rationale. Rejected alternatives.

## Brainstorming, 2026-10-08 (Davide with Claude)

**D1. Priority of uses.** Client-facing animation first, visual debugging second, statistics third. All three are in 1.0 except interactive live debugging. Rejected: debugging-first (live connection) as the v1 driver; a trace viewer with seek covers most debugging needs.

**D2. Record and replay as the v1 core.** Runs produce a trace that the viewer replays; streaming during a run is supported but not controlling a running simulation. Rejected: live-control architecture first.

**D3. 2D top-down renderer, 3D-ready data.** Rejected for 1.0: 2.5D isometric (sprite art cost), full 3D (asset, camera and performance cost; FlexSim's strongest ground).

**D4. Continuous coordinates with a snapping grid, not a grid world.** The grid (single spacing `dx, dy` and an origin) is used for snapping and path network generation; entities keep true footprints. Rejected: entities on grid intersections as the core data model (inflexible shapes, unrealistic plants).

**D5. Three layout sources in 1.0** (auto, code, studio file) with per-property precedence auto < code < file. Davide's call: all three in v1.

**D6. Path network from generators plus overrides.** Lattice (blacklist) and lanes (whitelist) generators, explicit graphs still supported; arcs have enabled, direction and speed limit. Rejected: lattice only (unrealistic for industrial AGV layouts); lanes only (slow for prototypes and grid-based research).

**D7. The studio edits physical layout in 1.0** (paths, blocked areas), and edits trigger re-runs. Davide's call ("aim big"). Rejected: presentation-only editor in v1. Consequence: `layout.json` is a model input; physical and presentation hashes are separated.

**D8. Local studio server.** `simulatte studio model.py` serves the UI from a local Python process; runs happen in worker subprocesses. Rejected: static viewer with manual re-runs (clumsy loop, dead end for live debugging); Jupyter widget as primary (awkward for demos and full-screen editing); desktop app (packaging cost).

**D9. Five sub-projects** in order: events and trace, layout, viewer, studio, experiments. Each with spec, plan, two adversarial reviews by Codex (Astra), corrections, then Davide's go-ahead or a third review.

**D10. Event bus unifies logging.** `SimLogger` becomes bus subscribers; free-text logging becomes `log` events. Rejected: keeping `SimLogger` alongside a separate bus (two overlapping mechanisms).

**D11. Explicit emits from components, observer-only subscribers.** Rejected: deriving events from existing hooks and logs (incomplete); wrapping SimPy internals (fragile).

**D12. Stable entity ids with optional names** (*amended by D30*). Placeable entities take an optional `name`; otherwise per-kind construction-order ids. Transient entities get sequential ids, replacing `uuid4`. Orphaned layout entries are reported, not applied. Rejected: mandatory names (breaks every model; heavy for research scripts).

**D13. PixiJS for the scene**, as a framework-independent TypeScript module. Rejected: Canvas2D and SVG (do not scale to thousands of moving entities).

**D14. React 19 with React Compiler for the UI shell**, with clock-bound panels kept outside React's render cycle. Based on a researched comparison (2026-10-08): React scored highest on editor components (dockview, react-arborist, cmdk, Radix, Base UI), longevity and agent friendliness. Rejected: Svelte 5 (no maintained docking layout or tree view; would be reconsidered if docking were reduced to split panes); SolidJS (2.0 still a release candidate with breaking changes; thin component ecosystem).

**D15. Studio controls layout, seed, parameters and experiments in 1.0.** Davide's call ("aim big"). Typed `Params` dataclass generates forms. Rejected: layout and seed only; parameters without experiments.

**D16. KPIs computed only in Python**, as bus collectors whose output is stored in the trace. Rejected: computing KPIs in the viewer (two sources of truth).

**D17. Replications record KPIs only by default; full traces on demand** by deterministic re-run from the stored seed, with fingerprint comparison.

**D18. Local and remote SSH workers in 1.0.** Davide's call ("aim big"). One stdio protocol for both transports, plain `ssh` with the user's configuration, generic over any network. Rejected for 1.0: local pool only.

**D19. Remote environments shipped automatically with uv** (*amended by D37*). The model must be a uv project for remote runs; source bundles by content hash, `uv sync --frozen` on the worker. Rejected: pre-provisioned environments (fragile); Docker images (slow loop, Docker required everywhere).

**D20. uv installed on remote hosts only through an explicit `simulatte workers setup <host>`.** Rejected: silent auto-bootstrap (installing software unasked); documentation only (no connection check).

**D21. Release strategy: sub-projects merge into `main` and ship as 0.13–0.17, then 1.0 as stabilization.** Rejected: a long-lived feature branch merged at the end (months of drift and rebases, nothing ships); a mixed strategy.

**D22. Design documents live in root `specs/` and `plans/`**, which the sdist already excludes, not in `docs/` (the published website).

## Added while writing the spec (open to objection)

**D23. Per-environment named RNG streams replace the global `random` module.** Found while writing the spec: `Runner` seeds the global generator and `distributions.py` and `router.py` draw from it. Named streams make runs reproducible inside a long-lived process and enable common random numbers across experiment configurations.

**D24. Studio binds to 127.0.0.1 with a session token** (*amended by D36*), because it executes user code.

**D25. Zero-subscriber overhead budget of 3 %**, enforced by a CI benchmark on CPython and PyPy.

**D26. Layout lifecycle: physical properties freeze at the first physical access.** *Superseded by D27* (review 1, A1: existing builders create warehouses after the graph, so early freezing cannot bind entities reliably).

## Adversarial review 1, 2026-10-08 (Astra; triage by Claude, questions answered by Davide)

All 41 findings were accepted; see `reviews/2026-10-08-global-spec-review-1.md` for the triage. Decisions that changed the design:

**D27. Layout lifecycle is declare → resolve → bind** (A1, A21) (*extended by D38*). `build` declares; the layout resolves and freezes after `build` returns; components resolve handles against one frozen graph before the first event. Supersedes D26. Rejected: declaring physical entity ids before graph resolution (keeps the early freeze but adds a declaration step that every model must get right).

**D28. Events carry state deltas** (A5). Replay is snapshot plus deltas, so the viewer needs no per-type reducers and custom events can replay. Rejected: versioned reducers per event type in TypeScript (duplicates simulation semantics in two languages).

**D29. Worker = supervisor plus a fresh execution process per run** (A11, A12, A13). The supervisor runs no user code, owns the protocol channel, heartbeats and cancellation. Rejected: reusing interpreters per source hash (state leaks between runs).

**D30. Physical layout overrides require explicit entity names** (A18). Presentation overrides on generated ids are allowed with a warning. Amends D12.

**D31. Replays must reproduce the trajectory** (review question 1, Davide: agreed). The fingerprint includes a semantic event digest, computed in `kpi` and `full` modes. Rejected: KPI-only agreement (two trajectories can share KPIs).

**D32. Default job-KPI cohort: jobs completing within `[warmup, horizon)`** (question 2, Davide: agreed). Arrival-based cohorts are available per KPI; terminating experiments are a separate type.

**D33. Models may read layout properties as physical inputs only through the physical accessor**, which declares the dependency (question 3, Davide: agreed).

**D34. Models must be retry-safe** (question 5, Davide: agreed). Execution is at-least-once; files go to `env.artifacts_dir`.

**D35. Video export: MP4 (H.264) required on Chromium-based browsers and Safari; WebM, then PNG frames, as fallbacks** (question 4, Davide: agreed). Firefox verified in SP3.

**D36. Loopback only, no flag to bind other interfaces in 1.0** (A17). Token exchanged for an HttpOnly session cookie; Host and Origin checks. Amends D24.

**D37. Source bundles use an explicit manifest** (A16): git-tracked files or a declared include list, plus declared inputs; `.gitignore` heuristics are not used. `uv sync --locked`, not `--frozen` (A15). Amends D19.

## Adversarial review 2, 2026-10-08 (Astra; triage by Claude, question answered by Davide)

All 25 findings were accepted; see `reviews/2026-10-08-global-spec-review-2.md` for the triage.

**D38. Physical layout values may determine how many entities exist and how they are built** (review 2 question; Davide chose this over restricting 1.0 to configuring declared entities). Implemented as ordered **layout stages**: each stage reads frozen values of earlier stages through the physical accessor and may attach entities; the network is generated once after the last stage that affects it; later stages cannot change its inputs. The lifecycle becomes declare → resolve in stages → validate → bind → finalize → activate. Extends D27. Rejected: restricting layout to configuring entities declared in `build` (Claude's recommendation, simpler lifecycle).

**D39. The semantic digest covers a projection of domain events** with their own ordinal and the initial state after activation, excluding logs, KPI samples and the global `seq` (B1). Refines D31.

**D40. Execution requests are separate from resolved manifests** (B5). The coordinator submits what it knows; the worker reports runtime, dependencies and layout hashes at a `ready` step before simulating.

**D41. `layout.json` is not part of source identity; runs store their layout layers** so presentation edits re-resolve as data (B6).

**D42. All studio and CLI runs execute from immutable source and input snapshots** (B7); source capture moves from SP5 to SP4 (B16).

**D43. Network obstacles are explicit** (`obstacle=True`) and must have explicit positions; drawn footprints alone do not shape the network (B4).

**D44. SP1 is built behind feasibility gates**: vertical slice, minimal TypeScript conformance reader, benchmarks, then full migration (B25).

**D45. Warm spare execution processes**, each used once, offset fresh-process start-up cost without weakening isolation (B20). Refines D29.

## Adversarial review 3, 2026-10-08 (Astra; triage by Claude)

All 10 findings were accepted; see `reviews/2026-10-08-global-spec-review-3.md`. No new product decisions were needed; D38 stands.

**D46. Activation is a core SP1 contract** (C2, C3): prelude collapsed into the initial state, initializers that do not advance time, initial-state capture and `ready`, then one ordered queue of all pre-activation commands executed as recorded transitions; a failing queued command fails the attempt without rollback.

**D47. Worker environment caches hold external dependencies only; local packages always load from the run's snapshot**, verified at preparation, locally and remotely (C5).

## SP1 decisions, 2026-10-08 (Davide, on Claude's recommendations from the SP1 inventory)

**D48. SP1 may lift two structural constraints when that simplifies design or implementation:** `simulatte/__init__.py` may export a public surface, and the import audit that forbids `intralogistics` modules from importing core production modules may be relaxed.

**D49. Investigate and fix in SP1 the possible off-by-one in the server `queue_length` log value** (inventory §3), with a regression test.

**D50. Drop loguru.** Text, JSON and SQLite sinks become plain bus subscribers; the log level becomes per-environment.

**D51. Built-in components stop calling `env.debug(...)`; their activity is expressed as domain events**, which the text sink renders as log lines at DEBUG. Filtered messages cost nothing, and debug arguments (for example `job.priority()`) are no longer evaluated eagerly. `env.info()` and friends stay for user code.

**D52. Old collector protocols are replaced by bus collectors.** `MetricsCollector`, `TimeSeriesCollector`, the intralogistics collector hooks and the `collect_time_series`/`collect_workload` wiring go; the default collectors keep their result attributes and plot helpers (`ema_*`, `wip_ts`, `plot_wip()`, …). Rejected: adapters for the old hook protocols until 1.0.

**D53. Trace encoding: MessagePack chunks compressed with deflate in a small custom container.** Python uses `msgpack` (pure-Python fallback on PyPy); the browser uses `@msgpack/msgpack` and the built-in `DecompressionStream`. Rejected: Arrow IPC (pyarrow is heavy), SQLite (needs a WebAssembly build in the browser).

**D54. When the trace writer saturates, recording applies bounded backpressure** (the simulation blocks until the writer drains) rather than failing the run. Proposed by Astra and Claude in SP1 review 2; confirmed by Davide on 2026-10-08.

## SP1 gate G3, 2026-10-09 (Davide, on Claude's recommendations from the G3 report and its review)

**D55. The CI overhead gate compares against two baselines with separate budgets.** Released `simulatte==0.12.0`: ≤ 3 % + calibrated band (the user-facing promise). 0.12.0 with its `env.debug` calls stripped (`benchmarks/strip_debug.py`): ≤ 10 % + 2 % on CPython and ≤ 3 % + 5 % on PyPy, tightened after tuning. Rejected: released baseline only (≈22 % slack hides SP1 regressions); stripped baseline only (synthetic; fails today).

**D56. A small hot-path tuning task precedes G4**, targeting the `env.wants` guard cost (≈46 checks per job), so SP1's own unobserved-path cost moves toward 3 % + band before G4 multiplies the emitting sites.

**D57. Regression budgets accepted** (reported in CI, not gated until calibrated on runners): digest ≤ 3.2× CPython / ≤ 4.3× PyPy; full ≤ 4.9× / ≤ 7.0×; ≤ 1.75 KB trace per job (10-server shop); cold seek p95 ≤ 100 ms at 50k jobs; extra peak RSS ≤ 256 MB; sampling ≤ 1.03 + band vs 0.12.0. The `digest` budget is the starting point for the `kpi` mode.

**D58. Float sums that reach events, KPIs or the digest use `math.fsum`** (exactly rounded, identical on CPython 3.11, 3.12+ and PyPy), starting with `BaseJob.total_queue_time`. This changes the pinned reference digests once.

*D58 implementation note (ruling R17, 2026-10-09):* applied to every float `sum()` in `src/simulatte`, including sums that decide behaviour (dispatching priorities, samplers, arrival rate, planned release dates, fleet path and load computations), since those reach the digest through the trajectory. On CPython the reference digests and every documented example output stayed identical; only the PyPy golden moved, and it now equals CPython's. Sequential `+=` accumulations were left as they are (already runtime-independent).

## SP1 final verification, 2026-10-10 (Davide, on Claude's recommendations from the SP1 final report)

**D59. Budgets for `default`, `default_logging` and `kpi` accepted as proposed** (reported in CI, not gated until calibrated on runners, like D57): `default` against released 0.12.0 ≤ 3 % + band, against 0.12.0 without debug calls ≤ 10 % + 2 % on CPython and ≤ 3 % + 5 % on PyPy, and against the branch's no-subscriber time ≤ 1.10× CPython / ≤ 1.06× PyPy; `default_logging` ≤ 3 % + band over the same shop with the default sinks closed (`bare`), with the no-subscriber limits unchanged against 0.12.0; `kpi` ≤ 3.7× CPython / ≤ 4.5× PyPy of no-subscriber time. D57's `full` ≤ 4.9× on CPython is **kept** although u90-50k measured 4.83 to 4.99×: the ratio is borderline because the unobserved path got about 10 % faster after Task 12b while the `full` time did not change, and the CI-size workloads give 4.45 to 4.71×. The scaled-up intralogistics workload stays a reported-only measurement with no budget.

**D60. CPython `full` budget raised to ≤ 5.3×** (2026-10-10, Davide; reported in CI, not gated until calibrated on runners, like D57). Supersedes the CPython `full` part of D57 and the "kept at 4.9×" sentence of D59. The fixes after the adversarial review add a `server.work_credited` event per operation (direct `Server` use must replay correctly), which makes `full` recording about 5 % slower; with the 4.83 to 4.99× already measured on u90-50k, the expected ratio is about 5.1 to 5.2×. The PyPy `full` budget (≤ 7.0×) and the other D57 limits are unchanged.


## Pre-0.13 implementation notes, 2026-10-10 (issues #54–#55)

Davide authorized addressing the post-SP1 enhancements before release. The wire format of D53 stays
MessagePack/zlib; the TypeScript reader now decodes the strict wire subset directly, removing its dependency
on `@msgpack/msgpack` internal methods. KPI declarations are an optional format-1.0 extension before the
first release (see the SP1 hardening amendment).

Runner artifact calibration keeps the CPython noise band at 2% and changes the PyPy noise band from 5%
to 7%: the largest measured upward bootstrap uncertainty was 6.22 percentage points. D55/D59 overhead
budgets are unchanged; only the empirical noise allowance changes. Methods, inputs and limitations are in
[`research/2026-10-10-pre-013-hardening.md`](research/2026-10-10-pre-013-hardening.md).

## SP2 amendments, 2026-10-10 (Davide)

Davide approved all five amendments after the SP2 reviews and a concise explanation of A1–A5 (“All approved”).
The [SP2 specification §3.1](2026-10-10-sp2-layout-design.md#31-approved-amendments-and-clarifications)
records their detailed contracts. Approval records decisions, not implemented behavior.

**D61. SP2 traffic remains node-based (A1).** Node capacity configures the existing resource-based manager;
enabled/directed/speed-limited arcs are supported, but segment reservations are outside SP2. The existing
head-on passing limitation remains. This bounds changes to fleet traversal, cancellation and deadlock behavior.
Amends C2.3/D6; rejected for SP2: segment locks or arc reservation attributes without enforcement.

**D62. Inserting a lane vertex makes removed-segment overrides stale (A2).** Inserting V between A and B
replaces `(A,B)` with `(A,V)` and `(V,B)`; existing vertex ids and unaffected segment addresses remain stable.
An override targeting `(A,B)` is reported stale, never silently transferred to both children. Clarifies C2.3;
rejected: preserving a removed address or inferring override intent from proximity or lineage.

**D63. Simulatte 0.14 writes trace format 1.1 with optional layout metadata (A3).** Optional `layout-v1` adds
`INITIAL.layout`; the manifest's activation-time fields include layout schema version and full/physical/layers
hashes, or null without Layout. INITIAL is written after preparation, avoiding provisional HEADER hashes.
Major version stays 1, older readers may ignore layout, and new readers retain 1.0 support. Amends C1.7 and
the SP1 trace contract; rejected: recording unresolved layout in HEADER or requiring a major-version break.

**D64. Layout mode guards manual stepping and shares traffic resources (A4).** Manual `env.step()` requires
completed activation with Layout attached. Resource-based fleets on the same layout graph share one manager,
so separate reservations cannot contradict each other. No-layout stepping and manager ownership are unchanged.
Refines C1.10/C2.7; rejected: allowing simulation before binding or independent managers over one layout graph.

**D65. Layout bindings cannot be invalidated by public mutation (A5).** After binding, static bays, station/
parking nodes, component graph references and graph traffic attributes are read-only. AGV movement accepts
only canonical enabled nodes of the resolved graph, or None; equal copies are rejected. No-layout mutation
behavior stays unchanged. Refines C2.7; rejected: constructor-only checks that later public mutation can bypass.


## SP3 approval, 2026-10-11 (Davide)

Davide approved [SP3 revision 2](2026-10-11-sp3-viewer-design.md) and its implementation plan after two adversarial reviews and correction verification (“Approved”). Browser/performance acceptance remains to be demonstrated.

**D66. Trace 1.2 adds optional KPI display metadata (A1).** Python KPI declarations gain interpolation/missing rules. Preserve the exact kpi-declarations-v1 wire fields and publish separate `KPI.series_display` under `kpi-display-v1`. Older readers ignore it; new readers retain 1.0/1.1 support and explicit legacy display defaults. No change to KPI arithmetic or digest.

**D67. Full traces capture observers separately (A2).** Optional `observer-events-v1` uses OBSERVERS type 9 with phase/bounds/catalog epoch and null-ordinal, empty-delta entries. Domain chunks/prelude/footer replay semantics stay unchanged, preserving released-reader verification. Merge histories only in new inspection APIs; KPI records remain the value source. Recorder diagnostics use a non-bus channel to prevent recursion, including across recorders. Rejected: adding observers to domain chunks, which makes released `Trace.verify()` report a false mismatch.

**D68. Portable keyframes (A3).** Define elapsed-time/progress-fraction keyframes and recorded accuracy, endpoint/monotonicity validation and timestamp rounding tolerance as specified by SP3 §3. Motion always follows recorded state; unknown/invalid descriptions are explicitly approximate.

**D69. Viewer implementation choices and limits.** Light industrial visual direction; React19/compiler, Pixi8 with CSP-safe extension, dockview, uPlot, Mediabunny ordinary MP4 with positioned streaming sinks, zip.js ZIP64. HTML caps100MiB trace/150MiB output, warn25MiB; every video/PNG export at most100000 frames. Provisional256MiB export working-set budget requires measurement. Direct file writes preferred; Safari uses OPFS as required by D35. Cleanup is current-origin/session scoped: crashes may leave temporary exports and random-port later sessions cannot remove another origin's files; document browser site-data cleanup and origin-reuse exposure. Actual Safari MP4 and all other acceptance tests remain release gates.


**D70. Lower the PNG frame cap after ZIP metadata measurements (2026-10-11).**
SP3 §9 explicitly permits lowering the provisional cap to stay within the export
working-set budget. Keep video at 100,000 frames; cap PNG at 10,000 plus its timing
manifest. A pinned zip.js 100,000-entry / >4 GiB ZIP64 stress archive passed Python
`zipfile.testzip()`, but Node peak RSS reached 1.61 GB; it does not justify a
100,000-frame browser promise. A Chromium 10,000-entry metadata probe peaked at
95,970,737 bytes of JS heap. Real-render export measurements and their narrower
memory-method limits belong in the SP3 acceptance report. This is an implementation
limit under the approved provisional-budget rule, not a relaxation of that budget.
Actual Safari was initially deferred at Davide’s request; his subsequent manual
checks passed and are recorded in the SP3 acceptance report.

## SP4 approval, 2026-10-11

**Status: approved by Davide ("Reviewed and approved. Proceed"), not yet implemented.** These entries record the focused
[SP4 design](2026-10-11-sp4-studio-design.md) and
[implementation plan](../plans/2026-10-11-sp4-studio.md). A1–A7 are the explicit approved amendment/clarification list. The direct implementation authorization supersedes the pending review gate; no separate adversarial review pass is claimed.

**D71. Execution boundaries and local scheduling.** Public optional metadata in
`model.py`, population/stopping API in `stopping.py`, internal `execution/`
package shared by headless CLI and Studio. One local slot, zero automatic retries,
no model imports in coordinator/supervisor, one-use inspection and run children.
Reuse SP3 scene/playback/export/auth and SP2 layout re-resolution. Rejected:
ASGI-process execution, interpreter reuse, or a second simulation/replay engine.

**D72. Worker protocol 1.0 uses bounded MessagePack envelopes and raw fragments.**
Fixed magic, u32 big-endian frames, 2 MiB frame/256 KiB envelope/1 MiB fragment
caps; stdlib plus existing pure-Python-capable msgpack. Separate control and
bounded stdout/stderr drains; ready acknowledgement before queued commands.
Rejected: pickle, line-delimited stdout JSON and encoding large trace bytes as
base64 JSON. A4 documents the required activation integration.

**D73. Content-addressed immutable working-byte snapshots.** Explicit Git-tracked
membership plus declared includes/inputs, current dirty disk bytes, normalized
PAX archives and domain-separated canonical manifest hashes. Layout remains
separate. Detached per-attempt read-only materialization, no editable hardlinks.
Source-only disposable metadata inspection resolves input declarations (A3);
additive includes clarify untracked Git membership (A6). Reject `git archive
HEAD`, implicit ignored-file capture and running directly from editable sources.

**D74. Import validation does not define watching.** Isolated `-I -S` bootstrap,
validated local import roots and guarded origins throughout execution, including
lazy/namespace imports; runtime/dependency evidence in resolved manifests.
Watch the whole manifest, not merely imported modules. Unsupported executable
startup hooks/editable loaders fail explicitly; custom/native code is not an OS
sandbox (A5). Reject sys.path-prepending alone as an isolation guarantee.

**D75. Bounded, acknowledged spool and independent lifecycle.** Default
256 MiB/1,024 files per attempt including reservations; maximum segment 128 MiB,
16 MiB transfer window, 64 MiB recorder pending budget. ACK only after durable
coordinator receipt; a full spool blocks, with cancel-responsive waits and
`disk_full` after 60 s without reclaiming ACKs. Supervisor heartbeat 1 s/loss 5 s,
stalled 10 s, graceful cancel 2 s then TERM/KILL escalation. POSIX guardian plus
process groups; Windows suspended child assigned to kill-on-close Job Object.
Deliberate POSIX session escape is a documented limitation, not hidden coverage.

**D76. No warm spare in SP4.** Default and implemented pool size zero; measure
cold inspection/ready/run/close cost on CPython and PyPy. C4 permits spares and
D45 motivates them but does not require one before measurement. Rejected:
pre-importing user code or a speculative background interpreter pool.

**D77. Durable atomic visibility and attempt fencing.** SQLite index schema 1,
immutable verified artifacts renamed before a fenced DB transaction publishes
results, crash recovery never adopts unindexed results. This explicitly amends
C5.1's literal cross-filesystem/SQLite transaction wording on approval (A1).
Single project writer lock; provisional streams fenced by attempt/generation;
presentation views link original result identity without changing its trace.
Rejected: marking success before artifacts are durable, or late-result wins.

**D78. watchfiles with manifest reconciliation and optimistic revisions.**
Optional Studio dependency, custom full-manifest filter, 250 ms quiet/1 s maximum
batch debounce, 5 s reconciliation and polling fallback. Monotonic revision plus
input digest prevents ABA; server/disk validation guards all writes. Reuse
`reresolve_presentation` including structural/negative dependencies; physical or
source changes cancel/rerun. Reject Uvicorn reload and visual-field-name heuristics.

**D79. Finite stopping needs an explicit population interface.** Add normalized
Horizon/FinitePopulation policies and Population admission/completion/pending
tracking. Count/time cutoff and safety limit determine drain time; registry or
queue emptiness is not a population definition. A2 fills the master API gap.
Optional trace `execution-v1` in container 1.3 records execution provenance without
changing domain events/digest (A7); layout schema stays 1.0. Rejected: opaque
SimPy event stopping with falsely complete provenance.

**D80. Extend SP3's server/viewer with bounded live protocols and editing.**
Reuse bootstrap/sessionStorage/terminal renewal, add explicit websocket backend,
Origin/Host checks and first-message secret auth. Incremental committed-prefix
reader retains SP3 cache limits; one websocket/tab carries control/availability,
HTTP carries immutable trace bytes. Full typed parameter and SP2 layout editing,
revision-aware undo/redo and orphan/stale controls. Exports pin committed results
and separate presentation overlays. Experiments, comparisons and SSH stay SP5.


**D81. Bounded spool receipts do not require unbounded duplicate history.**
Use attempt-local authenticated opaque segment ids carrying sequence, offset,
length and stream identity, bound to the content SHA-256. Authenticate every ACK
before recognizing a retired sequence as a duplicate. These ids are transport
receipts, never canonical run/content identities. Active temporary reservations
count against quotas; failed deletion retains credit usage. File trace output
keeps released bytes and interruption behavior; segmented output strictly rejects
oversized pending items, including latency-triggered seals. Partial transfer and
receiver durability are completed with the store integration in Tasks 6–7.


**D82. Fragment credit and complete-segment durability are different ACK states.**
The 128 MiB segment cap cannot operate under a 16 MiB window if every ACK waits
for a whole segment. Fsync and transactionally record each canonical fragment's
contiguous extent before returning transfer credit. Partial uploads stay invisible
and consume worker spool space. Only a hash-verified complete segment receipt
publishes a prefix and releases spool credit. Entering finalizing closes uploads;
publication streams receipt batches and checks the assignment again at commit.
KPI-only results preserve `not_verifiable` for semantic digest verification;
structural checks and exact byte/manifest/fingerprint equality still apply.

The store uses one mutex-serialized writable/checkpointing SQLite connection.
Any additional readers must use URI `mode=ro`; they cannot write or checkpoint.
This is consistent with ownership and avoids the documented trigger of the
[SQLite WAL-reset race](https://www.sqlite.org/wal.html) in older bundled
SQLite versions. Native version/platform acceptance remains required.

**D83. Preserve ready provenance and record the final import audit separately.**
`ResolvedManifest` is captured with INITIAL and referenced by `ready`; publication
requires identical resolved bytes in the trace and result proposal. Lazy imports
can occur after readiness, so the final result also retains `final_imports` from
a second origin-policy audit. Do not mutate INITIAL or the acknowledged manifest
to insert facts learned later. Every ordinary import remains guarded throughout
execution. This resolves the timing gap between the ready contract and complete
import evidence; it does not promise that arbitrary custom/native loading is
sandboxed.

The execution trace uses optional `execution-v1` in container 1.3. Python validates
request identity; the TypeScript reader validates its structure, retaining its
existing division of responsibility (coordinator verifies request hashes and
semantic fingerprints). Released Python 0.15.0 can check and verify the shared
new fixture. KPI selection filters recorded declarations, series and scalar
fingerprints; it never detaches collectors or changes domain accounting. Compare
common KPI keys, as required by the existing invariance contract.

**D84. Inspection and execution share fencing, but not a fabricated run identity.**
Inspection requests contain the model reference, captured source identity and
runtime evidence and use the `inspection-request-v1` hash domain. They have normal
attempt generations and durable metadata receipts, but successful inspection does
not insert a simulation result. Large metadata, request, runtime and layout maps
travel as verified local MessagePack document references; control frames retain
their existing bounds. A referenced document binds its kind, length and SHA-256,
and readers reject mutation and symlink replacement. These local references are
an SP4 transport detail; they are not an SSH protocol contract.

The physical source root and configured import search roots are separate. A
`src` project can load its selected model at the source root without implicitly
adding that root to its import path. The guarded finder preserves the stdlib
`find_distributions` extension for data-only installed metadata discovery (see
[Python's primary documentation](https://docs.python.org/3.11/library/importlib.metadata.html#extending-the-search-algorithm));
it does not restore editable finders or startup hooks. Runtime evidence includes
actual interpreter/platform facts, normalized installed versions and RECORD
manifest hashes and is checked in each child before model import and after the
run. RECORD identity does not claim to hash every installed package file or
snapshot the external environment; missing RECORD data is an explicit provenance
warning. No dependency provisioning is introduced.

The coordinator persists captured preparation independently of working project
configuration, so reproduction can reopen after the model or pyproject is edited
or removed. Snapshot archive and manifest directory entries must be durable before
the preparation index is committed. A run deadline includes queueing and detached
materialization; expiration before dispatch must not launch user code. A storage
failure that also prevents the terminal index write is reported as an undurable
live failure and closes admission until restart. Startup recovery owns the orphan
transition; the running coordinator must not report a durable terminal write that
did not happen.

**D85. Bind discovered project settings to the configuration bytes that produced them.**
Project discovery records a domain-separated identity for the parsed
`pyproject.toml` bytes, including an explicit absent-file identity. Source capture
checks that binding against its captured entries before publication. A changed
configuration must produce `snapshot_changed` within the existing three-attempt
capture budget and require fresh discovery; it must not pair new configuration
bytes with old include/exclude/import-root settings. Reproduction still uses the
stored project settings and captured bytes without reopening working configuration.
Synthetic trusted-runtime bundles do not derive their membership from a project's
pyproject and remain independent of this binding.

Input-state documents encode the seed as canonical decimal text, just like the
execution request wire schema. CLI integers and canonical text normalize to the
same value; the execution request retains its internal integer representation.
This preserves all supported 63-bit seeds without weakening the wire format's
safe-integer bound. Source refresh retains valid parameter values and same-family
stopping values; incompatible fields or a changed stopping family require Apply.

**D86. Coalesce watcher observations without losing the newest layout edit.**
The monitor has one refresh processor and one latest pending observation. Source
changes invalidate provisional work immediately. A layout observation made stale
by an earlier presentation commit may be re-observed against the current revision,
provided no newer observation, permission pause or shutdown superseded it. This
reconciles file observations; it does not retry a failed execution attempt.
Acknowledgement uses captured manifest/configuration/layout identities, never a
new working-tree scan that could swallow edits made during inspection.

A watcher or background-read permission failure pauses execution. If the index
cannot persist invalidation, admission closes, queued work is discarded and the
failure is explicitly marked `index_terminal_state: not_durable`. Last committed
results remain distinct from current invalid or provisional inputs.

**D87. Live reader generations share bounded caches, not mutable query metadata.**
A committed prefix is identified by attempt, monotonic generation, byte length and
SHA-256. Coalescing may skip notification generations, but every byte in the
new extent is read and hashed; missing bytes or a mismatched cumulative digest
reject the extension. Original prefix extents retain their original immutable
byte sources. Metadata parsing starts at the previous committed boundary and
publishes a new query view only after successful validation. Old views retain
their catalog, bounds and KPI declarations.

Use pinned `@noble/hashes` 2.0.1 for incremental SHA-256 state and cloning, with
1 MiB digest reads. The [primary API documentation](https://github.com/paulmillr/noble-hashes/tree/2.0.1)
and installed package source confirm incremental updates, cloned state and ESM
subpath imports; its Node >=20.19 requirement fits the existing Node >=22 workspace.
No full-prefix buffering or from-zero rehash is introduced. Standalone reader
bundle verification includes this dependency and still prohibits runtime network
and filesystem access.

Generations share the existing decoded-cache bounds and a two-read byte-source
scheduler. Each candidate's new chunks get distinct cache identities, so a
candidate rejected during metadata validation cannot contaminate a later one.
Previously committed chunk identities remain reusable. The live playback adapter
retains one newest pending notification and fences both reads and frames on an
attempt/revision change. Follow is explicit; manual seek, step and playback leave
Follow. Terminal status is supplied by the coordinator, never inferred from a
missing or present trace footer.

**D88. Preserve explicit websocket queue semantics and reconcile before replay.**
Studio selects Uvicorn's `WebSocketProtocol` class explicitly, with asyncio, h11,
256 KiB receive frames, a 16-message receive queue, compression disabled and no
proxy headers/access logging/reload. The locked Uvicorn 0.54.0 backend and
websockets 17.2 are exercised together. Uvicorn warns that its `websockets` alias
will change to the SansIO implementation; selecting a class avoids silently
changing this queue contract. The legacy implementation is deprecated, so a future
backend migration must verify equivalent bounds before replacing it. This does
not change the separate 256-message/4 MiB application send queue.

Primary source verification: [Uvicorn configuration](https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/config.py)
accepts a protocol class; the [selected backend](https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/protocols/websockets/websockets_impl.py)
passes the configured frame and queue limits to websockets. Existing optional
Starlette and Uvicorn versions remain unchanged; only websockets is added directly.

A newly authenticated socket receives its initial state before notifications
observed while that snapshot was being read. Its initial event sequence is
reserved before the read; later queued notifications retain their order. Events
with an attempt carry that attempt's original input revision and top-level
`attempt_id`/`generation`, separate from the current presentation association.
The browser retains one pending state refresh, bounds outstanding commands to 32,
and rejects unresolved commands as having an unknown outcome on disconnect.
Reconnect obtains authoritative state and never automatically replays a mutation.
HTTP layout PUT also uses a content-bound command id and an explicit base revision.

Partial valid startup parameters are retained as a draft. Missing required fields
prevent auto-run. Invalid CLI overrides open an explicit error and reviewable model
defaults without executing them; successful Apply is required before starting.
This refines startup recovery without relaxing validation for normal Apply.

Layout reads expose the immutable bytes captured for the returned input revision,
not an unobserved working-file read paired with that revision. A later disk edit
is reconciled by the watcher or rejected by the final protected-write comparison.
Finite float parameters outside JavaScript's safe-integer range use an explicit
JSON exponent; their field schema still distinguishes them from integer inputs.
Nonfinite command and layout values are rejected before sending.

## D89 — Editor drafts, inverse history and disposable physical previews (2026-10-12)

Parameter drafts retain their base revision until explicit review. A new schema
flags incompatible or removed edits; Apply sends one normalized input change.
Layout history retains guarded field patches (64 entries and 32 MiB total).
Arrays replace wholly, as in SP2. Undo and redo apply inverse/new patches to the
latest revision and refuse overlapping external changes; they do not restore
an old whole-file snapshot. Numeric controls and canvas gestures share these
drafts, with pointer movement kept imperative until pointer-up.

Use the existing SP2 JSON schema and validator for structured controls. Schema
forms cover fields that do not have specialized canvas handles. File Reset
removes an override, while `$unset` suppresses an optional inherited value.
Lane vertex ids remain stable; edits never transfer old segment overrides to
new vertex ids. Generated-id physical moves are refused, and presentation
references carry a visible stability warning.

Physical `layout.validate` reuses the normal immutable request, supervisor,
receiver and durable INITIAL/ready barrier in a disposable coordinator with its
own temporary run store. It cancels before `ready_ack`, so no simulation event
is stepped. Return the resolved layout only after the supervisor confirms exit
and descendant cleanup. No simulation result is published, no working layout
is written, and the real coordinator's revision/active attempt is unchanged.
One preview may run alongside the project's execution slot; there is no warm
spare. The default preview timeout is 30 seconds (an explicit serving timeout
also applies). A changed revision or coordinator shutdown cancels the preview.

This resolves the focused design's unspecified preview scheduling detail:
preview needs a separate bounded slot to avoid replacing an active run merely
to validate an unsaved draft. It uses existing worker protocol messages and
never weakens normal result publication. The read-only validation command does
not hold the Studio mutation lock during Python preparation, allowing cancel
and newer revision commands to proceed; the preview rechecks its base before
returning. Presentation-only validation still uses pure re-resolution.

Large preview drafts use authenticated `PUT /api/layout/validate` with the same
16 MiB streaming body cap and revision header as layout writes. This endpoint
only validates; it never replaces the configured file. Small websocket
`layout.validate` remains supported. This makes the existing large-document
contract apply to validation as well as saving, without raising control-frame
limits or creating a general upload API.

## D90 — Live presentation, pinned exports and explicit recovery (SP4)

Studio reuses `LiveRecording`, `Session`, `Playback`, the SP3 controller panels
and `Scene`. A `LiveSession` adapter owns one current and one pending prefix;
an imperative reconciliation layer switches attempts and committed results.
Frames do not enter React. Manual seek/step exits Follow; only the explicit
Follow control resumes edge-following. Failed or superseded provisional data
is invalidated, while an indexed previous result remains selectable. Historical
run selection uses that attempt's recorded layout, even when repeated attempts
share one input revision and its current presentation association has moved.
The history UI shows the newest 20 committed attempts.

A presentation overlay is a separate immutable, structurally validated
layout-v1 bundle whose physical hash must match the recording. Python's saved
association remains the authority for physical equivalence; the TypeScript
reader does not implement a second layout resolver/hash verifier. HTML embeds
an inert, escaped JSON envelope `{version:1,trace_sha256,layout}`, bound to the
unchanged embedded trace bytes. The offline player verifies that binding before
using the overlay. Video/PNG use the same `Session` overlay. Export dialogs pin
both recording and presentation at opening; later live or layout updates cannot
replace them. Live prefixes cannot be exported before result publication. SP3
file exports and no-network HTML CSP remain available.

Add `project.rescan` to the focused design's v1 control-command list. Its body
accepts only optional boolean `retry`. Focus requests rescan without retry and
does not rerun unchanged healthy inputs. The explicit Rescan / retry control
may restart a failed initial inspection; revision checks fence a scan racing a
newer edit. This fills the specified focus/manual-rescan behavior rather than
using `state.get` (which remains read-only reconciliation) as an execution
trigger.

An initially invalid layout is repairable before model metadata is available.
`layout.get` pins the exact bytes to the current revision; replacement requires
that captured invalid baseline, canonical layout validation, a durable backup,
a final disk comparison and a revision check. A valid initial file cannot use
this path to bypass normal input ownership. Saving invokes the normal watcher
and inspection path; it never executes against a substituted empty layout.

`trace_available.last_cursor` is a hint derived from each newly committed
segment, including INITIAL, domain indexes, simulation observers, KPI samples
and the footer. It is null before INITIAL. It never advances on preparation
records or uncommitted fragments. The scan skips compressed event bodies and
never rescans prior prefixes; metadata decoding uses the existing reader record
bound. The browser still derives playback state exclusively from verified
prefix bytes, and does not trust the hint as an index or a progress deadline.

Concurrent viewer/editor/export scenes share Pixi global resources. Dispose a
renderer with `releaseGlobalResources` only when the final owned scene exits;
otherwise disposing a preview can invalidate another active renderer. Native
Linux Firefox exposed this lifecycle defect; browser evidence is recorded in
`research/2026-10-12-sp4-live.md`.
