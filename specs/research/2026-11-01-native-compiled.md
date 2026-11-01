# Compiled scalar admission and feedback-driven jobs

2026-11-01, starting revision `7171549`, `feature/native-engine-feasibility` worktree.
Davide authorized the [bounded experiment](2026-11-01-native-compiled-plan.md) after the
[batch registration study](2026-11-01-native-registration.md). No production tracing/integration is included.

## Outcome

The compiled CPython boundary improves full scalar lifecycle performance without producer lookahead.
At 50k jobs it takes **61.95/70.58 ms** for independent input (none/record), versus ctypes
**180.29/189.89 ms**: **2.91x/2.69x**. It is **4.90x/5.15x** the faster compact PyPy control on this input.
The exact same C engine shared binary is used in both native paths.

With live observations, Python decisions and generated Job construction included, compiled CPython
takes **196.95/204.19 ms** versus ctypes **329.91/334.22 ms**: **1.68x/1.64x**. Its advantage over the
faster compact PyPy control falls to **1.56x/1.83x**. The binding improvement survives genuine per-job
feedback, but the roughly fivefold independent-input margin does not generalize to this controller.
The feedback workload also changes routing/durations, so subtracting the two producer timings would
not isolate a Python-feedback cost.

Keep the compiled path opt-in for research. This supports a conditional go for a CPython native backend,
not a general replacement decision. Stop boundary tuning after this candidate; the next gate is real
production trace encoding on representative scalar and feedback workloads. Python dispatch callbacks,
general processes and broader workload coverage remain unresolved.

## Full lifecycle results

50k jobs, medians in ms. Compare only within a producer/mode. Best compact PyPy selects the faster
reference/tuned control freshly measured under that exact schedule and generated workload.

| Producer | Mode | CPython ctypes | CPython compiled | Binding gain | Best compact PyPy | Native margin |
|---|---|---:|---:|---:|---:|---:|
| Independent | none | 180.29 | 61.95 | 2.91x | 303.39 | 4.90x |
| Independent | record | 189.89 | 70.58 | 2.69x | 363.78 | 5.15x |
| Feedback | none | 329.91 | 196.95 | 1.68x | 306.35 | 1.56x |
| Feedback | record | 334.22 | 204.19 | 1.64x | 373.32 | 1.83x |

The best compact CPython controls are tuned in these four cases: 548.77/709.84 ms independent and
542.00/689.59 ms feedback. Compiled CPython is 8.86x/10.06x and 2.75x/3.38x those controls respectively.
For 5k jobs, compiled CPython takes 6.33/7.59 ms independent and 19.48/20.32 ms feedback; its margins
over best compact PyPy are 5.13x/5.58x and 1.70x/2.07x. Small PyPy controls have greater timing spread,
so the 50k results provide the clearer comparison. All 64 cases, phases and ranges are in the CSV.

The feedback controller makes 49,999 observations for 50k jobs: 16,113 active, 23,708 pooled and 10,178
finished; the seed plus long/short/normal branches create 205,451 operations (versus 274,452 independent).
At 5k it creates 20,612 operations (versus 27,363). Exact jobs, observations and decisions match every
backend/interpreter. Recording is 766,353 records / 24,523,304 bytes feedback at 50k, 76,836 / 2,458,760
at 5k. Independent records retain prior sizes/content: 973,356 / 31,147,400 and 97,089 / 3,106,856.

## PyPy compatibility-path results

50k jobs, medians in ms. These exercise this C API/cpyext candidate, not a PyPy-native CFFI boundary.

| Producer | Mode | PyPy ctypes | PyPy compiled | Binding gain | Vs best compact PyPy |
|---|---|---:|---:|---:|---:|
| Independent | none | 338.22 | 220.64 | 1.53x | 1.38x |
| Independent | record | 346.95 | 230.07 | 1.51x | 1.58x |
| Feedback | none | 460.62 | 392.46 | 1.17x | 0.78x |
| Feedback | record | 440.34 | 417.94 | 1.05x | 0.89x |

PyPy's feed phase is faster through the extension than ctypes, but its independent extraction phase is
115.78/115.26 ms and close/GC 35.95/36.63 ms, compared with CPython compiled 5.12/5.37 and 6.27/6.85 ms.
For feedback, PyPy extraction is 90.53/94.02 ms and close/GC 41.50/43.29 ms. These measured conversion
and cleanup costs prevent recommending this interface as a general PyPy backend. They do not establish
which interpreter mechanism causes each millisecond or a performance ceiling for compiled bindings.

## Variability, cold lifecycle and memory

50k compiled CPython ranges: independent 60.96–62.62 ms (none), 70.04–71.62 ms (record), spreads
2.72%/2.25%; feedback 196.29–199.37 and 202.11–206.60 ms, spreads 1.57%/2.22%. Its cold lifecycles are
64.49/73.55 ms independent and 204.81/206.52 ms feedback. Process peak RSS is 131/131 MiB independent,
140/139 MiB feedback. These are absolute high-water values including templates, generated jobs/logs,
interpreter and output, not isolated native memory.

50k compiled PyPy ranges are 219.53–223.86 / 225.46–231.61 ms independent (1.97%/2.73%) and
384.62–394.50 / 414.86–418.90 ms feedback (2.57%/.97%). Its cold lifecycles are 286.00/292.10 ms and
423.34/455.77 ms; peak RSS is 355/355 and 290/290 MiB. This extra process memory is material to the
candidate comparison. Maximum spread anywhere is 27.17%, the 5k PyPy reference feedback/none case;
do not treat its five samples or seven warmups as confidence/steady-state evidence.

## Implemented boundary

`CompiledStream` retains the scalar API: add, advance, inspect, run, result, now and close. It accepts
exact validated Jobs, returns the existing owner/id JobHandle and serializes public calls with RLock.
Its small C API extension owns an opaque capsule with independent counts/lifecycle state, converts one
route/duration tuple in C, calls the existing nd_add and builds detached Python lists/dictionaries.
Every job still has an individual arrival and handle; no advance/inspection/decision is batched or omitted.

The extension dynamically links the exact same engine DSO as ctypes. C engine source/header/build,
the original ctypes boundary, compact controls and prior evidence are unchanged. It releases the GIL
for native construction/add/advance/run/close; the raw capsule rejects concurrent/reentrant calls with
a busy guard, including close and result extraction. Failed C calls poison the capsule; Python-invalid
input is rejected before mutation. Close/destruction preserve single ownership and detached outputs.

This candidate uses the [Python C API](https://docs.python.org/3.14/extending/extending.html) and
[opaque capsules](https://docs.python.org/3.14/c-api/capsule.html), adding no dependency. Installed CPython
and PyPy headers support local builds. PyPy's result exercises cpyext; its documentation recommends
[CFFI for PyPy integration](https://doc.pypy.org/extending.html) and warns about
[C API emulation costs](https://doc.pypy.org/faq.html). Neither CFFI nor a universal compiled-boundary
comparison is demonstrated. No stable ABI, wheel/platform, free-threaded or subinterpreter support claim.

## Producer semantics

Independent: the unchanged scalar loop advances to each historical input arrival and admits that Job.
Feedback: at each template arrival, advance, inspect a prior handle chosen deterministically in blocks
of 32, and only then construct/admit the next Job. A pooled observation reverses and halves its route
and scales durations by .75; scheduled/active scales durations by 1.125; finished keeps normal work.
The first job seeds the controller. Arrival epochs and due dates stay in the template; future job
contents are unavailable before observing the engine. This demonstrates a bounded Python controller,
not arbitrary generators, callbacks into Python from C, new dispatch hooks or general SimPy processes.

The decision log retains each observed id, arrival/release/finish availability, state and chosen branch.
Independent validation reconstructs the state from final trajectories using strict `advance(t)` (events
at t remain pending), then checks each generated route/duration. Actual workloads, logs, trajectories,
records and counters must match across every backend/interpreter/repetition. All controller decisions,
inspection, Job construction/validation, handle creation and log materialization are timed. Generated
jobs/logs are retained for later validation in every backend; reported RSS includes this audit retention.

## Measurement protocol and evidence

Same bigboss M4 Pro 12 cores / 48 GiB, macOS 27.2, Apple clang 21, CPython 3.14.3 and PyPy 3.11.15/7.3.22.
64 cases: independent/feedback x 5k/50k templates x none/record x ctypes/compiled/reference/tuned x both
interpreters, ConWIP cap 12, SPT. Historical serialized load .95 input templates are shared; feedback's
generated workload differs, so cross-producer timings are not equivalent work. Five samples, CPython1 /
PyPy7 warmups, separate fresh-process cold/RSS, deterministic shuffled order, serial execution after
builds/tests. Code and engine/two extension/input hashes are frozen throughout each worker/matrix.

Imports and DSO/extension loading are explicitly outside timing for both native boundaries, even cold
probes. Engine construction, validation/lowering/copying, handle creation, each advance/inspect/add,
processing, detached extraction, close and explicit GC are inside. This fresh study therefore defines
cold setup more narrowly than the earlier constructor-loaded ctypes probe; do not subtract historical
cold timings as the binding gain. Whole-process peak RSS is captured before correctness validation,
not an engine-allocation delta. Medians of phase durations need not add to the median total.

[Raw matrix](../../prototypes/native_engine_dynamic/compiled-results/matrix.jsonl),
[metadata](../../prototypes/native_engine_dynamic/compiled-results/metadata.json),
[summary](../../prototypes/native_engine_dynamic/compiled-results/summary.csv), and
[audit](../../prototypes/native_engine_dynamic/compiled-results/verification.json) retain new evidence.
[README](../../prototypes/native_engine_dynamic/README.md#compiled-scalar-and-feedback-experiment) has
commands. The audit checks exact coverage, identities, samples/timing sums, source/binaries/inputs,
counter/record sizes and generated-workload/decision parity. Independent-producer trajectories/records
also match prior evidence; historical timings are not reused. Eight negative checks reject duplicate,
source/library/trajectory/generated-job/decision/observation-count/timing corruption.

## Verification and limits

New compiled and feedback tests first failed on missing modules, then passed. A detached-output test
initially mutated its own expected reference result; fixed the test alias before continuing. Combined
suites pass 250 tests with one sanitizer-only skip per interpreter. The separate CPython ASan/UBSan
boundary suite passes 31 cases, including three scratch/state allocation failure positions, malformed
raw inputs, closed capsules, growth/copy lifetimes, serialized public threads and concurrent raw close
rejection while a GIL-released run is active. Python malloc backing is enabled; leak detection disabled.
The boundary itself is instrumented while linking the release engine DSO; the standalone instrumented
engine harness separately checks 143 scalar and 144 bulk allocation failure positions. This is not an
integrated PyPy sanitizer or a dedicated leak detector result.

Frozen source snapshot is `35515f7d50e9a7030ad480e62ca7122019b930382956a61c652b8f922065283c`.
Engine DSO remains `de1a540982cb43ccef363f6c4267d57324ec395c2c7596ba006f8d927ed49602` from the previous
study; metadata retains both extension hashes and `otool -L` linkage to that same DSO. The audit passes
64 cases / 320 timed samples / 64 cold probes and all eight corruption checks.

No kernel, original binding, dependencies, CI, production tests/runtime, Studio or earlier evidence
changes. Ruff, scope/link/whitespace checks and calculations are verified before completion. Work is
reviewed inline, without a separate agent review. This remains one host, fixed inputs/policy/cap, five
samples and seven PyPy warmups, not confidence intervals or proof of JIT steady state. NDYN recording
flushes to OS buffers without fsync and remains separate from production Studio tracing. Memory bounds,
cancellation/recycling and richer extension semantics remain unproven. Stop after this one candidate.
