# Native scalar execution: real replications and result reporting

Date: 2026-11-02. Approved [integration gate](2026-11-02-native-execution-plan.md),
following [native scalar KPIs](2026-11-02-native-kpi.md). Based on `c379217` in
`feature/native-engine-feasibility`; no production API, package, CI or C-kernel
changes. Davide authorized the proposal with “Proceed with next result”.

## Result

The restricted native scalar model runs through the unchanged managed experiment
pipeline: fresh child processes, readiness, seed pairing, result transfer and
publication, result-store reuse, scalar KPI files and statistical reports.
Production/native outputs match exactly, and both backends pass the real
`Coordinator.reproduce` fingerprint and artifact checks.

The gain survives full execution overhead. Three-replication experiments with
20k jobs per replication take 14.152 vs 2.751 seconds for independent input
(5.14×), and 7.648 vs 2.923 seconds for feedback input (2.62×). At 5k jobs the
gain is 1.39–1.66×. These are whole fresh-process experiment totals, a different
measure from the earlier single-run kernel timings.

This is a private scalar-model integration experiment, not a drop-in replacement
for Environment or existing builders. Its trace fingerprint describes the shell
result scope. Native actor state is checked by separate outcome evidence in the
gate; a native semantic state fingerprint and full tracing remain unimplemented.

## Adapter and execution contract

`ExecutionAdapter` owns a separate `NativeKPIStream` or unmodified
`ProductionKPIStream`. Both implementations use the same outer managed shell,
with a scalar Collector exposing the eight existing `ShopFloorKPIs` estimands.
Production actors and collectors remain unchanged inside the production stream.
The comparison therefore holds the scalar adapter/lifecycle/output scope equal;
it is not a comparison against a production model with full actor projection.

The shell steps only its input producer and the managed stop boundary. Immediately
before each outer step, the adapter advances the backend to that time with strict
right-open semantics. Arrivals at the horizon remain unregistered; native events
at the horizon remain unprocessed. KPI getters never advance the engine. Warm-up,
unfinished jobs, empty observations and idle tails retain the production meanings.

The managed build guard temporarily replaces `step()` and restores it on exit.
The adapter installs its wrapper at activation, after the guard ends. Managed
close frees the backend, retains detached final scalars and restores lifecycle
methods, including when execution or scalar export fails. Ready cancellation
executes no backend steps. These private lifecycle hooks are intentionally
confined to research code.

Supported: local GIL-enabled CPython, fixed unit-capacity servers, unique routes,
FIFO/SPT, EDD ConWIP, independent/feedback input and Horizon stopping. Input uses
`env.rng("native-execution/templates")`; replication index i has seed 100+i,
shared between paired configurations. Source and request identities include
validated parameters and native binary bytes.

The existing driver requires a KPI TraceRecorder. It writes a small scalar result
file for both implementations, with no operation events or time series. Full
tracing, finite-population stopping, nonempty layouts and unsupported parameters
are rejected. Shell progress time is real, but its event counter/digest do not
represent native actors. No general actor API, dynamic capacity, custom
operation hooks, remote execution or replay support is established.

## Runtime assets and reproduction

The project builder copies the model and its minimal prototype dependencies and
builds native assets once. Relative loader references allow the captured project
to move; isolated children load its captured binaries, not mutable worktree
libraries. A manifest checks two binary files and five C source/header files
before the model loads native code. Changing binary bytes changes captured source
identity; corrupt assets are rejected. Gate and timing builds produced identical
asset hashes on this host.

Published artifacts contain deterministic scalars/window, admission counts and
optional generated-input/feedback/partial-state hashes. Timing belongs only to
volatile trace metadata. Review found elapsed timings in an early draft artifact;
that draft was superseded before final measurement, and a regression plus real
reproduction checks were added. Both backends now reproduce the same fingerprint
and artifact hashes. Full event replay is still explicitly unsupported.

## Correctness and safety evidence

- 12 real paired experiments: independent/feedback × FIFO/SPT × horizons 1, 10
  (warm-up 2), and 200 (warm-up 20), fixed three-server plant/cap 2/40 templates.
  Three seeds per configuration give 36 matched seed pairs, 72 successful runs.
  KPI scalars/windows, generated inputs, feedback, admitted counts and partial
  job states match exactly. All 72 windows match the requested stopping policy.
- 96 nonempty paired statistical comparisons have zero mean difference. Standard
  empty/single-observation uncertainty handling is preserved; zero-width intervals
  are asserted where at least two paired observations exist.
- Re-running the same experiment executes no new requests; a renamed equivalent
  experiment reuses all six committed content results. Two additional fresh
  reproductions (one per backend) preserve fingerprints and artifact hashes.
- Full tracing, finite-population stopping and unknown KPI requests fail without
  publishing results. A corrupted native asset is rejected, and binary mutation
  changes source identity.
- 15 focused tests cover partial/warm-up/tied boundaries, unfinished jobs, idle
  tails, scalar purity, detached results, build-guard/ready cancellation, backend
  failure cleanup, relocation/integrity and deterministic artifacts.
- Full prototype suite: 491 passed / 1 skipped. Focused production execution,
  scheduler, CLI, experiment-result and statistics suites: 194 passed.
- Instrumented C API boundary: 15 passed with Python malloc and leak detection
  disabled; the relocated asset subprocess uses its ordinary build. Native C
  sources are unchanged from the preceding standalone ASan/UBSan-certified gate.
- Audit verifies 156 historical production/evidence files against `c379217`,
  unchanged C sources, frozen current sources and result hashes. Four corrupted
  result-field probes (KPIs, window snapshot, admitted count, seed) are rejected.
  Imported production runtime files match the frozen source tree. Ruff passes.

## Whole-experiment timing

Local CPython on bigboss/macOS arm64. A fixed-order randomized matrix has two
backends × two producers × 5k/20k jobs × three repeats: 24 fresh worker processes,
72 real replication executions. Each worker calls ordinary
`simulatte.experiments.run` with one local slot and three replications, then reads
the stored results and generates ExperimentReport statistics. Horizon is 30000,
warm-up 500. Every pair and repeat has exactly the same eight KPI values per seed.
All requested jobs are admitted. Verification proof generation is disabled for
both timing implementations; the correctness gate validates those proofs.

The measured outer process wall time includes interpreter/import startup, normal
source/runtime preparation, input generation and validation, fresh replication
children, readiness, simulation, scalar export, KPI result files, artifact/result
transport and publication, result validation/statistical reporting and coordinator
cleanup. This invokes the headless Python API; it does not time CLI argument
parsing. One-time native compilation and initial fixture creation/copying happen
before the worker process, as installation/preparation of the research project.
Deleting retained experiment stores is outside timing; normal executions retain
their results. These are not comparisons with the prior workload/input seeds or
exhaustion window, and should not be treated as before/after kernel regressions.

Seconds per experiment of **three replications**, median (min–max), three repeats.
The 20k row executes 60k jobs across its three replications.

| Producer | Jobs per replication | Production | Native | Speedup |
| --- | ---: | ---: | ---: | ---: |
| independent | 5,000 | 3.920 (3.919–4.013) | 2.366 (2.348–2.413) | 1.66× |
| independent | 20,000 | 14.152 (13.890–14.210) | 2.751 (2.745–2.960) | 5.14× |
| feedback | 5,000 | 3.436 (3.277–3.523) | 2.479 (2.406–2.512) | 1.39× |
| feedback | 20,000 | 7.648 (7.558–7.662) | 2.923 (2.919–2.963) | 2.62× |

The existing trace footer supplies volatile `run_seconds`. This includes managed
activation/ready waiting and stepping; it is not isolated native-kernel time.
For the native 20k cases:

| Producer | Whole process | Sum of three managed run intervals | Median fraction outside those intervals |
| --- | ---: | ---: | ---: |
| independent | 2.751 | 0.447 | 83.8% |
| feedback | 2.923 | 0.634 | 78.5% |

Preparation, input generation, runtime loading and the result lifecycle now
account for most native wall time. The timing breakdown does not attribute that
residual to individual components, so it does not justify changing process
isolation or caching rules. Three repeats on one host are descriptive ranges,
not portable confidence bounds. No PyPy timing/delivery investigation was done.

## Evidence and reproduction

Frozen manifest: 267 files, SHA-256
`080608ed067f1c6b2ac6fcdf3bfa12cedea2066700b06d60f7b406a3ac24d0de`.

Timing matrix SHA-256: `b62b911a7935702b93beec01b9fbd2c20e8270f4ec86c85bcea0a81cfc5ef20f`.

Root certificate SHA-256: `0c9a4aaf641a47e1044450aa5b7e335cb652e056987e1b616c84e684e3038738`.

- `_nd_scalar.cpython-314-darwin.so`: `caf222fa57a3eca381109227edad65c8f9b7aae6a1e28e9bf65099b846a844fa`
- `dynamic_engine.dylib`: `4a56f01721ebf05361e4248b93861bd224f0fae5fe250e59f7650178351b0394`

Evidence: [gate certificate](../../prototypes/native_engine_dynamic/execution-results/gate/verification.json),
[managed results and reports](../../prototypes/native_engine_dynamic/execution-results/gate/runs.json),
[raw timing matrix](../../prototypes/native_engine_dynamic/execution-results/scaling/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/execution-results/scaling/summary.json),
[checks](../../prototypes/native_engine_dynamic/execution-results/checks.json),
[complete audit](../../prototypes/native_engine_dynamic/execution-results/verification.json).

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.execution_experiment gate --output /tmp/native-execution/gate
.venv/bin/python -m prototypes.native_engine_dynamic.execution_experiment matrix --output /tmp/native-execution/scaling
.venv/bin/python -m prototypes.native_engine_dynamic.execution_experiment audit-experiment --output /tmp/native-execution
```

A local C compiler and matching Python development headers are required by the
project builder. Assets are host/interpreter-specific. Production APIs and native
C code stay unchanged; no package installation or release is part of this gate.

## Proposed next gate

Proposed, not adopted: replace private lifecycle wrapping with a small explicit
execution-backend contract, and give the supported native model a fingerprint of
its own state. Preserve the verified seed/window/scalar/reproduction, readiness,
cancellation and publication contracts. Keep capabilities explicit; native full
tracing and broader actor coverage remain separate work. This makes the supported
slice a maintainable opt-in integration before extending features or tuning an
already-small native compute interval. Setup costs should be measured at that
interface's phase boundaries before selecting any optimization.
