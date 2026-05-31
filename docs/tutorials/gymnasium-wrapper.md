# Gymnasium wrapper for RL

> **Experimental**: This feature is part of `simulatte.experimental` and may change in future releases.

Goal: wrap a simulatte simulation as a [Gymnasium](https://gymnasium.farama.org/) environment so you can train reinforcement learning agents on it.

`SimulatteEnv` is a thin abstract base class that handles the Gymnasium lifecycle (`reset`, `step`, `close`) while you define the simulation setup, observation extraction, action application, reward, and termination logic.

## Define your environment

Subclass `SimulatteEnv` and implement six abstract methods:

```python
import numpy as np
from gymnasium import spaces
from simulatte.experimental.gymnasium import SimulatteEnv
from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor
from simulatte.server import Server
from simulatte.psp import PreShopPool
from simulatte.job import ProductionJob


class JobReleaseEnv(SimulatteEnv):
    def __init__(self):
        super().__init__()
        self.observation_space = spaces.Box(
            low=0.0, high=np.inf, shape=(10,), dtype=np.float64,
        )
        self.action_space = spaces.Discrete(2)  # 0 = hold, 1 = release

    def setup(self, *, seed, options):
        self.sim_env = Environment()
        self.shopfloor = ShopFloor(env=self.sim_env)
        self.servers = [
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor)
            for _ in range(5)
        ]
        self.psp = PreShopPool(env=self.sim_env, shopfloor=self.shopfloor)
        # Create jobs using self.np_random for reproducibility
        for _ in range(100):
            routing = list(self.np_random.choice(self.servers, size=3, replace=False))
            times = self.np_random.uniform(1.0, 5.0, size=3).tolist()
            job = ProductionJob(
                env=self.sim_env,
                sku="A",
                servers=routing,
                processing_times=times,
                due_date=self.sim_env.now + self.np_random.uniform(50.0, 200.0),
            )
            self.psp.add(job)

    def teardown(self):
        self.sim_env.close()

    def apply_action(self, action):
        if action == 1 and len(self.psp) > 0:
            self.psp.release(self.psp[0])
        self.sim_env.run(until=self.sim_env.now + 10)

    def get_observation(self):
        obs = []
        for s in self.servers:
            obs.extend([len(s.queue), s.utilization_rate])
        return np.array(obs, dtype=np.float64)

    def compute_reward(self, action):
        return -sum(1.0 for j in self.shopfloor.jobs_done if j.late)

    def is_terminated(self):
        return len(self.shopfloor.jobs_done) >= 100

    def is_truncated(self):
        return self.sim_env.now > 10_000
```

## Use with an RL library

The resulting environment can be used with Stable-Baselines3, CleanRL, or any Gymnasium-compatible library:

```python
env = JobReleaseEnv()
obs, info = env.reset(seed=42)

for _ in range(1000):
    action = env.action_space.sample()
    obs, reward, terminated, truncated, info = env.step(action)
    if terminated or truncated:
        obs, info = env.reset()

env.close()
```

## Method reference

| Method | Abstract? | Description |
|--------|-----------|-------------|
| `setup(*, seed, options)` | Yes | Create and configure the simulation for a new episode |
| `get_observation()` | Yes | Extract observation from simulation state |
| `apply_action(action)` | Yes | Apply action and advance simulation to next decision point |
| `compute_reward(action)` | Yes | Compute step reward (receives the action) |
| `is_terminated()` | Yes | Whether the episode ended naturally |
| `is_truncated()` | Yes | Whether the episode was cut short |
| `teardown()` | No | Clean up resources between episodes and on `close()` (default: no-op) |
| `get_info()` | No | Return step info dict, called last in `step()` only (default: `{}`) |

> **Note:** `reset()` always returns an empty info dict `{}`. It does not call `get_info()` — that hook fires only during `step()`.

## Seeding

`self.np_random` is a numpy `Generator` automatically seeded by Gymnasium when you call `reset(seed=...)`. Use it for all numpy-based randomness to get deterministic episodes:

```python
def setup(self, *, seed, options):
    processing_time = self.np_random.uniform(1.0, 5.0)
    # For non-numpy randomness, use the raw seed:
    if seed is not None:
        import random
        random.seed(seed)
```
