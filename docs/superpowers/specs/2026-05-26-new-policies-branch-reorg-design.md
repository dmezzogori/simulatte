# `feat/new-policies` Branch Reorganization

**Date:** 2026-05-26
**Status:** Approved, ready for execution
**Source branch:** `origin/feat/new-policies` (9 commits, +3994 lines)
**Target base:** `main` at `af32280` (v0.6.1)

## Context

A contributor delivered three weeks of work on a single feature branch (`origin/feat/new-policies`) that diverged from `eb7f8ba` — before the v0.6.0 and v0.6.1 releases. The branch conflates three independent tracks of work plus one piece of work that has been superseded by the v0.6.1 release on `main`.

This spec describes how the branch will be split into three reviewable PRs against current `main`, what is dropped, and the mechanical git plan.

## Commit inventory

The 9 commits on `origin/feat/new-policies` (oldest first):

| # | Commit | Subject | Disposition |
|---|--------|---------|-------------|
| 1 | `fa8132f` | SLAR-Limit release policy (Thürer & Stevenson 2021) | → `feat/slar-limit` |
| 2 | `7894d5e` | docs: complete reference in SlarLimit | → `feat/slar-limit` (squash with #1) |
| 3 | `7d5eb9f` | DRACO policy + FOCUS dispatching rule (v1) | → `feat/draco-focus` (+ package skeleton bit to `feat/dispatching-rules`) |
| 4 | `f31512a` | `Server.refresh_priorities` + dynamic-queue-priorities spec | **Dropped.** Superseded by v0.6.1's `sort_queue`-driven refresh. The wiring of DRACO/FOCUS in this commit is rewritten against main's API. |
| 5 | `1d05d66` | Refactor: rename `job.priority_policy` → `job.priority_rule` (wide) | **Dropped.** Naming choice not adopted; only the `DispatchingRule` type alias from `typing.py` is kept. |
| 6 | `911073e` | New rules: EDD, ODD, MODD, CR, PST, SOPN | → `feat/dispatching-rules` (split into basic + parametrized commits) |
| 7 | `89b3930` | Extend FOCUS with WIP balancing | → `feat/draco-focus` |
| 8 | `05cb1d1` | docs: ODD static-routing design | → `feat/dispatching-rules` (with #9) |
| 9 | `3fcba35` | Fix ODD to use static route length | → `feat/dispatching-rules` (squash with #8) |

## Output branches

Three new feature branches off `main`, each independently reviewable and releasable.

### `feat/slar-limit` — new release policy

One new release policy from literature: SLAR-Limit (Thürer & Stevenson 2021). Pure addition with minor shared-code refactor in `policies/slar.py`.

**Contents (1 squashed commit):**
- New: `src/simulatte/policies/slar_limit.py`, `tests/core/test_slar_limit.py`.
- Modified: `policies/__init__.py` (export), `builders.py` (factory `build_slar_limit_system`), `policies/slar.py` (internal refactor to share code), `tests/core/test_builders.py`.
- Commit message: `feat(policies): SLAR-Limit release policy (Thürer & Stevenson 2021)`.

**Dependencies:** none. Ships first.

**Expected conflicts:** minimal — `builders.py` and `policies/__init__.py` may need 3-line conflict resolution against v0.6.0/v0.6.1 additions.

### `feat/dispatching-rules` — new dispatching rules package

Introduces `simulatte.dispatching_rules` as the home for priority-callback rules, populated with basic and parametrized rules from the literature. Does **not** rename `job.priority_policy` — new rules are wired in via the existing attribute.

**Contents (3 commits, no squash):**

1. `feat(dispatching_rules): introduce package with basic rules (EDD, ODD, MODD, CR)`
   - New: `src/simulatte/dispatching_rules/__init__.py` (skeleton, basic exports only — FOCUS exports added later in `feat/draco-focus`).
   - New: `src/simulatte/dispatching_rules/basic.py`, `tests/core/test_basic_rules.py`.
   - Modified: `src/simulatte/typing.py` — adds `DispatchingRule` type alias (the one piece of commit `1d05d66` that is retained).

2. `feat(dispatching_rules): parametrized rules (PST, SOPN)`
   - New: `src/simulatte/dispatching_rules/parametrized.py`, `tests/core/test_parametrized_rules.py`.
   - Modified: `dispatching_rules/__init__.py` to export the parametrized rules.

3. `fix(dispatching_rules): ODD uses static route length`
   - Modified: `dispatching_rules/basic.py`, `tests/core/test_basic_rules.py`.
   - New: `docs/superpowers/specs/2026-05-20-odd-static-routing-design.md`.
   - Squash of commits `05cb1d1` + `3fcba35`.

**Dependencies:** none — independent of `feat/slar-limit`. Can be reviewed in parallel.

**Expected conflicts:** low. Pure additions plus a type alias in `typing.py`.

**Constraint:** rule signatures must match main's `priority_policy: Callable[[Job, Server], float] | None`. Verify on cherry-pick that each new rule has signature `(job, server) -> float` and is assignable to `job.priority_policy`.

### `feat/draco-focus` — DRACO release policy with FOCUS dispatching rule

Introduces DRACO (a release policy) and its companion FOCUS dispatching rule. FOCUS uses dynamic state (current WIP, time-of-evaluation, PSP membership) so the priority callable must be refreshed periodically; the original branch used a now-deleted `Server.refresh_priorities(key_fn)` method, which is rewritten against main's API.

**Contents (2 commits):**

1. `feat(policies): DRACO release policy with FOCUS dispatching rule`
   - New: `src/simulatte/policies/draco.py`, `src/simulatte/dispatching_rules/focus.py`, `tests/core/test_draco.py`, `tests/core/test_focus.py`.
   - Modified: `dispatching_rules/__init__.py` (export FOCUS), `policies/__init__.py` (export DRACO), `builders.py` (DRACO factory).
   - Squash of `7d5eb9f` + the DRACO/FOCUS-side rewiring extracted from `f31512a`.

2. `feat(dispatching_rules): FOCUS WIP-balancing extension`
   - From commit `89b3930`.

**Dependencies:** soft dependency on `feat/dispatching-rules` (needs the package to exist). Branch is created off `feat/dispatching-rules`. After PR2 merges, rebase onto main: `git rebase --onto main feat/dispatching-rules feat/draco-focus`.

**Rewiring detail.** The student's `f31512a` introduced `Server.refresh_priorities(key_fn)` so DRACO/FOCUS could pass a fresh closure capturing per-tick state. Main's v0.6.1 instead has `Server.sort_queue()` which re-reads `job.priority_policy(server)` for every queued request (auto-called from `_trigger_put`, also invocable explicitly).

The rewiring pattern: at each call site that used `server.refresh_priorities(lambda j, s: -score(j, s, ctx, now, ...))`, replace with:

```python
fresh_rule = lambda j, s: -score(j, s, ctx, now, ...)
for req in server.put_queue:
    req.job.priority_policy = fresh_rule
server.sort_queue()
```

Mechanically equivalent: assigning `priority_policy` then calling `sort_queue()` re-evaluates priorities exactly as the deleted `refresh_priorities` did. Two call sites total (one in `draco.py` line ~308, one in `focus.py` line ~456 per the source branch). Tests in `test_server.py` that exercised `refresh_priorities` are dropped — equivalent coverage exists in the DRACO/FOCUS test files.

**Expected conflicts:** medium. The rewiring is the only real engineering work in the reorganization.

## Dropped work

- **Commit `f31512a`** in its entirety — both the `Server.refresh_priorities` method and its design doc at `docs/superpowers/specs/2026-05-14-dynamic-queue-priorities-design.md` (which would conflict with main's `2026-05-25-dynamic-priority-queue-refresh-design.md` anyway, and reflects an obsolete approach).
- **Commit `1d05d66`** in its entirety, *except* for the `DispatchingRule` type alias in `src/simulatte/typing.py` (kept in `feat/dispatching-rules`). The attribute `job.priority_policy` is **not** renamed.
- Tests in `tests/core/test_server.py` that target `refresh_priorities`.

## Execution plan

Three branches built sequentially. Each branch is reviewed before the next is opened — surprises in earlier branches may inform later ones.

### Setup

```bash
git fetch origin
git checkout -b archive/feat/new-policies origin/feat/new-policies  # local archive
git checkout main
```

### Branch 1: `feat/slar-limit`

```bash
git checkout -b feat/slar-limit main
git cherry-pick fa8132f 7894d5e          # resolve conflicts if any
git reset --soft main                    # collapse to staged diff
git commit -m "feat(policies): SLAR-Limit release policy (Thürer & Stevenson 2021)"
uv run pytest tests/core/test_slar_limit.py tests/core/test_slar.py tests/core/test_builders.py
uv run pre-commit run --all-files
```

Review gate: full diff review with user before continuing.

### Branch 2: `feat/dispatching-rules`

```bash
git checkout -b feat/dispatching-rules main

# Commit 1: package + basic rules + typing alias
git checkout origin/feat/new-policies -- src/simulatte/dispatching_rules/__init__.py src/simulatte/dispatching_rules/basic.py tests/core/test_basic_rules.py
# Hand-edit __init__.py to remove FOCUS exports (FOCUS lives in feat/draco-focus)
# Surgically apply only the DispatchingRule alias bit of typing.py
git checkout origin/feat/new-policies -- src/simulatte/typing.py
# Manually revert the non-alias parts of typing.py if any
git commit -m "feat(dispatching_rules): introduce package with basic rules (EDD, ODD, MODD, CR)"

# Commit 2: parametrized rules
git checkout origin/feat/new-policies -- src/simulatte/dispatching_rules/parametrized.py tests/core/test_parametrized_rules.py
# Update __init__.py to export them
git commit -m "feat(dispatching_rules): parametrized rules (PST, SOPN)"

# Commit 3: ODD fix
git cherry-pick 05cb1d1 3fcba35
git reset --soft HEAD~2
git commit -m "fix(dispatching_rules): ODD uses static route length"

uv run pytest tests/core/test_basic_rules.py tests/core/test_parametrized_rules.py
uv run pre-commit run --all-files
```

Note: branch 2 mixes `cherry-pick` with `git checkout -- <path>` because the student's commits intermingle FOCUS files with basic/parametrized files (e.g., `dispatching_rules/__init__.py` is edited by both #3 and #6). Pulling files surgically and re-authoring commits is cleaner than fighting cherry-pick conflicts.

Review gate: full diff review with user before continuing.

### Branch 3: `feat/draco-focus`

```bash
git checkout -b feat/draco-focus feat/dispatching-rules

# Commit 1: DRACO + FOCUS (rewired)
git checkout origin/feat/new-policies -- src/simulatte/policies/draco.py src/simulatte/dispatching_rules/focus.py tests/core/test_draco.py tests/core/test_focus.py
# Update dispatching_rules/__init__.py to also export FOCUS
# Update policies/__init__.py and builders.py for DRACO factory
# REWIRING: in draco.py and focus.py, replace every
#     server.refresh_priorities(lambda j, s: -score(j, s, ctx, now, ...))
# with
#     fresh = lambda j, s: -score(j, s, ctx, now, ...)
#     for req in server.put_queue: req.job.priority_policy = fresh
#     server.sort_queue()
# Drop test_server.py changes from f31512a that test refresh_priorities
uv run pytest tests/core/test_draco.py tests/core/test_focus.py
git add -A && git commit -m "feat(policies): DRACO release policy with FOCUS dispatching rule"

# Commit 2: FOCUS WIP-balancing extension
git cherry-pick 89b3930
# Resolve conflicts against the rewired v1 baseline
uv run pytest tests/core/test_draco.py tests/core/test_focus.py
uv run pre-commit run --all-files
```

After PR2 (`feat/dispatching-rules`) merges to main, rebase branch 3:

```bash
git rebase --onto main feat/dispatching-rules feat/draco-focus
```

Review gate: full diff review with user before continuing.

### Publication

PRs are **not** opened automatically. After each branch passes its review gate, the user signals when to push and open the PR.

Three independent releases are expected — version numbers chosen at release time, not pinned in this spec.

### Cleanup

After all three PRs merge:

```bash
git push origin --delete feat/new-policies
git branch -D archive/feat/new-policies   # optional, if local archive no longer needed
```

## Risks and unknowns

- **Branch 3 rewiring** is the only real engineering work. Two call sites, mechanically straightforward, but assumes DRACO/FOCUS tests pass after rewiring. If they don't, the priority-refresh semantics may differ subtly between `refresh_priorities` and `sort_queue` — investigate before declaring the rewiring complete.
- **Test surprises.** The branch was authored against an older `main` (pre-v0.6.0). Tests may fail for reasons unrelated to the reorganization (e.g., changed API on `ContinuousRelease`, server-side changes in v0.6.1). Treat unexpected failures as signals to investigate, not bypass.
- **Naming and design surprises.** The user expects to find questionable naming and design choices during per-branch review. The plan accommodates this by gating each branch on user review before proceeding.
- **`dispatching_rules/__init__.py` editing.** This file is touched by both branch 2 (basic + parametrized) and branch 3 (FOCUS). The file split must be done by hand — easy to miss an export. Verify each branch's `__init__.py` matches its intended scope.

## Out of scope

- Renaming `job.priority_policy` to `job.priority_rule` (could be a future cleanup PR if desired).
- Refactoring the existing `pst_priority_policy` methods on SLAR/LumsCor into the new `dispatching_rules` package (could be a future cleanup PR).
- Documentation updates beyond what each branch already carries.
