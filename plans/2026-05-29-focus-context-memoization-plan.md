# FOCUS Context Memoization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the two redundant-computation findings (#4, #5) in the FOCUS/DRACO dispatch path without changing behavior for valid callers.

**Architecture:** (#5) Cache each job's `c_i` in `FocusContext` so `beta()` looks it up instead of recomputing `_delta_entropy`. (#4) A policy-local single-entry `_StateMemo` memoizes `build_context` across the repeated per-request calls inside one `Server.sort_queue` pass, keyed on an identity fingerprint of the scanned shop state; DRACO additionally caches `_count_wip` keyed on `FocusContext` identity.

**Tech Stack:** Python 3.12+, SimPy, pytest (`--no-cov` for single-test runs), ruff, ty. Design doc: `plans/2026-05-29-focus-context-memoization-design.md`.

**Conventions for every test run in this plan:**
- Single test: `uv run pytest <path>::<name> --no-cov -q`
- Full suite (coverage gate 99%): `uv run pytest -q`
- Lint/type: `uv run ruff check src tests` and `uv run ty check src`

---

## File Structure

- `src/simulatte/dispatching_rules/focus.py` — add `field` import + `Callable` type import; add `c_values` field to `FocusContext`; populate it in `build_context`; consume it in `beta`; add the `_StateMemo` class; wire it into `FocusPriorityRule`.
- `src/simulatte/policies/draco.py` — import `_StateMemo`; add `_ctx_memo`/`_last_ctx`/`_last_wip` to `__init__`; add `_ctx_and_wip` helper; route `decide_next_job` and `_queue_side_score` through it.
- `tests/core/test_focus.py` — add `beta` cache-reuse test, `beta` uncached-fallback test, `FocusPriorityRule` memoization test.
- `tests/core/test_draco.py` — rewrite `test_draco_priority_policy_rebuilds_ctx_per_server` into a memoization test.

---

## Task 1: Cache `c_i` in `FocusContext` (#5)

**Files:**
- Modify: `src/simulatte/dispatching_rules/focus.py`
- Test: `tests/core/test_focus.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_focus.py` (the file already imports `Focus`, `Environment`, `ProductionJob`, `Server`, `ShopFloor`, and `pytest`; add the `import simulatte.dispatching_rules.focus as focus_mod` line at the top of the file if not present):

```python
def test_focus_beta_reuses_cached_c_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """beta() reads c_i from ctx.c_values instead of recomputing _delta_entropy."""
    import simulatte.dispatching_rules.focus as focus_mod

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    focus = Focus(weights=(0.0, 0.0, 0.0, 0.0, 1.0))  # beta-only → compute_beta default True

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    cand = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=1000.0)
    sf.add(blocker)
    sf.add(cand)
    env.run(until=0.001)  # blocker in s1.users, cand in s1.queue

    ctx = focus.build_context(sf, now=0.001)
    assert cand in ctx.c_values  # c_i cached at build time

    def boom(**kwargs: object) -> float:
        raise AssertionError("beta recomputed _delta_entropy for a cached job")

    monkeypatch.setattr(focus_mod, "_delta_entropy", boom)
    # cand's first uncompleted server is s1 (the invariant) → beta must hit the cache.
    focus.beta(cand, s1, ctx)  # must not raise


def test_focus_beta_computes_for_uncached_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """beta() falls back to _delta_entropy for a job absent from ctx.c_values."""
    import simulatte.dispatching_rules.focus as focus_mod

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    focus = Focus(weights=(0.0, 0.0, 0.0, 0.0, 1.0))

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.001)
    ctx = focus.build_context(sf, now=0.001)

    outsider = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=1000.0)
    assert outsider not in ctx.c_values

    calls: list[object] = []
    real_delta = focus_mod._delta_entropy

    def spy(**kwargs: object) -> float:
        calls.append(kwargs["job"])
        return real_delta(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(focus_mod, "_delta_entropy", spy)
    focus.beta(outsider, s1, ctx)
    assert outsider in calls  # computed fresh for the uncached job
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_focus.py::test_focus_beta_reuses_cached_c_value tests/core/test_focus.py::test_focus_beta_computes_for_uncached_job --no-cov -q`
Expected: FAIL — `AttributeError: 'FocusContext' object has no attribute 'c_values'`.

- [ ] **Step 3: Add the `field` import**

In `src/simulatte/dispatching_rules/focus.py`, change line 30:

```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: Add the `c_values` field to `FocusContext`**

In the `FocusContext` attributes docstring (after the `max_positive_c:` entry, ~line 144), add:

```python
        c_values: Read-only mapping ``job -> c(i)`` for every job in ``O`` with
            remaining ops, computed in the beta pass at the job's first
            uncompleted server. Empty when ``compute_beta=False``. Lets
            ``beta`` reuse the per-job entropy delta instead of recomputing it.
```

Then, after the `max_positive_c: float` field (~line 154), add:

```python
    c_values: Mapping[BaseJob, float] = field(default_factory=lambda: MappingProxyType({}))
```

- [ ] **Step 5: Populate `c_values` in `build_context`**

In `build_context`, just after `max_positive_c = 0.0` (~line 266), add:

```python
        c_values: dict[BaseJob, float] = {}
```

Inside the `if compute_beta:` block, after the `if c_i > max_positive_c:` update (~line 298), add the store (same indentation as `c_i = _delta_entropy(...)`):

```python
                c_values[job] = c_i
```

In the `return FocusContext(...)` call (~line 300), add the field after `max_positive_c=max_positive_c,`:

```python
            c_values=MappingProxyType(c_values),
```

- [ ] **Step 6: Consume the cache in `beta`**

In `Focus.beta`, replace the opening `_delta_entropy` call (~lines 392-398):

```python
        c_i = _delta_entropy(
            job=job,
            server=server,
            workloads=ctx.workloads,
            server_index=ctx.server_index,
            pre_entropy=ctx.pre_entropy,
        )
```

with a cache-then-compute lookup:

```python
        c_i = ctx.c_values.get(job)
        if c_i is None:
            c_i = _delta_entropy(
                job=job,
                server=server,
                workloads=ctx.workloads,
                server_index=ctx.server_index,
                pre_entropy=ctx.pre_entropy,
            )
```

(The two guards below — `if c_i <= 0.0: return 0.0` and `if ctx.max_positive_c <= 0: return 0.0` — stay unchanged.)

Also extend the `beta` docstring's "Invariant" paragraph with one sentence: the same `server == unfinished_routing[0]` invariant now also governs cache validity (a valid caller's cached `c_i` equals a fresh compute).

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/core/test_focus.py::test_focus_beta_reuses_cached_c_value tests/core/test_focus.py::test_focus_beta_computes_for_uncached_job --no-cov -q`
Expected: PASS (2 passed).

- [ ] **Step 8: Run the full FOCUS + DRACO suites and lint/type**

Run: `uv run pytest tests/core/test_focus.py tests/core/test_draco.py --no-cov -q`
Expected: all pass.
Run: `uv run ruff check src tests` and `uv run ty check src`
Expected: both clean.

- [ ] **Step 9: Commit**

```bash
git add src/simulatte/dispatching_rules/focus.py tests/core/test_focus.py
git commit -m "perf(focus): cache per-job c_i in FocusContext so beta reuses it (review #5)"
```

---

## Task 2: `_StateMemo` + `FocusPriorityRule` memoization (#4, part 1)

**Files:**
- Modify: `src/simulatte/dispatching_rules/focus.py`
- Test: `tests/core/test_focus.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_focus.py` (add `FocusPriorityRule` to the existing `from simulatte.dispatching_rules.focus import ...` line, or import it directly):

```python
def test_focus_priority_rule_memoizes_ctx_across_unchanged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """FocusPriorityRule reuses one FocusContext across calls at unchanged state.

    sort_queue invokes the priority policy once per queued request within a
    single synchronous pass (state frozen). The per-request build_context is
    memoized: same state -> one build reused across servers; a state change
    -> a rebuild.
    """
    from simulatte.dispatching_rules.focus import FocusPriorityRule

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    focus = Focus()
    rule = FocusPriorityRule(focus, sf)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=50.0)
    sf.add(job)  # scheduled; jobs carry no priority_policy here, so the memo stays cold until called

    call_count = 0
    real_build = Focus.build_context

    def counting_build(shopfloor, now, *, psp=None, compute_beta=True):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp, compute_beta=compute_beta)

    monkeypatch.setattr(Focus, "build_context", staticmethod(counting_build))

    # Same state (now=0, all empty), two servers → exactly one build (ctx is
    # shop-wide / server-agnostic).
    rule(job, s1)
    rule(job, s2)
    assert call_count == 1

    # Mutate the scanned state at the same instant by enqueuing another job,
    # then advancing a hair so it settles → next call rebuilds.
    sf.add(ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0))
    env.run(until=0.001)
    rule(job, s1)
    assert call_count == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_focus.py::test_focus_priority_rule_memoizes_ctx_across_unchanged_state --no-cov -q`
Expected: FAIL — `assert call_count == 1` fails with `call_count == 2` (today `__call__` rebuilds every call).

- [ ] **Step 3: Add the `Callable` type import**

In `src/simulatte/dispatching_rules/focus.py`, change the `TYPE_CHECKING` import (~line 35):

```python
    from collections.abc import Callable, Iterable, Mapping, Sequence
```

- [ ] **Step 4: Add the `_StateMemo` class**

Insert immediately after the `FocusContext` class (after its closing, ~line 155, before `class Focus`):

```python
class _StateMemo:
    """Single-entry memo of a FocusContext derived from frozen shop state.

    ``Server.sort_queue`` invokes the priority policy once per queued request
    within one synchronous pass, during which ``env.now`` and the scanned shop
    state are frozen; ``Focus.build_context`` would otherwise recompute an
    identical context once per request. This caches the last context keyed on
    an identity fingerprint of the scanned state — ``now`` plus the identities
    of all queued, in-service, and PSP jobs — rebuilding only when the
    fingerprint changes. The fingerprint provably determines the context: every
    ``FocusContext`` field derives from those jobs and ``now``, and a job's
    ``unfinished_routing`` changes only when it leaves the scanned set.
    """

    def __init__(
        self,
        shopfloor: ShopFloor,
        *,
        psp: PreShopPool | None,
        build: Callable[[float], FocusContext],
    ) -> None:
        self._shopfloor = shopfloor
        self._psp = psp
        self._build = build
        self._key: tuple[object, ...] | None = None
        self._ctx: FocusContext | None = None

    def _fingerprint(self, now: float) -> tuple[object, ...]:
        servers = self._shopfloor.servers
        return (
            now,
            tuple(j for s in servers for j in s.queueing_jobs),
            tuple(j for s in servers for j in s.current_jobs),
            tuple(self._psp.jobs) if self._psp is not None else (),
        )

    def get(self, now: float) -> FocusContext:
        key = self._fingerprint(now)
        if self._ctx is None or key != self._key:
            self._ctx = self._build(now)
            self._key = key
        return self._ctx
```

- [ ] **Step 5: Wire `_StateMemo` into `FocusPriorityRule`**

In `FocusPriorityRule.__init__` (~line 449), after `self.psp = psp`, add:

```python
        self._memo = _StateMemo(
            shopfloor,
            psp=psp,
            build=lambda now: focus.build_context(shopfloor, now, psp=psp, compute_beta=focus.w5 != 0.0),
        )
```

Replace `FocusPriorityRule.__call__` body (~lines 454-457) with:

```python
    def __call__(self, job: BaseJob, server: Server) -> float:
        now = self.shopfloor.env.now
        ctx = self._memo.get(now)
        return -self.focus.score(job, server, ctx, now)
```

Update the `FocusPriorityRule` class "Liveness guarantee" docstring paragraph to say the context is rebuilt whenever the scanned shop state changes (memoized within a single `sort_queue` pass), rather than "rebuilds `ctx` per invocation".

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_focus.py::test_focus_priority_rule_memoizes_ctx_across_unchanged_state --no-cov -q`
Expected: PASS.

- [ ] **Step 7: Run the FOCUS + DRACO suites and lint/type**

Run: `uv run pytest tests/core/test_focus.py tests/core/test_draco.py --no-cov -q`
Expected: all pass.
Run: `uv run ruff check src tests` and `uv run ty check src`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/simulatte/dispatching_rules/focus.py tests/core/test_focus.py
git commit -m "perf(focus): memoize build_context per sort pass via _StateMemo (review #4)"
```

---

## Task 3: DRACO memoization with folded `_count_wip` (#4, part 2)

**Files:**
- Modify: `src/simulatte/policies/draco.py`
- Test: `tests/core/test_draco.py`

- [ ] **Step 1: Rewrite the ctx-rebuild test as a memoization test**

In `tests/core/test_draco.py`, replace the entire `test_draco_priority_policy_rebuilds_ctx_per_server` function (currently ~lines 714-754, including its `monkeypatch` parameter) with:

```python
def test_draco_priority_policy_memoizes_ctx_across_unchanged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draco reuses one FocusContext across calls at unchanged shop state.

    sort_queue calls priority_policy once per queued request within a single
    synchronous pass (state frozen). Each call no longer rebuilds the context:
    identical state -> one build reused across servers; a state change -> a
    rebuild. (build_context is shop-wide / server-agnostic.)
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=10, loop_target=5)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=50.0)
    sf.add(job)  # scheduled, not yet enqueued (no env.run) → memo cold

    call_count = 0
    real_build = Focus.build_context

    def counting_build(shopfloor, now, *, psp=None, compute_beta=True):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp, compute_beta=compute_beta)

    monkeypatch.setattr(Focus, "build_context", staticmethod(counting_build))

    # Same state, two servers → exactly one build reused.
    draco.priority_policy(job, s1)
    draco.priority_policy(job, s2)
    assert call_count == 1

    # Mutate the scanned state at the same instant (a PSP arrival) → rebuild.
    other = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    psp.add(other)
    draco.priority_policy(job, s1)
    assert call_count == 2
```

Also update the section comment above it (currently `# ----- priority_policy: liveness (ctx rebuilt per call / per server) -----`) to:

```python
# ----- priority_policy: ctx memoization (rebuilt only when shop state changes) -----
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_draco.py::test_draco_priority_policy_memoizes_ctx_across_unchanged_state --no-cov -q`
Expected: FAIL — `assert call_count == 1` fails with `call_count == 2` (today `_queue_side_score` rebuilds every call).

- [ ] **Step 3: Import `_StateMemo` in draco.py**

Change the import (~line 21):

```python
from simulatte.dispatching_rules.focus import Focus, FocusContext, _StateMemo, _next_server_after
```

- [ ] **Step 4: Add the memo and wip cache to `Draco.__init__`**

At the end of `Draco.__init__`, after `self._forced_at_server: dict[Server, ProductionJob] = {}` (~line 163), add:

```python
        self._ctx_memo = _StateMemo(
            self._shopfloor,
            psp=self._psp,
            build=lambda now: self.focus.build_context(
                self._shopfloor, now, psp=self._psp, compute_beta=self.focus.w5 != 0.0
            ),
        )
        self._last_ctx: FocusContext | None = None
        self._last_wip: int = 0
```

- [ ] **Step 5: Add the `_ctx_and_wip` helper**

Add this method to `Draco` (place it just above `_queue_side_score`):

```python
    def _ctx_and_wip(self, now: float) -> tuple[FocusContext, int]:
        """Shared, memoized (ctx, wip) for the decision and queue-ordering paths.

        The memo returns a new FocusContext object exactly when the scanned
        shop state changes, so count-WIP (over that same state) is recomputed
        only when the context is rebuilt — collapsing both the per-request
        build_context and _count_wip in a sort_queue pass to one evaluation.
        """
        ctx = self._ctx_memo.get(now)
        if ctx is not self._last_ctx:
            self._last_ctx = ctx
            self._last_wip = self._count_wip()
        return ctx, self._last_wip
```

- [ ] **Step 6: Route `decide_next_job` through the helper**

In `decide_next_job`, replace the `now`/`wip`/`ctx` block (currently the comment plus `wip = self._count_wip()` and the `ctx = self.focus.build_context(...)` line, ~lines 209-216) with:

```python
        now = self._shopfloor.env.now
        # ctx and wip come from the shared memo. The callback fires before the
        # just-finished job re-enters its next server's queue, so a multi-op
        # triggering job is (deliberately) not counted — see the class docstring
        # "Decision instant" note; the force-pin makes dispatch follow this
        # decision verbatim, so R/A/D are consistent at this single instant.
        ctx, wip = self._ctx_and_wip(now)
```

(Keep the `psp = self._psp` line and the flag-clear `self._forced_at_server.pop(server_k, None)` exactly as they are above this block.)

- [ ] **Step 7: Route `_queue_side_score` through the helper**

Replace the body of `_queue_side_score` (~lines 336-339) with:

```python
        now = self._shopfloor.env.now
        ctx, wip = self._ctx_and_wip(now)
        return self._full_score(job, server, ctx, now, wip, in_psp=False)
```

Update the `_queue_side_score` docstring: it now pulls a memoized `ctx`/`wip` (shared with `decide_next_job`) rather than rebuilding per call.

- [ ] **Step 8: Run the rewritten test to verify it passes**

Run: `uv run pytest tests/core/test_draco.py::test_draco_priority_policy_memoizes_ctx_across_unchanged_state --no-cov -q`
Expected: PASS.

- [ ] **Step 9: Run the full suite, lint, type, and examples**

Run: `uv run pytest -q`
Expected: all pass; coverage gate (99%) reached; `focus.py` and `draco.py` at 100%.
Run: `uv run ruff check src tests` and `uv run ty check src`
Expected: both clean.
Run: `uv run python examples/draco_simple.py` and `uv run python examples/focus_simple.py`
Expected: both run cleanly and print sensible job counts.

- [ ] **Step 10: Commit**

```bash
git add src/simulatte/policies/draco.py tests/core/test_draco.py
git commit -m "perf(draco): share memoized ctx/wip across decision and queue-ordering paths (review #4)"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** #5 → Task 1; #4 context memo → Tasks 2-3; folded `_count_wip` → Task 3 (`_ctx_and_wip`); identity fingerprint → Task 2 `_StateMemo._fingerprint`; the test that flips → Task 3 Step 1; `FocusPriorityRule`/`beta` tests → Tasks 2/1. Covered.
- **Placeholders:** none — every code step shows complete code and exact run/expected.
- **Type consistency:** `_StateMemo(shopfloor, *, psp, build)` and `.get(now) -> FocusContext` used identically in `FocusPriorityRule` (Task 2) and `Draco` (Task 3). `_ctx_and_wip(now) -> tuple[FocusContext, int]` used by both DRACO call sites. `c_values` field name consistent across Task 1 steps.

## Notes / risks for the executor

- `FocusContext` gains a field with a `default_factory`, so any direct `FocusContext(...)` construction in tests stays valid. If a test breaks on the new field, it is constructing the dataclass positionally — fix by passing `c_values=` or relying on the default.
- The DRACO wip change keeps **plain** `_count_wip()` (no `+1`) — this matches the current committed behavior; do not reintroduce a correction.
- Keep all three commits behavior-preserving: the integration tests in `test_draco.py` (PSP-winner, queue-winner, force-flag) and the example job counts are the end-to-end guard.
