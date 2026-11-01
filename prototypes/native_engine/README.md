# Native-engine vertical prototype

An isolated feasibility experiment, not an installed Simulatte API. Production code and dependencies are
unchanged. See [CONTRACT.md](CONTRACT.md), [OPTIMIZATIONS.md](OPTIMIZATIONS.md), and the
[evaluation report](../../specs/research/2026-10-15-native-engine-prototype.md).

The [binding-only follow-up](../../specs/research/2026-11-01-native-engine-bindings.md) retains the same C
engine and detached-list API, with buffer-backed input conversion and bulk result extraction. Its fresh
24-case before/after study is separate from the historical 384-case evidence below: CPython native built-ins
improve 3.07x without recording and 2.85x with recording for 50k/.95/SPT, reaching 6.89x / 7.02x the fastest
compact PyPy baseline. Python hooks remain roughly at parity or slower than that baseline.

The four backends share inputs and results: actual current Simulatte with observation adapters; a small
five-phase Python reference; the reference with specialized Python queues; and independent C17 state,
calendar, resources, operation transitions, KPIs and recording, exposed with ctypes. Python authoring
and one dispatch hook are retained. This is a job-shop slice, not a general process/event replacement.

## Build and verify

From this worktree root, on macOS or Linux with a C17 compiler:

```sh
uv sync --dev --all-extras
.venv/bin/python -m prototypes.native_engine.build
.venv/bin/python -m pytest -o addopts='' prototypes/native_engine/tests -q
.venv/bin/ruff check prototypes/native_engine
.venv/bin/python -m prototypes.native_engine.build --sanitize
prototypes/native_engine/.build/native_sanitizer prototypes/native_engine/.build/sanitizer-trace.bin
```

Release flags are C17, `-O3 -fPIC`, warnings as errors, and shared-library linkage; no fast-math.
The sanitizer build uses ASan/UBSan with fatal recovery and injected allocation failures. Its successful
run does not prove leak freedom; a dedicated leak detector is not part of this macOS configuration.
Use `CC` to choose a compiler. Compilation is a separate setup step, not the per-model `build` phase.

## Reproduce the measurements

Generate inputs once on CPython; the exact JSON files are reused by every interpreter and backend.
Use CPython 3.14.3 to reproduce the historical input hashes (`uv sync --python 3.14.3 --dev --all-extras`);
later standard-library sampling implementations could generate different routes from the same seed:

```sh
.venv/bin/python -m prototypes.native_engine.workloads
uv venv --python pypy@3.11 prototypes/native_engine/.build/pypy-env
uv pip install --python prototypes/native_engine/.build/pypy-env/bin/python -e . pytest
prototypes/native_engine/.build/pypy-env/bin/python -m pytest -o addopts='' prototypes/native_engine/tests -q
.venv/bin/python -m prototypes.native_engine.benchmark matrix \
  --output prototypes/native_engine/results/local-matrix.jsonl \
  --backends simulatte reference tuned native \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python \
  --pypy-backends simulatte reference tuned native
.venv/bin/python -m prototypes.native_engine.analyze \
  prototypes/native_engine/results/local-matrix.jsonl \
  --csv prototypes/native_engine/results/local-summary.csv
```

The recorded PyPy executable was 3.11.15 / PyPy 7.3.22. Pin that version when reproducing historical results;
the convenience `pypy@3.11` selector may resolve a newer version. See `results/metadata.json` and each raw
case for exact interpreter, compiler, source/library/input hashes and dependencies. PyPy's minimal test
environment omits pytest-timeout, resulting in two harmless unknown-option warnings from repo config.

The full matrix has 384 cases: 5k/50k jobs, offered loads .50/.95, FIFO/SPT, none/KPI/record modes, built-in
or Python dispatch, four engines and two interpreters. Each timing case runs in a fresh subprocess with
one CPython or seven PyPy warm-ups and three samples. Every case also gets a separate cold subprocess for
peak RSS and a cold lifecycle timing. Cases run serially; do not run builds, tests or other benchmarks beside
them. An interrupted run can resume into its existing output if source hashes match. Use a new file after
source changes. The completed raw evidence is `results/final-matrix.jsonl`; CSV gives per-case medians.

Generation/parsing, imports and common input-model validation are outside timing. Native boundary
checks remain inside its constructor; the binding follow-up skips redundant element checks only for exact
validated Workload/Job objects, preserving checked duck-typed/subclass handling. Model lowering/build, run, detached
Python extraction and close are inside. Close includes explicit cyclic garbage collection. Recording flushes
at successful run completion; close releases the file. Writes are buffered OS writes, without fsync or
durability guarantees. All modes collect trajectories, including `none`. Memory is absolute process peak
RSS, including interpreter/imports, input parsing, model and output, measured before validation; it is not
an allocation delta or solely engine storage. macOS reports bytes; the harness converts Linux KiB to bytes.

Exact trajectory and dispatch fingerprints are checked on every repetition. The first iteration in each
worker and each independent memory probe checks analytical invariants/KPIs and replays the recording;
recording hashes are checked every repetition and across all equivalent cases. Fingerprints use this host's
native-endian array encoding; compare regenerated hashes across backends on the same host.

## Minimal use

```python
from prototypes.native_engine.model import Job, Workload, spt_hook
from prototypes.native_engine.native import NativeRunner

workload = Workload(10, (Job(0, (0, 4), (2, 1), 8),))
runner = NativeRunner(workload, policy="spt", mode="kpi")
try:
    runner.run()
    result = runner.result()
finally:
    runner.close()
```

Pass `hook=spt_hook` to run the equivalent Python selection once per grant; omit it for fully native
dispatch. Hooks receive immutable `(operation_id, duration)` candidates in enqueue order. IDs are local
to this workload. Exceptions fail the run and propagate; close remains safe. Results survive close as
detached Python data. General generator processes, cancellation, interrupts and stable handles are deferred.

`record` requires `trace_path`. Its `NEVT0001` stream contains only ARRIVAL/QUEUE/START/END/FINISH records.
It does **not** contain Studio's entity schemas, deltas, checkpoints, index, checksums, semantic digest,
provenance or compressed chunks. Equivalent prototype-recording speed is not a Studio full-trace speedup.
