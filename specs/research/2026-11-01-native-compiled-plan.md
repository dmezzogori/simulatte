# Compiled scalar binding and feedback producer experiment

Starting revision `7171549`, existing `feature/native-engine-feasibility` worktree, 2026-11-01.
Davide authorized step 1 of the proposed next steps: one compiled scalar binding, original per-job
advancement/admission, and a producer responding to observed simulation state. Production tracing is
a separate subsequent gate. Work inline; no production integration, push or merge.

## Fixed design

Use one minimal Python C API extension and a thin Python owner/lock/JobHandle wrapper. Link the exact
existing dynamic shared library, without recompiling or modifying C engine/header/build or ctypes
binding. Convert one route/duration tuple in C, copy through nd_add, return one ordinary handle, and
materialize detached Python result lists. Preserve RLock serialization and release the GIL for native
create/admit/advance/run/close. Guard raw capsule state against concurrent/reentrant use and ensure
destruction is idempotent. No native array views or raw engine pointers escape the public API.

Alternatives: CFFI API mode offers a different cross-interpreter boundary but introduces a build
dependency; ctypes plus batching is already measured and needs lookahead. Choose C API for this bounded
comparison because both installed interpreters have headers and it adds no dependency. PyPy's cpyext
compatibility path is measured explicitly, not claimed representative of CFFI or all compiled interfaces.
No performance-driven second candidate or changes to the event loop.

Two producer schedules, all scalar:

- Independent: unchanged historical per-job `advance(job.arrival); add(job)` loop on serialized input.
- Feedback: at deterministic controller epochs, advance then inspect the previous handle. Construct
  the next Job at that time with route/duration/due choices depending on that snapshot. A job cannot be
  constructed/admitted before the observation. Include all inspection, decisions and Job construction
  inside timing for every backend. Keep an immutable audit log of observations/decisions, compare it
  exactly, and validate the generated workload independently after timing. This proves a bounded
  controller interaction, not arbitrary SimPy generators, callbacks from C or a general process API.

## Verification and measurement

Before timing: existing suites and new compiled lifecycle/error/ownership/thread tests, exact reference
parity with tied/random workloads and both producers, ASan/UBSan for unchanged engine and compiled
boundary where supported. Check direct extension misuse fails safely; invalid wrapper input remains
usable, native faults fail safely, no leaked borrowed references after results/close. Verify C engine,
shared binary and old sources/evidence stay unchanged.

Freeze sources and three libraries (existing engine plus separate CPython/PyPy extensions), input and
commands. 64 fresh-process cases: two producers x two job counts (5k/50k) x none/record x four backends
(ctypes, compiled, compact reference, tuned) x two interpreters = 64 cases. Five samples, one CPython /
seven PyPy warmups and separate cold/RSS probes, seeded shuffled serial order. Fixed ConWIP cap 12,
SPT, historical load .95 input for independent schedule. Feedback uses the same primitive template input,
with its changed generated workload hashed separately; do not compare its timings as equivalent to the
independent schedule. Parsing/template validation/imports/build outside timing; all execution, buffers,
handles, generated Job construction, decisions, audit-log materialization, detached output and cleanup
inside. Report whole-process RSS, full timing spread, exact fingerprints/records/counters/decisions,
frozen provenance and negative evidence audits. Historical timings remain historical.

Preserve raw evidence separately in compiled-results. Report gains or regressions without further tuning.
Success is trustworthy evidence about scalar interaction, not reaching a preselected speed threshold.

## Completed outcome

250 tests pass on each interpreter with one sanitizer-only skip; separate CPython boundary ASan/UBSan
passes 31 cases, and unchanged engine scalar/bulk 143/144 allocation-failure positions pass. The frozen
64 cases, 320 samples and 64 cold probes pass exact generated-workload/decision/trajectory/record parity,
historical independent parity and eight negative evidence checks. At 50k, compiled CPython improves
ctypes by 2.69–2.91x independent and 1.64–1.68x feedback. Margins over compact PyPy are 4.90–5.15x and
1.56–1.83x. The PyPy C API candidate improves ctypes but loses to compact PyPy for 50k feedback and has higher process
RSS. The [report](2026-11-01-native-compiled.md) records exact medians/ranges, cold boundaries, cpyext
limitations and the remaining production tracing/general-extension gates. No further tuning.
