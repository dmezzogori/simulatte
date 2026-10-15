# Simulatte native-engine vertical prototype

Date: 2026-10-15. Dedicated worktree: `/Users/davide/.codex/worktrees/aee0/simulatte`;
branch: `feature/native-engine-feasibility`. This follows the
[feasibility assessment](2026-10-15-native-engine-feasibility.md) and the narrower user-authorized prototype.

## Question and delivered boundary

Can a Python-authored job shop move its state, event calendar, resource allocation, operation transitions,
KPI accumulation and buffered recording into C, while retaining a Python dispatch hook and exact results?

Implementation is isolated under [`prototypes/native_engine/`](../../prototypes/native_engine/README.md).
The production runtime, SimPy dependency and packaging are unchanged. Four implementations consume the
same immutable input: actual current Simulatte with observations; a small independent Python event model;
that reference with specialized queues; and a standalone C17 engine with ctypes bindings. The C library
does not use the Python C API or link against Python. The contract is deliberately narrower than a new
general-purpose simulation API.

The current baseline still carries general job/entity/WIP bookkeeping, while the new engines carry only
this contract's state. Speedup against current Simulatte therefore combines API simplification, data layout
and implementation language. The Python reference helps separate those effects; this is not an isolated
claim that translating an existing scheduler into C yields the whole improvement. All jobs are known at
construction time; efficient dynamic creation and richer hook access to live state are not demonstrated.

The model has ten capacity-one servers, immediate release, fixed arrivals/routes/durations/due dates,
FIFO or SPT dispatch, and runs until drained. Each job visits 1–10 distinct servers. There are no generator
process extensions, interrupts, cancellation, preemption, repeated-server routes, zero durations, material
waits, AGVs, release policies, remote workers or browser integration. Those remain separate engineering work.

The hook receives immutable `(operation_id, duration)` candidates in enqueue order and returns one ID.
It runs exactly once per grant, including singleton decisions. Built-in native policies enter Python zero
times during execution. Hook exceptions and invalid choices fail the run; close is safe after failure.
Callbacks cannot close, rerun or extract the active runner; another independent runner is permitted.

## Semantic and correctness evidence

The five-phase event contract preserves the useful current Simulatte ordering: arrival batch, operation
start, operation end, job advancement, release dispatch. Ties use insertion order; SPT ties use enqueue
order. Allocation reserves a server before its START event. Omitted SimPy signals are inert within this
slice. The baseline retains real `Environment`, `Server`, `ProductionJob`, `ShopFloor.main` and
`Server.process_job`; default EMA collection is disabled in favor of the same explicitly defined KPIs.

Independent review found and repaired an arrival adapter bug: `now + (at - now)` need not equal `at` in
binary64 (`.2` then `.9` is a counterexample). The feeder now schedules absolute, normal-priority SimPy
events with the existing insertion counter. Resource/process behavior remains unchanged. A red-then-green
regression checks both shifted timestamps and accidentally split equal-time batches. The partial timing
matrix from before this repair was discarded and restarted. The historical pilot remains labeled as such.

Tests cover hand-calculated FIFO/SPT trajectories and KPIs, cross-route ties, randomized exact comparisons,
fractional times, empty input, invalid IDs/options/times, scheduling overflow or failure to advance time,
record corruption, hook errors, reentrant lifecycle calls and recording write/flush failures. Independent
validation checks every operation once, every job completed, job precedence, positive exact durations,
server nonoverlap, record lifecycle, drained resources, dispatch order, WIP/flow identity and KPI totals.
Trajectory arrays/dispatch and record bytes match exactly; aggregate KPIs use tight floating tolerance.
Extreme finite inputs may overflow aggregate KPIs to infinity; event times must remain finite.

The standalone sanitizer executable exercises 120 randomized 1,024-job lifecycles, callback/native policy
equivalence, recording and input/error paths, and injected constructor/calendar allocation failures.
ASan and UBSan pass with fatal recovery. This is not a claim of leak freedom: the macOS run does not
explicitly enable a dedicated leak detector. Python/ctypes boundary behavior has separate tests.

## Measurement method

All timings are local to bigboss: Apple M4 Pro, 12 cores, 48 GiB RAM; macOS 27.0.1 (26A434).
CPython 3.14.3; PyPy 3.11.15 / 7.3.22; SimPy 4.1.2; Apple clang 21.0.0. Release flags:
`-std=c17 -O3 -fPIC -Wall -Wextra -Wpedantic -Werror`, dynamic library, no fast-math. Compilation is a
separate setup step; the reported build phase is per-model construction and lowering.

Inputs are generated once on CPython using seed 20261015 and shared as identical hashed JSON bytes.
Arrivals are exponential with mean interval `5.5 / (10 * offered_load)`, routes are uniform random
permutations of 1–10 servers, durations uniform [.5, 1.5], due dates arrival + uniform [30,45]. The
5,000-job input is the prefix of the 50,000-job input at each offered load (.50 and .95). The realized
workloads are deterministic; no RNG executes in any measured engine. Offered load describes generation,
not a guarantee of stationary utilization in a finite drained run.

The main matrix has 384 cases: two sizes × two loads × two policies × three observation modes × two hook
settings × four backends × two interpreters. Each case has a fresh timing process, one CPython or seven
PyPy warm-ups, and three measured repetitions. A separate fresh process measures cold lifecycle time and
peak RSS. Timings are serial, without simultaneous test/build work. Fixed case order, one machine, one
generated workload per size/load, and three within-process samples limit statistical generalization;
these are local feasibility measurements, not production guarantees or confidence intervals.

Build/lowering, run, detached result extraction and close all count in throughput. Close includes explicit
cyclic garbage collection after dropping the runner. Input parsing/common model validation and imports
are outside timing. Baseline production jobs are constructed as arrivals execute, so its build/run split
is not directly comparable to native eager lowering; compare lifecycle totals. Native includes validation,
Python-to-C copies and C-to-Python output copies. All modes retain trajectory arrays and dispatch order.

Peak RSS is absolute process high-water memory, including interpreter/imports, JSON parsing, input/model,
temporaries and detached results, before validators run. It is not engine-only storage or a per-run delta.
PyPy JIT warm-up and memory costs are visible in raw warm-up arrays and separate cold probes. Seven warm-ups
do not prove steady state; [PyPy's performance guidance](https://pypy.org/performance.html) likewise calls
for measuring the actual workload rather than extrapolating from test speed.

Every repetition verifies trajectory/dispatch fingerprints; recorded runs also verify file hashes.
The first iteration in each timing process and every cold probe independently checks invariants/KPIs and
replays records. Equivalent cases must share trajectory hashes across backend, mode and hook choice, and
record hashes in record mode. Fingerprints use native-endian arrays and are compared on this host.

The 5,000-job inputs contain 27,363 operations and produce 92,089 records / 2,946,856 bytes. The 50,000-job
inputs contain 274,452 operations and produce 923,356 records / 29,547,400 bytes. Python-hook counts equal
operation counts; native built-ins report zero explicit callbacks. Current Simulatte's internal priority
function calls are not these batch-hook counts and remain part of its measured execution cost.

Raw samples, phase medians, throughput, memory, callback/record counts, input/source/library hashes,
dependency versions and reproduction commands are linked from the prototype README. Implementation lineage:
`c08b066` establishes the correct pilot; `6d55b61` adds the Python queue comparison; `750219a` repairs absolute
arrival scheduling before the final matrix. The report does not compare these runs to earlier LumsCor
measurements as if they were equivalent workloads.

## Optimization budget and attribution

One measured optimization iteration was used, below the maximum of three. Profiling the correct pilot
showed repeated Python SPT queue scans and native input conversion as significant costs. The separate tuned
Python implementation uses deque FIFO and heap SPT, preserving the original simple reference as an oracle.
The callback path keeps enqueue-order candidate lists. No native algorithm/binding optimization was made.
The native SPT implementation still scans its waiting list; callback scratch arrays are allocated in
proportion to total operations. See the [ledger](../../prototypes/native_engine/OPTIMIZATIONS.md).

## Recording interpretation

`none` still captures trajectories. `kpi` adds online accumulators. `record` adds a 64 KiB buffered binary
stream containing ARRIVAL, QUEUE, START, END and FINISH records. It flushes before successful run completion,
without fsync. All backends emit the same 8-byte header and 32-byte records. Recording cost is measured
against the otherwise equivalent KPI case, as well as total throughput against the current baseline.

**This is not Studio full tracing.** The prototype does not encode entity schemas, state deltas, snapshots,
seek indexes, checksums, semantic digest, provenance, compression or arbitrary observers. A 2× threshold
for equivalent prototype recording says nothing directly about the speed of the existing full trace path.

## Results and recommendation

The completed CPython half exceeds both evaluation thresholds on every size/load/policy combination.
Native built-ins deliver **19.46–25.61×** unrecorded lifecycle throughput and **19.96–25.44×** equivalent
recorded throughput versus current CPython Simulatte. KPI-only speedups are 19.85–25.92×. These ranges
are ratios of per-case median lifecycle times, not run-loop-only comparisons.

For the representative 50,000-job, .95-load SPT case:

| CPython backend | Mode / policy implementation | Build ms | Run ms | Extract ms | Close ms | Total s | Jobs/s | Peak RSS MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Current Simulatte | none / built-in | 9.2 | 3103.4 | 2.4 | 112.8 | 3.2278 | 15,491 | 214.4 |
| Python reference | none / built-in | 7.3 | 417.0 | 1.3 | 11.5 | .4372 | 114,377 | 123.2 |
| Tuned Python | none / built-in | 7.3 | 336.2 | 1.3 | 11.6 | .3564 | 140,274 | 123.2 |
| Native | none / built-in | 91.6 | 20.0 | 15.3 | 6.1 | .1331 | 375,759 | 122.8 |
| Current Simulatte | record / built-in | 9.7 | 3370.1 | 2.3 | 116.9 | 3.4993 | 14,289 | 218.2 |
| Native | record / built-in | 93.3 | 26.3 | 15.4 | 6.7 | .1417 | 352,946 | 122.7 |
| Python reference | none / Python hook | 7.3 | 552.6 | 1.4 | 11.6 | .5731 | 87,246 | 121.7 |
| Native | none / Python hook | 92.5 | 367.1 | 15.5 | 6.5 | .4821 | 103,710 | 122.7 |
| Native | record / Python hook | 92.7 | 377.8 | 16.4 | 6.9 | .4940 | 101,225 | 122.8 |

Phase medians are reported independently, so their sum can differ slightly from the median total.
In this case C provides 24.26× current-Simulatte throughput but 3.29× reference and 2.68× tuned-Python
throughput. The architecture simplification accounts for much of the gain before C is introduced.
The native kernel is only about 15% of lifecycle time; eager Python validation/lowering dominates.
Native recorded throughput is about 1.94 million operations/s, including all lifecycle phases.

The single queue optimization improves CPython built-in lifecycle throughput by 1.04–1.23× across the
matrix. It is not uniformly beneficial: the representative hooked tuned runner takes .6090 seconds versus
.5731 for the reference (6.3% slower). Extra subclass delegation remains on the hook path and is a plausible
contributor, but these timings do not isolate that cause.
Keeping both implementations makes that regression visible rather than replacing the baseline selectively.

Python hooks materially change the outcome. Across CPython cases native remains 6.98–9.20× faster than
current Simulatte without recording, but the representative advantage over the compact Python reference
shrinks to 1.19×. Calling Python on every grant makes the native lifecycle 3.62× slower than native
built-ins in this high-contention SPT case. This includes candidate construction, policy evaluation and
boundary crossing, not just the foreign-call instruction itself.

For the same native built-in case, adding recording to KPI mode increases median total time from .1351
to .1417 seconds (+4.85%); run time rises from 20.4 to 26.3 ms. Those writes deliver a 29.5 MB file to OS
buffers, not durable storage. No equivalent claim is made for Studio full traces.

PyPy materially improves the Python alternatives. Its native built-ins still exceed both thresholds versus
current **PyPy** Simulatte: 9.44–13.92× without recording and 9.60–14.02× with recording. Representative
50,000-job, .95-load SPT results are:

| PyPy backend | Policy implementation | Build ms | Run ms | Extract ms | Close ms | Total s | Jobs/s | Peak RSS MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Current Simulatte | built-in | 13.8 | 1520.6 | .1 | 25.0 | 1.5598 | 32,055 | 319.0 |
| Python reference | built-in | 10.6 | 270.9 | 3.3 | 9.8 | .2947 | 169,646 | 163.4 |
| Tuned Python | built-in | 10.9 | 298.5 | 3.3 | 10.4 | .3231 | 154,753 | 163.2 |
| Native | built-in | 63.9 | 20.3 | 27.3 | 6.8 | .1182 | 423,179 | 173.3 |
| Python reference | Python hook | 10.7 | 328.6 | 3.5 | 9.7 | .3525 | 141,861 | 164.4 |
| Native | Python hook | 63.4 | 370.1 | 26.8 | 7.5 | .4680 | 106,846 | 174.8 |

These rows use `none` mode. On PyPy, queue specialization gives 0.91–1.04× the reference's built-in
throughput, so the CPython improvement does not generalize. Native hooks are slower than the compact
reference in this case; its Python execution avoids repeatedly crossing the ctypes boundary.

The stronger cross-interpreter comparison is important: **CPython-native versus the faster of the two
compact PyPy engines** gives only **1.90–2.44×** built-in throughput without recording and **2.31–2.62×**
with recording. Thus the 3× criterion is exceeded against current Simulatte, but not against the strongest
redesigned Python alternative. With Python hooks, this comparison is 0.73–0.96× without recording and
0.82–1.14× with recording. A C backend is not an unconditional speed improvement for custom Python policies.

RSS also does not imply universal native savings: in the representative PyPy case, native peaks at
173.3 MiB versus the reference's 163.4 MiB. CPython native and reference are both about 123 MiB, while
current Simulatte is about 214 MiB. Shared input parsing and temporary conversion data contribute strongly
to these absolute peaks. Cold timings and all warm-up samples are retained alongside the steady-run samples;
none of these tables includes interpreter startup or input loading time.

The largest within-case spread (`max / min - 1`) among the three main samples is 18.3% (a 5k hooked tuned-PyPy recording
case). Small queue-optimization ratios should be treated cautiously; the large advantage over current
Simulatte is much larger than this local timing variation. Case order was fixed and CPU affinity/thermal
state were not controlled. No second or third optimization iteration was used: the central feasibility
question and callback limitation are already clear, and optimizing until a preferred outcome appears
would weaken the evaluation.

## Recommendation

**Conditional go for further native-core investment; no commitment yet to a complete rewrite.**
The slice works, preserves exact behavior, passes the targeted correctness/sanitizer checks, and exceeds
the stated throughput gates against current Simulatte on both interpreters. C provides an additional
measurable benefit over a redesigned Python engine, especially when recording. Keeping Python authoring
with native built-ins and explicit extension hooks is technically viable within this tested boundary.

The conditions are substantive:

1. Keep the compact Python implementation as a semantic and performance reference. Much of the gain is
   architectural, and PyPy is a competitive lower-complexity option for Python-heavy policies.
2. Design model lowering, output views and hook data access before growing the C implementation. In this
   prototype conversion dominates native built-in lifecycle time, while per-grant Python hooks can erase
   the incremental advantage over PyPy. More features alone do not address either cost.
3. Require another bounded milestone before authorizing the full rewrite: dynamic state/event ownership,
   a realistic release policy and production-quality trace encoding must preserve semantics and be measured
   against these strong Python baselines. Wheel/platform support and maintainability need separate evidence.

LumsCor parity, AGVs, remote provisioning, browser support and the full rewrite remain deferred. These are
proposed subsequent investigations, not additional work performed or new adopted commitments in this goal.

## Reproduction and delivery evidence

- [Runnable commands and API example](../../prototypes/native_engine/README.md).
- [Raw 384-case matrix](../../prototypes/native_engine/results/final-matrix.jsonl) and
  [per-case phase/throughput/memory CSV](../../prototypes/native_engine/results/final-summary.csv).
- [Expanded representative tables](../../prototypes/native_engine/results/final-tables.md),
  [environment and hashes](../../prototypes/native_engine/results/metadata.json), and
  [optimization ledger](../../prototypes/native_engine/OPTIMIZATIONS.md).

The measured source is frozen at `750219a`. The launcher initially dereferenced a PyPy virtualenv symlink
and failed its first import before timing; a shell launcher preserving the venv path resumed the unchanged
matrix. The delivered driver uses a lexical absolute path instead. A focused fresh-process repeat validates
that repair and the closest native/reference comparisons; no measured engine code changed.

The [focused repeat](../../prototypes/native_engine/results/validation-repeat.jsonl) contains 16 cases,
five measured samples each, with fresh timing and memory processes. Every trajectory/record hash matches
the main matrix; all median totals are within 2.32% of the original case. The callback/PyPy crossover
persists. Across both datasets there are 1,232 timed samples and 400 separate cold memory probes.
The [audit record](../../prototypes/native_engine/results/verification.json) confirms exact Cartesian
coverage, counters, hashes, source provenance and absence of production/engine changes.

Final checks: 81 prototype tests pass on CPython and 81 on PyPy; ASan/UBSan and Clang static analysis pass;
Ruff lint/format and `git diff --check` pass. `uv run --all-extras pytest` passes **3,414 tests**, skips one,
and reaches **99.05%** production coverage (required 99%). Its three warnings are existing dependency
deprecations in Studio's HTTP/WebSocket test stack. Local documentation links resolve. Production runtime,
dependencies and CI files have no diff against `d3e17e6845e75163be34d583eac4417ee5d67290`.
