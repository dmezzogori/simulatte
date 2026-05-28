# DRACO/FOCUS Review Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every finding (should-fix and nice-to-have) from the DRACO/FOCUS branch review — DRY cleanups, a safe perf optimization, stronger tests, a standalone-FOCUS builder, and full web-docs + agent-skill coverage — without changing dispatch behavior.

**Architecture:** Pure follow-up work on the existing `feat/draco-focus` branch. Code edits are confined to `src/simulatte/dispatching_rules/focus.py`, `src/simulatte/policies/draco.py`, `src/simulatte/builders.py`, and the two `__init__.py` exports. Tests are added/strengthened in `tests/core/test_focus.py` and `tests/core/test_draco.py`. Docs land in `docs/reference.md`, `docs/tutorials/release-control-and-dispatching.md`, two new files under `examples/`, and the agent skill under `skills/simulatte-dev/`.

**Tech Stack:** Python 3.12–3.14, SimPy, `uv` for env/test, `ruff` (lint+format), `ty` (type-check), `pytest` (99% coverage gate), `zensical` (docs site, mkdocstrings).

**Decisions taken (override if you disagree):**
1. **`Focus`/`Draco` stay classes** (not converted to the factory convention). They are stateful building blocks exposing per-mechanism methods and a shared `build_context`; a bare `(job, server) -> float` closure cannot serve DRACO. We document the deviation rather than refactor.
2. **`FocusPriorityRule` is kept and made first-class** (a new `build_focus_system` builder + example + docs) rather than dropped from `__all__`. Standalone FOCUS dispatching is a legitimate use of the Omega paper's rule.
3. **No `focus_dispatching` factory wrapper** is added — `FocusPriorityRule` already serves the standalone use; a parallel factory would duplicate it (YAGNI/DRY).
4. **Perf fix = "skip the beta entropy pass when beta is disabled"**, NOT the `env.now`-keyed memoization the original review floated. Rationale in Task 3 — the memoization conflicts with an existing regression test and the documented priority-policy contract.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/simulatte/dispatching_rules/focus.py` | FOCUS rule. DRY (use `unfinished_routing`), perf (gate beta pass), docstring polish | 1, 3, 4 |
| `src/simulatte/policies/draco.py` | DRACO policy. DRY (`_next_server_after`), docstring polish, cold-start note | 2, 4, 5 |
| `src/simulatte/builders.py` | Add `build_focus_system`; pass `compute_beta`; drop redundant lambda | 3, 7 |
| `src/simulatte/dispatching_rules/__init__.py` | (already exports FOCUS — no change) verified in Task 7 | 7 |
| `tests/core/test_focus.py` | Strengthen score tests, perf test, builder test, monkeypatch stub | 3, 6, 7 |
| `tests/core/test_draco.py` | Independent `_full_score`, force-flag-set, multi-stage WIP, monkeypatch stub | 3, 6 |
| `docs/reference.md` | mkdocstrings blocks for `Draco`, `Focus`, `FocusContext`, `FocusPriorityRule` | 8 |
| `docs/tutorials/release-control-and-dispatching.md` | DRACO §4 subsection, FOCUS dispatching note, §5 comparison | 8 |
| `examples/draco_simple.py`, `examples/focus_simple.py` | Runnable examples | 8 |
| `skills/simulatte-dev/SKILL.md`, `skills/simulatte-dev/references/api-reference.md` | DRACO/FOCUS guidance; refresh stale catalog | 9 |

---

## Task 1: DRY — replace `_remaining_after_completion` with `job.unfinished_routing`

**Why:** `focus.py:_remaining_after_completion` is a near-exact duplicate of the existing public `job.unfinished_routing` (`src/simulatte/job.py:224-232`, `tuple(srv for srv in self._servers if self.servers_exit_at[srv] is None)`). Its docstring even contrasts itself with `remaining_routing` but never mentions the sibling that already does the job.

**Files:**
- Modify: `src/simulatte/dispatching_rules/focus.py` (delete helper at ~102-111; update 3 call sites)
- Test: `tests/core/test_focus.py` (no new test — behavior is identical; existing suite is the regression guard)

- [ ] **Step 1: Confirm the helper is not imported elsewhere**

Run: `grep -rn "_remaining_after_completion" src tests`
Expected: matches only inside `src/simulatte/dispatching_rules/focus.py`. If any test imports it, stop and adjust that test in this task.

- [ ] **Step 2: Delete the helper function**

Remove this entire block from `src/simulatte/dispatching_rules/focus.py` (currently ~lines 102-111):

```python
def _remaining_after_completion(job: BaseJob) -> list[Server]:
    """Servers in *job*'s routing that have not been completed (exited) yet.

    Spec-compliant definition of FOCUS's ``R_i``: every server at which the
    job has yet to be *processed*. Differs from
    ``simulatte.job.BaseJob.remaining_routing``, which excludes
    servers already entered into the queue but not yet exited — FOCUS's
    ``R_i`` keeps them included as long as the operation has not finished.
    """
    return [srv for srv in job.servers if job.servers_exit_at[srv] is None]
```

- [ ] **Step 3: Update the three call sites to use `job.unfinished_routing`**

In `build_context` (the `for job in jobs:` loop), change:

```python
        for job in jobs:
            remaining = _remaining_after_completion(job)
```

to:

```python
        for job in jobs:
            remaining = job.unfinished_routing
```

In `gamma`, change:

```python
        remaining = _remaining_after_completion(job)
        if not remaining:
            return 1.0
```

to:

```python
        remaining = job.unfinished_routing
        if not remaining:
            return 1.0
```

In `_slack`, change:

```python
    @staticmethod
    def _slack(job: BaseJob, now: float) -> float:
        """``S_i = d_i - now - sum(p_ij for j in R_i)``."""
        remaining = _remaining_after_completion(job)
        return job.due_date - now - sum(job.routing[srv] for srv in remaining)
```

to:

```python
    @staticmethod
    def _slack(job: BaseJob, now: float) -> float:
        """``S_i = d_i - now - sum(p_ij for j in R_i)`` over ``job.unfinished_routing``."""
        return job.due_date - now - sum(job.routing[srv] for srv in job.unfinished_routing)
```

- [ ] **Step 4: Update the `_remaining_after_completion` docstring reference in `_delta_entropy`**

`_delta_entropy` and `beta` reference the old name in prose. In `src/simulatte/dispatching_rules/focus.py`, search the file for the literal `_remaining_after_completion` in any remaining docstring/comment and replace the phrase with `job.unfinished_routing`. (After Step 2 the symbol no longer exists; leaving the name in prose would be misleading.)

Run: `grep -n "_remaining_after_completion" src/simulatte/dispatching_rules/focus.py`
Expected: no matches.

- [ ] **Step 5: Run the FOCUS + DRACO suites to verify identical behavior**

Run: `uv run pytest tests/core/test_focus.py tests/core/test_draco.py -q`
Expected: PASS (same count as before; behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/focus.py
git commit -m "refactor(focus): use job.unfinished_routing, drop duplicate helper"
```

---

## Task 2: DRY — de-duplicate `_next_server_after`

**Why:** `focus.py:114-123` defines a module-level `_next_server_after`; `draco.py:224-232` defines an identical method. DRACO should reuse the FOCUS helper.

**Files:**
- Modify: `src/simulatte/policies/draco.py` (import the helper; delete the method; update the one call site)
- Test: existing `tests/core/test_draco.py` is the regression guard

- [ ] **Step 1: Confirm DRACO's method is not directly referenced by tests**

Run: `grep -rn "_next_server_after" src tests`
Expected: matches in `focus.py` (definition + uses), `draco.py` (method + use in `_authorization_impact`), and `tests/core/test_focus.py` (imports the focus module-level helper). If `tests/core/test_draco.py` references `draco._next_server_after`, stop and adjust that test here.

- [ ] **Step 2: Import the FOCUS helper into DRACO**

In `src/simulatte/policies/draco.py`, change the import:

```python
from simulatte.dispatching_rules.focus import Focus, FocusContext
```

to:

```python
from simulatte.dispatching_rules.focus import Focus, FocusContext, _next_server_after
```

- [ ] **Step 3: Delete the duplicate method**

Remove this block from `src/simulatte/policies/draco.py` (currently ~lines 224-232):

```python
    def _next_server_after(self, job: BaseJob, server: Server) -> Server | None:
        servers = job.servers
        try:
            idx = servers.index(server)
        except ValueError:
            return None
        if idx + 1 >= len(servers):
            return None
        return servers[idx + 1]
```

- [ ] **Step 4: Update the call site in `_authorization_impact`**

Change:

```python
        u = self._next_server_after(job, server_k)
```

to:

```python
        u = _next_server_after(job, server_k)
```

- [ ] **Step 5: Run checks**

Run: `uv run ty check src && uv run pytest tests/core/test_draco.py -q`
Expected: type-check clean; tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/policies/draco.py
git commit -m "refactor(draco): reuse focus._next_server_after, drop duplicate method"
```

---

## Task 3: Perf — skip the beta entropy pass when beta is disabled

**Why:** `FocusPriorityRule.__call__` and `Draco._queue_side_score` rebuild a full `FocusContext` on every `priority_policy` call, and `Server.sort_queue` calls `priority_policy` once per queued request per dispatch. The dominant per-rebuild cost is the per-job entropy loop in `build_context` (the only part that scales beyond `O(|O|)`), and it is pure waste when the beta weight is `0` — which is the default and the configuration Kasper et al. recommend.

**Complexity note (size expectations correctly):** the saved factor is `|servers|`, not `|J|`. The per-job loop calls `_delta_entropy` once per candidate, and each call runs `_entropy` over the `|servers|`-length workload vector — so the gated work is `O(|O|·|servers|)`, not the `O(|O|·|J|)` implied by the `FocusContext` docstring's loose wording. Still worth removing (beta-off is the default), just don't expect a `|J|`-scale speedup.

**Rejected alternative — `env.now`-keyed ctx memoization:** It conflicts with (a) the existing regression test `test_focus_priority_rule_rebuilds_ctx_per_server` (`tests/core/test_focus.py:894`), which calls the adapter twice at the **same** `env.now` and asserts `build_context` runs **twice**; and (b) the documented priority-policy contract/cost in `docs/tutorials/release-control-and-dispatching.md` §7 ("once per queued request per dispatch decision"). It also risks within-instant cross-server staleness. The beta-skip below is fully behavior-preserving (when beta is off, `beta`'s contribution is `0` regardless) and needs no test rewrites beyond extending two monkeypatch stub signatures.

**Files:**
- Modify: `src/simulatte/dispatching_rules/focus.py` (`build_context` gains `compute_beta`; `score` short-circuits beta; `FocusPriorityRule.__call__` passes the flag)
- Modify: `src/simulatte/policies/draco.py` (`decide_next_job` and `_queue_side_score` pass the flag)
- Test: `tests/core/test_focus.py` (new perf test + extend monkeypatch stub), `tests/core/test_draco.py` (extend monkeypatch stub)

- [ ] **Step 1: Write the failing perf test**

Add to `tests/core/test_focus.py` (after the existing `# ----- FocusContext aggregates -----` tests, e.g. near the other `build_context` tests):

```python
def test_focus_build_context_skips_beta_pass_when_disabled() -> None:
    """compute_beta=False gates only the beta normalizer (max_positive_c)."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Blocker holds s1; the queued job's s1->s2 routing moves load to the
    # empty server, improving balance => c(i) > 0 under the full pass.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    rebal = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(rebal)
    env.run(until=0.002)

    ctx_full = Focus.build_context(sf, now=0.002, compute_beta=True)
    ctx_skip = Focus.build_context(sf, now=0.002, compute_beta=False)

    assert ctx_full.max_positive_c > 0.0
    assert ctx_skip.max_positive_c == 0.0
    # Non-beta aggregates are identical — only the beta normalizer is gated.
    assert ctx_skip.max_pij == ctx_full.max_pij
    assert ctx_skip.max_positive_slack == ctx_full.max_positive_slack
    assert ctx_skip.max_positive_pacing == ctx_full.max_positive_pacing


def test_focus_score_identical_with_beta_off_regardless_of_compute_beta() -> None:
    """With w5=0, the score is identical whether ctx skipped the beta pass."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(j)
    env.run(until=0.002)

    focus = Focus()  # default weights -> w5 == 0
    ctx_full = focus.build_context(sf, now=0.002, compute_beta=True)
    ctx_skip = focus.build_context(sf, now=0.002, compute_beta=False)
    assert focus.score(j, s1, ctx_skip, now=0.002) == pytest.approx(focus.score(j, s1, ctx_full, now=0.002))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/test_focus.py::test_focus_build_context_skips_beta_pass_when_disabled -v`
Expected: FAIL with `TypeError: build_context() got an unexpected keyword argument 'compute_beta'`.

- [ ] **Step 3: Add the `compute_beta` parameter to `build_context` and gate the entropy block**

In `src/simulatte/dispatching_rules/focus.py`, change the `build_context` signature:

```python
    @staticmethod
    def build_context(
        shopfloor: ShopFloor,
        now: float,
        *,
        psp: PreShopPool | None = None,
    ) -> FocusContext:
```

to:

```python
    @staticmethod
    def build_context(
        shopfloor: ShopFloor,
        now: float,
        *,
        psp: PreShopPool | None = None,
        compute_beta: bool = True,
    ) -> FocusContext:
```

Then gate the beta block inside the `for job in jobs:` loop. Change:

```python
            # Beta: c(i) at the job's first uncompleted server.
            k = remaining[0]
            c_i = _delta_entropy(
                job=job,
                server=k,
                workloads=workloads,
                server_index=server_index,
                pre_entropy=pre_entropy,
            )
            if c_i > max_positive_c:
                max_positive_c = c_i
```

to:

```python
            # Beta: c(i) at the job's first uncompleted server. Skipped when
            # beta is disabled (compute_beta=False) — the dominant per-rebuild
            # cost, pure waste when the beta weight is 0.
            if compute_beta:
                k = remaining[0]
                c_i = _delta_entropy(
                    job=job,
                    server=k,
                    workloads=workloads,
                    server_index=server_index,
                    pre_entropy=pre_entropy,
                )
                if c_i > max_positive_c:
                    max_positive_c = c_i
```

Also extend the `build_context` docstring note (the `compute_beta` paragraph). Append to the existing docstring, just before `Note on the empty-shop case:`:

```python
        Pass ``compute_beta=False`` to skip the per-job workload-entropy
        pass when the beta mechanism is inactive (weight 0). ``workloads``,
        ``server_index`` and ``pre_entropy`` are still populated (cheap), so
        a direct ``beta`` call is still safe and returns ``0`` via its
        ``max_positive_c <= 0`` guard.
```

- [ ] **Step 4: Short-circuit beta in `score`**

In `src/simulatte/dispatching_rules/focus.py`, change `score`:

```python
    def score(self, job: BaseJob, server: Server, ctx: FocusContext, now: float) -> float:
        """Aggregate weighted score of the five mechanisms; value in ``[0, 1]``."""
        return (
            self.w1 * self.pi(job, server, ctx)
            + self.w2 * self.omega(job, server, ctx)
            + self.w3 * self.psi(job, ctx, now)
            + self.w4 * self.gamma(job, ctx, now)
            + self.w5 * self.beta(job, server, ctx)
        )
```

to:

```python
    def score(self, job: BaseJob, server: Server, ctx: FocusContext, now: float) -> float:
        """Aggregate weighted score of the five mechanisms; value in ``[0, 1]``."""
        beta_term = self.w5 * self.beta(job, server, ctx) if self.w5 != 0.0 else 0.0
        return (
            self.w1 * self.pi(job, server, ctx)
            + self.w2 * self.omega(job, server, ctx)
            + self.w3 * self.psi(job, ctx, now)
            + self.w4 * self.gamma(job, ctx, now)
            + beta_term
        )
```

- [ ] **Step 5: Pass `compute_beta` from `FocusPriorityRule.__call__`**

Change:

```python
    def __call__(self, job: BaseJob, server: Server) -> float:
        now = self.shopfloor.env.now
        ctx = self.focus.build_context(self.shopfloor, now, psp=self.psp)
        return -self.focus.score(job, server, ctx, now)
```

to:

```python
    def __call__(self, job: BaseJob, server: Server) -> float:
        now = self.shopfloor.env.now
        ctx = self.focus.build_context(self.shopfloor, now, psp=self.psp, compute_beta=self.focus.w5 != 0.0)
        return -self.focus.score(job, server, ctx, now)
```

- [ ] **Step 6: Pass `compute_beta` from DRACO**

In `src/simulatte/policies/draco.py`, in `decide_next_job`, change:

```python
        ctx = self.focus.build_context(self._shopfloor, now, psp=psp)
```

to:

```python
        ctx = self.focus.build_context(self._shopfloor, now, psp=psp, compute_beta=self.focus.w5 != 0.0)
```

In `_queue_side_score`, change:

```python
        ctx = self.focus.build_context(self._shopfloor, now, psp=self._psp)
```

to:

```python
        ctx = self.focus.build_context(self._shopfloor, now, psp=self._psp, compute_beta=self.focus.w5 != 0.0)
```

- [ ] **Step 7: Extend the two monkeypatch stubs to accept the new kwarg**

In `tests/core/test_focus.py`, in `test_focus_priority_rule_rebuilds_ctx_per_server`, change:

```python
    def counting_build(shopfloor, now, *, psp=None):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp)
```

to:

```python
    def counting_build(shopfloor, now, *, psp=None, compute_beta=True):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp, compute_beta=compute_beta)
```

In `tests/core/test_draco.py`, in `test_draco_priority_policy_rebuilds_ctx_per_server`, apply the **same** change to its `counting_build` stub.

- [ ] **Step 8: Run the perf tests and the two regression guards**

Run: `uv run pytest tests/core/test_focus.py::test_focus_build_context_skips_beta_pass_when_disabled tests/core/test_focus.py::test_focus_score_identical_with_beta_off_regardless_of_compute_beta tests/core/test_focus.py::test_focus_priority_rule_rebuilds_ctx_per_server tests/core/test_draco.py::test_draco_priority_policy_rebuilds_ctx_per_server -v`
Expected: all PASS (both monkeypatch guards still assert `call_count == 2` — the number of `build_context` calls is unchanged; only the work inside is gated).

- [ ] **Step 9: Run the full FOCUS + DRACO suites**

Run: `uv run pytest tests/core/test_focus.py tests/core/test_draco.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/simulatte/dispatching_rules/focus.py src/simulatte/policies/draco.py tests/core/test_focus.py tests/core/test_draco.py
git commit -m "perf(focus): skip workload-entropy pass when beta weight is zero"
```

---

## Task 4: Docstring polish — beta invariant, `_count_wip` note, class-vs-factory rationale

**Why:** Three nice-to-have documentation gaps: the `beta in [0,1]` invariant relies on an undocumented caller assumption; `_count_wip` differs from the shopfloor `WIPStrategy` without explanation; and the deliberate class-vs-factory deviation should be recorded so the inconsistency reads as intentional.

**Files:**
- Modify: `src/simulatte/dispatching_rules/focus.py` (`beta` docstring; `Focus` class docstring)
- Modify: `src/simulatte/policies/draco.py` (`_count_wip` docstring; `Draco` class docstring)
- Test: none (docstring-only; verified by `ruff`/`ty`/`zensical` in Task 10)

- [ ] **Step 1: Add the beta invariant note**

In `src/simulatte/dispatching_rules/focus.py`, in the `beta` method docstring, append after the existing `Guard:` paragraph:

```python
        Invariant: callers must pass *server* equal to *job*'s first
        uncompleted server (``job.unfinished_routing[0]``) — the server at
        which ``ctx.max_positive_c`` was normalized in ``build_context``. All
        built-in call paths satisfy this (queue ordering scores a job at the
        server whose queue it sits in; PSP candidates start at *server* via
        ``starts_at``), which keeps ``beta`` in ``[0, 1]``. Passing any other
        server can yield ``beta > 1``.
```

- [ ] **Step 2: Add the class-vs-factory rationale to `Focus`**

In `src/simulatte/dispatching_rules/focus.py`, in the `Focus` class docstring, append a new paragraph after the existing first paragraph (before `Args:`):

```python
    Design note: unlike the stateless dispatching-rule factories (e.g.
    ``planned_slack_time``), FOCUS is a class because it exposes each
    mechanism as an independently testable method and a shared per-decision
    ``build_context`` consumed by higher-level policies (DRACO). A bare
    ``(job, server) -> float`` closure cannot expose these. Use
    ``FocusPriorityRule`` to adapt a ``Focus`` to the ``priority_policy``
    contract.
```

- [ ] **Step 3: Add the `_count_wip` vs `WIPStrategy` note**

In `src/simulatte/policies/draco.py`, change the `_count_wip` docstring:

```python
    def _count_wip(self) -> int:
        """``W = Σ(|Q_j| + |H_j|)`` over all servers (spec §3.1)."""
        return sum(len(s.queue) + s.count for s in self._shopfloor.servers)
```

to:

```python
    def _count_wip(self) -> int:
        """``W = Σ(|Q_j| + |H_j|)`` over all servers (spec §3.1).

        Count-based (jobs), independent of the shopfloor's ``WIPStrategy``
        (Standard/Corrected), which measures *workload*. The two metrics
        will not match numerically — DRACO's ``τ`` is a job count.
        """
        return sum(len(s.queue) + s.count for s in self._shopfloor.servers)
```

- [ ] **Step 4: Add the class-vs-factory + cold-start rationale to `Draco` (cold-start covered in Task 5)**

In `src/simulatte/policies/draco.py`, in the `Draco` class docstring, append after the first paragraph (before the `Trigger:` paragraph):

```python
    Design note: DRACO is a class (not a dispatching-rule factory) because
    it holds shop-coupled state (the WIP target, per-pair loop targets, the
    embedded ``Focus``, and the one-shot force flags) and exposes both a
    ``priority_policy`` and an ``on_completion`` callback.
```

- [ ] **Step 5: Verify docstrings render (no Sphinx roles, valid markdown)**

Run: `grep -nE ":(func|class|meth|mod|attr|obj):" src/simulatte/dispatching_rules/focus.py src/simulatte/policies/draco.py`
Expected: no matches (convention is plain double-backtick code).

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/dispatching_rules/focus.py src/simulatte/policies/draco.py
git commit -m "docs(focus,draco): document beta invariant, count-WIP semantics, class rationale"
```

---

## Task 5: Document DRACO's cold-start bootstrapping (starvation avoidance)

**Why:** The `Draco` class implements only the on-completion decision. `build_draco_system` layers `psp.on_arrival(starvation_avoidance)` (`builders.py:444`); in idle/cold-start regimes no completion fires, so releases come from that FIFO-ish rule, bypassing DRACO's `R/A/D` scoring. Without it the system would deadlock from a cold start. This is a documentation gap (not a bug); faithfulness to the paper cannot be verified (paywalled).

**Files:**
- Modify: `src/simulatte/policies/draco.py` (`Draco` class docstring — bootstrapping paragraph)
- Test: none (docstring-only)

- [ ] **Step 1: Add the cold-start paragraph to the `Draco` docstring**

In `src/simulatte/policies/draco.py`, in the `Draco` class docstring, append after the `Timing — why this is correct...` paragraph (before `Args:`):

```python
    Cold start / bootstrapping:
        DRACO's decision is triggered *only* on job completions. In an idle
        or lightly loaded shop, no completion fires, so an arriving job would
        sit in the PSP indefinitely. ``build_draco_system`` therefore also
        wires ``psp.on_arrival(starvation_avoidance)``: when a new arrival's
        first server is completely idle, the job is released immediately,
        bypassing the ``R/A/D`` scoring. This is a liveness provision, not a
        DRACO decision — in steady state, completion-triggered decisions
        dominate. (Faithfulness of this provision to Kasper et al. 2023 has
        not been verified against the primary source.)
```

- [ ] **Step 2: Verify**

Run: `uv run ty check src && uv run pytest tests/core/test_draco.py -q`
Expected: clean / PASS.

- [ ] **Step 3: Commit**

```bash
git add src/simulatte/policies/draco.py
git commit -m "docs(draco): document cold-start starvation-avoidance bootstrapping"
```

---

## Task 6: Strengthen test diagnostic power

**Why:** Several score tests re-derive the expected value by calling the same per-mechanism methods the implementation sums (low diagnostic power), the headline weighted-average test runs in a degenerate empty-candidate state, no DRACO test pins `_full_score` against an independently hand-computed constant, the force-flag *set* on a PSP win is only inferred from dispatch timing, and `_count_wip` is only probed on single-server routings.

**Files:**
- Modify: `tests/core/test_focus.py` (add shared helper; add independent score tests; replace the degenerate weighted-average test)
- Modify: `tests/core/test_draco.py` (add independent `_full_score`, force-flag-set, multi-stage WIP tests)

- [ ] **Step 1: Add a shared, hand-computed scenario helper to `tests/core/test_focus.py`**

Add near the top of `tests/core/test_focus.py` (after the imports, before the first test):

```python
def _loaded_two_server_shop() -> tuple[ShopFloor, Server, Server, ProductionJob, ProductionJob]:
    """A 2-server shop with a blocker on s1 and two queued candidates.

    Hand-computed FOCUS values at now=0.0 (blocker is in users -> excluded
    from the candidate set O; only `cand` and `other` are candidates):

      Aggregates: max_pij=8 (other's op), max_positive_slack=20 (cand),
      max_positive_pacing=10 (both jobs tie at V=10).

      cand  (routing s1->s2, p=[4,6], due=30): S=20, V=10
        pi   = 1 - 4/8 = 0.5
        omega= 0.5      (next server s2 has an empty queue; omega == pi)
        psi  = 1 - 20/20 = 0.0
        gamma= 1 - 10/10 = 0.0
        beta = 1.0      (sole balance-improving candidate -> own normalizer)

      other (routing s1, p=[8], due=18): S=10, V=10
        pi   = 1 - 8/8 = 0.0
        psi  = 1 - 10/20 = 0.5
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    cand = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[4.0, 6.0], due_date=30.0)
    other = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[8.0], due_date=18.0)
    sf.add(cand)
    sf.add(other)
    env.run(until=0.002)
    return sf, s1, s2, cand, other
```

- [ ] **Step 2: Replace the degenerate weighted-average test with an independent one**

In `tests/core/test_focus.py`, replace the body of `test_focus_score_is_exact_weighted_average` (currently building a job with `sf.add(j)` and never running the env, then mirroring `focus.pi/omega/...`) with:

```python
def test_focus_score_is_exact_weighted_average() -> None:
    """Score equals the hand-computed weighted average of independent constants."""
    sf, s1, _s2, cand, _other = _loaded_two_server_shop()

    focus = Focus(weights=(0.3, 0.2, 0.2, 0.1, 0.2))
    ctx = focus.build_context(sf, now=0.0)
    # pi=0.5, omega=0.5, psi=0.0, gamma=0.0, beta=1.0 (see helper docstring).
    expected = 0.3 * 0.5 + 0.2 * 0.5 + 0.2 * 0.0 + 0.1 * 0.0 + 0.2 * 1.0  # = 0.45
    assert focus.score(cand, s1, ctx, now=0.0) == pytest.approx(0.45)
    assert expected == pytest.approx(0.45)
```

- [ ] **Step 3: Add single-mechanism isolation tests (independent constants)**

Add to `tests/core/test_focus.py` (after the replaced test):

```python
def test_focus_score_pi_only_equals_known_constant() -> None:
    """weights=(1,0,0,0,0) -> score == pi == 0.5 for cand (independent constant)."""
    sf, s1, _s2, cand, _other = _loaded_two_server_shop()
    focus = Focus(weights=(1.0, 0.0, 0.0, 0.0, 0.0))
    ctx = focus.build_context(sf, now=0.0)
    assert focus.score(cand, s1, ctx, now=0.0) == pytest.approx(0.5)


def test_focus_score_psi_only_equals_known_constant() -> None:
    """weights=(0,0,1,0,0) -> score == psi == 0.5 for other (independent constant)."""
    sf, s1, _s2, _cand, other = _loaded_two_server_shop()
    focus = Focus(weights=(0.0, 0.0, 1.0, 0.0, 0.0))
    ctx = focus.build_context(sf, now=0.0)
    assert focus.score(other, s1, ctx, now=0.0) == pytest.approx(0.5)
```

- [ ] **Step 4: Run the new/replaced FOCUS tests**

Run: `uv run pytest tests/core/test_focus.py::test_focus_score_is_exact_weighted_average tests/core/test_focus.py::test_focus_score_pi_only_equals_known_constant tests/core/test_focus.py::test_focus_score_psi_only_equals_known_constant -v`
Expected: all PASS. (If a value mismatches, recompute against the helper docstring — the constants, not the implementation, are authoritative.)

- [ ] **Step 5: Add an independent `_full_score` test to `tests/core/test_draco.py`**

Add to `tests/core/test_draco.py` (after the `_count_wip` tests, before the priority_policy tests):

```python
def test_draco_full_score_matches_hand_computed_total_impact() -> None:
    """_full_score = w^R*R + w^A*A + w^D*D against independent constants.

    Setup: s1 only, a blocker in users, two queued jobs (cand p=4, other p=8)
    so max_pij=8 and pi(cand)=1-4/8=0.5. FOCUS uses pi-only weights, so the
    dispatching impact D = focus.score(cand) = 0.5. A(cand)=1.0 (single op).
    wip is passed explicitly (=4): ro^Q=min(1,4/20)=0.2, ro^P=max(0,1-4/20)=0.8.
    With equal total-impact weights (1/3 each):
      queue side  = (0.2 + 1.0 + 0.5)/3 = 1.7/3
      psp side    = (0.8 + 1.0 + 0.5)/3 = 2.3/3
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    cand = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[4.0], due_date=10000.0)
    other = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[8.0], due_date=10000.0)
    sf.add(blocker)
    sf.add(cand)
    sf.add(other)
    env.run(until=0.001)

    draco = Draco(
        shopfloor=sf,
        focus_weights=(1.0, 0.0, 0.0, 0.0, 0.0),
        total_impact_weights=(1.0 / 3, 1.0 / 3, 1.0 / 3),
        wip_target=10,
        loop_target=5,
    )
    ctx = draco.focus.build_context(sf, now=0.0)

    assert draco._full_score(cand, s1, ctx, now=0.0, wip=4, in_psp=False) == pytest.approx(1.7 / 3)
    assert draco._full_score(cand, s1, ctx, now=0.0, wip=4, in_psp=True) == pytest.approx(2.3 / 3)
```

- [ ] **Step 6: Add a force-flag-set test to `tests/core/test_draco.py`**

Add after the existing force-flag tests:

```python
def test_draco_force_flag_set_on_psp_win() -> None:
    """A PSP candidate that wins decide_next_job sets the one-shot force flag.

    Under-target shop (tau=100) with a high w^R makes ro^P dominate, so the
    PSP candidate outscores the queued job. Directly assert the flag is set
    to the PSP winner and the winner was released from the PSP.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=100, loop_target=5, total_impact_weights=(0.8, 0.1, 0.1))

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    queued = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[2.0], due_date=10000.0)
    sf.add(blocker)
    sf.add(queued)
    env.run(until=0.001)

    psp_cand = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10000.0)
    psp.add(psp_cand)

    class _FakeTrigger:
        previous_server = s1

    draco.decide_next_job(_FakeTrigger(), psp)  # type: ignore[arg-type]

    assert draco._forced_at_server.get(s1) is psp_cand
    assert psp_cand not in psp
```

- [ ] **Step 7: Add a multi-stage `_count_wip` test to `tests/core/test_draco.py`**

Add after the existing `_count_wip` tests:

```python
def test_draco_count_wip_spans_multiple_servers() -> None:
    """_count_wip sums |Q|+|H| across servers, including a mid-routing job.

    j1 (s1->s2) finishes s1 at t=1 and occupies s2 thereafter; j2 then holds
    s1 while j3 queues behind it. At t=1.5: s1 -> count 1 (j2) + queue 1 (j3),
    s2 -> count 1 (j1). Total W = 3.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)

    j1 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 100.0], due_date=10000.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    j3 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(j1)
    sf.add(j2)
    sf.add(j3)
    env.run(until=1.5)

    assert j1.servers_exit_at[s1] is not None  # j1 finished s1
    assert j1.servers_exit_at[s2] is None  # j1 still at s2
    assert draco._count_wip() == 3
```

- [ ] **Step 8: Run the new DRACO tests**

Run: `uv run pytest tests/core/test_draco.py::test_draco_full_score_matches_hand_computed_total_impact tests/core/test_draco.py::test_draco_force_flag_set_on_psp_win tests/core/test_draco.py::test_draco_count_wip_spans_multiple_servers -v`
Expected: all PASS. (If `_count_wip` returns something other than 3, print the per-server `len(s.queue)`/`s.count` to confirm the t=1.5 state, then adjust the run time or assertion to match the verified state — the goal is a genuine two-server WIP sum.)

- [ ] **Step 9: Run the full FOCUS + DRACO suites**

Run: `uv run pytest tests/core/test_focus.py tests/core/test_draco.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add tests/core/test_focus.py tests/core/test_draco.py
git commit -m "test(focus,draco): independent score/full_score/force-flag/multi-server-WIP assertions"
```

---

## Task 7: Add `build_focus_system` builder; clean up the DRACO lambda

**Why:** `FocusPriorityRule` is exported and tested but no builder/example wires it — standalone FOCUS dispatching has no usage path. Add `build_focus_system` (an immediate-release push system whose queue ordering is FOCUS). Also replace the redundant lambda in `build_draco_system`.

**Files:**
- Modify: `src/simulatte/builders.py` (imports; new `build_focus_system`; lambda cleanup)
- Test: `tests/core/test_focus.py` (smoke test for the builder)
- Verify: `src/simulatte/dispatching_rules/__init__.py` already exports `Focus`, `FocusContext`, `FocusPriorityRule` (no change needed)

- [ ] **Step 1: Write the failing builder smoke test**

Add to `tests/core/test_focus.py` (new section at the end of the file):

```python
# ----- build_focus_system builder -----


def test_build_focus_system_runs_and_completes_jobs() -> None:
    from simulatte.builders import build_focus_system

    env = Environment()
    psp, servers, shopfloor, router = build_focus_system(env, n_servers=4, arrival_rate=1.0)
    assert psp is None  # push system
    assert len(servers) == 4
    env.run(until=200.0)
    assert len(shopfloor.jobs_done) > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/test_focus.py::test_build_focus_system_runs_and_completes_jobs -v`
Expected: FAIL with `ImportError: cannot import name 'build_focus_system'`.

- [ ] **Step 3: Import FOCUS classes into `builders.py`**

In `src/simulatte/builders.py`, change:

```python
from simulatte.dispatching_rules import planned_slack_time
```

to:

```python
from simulatte.dispatching_rules import Focus, FocusPriorityRule, planned_slack_time
```

- [ ] **Step 4: Add `build_focus_system`**

Add to `src/simulatte/builders.py` (after `build_immediate_release_system`, before `build_lumscor_system`):

```python
def build_focus_system(
    env: Environment,
    *,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PushSystem:
    """Build an immediate-release (push) system that dispatches with FOCUS.

    Jobs enter the shopfloor on arrival (no release control); queue ordering
    at every server uses the FOCUS self-establishing rule (Kasper, Land,
    Teunter 2023, Omega 114, 102726) via ``FocusPriorityRule``. Use this to
    study FOCUS as a standalone dispatching rule, independent of DRACO.

    Args:
        env: The simulation environment.
        focus_weights: FOCUS mechanism weights ``(w1, w2, w3, w4, w5)`` for
            (pi, omega, psi, gamma, beta); must each be in ``[0, 1]`` and sum
            to 1. Defaults to beta-dormant ``(0.25, 0.25, 0.25, 0.25, 0.0)``.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(None, servers, shop_floor, router)`` (push system; no PSP).

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_focus_system(env)
        >>> env.run(until=1000)

    References:
        Kasper, A., Land, M., Teunter, R. (2023). Towards system state
        dispatching in high-variety manufacturing. *Omega*, 114, 102726.
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    focus = Focus(weights=focus_weights)
    priority = FocusPriorityRule(focus, shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        priority_policies=priority,
    )
    return None, servers, shop_floor, router
```

- [ ] **Step 5: Replace the redundant lambda in `build_draco_system`**

In `src/simulatte/builders.py`, in `build_draco_system`'s `Router(...)`, change:

```python
        priority_policies=lambda job, server: draco.priority_policy(job, server),
```

to:

```python
        priority_policies=draco.priority_policy,
```

- [ ] **Step 6: Run the builder test + type-check**

Run: `uv run ty check src && uv run pytest tests/core/test_focus.py::test_build_focus_system_runs_and_completes_jobs tests/core/test_draco.py -q`
Expected: clean / PASS (DRACO suite still green after the lambda swap).

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_focus.py
git commit -m "feat(builders): add build_focus_system; drop redundant draco lambda"
```

---

## Task 8: Web documentation — reference page, tutorial sections, examples

**Why:** Confirmed gap — zero DRACO/FOCUS content anywhere under `docs/`, and no `examples/` for either. Per CLAUDE.md/CONTRIBUTING, docs must accompany new functionality.

**Files:**
- Modify: `docs/reference.md` (mkdocstrings blocks)
- Modify: `docs/tutorials/release-control-and-dispatching.md` (DRACO §4 subsection; FOCUS dispatching note; §5 comparison)
- Create: `examples/draco_simple.py`, `examples/focus_simple.py`

- [ ] **Step 1: Add `Draco` to the reference page (Release Policies)**

In `docs/reference.md`, after the `### LumsCor` block (ends at the `members: false` line, ~line 33), insert:

```markdown

### Draco

::: simulatte.policies.Draco
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false
```

- [ ] **Step 2: Add a FOCUS tier to the reference page (Dispatching Rules)**

In `docs/reference.md`, at the end of the file (after the `slack_per_remaining_operation` block), append:

```markdown

### Tier 3 — system-state rules

#### Focus

::: simulatte.dispatching_rules.Focus
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 5
      members: false

#### FocusContext

::: simulatte.dispatching_rules.FocusContext
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 5
      members: false

#### FocusPriorityRule

::: simulatte.dispatching_rules.FocusPriorityRule
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 5
      members: false
```

- [ ] **Step 3: Add the DRACO subsection to the tutorial (§4)**

In `docs/tutorials/release-control-and-dispatching.md`, insert a new subsection immediately before the `### Dispatching rules` heading (currently ~line 259). The content below is wrapped in `~~~markdown` fences so its inner ` ```python ` block is unambiguous — copy only what is *between* the `~~~` lines:

~~~markdown
### DRACO

**Dispatching, Release, and Authorization for Controlled Order flow** (Kasper, Land & Teunter, 2023 — [DOI](https://doi.org/10.1016/j.ijpe.2022.108768)).

DRACO is *non-hierarchical*: it merges release, authorization, and dispatching into a single per-server decision taken on every job completion. At each completion at server `k`, DRACO scores every candidate in `Q_k ∪ P_k` (jobs queued at `k`, plus PSP jobs whose first server is `k`) by a weighted total impact `w^R·R + w^A·A + w^D·D` and selects the maximum. The dispatching component `D` is the FOCUS rule (below).

```python
from simulatte.builders import build_draco_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_draco_system(
    env,
    wip_target=8,    # target shop WIP (job count), tau
    loop_target=4,   # target overlapping loop per server pair, epsilon
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
```

Key parameters:

- `wip_target` (`τ`): target shop WIP as a **job count** (sum of queued + in-process jobs across servers). This is independent of any `WIPStrategy` workload metric.
- `loop_target` (`ε`): target overlapping loop per `(k, u)` server pair. Pass a scalar for a uniform target; instantiate `Draco` directly with a `dict[(Server, Server), int]` for per-pair targets.
- `focus_weights`: the five FOCUS mechanism weights used for `D`.
- `total_impact_weights`: `(w^R, w^A, w^D)`, must sum to 1.

**How it differs:** classic workload control separates release (PSP → shop) from dispatching (queue ordering). DRACO makes one combined choice per completion, so a PSP job can be released *and* placed first at the freed server in a single decision. A `_forced_at_server` flag guarantees a PSP winner is dispatched before any queued job, even when the queued job has a higher queue-side priority.

**Cold start:** the decision fires only on completions, so `build_draco_system` also wires `psp.on_arrival(starvation_avoidance)` to release a job when an arrival's first server is idle — a liveness provision that prevents a cold-start deadlock.
~~~

- [ ] **Step 4: Add a FOCUS note to the tutorial "Dispatching rules" subsection**

In `docs/tutorials/release-control-and-dispatching.md`, at the end of the `### Dispatching rules` subsection (after the paragraph ending "...safe for priority comparisons and `min()` calls.", ~line 297), append the content between the `~~~markdown` fences:

~~~markdown

**Tier 3 — system-state rules.** `Focus` (Kasper, Land & Teunter, 2023 — [DOI](https://doi.org/10.1016/j.omega.2022.102726)) is a *self-establishing* rule: a weighted combination of five mechanisms — SPT (`pi`), starvation response (`omega`), slack timing (`psi`), pacing (`gamma`), and WIP balancing (`beta`), each in `[0, 1]`. Unlike Tier 1/2 it is a class (it exposes per-mechanism methods and a shared `build_context`), adapted to the `priority_policy` contract by `FocusPriorityRule`. A ready-made push system that dispatches with FOCUS is available:

```python
from simulatte.builders import build_focus_system

_, servers, shopfloor, router = build_focus_system(
    env,
    focus_weights=(0.25, 0.25, 0.25, 0.25, 0.0),  # beta dormant (default)
)
```

FOCUS is also the dispatching component of DRACO (above).
~~~

- [ ] **Step 5: Add DRACO to the §5 comparison**

In `docs/tutorials/release-control-and-dispatching.md` §5, change the import block:

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
)
```

to:

```python
from simulatte.builders import (
    build_draco_system,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
)
```

and change the comparison tail:

```python
slar_limit = run_system(build_slar_limit_system, {"allowance_factor": 3, "wl_norm_level": 5})

policies = [("Immediate", immediate), ("LumsCor", lumscor), ("SLAR", slar), ("SLAR-Limit", slar_limit)]
```

to:

```python
slar_limit = run_system(build_slar_limit_system, {"allowance_factor": 3, "wl_norm_level": 5})
draco = run_system(build_draco_system, {"wip_target": 8, "loop_target": 4})

policies = [
    ("Immediate", immediate),
    ("LumsCor", lumscor),
    ("SLAR", slar),
    ("SLAR-Limit", slar_limit),
    ("DRACO", draco),
]
```

- [ ] **Step 6: Create `examples/draco_simple.py`**

Create `examples/draco_simple.py`:

```python
from __future__ import annotations

from simulatte.builders import build_draco_system
from simulatte.environment import Environment


def main() -> None:
    with Environment() as env:
        _, servers, shopfloor, _ = build_draco_system(
            env,
            wip_target=8,
            loop_target=4,
        )
        env.run(until=2000.0)

        done = shopfloor.jobs_done
        print("DRACO non-hierarchical WIP-control example")
        print(f"Servers: {len(servers)}")
        print(f"Simulation time: {env.now:.1f}")
        print(f"Jobs completed: {len(done)}")
        if done:
            print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")
        print(f"Avg server utilization: {sum(s.utilization_rate for s in servers) / len(servers):.1%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Create `examples/focus_simple.py`**

Create `examples/focus_simple.py`:

```python
from __future__ import annotations

from simulatte.builders import build_focus_system
from simulatte.environment import Environment


def main() -> None:
    with Environment() as env:
        _, servers, shopfloor, _ = build_focus_system(
            env,
            focus_weights=(0.25, 0.25, 0.25, 0.25, 0.0),
        )
        env.run(until=2000.0)

        done = shopfloor.jobs_done
        print("FOCUS standalone-dispatching example (immediate release)")
        print(f"Servers: {len(servers)}")
        print(f"Simulation time: {env.now:.1f}")
        print(f"Jobs completed: {len(done)}")
        if done:
            print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Verify examples run and the docs build cleanly**

Run: `uv run python examples/draco_simple.py && uv run python examples/focus_simple.py`
Expected: each prints a summary with `Jobs completed: N` where N > 0, no traceback.

Run: `uv run zensical build --clean`
Expected: `Build finished` with no errors; the rendered `reference/` page shows Draco + Focus, and the tutorial renders the DRACO and FOCUS sections (no broken fences). If a fence renders wrong, fix the affected block and rebuild.

- [ ] **Step 9: Commit**

```bash
git add docs/reference.md docs/tutorials/release-control-and-dispatching.md examples/draco_simple.py examples/focus_simple.py
git commit -m "docs(draco,focus): reference blocks, tutorial sections, runnable examples"
```

---

## Task 9: Agent skill — DRACO/FOCUS guidance and catalog refresh

**Why:** Confirmed gap — zero DRACO/FOCUS mention under `skills/simulatte-dev/`. The skill is also stale ("ships three system configurations"; no SlarLimit/ConWIP/ContinuousRelease). Update both files so the agent can guide users to these policies.

**Files:**
- Modify: `skills/simulatte-dev/SKILL.md`
- Modify: `skills/simulatte-dev/references/api-reference.md`

- [ ] **Step 1: Fix the stale catalog claim in `SKILL.md`**

In `skills/simulatte-dev/SKILL.md`, change:

```markdown
Simulatte ships three system configurations. The choice depends on what the
researcher wants to study.
```

to:

```markdown
Simulatte ships several system configurations (Immediate, LumsCor, SLAR,
SLAR-Limit, DRACO, plus ConWIP and Continuous Release for manual
composition). The choice depends on what the researcher wants to study.
```

- [ ] **Step 2: Add a DRACO entry under "Choosing a release policy" in `SKILL.md`**

In `skills/simulatte-dev/SKILL.md`, after the `### SLAR (slack-based pull)` block (ends at the `build_slar_system(...)` code fence, ~line 107), insert:

```markdown
### DRACO (non-hierarchical WIP control)

DRACO merges release, authorization, and dispatching into one per-server
decision taken on every job completion. At each completion at server `k`, it
scores every candidate in `Q_k ∪ P_k` by `w^R·R + w^A·A + w^D·D` and dispatches
the maximum; its dispatching term `D` is the FOCUS rule. A PSP winner is forced
to dispatch first via a one-shot per-server flag.

Use DRACO when the researcher wants a **single integrated release+dispatch
policy** rather than separate workload-control and dispatching layers. Key
parameters to vary:
- `wip_target` (`τ`): target shop WIP as a job count (not a workload metric)
- `loop_target` (`ε`): target overlapping loop per server pair
- `total_impact_weights`: `(w^R, w^A, w^D)` — release vs. authorization vs. dispatch

```python
from simulatte.builders import build_draco_system

psp, servers, shopfloor, router = build_draco_system(
    env, wip_target=8, loop_target=4,
)
```

`build_draco_system` also wires `psp.on_arrival(starvation_avoidance)` to avoid
a cold-start deadlock (the decision fires only on completions).
```

- [ ] **Step 3: Add a FOCUS note under "Custom dispatching rules" in `SKILL.md`**

In `skills/simulatte-dev/SKILL.md`, at the end of the "Custom dispatching rules" section (after the paragraph ending "...via its `priority_policies=` argument.", ~line 184), append:

```markdown

**FOCUS (system-state rule).** `simulatte.dispatching_rules.Focus` is a
self-establishing rule combining five weighted mechanisms (SPT, starvation,
slack timing, pacing, WIP balance). Unlike the Tier-1/2 rules it is a class
(it exposes per-mechanism methods and a shared `build_context`), wrapped for
the router by `FocusPriorityRule`. For a ready-made push system that
dispatches with FOCUS, use `build_focus_system(env, focus_weights=...)`. FOCUS
is also DRACO's dispatching component.
```

- [ ] **Step 4: Add `Draco` to the Release Policies section of `api-reference.md`**

In `skills/simulatte-dev/references/api-reference.md`, after the `### Slar` block (ends at "...No separate method calls needed.", ~line 258), insert:

```markdown

### SlarLimit

```python
from simulatte.policies.slar_limit import SlarLimit

SlarLimit(
    *,
    shopfloor: ShopFloor,
    psp: PreShopPool,
    router: Router,
    wl_norm: dict[Server, float],
    allowance_factor: float = 2.0,
)
```

SLAR variant that gates urgent insertion by a workload norm. Requires
`CorrectedWIPStrategy`. Construction is active (wires triggers + PST dispatching).

### Draco

```python
from simulatte.policies.draco import Draco

Draco(
    *,
    shopfloor: ShopFloor,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (1/3, 1/3, 1/3),
    wip_target: int,                                  # tau (job count)
    loop_target: int | dict[tuple[Server, Server], int],  # epsilon
    psp: PreShopPool | None = None,
)
```

**Methods:**
- `draco.priority_policy(job, server)` — queue-side priority (`-inf` for a forced PSP winner); pass as `Router(priority_policies=...)`
- `draco.decide_next_job(triggering_job, psp)` — the non-hierarchical decision (for `on_completion_trigger`)

Non-hierarchical: scores `Q_k ∪ P_k` by `w^R·R + w^A·A + w^D·D` on each completion. `D` is FOCUS. `build_draco_system` wires both the priority policy and the completion trigger, plus `starvation_avoidance` for cold start.

> Note: ConWIP and Continuous Release are also available (`simulatte.policies.conwip.ConWIP`, `simulatte.policies.continuous_release.ContinuousRelease`) for manual composition via triggers.
```

- [ ] **Step 5: Add the builders + FOCUS dispatching entries to `api-reference.md`**

In `skills/simulatte-dev/references/api-reference.md`, change the builder import block:

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
)
```

to:

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_focus_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
    build_draco_system,
)
```

Then, after the `### build_slar_limit_system` block (ends at "...set automatically by the builder).", ~line 447), insert:

```markdown

### build_focus_system

```python
build_focus_system(
    env: Environment,
    *,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PushSystem  # (None, servers, shopfloor, router)
```

Immediate-release push system whose queue ordering is FOCUS (via `FocusPriorityRule`).

### build_draco_system

```python
build_draco_system(
    env: Environment,
    *,
    wip_target: int,                                  # tau (job count)
    loop_target: int,                                 # epsilon (scalar; use Draco() for per-pair)
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (1/3, 1/3, 1/3),
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem  # (psp, servers, shopfloor, router)
```

Non-hierarchical release+dispatch. Wires `Draco.priority_policy`, `on_completion_trigger(... Draco.decide_next_job)`, and `psp.on_arrival(starvation_avoidance)`.
```

Then, at the end of the "## Dispatching Rules" section (after the `job.planned_slack_time_at` note block, ~line 505), append:

```markdown

### Tier 3 — system-state rules

```python
from simulatte.dispatching_rules import Focus, FocusContext, FocusPriorityRule
```

`Focus(weights=(w1, w2, w3, w4, w5))` — five mechanisms (pi/omega/psi/gamma/beta),
each in `[0, 1]`, weights sum to 1. A class, not a `(job, server) -> float`
callable. Key methods: `focus.build_context(shopfloor, now, *, psp=None,
compute_beta=True)` (shared per-decision aggregates) and `focus.score(job,
server, ctx, now)`. Adapt to the router with `FocusPriorityRule(focus,
shopfloor, *, psp=None)` (negates the score; lower = served first), or use
`build_focus_system`. FOCUS is also DRACO's dispatching component.
```

- [ ] **Step 6: Verify the skill markdown is consistent**

Run: `grep -rni "draco\|focus" skills/simulatte-dev/`
Expected: multiple matches in both `SKILL.md` and `references/api-reference.md`. Eyeball the inserted code fences for balance.

- [ ] **Step 7: Commit**

```bash
git add skills/simulatte-dev/SKILL.md skills/simulatte-dev/references/api-reference.md
git commit -m "docs(skill): add DRACO/FOCUS guidance; refresh release-policy catalog"
```

---

## Task 10: Full verification gate

**Why:** Confirm the whole branch is still green end-to-end (lint, format, type-check, 99% coverage, docs build).

**Files:** none (verification only)

- [ ] **Step 1: Lint + format**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: "All checks passed!" and "N files already formatted". If format fails, run `uv run ruff format src tests`, re-run `--check`, and amend the relevant commit.

- [ ] **Step 2: Type-check**

Run: `uv run ty check src`
Expected: "All checks passed!" (ignore any Pyright/LSP "unresolved import" noise — `ty` is authoritative).

- [ ] **Step 3: Tests + coverage**

Run: `uv run pytest`
Expected: all tests PASS; "Required test coverage of 99% reached." (`focus.py` and `draco.py` should remain ≥99% / 100% — the new code paths are exercised by Tasks 3/6/7).

- [ ] **Step 4: Docs build**

Run: `uv run zensical build --clean`
Expected: "Build finished" with no errors.

- [ ] **Step 5: Final commit (only if Step 1 reformatted anything)**

```bash
git add -A
git commit -m "chore: formatting after draco/focus follow-ups"
```

---

## Self-Review

**1. Spec coverage** — every review finding maps to a task:

| Finding (severity) | Task |
|--------------------|------|
| Perf: redundant ctx rebuild (should-fix) | 3 |
| DRY: `_remaining_after_completion` → `unfinished_routing` (should-fix) | 1 |
| DRY: duplicate `_next_server_after` (should-fix) | 2 |
| Docstring: `remaining_routing` reference (should-fix) | 1 (Step 4) |
| Doc/semantics: starvation_avoidance cold-start (should-fix) | 5 |
| Tests: independent `score`/`_full_score`, force-flag set (should-fix) | 6 |
| Web docs: reference + tutorial + example (should-fix) | 8 |
| Skill: DRACO/FOCUS entries (should-fix) | 9 |
| FocusPriorityRule hidden API — wire it (should-fix) | 7, 8 |
| Class-vs-factory deviation note (nice-to-have) | 4 |
| beta>1 invariant note (nice-to-have) | 4 |
| `_count_wip` vs `WIPStrategy` note (nice-to-have) | 4 |
| Redundant lambda in builder (nice-to-have) | 7 (Step 5) |
| Mechanism-isolation score tests (nice-to-have) | 6 (Step 3) |
| Multi-stage `_count_wip` test (nice-to-have) | 6 (Step 7) |
| Skill catalog staleness (nice-to-have) | 9 (Steps 1, 4) |
| No `focus_dispatching` factory wrapper | Intentionally skipped (Decision 3) |

No finding is unaddressed.

**2. Placeholder scan** — every code/test/doc step contains the literal content to write (no "TBD"/"add validation"/"similar to Task N"). The one prose caution (Task 8 Step 3, about the code-fence boundary) is a verification instruction, not a placeholder.

**3. Type/name consistency** — `build_focus_system` (Task 7) is referenced consistently in Tasks 8 (tutorial, example) and 9 (skill). `compute_beta` (Task 3) is added to `build_context` and consumed identically in `FocusPriorityRule`, `Draco.decide_next_job`, `Draco._queue_side_score`, and both monkeypatch stubs. `_loaded_two_server_shop` (Task 6 Step 1) is defined before its uses (Steps 2–3). Hand-computed constants (0.45, 0.5, 1.7/3, 2.3/3, WIP=3) are derived in the test docstrings.

**Caveat for the executor:** the hand-computed test constants (Task 6) and the `_count_wip == 3` state (Task 6 Step 7) assume the documented sim state at the chosen `now`/`until`. If an assertion fails, print the actual aggregates/queue state, confirm against the scenario docstring, and adjust the *scenario* (not the implementation) — the constants are the source of truth, the implementation is under test.
