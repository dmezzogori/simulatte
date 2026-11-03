# Provider-owned experimental state projection

The provider-owned hook is implemented and the gate passes. Callers can obtain
trajectory material, current reporting state and complete controller authority
through the experimental Python API without accessing private owners or parsing
native binary layouts. The new hook reproduces the preceding projection exactly.
Moving ownership did not establish a speedup; dense capture remains expensive.

Davide authorized this step after signed base
`7bde5941b794cee8ff476b22d6f93369cf6104c7`. Nine implementation/documentation files
change; the other 2,235 base files remain byte-identical, including every earlier
research fixture/report/evidence file and all native event-transition sources.
The local core/provider pair stays 0.17.0, provider API 2. Projection v1 is additive;
there is no publication, deployment-baseline selection or default digest migration.

## Public contract and implementation

```python
from simulatte.experimental.native import Job, NativeStream

with NativeStream(servers=2, retention="bounded") as engine:
    engine.add(Job(0, (0, 1), (2, 3), due=10))
    engine.advance(1)
    view = engine.state_projection()
    print(view["trajectory"])
    print(view["snapshot"]["scalars"])
```

`NativeStream.state_projection()` returns detached data with schema
`native-stream-projection-v1`, `trajectory`, `snapshot`, `native_sha256` and
`native_bytes`. The hash/size describe the exact original `state_bytes()` encoding.
Full-retention material uses NSTA0001; bounded material combines the existing
completion-history commitment with the serializer's NBST0001 live-state hash.
The provider derives the original NRST0001 commitment incrementally from its
pieces, without concatenating/decoding a native blob. Layout knowledge remains
with the native serializers. No offsets are decoded by the new consumer.

For an attached, live `NativeProduction` at a synchronized managed boundary:

```python
view = backend.state_projection()
assert view["schema"] == "native-production-projection-v1"
assert view["authority"]["digest"] == backend.state_digest()
reporting = view["reporting"]  # configuration, catalog, snapshot
```

The managed view also returns `scope`, `authority_preimage` and `native_bytes`.
Authority retains the complete original callback state. All results are detached;
changing a returned collection does not alter native/model state. Stream queries
after close and managed queries before preparation, after finalization/close or at
unsynchronized boundaries fail. An old API-2 companion keeps legacy operations
and raises an actionable error when projection v1 is requested; rebuild/reinstall
the matching local wheels to use it.

The C binding keeps the owner busy while serializing native state, obtaining stock
KPIs and creating the detached result, including across GIL release. The Python
stream holds its existing owner lock through completion-history composition and
hashing. Managed capture holds that lock through both pure callback reads and
authority/reporting assembly. A private mutation generation detects reentrant
add/advance/run/close operations; changing callback values also rejects capture.
Read-allocation failure clears the busy guard and permits retry. Failed impure
callbacks do not roll back their own side effects. Purity and coverage of all
future Python state remain caller responsibilities.

Admission actions belong to the model rather than a generic native owner. The new
research consumer composes the existing `native-identity-set-v1` envelope from the
public hook plus the same rolling model action commitment. Default `hook` mode
never calls the parser; explicit `reference` and `compare` modes use byte-identical
copies of the preceding parser and fixtures. No fallback is installed in the
product API. The default state digest, fingerprint, reuse and reproduction path
remain authoritative and produce the same historical values.

Trajectory material omits unconsumed future Python state and cannot authorize
reuse/reproduction. Full and bounded identities remain encoding-specific.
Reporting is the native stock snapshot/configuration/catalog, not arbitrary
collector history or trace/export identity. These commitments do not restore an
engine or prove correctness against malicious native/model code. Ordinary pinned
model/runtime/request provenance and fresh Coordinator reproduction still apply.

## Correctness and installation gate

The final clean installed run on macOS 27.2 arm64 / CPython 3.14.3 runs outside the
checkout with compilers unavailable. It covers:

- **72 primary cases:** two control fixtures × FIFO/SPT × seeds 100/101 × warmup
  0/10/40 × ordinary Python/full-native/bounded-native. Comparison mode checks the
  entire public-hook identity envelope against the frozen parser at every captured
  initial/final boundary. All 72 historical artifacts, default fingerprints and
  projected identities match the preceding signed evidence exactly.
- **24 physical scenarios, 4,224 decision boundaries and 28 proof streams:** native
  inputs, control observations, final state, reporting and KPIs equal ordinary
  Python. Independent source/control validators and action reconstruction check
  recorded actual inputs. Eight fixed-controller native groups keep one trajectory
  across warmups; eight KPI-fed groups change actions and trajectory. All 16 initial
  native groups share past trajectory across windows while complete/reporting
  commitments differ. Future seed/pressure/schedule mutations change authority.
- **18 correctness controls:** six capture-disabled cases preserve existing
  artifacts/default fingerprints; four dense comparison cases and eight pure
  hook/reference twins preserve complete identity artifacts as well.
- **14 source-deleted fresh reproductions:** 12 primary cases plus two further
  attempts in one long-case parent exactly match fingerprints, artifacts, original
  authority and projected components. All attempt ids are distinct.

Product validation: **3,626 tests passed, one skipped, 99.05% coverage** in 351.34 s.
The three warnings are dependency deprecations in the WebSocket/Studio stack.
The latest 40 focused checks pass (34 API cases plus six fixture cases), including
detachment, old/malformed provider responses, synchronization/lifecycle, future
state, callback mutation and public-consumer/parser isolation. Fully ASan/UBSan
instrumented installed binding and engine images pass **123 focused tests** plus
full/bounded projection allocation-failure recovery. Python leak detection is
disabled for that instrumented interpreter run. A safety harness initially selected
a macOS `.dSYM` directory for ctypes; corrected file selection passed on rerun.

The existing clean-install gate passes pure-base optional import, matching native
extra, source-deleted reproduction and seven rejection classes: ABI, provider API,
manifest, binary corruption, wrong dependency mapping, runtime mutation and wheel
tag. Ruff, type checking, documentation build and lock verification pass. Fresh
platform validation here is macOS/CPython 3.14; no new Linux or other-ABI result is
claimed. Existing platform support/deferred portable-release policy is unchanged.

The final source freeze covers 189 files, SHA-256
`4c46b8750d3e2e252621e18fe9c49d1c5b8690b7419fc07ea65ba13841a8048c`.
Core wheel SHA-256 is
`a58088346220aafc683281923b362e2e24cb58836383e43838b3cdeb8d6a9dec`;
native wheel SHA-256 is
`c9045808af932adda56455a4b04790f463da0cebb072f823d96eeddeda33b3af`.
Archive bytes, native manifest/source hashes and actual installed binary origins
are checked. The prior wheels/evidence remain untouched.

## Bounded memory and query cost

Eighteen serial scaling cases use 4,096/32,768/262,144 decision ticks, three paired
seeds and both fixtures, querying the hook every 64 polls. Thirty cost controls
cover disabled capture, initial/final-only hook, every-poll hook, reference every
64 and reference every poll, each with three paired seeds and both fixtures. One
same-parent case adds two further reproductions. These are **49 resource cases**
and **153 fresh attempts overall**. No tests or compilers run during timing.

Median values across the three scaling seeds:

| Controller | Ticks | Jobs | Tree MiB | Child MiB | Parent MiB | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed | 4,096 | 1,536 | 145.63 | 45.83 | 46.59 | 1.45 |
| Fixed | 32,768 | 12,288 | 145.58 | 45.59 | 45.98 | 2.69 |
| Fixed | 262,144 | 98,304 | 145.13 | 45.83 | 45.16 | 13.31 |
| KPI-fed | 4,096 | 3,070 | 143.95 | 45.48 | 44.95 | 1.34 |
| KPI-fed | 32,768 | 24,230 | 144.59 | 45.80 | 45.47 | 3.11 |
| KPI-fed | 262,144 | 193,863 | 144.19 | 45.83 | 45.09 | 16.35 |

All predeclared child/tree/parent median-range budgets of 16/24/16 MiB pass across
64× more ticks. Child median peaks remain 45.48–45.83 MiB. At most 12 handles are
owned; maxima are 1,329 bytes for the callback, 2,671 queried native bytes, 3,676
canonical identity-view bytes, 7,227 identity-artifact bytes and 9,831 reported
native allocation bytes. The repeated parent peak grows 2.45 MiB across its two
additional reproductions, below the 16 MiB limit. Tree RSS is externally sampled
every 40 ms; child peaks also use their own resource counters.

End-to-end small-case costs (4,096 polls; medians of three paired seeds):

| Capture mode | Fixed seconds | KPI-fed seconds |
| --- | ---: | ---: |
| Disabled | 1.359 | 1.282 |
| Hook, initial/final only | 1.339 | 1.430 |
| Hook, every 64 polls | 1.454 | 1.341 |
| Hook, every poll | 2.403 | 2.403 |
| Parser reference, every 64 | 1.321 | 1.425 |
| Parser reference, every poll | 2.365 | 2.331 |

Every-poll hook capture adds approximately 77%/87% to the disabled medians. Its
medians are approximately 2%/3% slower than the parser, with overlapping observed
ranges. There is no established speedup from moving ownership. Periodic and
initial/final-only controls show startup/noise effects and do not establish a
precise per-query cost. Disabled mode also omits the model's action-hash/input
reconstruction instrumentation, so that comparison is broader than query cost
alone. These timings include Coordinator preparation, child startup, artifact
serialization and result checks; they are not hot-query microbenchmarks.

## Implications and next work

The ownership goal is achieved: a model can use a documented, versioned hook while
the provider controls atomic native observation and encoding details. The old
semantic matrix and historical outputs remain a concrete oracle. No weakened
complete identity or default fingerprint behavior was needed to expose the split.

Continue using on-demand/periodic capture. The next performance experiment should
profile the hook and identity composition separately, including dictionary copying,
canonical packing and callback reads; these are hypotheses for the remaining cost,
not findings from these end-to-end timings. Preserve this exact oracle/authority
gate while testing a smaller internal representation or fewer repeated conversions.
Fresh Linux/other-CPython validation of the new C entry points also remains useful
before widening use. Neither follow-up is performed in this change. Production
wheels/deployment baselines remain deferred until after successful Studio v2.

Evidence: [source/wheel freeze](../../prototypes/native_provider_projection/results/macos-final/metadata.json),
[installed gate](../../prototypes/native_provider_projection/results/macos-final/gate.json),
[resource rows](../../prototypes/native_provider_projection/results/macos-final/rows.json),
[independent audit](../../prototypes/native_provider_projection/results/macos-final/audit.json),
[raw measurement hashes](../../prototypes/native_provider_projection/results/macos-final/verification.json),
[regression/safety/install checks](../../prototypes/native_provider_projection/results/checks/verification.json),
[predeclared plan](2026-11-03-native-provider-projection-plan.md),
[public API guide](../../docs/guides/native-engine.md#experimental-state-projection).
