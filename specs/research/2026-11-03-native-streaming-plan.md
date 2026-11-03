# Bounded-memory native streaming experiment

Davide authorized this experiment on 2026-11-03 after clarifying that native work
is Simulatte v3 research, following a successful v2/Studio release. Base: signed
`9ecc2629b0db0fa67f1e39efa7e48d60dfff81fa`. Work inline in the existing isolated
research worktree. No release/deployment baselines, portable wheels, push or merge.

## Boundary

Implement a research-only `Stream` under `prototypes/native_engine_streaming`.
Reuse the unchanged validated C event transitions and strict stock KPI helper by
including/linking the existing prototype sources. Do not change `native/`, `src/`,
the packaged public API, earlier experiments or their raw evidence.

- `retention="full"` is the default. `retention="bounded"` automatically compacts
  completed jobs/operations at successful advance/run boundaries. It retains
  active/pool/future jobs, including earlier completed operations of a live job.
- Stable owner-scoped 64-bit logical IDs survive physical compaction. Completed
  handles explicitly expire in bounded mode; missing/foreign/closed handles fail
  safely. No tombstone collection grows with completed-job count. IDs never reuse
  during one engine lifetime and overflow fails explicitly.
- Compaction preserves event times/sequences, queue and pool order, all KPI
  accumulators and event-phase semantics. Full mode retains completed inspection.
- A bounded completion batch records completed inputs and trajectories in actual
  completion order. Python incrementally SHA-256 hashes it, then releases it.
  Canonical state uses stable IDs and live logical state, excluding physical
  indices/capacities/retention mode. Full/bounded digests must agree under equal
  controller state, even across different compaction partitions. Past execution
  remains committed by the rolling hash. This is a new research state schema,
  not `NSTA0001` or the installed `native-production-v1` promise.
- Controller/input state is explicit compact data (seed/cursor/parameters), not a
  retained job list. Generators emit bounded windows; the memory worker retains
  neither completed handles, trajectories nor a sample per window.

## Gates

1. Differential parity with full retention and the packaged full-history stream:
   exact eight production scalars/windows, event counts, final time, live
   inspections and deterministic history/state commitments. Include FIFO/SPT,
   cap/unlimited, due/timestamp ties, fractional durations, right-open horizons,
   warmup, empty/drained/refilled calendars, active jobs crossing compaction and
   different advancement partitions. Retain existing production KPI comparisons.
2. Repeated compaction and expired-handle safety, non-mutating queries, allocation
   failures with destroy-after-failure, independent streams and repeated close.
   Standalone ASan/UBSan and an instrumented allocation-failure sweep.
3. Fresh-process full/bounded measurements with identical lazy generated inputs:
   increasing completed count at a stable offered load, fixed input lookahead and
   bounded live workload. Whole-process peak/current RSS, native capacities,
   maximum retained jobs/operations, state bytes/query latency, loop and
   finalization costs, exact KPI/history/state checks. Include overload as a
   negative workload-bound control, not a passing bounded-RSS case.
4. Freeze source/binary/input generator hashes before serial timing. No tests or
   compiler jobs during timed measurements. Record raw rows and reproducible audit,
   report limitations and observed results, local signed commit and Cortex
   checkpoint. Do not claim arbitrarily infinite memory, portable deployment,
   general policies, trace support or a universal speedup.
