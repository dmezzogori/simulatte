# Materials, warehouse, and AGVs

> **Experimental**: This feature is part of `simulatte.experimental` and may change in future releases.

Goal: add a warehouse + AGVs and run a `ProductionJob` that requires materials.

Simulatte's material handling uses **FIFO blocking**: if a job needs materials at an operation, it holds the server while the warehouse pick + AGV transport happens.

## Build a material system

```python
from simulatte.experimental import MaterialSystemBuilder
from simulatte.environment import Environment
from simulatte.job import ProductionJob

env = Environment()
shopfloor, servers, warehouse, agvs, coordinator = MaterialSystemBuilder.build(
    env,
    n_servers=2,
    n_agvs=1,
    n_bays=1,
    products=["A", "B"],
    initial_inventory={"A": 10, "B": 10},
    pick_time=1.0,
    travel_time=2.0,
)
```

`MaterialSystemBuilder` wires the coordinator into the shopfloor automatically, so material delivery is handled inside `ShopFloor.main()`.

## Create a job with material requirements

Material requirements are keyed by **operation index** (0-based):

```python
job = ProductionJob(
    env=env,
    sku="P1",
    servers=servers,
    processing_times=[5.0, 3.0],
    due_date=100.0,
    material_requirements={
        0: {"A": 2},  # before operation 0 starts, deliver 2 units of A
        1: {"B": 1},  # before operation 1 starts, deliver 1 unit of B
    },
)
shopfloor.add(job)
env.run()
```

## Inspect what happened

```python
print(f"Job makespan: {job.makespan:.1f}")
print(f"Warehouse picks: {warehouse.total_picks} (avg pick time: {warehouse.average_pick_time:.1f})")
print(f"AGV trips: {agvs[0].trip_count} (avg travel time: {agvs[0].average_travel_time:.1f})")
print(f"Deliveries: {coordinator.total_deliveries} (avg delivery time: {coordinator.average_delivery_time:.1f})")
```

