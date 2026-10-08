# Release Triggers & Starvation Avoidance

Release policies in Simulatte are composed from **triggers** — small SimPy processes that decide *when* to consult the Pre-Shop Pool — and **release functions** that decide *what* to release. Separating the two lets the same release logic run on a clock, on shop events, or on arrivals. The runnable example below exercises `periodic_trigger` directly to build a periodic-release pull system; the other primitives and the callback API are described here as building blocks. It compares two minimal pull systems against the immediate-release (push) baseline.

The building blocks:

- **`periodic_trigger(psp, interval, release_fn)`** — fires `release_fn(psp)` every `interval` time units while the pool is non-empty (time-driven release).
- **`on_arrival_trigger(psp, release_fn)`** — fires `release_fn(job, psp)` the moment a job enters the pool (react to new demand).
- **`on_completion_trigger(shopfloor, psp, release_fn)`** — fires `release_fn(job, psp)` whenever any job finishes an operation (event-driven, load-aware release).
- **`starvation_avoidance`** — an `on_arrival` callback that releases a newly arrived job immediately when its first routing server is idle, keeping the entrance fed.

See also: [Release Policies API](../api/release-policies.md)

## Comparison

```python { .run }
"""Release triggers and starvation avoidance.

Exercises the periodic_trigger primitive directly (in the Periodic-release
system) and contrasts a starvation-avoidance pull — built via the callback API
(psp.on_arrival / shop_floor.on_processing_end) — and a periodic-release pull
against the immediate-release baseline:

  - Immediate        : push baseline (no PSP).
  - Starvation-only  : release only when a job's first server is idle.
  - Periodic-release : release the whole pool every fixed interval.

Run: uv run python examples/gallery_release_triggers.py
"""

from __future__ import annotations

from simulatte.builders import (
    build_immediate_release_system,
    build_starvation_avoidance_system,
)
from simulatte.distributions import Exponential, TruncatedErlang, Uniform, pure_job_shop_routing
from simulatte.environment import Environment
from simulatte.policies.triggers import periodic_trigger
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.scenario import Scenario
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.typing import BuiltSystem

SEED = 42
HORIZON = 800.0
N_SERVERS = 6
# Derive the arrival rate from the same Scenario the builder rows use, so all
# three compared systems share one rate and the comparison can never drift.
ARRIVAL_RATE = Scenario().resolved_arrival_rate()
SERVICE_RATE = 2.0


def build_periodic_release(env: Environment, interval: float = 10.0):
    """A custom pull system: release the entire pool every `interval` units.

    Its entities carry the builders' ids (``wc-<i>``, ``shopfloor``, ``psp``,
    ``router``), so its random streams, which are named after those ids, draw
    the same arrivals and service times as the builder rows.
    """
    shop_floor = ShopFloor(env=env, name="shopfloor")
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor, name=f"wc-{i}") for i in range(N_SERVERS))
    psp = PreShopPool(env=env, shopfloor=shop_floor, name="psp")
    router = Router(
        env=env,
        name="router",
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=Exponential(ARRIVAL_RATE),
        sku_distributions={"F1": 1},
        sku_routings={"F1": pure_job_shop_routing(servers)},
        sku_service_times={"F1": {s: TruncatedErlang(rate=SERVICE_RATE, shape=2, max_value=4.0) for s in servers}},
        due_date_offset_distribution={"F1": Uniform(30, 45)},
    )

    def release_all(pool: PreShopPool) -> None:
        for job in list(pool.jobs):
            pool.release(job)

    env.process(periodic_trigger(psp, interval, release_all))
    return BuiltSystem(psp=psp, servers=servers, shop_floor=shop_floor, router=router, policy=None)


def run_system(builder) -> tuple[int, float, float, float]:
    with Environment(seed=SEED) as env:
        _psp, _servers, shop_floor, _router, _policy = builder(env)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env=env),
    "Starvation-only": lambda env: build_starvation_avoidance_system(env=env),
    "Periodic-release": build_periodic_release,
}


def main() -> None:
    print("Release triggers & starvation avoidance (seed=42)")
    print(f"{'System':<18}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
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
Immediate           1162    18.69      0.52    8.1%
Starvation-only     1161    10.72      0.59    6.8%
Periodic-release    1154    19.19      1.18   15.7%
```

## Interpretation

All three systems run on the **same scenario** — the same `Scenario`-derived arrival rate for all three systems, identical service times, due dates, seed, and horizon — so every difference below is attributable to *release timing alone*. The starvation-only system is wired via the on-arrival and on-completion **callbacks** (`psp.on_arrival` / `shop_floor.on_processing_end`) — not the trigger-process primitives — to release a job the instant its first server goes idle. This keeps the shop entrance fed and trims average time in system (10.72 vs the push baseline's 18.69), while its tardiness stays close to the baseline (6.8% tardy vs 8.1%, mean tardiness 0.59 vs 0.52): average time in system excludes the wait in the pool, but lateness includes it. The periodic system releases the entire pool every ten time units: arrivals are batched into bursts that briefly flood the floor, so it shows the worst flow time (19.19), the largest share of tardy jobs (15.7%, against 8.1% for the push baseline and 6.8% for starvation-only) and the highest mean tardiness (1.18). A clear illustration that *when* you release matters as much as *what* you release. Both pull systems are deliberately minimal; production policies (LumsCor, ConWIP, Continuous Release) layer load- or count-based release functions onto these same trigger primitives.
