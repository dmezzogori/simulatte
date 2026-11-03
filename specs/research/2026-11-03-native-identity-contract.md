# Native trajectory, reporting and reproduction identity experiment

The research gate passes. Reporting-window changes can be distinguished from
changes to the realized native trajectory, without weakening the original backend
commitment used by reproduction. Identity capture preserves bounded memory, but
capturing at every control poll has a measurable cost.

This is a research projection on top of signed base
`fb55e0cc6368cddc540894dffbef39bd3ed4cb00`. Every one of its 2,148 tracked files
remains byte-identical. No core/provider code, public Python API, default fingerprint,
package version, event loop or deployment baseline changes. The matching optional
core/provider pair stays 0.17.0 with provider API 2. No push or publication.

## Contract and scope

The three versioned components have deliberately different meanings:

| Component | Commits to | Appropriate interpretation |
| --- | --- | --- |
| `native-trajectory-control-v1` | Encoding-specific native history/current live state, plus the rolling admission-target/actual-job action history | Same realized native path and actuation within this encoding/model contract |
| `native-reporting-state-v1` | Scope/time unit, declared Horizon and warmup, KPI catalog, current native stock snapshot | Same configured current reporting state |
| `native-reproduction-state-v1` | Both components and the unchanged original backend authority | Complete backend-state comparison within the existing pinned reproduction context |

The overall envelope is `native-identity-set-v1`; action history uses
`admission-actuation-v1`. Scope includes backend name, execution scope and time
unit. Original controller callbacks remain unchanged and commit to source seed,
cursor, owned jobs, control memory, phase/schedule and history/configuration.
Changing an unconsumed seed, pressure or next-wake value changes the complete
commitment even when the realized native path has not changed.

For full retention, the trajectory component hashes NSTA0001 bytes. For bounded
retention, it combines the existing completion-history commitment with NBST0001
live-state bytes, excluding the NRST0001 current KPI tail. Reporting retains that
actual snapshot and its configuration. The original digest still hashes the
entire original native byte stream plus the complete callback state; capture
recomputes that preimage independently and checks snapshot/callback stability.

The parser recognizes only the existing versioned codecs, checks structural
lengths and shape, and requires the NRST tail to equal the canonical actual
snapshot. Unknown codecs, truncations, tail mismatches and inconsistent authorities
fail closed. Initial captures occur on activation; final captures occur at the
same synchronized right-open Horizon boundary as the original fixture finalizer,
before managed backend finalization. Query counters stay outside semantic state.

These hashes are not resumable checkpoints. Trajectory identity must never decide
reuse or reproduction: it intentionally omits unconsumed future controller state.
Complete identity supplements the pinned model/runtime/request and ordinary
Coordinator reproduction; it does not replace that context. Full and bounded
trajectory encodings are not normalized into one cross-retention identity.
Reporting identity covers native stock snapshots, not arbitrary collector history,
traces or exports. Exported envelopes validate integrity and authority links, but
are not proofs against malicious producers: bounded export retains the live-state
hash rather than its raw preimage. Trust remains in the frozen capture/runtime.

## Evidence and results

The wrapper instruments byte-identical copies of both preceding completed models:
fixed specifications with reporting-independent admission, and adaptive production
whose admission law reads windowed tardy fraction. It adds a bounded rolling action
commitment, reconstructs every actual admitted job, and checks it against each
fixture's existing input-history hash. Original model bytes are preserved.

The final installed gate ran outside the repository with clean matching wheels,
CPython 3.14.3 on macOS arm64 and no compiler available at runtime. Primary coverage:
two fixtures × FIFO/SPT × seeds 100/101 × warmup 0/10/40 × ordinary Python/full-native/
bounded-native = **72 cases**. The independent audit checked **24 physical scenarios,
4,224 decision boundaries and 28 content-addressed proof streams**, reconstructing
control laws and source requirements with the preceding independent validators,
and reconstructing action commitments directly from the recorded actual inputs.

- All native inputs, control observations, final physical state, reporting values
  and scalar KPIs match the ordinary Python reference within each scenario/window.
- All **8 fixed-controller native groups** preserve trajectory/control identity
  across the three warmups; reporting and complete identities distinguish all
  three windows at both initial and final boundaries.
- All **8 KPI-fed native groups** change realized actions across warmups, and their
  trajectory/control identities change accordingly. All **16 native groups** start
  with equal trajectory identity across windows but distinct reporting/complete
  identities. Complete comparisons reject cross-window reproduction equivalence.
- **12 primary source-deleted fresh-process reproductions**, plus **2 extra attempts
  from one long-case Coordinator parent**, match original fingerprints, all artifacts,
  authority and projected components exactly. Attempt ids are distinct.
- **6 capture-disabled controls** preserve all original artifacts and default
  fingerprints; **4 every-poll capture controls** additionally preserve projected
  identity artifacts. Long-case dense/sparse and capture-disabled twins also match.
- **84 focused tests passed**: 23 new identity tests, 25 fixed-admission tests and
  36 feedback tests. Negative coverage includes malformed/versioned encodings,
  altered tails/authority/components, impure callbacks, closed/finalized backends,
  equal-report/different-completed-history cases and unconsumed future-state changes.

Example, bounded native, SPT, seed 100; digest prefixes are illustrative:

| Controller | Warmup | Jobs admitted | Trajectory/control | Reporting | Complete |
| --- | ---: | ---: | --- | --- | --- |
| Fixed | 0 | 72 | `f0aa496b19d3` | `ce32b9a58c00` | `809a4b07ffcb` |
| Fixed | 10 | 72 | `f0aa496b19d3` | `342ee89b7c63` | `d5e599719bfe` |
| Fixed | 40 | 72 | `f0aa496b19d3` | `5f84672965f2` | `4c4902471411` |
| KPI-fed | 0 | 122 | `ea3f89225acd` | `b3789d129824` | `1b03ddac17dd` |
| KPI-fed | 10 | 122 | `948b77bed454` | `e2a2e9beec84` | `80b9c44e4a86` |
| KPI-fed | 40 | 167 | `a2c418c0fbb5` | `d3c4e065011c` | `22e62e1bea39` |

The first two KPI-fed cases admit the same count but different jobs/timing/actions;
equal counts are insufficient to establish equal trajectories. The bounded
original authority still differs across fixed-controller windows because it
retains the original KPI snapshot. That intentional existing behavior is untouched.

## Resource gate and cost

Serial bounded cases use 4,096/32,768/262,144 decision ticks, three seeds, both
controllers, no per-poll proof files, and identity queries every 64 polls. Eighteen
scaling cases plus five controls and two extra reproduction attempts give **119
fresh attempts overall**. No tests or compilers ran during resource measurement.
Process-tree RSS is externally sampled every 40 ms; child peak RSS also comes from
the child's own resource counter. Values below are medians of three seeds.

| Controller | Ticks | Jobs | Tree MiB | Child MiB | Parent MiB | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed | 4,096 | 1,536 | 143.81 | 45.53 | 45.02 | 1.39 |
| Fixed | 32,768 | 12,288 | 143.81 | 45.64 | 45.08 | 2.70 |
| Fixed | 262,144 | 98,304 | 144.86 | 45.83 | 45.94 | 13.46 |
| KPI-fed | 4,096 | 3,070 | 144.31 | 45.73 | 44.64 | 1.38 |
| KPI-fed | 32,768 | 24,230 | 144.64 | 45.88 | 45.16 | 2.97 |
| KPI-fed | 262,144 | 193,863 | 143.91 | 45.83 | 44.33 | 16.19 |

All predeclared memory limits pass: child/tree/parent median ranges remain below
16/24/16 MiB respectively across 64× more ticks. At most 12 handles are owned;
maximum controller callback is 1,329 bytes, queried native blob 2,671 bytes,
canonical projected view 3,676 bytes, final initial+final identity artifact 7,227
bytes, and reported retained native allocation 9,831 bytes. The same parent grows
only 0.55 MiB in peak RSS across two further source-deleted reproductions.

The single seed-100 small controls provide a cost warning, not a precise benchmark:
capture-disabled / every-64 / every-poll end-to-end wall times are 1.320 / 1.385 /
2.281 seconds for fixed and 1.305 / 1.336 / 2.405 seconds for KPI-fed. Every-poll
capture is approximately 73%/84% slower than disabled in those pairs. Capture
currently repeats raw-state/snapshot/authority checks and canonical serialization.
Periodic queries stay bounded; this experiment does not establish cheap continuous
capture, statistically precise overhead, or any new native-versus-Python speedup.

## Implications and next gate

This resolves the previous identity ambiguity: reporting configuration can be
observational for one controller and affect plant behavior for another. The system
can express both cases without dropping reporting state from the authoritative
commitment or pretending that a trajectory digest describes future controller state.

The recommended next gate is a provider-owned experimental projection hook that
returns explicitly versioned native trajectory material and reporting state under
one synchronized lock. Keep the default complete digest/reproduction path intact,
and reuse this exact semantic matrix to compare the hook with this frozen parser
reference. Measure on-demand/periodic query cost before considering a per-poll path.
That would remove the wrapper's private owner access and binary-layout knowledge
while retaining a concrete correctness oracle. It is a proposed next experiment,
not implemented or a production-readiness commitment. Portable wheels and deployment
baselines remain deferred until after Studio v2.

Evidence: [frozen source/wheel metadata](../../prototypes/native_identity_contract/results/macos-final/metadata.json),
[raw installed gate](../../prototypes/native_identity_contract/results/macos-final/gate.json),
[resource rows](../../prototypes/native_identity_contract/results/macos-final/rows.json),
[evidence hashes](../../prototypes/native_identity_contract/results/macos-final/verification.json),
[independent audit](../../prototypes/native_identity_contract/results/macos-final/audit.json),
[predeclared plan](2026-11-03-native-identity-contract-plan.md).
