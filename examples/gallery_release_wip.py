"""WIP-cap release policies compared on one seeded shop.

ConWIP (constant WIP, EDD release) vs DRACO (non-hierarchical WIP control that
merges release, authorisation, and dispatching). Both keep shop WIP near a
target; DRACO additionally governs dispatching with FOCUS internally.

Run: uv run python examples/gallery_release_wip.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_conwip_system, build_draco_system
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

SYSTEMS = {
    "ConWIP": lambda env: build_conwip_system(env, wip_cap=18),
    "DRACO": lambda env: build_draco_system(env, wip_target=8, loop_target=4),
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
    print("WIP-cap release policies (seed=42)")
    print(f"{'Policy':<8}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<8}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
