# Gymnasium Environment Wrapper — Design Spec

**Date:** 2026-04-29
**Status:** Approved
**Module:** `simulatte.experimental.gymnasium`

## Overview

A thin abstract base class that lets developers wrap any simulatte simulation as a Gymnasium environment for training RL agents. The wrapper is framework-agnostic — it handles Gymnasium lifecycle plumbing without encoding simulatte-specific assumptions. Users subclass it and implement six abstract methods to define their observation space, action space, simulation setup, reward function, and termination conditions. Two optional hooks (`teardown()` and `get_info()`) handle resource cleanup and step metadata.

## Requirements

- Fully general: agnostic to the specific RL decision problem (job release, dispatching, combined, etc.)
- User-defined step boundaries: the user controls time advancement inside `apply_action()`
- Single ABC interface: users subclass and override abstract methods
- User defines `observation_space`, `action_space`, `is_terminated()`, `is_truncated()`
- Seed forwarded to user's builder; `self.np_random` (seeded by Gymnasium) is the recommended RNG
- Lifecycle guards: `step()` raises if called before `reset()` or after episode end
- Resource cleanup via optional `teardown()` hook
- No rendering support in v1
- No simulatte-specific imports or assumptions in the base class

## Module Structure

**Location:** `src/simulatte/experimental/gymnasium.py` — single module, not a subpackage.

**Dependency:** `gymnasium>=1.0.0` added to `[project.dependencies]` in `pyproject.toml`.

**Public API:** `SimulatteEnv` class, re-exported from `src/simulatte/experimental/__init__.py`.

## ABC Interface

Six abstract methods and two optional hooks:

```python
from abc import ABC, abstractmethod
from typing import Any

import gymnasium


class SimulatteEnv(gymnasium.Env, ABC):

    @abstractmethod
    def setup(self, *, seed: int | None, options: dict[str, Any] | None) -> None:
        """Create and configure the simulation from scratch.

        Called at the beginning of each episode. Must set up all simulation
        state (environment, servers, jobs, etc.) needed for the episode.

        The `seed` parameter is forwarded from `reset(seed=...)`. For
        numpy-based randomness, prefer `self.np_random` — it is automatically
        seeded by Gymnasium's `super().reset()` and persists correctly across
        unseeded resets. Use `seed` directly only for sources that cannot
        consume a numpy Generator (e.g., `random.seed(seed)`).

        Pitfall: after `reset(seed=42)`, a subsequent `reset()` passes
        `seed=None`. Code like `np.random.default_rng(seed)` would get
        entropy instead of deterministic continuation. `self.np_random`
        handles this correctly.
        """

    @abstractmethod
    def get_observation(self) -> Any:
        """Extract the current observation from the simulation state."""

    @abstractmethod
    def apply_action(self, action: Any) -> None:
        """Apply the agent's action and advance the simulation to the next decision point.

        This method has a single responsibility: mutate the simulation state
        and advance time. It does not return a value. Use `get_info()` to
        surface step metadata.
        """

    @abstractmethod
    def compute_reward(self, action: Any) -> float:
        """Compute the reward for the current step.

        Receives the action so that action-dependent costs or penalties
        can be computed without storing side-effect state.
        """

    @abstractmethod
    def is_terminated(self) -> bool:
        """Whether the episode ended naturally (e.g., all jobs processed)."""

    @abstractmethod
    def is_truncated(self) -> bool:
        """Whether the episode was cut short (e.g., time budget exceeded)."""

    # --- Optional hooks (non-abstract, safe defaults) ---

    def teardown(self) -> None:
        """Clean up simulation resources from the previous episode.

        Called before `setup()` on every `reset()` after the first, and
        from `close()`. Override to release log files, database connections,
        or other resources held by the simulation.
        """

    def get_info(self) -> dict[str, Any]:
        """Return the info dict for this step.

        Called last in `step()`, after observation, reward, and termination
        have all been computed. Override to include reward decomposition,
        terminal-reason flags, diagnostic metrics, etc.
        """
        return {}
```

**Design notes:**

- `apply_action()` returns `None`. Info is decoupled into `get_info()`, called last in `step()`, so it can reference reward, termination, or any other computed value.
- `compute_reward(action)` receives the action for action-dependent rewards (penalties, costs). All other state is accessible via `self`.
- `teardown()` and `get_info()` are not abstract — they have safe defaults. Users override only when needed.

## Lifecycle Orchestration

The base class implements `reset()`, `step()`, and `close()` with state tracking:

```python
    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed, options=options)
        if self._is_initialized:
            self.teardown()
        self.setup(seed=seed, options=options)
        self._is_initialized = True
        self._done = False
        return self.get_observation(), {}

    def step(self, action: Any):
        if not self._is_initialized or self._done:
            msg = "Cannot call step() before reset() or after episode end. Call reset() first."
            raise RuntimeError(msg)
        self.apply_action(action)
        obs = self.get_observation()
        reward = self.compute_reward(action)
        terminated = self.is_terminated()
        truncated = self.is_truncated()
        if terminated or truncated:
            self._done = True
        info = self.get_info()
        return obs, reward, terminated, truncated, info

    def close(self) -> None:
        if self._is_initialized:
            self.teardown()
            self._is_initialized = False
        super().close()
```

**State tracking:**

- `_is_initialized` (`bool`, initially `False`): whether `setup()` has been called at least once. Guards `teardown()` calls and `step()` access.
- `_done` (`bool`, initially `False`): whether the current episode has ended. Set to `True` when `is_terminated()` or `is_truncated()` returns `True`. Guards against `step()` after episode end.

**Call order in `step()`:** guard check, apply action (advance simulation), observe resulting state, compute reward (with action), evaluate termination, collect info. Info is last so it can reference all prior computations.

**`super().reset()`** is called first in `reset()` as required by Gymnasium — this seeds `self.np_random`. On subsequent resets without a seed, `self.np_random` continues deterministically from its current state.

**Teardown lifecycle:** On the first `reset()`, no teardown occurs (nothing to clean up). On subsequent resets, `teardown()` is called before `setup()`. On `close()`, `teardown()` is called once if the env was initialized.

## Seeding and Reproducibility

Gymnasium provides `self.np_random` — a `numpy.random.Generator` instance seeded automatically by `super().reset(seed=...)`. This is the recommended RNG for all numpy-based randomness in the simulation.

**Contract:**

- `reset(seed=42)` → `self.np_random` is seeded with 42. `setup()` receives `seed=42`.
- `reset()` → `self.np_random` continues from its current state. `setup()` receives `seed=None`.
- `reset(seed=42)` again → identical `self.np_random` state, deterministic replay.

**Guidance for subclass authors:**

- Use `self.np_random` for numpy-based distributions (inter-arrival times, processing times, etc.).
- Use `seed` directly only for non-numpy randomness (e.g., `random.seed(seed)` for Python stdlib).
- If the simulation uses external libraries with their own RNG, derive seeds from `self.np_random` to maintain determinism: `lib_seed = int(self.np_random.integers(2**31))`.

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
        # Seed Python's random for any stdlib-based randomness
        if seed is not None:
            import random
            random.seed(seed)
        # Use self.np_random for numpy-based distributions
        self.shopfloor = ShopFloor(env=self.sim_env)
        self.servers = [
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor)
            for _ in range(5)
        ]
        self.psp = PSP(env=self.sim_env, shopfloor=self.shopfloor)

    def teardown(self):
        # Clean up simulation resources if needed
        pass

    def apply_action(self, action):
        if action == 1 and len(self.psp) > 0:
            self.psp.release(self.psp[0])
        self.sim_env.run(until=self.sim_env.now + 10)

    def get_observation(self):
        obs = []
        for server in self.servers:
            obs.extend([len(server.queue), server.utilization_rate])
        return np.array(obs, dtype=np.float64)

    def compute_reward(self, action):
        return -sum(job.lateness for job in self.shopfloor.jobs_done[-5:] if job.late)

    def is_terminated(self):
        return len(self.shopfloor.jobs_done) >= 100

    def is_truncated(self):
        return self.sim_env.now > 10_000
```

## Testing Strategy

Tests live in `tests/experimental/test_gymnasium.py`. Four levels:

1. **Contract tests** — A minimal concrete subclass with trivial implementations. Verify correct call order, return types and shapes, that `super().reset()` is called, and that lifecycle guards raise `RuntimeError` on `step()` before `reset()` and after episode end.
2. **Gymnasium baseline checks** — Run `gymnasium.utils.env_checker.check_env()` on the minimal subclass. This validates space consistency, dtype correctness, and basic API compliance. Note: this does not cover subclass-specific issues like SimPy boundary correctness or resource leaks.
3. **Seeded determinism test** — Verify that same seed + same action sequence produces identical trajectories (observations, rewards, termination points). Verify that different seeds produce different trajectories.
4. **Integration test** — A small real simulatte simulation (2 servers, a handful of jobs) wrapped as a Gymnasium env, run for a few episodes. Validates the full loop with actual SimPy event processing, including teardown between episodes.

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
