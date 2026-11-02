# Native live observation contract experiment

Authorized by Davide after the [simultaneous-event experiment](2026-11-02-native-semantics.md).
Starting revision `c69bcbe`; work stays in the native feasibility worktree.

Prototype an opt-in adapter over the corrected scheduler. Preserve the buffered
direct emitter as a control. Add transport flushing without changing calendar
ordering, native results or the rich journal format.

The proposed contract is deliberately limited and testable:

- `add()` delivers entity creation before returning. `advance(t)` delivers all
  observations from native events strictly before t, then exposes clock t;
  equal-time events remain pending. Repeated equal boundaries are no-ops.
- Callbacks see event-time views and event-time handle inspection, with no
  look-ahead into the already-advanced C engine. Callback calls that advance,
  register, close or extract the model are rejected. Observers cannot control C.
- Read-only job completion/timing/processing fields, floor WIP and counts, and
  server capacity/queue/utilization counts support the five unmodified stock
  collectors. General actor methods, queue iteration and live snapshots remain
  unsupported. Historical Trace seeking and native handles remain available.
- Stock collectors attach before activation. Warm-up, right-open horizon windows,
  exhaustion, early close and scalar/series output are checked against actual
  production actors, including intermediate queries after each boundary.

Use typed counters and minimal timing arrays, not generic delta replay or a second
full entity state dictionary. A new observation publisher may correct the WIP
update/release observation order while leaving scheduling and the old publisher
unchanged. Native benchmark totals and stock KPI definitions must not collide.

First require failing boundary/collector tests, then implement and compare stock
collector state, actual event-time probes, full traces and trajectories. Expand to
tie-heavy and continuous inputs, debug guards, both producers and interpreters.
Exercise new flush/error paths under sanitizers. Preserve old fixtures/evidence
and add an observation fixture if needed for Studio compatibility.

After correctness, freeze sources/binaries/input and benchmark equal stock
collector workloads on production and native, separating boundary flushing from
buffered replay where possible. Include writer close/cleanup and record output
volume/RSS. Keep scope bounded to this model and observation surface; no public
backend integration, packaging or unrelated optimization. Save evidence/report
and Cortex checkpoint, then commit locally; no push requested.
