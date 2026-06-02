# PyPy compatibility assessment

**Goal:** define exactly what is required to legitimately state *"simulatte is compatible
with PyPy"* and let users run on PyPy when they need to. **Performance is explicitly out of
scope** here — this is a compatibility-and-support-posture question, not a speed one.

---

## Verdict

simulatte's **simulation core and every non-plotting feature are PyPy-compatible today** —
verified, not assumed. On PyPy 3.11 (via `uv`), after the changes on this branch, the
`tests/core` + `tests/intralogistics` suites run **811 passed, 5 failed**, where the **only 5
failures are matplotlib plotting tests** (they need matplotlib installed via cpyext — see the
feature matrix). All dispatch policies, intralogistics, and text/JSON/**SQLite** logging pass,
and seeded runs produce **identical results** to CPython. There are **no 3.12+ language/stdlib
features left** outside the ones already backported.

Therefore "what we need to do" is now **a support-posture commitment plus CI + docs** — the
porting/code work is essentially **done on this branch**. The single real decision is
committing to a **Python 3.11 language floor**. Everything else follows from it.

---

## The one foundational decision: adopt a Python 3.11 language floor

PyPy's newest release implements **Python 3.11** (PyPy 3.12 is in development, unreleased). To
support PyPy *at all*, the codebase must stay within the 3.11 language. Stating PyPy
compatibility therefore means:

- `requires-python = ">=3.11"` (today: `>=3.12`)
- ruff `target-version = "py311"` (today: `py312`)
- **no PEP 695 syntax and no other 3.12+ language/stdlib features** anywhere in `src/`

**This is an ongoing constraint, not a one-time change.** It is the real cost of the claim:
- You give up PEP 695 type-syntax sugar (already backported) and **any future 3.12+ feature**
  on all shipped code, for as long as you support PyPy.
- It is **temporary in principle**: when PyPy ships 3.12, the floor can rise and the backport
  can be reverted. There is no committed PyPy-3.12 date.
- Side effect (positive): it also makes simulatte run on **CPython 3.11**, broadening support
  rather than narrowing it.

> If this floor is **not** acceptable, then "compatible with PyPy" cannot be honestly stated;
> the most you can offer is *"runs on PyPy if you backport the 3.12+ syntax yourself"* — which
> is what this branch demonstrates, but is not a support claim.

Everything below assumes the floor decision is **yes**.

---

## Work items

### A. Code (the simulation core) — mostly done

| Item | Status | Notes |
|---|---|---|
| PEP 695 backport (`typing.py`, `runner.py`) | ✅ done (this branch) | `TypeVar`/`TypeAlias`/`Generic`; identical on CPython 3.12+; full suite still 843 green @99 %. |
| Lazy `matplotlib` import (`server.py`) | ✅ done (this branch) | Lets the package import on PyPy without the cpyext matplotlib/numpy bridge; also speeds headless CPython. |
| **SQLite logger cursor handling** | ✅ **done + verified** (this branch) | Fixed; see below. |
| Audit for other 3.12+ features | ✅ none found | Confirmed by grep + 811 passing PyPy tests. |

**The SQLite logger fix (was 21 PyPy test failures — now resolved).** PyPy's cffi `sqlite3`
is stricter than CPython's C module: it refuses `commit()` while a cursor still has an
unconsumed statement (`OperationalError: cannot commit transaction - SQL statements in
progress`). In `logger.py`, the setup `PRAGMA journal_mode=WAL` / `busy_timeout` calls
returned rows whose cursors were never consumed, and `SQLiteStore.insert` did `execute(...)`
then `commit()` without closing the cursor. **Fixed** by `.fetchall()`-ing the PRAGMA cursors
and `cursor.close()` before the insert `commit()` — **portable** (CPython behaviour
unchanged). Verified: the SQLite-logger tests now pass **31/31 on both PyPy and CPython**, and
the full PyPy core+intralogistics suite is green except matplotlib plotting.

### B. Packaging metadata & lint posture

| Item | Change |
|---|---|
| `requires-python` | `">=3.12"` → `">=3.11"` |
| ruff `target-version` | `"py312"` → `"py311"` — **this auto-disables UP040/UP046**, so the two `# ruff: noqa` lines added for the backport can be removed (the rules stop firing) and *new* 3.12+ syntax is rejected at lint time. |
| Trove classifiers | add `Programming Language :: Python :: Implementation :: PyPy` (and `:: 3.11`). |

Lowering the ruff target is what turns the backport from a "fights the project's own rules"
hack into the enforced norm.

### C. CI — essential to *maintain* the claim

A compatibility statement that isn't tested in CI silently rots. Add a **PyPy 3.11 lane** to
`.github/workflows/ci.yml` (current matrix: CPython 3.12/3.13/3.14):

- `uv python install pypy-3.11`; install the pure-Python deps; run lint + `pytest tests/core`
  (and `tests/intralogistics`).
- **Coverage:** coverage.py's C tracer doesn't load on PyPy, but it falls back to a pure-Python
  tracer — either accept the slower tracer or run the PyPy lane with `--no-cov`.
- **`tests/experimental`:** excluded or best-effort (needs numpy/gymnasium via cpyext — see D).

### D. Dependency / supported-feature matrix on PyPy

State precisely *what* is compatible, so users know what they're getting:

| Capability | PyPy 3.11 status |
|---|---|
| Simulation engine + **all** dispatch policies | ✅ **fully supported** (proven, identical results) |
| Intralogistics (AGV/warehouse/graph) | ✅ supported (pure Python + `simpy`) |
| Text / JSON logging | ✅ supported |
| **SQLite logging** (`log_db_path`) | ✅ supported (fixed + verified 31/31 on PyPy) |
| Plotting (`plot_*`, matplotlib/numpy) | ⚠️ works via **cpyext** (slow C-ext bridge); optional/visualization only |
| Experimental RL (`experimental`, gymnasium/numpy) | ⚠️ **best-effort** (cpyext; module is explicitly unstable) |

Pure-Python deps (`simpy`, `loguru`, `tqdm`, `tabulate`) are first-class on PyPy.
`matplotlib`/`numpy` publish PyPy wheels but run through cpyext — confirm they install on the
target platform (not verified here); they are never on the simulation hot path.

### E. Documentation

- A "Running on PyPy" page: install via `uv python install pypy-3.11` + `uv venv --python
  pypy-3.11` + `uv pip install` the deps; the supported-feature matrix above; the 3.11-floor
  note and its temporariness; and the explicit limitation list (SQLite until fixed, plotting
  via cpyext, experimental best-effort).
- A one-line support statement in the README / docs landing.

---

## Checklist (if the floor decision is "yes")

1. **[decision]** Accept the Python 3.11 language floor (ongoing, until PyPy 3.12). *(only real decision)*
2. **[code]** ~~Fix `SQLiteStore` cursor/commit handling~~ ✅ **done + verified** on this branch.
3. **[meta]** `requires-python = ">=3.11"`; ruff `target-version = "py311"` (drop the two backport `noqa`s); add PyPy classifiers.
4. **[ci]** Add a `pypy-3.11` lane (lint + `tests/core` + `tests/intralogistics`; coverage via pure tracer or `--no-cov`; experimental excluded).
5. **[verify]** Green PyPy lane; confirm `matplotlib`/`numpy` install on PyPy if plotting is to be claimed.
6. **[docs]** "Running on PyPy" page + feature matrix + support statement.

The weight of the decision is entirely in **item 1**; items 3–6 are small and mechanical.

**Already complete + verified on `investigate/pypy`:** the PEP 695 backport, the
lazy-matplotlib change, and the SQLite-logger fix — i.e. the entire *code* side of PyPy
compatibility. What remains is metadata/lint posture (3), a CI lane (4), and docs (6).

---

*Scope note: this assessment deliberately excludes the performance-optimisation work explored
separately (`perf/hotpath-investigation`), which is being set aside.*
