from __future__ import annotations

import random

from simulatte.builders import build_draco_system
from simulatte.environment import Environment


def main() -> None:
    random.seed(42)  # Fixed seed for reproducible output; remove for non-deterministic runs.
    with Environment() as env:
        _, servers, shopfloor, _, _ = build_draco_system(
            env=env,
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
