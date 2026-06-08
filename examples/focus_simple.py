from __future__ import annotations

import random

from simulatte.builders import build_focus_system
from simulatte.environment import Environment


def main() -> None:
    random.seed(42)  # Fixed seed for reproducible output; remove for non-deterministic runs.
    with Environment() as env:
        _, servers, shopfloor, _ = build_focus_system(
            env=env,
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
