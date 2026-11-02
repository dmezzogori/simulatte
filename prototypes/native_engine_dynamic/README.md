# Dynamic-job and ConWIP research prototype

This separate C17 engine tests dynamic ownership and a real release policy while keeping the measured
static C prototype and production runtime unchanged. See [CONTRACT.md](CONTRACT.md) for exact semantics
and [the study plan](../../specs/research/2026-11-01-native-hooks-dynamic-plan.md) for scope/evaluation.
The [completed report](../../specs/research/2026-11-01-native-hooks-dynamic.md) contains the audited 80-case
matrix: CPython-native dynamic lifecycle is 2.97–3.80x the faster compact CPython control and 1.79–2.24x
the faster compact PyPy control on this slice. Owned-buffer hooks help CPython modestly but regress PyPy.

It begins empty, copies jobs as Python registers them while earlier jobs execute, grows storage
geometrically, and exposes stable stream-local handles. `advance(t)` processes events strictly before t,
so t's new arrivals can be registered before its pending completions. `run()` seals input and drains.
ConWIP releases arrivals when a floor slot is free, and selects pending jobs by EDD on completion.
Completed state remains stored until close; this does not demonstrate bounded memory over an infinite
stream, slot recycling, cancellation, generator processes or preemption.

```python
from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.native import NativeStream

stream = NativeStream(1, wip_cap=1, mode="kpi")
try:
    first = stream.add(Job(0, (0,), (2,), 5))
    stream.advance(1)
    assert stream.inspect(first)["state"] == "active"
    stream.add(Job(1, (0,), (1,), 4))
    stream.run()
    assert stream.result()["released"] == [0.0, 2.0]
finally:
    stream.close()
```

`ReferenceStream` and queue-specialized `TunedStream` provide independent compact Python controls with
this API. Native and Python results/recordings must match exactly; KPI totals use tight float tolerance.
`none` still captures trajectories and enforces the cap. KPIs distinguish total system WIP (arrival to
finish) from floor WIP (release to finish), and include pool wait.

Recording is explicitly `NDYN0001` with ARRIVAL/RELEASE/QUEUE/START/END/FINISH records. It is not the static
prototype's `NEVT0001` or the production Studio format. No schemas, snapshots, checksums, seeking,
provenance, compression or arbitrary observers are implemented by this stream writer.

## Build and verify

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.build
.venv/bin/python -m pytest -o addopts='' --import-mode=importlib \
  prototypes/native_engine/tests prototypes/native_engine_dynamic/tests -q
prototypes/native_engine/.build/pypy-env/bin/python -m pytest -o addopts='' --import-mode=importlib \
  prototypes/native_engine/tests prototypes/native_engine_dynamic/tests -q
.venv/bin/python -m prototypes.native_engine_dynamic.build --sanitize
prototypes/native_engine_dynamic/.build/native_sanitizer prototypes/native_engine_dynamic/.build/sanitizer-trace.bin
```

Use `--import-mode=importlib` when collecting both prototype test directories, whose test basenames overlap.
Release flags are C17, O3, PIC, warnings as errors, no fast-math. macOS/Linux with a C17 compiler are the
local build target; this is not packaging/platform certification. Generated libraries and run files are
ignored. ASan/UBSan includes checked C array lengths, randomized growth and allocation-failure injection.

## Equivalent-work measurements

Generate the shared static inputs with pinned CPython 3.14.3, as documented in the static README. Record
exact interpreter/library/input/source versions. Run serially after all builds/tests stop, with a new
output path after any code or binary change:

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.benchmark matrix \
  --study all --output prototypes/native_engine_dynamic/results/local-matrix.jsonl \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
```

The study has 32 hook cases and 48 dynamic cases, five timed samples per case after one CPython or seven
PyPy warm-ups, and separate fresh-process cold/RSS probes. Every repetition checks fingerprints; first
iterations and cold probes check independent invariants/KPIs and replay records outside timing. Workers
and the parent enforce frozen source, binary and input hashes throughout the run.

Common model validation/input parsing and imports are outside timing. Dynamic registration and each
advance, native copies and handle creation, execution, detached lists, close and explicit GC are inside.
Peak RSS includes interpreter, input/model, temporaries and output; it is not engine-only memory. Models
are streamed one job at a time in timing; no model data is registered with C before the measured lifecycle.

## Registration experiment

The [follow-up plan](../../specs/research/2026-11-01-native-registration-plan.md) compares advancement
granularity and opt-in native `add_many(tuple_of_jobs)` at identical producer windows. The original
scalar API and independent Python controls remain available. Bulk registration still creates every
arrival and stable handle, and copies all data. Future jobs must already be known to the producer;
this is not a general substitution for Python feedback at each arrival.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.registration_benchmark matrix \
  --output prototypes/native_engine_dynamic/registration-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python specs/research/native-engine-data/registration_analysis.py \
  prototypes/native_engine_dynamic/registration-results
```

Use a new output directory per source/build snapshot. The 64-case matrix has interleaved windows of
1/32/256 jobs and an eager diagnostic separating feed from execution, fixed at 50k jobs, load 0.95,
SPT, cap 12, none/record, both interpreters. All lifecycle costs remain timed. Larger windows change
producer visibility, and eager feed changes calendar population; compare matched schedules and avoid
treating interleaved feed as isolated FFI cost. Historical results require their recorded source/build
snapshot for provenance audits; they are not overwritten by this experiment.

The [completed registration report](../../specs/research/2026-11-01-native-registration.md) audits all
64 cases. Bulk windows improve native lifecycle by 1.94–2.06x on CPython and 3.95–3.98x on PyPy over
per-job advancement/admission. Against the strongest compact PyPy control across all schedules, the
selected native paths are 3.71–4.54x faster. Producer lookahead and the 15.28% spread in the fastest PyPy
recording case qualify that result; scalar remains the default.

## Compiled scalar and feedback experiment

The [plan](../../specs/research/2026-11-01-native-compiled-plan.md) and
[report](../../specs/research/2026-11-01-native-compiled.md) compare a minimal Python C API boundary with
ctypes and compact controls under the original per-job schedule. Both extensions link the exact existing
dynamic engine DSO; no kernel or original binding changes. Use `CompiledStream` from `compiled.py` for
the scalar API; batching remains an independent opt-in path in `NativeStream`.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.compiled_build
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.compiled_build
.venv/bin/python -m prototypes.native_engine_dynamic.tests.run_scalar_sanitizer
.venv/bin/python -m prototypes.native_engine_dynamic.scalar_benchmark matrix \
  --output prototypes/native_engine_dynamic/compiled-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python specs/research/native-engine-data/scalar_analysis.py \
  prototypes/native_engine_dynamic/compiled-results
```

Build/imports/DSO loading are outside timing for both native boundaries, including cold probes; engine
construction, copies, handles, all processing, detached results and close/GC remain inside. The feedback
producer also includes every inspect, decision, generated Job and decision-log materialization. It keeps
generated inputs/logs for validation after timing; all backends pay that cost. It chooses a new job only
after inspecting a prior handle, and replay independently reconstructs those strict-before-time states.
Independent/feedback producers are different workloads; compare within a producer only.

The 64-case matrix fixes 5k/50k templates, cap 12, SPT, none/record, both interpreters and four scalar
backends. Five samples after one CPython/seven PyPy warmups, separate cold/RSS probes and frozen
source/engine/two-extension/input hashes. PyPy C API measurements exercise cpyext, not CFFI. No production
packaging or free-threaded/subinterpreter support is implied. The sanitizer runner is a separate CPython
build with ASan/UBSan, malloc-backed Python allocation and three scratch/state fault positions; leak
detection is disabled. It does not replace the standalone engine sanitizer or certify PyPy internals.

The completed 64-case study finds CPython compiled scalar lifecycle 2.69–2.91x faster than ctypes for 50k
independent jobs and 1.64–1.68x with live feedback/Job creation. Margins over best compact PyPy narrow from
4.90–5.15x to 1.56–1.83x. This C API candidate on PyPy improves ctypes but loses to compact PyPy at 50k
feedback and uses materially more whole-process memory. Keep it opt-in; see the report for uncertainty
and remaining production trace/extension gates.

## Production Studio trace gate

The [plan](../../specs/research/2026-11-01-native-studio-trace-plan.md) and
[report](../../specs/research/2026-11-01-native-studio-trace.md) evaluate real production trace encoding.
Source mode studio writes NTRC0001, an intermediate journal, and TraceStream projects it through the
actual TraceRecorder into a .simtrace with typed production entities/events, snapshots, digest, CRCs,
compression, KPIs and provenance. Grants/departures are captured at real execution points. Full projection
accepts scalar registration at arrival only; it never resimulates a SimPy model or changes native state.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.trace_fixture
cd studio
pnpm --filter @simulatte/playback exec vitest run test/native-prototype.test.ts
pnpm build
pnpm exec playwright test browser/native-prototype.spec.ts
cd ..
.venv/bin/python -m prototypes.native_engine_dynamic.trace_benchmark matrix \
  --output prototypes/native_engine_dynamic/trace-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python specs/research/native-engine-data/trace_analysis.py \
  prototypes/native_engine_dynamic/trace-results
```

Regenerate the retained fixture only deliberately, before freezing sources: its bytes participate in
study provenance. Python supplies full container/snapshot/digest checks; these APIs are not ported to
TypeScript. The fixture verifies history, forward/backward seek, exact KPIs and scene placement in the
TypeScript stack, and opens/steps/seeks/plays/renders in the production browser viewer.

72 cases: 500/5000 historical prefixes, independent/feedback, none/rich raw/full trace,
compiled/reference/tuned, CPython/PyPy. All bridge/intermediate I/O and writer shutdown count. Default
count/byte/pending limits, latency disabled for deterministic chunks. Every full repetition checks CRCs
and normalized deterministic wire content; first/cold iterations fully check reconstruction/digest and
expected events/states/KPIs. Compare semantic signatures across interpreters, not volatile file bytes.
This bridge is not a direct native serializer ceiling. Live delivery, general integration and 50k full
trace scaling remain unproven. Prior measured binaries are archived in ignored .build/pre-studio-0f20924.

The completed gate passes real file compatibility and replay/browser checks, but not a useful native
margin through this bridge. At 5k, full native CPython takes 2.13/1.58 s independent/feedback versus
compact PyPy 1.83/1.48 s (0.85x/0.94x throughput). Relative to compact CPython it gains only 1.02x/1.03x.
The kernel still helps untraced/rich-raw work; keep it experimental and address trace architecture or
product needs before broader integration. No further kernel/binding/batch tuning here.

## Direct production baseline and profiling

The [production comparison](../../specs/research/2026-11-01-native-production.md)
adds actual production actors, resources, ConWIP and direct TraceRecorder output.
The previous full-trace study compared compact/native engines through the same
bridge; it did not measure this direct production baseline. ProductionStream uses
two public operation hooks only to export the native API's trajectory arrays.
Exact generation, feedback, releases, operation timings and dispatch are checked;
event digests differ because the production lifecycle records decisions/retirement.

TraceStream now accepts level="kpi" as well as the default "full". Both use the
same journal/projection; the KPI recorder still computes the semantic digest.
The new comparison records identical scalar declarations and two WIP series.
No production runtime, writer, viewer, policy or native kernel code is changed.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark matrix \
  --output prototypes/native_engine_dynamic/production-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
for producer in independent feedback; do
  for backend in production native; do
    for mode in none kpi full; do
      .venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark profile \
        --input prototypes/native_engine/.inputs/jobs-5000-load-0.95.json \
        --producer "$producer" --backend "$backend" --mode "$mode" \
        --output "prototypes/native_engine_dynamic/production-results/profiles/$producer-$mode-$backend.json"
    done
  done
done
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/production-results
```

The matrix has 24 cases at 5k jobs, five samples after CPython 1/PyPy 7 warmups and
separate cold/RSS probes. Profiling is CPython-only, outside those measurements:
separate simulation/writer thread CPU passes and an output-span/snapshot pass.
Retained function self/cumulative CPU and snapshot/compression/output spans are
diagnostics, not additive wall-time components. All runs require the recorded
source/binary/input snapshot; previous results remain historical evidence.

The completed comparison finds native CPython 23.36x/6.12x faster than production
PyPy without recording, but full traces take 1.53x/1.50x as long. The bridge adds
shadow replay and retains completed jobs: aggregate snapshot bytes are 4.63x/
74.08x production. Lifecycle alignment is the proposed next experiment, with no
production optimization or direct native serializer implemented here.

## Production lifecycle in the trace bridge

The [lifecycle experiment](../../specs/research/2026-11-01-native-lifecycle.md)
adds ConWIP release decisions when a cap is configured and retires completed
shadow jobs from the actual entity registry/projection state. Completed native
handles and results remain available; historical trace state is retained in
events and earlier snapshots. Uncapped release does not claim a ConWIP policy.
The journal/core/bindings and production runtime remain unchanged.

The generator now writes lifecycle.*; the older native.* fixture is preserved.
Both fixtures pass TypeScript seek/playback/scene parity and the browser smoke.
Use the preceding matrix/profile commands with lifecycle-results as destination,
then compare against the committed historical baseline:

```sh
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/lifecycle-results \
  --baseline prototypes/native_engine_dynamic/production-results \
  --baseline-revision 1b234fc
```

Earlier measurement audits require their recorded checkout. The new audit can
also verify the historical source/evidence bytes against that commit and report
runtime/file/RSS changes alongside drift in unchanged production controls.

The lifecycle candidate improves full native throughput 1.14–1.21x, brings snapshot
bytes/file size close to production and reduces full native CPython whole-process
peak RSS from 120/94 to 80/62 MiB. Native PyPy still takes 17–20% longer than production
PyPy for full traces. Native CPython KPI time rises 3–5%; none-mode paths/results
remain unchanged. Extra Python shadow replay remains a proposed next candidate.

## Direct typed event emitter

The [direct-emission experiment](../../specs/research/2026-11-01-native-direct.md)
adds opt-in DirectTraceStream. It uses the same native core, compiled boundary
and rich journal, but builds typed events/deltas from small counters rather than
applying every delta to a second complete entity-state map. The real writer is
the sole generic replay owner. Native handles/results and exact aligned-bridge
recording semantics remain intact.

The internal publisher's registry binds identity/kind/lifecycle only. It supplies
creation/activation state then explicitly rejects live snapshot requests; seek
the resulting Trace for historical state. It is not a live model Environment.
This limitation is part of the experimental boundary.

The generator now writes direct.*, preserving native.* and lifecycle.*. All
three fixtures are checked by the playback/scene/browser tests. The current
matrix has 36 cases with production/native/direct backends and includes 18
CPython profiling commands. Native/direct none cases use the same compiled
engine as controls. Use direct-results as the output directory, then:

```sh
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/direct-results \
  --baseline prototypes/native_engine_dynamic/lifecycle-results \
  --baseline-revision 27316bf
```

The audit requires exact aligned/direct trace signatures and normalized wire,
zero direct main-thread generic replay and continued full writer replay. Earlier
audits/results require their recorded checkout. No earlier evidence is rewritten.

The completed emitter study gains 1.087–1.105x full and 1.193–1.235x KPI throughput
over the contemporary aligned bridge, with identical recorded output. Best native
full is 0.6%/3.9% longer than production PyPy independent/feedback (near parity,
not a consistent win). Full memory has no consistent improvement. Keep this
path opt-in and validate larger/tie-heavy workloads and the observation contract
before a public native interface; no production integration was attempted here.

## Live observation contract

The [observation report](../../specs/research/2026-11-02-native-observation.md)
defines opt-in `LiveTraceStream` over the
[corrected simultaneous-event scheduler](../../specs/research/2026-11-02-native-semantics.md).
Boundary delivery flushes produced journal records after `add()` and `advance(t)`;
callbacks see read-only event-time views, and return from `advance(t)` exposes
clock `t` with scheduled events at `t` still pending. Callbacks cannot control the
model. `nd_flush()` changes transport visibility only, not calendar semantics.

The adapter supports the limited job timing, floor workload/count and server
queue/count/worked-time reads used by five unmodified stock collector classes.
General actors, mutable policies, queue iteration and live snapshots are outside
the contract. A buffered delivery variant uses the same views/collectors as a
cost control. The old `DirectTraceStream` and production package stay unchanged.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.observation_gate --output /tmp/observation-gate-cpython
prototypes/native_engine/.build/pypy-env/bin/python -m prototypes.native_engine_dynamic.observation_gate --output /tmp/observation-gate-pypy
.venv/bin/python -m prototypes.native_engine_dynamic.observation_benchmark matrix \
  --output /tmp/observation-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.observation_benchmark audit-experiment \
  --output prototypes/native_engine_dynamic/observation-results
```

Use fresh destinations for new measurements. The retained audit expects the
committed `gate-cpython`, `gate-pypy` and `scaling` layout. Stock scalar definitions
differ from earlier benchmark totals; compare matched paths within this study.

The completed gate passes 1,536 configurations per interpreter, including live
stock reads and intermediate windows. Its 36-case matched trace matrix finds
native live CPython uses 12–26% less time than production CPython, while the best
native live path takes 17–40% longer than production PyPy. Boundary-delivery cost
is much larger on PyPy. Keep it experimental; the recommended next control is
the same live stock workload without trace recording, with profiling.

The [no-recording study](../../specs/research/2026-11-02-native-observation-untraced.md)
adds `LiveTraceStream(..., level="none", trace_path=None)`. It keeps the same
stock collectors and live reads while omitting the final TraceRecorder, its
worker and semantic digest. Native rich journal I/O remains the observation
transport. Actual production uses `ProductionLiveStream(..., mode="none")`;
both modes retain identical detached result/collector export work.

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_gate --output /tmp/untraced-gate
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_benchmark matrix \
  --output /tmp/untraced-scaling --pypy prototypes/native_engine/.build/pypy-env/bin/python
.venv/bin/python -m prototypes.native_engine_dynamic.untraced_benchmark audit-experiment \
  --output prototypes/native_engine_dynamic/untraced-results
```

The matrix has contemporary none/full controls, 5k/20k jobs, both producers,
interpreters and delivery modes. Separate CPython thread-CPU profiles are
diagnostics, not timing samples. The linked report gives the exact scope and
reproduction commands.

The completed no-recording gate preserves stock observations across all 1,536
configurations per interpreter. Its 48-case cost matrix finds native live
CPython uses 15–46% less time than production CPython, but takes 68–142% longer
than production PyPy with the same collector bundle. Profiles identify about
43% excess native event publication without extra stock deliveries. An opt-in
subscriber-interest check is the next proposed experiment; it is not included.
