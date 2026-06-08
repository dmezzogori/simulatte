"""Parameterized dispatching rules compared on one seeded multi-stage shop.

Runs PST, S/RO, ATC, COVERT, and Raghu-Rajendran. Each is a factory: call it
with its parameter(s) to obtain the queue-ordering callable.

Run: uv run python examples/gallery_dispatching_parameterized.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.dispatching_rules import (
    apparent_tardiness_cost,
    cost_over_time,
    planned_slack_time,
    raghu_rajendran,
    slack_per_remaining_operation,
)
from simulatte.distributions import Uniform
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SEED = 42
HORIZON = 800.0

RULES = {
    "PST": planned_slack_time(allowance=2.0),
    "S/RO": slack_per_remaining_operation(allowance=2.0),
    "ATC": apparent_tardiness_cost(lookahead=2.0),
    "COVERT": cost_over_time(lookahead=2.0),
    "Raghu": raghu_rajendran(),
}


def run_rule(rule) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        _, _servers, shop_floor, _ = build_immediate_release_system(
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
    print("Parameterized dispatching rules (immediate release, seed=42)")
    print(f"{'Rule':<8}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, rule in RULES.items():
        n, tis, mt, pt = run_rule(rule)
        print(f"{name:<8}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
