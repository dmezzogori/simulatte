# Basic Usage

This guide gets you from “installed” to “first simulation” using Simulatte’s core objects:

- `Environment`: simulation clock + event scheduler (a thin wrapper around `simpy.Environment`)
- `ShopFloor`: orchestrates job processing across servers
- `Server`: a resource with queue/utilization tracking
- `ProductionJob`: a job with a routing (servers) and processing times

See [Installation](installation.md) for setup.

## First simulation (single server)

```python { .run }
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
    due_date=100.0,  # absolute simulation time
)

shopfloor.add(job)
env.run()  # runs until the event queue is empty

print(f"Job makespan: {job.makespan:.1f}")
print(f"Server utilization: {server.utilization_rate:.1%}")
```

## Next

- [Tutorials](../tutorials/index.md) (job-shop walkthroughs)
- [Intralogistics](../guides/intralogistics.md) (warehouse and AGV simulation)
- [Agent Skill](../development/agent-skill.md)
