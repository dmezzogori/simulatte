# Native execution cancellation and failure behavior

Roadmap step 2 passes for the managed experimental backend on macOS and Linux,
with both full and bounded retention. The gate found and corrected four lifecycle
problems in the Python execution layer. Native transitions, C/provider sources,
ABI, serialization and the native public API remain unchanged.

This is whole-run cancellation through managed execution. Direct in-process C
calls remain synchronous; no native cancellation token, per-job cancellation,
preemption or interrupted-state restoration was added.

## Corrections

1. **Failed close sampled unavailable partial KPIs.** `BackendBinding.close()` now
   skips KPI capture after an execution failure and still closes its owned backend.
   Queries after failed close do not expose a cached successful result.
2. **Cleanup could replace the primary error.** The managed driver now retains the
   execution/producer error and attaches a later Environment cleanup error as an
   exception note. Its failure flag also covers errors before the stopping loop.
3. **Cancellation during C advancement could run another Python callback.** The
   stopping loop now checks cancellation/deadline immediately after advancement,
   before `env.step()` can resume the next model process.
4. **A queued producer OOM could be masked by another failed-owner advance.**
   SimPy queues a failed process at the current time. The stopping loop delivers
   that failed event before touching an owner the producer may have poisoned.

Five initial cleanup/error regressions failed before the changes. A separate
cancellation-continuation regression and an actual allocation-fault investigation
exposed the remaining ordering problems. The final focused backend/driver/native
diagnostic suite passes 64 tests, including cancellation and deadline variants.

## Installed behavioral gate

Both hosts use matching clean-installed local core/provider wheels outside the
checkout, GIL-enabled CPython, and no compiler on the runtime path:

| Host | Platform | Python | Release attempts | Private fault attempts |
| --- | --- | --- | ---: | ---: |
| bigboss | macOS 27.2 arm64 | 3.14.3 | 78 | 8 |
| strangelove | Linux 7.0.0-42 x86-64 | 3.14.0 | 78 | 8 |

Every case completes a native job before its fault or cancellation. A research
watcher verifies the actual binding owner is busy while C work has released the
GIL, and verifies that a raw close is rejected while busy. Long native work uses
a finite SPT queue without a WIP cap; short work uses FIFO/ConWIP. The deliberately
large live queue is not a bounded-memory streaming claim.

For each retention mode the release gate covers:

- Cooperative cancellation at a progressed Python boundary, cancellation during
  a short native call and during Python admission, and a genuinely long C advance.
- SIGTERM escalation, then a SIGTERM-ignored case requiring SIGKILL. The latter
  also spawns an ordinary descendant; both process identities must disappear.
- A wall deadline during actual native advancement, classified as failed/timeout
  rather than explicit cancellation.
- Cancellation after the native owner has closed but before publication, followed
  by controlled release of that boundary. The cancelled attempt still cannot publish.
- Producer, input callback, invalid input, post-advance, primary-close and
  secondary-cleanup errors, plus abrupt child exit after native progress.
- Bounded completion-history commit failure after native mutation.
- Three native-advance abort cycles and three producer-failure cycles, each
  followed by a healthy fresh child in the same Coordinator.
- A baseline and source-deleted reproduction. Reproduction uses the captured
  ordinary source/runtime contract, not an interrupted native checkpoint.

Private `ND_TEST_ALLOCATIONS` wheels add real mutating admission OOM after earlier
work completed, followed by a matching healthy fresh execution and reproduction.
These builds retain strict stock arithmetic, normal installed-asset checks and
the same source manifest. Release wheels are not modified.

The final matrix contains **172 unique attempts**: **94 succeeded, 32 cancelled,
46 failed**. The 94 successes include eight source-deleted reproductions. Every
bad attempt is followed by a matching healthy fresh run. Healthy fingerprints and
artifacts are exact within each retention mode across both hosts and release/fault
builds; physical KPIs agree across retention modes.

A separate read-only SQL audit of all four retained attempt databases verifies
the recorded terminal states, exactly 94 published successes, and **zero published
results for all 78 failed/cancelled attempts**. Timing ends only after the slot is
released and the recorded child/descendant PIDs disappear, not at the store's
immediate cancellation revocation.

## Observed stop responsiveness

Seconds from explicit cancellation to actual cleanup; ranges include both
retention modes and repeated cases. These are finite observations, not universal
latency guarantees for other machines or arbitrary model behavior.

| Stop path | macOS | Linux |
| --- | ---: | ---: |
| Cooperative Python boundary | 0.101–0.109 | 0.092–0.102 |
| Short native call returns, then cancellation is checked | 0.121–0.126 | 0.122–0.133 |
| Long native call, SIGTERM required | 2.062–2.108 | 2.072–2.083 |
| SIGTERM ignored, SIGKILL required | 3.065–3.110 | 3.083–3.089 |

Every explicit cancellation case, including admission and late publication,
cleans up within the declared five-second gate. The native-busy deadline cases
have a three-second attempt budget and clean up about 5.10–5.14 seconds after
submission, approximately 2.1 seconds after the deadline. They publish no result.

Forced termination deliberately does not run Python finalizers: the long-call
cases have neither a native-return marker nor a Python-close marker. OS process
cleanup releases that execution's memory. The healthy follow-up runs verify that
the same Coordinator/worker can execute again without accepting stale output.

Across the fixed release matrix, parent RSS spans 43.91–51.33 MiB on macOS and
39.38–43.35 MiB on Linux, inside the declared 16 MiB finite-range gate. Parent
descriptor counts remain exactly 11 and 13 respectively. This is repeated-lifecycle
evidence, not an indefinite no-leak proof or a bound on user-retained histories.

## Fault and sanitizer checks

Each host runs 100 cycles per retention mode for each of:

- Read-only projection allocation failure, then an exact state-byte retry.
- Invalid advance input, then an exact state-byte retry.
- Mutating admission allocation failure, followed by rejection of state queries
  and safe repeated close.

The same direct probes run under private ASAN/UBSAN images, including a real
busy-owner close race that completes safely afterward. Across both hosts this is
400 cycles of each condition in the ordinary fault builds, repeated under
sanitizers, plus four busy-close races in each probe family. Existing provider
sanitizer suites pass 123 tests on each host. Leak detection for Python is disabled;
these results do not establish Python leak freedom.

Managed fault injection uses allocation-test-only wheels. On macOS, ASAN removes
its preload environment on startup, so execution grandchildren otherwise load it
too late for interceptors. The launcher was not weakened to accommodate that
instrumentation. Standalone sanitizer probes run in one explicitly instrumented
process. The private macOS debug-symbol bundle was moved outside the installed
`_bin` directory so the probe selects the actual dylib; active image bytes and
RECORD hashes did not change.

## Other verification and provenance

- Full macOS suite: **3,659 passed, one skipped, three existing dependency warnings**
  in 366.24 seconds; **99.03% combined statement/branch coverage**, retaining the
  99% gate (statement coverage 99.39%, branch coverage 98.00%).
- Linux focused execution/native suite: **206 passed** in 13.77 seconds.
- Installed-wheel regression check equivalent to the new native CI step:
  **four passed** in 5.26 seconds on macOS. CI now runs these regressions in genuine
  copy-installed environments in its existing macOS/Linux CPython 3.11–3.15 matrix.
  Those other ABI lanes were configured, not executed locally by this task.
- Clean-install/ABI/integrity gates pass on both hosts, including ordinary and
  identity-helper reproduction. Separately from the 172 lifecycle attempts, these
  contribute 40 successful managed attempts, 16 source-deleted reproductions,
  32 direct model oracle runs and 14 negative install/ABI/integrity checks,
  including two failed managed runtime-mutation probes.
- Source/test/native/research lint, formatting for those scopes, `ty check src`,
  lockfile check, docs build and CI YAML validation pass. The pre-existing 12
  historical research-formatting findings remain untouched; this is not a clean
  whole-repository formatting claim.
- The four final lifecycle matrices share a 177-file runtime/measurement freeze:
  `8a4b4dc4e192561498c1e8f4da0e8543caf6d8cbe81efc3baaec4c79e5acc754`.
  Post-run analysis/exporters and CI are audited separately. The shared pure core
  wheel is `4c8b3443e44415dd0b70d44b39e5b40f395efa898b0659b69bdd8dbc000e5272`.
  Companion release images are unchanged; private fault/sanitizer provenance and
  every attempt/marker/status are retained under `prototypes/native_execution_lifecycle/results/`.
- Of 4,791 base files, 4,785 remain unchanged. The six edits are the three execution
  modules, neighboring backend tests, native guide and native CI job. Native C,
  provider runtime, identity implementation and all historical evidence remain exact.

Draft evidence is retained: the short FIFO workload initially returned before
forced termination; the private-runtime runner resolved a venv symlink incorrectly;
macOS sanitizer inheritance required separating managed fault and direct sanitizer
checks; and real OOM exposed the SimPy error-ordering bug. Full release matrices
were rerun after the final runtime corrections. An exploratory `uv run --with`
regression invocation failed worker startup; explicit copy-installed venvs pass and
are used by CI. No ephemeral-environment support change was made.

## Roadmap status and limits

This closes step 2 for the declared managed production slice. Cancellation is
cooperative at Python/native return boundaries, with process escalation for a
stuck native call. Direct `NativeStream` calls have no cancellation token/deadline,
and concurrent high-level close waits for ownership. Interrupted or poisoned state
must not be resumed; create a fresh engine. Ordinary descendant containment is not
a sandbox for deliberately escaped processes or arbitrary native code.

Step 3 remains next: select and validate one meaningful modeling capability against
the Python oracle. No capability was added here. Step 4 remains partial, and the
bytes-consuming observer optimization stays deferred. Native v3 remains research
after Studio v2; no push, release, deployment baseline or production promotion is
included.
