# Simulatte Studio: global design

- **Status:** draft for adversarial review
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
- G2. A recorded **trace** of a run (events plus periodic state snapshots) that supports fast seeking, can be streamed while the run is in progress, and is reproducible from its header.
- G3. A **layout model**: continuous world coordinates, entity footprints and ports, a snapping grid, AGV path network generation and editing, with three sources of layout (automatic, code, studio editor) and defined precedence.
- G4. A **2D viewer** that replays traces with play, pause, speed and seek; shows KPI panels synchronized to the playback clock; exports MP4 video and self-contained HTML.
- G5. A **local studio** (`simulatte studio model.py`) that runs models, edits layouts and parameters, re-runs on change and streams results to the viewer.
- G6. **Experiments**: parameter grid × replications, confidence intervals, warm-up handling, a persistent run store, comparison views, and execution on local and remote (SSH) workers.
- G7. A headless CLI path (`simulatte run`, `simulatte experiment`) with the same semantics as the studio, for CI and batch work.

### Non-goals for 1.0

- N1. 3D rendering. The data model is 3D-ready (§9.4); the renderer is 2D.
- N2. Interactive live debugging of a running simulation (pause a running process, breakpoints, step into the live model). Traces streamed during a run are watched, not controlled. Live control is the main post-1.0 candidate and the protocols must not preclude it.
- N3. Graphical model building. Model logic stays in Python; the studio edits layout and parameters only.
- N4. Transport time derived from layout distances for production (non-AGV) flows. Job movement between servers is drawn but takes no simulated time unless the model says so.
- N5. Windows as a remote worker host. Windows as a local studio host is supported on a best-effort basis.
- N6. Cloud or cluster schedulers (Slurm, Kubernetes, Ray). Remote workers are plain SSH hosts.
- N7. Jupyter widget. The scene module is designed to make it a thin wrapper later.
- N8. Multi-user or networked studio. The studio is a local, single-user tool.

## 3. Architecture overview

```
 model.py (@simulatte.model, typed Params)            layout.json (studio overrides)
        │                                                       │
        ▼                                                       ▼
 ┌──────────────── Worker process (local subprocess or via SSH) ────────────────┐
 │ Environment ─ RNG streams                                                    │
 │      │                                                                       │
 │   entities (stable ids) ── emit typed events ──► EventBus                    │
 │                                                   ├─ TraceRecorder          │
 │                                                   ├─ KPI collectors         │
 │                                                   └─ log sinks (text/json/  │
 │                                                      sqlite)                 │
 └──────────────── worker protocol over stdio (length-prefixed frames) ─────────┘
        │
        ▼
 Coordinator (inside studio server or headless CLI)
   ├─ scheduler: local pool + SSH workers
   └─ run store: .simulatte/ (SQLite index, trace and KPI files)
        │  websocket (127.0.0.1, session token)
        ▼
 Studio UI: React shell + framework-independent PixiJS scene module
   └─ standalone HTML export · in-browser MP4 export
```

Layers, from the bottom up:

- **Core** (`simulatte`): environment, entities, event bus, RNG streams, trace recorder, KPI collectors, layout model. No web or network dependencies. Must stay usable exactly as today for scripts, research and RL training.
- **Execution** (`simulatte.execution`, *name deferred to SP4*): model entrypoint loading, worker process, worker protocol, coordinator, scheduler, run store.
- **Studio server** (`simulatte.studio`, optional extra `simulatte[studio]`): Starlette app, websocket protocol, static assets.
- **Frontend** (`studio/` TypeScript workspace in this repo): scene module, React shell, exporters. Built assets are shipped inside the wheel.

## 4. Sub-projects and releases

Sub-projects are implemented in order. Each one merges into `main` and ships as a 0.x release. Breaking changes are allowed in each (all APIs are unstable before 1.0). 1.0 is a stabilization release after SP5.

| # | Sub-project | Release | Contents |
|---|---|---|---|
| SP1 | Events and trace | 0.13 | Entity identity, event model, event bus, logger unification, RNG streams, trace recorder and reader (Python), KPI collector API, migration of existing collectors |
| SP2 | Layout | 0.14 | Layout model, grid, auto-layout, code API, `layout.json`, path network generators and overrides, binding and orphan detection, `LayoutGraph` generation |
| SP3 | Viewer | 0.15 | `studio/` workspace, trace reader (TS), PixiJS scene, playback, KPI panels, inspector, HTML and MP4 export, static `simulatte view trace` command |
| SP4 | Studio | 0.16 | Model entrypoint, worker and protocol (local), coordinator, studio server and websocket protocol, parameter forms, layout editor, file watching, `simulatte studio` and `simulatte run` |
| SP5 | Experiments | 0.17 | Experiment definition, replications, CIs, warm-up, run store, comparison UI, SSH workers and `simulatte workers setup` |

Dependencies: SP2 depends on SP1 (entity ids). SP3 depends on SP1 (trace) and SP2 (layout schema). SP4 depends on SP1–SP3. SP5 depends on SP4.

## 5. Contracts

These are the interfaces between sub-projects. Changing any of them after its sub-project ships requires amending this spec and bumping the relevant schema version.

### C1. Entities, events and the trace (SP1)

#### C1.1 Entity identity

- Every entity the viewer can draw or the trace refers to has `kind: str` and `id: str`, unique per environment. Built-in kinds include `server`, `psp`, `job`, `warehouse`, `agv`, `order`, `node`, `charging_station`, `parking_area`, `fleet`, `shopfloor`. User code can register new kinds.
- **Placeable entities** (those with a position in a layout: servers, PSPs, warehouses, stations, parking areas, AGVs, nodes) accept an optional `name`. When given, `id = name`. When omitted, `id = f"{kind}-{n}"` with `n` a per-kind counter in construction order. Duplicate ids raise at construction.
- **Transient entities** (jobs, orders) get `id = f"{kind}-{n}"` from a per-environment counter. `uuid4` ids are removed.
- Entities register with their environment at construction (`env.entities`). This registry is the source for snapshots and layout binding.
- Builders (`build_*_system`) assign meaningful default names.

#### C1.2 Events

- An event is an immutable record (frozen, slotted dataclass) with: `type` (stable string), `t` (simulation time), `seq` (per-environment monotonically increasing integer, total order for events at equal `t`), entity references as ids (never object references), and a payload of primitive values (numbers, strings, booleans, lists and mappings of these).
- Event types are registered in a catalog with a name, a schema version and a field list. The catalog is serializable, so the trace and the TypeScript side can validate and decode events generically.
- The core catalog covers at least: entity created and removed; job arrived, released, queued, operation started, operation completed, finished; server state changes; AGV state changes; **motion** (start, planned end, path or segment, interruption with position); order created, dispatched, picked up, delivered; inventory changes; charging start and end; traffic waits; KPI samples; log messages. The exact list is *deferred to SP1*.
- **Motion events carry their plan.** A motion event states the start time, planned arrival time and path, so the viewer interpolates position without per-step events. Any deviation (interruption, rerouting, stranding) emits a new event that supersedes the plan.
- User-defined entities and processes may define and emit their own event types through the same API. The viewer renders unknown kinds and events generically (§C7).

#### C1.3 Event bus

- `env.emit(event)` publishes; `env.bus.subscribe(handler, types=...)` subscribes, optionally filtered by type.
- **Subscribers are observers.** They run synchronously inside `emit`, must not schedule SimPy events, draw random numbers, or mutate simulation state. Recording, logging or computing KPIs must never change simulation results. Behavior-changing extension stays with the existing hook mechanisms (`OperationHook`, dispatchers, policies).
- **Zero-subscriber fast path.** Emitting sites guard event construction with a cheap check (for example `if env.tracing:`), so a model with no subscribers does not allocate event objects. Budget: with no subscribers, wall-clock overhead ≤ 3 % against the pre-SP1 baseline on the reference benchmarks, measured in CI on CPython and PyPy.
- Subscriber exceptions propagate (fail loudly); a subscriber that wants to be tolerant catches its own errors.

#### C1.4 Logging

- `SimLogger` becomes a set of bus subscribers (text, JSON, SQLite sinks). `env.info(...)`, `env.debug(...)` and friends remain and emit `log` events carrying the message, level, component and extra fields.
- The `EventHistoryBuffer` and SQLite query APIs are preserved in spirit; their exact shape is *deferred to SP1*. The loguru dependency may be dropped if it is no longer needed.

#### C1.5 Randomness

- Today all randomness goes through the module-level `random` generator, seeded by `Runner` with `random.seed(seed)`. This is replaced by **per-environment named RNG streams**: `env.rng(name)` returns an independent `random.Random` derived deterministically from the run seed and the stream name.
- Built-in components draw from named streams (for example arrivals, routing, processing times, AGV load times). Distributions accept a stream or default to a component-specific one.
- Named streams give **common random numbers** across configurations of an experiment (configuration A and B see the same arrival sequence for the same seed), which reduces variance in comparisons (SP5).
- The global `random` module is no longer seeded or used by the library. User code that still uses it is not reproducible through the studio, and the documentation says so.

#### C1.6 Determinism

- Given the same simulatte version, model source, parameters, physical layout (§C2.6), seed and platform, a run produces an identical event sequence and identical KPI values.
- Across platforms, results may differ because `math` functions come from the platform's libm. This is detected, not prevented: each run stores a **result fingerprint** (a hash over KPI scalars and the event count) and its host platform; any re-run compares fingerprints and reports a mismatch.

#### C1.7 Trace format

The encoding is *deferred to SP1*, under these fixed requirements:

- A single file with a **header**: format version, event catalog, entity registry at t=0, the **resolved layout** (from SP2 on; a trace is viewable without the model or `layout.json`), run metadata (simulatte version, model identifier and source hash, parameters, seed, layout full and physical hashes, horizon, warm-up, host platform, wall-clock start, recording level).
- The body is a sequence of **chunks** covering consecutive simulation-time windows. Each chunk starts with a **snapshot** of all entity states and is decodable on its own. An index maps time to chunk offsets. Seeking costs one snapshot load plus replay within a chunk.
- **Appendable while running.** A reader can open a trace that is still being written and see every complete chunk. A truncated final chunk (crash) is detected and skipped.
- Readable from Python and from TypeScript in the browser without native extensions. Compression is allowed if both sides support it without native code.
- Each entity kind provides a `snapshot()` of its viewer-relevant state. Snapshot contents are part of the catalog.
- KPI series are stored in the trace (as `kpi` samples or a separate section, *deferred to SP1*), so a single file is enough to view a run.
- **Recording levels:** `full` (events, snapshots, KPIs), `kpi` (KPI series and scalars only), `none`. Experiments default to `kpi` (§C5.4).
- Size target: a reference 10-server job shop over 50,000 jobs produces a `full` trace under 100 MB; the viewer can seek in it in under 200 ms on a 2021 laptop. Targets are verified in SP1 and SP3 with the reference model and may be renegotiated with evidence.

#### C1.8 KPIs

- A **KPI** is declared with a name, unit, kind (`series`, `scalar`, or both), aggregation and a short description, and is computed by a collector subscribed to the bus. KPIs are computed only in Python; the viewer never recomputes them.
- Collectors respect the warm-up period: scalar KPIs are computed over `[warmup, horizon]`; series cover the whole run and mark the warm-up boundary.
- Existing `MetricsCollector`, `TimeSeriesCollector`, `EMAMetricsCollector`, `DefaultTimeSeriesCollector`, `CurrentWorkLoadCollector` and the intralogistics collectors move onto the bus. Their matplotlib `plot_*` helpers stay for scripts.

### C2. Layout (SP2)

#### C2.1 World model

- **Continuous world coordinates** in meters: `(x, y, z)`, right-handed, `y` pointing up (north), `z` up from the floor and `0` in 1.0. The viewer converts to screen coordinates.
- A **placement** binds an entity id to: position, rotation (degrees about `z`), **footprint** (width, depth, height; rectangle in 1.0), **ports** (named points relative to the placement, where material enters or leaves, for example `in`, `out`, a warehouse's bays), and **visual** properties (shape, icon, color, label).
- Entities without a placement are positioned by auto-layout.

#### C2.2 Grid

- A layout has one **grid**: spacing `(dx, dy)` in meters and an origin. The grid is used for **snapping** (positions, ports, lane vertices snap to grid points) and for **network generation**. Entities keep their true footprint; only anchors snap.
- Snapping is on by default and can be disabled per placement for exact coordinates.

#### C2.3 Path network

- AGV movement uses `LayoutGraph` (nodes with coordinates, arcs). A layout's **network spec** produces it from one of:
  - **lattice:** one node per free grid point inside a bounding area, arcs to 4 or 8 neighbours; nodes and arcs that intersect a footprint or a blocked area are removed; each port connects to its nearest lattice node.
  - **lanes:** user-drawn polylines snapped to the grid; only lanes become arcs; lane intersections become nodes.
  - **explicit:** a hand-built `LayoutGraph` (today's API), which remains supported.
- Arcs carry attributes: `enabled`, direction (`both`, `forward`, `backward`), `speed_limit` (optional), and capacity-related attributes used by traffic managers (*deferred to SP2*).
- **Overrides** modify the generated network: block or unblock areas, disable or enable arcs and nodes, set direction and speed limits on single arcs or on selections (a grid row or column, a lane, a rectangle).

#### C2.4 Layout sources and precedence

1. **Auto-layout** (lowest): a deterministic layout from the model's structure (for production, a layered flow layout from routings; algorithm *deferred to SP2*). Intralogistics nodes use their own coordinates.
2. **Code**: the model's `Layout` object (placements, grid, network spec, overrides).
3. **File** (highest): `layout.json`, written by the studio editor.

Precedence is resolved **per property**: dragging a server in the studio overrides its position but not a color set in code. Resolution is deterministic and its result (the **resolved layout**) is what the run uses and what the trace header hashes.

#### C2.5 `layout.json`

- Versioned schema, published as JSON Schema. Stable formatting (sorted keys, fixed float precision) so diffs are readable in version control.
- Contains only overrides relative to code, keyed by entity id. A model can load it explicitly (`Layout.load(path)`) or the studio passes it to `build`.
- **Orphans:** entries whose id matches no entity, or matches an entity of a different kind, are reported and not applied. The studio lists them and offers to reassign or delete them. Dragging an entity whose id was auto-generated triggers a warning suggesting a `name` in code.

#### C2.6 Physical versus presentation properties

- **Physical** properties change simulation results: the path network and its attributes, node and port positions used by intralogistics distances, and anything else a model reads from the layout.
- **Presentation** properties do not: icons, colors, labels, positions of entities whose position the model does not read.
- The trace header records two hashes: the **full layout hash** and the **physical layout hash**. Experiment results remain valid across presentation-only edits; a change of physical hash marks results as stale.
- Whether a property is physical is declared in the layout schema, not inferred.
- Entities positioned only by auto-layout are presentation-only by construction: auto-layout never feeds the path network or any physical property.

#### C2.7 Layout lifecycle during a run

1. The caller (studio, CLI or script) creates a `Layout`, loads `layout.json` overrides if any, and passes it to `build`.
2. Inside `build`, model code declares the grid, network spec, placements and overrides (the code layer). File overrides are already present and win per property.
3. The first call that needs physical data (for example `layout.graph()` to construct a fleet) **freezes the physical part** of the layout: the network and every physical property are resolved then. Later attempts to change a physical property raise.
4. After `build` returns, presentation properties and auto-layout are resolved and the whole layout is frozen. The resolved layout is written to the trace header and hashed.

Models that never call physical accessors (pure production models) have presentation-only layouts and a constant physical hash.

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


@simulatte.model(params=Params, horizon=10_000, warmup=1_000)
def build(env: Environment, layout: Layout, params: Params) -> None:
    ...  # construct entities; they register on env
```

- `@simulatte.model` attaches metadata (parameter type, horizon, default warm-up, optional default KPI selection) and returns the function unchanged, so the model is still callable from plain scripts and tests.
- Supported parameter field types: `bool`, `int`, `float`, `str`, `Literal[...]`, `Enum`, each optionally `Annotated` with constraints (`Range`, `Choices`, step) and a description. The studio generates forms from them; the CLI accepts them as `--param name=value`.
- `build` must not run the simulation; the caller runs `env.run(until=horizon)`. The seed is set on the `Environment` (`Environment(seed=...)`) before `build` is called, so all RNG streams derive from it.
- A model file may define one decorated model, or several selected by name (`simulatte studio plant.py:build`).
- `Runner` and the builders keep working without the decorator for code-first use.

### C4. Worker protocol (SP4, remote transport in SP5)

- A worker is `simulatte worker --stdio`, a long-lived process that executes **run requests** one at a time per process. Parallelism comes from multiple worker processes.
- Messages are length-prefixed binary frames on stdin and stdout (stderr is free-form diagnostics). Encoding *deferred to SP4*; it must be implementable with the standard library or a pure-Python dependency.
- Messages at minimum: `hello` (protocol version, simulatte version, platform, capabilities), `run` (model reference, source bundle hash, parameters, seed, layout, horizon, warm-up, recording level, run id), `cancel`, `progress` (simulated time, event count), `trace_chunk`, `kpi`, `result` (scalars, fingerprint), `error` (exception type, message, traceback), `heartbeat`, `shutdown`.
- Version negotiation in `hello`: incompatible coordinator and worker versions refuse to run with an explicit error.
- **Local transport:** a subprocess of the coordinator. **SSH transport:** `ssh <host> <remote command>` started by the coordinator using the system `ssh` client and the user's SSH configuration; no extra ports, no daemon, no assumptions about the network.
- Each run executes in a fresh interpreter state for the model: the worker loads the model source anew per source hash and never relies on `importlib.reload`. Whether one process serves several runs of the same source hash is an optimization *deferred to SP4*.

### C5. Coordinator, run store and experiments (SP4, SP5)

#### C5.1 Coordinator

- The coordinator runs inside the studio server or the headless CLI. It owns the scheduler, the run store and the worker pool. The studio server talks to it in-process.
- The **scheduler** sees workers as slots. Run requests are idempotent by `(experiment config, seed)`; a lost worker's in-flight runs are rescheduled; duplicate results are ignored.

#### C5.2 Remote environments

- Remote workers require: non-interactive SSH access, a POSIX shell (Linux or macOS), and **uv**. Remote hosts do not need Python; uv provides it.
- For remote runs the model must live in a **uv project** (`pyproject.toml` and `uv.lock`). Per run, the coordinator builds a **source bundle** (project files, respecting `.gitignore`, plus the lockfile), identified by content hash. The worker caches bundles by hash and runs `uv sync --frozen` in an isolated environment.
- `simulatte workers setup <host>` checks connectivity, platform and uv, shows what it will change, and installs uv on request. Runs never install software on a host implicitly; a missing uv fails with a message pointing to this command.
- Worker hosts are listed explicitly in a config file (location and format *deferred to SP5*). There is no discovery.
- Local-only use (studio, local replications) requires neither uv nor a uv project.

#### C5.3 Run store

- Per project directory, `.simulatte/`: a SQLite index of experiments, configurations, runs (seed, host, platform, status, fingerprint, timings) and KPI scalars, plus trace and KPI files per run.
- The store is append-mostly. Schema is versioned and migrated forward. It is safe to delete; deleting it loses history, not models.

#### C5.4 Experiments

- An **experiment** is a set of configurations (parameter grid or explicit list, optionally including layout variants) × N seeds, with a warm-up and a horizon. Experiments can be defined in Python, in a file, or in the studio.
- Replications record at level `kpi` by default. Any replication can be **re-run at level `full`** from its stored seed for animation; the re-run's fingerprint is compared to the stored one (§C1.6). The coordinator prefers the original host or platform for re-runs.
- Statistics: per-configuration means, standard deviations, confidence intervals (t-based, configurable level), paired comparisons between configurations using common random numbers, and warm-up deletion with a user-set warm-up. Warm-up estimation aids (for example a Welch plot) are *deferred to SP5*.
- Comparison views: KPI table with intervals across configurations, overlaid series, and two runs animated side by side on a synchronized clock.

### C6. Studio server and websocket protocol (SP4)

- `simulatte studio [model]` starts a Starlette server bound to `127.0.0.1` on a free port, prints and opens a URL carrying a random **session token**. Every HTTP request and websocket connection must present the token. Origin headers are checked. Binding to other interfaces requires an explicit flag and a warning.
- One websocket per browser tab carries JSON messages for: model info and parameter schema, run control (start, cancel), run progress, trace chunk availability (chunks are fetched over HTTP by range), KPI updates, layout read and write, layout validation results and orphans, experiment control and results, file-change notifications, errors.
- The protocol is versioned. The UI shipped in a wheel always matches its server; no cross-version compatibility is required between UI and server.
- **File watching:** changes to the model source or `layout.json` trigger a re-run of the current configuration, debounced, cancelling the previous run.

### C7. Viewer (SP3)

- The **scene module** is framework-independent TypeScript: given a resolved layout, a trace reader and a playback time `t`, it renders deterministically (`render(t)`), with no hidden dependence on wall-clock time. It owns the PixiJS application and its ticker.
- The **React shell** hosts the scene and the panels. **Rule:** anything bound to the playback clock (timeline position, KPI tiles, live charts, inspector numeric fields) is updated outside React's render cycle, by imperative writes from the scene ticker through refs or narrow external-store subscriptions. Only discrete events (selection, seek end, layout edits, undo and redo, run and experiment state) pass through React state. React Compiler is enabled.
- Unknown entity kinds render as a generic shape with their id; unknown event types appear in the event log and the inspector.
- Production job movement between servers is drawn as a short animated transfer that takes no simulated time (N4).
- **Exports:** MP4 rendered in the browser by stepping `render(t)` frame by frame and encoding with WebCodecs, no server involvement; self-contained HTML containing the scene, a read-only player and an embedded trace (size limits *deferred to SP3*).
- The viewer works in three modes: inside the studio (live, editable), as `simulatte view run.trace` (static server, read-only), and as an exported HTML file (no server).

## 6. Packaging and repository layout

- `pip install simulatte`: core only. No web, network or frontend dependencies are added to the core.
- `pip install "simulatte[studio]"`: studio server dependencies (Starlette, an ASGI server, a file watcher). The frontend is prebuilt and shipped as static files inside the wheel; users never need Node.
- `studio/`: TypeScript workspace (Vite, React, PixiJS, Vitest, Playwright). Its build output is copied into `src/simulatte/studio/static/` at wheel build time; CI builds and tests it.
- Internal design documents live in `specs/` and `plans/` at the repository root (already excluded from the sdist), not in `docs/`, which is the published website.

## 7. Compatibility and migration

- Breaking changes, by sub-project:
  - SP1: logger API reshaped; `Environment(seed=...)`; `Server(name=...)` and stable ids; job and AGV ids deterministic instead of `uuid4`; `Runner` seeds environments instead of the global `random`; distributions and router draw from named streams; collectors move onto the bus.
  - SP2: intralogistics systems can take their `LayoutGraph` from a `Layout`; the existing explicit graph API remains.
  - SP4: the `@simulatte.model` entrypoint is new and optional for code-first use.
- Each release's `CHANGELOG.md` entry includes a migration section. Examples and docs are updated in the same release.
- The PyPy lane stays green for the core, the worker and the trace recorder. The studio server targets CPython only.

## 8. Testing strategy

- **Core:** unit tests for ids, bus, RNG streams, recorder and reader round-trips; determinism tests (same seed, same trace bytes); golden traces for reference models, regenerated deliberately.
- **Overhead:** a benchmark suite comparing zero-subscriber runs with the pre-SP1 baseline, run in CI with a tolerance band; regressions fail CI.
- **Layout:** property tests for precedence resolution, network generation (connectivity, blocked areas, one-way arcs), orphan detection and schema round-trips.
- **Cross-language:** the Python trace writer and the TypeScript reader are tested against the same fixture traces.
- **Frontend:** Vitest for the scene module and stores; Playwright end-to-end tests against a running studio with a small model, including seek, export and a layout edit triggering a re-run.
- **Workers:** protocol tests with a fake transport; local subprocess integration tests; SSH transport tested against `localhost` sshd in CI where available, otherwise marked and run manually.
- **Experiments:** statistical tests against known analytic results (for example M/M/1 queue means within intervals).

## 9. Cross-cutting rules

### 9.1 Observability without interference

Nothing that observes a run (recording, logging, KPIs, streaming, the studio) may change its results. §C1.3 states the rule for subscribers; workers and the coordinator follow it too.

### 9.2 Reproducibility

A run is identified by its header: simulatte version, model source hash, parameters, seed, physical layout hash, horizon, warm-up, platform. The studio and the CLI can reproduce any stored run from it, and report when they cannot (missing source, changed version, fingerprint mismatch).

### 9.3 Generic by default

No assumptions about network topology, VPNs, host names, operating system setup or user tooling beyond the stated requirements: SSH and uv for remote workers; a modern evergreen browser for the studio (WebGL2; WebCodecs for MP4 export).

### 9.4 3D-readiness

Coordinates are `(x, y, z)` everywhere; footprints have a height; entity visuals are described by kind, footprint and visual properties rather than by 2D sprites. A future 3D renderer consumes the same trace and layout.

### 9.5 Security

The studio executes user code and is reachable only from the local machine with a session token (§C6). Remote execution uses the user's own SSH configuration and credentials; Simulatte stores no credentials. Source bundles are sent only to hosts the user listed.

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Event emission slows simulations | Research and RL users regress | Zero-subscriber fast path; CI benchmark with a 3 % budget (§C1.3) |
| Trace size or seek time too large for long runs | Viewer unusable for real studies | Chunked format with snapshots; size and seek targets verified early in SP1 and SP3 (§C1.7) |
| Cross-platform float differences | Re-run for animation diverges from the stored replication | Fingerprints, platform recorded, prefer original host (§C1.6, §C5.4) |
| User code uses global `random` or other nondeterminism | Irreproducible runs, CRN broken | Named streams; fingerprint mismatch detection; documentation |
| Scope (five sub-projects, two languages) | Long delivery, partial abandonment | Each sub-project ships standalone value as a 0.x release; spec and plan reviews per sub-project |
| Layout file drifts from model code | Overrides applied to the wrong entity | Stable names, kind check, orphan reporting (§C2.5) |
| Remote environment mismatch | Wrong or failed results | Source bundles by hash, `uv sync --frozen`, version negotiation (§C4, §C5.2) |
| Frontend re-render storms at 60 Hz | Choppy playback | Clock-bound panels outside React (§C7) |
| Maintenance burden of a TypeScript frontend in a Python library | Contributors cannot work on it | Separate `studio/` workspace with its own tests and docs; framework-independent scene module |

## 11. Open questions for sub-project specs

- SP1: trace encoding and compression; exact event catalog; snapshot frequency policy; shape of the history and SQLite query APIs; whether loguru stays.
- SP2: auto-layout algorithm; traffic capacity attributes on arcs; lane intersection rules; how intralogistics builders consume a `Layout`.
- SP3: chart library (uPlot is the default candidate); docking layout library (dockview is the default candidate); HTML export size limits; icon set and visual style.
- SP4: worker frame encoding; process reuse per source hash; module names; file watcher library.
- SP5: worker host config format and location; warm-up aids; experiment file format; store retention policy.
