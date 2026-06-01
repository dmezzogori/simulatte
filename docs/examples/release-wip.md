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
    "ConWIP": lambda env: build_conwip_system(env, wip_cap=8),
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
```

**Run it:**

```bash
uv run python examples/gallery_release_wip.py
```

## Output

```text
WIP-cap release policies (seed=42)
Policy    Done   AvgTIS  MeanTard  %Tardy
ConWIP     918     6.89     61.85   79.7%
DRACO     1162     7.60      0.12    1.2%
```

## Interpretation

Both policies achieve a tight, low time in system on the floor (≈6.9–7.6) by capping WIP, but the outcomes diverge sharply. ConWIP's blunt shop-wide count cap of 8 throttles release so hard at this arrival load that jobs back up in the PSP: it completes far fewer jobs (918 vs 1162) and, because lateness is measured against the due date including PSP wait, almost 80% finish tardy. DRACO reaches the same WIP target but, by merging release, authorisation, and dispatching into one per-server score (rather than a single global count), it keeps the floor fed and the right jobs moving — completing essentially every arrival with near-zero tardiness. The contrast shows that *how* a WIP cap is allocated and dispatched matters as much as the cap itself.
