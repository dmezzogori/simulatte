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
        mean_tardiness = sum(max(0.0, j.lateness) for j in done) / n_done if n_done else 0.0
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
        "Immediate": lambda env: build_immediate_release_system(env=env),
        "LumsCor": lambda env: build_lumscor_system(env=env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2),
        "SLAR": lambda env: build_slar_system(env=env, allowance_factor=3.0),
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
