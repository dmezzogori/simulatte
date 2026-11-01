# Direct native event emission

2026-11-01; starting revision 27316bf, feature/native-engine-feasibility.
Davide authorized this [bounded experiment](2026-11-01-native-direct-plan.md)
after [lifecycle alignment](2026-11-01-native-lifecycle.md) fixed snapshot growth
but left the bridge's extra Python state replay.

## Result

**The counter-based emitter improves recording throughput while preserving exact
aligned-bridge output.** Full-trace gains over the contemporary aligned bridge
are 1.087–1.105x; KPI gains are 1.193–1.235x. Keep it opt-in: the best native full
path is effectively tied with production PyPy for independent jobs and takes
3.9% longer with feedback. There is no consistent cross-workload native win.

Follow-up: the [scaling and semantics gate](2026-11-01-native-gate.md) finds
equal-time job-outcome differences against production and unsupported standard
live collectors in both publishers. Exact aligned/direct parity remains valid;
production equivalence here covers the tested continuous-time workload family.

Full lifecycle medians, milliseconds; production uses the row's interpreter:

| Producer | Interpreter | Aligned bridge | Direct emitter | Throughput gain | Production |
|---|---|---:|---:|---:|---:|
| Independent | CPython |1,763.16|1,606.12|1.098x|2,204.00|
| Feedback | CPython |1,317.87|1,192.59|1.105x|1,691.12|
| Independent | PyPy |1,577.05|1,450.79|1.087x|1,442.14|
| Feedback | PyPy |1,314.73|1,200.47|1.095x|1,147.34|

Direct CPython gains 1.37x/1.42x over actual production CPython for full recording,
but production PyPy is the stronger warmed control. The fastest native option
is PyPy for independent jobs and CPython for feedback. Its independent time is
only 0.6% above production PyPy, with overlapping sample ranges; feedback is 3.9%
above. Compare within producers because they generate different operation counts.

KPI recorder medians, milliseconds:

| Producer | Interpreter | Aligned bridge | Direct emitter | Throughput gain | Production |
|---|---|---:|---:|---:|---:|
| Independent | CPython |1,186.52|994.82|1.193x|1,612.55|
| Feedback | CPython |942.76|777.95|1.212x|1,248.53|
| Independent | PyPy |1,125.25|910.83|1.235x|970.89|
| Feedback | PyPy |980.76|811.07|1.209x|762.60|

KPI mode benefits more because it has no repeated full snapshot/compression work
to overlap with the emitter. The best native independent KPI path is 1.066x
production PyPy throughput, while the best feedback path takes 2.0% longer. This
does not establish a universal recording advantage.

No-recording native/direct cases are deliberately the same CompiledStream path.
Direct-labelled CPython medians are 6.97/19.81 ms, aligned-labelled 7.21/20.66 ms;
the difference is sampling/control drift. Production PyPy is 180.43/143.43 ms.
The no-observer opportunity remains strong, but it is not a new emitter gain.

## Historical drift, ranges, cold probes and memory

Contemporary controls are the primary comparison. Historical comparisons use
the exact 27316bf source/evidence snapshot, but the previous and current matrices
have different randomized orders and process histories. Unchanged production
controls and small/JIT-sensitive cases move between studies; do not attribute
every historical delta to the new emitter.

Direct full CPython ranges are 1,597.46–1,626.44/1,188.53–1,200.52 ms independent/
feedback; PyPy ranges 1,393.96–1,470.68/1,184.69–1,207.46 ms. The broad independent
PyPy range reinforces the parity conclusion against production. Largest spread
across any case is 7.84% (PyPy feedback no-recording aligned native). Five samples
and single cold probes do not establish confidence intervals or distributions.

Cold direct full lifecycle is 1,619.59/1,190.24 ms on CPython and 2,129.85/2,117.73 ms
on PyPy, versus aligned 1,748.81/1,324.45 and 2,366.77/2,187.51. Production cold is
2,213.98/1,697.42 on CPython and 2,673.51/2,416.33 on PyPy. Startup/imports/parsing
are excluded here too. CPython remains the faster one-shot direct path in these
single fresh-process probes.

Full cold peak RSS, MiB (independent/feedback): direct CPython 73/64 versus aligned
74/62 and production 88/76; direct PyPy 169/155 versus aligned 158/149 and production
149/146. There is no consistent full-RSS improvement. KPI direct peaks are 48/50
MiB CPython and 136/125 MiB PyPy versus aligned 50/51 and 154/148. Peaks include the
harness, both loaded implementations, templates/results and pending writer work;
they do not isolate emitter allocations, and single-probe variability matters.

Aligned/direct files have identical sizes as well as normalized wire: full
10,367,812/7,464,392 bytes and 32/26 chunks on CPython, two bytes smaller on PyPy.
Both contain 226,541/179,284 domain events and 20,002 simulation observers. Source
journals remain 5,018,088/3,937,928 bytes. The emitter saves construction/replay
work, not trace content or compression quality.

## Profiles confirm the mechanism

Simulation-thread generic apply_deltas calls fall from 216,541/169,284 to zero,
independent/feedback. Full writer calls remain 226,541/179,284 in both paths.
Minimal event-building counters and phase guards remain on the direct path;
this removes full generic replay, not all Python bookkeeping.

Instrumented CPython simulation self CPU falls 8.166→6.799 s independent and
6.819→5.784 s feedback. Writer self CPU is essentially unchanged:2.258→2.264 s and
1.263→1.250 s. These heavily perturbed profile seconds are diagnostics, not
estimates of unprofiled time saved. Profiles still show shared digest handling,
trace append, canonical preparation, event construction/dispatch and emission
among the hot work. The C simulation core is not the dominant full-trace cost.

Separate unprofiled output spans agree with the identical-output contract:

| Producer/path | Aggregate snapshot bytes | Snapshot-pack CPU(ms) | Compression CPU(ms) |
|---|---:|---:|---:|
| Independent aligned |5,584,436|63.58|429.91|
| Independent direct |5,584,436|60.29|427.32|
| Feedback aligned |211,727|2.77|285.68|
| Feedback direct |211,727|2.75|287.34|

The writer performs the same work while the simulation side constructs events
more cheaply. CPU compression spans cover whole event+snapshot chunks. Wall
spans include waits and overlap with main-thread work; do not add them to the
profiled CPU or infer an isolated storage bottleneck.

Unchanged production full controls are 0.9–6.5% slower than the previous study;
KPI/no-recording drift can be larger. This is why contemporary aligned/direct
comparisons are primary. There is no isolated function-only ablation: the
candidate also changes binding storage and queued-time/counter layouts. Exact
recording parity establishes equal output, not a general live-model API match.

## Decision and recommended next step

Retain the direct emitter as an opt-in research path. It improves the same-runtime
CPython comparison and brings full/KPI recording close to production PyPy, while
preserving all aligned recording content. It does not establish an across-model
performance advantage or justify replacing production's live entity interface.

Next validate scaling and semantics before more implementation: larger workloads,
tie-heavy/event-boundary inputs, both producers and the required observation
contract, keeping contemporary production controls. Check whether the modest
near-parity survives increasing pool/snapshot sizes and whether consumers need
live entity snapshots. A public native interface would need an explicit supported
contract and the remaining extension/packaging/semantic gates. Further codec or
serializer work is a separate decision based on that evidence.

No scaling/public integration experiment or new serializer was implemented in
this milestone. These next steps are recommendations, not adopted commitments.

## Retained evidence and final audit

36 pre-measurement gate outputs match the final matrix's result/generation/
feedback/counter/trace signatures. 36 cases/180 timed samples/36 cold probes pass
exact coverage/order/provenance, output invariance, backend digest equivalence,
aligned/direct recorded parity and 11 corrupt-evidence rejection checks, including
wire-hash drift. Historical verification checks 233 source/evidence files against
27316bf. 18 diagnostic files cover 18 simulation and 12 writer threads, plus 12
separate output-span passes. Replay absence/presence is an audit condition.

Previous measurement data and fixtures remain intact; the lifecycle report gains
only a follow-up link. Native C/bindings/production runtime/dependencies/CI remain
unchanged. Source SHA-256:
794b2c7d8c2f2549b6a49fb3e06a4d5aff999a8619321e2e6129a6f9d7908cfc.
Metadata retains all input/source/library hashes, interpreter/software/platform
details and shuffled order. Environment: bigboss, CPython 3.14.3/PyPy 3.11.15(7.3.22),
Simulatte 0.17.0, SimPy 4.1.2, msgpack 1.2.3.

Evidence: [matrix](../../prototypes/native_engine_dynamic/direct-results/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/direct-results/summary.csv),
[metadata](../../prototypes/native_engine_dynamic/direct-results/metadata.json),
[preflight](../../prototypes/native_engine_dynamic/direct-results/preflight.json),
[comparison](../../prototypes/native_engine_dynamic/direct-results/comparison.json),
[profiles](../../prototypes/native_engine_dynamic/direct-results/profile-summary.json),
[audit](../../prototypes/native_engine_dynamic/direct-results/verification.json).

## Candidate and contract

The existing rich source rows and immutable Job inputs contain enough data to
construct the same typed events/deltas. DirectProjection keeps only event-building
counters/arrays: system/floor and queue sizes, occupancy, server work/WIP totals,
queued timestamps, per-job waits and stage/phase validation. It emits through the
actual Environment/EventBus, semantic digest and TraceRecorder. Generic delta
replay and complete historical entity state are owned by the writer alone.

The aligned bridge remains available as a contemporary control. C engine,
compiled boundary, source journal/transport, production writer/runtime and
dependencies are unchanged. This isolates one emitter/bookkeeping candidate;
it does not test a memory transport, direct C serializer or kernel change.

Registry bindings carry identity, kind and lifecycle, with creation/activation
snapshots only. Their creation fields are discarded afterwards. Live snapshot
requests on this internal publisher raise an explicit RuntimeError, directing
the caller to Trace.state_at; returning a stale model view would be incorrect.
This is an observation adapter, not a drop-in live model Environment. Native
Stream.inspect/result and completed handles remain unchanged. Arbitrary live
Python entity inspection/collectors or general SimPy semantics are not certified.

ConWIP decisions, retirement, event order, initial state, KPI declarations/series,
all payload/delta values and digest match the aligned bridge exactly. Both share
the native model; production SimPy still has its own micro-event ordering and
initial WIP representation. Do not require a production/native digest match.

## Protocol

36 cases: production/aligned-native/direct x CPython/PyPy x independent/feedback
x none/KPI/full, 5,000 jobs, SPT/cap 12. Both native none cases deliberately use
the same compiled engine as controls; neither constructs a trace emitter.
Five samples after CPython 1/PyPy 7 warmups, separate cold/RSS probes, serial order
shuffled with seed 20261106. All 36 pre-measurement gate cases pass first.

Construction, generation/feedback/logs, copied inputs and outputs, all journal/
trace I/O, projection/emission, digest, compression, complete writer shutdown and
GC are timed. Imports/DSO loading/parsing and post-run correctness checks are
excluded, including cold probes. Defaults:10k events/1 MiB/64 MiB pending, infinite
latency for deterministic chunks, no fsync. KPI level still emits domain events
and computes the semantic digest; it is not a lightweight scalar-counter run.

Every repetition checks complete CRCs, normalized deterministic wire and stored
digest/KPI/series/results. First/cold cases reconstruct/verify full traces and
validate source records. Aligned/direct signatures and normalized wire must
match exactly within each interpreter/producer/mode. Production shares exact
trajectories/generation/feedback, KPI tolerance and ordered WIP(time,value) series.

18 separate CPython diagnostic commands cover simulation/writer CPU and 12 output
span passes. The standard Python profiler uses each thread's CPU clock and
immediate same-thread finalization in separate thread passes. Its seconds and
percentages are heavily perturbed; use call counts and unprofiled snapshot/
compression/output spans to support the mechanism, not as additive wall savings.
The audit requires zero direct simulation-thread apply_deltas calls and retained
full writer replay. No PyPy profile is claimed.

The historical baseline's recorded source/evidence bytes are checked against
27316bf Git blobs. Writer/input/software/binary settings and engine results/
counters/journal sizes must remain invariant. New direct rows map to historical
aligned-native cases. Case orders differ because of the added contemporary
control; report historical drift separately from within-matrix comparisons.

## Verification before measurement

318 prototype tests pass CPython (one sanitizer-only skip),317 pass PyPy
(sanitizer/CPython profile skips, two known pytest-timeout configuration warnings).
156 production trace/ConWIP/invariance tests pass. New 32-way parity checks cover
both debug modes, FIFO/SPT, cap/no-cap, independent/feedback and KPI/full; extra
checks verify no full live map, explicit snapshot rejection and zero generic
replay on the simulation thread. Existing lifecycle ownership/history/weakref and
empty/cancel/failure checks now exercise both bridge types.

TypeScript 301 trace/27 playback/16 scene tests, three typechecks and production
build pass. The new direct fixture has the exact lifecycle fixture's signature:
252 domain events,50 observers,15 chunks and digest
11bce447f79e37c176f9e4c2b4a8430fa458f0cf780fdf631bcf32e4ce29bb96.
Its raw source is byte-identical too. Old fixtures remain intact.

Browser smoke initially exposed an async test race on the unchanged lifecycle
fixture in Firefox, reproduced once in five repeat cases. The viewer's step()
awaits event lookup/seek, while the button handler does not await it; the test
read its cursor after a rapid click loop without waiting for rendering. The
smoke now waits for an observed cursor change after each step. No production
viewer source changed. All 9 three-fixture/browser cases and 5 targeted Firefox
repeat cases then pass. Ruff passes; historical source/evidence audit verifies
233 committed files before timing. Old native sanitizer evidence applies to
unchanged native/binding sources and binaries; this step changes Python only.

## Reproduction

```sh
# Deliberately generate direct.* before freezing sources; older fixtures remain.
.venv/bin/python -m prototypes.native_engine_dynamic.trace_fixture
.venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark matrix \
  --output prototypes/native_engine_dynamic/direct-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
for producer in independent feedback; do
  for backend in production native direct; do
    for mode in none kpi full; do
      .venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark profile \
        --input prototypes/native_engine/.inputs/jobs-5000-load-0.95.json \
        --producer "$producer" --backend "$backend" --mode "$mode" \
        --output "prototypes/native_engine_dynamic/direct-results/profiles/$producer-$mode-$backend.json"
    done
  done
done
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/direct-results \
  --baseline prototypes/native_engine_dynamic/lifecycle-results --baseline-revision 27316bf
```

Prior audits require their recorded checkout. The new audit supports historical
baseline verification without rewriting those earlier sources/results. Large
performance traces are validated then removed; samples/hashes/counts/sizes,
diagnostic profiles and the small compatibility fixture are retained.
