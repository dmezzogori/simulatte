"""Stateless dispatching rules compared on one seeded multi-stage shop.

Runs SPT, EDD, ODD, MODD, CR, FCFS, and WINQ as the queue-ordering rule of an
immediate-release (push) shop and prints a comparison table. The shop has
variable multi-operation routings and due dates, so every rule produces a
distinct ordering.

Run: uv run python examples/gallery_dispatching_stateless.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.dispatching_rules import (
    critical_ratio,
    earliest_due_date,
    first_come_first_served,
    modified_operational_due_date,
    operational_due_date,
    shortest_processing_time,
    work_in_next_queue,
)
from simulatte.distributions import Uniform
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SEED = 42
HORIZON = 800.0

RULES = {
    "SPT": shortest_processing_time,
    "EDD": earliest_due_date,
    "ODD": operational_due_date,
    "MODD": modified_operational_due_date,
    "CR": critical_ratio,
    "FCFS": first_come_first_served,
    "WINQ": work_in_next_queue,
}


def run_rule(rule) -> tuple[int, float, float, float]:
    random.seed(SEED)  # identical seeded stream for every rule -> fair comparison
    with Environment() as env:
        _, _servers, shop_floor, _, _ = build_immediate_release_system(
            env=env, scenario=Scenario(due_date_offset=Uniform(10.0, 18.0)), priority_policies=rule
        )
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("Stateless dispatching rules (immediate release, seed=42)")
    print(f"{'Rule':<6}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, rule in RULES.items():
        n, tis, mt, pt = run_rule(rule)
        print(f"{name:<6}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
