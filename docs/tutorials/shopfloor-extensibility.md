# ShopFloor extensibility

Goal: customize simulation behavior by composing a `ShopFloor` with hooks, WIP strategies, and metrics collectors.

## Hooks: `before_operation` / `after_operation`

Hooks are called for each operation of each job:

- `before_operation`: after the server is acquired, before material delivery and processing
- `after_operation`: after processing (and WIP update), before the operation-completed signal is emitted

Hooks are **generator-based** (they can `yield` SimPy events).

### Example: add setup time before processing

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.typing import ProcessGenerator

def setup_hook(job, server, op_index, processing_time) -> ProcessGenerator:
    yield server.env.timeout(2.0)  # fixed setup time

env = Environment()
shopfloor = ShopFloor(env=env, before_operation=setup_hook)
server = Server(env=env, capacity=1, shopfloor=shopfloor)

job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
shopfloor.add(job)
env.run()

assert job.finished_at == 7.0
```

## WIP strategies

WIP is stored as `shopfloor.wip[server]` and updated when jobs enter the shopfloor and when operations complete.

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

## Metrics collectors

By default, `ShopFloor` uses `EMAMetricsCollector` and updates it once per completed job.

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

## Time-series collectors and plotting

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

## Job-finished callbacks

Use `on_job_finished` to run synchronous callbacks when a job completes its full routing:

```python
finished = []

def on_finished(job) -> None:
    finished.append(job)

shopfloor = ShopFloor(env=env, on_job_finished=on_finished)
```
