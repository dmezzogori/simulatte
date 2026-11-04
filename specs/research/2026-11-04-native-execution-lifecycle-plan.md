# Native execution cancellation and failure gate

Davide authorized roadmap step2 on both macOS and Linux, with full and bounded
retention. Work inline in the existing research worktree and a new isolated Linux
checkout. No job cancellation/preemption, modeling extension, identity optimization,
Studio integration, deployment baseline or push/publication.

## Contract and corrections

Validate cooperative cancellation at Python boundaries and bounded supervisor
termination/escalation during actual long native work. Direct in-process native
calls remain synchronous; this gate must not imply an interruptible C API. Require
explicit inside-native evidence, progress before cancellation/failure, terminal
failed/cancelled accounting, no successful result publication, cleaned child trees
and a healthy fresh execution after each fault in the same Coordinator.

Initial five regression tests expose primary-error masking: failed backend close
queries unavailable partial KPIs, and a later cleanup exception replaces the
execution error. Correct only managed close/failure bookkeeping and preserve a
secondary cleanup error as an exception note. Keep native transitions, ABI,
serialization, successful fingerprints and installed-asset verification unchanged.
An additional regression proves a cancellation received during advancement could
run the next Python continuation before being checked. Add the control check
immediately after native advancement; cover both cancellation and deadline expiry.
Real allocation injection also exposed a queued SimPy producer error being masked
by another advance of the failed owner. Deliver same-time failed events before
touching the backend again, with a dedicated regression.
The first FIFO/ConWIP workload returned in about36ms and stopped cooperatively.
Use a large finite SPT queue without a WIP cap to exercise actual escalation, and
retain a short native-call control. This is workload calibration, not a speed study.

## Installed matrix

Use matching pure-core and existing API2 companion wheels outside the checkout on
GIL-enabled CPython3.14/macOS arm64 and Linux x86-64. For each retention mode:

- Successful baseline and source-deleted fresh reproduction.
- Cancellation at a progressed Python boundary; during admission; while a real
  native advance is busy; and with SIGTERM ignored to require SIGKILL escalation.
- Deadline while native advance is busy, distinguished from explicit cancellation.
- Late cancellation after native close but before publication, with controlled release.
- Uncaught producer, callback, invalid-input, post-advance, cleanup and close errors;
  abrupt child exit after progress. Bounded history-commit failure is also tested.
- Three cycles of native-advance abort and producer failure, with a fresh healthy
  request after every failed/cancelled attempt. No stale success or result reuse.
- Private allocation-instrumented provider: mutating native admission OOM after
  progress must poison state; read-only projection OOM must permit retry. Reset the
  injector before close and compare healthy fresh runs with release-wheel results.

Observe actual owner-busy status from a research watcher while the binding releases
the GIL. Record entry/return markers, process identities and descendants. Do not use
sleep as evidence of native execution. Bound long workloads/parent waits and require
process disappearance, not merely the immediately revoked store status. Target
stop-to-cleanup under5seconds (existing escalation at2/3seconds); finite deadline
completion gets an explicit allowance for worker startup. Report all actual samples.

Exercise direct busy-owner/close races, poisoned-state query rejection, invalid
input recovery, repeated close/create/failure recovery and finite RSS/descriptor
stability. Native owner failure does not authorize continued state use; fresh
engines are the recovery path. Cleanup failures must retain original diagnostics.

## Verification and evidence

Run focused regression tests before/after the fix, existing native/execution
checks, full Python coverage gate, lint/type/docs/lock and clean-install/ABI checks
appropriate to the core-only change. Repeat relevant native/provider tests and
failure probes under private ASAN/UBSAN builds on both hosts (Python leak detection
disabled); release binaries remain intact. Keep final matrix sources frozen and
record wheel/source hashes, runtime, instrumented-image provenance, each attempt,
cleanup timing, result-store counts, healthy fingerprints and finite resource checks.
Run one host's behavioral timing without competing local tests/builds. No throughput
claim. Finish with report, signed local commit and Cortex checkpoint. Findings may
require further scoped fixes and complete reruns; retain earlier failed evidence.
Freeze measured/runtime sources per matrix; hash post-run analysis separately.
For managed allocation injection, use private ND_TEST_ALLOCATIONS-only wheels.
ASAN/UBSAN direct/provider probes run separately: on macOS the sanitizer removes
its preload variable at startup and does not propagate into execution grandchildren.
Do not weaken the production launcher or runtime checks to accommodate sanitizers.
