# Focus Dispatching (Production)

This example shows the **FOCUS** dispatching rule used as a standalone policy — without any workload-control layer. Jobs enter the shopfloor immediately on arrival (push system), but queue ordering at every server is governed by the FOCUS self-establishing rule, which uses five weighted mechanisms to balance urgency, remaining work, and system state.

The script uses `build_focus_system()` with equal weights across the first four mechanisms (`(0.25, 0.25, 0.25, 0.25, 0.0)`, the beta-dormant configuration from the original paper), runs for 2 000 time units, and reports throughput and average time in system.

See also: [Dispatching Rules API](../api/dispatching-rules.md)

## Code

```python
from __future__ import annotations

import random

from simulatte.builders import build_focus_system
from simulatte.environment import Environment


def main() -> None:
    random.seed(42)  # Fixed seed for reproducible output; remove for non-deterministic runs.
    with Environment() as env:
        _, servers, shopfloor, _ = build_focus_system(
            env,
            focus_weights=(0.25, 0.25, 0.25, 0.25, 0.0),
        )
        env.run(until=2000.0)

        done = shopfloor.jobs_done
        print("FOCUS standalone-dispatching example (immediate release)")
        print(f"Servers: {len(servers)}")
        print(f"Simulation time: {env.now:.1f}")
        print(f"Jobs completed: {len(done)}")
        if done:
            print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")


if __name__ == "__main__":
    main()
```

**Run it:**

```bash
uv run python examples/focus_simple.py
```

## Output

```text
FOCUS standalone-dispatching example (immediate release)
Servers: 6
Simulation time: 2000.0
Jobs completed: 3036
Avg time in system: 14.63
```

## Interpretation

With no release control, FOCUS completes 3 036 jobs and shows an average shop-floor residence of **14.63** time units — nearly double DRACO's 8.00 under the same seeded arrival stream and server load — because queues grow freely when WIP is uncapped. The comparison illustrates how dispatching alone cannot compensate for uncontrolled WIP. For best results, combine FOCUS with a release policy such as DRACO (which uses FOCUS internally) or LumsCor/SLAR.
