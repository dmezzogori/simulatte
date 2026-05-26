# `feat/new-policies` Branch Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `origin/feat/new-policies` into three independently reviewable branches off current `main` (v0.6.1), dropping superseded work and the unwanted attribute rename.

**Architecture:** Cherry-pick / surgical file-checkout from `origin/feat/new-policies` onto three fresh branches: `feat/slar-limit` (1 commit), `feat/dispatching-rules` (3 commits), `feat/draco-focus` (2 commits, includes rewiring of DRACO/FOCUS off the deleted `Server.refresh_priorities` onto main's `sort_queue()` API). The source branch remains untouched as a safety net; no pushes happen without explicit user approval per branch.

**Tech Stack:** git (cherry-pick, checkout, reset), uv, pytest, pre-commit. Python 3.12+. Project source spec: `docs/superpowers/specs/2026-05-26-new-policies-branch-reorg-design.md`.

**Hard constraint from user:** Do NOT `git push` or open PRs without explicit user approval per branch. After each branch's tests pass, stop and wait for the user to review the diff and authorize the next phase.

---

## Phase 0: Setup

### Task 0: Verify clean working tree and create local archive

**Files:** none modified. Pure git state operation.

- [ ] **Step 0.1: Verify working tree is clean and on `main`**

Run:
```bash
git status && git branch --show-current
```

Expected output:
```
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
main
```

If working tree is not clean, stop and ask the user how to proceed. Do not stash or discard changes.

- [ ] **Step 0.2: Fetch latest from origin**

Run:
```bash
git fetch origin
```

- [ ] **Step 0.3: Verify the source branch tip matches what we planned against**

Run:
```bash
git log --oneline origin/feat/new-policies -1
```

Expected first line: `3fcba35 fix(dispatching): use static route length for odd`

If the tip differs, stop and reconcile with the user — the spec was written against a specific tip.

- [ ] **Step 0.4: Create a local archive branch pointing at the source tip**

Run:
```bash
git branch archive/feat/new-policies origin/feat/new-policies
git branch --list 'archive/*'
```

Expected: `archive/feat/new-policies` listed.

This is a safety net. If anything goes wrong during cherry-picking, the original commits are always reachable.

- [ ] **Step 0.5: Confirm main is at v0.6.1 tip**

Run:
```bash
git log --oneline main -1
```

Expected first line should be the v0.6.1 tip (currently `af32280 chore(deps): bump codecov/codecov-action from 6.0.0 to 6.0.1 (#18)` — or whatever follows it if `main` has advanced since this plan was written; just confirm with the user that this is the intended base).

---

## Phase 1: Branch 1 — `feat/slar-limit`

One commit. Smallest, most isolated. Acts as the "warmup" — surfaces any conflict patterns we'll see in later branches.

### Task 1: Create `feat/slar-limit` and apply SLAR-Limit

**Files:**
- Create branch: `feat/slar-limit`
- Cherry-pick (squash to 1 commit): `fa8132f`, `7894d5e`
- Net file changes (created): `src/simulatte/policies/slar_limit.py`, `tests/core/test_slar_limit.py`
- Net file changes (modified): `src/simulatte/policies/__init__.py`, `src/simulatte/builders.py`, `src/simulatte/policies/slar.py`, `tests/core/test_builders.py`

- [ ] **Step 1.1: Create the branch off main**

Run:
```bash
git checkout -b feat/slar-limit main
```

Expected: `Switched to a new branch 'feat/slar-limit'`.

- [ ] **Step 1.2: Cherry-pick both SLAR-Limit commits (no commit yet)**

Run:
```bash
git cherry-pick -n fa8132f 7894d5e
```

`-n` (`--no-commit`) applies the changes without creating commits, so we can collapse them into one. Expected output: a successful apply, possibly with merge-conflict messages.

- [ ] **Step 1.3: If conflicts occurred, resolve them**

If `git cherry-pick` reported conflicts, run:
```bash
git status
```

Likely conflict files: `src/simulatte/builders.py`, `src/simulatte/policies/__init__.py`.

For each conflicted file:
1. Open the file. The student's branch was based pre-v0.6.0, so conflict markers will show the student's edit vs. v0.6.0/v0.6.1 additions (ContinuousRelease, etc.).
2. The correct resolution is: keep BOTH the student's SLAR-Limit additions AND main's existing additions (they are non-overlapping in intent).
3. After resolving, run `git add <file>`.

Verify no conflict markers remain:
```bash
grep -rn '<<<<<<<\|>>>>>>>\|=======' src/simulatte/ tests/ 2>/dev/null | grep -v Binary
```

Expected: no output. If any conflict markers remain, resolve and re-`git add`.

- [ ] **Step 1.4: Verify the staged diff matches expectations**

Run:
```bash
git diff --cached --stat
```

Expected (approximately):
```
 src/simulatte/builders.py            |  +N
 src/simulatte/policies/__init__.py   |  +2
 src/simulatte/policies/slar.py       |  +M (internal refactor for shared code)
 src/simulatte/policies/slar_limit.py | +148
 tests/core/test_builders.py          |  +31
 tests/core/test_slar_limit.py        | +262
```

If `slar_limit.py` is missing or the line counts are wildly off, stop and investigate.

- [ ] **Step 1.5: Commit as a single squashed commit**

Run:
```bash
git commit -m "feat(policies): SLAR-Limit release policy (Thürer & Stevenson 2021)"
```

Expected: one commit on top of main.

- [ ] **Step 1.6: Run the SLAR-Limit tests**

Run:
```bash
uv run pytest tests/core/test_slar_limit.py tests/core/test_slar.py tests/core/test_builders.py -v
```

Expected: all tests pass. If anything fails, do NOT proceed — debug and fix. The most likely cause of failure is a conflict resolution that lost a necessary line in `builders.py` or `policies/__init__.py`.

- [ ] **Step 1.7: Run the full test suite as a sanity check**

Run:
```bash
uv run pytest
```

Expected: all tests pass. SLAR-Limit is supposed to be a pure addition; nothing existing should break.

- [ ] **Step 1.8: Run pre-commit on the changes**

Run:
```bash
uv run pre-commit run --from-ref main --to-ref HEAD
```

Expected: ruff/ty/pytest hooks pass.

- [ ] **Step 1.9: Show the final diff for review**

Run:
```bash
git log --oneline main..HEAD
git diff main..HEAD --stat
```

- [ ] **Step 1.10: STOP — Branch 1 review gate**

Report to the user:
> Branch 1 (`feat/slar-limit`) is ready locally. Tests pass. Do not push or open PR yet. Please review the diff (`git diff main..feat/slar-limit`) and authorize before continuing.

Wait for explicit user approval. Do not proceed to Phase 2 until the user says go.

---

## Phase 2: Branch 2 — `feat/dispatching-rules`

Three commits. The student's source commits intermingle FOCUS (which belongs to Branch 3) with the basic/parametrized rules — so we use `git checkout <ref> -- <path>` surgically per-file rather than cherry-picking whole commits.

### Task 2: Create `feat/dispatching-rules` and bootstrap the package

**Files:**
- Create branch: `feat/dispatching-rules` off `main`
- Create: `src/simulatte/dispatching_rules/__init__.py` (basic exports only — FOCUS exports excluded)
- Create: `src/simulatte/dispatching_rules/basic.py`
- Create: `tests/core/test_basic_rules.py`
- Modify: `src/simulatte/typing.py` (add `DispatchingRule` alias only)

- [ ] **Step 2.1: Create the branch off main**

Run:
```bash
git checkout -b feat/dispatching-rules main
```

Expected: `Switched to a new branch 'feat/dispatching-rules'`.

- [ ] **Step 2.2: Pull basic rules files from the source branch**

Run:
```bash
git checkout origin/feat/new-policies -- \
  src/simulatte/dispatching_rules/__init__.py \
  src/simulatte/dispatching_rules/basic.py \
  tests/core/test_basic_rules.py
```

Expected: three files now staged.

- [ ] **Step 2.3: Edit `__init__.py` to remove FOCUS-related exports**

Open `src/simulatte/dispatching_rules/__init__.py`. The file (as it exists on `origin/feat/new-policies`) re-exports rules from `basic`, `parametrized`, AND `focus`. For this commit we want only the `basic` exports. Remove any imports from `parametrized` (added in Task 3) and any imports from `focus` (added in Branch 3).

After editing, the file should import only from `basic` (e.g., `EDD`, `ODD`, `MODD`, `CR`, plus any pre-existing `SPT` / `FIFO` / etc. rules that the rename commit moved into `basic.py`).

Verify by running:
```bash
python -c "from simulatte.dispatching_rules import __all__; print(__all__)"
```

Expected: only basic-rule names listed.

- [ ] **Step 2.4: Add the `DispatchingRule` alias to `typing.py`**

`typing.py` exists on main and uses PEP 695 `type` syntax. Look at the student's diff:
```bash
git show 1d05d66 -- src/simulatte/typing.py
```

Then open `src/simulatte/typing.py` on the current branch and ADD only a `DispatchingRule` alias matching the file's style. The existing aliases look like:
```python
type Distribution[T] = Callable[[], T]
```

Add (somewhere in the alias block — match surrounding style):
```python
type DispatchingRule = Callable[["BaseJob", "Server"], float]
```

You may need to add a `TYPE_CHECKING` import for `BaseJob` from `simulatte.job` if not already present. `Server` is already imported. Use string forward references if circular-import concerns arise.

Do NOT pull anything else from the student's `typing.py` changes — only the alias.

- [ ] **Step 2.5: Inspect `basic.py` for unwanted dependencies**

Open `src/simulatte/dispatching_rules/basic.py`. Verify:
- Rules import from `simulatte.typing` (DispatchingRule alias) — fine.
- Rules do NOT reference the renamed attribute `job.priority_rule`. They should reference `job` and `server` directly inside their bodies (they are callbacks, not setters).
- The signature pattern is `def edd(job: BaseJob, server: Server) -> float:` (or equivalent).

If `basic.py` references `priority_rule` anywhere (e.g., setting an attribute on a job), replace with `priority_policy`. The rule files should rarely set anything — they are pure functions.

- [ ] **Step 2.6: Stage and verify**

Run:
```bash
git add src/simulatte/dispatching_rules/__init__.py \
        src/simulatte/dispatching_rules/basic.py \
        src/simulatte/typing.py \
        tests/core/test_basic_rules.py
git diff --cached --stat
```

Expected approximate output:
```
 src/simulatte/dispatching_rules/__init__.py | +N
 src/simulatte/dispatching_rules/basic.py    | +M
 src/simulatte/typing.py                     |  +1-2 lines (just the alias)
 tests/core/test_basic_rules.py              | +K
```

- [ ] **Step 2.7: Run the basic-rule tests**

Run:
```bash
uv run pytest tests/core/test_basic_rules.py -v
```

Expected: all pass.

If failures: most likely cause is either (a) the test file references a parametrized rule not yet pulled in (defer that test to Task 3 — comment it out and re-enable in Task 3), or (b) a rule references `priority_rule` instead of `priority_policy`.

- [ ] **Step 2.8: Commit**

Run:
```bash
git commit -m "feat(dispatching_rules): introduce package with basic rules (EDD, ODD, MODD, CR)"
```

### Task 3: Add parametrized rules

**Files:**
- Create: `src/simulatte/dispatching_rules/parametrized.py`
- Create: `tests/core/test_parametrized_rules.py`
- Modify: `src/simulatte/dispatching_rules/__init__.py` (add parametrized exports)

- [ ] **Step 3.1: Pull parametrized files**

Run:
```bash
git checkout origin/feat/new-policies -- \
  src/simulatte/dispatching_rules/parametrized.py \
  tests/core/test_parametrized_rules.py
```

- [ ] **Step 3.2: Edit `__init__.py` to add parametrized exports**

Open `src/simulatte/dispatching_rules/__init__.py`. Add imports/re-exports for the parametrized rules (PST, SOPN, or whatever the file defines). Use the layout from `origin/feat/new-policies` as a reference:

```bash
git show origin/feat/new-policies:src/simulatte/dispatching_rules/__init__.py
```

Copy only the parametrized section. Do NOT include FOCUS exports.

- [ ] **Step 3.3: Re-enable any test cases that were deferred in Task 2.7**

If you commented out parametrized-rule tests in Task 2 (Step 2.7), re-enable them now.

- [ ] **Step 3.4: Stage and verify**

Run:
```bash
git add src/simulatte/dispatching_rules/parametrized.py \
        tests/core/test_parametrized_rules.py \
        src/simulatte/dispatching_rules/__init__.py
git diff --cached --stat
```

- [ ] **Step 3.5: Run parametrized tests**

Run:
```bash
uv run pytest tests/core/test_parametrized_rules.py -v
```

Expected: all pass.

- [ ] **Step 3.6: Commit**

Run:
```bash
git commit -m "feat(dispatching_rules): parametrized rules (PST, SOPN)"
```

### Task 4: Apply ODD static-route-length fix

**Files:**
- Modify: `src/simulatte/dispatching_rules/basic.py` (ODD function)
- Modify: `tests/core/test_basic_rules.py`
- Create: `docs/superpowers/specs/2026-05-20-odd-static-routing-design.md`

- [ ] **Step 4.1: Cherry-pick both ODD-fix commits without committing**

Run:
```bash
git cherry-pick -n 05cb1d1 3fcba35
```

`05cb1d1` is the design doc, `3fcba35` is the fix. We squash them into one commit since the doc explains the fix.

- [ ] **Step 4.2: Resolve any conflicts**

If conflicts in `basic.py` (because of the file structure changes from earlier tasks), resolve manually. The fix is small (per the source diff, ~12 lines changed in `basic.py` plus a handful of test changes).

Verify no conflict markers remain:
```bash
grep -rn '<<<<<<<\|>>>>>>>' src/simulatte/ tests/ docs/ 2>/dev/null
```

Expected: no output.

- [ ] **Step 4.3: Stage all changes**

Run:
```bash
git add -A
git diff --cached --stat
```

Expected approximate output:
```
 docs/superpowers/specs/2026-05-20-odd-static-routing-design.md | +65
 src/simulatte/dispatching_rules/basic.py                       | +N -M
 tests/core/test_basic_rules.py                                 | +K -J
```

- [ ] **Step 4.4: Run basic-rule tests**

Run:
```bash
uv run pytest tests/core/test_basic_rules.py -v
```

Expected: all pass, including the ODD tests that exercise static-route-length behavior.

- [ ] **Step 4.5: Commit**

Run:
```bash
git commit -m "fix(dispatching_rules): ODD uses static route length"
```

### Task 5: Full validation and review gate for Branch 2

- [ ] **Step 5.1: Run the full dispatching-rules test suite**

Run:
```bash
uv run pytest tests/core/test_basic_rules.py tests/core/test_parametrized_rules.py -v
```

Expected: all pass.

- [ ] **Step 5.2: Run the full project test suite**

Run:
```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 5.3: Run pre-commit**

Run:
```bash
uv run pre-commit run --from-ref main --to-ref HEAD
```

Expected: all hooks pass.

- [ ] **Step 5.4: Show the branch history**

Run:
```bash
git log --oneline main..HEAD
git diff main..HEAD --stat
```

Expected: 3 commits, all under `dispatching_rules/` and `tests/core/test_*_rules.py`, plus the small `typing.py` alias addition and the ODD design doc.

- [ ] **Step 5.5: STOP — Branch 2 review gate**

Report to the user:
> Branch 2 (`feat/dispatching-rules`) is ready locally. Three commits, tests pass. Do not push or open PR yet. Please review the diff and authorize before continuing.

Wait for explicit user approval before proceeding to Phase 3.

---

## Phase 3: Branch 3 — `feat/draco-focus`

Two commits. Includes the only real engineering work in the reorganization: rewiring DRACO and FOCUS off the (deleted) `Server.refresh_priorities` API onto main's `sort_queue()` API. Branched off `feat/dispatching-rules` so the package already exists.

### Task 6: Create the branch and pull DRACO + FOCUS files

**Files:**
- Create branch: `feat/draco-focus` off `feat/dispatching-rules`
- Create: `src/simulatte/policies/draco.py`, `src/simulatte/dispatching_rules/focus.py`, `tests/core/test_draco.py`, `tests/core/test_focus.py`
- Modify: `src/simulatte/dispatching_rules/__init__.py` (add FOCUS export), `src/simulatte/policies/__init__.py` (add DRACO export), `src/simulatte/builders.py` (add DRACO factory)

- [ ] **Step 6.1: Create the branch**

Run:
```bash
git checkout -b feat/draco-focus feat/dispatching-rules
```

- [ ] **Step 6.2: Pull DRACO + FOCUS source and test files**

Run:
```bash
git checkout origin/feat/new-policies -- \
  src/simulatte/policies/draco.py \
  src/simulatte/dispatching_rules/focus.py \
  tests/core/test_draco.py \
  tests/core/test_focus.py
```

- [ ] **Step 6.3: Update `dispatching_rules/__init__.py` to export FOCUS**

Open `src/simulatte/dispatching_rules/__init__.py`. Reference the source-branch version:
```bash
git show origin/feat/new-policies:src/simulatte/dispatching_rules/__init__.py
```

Add the FOCUS-related imports and `__all__` entries. Keep everything from Branch 2 intact.

- [ ] **Step 6.4: Update `policies/__init__.py` to export DRACO**

Open `src/simulatte/policies/__init__.py`. Reference the source-branch version:
```bash
git show origin/feat/new-policies:src/simulatte/policies/__init__.py
```

Add DRACO to the exports.

- [ ] **Step 6.5: Update `builders.py` to add the DRACO factory**

Open `src/simulatte/builders.py`. The DRACO factory function was added in commit `7d5eb9f`. Reference:
```bash
git show 7d5eb9f -- src/simulatte/builders.py
```

Apply that addition to `builders.py`. The addition is purely additive (a new function). Confirm no other parts of `builders.py` are touched — if the diff suggests broader edits, re-check whether you're picking up the rename, which we explicitly skip.

If `builders.py` references `priority_rule` anywhere in the DRACO factory code, change it to `priority_policy` to match main's attribute name.

### Task 7: Rewire DRACO and FOCUS off `refresh_priorities`

This is the only real engineering work in the reorganization. Two call sites total.

**Background:** The student's deleted method `Server.refresh_priorities(priority_fn)` did, for each request in `server.queue`: `req.key = (priority_fn(req.job, server), req.time, not req.preempt)`, then sorted. Main's `Server.sort_queue()` does effectively the same but reads from `req.job.priority(req.server)` (which calls `req.job.priority_policy(req.job, req.server)`). Therefore the rewiring pattern is: set each queued job's `priority_policy` to the fresh closure, then call `server.sort_queue()`.

**Call site 1 (DRACO):** `src/simulatte/policies/draco.py`. Find the method that contained `server.refresh_priorities(lambda j, s: -self._full_score(j, s, ctx, now, wip, in_psp=False))`.

- [ ] **Step 7.1: Open `draco.py` and locate the `refresh_priorities` call**

Run:
```bash
grep -n "refresh_priorities" src/simulatte/policies/draco.py
```

Expected: one match (the line that originally read `server.refresh_priorities(lambda j, s: -self._full_score(j, s, ctx, now, wip, in_psp=False))`).

- [ ] **Step 7.2: Replace the DRACO call site**

In `draco.py`, replace:
```python
server.refresh_priorities(lambda j, s: -self._full_score(j, s, ctx, now, wip, in_psp=False))
```

with:
```python
fresh_rule = lambda j, s: -self._full_score(j, s, ctx, now, wip, in_psp=False)
for req in server.queue:
    req.job.priority_policy = fresh_rule
server.sort_queue()
```

Also update the surrounding docstring in the enclosing method: change any reference to `Server.refresh_priorities` to `Server.sort_queue` and briefly describe the assign-then-sort pattern. Search for the docstring text:
```bash
grep -n "refresh_priorities" src/simulatte/policies/draco.py
```

Expected after edits: zero matches.

- [ ] **Step 7.3: Replace the FOCUS call site**

Run:
```bash
grep -n "refresh_priorities" src/simulatte/dispatching_rules/focus.py
```

Expected: one match (in `refresh_focus_queue` or similar).

In `focus.py`, replace:
```python
server.refresh_priorities(lambda j, s: -focus.score(j, s, ctx, now))
```

with:
```python
fresh_rule = lambda j, s: -focus.score(j, s, ctx, now)
for req in server.queue:
    req.job.priority_policy = fresh_rule
server.sort_queue()
```

Update the surrounding docstring similarly. Verify:
```bash
grep -n "refresh_priorities" src/simulatte/dispatching_rules/focus.py
```

Expected: zero matches.

- [ ] **Step 7.4: Scan the entire branch for any lingering references**

Run:
```bash
grep -rn "refresh_priorities" src/simulatte/ tests/ docs/ 2>/dev/null
```

Expected: zero matches. If any remain (e.g., a stray test file that imported the deleted method), remove them or rewrite them. Tests of `Server.refresh_priorities` belong on Branch 2/main only if the method existed — it doesn't, so they should be dropped.

- [ ] **Step 7.5: Inspect DRACO/FOCUS test files for `refresh_priorities` setup**

Run:
```bash
grep -n "refresh_priorities\|priority_rule" tests/core/test_draco.py tests/core/test_focus.py
```

Expected: zero matches (FOCUS/DRACO tests should observe queue *order*, not call the refresh method directly). If matches found:
- `refresh_priorities` calls in tests: replace with the assign-then-sort_queue pattern.
- `priority_rule` attribute references: replace with `priority_policy`.

- [ ] **Step 7.6: Scan for the renamed attribute everywhere**

Run:
```bash
grep -rn "priority_rule" src/simulatte/ tests/ 2>/dev/null
```

Expected: zero matches in `src/`. If found in `tests/test_draco.py` or `tests/test_focus.py`, replace with `priority_policy`.

The student's rename was wholesale; we are reverting it everywhere except the `DispatchingRule` type alias.

### Task 8: Validate Branch 3 commit 1

- [ ] **Step 8.1: Stage all changes**

Run:
```bash
git add -A
git status --short
```

Expected files added/modified:
- `src/simulatte/policies/draco.py` (new)
- `src/simulatte/dispatching_rules/focus.py` (new)
- `src/simulatte/dispatching_rules/__init__.py` (modified: add FOCUS export)
- `src/simulatte/policies/__init__.py` (modified: add DRACO export)
- `src/simulatte/builders.py` (modified: DRACO factory)
- `tests/core/test_draco.py` (new)
- `tests/core/test_focus.py` (new)

- [ ] **Step 8.2: Run DRACO and FOCUS tests**

Run:
```bash
uv run pytest tests/core/test_draco.py tests/core/test_focus.py -v
```

Expected: all pass.

If failures appear, the most likely causes:
1. **The rewiring is semantically off.** Re-check: does the test exercise a sequence where the priority changes between dispatch events? Set a breakpoint in the modified method, verify the closure captures the expected `ctx`/`now`/`wip`, and that `server.queue` contains the expected requests.
2. **`server.queue` is empty when the refresh fires.** This is possible if the test sequence doesn't enqueue before the refresh. Compare against the student's intent: were they refreshing before any jobs were queued? If so, the rewrite still works (the for-loop simply doesn't iterate), and `sort_queue` is a no-op.
3. **An import or rename slipped through.** Run the `priority_rule` grep again (Step 7.6).
4. **A FOCUS-extension feature is needed.** Recall that commit `89b3930` extended FOCUS with WIP balancing. If the v1 FOCUS at this point doesn't have it, some tests may be testing the extension's behavior — defer those tests to Task 9 (comment out and re-enable after the WIP-balancing extension is added).

- [ ] **Step 8.3: Run the full test suite**

Run:
```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 8.4: Commit Branch 3, commit 1**

Run:
```bash
git commit -m "feat(policies): DRACO release policy with FOCUS dispatching rule"
```

### Task 9: Apply FOCUS WIP-balancing extension

**Files:**
- Cherry-pick: `89b3930`
- Modify: `src/simulatte/dispatching_rules/focus.py`, `src/simulatte/policies/draco.py`, `src/simulatte/builders.py`, `tests/core/test_draco.py`, `tests/core/test_focus.py`

- [ ] **Step 9.1: Cherry-pick the WIP-balancing extension**

Run:
```bash
git cherry-pick 89b3930
```

Expected: success, or a conflict in `focus.py` because the v1 baseline we built in Task 6 is slightly different from what the student had at the time of `89b3930` (specifically, the `refresh_priorities` call site differs — we rewired it, they hadn't yet).

- [ ] **Step 9.2: If conflicts, resolve**

The conflict region will likely involve the call site we already rewired. Keep the rewired pattern (assign-then-sort_queue) and integrate the WIP-balancing additions around it.

Verify no markers remain:
```bash
grep -rn '<<<<<<<\|>>>>>>>' src/simulatte/ tests/ 2>/dev/null
```

Re-scan for the dead API:
```bash
grep -rn "refresh_priorities\|priority_rule" src/simulatte/ tests/ 2>/dev/null
```

Expected: zero matches.

- [ ] **Step 9.3: Re-enable any tests deferred at Step 8.2**

If you commented out WIP-balancing tests in Task 8, uncomment them now.

- [ ] **Step 9.4: Run DRACO + FOCUS tests**

Run:
```bash
uv run pytest tests/core/test_draco.py tests/core/test_focus.py -v
```

Expected: all pass.

- [ ] **Step 9.5: Run the full test suite**

Run:
```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 9.6: If cherry-pick succeeded cleanly, the commit already exists**

If `git cherry-pick 89b3930` completed without conflicts in Step 9.1, the commit is already on the branch. Verify:
```bash
git log --oneline feat/dispatching-rules..HEAD
```

Expected: 2 commits (Task 8's commit + the cherry-picked WIP-balancing commit).

If conflict resolution was needed, `git cherry-pick --continue` will have prompted for the commit. Use a commit message of:
```
feat(dispatching_rules): FOCUS WIP-balancing extension
```

### Task 10: Final validation and review gate for Branch 3

- [ ] **Step 10.1: Final full test run**

Run:
```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 10.2: Run pre-commit on the changes**

Run:
```bash
uv run pre-commit run --from-ref feat/dispatching-rules --to-ref HEAD
```

Expected: all hooks pass.

- [ ] **Step 10.3: Show the branch history**

Run:
```bash
git log --oneline feat/dispatching-rules..HEAD
git diff feat/dispatching-rules..HEAD --stat
```

Expected: 2 commits.

- [ ] **Step 10.4: Final scan for forbidden patterns**

Run:
```bash
grep -rn "refresh_priorities\|priority_rule" src/simulatte/ tests/ 2>/dev/null
```

Expected: zero matches across the entire working tree.

- [ ] **Step 10.5: STOP — Branch 3 review gate**

Report to the user:
> Branch 3 (`feat/draco-focus`) is ready locally. Two commits, rewiring complete, tests pass. Do not push or open PR yet. Please review the diff and authorize.

Wait for explicit user approval.

---

## Phase 4: Hand-off

Do NOT execute Phase 4 without explicit user instruction. These steps are listed for completeness; the user runs them once all three PRs are merged.

- [ ] **Step F.1: Push branches (user-gated, per branch)**

When the user authorizes push for a given branch:
```bash
git push -u origin feat/slar-limit       # only when authorized
git push -u origin feat/dispatching-rules  # only when authorized
git push -u origin feat/draco-focus       # only when authorized
```

- [ ] **Step F.2: Open PRs (user-gated)**

When the user authorizes, open PRs against `main` for each branch. `feat/draco-focus`'s base may be `feat/dispatching-rules` until PR2 merges, then rebased onto `main`:
```bash
git rebase --onto main feat/dispatching-rules feat/draco-focus
git push --force-with-lease
```

- [ ] **Step F.3: Cleanup (after all three PRs merge)**

```bash
git push origin --delete feat/new-policies
git branch -D archive/feat/new-policies   # optional, if local archive no longer needed
```

---

## Risk register

- **Conflict resolution in Step 1.3 loses a line.** Mitigation: Step 1.4 stat check + Step 1.6 targeted test run.
- **`__init__.py` editing drift across Branches 2 and 3.** Mitigation: explicit FOCUS-exclusion check in Step 2.3; explicit FOCUS-inclusion check in Step 6.3. Final scan in Step 7.4.
- **The rewiring (Task 7) has subtle semantic differences vs `refresh_priorities`.** The Task 7 background note explains why mechanical equivalence holds: both methods iterate `server.queue` and re-key. The only difference is the key source (passed function vs `job.priority_policy`), and we make them equivalent by writing the fresh closure into `priority_policy` first. If a test fails, Step 8.2's failure-mode list gives concrete debugging paths.
- **`89b3930`'s cherry-pick conflicts with the rewired baseline.** Mitigation: Task 9 explicitly anticipates this and instructs to keep the rewired pattern while integrating WIP-balancing changes.
- **Pushes happen without authorization.** Mitigation: every review gate explicitly says "do not push". Push is a separate Phase 4, never reached without user instruction.

## Out of scope (per spec)

- Renaming `job.priority_policy` to `job.priority_rule`.
- Refactoring the existing `pst_priority_policy` methods on SLAR/LumsCor into the new `dispatching_rules` package.
- Any documentation update beyond what the source commits already include.
