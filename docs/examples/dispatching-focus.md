# FOCUS System-State Dispatching

**FOCUS** (Kasper, Land, Teunter 2023) is *the* system-state dispatching rule in Simulatte. Where the stateless and parameterized rules look only at the candidate job (and at most its next queue), FOCUS is a self-established rule that blends five shop-wide impact mechanisms into a single priority. Its behaviour is steered by a five-element weight vector `(w1, w2, w3, w4, w5)`, where each weight is in `[0, 1]` and the five sum to 1. In Simulatte's tuple order the mechanisms are:

- **w1 — pi (SPT):** favour short operations at the server.
- **w2 — omega (starvation response):** favour jobs whose next server has an empty queue, to relieve idle downstream servers.
- **w3 — psi (slack timing):** favour due-date-urgent jobs (small or negative slack).
- **w4 — gamma (pacing):** favour orders that have fallen behind their per-operation pace.
- **w5 — beta (WIP balancing):** favour dispatches that improve shop-wide workload balance; dormant by default.

Because FOCUS reads shop-wide state, it cannot be a plain `(job, server) -> float` callable; it is wired into the shop by `build_focus_system`, which constructs its support classes for you. `FocusContext` (the snapshot of shop state) and `FocusPriorityRule` (the adapter that exposes FOCUS as a server priority policy) are *internals* of FOCUS, not separate dispatching rules. This page is the featured home for FOCUS.

This gallery compares three FOCUS weight vectors against an FCFS baseline on one seeded shop.

See also: [Dispatching Rules API](../api/dispatching-rules.md)

## Comparison

```python { .run }
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
from simulatte.distributions import Uniform
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
        scenario = Scenario(due_date_offset=Uniform(10.0, 18.0))
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
```

**Run it:**

```bash
uv run python examples/gallery_dispatching_focus.py
```

## Output

```text
FOCUS system-state dispatching (immediate release, seed=42)
Config                Done   AvgTIS  MeanTard  %Tardy
FCFS baseline         1172    16.07      5.26   53.8%
FOCUS beta-dormant    1174    11.99      1.60   28.2%
FOCUS SPT-heavy       1179    10.37      1.93   18.0%
FOCUS balanced        1177    12.73      2.19   37.5%
```

## Interpretation

The shop's due dates are set to **bind** here — the uniform offset is tightened to `(10.0, 18.0)`, the same range used across the dispatching galleries — so the due-date-blind FCFS baseline runs about half its jobs late (53.8% tardy, mean tardiness 5.26). Every FOCUS configuration beats that baseline on **both** average time in system **and** tardiness, because FOCUS folds shop state into each priority decision rather than serving in arrival order. The weight vector shifts the balance between the five mechanisms: the **SPT-heavy** vector `(0.6, 0.1, 0.1, 0.1, 0.1)` puts most weight on the `pi` (SPT) term, so it clears short jobs fastest (lowest AvgTIS, 10.37, and fewest tardy, 18.0%). The **beta-dormant** vector `(0.25, 0.25, 0.25, 0.25, 0.0)` switches off the WIP-balancing `beta` mechanism (its default state) and spreads weight evenly across the SPT, starvation, slack-timing, and pacing terms, landing at 11.99 AvgTIS and 28.2% tardy. The **balanced** vector `(0.2, 0.2, 0.2, 0.2, 0.2)` activates all five mechanisms and is the weakest of the three FOCUS configs on both flow time (12.73) and tardiness (37.5%) — spreading weight onto the WIP-balancing `beta` term dilutes the SPT pull that the other two exploit — yet it still comfortably beats the FCFS baseline. Tuning these five weights lets you trade flow time against tardiness without changing the shop itself. The FCFS baseline row is identical to the FCFS row in the [stateless gallery](dispatching-stateless.md): both use the same seeded shop and the same binding due dates.
