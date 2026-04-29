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


class TestGymnasiumCompliance:
    """Run Gymnasium's built-in env checker for baseline API compliance."""

    def test_check_env_passes(self) -> None:
        from gymnasium.utils.env_checker import check_env

        env = MinimalEnv()
        check_env(env.unwrapped, skip_render_check=True)


class SimulatteIntegrationEnv(SimulatteEnv):
    """A real simulatte simulation wrapped as a Gymnasium environment."""

    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Box(low=0.0, high=np.inf, shape=(4,), dtype=np.float64)
        self.action_space = spaces.Discrete(2)

    def setup(self, *, seed: int | None, options: dict | None) -> None:
        from simulatte.environment import Environment
        from simulatte.server import Server
        from simulatte.shopfloor import ShopFloor

        self.sim_env = Environment()
        self.shopfloor = ShopFloor(env=self.sim_env)
        self.servers = [
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor),
            Server(env=self.sim_env, capacity=1, shopfloor=self.shopfloor),
        ]
        self.jobs_released = 0
        self.total_jobs = 5
        self._rng = self.np_random
        self._create_pending_jobs()

    def _create_pending_jobs(self) -> None:
        from simulatte.job import ProductionJob

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
