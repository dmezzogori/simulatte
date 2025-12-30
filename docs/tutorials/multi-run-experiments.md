# Multi-run experiments

Goal: run the same stochastic system multiple times (different seeds) and collect results.

`Runner` handles:

- seeding `random`
- building a fresh `Environment` for each run
- optional multiprocessing (`parallel=True`)
- a `tqdm` progress bar (auto-enabled on TTY; override with `progress=True/False`)

## Example: push system with stochastic arrivals

```python
from simulatte.builders import build_immediate_release_system
from simulatte.runner import Runner

def builder(*, env):
    return build_immediate_release_system(env, n_servers=6, arrival_rate=1.5, service_rate=2.0)

def extract(system):
    _psp, servers, shopfloor, _router = system
    avg_util = sum(s.utilization_rate for s in servers) / len(servers)
    return {
        "jobs_done": len(shopfloor.jobs_done),
        "avg_utilization": avg_util,
        "avg_time_in_system": shopfloor.average_time_in_system,
    }

runner = Runner(
    builder=builder,
    seeds=range(10),
    parallel=False,
    # progress=None (default) auto-enables on TTY; set False to disable
    extract_fn=extract,
)

results = runner.run(until=1_000)
print(results)
```

## Parallel runs

If you turn on multiprocessing, keep your code in a `if __name__ == "__main__":` guard.

```python
if __name__ == "__main__":
    runner = Runner(builder=builder, seeds=range(20), parallel=True, extract_fn=extract)
    print(runner.run(until=1_000))
```
