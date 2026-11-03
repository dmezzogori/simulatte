# Declared native assets in managed execution

The integration gate passes. Models can now declare captured native bundles and
registered backends receive verified handles through `BackendContext.native_assets`.
The ordinary coordinator and run store own publication, leases and manual pruning.
The research provider uses this interface without private coordinator configuration,
environment-variable selection or an instrumented copy of the execution runtime.

On bigboss (macOS arm64, CPython 3.14.3), this preserves exact results and reduces
median whole-workflow time by 3.6–6.1% with a fresh store and 4.2–10.9% with a warm
store. These are three-repeat observations on one host, not portable confidence
bounds. The native engine remains a research provider; this change does not package
or release it.

## Implemented contract

`@model(native_assets=(NativeAssetBundle(name, files), ...))` declares bounded,
data-only bundles of normalized project-relative binary paths. Every file stays
inside captured source and cannot also be a data input. Preparation records exact
member names, sizes, hashes, interpreter ABI and source identity in a hashed
document. Empty declarations preserve existing model metadata and ordinary loading.

The local slot materializes the captured source, acquires the bundle and supplies
an execution-specific binding document. The run store is the sole process owner;
a per-store lock serializes slot threads. Content addresses depend on binary bytes,
basenames and ABI. Publication writes and fsyncs payloads and a deterministic
marker, makes entries read-only, then atomically renames them into place. Leases
remain held through the supervisor's execution/descendant cleanup protocol. No
simulation state is cached.

Children verify the captured source, binding, ABI and cached bytes before model
import. Only exact declared native files are allowed through import-origin auditing;
the resolved manifest records their actual external paths. No native directory is
added wholesale to the import roots. A backend can use `bundle.path(name)` or
`bundle.load_extension(name, filename, dependencies=(...))`. Checked extension
loading verifies bytes and file identities before and after loading, the extension
origin, and actual mapped paths of specified bundled dependencies. Verification
runs again before a result proposal is emitted.

Missing entries rebuild from captured bytes. Corrupt existing entries fail without
automatic repair. `store prune` can remove idle derived entries and abandoned
publication staging; active leases are retained. Original request/source retention
rules are unchanged. Fault injection covers failures before and after rename:
an unsuccessful acquisition creates no lease; a verified entry surviving a
post-rename sync error can be verified and reused on the next acquisition.

This is an experimental local POSIX contract. Remote and Windows native-asset
execution fail explicitly. Actual dependency-image checks support macOS and Linux;
real native loading in this gate was exercised only on macOS. Native code is
trusted: read-only storage and byte checks are provenance/integrity enforcement,
not protection against hostile code running as the same user. No C transitions,
actor coverage, stopping rules or trace schema changed.

## Correctness and lifecycle evidence

The gate ran 96 ordinary executions: two model entry points, production/native
backends, independent/feedback producers, FIFO/SPT, two seeds, and three stopping
windows (H1, H10/WU2, H200/WU20). All 48 pairs between model entry points match
exactly in KPI values/windows, steps, admission counts, trajectory proofs where
enabled, each backend's initial/final state, normal semantic fingerprint and
deterministic result artifact. Backend representations have different state hashes
by design; production and native shared outcomes are compared separately.

The declared-asset model passed readiness-preview cancellation. A corrupted idle
entry caused fresh reproduction to fail with no additional committed result.
Explicit pruning removed it. After deleting live prototype source and evicting the
cache, production and native reproduction both rebuilt from captured source and
reproduced their fingerprints and artifact hashes. Focused tests also exercise
four replications over two local slots, concurrent acquisition, live-lease pruning,
immutable descriptors, source/ABI mismatch, invalidation and publication failures.

The new manifest capabilities and actual cached extension origin were read from
a real trace by the unchanged TypeScript reader. The root evidence audit rejects
deliberate corruption of fingerprints, artifacts, backend state, KPIs, native
origin and asset capabilities. All 56 files in the five historical evidence
directories and all native C sources remain byte-identical to `1330e52`.

## Whole-workflow measurements

Reference: the existing native provider loading from each attempt's captured
snapshot. Managed: the same provider using the declared bundle. Both run through
the uninstrumented current production execution code with identical C/binary bytes.
The entry-point declarations differ, so request/source identities legitimately
differ; semantic fingerprints and deterministic artifacts match exactly.

Each row below summarizes three fresh worker processes, each running an ordinary
three-replication experiment. Replication seeds are 100–102; jobs are per
replication. Parameters: 10 servers, SPT/EDD ConWIP cap 12, load 0.95,
H30000/WU500. Case order was shuffled with fixed seed 20261103.

| Producer | Jobs | Store | Reference median (s) | Managed median (s) | Reduction |
| --- | ---: | --- | ---: | ---: | ---: |
| Independent | 5,000 | Fresh | 2.504 | 2.351 | 6.10% |
| Independent | 20,000 | Fresh | 2.968 | 2.862 | 3.57% |
| Feedback | 5,000 | Fresh | 2.476 | 2.374 | 4.14% |
| Feedback | 20,000 | Fresh | 3.190 | 3.031 | 4.97% |
| Independent | 5,000 | Warm | 2.307 | 2.054 | 10.93% |
| Independent | 20,000 | Warm | 2.833 | 2.567 | 9.40% |
| Feedback | 5,000 | Warm | 2.378 | 2.217 | 6.78% |
| Feedback | 20,000 | Warm | 2.977 | 2.852 | 4.20% |

The 48 experiments contain 144 measured native replications with distinct attempt
identities and no result reuse. Fresh-store timing includes initial publication
and first-path loading. Warm trials use a separate untimed ordinary API run
(zero jobs, H1, seed 99), then measure a fresh worker; there are 24 such preparatory
runs. Both modes receive the same preparation treatment. This warms the store and
runtime; it is not an OS-wide cold/warm page-cache experiment.

Outer time includes process startup/imports, preparation, source/runtime/input
checks, native verification/loading, managed execution, result publication,
statistics and coordinator cleanup. One-time fixture compilation/project copying
and deletion of the disposable store are outside timing. No tests or compiler
jobs overlapped the matrix. Observed ranges do not overlap between modes in any
of the eight groups; three repeats still support only a local descriptive result.
The earlier 6–12% research-loader result is separate evidence with a different
control and timing setup; it must not be combined into a cross-session speedup.

The smaller first-use benefit is consistent with paying for publication and the
initial load. The workflow-level saving survives the real integration; this does
not establish a new kernel speedup or identify the OS/linker mechanism.

An earlier matrix stopped after three rows during deletion of read-only cached
files outside the measured interval. The harness now uses the existing store
removal helper. Its previous gate and partial timings were preserved under ignored
`.build/assets-before-readonly-cleanup/`; the complete gate and all reported timings
were regenerated from the final frozen harness. No partial timings were reused.

## Verification and reproducibility

- Broad Python regressions: 3,431 passed, 1 skipped (three existing dependency
  warnings). This preceded one additional publication-fault test and harness-only
  corrections; the production implementation then changed only in whitespace.
- Final backend/asset/prototype suite: 548 passed, 1 skipped, including all 19 new
  native-asset cases. A separate store/driver/import/coordinator selection passed
  93 tests. Counts overlap.
- Ruff for changed Python files, `ty check src`, and Zensical build pass.
- TypeScript trace tests: 301 passed; typecheck and the real native trace check pass.
- `python -m prototypes.native_engine_dynamic.assets_experiment audit-experiment
  --output prototypes/native_engine_dynamic/assets-results` passes.

Authoritative raw evidence is in `prototypes/native_engine_dynamic/assets-results/`.
`checks.json` records verification scope and timing isolation; `gate/` contains
paired results, `scaling/` contains ordered raw timings and summaries, and root
`verification.json` hashes the evidence. Report and generated evidence are excluded
from the measured source manifest.

| Item | SHA-256 |
| --- | --- |
| Frozen 292-file source manifest | `7390ab23fc58be7fd630f463869be49fb04b4b6a40aebd844cf1a9140a5a16d8` |
| Raw timing matrix | `a2e7fc4fd908f112a0bad4e2b34d6d063eb5fa2e1077b1a280489a8785faf33b` |
| Root audit | `2d16c32286e68669ce7cddad15fa51828ac772d0801875a799f77d65584d6f59` |
| Disposable compiled extension | `c38ed0db5a8e5bfd7f3c3998d043d623fc2c79b5542b2350cc8b93d0322d2fa1` |
| Disposable linked engine | `3aec319af23545f5f292f30c170e11b4a35609d8df5e4d5ec132aed9e79cec0d` |

## Proposed next gate

Validate this declared-bundle path on Linux with a clean build and fresh managed
children, repeating exact-outcome, mapped-dependency, concurrent-slot,
mutation/prune and deleted-source reproduction checks. Then package the limited
native scalar provider as an optional experimental backend with clean-install and
ABI/platform CI gates. This proposal is not an adopted commitment and no next gate
has started. Broader actor coverage and full tracing remain separate decisions.
