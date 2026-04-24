# Experiment Runner Design

**Date:** 2026-04-24  
**Branch:** fix/slar  

## Goal

Add SPT dispatching support to the immediate-release builder, implement a result extractor, and create a CLI-driven experiment runner for comparing four release policies across 30 seeds.

---

## 1. Builder Change (`src/simulatte/builders.py`)

Add one optional parameter to `build_immediate_release_system`:

```python
priority_policies: Callable[[ProductionJob, Server], float] | None = None
```

Forwarded directly to `Router`. Default `None` = FIFO (all priorities 0).

Add a module-level constant for SPT:

```python
spt_priority_policy = lambda job, server: job.routing[server]
```

Lower value = shorter processing time = served first (correct for SimPy's `PriorityResource`). Callers use it as:

```python
build_immediate_release_system(env, priority_policies=spt_priority_policy)
```

No new builder function is introduced.

---

## 2. `experiments/extractors.py`

Single public function:

```python
def extract_results(warmup: SimTime, system: System) -> dict[str, float]
```

- Unpacks `(_, servers, shopfloor, _) = system`
- Filters `shopfloor.jobs_done` to jobs where `finished_at > warmup`
- Returns:

| key | computation |
|---|---|
| `completed_jobs` | `len(jobs_done)` |
| `avg_time_in_shopfloor` | mean of `job.time_in_shopfloor` |
| `avg_time_in_psp` | mean of `job.time_in_psp if job.psp_exit_at is not None else 0.0` |
| `avg_queue_time` | mean of `job.total_queue_time` |
| `pct_tardy` | `sum(j.lateness > 0) / n * 100` |
| `avg_lateness` | mean of `job.lateness` |
| `avg_utilization` | mean of `server.utilization_rate` across all servers |

For push systems (immediate release) `avg_time_in_psp` is always 0.0 — semantically correct.

Used in `run_experiment.py` via partial application:

```python
functools.partial(extract_results, warmup=cfg["simulation"]["warmup"])
```

---

## 3. `experiments/experiment_config.yaml`

```yaml
simulation:
  n_servers: 6
  arrival_rate: 1.5432098765432098
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

Seeds are pre-generated (`random.seed(42); random.sample(range(2**31), 30)`) and stored explicitly for full reproducibility.

---

## 4. `experiments/run_experiment.py`

### CLI

```bash
uv run python experiments/run_experiment.py <policy_name>
# e.g.:
uv run python experiments/run_experiment.py immediate_fifo
uv run python experiments/run_experiment.py slar
uv run python experiments/run_experiment.py lumscor
```

### Policy Registry

```python
POLICY_REGISTRY: dict[str, Callable[[dict], list[tuple[dict, Runner]]]]
```

Each entry is a factory taking the simulation config dict and returning a list of `(param_columns, runner)` pairs — one per parameter combination. `itertools.product` generates all combinations for multi-param policies.

| policy | combinations | runs |
|---|---|---|
| `immediate_fifo` | 1 | 30 |
| `immediate_spt` | 1 | 30 |
| `slar` | 5 (allowance_factor) | 150 |
| `lumscor` | 35 (wl_norm_level × allowance_factor) | 1050 |

### Execution Flow

1. Load `experiment_config.yaml` with `pyyaml`
2. Look up policy name in registry → list of `(params, runner)` pairs
3. For each pair: `runner.run(until=run_until)` → list of result dicts
4. Zip seeds + params + results → CSV rows
5. Write to `results/{policy_name}.csv` (create folder if needed)

### CSV Schema

All CSVs share the same result columns. Policy-param columns vary:

- `immediate_fifo` / `immediate_spt`: `seed, completed_jobs, avg_time_in_shopfloor, avg_time_in_psp, avg_queue_time, pct_tardy, avg_lateness, avg_utilization`
- `slar`: prepends `allowance_factor`
- `lumscor`: prepends `wl_norm_level, allowance_factor, check_timeout`

---

## 5. File Layout

```
experiments/
  experiment_config.yaml
  extractors.py
  run_experiment.py
results/                  # created at runtime
  immediate_fifo.csv
  immediate_spt.csv
  slar.csv
  lumscor.csv
```

`pyyaml` added to `[dependency-groups] dev` in `pyproject.toml`.
