# Experimental API

This module contains unstable APIs that may change or be removed without prior notice.

The optional [native production backend](../guides/native-engine.md) exposes
`Job`, `JobHandle`, `NativeStream` and `NativeProduction` through
`simulatte.experimental.native`.
Both native classes expose an experimental `state_projection()` query. The stream
returns provider-owned trajectory material and the current stock snapshot; the
managed backend adds reporting configuration, scope and the unchanged complete
controller/state authority. See the [projection contract](../guides/native-engine.md#experimental-state-projection)
for schemas, lifecycle requirements and reproduction limits.
`simulatte.experimental.native_identity` composes one supplied managed projection
and model-owned action commitment into an immutable `IdentityComposition` with
canonical bytes and detached dictionaries. See [identity composition](../guides/native-engine.md#composing-experimental-identities)
for the three identities and their limits.
`SimulatteEnv` is an abstract base class (ABC) that wraps a Simulatte simulation as a
[Gymnasium](https://gymnasium.farama.org/) `Env`, enabling reinforcement-learning training
loops. Subclasses implement six abstract methods (`setup`, `get_observation`, `apply_action`,
`compute_reward`, `is_terminated`, `is_truncated`); the base class handles the
`reset`/`step`/`close` lifecycle and state guards. Two optional hooks — `teardown()` and
`get_info()` — are also available.
See the [Reinforcement Learning guide](../guides/reinforcement-learning.md) and the
[Gymnasium wrapper tutorial](../tutorials/gymnasium-wrapper.md) for worked examples.

::: simulatte.experimental.SimulatteEnv
    options:
      heading_level: 2
      members: null
      filters: ["!^_"]

::: simulatte.experimental.native
    options:
      heading_level: 2
      members: [Job, JobHandle, NativeStream, NativeProduction]
      filters: ["!^_"]

::: simulatte.experimental.native.ExpiredHandleError

::: simulatte.experimental.native_identity
    options:
      heading_level: 2
      members: [IdentityComposition, compose_native_identity]
      filters: ["!^_"]
