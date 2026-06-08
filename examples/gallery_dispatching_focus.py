"""FOCUS system-state dispatching rule across weight configurations.

FOCUS (Kasper, Land, Teunter 2023) blends five shop-state mechanisms. Because
it needs shop-wide state, it is wired by build_focus_system. This gallery
compares a few weight vectors against an FCFS baseline on one seeded shop.

Run: uv run python examples/gallery_dispatching_focus.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_focus_system, build_immediate_release_system
from simulatte.dispatching_rules import first_come_first_served
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SEED = 42
HORIZON = 800.0

# (label, focus_weights or None for the FCFS baseline)
CONFIGS = [
    ("FCFS baseline", None),
    ("FOCUS beta-dormant", (0.25, 0.25, 0.25, 0.25, 0.0)),
    ("FOCUS SPT-heavy", (0.6, 0.1, 0.1, 0.1, 0.1)),
    ("FOCUS balanced", (0.2, 0.2, 0.2, 0.2, 0.2)),
]


def metrics(shop_floor) -> tuple[int, float, float, float]:
    done = shop_floor.jobs_done
    n = len(done)
    avg_tis = shop_floor.average_time_in_system
    tardiness = [max(0.0, j.lateness) for j in done]
    mean_tard = sum(tardiness) / n if n else 0.0
    pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
    return n, avg_tis, mean_tard, pct_tardy


def run_config(weights) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        scenario = Scenario(due_date_offset_range=(10.0, 18.0))
        if weights is None:
            _, _s, shop_floor, _ = build_immediate_release_system(
                env=env, scenario=scenario, priority_policies=first_come_first_served
            )
        else:
            _, _s, shop_floor, _ = build_focus_system(env=env, scenario=scenario, focus_weights=weights)
        env.run(until=HORIZON)
        return metrics(shop_floor)


def main() -> None:
    print("FOCUS system-state dispatching (immediate release, seed=42)")
    print(f"{'Config':<20}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for label, weights in CONFIGS:
        n, tis, mt, pt = run_config(weights)
        print(f"{label:<20}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
