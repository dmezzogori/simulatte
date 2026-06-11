# Stateless Dispatching Rules

This gallery compares the **stateless** dispatching rules on a single seeded, multi-stage push shop. Each rule decides only the order in which a server processes the jobs already waiting in its queue — it needs no shop-wide state, just attributes of the candidate job (and, for WINQ, the next queue). Because the shop has variable multi-operation routings and due dates, every rule produces a distinct ordering, so their flow-time and tardiness trade-offs are directly comparable.

The seven rules:

- **SPT** — shortest processing time first; picks the job with the smallest imminent operation time.
- **EDD** — earliest due date; orders by the job's global due date.
- **ODD** — operational due date; orders by the per-operation due date derived from the global one.
- **MODD** — modified operational due date; falls back to processing time once a job is no longer at risk of tardiness.
- **CR** — critical ratio (slack ÷ remaining work); smaller ratios are more urgent.
- **FCFS** — first come first served; the neutral arrival-order baseline.
- **WINQ** — work in next queue; favours jobs heading to the least-loaded downstream server.

See also: [Dispatching Rules API](../api/dispatching-rules.md)

## Comparison

```python { .run }
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
```

**Run it:**

```bash
uv run python examples/gallery_dispatching_stateless.py
```

## Output

```text
Stateless dispatching rules (immediate release, seed=42)
Rule    Done   AvgTIS  MeanTard  %Tardy
SPT     1177    10.16      2.49   20.1%
EDD     1167    15.36      3.96   56.6%
ODD     1172    14.96      3.15   57.6%
MODD    1178    12.73      1.69   29.8%
CR      1170    15.47      3.48   63.9%
FCFS    1172    16.07      5.26   53.8%
WINQ    1173    13.74      4.25   42.5%
```

## Interpretation

SPT minimises average time in system (10.16 vs FCFS's 16.07) by always clearing the shortest job first; the due-date-blind FCFS baseline runs about half its jobs late (53.8% tardy, mean tardiness 5.26) and posts the highest AvgTIS of all the rules here. The shop's due dates are deliberately set to **bind** — the uniform offset is tightened to `(10.0, 18.0)`, close to the achievable flow times — so the due-date family is genuinely exercised rather than collapsing to zero tardiness. With binding due dates the rules separate clearly: **MODD** is the standout of the due-date family, cutting mean tardiness to 1.69 (29.8% tardy) while keeping AvgTIS at 12.73, because it falls back to shortest-processing-time once a job is no longer at risk of lateness. The pure due-date rules trade differently: **ODD** (14.96) and **EDD** (15.36) both lower flow time versus FCFS and cut *mean* tardiness below it (3.15 and 3.96 vs 5.26), but leave *more* jobs marginally late (57.6% and 56.6% tardy) as they chase the now-binding due dates. **CR** is the most conservative on tardiness (the most jobs late, 63.9% tardy), its slack ratio repeatedly deferring short jobs. SPT itself, despite reading no due date, still posts the fewest tardy jobs (20.1%): in this loaded shop its flow-time dominance clears queues fast enough to suppress lateness. Note that none of these rules controls work-in-process — they only reorder what is already on the floor. To cap WIP and shape release, combine a dispatching rule with a release policy (see the [workload-control release gallery](release-workload.md)).
