# Installation

Simulatte requires **Python 3.11 or later** (CI tests CPython 3.11–3.15 and PyPy 3.11).

Install from PyPI with pip:

```bash
pip install simulatte
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add simulatte
```

The base install includes production and intralogistics simulation, collectors, traces, and `Runner`.
Plotting and reinforcement learning are optional:

```bash
pip install "simulatte[plot]"  # Collector plot_* methods and plotting examples
pip install "simulatte[rl]"    # Experimental SimulatteEnv Gymnasium wrapper
pip install "simulatte[all]"   # Both integrations
```

With uv, use `uv add "simulatte[plot]"`, `uv add "simulatte[rl]"`, or `uv add "simulatte[all]"`.
Starting in 0.13, existing applications that plot or use `SimulatteEnv` must select the corresponding extra.
Collecting time series and KPIs does not require plotting dependencies.

Once installed, continue with [Basic Usage](basic-usage.md) for your first simulation.
