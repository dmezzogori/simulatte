# Workload-Control Release Policies

This gallery contrasts a **push** baseline against four **workload-control pull** policies on a single seeded, multi-stage shop. The push baseline (immediate release) lets every job onto the floor the instant it arrives. The pull policies instead hold jobs in a Pre-Shop Pool (PSP) and release them only when the shop's aggregate load stays within configured norms — trading PSP waiting time for a calmer, less congested floor.

The five systems:

- **Immediate** — push baseline; no release control, every arrival enters the shop at once.
- **LumsCor** — LUMS-COR load-based release; releases against corrected workload norms checked periodically, with starvation avoidance.
- **SLAR** — Superfluous Load Avoidance Release; releases on planned-slack starvation risk and urgent-job insertion, avoiding load that does not help.
- **SLAR-Limit** — load-bounded SLAR; adds a workload-norm limit to SLAR's urgent-insertion branch.
- **Continuous** — Continuous Release; releases continuously (on each completion and arrival) whenever a job's corrected load contribution fits every server norm.

See also: [Release Policies API](../api/release-policies.md)

## Comparison

```python { .run }
"""Workload-control release policies compared on one seeded shop.

Immediate release (push baseline) vs LumsCor, SLAR, SLAR-Limit, and Continuous
Release — all workload-control pull policies that hold jobs in a Pre-Shop Pool
and release against load norms.

Run: uv run python examples/gallery_release_workload.py
"""

from __future__ import annotations

from simulatte.builders import (
    build_continuous_release_system,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
)
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

# label -> builder thunk taking only env
SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env=env),
    "LumsCor": lambda env: build_lumscor_system(env=env, check_timeout=10.0, wl_norm_level=6.0, allowance_factor=2),
    "SLAR": lambda env: build_slar_system(env=env, allowance_factor=3.0),
    "SLAR-Limit": lambda env: build_slar_limit_system(env=env, allowance_factor=3.0, wl_norm_level=6.0),
    "Continuous": lambda env: build_continuous_release_system(env=env, wl_norm_level=6.0, allowance_factor=2),
}


def run_system(builder) -> tuple[int, float, float, float]:
    with Environment(seed=SEED) as env:
        _psp, _servers, shop_floor, _router, _policy = builder(env)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("Workload-control release policies (seed=42)")
    print(f"{'Policy':<12}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<12}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
uv run python examples/gallery_release_workload.py
```

## Output

```text
Workload-control release policies (seed=42)
Policy        Done   AvgTIS  MeanTard  %Tardy
Immediate     1162    18.69      0.52    8.1%
LumsCor       1162     9.46      0.58    5.5%
SLAR          1162    10.31      0.01    0.3%
SLAR-Limit    1161     9.39      0.37    2.8%
Continuous    1158     9.94      0.75    4.7%
```

## Interpretation

All four pull policies cut average time in system by roughly 45–50% versus the push baseline (≈9.4–10.3 vs 18.69), because they keep the floor lightly loaded and queues short. But `average_time_in_system` measures only first-server-entry to completion — it excludes the time a job waits in the PSP. `MeanTard` and `%Tardy` are derived from `job.lateness`, which is measured against the due date and so includes the *full* sojourn, PSP wait included. That is why the pull policies can show much lower AvgTIS yet comparable or even higher tardiness: the waiting they remove from the floor is partly pushed back into the pool. SLAR is the standout here, holding tardiness near zero while still cutting flow time by about 45%. For a deeper, step-by-step walkthrough of these trade-offs, see [comparing release policies](../tutorials/comparing-release-policies.md).
