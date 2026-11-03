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
supported. CI builds and clean-installs CPython 3.11, 3.14 and 3.15 on macOS/Linux.
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

Completed jobs and operations remain in memory until close; there is no bounded
infinite-stream retention guarantee, cancellation or preemption.

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
`level="kpi"`. The state projection is an opaque digest, not individual actors.
Full native trace playback, arbitrary actor APIs, other release policies,
and intralogistics remain unsupported. SSH native worker execution has not been
validated by this packaging gate.

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

The companion's C sources are byte-identical to the validated research engine;
`native/sources.json` and CI check that identity. The clean-install gate also checks
base-only installs, ABI/provider API rejection before extension import, corrupt
bytes, actual wrong dependency mappings, runtime mutation without publication,
source-deleted reproduction and incompatible wheel tags outside the checkout.
