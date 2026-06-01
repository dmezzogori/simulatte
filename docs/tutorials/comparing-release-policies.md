# Comparing Release Policies

This tutorial builds a six-server job shop and compares three release policies side by side:

- **Immediate Release** — push system, no workload control
- **LumsCor** — load-based pull system (Land's LUMS-COR)
- **SLAR** — superfluous-load avoidance pull system

By running each policy with the same random seed you get a controlled, apples-to-apples comparison.

> Prefer to run it live? The [workload-control](../examples/release-workload.md) and [WIP-cap](../examples/release-wip.md) galleries compare these and related policies in-browser.

---

## Why release control matters

In a push system every arriving job enters the shop floor immediately. At high utilisation (≈ 90 %) queues grow without bound and most jobs finish late. A **release policy** holds new jobs in a pre-shop pool (PSP) and only releases them when servers can absorb the load — trading a little PSP waiting time for dramatically shorter queue times and better due-date performance.

Simulatte ships three ready-made builder functions that encapsulate the full wiring of shop floor, servers, pre-shop pool, router, and release triggers. That makes policy comparison a matter of calling each builder in turn.

---

## Scenario setup

Six servers, exponential inter-arrivals (λ ≈ 1.54/time-unit), truncated 2-Erlang service times (µ = 2.0), uniform due dates at 30–45 time units after arrival. Target utilisation ≈ 87–88 %. Simulation duration: 2 000 time units. Fixed seed: 42.

---

## Running the three policies

Each builder returns `(psp, servers, shopfloor, router)`. The PSP is `None` for the push system and a `PreShopPool` instance for the pull systems.

```python
"""Compare Immediate Release, LumsCor, and SLAR release policies.

Runs each policy for a fixed simulation time using the same random seed,
then prints a comparison table of key performance metrics.

Simplification note: uses a single run per policy (same seed for each)
rather than multi-seed averaging, to keep the script self-contained and
deterministic. The same seed gives identical arrival / service-time
streams to all three policies (common-random-numbers design).
"""

from __future__ import annotations

import random

from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
)
from simulatte.environment import Environment

SEED = 42
SIM_TIME = 2000.0


def run_policy(builder_fn, seed: int = SEED, until: float = SIM_TIME) -> dict:
    """Run a single simulation with the given builder and seed."""
    random.seed(seed)
    with Environment() as env:
        psp, servers, shopfloor, _router = builder_fn(env)
        env.run(until=until)

        done = shopfloor.jobs_done
        n_done = len(done)
        psp_size = len(psp) if psp is not None else 0
        n_late = sum(1 for j in done if j.late)
        mean_tardiness = (
            sum(max(0.0, j.lateness) for j in done) / n_done if n_done else 0.0
        )
        mean_makespan = sum(j.makespan for j in done) / n_done if n_done else 0.0
        avg_util = sum(s.utilization_rate for s in servers) / len(servers)
        # End-of-simulation WIP: remaining work queued / in-progress on the shop floor
        end_wip = sum(shopfloor.wip.values())

    return {
        "completed": n_done,
        "psp_remaining": psp_size,
        "late_pct": n_late / n_done * 100 if n_done else 0.0,
        "mean_tardiness": mean_tardiness,
        "mean_makespan": mean_makespan,
        "end_wip": end_wip,
        "avg_util_pct": avg_util * 100,
    }


def main() -> None:
    policies = {
        "Immediate": lambda env: build_immediate_release_system(env),
        "LumsCor": lambda env: build_lumscor_system(
            env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2
        ),
        "SLAR": lambda env: build_slar_system(env, allowance_factor=3.0),
    }

    results = {name: run_policy(fn) for name, fn in policies.items()}

    print(f"Release policy comparison  (seed={SEED}, sim_time={SIM_TIME:.0f})")
    print()

    headers = ["Policy", "Done", "PSP left", "Late %", "Mean tardy", "Mean span", "End WIP", "Util %"]
    widths = [10, 6, 8, 7, 10, 10, 9, 7]
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False))
    print(header_line)
    print("-" * len(header_line))

    for name, r in results.items():
        row = [
            name.ljust(widths[0]),
            str(r["completed"]).ljust(widths[1]),
            str(r["psp_remaining"]).ljust(widths[2]),
            f"{r['late_pct']:.1f}%".ljust(widths[3]),
            f"{r['mean_tardiness']:.2f}".ljust(widths[4]),
            f"{r['mean_makespan']:.2f}".ljust(widths[5]),
            f"{r['end_wip']:.1f}".ljust(widths[6]),
            f"{r['avg_util_pct']:.1f}%".ljust(widths[7]),
        ]
        print("  ".join(row))

    print()
    print("Columns:")
    print("  Done       = jobs completed by sim_time")
    print("  PSP left   = jobs still held in the pre-shop pool at end")
    print("  Late %     = % of completed jobs that finished after their due date")
    print("  Mean tardy = average tardiness (0 for on-time / early jobs)")
    print("  Mean span  = average makespan (creation -> finish)")
    print("  End WIP    = total remaining work on the shop floor at sim_time")
    print("  Util %     = average server utilization")


if __name__ == "__main__":
    main()
```

The complete runnable script is at [`examples/comparing_release_policies.py`](https://github.com/dmezzogori/simulatte/blob/main/examples/comparing_release_policies.py).

**Run it:**

```bash
uv run python examples/comparing_release_policies.py
```

---

## Results

```text
Release policy comparison  (seed=42, sim_time=2000)

Policy      Done    PSP left  Late %   Mean tardy  Mean span   End WIP    Util % 
---------------------------------------------------------------------------------
Immediate   3026    0         11.9%    1.04        19.91       91.8       87.7%  
LumsCor     3017    24        12.2%    1.82        20.89       27.7       87.6%  
SLAR        3029    14        0.9%     0.05        19.54       55.9       87.8%  

Columns:
  Done       = jobs completed by sim_time
  PSP left   = jobs still held in the pre-shop pool at end
  Late %     = % of completed jobs that finished after their due date
  Mean tardy = average tardiness (0 for on-time / early jobs)
  Mean span  = average makespan (creation -> finish)
  End WIP    = total remaining work on the shop floor at sim_time
  Util %     = average server utilization
```

---

## Interpretation

**Immediate Release** pushes all 3 026 jobs straight onto the shop floor. There is no PSP, so End WIP is high (91.8 units of remaining work) and 11.9 % of jobs finish late.

**LumsCor** holds 24 jobs in the PSP at end of simulation. Its workload norm keeps queues short (End WIP = 27.7, the lowest in the table), but because it checks periodically (`check_timeout=10`), the PSP waiting time it introduces means jobs held in the pool may already be at or past their due date when released — adding tardiness (1.82 vs 1.04 for Immediate) without a corresponding WIP benefit. (LumsCor gate-checks each candidate individually against the workload norm; it is not a true batch burst.)

**SLAR** keeps late-ness dramatically lower (0.9 %) with a mean tardiness of just 0.05. It is reactive rather than periodic: it only releases when a server risks starvation or an urgent job needs insertion. That responsiveness gives SLAR the lowest tardiness in the table, while its End WIP (55.9) sits between Immediate (91.8) and LumsCor (27.7) — SLAR trades tighter WIP control for better due-date performance.

### When to choose which policy

| Policy | Best for |
|--------|----------|
| Immediate Release | Baseline; low-utilisation shops where queues stay short naturally |
| LumsCor | Shops with predictable, stable arrival patterns; simple to tune via `wl_norm_level` |
| SLAR | High-utilisation shops with tight due dates; reacts to starvation and urgency events |

---

## Next steps

- [Release control and dispatching](release-control-and-dispatching.md) — deeper walkthrough of PSP, triggers, and composable policies
- [Release Policies API](../api/release-policies.md) — full parameter reference
