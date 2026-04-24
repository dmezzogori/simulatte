# Experiment Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SPT dispatching to the immediate-release builder, implement a result extractor, and build a CLI-driven experiment runner comparing four release policies across 30 seeds.

**Architecture:** Extend `build_immediate_release_system` with optional `priority_policies` (enabling SPT as a pass-in); implement `experiments/extractors.py` with a single `extract_results(warmup, system)` function applied via `functools.partial`; wire everything through a policy registry in `experiments/run_experiment.py` that generates all parameter combinations with `itertools.product` and writes per-policy CSV files.

**Tech Stack:** Python 3.12, SimPy, pyyaml (new dev dep), stdlib `csv`, `argparse`, `itertools`, `functools`

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Modify | `src/simulatte/builders.py` | Add `spt_priority_policy` fn + `priority_policies`, `collect_time_series`, `retain_job_history` params to `build_immediate_release_system` |
| Modify | `tests/core/test_builders.py` | Add tests for new builder params |
| Modify | `pyproject.toml` | Add `pyyaml` to dev deps |
| Create | `experiments/extractors.py` | `extract_results(warmup, system) -> dict` |
| Create | `experiments/experiment_config.yaml` | Single source of truth for all experiment parameters |
| Create | `experiments/run_experiment.py` | Policy registry + CLI + CSV output |

---

## Task 1: Fix and extend `build_immediate_release_system`

**Files:**
- Modify: `src/simulatte/builders.py`
- Modify: `tests/core/test_builders.py`

The builder is missing `collect_time_series`, `retain_job_history` (documented in docstring, tested, but never wired) and the new `priority_policies` parameter. Also add a module-level `spt_priority_policy` function (not a lambda — named functions are picklable by multiprocessing).

- [ ] **Step 1: Run existing builder tests to see the baseline failure**

```bash
uv run pytest tests/core/test_builders.py -v
```

Expected: `test_build_immediate_release_system_with_options` FAILS with `TypeError: build_immediate_release_system() got an unexpected keyword argument 'collect_time_series'`. The other 3 pass.

- [ ] **Step 2: Write the new failing tests**

Add to `tests/core/test_builders.py` inside `TestBuildImmediateReleaseSystem`:

```python
from simulatte.builders import spt_priority_policy

def test_spt_priority_policy_returns_processing_time(self) -> None:
    from unittest.mock import MagicMock
    server = MagicMock()
    job = MagicMock()
    job.routing = {server: 2.5}
    assert spt_priority_policy(job, server) == 2.5

def test_build_immediate_release_system_with_spt(self) -> None:
    env = Environment()
    _, _, _, router = build_immediate_release_system(env, n_servers=3, priority_policies=spt_priority_policy)
    assert router.priority_policies is spt_priority_policy
```

- [ ] **Step 3: Run to confirm new tests fail**

```bash
uv run pytest tests/core/test_builders.py::TestBuildImmediateReleaseSystem::test_spt_priority_policy_returns_processing_time tests/core/test_builders.py::TestBuildImmediateReleaseSystem::test_build_immediate_release_system_with_spt -v
```

Expected: both FAIL with `ImportError` or `TypeError`.

- [ ] **Step 4: Implement the changes in `src/simulatte/builders.py`**

Add the `spt_priority_policy` function right before `build_immediate_release_system` (after imports):

```python
def spt_priority_policy(job: ProductionJob, server: Server) -> float:
    """Shortest Processing Time dispatching: priority = processing time at server."""
    return job.routing[server]
```

Add to the imports at the top of `builders.py`:

```python
from simulatte.job import ProductionJob
```

Update `build_immediate_release_system` signature and body:

```python
def build_immediate_release_system(
    env: Environment,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
    priority_policies: Callable[[ProductionJob, Server], float] | None = None,
) -> PushSystem:
```

Update the `servers` line in the body:

```python
servers = tuple(
    Server(env=env, capacity=1, shopfloor=shop_floor,
           collect_time_series=collect_time_series,
           retain_job_history=retain_job_history)
    for _ in range(n_servers)
)
```

Pass `priority_policies` to the `Router`:

```python
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
    due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},
    priority_policies=priority_policies,
)
```

Also add `Callable` to the `TYPE_CHECKING` import block if not already present:

```python
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable
    from simulatte.typing import PullSystem, PushSystem
```

- [ ] **Step 5: Run all builder tests**

```bash
uv run pytest tests/core/test_builders.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_builders.py
git commit -m "feat: add priority_policies, collect_time_series and retain_job_history to build_immediate_release_system, add spt_priority_policy"
```

---

## Task 2: Add `pyyaml` dev dependency and create `experiments/` folder

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `pyyaml` to dev deps in `pyproject.toml`**

In the `[dependency-groups]` section, update `dev` to include `pyyaml`:

```toml
[dependency-groups]
dev = [
    "pre-commit>=3.3.3",
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "pyyaml>=6.0",
    "ruff>=0.1.5",
    "ty>=0.0.1a32",
    "zensical>=0.0.14",
]
```

- [ ] **Step 2: Sync dependencies**

```bash
uv sync --dev
```

Expected: pyyaml installs without errors.

- [ ] **Step 3: Create the `experiments/` directory**

```bash
mkdir -p experiments
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pyyaml dev dependency for experiment config"
```

---

## Task 3: Create `experiments/extractors.py`

**Files:**
- Create: `experiments/extractors.py`

> Coverage note: `experiments/` is outside `src/simulatte/` so it is not tracked by the test suite coverage threshold.

- [ ] **Step 1: Create `experiments/extractors.py`**

```python
"""Result extraction functions for post-simulation analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpy.core import SimTime
    from simulatte.typing import System


def extract_results(warmup: SimTime, system: System) -> dict[str, float]:
    """Extract performance metrics from a completed simulation.

    Filters out jobs that finished during the warmup period to report
    steady-state statistics only.

    Args:
        warmup: Discard jobs with finished_at <= this time.
        system: Tuple of (psp_or_none, servers, shopfloor, router).

    Returns:
        Dict with keys: completed_jobs, avg_time_in_shopfloor, avg_time_in_psp,
        avg_queue_time, pct_tardy, avg_lateness, avg_utilization.
    """
    _, servers, shopfloor, _ = system
    jobs_done = [j for j in shopfloor.jobs_done if j.finished_at is not None and j.finished_at > warmup]
    n = len(jobs_done)

    if n == 0:
        return {
            "completed_jobs": 0,
            "avg_time_in_shopfloor": float("nan"),
            "avg_time_in_psp": float("nan"),
            "avg_queue_time": float("nan"),
            "pct_tardy": float("nan"),
            "avg_lateness": float("nan"),
            "avg_utilization": float("nan"),
        }

    avg_time_in_shopfloor = sum(j.time_in_shopfloor for j in jobs_done) / n
    avg_time_in_psp = sum(j.time_in_psp if j.psp_exit_at is not None else 0.0 for j in jobs_done) / n
    avg_queue_time = sum(j.total_queue_time for j in jobs_done) / n
    pct_tardy = sum(1 for j in jobs_done if j.lateness > 0) / n * 100
    avg_lateness = sum(j.lateness for j in jobs_done) / n
    avg_utilization = sum(s.utilization_rate for s in servers) / len(servers)

    return {
        "completed_jobs": n,
        "avg_time_in_shopfloor": avg_time_in_shopfloor,
        "avg_time_in_psp": avg_time_in_psp,
        "avg_queue_time": avg_queue_time,
        "pct_tardy": pct_tardy,
        "avg_lateness": avg_lateness,
        "avg_utilization": avg_utilization,
    }
```

- [ ] **Step 2: Commit**

```bash
git add experiments/extractors.py
git commit -m "feat: add extract_results function in experiments/extractors.py"
```

---

## Task 4: Create `experiments/experiment_config.yaml`

**Files:**
- Create: `experiments/experiment_config.yaml`

- [ ] **Step 1: Create `experiments/experiment_config.yaml`**

```yaml
simulation:
  n_servers: 6
  arrival_rate: 1.5432098765432098  # 1 / 0.648
  service_rate: 2.0
  due_date_low: 30
  due_date_high: 45
  run_until: 20000
  warmup: 5000
  seeds: [478163327, 107420369, 1181241943, 1051802512, 958682846, 599310825, 440213415,
          373399426, 1812140441, 136505587, 127978094, 402418010, 939042955, 999270936,
          113971123, 854001193, 1801823908, 946785248, 1929338154, 1194819984, 27911967,
          685731524, 1815115025, 1461364854, 1193448329, 667779376, 924765563, 1445662585,
          438989805, 398340369]

policies:
  immediate_fifo: {}
  immediate_spt: {}
  slar:
    allowance_factor: [3, 4, 5, 6, 7]
  lumscor:
    wl_norm_level: [4, 5, 6, 7, 8, 9, 10]
    allowance_factor: [3, 4, 5, 6, 7]
    check_timeout: 4
```

- [ ] **Step 2: Commit**

```bash
git add experiments/experiment_config.yaml
git commit -m "feat: add experiment_config.yaml with simulation parameters and policy sweeps"
```

---

## Task 5: Create `experiments/run_experiment.py`

**Files:**
- Create: `experiments/run_experiment.py`

This script is the CLI entry point. The policy registry maps each policy name to a factory function that takes the full config dict and returns a list of `(param_columns: dict, runner: Runner)` pairs — one per parameter combination.

- [ ] **Step 1: Create `experiments/run_experiment.py`**

```python
"""CLI for running jobshop policy experiments."""

from __future__ import annotations

import argparse
import csv
import sys
from functools import partial
from itertools import product
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    spt_priority_policy,
)
from simulatte.runner import Runner

from extractors import extract_results


def _immediate_fifo_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    extract = partial(extract_results, warmup=sim["warmup"])
    builder = partial(
        build_immediate_release_system,
        n_servers=sim["n_servers"],
        arrival_rate=sim["arrival_rate"],
        service_rate=sim["service_rate"],
    )
    return [({}, Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract))]


def _immediate_spt_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    extract = partial(extract_results, warmup=sim["warmup"])
    builder = partial(
        build_immediate_release_system,
        n_servers=sim["n_servers"],
        arrival_rate=sim["arrival_rate"],
        service_rate=sim["service_rate"],
        priority_policies=spt_priority_policy,
    )
    return [({}, Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract))]


def _slar_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    pol = cfg["policies"]["slar"]
    extract = partial(extract_results, warmup=sim["warmup"])
    runs = []
    for af in pol["allowance_factor"]:
        builder = partial(
            build_slar_system,
            allowance_factor=af,
            n_servers=sim["n_servers"],
            arrival_rate=sim["arrival_rate"],
            service_rate=sim["service_rate"],
        )
        runner = Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract)
        runs.append(({"allowance_factor": af}, runner))
    return runs


def _lumscor_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    pol = cfg["policies"]["lumscor"]
    extract = partial(extract_results, warmup=sim["warmup"])
    runs = []
    for wl, af in product(pol["wl_norm_level"], pol["allowance_factor"]):
        builder = partial(
            build_lumscor_system,
            wl_norm_level=wl,
            allowance_factor=af,
            check_timeout=pol["check_timeout"],
            n_servers=sim["n_servers"],
            arrival_rate=sim["arrival_rate"],
            service_rate=sim["service_rate"],
        )
        runner = Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract)
        runs.append(({"wl_norm_level": wl, "allowance_factor": af, "check_timeout": pol["check_timeout"]}, runner))
    return runs


POLICY_REGISTRY: dict[str, object] = {
    "immediate_fifo": _immediate_fifo_runs,
    "immediate_spt": _immediate_spt_runs,
    "slar": _slar_runs,
    "lumscor": _lumscor_runs,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a jobshop policy experiment.")
    parser.add_argument("policy", choices=list(POLICY_REGISTRY), help="Policy to run")
    args = parser.parse_args()

    config_path = Path(__file__).parent / "experiment_config.yaml"
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    sim = cfg["simulation"]
    runs = POLICY_REGISTRY[args.policy](cfg)

    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    csv_path = results_dir / f"{args.policy}.csv"

    all_rows = []
    for param_cols, runner in runs:
        results = runner.run(until=sim["run_until"])
        for seed, result in zip(sim["seeds"], results, strict=True):
            all_rows.append({"seed": seed, **param_cols, **result})

    if all_rows:
        fieldnames = list(all_rows[0].keys())
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"Saved {len(all_rows)} rows to {csv_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the CLI is importable**

```bash
cd experiments && uv run python run_experiment.py --help
```

Expected output:
```
usage: run_experiment.py [-h] {immediate_fifo,immediate_spt,slar,lumscor}
...
```

- [ ] **Step 3: Run a quick smoke test with immediate_fifo**

```bash
uv run python experiments/run_experiment.py immediate_fifo
```

Expected: progress bar runs 30 seeds, prints `Saved 30 rows to results/immediate_fifo.csv`. Verify the CSV has the right columns:

```bash
head -2 results/immediate_fifo.csv
```

Expected first line: `seed,completed_jobs,avg_time_in_shopfloor,avg_time_in_psp,avg_queue_time,pct_tardy,avg_lateness,avg_utilization`

- [ ] **Step 4: Commit**

```bash
git add experiments/run_experiment.py
git commit -m "feat: add run_experiment.py with policy registry and CLI"
```

---

## Task 6: Run full test suite and verify coverage

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest
```

Expected: all tests PASS, coverage ≥ 99%.

- [ ] **Step 2: Run linter**

```bash
uv run ruff check src tests
```

Expected: no errors.

- [ ] **Step 3: If coverage drops below 99%**

Check which lines in `src/simulatte/builders.py` are uncovered. The new `priority_policies` param branch (the `None` vs non-`None` path in `Router`) should already be covered by the existing router tests. If any new branch in the builder is uncovered, add a minimal targeted test.

---

## Self-Review

**Spec coverage check:**
- ✅ `spt_priority_policy` + `priority_policies` param → Task 1
- ✅ Fix existing broken test (`collect_time_series`/`retain_job_history`) → Task 1
- ✅ `pyyaml` dev dep → Task 2
- ✅ `experiments/extractors.py` with `extract_results(warmup, system)` → Task 3
- ✅ Warmup filtering (`finished_at > warmup`) → Task 3
- ✅ All 7 metrics (completed_jobs, avg_time_in_shopfloor, avg_time_in_psp, avg_queue_time, pct_tardy, avg_lateness, avg_utilization) → Task 3
- ✅ Push systems handle missing PSP (`psp_exit_at is None → 0.0`) → Task 3
- ✅ `experiment_config.yaml` with 30 random seeds → Task 4
- ✅ Policy registry with `itertools.product` for combinations → Task 5
- ✅ `argparse` CLI with policy name → Task 5
- ✅ CSV per policy in `results/` → Task 5
- ✅ `functools.partial` for warmup binding → Task 5

**Type consistency check:**
- `extract_results(warmup, system)` defined in Task 3, called via `partial(extract_results, warmup=...)` in Task 5 ✅
- `spt_priority_policy` defined in Task 1, imported in Task 5 ✅
- `POLICY_REGISTRY` values are `Callable[[dict], list[tuple[dict, Runner]]]`, called as `POLICY_REGISTRY[args.policy](cfg)` ✅
- `Runner(builder=..., seeds=..., extract_fn=...)` matches `runner.py` signature ✅

**No placeholders found.** All steps contain complete code.
