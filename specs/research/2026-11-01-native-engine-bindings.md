# Native-engine binding-only follow-up

Date: 2026-11-01. Branch: `feature/native-engine-feasibility`, starting revision `2c2dfd2`.
Implements the [binding-only plan](2026-11-01-native-engine-bindings-plan.md), following the
[October prototype report](2026-10-15-native-engine-prototype.md) and its Cortex review
`capture-e76aec07-d100-4b84-9361-b3cf4ab8c399`.

## Outcome

The binding change reduces CPython native built-in lifecycle time from **127.25 to 41.49 ms** without
recording (**3.07x**) and **137.52 to 48.33 ms** with recording (**2.85x**). These fresh before/after
measurements include lowering, run, detached Python list extraction, close and cyclic garbage collection.
The C source, release binary, callbacks, other backends and timing driver are identical across measurements.

Against the faster freshly measured compact PyPy engine, CPython native now delivers **6.89x** throughput
without recording and **7.02x** with recording, up from **2.22x** and **2.44x** before the change.
These results support the review's main finding: the previous narrow margin over redesigned Python was
largely binding overhead. The rough 35–40 ms / 8x projection was optimistic; returning actual Python lists
still costs several milliseconds, and the measured outcome is the conclusion.

Python hooks remain the constraint. Removing conversion overhead improves their CPython native lifecycle
by 1.22–1.23x, but native remains slower than the best compact PyPy engine without recording, and roughly
at parity with recording. This follow-up does not establish a native advantage for Python-heavy policies.

## What changed

Only `prototypes/native_engine/native.py` changes simulation/binding behavior:

- Exact frozen `Workload`/`Job` inputs skip redundant per-element validation after common model validation.
  Duck-typed inputs and subclasses retain the checked boundary. Top-level count limits and all C-side
  validation remain. Flattened lists are staged into five contiguous `array` buffers; ctypes uses
  `from_buffer` views instead of constructing arrays through argument splatting.
- Output arrays are copied using `memmove` into owned buffers and converted to new Python lists on each
  `result()` call. Empty fields avoid pointer access. No output views, cached inputs or results, or copies
  moved outside lifecycle timing. Numeric widths are checked before using the native buffer ABI.
- Tests cover typed/checked/subclass equivalence, boolean/oversized inputs, empty input, input lifetime,
  and list independence through mutation, close and garbage collection. Existing randomized, fractional,
  recording and callback/error tests remain unchanged.

One independent review found that a malformed duck-typed container could advertise a different length
from its iteration, producing a buffer smaller than the count passed to C. Added explicit checks of all
five flattened lengths before creating native views. Four regression cases failed before the correction
and pass afterward; a fail-fast library-loader guard keeps those tests from entering C with unsafe input.
The review also caught an OS-version typo, corrected from the historical 27.0.1 to the measured 27.2.
The corrected candidate was remeasured in full; the first candidate's 120 samples, 24 cold probes, metadata
and measured binding source are retained under `results/binding-pre-review/`, excluded from final ratios.

The C ABI/algorithms, hook candidate data and call counts, result types, locking, recording and public
lifecycle are unchanged. Production runtime, dependencies/lockfile, CI and Studio have no diff.

## Measurements

Same bigboss host as the original study: Apple M4 Pro, 12 cores, 48 GiB RAM; now macOS 27.2, Apple clang 21.0.0.
CPython 3.14.3 and PyPy 3.11.15 / 7.3.22 match the historical interpreters. Restoring the moved editable
environments installed the current locked tool/dependency versions; exact versions are in the metadata.
The before and after environments match. Release flags remain C17, `-O3 -fPIC`, warnings as errors,
dynamic-library linkage, no fast-math. The release library was built once and retained for both phases.

Scope is deliberately **50,000 jobs, offered load .95, SPT**, on the historical hashed input with 274,452
operations. This repeats all 16 original validation cases and adds eight tuned-Python controls: reference,
tuned and native, CPython and PyPy, none/record, built-in/Python hooks. Each phase has 24 fresh timing
processes, one CPython or seven PyPy warm-ups, five measured samples per case and 24 separate cold/RSS
probes. Total: **240 measured samples and 48 cold probes**. Measurements were serial, without concurrent
tests/builds. Input parsing/common model validation and imports remain outside timing.

| CPython native | Hook | Before ms | After ms | Before/after speedup | After vs fastest compact PyPy |
|---|---|---:|---:|---:|---:|
| none | built-in | 127.25 | 41.49 | 3.07x | 6.89x |
| record | built-in | 137.52 | 48.33 | 2.85x | 7.02x |
| none | Python | 450.57 | 368.90 | 1.22x | 0.92x |
| record | Python | 475.50 | 387.72 | 1.23x | 1.02x |

Ratios use median lifecycle totals, never sums of phase medians. PyPy reference is the faster compact PyPy
engine in all four after comparisons. Its recorded hook total of 396.65 ms versus native 387.72 ms is
too close to support a meaningful win given timing variation and control drift.

| CPython native built-in | Build ms | Run ms | Extract ms | Close ms | Lifecycle ms |
|---|---:|---:|---:|---:|---:|
| none, before | 87.25 | 18.42 | 14.98 | 6.60 | 127.25 |
| none, after | 10.62 | 18.64 | 5.52 | 6.75 | 41.49 |
| record, before | 90.12 | 25.20 | 15.24 | 7.04 | 137.52 |
| record, after | 11.04 | 24.76 | 5.54 | 6.82 | 48.33 |

Phase medians are independent, so they need not sum to the median lifecycle. Extraction includes list
materialization; the review's 0.2 ms probe measured the bulk copy alone. Native unrecorded built-in
throughput is now about 1.20 million jobs/s, recorded about 1.03 million jobs/s for this input.

PyPy-native built-in totals also improve: **115.20 to 42.77 ms** without recording and **121.87 to 51.32 ms**
with recording (2.69x / 2.37x). Its unrecorded extraction remains 11.46 ms, versus CPython's 5.52 ms.
Absolute cold-process peak RSS is approximately 122 MiB for CPython native before and after; PyPy native
built-ins fall from about 174 to 153 MiB. These peaks include interpreter, input parsing, model, temporaries
and result, and are not engine-only allocation measurements.

The separate single cold CPython-native built-in probes fall from 129.73 to 43.74 ms without recording
and 141.01 to 50.45 ms with recording. These are cold lifecycle samples, still excluding process startup,
imports and input parsing; all cold phase samples are retained in the CSV/raw evidence.

All 16 unchanged reference/tuned control timing changes lie between **−0.18% and +2.41%**, consistent with
modest drift. The effect is small relative to the binding gain, so no additional repeat was needed to
distinguish that gain. The largest after within-case sample spread is 7.32% (PyPy native built-in record);
built-in CPython native spreads are 5.92% / 1.41%. Near-parity hook ratios are not robust wins. Five within-process samples,
fixed case order, one host and one representative workload do not establish general confidence intervals
or new ranges for the original 384-case matrix. Current Simulatte was not retimed in this follow-up.

## Verification and evidence

- Both original prototype baselines: 81 passed. Optimized suites: **111 passed on CPython, 111 on PyPy**;
  PyPy retains its two existing missing pytest-timeout configuration warnings.
- Standalone ASan/UBSan passes boundaries, 120 randomized lifecycles and allocation faults. No additional
  leak-detector claim. Ruff lint/format and diff checks pass.
- Exact trajectory and recording fingerprints, all corresponding counters and makespans, and record byte
  sizes agree before/after and across equivalent cases. Each record run has 923,356 records / 29,547,400 bytes.
  Existing independent KPI/invariant checks and recording replay execute in timing workers outside timing.
- Analysis audits exact coverage, sample/probe presence, timing sums and metadata provenance. Negative
  checks reject duplicate-case evidence, corrupt trajectory fingerprints and a library metadata mismatch.
- Production files, C sources, other runners, build, benchmark and validators have no diff against `2c2dfd2`.
  The production suite was not rerun for this isolated binding change.

Raw [before](../../prototypes/native_engine/results/binding-before.jsonl) and
[after](../../prototypes/native_engine/results/binding-after.jsonl) samples,
[phase/min/max/throughput/RSS summary](../../prototypes/native_engine/results/binding-summary.csv),
[metadata and commands](../../prototypes/native_engine/results/binding-metadata.json), and
[audit](../../prototypes/native_engine/results/binding-verification.json) are retained separately from
the immutable historical evidence. Reproduction of the analysis:

```sh
.venv/bin/python specs/research/native-engine-data/binding_comparison.py \
  --before prototypes/native_engine/results/binding-before.jsonl \
  --after prototypes/native_engine/results/binding-after.jsonl \
  --csv prototypes/native_engine/results/binding-summary.csv \
  --audit prototypes/native_engine/results/binding-verification.json
```

The analysis checks current source, input and library hashes; rebuilds or later source changes require
matching evidence or a new study. Before-timing source is `2c2dfd2`; after-source hashes are recorded in the
metadata. The measured source differs only in `native.py`; no new compiled library is involved.

## Recommendation

Retain the binding optimization and the compact Python/PyPy reference. The conditional go for further
native-core research is stronger for built-in policies after removing the avoidable conversion cost.
Hook data access/batching remains a separate design question. Dynamic event/job ownership, a realistic
release policy and production trace encoding remain a proposed next milestone, requiring a separate scope
decision; this study neither implements them nor authorizes a full rewrite.
