# Native scaling, event ordering and observation gate

Starting revision `62b1f85`, `feature/native-engine-feasibility`.
Davide authorized the [next gate](2026-11-01-native-gate-plan.md) after the
[direct emitter experiment](2026-11-01-native-direct.md). The C engine, compiled
binding, source journal, both emitters, production actors and recorder are fixed.
Only research harnesses, characterization tests and evidence are added.

## Decision

**The gate fails general production equivalence and live-observer compatibility.**
Equal-time cases can change which job reserves a server and when jobs finish.
The native publisher also lacks the live actor properties used by standard
collectors, and its event callbacks can lag behind the native engine.

At 20,000 jobs, the best measured native full path gives 2.3% higher throughput
for independent jobs and takes 3.3% longer for feedback than production PyPy.
Same-CPython full throughput gains are 1.42–1.55x. Scaling preserves useful
CPython benefits without establishing a consistent lead over the strongest
production interpreter.

The direct emitter remains an opt-in research path. Earlier exact aligned/direct
parity remains valid; earlier production equivalence is limited to its tested
continuous-time workload family. Valid, self-consistent trace files certify the
native execution they describe, while model equivalence requires separate checks.

Follow-up: the [simultaneous-event experiment](2026-11-02-native-semantics.md)
corrects the scheduling mismatch in an expanded differential suite. The live
observation limitations described here remain unresolved.

## Event ordering

Both interpreters run 288 comparisons: eight deterministic workload families ×
caps 1/2/12 × FIFO/SPT × independent/feedback producers × none/KPI/full.
Each comparison runs actual production, the aligned publisher and the direct
publisher: 864 streams per interpreter, including 288 full and 288 KPI traces.
Families cover empty input, arrival bursts, arrivals at completions, crossed
routes, integer-time chains, two quantized seeds and the reduced reproducer.

Aligned/direct outputs agree exactly in every case, including trajectory,
generated inputs, feedback decisions, native KPI totals, source journal, full
signature and normalized wire. All trajectories and native source journals pass
their independent validators; all recorded files pass actual `Trace.check()` and
full digest verification (KPI files correctly report `not_verifiable`).
CPython/PyPy trajectories, signatures, witness events and structured outcomes
agree. Two error messages spell the same class name differently between interpreters.

Against production:

| Difference | Comparisons | Distinct model/policy/producer combinations |
|---|---:|---:|
| Changed start/end/finish times |33|11|
| Changed release times |21|7|
| Changed dispatch sequence |51|17|
| Changed ordered WIP samples |42|21|
| Any compared value |67|25|

Modes repeat a model configuration, so these counts are not independent failure
rates. The workloads are targeted examples, not a statistical sample. Generated
job contents and feedback decisions agree throughout this particular suite;
the changed trajectories and KPI totals still disqualify general equivalence.
All cap-1 comparisons pass this suite. The changed outcomes at caps 2 and 12
show why concurrent completions in deterministic-duration models need explicit
phase semantics even when arrival handling and local queue tie breakers agree.

The 67 total also includes four comparisons affected solely by an existing
research export definition: `ProductionStream.totals()` reconstructs peak floor
WIP with departures before arrivals at the same time. In the boundary/cap-12
case it exports 2, while the actual production event series and native execution
both reach 3. This is a comparison-harness issue, separate from production actor
behavior. It is preserved and identified, with event-series peaks in the evidence;
it does not explain the 33 changed-time cases.

### Four-job reproducer

Three servers, capacity one; cap 2; SPT; independent scalar arrivals. All due
dates are 10. The same mismatch also occurs with FIFO.

| Job | Arrival | Route | Durations | Release, both | Production finish | Native finish |
|---|---:|---|---|---:|---:|---:|
| 0 |5|1 → 0|1, 2|5|9|9|
| 1 |6|0 → 2 → 1|1, 2, 1|6|10|10|
| 2 |6|0 → 1|1, 1|9|11|12|
| 3 |7|1|1|10|12|11|

At time 10, jobs 1 and 2 finish operations on different servers. Production lets
job 2 leave server 0 and reserve server 1 before the SimPy completion-trigger
process releases job 3. The native completion handler calls `release_pending()`
synchronously when job 1 finishes; job 3 enters and reserves server 1 before
job 2's same-time departure is handled. Its eager reservation then determines
the winner even when queued durations tie.

The two event sequences are saved in `reduced_time_10_events`. The reproducer was
obtained by deleting jobs from quantized seed 1 until no further single-job
deletion retained a changed time result. This establishes deletion minimality
for that reduction procedure, not global minimality.

Strict boundaries do pass in the dedicated inspector check: an operation ending
at 2 remains active after `advance(2)`, repeated `advance(2)` does not consume it,
and `advance(nextafter(2, +inf))` observes it finished. A newly registered job at
2 is scheduled before advancing and active afterwards. Inspector correctness at
this boundary does not establish full same-time scheduling equivalence.

## Observation contract

A payload-only `JobFinished` observer receives identical completion payloads in
the dedicated boundary workload. Adding it preserves the trajectory, decisions,
KPI output and normalized trace wire for each backend. This validates this event
consumer and post-drain observation; it is not arbitrary observer certification.

Delivery is buffered. At `nextafter(2, +inf)`, native `inspect()` already reports
job 0 finished, but both publisher clocks remain 0 and neither has delivered the
completion callback. Production has delivered it. After `run()` drains the
journal, all deliver completions stamped 2 and 3. The publisher therefore offers
no synchronous live event-delivery guarantee at `advance()` boundaries.

All five standard production collector classes were exercised on actual actors
and both publisher registries, under both interpreters:

| Collector | Actual production | First missing property in both publishers |
|---|---|---|
| EMACollector |Pass|Shop-floor `env` at construction|
| ShopFloorTimeSeries |Pass|Shop-floor `wip` during events|
| CurrentWorkloadCollector |Pass|Job `_processing_times` during events|
| ServerTimeSeries |Pass|Server `env` at construction|
| ShopFloorKPIs |Pass|Shop-floor `servers` at activation|

Each collector has its own run, so an earlier failure cannot hide a later one.
Each failed probe stops at its first exception; more missing actor behavior may
remain beyond that point.
The stock KPI capability probe removes the benchmark collector from the private
collector registry to avoid their unrelated shared `makespan` name (native totals
versus stock means). Failed collector subscriptions are cancelled before cleanup.
These isolated probes check construction, activation and execution; their files
are discarded. No actor shims are introduced to make the probes pass.

The direct publisher's live `entities.snapshot()` rejection remains intentional;
historical `Trace.state_at()` and native handle inspection remain available.
The aligned publisher also fails all five collector probes, so the direct
emitter did not introduce this compatibility gap. Public integration needs a
deliberate contract: either provide the required live actor behavior or expose
a narrower observation interface with explicit delivery rules and suitable
collectors. The present benchmark totals and two WIP series do not cover all
stock collector estimands or observation windows.

## Scaling method

The 48-case matrix compares actual production and direct native execution with
5,000/20,000 jobs, CPython/PyPy, independent/feedback producers and none/KPI/full.
It reuses the unchanged production worker. The existing 50,000-job, load-0.95
input supplies the two prefixes: ten unit-capacity servers, ConWIP cap 12, SPT.
Both producers construct/copy inputs as before; feedback inspects earlier native
handles or real production actors before constructing the next job.
Default EMA metrics remain disabled in the comparison adapter, as in the previous
study; stock collector compatibility is checked separately above.

Cases run serially in fresh processes, shuffled with seed 20261107. CPython has
one warm-up, PyPy seven; each has five measured repetitions and a separate
one-repetition cold/RSS process. Timings include construction, feed, run,
extraction, trace writer join, close and GC. Imports, DSO loading, JSON parsing
and post-run validation are excluded. Both sizes keep the loaded 50,000-job
source workload resident, including during timed GC; RSS includes this common
harness input, returned arrays, interpreter/JIT and writer buffers. Cold probes
exclude process startup/imports and are not distributions.

Every repetition checks stable trajectory/input/decision signatures and recorded
wire. The first repetition additionally validates routing/resource/cap/KPI/source
invariants and full trace contents. All controls are contemporary. Eight separate
20,000-job aligned cold probes cover both interpreters/producers and recorded
levels, requiring exact aligned/direct source sizes, counters, trace signature
and wire parity. Their times are diagnostic single probes, not warmed comparisons.

Recorder settings remain 10,000 events or 1 MiB per chunk, infinite latency,
64 MiB pending buffer, no fsync. No callback bridge, serializer, kernel change,
writer change, sampling reduction or omitted output is introduced.
In none mode the direct label runs the existing `CompiledStream` with no publisher.

## Scaling results

All **48 cases / 240 measured repetitions / 48 cold probes / 8 aligned parity
probes** complete and pass the audit. Trajectories, generated inputs, controller
decisions, KPI totals within tolerance and ordered WIP series match production
throughout this continuous-time matrix. Aligned/direct output agrees exactly at
20,000 jobs for both recorded levels and both interpreters/producers.

Full lifecycle medians, seconds; each production column uses the named interpreter:

| Producer | Interpreter | Production 5k | Native 5k | Production 20k | Native 20k | 20k throughput ratio |
|---|---|---:|---:|---:|---:|---:|
| Independent |CPython|2.254|1.595|12.292|7.937|1.549x|
| Independent |PyPy|1.471|1.417|7.232|7.066|1.023x|
| Feedback |CPython|1.718|1.229|6.850|4.820|1.421x|
| Feedback |PyPy|1.115|1.166|4.518|4.668|0.968x|

Best measured choices among CPython/PyPy at 20,000 jobs, seconds. Production PyPy
is fastest in every row; native's interpreter is shown explicitly:

| Producer | Output | Production PyPy | Native | Native interpreter | Throughput ratio |
|---|---|---:|---:|---|---:|
| Independent |None|1.1059|0.0293|CPython|37.780x|
| Feedback |None|0.5528|0.0835|CPython|6.618x|
| Independent |KPI|4.3042|3.8504|PyPy|1.118x|
| Feedback |KPI|3.1948|3.2238|CPython|0.991x|
| Independent |Full|7.2315|7.0658|PyPy|1.023x|
| Feedback |Full|4.5177|4.6684|PyPy|0.968x|

Native's no-recording opportunity remains substantial. With recording enabled,
the shared event/digest/recorder path substantially narrows that advantage. The
independent KPI workload improves 11.8% in throughput, whereas feedback KPI takes
0.9% longer. This gate changes no runtime code and measures no new optimization.

The 20k full sample ranges are 7.037–7.078 seconds for native PyPy independent
versus production PyPy 7.200–7.261; feedback is 4.664–4.676 versus 4.509–4.544.
These within-process ranges are disjoint, but there is one shuffled case order on
one host and five in-process repetitions, rather than independent matrix repeats
or cross-machine uncertainty estimates. The largest spread anywhere is 6.16%,
in the small PyPy feedback/no-recording native case. A small observed lead is
bounded evidence, not a general superiority claim.

### Growth, output volume and memory

For a fourfold increase in jobs, native full time grows 4.98–4.99x for independent
jobs on both interpreters and 3.92x/4.00x for feedback (CPython/PyPy). Production
grows 5.45x/4.92x independent and 3.99x/4.05x feedback. Operation counts grow
4.02x and 3.98x respectively. Full trace sizes grow 6.10x independent and 3.99x
feedback. This gate does not profile or isolate the causes of that extra growth.

20k native full files contain 910,420/713,797 domain events and 80,002 simulation
observer events (independent/feedback), with 130/102 chunks. CPython file sizes
are 63,274,087/29,816,250 bytes; PyPy's headers make each two bytes smaller.
Actual production has the same domain counts, 80,000 simulation observer events
plus two prelude samples, and files 63,253,039/29,804,162 bytes on CPython. Native
source journals are 20,169,608/15,675,368 bytes, exactly aligned/direct. Every
native/aligned recorded file agrees in normalized wire and full signature.

Full-level cold-process peak RSS, MiB, at 20,000 jobs:

| Producer | Interpreter | Production | Native |
|---|---|---:|---:|
| Independent |CPython|323|339|
| Independent |PyPy|413|365|
| Feedback |CPython|216|135|
| Feedback |PyPy|299|299|

There is no consistent memory reduction across cases. KPI RSS is also mixed:
native/production CPython 115/136 MiB independent and 117/141 feedback; PyPy
258/260 and 295/235. These are whole-process cold peaks captured before output
validation, including the common resident input and interpreter/JIT. They are
not allocations attributable solely to the emitter or writer.

20k full cold times (native/production) are CPython 7.951/12.255 seconds
independent and 4.800/6.836 feedback; PyPy 7.898/8.868 and 5.868/5.993. Single cold
probes characterize those invocations, including incomplete JIT warm-up; warmed
rankings and cold timings answer different questions.

All 24 contemporary 5k output signatures exactly match the verified preceding
study, including trajectory, input, decisions, WIP series, trace signature/wire
and counters. Historical times/RSS are not a controlled before/after comparison:
this matrix keeps the shared 50k source workload resident, affecting GC and memory
even for a 5k prefix, and uses a new shuffled execution order. Contemporary
production controls remain the comparison used for all speed conclusions.

## Evidence and reproduction

- [Gate harness](../../prototypes/native_engine_dynamic/semantics_gate.py)
- [Scaling runner and audit](../../prototypes/native_engine_dynamic/scaling_benchmark.py)
- [Characterization/evidence tests](../../prototypes/native_engine_dynamic/tests/test_semantics_gate.py)
- [CPython semantic evidence](../../prototypes/native_engine_dynamic/gate-results/semantics-cpython.json)
- [PyPy semantic evidence](../../prototypes/native_engine_dynamic/gate-results/semantics-pypy.json)
- [Semantic audit](../../prototypes/native_engine_dynamic/gate-results/semantics-verification.json)
- [Scaling metadata](../../prototypes/native_engine_dynamic/gate-results/scaling/metadata.json)
- [Raw timing matrix](../../prototypes/native_engine_dynamic/gate-results/scaling/matrix.jsonl)
- [Larger aligned parity probes](../../prototypes/native_engine_dynamic/gate-results/scaling/parity.jsonl)
- [Timing summary](../../prototypes/native_engine_dynamic/gate-results/scaling/summary.csv)
- [Best-path and growth comparisons](../../prototypes/native_engine_dynamic/gate-results/scaling/comparison.json)
- [Scaling audit](../../prototypes/native_engine_dynamic/gate-results/scaling/verification.json)

Runtime source SHA256:
`fe6f37016b77730351c0ebada4d3eba3078cb7dd26210b4866766c8226d4546e`.
Shared input SHA256:
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.
Metadata records all 221 source hashes, unchanged native libraries, host/software,
case order, writer settings and the additional gate-test hash.
All 245 historical source/evidence files from the preceding direct study match
their committed `62b1f85` blobs. All 219 overlapping measured source files are
unchanged; the two additional measured sources are these new gate harnesses.
The scaling audit rejects five corrupt-evidence cases (source, trajectory, wire,
counters and file size), and tests additionally reject a broken phase sum and
changed controller decisions. Matrix SHA256:
`b677a0f0b0eda4283675c2a91bfc44007a30cd2b60917877a5bdef01248602f5`.

From the research worktree:

```bash
.venv/bin/python -m prototypes.native_engine_dynamic.semantics_gate --output /tmp/semantics-cpython.json
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.semantics_gate --output /tmp/semantics-pypy.json
.venv/bin/python -m prototypes.native_engine_dynamic.scaling_benchmark matrix --output /tmp/native-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.scaling_benchmark audit --output prototypes/native_engine_dynamic/gate-results/scaling
```

## Verification and next action

Fresh prototype suites: **327 passed/1 skipped on CPython; 326 passed/2 skipped
on PyPy**, with two known pytest-timeout configuration warnings on PyPy.
The production trace reader/writer/sink, ConWIP and observer-invariance suites
also pass freshly: **156 tests**.
Nine new checks cover the reduced outcome mismatch at all three modes, strict
boundaries/delivery, payload observer invariance, stock collector failures, matrix
coverage and corrupted evidence rejection. Ruff check/format passes for all three
new Python files. No C/binding sanitizer repetition is needed for unchanged code.
The characterization tests assert the current known mismatch; subsequent semantic
work must convert them to production-equivalence expectations. A passing harness
suite therefore coexists with the explicitly failed integration gate.
Production/TypeScript/browser source and old fixtures/evidence remain unchanged;
their preceding compatibility checks are historical, not new large-file Studio
verification. Native full files in this gate are checked by the production Python
reader and digest verifier.

Recommended next experiment: align native same-time completion/release phasing
with production, using the reduced counterexample and the entire targeted suite
as equivalence requirements. Broaden deterministic quantized cases before timing
again. Separately settle the observation interface before claiming a general
backend replacement. Any semantic adjustment can alter performance, so these
measurements describe the current prototype. No additional kernel/serializer
optimization or public backend integration is part of this gate.
