# Verified immutable native-binary loading

The experiment passes on bigboss/macOS: reusing a verified immutable binary path
reduces whole-workflow median time by **6.16–12.39%** against an equally checked
per-attempt-path control. All fresh-process, state/KPI, artifact and reproduction
checks pass. Warm imports fall from approximately 93 ms to 0.43 ms per process;
the first shared-path import still costs about 92 ms. The benefit comes from
reusing binary files, with fresh simulation state in every child.

This completes the [authorized plan](2026-11-03-native-immutable-loading-plan.md),
following the [phase profile](2026-11-02-native-phase-profile.md). It is a
fixture-local research implementation, not an installed production cache. Native
transitions, repository production Python, existing backend APIs and default
research loading remain unchanged.

## Loader and provenance contract

The new research helper publishes a pair consisting of the Python extension and
its linked dynamic engine. A SHA-256 address covers their exact names/content
hashes, interpreter implementation/ABI and platform. Publication stages the pair,
checks it, marks files/entry read-only, then renames the entry atomically. Existing
entries must validate; loading never repairs or silently falls back from an invalid
entry. The fixture has one publisher; production concurrent-cache lifecycle is
not established here.

Every load checks the snapshotted pair against its native asset manifest, then
checks the selected pair before import. Shared-entry validation rejects missing,
corrupted, writable, symlinked, mismatched or unexpected files. Source-file
symlinks are also rejected. Both selected binaries are checked again after import,
with file identities compared across loading. The actual extension origin and
mapped engine-library path must agree with the chosen location. Shared binary
file identities remain constant across the timing matrix.

The existing import-origin guard initially rejected a shared extension outside its
declared roots. The corrected research configuration declares only the addressed
entry as an external root. It retains the real cached origin in resolved execution
provenance; it does not rewrite the origin to pretend that loading occurred from
the snapshot. Expected native bytes remain part of the source snapshot/request,
and the loader verifies that the external pair matches them. Ordinary runtime
verification and publication checks remain enabled.

Checked disposable runtime/project instrumentation supplies the location through
private research child configuration. Both path choices have identical code,
source and runtime identities. The only selection difference is the binary
location. Timing/path evidence goes to diagnostic sidecars outside traces and
artifacts. Normal request IDs, semantic fingerprints, backend state and deterministic
artifacts match across choices. The application adapter continues to use the
explicit managed backend lifecycle.

Read-only modes and pre/post checks detect ordinary mutation; this is not a
security boundary against malicious same-user native-code attacks. The evidence
establishes this local interpreter/platform and the existing restricted native
production slice. No broader actor coverage, remote loader, package/release or
full native tracing is added.

## Correctness and ownership gate

The frozen gate covers both binary locations, independent/feedback producers,
FIFO/SPT, two seeds and Horizon 10/warm-up 2. Its 32 main runs form 16 matched
path pairs, with exact shared results, ordinary requests, native/domain state,
fingerprints and deterministic artifact hashes. Each case also reproduces both
backends in fresh children while selecting the other native path: 16 cross-path
reproductions, including eight native loading-path changes.

Two readiness cancellations, one per path, stop before backend advancement and
publish no result. Missing and corrupted shared entries fail before native import
and publish no result. These use the normal managed coordinator/control path.
Fifteen focused loader tests cover publication/reuse, invalid entries, source
mutation/symlinks, unexpected imported origins/engine images, changes during
import, no fallback/repair and diagnostic separation.

Eight additional runs using the original runtime/loader cover both producers and
backends. They match the shared results, backend state and ordinary semantic
fingerprints. Their source hashes and request IDs differ with the actual source
copies, as expected. Original-loader controls are correctness evidence, not a new
throughput baseline.

The harness's initial cancellation audit accidentally nested SQLite transactions;
that harness error was corrected before freezing the final gate and timing matrix.
The draft runs are kept only under ignored `.build/`. No production-store change
was made.

## Whole-workflow result

bigboss, macOS arm64, CPython 3.14.3. The same supported slice uses ten unit servers,
SPT, EDD ConWIP cap 12, load 0.95, Horizon 30000/warm-up 500 and seeds 100–102.
Optional proof generation is off for all timed runs. Both choices still verify
the source/selected binaries, inspect actual loaded images and record low-volume
loader diagnostics.

There are 48 randomized fresh worker processes: two locations × two phase-probe
modes × two producers × 5k/20k jobs × three repeats. Each experiment executes three
ordinary managed replications: **144 timed replications in 144 distinct native
child processes**. Per-attempt source paths are distinct; the immutable pair's
path and file identities are shared. No native image is loaded during fixture
construction. The first shared-path use is retained in the matrix.

Outer wall time includes interpreter/import startup, ordinary runtime/source
preparation and inspection, input generation, fresh children, readiness,
simulation, state fingerprints, per-load verification/diagnostics, transfer,
publication, reporting and cleanup. One-time compilation and fixture copying
precede timing; deletion of retained stores follows timing. Store construction
takes **1.23 ms**, measured separately outside workflow timing. Regression checks
and compilation did not run concurrently with the timed matrix.

Primary results use **dormant full phase probes**. The small loader diagnostics
remain enabled in both choices and included in wall time. Seconds per experiment
of **three replications**, median (min–max), three repeats. The 20k row executes
60k jobs in total.

| Producer | Jobs per replication | Checked per-attempt path | Immutable path | Time reduction |
| --- | ---: | ---: | ---: | ---: |
| independent | 5,000 | 2.551 (2.443–2.585) | 2.243 (2.231–2.345) | **12.08%** |
| independent | 20,000 | 2.983 (2.982–3.160) | 2.799 (2.757–2.825) | **6.16%** |
| feedback | 5,000 | 2.592 (2.590–2.594) | 2.271 (2.256–2.383) | **12.39%** |
| feedback | 20,000 | 3.228 (3.204–3.311) | 2.984 (2.983–3.015) | **7.56%** |

The fully active phase-probe controls also improve: 9.00%, 7.48%, 8.47% and 6.86%
for the corresponding rows. All eight producer/size/probe groups have nonoverlapping
observed timing ranges between locations. This is consistent local evidence,
not portable confidence bounds: there are only three repeats per group on one
host. The checked per-attempt control includes the new helper's pre/post checks
and diagnostics, so it is not byte-for-byte the pristine loader from the previous
report. The controlled comparison isolates location with the same helper and
verification work in both choices. Do not combine timings from earlier sessions
to infer a new production-versus-native speedup.

## Warm loading and first use

Across the matrix, 72 per-attempt imports have a median of **93.33 ms**. Of the 72
shared-path imports, the first costs **92.15 ms** and the remaining 71 range from
**0.414 to 0.514 ms**, median **0.435 ms**. Each is a new interpreter process.
This is first use of the shared path in the fixture; it is not a guarantee of an
OS-wide cold filesystem/code cache. No first-use sample was dropped or preloaded
outside measurement.

For three-replication experiments with dormant phase probes:

| Producer/size | Import only, per-attempt | Import only, immutable | Verified load total, per-attempt | Verified load total, immutable |
| --- | ---: | ---: | ---: | ---: |
| independent 5k | 283.1 ms | 1.30 ms | 288.7 ms | 5.25 ms |
| independent 20k | 283.2 ms | 1.32 ms | 288.2 ms | 5.22 ms |
| feedback 5k | 283.4 ms | 1.33 ms | 288.9 ms | 5.16 ms |
| feedback 20k | 272.4 ms | 1.33 ms | 277.2 ms | 5.45 ms |

These are median sums over three execution children, not per-process values.
Verified-load totals include pre/post hash and identity checks and actual-image
inspection; their JSON sidecar write is outside that narrow interval but inside
outer workflow time. The first shared-path experiment uses active probes and
includes its expensive first import. A one-off workflow should therefore not be
expected to receive the fully warm saving on its first replication.

The path/inode reuse clearly removes most repeated import cost in this experiment.
The specific OS/linker/verification-cache mechanism was not isolated. Larger
workloads retain more input, state and Python-control work, so the fixed loading
saving accounts for a smaller percentage of their workflow. A fast warm import
does not imply an equally large simulation speedup.

## Decision and next step

Retain verified immutable loading as a successful research result. The measured
whole-workflow benefit is consistent across both producers, both sizes and both
probe modes, while normal integrity/reproduction checks hold. Production Python
and native C remain untouched; the experiment has not installed a general cache
or changed the default loader.

Proposed, not adopted: integrate a **declared native-asset bundle** into managed
preparation and pass its verified immutable paths to the opt-in backend through
an explicit interface. That replaces this experiment's private configuration and
environment variables. The coordinator would own publication, invalidation and
cleanup, with concurrency/crash behavior and platform checks completed before
production adoption. Preserve the binary content checks, actual-origin evidence,
fresh-process rule and current restricted backend capability boundary.

## Verification and evidence

- Backend/prototype regression suites: **529 passed, one skipped**, including all
  15 new loader tests. Ruff passes for the helper/harness/tests.
- 32 gate runs, 16 cross-path reproductions, two readiness cancellations, two
  fail-closed cache runs, eight original-loader controls and 144 timed replications.
- Independent audit checks all gate path pairs, mixed-path native reproduction
  observations, original controls, cancellation/failure accounting, 144 actual
  native origins and retained first-use evidence. Final gate/matrix disposable
  runtime and project file manifests match exactly.
- Root audit rejects six corruptions: ordinary fingerprint, artifact hash, backend
  state, KPIs, mapped images and expected binary hashes.
- Production Python and native C match `5c16f6f` byte for byte; 41 historical
  evidence files are unchanged. Default native-library hashes remain unchanged.

Frozen source manifest: **285 files**, SHA-256
`83254469619c51bdc9691a8aeb91077d7a05b9806c3dc0c52f2a209252132f43`.
Timing matrix SHA-256:
`323736ada4d74338d022838eddae1dd0c78998da39d1a820b9ea60afefc780b1`.
Complete audit SHA-256:
`669e7fd37fee5afd2e40872ac5dd45b71916d902ecb2fc771a2d0c9c162921b0`.
Metadata records the exact binary hashes/address and final disposable project and
runtime manifests. The report and generated evidence are outside the measured
source manifest.

Evidence: [gate](../../prototypes/native_engine_dynamic/loading-results/gate/verification.json),
[gate runs](../../prototypes/native_engine_dynamic/loading-results/gate/runs.json),
[negative runs](../../prototypes/native_engine_dynamic/loading-results/gate/negatives.json),
[raw matrix](../../prototypes/native_engine_dynamic/loading-results/scaling/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/loading-results/scaling/summary.json),
[first-use analysis](../../prototypes/native_engine_dynamic/loading-results/analysis.json),
[original controls](../../prototypes/native_engine_dynamic/loading-results/original-controls.json),
[independent audit](../../prototypes/native_engine_dynamic/loading-results/independent-audit.json),
[checks](../../prototypes/native_engine_dynamic/loading-results/checks.json),
[complete audit](../../prototypes/native_engine_dynamic/loading-results/verification.json).

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.loading_experiment gate --output /tmp/native-loading/gate
.venv/bin/python -m prototypes.native_engine_dynamic.loading_experiment matrix --output /tmp/native-loading/scaling
.venv/bin/python -m prototypes.native_engine_dynamic.loading_experiment audit-experiment --output /tmp/native-loading
```

Original-control and independent-audit scripts are preserved with the evidence.
Their records and the derived first-use analysis are hashed by the root audit;
unit tests, original controls and analysis generation are not rerun by that audit.
Cortex was unavailable during this work; memory checkpoints are saved in the
documented local fallback rather than claimed as applied in Cortex.
