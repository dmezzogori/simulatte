# Native scaling, event ordering and observation gate

Authorized by Davide after the [direct emitter experiment](2026-11-01-native-direct.md).
Starting revision: `62b1f85`. Work remains in the native feasibility worktree.

The C engine, compiled binding, direct/aligned emitters, production actors and
recorder stay fixed. This gate adds research harnesses, evidence and regression
characterization only. Findings that fail the gate are reported rather than
repaired by changing the comparison semantics.

1. Compare strict `advance(t)` boundaries and deterministic simultaneous arrivals,
   completions, crossed routes, tied EDD/SPT choices and integer-time workloads.
   Exercise FIFO/SPT, caps 1/2/12, independent/feedback producers and none/KPI/full.
   Require aligned/direct equivalence, valid traces and valid trajectories.
   Distinguish dispatch-order-only differences from changed job outcomes against
   actual production. Preserve a reduced reproducer if the production gate fails.
2. Check event-only observer invariance and delivery boundaries. Exercise all five
   built-in production collector classes on real actors and both publishers.
   Characterize unsupported live-property access and snapshots explicitly; do not
   add replacement actor APIs or reinterpret custom benchmark totals as standard
   production KPIs.
3. Measure 5,000 and 20,000 jobs from the existing shared 50,000-job input,
   production/direct, CPython/PyPy, both producers, none/KPI/full (48 cases).
   Use serial shuffled fresh processes, one CPython/seven PyPy warm-ups, five
   measured repetitions and one separate cold/RSS probe per case. Use the existing
   production benchmark worker unchanged. Include construction, feeding, extraction,
   writer close and cleanup; exclude loading/imports and post-run validation.
   Contemporary controls determine speed comparisons. No further profiles are
   needed for this correctness/scaling gate. Aligned/direct trace parity is also
   checked at 20,000 jobs with eight untimed cold probes.
4. Freeze and audit source/binary/input hashes, coverage, phase sums, output parity
   and cold probes. Record failures as evidence. Run neighboring Python checks;
   production/TypeScript/browser code and fixtures are unchanged, so their previous
   compatibility results remain historical evidence, not new large-trace checks.

Success requires production ordering/outcome equivalence and an explicitly usable
observation contract as well as scaling. A performance improvement alone does not
authorize public backend integration. The selected matrix covers one load, ten
unit-capacity servers, cap 12, SPT and this model family; it is not a general model
or packaging certification.
