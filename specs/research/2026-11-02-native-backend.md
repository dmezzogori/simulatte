# Explicit managed backend lifecycle and native state fingerprints

The supported native production slice now uses a first-party, opt-in managed
backend interface. Research code no longer replaces environment lifecycle
methods. Its normal semantic fingerprint covers actual native state and future
Python input, so equal KPI values no longer conceal different pending work.
The frozen paired experiments, reproduction and result-reuse gates pass. With
state fingerprinting enabled, whole-experiment speedups at 20,000 jobs per
replication are 5.05× for independent arrivals and 2.65× for feedback arrivals.

This implements the [authorized plan](2026-11-02-native-backend-plan.md), following
the [managed execution gate](2026-11-02-native-execution.md). It adds an explicit
integration contract to production code, but leaves the native provider under
`prototypes/`. No installed native backend, release, remote execution, broader
actor coverage or full native tracing is established here.

## Ownership and fingerprint contract

`Environment.attach_execution_backend()` accepts one single-use
`ExecutionBackend`. A provider declares its scalar KPIs and a versioned
`BackendCapabilities` descriptor, then implements preparation, advancement, pure
scalar/state queries, successful finalization and resource close. Registration
rejects unsupported capabilities before preparation. The managed stopping loop
advances the provider strictly before each next Python boundary and finalizes it
at successful Horizon stopping. Environment close owns cleanup, including after
partially failed preparation, execution, finalization or recorder close. The same
provider cannot be shared or reattached, and successful scalar results remain
available after resource close.

Ordinary models retain their SimPy execution flow; `Environment.step()` is
untouched. Backend imports are lazy for ordinary environments. The managed loop
has an optional backend branch. Registered backends require managed execution;
ordinary `Environment.run()` rejects them. The initial capability boundary is
Horizon stopping, an empty layout and KPI result files. Full tracing and
finite-population stopping fail explicitly. The native provider retains fixed
unit-capacity servers, FIFO/SPT dispatch, EDD ConWIP, and the existing independent
or feedback Python producers.

The framework registers an `execution_backend` state entity containing the
backend name, fingerprint schema and initial state digest. A typed
`execution.backend_state_captured` event records the final digest. This uses the
existing semantic projection, fingerprint and result-publication path. Resolved
execution capabilities identify the provider/schema. No trace or result-file
wire schema changes were needed. An actual new native KPI trace opens in the
unchanged TypeScript reader, preserving nested capabilities, state and fingerprint.

The projection is opaque: it does not expose native actors or establish native
event playback, restore/checkpoint support, or full trace equivalence. Production
and native implementations use distinct state schemas; their shared results are
compared, while their representation-specific hashes are expected to differ.
The production oracle hashes domain entities and relevant partial results, not
SimPy generator frames.

## State identity and native safety

The C API adds two pure, checked getters for the size and bytes of versioned
`NSTA0001` state. The canonical encoding uses little-endian integers and IEEE
binary64 values, normalizes signed zero, and includes configuration, time,
lifecycle, submitted jobs/operations, trajectories, resource queue links,
dispatch order, pool membership, pending calendar events and control counters.
Copies of the pool and calendar are sorted canonically; their live heaps are
never reordered. The Python extension returns owned bytes, so no engine pointer
escapes and a captured value survives engine close.

Addresses, allocation capacities, file paths, observer buffers/modes and metric
accumulator internals are excluded from model-state identity. Current KPI values
remain part of the normal fingerprint separately. The adapter additionally hashes
all future input templates, admitted/generated work, controller decisions,
producer selection and model configuration. This covers inputs still waiting in
Python as well as work already admitted to C. Timing data stays outside semantic
artifacts.

Hand-derived encodings and an independent decoder check the layout. Sensitivity
tests change unfinished due dates or processing times while preserving current
KPIs. Another adapter test changes C-only pending work with equal initial state
and KPIs, proving that normal fingerprints consume actual native state. Repeated
queries preserve trajectories; warm-up and legacy observation modes preserve raw
model state at matched boundaries. Invalid output capacity fails before writing,
and both injected scratch-allocation failures leave the engine usable.

The added getter block is the only change to `dynamic_engine.c`: removing it
recovers the transition source at `e061ac1` byte for byte. Independent regeneration
also preserved old raw fixture sources and normalized traces. Historical KPI and
execution evidence files remain byte-identical.

## Frozen correctness and lifecycle gate

The gate executes 12 experiment definitions across independent/feedback producers,
FIFO/SPT and three horizons/warm-ups, with two backends and three paired seeds:
72 main runs, or 36 matched native/production pairs. Shared KPI values, observation
windows, generated input, feedback decisions and partial-state proofs match
exactly. Additional evidence covers:

- Four native future-input sensitivity runs: identical current KPIs/shared proofs,
  different state and ordinary fingerprints.
- Two fresh reproduction executions, one per backend, preserving the normal
  fingerprint and deterministic artifacts.
- Existing-request reuse and six replications reused by an equivalent definition.
- Rejection of full tracing, finite-population stopping and an unknown KPI without
  committing a result.
- Ready-barrier cancellation without advancement, prepare/advance/finish failure
  cleanup, recorder failure cleanup, single-use ownership and closed getters.
- Audit rejection of corrupted backend state, fingerprint, KPI and seed fields.

## Whole-experiment timing

On bigboss, macOS arm64 and CPython 3.14.3, the randomized matrix runs 24 fresh
worker processes, each executing three replications through the normal headless
experiment API: 72 timed replications. Both implementations disable optional
proof generation. The configuration is ten servers, WIP cap 12, SPT, load 0.95,
Horizon 30000 and warm-up 500, with seeds 100–102. All requested jobs are admitted;
each pair matches on all eight KPIs, and each backend's state and ordinary
fingerprints repeat exactly across processes.

Outer wall time includes interpreter/import startup, ordinary runtime/source
preparation, input generation, fresh replication children, readiness, simulation,
state hashing, KPI/artifact transport and publication, validation/statistical
reporting and coordinator cleanup. One-time native compilation and project
creation/copying precede timing. Deleting retained stores is outside timing. This
does not measure CLI parsing or a native-kernel-only interval.

Seconds per experiment of **three replications**, median (min–max), three repeats.
Each 20k experiment executes 60k jobs in total.

| Producer | Jobs per replication | Production | Native | Speedup |
| --- | ---: | ---: | ---: | ---: |
| independent | 5,000 | 4.202 (4.172–4.248) | 2.498 (2.408–2.752) | 1.68× |
| independent | 20,000 | 14.996 (14.859–15.290) | 2.971 (2.935–3.009) | 5.05× |
| feedback | 5,000 | 3.533 (3.483–3.729) | 2.509 (2.394–2.535) | 1.41× |
| feedback | 20,000 | 8.335 (8.081–8.341) | 3.143 (3.085–3.196) | 2.65× |

Pure C state encoding plus SHA-256, measured separately with one warm capture
and seven samples at a partial horizon, costs:

| Submitted jobs | Operations | Encoded bytes | Median encoding + hash |
| ---: | ---: | ---: | ---: |
| 100 | 473 | 27,244 | 0.0098 ms |
| 5,000 | 27,363 | 1,508,232 | 0.582 ms |
| 20,000 | 110,060 | 6,054,752 | 2.492 ms |

That last measurement is approximately 2.5 ms for 6 MB of native state. It excludes
Python future-input/composite hashing and is not the total fingerprint cost. State
size grows with retained jobs/operations; canonical calendar sorting also needs
temporary memory and O(E log E) work. These results do not justify frequent full
state polling or establish bounded memory for arbitrarily long streams.

The gains remain substantial with stronger state verification. Three repeats on
one host provide descriptive ranges, not portable confidence bounds. The previous
gate ran in a separate timing session; comparing the two reports cannot isolate
the exact overhead of this interface or hashing. No PyPy performance or delivery
diagnostics were pursued.

## Verification and evidence

- Core/execution regression: 2,307 passed, one skipped; three existing dependency
  warnings. This broad run preceded the final single-use ownership guard.
- Intralogistics/experimental/layout: 1,105 passed. Research prototype suites:
  500 passed, one skipped.
- Final lifecycle/adapter/codec suite after the ownership guard: 34 passed.
  Earlier PyPy compatibility run: 33 passed, with existing timeout-config warnings.
- ASan/UBSan boundary suite: 65 passed. The extension was instrumented against
  the release engine DSO; the standalone sanitizer separately instrumented the
  C engine. Existing fault paths and both new state-copy allocation failures pass.
- TypeScript trace reader: 301 passed and typecheck passed. A representative new
  native KPI trace also opened successfully in the unchanged reader.
- Ruff, `ty check src`, documentation build and the final source/evidence audit pass.

The final frozen manifest covers 276 files, SHA-256
`97921c21c118c848485d0e1965b42461e7ff83775d3ce64bcde794514ec5305b`.
The timing matrix SHA-256 is
`3fe1d7f172fe1fed997a8fbf128d566a63c6f785647a798946d2a4f9accfa15a`.
The final report and generated evidence are outside the measured source manifest.
Metadata records the base revision plus hashes of the actual changed sources.
Default and project-specific native binary hashes are recorded separately because
the relocated research project builds its own assets.

Evidence: [gate](../../prototypes/native_engine_dynamic/backend-results/gate/verification.json),
[paired runs](../../prototypes/native_engine_dynamic/backend-results/gate/runs.json),
[sensitivity](../../prototypes/native_engine_dynamic/backend-results/gate/sensitivity.json),
[raw timings](../../prototypes/native_engine_dynamic/backend-results/scaling/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/backend-results/scaling/summary.json),
[state cost](../../prototypes/native_engine_dynamic/backend-results/scaling/state-cost.json),
[checks](../../prototypes/native_engine_dynamic/backend-results/checks.json),
[complete audit](../../prototypes/native_engine_dynamic/backend-results/verification.json).
The pre-ownership-guard gate/matrix was superseded and retained only under ignored
`.build/backend-before-ownership-guard`; all committed measurements use the guard.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.backend_experiment gate --output /tmp/native-backend/gate
.venv/bin/python -m prototypes.native_engine_dynamic.backend_experiment matrix --output /tmp/native-backend/scaling
.venv/bin/python -m prototypes.native_engine_dynamic.backend_experiment audit-experiment --output /tmp/native-backend
```

A matching local C compiler and Python headers are required. Generated assets
remain host/interpreter-specific. Independent sanitizer/regression checks are
documented in `checks.json`; the audit validates their frozen record rather than
rerunning those suites.

## Proposed next gate

Proposed, not adopted: profile the whole managed workflow at this explicit
interface's phase boundaries, separating runtime/source preparation, input
generation, backend preparation, advancement, state capture and result
publication. Use the same supported slice and fresh-process/reproduction rules,
then select an optimization from the measured dominant cost. The raw state-copy
measurement alone does not attribute the remaining workflow time. Packaging and
native full tracing remain separate decisions.
