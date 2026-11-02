# Native live observation contract experiment

Date: 2026-11-02. Starting revision: `c69bcbe`. Research-only, opt-in adapter;
the production package, public API, dependencies and default buffered emitter
are unchanged. See the [authorized scope](2026-11-02-native-observation-plan.md)
and the preceding [simultaneous-event experiment](2026-11-02-native-semantics.md).

## Question and result

Can the corrected native scheduler support useful live observation through
Simulatte's existing stock collectors, without reconstructing a general mutable
Python model or returning to generic delta replay?

The correctness result is yes for the explicitly limited read surface below.
Five unmodified stock collector classes agree with actual production actors at
intermediate boundaries, inside callbacks and at final export. This is evidence
for a read-only observation adapter over this model, not compatibility with
arbitrary production actors, collectors or policies.

With equal stock-collector work, native live delivery takes 12–26% less elapsed
time than production CPython, but the best native live path takes 17–40% longer
than production PyPy. Keep this contract opt-in. The next informative control is
the same live observation workload without trace recording, before choosing a
further optimization or public integration.

## Delivery and lifecycle contract

`LiveTraceStream` adds an opt-in publisher over `DirectProjection` and the
corrected C scheduler. Its default `delivery="boundary"` contract is:

1. `add(job)` publishes the entity's creation before returning its handle.
   Creation can therefore occur at the current boundary; the rule about strict
   times below concerns scheduled model events.
2. `advance(t)` processes native model events strictly before `t`, flushes their
   already-produced journal records, and delivers observations synchronously
   before returning. Scheduled events at `t` remain pending. On return the
   observation clock is `t`. Repeated equal boundaries do not advance the model.
3. During each callback, entity reads, `now` and handle `inspect()` describe that
   event's state and time. They do not expose the C engine's later state when a
   single native call has already processed more events. Outside callbacks,
   handle inspection and stream time retain native-engine semantics.
4. Callbacks are observational. Registering, advancing, running, extracting a
   result or closing the stream from a callback raises an error. The observation
   environment rejects scheduling before any queue mutation. This is a
   single-driver adapter, not a general thread-safe actor facade.
5. `run()` exhausts the model. A successful close after a finite horizon seals a
   **completed observation prefix**, matching production, without exhausting
   pending jobs. Completed observation output does not assert that every job
   completed. The horizon remains right-open; an exhausted run uses its normal
   closed observation window. Warm-up and idle tails follow production rules.
6. Callback or transport failures poison the stream and preserve a failed trace
   prefix. They are not silently retried as successful delivery.

The only C engine implementation change is `nd_flush()`: it flushes the current
source file buffer, without advancing time, scheduling events or adding records.
It is also exposed through the scalar extension, ctypes wrapper and Python
reference. This makes bytes visible to the reader; it is not an `fsync` or a
durability guarantee. The audit removes this one function and requires the rest
of the C engine source to match `c69bcbe` exactly.

`delivery="buffered"` is an experimental cost control using the same new views
and stock collectors. Its observations may lag at an intermediate `advance()`
until journal bytes become available. Its callbacks still see event-time reads,
and its final exports are identical to boundary delivery. It does not provide
the boundary-delivery guarantee. The preceding `DirectTraceStream` remains a
separate, unchanged default with its original output and close behavior.

## Supported reads

| View | Supported observation surface |
|---|---|
| Job | Immutable input processing times, creation and due dates; release/completion timestamps, `done`, elapsed PSP/shop-floor times, and the inherited completed-job due-date-window predicate. Unavailable elapsed times raise the same errors as production. |
| Shop floor | Observation environment, a read-only server-to-workload mapping, a fixed tuple of server views, and `len(jobs)` / `len(jobs_done)`. |
| Server | Observation environment, capacity one, occupant count, `len(queue)`, and credited worked time. |
| Native handle | Existing identity/ownership checks; event-time inspection during delivery and native inspection outside delivery. |

The job/queue count views support length only, not iteration, indexing or actor
methods. Mutation, general queue access, model hooks and post-activation live
snapshots are outside this contract and are rejected where exposed. Creation and
activation snapshots remain available to the trace; historical seeking uses the
ordinary `Trace` reader. No general state dictionary or main-thread
`apply_deltas` replay is added.

The implementation reuses the direct emitter's typed WIP, reservation and queue
counters, adding minimal per-job arrival/release/completion state and floor
counts needed for event-time reads. Workload mapping order follows first use,
as production does. Job views follow the existing registry retirement mechanism
and can be collected after completion.
Native results and input/timing arrays, plus the requested collector histories,
still retain run data. This does not establish bounded memory for an infinite
stream.

One observation-order adjustment is necessary: on departure, production emits
the shop-floor WIP update while the server is still held, then emits its release.
The opt-in publisher reproduces that order so a stock collector's live reads
agree with its event. This changes neither the C calendar nor the old direct
publisher. In this model, without operation hooks, first queueing equals release
and last departure equals completion; the simplified job timing reads rely on
that model restriction.

## Collector compatibility and KPI definitions

Both compared paths attach the actual, unmodified `EMACollector`,
`ShopFloorTimeSeries`, `CurrentWorkloadCollector`, `ServerTimeSeries` and
`ShopFloorKPIs` before activation. A ten-server benchmark therefore has fourteen
stock collector instances: four on the floor and one on each server. Both paths
also retain the two system/floor WIP trace series used in preceding experiments.

Exports include all seven EMA values, the four shop-floor histories, workload
history, each server's queue/utilization histories, all eight stock scalar KPIs,
and the observation window. The benchmark copies those values inside the timer.

There is a pre-existing naming conflict to avoid: stock
`shopfloor/makespan` means mean time from job creation to completion, whereas the
earlier research benchmark's identically named scalar means absolute last
completion time. The
opt-in adapter registers the stock scalars and only the two series from the old
benchmark collector. Native result dictionaries still expose their original
model totals. It does not redefine the stock KPI or compare the two meanings as
though they were equivalent.

Consequently, times in this report measure a different collector/output workload
from previous reports. Comparisons within this experiment are matched; differences
from previous experiments cannot be attributed solely to flushing or scheduling.
The preceding research exporter's four known tie-heavy `max_floor_wip` cases
remain a separate limitation of that exporter, not a repaired public metric here.

## Correctness evidence

Each interpreter passes **1,536 configurations / 3,072 model runs**, comparing
native boundary delivery with actual production actors. The dimensions are
sixteen workload families (eight prior semantics cases, including the reduced
counterexample, and eight fractional, burst-heavy families), WIP caps 1/2/12,
FIFO/SPT, independent/feedback producers,
KPI/full traces, warm-up 0/2, and exhaustion/horizon 4.

Checks cover every intermediate `add()` and `advance()` checkpoint, final stock
values, complete histories and eleven event-type probes. The probes read floor
and server counts/workloads and job timing/state from inside real callbacks.
Completed traces pass structural checks and full semantic verification; KPI
traces correctly report that full verification is unavailable. Early close
preserves the queried stock values and production's observation-window meaning.

The CPython and PyPy persisted rows agree exactly. In these binary-fraction
workloads all native/production final-value and callback-probe canonical hashes
also agree exactly. Their shared rows SHA256 is
`3951eb9aa137489126cc32f222c823c1b4855764c6895d07d6491dea9d5a9b03`.
Continuous-time checks compare every numeric value with relative tolerance
`2e-11` and absolute tolerance `1e-8`; they do not round histories into agreement.

Targeted tests additionally cover:

- A callback on the first completion sees a later job still in the pool even
  though C has already completed it, directly detecting look-ahead errors.
- Read-only views, unsupported scheduling, callback control rejection,
  handle ownership, retirement after garbage collection and repeated boundaries.
- Horizon/warm-up cases, empty input and a continuous 80-job workload with an
  idle observation tail.
- Flushing with no journal, an open journal and a successfully sealed engine;
  exact calendar/result/source parity with and without explicit flushes.
- Transport failures and callback exceptions, including a valid failed prefix.
- A buffered control with event-time callbacks and deliberately delayed boundary
  visibility, plus equal-work benchmark worker smoke checks.
- Exhaustion followed directly by close, without an explicit `result()` call,
  for both delivery modes and both recording levels, including repeated close.

Prototype suites pass **384 tests on CPython (one skipped)** and **383 on PyPy
(two skipped)**. The latter has two known pytest-timeout configuration warnings.
There are 40 new observation tests, sized to the existing prototype tests.

The standalone ASan/UBSan engine harness passes scalar/rich/bulk allocation-failure
injection at 143/143/144 positions, growth/overflow/lifetime checks and periodic
flushes. The instrumented CPython boundary passes 71 existing/new boundary and
observation tests. That boundary run uses Python's malloc allocator with leak
detection disabled and links the release engine; the separate engine harness
instruments the engine. This is not an integrated PyPy sanitizer or leak
certificate.

The new `observed` fixture contains 252 domain events, 50 observer events and
15 chunks, uses warm-up 2 and stock KPIs, and has semantic digest
`181bad5ae056b96b2d773992193197549243c822810ccb65cfcc4a5bcf500cc7`.
All four preceding fixtures remain unchanged. Studio checks pass 301 trace,
29 playback and 16 scene tests, the three type checks and both application
builds, plus fifteen browser smoke cases: all five fixtures in three engines.
The 156 existing production trace, ConWIP and observer-invariance checks also
pass.

Review caught an inherited wrapper defect: its close path marked the stream
closed before trying optional result extraction, so `run(); close()` without
`result()` raised an error. A new failing regression reproduced it in KPI/full
mode. The opt-in adapter now extracts while still open; the old wrapper is left
unchanged and remains a follow-up. The initial cost run was deliberately stopped
after seven cases, quarantined locally under
`/tmp/native-observation-interrupted-6e004a09`, and excluded. Both correctness
gates and the full cost matrix were restarted on the final source fingerprint.

## Matched cost experiment

The frozen matrix has 36 cases: CPython/PyPy, independent/feedback producers,
5,000 jobs with KPI/full recording and 20,000 with full recording, each on
production, native buffered observation and native boundary observation.
There is no 20,000-job KPI timing claim. Every case enables a trace, including
the KPI cases; this matrix does not measure live stock collectors without trace
recording or semantic-digest work.

Cases run serially in seeded shuffled order (`20261102`) in fresh processes.
CPython has one warm-up, PyPy seven; each has five timed repetitions and a
separate cold/RSS probe. Both paths parse the same 50,000-job input before timing
and select the measured prefix; the worker retains its `original` workload local.
No independent measurement isolates input retention from interpreter GC behavior.
The model has ten unit-capacity servers, SPT dispatch and WIP cap
12. Feedback-generated jobs and dispatch decisions are checked against the same
production reference.

Timing includes construction, feeding, run, model-result extraction, copying all
stock values, trace/journal writing, close/join and final garbage collection.
Imports, DSO loading, input parsing, initial garbage collection and subsequent
validation/hashing/compression are outside the timer. The cold sample excludes
startup too; it is one no-warm-up probe, not a cold-start distribution.
Whole-process cold peak RSS is captured before validation and compressed export;
it includes the interpreter/JIT and input/model storage retained by that process.

Each distinct full observer export is retained as compressed JSON outside the
timer, with no numeric rounding. The audit compares all values point by point
across backends, interpreters and recording levels. Native buffered/boundary
outputs must additionally have identical normalized trace bytes, digests, sizes,
counters and observation hashes. A zero native `callbacks` counter means zero
callbacks inside the C model; Python stock observers do run and are timed.
The buffered/boundary timing difference measures the whole delivery strategy:
flushing, boundary calls, shorter consumption batches and changed overlap/JIT
behavior. It does not isolate the cost of `fflush()` alone.

Frozen source SHA256, covering 232 files:
`0e8478b0a7bc9f6ae7b6df6dffdffe080bd924c5055584a2ae0a09d8e5124948`.
Input SHA256:
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.
Frozen library hashes, versions, case order and individual samples are retained
with the raw evidence.

### Timing and memory results

All times below are medians of five measured lifecycle samples, in seconds.
"Buffered" and "live" use the same new views and stock collectors; only live
provides the synchronous boundary guarantee.

| Jobs / recording | Producer | Interpreter | Production | Native buffered | Native live |
|---|---|---|---:|---:|---:|
| 5k KPI | Independent | CPython | 1.843 | 1.364 | 1.501 |
| 5k KPI | Independent | PyPy | 1.110 | 1.263 | 2.442 |
| 5k KPI | Feedback | CPython | 1.441 | 1.139 | 1.264 |
| 5k KPI | Feedback | PyPy | 0.906 | 1.090 | 2.343 |
| 5k full | Independent | CPython | 2.521 | 2.019 | 2.077 |
| 5k full | Independent | PyPy | 1.646 | 1.765 | 3.028 |
| 5k full | Feedback | CPython | 1.929 | 1.594 | 1.698 |
| 5k full | Feedback | PyPy | 1.215 | 1.448 | 2.892 |
| 20k full | Independent | CPython | 13.440 | 9.727 | 9.906 |
| 20k full | Independent | PyPy | 8.435 | 8.960 | 17.930 |
| 20k full | Feedback | CPython | 7.730 | 6.314 | 6.786 |
| 20k full | Feedback | PyPy | 5.409 | 6.418 | 16.696 |

CPython is the fastest native live interpreter in every measured workload.
Relative to production CPython it uses 12.0–26.3% less time. Production PyPy is
the fastest production control in every workload; native live takes 35.2/39.6%
longer for 5k KPI, 26.2/39.8% longer for 5k full and 17.4/25.5% longer for 20k
full, independent/feedback respectively. Even the fastest native **buffered**
controls take 6.2–20.3% longer than production PyPy. Removing only the measured
boundary-delivery surcharge would therefore leave a gap in this matrix.

Boundary delivery adds 10.1–11.0% to CPython KPI time and 1.8–7.5% to CPython
full time. PyPy adds 93.4–115.0% for KPI and 71.6–160.2% for full. The particularly
large PyPy gap needs profiling of the delivery path; this comparison cannot
identify how much comes from extra boundary calls, transport or fragmented
batch/JIT behavior. It is not evidence for another scheduler optimization.

The largest within-case `(maximum - minimum) / median` spread is 4.0%; the 20k
PyPy feedback live case spans 16.259–16.843 seconds, 3.5%. These are single-machine
measurements in one shuffled matrix, not independent repeated benchmark sessions.
The small CPython full differences of 1.8–2.9% deserve particular caution.

Cold 20k native CPython live probes take 9.950/6.767 seconds, versus production
CPython 13.395/7.739 and production PyPy 10.073/6.938, independent/feedback. Thus
native CPython is near the best production cold probe, about 1–2% lower. One
probe per case cannot establish a cold advantage, and startup/import/parsing time
is excluded. The warmed production-PyPy advantage should not be generalized into
a claim about complete fresh-process startup.

Whole-process cold peak RSS for 20k full, in MiB:

| Producer | Interpreter | Production | Native buffered | Native live |
|---|---|---:|---:|---:|
| Independent | CPython | 368 | 382 | 345 |
| Independent | PyPy | 458 | 394 | 412 |
| Feedback | CPython | 242 | 203 | 202 |
| Feedback | PyPy | 367 | 359 | 376 |

Native live is lower on both CPython cases, while PyPy is mixed. These peaks
include earlier import/input-parsing peaks and retained run data; they do not
isolate view storage or the native event heap.

The CPython native 20k full files contain 63,256,552/29,803,779 bytes, versus
production 63,252,817/29,803,940, independent/feedback. Native journals are
20,169,608/15,675,368 bytes. Domain counts remain 910,420/713,797; native observer
counts are 80,002, production 80,000. The two extra native initialization samples
are retained. No performance result comes from dropping collector history or
omitting model events. The sixteen compressed complete observer exports occupy
75,288,582 bytes and are all checked numerically, with no rounding.

The matrix certificate covers **36 cases, 180 timed samples and 36 cold probes**.
All stock scalars/series agree numerically across production/native and both
interpreters. Native buffered/live outputs agree exactly, including normalized
trace bytes. Matrix SHA256:
`5001e77eee4fd2c8480ad7358a18910abe3f4bf14fb173e001d283b2e80219a0`.

The complete audit revalidates both gate coverages and cross-interpreter rows,
the frozen runtime/binaries/input/test hashes and all timing evidence. It verifies
243 historical source/test/evidence files against `c69bcbe` Git blobs, regenerates
the old default trace with identical source bytes/signature/normalized wire, and
confirms the scheduler implementation is unchanged after removing `nd_flush()`.
Four deliberate corruptions—trajectory, scalar values, normalized trace and
observation hash—are rejected. The audit passes.

## Artifacts and reproduction

- [Adapter and supported views](../../prototypes/native_engine_dynamic/live_observation.py)
- [Targeted tests](../../prototypes/native_engine_dynamic/tests/test_live_observation.py)
- [Differential gate](../../prototypes/native_engine_dynamic/observation_gate.py)
- [Cost matrix and audit](../../prototypes/native_engine_dynamic/observation_benchmark.py)
- [CPython gate certificate](../../prototypes/native_engine_dynamic/observation-results/gate-cpython/verification.json)
- [PyPy gate certificate](../../prototypes/native_engine_dynamic/observation-results/gate-pypy/verification.json)
- [Measured source and binaries](../../prototypes/native_engine_dynamic/observation-results/scaling/metadata.json)
- [Raw matrix](../../prototypes/native_engine_dynamic/observation-results/scaling/matrix.jsonl)
- [Retained observation values](../../prototypes/native_engine_dynamic/observation-results/scaling/observations/)
- [Case summary](../../prototypes/native_engine_dynamic/observation-results/summary.json)
- [Complete experiment audit](../../prototypes/native_engine_dynamic/observation-results/verification.json)
- [Studio fixture](../../prototypes/native_engine_dynamic/fixtures/observed.simtrace)

Run from this experiment's checkout, using fresh output directories for gates and
timing. The final audit expects the retained directory layout and historical
`c69bcbe` Git objects.

```bash
.venv/bin/python -m prototypes.native_engine_dynamic.build
.venv/bin/python -m prototypes.native_engine_dynamic.compiled_build
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.compiled_build
.venv/bin/python -m prototypes.native_engine_dynamic.observation_gate --output /tmp/observation-gate-cpython
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.observation_gate --output /tmp/observation-gate-pypy
.venv/bin/python -m prototypes.native_engine_dynamic.observation_benchmark matrix --output /tmp/observation-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.observation_benchmark audit-experiment --output prototypes/native_engine_dynamic/observation-results
```

## Scope and next decision

This establishes one concrete observation option: stock collectors over a narrow,
read-only view, with synchronous delivery at driver boundaries. It does not add a
public native backend, packaging, arbitrary Python callbacks inside C, mutable
actor compatibility, custom operation hooks or a general simulation environment.

Keep the adapter experimental. It removes the demonstrated stock-read and
delivery-visibility blockers while preserving the corrected calendar, but the
recorded workload still favors warmed production PyPy.

The recommended next experiment is **live stock observation without trace
recording**, with the same production actors, collectors, callback/boundary
checks and detached outputs. Include matched profiling to distinguish view and
callback cost from journal delivery and TraceRecorder/digest work. The current
matrix always includes that trace work, so it cannot establish the performance
of observation alone. Only after that control should a profile justify a fused
boundary call, in-memory record transport or another focused change. No new
kernel tuning, broader actor API, packaging or public integration is justified
by this gate alone. This is a recommendation, not additional implemented work.
