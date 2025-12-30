<p align="center">
  <img src="docs/assets/logo.png" alt="Simulatte" width="200">
</p>

# Simulatte

[![PyPI](https://img.shields.io/pypi/v/simulatte)](https://pypi.org/project/simulatte/)
[![Python](https://img.shields.io/pypi/pyversions/simulatte)](https://pypi.org/project/simulatte/)
[![License](https://img.shields.io/pypi/l/simulatte)](https://github.com/dmezzogori/simulatte/blob/main/LICENSE)
[![codecov](https://codecov.io/gh/dmezzogori/simulatte/graph/badge.svg)](https://codecov.io/gh/dmezzogori/simulatte)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://dmezzogori.github.io/simulatte/)

Discrete-event simulation framework for job-shop scheduling and intralogistics, built on [SimPy](https://simpy.readthedocs.io/).

---

## Table of Contents

- [What is Simulatte?](#what-is-simulatte)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Documentation](#documentation)
- [Citation](#citation)
- [Contributing](#contributing)
- [License](#license)

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
- Built-in policies: Immediate Release, LumsCor, SLAR
- Composable triggers (periodic, on-arrival, on-completion)
- Starvation avoidance mechanisms

### Extensibility
- **Operation hooks**: inject logic before/after processing (e.g., setup times)
- **WIP strategies**: Standard and Corrected workload estimation
- **Custom metrics collectors**: plug in your own real-time or time-series collectors
- **Job-finished callbacks**: react to completed jobs synchronously

### Time-Series Analysis
- Built-in collectors for WIP, throughput, job count, lateness
- Matplotlib integration: `plot_wip()`, `plot_throughput()`, `plot_lateness()`
- Custom time-series collectors via simple protocol

### Material Handling *(experimental)*
- Warehouse with inventory management
- AGV fleet coordination
- FIFO blocking semantics for realistic material flow

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

## Documentation

Full documentation is available at [dmezzogori.github.io/simulatte](https://dmezzogori.github.io/simulatte/).

---

## Citation

If you use Simulatte in your research, please cite:

```bibtex
@software{Mezzogori2025Simulatte,
  author = {Mezzogori, Davide},
  title = {{Simulatte}: A discrete-event simulation framework for job-shop scheduling and intralogistics},
  year = {2025},
  url = {https://github.com/dmezzogori/simulatte},
  note = {Python package version 0.1.4}
}
```

---

## Contributing

Issues and pull requests are welcome at [github.com/dmezzogori/simulatte](https://github.com/dmezzogori/simulatte).

---

## License

Simulatte is released under the [MIT License](LICENSE).
