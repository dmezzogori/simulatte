# Native hooks and dynamic ConWIP implementation plan

Execution authorized by Davide's repeated "I agree. Proceed" after the proposed bounded milestones.
Start: `281baa7`, existing native-engine-feasibility worktree. Keep all work on this research branch.
Use one bounded C/binding implementer alongside inline hook/reference/evaluation work, then one independent
read-only reviewer. No push/merge, dependencies, production changes, or full-engine rewrite.

## Design

First compare the legacy immutable tuple callback with an opt-in callback receiving owned read-only
numeric buffers, preserving one invocation per grant and candidate order. Copying into owned bytes
allows retention after callbacks/close without dangling pointers. Borrowed raw views would require a
different lifetime contract; batching/skipping singletons would change stateful policies. Measure one
alternative rather than optimize toward a target. Built-in policies remain a zero-callback control.

Then implement the [dynamic/ConWIP contract](../../prototypes/native_engine_dynamic/CONTRACT.md) with a
separate C library, ctypes binding and independent compact Python baseline. A separate prototype keeps
the already measured static C engine unchanged. Registration while earlier jobs execute, geometric
storage growth, opaque stable handles, temporary calendar draining, and cap/EDD release semantics are
the tested boundary. Complete Studio trace encoding remains a separate subsequent gate.

## Tasks

- [x] Hook tests first: owned read-only candidate retention, stateful once-per-grant ordering, FIFO/SPT
  parity including exceptions, invalid choice/overflow and lifecycle reentrancy. Implement opt-in
  `hook_style="buffers"` in static native.py; default tuple behavior stays unchanged. Mirror equivalent
  callback data access in a Python baseline for fair comparison. Do not change static C or old evidence.
- [x] Dynamic C implementer owns only `native_engine_dynamic/{csrc,build.py,native.py}` and native/sanitizer
  tests. Implement the contract with tests first, checked C sizes/counts, allocation-failure-safe growth,
  correct event ordering and explicit NDYN recording. Parent owns reference/validation/benchmark/docs.
- [x] Parent implements independent ReferenceStream and recording/invariant validation. Hand-derived
  cap1/EDD example: three jobs at 0, durations 3/2/1, due 10/9/1; releases 0/4/3, starts 0/4/3, finishes
  3/6/4. Cap and floor WIP apply in none mode too. Test registration partitions and growth/handle safety.
- [x] Build both C libraries, run old/new suites on CPython/PyPy, sanitizers and lint. Compare dynamic
  trajectories/releases/records against reference on randomized fractional/tied cases and, for an
  appropriate ordinary fixture, production ConWIP behavior. Fix discrepancies before measurements.
- [x] Freeze sources and measure serially in fresh processes, 5 samples, CPython1/PyPy7 warmups, separate
  cold/RSS probes. Hook study: 50k/.95/SPT, none/record, native built-in/tuple/buffers, reference built-in/
  tuple/buffers and tuned built-in/tuple, both interpreters (32 cases). Dynamic study: 5k and 50k/.95/SPT,
  none/record, cap None/12, native/reference/tuned on both interpreters (48 cases), identical streamed
  workload and all registration/cleanup inside timing. Stronger controls avoid crediting native built-ins
  merely for eliminating Python policy calls that the compact baselines could eliminate too.
  Measure alternative feed partition as a correctness check; no unfair semantic comparison.
- [x] Audit hashes/counters/equivalence/coverage, write reports with actual ratios and uncertainty,
  independent review and one fix pass, verify scope, commit locally and checkpoint Cortex. Preserve
  intermediate evidence if a correctness repair requires a fresh matrix. State remaining architecture
  gates: richer Python extensions, slot recycling/cancellation, Studio traces, wheels/platforms.

## Review focus

Buffer ownership after callback and close; hidden skipped/batched policy calls; append/growth failures;
stable handles and foreign-owner rejection; arrival/completion tie order; ConWIP floor vs system WIP;
same input source and registration costs across interpreters/backends; explicitly restricted trace format.


## Completed outcome

190 prototype tests pass on each interpreter;10 existing ConWIP tests and both sanitizer harnesses pass. One independent review repaired two evidence checks before timing; the same reviewer verified final calculations and limits without further findings. Frozen matrix:80 cases,400 timed samples,80 cold/RSS probes, exact trajectories/records and provenance audited. Dynamic CPython native gives2.97–3.80x vs best compact CPython and1.79–2.24x vs best compact PyPy. Buffer hooks give1.06–1.07x on CPython but0.77–0.78x on PyPy; tuple stays default. The [report](2026-11-01-native-hooks-dynamic.md) records the narrower dynamic margin and remaining boundary/trace gates.
