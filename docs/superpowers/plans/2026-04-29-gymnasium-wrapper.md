# Gymnasium Environment Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a thin Gymnasium ABC (`SimulatteEnv`) in `simulatte.experimental.gymnasium` that lets developers wrap simulations as Gymnasium environments for RL training.

**Architecture:** Single module with one ABC. Six abstract methods for user-defined simulation lifecycle, two optional hooks for cleanup and info. The base class handles Gymnasium `reset()`/`step()`/`close()` orchestration with lifecycle guards and state tracking. Framework-agnostic — no simulatte-specific imports.

**Tech Stack:** Python 3.12+, gymnasium >= 1.0.0, pytest, ruff

**Spec:** `docs/superpowers/specs/2026-04-29-gymnasium-wrapper-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/simulatte/experimental/gymnasium.py` | `SimulatteEnv` ABC with lifecycle orchestration |
| Modify | `src/simulatte/experimental/__init__.py` | Re-export `SimulatteEnv` |
| Modify | `pyproject.toml` | Add `gymnasium>=1.0.0` dependency |
| Create | `tests/experimental/test_gymnasium.py` | Contract, compliance, determinism, integration tests |

---

### Task 1: Add gymnasium dependency

**Files:**
- Modify: `pyproject.toml:25-31`

- [ ] **Step 1: Add gymnasium to project dependencies**

In `pyproject.toml`, add `"gymnasium>=1.0.0"` to the `dependencies` list:

```toml
dependencies = [
    "simpy>=4.0.1",
    "matplotlib>=3.7.2",
    "tabulate>=0.9.0",
    "loguru>=0.7.2",
    "tqdm>=4.67.1",
    "gymnasium>=1.0.0",
]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync --dev`
Expected: gymnasium is installed, no errors.

- [ ] **Step 3: Verify import**

Run: `uv run python -c "import gymnasium; print(gymnasium.__version__)"`
Expected: Prints version >= 1.0.0

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add gymnasium dependency"
```

---

### Task 2: Write contract tests for lifecycle guards

**Files:**
- Create: `tests/experimental/test_gymnasium.py`

These tests define the contract BEFORE the implementation exists. They will fail until Task 4.

- [ ] **Step 1: Write the test file with a minimal concrete subclass and all contract tests**

Create `tests/experimental/test_gymnasium.py`:

```python
from __future__ import annotations

import numpy as np
import pytest
from gymnasium import spaces

from simulatte.experimental.gymnasium import SimulatteEnv


class MinimalEnv(SimulatteEnv):
    """Minimal concrete subclass for testing the base class contract."""

    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Box(low=0.0, high=1.0, shape=(2,), dtype=np.float64)
        self.action_space = spaces.Discrete(2)
        self.step_count = 0
        self.max_steps = 3
        self.setup_calls: list[int | None] = []
        self.teardown_calls: int = 0

    def setup(self, *, seed: int | None, options: dict | None) -> None:
        self.step_count = 0
        self.setup_calls.append(seed)

    def teardown(self) -> None:
        self.teardown_calls += 1

    def get_observation(self):
        return np.array([0.5, 0.5], dtype=np.float64)

    def apply_action(self, action) -> None:
        self.step_count += 1

    def compute_reward(self, action) -> float:
        return 1.0

    def is_terminated(self) -> bool:
        return self.step_count >= self.max_steps

    def is_truncated(self) -> bool:
        return False


class TestLifecycleGuards:
    """Verify that step() raises RuntimeError in invalid states."""

    def test_step_before_reset_raises(self) -> None:
        env = MinimalEnv()
        with pytest.raises(RuntimeError, match="Call reset\\(\\) first"):
            env.step(0)

    def test_step_after_terminated_raises(self) -> None:
        env = MinimalEnv()
        env.max_steps = 1
        env.reset(seed=42)
        _, _, terminated, _, _ = env.step(0)
        assert terminated
        with pytest.raises(RuntimeError, match="Call reset\\(\\) first"):
            env.step(0)

    def test_step_after_truncated_raises(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        # Override is_truncated to return True on first step
        env.is_truncated = lambda: True  # type: ignore[assignment]
        _, _, _, truncated, _ = env.step(0)
        assert truncated
        with pytest.raises(RuntimeError, match="Call reset\\(\\) first"):
            env.step(0)


class TestResetLifecycle:
    """Verify reset/teardown call ordering."""

    def test_first_reset_does_not_call_teardown(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        assert env.teardown_calls == 0
        assert env.setup_calls == [42]

    def test_second_reset_calls_teardown_before_setup(self) -> None:
        env = MinimalEnv()
        env.reset(seed=1)
        env.reset(seed=2)
        assert env.teardown_calls == 1
        assert env.setup_calls == [1, 2]

    def test_reset_after_done_resets_lifecycle(self) -> None:
        env = MinimalEnv()
        env.max_steps = 1
        env.reset(seed=10)
        env.step(0)  # terminates
        env.reset(seed=20)  # should work, not raise
        obs, _ = env.reset(seed=30)
        assert obs is not None


class TestCloseLifecycle:
    """Verify close() teardown behavior."""

    def test_close_without_reset_does_not_call_teardown(self) -> None:
        env = MinimalEnv()
        env.close()
        assert env.teardown_calls == 0

    def test_close_after_reset_calls_teardown(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        env.close()
        assert env.teardown_calls == 1

    def test_close_then_step_raises(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        env.close()
        with pytest.raises(RuntimeError, match="Call reset\\(\\) first"):
            env.step(0)


class TestStepCallOrder:
    """Verify step() returns correct 5-tuple in correct order."""

    def test_step_returns_five_tuple(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        result = env.step(0)
        assert len(result) == 5
        obs, reward, terminated, truncated, info = result
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_reset_returns_obs_and_info(self) -> None:
        env = MinimalEnv()
        result = env.reset(seed=42)
        assert len(result) == 2
        obs, info = result
        assert isinstance(obs, np.ndarray)
        assert isinstance(info, dict)


class TestGetInfo:
    """Verify get_info() is called and its return is used."""

    def test_default_get_info_returns_empty_dict(self) -> None:
        env = MinimalEnv()
        env.reset(seed=42)
        _, _, _, _, info = env.step(0)
        assert info == {}

    def test_custom_get_info_is_used(self) -> None:
        class InfoEnv(MinimalEnv):
            def get_info(self):
                return {"step": self.step_count}

        env = InfoEnv()
        env.reset(seed=42)
        _, _, _, _, info = env.step(0)
        assert info == {"step": 1}


class TestComputeRewardReceivesAction:
    """Verify compute_reward() receives the action passed to step()."""

    def test_reward_receives_action(self) -> None:
        class ActionRewardEnv(MinimalEnv):
            def compute_reward(self, action) -> float:
                return -10.0 if action == 1 else 0.0

        env = ActionRewardEnv()
        env.reset(seed=42)
        _, reward_0, _, _, _ = env.step(0)
        assert reward_0 == 0.0

        env.reset(seed=42)
        _, reward_1, _, _, _ = env.step(1)
        assert reward_1 == -10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/experimental/test_gymnasium.py -v`
Expected: ImportError — `simulatte.experimental.gymnasium` does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/experimental/test_gymnasium.py
git commit -m "test: add contract tests for SimulatteEnv lifecycle"
```

---

### Task 3: Write seeded determinism tests

**Files:**
- Modify: `tests/experimental/test_gymnasium.py`

- [ ] **Step 1: Add a stochastic subclass and determinism tests**

Append to `tests/experimental/test_gymnasium.py`:

```python
class StochasticEnv(SimulatteEnv):
    """Subclass that uses self.np_random to produce stochastic observations."""

    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Box(low=0.0, high=100.0, shape=(1,), dtype=np.float64)
        self.action_space = spaces.Discrete(2)
        self.step_count = 0

    def setup(self, *, seed: int | None, options: dict | None) -> None:
        self.step_count = 0

    def get_observation(self):
        return np.array([self.np_random.uniform(0.0, 100.0)], dtype=np.float64)

    def apply_action(self, action) -> None:
        self.step_count += 1

    def compute_reward(self, action) -> float:
        return self.np_random.uniform(-1.0, 1.0)

    def is_terminated(self) -> bool:
        return self.step_count >= 5

    def is_truncated(self) -> bool:
        return False


class TestSeededDeterminism:
    """Verify that seeding produces reproducible trajectories."""

    @staticmethod
    def _collect_trajectory(env: StochasticEnv, seed: int, actions: list[int]) -> tuple[list, list]:
        observations = []
        rewards = []
        obs, _ = env.reset(seed=seed)
        observations.append(obs.copy())
        for action in actions:
            obs, reward, terminated, truncated, _ = env.step(action)
            observations.append(obs.copy())
            rewards.append(reward)
            if terminated or truncated:
                break
        return observations, rewards

    def test_same_seed_same_trajectory(self) -> None:
        env = StochasticEnv()
        actions = [0, 1, 0, 1, 0]
        obs_1, rew_1 = self._collect_trajectory(env, seed=42, actions=actions)
        obs_2, rew_2 = self._collect_trajectory(env, seed=42, actions=actions)
        for o1, o2 in zip(obs_1, obs_2, strict=True):
            np.testing.assert_array_equal(o1, o2)
        assert rew_1 == rew_2

    def test_different_seed_different_trajectory(self) -> None:
        env = StochasticEnv()
        actions = [0, 1, 0, 1, 0]
        obs_1, _ = self._collect_trajectory(env, seed=42, actions=actions)
        obs_2, _ = self._collect_trajectory(env, seed=99, actions=actions)
        any_different = any(not np.array_equal(o1, o2) for o1, o2 in zip(obs_1, obs_2, strict=True))
        assert any_different
```

- [ ] **Step 2: Run tests to verify they still fail (ImportError)**

Run: `uv run pytest tests/experimental/test_gymnasium.py -v`
Expected: ImportError — module not yet implemented.

- [ ] **Step 3: Commit**

```bash
git add tests/experimental/test_gymnasium.py
git commit -m "test: add seeded determinism tests for SimulatteEnv"
```

---

### Task 4: Implement SimulatteEnv

**Files:**
- Create: `src/simulatte/experimental/gymnasium.py`

- [ ] **Step 1: Write the full implementation**

Create `src/simulatte/experimental/gymnasium.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import gymnasium


class SimulatteEnv(gymnasium.Env, ABC):
    """Base class for wrapping simulations as Gymnasium environments.

    Subclass this and implement the six abstract methods to define your
    simulation's observation space, action space, setup, reward, and
    termination logic. The base class handles Gymnasium lifecycle
    orchestration, state tracking, and resource cleanup.

    Two optional hooks are available:
    - ``teardown()``: clean up simulation resources between episodes.
    - ``get_info()``: return step metadata after all computations.

    Subclasses must set ``observation_space`` and ``action_space`` in
    ``__init__`` before calling ``reset()``.
    """

    _is_initialized: bool = False
    _done: bool = False

    @abstractmethod
    def setup(self, *, seed: int | None, options: dict[str, Any] | None) -> None:
        """Create and configure the simulation from scratch.

        Called at the beginning of each episode. Must set up all simulation
        state needed for the episode.

        For numpy-based randomness, prefer ``self.np_random`` — it is
        automatically seeded by Gymnasium and persists correctly across
        unseeded resets.
        """

    @abstractmethod
    def get_observation(self) -> Any:
        """Extract the current observation from the simulation state."""

    @abstractmethod
    def apply_action(self, action: Any) -> None:
        """Apply the agent's action and advance the simulation to the next decision point."""

    @abstractmethod
    def compute_reward(self, action: Any) -> float:
        """Compute the reward for the current step."""

    @abstractmethod
    def is_terminated(self) -> bool:
        """Whether the episode ended naturally."""

    @abstractmethod
    def is_truncated(self) -> bool:
        """Whether the episode was cut short."""

    def teardown(self) -> None:
        """Clean up simulation resources from the previous episode.

        Called before ``setup()`` on every ``reset()`` after the first,
        and from ``close()``.
        """

    def get_info(self) -> dict[str, Any]:
        """Return the info dict for this step.

        Called last in ``step()``, after observation, reward, and
        termination have all been computed.
        """
        return {}

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        super().reset(seed=seed, options=options)
        if self._is_initialized:
            self.teardown()
        self.setup(seed=seed, options=options)
        self._is_initialized = True
        self._done = False
        return self.get_observation(), {}

    def step(self, action: Any) -> tuple[Any, float, bool, bool, dict[str, Any]]:
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

- [ ] **Step 2: Run the contract and determinism tests**

Run: `uv run pytest tests/experimental/test_gymnasium.py -v`
Expected: All tests pass.

- [ ] **Step 3: Run ruff to check style compliance**

Run: `uv run ruff check src/simulatte/experimental/gymnasium.py`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/experimental/gymnasium.py
git commit -m "feat: implement SimulatteEnv gymnasium wrapper"
```

---

### Task 5: Export from experimental __init__.py

**Files:**
- Modify: `src/simulatte/experimental/__init__.py`

- [ ] **Step 1: Add the import and __all__ entry**

Add the import of `SimulatteEnv` and add it to `__all__` in `src/simulatte/experimental/__init__.py`:

```python
"""Experimental features for material handling, warehouse, and AGV operations.

This module contains less mature implementations that are complete and tested
but not yet considered stable for the core API. These features may change
in future releases.

Exports:
    AGV: Automated guided vehicle server for transport operations.
    MaterialCoordinator: Orchestrates material delivery with FIFO blocking.
    MaterialSystem: Type alias for material handling system tuple.
    MaterialSystemBuilder: Factory for building material handling systems.
    SimulatteEnv: Gymnasium environment base class for RL integration.
    TransportJob: Job type for AGV transport operations.
    Warehouse: Warehouse server with inventory containers.
    WarehouseJob: Job type for warehouse pick/put operations.
"""

from __future__ import annotations

from simulatte.experimental.agv import AGV
from simulatte.experimental.builders import MaterialSystemBuilder
from simulatte.experimental.gymnasium import SimulatteEnv
from simulatte.experimental.job import TransportJob, WarehouseJob
from simulatte.experimental.materials import MaterialCoordinator
from simulatte.experimental.typing import MaterialSystem
from simulatte.experimental.warehouse import Warehouse

__all__ = [
    "AGV",
    "MaterialCoordinator",
    "MaterialSystem",
    "MaterialSystemBuilder",
    "SimulatteEnv",
    "TransportJob",
    "Warehouse",
    "WarehouseJob",
]
```

- [ ] **Step 2: Verify the re-export works**

Run: `uv run python -c "from simulatte.experimental import SimulatteEnv; print(SimulatteEnv)"`
Expected: Prints `<class 'simulatte.experimental.gymnasium.SimulatteEnv'>`

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `uv run pytest -v`
Expected: All tests pass, including the new gymnasium tests.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/experimental/__init__.py
git commit -m "feat: export SimulatteEnv from experimental package"
```

---

### Task 6: Add Gymnasium baseline compliance test

**Files:**
- Modify: `tests/experimental/test_gymnasium.py`

- [ ] **Step 1: Add the check_env test**

Append to `tests/experimental/test_gymnasium.py`:

```python
from gymnasium.utils.env_checker import check_env


class TestGymnasiumCompliance:
    """Run Gymnasium's built-in env checker for baseline API compliance."""

    def test_check_env_passes(self) -> None:
        env = MinimalEnv()
        check_env(env.unwrapped, skip_render_check=True)
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/experimental/test_gymnasium.py::TestGymnasiumCompliance -v`
Expected: PASS. If `check_env` raises a warning or error, fix the issue in `src/simulatte/experimental/gymnasium.py` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/experimental/test_gymnasium.py
git commit -m "test: add Gymnasium baseline compliance check"
```

---

### Task 7: Add integration test with real simulatte simulation

**Files:**
- Modify: `tests/experimental/test_gymnasium.py`

- [ ] **Step 1: Add the integration test**

Append to `tests/experimental/test_gymnasium.py`:

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class SimulatteIntegrationEnv(SimulatteEnv):
    """A real simulatte simulation wrapped as a Gymnasium environment."""

    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Box(low=0.0, high=np.inf, shape=(4,), dtype=np.float64)
        self.action_space = spaces.Discrete(2)

    def setup(self, *, seed: int | None, options: dict | None) -> None:
        self.sim_env = Environment()
        self.shopfloor = ShopFloor(env=self.sim_env)
        self.servers = [
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor),
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor),
        ]
        self.jobs_released = 0
        self.total_jobs = 5
        self._rng = self.np_random
        self._pending_jobs = [
            ProductionJob(
                env=self.sim_env,
                sku="A",
                servers=self.servers,
                processing_times=[self._rng.uniform(1.0, 5.0), self._rng.uniform(1.0, 5.0)],
                due_date=50.0,
            )
            for _ in range(self.total_jobs)
        ]

    def teardown(self) -> None:
        self.sim_env.close()

    def apply_action(self, action) -> None:
        if action == 1 and self._pending_jobs:
            job = self._pending_jobs.pop(0)
            self.shopfloor.add(job)
            self.jobs_released += 1
        self.sim_env.run(until=self.sim_env.now + 5.0)

    def get_observation(self):
        return np.array(
            [
                len(self.servers[0].queue),
                len(self.servers[1].queue),
                self.sim_env.now,
                len(self._pending_jobs),
            ],
            dtype=np.float64,
        )

    def compute_reward(self, action) -> float:
        return -sum(1.0 for job in self.shopfloor.jobs_done if job.late)

    def is_terminated(self) -> bool:
        return len(self.shopfloor.jobs_done) >= self.total_jobs

    def is_truncated(self) -> bool:
        return self.sim_env.now > 200.0


class TestSimulatteIntegration:
    """Integration test using real simulatte simulation components."""

    def test_full_episode(self) -> None:
        env = SimulatteIntegrationEnv()
        obs, info = env.reset(seed=42)
        assert obs.shape == (4,)
        assert info == {}

        done = False
        steps = 0
        while not done:
            action = 1 if obs[3] > 0 else 0  # release if pending jobs remain
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            steps += 1

        assert steps > 0
        assert len(env.shopfloor.jobs_done) > 0
        env.close()

    def test_multiple_episodes_with_teardown(self) -> None:
        env = SimulatteIntegrationEnv()
        for seed in [1, 2, 3]:
            obs, _ = env.reset(seed=seed)
            for _ in range(3):
                obs, _, terminated, truncated, _ = env.step(1)
                if terminated or truncated:
                    break
        env.close()

    def test_deterministic_episodes(self) -> None:
        env = SimulatteIntegrationEnv()

        def run_episode(seed: int) -> list:
            obs_list = []
            obs, _ = env.reset(seed=seed)
            obs_list.append(obs.copy())
            for _ in range(5):
                obs, _, terminated, truncated, _ = env.step(1)
                obs_list.append(obs.copy())
                if terminated or truncated:
                    break
            return obs_list

        traj_1 = run_episode(seed=42)
        traj_2 = run_episode(seed=42)
        for o1, o2 in zip(traj_1, traj_2, strict=True):
            np.testing.assert_array_equal(o1, o2)

        env.close()
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/experimental/test_gymnasium.py -v`
Expected: All tests pass.

- [ ] **Step 3: Run full test suite for regressions**

Run: `uv run pytest -v`
Expected: All tests pass, coverage >= 99%.

- [ ] **Step 4: Commit**

```bash
git add tests/experimental/test_gymnasium.py
git commit -m "test: add simulatte integration tests for gymnasium wrapper"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run ruff on all changed files**

Run: `uv run ruff check src/simulatte/experimental/gymnasium.py src/simulatte/experimental/__init__.py tests/experimental/test_gymnasium.py`
Expected: No errors.

- [ ] **Step 2: Run full test suite with coverage**

Run: `uv run pytest -v`
Expected: All tests pass, branch coverage >= 99%.

- [ ] **Step 3: Verify type checking**

Run: `uv run ty check`
Expected: No new errors introduced.

- [ ] **Step 4: Final commit if any fixups needed**

Only if Steps 1-3 required changes:
```bash
git add -u
git commit -m "chore: fixups from final verification"
```
