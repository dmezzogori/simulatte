"""Preconfigured PPC benchmark shops compared on one seeded run.

Pure Job Shop (PJS, undirected routing), General Flow Shop (GFS, directed/sorted
routing), and Pure Flow Shop (PFS, every job visits all machines in a fixed
order) — the standard stylized shops of the workload-control literature
(Oosterman, Land & Gaalman 2000; Kasper, Land & Teunter 2023). Each shop runs at
an arrival rate derived from a common target utilization, so all three sit at the
same load despite different mean routing lengths (PFS visits every machine, so it
arrives slower than the job shop to hold the same utilization).

Run: uv run python examples/gallery_benchmark_shops.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SEED = 42
HORIZON = 2000.0

# label -> builder thunk taking only env
SYSTEMS = {
    "PureJobShop": lambda env: build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop()),
    "GeneralFlowShop": lambda env: build_immediate_release_system(env=env, scenario=Scenario.general_flow_shop()),
    "PureFlowShop": lambda env: build_immediate_release_system(env=env, scenario=Scenario.pure_flow_shop()),
}


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


def main() -> None:
    print("Benchmark shop environments (seed=42, rho=0.90)")
    print(f"{'Shop':<16}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<16}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
