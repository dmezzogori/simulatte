# Native registration and advancement boundaries

Date: 2026-11-01. Starting revision: `69010b7`, research branch `feature/native-engine-feasibility`.
Davide authorized the [bounded experiment](2026-11-01-native-registration-plan.md) following the
[dynamic jobs/ConWIP study](2026-11-01-native-hooks-dynamic.md). This is one performance iteration on
that functional baseline, with no production integration or compiled Python extension.

## Outcome

Amortizing admission materially improves this independent-input workload. CPython native with 32-job
bulk windows takes **88.25/97.91 ms** in none/record versus **182.10/190.02 ms** with per-job advance/add
(2.06x/1.94x). PyPy native with 256-job bulk windows takes **80.07/86.22 ms** versus **318.44/340.55 ms**
(3.98x/3.95x). These are the lowest medians among the two preselected bulk windows on each interpreter,
not an automatically selected default or a proof of optimal batch sizes.

Against the fastest compact PyPy control across **all** measured schedules/windows, the CPython bulk
path is **3.71x/4.00x** faster and the PyPy bulk path **4.09x/4.54x**. Those controls are reference PyPy
with per-job advancement (327.76/391.43 ms). Matched-window controls are slower, so quoting only those
ratios would overstate the gain available against the strongest compact alternative.

Keep scalar registration as the default and bulk as an opt-in interface for producers with independent
lookahead. This recovers a useful native margin without changing C event processing. It does not show
the same benefit for feedback-dependent production, Python dispatch hooks or full Studio encoding.

## Streamed lifecycle results

Medians in ms; all registration, processing, extraction and cleanup are included.

| Interpreter | Mode | Scalar window 1 | Scalar 32 | Bulk 32 | Scalar 256 | Bulk 256 |
|---|---|---:|---:|---:|---:|---:|
| CPython | none | 182.10 | 164.41 | 88.25 | 163.73 | 94.61 |
| CPython | record | 190.02 | 172.46 | 97.91 | 178.78 | 104.83 |
| PyPy | none | 318.44 | 280.26 | 114.23 | 308.98 | 80.07 |
| PyPy | record | 340.55 | 314.21 | 122.60 | 302.65 | 86.22 |

The best larger scalar window yields 1.10–1.14x over per-job advancement in these cases.
At the same selected window, bulk admission improves CPython by 1.86x/1.76x and PyPy by 3.86x/3.51x.
Most of the observed improvement therefore needs the admission-interface change; reducing advance
frequency alone does not recover it. The combined bulk change amortizes locking, native-time queries,
ctypes argument/view creation and calls, while changing Python buffer allocation/conversion. It does
not isolate each of those costs individually.

| Native path | Mode | Vs same-window native scalar | Vs same-window best compact PyPy | Vs strongest compact PyPy overall |
|---|---|---:|---:|---:|
| CPython bulk 32 | none | 1.86x | 4.27x | 3.71x |
| CPython bulk 32 | record | 1.76x | 4.55x | 4.00x |
| PyPy bulk 256 | none | 3.86x | 5.67x | 4.09x |
| PyPy bulk 256 | record | 3.51x | 6.09x | 4.54x |

The fastest compact CPython control is tuned scalar window 1 at 539.03/717.44 ms. Its ratios against
CPython bulk 32 are 6.11x/7.33x; against PyPy bulk 256, 6.73x/8.32x. Neither eager registration nor
larger windows improved the fastest compact PyPy control on this input.

Selected CPython bulk-32 ranges are 87.85–88.86 ms (none) and 97.51–98.54 ms (record), spreads 1.15%/1.05%.
PyPy bulk-256 ranges are 78.92–84.32 ms and 80.08–92.31 ms, spreads 6.85%/15.28%; the latter is the
largest spread in the matrix. A general claim that PyPy native is faster than CPython native is not
supported across the tested windows. Their separate cold lifecycles are 92.54/101.30 ms for CPython
bulk 32 and 112.28/119.52 ms for PyPy bulk 256. Corresponding process peak RSS is 132/131 MiB and
156/156 MiB; these are whole-process values, not allocation savings attributed to batching.

Window 32 makes 1,563 registration and advancement API calls each; window 256 makes 196 each, versus
50,000 each for window 1. Bulk still returns 50,000 handles and schedules 50,000 separate arrivals.
All record cases emit 973,356 records / 31,147,400 bytes with identical ordering and content.

## Eager registration diagnostic

Feed medians in ms. No simulation event is processed in this phase.

| Interpreter | Mode | Scalar feed | Bulk 32 feed | Bulk 256 feed |
|---|---|---:|---:|---:|
| CPython | none | 116.47 | 49.29 | 42.98 |
| CPython | record | 114.47 | 48.20 | 42.97 |
| PyPy | none | 249.67 | 61.02 | 21.47 |
| PyPy | record | 250.62 | 63.56 | 22.85 |

Bulk 256 lowers isolated eager admission time by 2.71x/2.66x on CPython and 11.63x/10.97x on PyPy.
This confirms that amortized admission matters even without interleaved simulation work. Eager total
lifecycles are 125.59/134.84 ms for CPython bulk 256 and 110.38/114.49 ms for PyPy bulk 256, slower than
their best interleaved bulk paths. Registering the entire known workload up front changes the calendar
and moves execution to run; it is a diagnostic, not the recommended producer schedule.

## Interface and semantics

The dynamic native prototype adds opt-in `add_many(exact_tuple_of_Jobs)`, returning one stable handle
per job. Python validates the batch before native mutation and builds contiguous owned arrays inside
the measured lifecycle. C checks every declared length before dereferencing, validates offsets before
pointer arithmetic, then invokes the unchanged scalar C admission routine for each job. It copies all
data and creates every individual arrival event. The scalar binding, C event loop, policies, ConWIP,
ownership rules, results and NDYN records remain unchanged. C failure poisons partial internal state
and leaves it safely destroyable; no partial batch handles escape.

Interleaved windows advance to their first arrival, then register 1, 32 or 256 jobs. Scalar and bulk
registration at a given window have identical advance boundaries. Window 1 uses the original per-job
driver exactly. Larger windows require the producer to know independent future jobs. Final event
order/results match, but those registrations and handles become visible earlier to Python. This is
not a semantic replacement for feedback-dependent job creation or inspection between arrivals.

The eager diagnostic feeds all jobs before any advancement. Its feed phase contains registration,
validation/lowering/copies, arrival scheduling and handle creation, with no simulation processing.
Its larger calendar also changes later execution cost. Interleaved feed includes simulation work;
neither feed time is pure FFI overhead, and eager feed cannot be subtracted as an additive decomposition
of interleaved time. Scalar-versus-bulk at matched windows measures the complete admission-interface
change; scalar window 1 versus larger windows combines fewer advance calls and a different calendar.

## Measurement protocol

Bigboss: Apple M4 Pro, 12 cores, 48 GiB, macOS 27.2 arm64, Apple clang 21.0.0. Historical CPython-generated
input: 50,000 jobs, 274,452 operations, load 0.95, SPT, ConWIP cap 12; none and record modes.
CPython 3.14.3 and PyPy 3.11.15/7.3.22 use identical serialized/model-validated inputs.

64 cases: 11 interleaved and five eager configurations per interpreter/mode. Compact reference and
queue-specialized Python controls use the same schedules; select the faster control per comparison.
Five samples after one CPython/seven PyPy warmups; each case also has a separate fresh-process cold/RSS
probe. Case order is shuffled with seed 20261101 and workers run serially after tests/builds stop.
Source, both libraries and input hashes are frozen and checked before/after workers and by the parent.

Parsing/model construction and imports are outside timing. Construction, window slicing, admission
validation and conversion, all C copies, every handle, advancement and processing, detached list results,
close and explicit GC are inside. Bulk returns a list of handles per batch; scalar handles are created
and discarded individually. Neither path retains a full-run handle list. Reported registration/advance
call counts refer to API operations, not all ctypes crossings: accepting calls also query native time.
Cold timing has the same boundary, excluding interpreter startup/imports/parsing. Peak RSS is absolute
whole-process high-water memory including inputs, interpreter and result, not engine-only allocation.

## Verification and evidence

Before timing, the 22 new bulk tests failed on the missing interface and then passed. The combined
static/dynamic suites pass **213 tests on each interpreter**. PyPy reports the two known missing
pytest-timeout configuration warnings. The actual registration worker is exercised in separate processes
on both interpreters. FIFO/SPT, cap None/1/4, none/kpi/record fixtures compare scalar reference/native and
native bulk windows 1/2/7/32/256 with exact results and record bytes and independent conservation/replay
checks. Cases cover malformed batches before mutation, empty/sealed/closed calls, ID/count limits,
mixed scalar/bulk growth, old and foreign handles, equal-time arrivals/completions and copied lifetimes.

ASan/UBSan passes the existing 1,025-job growth exercise with 143 allocation-failure positions, and the
new 1,025-job batch exercise with 144 positions. Batch boundary cases include all five mismatched array
lengths before touching unreadable nonnull pointers, null arrays/output, bad offsets, sentinel counts,
partial admission failure and sequence overflow. Ruff lint/format and scoped diff checks pass. All
production source/tests/dependencies/CI/docs/Studio/benchmarks and the static prototype are unchanged.
This work was reviewed inline; no separate agent review was commissioned for this experiment.

The [raw matrix](../../prototypes/native_engine_dynamic/registration-results/matrix.jsonl),
[metadata/source snapshot](../../prototypes/native_engine_dynamic/registration-results/metadata.json),
[summary CSV](../../prototypes/native_engine_dynamic/registration-results/summary.csv) and
[audit](../../prototypes/native_engine_dynamic/registration-results/verification.json) are separate from
historical evidence. The [README](../../prototypes/native_engine_dynamic/README.md#registration-experiment)
contains reproduction commands. The audit checks full coverage, identities, timing sums, counters, API
operation counts, current hashes, exact trajectory/record equivalence and matching hashes against the
previous study. Negative audits reject duplicate cases and corrupt source, library, trajectory,
registration counts and timing totals. Historical timings are never reused as fresh controls.

The source snapshot and locally retained release library/input are required to rerun the provenance
audit. This remains a single-host experiment with five samples, fixed inputs/cap and seven PyPy warmups;
these are not confidence intervals or proof of steady-state JIT behavior. Recording is NDYN0001 flushed
to OS buffers without fsync, not production Studio encoding. Python dispatch hooks, generators,
cancellation/recycling, bounded infinite-stream memory and platform/wheel support remain separate gates.

## Next decision

Retain the conditional go for bounded native-core research. An opt-in producer window can restore a
roughly fourfold lifecycle advantage over the strongest compact PyPy control on this slice, with exact
results and owned dynamic jobs. Stop this iteration without further batch-size or kernel tuning.
If job-by-job Python feedback is required, the next boundary comparison should use a compiled scalar
binding under the original advancement schedule; this experiment has not measured that interface.
Production Studio trace encoding remains its own correctness/performance gate before general adoption.
No complete rewrite, push, merge or deployment is implied.
