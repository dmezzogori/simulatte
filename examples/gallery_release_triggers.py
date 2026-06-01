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


def build_periodic_release(env: Environment, interval: float = 10.0):
    """A custom pull system: release the entire pool every `interval` units."""
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(N_SERVERS))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    router = Router(
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
    return psp, servers, shop_floor, router


def run_system(builder) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        _psp, _servers, shop_floor, _router = builder(env)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env),
    "Starvation-only": lambda env: build_starvation_avoidance_system(env),
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
