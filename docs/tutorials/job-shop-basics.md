# Job-shop basics

Goal: run a small job-shop, then inspect basic performance metrics.

## 1) Build a shop with two servers

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)

s1 = Server(env=env, capacity=1, shopfloor=shopfloor)
s2 = Server(env=env, capacity=1, shopfloor=shopfloor)
```

## 2) Create jobs with a routing

Each job has:

- `servers`: the routing (operation order)
- `processing_times`: time per operation
- `due_date`: absolute simulation time

```python
jobs = [
    ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5.0, 3.0], due_date=40.0),
    ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[4.0, 2.0], due_date=40.0),
    ProductionJob(env=env, sku="B", servers=[s1, s2], processing_times=[6.0, 2.0], due_date=40.0),
]
for job in jobs:
    shopfloor.add(job)
```

## 3) Run and read results

```python
env.run()

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")
print(f"s1 utilization: {s1.utilization_rate:.1%} (avg queue len: {s1.average_queue_length:.2f})")
print(f"s2 utilization: {s2.utilization_rate:.1%} (avg queue len: {s2.average_queue_length:.2f})")

tardy = sum(1 for j in shopfloor.jobs_done if j.late)
print(f"Tardy jobs: {tardy}/{len(shopfloor.jobs_done)}")
```

## Notes

- Simulatte avoids global singletons: pass the same `env` to every component you want in the same simulation.
- `Server(id=...)` is assigned when the server is registered on a `ShopFloor` (via the `shopfloor=` argument).

