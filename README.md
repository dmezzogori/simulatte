<p align="center">
  <img src="https://raw.githubusercontent.com/dmezzogori/simulatte/main/docs/assets/logo.png" alt="Simulatte" width="200">
</p>

# Simulatte

[![PyPI](https://img.shields.io/pypi/v/simulatte)](https://pypi.org/project/simulatte/)
[![Python](https://img.shields.io/pypi/pyversions/simulatte)](https://pypi.org/project/simulatte/)
[![License](https://img.shields.io/pypi/l/simulatte)](https://github.com/dmezzogori/simulatte/blob/main/LICENSE)
[![codecov](https://codecov.io/gh/dmezzogori/simulatte/graph/badge.svg)](https://codecov.io/gh/dmezzogori/simulatte)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://simulatte.dev)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21027366.svg)](https://doi.org/10.5281/zenodo.21027366)

Discrete-event simulation framework for production planning and control and intralogistics, built on [SimPy](https://simpy.readthedocs.io/).

> **Note:** Simulatte is under active development. All APIs — including those outside `simulatte.experimental` — should be considered unstable and may change between releases without prior deprecation. Pin your dependency to a specific version if you need stability.

---

## What is Simulatte?

Simulatte is a Python library for simulating manufacturing job-shops with integrated intralogistics. It models production servers, warehouses, AGVs, and material flow in a unified framework. Use it to evaluate scheduling policies, analyze bottlenecks, and study system performance under stochastic conditions.

The library provides ready-to-use components for common manufacturing scenarios while remaining extensible for custom requirements. Whether you're researching release control policies, optimizing warehouse layouts, or teaching discrete-event simulation, Simulatte offers a clean API built on proven SimPy foundations.

---

## Features

### Job-Shop Scheduling
- Multi-server routing with configurable processing times
- Due dates and tardiness tracking
- Queue time and utilization metrics per server

### Release Control
- **Pre-Shop Pool (PSP)** for workload-based job release
- Built-in policies: Immediate Release, LumsCor, SLAR, SLAR-Limit, DRACO, ConWIP, and Continuous Release
- Event callbacks (`psp.on_arrival`, `shopfloor.on_processing_end`) and composable triggers
- Starvation avoidance mechanisms

### Extensibility
- **Operation hooks**: sync or generator-based, before/after processing (e.g., setup times, dispatching)
- **Event callbacks**: `on_processing_end`, `on_job_finished`, `on_arrival` for reactive logic
- **Dispatcher protocol**: one-call wiring of multi-event controllers via `attach_dispatcher()`
- **WIP strategies**: Standard and Corrected workload estimation
- **Custom metrics collectors**: plug in your own real-time or time-series collectors

### Time-Series Analysis
- Built-in collectors for WIP, throughput, job count, lateness
- Matplotlib integration: `plot_wip()`, `plot_throughput()`, `plot_lateness()`
- Custom time-series collectors via simple protocol

### Reinforcement Learning Integration *(experimental)*
- **Gymnasium wrapper** ([`SimulatteEnv`](https://simulatte.dev/tutorials/gymnasium-wrapper/)): subclass a single ABC to turn any simulation into a Gymnasium environment (`from simulatte.experimental.gymnasium import SimulatteEnv`)
- Can be used with Stable-Baselines3, CleanRL, and other Gymnasium-compatible RL libraries
- Built-in lifecycle guards, seeding support, and resource cleanup

### Intralogistics
- **Warehouse** with per-SKU inventory, finite pick/put slots, input/output bays
- **AGV fleet** with trapezoidal speed profiles, battery lifecycle, and charging stations
- **FleetCoordinator** orchestrating dispatch, travel, pick, transit, deliver, reposition, and charge
- Pluggable policies: dispatch (`NearestIdleStrategy`, `RoundRobinStrategy`), repositioning, replenishment (`ReorderPointPolicy`), load recovery
- Time-series metrics with `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`
- Three progressive [examples](https://github.com/dmezzogori/simulatte/tree/main/examples): simple setup, manufacturing plant floor, multi-warehouse distribution hub

### Logging
- Per-component event logging (Server, ShopFloor, Router, Warehouse, AGV)
- JSON or text format output
- Queryable in-memory history with filtering by component, level, time range

### Multi-Run Experiments
- **Runner** class for stochastic experiments across multiple seeds
- Automatic seed management for reproducibility
- Parallel execution with multiprocessing support
- Progress bars via tqdm

---

## Installation

```bash
pip install simulatte
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add simulatte
```

---

## Quick Start

```python
from simulatte.environment import Environment
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.job import ProductionJob

# Create simulation environment
env = Environment()
shopfloor = ShopFloor(env=env)
server = Server(env=env, capacity=1, shopfloor=shopfloor)

# Create a job with routing through the server
job = ProductionJob(
    env=env,
    sku="A",
    servers=[server],
    processing_times=[5.0],
    due_date=100,
)

# Run simulation
shopfloor.add(job)
env.run()

# Analyze results
print(f"Makespan: {job.makespan}")
print(f"Server utilization: {server.utilization_rate:.1%}")
```

---

## AI Coding Agent Skill

Simulatte ships a skill for AI coding agents (Claude Code, Cursor, Windsurf, etc.) that helps them write correct Simulatte simulations — from choosing a release policy to running multi-seed experiments.

Install it with the [Vercel Skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add https://github.com/dmezzogori/simulatte/tree/main/skills/simulatte-dev
```

Once installed, invoke it with `/simulatte-dev` or let the agent auto-trigger it when working with Simulatte code.

---

## Documentation

Full documentation is available at [simulatte.dev](https://simulatte.dev).

---

## Citation

If you use Simulatte in your research, please cite it via its [Zenodo record](https://doi.org/10.5281/zenodo.21027366). The concept DOI [`10.5281/zenodo.21027366`](https://doi.org/10.5281/zenodo.21027366) always resolves to the latest version; cite the version DOI [`10.5281/zenodo.21027367`](https://doi.org/10.5281/zenodo.21027367) to pin v0.12.0.

```bibtex
@software{Mezzogori2026Simulatte,
  author  = {Mezzogori, Davide and Mercogliano, Nicola},
  title   = {{Simulatte}: A discrete-event simulation framework for production planning and control and intralogistics},
  year    = {2026},
  version = {0.12.0},
  doi     = {10.5281/zenodo.21027366},
  url     = {https://doi.org/10.5281/zenodo.21027366}
}
```

---

## Contributing

Contributions are welcome — please read [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow (branching, PR requirements, merge process).

---

## License

Simulatte is released under the [MIT License](LICENSE).
