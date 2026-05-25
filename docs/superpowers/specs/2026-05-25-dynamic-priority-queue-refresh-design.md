# Dynamic Priority Queue Refresh

**Date:** 2026-05-25
**Status:** Approved design, ready for implementation plan
**Component:** `simulatte.server`

## Problem

`simulatte.server.Server` extends `simpy.PriorityResource`. A job's priority is taken as a snapshot at request-construction time, baked into the immutable tuple `PriorityRequest.key`, and never refreshed.

`simulatte/src/simulatte/server.py:49`:

```python
super().__init__(resource=resource, priority=job.priority(resource), preempt=preempt)
```

The parent `simpy.resources.resource.PriorityRequest.__init__` then sets:

```python
self.key = (self.priority, self.time, not self.preempt)
```

Queue ordering uses `req.key`. Once a request is queued, its priority is frozen for the rest of its time in the queue.

This breaks every form of dynamic priority that simulatte's design otherwise supports:

1. **Time-dependent policies** — e.g. SLAR/LumsCor planned slack time, where `job.priority_policy(job, server)` returns a value that depends on `env.now`. Queued jobs do not advance as time passes.
2. **Policy reassignment at runtime** — e.g. an NN dispatcher writes `job.priority_policy = new_closure` on jobs already queued. The new policy never affects queue order.
3. **Mutable state read by the policy** — e.g. a policy that reads an external shared score table. Updates to the table do not propagate to queued requests.

The bug surfaces as: when a new job enters the queue, it is sorted against stale keys of jobs already queued, so the new job lands in the wrong position. When a release fires, `_trigger_put` walks the queue in stale order, so the wrong job is granted next.

## Goal

At every dispatch decision (both put and get paths), the queue is ordered by the current value of `job.priority(server)` for every queued job — evaluated exactly once per request per dispatch decision.

## Non-goals

- **Preemption.** `Server` extends `PriorityResource`, not `PreemptiveResource`; the `preempt` flag is stored but ignored by SimPy. The design must remain compatible with a future switch but does not add preemption.
- **Cross-server priority changes.** Each server's queue is independent.
- **Opt-in / opt-out flag.** Static priority users see no behavior change (re-evaluation of a static policy returns the same value), so the fix is on for everyone.
- **Live `req.key` reads outside dispatch.** External code that wants the current priority of a queued request should call `req.job.priority(req.server)` directly. `req.key` is a snapshot from the most recent dispatch decision.

## priority_policy contract

`priority_policy(job, server)` must be a **deterministic function of `(job, server, current simulation state)`**: repeated calls at the same `env.now` with the same external state must return the same value.

Permitted:

- Reading `env.now`, attributes of `job` and `server`, attributes of other simulation objects, entries of shared dictionaries owned by user code.
- Reassigning `job.priority_policy` to a different callable at any time.
- Mutating shared state outside of policy evaluation (between SimPy events) and having the next dispatch decision see the new value.

Not permitted:

- Consuming RNG inside the policy (`random.random()`, etc.).
- Mutating shared state inside the policy in a way that affects subsequent calls.
- Any side effect that makes repeated calls at the same `env.now` return different values.

This contract is what makes "evaluated exactly once per request per dispatch decision" a meaningful guarantee. If a policy violates the contract, the framework will still call it the documented number of times, but ordering becomes unspecified.

## Design

Two coordinated changes to `simulatte/src/simulatte/server.py`. The first is the core fix; the second is the trigger that makes it automatic.

### Sort with explicit key refresh

`Server.sort_queue` recomputes each queued request's `key` from `job.priority(server)` at call time, then sorts. This is functionally identical to the patch in `pac-ppc/src/pac_ppc/simulation/patches.py`.

```python
class Server(simpy.PriorityResource):
    def sort_queue(self) -> None:
        """Refresh queued requests' priority keys and resort.

        Re-evaluates each queued job's priority_policy and rewrites
        req.key before sorting, so dynamic priority changes (time-dependent
        policies, policy reassignment, mutable external state) take effect.

        Called automatically before every dispatch decision via _trigger_put.
        May also be called explicitly by user code that wants to observe
        the resulting order before the next dispatch event.
        """
        queue_list = cast(list, self.queue)
        for req in queue_list:
            req.key = (req.job.priority(req.server), req.time, not req.preempt)
        queue_list.sort(key=lambda req: req.key)
```

`req.key` is a plain attribute (inherited as a tuple from `simpy.PriorityRequest`). We overwrite it in place with a new tuple — no property, no setter, no SimPy contract violation. SimPy itself never re-reads `req.key` outside of comparisons during a sort, so updating it between sorts is safe.

`ServerPriorityRequest` is unchanged from the current implementation. `self.priority` and the initial `self.key` come from `super().__init__(...)` exactly as today; `sort_queue` overwrites `req.key` from there on.

### Refresh at every dispatch decision

`Server._trigger_put` is overridden to call `sort_queue` before delegating to SimPy:

```python
class Server(simpy.PriorityResource):
    def _trigger_put(self, get_event) -> None:
        self.sort_queue()
        super()._trigger_put(get_event)
```

SimPy invokes `_trigger_put` from exactly two call sites (verified against the installed SimPy source):

- `simpy/resources/base.py:54` — at the end of `Put.__init__`, after `resource.put_queue.append(self)`. Every new arrival fires it.
- `simpy/resources/base.py:104` — registered as a callback on every Get event. Every release fires it.

So overriding `_trigger_put` covers both paths. No other hook is needed.

### Why this is correct

| Path | Mechanism | Correctness |
|---|---|---|
| Put (new arrival) | `Put.__init__` calls `put_queue.append(self)` (which triggers `SortedQueue.append`'s sort over stored tuples — placement may be temporarily wrong for queued items whose priorities have drifted), then immediately calls `_trigger_put(None)` → our override → `sort_queue` refreshes all keys and sorts → `super()._trigger_put` iterates the corrected queue. | ✅ Final order at dispatch is correct. The interim SortedQueue.append sort is not observed by user code (Put.__init__ holds the only reference, no event fires between append and `_trigger_put`). |
| Get (release) | Release event triggers `_trigger_put` via callback → our override → `sort_queue` refreshes all keys and sorts → `super()._trigger_put` iterates the corrected queue and grants. | ✅ The release path is the one B-alone would have broken; here, `_trigger_put`'s explicit refresh handles it correctly. |

### Idempotence

Each priority is evaluated O(1) times per dispatch decision per queued request:

- **New arrival into a queue of N**:
  - 1 evaluation in `ServerPriorityRequest.__init__` (the initial `super().__init__(priority=job.priority(resource), ...)`).
  - 1 evaluation per queued request in `sort_queue` (N+1 total, including the new arrival).
  - Total: N+2 evaluations.
- **Release with N pending requests**:
  - 1 evaluation per queued request in `sort_queue` (N total).
  - Total: N evaluations.

`SortedQueue.append`'s automatic sort does *not* evaluate priorities — it sorts by stored `req.key` tuples. Our override's `sort_queue` is the only place priorities are re-evaluated for queued items.

The new request's priority is evaluated twice on arrival (once in `__init__`, once in `sort_queue`). Both evaluations happen at the same `env.now` and must return the same value per the policy contract; the value used for dispatch is the one written by `sort_queue`. Optimizing this to a single evaluation is possible (pass a sentinel priority to `super().__init__` and rely solely on `sort_queue`) but is deferred — the current shape costs one extra evaluation per arrival, not per queued request, so it does not scale with queue length.

### Why not a live `key` property

An earlier draft of this design made `ServerPriorityRequest.key` a `@property` that recomputed `job.priority(server)` on every read, with a no-op setter to absorb `PriorityRequest.__init__`'s assignment. We rejected this for two reasons:

1. **Non-idempotent evaluation count.** With a live property, every sort over the queue re-evaluates every key — including `SortedQueue.append`'s automatic sort on arrival, which would then double the per-queued-request evaluation count (2N+3 instead of N+2 on arrival). This is not just a performance concern: it would make the framework call user policies an implementation-defined number of times. For policies that strictly satisfy the purity contract, this is invisible. For policies that drift from the contract, it amplifies the drift.
2. **No real use case for live external reads.** The only thing a live property buys over an explicit refresh is that `req.key` reads outside the dispatch flow are always current. Callers who want that can call `req.job.priority(req.server)` directly, which is more explicit anyway.

The current design uses `req.key` as a "snapshot from the most recent dispatch decision." That is documented in the non-goals.

## API impact

- **`Server.sort_queue()`** — contract change. Previously sorted by frozen keys; now refreshes keys from `job.priority(server)` then sorts. Docstring updated.
- **`Server._trigger_put`** — new override. Internal SimPy method; not part of the documented user API but worth noting in the changelog.
- **`ServerPriorityRequest`** — no changes. `self.priority` remains a construction-time snapshot. `self.key` is mutated in place by `sort_queue`.
- **No new public API**, no opt-in flag, no breaking signature changes for user code.

## Compatibility

- **Static priority policies:** no observable change. Re-evaluation of a static policy is idempotent.
- **Dynamic priority policies that obey the contract:** behavior corrects. Any downstream test that asserts the *buggy* ordering will fail and needs updating.
- **Dynamic priority policies that violate the contract** (RNG, in-policy state mutation): behavior was already undefined under the old code (priorities frozen) and remains undefined under the new code (sort order depends on whichever call to `priority_policy` is taken as authoritative). Documented.
- **pac-ppc:** the monkey-patch in `pac_ppc/simulation/patches.py:30-46` becomes redundant — its body is essentially identical to the new `Server.sort_queue`. The patch can be removed in a follow-up PR; leaving it in place is also safe (it overrides `sort_queue` with the same logic). The five `apply_patches()` call sites in pac-ppc become no-ops worth deleting.
- **Simulatte API stability:** per `CLAUDE.md`, simulatte's APIs are explicitly unstable. This change does not require a deprecation cycle.

## Testing strategy

New tests live in `tests/core/test_server.py`. They are the empirical contract for the design.

### Regression — static priorities unchanged

- `test_static_priorities_unchanged`: existing `TestPriorityQueueOrdering` tests (lines 277-507) must continue to pass without modification.

### New — put path correctness

- `test_new_arrival_sorted_against_fresh_keys_of_queued_jobs`: queue jobs A and B with policies reading from a shared dict. Mutate the dict so A's effective priority should now rank ahead of B. `request()` a new job C with intermediate priority. Assert the queue order at dispatch reflects the *current* dict values, not the snapshot taken when A and B were queued.

### New — get path correctness (the bug A-alone-without-refresh and B-alone both miss)

- `test_release_dispatches_by_live_priority_not_stale_order`: queue A and B at time `t0`. Advance `env.now` so a time-dependent policy inverts their ranking. Trigger a release. Assert the dispatched job is whichever ranks highest *now*. This is the test that proves `sort_queue` actually refreshes keys (a naïve sort by stored `req.key` would fail it).

### New — policy reassignment

- `test_policy_reassignment_affects_next_dispatch`: queue A and B with static policies. Reassign `A.priority_policy = new_fn` so the new function ranks A above B. Trigger a release. Assert A is dispatched first.

### New — external mutable state

- `test_external_state_change_affects_dispatch`: policy reads from a shared dict; mutate the dict between events; assert the next dispatch decision uses the new values.

### New — `sort_queue()` public API

- Extend the existing `test_sort_queue` (lines 137-185) so that priority policies for queued jobs change between request creation and the explicit `sort_queue()` call. Assert the resulting order reflects the updated priorities, not the construction-time snapshot.

### New — contract-violation behavior is bounded

- `test_stochastic_policy_does_not_crash`: a policy that uses an RNG (violating the contract) does not raise an exception; ordering is unspecified but the simulation completes. This is a guard against introducing assertions that assume idempotence.

### Reference: pac-ppc regression tests

The three tests in `pac-ppc/tests/test_migration_regression.py:33-126` are the clearest published reproducers of the bug pattern. Adapt their structure for the simulatte-side tests; once the upstream fix lands, those tests should pass against unpatched simulatte.

## Implementation order

1. Replace the body of `Server.sort_queue` with the refresh-and-sort implementation.
2. Add the `Server._trigger_put` override that calls `self.sort_queue()` before `super()`.
3. Update docstrings on `Server.sort_queue` and `ServerPriorityRequest` to describe the new behavior, the contract, and the per-dispatch evaluation count.
4. Add the new tests in `tests/core/test_server.py`.
5. Run the full test suite (`uv run pytest`); fix any test that asserted the old buggy ordering.
6. Update any module-level documentation in `docs/` that describes the queue as static-priority.

## Future work (out of scope)

- **pac-ppc cleanup**: remove `apply_patches()` calls and `patches.py` once a simulatte release containing this fix is pinned.
- **Single evaluation per arrival**: optimize so that `ServerPriorityRequest.__init__` does not call `job.priority(resource)` — defer to `sort_queue` for the only evaluation. Saves one priority call per arrival regardless of queue length.
- **`PreemptiveResource` compatibility**: a preemptive variant of `Server` would also need to refresh `users`' keys (currently set at grant time and not refreshed) before preemption decisions. Not needed today; would need its own design.
- **Documentation**: add a section to the simulatte docs explaining dynamic priorities — the priority_policy contract, the cost model (N+2 priority evaluations per arrival, N per release, where N is queue length), and recommended patterns.
