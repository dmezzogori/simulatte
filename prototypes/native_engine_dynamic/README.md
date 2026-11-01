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
