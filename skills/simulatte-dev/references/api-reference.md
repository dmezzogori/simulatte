# Simulatte API Reference

Exact signatures and configuration patterns for the Simulatte core library.
Read this file when writing Simulatte code to get constructor parameters,
types, and return values right.

## Environment

```python
from simulatte.environment import Environment

Environment(
    *,
    log_file: str | Path | None = None,        # Log output path (default: stderr)
    log_format: Literal["text", "json"] = "text",
    log_history_size: int = 1000,               # In-memory ring buffer capacity
    log_db_path: str | Path | None = None,      # SQLite path for persistent logs
)
```

Extends `simpy.Environment`. Supports context manager (`with Environment() as env:`).

**Logging methods:** `env.debug(msg, component=..., **extra)`, `.info()`,
`.warning()`, `.error()`.

**Log querying:**
```python
env.log_history.query(level="ERROR", component="Server", since=100.0, until=500.0)
```

**Component filtering:**
```python
env.logger.disable_component("Server")
env.logger.enable_component("ShopFloor")
```

## ShopFloor

```python
from simulatte.shopfloor import ShopFloor

ShopFloor(
    *,
    env: Environment,
    ema_alpha: float = 0.01,                    # EMA smoothing (0, 1]
    material_coordinator=None,                  # Experimental, skip
    wip_strategy: WIPStrategy | None = None,    # Default: StandardWIPStrategy
    metrics_collector=_DEFAULT,                  # Default: EMAMetricsCollector
    collect_time_series: bool = False,           # Auto-create DefaultTimeSeriesCollector
    time_series_collector: TimeSeriesCollector | None = None,
    before_operation: OperationHook | list[OperationHook] | None = None,
    after_operation: OperationHook | list[OperationHook] | None = None,
    on_job_finished: Callable | list[Callable] | None = None,
)
```

**Key methods:**
- `shopfloor.add(job)` — release a job onto the shopfloor
- `shopfloor.set_wip_strategy(strategy)` — replace WIP strategy at runtime
- `shopfloor.set_metrics_collector(collector)` — replace or disable (None) metrics

**Key attributes:**
- `shopfloor.jobs: set[ProductionJob]` — currently active jobs
- `shopfloor.jobs_done: list[ProductionJob]` — completed jobs (in order)
- `shopfloor.wip: dict[Server, float]` — current WIP per server
- `shopfloor.average_time_in_system: float`
- `shopfloor.total_time_in_system: float`
- `shopfloor.maximum_wip_value: float`
- `shopfloor.maximum_shopfloor_jobs: int`

**Events (for custom triggers):**
- `shopfloor.job_processing_end` — fires when any job finishes an operation
- `shopfloor.job_finished_event` — fires when any job completes its routing

Both are one-shot events that get recreated after each trigger. Yield them in
a `while True` loop to react continuously.

## Server

```python
from simulatte.server import Server

Server(
    *,
    env: Environment,
    capacity: int,                              # Concurrent job slots
    shopfloor: ShopFloor | None = None,         # Auto-registers if provided
    collect_time_series: bool = False,           # Queue length & utilization plots
    retain_job_history: bool = False,            # Keep list of processed jobs
)
```

Extends `simpy.PriorityResource`.

**Key attributes:**
- `server.utilization_rate: float` — fraction of time busy (0 to 1)
- `server.average_queue_length: float` — time-weighted average
- `server.idle_time: float`
- `server.worked_time: float`
- `server.empty: bool` — whether queue is empty
- `server.queue` — SimPy queue of waiting requests
- `server.queueing_jobs` — iterator over jobs in queue

**Methods:**
- `server.sort_queue()` — re-sort queue by priority keys
- `server.plot_qt()` — queue length over time (requires `collect_time_series=True`)
- `server.plot_ut()` — utilization over time (requires `collect_time_series=True`)

## ProductionJob

```python
from simulatte.job import ProductionJob

ProductionJob(
    *,
    env: Environment,
    sku: str,                                   # Product identifier
    servers: Sequence[Server],                  # Routing sequence
    processing_times: Sequence[float],          # Must match len(servers)
    due_date: SimTime,                          # ABSOLUTE sim time, not offset
    priority_policy: Callable[[job, Server], float] | None = None,
    material_requirements: dict[int, dict[str, int]] | None = None,  # Experimental
)
```

**Timing attributes (set automatically during simulation):**
- `job.created_at: float`
- `job.psp_exit_at: float | None`
- `job.servers_entry_at: dict[Server, float | None]`
- `job.servers_exit_at: dict[Server, float | None]`
- `job.finished_at: float | None`
- `job.done: bool`

**Computed properties:**
- `job.makespan: float` — elapsed since creation (or total if done)
- `job.lateness: float` — `finished_at - due_date` (only when done)
- `job.late: bool` — finished after due date
- `job.time_in_system: float` — first server entry to last exit
- `job.time_in_shopfloor: float` — alias for time_in_system
- `job.time_in_psp: float` — time in Pre-Shop Pool
- `job.total_queue_time: float` — sum of queue waits (only when done)
- `job.slack_time: float` — `due_date - env.now`
- `job.planned_slack_time: float` — slack minus total remaining processing
- `job.remaining_routing: tuple[Server, ...]` — servers not yet visited
- `job.next_server: Server | None`
- `job.previous_server: Server | None`
- `job.routing: dict[Server, float]` — server-to-processing-time mapping
- `job.server_processing_times` — iterable of `(server, processing_time)` pairs

**Due date helpers:**
- `job.is_finished_in_due_date_window(window_size=7) -> bool`
- `job.planned_release_date(allowance=2.0) -> float`
- `job.planned_slack_time_at(server, allowance=0) -> float | None`

## Router

```python
from simulatte.router import Router

Router(
    *,
    env: Environment,
    shopfloor: ShopFloor,
    servers: Sequence[Server],                  # All available servers
    psp: PreShopPool | None,                    # None = push, set = pull
    inter_arrival_distribution: Callable[[], float],
    sku_distributions: dict[str, float],        # SKU -> probability weight
    sku_routings: dict[str, Callable[[], Sequence[Server]]],
    sku_service_times: dict[str, dict[Server, Callable[[], float]]],
    due_date_offset_distribution: dict[str, Callable[[], float]],
    priority_policies: Callable[[ProductionJob, Server], float] | None = None,
)
```

The Router auto-starts as a SimPy process on instantiation. It runs forever,
generating jobs at intervals drawn from `inter_arrival_distribution`.

**Configuration structure for `sku_service_times`:**
```python
{
    "SKU_A": {
        server1: lambda: truncated_2erlang(lam=2.0, max_value=4.0),
        server2: lambda: truncated_2erlang(lam=2.0, max_value=4.0),
    },
    "SKU_B": {
        server1: lambda: random.uniform(1.0, 3.0),
        server2: lambda: random.expovariate(0.5),
    },
}
```

**Due date computation inside Router:** `due_date = env.now + offset()` where
`offset = due_date_offset_distribution[sku]()`. This is an absolute time.

## PreShopPool

```python
from simulatte.psp import PreShopPool

PreShopPool(*, env: Environment, shopfloor: ShopFloor)
```

Pure container — no built-in release logic. Release policies are external.

**Methods:**
- `psp.add(job)` — add job, triggers `psp.new_job` event
- `psp.remove(job=None)` — remove specific job or FIFO (oldest)
- `len(psp)`, `psp.empty`, `job in psp`, `psp[index]`
- `psp.jobs` — iterable over jobs in FIFO order

**Event:** `psp.new_job` — one-shot event, fires when a job arrives.

## Release Policies

### LumsCor

```python
from simulatte.policies.lumscor import LumsCor

LumsCor(
    *,
    wl_norm: dict[Server, float],              # Workload norm per server
    allowance_factor: int,                      # Buffer time per server for due dates
)
```

**Methods:**
- `lumscor.periodic_release(psp)` — release jobs within norms (for periodic_trigger)
- `lumscor.starvation_release(triggering_job, psp)` — release on starvation (for on_completion_trigger)
- `lumscor.pst_priority_policy(job, server) -> float` — PST dispatching

Requires `CorrectedWIPStrategy` on the shopfloor.

### Slar

```python
from simulatte.policies.slar import Slar

Slar(allowance_factor: float = 2.0)
```

**Methods:**
- `slar.decide_next_job(triggering_job, psp)` — main release callback (for on_completion_trigger)
- `slar.pst_priority_policy(job, server) -> float` — PST dispatching

## Trigger Functions

```python
from simulatte.policies.triggers import (
    periodic_trigger,
    on_arrival_trigger,
    on_completion_trigger,
)
from simulatte.policies.starvation_avoidance import starvation_avoidance_backup
```

All return SimPy generators — register with `env.process(...)`.

```python
periodic_trigger(psp, interval: float, release_fn: (PreShopPool) -> None)
on_arrival_trigger(psp, release_fn: (ProductionJob, PreShopPool) -> None)
on_completion_trigger(shopfloor, psp, release_fn: (ProductionJob, PreShopPool) -> None)
starvation_avoidance_backup(shopfloor, psp)
```

**`periodic_trigger`**: calls `release_fn(psp)` every `interval` time units
(only if PSP is non-empty).

**`on_arrival_trigger`**: calls `release_fn(job, psp)` when a new job enters
the PSP.

**`on_completion_trigger`**: calls `release_fn(triggering_job, psp)` when any
job finishes processing at a server.

**`starvation_avoidance_backup`**: monitors PSP arrivals and immediately
releases any job whose first server is completely idle (empty queue and no
active users).

## Pluggable Protocols

### OperationHook

```python
def my_hook(
    job: ProductionJob,
    server: Server,
    op_index: int,
    processing_time: float,
) -> ProcessGenerator:
    yield server.env.timeout(1.0)
```

Must be a generator (use `yield`). Used with `before_operation` / `after_operation`.

### WIPStrategy

```python
class MyStrategy:
    def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None: ...
    def complete_operation(self, job, server, op_index, processing_time, wip) -> None: ...
```

Built-in: `StandardWIPStrategy` (full processing times), `CorrectedWIPStrategy`
(position-discounted: 1/1, 1/2, 1/3, ...).

### MetricsCollector

```python
class MyCollector:
    def record(self, job: ProductionJob) -> None: ...
```

Called when each job completes. Built-in: `EMAMetricsCollector(alpha=0.01)`.

### TimeSeriesCollector

```python
class MyTSCollector:
    def on_job_entered(self, shopfloor, job) -> None: ...
    def on_operation_completed(self, shopfloor, job, server, op_index) -> None: ...
    def on_job_finished(self, shopfloor, job) -> None: ...
```

Built-in: `DefaultTimeSeriesCollector` (WIP, job count, throughput, lateness
with matplotlib plots), `CurrentWorkLoadCollector` (true remaining work).

## WIP Strategy Classes

```python
from simulatte.shopfloor import (
    StandardWIPStrategy,
    CorrectedWIPStrategy,
    EMAMetricsCollector,
    DefaultTimeSeriesCollector,
    CurrentWorkLoadCollector,
)
```

## Builder Functions

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    spt_priority_policy,
)
```

### build_immediate_release_system

```python
build_immediate_release_system(
    env: Environment,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,            # ~1.543 jobs/time unit
    service_rate: float = 2.0,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
    priority_policies: Callable | None = None,
    collect_workload: bool = False,
) -> PushSystem  # (None, servers, shopfloor, router)
```

### build_lumscor_system

```python
build_lumscor_system(
    env: Environment,
    *,
    check_timeout: float,                       # Periodic release interval
    wl_norm_level: float,                       # Workload norm per server
    allowance_factor: int,                      # Buffer per server for due dates
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem  # (psp, servers, shopfloor, router)
```

### build_slar_system

```python
build_slar_system(
    env: Environment,
    allowance_factor: float,                    # Slack allowance per operation
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem  # (psp, servers, shopfloor, router)
```

Note: `allowance_factor` is positional in `build_slar_system` but keyword-only
in `build_lumscor_system`.

### spt_priority_policy

```python
spt_priority_policy(job: ProductionJob, server: Server) -> float
```

Returns `job.routing[server]` — Shortest Processing Time dispatching.

## Distribution Helpers

```python
from simulatte.distributions import server_sampling, truncated_2erlang, RunningStats
```

- `server_sampling(servers) -> Callable[[], Sequence[Server]]` — returns a
  factory that samples random subsets (1 to len) without replacement.
- `truncated_2erlang(lam=2, max_value=4.0) -> float` — single sample from
  Gamma(2, 1/lam), truncated at max_value. Mean = 2/lam.
- `RunningStats` — Welford's algorithm for online mean/variance/std.
  Methods: `.update(x)`, `.mean`, `.variance`, `.std`, `.z_norm(x)`.

## Type Aliases

```python
from simulatte.typing import (
    Distribution,           # Callable[[], T]
    DiscreteDistribution,   # dict[K, T]
    System,                 # tuple[T, tuple[Server, ...], ShopFloor, Router]
    PushSystem,             # System[None]
    PullSystem,             # System[PreShopPool]
    Builder,                # Callable[..., S]
    ProcessGenerator,       # re-exported from simpy.events
)
```

## Runner

```python
from simulatte.runner import Runner

Runner(
    *,
    builder: Builder[S],                        # Callable(*, env) -> system
    seeds: Sequence[int],
    parallel: bool = False,
    progress: bool | None = None,               # None = auto-detect TTY
    extract_fn: Callable[[S], T],
    n_jobs: int | None = None,                  # Parallel workers (default: CPU count)
    log_dir: Path | None = None,                # Per-run log files
    log_format: Literal["text", "json"] = "text",
)
```

**Method:** `runner.run(until: float) -> list[T]` — returns extracted results
in seed order, one entry per seed.

Each run creates its own `Environment` (with optional per-run log file),
seeds `random.seed(seed)`, calls `builder(env=env)`, runs `env.run(until=until)`,
and extracts results via `extract_fn(system)`.
