# Release Triggers & Starvation Avoidance

Release policies in Simulatte are composed from **triggers** — small SimPy processes that decide *when* to consult the Pre-Shop Pool — and **release functions** that decide *what* to release. Separating the two lets the same release logic run on a clock, on shop events, or on arrivals. This gallery shows the trigger primitives in action and compares two minimal pull systems built from them against the immediate-release (push) baseline.

The building blocks:

- **`periodic_trigger(psp, interval, release_fn)`** — fires `release_fn(psp)` every `interval` time units while the pool is non-empty (time-driven release).
- **`on_arrival_trigger(psp, release_fn)`** — fires `release_fn(job, psp)` the moment a job enters the pool (react to new demand).
- **`on_completion_trigger(shopfloor, psp, release_fn)`** — fires `release_fn(job, psp)` whenever any job finishes an operation (event-driven, load-aware release).
- **`starvation_avoidance`** — an `on_arrival` callback that releases a newly arrived job immediately when its first routing server is idle, keeping the entrance fed.

See also: [Release Policies API](../api/release-policies.md)

## Comparison

```python { .run }
"""Release triggers and starvation avoidance.

Demonstrates the three trigger primitives (periodic_trigger, on_completion_
trigger, on_arrival_trigger) and the starvation_avoidance callback by building
three small pull systems and comparing them to the immediate-release baseline:

  - Immediate        : push baseline (no PSP).
  - Starvation-only  : release only when a job's first server is idle.
  - Periodic-release : release the whole pool every fixed interval.

Run: uv run python examples/gallery_release_triggers.py
"""

from __future__ import annotations

import random

from simulatte.builders import (
    build_immediate_release_system,
    build_starvation_avoidance_system,
)
from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.policies.triggers import periodic_trigger
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

SEED = 42
HORIZON = 800.0
N_SERVERS = 6
ARRIVAL_RATE = 1 / 0.648
SERVICE_RATE = 2.0


def metrics(shop_floor) -> tuple[int, float, float, float]:
    done = shop_floor.jobs_done
    n = len(done)
    avg_tis = shop_floor.average_time_in_system
    tardiness = [max(0.0, j.lateness) for j in done]
    mean_tard = sum(tardiness) / n if n else 0.0
    pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
    return n, avg_tis, mean_tard, pct_tardy


def build_periodic_release(env: Environment, interval: float = 10.0):
    """A custom pull system: release the entire pool every `interval` units."""
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(N_SERVERS))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(ARRIVAL_RATE),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={"F1": {s: lambda: truncated_2erlang(lam=SERVICE_RATE, max_value=4.0) for s in servers}},
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
    )

    def release_all(pool: PreShopPool) -> None:
        for job in list(pool.jobs):
            pool.release(job)

    env.process(periodic_trigger(psp, interval, release_all))
    return shop_floor


def run(builder) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        result = builder(env)
        shop_floor = result if isinstance(result, ShopFloor) else result[2]
        env.run(until=HORIZON)
        return metrics(shop_floor)


SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env),
    "Starvation-only": lambda env: build_starvation_avoidance_system(env),
    "Periodic-release": build_periodic_release,
}


def main() -> None:
    print("Release triggers & starvation avoidance (seed=42)")
    print(f"{'System':<18}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run(builder)
        print(f"{name:<18}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
uv run python examples/gallery_release_triggers.py
```

## Output

```text
Release triggers & starvation avoidance (seed=42)
System              Done   AvgTIS  MeanTard  %Tardy
Immediate           1159    14.44      0.11    1.6%
Starvation-only     1153    10.64      1.02    7.1%
Periodic-release    1144    16.66      0.43    7.5%
```

## Interpretation

The starvation-only system uses arrival and completion triggers to release a job the instant its first server goes idle. This keeps the shop entrance fed and trims average time in system (10.64 vs the push baseline's 14.44), but because it never throttles, WIP is free to grow and a longer tail of jobs turns tardy (7.1%). The periodic system releases the entire pool every ten time units: arrivals are batched into bursts that briefly flood the floor, so it shows the highest flow time (16.66) — a clear illustration that *when* you release matters as much as *what* you release. Both are deliberately minimal; production policies (LumsCor, ConWIP, Continuous Release) layer load- or count-based release functions onto these same trigger primitives.
