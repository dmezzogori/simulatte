# FOCUS context memoization — design

**Date:** 2026-05-29
**Scope:** Review findings #4 and #5 (efficiency) on `feat/draco-focus`.
**Status:** Approved (design); pending implementation plan.

## Problem

Two redundant-computation findings in the FOCUS/DRACO dispatch path:

- **#4 — `build_context` rebuilt per queued request.** `Server.sort_queue`
  (`server.py:309`) is a synchronous loop calling `req.job.priority(req.server)`
  for all *N* queued requests, with no state mutation between calls. For
  FOCUS/DRACO that routes to `FocusPriorityRule.__call__` (`focus.py:454`) and
  `Draco._queue_side_score` (`draco.py:327`), each of which calls
  `Focus.build_context` from scratch (DRACO also `_count_wip`). All *N*
  contexts in one pass are byte-identical, so one sort is `O(N·|O|)` — or
  `O(N·|O|·|J|)` with beta active — instead of `O(N+|O|)`. `_trigger_put` runs
  `sort_queue` on *every* arrival and *every* release, so this is paid
  continuously. (`|O|` = queued jobs shop-wide + PSP; `|J|` = server count.)

- **#5 — `_delta_entropy` computed twice per in-set candidate.** Within a
  *single* `build_context`, the beta pass (`focus.py:288`) computes
  `c_i = _delta_entropy(...)` for every job in `O` but keeps only the max;
  then `beta()` (`focus.py:392`) recomputes the identical `_delta_entropy` per
  scored candidate. Independent of #4: even with context caching, one build
  still double-computes `c_i`.

Both are pure efficiency issues. The fix must be **behavior-preserving** for
all valid callers.

## Constraints / decisions

- **Policy-local, no shopfloor or interface changes** (chosen approach). The
  memo lives inside the policy classes; `priority_policy(job, server)` and
  `Server.sort_queue` are untouched.
- **Correct identity fingerprint, not count-based.** A count-based fingerprint
  (`env.now` + per-server queue/users counts + psp length) can collide on a
  same-instant job swap (one job leaves a queue, another enters — equal counts,
  different jobs) and silently serve a stale context. In a research/repro
  framework that is an invisible-in-output bug, so the fingerprint is
  identity-based and provably correct.
- **Fold `_count_wip` into DRACO's memo** (addresses that part of #4) rather
  than leave it recomputed per call.

## Design

### #5 — cache `c_i` in `FocusContext` (do first; independent, lowest risk)

- Add a frozen field to `FocusContext`:

  ```python
  c_values: Mapping[BaseJob, float] = field(default_factory=lambda: MappingProxyType({}))
  ```

  The `default_factory` keeps any direct `FocusContext(...)` construction valid
  (back-compat) and matches the existing `MappingProxyType` treatment of
  `server_index`.

- `build_context`'s beta pass populates a local `dict[BaseJob, float]` mapping
  `job -> c_i` (computed at `remaining[0]`), wrapped in `MappingProxyType` on
  return. When `compute_beta=False`, `c_values` stays the empty mapping.

- `beta(job, server, ctx)`:

  ```python
  c_i = ctx.c_values.get(job)
  if c_i is None:
      c_i = _delta_entropy(job=job, server=server, workloads=ctx.workloads,
                           server_index=ctx.server_index, pre_entropy=ctx.pre_entropy)
  ```

  then the existing guards (`c_i <= 0 -> 0`; `max_positive_c <= 0 -> 0`) apply
  unchanged.

- **Correctness.** The cached `c_i` is computed at `remaining[0]`. The existing
  documented invariant — callers must pass `server == job.unfinished_routing[0]`
  — means valid callers get exactly what a fresh compute would return. Out-of-set
  callers (`job not in c_values`) miss and compute fresh, identical to today.
  Invariant-violating callers (`server != remaining[0]`) already have undefined
  (`beta > 1`) behavior per the docstring; the cache does not worsen that. The
  beta-invariant docstring is updated to note it now also governs cache validity.

### #4 — `_StateMemo` helper (memoize `build_context` across a sort pass)

- New internal helper in `focus.py`:

  ```python
  class _StateMemo:
      """Single-entry memo of a value derived from frozen shop state.

      sort_queue invokes the priority policy once per queued request within one
      synchronous pass, during which env.now and the scanned shop state are
      frozen; build_context would otherwise recompute identically N times. This
      caches the last payload keyed on an identity fingerprint of the scanned
      state, rebuilding only when that fingerprint changes.
      """
      def __init__(self, shopfloor, *, psp, build):
          self._shopfloor = shopfloor
          self._psp = psp
          self._build = build          # callable(now) -> payload
          self._key = None
          self._value = None

      def _fingerprint(self, now):
          servers = self._shopfloor.servers
          return (
              now,
              tuple(j for s in servers for j in s.queueing_jobs),
              tuple(j for s in servers for j in s.current_jobs),
              tuple(self._psp.jobs) if self._psp is not None else (),
          )

      def get(self, now):
          key = self._fingerprint(now)
          if key != self._key:
              self._value = self._build(now)
              self._key = key
          return self._value
  ```

  Key elements are job objects; `BaseJob` defines no `__eq__`, so tuple
  comparison is identity-based and short-circuits on the first difference. The
  memo holds only the last key (the referenced jobs are alive in the
  queues/PSP anyway, so no meaningful retention).

- **`FocusPriorityRule`** holds `self._memo = _StateMemo(shopfloor, psp=psp,
  build=lambda now: focus.build_context(shopfloor, now, psp=psp, compute_beta=focus.w5 != 0.0))`.
  `__call__` becomes `ctx = self._memo.get(now); return -self.focus.score(job, server, ctx, now)`.

- **`Draco`** holds `self._memo = _StateMemo(shopfloor, psp=psp,
  build=lambda now: (focus.build_context(...), self._count_wip()))`.
  `_queue_side_score` pulls `ctx, wip = self._memo.get(now)`; `decide_next_job`
  routes through the same memo for uniformity (it runs once per completion, so
  this is for consistency, not the hot path — and the post-release /
  pre-re-enqueue state there is a distinct fingerprint, so it does not collide
  with the subsequent sort's state).

- **Correctness — fingerprint completeness.** Every `FocusContext` field
  derives only from `(now, queued jobs, in-service jobs, psp jobs)`:
  `max_pij`/`max_positive_slack`/`max_positive_pacing`/`max_positive_c`/
  `c_values` from the jobs scan (queue + psp) plus `now`; `workloads` and
  `pre_entropy` from queue + users membership; `empty_queue_servers` from queue
  membership; `server_index` is constant (servers are fixed). A job's
  `unfinished_routing` only changes when it leaves the scanned set (release
  stamps `servers_exit_at` and removes it from `users`), so identity membership
  captures it. DRACO's `wip = Σ(len(queue)+count)` likewise depends only on
  queue + users membership. Therefore the fingerprint is sufficient.

## Performance

- The fingerprint is `O(|O|)` (reference collection only). On a cache hit it
  skips `build_context`'s arithmetic — the `_entropy` pass, slack/pacing math,
  and the `O(|O|·|J|)` beta pass — plus DRACO's `_count_wip`. Net: a large win
  when beta is active, a modest constant-factor win otherwise. Within one
  `sort_queue` pass all *N* calls after the first are hits.

## Testing

- `test_draco_priority_policy_rebuilds_ctx_per_server` **flips meaning**: today
  it asserts `build_context` is called twice (the redundancy under review). It
  becomes a cache-behavior test — one build reused across two servers at the
  same state (`call_count == 1`), **and** a rebuild after a state mutation
  (add/remove a job → `call_count` increments). This guards both the win and
  the correctness (no over-caching).
- Add the `FocusPriorityRule` mirror: two `__call__`s at the same state share
  one build; a state change forces a rebuild.
- #5: a test that `beta()` reuses `ctx.c_values` (e.g. spy on `_delta_entropy`,
  assert it is not re-called for an in-set candidate), and a test that an
  out-of-set job still computes fresh (miss path).
- Full suite must stay green; coverage gate is 99% and `focus.py`/`draco.py`
  must remain at 100%. ruff + ty clean.

## Out of scope

- The #2 normalizer-population (`O` set) literature question — already a `TODO`.
- Review finding #7 (capacity>1 force-flag clearing) and the #11/#12 reuse nits.
- Any change to the `priority_policy` / `Server.sort_queue` contract.
