# Simulatte

[![codecov](https://codecov.io/gh/dmezzogori/simulatte/graph/badge.svg)](https://codecov.io/gh/dmezzogori/simulatte)

Discrete-event simulation framework for job-shop scheduling and intralogistics.

## What is Simulatte?

Simulatte is a Python library for simulating manufacturing job-shops with integrated intralogistics. Built on [SimPy](https://simpy.readthedocs.io/), it models production servers, warehouses, AGVs, and material flow in a unified framework. Use it to evaluate scheduling policies, analyze bottlenecks, and study system performance under stochastic conditions.

## Features

- **Job-shop scheduling** with multi-server routing and due dates
- **Push/pull architectures** with configurable release policies
- **Material requirements** per operation with FIFO blocking semantics
- **AGV transport** and warehouse inventory management
- **Built-in policies**: LumsCor, SLAR, starvation avoidance
- **Comprehensive metrics**: utilization, queue times, tardiness, WIP
- **Multi-run experiments** with seed management and parallel execution

## Installation

```bash
pip install simulatte
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add simulatte
```

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
    family="A",
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

## Contributing

Issues and pull requests are welcome at [github.com/dmezzogori/simulatte](https://github.com/dmezzogori/simulatte).
