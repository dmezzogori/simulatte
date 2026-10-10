# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## API Stability

Simulatte is under active development. All APIs — including those outside `simulatte.experimental` — should be considered unstable and may introduce breaking changes between releases without prior deprecation.

## Build and Development Commands

```bash
# Setup
uv sync --dev
uv run pre-commit install

# Tests
uv run pytest

# Docs
uv run zensical build
uv run zensical serve
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
│   ├── builders.py         # Factory functions for system setup
│   ├── distributions.py    # Statistical distributions
│   ├── typing.py           # Shared type definitions
│   ├── policies/           # Release policies and triggers
│   │   ├── draco.py        # DRACO policy
│   │   ├── norms.py        # Workload norm definitions
│   │   └── slar_limit.py   # SLAR-Limit policy
│   ├── intralogistics/     # Warehouse, AGV fleet, material transport (graph, pathfinding, traffic, fleet coordinator, metrics, policies)
│   └── experimental/       # Unstable modules (gymnasium wrapper)
├── studio/                 # TypeScript workspace: @simulatte/trace conformance reader (pnpm)
├── benchmarks/             # Overhead benchmarks against simulatte 0.12.0 (CI bench job)
├── examples/               # Runnable example scripts (intralogistics_simple, _intermediate, _advanced)
├── tests/
│   ├── core/               # Tests for stable modules
│   ├── intralogistics/     # Tests for intralogistics modules
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

**Events and entities** (`events.py`, `entities.py`): every state change is a typed event (`DomainEvent` with deltas, `ObserverEvent` for logs and KPI samples) registered with `@event_type` and emitted behind `if env.wants(Cls): env.emit(Cls(...))`; event construction must call no user code. Components are entities with ids (`job-<n>`, `server-<n>` or `name=`, builder names `wc-<i>`, `shopfloor`, `router`, `psp` plus `prefix=`). Observers never change results (see `tests/core/test_invariance.py`).

**Trace** (`src/simulatte/trace/`): `TraceRecorder` writes a run to a chunked, checksummed file (levels `full` and `kpi`); `Trace` reads, seeks (`state_at`), verifies (`verify`) and checks (`check`) it. `studio/packages/trace` is the TypeScript reader.

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

- **LayoutGraph** (`intralogistics/graph.py`): Directed graph of `Node`/`Arc` with Dijkstra and A* pathfinding
- **Warehouse** (`intralogistics/warehouse.py`): Per-SKU inventory, finite pick/put slots, input/output bays
- **AGV** (`intralogistics/agv.py`): State machine (IDLE, TRAVELING_EMPTY/LOADED, WAITING_LOAD/UNLOAD, CHARGING, STRANDED) with battery and speed profile
- **FleetCoordinator** (`intralogistics/fleet.py`): Central orchestrator for AGV missions — dispatch, travel, pick, deliver, reposition, charge. Pluggable strategies for dispatch, repositioning, replenishment, and load recovery
- **Policies** (`intralogistics/policies.py`): `NearestIdleStrategy`, `RoundRobinStrategy`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`
- **Metrics** (`intralogistics/metrics.py`): bus collectors scoped to a fleet: `OrderEMACollector` (default `coordinator.metrics`: fulfillment time, dispatch delay, travel times), `FleetTimeSeries` (`plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`) and `FleetKPIs`
- **Events** (`intralogistics/events.py`): fleet, order, AGV, traffic, warehouse, charging and parking events
- **Traffic** (`intralogistics/traffic.py`): `ResourceBasedTrafficManager` with node capacity enforcement and deadlock resolution
- **Facilities**: `ChargingStation` (`charging.py`), `ParkingArea` (`parking.py`)
- **Builders** (`intralogistics/builders.py`): `build_simple_system()` for quick setup

### Experimental Modules (`experimental/`)

Unstable APIs, subject to change:

- **SimulatteEnv** (`experimental/gymnasium.py`): Gymnasium ABC for wrapping simulations as RL environments. Users subclass it and implement six abstract methods (setup, get_observation, apply_action, compute_reward, is_terminated, is_truncated). Two optional hooks: `teardown()` for resource cleanup between episodes, `get_info()` for step metadata. Base class handles reset/step/close lifecycle and state guards.


## CI/CD

GitHub Actions workflows live in `.github/workflows/`:

- **ci.yml**: CPython 3.12/3.13/3.14, an allowed-to-fail CPython 3.15 pre-release lane, plus a PyPy 3.11 core/intralogistics compatibility lane; lint and type checks run on CPython 3.14. A `trace-ts` job tests the TypeScript trace reader and a `bench` job gates the no-subscriber overhead against `simulatte==0.12.0`.
- **docs.yml**: Builds and deploys documentation to GitHub Pages on push to `main`.
- **publish.yml**: Publishes to PyPI via trusted publishing when a `v*` tag is pushed.

## Contributing

See `CONTRIBUTING.md` for the full workflow. Key rules: branch from `main` as `feature/<name>` or `fix/<name>`, open a PR, all checks must pass, squash-merge by maintainer only. Update `docs/` when adding or changing functionality.

## Documentation

The `docs/` folder contains the sources for the official website at [simulatte.dev](https://simulatte.dev), built with [Zensical](https://github.com/dmezzogori/zensical) (MkDocs-based). Configuration lives in `zensical.toml`; theme overrides in `overrides/`.
