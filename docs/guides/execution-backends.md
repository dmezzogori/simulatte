# Managed execution backends

Managed models can opt into an `ExecutionBackend` with
`env.attach_execution_backend(backend, scope="shopfloor")` during their synchronous
build. Ordinary models continue to use SimPy. This initial backend contract is
experimental and supports Horizon stopping, an empty layout and scalar KPI result
files. The research native engine is not an installed backend package.

Import the contract from `simulatte.execution.backend`. A backend declares
`BackendCapabilities(name, state_schema)` and a tuple of scalar `KPI` declarations,
then implements:

| Method | Responsibility |
| --- | --- |
| `prepare(context)` | Open resources and schedule supported Python input processes using `context.env`. |
| `advance(until)` | Process backend events strictly before the next managed boundary. |
| `kpi_values()` | Return a pure, unscoped mapping of KPI names to values. |
| `state_digest()` | Return a pure SHA-256 hex digest of deterministic backend state. |
| `finish()` | Seal successful results and write deterministic artifacts without advancing time. |
| `close()` | Release resources, including after partially failed preparation. |

Registration validates capabilities before preparation. The managed stopping loop
owns one single-use backend instance per environment; instances cannot be shared
or reattached after close. The stopping loop
advances the backend before environment steps and finalizes it at the successful
stop boundary. Environment close owns cleanup, even after recorder or execution
errors. A backend must not replace environment methods. `Environment.run()` is
not the execution entry point for registered backends; use managed execution,
for example an experiment with `level="kpi"`.

Scalar queries must not schedule events, draw random numbers or mutate the backend.
The state digest must cover pending work, relevant queues, future input/controller
state and configuration as well as completed work. Exclude wall time, addresses,
allocation capacities and observer settings. Give each encoding a versioned state
schema; hashes of different simulator representations need not be equal.

The framework registers an opaque state entity. Its initial digest is captured
before activation projection; a typed domain event captures the final digest.
Normal semantic fingerprints, stored KPI files and reproduction checks therefore
include backend state without changing the trace format. Capability/schema details
are recorded in the resolved execution manifest. The final scalar values remain
available after resource close.

This opaque projection does not expose individual backend actors or establish full
event tracing/replay. Full tracing and finite-population stopping are rejected in
this first contract. Time measurements belong in volatile metadata, so semantic
artifacts remain reproducible.
