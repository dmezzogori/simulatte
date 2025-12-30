# Release control

Goal: control when jobs enter the shopfloor using release policies.

## 1) Push vs pull systems

In a **push** system, jobs enter the shopfloor immediately upon arrival. Simple, but can lead to high WIP and long queues.

In a **pull** system, jobs wait in a Pre-Shop Pool (PSP) and are released only when conditions are met (e.g., workload below threshold, server starving). This controls WIP and improves flow times.

```
Push:  Arrivals ────────────────> ShopFloor

Pull:  Arrivals ───> PSP ───> ShopFloor
                      │
              (release policy decides when)
```

## 2) The Pre-Shop Pool

The `PreShopPool` is a pure container with no built-in release logic. It holds jobs and provides events that external processes can monitor.

```python
from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)
psp = PreShopPool(env=env, shopfloor=shopfloor)
```

Key properties:

- `psp.empty`: True if no jobs waiting
- `psp.jobs`: Iterate over waiting jobs (FIFO order)
- `psp.new_job`: Event that fires when a job is added

## 3) Composable triggers

Release policies are implemented as external SimPy processes using trigger functions from `simulatte.policies.triggers`:

| Trigger | Fires when | Use case |
|---------|-----------|----------|
| `periodic_trigger` | At regular intervals | Workload checks |
| `on_arrival_trigger` | New job enters PSP | Immediate decisions |
| `on_completion_trigger` | Job finishes at server | Starvation avoidance |

Example: compose periodic and event-driven triggers together:

```python
from simulatte.policies.triggers import periodic_trigger, on_completion_trigger

def my_release_fn(psp):
    """Release oldest job if shopfloor WIP is low."""
    if not psp.empty and len(psp.shopfloor.jobs) < 10:
        job = psp.remove()
        psp.shopfloor.add(job)

def my_starvation_fn(triggering_job, psp):
    """Release a job when a server might starve.

    The triggering_job is the job that just finished processing.
    We check if its previous server is now empty.
    """
    server = triggering_job.previous_server
    if server is not None and server.empty and not psp.empty:
        # Find a job that starts at this server
        for candidate in psp.jobs:
            if candidate.starts_at(server):
                psp.remove(job=candidate)
                psp.shopfloor.add(candidate)
                break

# Register triggers
env.process(periodic_trigger(psp, 5.0, my_release_fn))
env.process(on_completion_trigger(shopfloor, psp, my_starvation_fn))
```

## 4) Using builders

Simulatte provides builder functions for common configurations.

### Immediate release (baseline)

Jobs bypass the PSP entirely. Useful as a baseline for comparison.

```python
from simulatte.builders import build_immediate_release_system
from simulatte.environment import Environment

env = Environment()
_, servers, shopfloor, router = build_immediate_release_system(
    env,
    n_servers=6,
    arrival_rate=1.5,
    service_rate=2.0,
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")
```

### LumsCor (workload-based)

Jobs are released only if adding them keeps corrected WIP at or below a workload norm. Combines periodic checks with starvation avoidance.

```python
from simulatte.builders import build_lumscor_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_lumscor_system(
    env,
    check_timeout=10.0,      # Check every 10 time units
    wl_norm_level=5.0,       # Workload threshold per server
    allowance_factor=2,      # Buffer for due date calculation
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
print(f"Avg time in PSP: {sum(j.time_in_psp for j in shopfloor.jobs_done) / len(shopfloor.jobs_done):.2f}")
```

Key parameters:

- `check_timeout`: Time between periodic release checks
- `wl_norm_level`: Maximum corrected WIP allowed per server
- `allowance_factor`: Multiplier for due date slack (higher = more conservative)

### SLAR (slack-based)

Event-driven release based on planned slack times. No periodic checks—releases happen when servers risk starvation or urgent jobs need insertion.

```python
from simulatte.builders import build_slar_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_slar_system(
    env,
    allowance_factor=3.0,    # Slack per operation
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
```

Key parameter:

- `allowance_factor`: Slack allowance per operation (higher = more buffer time)

## 5) Comparing systems

Run all three systems and compare:

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
)
from simulatte.environment import Environment
from simulatte.runner import Runner

def run_system(builder_fn, builder_kwargs, until=1000):
    def builder(*, env):
        return builder_fn(env, **builder_kwargs)

    def extract(system):
        psp, servers, shopfloor, router = system
        return {
            "jobs_done": len(shopfloor.jobs_done),
            "avg_time_in_system": shopfloor.average_time_in_system,
            "avg_utilization": sum(s.utilization_rate for s in servers) / len(servers),
        }

    runner = Runner(builder=builder, seeds=range(5), parallel=False, extract_fn=extract)
    return runner.run(until=until)

# Compare
immediate = run_system(build_immediate_release_system, {"n_servers": 6, "arrival_rate": 1.5})
lumscor = run_system(build_lumscor_system, {"check_timeout": 10, "wl_norm_level": 5, "allowance_factor": 2})
slar = run_system(build_slar_system, {"allowance_factor": 3})

for name, results in [("Immediate", immediate), ("LumsCor", lumscor), ("SLAR", slar)]:
    avg_tis = sum(r["avg_time_in_system"] for r in results) / len(results)
    print(f"{name}: avg time in system = {avg_tis:.2f}")
```

## Notes

- Multiple triggers can run simultaneously on the same PSP.
- The PSP's `new_job` event is broadcast: all waiting processes receive the job.
- LumsCor requires `CorrectedWIPStrategy` on the shopfloor (set automatically by the builder).
- SLAR is purely event-driven (no periodic trigger).
