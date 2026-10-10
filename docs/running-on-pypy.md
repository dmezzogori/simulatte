# Running on PyPy

The supported built-in simulation engine, release/dispatching policies, intralogistics modules, text/JSON/SQLite logging and trace recording have been exercised on PyPy 3.11 with seeded outputs matching CPython for the tested workloads.

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

Your simulation scripts are unchanged — only the interpreter differs. The base install excludes NumPy,
Matplotlib, and Gymnasium. For plots, install `simulatte[plot]`; for the experimental Gymnasium wrapper,
install `simulatte[rl]` (or `simulatte[all]` for both).

## Supported features on PyPy

| Capability | PyPy 3.11 |
|---|---|
| Simulation engine + all release/dispatching policies | ✅ fully supported |
| Intralogistics (AGV fleet, warehouse, graph/pathfinding) | ✅ fully supported |
| Text / JSON logging | ✅ supported |
| Trace recording and reading (`TraceRecorder`, `Trace`) | ✅ supported |
| SQLite logging (`Environment(log_db_path=…)`) | ✅ supported |
| Plotting (collector `plot_*`, …) | ⚠️ works, but matplotlib/numpy run through PyPy's slower `cpyext` C-extension bridge |
| `simulatte.experimental` (Gymnasium RL wrapper) | ⚠️ best-effort — depends on numpy/gymnasium via `cpyext`; the module is unstable regardless |

Notes:

- The pure-Python dependencies (`simpy`, `tqdm`, `tabulate`) are first-class on PyPy.
- `matplotlib` and `numpy` are only needed for **plotting** and the experimental RL module —
  they are never on the simulation hot path. Headless simulations that do not use the RL wrapper
  need neither extra. The PyPy CI lane runs the core and intralogistics suites without either extra;
  dependency-related skips are limited to NumPy-specific checks and the optional RL export check. Plotting and RL tests
  run on CPython with all extras installed.

## Determinism across interpreters

Within one interpreter, runs are fully deterministic under a fixed `Environment(seed=...)`. Across
CPython and PyPy, the standard-library `random.Random` streams are **byte-identical**, so seeded
simulations evolve the same way.

Simulatte sums floating-point values that feed events, dispatching priorities, due dates or KPIs with
`math.fsum`, which is exactly rounded on every interpreter. (The builtin `sum()` is compensated on
CPython 3.12+ and plain on CPython 3.11 and PyPy, so it can differ in the last bit under cancellation.) The
reference workloads therefore give the same semantic digest on CPython and PyPy. The guarantee covers the
library's own sums and the tested workloads: a custom model that sums signed terms of very different
magnitudes with `sum()` on the decision path can still diverge, and results that go through the platform's
`libm` (`log`, `exp`) may differ in the last bit between platforms. The run manifest records the interpreter,
so a digest mismatch can be explained. If you need cross-interpreter bit-reproducibility, validate your own
model.

## Performance

PyPy's JIT can improve throughput on long, compute-heavy runs (it pays off after warmup, so
short one-shot simulations may see little benefit or a small startup cost). The gain is
workload-dependent — benchmark your own model. Keep CPython as your default for development,
plotting, and the experimental RL module.
