# Native stock scalar KPIs: production contract and CPython cost

Date: 2026-11-02. Approved gate: [plan](2026-11-02-native-kpi-plan.md).
Research branch `feature/native-engine-feasibility`, based on `4193642`.

## Result

The eight production `ShopFloorKPIs` can remain in C and be read at Python
boundaries without a Python event publisher, observation journal or recorder.
Native and unmodified production scalars, windows, physical trajectories,
generated jobs and feedback decisions match exactly in the tested slice.

The paired CPython experiment finds an 18.1–140.8× whole-run speedup, depending
on workload, scale and query frequency. At 20k jobs with all eight KPIs queried
at every arrival, the speedup is 92.9× for independent arrivals and 19.9× for
the feedback producer. This supports native built-in aggregation as the next
architecture component. These are whole-model comparisons: they include the
C engine's existing calendar and release/dispatch implementation advantages,
rather than isolating the cost of KPI arithmetic alone.

## Implemented scope

`NativeKPIStream` is a prototype adapter over opt-in native state. It configures
warm-up before registration/time advancement, retains scalar accumulators and
exports detached `snapshot()` values with `(start, end, closed)` window metadata.
The old observer path and existing default engine modes retain their behavior.
No production package, dependencies, execution driver or CI configuration changed.

The eight scalars preserve the production definitions:

- makespan: mean creation-to-completion time, not final completion timestamp;
- lateness, tardiness and tardy fraction over the completion cohort;
- total queue time: each completed job's sum of exit minus entry minus planned
  processing duration, including the production rounding order;
- throughput: completions at/after warm-up divided by window length;
- utilization: time-weighted granted slots divided by fixed total capacity;
- jobs in system: time-weighted shop-floor jobs, excluding the pre-shop pool.

Job means exclude unfinished jobs and retain the full lifetime of jobs completed
in the window. Busy/floor signals clip at warm-up and include unfinished work.
Their clocks are independent: unrelated events must not split rounded products.
Exactly rounded expansions follow the existing Python `_ExactSum`; final
half-even correction follows [CPython math.fsum](https://github.com/python/cpython/blob/v3.14.0/Modules/mathmodule.c).
Only the KPI translation unit disables floating-point contraction, preserving
legacy engine code generation. Expansion storage grows with checked allocations.

`advance(t)` processes events strictly before `t`. Horizon windows remain
right-open; exhaustion closes the window, including an already-advanced idle
tail. A zero-length window omits the three rate/time-weighted scalars but can
contain completion-cohort means when exhaustion occurs exactly at warm-up.
Unavailable scalars are omitted. Non-finite scalar values follow the production
collector. Snapshot tail integration copies accumulators and cannot change future
values. A failed mutating allocation poisons the stream; a failed temporary
snapshot allocation leaves it usable. Close never drains pending work.

## Correctness and safety evidence

- 432 paired configurations / 864 model runs: 12 quantized workload families,
  FIFO/SPT, caps 1/2/12, independent/feedback producers and warm-up 0/1.5/1000.
  Snapshots are compared at every arrival and an additional partial boundary,
  including repeated queries; final scalars, trajectories, generated jobs and
  decisions are exact. The gate saves boundary hashes and scalar values.
- 65 new tests cover continuous times, arrival/completion ties, partial windows,
  unfinished jobs, cohort boundaries, empty/zero-length windows, idle tails,
  detached snapshots, invalid configuration, failed/closed engines, polling
  invariance, exact-sum cancellation/growth/rounding and non-finite scalars.
- Full prototype regression suites: CPython 476 passed / 1 skipped; PyPy 473
  passed / 4 skipped with the two existing timeout-configuration warnings.
  PyPy was checked for correctness only; no delivery diagnostics or timing study.
- Production trace reader/writer/sink, ConWIP and invariance: 156 passed.
- Standalone ASan/UBSan: 90 mutating allocation-failure positions for native KPIs,
  both read-only snapshot allocation failures, three expansion allocation/growth
  failures, plus existing scalar/rich/bulk fault sweeps (143/143/144).
- Instrumented Python C API boundary: 96 passed, linked to the release engine;
  the standalone harness separately instruments the engine and KPI helper.
  Python allocator set to malloc; leak detection disabled for that boundary run.
- Existing compact source fixtures regenerate byte-for-byte; normalized production
  traces for the original and stock-observed paths are unchanged. 180 historical
  production/evidence files verified against `4193642`.
- Five deliberately corrupted timing rows (trajectory, generated jobs, feedback,
  snapshots, query counts) are rejected by the audit. Ruff and whitespace checks pass.

No claim extends to arbitrary actors/hooks, history, EMA, custom collectors,
dynamic capacities, Studio execution or a general production backend. C still
retains per-job/per-operation trajectory arrays: this is not bounded-memory
simulation. Native queue-sum overflow is rejected and poisons the run; exact
exception-type equivalence for that extreme case is not established.

## Timing method and results

Same eight scalar outputs on each side. `ProductionKPIStream` attaches only
unmodified `ShopFloorKPIs`; no EMA, history, time-series or other collectors.
`NativeKPIStream` creates neither Environment/publisher nor journal/trace.
Both sides use the same FIFO/SPT/ConWIP production slice (SPT, cap 12 here),
input bytes, registration-at-arrival behavior and feedback controller.

CPython on bigboss, macOS arm64; the full interpreter/platform strings are in
metadata. 32 fresh-process cases: two producers × 5k/20k jobs × warm-up 0/500 ×
final-only/every-arrival queries × two backends. Each process has one warm-up
and five measured runs: 160 measured repetitions and 32 warm-ups. Case order is
randomized with fixed seed 20261102. Every run validates its trajectory and
feedback independently, and every pair's outputs match exactly. Boundary-query
cases discard intermediate snapshots, retaining no history. The correctness
gate separately checks all boundary values. Final-only cases still support
live job inspection used by the feedback controller.

Timed total sums construction, feed+run, final scalar export, and close/drop/GC.
Input parsing, process/import startup, trajectory copying and validation are
outside timing. The engine and production adapter both maintain trajectories
during simulation. Raw phase wall/CPU samples and whole-process peak RSS are
saved. RSS includes input loading and validation and is not simulation-only
memory. No tracing/fsync or custom observer costs are included.

20k jobs, warm-up zero. Times are seconds: median (min–max), five repetitions.

| Producer | Queries | Production CPython | Native CPython | Ratio |
| --- | --- | ---: | ---: | ---: |
| feedback | boundary | 1.9551 (1.9518–1.9570) | 0.0983 (0.0976–0.0992) | 19.9× |
| feedback | final | 1.7899 (1.7831–1.7935) | 0.0796 (0.0794–0.0800) | 22.5× |
| independent | boundary | 4.1324 (4.1145–4.1478) | 0.0445 (0.0443–0.0449) | 92.9× |
| independent | final | 4.0479 (4.0439–4.0554) | 0.0288 (0.0286–0.0291) | 140.8× |

Nonzero warm-up has the same result: 20k every-arrival cases take 3.9911 vs
0.0438 seconds (91.1×) for independent input, and 1.9118 vs 0.0959 seconds
(19.9×) for feedback. Full results are in `summary.json`. Feedback changes job
routes/durations from live observations, so independent and feedback timings
represent different workloads; only matched producer pairs are compared.

Five repetitions on one host are descriptive, not portable confidence bounds.
The comparison establishes CPython performance for this supported slice and
requested outputs. It does not rank a same-eight-output PyPy configuration,
which was not timed, or measure a public API/normal experiment execution path.

## Evidence and reproduction

Frozen manifest: 261 source/test/configuration files, SHA-256
`bd7a4c14b28a387ec436653b85de778f053d6fe3699c92898b793b441f85e65b`.

Shared 50k input file (prefixes used): SHA-256
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.

- `static`: `46385e00a680543ec453ce8e7667670e0391982049721f7d326595aa6d2a8dfb`
- `dynamic`: `55768ef73a81b219e422a15d60610c88e5b3568df49a53a9615386c92d02dea4`
- `_nd_scalar.cpython-314-darwin.so`: `ecc3dfeecea9abf3564197b9c23555d2f6ee1fcf43bf9aa9f97c88a6023dc6dc`
- `_nd_scalar.pypy311-pp73-darwin.so`: `61c6eacfc6c9f980526150a76737847c1764cbef1ea328edea833bc31532abbb`

Root certificate SHA-256: `dde56b996af009d9e8a753d8b421659a8d2d739f303dbed3b7e4bd671ef5d173`.
Timing matrix SHA-256: `fd977117241bb4738b6ad0fb9c309cfef2ada5ddd0dfc237048dbab312b7e927`.

Evidence: [gate](../../prototypes/native_engine_dynamic/kpi-results/gate-cpython/verification.json),
[raw timings](../../prototypes/native_engine_dynamic/kpi-results/scaling/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/kpi-results/scaling/summary.json),
[checks](../../prototypes/native_engine_dynamic/kpi-results/checks.json),
[complete audit](../../prototypes/native_engine_dynamic/kpi-results/verification.json).

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.build
.venv/bin/python -m prototypes.native_engine_dynamic.compiled_build
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.compiled_build
.venv/bin/python -m prototypes.native_engine_dynamic.kpi_experiment gate --output /tmp/native-kpi/gate-cpython
.venv/bin/python -m prototypes.native_engine_dynamic.kpi_experiment matrix --output /tmp/native-kpi/scaling
.venv/bin/python -m prototypes.native_engine_dynamic.kpi_experiment audit-experiment --output /tmp/native-kpi
```

The standalone sanitizer command and complete test invocations are recorded in
`checks.json`. Reproduction must use this frozen revision and matching input;
binary hashes are local build identities, not portable reproducibility claims.

## Implications and proposed next gate

The scalar result contract no longer requires reconstructing Python actors and
publishing every native event. Python can inspect results at meaningful
boundaries while built-in aggregation stays native. The result supports the
original Python-authoring/native-built-ins direction and makes an integration
gate more useful than further delivery diagnostics.

Proposed, not yet adopted: expose the tested production slice through a narrow
experimental execution adapter, then run a real replicated experiment through
normal execution/result handling. Preserve the eight native KPIs, finite-horizon
and seed/result semantics; declare and reject unsupported model features. Measure
startup, input/model preparation, execution, scalar result export and cleanup
end-to-end, checking the same outputs against production. This establishes
whether the prototype gains survive ordinary use before widening actor support.
Trace transport through bounded native batches remains a separate later gate.
