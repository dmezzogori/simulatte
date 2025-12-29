# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Development Commands

```bash
# Install dependencies
uv sync --dev

# Run tests (with coverage, 99% threshold enforced)
uv run pytest

# Run a single test file
uv run pytest tests/core/test_shopfloor.py

# Run a specific test
uv run pytest tests/core/test_shopfloor.py::test_function_name

# Linting
uv run ruff check src tests

# Auto-fix lint issues
uv run ruff check --fix src tests

# Format code
uv run ruff format src tests

# Type checking
uv run ty check src

# Build documentation
uv run zensical build
```

## Architecture Overview

Simulatte is a discrete-event simulation framework for job-shop scheduling and intralogistics, built on SimPy.

### Core Components

**Environment** (`src/simulatte/environment.py`): SimPy wrapper with integrated per-environment logging. Supports JSON/text output and optional SQLite persistence.

**ShopFloor** (`src/simulatte/shopfloor.py`): Central orchestrator managing job flow through the simulation. Tracks WIP, coordinates routing, maintains EMA metrics. Extensible via:
- `OperationHook`: Generator-based hooks for before/after operations
- `WIPStrategy`: Pluggable WIP calculation (StandardWIPStrategy, CorrectedWIPStrategy)
- `MetricsCollector`: Pluggable metrics recording

**ProductionJob** (`src/simulatte/job.py`): Jobs with routing through servers, processing times, due dates. Also TransportJob and WarehouseJob variants.

**Server** (`src/simulatte/server.py`): Processing resource extending `simpy.PriorityResource`. Tracks queue times, utilization.

**Policies** (`src/simulatte/policies/`): Release policies for job scheduling:
- LumsCor: Load-based scheduling
- SLAR: Server load adjustment rule
- StarvationAvoidance: Prevents resource starvation

### Supporting Modules

- **MaterialCoordinator** (`materials.py`): FIFO material delivery coordination
- **Runner** (`runner.py`): Multi-simulation execution with seed management
- **AGV** (`agv.py`): Automated guided vehicle transport
- **WarehouseStore** (`warehouse_store.py`): Inventory management
- **PSP** (`psp.py`): Pre-shop pool for job release control
- **Builders** (`builders.py`): Factory functions (`build_push_system`, `build_pull_system`, `build_material_system`)

### Typical Simulation Flow

1. Create `Environment`
2. Create `ShopFloor` with optional hooks/strategies
3. Create `Server` instances attached to ShopFloor
4. Create `ProductionJob` instances with routing (list of servers) and processing times
5. Add jobs to ShopFloor via `shopfloor.add(job)`
6. Run simulation with `env.run()`
7. Analyze metrics (job.makespan, server.utilization_rate, etc.)

## Code Style

- Python 3.14+, line length 120
- All files must have `from __future__ import annotations`
- Ruff for linting/formatting, ty for type checking
- 99% test coverage required
