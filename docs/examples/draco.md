# Draco Release (Production)

This example shows the **DRACO** (non-hierarchical WIP control) policy in action. DRACO merges release, authorisation, and dispatching into a single per-server decision: on every job completion it scores every candidate in the queue and the pre-shop pool by a weighted total impact and selects the highest scorer. The result is a pull system that keeps WIP near a configurable target without separate release and dispatching layers.

The script uses `build_draco_system()` with `wip_target=8` and `loop_target=4`, runs for 2 000 time units, and reports throughput, average time in system, and server utilisation.

See also: [Release Policies API](../api/release-policies.md)

## Code

```python { .run }
from __future__ import annotations

import random

from simulatte.builders import build_draco_system
from simulatte.environment import Environment


def main() -> None:
    random.seed(42)  # Fixed seed for reproducible output; remove for non-deterministic runs.
    with Environment() as env:
        _, servers, shopfloor, _ = build_draco_system(
            env,
            wip_target=8,
            loop_target=4,
        )
        env.run(until=2000.0)

        done = shopfloor.jobs_done
        print("DRACO non-hierarchical WIP-control example")
        print(f"Servers: {len(servers)}")
        print(f"Simulation time: {env.now:.1f}")
        print(f"Jobs completed: {len(done)}")
        if done:
            print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")
        print(f"Avg server utilization: {sum(s.utilization_rate for s in servers) / len(servers):.1%}")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
uv run python examples/draco_simple.py
```

## Output

```text
DRACO non-hierarchical WIP-control example
Servers: 6
Simulation time: 2000.0
Jobs completed: 3036
Avg time in system: 8.00
Avg server utilization: 87.9%
```

## Interpretation

DRACO completes 3 036 jobs in 2 000 time units with an average shop-floor residence of **8.00** time units per job. Note that `average_time_in_system` measures first-server-entry to completion — it excludes PSP wait, so it is not directly comparable to a raw makespan figure. Server utilisation stays at 87.9 %, showing that tight WIP control does not starve servers at this load level. The `wip_target` and `loop_target` parameters govern how aggressively jobs are pulled: lower values reduce WIP and lateness at the cost of slightly lower throughput. To compare DRACO against other release policies on equal-metric terms, see the [Comparing release policies tutorial](../tutorials/comparing-release-policies.md).
