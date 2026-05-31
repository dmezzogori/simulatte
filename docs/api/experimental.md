# Experimental API

This module contains unstable APIs that may change or be removed without prior notice.
`SimulatteEnv` is an abstract base class (ABC) that wraps a Simulatte simulation as a
[Gymnasium](https://gymnasium.farama.org/) `Env`, enabling reinforcement-learning training
loops. Subclasses implement six abstract methods (`setup`, `get_observation`, `apply_action`,
`compute_reward`, `is_terminated`, `is_truncated`); the base class handles the
`reset`/`step`/`close` lifecycle and state guards. Two optional hooks — `teardown()` and
`get_info()` — are also available.
See the [Reinforcement Learning guide](../guides/reinforcement-learning.md) and the
[Gymnasium wrapper tutorial](../tutorials/gymnasium-wrapper.md) for worked examples.

## SimulatteEnv

::: simulatte.experimental.SimulatteEnv
    options:
      heading_level: 3
      members: true
