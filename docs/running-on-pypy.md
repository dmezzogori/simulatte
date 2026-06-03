# Running on PyPy

simulatte is compatible with **[PyPy](https://pypy.org/) 3.11**, the tracing-JIT Python
implementation. The simulation engine, every release/dispatching policy, the intralogistics
modules, and text/JSON/SQLite logging all run on PyPy and produce the **same results** as
CPython for a given seed.

You can use PyPy when it helps your workload — typically long, compute-heavy simulations and
large multi-run studies — without changing any of your code.

## Why 3.11?

PyPy's latest release implements the **Python 3.11** language. simulatte therefore keeps its
source within the 3.11 language (`requires-python = ">=3.11"`) so it runs on PyPy as well as on
CPython 3.11+. When PyPy ships a 3.12 release, this floor can rise.

## Installation (with uv)

[`uv`](https://docs.astral.sh/uv/) can download and manage a PyPy interpreter for you:

```bash
# 1. Install a PyPy 3.11 interpreter
uv python install pypy-3.11

# 2. Create a PyPy virtual environment
uv venv --python pypy-3.11 .venv-pypy

# 3. Install simulatte into it
uv pip install --python .venv-pypy simulatte

# 4. Run your simulation on PyPy
.venv-pypy/bin/python my_simulation.py
```

Your simulation scripts are unchanged — only the interpreter differs.

## Supported features on PyPy

| Capability | PyPy 3.11 |
|---|---|
| Simulation engine + all release/dispatching policies | ✅ fully supported |
| Intralogistics (AGV fleet, warehouse, graph/pathfinding) | ✅ fully supported |
| Text / JSON logging | ✅ supported |
| SQLite logging (`Environment(log_db_path=…)`) | ✅ supported |
| Plotting (`Server.plot_qt`, collector `plot_*`, …) | ⚠️ works, but matplotlib/numpy run through PyPy's slower `cpyext` C-extension bridge |
| `simulatte.experimental` (Gymnasium RL wrapper) | ⚠️ best-effort — depends on numpy/gymnasium via `cpyext`; the module is unstable regardless |

Notes:

- The pure-Python dependencies (`simpy`, `loguru`, `tqdm`, `tabulate`) are first-class on PyPy.
- `matplotlib` and `numpy` are only needed for **plotting** and the experimental RL module —
  they are never on the simulation hot path. If you run headless (no plots), you don't need
  them at all.

## Determinism across interpreters

Within one interpreter, runs are fully deterministic under a fixed `random.seed`. Across
CPython and PyPy, the standard-library random streams are **byte-identical**, so seeded
simulations evolve the same way.

One subtlety: CPython 3.12+ uses compensated (Neumaier) floating-point summation while PyPy
3.11 uses naive summation, so `sum()` over floats *can* differ in the last bit under
catastrophic cancellation. simulatte's decision-path sums are well-conditioned
(similar-magnitude positives), so this does not change results in practice — but a custom model
that sums signed terms of very different magnitudes on the decision path could diverge. If you
need cross-interpreter bit-reproducibility, validate your own model.

## Performance

PyPy's JIT can improve throughput on long, compute-heavy runs (it pays off after warmup, so
short one-shot simulations may see little benefit or a small startup cost). The gain is
workload-dependent — benchmark your own model. Keep CPython as your default for development,
plotting, and the experimental RL module.
