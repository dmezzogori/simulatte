# ShopFloor extensibility

Goal: customize simulation behavior by composing a `ShopFloor` with hooks, WIP strategies, and collectors.

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

## 3) Metrics: the default EMA collector

Metrics come from **collectors** that listen to the events of the simulation (`simulatte.collectors`). Every `ShopFloor` attaches an `EMACollector` (EMA: exponential moving average) as `shopfloor.metrics`; it updates at each completed job. Its smoothing factor is `ema_alpha` (default 0.01):

```python
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env, ema_alpha=0.05)
server = Server(env=env, capacity=1, shopfloor=shopfloor)
shopfloor.add(ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=10.0))
env.run()

metrics = shopfloor.metrics
print(metrics.ema_makespan, metrics.ema_total_queue_time)
```

It exposes `ema_makespan`, `ema_tardy_jobs`, `ema_early_jobs`, `ema_in_window_jobs`, `ema_time_in_psp`, `ema_time_in_shopfloor` and `ema_total_queue_time`.

The EMAs split jobs by the due-date window (±7 time units): `ema_tardy_jobs` counts late jobs *outside* the window and `ema_in_window_jobs` those inside it, so a job that finishes a little late is not "tardy" here. `ShopFloorKPIs` (below) defines tardy as any positive lateness.

### Disable the default metrics

```python
shopfloor = ShopFloor(env=env, default_metrics=False)  # shopfloor.metrics is None
```

### Window-aware KPIs

The EMAs include every completed job, warm-up included. `ShopFloorKPIs` computes results over the observation window, which starts at the warm-up set with `env.configure_kpis(warmup=...)`: job means (`makespan`, `lateness`, `tardiness`, `tardy_fraction`, `total_queue_time`) over the jobs completed in the window, `throughput`, and the time-weighted `utilization` of the servers and `jobs_in_system`, clipped at the window boundaries. The results are keyed `"<shop floor id>/<kpi>"` and also appear in `env.fingerprint().kpis`:

```python
from simulatte.collectors import ShopFloorKPIs

env = Environment()
env.configure_kpis(warmup=100.0)
shopfloor = ShopFloor(env=env)
kpis = ShopFloorKPIs(shopfloor).attach(env)
# ... add servers and jobs, run ...
print(kpis.scalars())  # {"shopfloor-0/makespan": ..., "shopfloor-0/utilization": ..., ...}
```

## 4) Time-series collectors and plotting

Time-series collectors record `(time, value)` points over simulation time for analysis and plots. Attach them to their owner with `attach(env)` before the run; `env.collectors` lists every attached collector.

### Shop-floor series

```python
from simulatte.collectors import ShopFloorTimeSeries
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)
server = Server(env=env, capacity=1, shopfloor=shopfloor)
series = ShopFloorTimeSeries(shopfloor).attach(env)

job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=10.0)
shopfloor.add(job)
env.run()

# Plot collected metrics (requires matplotlib)
series.plot_wip()
series.plot_job_count()
series.plot_throughput()
series.plot_lateness()
```

`ShopFloorTimeSeries` tracks:

- `wip_ts`: Total WIP over time
- `job_count_ts`: Number of jobs in system over time
- `throughput_ts`: Cumulative completed jobs
- `lateness_ts`: Job lateness at completion

### Access raw data

```python
# Each time-series is a list of (time, value) tuples
for time, wip in series.wip_ts:
    print(f"t={time}: WIP={wip}")
```

### Server series

`ServerTimeSeries` records the queue length (`qt`) and the utilization (`ut`) of one server, with `plot_qt()` and `plot_ut()`:

```python
from simulatte.collectors import ServerTimeSeries

queue = ServerTimeSeries(server).attach(env)
# ... run ...
queue.plot_qt()
```

### CurrentWorkloadCollector

`CurrentWorkloadCollector` measures the **true remaining processing work** on the shop floor — the sum of the processing times of the operations not yet completed by the jobs on it — regardless of WIP strategy. Unlike `ShopFloorTimeSeries.wip_ts` (which reflects the active `WIPStrategy` and its position discounting), these values represent actual workload. Work leaves the total when its operation completes, even if an after-operation hook still holds the server.

```python
from simulatte.collectors import CurrentWorkloadCollector
from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)
workload = CurrentWorkloadCollector(shopfloor).attach(env)
```

It records a point on every job entry and every operation completion:

```python
# After simulation
for time, remaining in workload.wip_ts:
    print(f"t={time}: remaining work={remaining:.2f}")
```

The builder functions also accept `collect_workload=True` as a shorthand (and `build_immediate_release_system` accepts `collect_time_series=True`, which attaches a `ServerTimeSeries` to each server):

```python
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import CurrentWorkloadCollector

_, servers, shopfloor, router, _ = build_immediate_release_system(
    env=env,
    collect_workload=True,
)
workload = next(c for c in env.collectors if isinstance(c, CurrentWorkloadCollector))
```

### Write your own collector

A collector subclasses `simulatte.kpi.Collector`, names the event classes it listens to in `subscribes`, and handles them in `on_event`. With `scope_field = "shopfloor"` it only receives the events of its own shop floor. Collectors observe: they read objects without changing them and never schedule SimPy events or draw random numbers.

```python
from typing import ClassVar

from simulatte.kpi import Collector
from simulatte.shopfloor import JobFinished


class TardyTracker(Collector):
    subscribes: ClassVar = (JobFinished,)
    scope_field: ClassVar = "shopfloor"

    def __init__(self, shopfloor) -> None:
        super().__init__(shopfloor)
        self.tardy_times: list[float] = []

    def on_event(self, event) -> None:
        if event.lateness > 0:
            self.tardy_times.append(event.t)


tracker = TardyTracker(shopfloor).attach(env)
```

The events of a job's life on the shop floor are `ShopFloorEntered`, `OperationStarted`, `OperationCompleted`, `ShopFloorWipUpdated` and `JobFinished` (in `simulatte.shopfloor`); the server events are `JobQueued`, `JobGranted`, `JobQueueLeft`, `JobReleased`, `ServerQueueReordered` and `ServerWorkCredited` (in `simulatte.server`).

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
