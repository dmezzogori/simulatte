<p align="center">
  <img src="assets/logo.png" alt="Simulatte" width="200">
</p>

# Simulatte

Discrete-event simulation framework for job-shop scheduling and intralogistics, built on [SimPy](https://simpy.readthedocs.io/).

- New here? Start with [Getting Started](getting-started.md).
- Want examples? Go to [Tutorials](tutorials/index.md).

## Install

Requires Python 3.14+.

```bash
pip install simulatte
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add simulatte
```

## 5-minute example

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)
server = Server(env=env, capacity=1, shopfloor=shopfloor)

job = ProductionJob(
    env=env,
    sku="A",
    servers=[server],
    processing_times=[5.0],
    due_date=100.0,
)

shopfloor.add(job)
env.run()

print(f"Makespan: {job.makespan:.1f}")
print(f"Utilization: {server.utilization_rate:.1%}")
```

## What's next

- [Job-shop basics](tutorials/job-shop-basics.md): multiple servers, multiple jobs, common metrics.
- [Release control](tutorials/release-control.md): pre-shop pool, release policies (LumsCor, SLAR), and triggers.
- [ShopFloor extensibility](tutorials/shopfloor-extensibility.md): hooks, WIP strategies, and metrics collectors.
- [Materials, warehouse, and AGVs](tutorials/materials-warehouse-agvs.md): FIFO blocking material delivery.
- [Multi-run experiments](tutorials/multi-run-experiments.md): repeatable runs across seeds.
- [Logging](tutorials/logging.md): trace events, debug simulations, analyze history.
