# Gymnasium Environment Wrapper — Design Spec

**Date:** 2026-04-29
**Status:** Approved
**Module:** `simulatte.experimental.gymnasium`

## Overview

A thin abstract base class that lets developers wrap any simulatte simulation as a Gymnasium environment for training RL agents. The wrapper is framework-agnostic — it handles Gymnasium lifecycle plumbing without encoding simulatte-specific assumptions. Users subclass it and implement six abstract methods to define their observation space, action space, simulation setup, reward function, and termination conditions.

## Requirements

- Fully general: agnostic to the specific RL decision problem (job release, dispatching, combined, etc.)
- User-defined step boundaries: the user controls time advancement inside `apply_action()`
- Single ABC interface: users subclass and override abstract methods
- User defines `observation_space`, `action_space`, `is_terminated()`, `is_truncated()`
- Seed forwarded to user's builder with no automatic seeding magic
- No rendering support in v1
- No simulatte-specific imports or assumptions in the base class

## Module Structure

**Location:** `src/simulatte/experimental/gymnasium.py` — single module, not a subpackage.

**Dependency:** `gymnasium>=1.0.0` added to `[project.dependencies]` in `pyproject.toml`.

**Public API:** `SimulatteEnv` class, re-exported from `src/simulatte/experimental/__init__.py`.

## ABC Interface

```python
from abc import ABC, abstractmethod
from typing import Any

import gymnasium
import numpy as np


class SimulatteEnv(gymnasium.Env, ABC):

    @abstractmethod
    def setup(self, *, seed: int | None, options: dict[str, Any] | None) -> None:
        """Create and configure the simulation from scratch.

        Called at the beginning of each episode. Must set up all simulation
        state (environment, servers, jobs, etc.) needed for the episode.
        """

    @abstractmethod
    def get_observation(self) -> Any:
        """Extract the current observation from the simulation state."""

    @abstractmethod
    def apply_action(self, action: Any) -> dict[str, Any]:
        """Apply the agent's action and advance the simulation to the next decision point.

        Returns the info dict for this step.
        """

    @abstractmethod
    def compute_reward(self) -> float:
        """Compute the reward for the current step."""

    @abstractmethod
    def is_terminated(self) -> bool:
        """Whether the episode ended naturally (e.g., all jobs processed)."""

    @abstractmethod
    def is_truncated(self) -> bool:
        """Whether the episode was cut short (e.g., time budget exceeded)."""
```

## Lifecycle Orchestration

The base class implements `reset()` and `step()`:

```python
    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed, options=options)
        self.setup(seed=seed, options=options)
        return self.get_observation(), {}

    def step(self, action: Any):
        info = self.apply_action(action)
        obs = self.get_observation()
        reward = self.compute_reward()
        terminated = self.is_terminated()
        truncated = self.is_truncated()
        return obs, reward, terminated, truncated, info
```

**Call order in `step()`:** action first (advances simulation), then observe the resulting state, then evaluate reward and termination on the new state.

**`super().reset()`** is called first in `reset()` as required by Gymnasium — this handles internal `self.np_random` seeding.

**`reset()` info dict** is always empty. If setup diagnostics are needed later, `setup()` can be changed to return an optional dict.

## Usage Example

A job-release control environment:

```python
import numpy as np
from gymnasium import spaces
from simulatte.experimental.gymnasium import SimulatteEnv
from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor
from simulatte.server import Server
from simulatte.psp import PSP


class JobReleaseEnv(SimulatteEnv):
    def __init__(self):
        super().__init__()
        self.observation_space = spaces.Box(
            low=0, high=np.inf, shape=(10,), dtype=np.float64
        )
        self.action_space = spaces.Discrete(2)  # release or hold

    def setup(self, *, seed, options):
        self.sim_env = Environment()
        rng = np.random.default_rng(seed)
        self.shopfloor = ShopFloor(env=self.sim_env)
        self.servers = [
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor)
            for _ in range(5)
        ]
        self.psp = PSP(env=self.sim_env, shopfloor=self.shopfloor)

    def apply_action(self, action):
        if action == 1 and len(self.psp) > 0:
            self.psp.release(self.psp.jobs[0])
        self.sim_env.run(until=self.sim_env.now + 10)
        return {}

    def get_observation(self):
        obs = []
        for server in self.servers:
            obs.extend([len(server.queue), server.utilization_rate])
        return np.array(obs, dtype=np.float64)

    def compute_reward(self):
        return -sum(job.lateness for job in self.shopfloor.jobs_done[-5:] if job.late)

    def is_terminated(self):
        return len(self.shopfloor.jobs_done) >= 100

    def is_truncated(self):
        return self.sim_env.now > 10_000
```

## Testing Strategy

Tests live in `tests/experimental/test_gymnasium.py`. Three levels:

1. **Contract tests** — A minimal concrete subclass with trivial implementations. Verify correct call order, return types and shapes, and that `super().reset()` is called.
2. **Gymnasium compliance** — Run `gymnasium.utils.env_checker.check_env()` on the minimal subclass. This validates space consistency, dtype correctness, and full API compliance.
3. **Integration test** — A small real simulatte simulation (2 servers, a handful of jobs) wrapped as a Gymnasium env, run for a few episodes. Validates the full loop with actual SimPy event processing.

## Scope Boundaries

**Not in scope:**

- Rendering support
- Built-in observation or reward helpers
- `gymnasium.make()` registration machinery
- Vectorized environment support (users use `gymnasium.vector.SyncVectorEnv` directly)
- SimPy-Gymnasium synchronization primitives
- Simulatte-specific imports or utilities in the base class

**Future growth path (not implemented now):**

- Rendering via optional `render()` override
- Simulatte-aware utilities (metric snapshots, logging integration)
- Synchronization primitives for event-driven decision points
- All additive — nothing in this design blocks them
