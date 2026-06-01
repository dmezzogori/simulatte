# Parameterized Dispatching Rules

This gallery compares the **parameterized** dispatching rules on the same seeded, multi-stage push shop used by the stateless gallery. Unlike the stateless rules, each of these is a *factory*: you call it with one or more tuning parameters to obtain the queue-ordering callable. The knob lets you slide each rule between a flow-time-greedy and a tardiness-averse regime without changing the underlying shop.

The five rules:

- **PST** — planned slack time; ranks by slack against an `allowance` buffer.
- **S/RO** — slack per remaining operation; normalises slack by the number of operations still to do.
- **ATC** — apparent tardiness cost; exponential urgency weighting tuned by a `lookahead`.
- **COVERT** — cost over time; expected tardiness cost per unit of processing time, also tuned by a `lookahead`.
- **Raghu-Rajendran** — a composite rule blending slack and processing-time terms.

See also: [Dispatching Rules API](../api/dispatching-rules.md)

## Comparison

```python { .run }
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
from simulatte.environment import Environment

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
            env, priority_policies=rule, due_date_offset_range=(10.0, 18.0)
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
```

**Run it:**

```bash
uv run python examples/gallery_dispatching_parameterized.py
```

## Output

```text
Parameterized dispatching rules (immediate release, seed=42)
Rule      Done   AvgTIS  MeanTard  %Tardy
PST       1155    14.56      2.92   56.3%
S/RO      1152    15.13      3.36   57.4%
ATC       1160    13.30      2.38   44.2%
COVERT    1160    12.98      1.58   41.5%
Raghu     1162    11.11      2.02   27.0%
```

## Interpretation

ATC and COVERT take a `lookahead` parameter that sets how far ahead the rule discounts a job's expected tardiness cost; PST and S/RO take an `allowance` that sizes the slack buffer. These knobs are the whole point: they trade average flow time against tardiness. The shop's due dates are set to **bind** (the uniform offset is tightened to `(10.0, 18.0)`, the same range used across the dispatching galleries), so the slack/cost rules are genuinely exercised rather than reading zero tardiness everywhere. The cost-based rules now separate clearly from the pure-slack rules: **COVERT** and **ATC**, which weight expected tardiness cost per unit of processing time, fold in a short-job preference and so cut both AvgTIS (12.98 and 13.30) and tardiness (41.5% and 44.2%) below the slack-buffer rules **PST** and **S/RO** (~14.5–15.1 AvgTIS, ~56–57% tardy). The composite Raghu-Rajendran rule, which mixes a processing-time term, achieves the lowest AvgTIS (11.11) and the fewest tardy jobs (27.0%) here. Tune the parameters to push each rule toward whichever objective matters for your shop.
