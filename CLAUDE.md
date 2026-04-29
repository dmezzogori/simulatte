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
│   ├── environment.py      # SimPy environment wrapper
│   ├── shopfloor.py        # Central orchestrator
│   ├── job.py              # ProductionJob and variants
│   ├── server.py           # Processing resources
│   ├── psp.py              # Pre-shop pool
│   ├── router.py           # Job routing logic
│   ├── runner.py           # Multi-simulation execution
│   ├── builders.py         # Factory functions for system setup
│   ├── distributions.py    # Statistical distributions
│   ├── logger.py           # Logging with JSON/text/SQLite output
│   ├── typing.py           # Shared type definitions
│   ├── policies/           # Release policies (LumsCor, SLAR, StarvationAvoidance, triggers)
│   └── experimental/       # Unstable modules (AGV, warehouse, materials, experimental builders/job/typing)
├── tests/
│   ├── core/               # Tests for stable modules
│   └── experimental/       # Tests for experimental modules
├── docs/                   # Website sources (simulatte.dev), built with Zensical
├── overrides/              # MkDocs theme overrides
├── pyproject.toml          # Project metadata, tool config
└── zensical.toml           # Documentation site config
```

## Architecture Overview

Simulatte is a discrete-event simulation framework for job-shop scheduling and intralogistics, built on SimPy.

### Core Components

**Environment** (`src/simulatte/environment.py`): SimPy wrapper with integrated per-environment logging. Supports JSON/text output and optional SQLite persistence.

**ShopFloor** (`src/simulatte/shopfloor.py`): Central orchestrator managing job flow through the simulation. Tracks WIP, coordinates routing, maintains EMA metrics. Extensible via:
- `OperationHook`: Sync or generator-based hooks for before/after operations
- `WIPStrategy`: Pluggable WIP calculation (StandardWIPStrategy, CorrectedWIPStrategy)
- `MetricsCollector`: Pluggable metrics recording
- `Dispatcher`: Protocol for one-call hook wiring via `attach_dispatcher()`

**ProductionJob** (`src/simulatte/job.py`): Jobs with routing through servers, processing times, due dates. Also TransportJob and WarehouseJob variants.

**Server** (`src/simulatte/server.py`): Processing resource extending `simpy.PriorityResource`. Tracks queue times, utilization.

**Policies** (`src/simulatte/policies/`): Release policies for job scheduling:
- LumsCor: Load-based scheduling
- SLAR: Server load adjustment rule
- `starvation_avoidance`: Callback for `psp.on_arrival()` that releases jobs when first server is idle

### Supporting Modules

- **Router** (`router.py`): Job routing logic through server sequences
- **Runner** (`runner.py`): Multi-simulation execution with seed management
- **PSP** (`psp.py`): Pre-shop pool for job release control
- **Builders** (`builders.py`): Factory functions (`build_immediate_release_system`, `build_lumscor_system`, `build_slar_system`)
- **Distributions** (`distributions.py`): Statistical distribution helpers
- **Triggers** (`policies/triggers.py`): Event-driven triggers for release policies

### Experimental Modules (`experimental/`)

Unstable APIs, subject to change:

- **MaterialCoordinator** (`experimental/materials.py`): FIFO material delivery coordination
- **AGV** (`experimental/agv.py`): Automated guided vehicle transport
- **Warehouse** (`experimental/warehouse.py`): Inventory management
- **MaterialSystemBuilder** (`experimental/builders.py`): Builder for material-aware systems


## CI/CD

GitHub Actions workflows live in `.github/workflows/`:

- **ci.yml**: Runs lint, type-check, and tests across Python 3.12/3.13/3.14 on push/PR to `main`. Coverage uploaded to Codecov on 3.14.
- **docs.yml**: Builds and deploys documentation to GitHub Pages on push to `main`.
- **publish.yml**: Publishes to PyPI via trusted publishing when a `v*` tag is pushed.

## Contributing

See `CONTRIBUTING.md` for the full workflow. Key rules: branch from `main` as `feature/<name>` or `fix/<name>`, open a PR, all checks must pass, squash-merge by maintainer only. Update `docs/` when adding or changing functionality.

## Documentation

The `docs/` folder contains the sources for the official website at [simulatte.dev](https://simulatte.dev), built with [Zensical](https://github.com/dmezzogori/zensical) (MkDocs-based). Configuration lives in `zensical.toml`; theme overrides in `overrides/`.
