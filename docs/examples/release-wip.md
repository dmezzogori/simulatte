# WIP-Cap Release Policies

This gallery compares two **WIP-cap pull** policies on a single seeded, multi-stage shop. Both hold arriving jobs in a Pre-Shop Pool (PSP) and meter them onto the floor to keep work-in-process near a target — but they enforce that cap very differently. This page is the featured home for **DRACO**.

The two policies:

- **ConWIP** — Constant Work-In-Process; a single shop-wide job-count cap. A job is released (earliest due date first) only while the floor holds fewer than `wip_cap` jobs; release is re-checked on every completion and arrival.
- **DRACO** — non-hierarchical WIP control (Kasper, Land & Teunter 2023); merges release, authorisation, and dispatching into one per-server decision. On every completion at a server it scores all candidates in that server's queue and PSP by a weighted total impact and dispatches the winner, governing dispatching with FOCUS internally.

See also: [Release Policies API](../api/release-policies.md)

## Comparison

```python { .run }
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
    "ConWIP": lambda env: build_conwip_system(env=env, wip_cap=18),
    "DRACO": lambda env: build_draco_system(env=env, wip_target=8, loop_target=4),
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
```

**Run it:**

```bash
uv run python examples/gallery_release_wip.py
```

## Output

```text
WIP-cap release policies (seed=42)
Policy    Done   AvgTIS  MeanTard  %Tardy
ConWIP    1125    12.15      1.15   22.2%
DRACO     1177     7.76      0.17    1.8%
```

## Interpretation

With its count cap sized to the load (`wip_cap=18`), ConWIP roughly keeps pace with arrivals — completing 1125 jobs at 22.2% tardy — but still visibly trails DRACO (1177 jobs, 1.8% tardy) and carries a longer floor time (12.15 vs 7.76). DRACO reaches a comparable shop WIP but, by merging release, authorisation, and dispatching into one per-server score rather than enforcing a single global count, it keeps the right jobs moving and finishes essentially every arrival with near-zero tardiness. A ConWIP cap must be sized to the load: too tight a cap (for example `wip_cap=8`) throttles throughput below the arrival rate, so the PSP backs up without bound and almost every job turns tardy. The contrast shows that even when the cap is well chosen, *how* WIP is allocated and dispatched matters as much as the cap itself.
