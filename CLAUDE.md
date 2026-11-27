# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## API Stability

Simulatte is under active development. All APIs — including those outside `simulatte.experimental` — should be considered unstable and may introduce breaking changes between releases without prior deprecation.

## Build and Development Commands

```bash
# Setup
uv sync --dev --all-extras
uv run --all-extras pre-commit install

# Tests
uv run --all-extras pytest

# Docs
uv run --all-extras zensical build
uv run --all-extras zensical serve
```

## Repository Structure

```
simulatte/
├── src/simulatte/          # Main package
│   ├── __init__.py         # Stable entry points (Environment, Runner, Provenance, TraceRecorder, Trace, KPI, Collector, event base classes)
│   ├── environment.py      # SimPy environment wrapper: event bus, entities, RNG streams, activation, log sinks
│   ├── events.py           # Event base classes, @event_type catalog, deltas, EventBus
│   ├── entities.py         # Entity base, registry, ids, state schemas, lifecycle
│   ├── rng.py              # RNG stream derivation and sampler binding
│   ├── digest.py           # Semantic projection and digest
│   ├── provenance.py       # Provenance and run manifest
│   ├── kpi.py              # KPI declarations, Collector base class, observation windows
│   ├── collectors.py       # Built-in production collectors (EMA, time series, KPIs)
│   ├── logsinks.py         # Text, JSON, SQLite and history log sinks
│   ├── trace/              # Trace container: writer (TraceRecorder) and reader (Trace)
│   ├── scenario.py         # Scenario definitions
│   ├── shopfloor.py        # Central orchestrator
│   ├── job.py              # BaseJob and ProductionJob
│   ├── server.py           # Processing resources
│   ├── psp.py              # Pre-shop pool
│   ├── router.py           # Job routing logic
│   ├── dispatching_rules/  # Dispatching rule implementations
│   ├── runner.py           # Multi-simulation execution
│   ├── statistics.py       # Replication-level t intervals, Bonferroni, paired differences, Welch averaging (pure Python)
│   ├── experiments/        # experiment-v1 definitions and JSON schema, expansion, run(), per-run reports (results.py)
│   ├── execution/          # Managed execution: coordinator, store, supervisor, child; SP5 adds the pieces below
│   │   ├── scheduler.py, slots.py            # Experiment dispatch over K slots; local slot default
│   │   ├── ssh.py, bootstrap_remote.py       # SSH transport and POSIX bootstrap scripts for remote hosts
│   │   ├── staging.py, provisioning.py       # Hash-verified staging and uv environment provisioning (coordinator and host sides)
│   │   ├── preflight.py                      # Local checks before a project runs remotely
│   │   ├── replay.py                         # Full-level replay verification
│   │   └── workers.py, workers_cli.py        # workers.toml and `simulatte workers list|check|setup|prune`
│   ├── studio/             # Optional Studio server; experiments.py holds the experiment, comparison and replay commands
│   ├── layout/             # Layout of every environment: placement (facilities at their nodes), generated transport networks, stages, bundle and hashes
│   ├── builders.py         # Factory functions for system setup
│   ├── distributions.py    # Statistical distributions
│   ├── typing.py           # Shared type definitions
│   ├── policies/           # Release policies and triggers
│   │   ├── draco.py        # DRACO policy
│   │   ├── norms.py        # Workload norm definitions
│   │   └── slar_limit.py   # SLAR-Limit policy
│   ├── intralogistics/     # Warehouse, AGV fleet, material transport (graph, routing, traffic control, motion, fleet coordinator, metrics, policies)
│   └── experimental/       # Unstable modules (gymnasium wrapper)
├── studio/                 # TypeScript workspace (pnpm): trace reader, playback, scene (the map), export, viewer app (experiments, results, compare panels, Overview panel) and offline player; harness/ (render harness), fixtures/map/ (frame fixtures), bench/, browser/ (Playwright specs and screenshot baselines)
├── benchmarks/             # Overhead benchmarks against simulatte 0.12.0 (CI bench job); layout_overhead.py measures what a layout costs
├── examples/               # Runnable example scripts (intralogistics_simple, _intermediate, _advanced, _traffic)
├── tests/
│   ├── core/               # Tests for stable modules
│   ├── execution/          # Tests for managed execution, experiments' scheduler, workers (fake ssh and uv are POSIX-only)
│   ├── intralogistics/     # Tests for intralogistics modules; random_worlds.py draws random worlds built through the layout, with an independent monitor
│   ├── layout/             # Tests for the layout package and the documented layout examples
│   └── experimental/       # Tests for experimental modules
├── docs/                   # Website sources (simulatte.dev), built with Zensical
├── overrides/              # MkDocs theme overrides
├── pyproject.toml          # Project metadata, tool config
└── zensical.toml           # Documentation site config
```

## Architecture Overview

Simulatte is a discrete-event simulation framework for production planning and control and intralogistics, built on SimPy.

### Core Components

**Environment** (`src/simulatte/environment.py`): SimPy wrapper that carries the event bus (`env.bus`, `env.emit`, `env.wants`), the entity registry (`env.entities`), named RNG streams (`env.rng`, `env.bind`, seeded by `Environment(seed=)`), activation (`env.activate()`, `env.on_activate`; `env.run()` activates on first use, manual `env.step()` needs `env.activate()` first), the semantic digest and run manifest (`env.enable_digest()`, `env.fingerprint()`, `env.manifest()`), KPI collectors (`env.collectors`, `env.configure_kpis`) and per-environment logging through sinks (`simulatte.logsinks`: text/JSON/SQLite/history). Supports a context manager that closes trace recorders and sinks.

**Layout** (`src/simulatte/layout/`): every environment has one layout, `env.layout`, created on first read or at activation (`Layout(env, file=, limits=)` only to give it a file layer or limits, before the first read). It positions entities, holds the transport network and is written to every trace and manifest. A network is declared once with `env.layout.set_network(Explicit(graph) | Lattice(...) | Lanes(...))` before activation (the last declaration before preparation wins); components take handles only (`layout.graph()`, `layout.node(id)`, `layout.node_at(address)`, `layout.port(entity, name)`), and a concrete `LayoutGraph` or `Node` raises `LayoutError`. Physical fields (`agv.current_node`, `fleet.graph`, bays) raise `LayoutNotReady` until `env.layout.prepare()` or `env.activate()` and then hold canonical nodes: compare them with `==`. `import simulatte` imports neither the layout nor the intralogistics graph. A warehouse, parking area or charging station without an explicit position is placed at the node it is bound to (`layout/auto.py`: beside its first input bay and away from the network for a warehouse, centred with a 3 x 3 m footprint for the other two); a vehicle's default footprint is its type's `size`. Both read inputs saved in the bundle's `targets` (`size`, `node_ids`), and a bundle that has one is named `layered-v2` (hashes differ), every other `layered-v1`; both readers accept both.

**Events and entities** (`events.py`, `entities.py`): every state change is a typed event (`DomainEvent` with deltas, `ObserverEvent` for logs and KPI samples) registered with `@event_type` and emitted behind `if env.wants(Cls): env.emit(Cls(...))`; event construction must call no user code. Components are entities with ids (`job-<n>`, `server-<n>` or `name=`, builder names `wc-<i>`, `shopfloor`, `router`, `psp` plus `prefix=`). Observers never change results (see `tests/core/test_invariance.py`).

**Trace** (`src/simulatte/trace/`): `TraceRecorder` writes a run to a chunked, checksummed file (levels `full` and `kpi`); `Trace` reads, seeks (`state_at`), verifies (`verify`) and checks (`check`) it. `studio/packages/trace` is the TypeScript reader.

**Studio map** (`studio/packages/scene`, `studio/apps/viewer/src/overview-panel.ts`): for a trace with a network the scene's `Projector` builds a pure `MapModel` (`src/map/`: floor strips, vehicles with shown state, facilities, wait lines, orders, traffic summary) from the frame alone, and `src/draw/` draws one sprite per kind at one of two levels chosen by the zoom (`detailAt`: symbols of fixed pixel size, or true footprints). The static part of the model is computed once per layout object. The Overview panel and the click detail (`describe`, `renderDetail`) are built from the same model. Traces without a network keep today's glyphs (`registerRenderer` still overrides a kind). `studio/harness` renders fixture frames (`node studio/harness/render.mjs <out-dir>`, out-dir outside `studio/`); `browser/map.spec.ts` compares seven canvases with Linux Chromium baselines (`browser/README-baselines.md`); `studio/bench` measures the frame time on a GPU-backed renderer.

**Experiments and remote workers** (`experiments/`, `execution/`, `statistics.py`): an experiment (`*.experiment.json`, the `simulatte.experiments` API or a Studio form) expands configurations x replications into ordinary execution requests, seed `base_seed + i` shared across configurations. The coordinator owns K slots (`interactive`, `local-<n>`, `<worker>-<n>`), each with one transport; the scheduler dispatches replication-major, re-dispatches `lost` attempts at most twice and never retries `failed`. Results are replication-level scalars; `statistics.py` and `experiments/results.py` produce t intervals (pointwise, or Bonferroni per family) and paired differences by replication index. `simulatte replay` reruns a request at `full` level and labels it `verified` or `unverified`. Remote workers are POSIX hosts named in the user-level `workers.toml` (no credentials), reached over system `ssh`; the project must be a uv project, `preflight.py` checks it locally, `staging.py` ships hash-verified bundles and `provisioning.py` builds immutable cached environments with `uv sync --locked`. uv is installed only by a confirmed `workers setup --install-uv`. The store (`execution/store.py`, schema 2) holds slots, experiments, replications and replays; retention is manual (`experiment delete`, `store prune`).

**ShopFloor** (`src/simulatte/shopfloor.py`): Central orchestrator managing job flow through the simulation. Tracks WIP, coordinates routing, attaches an `EMACollector` as `shopfloor.metrics` (`default_metrics=False` to opt out). Extensible via:
- `OperationHook`: Sync or generator-based hooks for before/after operations
- `WIPStrategy`: Pluggable WIP calculation (StandardWIPStrategy, CorrectedWIPStrategy)
- `Collector` subclasses (`simulatte.kpi`, built-ins in `simulatte.collectors`): bus subscribers bound to the shop floor with `collector.attach(env)`
- `Dispatcher`: Protocol for one-call hook wiring via `attach_dispatcher()`

**BaseJob/ProductionJob** (`src/simulatte/job.py`): `BaseJob` defines the common job interface and state; `ProductionJob` represents manufacturing jobs with routing, processing times, due dates, and optional material requirements.

**Server** (`src/simulatte/server.py`): Processing resource extending `simpy.PriorityResource`. Tracks queue times, utilization.

**Policies** (`src/simulatte/policies/`): Release policies for job scheduling:
- LumsCor: Load-based scheduling
- SLAR: Server load adjustment rule
- SlarLimit: SLAR with a workload limit
- Draco: Non-hierarchical WIP control
- ConWIP: Constant Work-In-Process release (shop-wide job count cap)
- ContinuousRelease: Workload-controlled continuous release (corrected aggregate load norms)
- `starvation_avoidance`: Callback for `psp.on_arrival()` that releases jobs when first server is idle

### Supporting Modules

- **Router** (`router.py`): Job routing logic through server sequences
- **Runner** (`runner.py`): Multi-simulation execution with seed management
- **PSP** (`psp.py`): Pre-shop pool for job release control
- **Builders** (`builders.py`): Factory functions (`build_immediate_release_system`, `build_lumscor_system`, `build_slar_system`), each with `scenario=None` and `prefix=""`
- **Distributions** (`distributions.py`): Statistical distribution descriptions (`sampler(rng)`), bound to named RNG streams by `env.bind`; plain callables are accepted but opaque (the run manifest becomes incomplete)
- **Triggers** (`policies/triggers.py`): Event-driven triggers for release policies
- **ConWIP** (`policies/conwip.py`): Constant WIP release policy with EDD selection
- **ContinuousRelease** (`policies/continuous_release.py`): Workload-controlled continuous release

### Intralogistics (`intralogistics/`)

Warehouse-to-warehouse material transport via AGV fleets:

- **LayoutGraph** (`intralogistics/graph.py`): Directed graph of `Node`/`Arc` (`speed_limit`, travelled `length`, `passing`); `length`, `path_length`, `subdivide(max_length)`. Written by hand, it is declared with `Explicit(graph)`; the graph a model runs on is the layout's resolved, read-only one
- **Warehouse** (`intralogistics/warehouse.py`): Per-SKU inventory, finite pick/put slots, input/output bays
- **AGV** (`intralogistics/agv.py`): State machine (IDLE, TRAVELING_EMPTY/LOADED, WAITING_LOAD/UNLOAD, CHARGING, STRANDED) with battery and speed profile; `AGVType` carries `length`/`min_gap` (required by `ReservationController`), `width` (keyword-only, descriptive; `AGVType.size` is `(length, width)` and none for a length of 0 or None), `corner_speed`, `turn_time` and `idle_power` (lazy; `AGV.battery_level_now()` is the pure read). Its state also holds `wait` and `route` (set by `traffic.wait_started`/`agv.routed`, cleared by `traffic.wait_ended` and the event that ends the travel, kept without subscribers) and the constant `battery_capacity`
- **Speed and kinematics** (`speed.py`, `kinematics.py`): `SpeedProfile`, `KinematicProfile.limits` -> `Kinematics`, `TrapezoidalProfile`; the pure run planner (`plan_run`, constant-acceleration phases, the `piecewise` motion curve)
- **FleetCoordinator** (`intralogistics/fleet.py`): Central orchestrator for AGV missions — dispatch, travel, pick, deliver, reposition, charge, make way (`make_way`), clearing service nodes, parking slots, `best_bay`. Pluggable strategies for dispatch, repositioning, replenishment, and load recovery. Its state counts `waits`, `stuck_waits` and `deadlocks` from the start of the run (a deadlock once per distinct fleet; a vehicle with no fleet counts nowhere)
- **Travel** (`intralogistics/travel.py`): the travel executor: routes, claims with look-ahead, waits, runs through nodes, turns, energy horizon, stop requests, outcomes (`ARRIVED`, `RETRY_FROM_CURRENT_POSITION`, `BATTERY_STRANDED`, `NO_ROUTE`, `GAVE_UP`), the standard search for idle targets
- **Traffic** (`traffic.py`, `network.py`, `reservation.py`, `traffic_holdings.py`, `traffic_view.py`, `traffic_entities.py`, `traffic_policy.py`): the `TrafficController`/`TrafficView`/`TrafficPolicy` protocols, reactions and value types and `FreeFlowController` (default) in `traffic.py`; `TrafficNetwork` (capacities, segments, `Zone`s, derived corridors and dead-end branches, claims) in `network.py`; `ReservationController` (queue, fairness gate, grants, waits, reviews, deadlock detection, make-way, stuck waits) with its holdings ledger (`controller.ledger`, also `mean_hold`), view and `segment`/`zone` entities; `StandardTrafficPolicy`
- **Routing** (`intralogistics/routing.py`): `Router`, `RouteRequest`, `Route`; `ShortestDistanceRouter` (default), `FastestRouter`, `CongestionAwareRouter` (heuristic); costs are comparable only within one router; the shared validation of limits and turning values
- **Policies** (`intralogistics/policies.py`): `DispatchContext` (`select(order, fleet, context)`), `RepositioningContext` (`router`, `view`, `cost`), `NearestIdleStrategy`, `RoundRobinStrategy`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`; rankings by route cost (`ranking_route`)
- **Metrics** (`intralogistics/metrics.py`): bus collectors scoped to a fleet: `OrderEMACollector` (default `coordinator.metrics`: fulfillment time, dispatch delay, travel times), `FleetTimeSeries` (`plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`), `FleetKPIs` and `TrafficKPIs`
- **Events** (`intralogistics/events.py`): fleet, order, AGV (including `agv.routed`), traffic (`traffic.reserved`/`released`/`wait_started`/`wait_ended`/`wait_stuck`/`deadlock`), warehouse, charging and parking events; `_resources.py` holds SimPy resources that report their changes
- **Facilities**: `ChargingStation` (`charging.py`, state constant `slots`), `ParkingArea` (`parking.py`, state constant `capacity`)
- **Builders** (`intralogistics/builders.py`): `build_simple_system(env)` for quick setup, on `env.layout`; returns a `GraphRef`, AGVs named `vehicle-<i>`

### Experimental Modules (`experimental/`)

Unstable APIs, subject to change:

- **SimulatteEnv** (`experimental/gymnasium.py`): Gymnasium ABC for wrapping simulations as RL environments. Users subclass it and implement six abstract methods (setup, get_observation, apply_action, compute_reward, is_terminated, is_truncated). Two optional hooks: `teardown()` for resource cleanup between episodes, `get_info()` for step metadata. Base class handles reset/step/close lifecycle and state guards.


## CI/CD

GitHub Actions workflows live in `.github/workflows/`:

- **ci.yml**: CPython 3.11/3.12/3.13/3.14/3.15, plus a PyPy 3.11 compatibility lane (core, execution, intralogistics, layout); lint and type checks run on CPython 3.14. An `execution-native` lane runs the execution, statistics, experiment and Studio tests on macOS and Windows, a two-slot local experiment through the CLI, and on Windows asserts that `workers check` reports SSH workers as untested (the fake-ssh, staging and provisioning tests are POSIX-only and skip on Windows). A `trace-ts` job tests the TypeScript workspace, browser specs (the map screenshot comparison runs on Linux Chromium against baselines made on arm64, unverified on x86_64) and packaged wheel, and a `bench` job gates the no-subscriber overhead against `simulatte==0.12.0`.
- **docs.yml**: Builds and deploys documentation to GitHub Pages on push to `main`.
- **publish.yml**: Publishes to PyPI via trusted publishing when a `v*` tag is pushed.

## Contributing

See `CONTRIBUTING.md` for the full workflow. Key rules: branch from `main` as `feature/<name>` or `fix/<name>`, open a PR, all checks must pass, squash-merge by maintainer only. Update `docs/` when adding or changing functionality.

## Documentation

The `docs/` folder contains the sources for the official website at [simulatte.dev](https://simulatte.dev), built with [Zensical](https://github.com/dmezzogori/zensical) (MkDocs-based). Configuration lives in `zensical.toml`; theme overrides in `overrides/`.
