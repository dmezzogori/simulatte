# Experimental native production backend

The optional native provider accelerates a bounded production model: fixed
unit-capacity servers, routes without repeated servers, FIFO/SPT dispatch, EDD
ConWIP and the eight `ShopFloorKPIs` scalars. Ordinary models keep their Python
engine. Existing `ShopFloor` actors and policies are not automatically translated.

The public Python API is `simulatte.experimental.native`. It imports without
loading native code. A matching `simulatte-native` wheel supplies the compiled
engine; the core wheel remains pure Python.

## Installation

During development, from this repository:

```sh
uv sync --extra native
```

This builds the companion once, non-editably, using a C17 compiler and matching
Python headers. Runtime execution does not compile code or download dependencies.
Build standalone source and wheel distributions with `uv build native`.

For a clean installation of the locally built pair:

```sh
uv venv /tmp/native-example
uv pip install --python /tmp/native-example/bin/python \
  path/to/simulatte-0.17.0-py3-none-any.whl \
  path/to/simulatte_native-0.17.0-<matching-ABI-and-platform>.whl
```

Once a coordinated core/provider release is published, the opt-in installation is
`pip install 'simulatte[native]'`. The current branch's companion is not published;
install its matching local wheel pair. The `all` convenience extra excludes native.
Source builds need a compiler; installing a built wheel does not.

Wheels target a specific GIL-enabled CPython ABI and macOS/Linux architecture.
They are neither universal nor `abi3`. PyPy, free-threading and Windows are not
supported. CI builds and clean-installs CPython 3.11–3.15 on macOS/Linux.
Locally built wheels use the current host's deployment/platform tag; portable
release baselines and coordinated version bumps belong to the release process.

## Direct simulation

```python
from simulatte.experimental.native import Job, NativeStream

with NativeStream(servers=2, wip_cap=2, policy="spt", warmup=0) as engine:
    first = engine.add(Job(arrival=0, route=(0, 1), durations=(2, 3), due=10))
    engine.add(Job(arrival=1, route=(1, 0), durations=(1, 2), due=10))
    engine.run()
    print(engine.inspect(first))
    print(engine.snapshot()["scalars"])
```

`Job` copies route/duration inputs and validates finite primitive numbers,
positive durations and unique zero-based server indices. Arrivals are registered
in nondecreasing order at/after current native time. `wip_cap=None` releases
immediately; a positive cap selects EDD from the pre-shop pool. Handles belong to
one stream and stay valid through native storage growth.

`advance(t)` processes events strictly before `t`, leaving events at `t` pending.
This permits Python to inspect state and add jobs at a synchronized boundary.
`run()` seals input and drains once. Scalar/state queries never advance time.
Snapshots are detached; stream queries after close fail. Use `snapshot()` for
production KPI definitions: `makespan` is mean job flow time, not the final
completion timestamp. Its `window` tuple records start, end and exhaustion closure.
`result()` additionally exposes detached trajectories and legacy engine totals.

Full retention is the default: completed jobs and operations remain until close.
For a lazy stream, select `retention="bounded"` on `NativeStream` or
`NativeProduction`. Completed handles then raise `ExpiredHandleError` after a
successful advance/run; live handles retain their identities through compaction.
`result()` trajectories require full retention. `snapshot()` works in both modes.
`storage_stats()` reports detached native allocation/capacity counters; on the
managed backend it is available while live at synchronized boundaries.

```python
from simulatte.experimental.native import ExpiredHandleError, Job, NativeStream

with NativeStream(servers=2, retention="bounded") as engine:
    handle = engine.add(Job(0, (0, 1), (2, 3), due=10))
    engine.advance(6)
    print(engine.snapshot()["scalars"])
    try:
        engine.inspect(handle)
    except ExpiredHandleError:
        print("completed job deliberately forgotten")
```

Storage is proportional to high-water outstanding work and input lookahead, not
completed history. ConWIP limits the floor, not an overloaded pre-shop pool.
Eagerly admitting an entire workload, retaining handles/history in Python, or a
growing controller callback still consumes memory. Fixed job references held by
the adapter are released after native admission. There is no unconditional
infinite-stream bound, cancellation or preemption.

Full retention keeps `NSTA0001` / `native-production-v1`. Bounded mode uses
`NRST0001` / `native-production-bounded-v1`, with a rolling completion-history
commitment, live logical state and scalar state. Those are fingerprint encodings;
restoring an engine from them is outside this API. Compare physical results/KPIs
across modes and reproduce fingerprints within the same mode. Provider API 2
requires rebuilding/reinstalling the matching local companion; the development
pair remains 0.17.0 and is not a published v3 release.

## Experimental state projection

`state_projection()` observes one boundary without advancing simulation time.
The provider captures native state and stock KPIs together; callers do not need
private owner access or knowledge of binary layouts. Returned data is detached.

```python
from simulatte.experimental.native import Job, NativeStream

with NativeStream(servers=2, retention="bounded") as engine:
    engine.add(Job(0, (0, 1), (2, 3), due=10))
    engine.advance(1)
    view = engine.state_projection()
    assert view["schema"] == "native-stream-projection-v1"
    print(view["trajectory"])
    print(view["snapshot"]["scalars"])
```

The stream view includes `trajectory` (encoding-specific native history/live
material), `snapshot` (the current stock reporting snapshot), `native_sha256`
(the SHA-256 of the existing `state_bytes()` commitment), and `native_bytes`.
Full and bounded trajectory encodings remain distinct.

`NativeProduction.state_projection()` is available while live at a synchronized
managed Python boundary, for example inside a scheduled controller. Its schema is
`native-production-projection-v1`; it returns `trajectory`, `reporting`
(`configuration`, `catalog`, `snapshot`), execution `scope`, and `authority` plus
`authority_preimage`. The authority digest equals the existing `state_digest()`
for a pure callback and includes the complete `input_state`, not just realized
native history. `native_bytes` reports queried state size. The query holds the
stream owner lock and rejects changing callback values or reentrant mutations.
Callbacks remain responsible for covering all future Python state. Failed impure
callbacks do not roll back their side effects.

Model-specific action-history commitments can be composed with this data by the
model. Trajectory material alone cannot authorize reuse or reproduction: an
unconsumed seed or control state can change future behavior without changing the
past native path. Reporting configuration can also change later admission actions
when a controller reads windowed KPIs. Keep the complete authoritative fingerprint
and ordinary pinned Coordinator reproduction. The projection is neither a
restoration interface nor an identity for arbitrary collector/trace history.

This additive hook requires companion projection v1 within provider API 2. Rebuild
and reinstall the matching local wheels to use it; older API-2 companions retain
existing stream operations and report an actionable error if projection is
requested. The local package pair remains 0.17.0. Queries copy/hash current native
state; full retention cost grows with retained history. Use on-demand or periodic
queries and measure their cost before capturing at every control poll.

### Composing experimental identities

`simulatte.experimental.native_identity` provides a pure-Python consumer of the
managed projection. It prepares each supplied observation once and returns an
immutable `IdentityComposition`; it does not query the backend or load native code.
For example, inside a synchronized live controller boundary:

```python
from simulatte.experimental.native_identity import compose_native_identity

# actions.state() is this model's wire-compatible realized-action commitment.
identity = compose_native_identity(backend.state_projection(), actions=actions.state())
payload = identity.to_dict()  # fresh detached dictionaries/lists on every call
print(payload["trajectory_control"]["digest"])
print(payload["reporting"]["digest"])
print(payload["reproduction_state"]["digest"])
assert len(identity.canonical_bytes) == identity.size_bytes
```

The `native-identity-set-v1` envelope preserves three experimental components:

| Component | Binds |
| --- | --- |
| `trajectory_control` | Scope, native trajectory material and supplied realized actions |
| `reporting` | Scope, reporting configuration, KPI catalog and current stock snapshot |
| `reproduction_state` | Scope, both component identities and the complete backend authority |

The envelope includes the full evidence and authority preimage. `canonical_bytes`
is its canonical MessagePack encoding; `size_bytes` is that encoding's length.
`native_bytes` is the provider's queried native-state size, not a memory estimate.
Full and bounded retention keep their distinct native encodings and authorities.
Keeping an `IdentityComposition` retains a snapshot; call the backend hook again
to observe a new boundary. Mutating the supplied data or a returned dictionary
does not change the composition or its bytes.

The caller owns the meaning and completeness of `actions` and `input_state`.
Realized actions alone do not describe unconsumed seeds or future control state.
The helper checks projection structure and wire compatibility; it does not verify
forged observations or authorize reuse. It neither restores an engine nor changes
default fingerprints, result reuse or pinned Coordinator reproduction. Its schema
and API remain experimental.

## Managed experiments

Save this as `plant.py` in a project:

```python
from dataclasses import dataclass
from simulatte import model
from simulatte.experimental.native import Job, NativeProduction


@dataclass(frozen=True)
class Parameters:
    duration: float = 2.0


@model(params=Parameters, horizon=100)
def build(env, layout, params):
    jobs = (
        Job(0, (0, 1), (params.duration, 3), due=10),
        Job(1, (1, 0), (1, 2), due=10),
    )
    env.attach_execution_backend(NativeProduction(servers=2, jobs=jobs, wip_cap=2, policy="spt"))
```

Run it through the ordinary managed API:

For a non-Git project directory, add a `pyproject.toml` source bundle declaration:

```toml
[tool.simulatte.bundle]
include = ["plant.py"]
```

Then run:

```python
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.stopping import Horizon

experiment = Experiment(
    name="native example",
    model="plant.py:build",
    level="kpi",
    stopping=Horizon(100, warmup=2),
    configurations=Explicit([{"label": "base", "parameters": {}}]),
    replications=Replications(count=3, base_seed=100),
)
summary = run(experiment, project=".")
```

The framework owns preparation, advancement, finalization and cleanup. Fixed jobs
are registered eagerly, including future arrivals, and their pending work enters
the native state digest. Managed Horizon stopping leaves events/arrivals at the
horizon unprocessed. The backend exposes scalar KPIs after successful close and
works with seed pairing, result reuse, fresh reproduction and normal fingerprints.
Use managed execution rather than `Environment.run()` for registered backends.

Dynamic Python producers can construct `NativeProduction(..., input_state=...)`
and call `add(job)`/`inspect(handle)` from their scheduled processes. The callback
must return pure wire-compatible data covering all future Python input and
controller state, including pending templates/cursor, decisions and RNG state for
lazily generated inputs. It must not
advance time, draw RNG values or mutate state. The digest combines it with actual
native state. Dynamic admission without this callback fails explicitly. Avoid
mixing eager future jobs with earlier incremental arrivals.

The initial managed contract requires an empty layout, Horizon stopping and
`level="kpi"`. The state interface exposes commitments and scalar snapshots;
individual native actors are not registered as Python entities.
Full native trace playback, arbitrary actor APIs, other release policies,
and intralogistics remain unsupported. SSH native worker execution has not been
validated by this packaging gate.

## Cancellation and execution failures

Managed cancellation and deadlines are checked at Python execution boundaries,
including immediately after native advancement and before the next Python model
callback. A running C call is synchronous: it does not poll Python cancellation.
If it does not return, the execution supervisor escalates to process termination,
then killing the contained process group. Forced termination relies on OS cleanup;
it does not guarantee that Python finalizers run. Cancelled and failed attempts
cannot publish a successful managed result.

Direct `NativeStream` calls have no cancellation token or wall deadline. A
concurrent `close()` waits for the stream lock; it does not interrupt an advance.
Use bounded advance intervals or managed execution when a stop deadline matters.
This concerns aborting a run, not cancellation or preemption of individual jobs.

After a mutating native failure, discard the failed stream and create a fresh one.
Failed owners reject state queries; close remains safe and idempotent. Invalid
inputs rejected before mutation and read-only projection allocation failures can
be retried where the owner remains valid. Failed managed cleanup does not sample
partial backend KPIs. The original execution error is retained; a secondary
cleanup error is attached to its traceback as an exception note. Partial native
state is not a supported recovery checkpoint. A queued producer failure at the
current boundary is delivered before another native advance can mask it with a
failed-owner error.

## Integrity and provenance

The companion preserves the validated engine and C binding in two images, with a
namespaced linked library. Its build manifest records exact member/source hashes,
ABI, platform and provider API. Loading checks the manifest against wheel RECORD,
checks bytes and identities before/after import, verifies actual linked-library
locations, and records real extension origins through ordinary import auditing.
Close rechecks installed bytes; mutation cannot publish a managed result.

Installed binaries use stable distribution paths. Their version/RECORD evidence
is pinned by the managed runtime contract. No model-side binary copying,
environment-variable loader selection or project `native_assets` declaration is
needed. Project-declared bundles remain available for project-owned native files.
Non-editable local installs stay runtime dependencies; editable local source
packages retain the existing snapshot-root and path-escape checks.
Native code and installed Python dependencies remain trusted, not sandboxed.

The companion shares validated event transitions through a private engine
interface and separate retention, stock KPI and binding translation units.
`native/sources.json` and CI check the integrated source hashes; the retention
research audit checks preserved transition bodies and historical evidence.
The clean-install gate also checks
base-only installs, ABI/provider API rejection before extension import, corrupt
bytes, actual wrong dependency mappings, runtime mutation without publication,
source-deleted reproduction and incompatible wheel tags outside the checkout.
