# ShopFloor extensibility

Goal: customize simulation behavior by composing a `ShopFloor` with hooks, WIP strategies, and metrics collectors.

## 1) Hooks: `on_before_operation` / `on_after_operation`

Hooks are called for each operation of each job:

- `on_before_operation`: after the server is acquired, before material delivery and processing
- `on_after_operation`: after processing (and WIP update), before the operation-completed signal is emitted

Hooks may be **plain synchronous functions** (returning `None`) or **generator-based** (yielding SimPy events). Both styles can coexist in the same hook list.

### Example: synchronous dispatch hook

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

def dispatch_hook(job, server, op_index, processing_time) -> None:
    server.sort_queue()

env = Environment()
shopfloor = ShopFloor(env=env, on_after_operation=dispatch_hook)
```

### Example: generator hook with setup time

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.typing import ProcessGenerator

def setup_hook(job, server, op_index, processing_time) -> ProcessGenerator:
    yield server.env.timeout(2.0)  # fixed setup time

env = Environment()
shopfloor = ShopFloor(env=env, on_before_operation=setup_hook)
server = Server(env=env, capacity=1, shopfloor=shopfloor)

job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
shopfloor.add(job)
env.run()

assert job.finished_at == 7.0
```

### Post-construction registration

When the hook object needs a back-reference to the shopfloor (chicken-and-egg), register after construction:

```python
shopfloor = ShopFloor(env=env)
shopfloor.on_after_operation(my_dispatcher.on_after_operation)
shopfloor.on_job_finished(my_dispatcher.on_job_finished)
```

## 2) WIP strategies

**WIP (Work-in-Progress)** is here treated as the overall workload — measured in time units — present in the shopfloor at a given moment. It is stored as `shopfloor.wip[server]` and updated when jobs enter the shopfloor and when operations complete.

Built-ins:

- `StandardWIPStrategy`: adds full processing time for each server in the routing
- `CorrectedWIPStrategy`: discounts by operation position (1/1, 1/2, 1/3, …) and adjusts remaining operations as the job progresses

### Choose a strategy at construction

```python
from simulatte.environment import Environment
from simulatte.shopfloor import CorrectedWIPStrategy, ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env, wip_strategy=CorrectedWIPStrategy())
```

### Swap a strategy later

```python
from simulatte.shopfloor import CorrectedWIPStrategy

shopfloor.set_wip_strategy(CorrectedWIPStrategy())
```

## 3) Metrics collectors

By default, `ShopFloor` uses `EMAMetricsCollector` (EMA — Exponential Moving Average) and updates it once per completed job.

### Disable metrics

```python
shopfloor = ShopFloor(env=env, metrics_collector=None)
```

### Provide a custom collector

Any object with a `record(job)` method works:

```python
class ThroughputCollector:
    def __init__(self) -> None:
        self.jobs_done = 0

    def record(self, job) -> None:
        self.jobs_done += 1

collector = ThroughputCollector()
shopfloor = ShopFloor(env=env, metrics_collector=collector)
```

### Read EMA metrics

```python
from simulatte.shopfloor import EMAMetricsCollector

collector = EMAMetricsCollector(alpha=0.05)
shopfloor = ShopFloor(env=env, metrics_collector=collector)

# ... run simulation ...
print(collector.ema_makespan, collector.ema_total_queue_time)
```

## 4) Time-series collectors and plotting

Time-series collectors capture metrics over simulation time for analysis and visualization. Unlike `MetricsCollector` (which aggregates per-job), time-series collectors record data points at each lifecycle event.

### Enable with convenience flag

```python
from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env, collect_time_series=True)
```

This creates a `DefaultTimeSeriesCollector` that tracks:

- `wip_ts`: Total WIP over time
- `job_count_ts`: Number of jobs in system over time
- `throughput_ts`: Cumulative completed jobs
- `lateness_ts`: Job lateness at completion

### Access the collector and plot

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import DefaultTimeSeriesCollector, ShopFloor

collector = DefaultTimeSeriesCollector()
env = Environment()
shopfloor = ShopFloor(env=env, time_series_collector=collector)
server = Server(env=env, capacity=1, shopfloor=shopfloor)

job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=10.0)
shopfloor.add(job)
env.run()

# Plot collected metrics (requires matplotlib)
collector.plot_wip()
collector.plot_job_count()
collector.plot_throughput()
collector.plot_lateness()
```

### Access raw data

```python
# Each time-series is a list of (time, value) tuples
for time, wip in collector.wip_ts:
    print(f"t={time}: WIP={wip}")
```

### CurrentWorkLoadCollector

`CurrentWorkLoadCollector` measures the **true remaining processing work** on the shop floor — the sum of remaining processing times across all in-system jobs — regardless of WIP strategy. Unlike `DefaultTimeSeriesCollector.wip_ts` (which reflects the active `WIPStrategy` and its position discounting), these values represent actual workload.

```python
from simulatte.environment import Environment
from simulatte.shopfloor import CurrentWorkLoadCollector, ShopFloor

collector = CurrentWorkLoadCollector()
env = Environment()
shopfloor = ShopFloor(env=env, time_series_collector=collector)
```

It records a snapshot on every job entry and every operation completion:

```python
# After simulation
for time, workload in collector.wip_ts:
    print(f"t={time}: remaining work={workload:.2f}")
```

The builder functions also accept `collect_workload=True` as a shorthand:

```python
from simulatte.builders import build_immediate_release_system

_, servers, shopfloor, router = build_immediate_release_system(
    env,
    n_servers=6,
    collect_workload=True,
)
# shopfloor.time_series_collector is now a CurrentWorkLoadCollector
```

### Provide a custom collector

Any object implementing the `TimeSeriesCollector` protocol works:

```python
class TardyTracker:
    def __init__(self) -> None:
        self.tardy_times: list[float] = []

    def on_job_entered(self, shopfloor, job) -> None:
        pass  # Called when job enters shopfloor

    def on_operation_completed(self, shopfloor, job, server, op_index) -> None:
        pass  # Called after each operation completes

    def on_job_finished(self, shopfloor, job) -> None:
        if job.lateness > 0:
            self.tardy_times.append(shopfloor.env.now)

tracker = TardyTracker()
shopfloor = ShopFloor(env=env, time_series_collector=tracker)
```

### Swap collector at runtime

```python
from simulatte.shopfloor import DefaultTimeSeriesCollector

shopfloor.set_time_series_collector(DefaultTimeSeriesCollector())
# or disable:
shopfloor.set_time_series_collector(None)
```

## 5) Job-finished callbacks

Use `on_job_finished` to run synchronous callbacks when a job completes its full routing:

```python
finished = []

def on_finished(job) -> None:
    finished.append(job)

shopfloor = ShopFloor(env=env, on_job_finished=on_finished)
```

## Next

- [Multi-run experiments](multi-run-experiments.md)
