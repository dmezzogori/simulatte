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
