# Benchmark Shop Environments

This gallery compares the three standard **stylized shop environments** of the production-planning-and-control literature on a single seeded run. The "shop type" is governed entirely by the job **routing** — its length and its direction — while every other parameter (machines, processing times, target utilization, due dates) is held fixed. Each shop sits at the same load: the arrival rate is *derived* from a common target utilization, even though their mean routing lengths differ.

Each shop is a `Scenario` **preset** — `Scenario.pure_job_shop()`, `.general_flow_shop()`, `.pure_flow_shop()` — passed to any builder via its `scenario=` parameter. Here we use the immediate-release (push) baseline, so each shop is built with `build_immediate_release_system(env, scenario=Scenario.<preset>())`; the same presets compose with the workload-control builders (LumsCor, SLAR, DRACO, …) to vary the *environment* independently of the *control method*.

The three shops, from least to most directed:

- **Pure Job Shop (PJS)** — random routing length `U[1, M]`, **undirected** (random order); a *randomly routed job shop*. Each machine is visited at most once (no re-entry).
- **General Flow Shop (GFS)** — same random length `U[1, M]`, but the selected machines are **sorted** into ascending index order, giving a directed flow with typical upstream and downstream stations.
- **Pure Flow Shop (PFS)** — fixed length `M`: every job visits **all** machines in the same fixed sequence (fully directed). Because each job carries `M` operations, the shop must receive arrivals more slowly to hold the same utilization — reusing the job-shop arrival rate here would drive the shop unstable.

See also: [Utilities API](../api/utilities.md)

## Comparison

```python { .run }
"""Preconfigured PPC benchmark shops compared on one seeded run.

Pure Job Shop (PJS, undirected routing), General Flow Shop (GFS, directed/sorted
routing), and Pure Flow Shop (PFS, every job visits all machines in a fixed
order) — the standard stylized shops of the workload-control literature
(Oosterman, Land & Gaalman 2000; Kasper, Land & Teunter 2023). Each shop runs at
an arrival rate derived from a common target utilization, so all three sit at the
same load despite different mean routing lengths (PFS visits every machine, so it
arrives slower than the job shop to hold the same utilization).

Run: uv run python examples/gallery_benchmark_shops.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SEED = 42
HORIZON = 2000.0

# label -> builder thunk taking only env
SYSTEMS = {
    "PureJobShop": lambda env: build_immediate_release_system(env, scenario=Scenario.pure_job_shop()),
    "GeneralFlowShop": lambda env: build_immediate_release_system(env, scenario=Scenario.general_flow_shop()),
    "PureFlowShop": lambda env: build_immediate_release_system(env, scenario=Scenario.pure_flow_shop()),
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
    print("Benchmark shop environments (seed=42, rho=0.90)")
    print(f"{'Shop':<16}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<16}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
uv run python examples/gallery_benchmark_shops.py
```

## Output

```text
Benchmark shop environments (seed=42, rho=0.90)
Shop              Done   AvgTIS  MeanTard  %Tardy
PureJobShop       3026    19.93      1.04   11.8%
GeneralFlowShop   3036    19.71      0.74   11.5%
PureFlowShop      1752    25.71      0.38   10.3%
```

## Interpretation

The Pure Job Shop and General Flow Shop complete a near-identical number of orders (≈3030): they share the same mean routing length `E[L] = (M + 1) / 2 = 3.5`, so they receive the same derived arrival rate (mean inter-arrival ≈ 0.648) and carry the same throughput. Their only difference is routing *direction* — the GFS sorts each routing into a forward flow — which leaves aggregate flow time almost unchanged here but slightly lowers tardiness as orders no longer double back upstream.

The Pure Flow Shop stands apart: every order visits all six machines, so `E[L] = M = 6`. To hold the same 90% utilization the shop must receive orders far more slowly (mean inter-arrival ≈ 1.111), which is why it completes far fewer orders (1752) and shows a longer average time in system (each order carries six operations). The arrival rate is **derived per shop type** — by the `Scenario` preset, from its `target_utilization` and mean routing length — precisely so this comparison stays at a common utilization; reusing the job shop's faster arrival rate for the flow shop would push utilization past 1 and the queue would grow without bound.
