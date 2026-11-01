# Native hooks, dynamic jobs and ConWIP

Date: 2026-11-01. Starting revision: `281baa7`, research branch `feature/native-engine-feasibility`.
Davide authorized the [bounded plan](2026-11-01-native-hooks-dynamic-plan.md) after the
[binding-only study](2026-11-01-native-engine-bindings.md). This delivers the hook experiment and the
dynamic-job/ConWIP milestone. Production Studio encoding remains the subsequent separate gate.

## Outcome

Dynamic native ownership and ConWIP work within the [tested contract](../../prototypes/native_engine_dynamic/CONTRACT.md).
Python can create/register jobs while earlier jobs execute, retain stable handles through native storage
growth, inspect state at boundaries, and add work after the calendar temporarily drains. Cap/EDD release,
trajectories, KPIs and lightweight records match independent reference and queue-specialized Python engines.
The hand-derived release fixture also matches the actual current Simulatte ConWIP policy.

Including streamed registration, advancement, processing, extraction and cleanup, **CPython native gives
2.97–3.80x lifecycle throughput versus the faster compact CPython control, and 1.79–2.24x versus the faster
compact PyPy control** across this study. The dynamic workload does not retain the roughly 7x margin of the
earlier eager/static slice. Registration granularity and the extension boundary remain architectural costs
to investigate before a complete engine commitment.

The owned-buffer hook alternative preserves callback behavior and lifetime safety but is not a universal
improvement: **1.06–1.07x** native throughput on CPython, **0.77–0.78x** on PyPy versus native tuple hooks.
Keep tuple hooks as the default. The experiment does not demonstrate that buffer representation alone
solves per-grant Python policy cost, or establish the cost of a compiled Python extension.

## Implemented boundary

The existing static C source, ABI and release binary are unchanged. Its Python binding adds opt-in
`hook_style='buffers'`: `(now, server, ids, durations)` receives read-only numeric memoryviews backed by
owned immutable bytes. Retention is safe after callbacks and close. Default tuple hooks retain their
signature, candidate order, exception behavior and one call on every grant, including singletons.
No decisions are silently batched or skipped. The compact Python buffer control performs equivalent
snapshot construction. Static built-ins still make zero policy callbacks.

The dynamic implementation is a separate C17 library under `prototypes/native_engine_dynamic/`:

- Empty construction, copied incremental Job registration, geometric native job/operation/calendar
  growth, stable monotonic IDs and Python owner/id handles; native pointers never escape.
- `advance(t)` processes events strictly before t; arrivals at t precede other t events. Eager and
  incremental feeding preserve results and bytes. `run()` seals and drains; inspections/results are
  detached and close is idempotent. Makespan remains maximum completion time when the clock advances
  beyond the final completion, including an empty stream.
- ConWIP reserves floor slots synchronously. Incoming jobs release when capacity is available;
  completion releases pending jobs by EDD, breaking ties by registration order. Cap accounting also
  applies in none mode. System WIP covers arrival-to-finish; floor WIP covers release-to-finish.
- Independent ReferenceStream and deque/heap-specialized TunedStream retain exact event/release
  semantics, allowing the stronger baseline to be selected per case rather than by interpreter alone.
- Explicit `NDYN0001` recording adds RELEASE to the static record vocabulary. It is not the existing
  static `NEVT0001` format and does not implement Studio schemas, snapshots, seek indexes, checksums,
  semantic digest, provenance, compression or arbitrary observers.

This is boundary-driven job creation, not a general generator/process API or job insertion from inside
a running native callback. Completed job state is retained until close; no slot recycling, cancellation,
preemption or infinite-stream memory bound is demonstrated. Dynamic dispatch is FIFO/SPT native built-ins;
the hook experiment stays separate from the initial dynamic slice.

## Method and provenance

Local bigboss: Apple M4 Pro, 12 cores, 48 GiB RAM, macOS 27.2, Apple clang 21.0.0. CPython 3.14.3 and
PyPy 3.11.15 / 7.3.22. C17 O3/PIC, warnings as errors, no fast-math; neither library links Python.
Exact dependencies, compiler flags, source/input/library hashes and commands are in the metadata.
The input JSON bytes match the earlier study; no RNG runs inside a timed engine.

One frozen serial matrix contains **80 fresh timing cases, 400 measured samples and 80 separate cold/RSS
probes**. Each case uses one CPython or seven PyPy warm-ups, then five samples in its timing process.
Hook cases: 50k/.95/SPT, none/record, eight variants across both interpreters (native built-in/tuple/buffers,
reference built-in/tuple/buffers, tuned built-in/tuple), 32 cases. Dynamic cases: 5k/50k, .95/SPT,
none/record, immediate release or cap12, native/reference/tuned on both interpreters, 48 cases.

Common model validation, input parsing and imports are outside lifecycle timing. Dynamic registration,
every advance, native copies and handle creation, processing, detached lists, close and explicit cyclic
GC are inside. No jobs are registered with C before its timed lifecycle. Workers verify source, input
and both binary hashes before/after samples; the parent enforces the frozen hashes on every worker and
cold probe, after each case and at completion. All timing and cold processes check trajectory fingerprints;
first iterations and cold probes independently check invariants/KPIs and replay recorded runs outside timing.

## Hook results

| Native interpreter | Mode | Tuple ms | Owned buffers ms | Tuple / buffers | Best compact PyPy hook ms |
|---|---|---:|---:|---:|---:|
| CPython | none | 363.05 | 340.75 | 1.07x | 334.32 |
| CPython | record | 379.88 | 356.84 | 1.06x | 399.15 |
| PyPy | none | 397.85 | 518.11 | 0.77x | 334.32 |
| PyPy | record | 412.22 | 527.43 | 0.78x | 399.15 |

Best compact PyPy means the faster reference tuple/buffer or tuned tuple implementation for that mode.
CPython-native buffers are about 0.98x / 1.12x that baseline (none/record). The CPython improvement is
small; five samples on one machine and fixed case order do not justify a general new callback interface
recommendation. Its native tuple/buffer within-case spreads are 0.94% / 1.12% unrecorded and 3.81% / 1.68%
recorded. The PyPy regression is much larger than its observed roughly 0.7–1.5% spreads.

Zero-callback native controls remain about 40.74 / 48.47 ms on CPython and 42.76 / 49.37 ms on PyPy
(none/record). Calling Python on every grant still materially changes the workload's cost. These tests
preserve stateful hook semantics; they make no claim about safe batching of arbitrary policy decisions.

## Dynamic/ConWIP results

All ratios below compare **CPython-native median lifecycle time** with the faster reference/tuned control
on the indicated interpreter, with the same input, cap, mode and one-job-at-a-time feeding protocol.
The PyPy-native column shows executing the native binding on PyPy; it is not the ratio denominator.

| Jobs | Cap | Mode | CPython native ms | PyPy native ms | Vs best CPython | Vs best PyPy |
|---:|---|---|---:|---:|---:|---:|
| 5,000 | none | none | 18.24 | 37.97 | 2.97x | 1.84x |
| 5,000 | none | record | 19.42 | 38.59 | 3.66x | 2.16x |
| 5,000 | 12 | none | 17.98 | 36.25 | 2.98x | 1.93x |
| 5,000 | 12 | record | 19.16 | 38.24 | 3.71x | 2.24x |
| 50,000 | none | none | 180.25 | 338.93 | 3.03x | 1.79x |
| 50,000 | none | record | 190.50 | 355.49 | 3.77x | 2.04x |
| 50,000 | 12 | none | 179.70 | 344.83 | 3.05x | 1.85x |
| 50,000 | 12 | record | 190.63 | 358.81 | 3.80x | 2.05x |

Tuned is the faster compact CPython control in these cases; the simple reference is faster on PyPy.
On PyPy itself, native is approximately 0.89–1.12x the best compact PyPy control across the matrix.
Interpreter choice therefore matters for a ctypes interface with frequent calls.

Representative 50k cap12 CPython-native phase medians (independent phase medians need not sum to total):

| Mode | Build ms | Feed/advance ms | Final drain ms | Extract ms | Close/GC ms | Total ms |
|---|---:|---:|---:|---:|---:|---:|
| none | 0.04 | 161.84 | 4.99 | 5.99 | 7.01 | 179.70 |
| record | 0.50 | 170.38 | 6.65 | 6.00 | 7.16 | 190.63 |

Feed includes registration, validation/copying and Python/native crossings **and** the simulation work
performed by advance; the table does not isolate foreign-call overhead. Final drain time alone is not
an engine-speed metric. Native cap12 throughput is about 278k jobs/s unrecorded and 262k jobs/s recorded.
This is a different contract and observation workload from the earlier static 41 ms result, not an
equivalent-work regression of that kernel.

Separate cold 50k cap12 native lifecycles are 181.84 / 191.71 ms on CPython and 784.55 / 800.01 ms on
PyPy (none/record); cold values still exclude interpreter startup/import/input parsing. Absolute native
peak RSS is about 135 / 130 MiB on CPython and 164 MiB on PyPy. Best compact controls in this same case
are about 139 MiB on CPython and 207–211 MiB on PyPy. These are whole-process peaks including input/model,
temporary storage and detached results, not engine-only storage measurements.

The maximum five-sample spread in the matrix is 16.34% (PyPy-native, 5k, recorded, uncapped); 50k native
CPython spreads are 0.77–2.12%. One host, fixed case order, one generated workload per size, five samples
and seven PyPy warm-ups do not establish general confidence intervals or prove steady state. No result
is extrapolated to LumsCor, AGVs, richer Python policies or production Studio trace overhead.

## Verification and review

- **190 prototype tests pass on CPython and PyPy**, plus **10 existing production ConWIP tests**. PyPy
  retains the two known missing pytest-timeout configuration warnings. The production suite was not
  broadly rerun; production runtime/dependencies/CI/Studio and static C/build/other backends have no diff.
- Dynamic randomized parity covers 18 policy/cap/mode configurations, 12 generated workloads each,
  three backends and eager/incremental feeding. Exact trajectories/releases/records match; KPI totals
  use tight tolerance. A separate ordinary fixture matches actual production ConWIP release/start/finish.
- Dynamic ASan/UBSan passes randomized 1,025-job growth and 143 allocation-failure positions, plus
  boundary/overflow/length checks. Static sanitizer passes its 120 randomized lifecycles and faults.
  No dedicated leak-detector or platform-packaging claim.
- Integration caught idle-clock vs makespan semantics; native/reference tests failed before the fix
  and pass afterward. One independent review found two evidence gaps: missing ongoing binary/source
  enforcement and a validator accepting an incorrect maximum system WIP. Failing regressions preceded
  both fixes. The engine's peak value was correct; independent trajectory and record replay now reject
  corruption. Both fixes preceded every timed case; no discarded benchmark matrix was needed.
- All 80 case identities, sample/probe counts, inputs, sources, libraries, trajectory/record hashes,
  completion/callback/record counts and record sizes pass audit. Negative checks reject duplicate cases,
  wrong source/library hashes, corrupt trajectories and inconsistent timing sums. Ruff and diff checks pass.

Hook recordings have 923,356 records / 29,547,400 bytes. Dynamic recordings have 97,089 records /
3,106,856 bytes for 5k and 973,356 records / 31,147,400 bytes for 50k. They flush to OS buffers without
fsync/durability guarantees.

## Evidence and next decision

[Raw matrix](../../prototypes/native_engine_dynamic/results/matrix.jsonl),
[medians/min/max/cold/RSS CSV](../../prototypes/native_engine_dynamic/results/summary.csv),
[metadata and commands](../../prototypes/native_engine_dynamic/results/metadata.json),
[audit](../../prototypes/native_engine_dynamic/results/verification.json), and
[API/build/reproduction README](../../prototypes/native_engine_dynamic/README.md) are retained separately
from immutable prior evidence. Reproduce the audited tables with:

```sh
.venv/bin/python specs/research/native-engine-data/hooks_dynamic_analysis.py \
  --matrix prototypes/native_engine_dynamic/results/matrix.jsonl \
  --metadata prototypes/native_engine_dynamic/results/metadata.json \
  --csv prototypes/native_engine_dynamic/results/summary.csv \
  --audit prototypes/native_engine_dynamic/results/verification.json
```

The audit checks the current source and locally retained library/input hashes. New builds or source
changes require matching evidence or a new output; the recorded snapshot identifies the measured code.

Keep the conditional go for bounded native-core research. Dynamic ownership and a real release policy
now have correctness and performance evidence, with a material but smaller margin over compact PyPy.
Before committing to a general native architecture, measure amortized producer/registration interfaces
or a compiled Python boundary; this study does not isolate which part accounts for each millisecond.
Preserve compact Python/PyPy controls and default tuple callbacks. Production Studio encoding remains
a separate correctness/performance gate, alongside richer extension semantics, cancellation/recycling
and wheels/platform support. No complete rewrite, push, merge or deployment is implied.
