# Simulatte Studio: global design

- **Status:** final (revision 4 plus the verification fix), approved by Davide on 2026-10-08 after adversarial reviews 1–3 and a verification pass ([`reviews/`](reviews/)); finding ids such as (A5), (B3) or (C2) mark the changes each review caused
- **Date:** 2026-10-08
- **Scope:** architecture of the visualization, layout, studio and experiments work that leads to Simulatte 1.0
- **Decision log:** [`studio-decisions.md`](studio-decisions.md) (numbered decisions with rationale and rejected alternatives)

This is the global spec. It fixes goals, architecture, the contracts between sub-projects, and cross-cutting rules. Each sub-project gets its own spec and implementation plan, which may refine anything here that is marked *deferred to SPn*, but may not contradict a contract without amending this document first.

## 1. Purpose

Simulatte is a discrete-event simulation library (SimPy-based) for production planning and control (PPC) and intralogistics. It has no visualization: results are inspected through logs and post-run matplotlib plots. Commercial tools such as FlexSim, Simul8 and AnyLogic are judged largely on what users can see. This work closes that gap while keeping Simulatte a code-first, open-source Python library.

Visualization serves three uses, in this priority order:

1. **Client-facing animation.** Show a recorded run as a polished 2D animation of the plant, with playback controls and video export, to customers and stakeholders.
2. **Visual debugging.** See where jobs, orders and vehicles are and why, inspect entity state at any time, and step through the event history.
3. **Statistics.** KPI panels synchronized with playback, and comparison of configurations across replications with confidence intervals.

Simulatte is public and generic. Nothing in this design may assume a particular network, host set, operating system setup or user workflow beyond what is stated in the requirements below.

## 2. Goals and non-goals

### Goals (in scope for 1.0)

- G1. A typed, complete event stream emitted by all built-in components and open to user-defined entities.
- G2. A recorded **trace** of a run (events with state deltas plus periodic snapshots) that supports fast seeking and event stepping, can be streamed while the run is in progress, and is reproducible from its manifest.
- G3. A **layout model**: continuous world coordinates, entity footprints and ports, a snapping grid, AGV path network generation and editing, with three sources of layout (automatic, code, studio editor) and defined precedence.
- G4. A **2D viewer** that replays traces with play, pause, speed, seek and event stepping; shows KPI panels synchronized to the playback clock; exports video and self-contained HTML.
- G5. A **local studio** (`simulatte studio model.py`) that runs models, edits layouts and parameters, re-runs on change and streams results to the viewer.
- G6. **Experiments**: parameter grid × replications, confidence intervals, warm-up handling, a persistent run store, comparison views, and execution on local and remote (SSH) workers.
- G7. A headless CLI path (`simulatte run`, `simulatte experiment`) with the same semantics as the studio, for CI and batch work.

### Non-goals for 1.0

- N1. 3D rendering. The data model is 3D-ready (§9.4); the renderer is 2D.
- N2. Interactive live debugging of a running simulation (pause a running process, breakpoints). Traces streamed during a run are watched, not controlled. Live control is the main post-1.0 candidate and the protocols must not preclude it.
- N3. Graphical model building. Model logic stays in Python; the studio edits layout and parameters only.
- N4. Transport time derived from layout distances for production (non-AGV) flows. Job transfers between servers are drawn as decoration and take no simulated time unless the model says so (§C7.4).
- N5. Windows as a remote worker host. Windows as a local studio host is supported on a best-effort basis.
- N6. Cloud or cluster schedulers (Slurm, Kubernetes, Ray). Remote workers are plain SSH hosts.
- N7. Jupyter widget. The scene module is designed to make it a thin wrapper later.
- N8. Multi-user or networked studio. The studio listens on loopback only; there is no option to bind other interfaces in 1.0 (A17).
- N9. Clearance-aware vehicle geometry beyond a single inflation radius (§C2.3).

## 3. Architecture overview

```
 model.py (@simulatte.model, typed Params, declared inputs)     layout.json
        │                                                           │
        ▼                                                           ▼
 Coordinator (studio server or headless CLI) ── run store (.simulatte/)
        │  worker protocol (framed stdio; local subprocess or `ssh host …`)
        ▼
 ┌──────────── Worker supervisor (no user code) ────────────┐
 │  heartbeats · cancellation · artifact shipping            │
 │      │ private pipe                                       │
 │      ▼                                                    │
 │  Execution process (fresh per run, stdout/stderr captured)│
 │   Environment(seed, time_unit) ─ RNG streams              │
 │   build() declares → layout resolves → components bind    │
 │   entities emit typed events (+ state deltas) ─► EventBus │
 │        ├─ semantic digest   ├─ TraceRecorder              │
 │        ├─ KPI collectors    └─ log sinks                  │
 └───────────────────────────────────────────────────────────┘
        ▲
        │  websocket + HTTP (127.0.0.1, session cookie)
 Studio UI: React shell + framework-independent PixiJS scene
   └─ standalone HTML export · in-browser video export
```

Layers, from the bottom up:

- **Core** (`simulatte`): environment, entities, event bus, RNG streams, trace recorder and reader, KPI collectors, layout model. No web or network dependencies. Must stay usable as today for scripts, research and RL training.
- **Execution** (*module names deferred to SP4*): model entrypoint loading, worker supervisor and execution process, worker protocol, coordinator, scheduler, run store.
- **Studio server** (`simulatte.studio`, optional extra `simulatte[studio]`): Starlette app, websocket protocol, static assets.
- **Frontend** (`studio/` TypeScript workspace in this repo): scene module, React shell, exporters. Built assets are shipped inside the wheel and the sdist.

## 4. Sub-projects and releases

Sub-projects are implemented in order. Each one merges into `main` and ships as a 0.x release. Breaking changes are allowed in each (all APIs are unstable before 1.0). 1.0 is a stabilization release after SP5.

| # | Sub-project | Release | Contents |
|---|---|---|---|
| SP1 | Events and trace | 0.13 | Entity identity and lifecycle, event model with state deltas, event bus, semantic digest, logger unification, RNG streams and samplers, observer-purity audit, trace writer and Python reader, a minimal TypeScript conformance reader, KPI collector API with window semantics, caller-supplied provenance API, migration of existing collectors, benchmarks |
| SP2 | Layout | 0.14 | Layout model, grid, auto-layout from static topology, code API, `layout.json`, network generators and overrides, declare/resolve/bind lifecycle, intralogistics migration to bound graphs, orphan and stale detection |
| SP3 | Viewer | 0.15 | `studio/` workspace, TS trace reader, PixiJS scene, playback clock, KPI panels, inspector, HTML and video export, `simulatte view` with the minimal `[studio]` extra for static serving (A31) |
| SP4 | Studio | 0.16 | Model entrypoint, worker supervisor and execution process (local transport), source and input capture with canonical hashing (B16), coordinator, minimal run store (A31), studio server and websocket protocol, parameter forms, layout editor, revisions and file watching, `simulatte studio` and `simulatte run` |
| SP5 | Experiments | 0.17 | Experiment definition, replications, statistics, run store extensions, comparison UI, SSH transport, remote provisioning, `simulatte workers setup` |

Dependencies: SP2 depends on SP1 (entity ids). SP3 depends on SP1 (trace) and SP2 (layout schema). SP4 depends on SP1–SP3. SP5 depends on SP4.

**SP1 feasibility gates** (B25). SP1 ships as one release but is built in stages, each a gate for the next: (1) a vertical slice with a few representative entity kinds, events with deltas, the digest and the trace codec; (2) a minimal TypeScript reader that decodes the slice's fixture traces and replays state, proving the format works in the browser; (3) benchmarks against §C1.9 on the slice; (4) only then the migration of every component. A failed gate changes the design before the migration starts.

## 5. Contracts

These are the interfaces between sub-projects. Changing any of them after its sub-project ships requires amending this spec and bumping the relevant schema version.

### C1. Entities, events and the trace (SP1)

#### C1.1 Entity identity

- Every entity the viewer can draw or the trace refers to has a `kind: str` and an `id: str`. Ids are unique per environment **across kinds**; references in events use the id alone. A separate `label` is for display only (A38).
- **Placeable entities** (servers, PSPs, warehouses, stations, parking areas, AGVs, nodes) accept an optional `name`. When given, `id = name`. When omitted, `id = f"{kind}-{n}"` with `n` a per-kind counter in attachment order. User names matching the generated pattern of any kind (`^<kind>-\d+$`) are rejected, so generated and user ids cannot collide. Duplicate ids raise.
- **Transient entities** (jobs, orders) get `id = f"{kind}-{n}"` from a per-environment counter. `uuid4` ids are removed.
- **Builders** (`build_*_system`) take a `prefix` and namespace every id they create, so several systems can share an environment (A38).
- **Definitions versus bindings** (A19). Environment-free objects (`Node`, `Arc`, `LayoutGraph`, `SKU`, distribution descriptions) are definitions, not entities. An object becomes an entity when it is **attached** to an environment: through a constructor that takes `env`, or through an attachment call (for example submitting a `TransferOrder` to a coordinator, or binding a layout node). Registration happens at attachment. Attaching an object that references entities of another environment raises.
- **Lifecycle** (A24). Entities are created and **retired**; both emit events. Retired transient entities leave the live registry and later snapshots. The trace keeps their history through events, so the viewer can still inspect them. Python-side retention (for example `ShopFloor.jobs_done`) is unaffected and independent.
- **Physical layout overrides require explicit names** (A18). The studio refuses to persist a physical override for an entity with a generated id and asks for a `name` in code. Presentation overrides on generated ids are allowed with a warning (D12, amended by D30).

#### C1.2 Events

- An event is an immutable record with: `type` (stable string), `t` (simulation time), `seq` (per-environment strictly increasing integer), entity references by id, a semantic **payload**, and **state deltas** (below). The pair `(t, seq)` is the **event cursor**: a total order used for stepping, seeking and snapshot boundaries (A27).
- **State deltas** (A5). Each event carries the changes it makes to the viewer-visible state of the entities it affects, plus entity creation and retirement. Replay is generic: state at cursor `c` = the latest snapshot before `c` with all deltas up to and including `c` applied in order. The viewer needs no per-type reducers.
- **Delta operations** (B19). Scalar fields are replaced. Collection fields are updated with bounded operations (insert at position, remove, move), never by re-sending the whole collection, so the cost of a delta does not grow with queue length. A single event whose encoded size exceeds a configured limit is an error in debug mode and a recorded warning otherwise.
- **Transition boundaries** (A5). An event is emitted after the state change it describes is complete. Operations with several phases (for example processing end, after-operation hooks, resource release and completion callbacks in `ShopFloor`) emit one event per phase, so every cursor position corresponds to a consistent state.
- **Catalog.** Event types are registered with a name, a schema version, payload fields, and the entity state fields their deltas may touch. Entity kinds register their state schema (field names and wire types). The catalog is written to the trace.
- **Custom events.** User-defined entities and processes register their own kinds and event types through the same API. A custom event with deltas replays fully; a custom event without deltas is **inspect-only**: it appears in the event log and inspector and changes no rendered state.
- **Wire types** (A26). Payload and state values use a closed set of canonical types: integers within ±(2⁵³−1) (larger values raise), float64 including explicitly encoded `+inf`, `-inf` and `NaN`, UTF-8 strings, booleans, null, lists, and maps with string keys. Each field declares nullability. These are representable in Python and in browser JavaScript without loss.
- **Deep immutability** (A6). Payloads and deltas contain only the wire types above, stored as immutable containers (tuples and frozen mappings). Debug mode validates this at emit time.
- **Motion** (A23). A motion event is emitted when movement actually begins, after any traffic permission is granted, and covers one or more arc traversals with per-segment start and end times and the speed profile used. The viewer interpolates position along the segment from a **portable motion description** (B14): either one of a fixed set of curve primitives (constant speed; trapezoidal acceleration and deceleration) with its parameters, or Python-generated keyframes with a declared accuracy. A speed profile that can provide neither is drawn with linear interpolation, and the trace marks the motion as approximate. Any deviation (interruption, rerouting, stranding) emits a superseding event that states the position the simulation actually uses. If the simulation treats an interrupted vehicle as being at the previous node, the viewer shows that discontinuity; physics is never changed to make animation smoother. Snapshots include each entity's active motion plan.

#### C1.3 Event bus

- `env.emit(event)` publishes; `env.bus.subscribe(handler, types=...)` subscribes, optionally filtered by type.
- **Interest checks** (A32). `env.wants(EventType)` is true when at least one subscriber (including the semantic digest when enabled) takes that type. Emitting sites guard event construction with it, so only events someone listens to are built.
- **Delivery order** (A6). Subscribers are called synchronously, in subscription order. Events emitted by a subscriber during delivery are queued and delivered FIFO after the current event has reached every subscriber; their `seq` is assigned at emit time. Subscription changes during delivery take effect from the next event.
- **Observers do not interfere.** Subscribers must not schedule SimPy events, draw random numbers, or mutate simulation state. They may emit derived events (for example KPI samples), which only other observers see. Behavior-changing extension stays with hooks, dispatchers and policies.
- **Observer invariance** (A7, B18). The core outcome of a run (the semantic projection of §C1.6 and the final state of the model) does not depend on which observers are attached. Among instrumented configurations (default logging, KPI only, full trace, any added collectors) the digest and every KPI common to them are identical. Getters that observers call must be pure; accounting that behavior depends on (for example the server utilization read by dispatching rules) stays in the core and is updated by the simulation, not by observers. SP1 includes an audit of current observational reads; known cases are `AGV.utilization()` and its siblings, which flush state when read.
- Subscriber exceptions propagate.

#### C1.4 Logging

- `SimLogger` becomes bus subscribers (text, JSON, SQLite sinks). `env.info(...)`, `env.debug(...)` and friends remain and emit `log` events with message, level, component and extra fields.
- The default environment subscribes only a text sink for `log` events at the configured level, so default logging does not cause other events to be constructed.
- Log events are excluded from the semantic digest.
- The exact shape of the history and SQLite query APIs, and whether loguru stays, are *deferred to SP1*.

#### C1.5 Randomness

- The module-level `random` generator is replaced by **per-environment named RNG streams**. `env.rng(name)` returns an independent generator whose seed is derived from the run seed and the stream name with a stable cryptographic hash (never Python's `hash()`).
- **Descriptions versus samplers** (A8). Distributions are immutable descriptions. A component that receives a distribution binds it at attachment into an environment-bound sampler on a stream named from the component's id and purpose (for example `server-3/processing`). A description shared across servers or reused across environments therefore yields independent, correctly bound samplers. Stream naming and derivation are *deferred to SP1*.
- **Unmanaged randomness.** Zero-argument callables that draw from the global `random` remain accepted for compatibility, but are flagged: the run manifest records unmanaged randomness and the studio warns that such runs are not reproducible.
- **Common random numbers** (A33). Named streams synchronize random numbers per stream across configurations of an experiment. They do not guarantee alignment when the number of draws differs between configurations (rejection sampling, configuration-dependent routing), and they do not guarantee variance reduction. Stronger coupling through per-job substreams is *deferred to SP1*. Paired comparisons remain valid whether or not CRN helps.

#### C1.6 Determinism and reproducibility

- **Semantic projection** (A3, B1). The digest covers a projection of the run that observers cannot change: the canonical initial state captured at activation (§C1.10), then every **domain event** (events emitted by simulation components, excluding `log` events, KPI samples and any other observer-derived or diagnostic events) in order, each with its own **domain ordinal** (a counter over domain events only, independent of `seq`), its type, time, entity references, payload and deltas. Presentation fields are excluded. The global `seq` is not part of the projection, so adding a log line or a collector does not change the digest.
- **Semantic digest.** A rolling hash over the canonical encoding of the semantic projection. It is computed in `kpi` and `full` recording modes, and is off by default in plain scripts. Its cost is part of the benchmarks (§C1.9). Comparing digests is a logical comparison of trajectories; comparing complete trace files is a separate, stricter check used only in determinism tests.
- **Fingerprint** = semantic digest + KPI scalars. A re-run of a replication must reproduce the fingerprint exactly; this proves the animated trajectory is the one whose KPIs are reported (D31).
- **Provenance** (B16). SP1 provides a caller-supplied provenance API: whoever starts a run (a script, `Runner`, or later the coordinator) supplies source, input and dependency identities it knows, and every field it cannot supply is recorded as `unavailable`. Plain scripts therefore produce honest, partial manifests; SP4 adds automatic source and input capture.
- **Run manifest** (A4). Every run records: the normalized stopping policy (§C5.5) (C7); simulatte version; Python implementation and version; OS and architecture; the resolved dependency set (lockfile hash, or the list of installed distributions and versions); RNG algorithm identifier; model source bundle hash; hashes of declared inputs; parameters; seed; physical and full layout hashes; horizon; warm-up; time unit; unmanaged-randomness flag.
- **Volatile fields** (A34) such as wall-clock start, host name and durations are stored separately and excluded from all comparisons and from the canonical content of a trace.
- **Reproducibility guarantee** (C10). Two runs with identical manifests produce the same semantic projection, digest and KPI values **provided the manifests are complete** (no `unavailable` provenance) and the run uses no unmanaged randomness. Identical complete trace files additionally require the same recording level and observer configuration. Partial manifests still record what is known and allow fingerprints of reruns to be compared, but promise nothing. Across platforms or runtimes, results may differ (libm, Python's `random` algorithms between versions); this is detected by fingerprint comparison, not prevented.
- SP1 audits iteration order in observable outputs (sets, hash-ordered collections; `Node` ordering varies with `PYTHONHASHSEED` today) and fixes it.

#### C1.7 Trace format

The encoding is *deferred to SP1*, under these requirements:

- One file with a **header** (format version, required and optional features, catalog, entity registry at the start, the **resolved layout** from SP2 on, the run manifest, volatile metadata kept apart), a body of **chunks**, an **index**, and a **footer**.
- **Chunks** (A25) are closed when any limit is reached: a simulation-time window, a maximum number of events, a maximum size in bytes, or a maximum wall-clock age of the oldest completed but unpublished event (B12). The latency bound covers completed events only; it cannot force progress inside long-running model code. Chunk boundaries are cursor positions `(t, seq)`, not times. Each chunk starts with a **snapshot** of live entities and bounded summary state, and is decodable on its own.
- **Safe points** (B12). Snapshots are built only between events, from the recorder's replay state (snapshot plus applied deltas), never by reading live simulation objects in the middle of a transition. Publishing a closed chunk never waits for the simulation.
- **Commit protocol.** A chunk is written completely before its index record is appended. Readers of a growing file see only chunks with index records. The footer records the outcome: `completed`, `cancelled` or `failed`. A missing footer means the run is in progress or the file is truncated; an incomplete trailing chunk is skipped.
- **Catalog growth** (A26, B17). Event types and kinds first registered during a run are written as catalog-extension records before their first use. The index records catalog epochs, so a reader seeking directly to a chunk can fetch every definition it needs without replaying the prefix.
- **Reader compatibility** (A26). Readers support the same major format version. Optional features they do not know are ignored; unknown required features make them refuse the file with a clear error.
- **Recording levels:** `full` (events with deltas, snapshots, KPIs), `kpi` (KPI series and scalars, digest), `none`.
- Readable from Python and from browser TypeScript without native extensions; compression allowed under the same condition.
- **Targets** (hypotheses to verify in SP1 and SP3 with a reference model, then fixed with evidence) (A32): a 10-server job shop over 50,000 jobs produces a `full` trace under 100 MB; seeking to any cursor takes under 200 ms at p95 on a 2021 laptop; recording sustains the simulation's event rate without unbounded memory.

#### C1.8 KPIs

- A **KPI** is declared with a name, unit, kind (`series`, `scalar`, or both), and its **estimand** (A9): observation unit (job, operation, time-weighted state), cohort rule, denominator, how intervals crossing the window boundary are clipped, how entities still in the system at the horizon are treated (censoring), whether EMAs reset at warm-up, the value when there are no observations, and finalization. KPIs are computed only in Python.
- **Observation window** (C7) depends on the stopping policy (§C5.5): `[warmup, horizon)` for steady-state runs and `[0, horizon)` for fixed-horizon terminating runs, both matching SimPy's `until`, which does not process events scheduled exactly at `horizon`; `[0, T_end]` for finite-population runs, where `T_end` is the policy's stopping time (§C5.5), included in the window. Time-weighted KPIs divide by the actual length of the window.
- **Default cohort for job KPIs** (flow time, tardiness, lateness): jobs that complete within the window. Arrival-based cohorts are available per KPI (D32). For finite-population runs every job completes inside the window by construction; for fixed-horizon terminating runs, censoring applies (§C5.5).
- **Time-weighted KPIs** (utilization, WIP) accumulate from state-change events and clip at the window boundaries, instead of crediting work at operation completion as `Server.worked_time` does today.
- **Values over playback** (A41). Series are Python-generated samples of a KPI's value as of each time, so the viewer can show the value at the current playback time. Final scalars are shown separately and labelled as end-of-run results. Interpolation and missing-value rules are declared per series.
- Existing collectors move onto the bus. Their matplotlib `plot_*` helpers stay for scripts.

KPI declarations (unit, description, kind and observation semantics) are recorded under the optional
`kpi-declarations-v1` feature, keyed by scope and KPI name; old traces expose no declarations. The format-1.0
extension and late-collector publication rules are specified in the SP1 pre-0.13 hardening amendment.

#### C1.9 Performance budgets

Benchmarks run in CI on CPython and PyPy, on reference workloads defined in SP1 (A32):

| Mode | Budget |
|---|---|
| No subscribers | ≤ 3 % + band against released 0.12.0; SP1's own cost ≤ 10 % + 2 % (CPython) / ≤ 3 % + 7 % (PyPy, runner-calibrated) against 0.12.0 with debug calls stripped, tightened after tuning (D55) |
| Default shop floor metrics (`default`) | Against released 0.12.0 `default`: ≤ 3 % + band. Against 0.12.0 `default` with debug calls stripped: ≤ 10 % + 2 % (CPython) / ≤ 3 % + 7 % (PyPy, runner-calibrated). Against the branch's no-subscriber time: ≤ 1.10× CPython / ≤ 1.06× PyPy. Report-only until calibrated on CI runners (D59) |
| Default logging (`default_logging`) | ≤ 3 % + band over the same shop with the default log sinks closed (`bare`), that is the 5 % target (CPython 5 %, PyPy 10 % with the runner-calibrated band); the cross-version limits of no subscribers apply unchanged. Report-only until calibrated (D59) |
| KPI only, with digest | digest ≤ 3.2× CPython / ≤ 4.3× PyPy of no-subscriber time (D57); `kpi` (digest, default EMA collector and KPI collector) ≤ 3.7× CPython / ≤ 4.5× PyPy of no-subscriber time, report-only until calibrated (D59) |
| Full trace | ≤ 5.3× CPython (D60) / ≤ 7.0× PyPy; ≤ 1.75 KB/job; seek p95 ≤ 100 ms at 50k jobs; extra peak RSS ≤ 256 MB (D57) |

Workloads include a congested case (long queues) so that delta and digest costs that grow with state size are caught (B19). **End-to-end replication throughput** (B20) is measured separately, on CPython and PyPy, for short and long runs, including process start, imports and warm-up; SP4 and SP5 establish the supported workload envelope from it before promising experiment throughput.

The workload, logging level and hardware class are recorded with the results; CI compares against the stored baseline with a tolerance band.

#### C1.10 Preparation and activation (SP1) (B3, C2, C3)

Activation is a core contract, defined in SP1 and used by the layout lifecycle from SP2 on. `env.run()` activates the environment on its first call if the caller has not done so explicitly. The sequence is:

1. **Prelude.** Everything before activation (entity construction, and from SP2 the layout stages, binding and finalization) is preparation. Events emitted during preparation form the **prelude**: they are recorded for inspection, but their effects are collapsed into the initial state and are never replayed as deltas.
2. **Initializers.** Components run their activation initializers in attachment order (for example registering AGV starting positions with the traffic manager). Initializers must complete without advancing simulated time; one that cannot is an error.
3. **Initial state.** The canonical initial state is captured. It starts the semantic projection (§C1.6); the domain ordinal starts at 0 after it. The worker sends `ready` at this point (§C4).
4. **Queued commands.** Before activation, every public command on a component that supports deferral (submit, cancel, update and similar) is appended to one queue in call order, whether or not it needs physical data, so dependent commands keep their order (for example `submit` then `cancel`). Reads made before activation return state as of before the queued commands, and objects affected by queued commands report a `pending activation` status. At activation the queued commands execute in order, at time zero, as ordinary recorded transitions. An uncaught exception in a queued command stops activation and fails the attempt; later commands are not executed, and nothing is rolled back.
5. **Simulation.** Ordinary events, including other time-zero events, follow.

### C2. Layout (SP2)

#### C2.1 World model

- **Continuous world coordinates** in meters: `(x, y, z)`, right-handed, `y` pointing up (north), `z` up from the floor and `0` in 1.0.
- A **placement** binds an entity id to: position, rotation (degrees about `z`), **footprint** (width, depth, height; rectangle in 1.0), **ports** (named points relative to the placement where material enters or leaves), and **visual** properties (shape, icon, color, label).
- **Simulation time units** (A39). `Environment(time_unit=...)` declares the unit (`"s"`, `"min"`, `"h"`, …) or leaves time unitless. The unit is in the manifest. Speeds are meters per time unit. The viewer labels time accordingly; playback speed is expressed as simulated time per wall-clock second.

#### C2.2 Grid

- A layout has one **grid**: spacing `(dx, dy)` in meters and an origin. It is used for **snapping** and for **network generation**. Entities keep their true footprint; only anchors snap.
- Snapping is applied when a value is edited (in code helpers or in the studio), never during persistence or resolution (A37). Exact coordinates remain possible.

#### C2.3 Path network

- AGV movement uses a single resolved `LayoutGraph`, produced by the layout's **network spec** from one of:
  - **lattice:** a node per free grid point inside a bounding area, arcs to 4 or 8 neighbours;
  - **lanes:** user-drawn polylines snapped to the grid; lane intersections become nodes;
  - **explicit:** a hand-built `LayoutGraph`.
- **Obstacles** (B4). A footprint is drawn for every placed entity, but only entities explicitly marked `obstacle=True` (and blocked areas) shape the network. Obstacles must have an explicit position from code or file; an auto-positioned obstacle is a resolution error. Moving entities (AGVs) are never obstacles.
- **Vehicle geometry** (A35). Vehicles are points. A layout-wide `clearance` radius inflates obstacles and blocked areas before generation, which is how aisle width is modeled in 1.0. Diagonal arcs are not generated when they would cut the corner of an obstacle.
- **Ports** connect to the network through connectors. Each port has an **approach point** outside its owner's inflated envelope, along the port's approach direction; the segment from the port to its approach point is exempt from the owner's clearance but checked against every other obstacle (B24). A port with no valid connector is a resolution error with a diagnostic.
- Arcs carry `enabled`, direction (`both`, `forward`, `backward`), an optional `speed_limit`, and traffic attributes (*deferred to SP2*).
- **Stable addressing** (A20, B13). Lattice nodes are addressed by grid indices `(i, j)`; lane vertices have persistent ids assigned when created and kept across edits, and lane segments are addressed by their vertex-id pair, so inserting a vertex changes no existing address; explicit nodes are addressed by their id, and arcs by their endpoint pair. Bulk overrides are stored as geometric selectors (row, column, rectangle, lane) rather than element lists. An override whose target no longer exists after regeneration is **stale**: reported and not applied, like an orphan.

#### C2.4 Layout sources and precedence

1. **Auto-layout** (lowest), from **static topology only** (A22): declared flows (`layout.flow([...])`), the order servers are attached to a shop floor, and routings that the model declares statically. Auto-layout never invokes routing callbacks or any simulation code. Without topology it falls back to a deterministic grid arrangement ordered by id. Auto-layout is presentation-only: it never affects the network or any physical property, and a physical read (in a stage or at bind time) of a value that came from auto-layout is rejected; such dependencies need an explicit value in code or in the file (C4). Auto-layout is computed once, over the complete entity set, after the last stage.
2. **Code**: placements, grid, network spec and overrides declared in `build`.
3. **File**: `layout.json`, written by the studio editor.

Precedence is resolved **per property**. The result is the **resolved layout**, which the run uses and the trace stores and hashes.

#### C2.5 `layout.json`

- Versioned schema, published as JSON Schema, with separate sections (A20): `grid`, `network` (generator settings, clearance, blocked areas, lanes, element and selector overrides) and `placements` (keyed by entity id).
- **Override semantics** (B23): an absent property inherits from code or auto-layout; an explicit `{"$unset": true}` removes an inherited optional value (for example, no speed limit on an arc whose code sets one); lists are replaced as a whole. Resetting an override to the inherited value means deleting it from the file.
- **Not part of source identity** (B6). `layout.json` is a run input with its own revision, excluded from the source bundle and its hash, so a presentation edit never changes the source identity.
- **Numbers** are serialized round-trip-safe (shortest exact representation) (A37). Keys are sorted. Hashes are computed over a normalized form, independent of formatting.
- **Orphans and stale targets** are reported and not applied. The studio lists them and offers to reassign or delete them.

#### C2.6 Physical versus presentation properties

- **Physical** properties change simulation results; **presentation** properties do not. Classification is **transitive** (A2, B4): when the network is generated (lattice or lanes), everything generation reads is physical: grid, clearance, blocked areas, lanes, and the position, rotation, footprint and ports of every obstacle.
- **Model-read properties** (D33). A model may read any resolved layout property that has an explicit value (from code or file), in a layout stage or at bind time, but only through the layout's physical accessor, which records the dependency and makes that property physical.
- **Structural dependencies** (C8). The physical dependency closure includes structural and negative dependencies: absent values and defaults that were read, membership of collections (the set of obstacles, blocked areas, lanes, ports), and the predicates that selected them. Adding, removing or enabling a physical input invalidates reuse just like changing one. Reading presentation properties by other means is unsupported and documented as such.
- The trace stores the **full** and the **physical** layout hash. A change of physical hash marks earlier results as stale; presentation edits do not.

#### C2.7 Layout lifecycle (A1, A21, B2, B3, D38)

1. **Declare.** The caller (studio, CLI or script) creates a `Layout`, loads `layout.json` if any, and passes it to `build`. Inside `build`, model code attaches entities and declares placements, the grid, the network spec, overrides, and optional **layout stages**. Components that need physical data take **handles** (node ids, port references such as `layout.port("WH-A", "out")`), not resolved objects.
2. **Resolve, in stages.** Everything declared in `build` is stage 0. A **layout stage** is a function registered with `layout.stage(reads=..., after=...)`. It receives the resolved, frozen values it declared it reads and may attach further entities and declare their placements, so physical layout values can decide how many entities exist and how they are built (for example the number of servers that fit a floor area). Rules:
   - Each stage is resolved (precedence applied) and frozen before any later stage runs. A stage reads only values of earlier stages, through the physical accessor, so everything a stage reads is physical.
   - Stages run in a deterministic order: declaration order, constrained by `after`. Cycles are rejected.
   - **Network barrier** (C1). The network is generated once, at the barrier: immediately before the first stage that declares a network read in its `reads`, or after the last stage when none does. At the barrier every graph-producing input freezes: grid, clearance, obstacles, blocked areas, lanes, ports and connectors, explicit nodes and arcs, and network overrides. Later stages may attach entities bound to existing graph elements, but may not introduce connectors or change topology, geometry or arc attributes; a stage that would need to is an error. The stage API is *deferred to SP2*.
   - Stages draw random numbers only from named RNG streams.
   - File overrides apply to stage-created entities by id like any other. When an edit changes how many entities a stage creates, overrides for ids that no longer exist become orphans.
3. **Validate.** After the last stage, orphans and stale targets are detected against all attached entities and all declared definitions, including layout nodes that are bound later (B3). Ports and connectors are validated. Errors stop the run before any simulation event.
4. **Bind.** Components resolve their handles against the one frozen graph. Fleet routing, traffic resources, warehouse bays, AGV initial nodes and distance computations all use **this one graph** instance. Binding may read further layout values through the accessor; those reads join the physical dependency set (B2).
5. **Finalize.** The physical dependency closure and the physical and full hashes are computed, and the layout is frozen completely. A later read of a property outside the closure raises.
6. **Activate.** The core activation sequence of §C1.10 runs: initializers, initial-state capture, queued commands, then the simulation.

**Stored layers** (B6). Each run stores its layout **layers**: the code declarations of every stage, the static topology used by auto-layout, the file overrides and the physical dependency closure. A later edit that touches nothing in the closure is re-resolved from the stored layers as pure data, without running model code. Any other edit requires a re-run.

Scripts that do not use a `Layout` keep passing an explicit `LayoutGraph` to their components, and that graph is authoritative. Mixing a `Layout` with directly passed graphs in one environment is an error. The intralogistics builders move to handles in SP2.

### C3. Model entrypoint (SP4)

```python
from dataclasses import dataclass
from typing import Annotated, Literal

import simulatte
from simulatte import Environment, Layout


@dataclass(frozen=True)
class Params:
    arrival_rate: Annotated[float, simulatte.Range(0.1, 2.0)] = 0.8
    policy: Literal["lumscor", "slar", "conwip"] = "lumscor"


@simulatte.model(
    params=Params,
    horizon=10_000,
    warmup=1_000,
    time_unit="min",
    inputs=["data/calibration.csv"],
)
def build(env: Environment, layout: Layout, params: Params) -> None:
    ...  # attach entities to env and declare their layout
```

- `@simulatte.model` attaches metadata and returns the function unchanged, so the model stays callable from plain scripts and tests.
- `inputs` declares the files the model reads (A16, A29). They are hashed into the manifest, included in source bundles and watched by the studio.
- Supported parameter types: `bool`, `int`, `float`, `str`, `Literal[...]`, `Enum`, each optionally `Annotated` with constraints and a description. The studio generates forms; the CLI accepts `--param name=value`.
- `@simulatte.model` also accepts a stopping policy (`stop=`) instead of `horizon` for finite-population models (C7).
- `build` must not run the simulation. The seed and time unit are set on the `Environment` before `build` is called.
- **Immutable inputs** (B7). Studio and CLI runs, local or remote, execute from an immutable snapshot of the source and the declared inputs (§C5.2). The working directory is the snapshot, and the import path contains the snapshot, never the editable project, so edits made during a run cannot leak into it.
- Models must be **retry-safe** (D34): a run may execute more than once. Files are written only to `env.artifacts_dir`, a per-attempt directory provided by Simulatte. Other external side effects are the user's responsibility, and the documentation says so.
- A model file may define several decorated models, selected by name (`simulatte studio plant.py:build`).
- `Runner` and the builders keep working without the decorator.

### C4. Worker protocol (SP4, SSH transport in SP5)

- **Two processes per worker** (A11, A12). The **supervisor** (`simulatte worker --stdio`) speaks the protocol on its stdin and stdout and never runs user code. For each run it starts a fresh **execution process**, connected over a private pipe. The execution process's stdout and stderr are captured as bounded diagnostics tagged with the run and attempt ids, so `print()` and native writes cannot corrupt protocol frames.
- **Framing.** Length-prefixed binary frames. The first frame from a supervisor is `hello`, preceded by a fixed magic sequence; bytes before it (for example bootstrap output on a remote shell) are logged as noise, not parsed. Encoding *deferred to SP4*; it must be implementable with the standard library or a pure-Python dependency.
- **Messages** at minimum: `hello` (protocol version, simulatte version, supervisor runtime, platform, capabilities), `run` (execution request, attempt id), `ready` (B5: sent after preparation, that is after the execution environment is provisioned, the model is loaded and the layout is finalized, and before the first simulation event; it carries the resolved manifest: the actual execution runtime, dependency set, layout hashes and capabilities), `cancel`, `heartbeat`, `progress` (simulation time, event count), `trace_chunk`, `kpi`, `diagnostics`, `result` (scalars, fingerprint), `error`, `shutdown`. Incompatible versions refuse to run with an explicit error.
- **Liveness and progress** (A13). The supervisor sends heartbeats independently of the execution process. Missing heartbeats past a timeout mean the worker is lost. Lack of simulation progress is reported as `stalled`, which is distinct from loss and does not kill the run unless a configured run timeout expires.
- **Cancellation and containment** (A13, B21). Cancellation is graceful first, forced after a grace period, and covers every process the model started. On Linux and macOS the execution process runs in its own process group, and a parent-death mechanism (for example `PR_SET_PDEATHSIG` on Linux, a watcher on macOS) ends it when the supervisor dies. On Windows (local studio only, N5) the execution process runs in a Job Object that kills all descendants when closed. Where a platform cannot guarantee descendant cleanup, the documentation says so. When the supervisor's stdin closes (coordinator gone, SSH disconnected) it ends all its execution processes and exits.
- **Spool and backpressure** (A13, B11). The execution process writes each committed chunk as a separate file in a spool directory. The supervisor ships chunks and deletes each one after the coordinator acknowledges durable receipt, so shipping frees space. A slow consumer delays shipping, not the simulation, until the spool reaches its configured bound; then the simulation blocks on its next write (observers cannot change results, so blocking is safe) and the supervisor reports `backpressure`, distinct from `stalled`. Cancellation remains responsive while blocked. If the bound is reached and nothing can be reclaimed (for example the coordinator is gone), the attempt ends with outcome `disk_full`.
- **Start-up cost** (B20). To hide interpreter start and import time, the supervisor may keep **warm spare** execution processes that have imported Simulatte but no user code. Each process still runs exactly one run and then exits, so isolation is unchanged.
- **Transports.** Local: the supervisor is a subprocess of the coordinator and runs in the current Python environment, with local packages loaded from the run's snapshot (§C5.2). SSH (SP5): the coordinator runs `ssh <host> <bootstrap command>` with the system `ssh` client and the user's SSH configuration; no extra ports, no daemon.

### C5. Coordinator, run store and experiments (SP4, SP5)

#### C5.1 Coordinator and run identity (A14)

- The coordinator runs inside the studio server or the headless CLI and owns the scheduler, the run store and the workers.
- An **execution request** (B5) is immutable and identified by a hash of what the coordinator knows before running anything: model reference, source snapshot hash, input hashes, layout file revision, parameters, seed, the normalized stopping policy (horizon, warm-up, or arrival cutoff and safety limit) (C7), recording level, KPI selection and the requested runtime and provisioning spec. It contains no value that only execution can produce. A stored result remains valid for a new request that differs only in the layout file revision when the difference touches nothing in that result's physical dependency closure (B6); the coordinator reuses it instead of re-running.
- The **resolved manifest** is produced by the worker during preparation and reported in `ready`: actual runtime, resolved dependencies, physical and full layout hashes, physical dependency closure. Results carry both. A `full` re-run of a `kpi` replication is a separate request linked to it as `replay-of`; verification compares resolved manifests and fingerprints.
- **Attempts.** Each execution of a request is an attempt with its own id. Execution is at-least-once. Only the attempt the coordinator currently assigns may publish results; late results from superseded attempts are discarded (fencing).
- **Publication** (B8). While an attempt runs, its committed chunks and KPI updates are **provisionally visible**: served to viewers tagged with the attempt and revision, and only for the attempt the coordinator currently assigns. Cancellation or supersession invalidates them, and viewers are notified. Final results are **committed** atomically: artifacts are written to staging, verified, moved into place, and recorded in the index in one transaction. Experiments, comparisons and replays use committed results only.

#### C5.2 Remote environments (SP5) (A15, A16)

- Remote hosts need non-interactive SSH access, a POSIX shell (Linux or macOS), and **uv**. They do not need Python; uv provides it.
- `simulatte workers setup <host>` checks connectivity, platform and uv, shows what it will change, and installs uv on request. This is the only step that installs uv. A missing uv makes runs fail with a message pointing to it.
- **Bootstrap before handshake.** The coordinator starts the supervisor through uv, pinned to the coordinator's simulatte version and the project's Python requirement, so the supervisor exists before the first `hello`.
- **Runtime provisioning is authorized by running.** The worker creates isolated environments in its cache with `uv sync --locked` (which fails if the lockfile does not match the project metadata), including the extras and dependency groups the request names. Cached environments contain **external dependencies only** and are keyed by the complete **provisioning spec** (runtime implementation and version, lockfile hash, extras, groups, installation options), not by the source bundle; they are built under a lock and are immutable once published (B10). **Local packages** (the root project, workspace members, path dependencies) are never installed into a cached environment: each attempt loads them from its source snapshot, ahead of any other installation of the same packages, and preparation verifies that every local package imports from the snapshot, failing the attempt otherwise (C5). The same rule applies to local runs in the user's environment, where the project may be installed in editable mode. The project's locked simulatte version must be protocol-compatible with the coordinator's; this is checked before scheduling.
- **Source capture** (B7, B16) is built in SP4 and used for local runs too: the source snapshot, input capture and canonical hashing below. SP5 adds shipping snapshots to remote hosts.
- **Source bundles.** The bundle manifest is explicit: git-tracked files of the project when it is a git repository, otherwise `[tool.simulatte.bundle] include`, plus declared `inputs`, minus configured excludes. Ignored files are never included implicitly, and untracked files never travel unless listed.
- **Preflight** before scheduling rejects: path dependencies or workspace members outside the project root, symlinks escaping it, bundles above a configured size, and dependencies on private indexes unless the worker has its own credentials (Simulatte never ships credentials).
- Bundles are normalized archives (sorted entries, fixed metadata) so their hash is stable; workers verify content against the hash before use.
- Worker hosts are listed explicitly in a config file (*format and location deferred to SP5*). There is no discovery. Local-only use needs neither uv nor a uv project.

#### C5.3 Run store

- Per project directory, `.simulatte/`: a SQLite index of experiments, execution requests, attempts (host, platform, status, resolved manifest, fingerprint, timings), KPI scalars, and artifact files. SP4 introduces the minimal store for single runs; SP5 extends it.
- Schema is versioned and migrated forward. The store is safe to delete.

#### C5.4 Replays

Any replication can be re-run at level `full` from its execution request. The coordinator prefers the original host, otherwise a host with an identical resolved runtime. The re-run's resolved manifest and fingerprint must match the stored ones; a mismatch is reported prominently and the replay is labelled as not verified.

#### C5.5 Experiments and statistics (A10)

- An **experiment** is a set of configurations (parameter grid or explicit list, optionally layout variants) × N replications, of one of three types (B15):
  - **steady-state:** fixed horizon, warm-up, window `[warmup, horizon)`, completion cohort by default (§C1.8);
  - **terminating, fixed horizon:** no warm-up, window `[0, horizon)`; entities still in the system at the horizon are censored, and each KPI declares how censored entities are reported (count, excluded, or partial durations);
  - **terminating, finite population:** arrivals stop at a cutoff (a number of jobs or a time); the run ends at `T_end`, the earliest time at or after the cutoff when no entity remains in the system and no arrival is pending, so every entity completes. If the system is already empty when the cutoff is reached, `T_end` is the cutoff time (for a count cutoff, the time of the last arrival); otherwise it is the time of the completion that drains the system. The same `T_end` defines the window and the denominator of time-weighted KPIs; a safety limit on simulated time ends runs that never drain, and such runs are reported as failed (C7).

  The stopping policy is normalized (type plus its parameters) and is part of every execution request and manifest (C7).

  Experiments can be defined in Python, in a file or in the studio.
- **Observations** are replication-level estimates: one value per KPI per replication. Jobs or time samples are never treated as independent observations.
- **Intervals:** t-based, configurable level, reported with the actual number of successful replications. Fewer than two observations is reported as insufficient, not as an interval.
- **Paired comparisons** pair replications by replication index (same seed, same stream names), never by completion order. Failed or missing pairs are reported and excluded explicitly.
- Intervals across a grid are **pointwise** unless a multiple-comparison correction is selected; the UI says so.
- Warm-up is user-set. Warm-up estimation aids (for example a Welch plot) are *deferred to SP5*.
- Comparison views: KPI table with intervals, overlaid series, and two runs animated side by side on one clock.

### C6. Studio server and websocket protocol (SP4)

- **Binding.** The server listens on `127.0.0.1` on a free port only (A17).
- **Authentication** (B9). The CLI opens a URL carrying a random one-time token. The server exchanges it for an `HttpOnly`, `SameSite=Strict` session cookie whose name includes a random instance id, so several studios on one machine do not overwrite each other's cookies, and redirects to a URL without the token, so it does not stay in history. This bootstrap request is the only request exempt from the `Origin` rule below. Browsers send cookies to every port of a host, so other loopback services are treated as untrusted: privileged operations (running code, writing files, websocket connections) additionally require a per-instance secret presented in a request header or the first websocket message. **Possessing the cookie alone must never be enough to obtain or regenerate this secret** (C6): it is delivered only in the response to the one-time-token bootstrap and kept in origin-scoped browser storage, never in a cookie. Recovery for reloads and new tabs (for example a fresh one-time link from the CLI, or handing the secret to a new tab of the same origin) is *deferred to SP4*. Tokens, cookies and secrets are never logged.
- **Request checks.** The `Host` header must be the loopback address and port (or `localhost` with that port), which blocks DNS rebinding. Websocket connections and state-changing requests must carry the server's own `Origin`.
- **File access** is limited to the model project directory. Layout writes go only to the configured layout path, atomically (temporary file and rename). Messages and uploads have size and parsing limits.
- **Untrusted content.** All strings from traces, models and layouts are rendered as text, never as HTML.
- **Protocol.** One websocket per tab carries versioned JSON messages for: model info and parameter schema, run control, progress, trace chunk availability (chunks are fetched over HTTP), KPI updates, layout reads and writes, validation results, orphans and stale targets, experiment control and results, input changes, errors. The UI in a wheel always matches its server.
- **Revisions** (A29). Each input set (source bundle hash, layout revision, parameters) has a revision id. Commands carry the revision they were based on. Layout writes use optimistic concurrency: a write based on an old revision is rejected, so two tabs cannot silently overwrite each other. Results are tagged with their revision; results for superseded revisions stay in the store but never replace the current view.
- **Watching** (A29, C9). The studio watches every file that belongs to the source snapshot (including files added or removed) and the declared inputs, so code that runs only in layout stages, binding or the simulation is covered too. Physical changes and source changes trigger a debounced re-run that cancels the previous one. Presentation-only layout edits are re-resolved from the run's stored layout layers (§C2.7) and re-rendered without re-running. An invalid file is reported and the last good run stays on screen.

### C7. Viewer (SP3)

#### C7.1 Scene and clock (A28)

- The **scene module** is framework-independent TypeScript. It renders a prepared state for an event cursor. Preparation is asynchronous (`prepare(cursor)`: fetch and decode chunks, apply deltas, load assets); rendering is synchronous on prepared state.
- One **playback clock** owns time. In interactive mode a ticker advances it; in manual mode an exporter or a test drives it and nothing else advances it. Comparison views slave two scenes to one clock.
- **Determinism** means the scene state for a cursor is deterministic. Pixel-identical output across GPUs and browsers is not promised.

#### C7.2 React shell

- The shell hosts the scene and the panels. **Rule** (D14, A36): values bound to the playback clock (timeline position, KPI tiles, live charts, inspector numeric fields) are written imperatively from the clock tick into DOM or canvas elements the shell hands over by ref. They do not go through `useSyncExternalStore` or React state. Only discrete events (selection, seek end, layout edits, undo and redo, run and experiment state) pass through React state. React Compiler is enabled. Update rates are measured in tests.

#### C7.3 Rendering rules

- Unknown entity kinds render as a generic shape with their label; unknown and inspect-only events appear in the event log and inspector.
- Event stepping moves the cursor one `(t, seq)` position at a time and shows the state after that event.
- KPI panels show values as of the playback time and, separately, end-of-run results (§C1.8).

#### C7.4 Zero-duration transfers (A27)

A job transfer between servers takes no simulated time (N4). At the transfer's cursor the job is at its destination. The viewer draws a decorative trail from source to destination over a fixed wall-clock duration in interactive playback and a fixed number of frames in exports. The trail is styled as decoration, is not selectable, and never stands for the job's position.

#### C7.5 Exports (A30)

- **Video.** Frames are rendered in manual clock mode and encoded in the browser with WebCodecs, then muxed by a pure-JavaScript library. Capabilities are checked with `VideoEncoder.isConfigSupported` before export. **Required:** MP4 (H.264) on current Chromium-based browsers and Safari. **Fallbacks:** WebM (VP9 or VP8) where MP4 encoding is unavailable, then a PNG frame sequence as a last resort. The **output sink** is specified and tested separately from encoding (B22): where the File System Access API is available, output streams to the chosen file; elsewhere (Safari) it is staged in the Origin Private File System and then offered as a download. Memory use stays bounded in both cases. Storage exhaustion ends the export with a clear error, and exports can be cancelled at any point. Firefox support is verified in SP3 (D35).
- **HTML.** A single file containing the scene, a read-only player and an embedded trace as data. Fonts, icons and scripts are inlined; it works with networking disabled; a Content Security Policy forbids network access and inline event handlers. Size limits *deferred to SP3*.
- Both are tested independently of interactive playback.

#### C7.6 Modes

Inside the studio (live, editable); `simulatte view run.trace` (static local server, read-only, `[studio]` extra); exported HTML (no server).

## 6. Packaging and repository layout

- `pip install simulatte`: core only. No web, network or frontend dependencies.
- `pip install "simulatte[studio]"`: server dependencies (Starlette, an ASGI server, a file watcher). Python extras cannot make files optional, so the frontend assets are always present in the wheel; the extra only adds the dependencies needed to serve them (A31).
- `studio/`: TypeScript workspace (Vite, React, PixiJS, Vitest, Playwright). Its build output goes to `src/simulatte/studio/static/`.
- **The sdist includes the built frontend assets**, so building a wheel from the sdist never needs Node. CI tests wheel installation and wheel construction from the published sdist (A31).
- Internal design documents live in `specs/` and `plans/` at the repository root (excluded from the sdist), not in `docs/`, which is the published website.

## 7. Compatibility and migration

- Breaking changes, by sub-project:
  - SP1: logger API reshaped; `Environment(seed=..., time_unit=...)`; stable ids, `name` and `label`; builder `prefix`; deterministic transient ids; `Runner` seeds environments instead of the global `random`; distributions become descriptions bound to samplers; collectors move onto the bus; observational getters become pure.
  - SP2: intralogistics components take handles resolved at bind time when a `Layout` is used; the explicit-graph path remains for scripts without a layout.
  - SP4: `@simulatte.model` is new and optional for code-first use.
- Each release's `CHANGELOG.md` entry includes a migration section. Examples and docs are updated in the same release.
- The PyPy lane stays green for the core, the worker and the trace recorder. The studio server targets CPython only.

## 8. Testing strategy

- **Core:** unit tests for ids, lifecycle, bus ordering, RNG streams and samplers, recorder and reader round-trips.
- **Observer invariance** (A40, B18): the same model run with default logging, KPI only, full trace and extra collectors yields identical digests and identical values for every KPI the configurations share. A separate test checks that a run with no subscribers reaches the same final model state as an instrumented run. The no-subscriber benchmarks stay free of instrumentation.
- **Determinism:** fresh processes with different `PYTHONHASHSEED` values yield identical canonical content; golden traces for reference models, regenerated deliberately.
- **Replay:** state reached by uninterrupted replay equals state reached by seeking, at every chunk boundary and at sampled cursors, including several events at the same `t`.
- **Malformed input:** truncated and corrupted traces, unknown required features, oversized messages.
- **Overhead:** the benchmark suite of §C1.9.
- **Layout:** property tests for stage ordering and cycle rejection, layout-dependent entity counts, pre-activation queuing, precedence, network generation (connectivity, clearance, corner cutting, one-way arcs), connectors, stale and orphan detection, schema and number round-trips, hash normalization.
- **Cross-language:** the Python writer and the TypeScript reader are tested on shared fixture traces, including non-finite floats and integer limits.
- **Frontend:** Vitest for the scene, clock and stores; Playwright end-to-end tests against a running studio: seek, stepping, export (video including the Safari sink path, and offline HTML), a physical layout edit triggering a re-run, a presentation edit not triggering one, and a stale-revision write being rejected.
- **Workers:** protocol tests with a fake transport; local integration tests for cancellation (including processes the model started), supervisor death, execution-process crash, `print()` noise, spool backpressure and reclamation, `disk_full`, and stale-attempt fencing; SSH transport against a `localhost` sshd in CI where available, otherwise marked and run manually.
- **Packaging:** install-from-wheel and wheel-from-sdist smoke tests that start `simulatte view` and load a fixture trace.
- **Statistics:** formulas tested deterministically against known values; interval coverage tested over controlled ensembles (for example many M/M/1 replications) against a binomial tolerance, in a slow suite, never as a single interval that must contain the true mean.

## 9. Cross-cutting rules

### 9.1 Observability without interference

Nothing that observes a run (recording, logging, KPIs, digests, streaming, the studio) may change its results (§C1.3).

### 9.2 Reproducibility

A run is identified by its manifest (§C1.6). The studio and the CLI can reproduce any stored run from it, and report when they cannot: missing source or inputs, changed runtime, unmanaged randomness, or fingerprint mismatch.

### 9.3 Generic by default

No assumptions about network topology, VPNs, host names, operating system setup or user tooling beyond the stated requirements: SSH and uv for remote workers; a current evergreen browser with WebGL2 for the studio, plus WebCodecs for video export.

### 9.4 3D-readiness

Coordinates are `(x, y, z)` everywhere; footprints have a height; visuals are described by kind, footprint and visual properties rather than 2D sprites.

### 9.5 Security

The studio executes user code and accepts requests only from the local machine with a session cookie (§C6). Trace and model strings are untrusted text everywhere they are displayed, including exports. Remote execution uses the user's own SSH configuration; Simulatte stores and ships no credentials. Bundles contain only declared files and go only to hosts the user listed.

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Event emission and digest slow simulations | Research and RL users regress | Interest checks; digest off in scripts; benchmarks per mode (§C1.9) |
| Trace size or seek time too large | Viewer unusable for real studies | Bounded chunks, live-only snapshots, early measurement against targets (§C1.7) |
| Cross-platform or cross-runtime differences | Replays fail verification | Manifest, fingerprint comparison, host preference (§C1.6, §C5.4) |
| Unmanaged randomness in user code | Irreproducible runs | Flag in manifest, studio warning (§C1.5) |
| Observer reads mutate state | Results depend on what is recorded | Purity audit, invariance tests (§C1.3, §8) |
| Lifecycle change breaks intralogistics users | Migration cost | Handles plus preserved explicit-graph path; migration notes (§C2.7, §7) |
| Scope (five sub-projects, two languages) | Long delivery | Each sub-project ships standalone value; reviews per sub-project |
| Layout file drifts from model code | Overrides on the wrong entity | Names required for physical overrides, kind checks, stale and orphan reporting |
| Remote environment mismatch | Wrong or failed results | Locked environments, bundle hashes, preflight, version negotiation |
| Frontend update storms at 60 Hz | Choppy playback | Imperative clock-bound updates (§C7.2) |
| TypeScript frontend in a Python library | Few contributors can maintain it | Separate workspace, own tests and docs, framework-independent scene |

## 11. Open questions for sub-project specs

- SP1: trace encoding and compression; event catalog and delta schema per kind; snapshot and chunk limits; stream naming and per-job substreams; history and SQLite query APIs; loguru; benchmark workloads.
- SP2: auto-layout algorithm; traffic attributes on arcs; lane intersection rules; handle API for intralogistics components; layout stage API.
- SP3: chart library (uPlot default candidate); docking library (dockview default candidate); MP4 muxing library; HTML export size limits; visual style and icon set; Firefox video support.
- SP4: frame encoding; module names; file watcher library; import tracking for watching; spool bounds; warm spare policy; source snapshot mechanism.
- SP5: worker host config; bundle manifest details; warm-up aids; experiment file format; store retention.
