# Native-engine binding optimization plan

Status: implementation, independent review/fix, measurement and report complete on 2026-11-01.
Execution: work inline in the existing `feature/native-engine-feasibility` worktree. This plan does not
request delegation or authorize pushing, merging, or beginning the next native-engine milestone.

**Goal:** measure how much of the native prototype's lifecycle cost can be removed by optimizing its
Python binding while preserving its input, output, callback, and ownership contracts.

**Architecture:** keep the C17 engine and ctypes ABI unchanged. Lower validated inputs into standard-library
contiguous buffers and extract results through bulk copies into temporary buffers, returning the same
detached Python lists as today. Retain checked handling of duck-typed inputs.

**Tech stack:** Python 3.11+, `array`, `ctypes`, pytest, existing C17 build and fresh-process benchmark driver;
CPython 3.14.3 and PyPy 3.11.15 / 7.3.22 for historical comparability where available.

**Sources:** [contract](../../prototypes/native_engine/CONTRACT.md),
[original report](2026-10-15-native-engine-prototype.md),
[optimization ledger](../../prototypes/native_engine/OPTIMIZATIONS.md), and Cortex review
`capture-e76aec07-d100-4b84-9361-b3cf4ab8c399` (review performed October 15, saved November 1).

## Design and boundaries

Recommended approach: retain ctypes and use `array.array` plus `from_buffer` for input, and `memmove`
followed by `array.tolist()` for output. This tests the review's hypothesis without introducing a dependency
or changing the C interface. A compiled Python extension would also affect interpreter support and callback
costs; borrowed output views would change the detached-list contract. Both belong to separate investigations.

- Only `prototypes/native_engine/native.py` changes executable simulation/binding behavior. Leave
  `csrc/`, build flags, `model.py`, all other runners, benchmark timing code, and validation semantics unchanged.
- Preserve `NativeRunner(workload, *, policy='fifo', mode='none', hook=None, trace_path=None)`, its lifecycle,
  options/errors, hook calls and candidates, recording format, and result dictionary/list types.
- Fast lowering applies only to an exact `model.Workload` containing exact `model.Job` objects. These
  objects have already normalized numeric data and validated route bounds/lengths. Subclasses and existing
  duck-typed inputs use the checked path, including `_number`, `_count`, length and operation-count checks.
  Deliberately bypassing frozen dataclass invariants is outside the validated-input guarantee.
- Keep top-level native count checks, the reserved `0xffffffff` limit, and C-side validation. Never narrow
  an unchecked signed or oversized integer into an unsigned buffer.
- Inputs remain copies owned by C after `ne_create`; Python buffers and their ctypes views stay alive until
  that call returns. No prepared-input cache, reusable result cache, or work moved outside lifecycle timing.
- Result extraction remains under the runner lock and returns newly allocated lists on every call. No
  Python object may retain a borrowed C pointer after extraction or close.
- Keep callbacks unchanged, including candidate construction, membership checks, and exceptions. No event
  algorithms, dynamic jobs, release policies, Studio tracing, packaging, or production changes.
- Treat this as one additional measured optimization iteration. Keep original evidence immutable; stop and
  report the measured result even if it misses the review's estimate.

## Review focus

1. Checked inputs: booleans, malformed lengths/counts, and oversized/negative IDs must still fail before
   unsigned conversion can hide the error; existing duck-typed tests must remain useful.
2. Empty workloads: zero-length input/output buffers must not trigger dereferences or invalid copies.
3. Buffer layout: verify `array('d').itemsize == sizeof(c_double) == 8` and
   `array('I').itemsize == sizeof(c_uint32) == 4` before using those buffers; fail clearly on an incompatible ABI.
4. Ownership: dropping input temporaries, collecting garbage, closing runners, and mutating one result must
   not invalidate or change another result.
5. Equivalent work: list creation, validation still required at the boundary, cleanup, and hooked execution
   stay measured; performance assertions do not belong in pytest.

## Task 1: Establish a fresh comparison baseline

**Files:** create `prototypes/native_engine/results/binding-before.jsonl` and
`prototypes/native_engine/results/binding-metadata.json`.

**Interface:** existing `benchmark matrix` JSONL schema; metadata records before/after provenance separately.

- [x] Confirm the worktree is still based on `2c2dfd2` or record any intervening changes; inspect its status
  and preserve unrelated edits. Refresh the moved virtualenv with `uv sync --python 3.14.3 --dev --all-extras`.
  Repair/recreate the local PyPy environment if its old worktree path is stale; record exact versions.
- [x] Build the release library and run the existing prototype tests on both interpreters. Record the
  observed count; 81 passing tests per interpreter is the historical baseline, not a fresh result.
- [x] Reuse existing generated inputs if hashes match `results/metadata.json`. Otherwise regenerate using
  pinned CPython and verify hashes before measurement. In particular, the 50k/.95 input SHA256 is
  `2e45f12f28e425c4614aff714ceb35902bb64ced1b1878dde82e5b086926565b`.
- [x] Record git revision/status, aggregate source hash, individual binding/C/reference/tuned source hashes,
  library/input hashes, OS/hardware, compiler/flags, interpreters/dependencies, and exact commands. Build the
  release library once and use that same binary for before and after timings.
- [x] Run the following matrix serially, without concurrent builds/tests or benchmark jobs. Require a new,
  empty output file; never append to historical evidence or a file with different source hashes.

```sh
.venv/bin/python -m prototypes.native_engine.benchmark matrix \
  --output prototypes/native_engine/results/binding-before.jsonl \
  --jobs 50000 --loads 0.95 --policies spt \
  --modes none record --hooks builtin python \
  --backends reference tuned native \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python \
  --pypy-backends reference tuned native \
  --warmups 1 --pypy-warmups 7 --repeats 5
```

This is the original 16-case validation subset plus eight tuned-Python controls: 24 cases, 120 measured
samples, and 24 separate cold time/RSS probes. No `--skip-memory`. Common input parsing/model validation
stays outside timing; build, run, detached-list extraction, close and explicit cyclic collection stay inside.

## Task 2: Optimize input lowering

**Files:** modify `prototypes/native_engine/native.py` and `prototypes/native_engine/tests/test_native.py`.
**Interface:** unchanged constructor and `_Input` ABI; all temporary storage is constructor-local.

- [x] Extend the existing native boundary tests, sized like their neighbors. Add a parameterized
  `test_typed_and_checked_workloads_match` for FIFO/SPT and none/kpi/record: construct equivalent real
  `Job`/`Workload` and current `SimpleNamespace` inputs, and require identical results and recording bytes.
  Include a `Job` or `Workload` subclass on the checked path. These characterize behavior and can pass before
  optimization; do not manufacture failures or assert private helper call counts.
- [x] Extend `test_invalid_input` for bool times/durations, negative and oversized route IDs, and reserved
  or oversized counts; retain `test_bad_paths_and_metadata` for inconsistent operation counts. Use tiny
  malformed objects, without allocating huge workloads. Run the focused tests before editing the binding.
- [x] Add the validated-input branch described above to `NativeRunner.__init__`. Build arrivals/due/durations
  as `array('d')` and offsets/routes as `array('I')`, without per-value `_number`/`_count` revalidation on that
  branch. Accumulate flattened offsets/routes/durations directly; do not build the unused owners array from
  `model.flatten()`. Preserve per-element checking on the generic path, which may use the same final buffers.
- [x] Verify native buffer widths; create ctypes views with `from_buffer` rather than splatting Python
  sequences into ctypes arrays. Keep buffers and views strongly referenced through `ne_create`.
- [x] Extend `test_empty_and_lifecycle` to real empty `Workload` input. Add an ownership case that constructs
  from a temporary workload, runs garbage collection before `run()`, and checks exact expected results.
  Run the native tests and then the complete prototype suite under both interpreters.

## Task 3: Optimize extraction while preserving lists

**Files:** modify `prototypes/native_engine/native.py` and `prototypes/native_engine/tests/test_native.py`.
**Interface:** unchanged `result()` dictionary, with float lists for starts/ends/finished/busy and an int list
for dispatch. A small private copy helper may be shared by these five fields; no public buffer API.

- [x] Add `test_result_lists_survive_close_and_are_independent`, parameterized over modes. Assert the exact
  list types, retain two results, mutate each array field in one, and verify the other and a fresh result
  remain correct. Close and collect the runner, then verify the retained lists again. Exercise typed empty
  results too. Existing fractional/randomized parity tests cover bit-exact trajectory preservation.
- [x] Replace per-element ctypes slicing with `memmove` into correctly sized, owned `array` buffers and call
  `.tolist()` before returning. Use the double/uint32 width checks from Task 2 and skip pointer access and
  copying entirely when the requested length is zero. Preserve scalar/KPI reads and locking/error guards.
- [x] Run both complete prototype suites, including randomized binary parity, hook errors/reentrancy,
  recording failures, overflow, and lifecycle tests. Then run the standalone ASan/UBSan harness and Ruff.

```sh
.venv/bin/python -m pytest -o addopts='' prototypes/native_engine/tests -q
prototypes/native_engine/.build/pypy-env/bin/python -m pytest -o addopts='' prototypes/native_engine/tests -q
.venv/bin/python -m prototypes.native_engine.build --sanitize
prototypes/native_engine/.build/native_sanitizer prototypes/native_engine/.build/sanitizer-trace.bin
.venv/bin/ruff check prototypes/native_engine
.venv/bin/ruff format --check prototypes/native_engine
git diff --check
```

The sanitizer command builds a separate executable; retain the unchanged release library for timings.
Do not rerun the production suite solely for this isolated change; verify the production diff is empty.

## Task 4: Remeasure and write the evidence-backed conclusion

**Files:** create `prototypes/native_engine/results/binding-after.jsonl`, `binding-summary.csv`, and
`binding-verification.json`; extend `binding-metadata.json`. Create the reproducible analysis script
`specs/research/native-engine-data/binding_comparison.py` and report
`specs/research/2026-11-01-native-engine-bindings.md`. Update prototype `README.md` and `OPTIMIZATIONS.md`.

**Analysis interface:** `binding_comparison.py --before PATH --after PATH --csv PATH --audit PATH` consumes
the existing JSONL schema, writes phase medians/min/max and ratios to CSV, writes coverage/equivalence checks
to JSON, and prints a Markdown comparison table. Reuse `analyze.summaries()` and `benchmark.assert_equivalent()`;
do not use the existing analysis CLI, which assumes Simulatte rows that this reduced matrix does not contain.

- [x] Freeze the binding implementation and record its source hashes/status. Verify C sources, ABI, release
  binary, other runners, driver, validators, and inputs match the before measurement. Rerun the Task 1 matrix
  with only the output changed to `prototypes/native_engine/results/binding-after.jsonl`.
- [x] Implement the small analysis script with audit assertions: exactly the expected 24 unique cases per
  file, five samples and a separate cold probe per case; matching case/input sets; one source hash per file;
  exact trajectory/record hashes across equivalent cases and before/after; unchanged completed/event/callback/
  record counts, makespan and record byte sizes for corresponding cases. Retain existing independent
  invariant/KPI/record replay validation from the benchmark driver. State metadata mismatches as failures.
- [x] Report before/after medians for build, run, extract, close and total, sample min/max, throughput, cold
  timings and absolute peak RSS. Derive each speedup from median lifecycle totals, not summed phase medians.
  For each mode and hook setting, compare CPython-native against the faster freshly measured PyPy reference
  or tuned runner. Keep hooked and built-in results separate. Inspect unchanged control timings for drift;
  if drift obscures the effect, repeat the affected controls/cases serially and retain both runs.
- [x] Write the new report and reproduction commands, and append this iteration to the ledger. Explain
  that extraction still creates Python lists: the review's 0.2 ms bulk-copy probe excluded that materialization,
  so 35–40 ms and approximately 8x are hypotheses, not acceptance thresholds. New conclusions cover only
  50k/.95/SPT; leave the original 384-case report/numbers intact and link the follow-up from the README.
- [x] Check documentation links, `git diff --check`, and final scope against the starting revision: executable
  changes are limited to the binding and analysis script, plus neighboring tests and research documentation/
  evidence. Confirm production files, C sources and other runners remain unchanged. Record actual check
  results and any environment mismatch in the audit and Cortex checkpoint. Do not claim the next native
  milestone or a complete rewrite is authorized by these results.

## Completion criteria

Behavioral compatibility, both-interpreter prototype tests, sanitizers, lint and scope checks pass; all
before/after hashes and counters agree; fresh equivalent-work timings quantify the actual change. Report
whether the bindings materially improve native lifecycle time and how the comparison with PyPy changes.
A smaller gain or regression is a valid study outcome and must be reported without expanding scope.

Deliver the binding diff, fresh raw evidence, reproducible summary, concise report and Cortex checkpoint.
No full 384-case rerun, new performance target, callback optimization, push or merge is part of this plan.

## Execution outcome

See the [follow-up report](2026-11-01-native-engine-bindings.md). One independent review identified a malformed-container length/count regression; four tests failed before the fix and passed afterward. Both final suites pass 111 tests. The corrected candidate was measured again; its original samples and source are retained separately. Built-in CPython lifecycle improves 3.07x / 2.85x (none/record); the comparison to the fastest compact PyPy baseline is 6.89x / 7.02x. Hooks remain 0.92x / 1.02x. No expansion beyond the binding or this representative workload.
