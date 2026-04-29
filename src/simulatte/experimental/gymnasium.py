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
