# Native roadmap step 4: packaged observation in experimental Studio

Base: signed local capacity commit `2c1ed7fe269eebba65b7182158b38b20bfe76f5d`.
This is the first integrated observation gate, not production promotion. Native
remains v3 work after a successful Studio v2 release. No C source, companion image,
provider API, deployment baseline or release version changes in this step.

## Result and public API

```python
from simulatte.experimental.native import NativeObservation, NativeProduction

backend = NativeProduction(
    servers=3,
    capacities=(2, 1, 3),
    retention="bounded",
    input_state=input_state,
    observation=NativeObservation(interval=10, max_samples=1000),
)
env.attach_execution_backend(backend)
```

Studio's existing committed-prefix/range transport now receives sampled series
of the eight stock native KPIs. Live and saved views name the native backend and
label it experimental, show effective spacing/point count, render recorded samples
and keep final scalars separate. Cancellation/failure revokes the observed prefix
and cannot publish a successful result. Compare honors the backend's trace
capabilities: it does not offer unsupported full entity replay, and the server
rejects that request before starting a child. Native KPI request reproduction
remains supported.

`examples/native_studio` is a usable Studio project with configurable capacity,
retention and observation. It admits jobs lazily with at most 32 outstanding,
commits the seed/cursor/next admission boundary, and reads control counts
independently of reporting warmup. With matching locally installed native/Studio
packages, run from that directory:

```sh
simulatte studio plant.py --level kpi
```

## Observation contract

- Interval is in simulation time. A positive finite primitive number is required;
  max_samples is an integer from 2 through 4096, default 1000.
- The schedule includes warmup and the right-open horizon. It increases spacing
  rather than truncating the tail when the requested interval exceeds the budget.
  Floating-point duplicate times are skipped. Only one sample event is pending.
- Sampling runs on the managed execution thread after native work strictly before
  t and before Python callbacks/native events exactly at t. The progress reporter
  thread never queries native KPIs. No user callback enters the C loop.
- Each frame queries scalar values once and emits the eight ordinary KpiSample
  observer records. Missing statistics produce NaN gaps, not zero. These are
  snapshots of existing window/cohort statistics, not instantaneous signals.
- No subscriber means no observation schedule or intermediate scalar queries.
  Omission preserves final-only reporting. Trace filtering and backpressure keep
  their ordinary behavior. Native state serialization/identity composition is not
  performed for each observation.
- Native authority, trajectory/control identity, final KPI estimands and default
  semantic fingerprints do not depend on cadence. Resolved trace provenance
  records the plan. The managed projection's reporting configuration includes it,
  so composed reporting/reproduction identities distinguish reporting settings.
  The no-observation identity encoding is unchanged.

Full native entity animation, per-operation events, event stepping and full replay
remain outside this scalar observation contract. It does not guarantee a wall-time
latency for a long native advance; supervisor cancellation/escalation still owns
that case. Global experiment/store retention remains a separate manual concern.

## Validation and packaging

The compatibility matrix reuses the exact native capacity wheels from step 3.
Each of CPython 3.11–3.15 runs on macOS arm64 and Linux x86-64 with the new pure
core. Each cell passes 285 installed backend/native/capacity/observation/Studio
regressions, existing clean-install/identity/capacity checks, seven integrity/ABI
rejections, 24 observation requests and eight source-deleted reproductions. It
also serves the actual packaged Studio assets, records a real native partial
prefix, completes it, deletes the model source and reproduces it in a fresh child.

The 24-request observation matrix covers full/bounded retention, fixed/dynamic
admission, FIFO/SPT and off/fine/budgeted observation. Within each modeling cell,
fingerprints match exactly across cadence variants. Fine sampling records 153
points per KPI; the budgeted schedule records 17, including both window endpoints.
Budgeted source-deleted reproductions match fingerprints and series counts.

Real Studio transport tests cover both retention modes: authenticated final and
partial traces, series.get, experimental/cadence metadata, full-replay refusal,
cooperative cancellation and producer failure after observed native progress.
Revoked prefixes return 410 and bad attempts have no successful result. Browser
tests use those installed real trace bytes with a controlled transport; server
behavior is separately tested against real native children rather than mocked.

All 69 browser checks pass across Chromium, Firefox and WebKit, including 18 new
native cases. The TypeScript workspace has 455 passing unit tests and passes all
type checks. A screenshot is preserved as results/native-studio.png.

Final review added the explicit composed-reporting identity distinction. Its two
contract tests pass alongside 80 existing native projection/identity tests. The
final reviewed package is separately clean-installed on CPython 3.14 on both hosts
with 287 installed regressions per host and the same managed reproduction/Studio
gates. The ten-ABI matrix predates only this reporting-key addition and a
browser-test selector correction; it is not relabeled as a matrix of the final
wheel bytes. Core/companion hashes and source dictionaries are preserved per cell.

After the selector correction, rebuilding the frontend changes only the build
stamp. JS, CSS, HTML and other assets are byte-identical; asset-verification.json
records that comparison. The reviewed pure wheel includes the fresh stamp and
the reporting-key addition. Final repository, coverage, lint/type/format and docs
results are preserved in the final summary/logs.

## Observation cost and memory

Thirty-six fresh installed measurement processes cover two hosts, two retention
modes, three budgets (off, 17, 1000), and three shuffled repeats. All record a
65,536-job lazy workload with 16-job batches, capacities (3,1,2) and ConWIP5.
Every cadence variant retains the same fingerprint and native state within its
host/retention cell. Normal GC remains enabled. These are small descriptive runs,
not deployment performance baselines or confidence intervals.

| Retention / host | Final-only wall median | 17 points | 1000 points |
|---|---:|---:|---:|
| Bounded / macOS | 0.184 s | 0.170 s | 0.192 s |
| Bounded / Linux | 0.287 s | 0.284 s | 0.344 s |
| Full / macOS | 0.181 s | 0.172 s | 0.211 s |
| Full / Linux | 0.289 s | 0.288 s | 0.345 s |

The low-budget differences are within the variability of this small run set.
More samples have a measurable cost; the limit makes it explicit and controllable.
Final-only performs one scalar query; 17 points perform 18 and 1000 perform 1001,
including finalization. Bounded native allocations stay at 7,052 bytes in every
mode, while full retention uses 8,980,756 bytes. The managed bounded number includes
the state-commitment buffer. macOS bounded peak RSS is approximately 37.7–37.8 MB
without intermediate samples and 39.2–39.7 MB at 1000 points; Linux is about
32.4–32.5 MB and 34.0–34.2 MB. Reader allocations occur after these measurements.

The 17-point traces are about 14.9 KB and the 1000-point traces about 362.8 KB,
independent of completed native history in this fixture. Native memory bounds
still require bounded outstanding work/lookahead; ConWIP alone does not bound an
overloaded pre-shop pool. Native and Python/reader memory are separate quantities.

## Evidence and rerunning

Sources and evidence are under prototypes/native_studio_observation. validate.py
checks installed core source hashes, builds fresh copy-installed environments
outside Git, runs the install probe/regressions and serves real packaged Studio
assets. studio_probe.py preserves a prefix and its completed trace and verifies
source-deleted reproduction, including missing-value sample equality. measure.py
runs one independent memory/cost process. analyze.py checks evidence hashes and
summarizes matching native/default fingerprints across budgets.

Build and package frontend assets before the pure core wheel. Supply the existing
matching step-3 companion wheel to validate.py. An example cell:

```sh
.venv/bin/python -m prototypes.native_studio_observation.validate \
  --python 3.14 --core-wheel path/to/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel path/to/matching/simulatte_native-0.17.0-cp314-cp314-platform.whl \
  --output prototypes/native_studio_observation/results/new-cell
pnpm --dir studio exec playwright test
```

Drafts preserve two harness corrections: the installed probe first inspected the
one-use bootstrap page instead of the app root, and the initial browser selector
matched two header labels. Neither required a product behavior change. The first
installed probe also needed to convert its FrozenMap fingerprint to plain JSON.
Frontend integrity correctly rejected stale assets before rebuilding. Historical
native prototypes/evidence remain unchanged. All work is local, with no push or
package publication.

## Roadmap status

The original four-step sequence is implemented within its experimental scopes:
bounded streaming, cancellation/failure, heterogeneous capacities and this Studio
KPI observation integration. Suitable next work is to stabilize these contracts
against representative native models and real Studio usage, including sampling
budgets for long runs. Full native entity/event observation and production wheel
baselines remain separate decisions, after the intended Studio v2 release.
