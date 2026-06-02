# PyPy investigation: does running simulatte on PyPy make it faster?

**Date:** 2026-06-02 · **Branch:** `investigate/pypy` (from `main`) · **Status:** investigation, working PyPy run

---

## TL;DR

Running simulatte unchanged on **PyPy 3.11** (via `uv`) gives a **real but modest speedup**,
**well below** the 3–8× I projected in the earlier report, and with **identical simulation
results**:

| Policy | CPython 3.14 (warm) | PyPy 3.11 (warm) | Speedup | Results |
|---|---|---|---|---|
| **DRACO**, `until=40000` (61 487 jobs) | 21.71 s | 11.58 s | **1.85×** | identical |
| DRACO, `until=15000` | 7.79 s | 4.39 s | 1.78× | identical |
| SLAR, `until=15000` | 2.18 s | 1.94 s | 1.12× | identical |
| LumsCor, `until=15000` | 2.03 s | 1.65 s | 1.23× | identical |

**Why not 3–8×?** That figure is the literature number for *pure-SimPy* event loops with
trivial process bodies. simulatte's dispatch hot paths are **object-graph traversal +
allocation** (attribute access, dict lookups, tuple/genexpr building), which PyPy's tracing
JIT accelerates moderately (~1.2–1.9×), not the tight numeric loops where it reaches 5–10×.
The heavier the per-event Python (DRACO/FOCUS), the better PyPy does.

**It needed almost no code changes** — only a PEP 695 syntax backport (PyPy maxes at 3.11)
and making one matplotlib import lazy. **No dependency install was even required**: the
pure-Python deps run straight from the existing CPython venv.

**Bottom line vs. the hand-caching prototype:** PyPy gives a *bigger* and *more uniform* win
(helps simple policies too, instead of regressing them) for *zero* hot-path code change — but
costs a second interpreter, loses the optional SQLite logger, and pays seconds of JIT/import
warmup. The two are **not mutually exclusive**, and whether they stack is the obvious next
experiment (below).

---

## 1. Correctness — the critical gate

PyPy is only useful if it produces the *same* simulation. Two sub-questions, both answered
empirically on a bare interpreter before any other work.

### RNG stream: byte-identical ✅

simulatte draws all randomness from the stdlib `random` module (Mersenne Twister). Seeded
runs of every function the sim uses produce **byte-identical** sequences on CPython 3.14 and
PyPy 3.11:

| function | CPython 3.14 | PyPy 3.11 |
|---|---|---|
| `random()` | `0.639426798458, …` | `0.639426798458, …` (identical) |
| `expovariate`, `randint`, `sample`, `uniform` | … | identical |

### Float `sum()`: diverges in theory, **did not matter in practice** ✅

CPython 3.12+ uses **Neumaier compensated summation**; PyPy 3.11 uses **naive** summation.
A crafted case shows the difference:

```python
sum([1.0, 1e100, 1.0, -1e100])   # CPython 3.14 -> 2.0   PyPy 3.11 -> 0.0
```

This *could* flip a FOCUS dispatch comparison and diverge the run. **It did not.** Every
aggregate matched to all 6 measured significant figures, on every policy and both horizons:

| metric | CPython | PyPy |
|---|---|---|
| DRACO jobs / avg_tis / worked | 61 487 / 7.905606 / 212748.5720 | **identical** |
| SLAR jobs / avg_tis | 22 931 / 11.315274 | **identical** |
| LumsCor jobs / avg_tis | 22 925 / 9.989390 | **identical** |

**Why:** the Neumaier/naive difference only appears under *catastrophic cancellation* (summing
large and small terms of opposite sign). simulatte's dispatch sums — per-server workload
(`sum p_ij`, all similar-magnitude positives) and slack (`d_i − now − sum p_ij`) — are
**well-conditioned**, so naive and compensated summation agree bit-for-bit. The divergence is
a real cross-interpreter hazard but is **latent** for this model's arithmetic.

> Correctness bar met: **same RNG stream + well-conditioned float ops → identical results.**
> Do not assume this for an arbitrary model; a model that sums signed terms of wildly different
> magnitude on the decision path could diverge.

### Test suite on PyPy: 465/486 core pass; failures are the optional SQLite logger

`tests/core` on PyPy: **465 passed, 21 failed**. All 21 failures are in `test_logger_sqlite.py`
and share one cause:

```
_sqlite3.OperationalError: cannot commit transaction - SQL statements in progress
```

PyPy's cffi-based `sqlite3` is stricter than CPython's C module about committing while a
cursor still has an in-progress statement (`SimLogger`'s `_db_store.insert` → `commit()`).
This is the **optional** SQLite persistence feature (`Environment(log_db_path=…)`), not the
simulation core. Likely a small fix (finalise/close the cursor before `commit`, or wrap in a
`with conn:` block), but out of scope here. `tests/experimental` is not runnable on PyPy
(imports `numpy`, a CPython C-extension). Everything else — engine, all dispatch policies,
text/JSON logging — passes.

---

## 2. Performance — cold vs. warm

PyPy's JIT compiles hot loops only after they have run enough, so the first run (and short
sims) pay compilation. Both numbers are real and matter for different uses.

`seed=42`, median of repeated in-process runs.

| Policy / horizon | CPython warm | PyPy **cold** (run 1) | PyPy **warm** (steady state) |
|---|---|---|---|
| DRACO `until=40000` | 21.71 s | 11.75 s (1.84×) | **11.58 s (1.85×)** |
| DRACO `until=15000` | 7.79 s | 4.99 s (1.55×) | **4.39 s (1.78×)** |
| SLAR `until=15000` | 2.18 s | 2.05 s (1.07×) | 1.94 s (1.12×) |
| LumsCor `until=15000` | 2.03 s | 2.28 s (**0.90× — slower**) | 1.65 s (1.23×) |

Observations:
- **Warm speedup scales with per-event Python weight.** DRACO/FOCUS (the heaviest dispatch)
  gets ~1.8–1.85×; the lighter rule-based policies get ~1.1–1.2×.
- **Cold can be a wash or a loss** for light/short runs (LumsCor cold = 0.90×) because JIT
  compilation isn't amortised. PyPy pays off on **long runs and multi-seed studies**
  (`Runner`), where warmup is absorbed and the JIT stays hot across seeds in one process.
- Whole-process wall for the `until=15000 × 6 runs × 3 policies` sweep: **CPython 71.9 s →
  PyPy 49.5 s** (≈1.45× on the mixed batch, dominated by DRACO).
- Add ~1–2 s of PyPy interpreter startup + cpyext import to any one-shot invocation; it
  swamps short sims and is irrelevant to long ones.

---

## 3. What it took to get there (enabling changes, committed on this branch)

1. **PEP 695 backport** (`typing.py`, `runner.py`): `type X[T] = …` and `class Runner[S, T]`
   are 3.12+ syntax that PyPy 3.11 can't even parse. Rewritten with `TypeVar`/`TypeAlias`/
   `Generic`; **identical on CPython 3.12+** (full suite still 843 green at 99 % coverage).
   `System`/`PushSystem`/`PullSystem` keep a string forward-ref to `Router` (a
   `TYPE_CHECKING`-only import — a runtime import would be circular).
2. **Lazy matplotlib import** (`server.py`): moved the top-level `import matplotlib.pyplot`
   into `plot_qt`/`plot_ut` (matching `shopfloor.py`). Headless runs no longer import
   matplotlib/numpy — a clean win on CPython too, and it lets the package import on PyPy
   without the cpyext matplotlib/numpy bridge.
3. **No dependency install needed.** PyPy interpreter via `uv venv --python pypy-3.11`. The
   runtime deps actually used by the sim (`simpy`, `loguru`, `tqdm`, `tabulate`) are pure
   Python and import straight from the existing CPython `site-packages` via `PYTHONPATH`;
   `matplotlib`/`numpy`/`gymnasium` (plotting + RL only, off the hot path) are simply never
   imported. A production setup would instead `uv pip install` the pure-Python deps into a
   dedicated PyPy venv.

Run command used for the benchmark:
```bash
uv venv --python pypy-3.11 /tmp/pypy-venv
PYTHONPATH=src:.venv/lib/python3.14/site-packages /tmp/pypy-venv/bin/python bench.py
```

---

## 4. PyPy vs. the CPython hand-caching prototype

| | Hand-caching (`perf/hotpath-investigation`) | PyPy (this branch) |
|---|---|---|
| DRACO speedup | 1.43–1.44× | **1.78–1.85× (warm)** |
| Simpler policies | **−5–7 % (regressed)** | **+12–23 % (helped)** |
| Results | bit-identical | identical (6 sig figs) |
| Hot-path code changes | 5 commits of caching | **none** (only PEP 695 + lazy import) |
| New runtime | none | **separate PyPy 3.11 interpreter** |
| Cost | code complexity; FOCUS-only benefit | dual-venv; loses SQLite logger; JIT/import warmup; PyPy trails CPython by ~3 minor versions |

PyPy is the **larger and more uniform** win for the least code change, but it is an
*operational* change (a second interpreter, a permanent version lag, a peripheral feature
loss) rather than a code change.

---

## 5. Recommendations & open follow-ups

1. **PyPy is worth adopting for compute-heavy / multi-seed studies** (`Runner` over many
   seeds, long horizons, FOCUS/DRACO) — ~1.8× for free there. Keep CPython as the default for
   development, plotting (matplotlib/numpy via cpyext is slow), the RL `experimental` module
   (numpy), and SQLite logging.
2. **Reset the expectation: ~1.8× on the heavy policy, not 3–8×.** The earlier projection was
   the wrong reference class for this object-allocation-heavy workload. This is the measured
   number.
3. **Two follow-ups worth running next (not done here):**
   - **Does PyPy stack with the hand-caching branch?** Both target the same FOCUS hot paths.
     PyPy's JIT may *partly subsume* the hand-caching (a JIT-compiled recompute can beat a
     Python cache-with-allocation), so the combined speedup is unlikely to be the product
     1.85 × 1.44. Worth measuring: run `perf/hotpath-investigation` (plus this branch's PEP
     695 backport) on PyPy.
   - **Fix the SQLite logger for PyPy** (finalise cursors before `commit`) if PyPy is to be a
     first-class target — small, and removes the only core-feature gap.
4. **If adopted, add a PyPy lane to CI** and either backport-keep PEP 695 out of new code or
   gate it; PyPy trails CPython, so the project would permanently carry the 3.11 constraint
   on the PyPy lane.

---

## Appendix — methodology

- Interpreters: CPython 3.14.3 (project default) and PyPy 3.11.15 (`uv python install
  pypy-3.11`), macOS arm64.
- This branch is from `main`, so the CPython numbers are the **unoptimised baseline** (no
  hand-caching) — the comparison isolates *what PyPy alone buys*.
- Correctness across interpreters is checked by **aggregate statistics** (jobs completed, avg
  time-in-system, total worked time), not a bit-identical fingerprint, because CPython 3.12+
  `sum()` (Neumaier) and PyPy 3.11 `sum()` (naive) are not guaranteed bit-equal. Within one
  interpreter, runs remain fully deterministic under a fixed seed.
- Timing measures `env.run()` only (cold = run 1, warm = median of later in-process runs);
  total process wall is reported separately to capture startup + JIT + import.
