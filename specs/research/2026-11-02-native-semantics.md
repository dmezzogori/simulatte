# Native simultaneous-event semantics

2026-11-02; starting revision `31bf33e`, `feature/native-engine-feasibility`.
Davide authorized the [experiment](2026-11-02-native-semantics-plan.md) after the
[scaling gate](2026-11-01-native-gate.md) exposed equal-time job-outcome differences.
Work stays in the native feasibility worktree.

## Result and scope

**The scheduling mismatch is corrected in the tested model family.** The reduced
four-job counterexample, trajectories/dispatch/WIP series in all 288 targeted
comparisons and a broader 2,560-configuration sweep agree with the unchanged production actors. The sweep
runs production plus four compact/native variants: 12,800 model runs on each of
CPython and PyPy. Their persisted sweep rows agree exactly between interpreters.

The direct and aligned emitters, compiled/ctypes binding API, production actors,
ConWIP implementation and journal format are unchanged. The C engine and compact
Python reference now model the missing initialization and policy-resume steps.
TunedStream inherits those phases while retaining its existing queue operations.

The prototype still has a narrower observation contract than a general production
Environment. Standard live collectors remain unsupported in both publishers,
callbacks remain buffered, and direct live entity snapshots remain unavailable.
This experiment removes the demonstrated scheduling obstacle within its scope;
it does not certify arbitrary SimPy processes, hooks, policies, resource capacities
or public backend integration.

## Why the previous ordering diverged

At a job's final operation departure, the old native engine immediately selected
and enqueued waiting jobs. SimPy resumes a separate completion-trigger process
later. Other already-scheduled departures at the same time can therefore enqueue
their next operation before that trigger releases another job.

Two explicit steps reproduce the relevant production behavior:

1. A released job updates floor membership/release time immediately, then schedules
   its first queue request as an **urgent initialization** event. Arrival process
   initializations and released-job initializations share urgency and retain their
   insertion sequence, matching the relevant SimPy process initialization order.
2. Every operation departure schedules a **normal policy wake-up before resource
   dispatch**, including departures from non-final operations. A pending flag
   coalesces completion signals until the waiting policy process resumes. That
   process checks the current pool and floor state, releases eligible jobs and
   waits on the latest completion signal again.

Coalescing lasts until that pending resume executes. A later departure at the
same timestamp can wake the policy again after it has resumed and resubscribed;
the implementation does not batch all policy work to the end of a timestamp.

Operation start, timed completion, departure and resource dispatch keep their
existing sequence-based ordering. Operation durations are strictly positive;
the processing child's immediate initialization can stay collapsed into start
without changing the relative scheduling of its later completion in this model.
The change adds initialization/policy calendar work without adding source-journal
records or Python callbacks into the C run loop.

Production remains the independent oracle. The Python reference made the phase
model inspectable before porting it to C; compact-model agreement alone was never
accepted as production equivalence.

## Correctness evidence

The first eight new tests failed on the old model: FIFO/SPT × reference/tuned/
ctypes/compiled. After the phase changes they pass. Three existing recorded-mode
characterization tests now require production equality instead of preserving the
known failure.

The reduced case still releases jobs at 5, 6, 9 and 10. Production and all native
variants now finish at **9, 10, 11 and 12**, instead of the old native 9, 10, 12
and 11. At time 10, job 2 reserves server 1 before the policy releases job 3, in
both the actual production trace and the native execution. This also holds for
FIFO, so the fix does not rely on an SPT-specific tie breaker.

The targeted suite preserves all prior dimensions: eight workload families,
caps 1/2/12, FIFO/SPT, independent/feedback and none/KPI/full. On each interpreter:

| Compared against production | Previous gate | Current experiment |
|---|---:|---:|
| Changed start/end/finish times |33|0|
| Changed release times |21|0|
| Changed dispatch sequence |51|0|
| Changed ordered WIP samples |42|0|
| Known research peak-export artifacts |4|4|

Every native/aligned source journal, result and recorded signature/wire agrees
exactly with its counterpart. Independent routing/cap/resource/KPI checks pass;
actual production `Trace.check()` and full digest verification pass on all full
files. KPI files correctly report `not_verifiable`. Strict `advance(t)`, repeated
boundary and `nextafter(t, +inf)` observations remain unchanged.

The four retained differences are specifically the pre-existing research export
artifact: `ProductionStream.totals()` reconstructs the instantaneous floor peak
with departures before arrivals. For the boundary/cap-12/independent workload,
FIFO/SPT × KPI/full, it exports 2 while actual production event samples and native
both peak at 3. `certify_targeted()` permits only those four exact cases and
requires the actual event-series peaks to match. No production actor or exporter
was changed to make the scheduling comparison pass.

The broader deterministic sweep uses 128 seeds, 48 jobs, 1/2/3/5/8 servers,
fractional/integer durations, tied due dates/priorities, all-at-zero bursts and
mixed simultaneous arrivals/completions. Caps are none/1/2/4/12, with both queue
policies and both input producers: **2,560 configurations per interpreter**.
Production ConWIP uses cap N as the nonbinding oracle for the N-job uncapped
native case. Every reference, tuned, ctypes and compiled trajectory, generated
input and feedback decision agrees with production. All four variants also
agree on result counters. CPython/PyPy sweep output hashes are identical.

This is extensive deterministic differential evidence for the supported model,
not a proof for all real-number inputs or general SimPy event graphs. Production
APIs outside the benchmark model remain outside the claim.

## Trace and memory-safety verification

The journal remains `NTRC0001`, with 32-byte records and the same count
`4N + 5 operations`. The `.simtrace` schema and both emitters are unchanged. Old
native/lifecycle/direct fixtures remain byte-for-byte intact. The new
`semantics.*` fixture contains 252 domain events, 50 observer events and 15 chunks;
its verified digest is
`e133ed46e6c7d86b775b5a5ebecdfd16524399361536c0236303e5d0bd8eff86`.
The fixture generator now defaults to its new name to preserve earlier fixtures.

- CPython prototype suite: **344 passed, 1 sanitizer-only skip**.
- PyPy prototype suite: **343 passed, 2 skips**, with the two existing
  pytest-timeout configuration warnings.
- Production trace reader/writer/sink, ConWIP and observer-invariance suites:
  **156 tests pass** freshly after measurement.
- Standalone instrumented C engine: ASan/UBSan, reduced four-job FIFO/SPT case,
  randomized growth/overflow/lifetime checks and **143 scalar / 143 rich /
  144 bulk allocation-failure positions** pass.
- Instrumented CPython boundary: **31 tests pass**, including allocation failures,
  malformed inputs, copied results, GIL release and concurrent close rejection.
  The boundary links the release engine DSO; the separate executable instruments
  the engine itself. Python malloc backing is enabled, leak detection disabled.
- TypeScript: **301 trace / 28 playback / 16 scene tests**, three type checks,
  player/viewer build and **12 browser smoke cases** pass. The four fixtures run
  on Chromium, Firefox and WebKit. Existing bundle-size/Babel/color warnings remain.

## Performance method and results

The implementation and all relevant source/test/fixture hashes were frozen before
timing. The existing production worker and scaling runner are unchanged. Matrix:
5k/20k prefixes of the same resident 50k/load-0.95 input, production/direct,
CPython/PyPy, independent/feedback and none/KPI/full; cap 12, SPT, ten unit-capacity
servers. Cases are serial fresh processes in the same shuffled order (seed
20261107), CPython one/PyPy seven warm-ups, five measured repetitions and a separate
cold/RSS process per case. Eight extra 20k aligned cold probes validate recorded
parity. No new profiles are mixed into the timings.

Construction, feeding, run, extraction, writer join/close and GC are timed;
imports, DSO loading, input parsing and post-run validation are excluded. Both
prefix sizes retain the common 50k input; RSS includes that input, results,
interpreter/JIT and pending writer data. Cold peaks are captured before validation.
The preceding `31bf33e` raw evidence is verified against committed Git blobs before
any historical native comparison; contemporary production controls remain primary.

The matrix completes **48 cases / 240 measured repetitions / 48 cold probes /
8 larger aligned parity probes**. All 48 trajectories, generated inputs, feedback
decisions, KPI values, ordered WIP series, recorded signatures and normalized wire
match the preceding continuous-time study exactly. Source and trace byte sizes
are unchanged. Actual files have new provenance headers, so this is normalized
recorded-wire equality rather than a claim of byte-identical whole files.

For these tie-free benchmark inputs, internal native calendar events increase
from `N + 4O` to `2N + 5O` (N jobs, O operations). At 20k this is
460,240 → 590,300 independent and 347,884 → 449,855 feedback: **28.3%/29.3% more
calendar work**. Source journal records remain `4N + 5O`, with no extra Python
callbacks into the C loop. Counters reflect the corrected semantics and are
checked independently of recorded output volume.

Full lifecycle medians at 20k jobs, seconds. Production uses the named interpreter:

| Producer | Interpreter | Previous native | Corrected native | Current production | Native/production throughput |
|---|---|---:|---:|---:|---:|
| Independent |CPython|7.937|7.913|12.275|1.551x|
| Independent |PyPy|7.066|7.147|7.097|0.993x|
| Feedback |CPython|4.820|4.912|6.915|1.408x|
| Feedback |PyPy|4.668|4.945|4.440|0.898x|

Same-CPython full throughput gains remain **1.41–1.55x**. The fastest native
interpreter is now PyPy for independent full recording and CPython for feedback.
Against the strongest production control (PyPy), those paths take **0.7% and
10.6% longer** respectively. The independent ranges overlap: native PyPy
7.133–7.180 seconds versus production PyPy 7.061–7.166. Feedback native CPython
ranges 4.876–4.953 versus production PyPy 4.423–4.440. Five repetitions in a single
fresh case process characterize these runs, not independent matrix replicates or
cross-machine uncertainty.

Best measured choices at 20k, seconds; production PyPy is fastest throughout:

| Producer | Output | Best native | Native interpreter | Production PyPy | Throughput ratio |
|---|---|---:|---|---:|---:|
| Independent |None|0.03127|CPython|1.10274|35.268x|
| Feedback |None|0.08358|CPython|0.53125|6.356x|
| Independent |KPI|3.86766|CPython|4.28872|1.109x|
| Feedback |KPI|3.20073|CPython|3.11975|0.975x|
| Independent |Full|7.14673|PyPy|7.09679|0.993x|
| Feedback |Full|4.91154|CPython|4.43963|0.904x|

The no-recording opportunity remains substantial. Independent KPI recording has
10.9% higher throughput than production PyPy; feedback KPI takes 2.6% longer.
The correction restores the demonstrated scheduling behavior without establishing
a consistent lead across recorded workloads and interpreters.

Relative to the preceding native 20k medians, CPython none changes +6.82%/+0.06%,
KPI −0.16%/−0.71%, full −0.31%/+1.91% (independent/feedback). PyPy none changes
+0.81%/+0.33%, KPI +4.56%/+2.86%, full +1.15%/+5.93%. The unchanged production
controls also move between −4.52% and +4.74% across cases. These historical pairs
do not isolate an exact causal wall-time penalty: the known added work is the
calendar-event increase, and no new profile attributes the timing changes.
Contemporary controls support the comparisons above. Largest in-process sample
spread is 6.40%, in PyPy feedback/5k/no-recording native.

20k full cold-process peak RSS, MiB:

| Producer | Interpreter | Native | Production |
|---|---|---:|---:|
| Independent |CPython|336|331|
| Independent |PyPy|349|387|
| Feedback |CPython|133|214|
| Feedback |PyPy|312|310|

There is no consistent full-memory advantage. KPI native/production RSS is
116/136 and 121/140 MiB on CPython, 267/282 and 300/250 on PyPy
(independent/feedback). These whole-process cold peaks include the shared resident
input and interpreter/JIT; they do not isolate the C event heap or emitter.
Full cold times native/production are CPython 7.956/12.180 seconds independent,
4.919/6.864 feedback; PyPy 8.045/8.660 and 5.928/5.856. Each is one probe excluding
startup/imports/parsing, not a cold-start distribution.

The 20k native full files retain 910,420/713,797 domain events, 80,002 simulation
observer events, 130/102 chunks and CPython sizes 63,274,087/29,816,250 bytes
(PyPy two bytes smaller). Journals remain 20,169,608/15,675,368 bytes. These
continuous-time output invariants confirm the benchmark's work was preserved.

## Artifacts and reproduction

- [C engine](../../prototypes/native_engine_dynamic/csrc/dynamic_engine.c)
- [Compact phase model](../../prototypes/native_engine_dynamic/reference.py)
- [Expanded sweep and audit](../../prototypes/native_engine_dynamic/simultaneous.py)
- [Production-oracle tests](../../prototypes/native_engine_dynamic/tests/test_simultaneous.py)
- [Targeted CPython evidence](../../prototypes/native_engine_dynamic/semantics-results/targeted-cpython.json)
- [Targeted PyPy evidence](../../prototypes/native_engine_dynamic/semantics-results/targeted-pypy.json)
- [CPython sweep verification](../../prototypes/native_engine_dynamic/semantics-results/sweep-cpython/verification.json)
- [PyPy sweep verification](../../prototypes/native_engine_dynamic/semantics-results/sweep-pypy/verification.json)
- [New trace fixture](../../prototypes/native_engine_dynamic/fixtures/semantics.simtrace)
- [Frozen timing metadata](../../prototypes/native_engine_dynamic/semantics-results/scaling/metadata.json)
- [Raw timings](../../prototypes/native_engine_dynamic/semantics-results/scaling/matrix.jsonl)
- [Timing summary](../../prototypes/native_engine_dynamic/semantics-results/scaling/summary.csv)
- [Aligned parity probes](../../prototypes/native_engine_dynamic/semantics-results/scaling/parity.jsonl)
- [Historical native/control comparison](../../prototypes/native_engine_dynamic/semantics-results/comparison.json)
- [Complete experiment audit](../../prototypes/native_engine_dynamic/semantics-results/verification.json)

Frozen source SHA256 (225 files):
`bf56492e7f2b882129db4035406688327080226fc818f4e40733550c514f0160`.
Engine DSO SHA256:
`a40114e6840bb687e3694b8fa73abf08c2a505663d66679d4d86b7b87496d1cc`.
The static engine and both normal compiled-boundary binaries retain their prior
hashes. The input retains SHA256
`2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.
The audit verifies **231 historical source/test/evidence files** against `31bf33e`
Git blobs, requires all 48 historical outputs to agree and confirms that only the
dynamic engine binary changed. The five existing corrupt timing-evidence checks
pass; the new targeted certificate rejects corrupted model output. Matrix SHA256:
`bb42d2033dd7234b05ade5c5cfdd63d2ed069f51e34dcae0ad3adbc00c0a35ea`.

From this research worktree, using a fresh destination for each run:

```bash
.venv/bin/python -m prototypes.native_engine_dynamic.semantics_gate --output /tmp/phase-targeted-cpython.json
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.semantics_gate --output /tmp/phase-targeted-pypy.json
.venv/bin/python -m prototypes.native_engine_dynamic.simultaneous --output /tmp/phase-sweep-cpython
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.simultaneous --output /tmp/phase-sweep-pypy
.venv/bin/python -m prototypes.native_engine_dynamic.scaling_benchmark matrix --output /tmp/phase-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.simultaneous --audit --output prototypes/native_engine_dynamic/semantics-results
```

The audit requires the retained targeted/sweep/scaling directory layout shown in
the committed evidence. Reproduction of the measured source guards requires this
experiment's checkout and the recorded input/binaries. Historical evidence stays
under `gate-results`; it is not regenerated with the corrected engine.

## Remaining work

Define the intended live observation interface before public integration. The
existing collector probes still fail on missing actor properties; a payload-only
observer remains invariant after journal drain, and callbacks remain delayed at
native `advance()` boundaries. Choosing/providing actor compatibility or a narrower
event/polling interface is a separate experiment. No serializer, binding tuning,
packaging or public backend integration is included here.

Follow-up: the [live observation contract experiment](2026-11-02-native-observation.md)
tests an opt-in read-only view with synchronous boundary delivery and unmodified
stock collectors. Its matched collector workload is different from the timings
above; use that experiment's own controls when assessing observation cost.
