# Managed execution backends

Managed models can opt into an `ExecutionBackend` with
`env.attach_execution_backend(backend, scope="shopfloor")` during their synchronous
build. Ordinary models continue to use SimPy. This initial backend contract is
experimental and supports Horizon stopping, an empty layout and scalar KPI result
files. An optional packaged provider is available through
[`simulatte.experimental.native`](native-engine.md).

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

## Declared native binaries

Managed local POSIX models can declare native binary bundles. Include every
member in the project's source bundle (`tool.simulatte.bundle.include`); native
files cannot also be declared data inputs. Paths are normalized project-relative
files, with unique basenames within each bundle. The filenames must match the
current platform/interpreter ABI.

```python
from simulatte import model
from simulatte.execution.native_assets import NativeAssetBundle

ENGINE = NativeAssetBundle(
    "engine",
    (
        "native/_engine.cpython-314-darwin.so",
        "native/libengine.dylib",
    ),
)


@model(params=Params, horizon=100, native_assets=(ENGINE,))
def build(env, layout, params):
    env.attach_execution_backend(MyBackend(params))
```

Preparation pins member hashes to the captured source. The coordinator publishes
an immutable entry under the run store's `native-assets/` directory and leases it
until the execution child and descendant cleanup finish. Slot threads share the
store safely; the ordinary exclusive run-store owner lock fences other processes.
The child verifies captured and cached bytes before importing the model, permits
only declared native files through origin auditing, and rechecks before result
publication. Missing entries rebuild from captured source; invalid existing entries
fail without automatic repair. Resolved execution capabilities record bundle
addresses/hashes, and import provenance retains the actual loaded paths.

Backends receive named `VerifiedNativeBundle` handles through
`BackendContext.native_assets`. `files` lists member basenames, `address` is the
deterministic bundle identity, and `path(name)` returns a verified path for an
explicit native loader. For Python extensions, use checked loading in `prepare`:

```python
def prepare(self, context):
    engine = context.native_assets["engine"]
    self.module = engine.load_extension(
        "_engine",
        "_engine.cpython-314-darwin.so",
        dependencies=("libengine.dylib",),
    )
```

Checked extension loading validates before/after byte identities, the extension
origin and the actual mapped locations of specified bundled dependencies (macOS
and Linux). Keep these paths out of deterministic artifacts/model state. Native
libraries remain trusted code; the checks do not create a security sandbox.

`simulatte store prune --dry-run` lists idle native entries and abandoned
publication staging; `simulatte store prune` removes them. Active leases are kept.
Source snapshots and requests retain their existing retention rules, so later
execution/reproduction can rebuild an evicted entry. No automatic eviction is
performed between replications. Native assets currently reject remote and Windows
execution explicitly; ordinary models retain their existing support.
