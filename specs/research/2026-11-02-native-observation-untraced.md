# Live stock observation with trace recording disabled

Date: 2026-11-02. Starting revision: `d4aa1f2`. Research-only opt-in change in the
native feasibility worktree. See the [authorized scope](2026-11-02-native-observation-untraced-plan.md)
and preceding [recorded observation experiment](2026-11-02-native-observation.md).

## Question

The preceding experiment established an event-time observation contract for five
unmodified stock collector classes, but every cost measurement included a trace.
Does disabling the final trace writer restore a useful native margin with those
same collectors and synchronous boundary delivery?

The no-recording correctness gate passes. Disabling the final trace reduces
native live CPython time by 61–69%, but native still takes 1.68–2.42 times as long
as production PyPy with the same stock collector bundle. It uses 15–46% less time
than production CPython. Recording is a major cost, and Python observation
delivery remains a limiting cost after its removal.

This study does not change the native scheduler, compiled boundary, transport
format or collector implementations. Keep the adapter experimental; the profiles
identify native subscriber-interest filtering as the next bounded candidate.

## What is disabled and what remains

`LiveTraceStream(..., level="none", trace_path=None)` does not construct a
`TraceRecorder`, start its worker, request semantic projection or attach a
semantic digest. An explicit final trace path in this mode is rejected. Actual
production actors use `ProductionLiveStream(..., mode="none")` with the same
collector bundle. The existing full/KPI defaults remain available.

The native rich journal **remains the observation transport**. It is written,
flushed at driver boundaries and read into the same typed publisher. This is
not a no-I/O native engine. Removing or replacing that transport would be a
different experiment. No-trace samples retain zero final `.simtrace` bytes, while
native journal volume remains `8 + 32 * (4 * jobs + 5 * operations)` bytes.

Both paths retain `EMACollector`, `ShopFloorTimeSeries`,
`CurrentWorkloadCollector`, one `ServerTimeSeries` per server and `ShopFloorKPIs`.
At ten servers this means fourteen stock instances, plus the existing collector
declaring system/floor WIP trace series. Without a trace subscriber those two
series do not construct `KpiSample` events. All stock curves and scalars are still
queried and copied, including every server's queue/utilization history.

Result extraction retains the same detached trajectories and legacy model totals
in both recording modes. The bare research `ProductionStream` normally omits
those derived totals in mode `none`; the stock-observation subclass explicitly
keeps them so disabling recording does not also remove benchmark work. Its default
behavior outside this subclass is unchanged. Stock KPI definitions remain those
of the preceding experiment, including mean creation-to-completion time for
`shopfloor/makespan`.

The native publisher still follows its existing typed event/delta construction
path. Production keeps its normal subscriber-interest guards. This experiment
does not add native demand filtering, fuse boundary calls or optimize collection.

The same read-only view and delivery contract applies: creation before `add()`
returns, model events strictly before `advance(t)`, event-time reads in callbacks,
clock `t` on boundary return, and no callback model control. Warm-up, right-open
horizons, exhaustion and idle tails match production. Closing an observation
prefix preserves its queried values and does not exhaust pending jobs. No final
trace artifact or stored trace outcome exists in mode `none`.

## Implementation and correctness

A small recorder-factory seam in `DirectProjection` permits the observation
subclass to return `None`; constructor/close cleanup handles that case. Recorded
paths still construct the same writer in the same lifecycle position. The
production research adapter gains a subclass-controlled switch for retaining
derived totals and attaches the WIP-series declaration in both modes.

The differential gate covers **1,536 configurations / 3,072 model runs per
interpreter**, with sixteen prior/fractional/burst workload families, caps 1/2/12,
FIFO/SPT, independent/feedback producers, none/full recording, warm-up 0/2, and
exhaustion/horizon 4. It checks every intermediate `add()`/`advance()` query and
eleven callback-time event probes against actual production.

Native/production final values and callback probes agree exactly in the gate;
recording-off/on rows also agree exactly after grouping by the other parameters.
CPython and PyPy rows are identical, SHA256:
`e3bf8d686716b7f2eb03b129a3a48e8af1031a92f970c4fb6fdaed5e7f35814f`.
Continuous large-run data is compared point by point with relative tolerance
`2e-11` and absolute tolerance `1e-8`, without rounding.

The new fourteen targeted tests cover absence of recorder/digest/projection,
the full collector bundle, retained totals, boundary versus buffered delivery,
warm-up and partial windows, idle tails, close without explicit `result()`,
callback failure/control guards, and worker/profile smoke cases. Every no-trace
benchmark/profile sample runs with recorder construction, digest enabling and
projection requests replaced by functions that raise on an attempted call.
This detects accidental recording work rather than merely checking file size.

Prototype suites pass **398 CPython tests (one skipped)** and **396 PyPy tests
(three skipped)**. PyPy retains the two known timeout-configuration warnings;
the added profiler test is explicitly CPython-only. The 156 production trace,
ConWIP and invariance checks pass. The instrumented CPython boundary/adapter
suite passes 85 tests; standalone ASan/UBSan engine checks pass 143 scalar,
143 rich and 144 bulk allocation-failure positions plus growth/flush/lifetime
checks. As before, the boundary run uses malloc-backed Python, disables leak
detection and links the release engine; subprocess smoke cases use their normal
compiled extension. This is not integrated PyPy sanitizer certification.

The default and stock-observed recorded fixtures regenerate with identical raw
source bytes and normalized trace bytes. Existing fixtures and Studio code are
unchanged; no new browser functionality is introduced.

## Measurement design

The new matrix contains **48 cases**: 5k/20k jobs, independent/feedback producers,
none/full recording, production/native-buffered/native-boundary and CPython/PyPy.
Both recording modes are measured on the current frozen sources. The previous
recorded results remain historical evidence, rather than serving as the sole
causal control for disabling the writer.

Cases run serially in fresh processes, shuffled with seed `20261102`. CPython
has one warm-up, PyPy seven, followed by five timed repetitions and a separate
cold/RSS probe. All cases parse the same 50k-job file before timing and select
the measured prefix. The worker retains the original workload local; interpreter
GC input retention is not isolated. The model uses ten unit-capacity servers,
SPT and WIP cap 12.

The sample function is shared with the preceding observation benchmark, with a
conditional final trace path. Timing includes construction, feeding, run, detached
trajectories/model totals, copying every stock history/scalar, transport/writer
I/O, close/join and final GC. Imports, DSO loading, input parsing, initial GC,
validation/hashing and compressed export are excluded. Cold probes also exclude
startup. Their whole-process peak RSS is captured before validation/compression
but includes earlier process/input/import peaks.

Complete numeric observer exports are retained as compressed JSON, shared across
recording levels and native delivery modes only when the exact exported payload
agrees. All values are compared across backends/interpreters; no-trace runs also
have exact output hashes checked against prior recorded data. Full controls
retain structural/digest checks and expected event counts. Native diagnostic
`callbacks=0` concerns callbacks inside the C model; the Python stock collectors
run and their cost is included.

Frozen source SHA256, covering 234 files:
`3e91986452f4112bc4513c80192ac72cd57fb693efc14a12167c74d28ba4f317`.
Input SHA256:
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.
The engine, compiled/ctypes boundary sources and all four recorded library hashes
must match `d4aa1f2`. Only Python research adapter/measurement code changes.

## Timing and memory results

All numbers are median lifecycle seconds over five samples. Each cell is
**no final trace / full trace**, on the same frozen sources and inputs:

| Jobs | Producer | Interpreter | Production | Native buffered | Native live |
|---|---|---|---:|---:|---:|
| 5k | Independent | CPython | 1.001 / 2.522 | 0.673 / 2.007 | 0.784 / 2.116 |
| 5k | Independent | PyPy | 0.352 / 1.623 | 0.508 / 1.765 | 1.446 / 3.138 |
| 5k | Feedback | CPython | 0.768 / 1.900 | 0.561 / 1.577 | 0.654 / 1.703 |
| 5k | Feedback | PyPy | 0.270 / 1.250 | 0.480 / 1.398 | 1.473 / 2.847 |
| 20k | Independent | CPython | 5.605 / 13.449 | 2.738 / 9.746 | 3.029 / 9.903 |
| 20k | Independent | PyPy | 1.802 / 8.504 | 2.336 / 8.851 | 8.890 / 17.923 |
| 20k | Feedback | CPython | 3.088 / 7.774 | 2.152 / 6.336 | 2.631 / 6.798 |
| 20k | Feedback | PyPy | 1.172 / 5.317 | 2.355 / 6.342 | 8.823 / 15.946 |

Disabling the final trace saves 61.3–69.4% of native live CPython elapsed time
and 44.7–53.9% on native live PyPy. Production improves too: 58.3–60.3% on CPython
and 78.0–78.8% on PyPy. The trace was masking a larger relative gap in observation
alone, because its removal benefits production PyPy more.

CPython remains the fastest native **live** interpreter in every measured
workload; PyPy is the fastest production interpreter. The untraced comparison is:

| Jobs / producer | Native live CPython | Production PyPy | Native additional elapsed time |
|---|---:|---:|---:|
| 5k independent | 0.784 | 0.352 | 123.0% |
| 5k feedback | 0.654 | 0.270 | 142.1% |
| 20k independent | 3.029 | 1.802 | 68.1% |
| 20k feedback | 2.631 | 1.172 | 124.6% |

Against production CPython, native live none uses 21.7/14.9% less time at 5k and
46.0/14.8% less at 20k, independent/feedback. Thus it provides a CPython benefit
on this slice, while the fastest available production control remains ahead.
Even the best native **buffered** none controls take 29.7–83.7% longer than
production PyPy. Eliminating only the measured boundary surcharge would leave a
gap in these results.

In none mode, live boundary delivery adds 10.6–22.3% over native buffering on
CPython and 184.4–280.5% on PyPy. The strategy changes flushing/call frequency,
consumption batch sizes and execution overlap; this does not isolate `fflush`
latency or prove a particular PyPy/JIT cause. Buffered delivery lacks the required
intermediate visibility and is not an equivalent replacement for live delivery.

Within-case `(maximum - minimum) / median` spread peaks at 6.0% for the 5k PyPy
independent none buffered case; production CPython 5k feedback none spans 5.1%.
These are one workstation and one seeded shuffled matrix, not repeated sessions
or a platform-wide performance certificate.

Cold 20k none native live CPython probes take 3.050/2.674 seconds versus production
CPython 5.599/3.077 and production PyPy 2.293/2.217, independent/feedback. The
native path also trails production PyPy in these probes. They are single samples
excluding startup/import/parsing and cannot establish a fresh-process latency
distribution.

Whole-process cold peak RSS at 20k, MiB, **none / full**:

| Producer | Interpreter | Production | Native buffered | Native live |
|---|---|---:|---:|---:|
| Independent | CPython | 215 / 381 | 195 / 378 | 196 / 343 |
| Independent | PyPy | 289 / 451 | 302 / 412 | 331 / 393 |
| Feedback | CPython | 199 / 241 | 183 / 206 | 185 / 205 |
| Feedback | PyPy | 299 / 363 | 284 / 371 | 341 / 373 |

Disabling the writer reduces these peaks. Native live CPython is below production
CPython on both none cases, but native live PyPy is above production PyPy. These
peaks include input/interpreter/JIT history and all requested collector histories;
they do not isolate the engine heap or establish bounded streaming memory.

All none cases have zero final trace bytes. Native 20k journals retain
20,169,608/15,675,368 bytes, independent/feedback. Sixteen complete observer
exports occupy 75,288,582 compressed bytes, with every point compared numerically
and exact within-backend hashes preserved across recording modes and delivery
controls. The result is not obtained by dropping histories or exporting fewer
model results.

The timing certificate covers **48 cases, 240 timed samples and 48 cold probes**.
Matrix SHA256:
`cedbdae94122f1591a0e8ac7fa0b947e617a1da2985b9a197234ac493bcb4626`.

The complete audit passes: both gates and all profiles match the frozen code,
all none/full observation outputs agree, 261 historical source/test/evidence
files match `d4aa1f2` Git blobs, and all historical full-control semantics and
stock exports are preserved. The five native engine/boundary source files and
all four library hashes are unchanged. It regenerates both fixed-provenance
recorded fixtures with exact source and normalized trace bytes. New benchmark
provenance identifies the new source hash, so its historical whole-file wire
hash is not required to remain identical. Four deliberate trajectory/scalar/
observation/trace corruptions are rejected.

## CPU profiles

Eight separate CPython diagnostics cover production/native-boundary, none/full
and both producers at 5k jobs. They use the existing Python profiler with
`time.thread_time`, uninstrumented warm-up and separate main/writer passes.
Every profile pass must reproduce the uninstrumented collector/trajectory
output. No-trace profiles must have zero writer profiles.

Profiles are not speed samples. Self CPU is exclusive to a function; cumulative
CPU includes callees and must not be added across nested functions. Writer and
main profiles are separate perturbed executions, not additive wall-time phases.
There is no instrumented PyPy profile or causal attribution of its JIT behavior.

Main/writer thread CPU below is from separate instrumented passes, in seconds:

| Producer | Recording | Path | Main profile CPU | Writer profile CPU |
|---|---|---|---:|---:|
| Independent | None | Production | 7.589 | 0 |
| Independent | None | Native boundary | 4.316 | 0 |
| Independent | Full | Production | 13.013 | 2.236 |
| Independent | Full | Native boundary | 9.331 | 2.237 |
| Feedback | None | Production | 4.732 | 0 |
| Feedback | None | Native boundary | 3.696 | 0 |
| Feedback | Full | Production | 9.261 | 1.234 |
| Feedback | Full | Native boundary | 7.829 | 1.215 |

The profiles locate substantial recorded-path work in main-thread digest and
writer delivery as well as the writer thread. Native full
`SemanticDigest._on_event` cumulative CPU is 2.344/1.859 seconds; writer
`_on_event` adds 0.972/0.784 cumulative CPU, independent/feedback. These functions
disappear in none mode. These are nested CPU diagnostics, not additive estimates
of benchmark savings.

Observation delivery remains substantial without recording. On native none,
`Environment.emit` cumulative CPU is 2.494/2.007 seconds, including downstream
callbacks. Exclusive self CPU in `collectors.py` plus `kpi.py` is 1.014/0.823
seconds. `Reader.take` cumulative CPU is 0.169/0.146 seconds; the profile does not
identify journal reading as the largest CPU item. Those totals do not quantify
I/O waiting or predict the speedup of replacing transport.

An unambiguous count difference identifies a narrower candidate:

| 5k producer, none mode | Production `Environment.emit` calls | Native calls | Stock `Collector.deliver` calls, either path |
|---|---:|---:|---:|
| Independent | 151,815 | 216,541 | 960,342 |
| Feedback | 118,060 | 169,284 | 730,808 |

Native constructs/publishes 42.6/43.4% more events for the same stock delivery
count. The difference is exactly `2 * operations + 2 * jobs`. Code inspection
identifies the native publisher's unconditional operation-start, server-work-credit,
policy-decision and PSP-exit emissions as having no stock subscriber. Actual
production uses its normal interest guards. A subsequent opt-in native
subscriber-interest check could avoid this work while preserving view updates
and emitting any event a user actually subscribes to. It is not implemented or
benchmarked here. Full recording requests those events and must preserve its
recorded output.

The production independent profile also shows the ConWIP pool selection doing
about three million priority-lambda/property calls at 5k jobs. This contributes
to the CPython model cost that native scheduling avoids; the diagnostic does not
establish how PyPy optimizes it.

## Artifacts and reproduction

- [Adapter](../../prototypes/native_engine_dynamic/live_observation.py)
- [Targeted tests](../../prototypes/native_engine_dynamic/tests/test_live_untraced.py)
- [Gate](../../prototypes/native_engine_dynamic/untraced_gate.py)
- [Matrix, profiles and audit](../../prototypes/native_engine_dynamic/untraced_benchmark.py)
- [CPython gate certificate](../../prototypes/native_engine_dynamic/untraced-results/gate-cpython/verification.json)
- [PyPy gate certificate](../../prototypes/native_engine_dynamic/untraced-results/gate-pypy/verification.json)
- [Frozen metadata](../../prototypes/native_engine_dynamic/untraced-results/scaling/metadata.json)
- [Raw timings](../../prototypes/native_engine_dynamic/untraced-results/scaling/matrix.jsonl)
- [Full observer exports](../../prototypes/native_engine_dynamic/untraced-results/scaling/observations/)
- [Profiles](../../prototypes/native_engine_dynamic/untraced-results/profiles/)
- [Summary](../../prototypes/native_engine_dynamic/untraced-results/summary.json)
- [Complete certificate](../../prototypes/native_engine_dynamic/untraced-results/verification.json)

Use this experiment's checkout, built native libraries and fresh destinations:

```bash
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_gate --output /tmp/untraced-gate-cpython
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.untraced_gate --output /tmp/untraced-gate-pypy
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_benchmark matrix --output /tmp/untraced-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_benchmark profile --input prototypes/native_engine/.inputs/jobs-50000-load-0.95.json --jobs 5000 --producer independent --level none --backend boundary --output /tmp/untraced-profile.json
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_benchmark audit-experiment --output prototypes/native_engine_dynamic/untraced-results
```

The complete audit expects the committed gate/scaling/profile layout and
historical `d4aa1f2` Git objects. Run all eight profile combinations described
above before that audit. The Python-only change needs no native rebuild relative
to the preceding gate.

## Implications and next step

Removing trace recording is a substantial improvement, and it preserves the
demonstrated stock observation contract. It does not establish a performance
advantage over production PyPy with this full Python collector bundle. Keep the
path opt-in and state the CPython benefit and PyPy comparison separately.

The recommended next experiment is a **native subscriber-interest guard** in
the untraced publisher. Preserve mandatory view/counter updates and publish every
event an observer requests; skip construction/delivery only when no subscriber
requests that class. Require the current native/production, recording-invariance,
callback-time and historical fixture gates to keep passing. Then measure the same
untraced workloads against this frozen baseline. The 43% excess publication
count provides a concrete hypothesis; it does not promise a 43% runtime reduction
or that this change will close the gap to PyPy.

Do not widen the actor API or tune the C scheduler on the basis of these results.
Native journal I/O and PyPy boundary behavior remain separate possible targets,
but the profiles do not justify choosing a transport rewrite first. No public
backend, packaging, transport replacement or collector optimization is included.
The recommended follow-up has not been implemented or adopted as a commitment.

Follow-up: Davide authorized the
[subscriber-interest experiment](2026-11-02-native-interest.md), retaining the
original publisher as a paired control and testing requested-event/view fidelity.
