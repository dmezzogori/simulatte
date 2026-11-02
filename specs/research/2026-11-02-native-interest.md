# Native publisher subscriber-interest experiment

Date: 2026-11-02. Starting revision: `0bcaa5c`. Research-only opt-in change in the
existing native feasibility worktree. See the [scope](2026-11-02-native-interest-plan.md)
and preceding [untraced observation study](2026-11-02-native-observation-untraced.md).

## Question

Can checking subscriber interest before native event construction reduce the
remaining Python publication cost while preserving the live read-only contract?

Correctness passes. The guard reduces native publications by 32–33% and live
CPython elapsed time by 10.5–11.9%. PyPy changes are small or mixed, including a
buffered regression. Keep the option explicit; it is not a general runtime win.
Production PyPy remains faster with the stock bundle.

This experiment changes no C/binding code, native journal, production actor,
collector implementation or global event bus.

## Change and contract

`LiveTraceStream(..., level="none", trace_path=None, interest_checks=True)` enables
the candidate. The option defaults to false, retaining the original publisher
as a control. It is inactive in full/KPI recording modes.

The guard is in `ObservationProjection.emit`, **after mandatory view updates**
and before `DirectProjection.emit` constructs the event and `Deltas`. Arrival,
PSP release/completion timestamps, floor counts and workload membership still
advance when their corresponding event is unrequested. The raw-row consumer
still advances the clock, validates every phase and updates reservation/queue/
worked-time counters. No journal records or model work are dropped.

Interest uses the existing environment/bus query. It matches subclasses and
wildcard subscriptions, and the bus invalidates its cache on subscription or
cancellation, including changes made inside a callback. Requested event payloads,
times and event-time reads are preserved. A catch-all subscriber receives the
exact original stream. With partial subscriptions, untraced `seq` values count
only events actually emitted and can compress compared with the original
publisher. This option does not promise the cursor numbering of unrequested
events. Recorded modes bypass filtering and preserve their complete stream.

The native NTRC journal remains the observation transport, including its flush,
read and validation costs. Final TraceRecorder/digest work is disabled in the
timings, as in the preceding study. All five unchanged stock collector classes,
fourteen stock instances at ten servers plus the existing WIP-series declaration,
remain attached. Detached trajectory/model-total and full stock-history exports
remain timed. No stock or global bus optimization is included.

## Publication counts

Let `N` be jobs and `O` operations in a completed benchmark run. The original
native publisher emits `5N + 7O` Python events without a final trace. With the
stock bundle, filtering emits `2N + 5O`, avoiding `3N + 2O` publications. Production
emits `3N + 5O` with its existing adapters.

The four classes identified by the earlier native/production count difference
were operation-start, server-work-credit, policy-decision and PSP-exit events.
The guard also omits PSP-enter events: native maintains the arrival/system state
directly, while production's existing WIP-series collector subscribes to PSP-enter
to update its counter. No stock output needs that native publication; adding an
explicit subscriber restores it. `OperationCompleted` remains requested by
`CurrentWorkloadCollector` and is not omitted.

This removes about 32–33% of the original native publications on these inputs.
The preceding finding of about 43% **excess over production** used a different
denominator and did not include this additional PSP-enter omission. Neither
percentage predicts the elapsed-time reduction.

The benchmark reads the existing environment emission counter once per sample,
after extracting the results. This adds no per-event instrumentation or catch-all
listener that would defeat filtering. Every repetition, cold probe and CPU
profile must agree with the actual expected publication count. The native C
diagnostic event/record counters and source bytes remain unchanged.

## Correctness and verification

The filtered gate passes **1,536 configurations / 3,072 model runs per interpreter**:
sixteen tie/fractional/burst families, caps 1/2/12, FIFO/SPT, independent/feedback,
none/full recording, warm-up 0/2 and exhaustion/horizon 4. Intermediate stock
queries and eleven callback-time probes match actual production. Every row is
identical to the preceding unfiltered gate, and CPython/PyPy rows match. Shared
rows SHA256:
`e3bf8d686716b7f2eb03b129a3a48e8af1031a92f970c4fb6fdaed5e7f35814f`.

The thirteen added tests check that the typed-construction path is not reached
for unrequested classes, view state progresses even with no subscribers, requested
PSP events are delivered, wildcard streams remain exact, and subscription changes
take effect during delivery. They also cover specific payload/time equality,
continuous-input boundary probes, callback failures/control guards, explicit
KPI/full filtering bypass, and worker/profile emission-count smoke checks.

Prototype suites pass **411 CPython tests (one skipped)** and **408 PyPy tests
(four skipped)**. The added CPU-profiler smoke is CPython-only; PyPy retains the
two known timeout-configuration warnings. The 156 production trace, ConWIP and
invariance checks pass. Instrumented CPython boundary/adapter checks pass 98
tests; standalone ASan/UBSan passes 143 scalar, 143 rich and 144 bulk allocation
failure positions plus growth/flush/lifetime checks. As before, the boundary run
uses Python malloc, disables leak detection and links the release engine;
subprocess smokes use normal extensions. No integrated PyPy sanitizer claim.

Default and stock-observed fixed-provenance recorded fixtures regenerate with
exact source and normalized trace bytes. Explicit flag-on/off tests also preserve
KPI/full bytes. Studio code and existing fixtures are unchanged.

## Measurement design

Forty none-mode cases compare two interpreters, two producers, 5k/20k jobs and
five variants: production, native buffered off/on, and native boundary off/on.
Both native variants use the current frozen code; historical results provide
output regression evidence, rather than the sole timing control.

Cases run serially in fresh processes, shuffled with seed `20261102`, with one
CPython/seven PyPy warm-ups, five timed samples and a separate cold/RSS probe.
Both paths parse the same 50k-job input before selecting a prefix. The original
workload stays in a local; interpreter GC retention is not independently isolated.
The model uses ten unit-capacity servers, SPT and WIP cap 12.

Construction, feed/run, full detached trajectory/legacy totals and stock exports,
journal I/O, close and final GC remain timed. Imports, DSO loading, input parsing,
initial GC, validation/hashing and gzip exports are excluded. Cold probes exclude
startup too; their RSS includes preceding import/input peaks and is captured
before validation/compression. Complete numeric exports are retained and compared
point by point across all variants/interpreters, with no rounding (relative
`2e-11`, absolute `1e-8`).

There is no recorded performance claim: the candidate is inactive in recorded
modes, whose compatibility is covered by the gates and exact fixture tests.
The five native engine/boundary sources and all four library hashes must match
`0bcaa5c`.

Frozen source SHA256, 235 files:
`d6c58de9a67bdaf78c917b13f2656da70d36ddc88adb5ebf12137c541fb16203`.
Input SHA256:
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.

## Timing and memory results

Median complete-lifecycle seconds over five samples. All cases have no final
trace; the original and filtered variants use the same frozen code and inputs.

| Jobs | Producer | Interpreter | Production | Buffered original | Buffered filtered | Live original | Live filtered |
|---|---|---|---:|---:|---:|---:|---:|
| 5k | Independent | CPython | 0.982 | 0.671 | 0.583 | 0.744 | 0.666 |
| 5k | Independent | PyPy | 0.344 | 0.471 | 0.460 | 1.330 | 1.335 |
| 5k | Feedback | CPython | 0.754 | 0.553 | 0.492 | 0.661 | 0.591 |
| 5k | Feedback | PyPy | 0.266 | 0.474 | 0.504 | 1.351 | 1.342 |
| 20k | Independent | CPython | 5.602 | 2.711 | 2.393 | 2.997 | 2.642 |
| 20k | Independent | PyPy | 1.753 | 2.266 | 2.147 | 7.909 | 7.678 |
| 20k | Feedback | CPython | 2.993 | 2.241 | 1.944 | 2.630 | 2.346 |
| 20k | Feedback | PyPy | 1.096 | 2.332 | 2.362 | 8.085 | 8.049 |

The CPython live saving is 10.6/10.5% at 5k and 11.9/10.8% at 20k,
independent/feedback. Buffered CPython saves 11.0–13.3%. These consistent gains
are materially smaller than the event-count reduction: requested callbacks,
argument preparation, journal handling, state updates and extraction remain.

PyPy live ranges from a 0.4% regression to a 2.9% saving, with feedback gains
below 1%. Buffered independent PyPy saves 2.2/5.3% at 5k/20k, but feedback
regresses 6.3/1.3%. This does not support enabling the option broadly on PyPy.
The CPython-only profiles cannot explain a PyPy/JIT cause.

The largest within-case `(maximum - minimum) / median` spread is 3.0%, in the
5k CPython filtered live feedback case; production PyPy 5k feedback spans 2.5%.
Most cases are narrower. The CPython gains are consistent in this matrix; the
small PyPy live differences deserve caution. These are one workstation and one
shuffled run, without independent repeated benchmark sessions.

CPython remains the fastest filtered native live path. Against contemporary
production CPython it uses 21.6–52.8% less time, depending on workload. Against
production PyPy it still takes 93.3/122.0% longer at 5k and 50.7/114.0% longer at
20k, independent/feedback. Even the best buffered native choices remain
22.4–77.9% behind production PyPy; the live boundary surcharge alone does not
explain the entire gap.

At 20k, native original/filtered publication counts are 870,420/590,300
independent (32.182% fewer) and 673,797/449,855 feedback (33.236% fewer).
Production counts are 610,300/469,855. Raw native journals remain
20,169,608/15,675,368 bytes, C event/record counters are identical, and all final
trace byte counts are zero. The sixteen complete observer exports occupy
75,288,582 compressed bytes and preserve every historical stock-output hash.

Cold 20k CPython live original/filtered probes take 3.028/2.661 seconds
independent and 2.626/2.337 feedback. Production PyPy takes 2.309/2.126 in those
probes. Filtering helps this CPython cold slice, while the production PyPy probes
are still shorter. There is one cold sample per case, excluding startup/import/
parsing; it is not a latency distribution.

Whole-process cold peak RSS, 20k, MiB:

| Producer | Interpreter | Production | Buffered original / filtered | Live original / filtered |
|---|---|---:|---:|---:|
| Independent | CPython | 214 | 198 / 195 | 198 / 196 |
| Independent | PyPy | 278 | 277 / 313 | 317 / 340 |
| Feedback | CPython | 203 | 186 / 183 | 188 / 186 |
| Feedback | PyPy | 265 | 319 / 307 | 335 / 335 |

CPython filtering has small lower peaks; PyPy is mixed. These are whole-process
peaks including prior import/input activity and requested histories. They do not
isolate allocator behavior or certify an engine-only memory improvement.

The certificate covers **40 cases, 200 timed samples and 40 cold probes**.
Matrix SHA256:
`714927a01ca7e859bf44d3272b01618b0b8a3a51b2de3c99af29e2498c07c61d`.

The complete audit passes: all filtered gate rows match the old control rows,
every numeric export matches current production/controls and historical hashes,
publication counts agree in every timed/cold/profile run, and all six profiles
reproduce their timed outputs. It verifies 271 historical source/test/evidence
files against `0bcaa5c` Git blobs, confirms the five native source/boundary files
and four library hashes are unchanged, and regenerates both recorded fixtures
with exact source/normalized trace bytes. Four deliberate trajectory, scalar,
observation-hash and publication-count corruptions are rejected.

## CPU profiles

Six separate CPython diagnostics cover production and native boundary off/on,
both producers at 5k jobs. The existing Python profiler uses `time.thread_time`
after uninstrumented warm-up. No writer is present; every pass must reproduce
stock/trajectory output and its emission counter. Self CPU is exclusive;
cumulative CPU includes callees and must not be summed across nested functions.
Profiles are diagnostics, not speed samples. No instrumented PyPy/JIT claim.

Profile main-thread CPU at 5k jobs, seconds:

| Producer | Production | Native original | Native filtered |
|---|---:|---:|---:|
| Independent | 7.393 | 4.243 | 3.963 |
| Feedback | 4.740 | 3.630 | 3.415 |

The filtered profile uses 6.6/5.9% less main CPU, independent/feedback. This is
instrumented diagnostic CPU, not the timed speedup. The smaller change than the
publication-count reduction is expected: skipped events had no handlers, while
the requested collector work, journal processing and mandatory state updates
remain.

| 5k producer | Measurement | Original calls | Filtered calls |
|---|---|---:|---:|
| Independent | Mandatory observation `emit` path | 216,541 | 216,541 |
| Independent | Typed construction / `Environment.emit` | 216,541 | 146,815 |
| Independent | Stock `Collector.deliver` | 960,342 | 960,342 |
| Independent | Journal `Reader.take` entries/resumptions | 166,818 | 166,818 |
| Feedback | Mandatory observation `emit` path | 169,284 | 169,284 |
| Feedback | Typed construction / `Environment.emit` | 169,284 | 113,060 |
| Feedback | Stock `Collector.deliver` | 730,808 | 730,808 |
| Feedback | Journal `Reader.take` entries/resumptions | 133,063 | 133,063 |

The counter sampled without profiling agrees with these actual publication
counts. Production emits 151,815/118,060 events; the filtered native count is
lower by one PSP-enter event per job, for the series-adapter reason above.
The mandatory path and collector counts show the optimization did not obtain
its benefit by losing view updates or stock observations.

`Environment.emit` cumulative CPU falls from 2.453 to 2.268 seconds independent
and 1.959 to 1.828 feedback. Stock-delivery cumulative CPU stays near
1.65/1.33 seconds. Journal-reader cumulative CPU stays near 0.163/0.144 seconds.
These nested times cannot be added together. They locate a bounded publication
saving while showing substantial remaining shared work.

## Artifacts and reproduction

- [Live adapter](../../prototypes/native_engine_dynamic/live_observation.py)
- [Targeted tests](../../prototypes/native_engine_dynamic/tests/test_interest.py)
- [Matrix, profiles and complete audit](../../prototypes/native_engine_dynamic/interest_benchmark.py)
- [CPython gate](../../prototypes/native_engine_dynamic/interest-results/gate-cpython/verification.json)
- [PyPy gate](../../prototypes/native_engine_dynamic/interest-results/gate-pypy/verification.json)
- [Frozen metadata](../../prototypes/native_engine_dynamic/interest-results/scaling/metadata.json)
- [Raw timings](../../prototypes/native_engine_dynamic/interest-results/scaling/matrix.jsonl)
- [Complete numeric exports](../../prototypes/native_engine_dynamic/interest-results/scaling/observations/)
- [CPU diagnostics](../../prototypes/native_engine_dynamic/interest-results/profiles/)
- [Summary](../../prototypes/native_engine_dynamic/interest-results/summary.json)
- [Complete certificate](../../prototypes/native_engine_dynamic/interest-results/verification.json)

Use this checkout and fresh output destinations. Native binaries need no rebuild
relative to `0bcaa5c`:

```bash
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_gate --interest-checks --output /tmp/interest-gate-cpython
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.untraced_gate --interest-checks --output /tmp/interest-gate-pypy
.venv/bin/python -m prototypes.native_engine_dynamic.interest_benchmark matrix --output /tmp/interest-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.interest_benchmark profile --input prototypes/native_engine/.inputs/jobs-50000-load-0.95.json --jobs 5000 --producer independent --backend boundary --interest-checks --output /tmp/interest-profile.json
.venv/bin/python -m prototypes.native_engine_dynamic.interest_benchmark audit-experiment --output prototypes/native_engine_dynamic/interest-results
```

The complete audit expects the retained gate/scaling/profile layout and
historical `0bcaa5c` Git objects. Run all six producer/variant profile combinations
before that audit.

## Implications and next step

Retain the option as an experimental CPython optimization, with the original
publisher as default/control. Its narrower publication path preserves the
demonstrated contract and saves about one tenth of live elapsed time here.
The PyPy results do not support a blanket default change, and users who request
every event have no unused publications for the guard to remove.

The data still favors production PyPy for this complete Python collector bundle.
Further C scheduler tuning is not indicated by these profiles. If work continues,
the next useful diagnostic is the large PyPy live-versus-buffered delivery gap,
using an appropriate PyPy profiler before selecting a boundary or transport
change. Buffered controls themselves still trail production, so eliminating that
gap alone would not establish a general native lead. The continued cost of
requested Python observation work is a real constraint on the fast-path scope.

This experiment is complete. No public integration, transport rewrite, broader
actor API or additional optimization is included; the suggested diagnostic is
not implemented or adopted as a commitment.
