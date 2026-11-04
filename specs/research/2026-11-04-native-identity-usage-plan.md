# Public identity helper: representative usage experiment

Davide authorized the next paired end-to-end experiment after signed helper commit
f6693d39c2cdb1531594f5f46f54d181bd89c81c. Work inline in the existing research
worktree. No product changes, release, push or deployment baseline selection.

## Questions and fixed comparisons

1. Does the public helper, including immutable result creation and dictionary
   detachment at each observation, retain the prepared prototype's cost benefit?
2. At periodic observation rates, how much of complete managed wall time changes?
3. In a fresh-state query, do composition, validation, callbacks or native hashing
   dominate now? Profile separately; never use instrumented times as latency data.

Use the frozen three-server SPT/ConWIP feedback models: fixed specifications with
reporting-independent control, and KPI-fed control that can change job admission
and requirements. These are proxies for v3 use, not measured production workloads.
Keep the frozen models, action ledger, pure callback, validator, binary parser and
prior evidence unchanged. Copy only into the new research namespace/external runs.

Primary installed macOS arm64 CPython3.14 study: bounded retention, 32,768 polls,
0.5 simulation units per poll, warmup10, six paired seeds100–105. Compare public
helper, frozen prepared and frozen unprepared at endpoint-only, every64polls
(32simulation units), every16polls (8units), and everypoll (stress). All six
permutations of the three consumers occur once within each model/rate cell;
shuffle blocks and permutation assignments with a fixed seed before execution.
This gives144 serial fresh Coordinator attempts. Keep the same validator and full
dictionary materialization in every consumer. Six further runs (two models×three
seeds) disable the complete identity instrumentation; interpret that control as
including action-ledger overhead, not solely query cost. Reproduce one helper
periodic request per model after deleting all project Python sources (two attempts).

Every paired run and rate must preserve exact model artifacts/default fingerprint;
the disabled control may omit only the identity artifact. Distinct attempts/no
reuse, pinned installed origins, original complete authority and query counts are
required. Record cold Coordinator lifecycle and dispatch-to-publication wall times,
parent/child/tree RSS, native/callback/envelope/storage sizes. Do not retain every
identity or insert timers in the live query path. No timing threshold is a semantic
gate. Report every primary sample/outlier; no outcome-dependent sample replacement.

Separate query attribution: both models, full/bounded retention, at65 and4096
simulation units (eight cells). At each synchronized boundary run frozen-prepared,
unprepared and public-helper composition and complete fresh-hook+validator pipelines,
plus object-only/detachment/hook/validation/native-hash/callback stages. Five shuffled
adaptive25ms blocks with normal GC, wall/thread CPU; cProfile and tracemalloc passes
after latency only. Check exact canonical bytes/size and unchanged native/controller
state before and after, then complete a matching uninstrumented model run.

## Verification and evidence

Preflight short installed runs and focused neighboring tests precede source freeze.
Reuse the exact validated new core/native wheels. Hash frozen sources, model copies,
installed distributions/assets and evidence. Final serial timing starts only after
all measured code is frozen; no tests/builds/profilers during live timing. Run the
query study separately. Audit every f6693d3 base file remains byte-identical, exact
cross-consumer/rate semantics and bounded-size diagnostics. Analyze paired ratios
and seed ranges; medians of paired ratios differ from ratios of medians. With six
seeds, report uncertainty as limited evidence rather than universal speedups.
Prepare an inspected standalone figure, report, signed local commit and Cortex
checkpoint. Linux/ABI compatibility from the preceding gate is not a new timing
claim for other hosts. Existing historical formatting findings remain untouched.
