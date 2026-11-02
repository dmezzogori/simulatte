# Managed native workflow phase profile

The native event loop is no longer the main performance target for the supported
managed production slice. Across three replications of 20,000 jobs each, measured
native advancement takes 55–65 ms within an approximately three-second workflow.
Extension loading takes 229–240 ms, input generation about 211–213 ms, and complete
backend state-digest queries about 132–133 ms. Publication takes about 17 ms.
There is no single remaining stage that dominates the whole workflow.

This completes the [authorized phase-profile plan](2026-11-02-native-phase-profile-plan.md)
following the [explicit backend gate](2026-11-02-native-backend.md). It measures
costs and proposes the next experiment; it implements no application optimization.
Production Python and native C sources remain byte-identical to `fee446b`.

## Measurement and correctness

The research harness instruments disposable copies of the trusted runtime and
research project. Checked AST transformations add timers to named lifecycle,
runtime, input, state, trace and publication functions; all expected targets must
match exactly. They also time each managed outer Python step. The instrumented
copies are frozen and verified through the ordinary source/runtime machinery.
Repository production files and native C transitions stay unchanged. The
application adapter continues to use the explicit backend lifecycle contract.

Active and dormant cases use identical copied source/runtime bytes. A private
child-configuration field enables measurement without changing the execution
request. Diagnostics are sidecar files outside the project, result artifacts,
traces and semantic fingerprints. Each replication and inspection still uses its
ordinary fresh child, readiness barrier, transfer, cleanup and publication path.
The dormant copy still contains inactive diagnostic wrappers; it is a control for
the measurement switch, not the pristine installed production runtime.

The timer records inclusive and exclusive wall-clock durations per thread. Coarse
spans retain timestamps; repeated advancement and Python-step calls retain counts
and totals. It records no per-event diagnostic history. Exclusive time excludes
nested measured calls in the same thread; it can still include waits, unmeasured
work and probe bookkeeping. It is not CPU time. Parent waits overlap work in
children, so cross-process totals and nested spans must not be added to reconstruct
outer wall time. All child timestamps were checked against the enclosing local
process interval.

The correctness gate has 20 main runs and eight fresh reproduction executions:
two seeds, both backends and producers, Horizon 10/warm-up 2, plus an original
independent-arrival control. Active/dormant requests, ordinary fingerprints,
deterministic artifact hashes, backend state, shared results and proofs match.
Eight additional original-runtime controls cover both producers/backends. Their
shared results, state and ordinary semantic fingerprints match the active copies,
while source hashes and request identities differ with the actual source/runtime
copies. Reproduction retains its normal checks; timing data never enters artifacts.

The randomized timing matrix has two backends × two producers × 5k/20k jobs ×
active/dormant measurement × three repeats: 48 fresh worker processes, each running
three replications, or 144 timed replications. All shared results match, and each
backend's request, fingerprint, state and artifact identities repeat across both
measurement modes. The eight stock KPIs remain unchanged. Native and production
state schemas remain distinct representations.

## Whole workflow and probe cost

bigboss, macOS arm64, CPython 3.14.3. Configuration: ten unit servers, SPT, EDD
ConWIP cap 12, load 0.95, Horizon 30000/warm-up 500, seeds 100–102. Optional proof
generation is off in both timed implementations. The same independent/feedback
producers and supported backend slice are retained.

Outer wall time includes interpreter/import startup, runtime/source preparation,
inspection, input generation, fresh replication children, readiness, advancement,
state fingerprints, normal result transfer/publication, reading/statistics and
cleanup. It also includes diagnostic collection/serialization in active cases.
One-time native compilation and disposable project/runtime copying precede timing;
deleting retained stores follows it. Regression checks did not run concurrently.

Seconds per experiment of **three replications**, median (min–max), three repeats.
Each 20k row executes 60k jobs in total.

| Backend | Producer | Jobs/replication | Dormant | Active | Median difference |
| --- | --- | ---: | ---: | ---: | ---: |
| native | independent | 5,000 | 2.475 (2.416–2.517) | 2.473 (2.463–2.557) | −0.05% |
| native | independent | 20,000 | 2.982 (2.753–3.251) | 3.116 (3.005–3.127) | +4.49% |
| native | feedback | 5,000 | 2.661 (2.491–2.664) | 2.492 (2.408–2.600) | −6.33% |
| native | feedback | 20,000 | 3.193 (3.090–3.220) | 3.242 (3.232–3.282) | +1.53% |
| production | independent | 5,000 | 4.115 (4.080–4.152) | 4.197 (4.173–4.275) | +1.99% |
| production | independent | 20,000 | 14.696 (14.454–14.762) | 14.941 (14.660–15.102) | +1.67% |
| production | feedback | 5,000 | 3.577 (3.430–3.596) | 3.622 (3.606–3.728) | +1.24% |
| production | feedback | 20,000 | 8.228 (8.085–8.279) | 8.395 (8.384–8.423) | +2.03% |

Active/dormant differences combine probe cost and ordinary scheduling/load
variation. Negative medians do not establish that instrumentation accelerates the
model. Three repeats on one host cannot isolate an exact probe overhead or provide
portable confidence bounds. These diagnostic copies also differ from the previous
gate's source/runtime; use its uninstrumented results for that gate's throughput
claim. This matrix is for attribution and measurement controls.

## Native phase costs

Seconds summed across the **three execution children**, then median over three
active experiments at 20k jobs per replication. The rows below are separate
measured function families, not an exhaustive partition of workflow time.

| Measured execution-child interval | Independent | Feedback |
| --- | ---: | ---: |
| Native extension loading | 0.240 | 0.229 |
| Input generation | 0.213 | 0.211 |
| Backend preparation excluding nested extension loading | 0.149 | 0.149 |
| Import provenance audits, all nine calls | 0.140 | 0.138 |
| Installed-distribution evidence, six scans | 0.119 | 0.119 |
| Model import excluding nested audits | 0.109 | 0.108 |
| Complete backend state-digest queries | 0.132 | 0.133 |
| Readiness barrier, including its nested transfer | 0.163 | 0.162 |
| Managed outer Python steps | 0.098 | 0.278 |
| Backend advancement | 0.065 | 0.055 |

State-digest queries include native byte encoding, SHA-256, Python generated-input
and controller hashing, and cached final-query calls. The native `state_bytes`
getter itself takes about 1.65/1.26 ms summed across the three runs; this excludes
SHA-256 and uses initial/final states. It is not the previous partial-state
encoding-plus-SHA measurement. Most current state-digest time is outside that raw
getter. Backend preparation also computes the future-input digest; its exclusive
interval does not distinguish that operation from the other preparation work.

Feedback increases managed Python-step time because the producer inspects jobs
and selects new routes/durations before registration. Backend advancement remains
small. Each family has 60,009 calls across the three replications. The measured
advancement function includes Python/C boundary work and collector updates, not
just the internal C transition loop.

Parent-process intervals have a different scope:

| Parent interval, whole experiment | Independent | Feedback |
| --- | ---: | ---: |
| Coordinator open, including runtime freeze/check | 0.180 | 0.179 |
| Preparation, including fresh inspection and its wait | 0.362 | 0.364 |
| Result publication, all three results | 0.017 | 0.017 |
| Coordinator close | 0.103 | 0.109 |
| Result reading/validation/statistical report | 0.033 | 0.033 |

Runtime freeze accounts for about 0.177/0.176 s within coordinator open. Source
capture takes about 10 ms per experiment; materializing all assignments totals
about 18–19 ms. These measurements do not support treating source snapshot I/O or
result publication as the main bottleneck. Preparation includes inspection-child
work, so adding that child time again would double count it. Startup before probe
activation, supervision, transfer waits and other unmeasured work remain in the
outer workflow; the profile does not assign all of that residual to one cause.

At 5k jobs, native extension loading remains about 0.200–0.232 s while input
generation falls to about 0.055 s, preparation outside loading to 0.039–0.041 s,
and state-digest queries to 0.034–0.036 s. This separates a fixed loading/setup
component from work that grows with submitted input. It does not establish the
specific OS, linker, filesystem or allocation mechanism behind loading time.

## Implications and proposed next experiment

For these single-slot experiments, advancement intervals do not overlap across
replications. Their median total is about 2% of dormant outer wall time. Even
eliminating the entire measured advancement interval would have little effect on
the whole workflow. Publication is below 1%. Further C-loop tuning or relaxing
publication checks is therefore not justified by this profile.

Proposed, not adopted: test **verified immutable native-binary loading** against
the current per-attempt materialized asset path. Use fresh child processes and
the same native bytes, verify content hashes before loading, preserve source and
runtime evidence, and compare state/KPIs/artifacts/reproduction. A content-addressed
asset location is a hypothesis for this experiment, not an established fix or an
adopted caching rule. Measure loader time and outer wall time; reject the change
if it weakens integrity or fails to improve the full workflow consistently.

The entire current loading interval is only 0.23–0.24 s per three-replication
experiment, roughly 7–8% of outer wall time. That bounds expectations: no large
speedup is implied, and a real improvement would recover only part of that time.
Input generation and repeated input/state encoding are other measured candidates;
this profile does not yet isolate their individual serialization operations.
Packaging, broader actors, remote execution and full native tracing remain
separate work. No PyPy performance/delivery investigation was performed.

## Verification and reproducible evidence

- Backend/prototype regression suites: 514 passed, one skipped. This includes four
  timer/transform tests for deterministic nesting/exception accounting, independent
  thread stacks, exact AST targets/decorators and the pure measurement switch.
- Ruff passes for the new research modules/tests.
- Twenty gate runs plus eight reproductions, 144 timed replications and eight
  independent original-runtime controls pass their comparisons.
- The final audit rejects six corrupted data probes: backend state, ordinary
  fingerprint, deterministic artifact hash, KPIs, seed and invalid timing accounting.
- All production Python and native C files match `fee446b` byte for byte. Twenty-nine
  historical evidence files from the preceding KPI/execution/backend gates remain
  byte-identical. Default native-library hashes are unchanged.

Frozen source manifest: **281 files**, SHA-256
`50d2cf7b6d4970dd3a1b89f1eab70e9c940875114b924ed78b3ad3ebe039784b`.
Timing matrix SHA-256:
`b2aa758bc23c87356617075ac1cef2e6331a9e1ac0f6048460dc0c7d4d026686`.
Complete audit SHA-256:
`2f54576912448bc1118fbc25c84660624c1d073a95a4266d816452c1f19d5e3f`.
Metadata also preserves original/transformed target hashes and the complete
instrumented runtime/project file manifests. Report and generated evidence are
outside the measured source manifest.

Evidence: [gate certificate](../../prototypes/native_engine_dynamic/phase-results/gate/verification.json),
[gate runs](../../prototypes/native_engine_dynamic/phase-results/gate/runs.json),
[raw profiles/timings](../../prototypes/native_engine_dynamic/phase-results/scaling/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/phase-results/scaling/summary.json),
[role-separated analysis](../../prototypes/native_engine_dynamic/phase-results/analysis.json),
[original controls](../../prototypes/native_engine_dynamic/phase-results/original-controls.json),
[checks](../../prototypes/native_engine_dynamic/phase-results/checks.json),
[complete audit](../../prototypes/native_engine_dynamic/phase-results/verification.json).

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.phase_experiment gate --output /tmp/native-phase/gate
.venv/bin/python -m prototypes.native_engine_dynamic.phase_experiment matrix --output /tmp/native-phase/scaling
.venv/bin/python -m prototypes.native_engine_dynamic.phase_experiment audit-experiment --output /tmp/native-phase
```

The independent original-control script is retained with the evidence. Its output
and `analysis.json` are independently generated records, hashed by the root audit;
the audit does not rerun original controls, unit tests or analysis generation.
The checked-in audit validates its measured source/library identities and raw
accounting/comparisons, so reruns after source changes require fresh evidence.
