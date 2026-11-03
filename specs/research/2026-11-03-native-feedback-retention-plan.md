# Bounded feedback admission validation

Davide authorized feedback-driven admission after the Linux integration gate.
Base: signed `d26edb967b9bfc2eab7357910e0c1efbe056dcb5`. Work inline in the
isolated native research worktree. Preserve all earlier code/evidence; native
remains v3 research after v2/Studio, with release work deferred.

- A self-contained model uses only the public experimental provider and ordinary
  Python ShopFloor/ConWIP/ShopFloorKPIs. Its Python controller owns at most a fixed
  number of live handles, no completed-job/template/decision history. Generate
  each candidate only after admission credit is available. Commit history with
  rolling hashes; include seed/cursor/live identities/phase/next wake/configuration
  in the pure native input-state callback.
- Feedback changes route/duration from current pool state, and admission target
  from live KPI state. A fixed hard limit provides backpressure, distinct from
  the native ConWIP floor cap. Check a feedback-disabled counterfactual.
- Observe at explicit right-open urgent boundaries, before ordinary events at
  that timestamp, matching advance(t). Test ties, partial Horizon/warmup, FIFO/SPT,
  two controller/floor caps and paired seeds. Compare every recorded observation,
  decision, admitted job, completion detection and all eight KPIs against the
  ordinary Python model, then compare full/bounded native modes.
- Stream deterministic proof rows to gzip for correctness, never a list. Audit
  decision rules and generated inputs independently; reject deliberately altered
  evidence. Memory cases retain only rolling commitments. Validate query purity,
  expired-handle cleanup, callback completeness/purity, hard bounds and exercised
  feedback/backpressure branches with focused neighboring research tests.
- Use clean installed matching wheels outside the checkout, normal two-slot
  Coordinator execution and source-deleted fresh reproduction. Compare state
  fingerprints within the same backend/schema; physical proofs across engines.
- Serial memory matrix: 4,096/32,768/262,144 decision ticks, three paired seeds,
  full/bounded native modes, same digest/KPI trace path and controller. Include
  extra pure queries and repeated attempts in one parent. No concurrent tests or
  compilers. Acceptance: bounded child median range <16 MiB, tree <24 MiB, parent
  <16 MiB; largest full child exceeds bounded by >16 MiB; <=hard-limit owned
  handles, <8 KiB callback state and <128 KiB retained bounded native storage.
- Save raw evidence, exact source/wheel hashes, report, signed local commit and
  Cortex checkpoint. No push, publication, production API expansion or rollout.
