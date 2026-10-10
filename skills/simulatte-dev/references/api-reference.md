# Simulatte API Reference

Exact signatures and configuration patterns for the Simulatte core library.
Read this file when writing Simulatte code to get constructor parameters,
types, and return values right.

## Environment

```python
from simulatte.environment import Environment

Environment(
    *,
    seed: int | None = None,                    # 0 <= seed < 2**63; None draws from os.urandom (read env.seed)
    time_unit: str | None = None,               # Recorded in the manifest
    provenance: Provenance | None = None,       # Hashes of model/source/inputs/dependencies
    debug: bool = False,                        # Validate events and state schemas (slower)
    log_level: str = "INFO",                    # Per environment, applies to the default sinks
    log_file: str | Path | None = None,         # Log output path (default: stderr)
    log_format: Literal["text", "json"] = "text",
    log_history_size: int = 1000,               # In-memory history capacity
    log_db_path: str | Path | None = None,      # SQLite path for persistent logs
)
```

Extends `simpy.Environment`. Supports context manager (`with Environment() as env:`),
which closes trace recorders and log sinks on exit.

**Members:** `env.seed`, `env.rng(name)` (named `random.Random` stream),
`env.bind(value, *, kind, stream, owner)`, `env.bus` (`subscribe(handler, types)`),
`env.emit(event)`, `env.wants(EventClass)`, `env.entities` (registry),
`env.activate()` / `env.activated` / `env.on_activate(fn)`, `env.configure_kpis(warmup=)`,
`env.collectors`, `env.enable_digest()`, `env.fingerprint()`, `env.manifest()`,
`env.opaque_sampler_owners`, `env.sinks`, `env.log_history`, `env.log_db`, `env.close()`.
`env.run()` activates on first use; call `env.activate()` before driving manually
with `env.step()`.

**Logging methods:** `env.debug(msg, component=..., **extra)`, `.info()`,
`.warning()`, `.error()`. They emit `log` events written by the sinks of
`simulatte.logsinks`. There is no `env.logger`.

**Log querying:**
```python
env.log_history.query(level="ERROR", component="Server", since=100.0)
env.log_db.query(...)          # needs log_db_path; also env.log_db.execute_sql(...)
```

**Component filtering** (per sink):
```python
for sink in env.sinks:
    sink.disable_component("Server")
    sink.enable_component("ShopFloor")
```

## Events, traces and KPIs

```python
from simulatte import (
    Collector, DomainEvent, Environment, Event, KPI, ObserverEvent, Provenance, Runner, Trace, TraceRecorder,
)
from simulatte.events import event_type
from simulatte.trace import ChunkLimits, ReaderLimits, TraceCorrupted
from simulatte.collectors import (
    EMACollector, ShopFloorTimeSeries, CurrentWorkloadCollector, ServerTimeSeries, ShopFloorKPIs,
)
```

```python
env.bus.subscribe(handler, (JobFinished,))        # or "*" (domain events) / "**" (all); returns Subscription
if env.wants(MyEvent): env.emit(MyEvent(...))    # guard pattern; payload from data you already hold

TraceRecorder(env, path, *, level="full" | "kpi", chunk_limits=None)   # before activation
trace = Trace.open(path, *, limits=None)
trace.state_at((t, seq)); trace.events(start, end); trace.kpis(); trace.kpi_series()
trace.manifest; trace.fingerprint; trace.outcome; trace.truncated; trace.cursor_range
trace.check()      # raises TraceCorrupted on damage
trace.verify()     # True / False / "not_verifiable"
```

**Catalog** (`entity.created/retired`, `psp.entered/exited`, `shopfloor.entered`,
`operation.started/completed`, `shopfloor.wip_updated`, `job.finished`, `job.queued/granted/queue_left/released`,
`server.queue_reordered`, `server.work_credited`, `policy.decision`, and the intralogistics events): classes live in
`simulatte.shopfloor`, `simulatte.server`, `simulatte.psp`, `simulatte.policies`,
`simulatte.intralogistics.events`. Observer events: `log`, `kpi.sample`.

**Collector** (`simulatte.kpi`): subclass with class attributes `kpis: tuple[KPI, ...]`,
`subscribes`, `scope_field`; implement `on_event(event)`; call `self.observe(kpi, value)` for
scalar aggregates (mean, sum, count, min, max) or `self.sample(kpi, value)` for a series KPI;
attach with `collector.attach(env)`. Results: `collector.scalars()` keyed
`"<scope id>/<kpi>"`, merged in `env.fingerprint().kpis`.

**Entity ids**: `job-<n>`, `server-<n>` (or `name=`), builders use `wc-<i>`, `shopfloor`,
`router`, `psp` (+ `prefix=`). Names must not contain `/` or NUL and must not match
`<kind>-<n>`.

## ShopFloor

```python
from simulatte.shopfloor import ShopFloor

ShopFloor(
    *,
    env: Environment,
    ema_alpha: float = 0.01,                    # EMA smoothing (0, 1]
    material_coordinator=None,                  # Experimental, skip
    wip_strategy: WIPStrategy | None = None,    # Default: StandardWIPStrategy
    default_metrics: bool = True,                # Attach EMACollector as shopfloor.metrics
    on_before_operation: OperationHook | list[OperationHook] | None = None,
    on_after_operation: OperationHook | list[OperationHook] | None = None,
    on_job_finished: Callable | list[Callable] | None = None,
    name: str | None = None,                     # Entity id (default shopfloor-<n>)
    label: str | None = None,
)
```

**Key methods:**
- `shopfloor.add(job)` — release a job onto the shopfloor
- `shopfloor.set_wip_strategy(strategy)` — replace WIP strategy at runtime
- `shopfloor.on_before_operation(hook)` — register hook post-construction
- `shopfloor.on_after_operation(hook)` — register hook post-construction
- `shopfloor.on_job_finished(callback)` — register callback post-construction
- `shopfloor.on_processing_end(callback)` — callback `(job, server) -> None`, fires after server release
- `shopfloor.attach_dispatcher(dispatcher, *, psp=None)` — wire a dispatcher object's methods

**Key attributes:**
- `shopfloor.jobs: dict[ProductionJob, None]` — currently active jobs (insertion ordered; use `jobs[job] = None` / `del jobs[job]`)
- `shopfloor.metrics: EMACollector | None` — default EMA collector (`ema_*` attributes)
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
    retain_job_history: bool = False,            # Keep list of processed jobs
    name: str | None = None,                     # Entity id (default server-<n>)
    label: str | None = None,
)
```

Extends `simpy.PriorityResource`.

**Key attributes:**
- `server.utilization_rate: float` — fraction of time busy (0 to 1)
- `server.average_queue_length: float` — time-weighted average
- `server.idle_time: float`
- `server.worked_time: float`
- `server.empty: bool` — whether queue is empty
- `server.is_idle: bool` — no active users and empty queue
- `server.current_jobs: tuple[BaseJob, ...]` — jobs occupying active slots
- `server.queue` — SimPy queue of waiting requests
- `server.queueing_jobs` — iterator over jobs in queue

**Methods:**
- `server.sort_queue()` — re-sort queue by priority keys
- Queue length and utilization over time: `ServerTimeSeries(server).attach(env)` gives `qt`, `ut`, `plot_qt()`, `plot_ut()`

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
- `job.remaining_routing: tuple[Server, ...]` — servers not yet entered (entry-based)
- `job.unfinished_routing: tuple[Server, ...]` — servers not yet exited, includes the in-progress operation (exit-based)
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
    inter_arrival_distribution: ScalarSource,   # Distribution description, number or callable
    sku_distributions: Mapping[str, float],     # SKU -> probability weight
    sku_routings: Mapping[str, RoutingSource],  # Routing description, fixed servers or callable
    sku_service_times: Mapping[str, Mapping[Server, ScalarSource]],
    due_date_offset_distribution: Mapping[str, ScalarSource],
    priority_policies: Callable[[ProductionJob, Server], float] | None = None,
    name: str | None = None,                    # Entity id (default router-<n>)
    label: str | None = None,
)
```

The Router auto-starts as a SimPy process on instantiation. It runs forever,
generating jobs at intervals drawn from `inter_arrival_distribution`. Distribution
descriptions, numbers and routing descriptions are *managed*: they are bound to the named
streams `<router>/interarrival`, `<router>/sku`, `<router>/routing/<sku>`,
`<router>/service/<sku>/<server>` and `<router>/due/<sku>` of `env.seed`. A plain callable
is *opaque* (accepted, not reproducible, makes the manifest incomplete). Changing the
arguments after construction has no effect.

**Configuration structure for `sku_service_times`:**
```python
{
    "SKU_A": {
        server1: TruncatedErlang(rate=2.0, shape=2, max_value=4.0),
        server2: TruncatedErlang(rate=2.0, shape=2, max_value=4.0),
    },
    "SKU_B": {
        server1: Uniform(1.0, 3.0),
        server2: Exponential(rate=0.5),
    },
}
```

**Due date computation inside Router:** `due_date = env.now + offset()` where
`offset = due_date_offset_distribution[sku]()`. This is an absolute time.

## PreShopPool

```python
from simulatte.psp import PreShopPool

PreShopPool(*, env: Environment, shopfloor: ShopFloor, name: str | None = None, label: str | None = None)
```

Pure container — no built-in release logic. Release policies are external.

**Methods:**
- `psp.add(job)` — add job, triggers `psp.new_job` event
- `psp.remove(job=None, *, reason="removed")` — remove specific job or FIFO (oldest); `reason` (`released`, `postponed`, `removed`) is recorded in the `psp.exited` event
- `psp.release(job)` — remove from PSP and add to shopfloor
- `psp.jobs_starting_at(server) -> list[ProductionJob]` — jobs whose routing starts at server
- `psp.on_arrival(callback)` — callback `(job, psp) -> None`, fires synchronously on add
- `len(psp)`, `psp.empty`, `job in psp`, `psp[index]`
- `psp.jobs` — iterable over jobs in FIFO order

**Event:** `psp.new_job` — one-shot event, fires when a job arrives.

## Release Policies

### LumsCor

```python
from simulatte.policies.lumscor import LumsCor

LumsCor(
    *,
    shopfloor: ShopFloor,
    psp: PreShopPool,
    router: Router,
    wl_norm: float | dict[Server, float],       # Workload norm (scalar -> all servers, or dict)
    check_timeout: float,                        # Periodic release interval
    allowance_factor: int,                       # Buffer time per server for due dates
)
```

Construction is active (like `Slar`): `LumsCor.__init__` sets
`CorrectedWIPStrategy` on the shopfloor, wires the PST priority rule on
`router`, starts a periodic release trigger, wires `shopfloor.on_processing_end`
for starvation release, and wires `psp.on_arrival(starvation_avoidance)`. No
separate method calls needed.

**Methods (also callable manually for custom wiring):**
- `lumscor.periodic_release(psp)` — release jobs within norms (for periodic_trigger)
- `lumscor.starvation_release(triggering_job, psp)` — release on starvation (for on_completion_trigger)

### Slar

```python
from simulatte.policies.slar import Slar

Slar(
    *,
    shopfloor: ShopFloor,
    psp: PreShopPool,
    router: Router,
    allowance_factor: float = 2.0,
)
```

Construction is active: wires `shopfloor.on_processing_end`, `psp.on_arrival(starvation_avoidance)`,
and sets `router.priority_policies` to PST dispatching automatically. No separate method calls needed.

### SlarLimit

```python
from simulatte.policies.slar_limit import SlarLimit

SlarLimit(
    *,
    shopfloor: ShopFloor,
    psp: PreShopPool,
    router: Router,
    wl_norm: float | dict[Server, float],
    allowance_factor: float = 2.0,
)
```

SLAR variant that gates urgent insertion by a workload norm. `wl_norm` accepts
either a scalar, which expands to every shopfloor server, or a mapping, which
is accepted verbatim subject to the existing validation. Sets
`CorrectedWIPStrategy` on the shopfloor. Construction is active (wires
triggers + PST dispatching).

### Draco

```python
from simulatte.policies.draco import Draco

Draco(
    *,
    shopfloor: ShopFloor,
    router: Router,                                    # self-wires router.priority_policies
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (0.25, 0.25, 0.5),  # paper full DRACO (Table 2)
    wip_target: int,                                  # tau (job count)
    loop_target: int | dict[tuple[Server, Server], int],  # epsilon
    psp: PreShopPool | None = None,
)
```

**Methods:**
- `draco.priority_policy(job, server)` — queue-side priority (`-inf` for a forced PSP winner)
- `draco.decide_next_job(triggering_job, server)` — the non-hierarchical decision

Non-hierarchical: scores `Q_k ∪ P_k` by `w^R·R + w^A·A + w^D·D` on each completion. `D` is FOCUS. **Construction is active (like `Slar`)**: `Draco.__init__` self-wires `router.priority_policies`, `shopfloor.on_processing_end(decide_next_job)`, and (when a `psp` is given) `psp.on_arrival(starvation_avoidance)` — so `build_draco_system` is a single `Draco(...)` call and a direct user cannot forget the hooks.

> Caveats: DRACO assumes `capacity == 1` (one freed slot per completion; the force-pin relies on it). `starvation_avoidance` bypasses R/A/D scoring (releases an arrival when its first server is idle) — a liveness provision, not a DRACO decision; likewise a released job entering an idle downstream server is auto-granted with no decision.

> Note: ConWIP and Continuous Release are also available (`simulatte.policies.conwip.ConWIP`, `simulatte.policies.continuous_release.ContinuousRelease`) for manual composition via triggers.

## Trigger Functions

```python
from simulatte.policies.triggers import (
    periodic_trigger,
    on_arrival_trigger,
    on_completion_trigger,
)
from simulatte.policies.starvation_avoidance import starvation_avoidance
```

`periodic_trigger`, `on_arrival_trigger`, and `on_completion_trigger` return
SimPy generators — register with `env.process(...)`.

`starvation_avoidance` is a plain callback `(job, psp) -> None` for use with
`psp.on_arrival()`.

```python
periodic_trigger(psp, interval: float, release_fn: (PreShopPool) -> None)
on_arrival_trigger(psp, release_fn: (ProductionJob, PreShopPool) -> None)
on_completion_trigger(shopfloor, psp, release_fn: (ProductionJob, PreShopPool) -> None)
starvation_avoidance(job, psp)
```

**`periodic_trigger`**: calls `release_fn(psp)` every `interval` time units
(only if PSP is non-empty).

**`on_arrival_trigger`**: calls `release_fn(job, psp)` when a new job enters
the PSP. SimPy process-based alternative for advanced use cases.

**`on_completion_trigger`**: calls `release_fn(triggering_job, psp)` when any
job finishes processing at a server. SimPy process-based alternative for
advanced use cases.

**`starvation_avoidance`**: plain callback for `psp.on_arrival()`. Immediately
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
) -> ProcessGenerator | None:
    yield server.env.timeout(1.0)
```

Used with `on_before_operation` / `on_after_operation`. Can be a generator
(use `yield`) or a plain sync function returning None.

### Dispatcher

Optional protocol for objects that bundle multiple shopfloor hooks.
All methods are optional — `attach_dispatcher` wires only those present.

```python
def on_before_operation(self, job, server, op_index, processing_time) -> ProcessGenerator | None: ...
def on_after_operation(self, job, server, op_index, processing_time) -> ProcessGenerator | None: ...
def on_job_finished(self, job) -> None: ...
def on_processing_end(self, job, server) -> None: ...
def on_psp_arrival(self, job, psp) -> None: ...
```

### WIPStrategy

```python
class MyStrategy:
    def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None: ...
    def complete_operation(self, job, server, op_index, processing_time, wip) -> None: ...
```

Built-in: `StandardWIPStrategy` (full processing times), `CorrectedWIPStrategy`
(position-discounted: 1/1, 1/2, 1/3, ...).

### Collector

```python
from typing import ClassVar
from simulatte.kpi import KPI, Collector
from simulatte.shopfloor import JobFinished

class MyCollector(Collector):
    kpis: ClassVar = (KPI("max_lateness", unit="time", aggregation="max"),)
    subscribes: ClassVar = (JobFinished,)
    scope_field: ClassVar = "shopfloor"           # only events of the owner

    def on_event(self, event: JobFinished) -> None:
        self.observe("max_lateness", event.lateness)

MyCollector(shopfloor).attach(env)
```

Collectors replace the 0.12 `MetricsCollector` / `TimeSeriesCollector` protocols. Built-in
(`simulatte.collectors`): `EMACollector(shopfloor, alpha=0.01)` (the default `shopfloor.metrics`),
`ShopFloorTimeSeries(shopfloor)` (`wip_ts`, `job_count_ts`, `throughput_ts`, `lateness_ts`, `plot_*`),
`CurrentWorkloadCollector(shopfloor)` (true remaining work), `ServerTimeSeries(server)` (`qt`, `ut`,
`plot_qt`, `plot_ut`) and `ShopFloorKPIs(shopfloor)` (window-aware KPIs).

## WIP Strategy Classes

```python
from simulatte.shopfloor import StandardWIPStrategy, CorrectedWIPStrategy
```

## Scenario

```python
from simulatte.scenario import Scenario, SkuFamily, ShopType
from simulatte.distributions import TruncatedErlang, Uniform

Scenario(
    shop_type: ShopType = ShopType.PJS,         # PJS / GFS / PFS
    n_servers: int = 6,
    target_utilization: float = 0.90,           # rho; arrival rate is derived to hold it
    families: tuple[SkuFamily, ...] = (SkuFamily(),),  # one or more product families
    due_date_offset: Distribution = Uniform(30.0, 45.0),  # fallback offset for families without one
    arrival_process: Callable[[float], Callable[[], float]] = Exponential,
    arrival_rate: float | None = None,          # explicit override; else derived from utilization
)
```

Immutable (`@dataclass(frozen=True)`) description of a shop *environment* and its
order stream — owned by every `build_*_system` via `scenario=`. The exponential
arrival rate is **derived** per shop type from `target_utilization` and the mix-
weighted mean routing length and service-time mean (so `rho` is held constant
across shops and product mixes); pass `arrival_rate=` to pin it explicitly.
The default `Scenario()` derives a rate of ≈1.5597 (mean inter-arrival ≈0.641).

> Caveat — `arrival_process` contract: the factory is called with the resolved
> arrival **rate** and must return a zero-arg inter-arrival sampler whose mean is
> `1 / rate`. `Exponential` is the only built-in that satisfies this (Poisson
> arrivals). Others — e.g. `Erlang`, whose `Erlang(rate)` has mean `shape / rate`
> — type-check but silently mis-calibrate `target_utilization`. For non-Poisson
> arrivals, wrap your factory so its sampler honours the mean-`1/rate` contract,
> or pin `arrival_rate=` explicitly.

**`SkuFamily` fields:**
```python
SkuFamily(
    name: str = "F1",
    weight: float = 1.0,                        # relative mix weight
    service_time: Distribution = TruncatedErlang(rate=2.0, shape=2, max_value=4.0),
    routing_factory: ... | None = None,          # custom routing; else uses shop_type default
    expected_routing_length: float | None = None,  # required when routing_factory is set
    due_date_offset: Distribution | None = None, # per-family override; else Scenario.due_date_offset
    twk_allowance_factor: float | None = None,  # if set, due dates use TWK rule (k * total work)
)
```

**Convenience constructor** for the one-product case:
```python
Scenario.single(service_time=TruncatedErlang(rate=2.0), n_servers=8)
# equivalent to: Scenario(families=(SkuFamily(service_time=TruncatedErlang(rate=2.0)),), n_servers=8)
```

**Presets** (each accepts keyword overrides, e.g. `Scenario.pure_flow_shop(n_servers=8)`):
- `Scenario.pure_job_shop()` — PJS: routing length `U[1, M]`, undirected.
- `Scenario.general_flow_shop()` — GFS: length `U[1, M]`, directed (ascending index), `E[L]=(M+1)/2`.
- `Scenario.pure_flow_shop()` — PFS: every job visits all `M` machines in fixed order, `E[L]=M`.

## Builder Functions

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_focus_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
    build_draco_system,
    build_conwip_system,
    build_continuous_release_system,
    build_starvation_avoidance_system,
)
```

Every builder takes `scenario: Scenario | None = None` (a fresh `Scenario()`) and `prefix: str = ""` (prefixes every entity id). Pass a preset or a custom
`Scenario` to vary the shop environment independently of the control method.

### build_immediate_release_system

```python
build_immediate_release_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    priority_policies: Callable | None = None,
    collect_workload: bool = False,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
) -> BuiltSystem[None]  # (None, servers, shopfloor, router, None)
```

### build_lumscor_system

```python
build_lumscor_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    check_timeout: float,                       # Periodic release interval
    wl_norm_level: float,                       # Workload norm per server
    allowance_factor: int,                      # Buffer per server for due dates
    collect_workload: bool = False,
) -> BuiltSystem[LumsCor]  # (psp, servers, shopfloor, router, lumscor)
```

### build_slar_system

```python
build_slar_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    allowance_factor: float,                    # Slack allowance per operation
    collect_workload: bool = False,
) -> BuiltSystem[Slar]  # (psp, servers, shopfloor, router, slar)
```

### build_slar_limit_system

```python
build_slar_limit_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    allowance_factor: float,                    # Slack allowance per operation
    wl_norm_level: float,                       # Workload norm per server
    collect_workload: bool = False,
) -> BuiltSystem[SlarLimit]  # (psp, servers, shopfloor, router, slar_limit)
```

Requires `CorrectedWIPStrategy` on the shopfloor (set automatically by the builder).

### build_focus_system

```python
build_focus_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    collect_workload: bool = False,
) -> BuiltSystem[None]  # (None, servers, shopfloor, router, None)
```

Immediate-release push system whose queue ordering is FOCUS (via `FocusPriorityRule`).

### build_draco_system

```python
build_draco_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    wip_target: int,                                  # tau (job count)
    loop_target: int,                                 # epsilon (scalar; use Draco() for per-pair)
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (0.25, 0.25, 0.5),  # paper full DRACO (Table 2)
    collect_workload: bool = False,
) -> BuiltSystem[Draco]  # (psp, servers, shopfloor, router, draco)
```

Non-hierarchical release+dispatch. Wires `Draco.priority_policy`, `shopfloor.on_processing_end(Draco.decide_next_job)`, and `psp.on_arrival(starvation_avoidance)`.

### build_conwip_system

```python
build_conwip_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    wip_cap: int,                               # max jobs on the floor at once
    collect_workload: bool = False,
) -> BuiltSystem[ConWIP]  # (psp, servers, shopfloor, router, conwip)
```

Constant-WIP release: holds jobs in the PSP and releases (earliest due date first)
while the shop holds fewer than `wip_cap` jobs. Constructs `ConWIP`, which
self-wires release on PSP arrival and on every completion.

### build_continuous_release_system

```python
build_continuous_release_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    wl_norm_level: float,                       # corrected workload norm per server
    allowance_factor: int = 2,                  # buffer per server for due-date planning
    collect_workload: bool = False,
) -> BuiltSystem[ContinuousRelease]  # (psp, servers, shopfloor, router, continuous_release)
```

Workload-controlled continuous release. Constructs `ContinuousRelease`, which
sets `CorrectedWIPStrategy` on the shopfloor and self-wires its triggers.

### build_starvation_avoidance_system

```python
build_starvation_avoidance_system(
    *,
    env: Environment,
    scenario: Scenario | None = None,       # None: a fresh Scenario()
    prefix: str = "",                         # Entity id prefix
    collect_workload: bool = False,
) -> BuiltSystem[None]  # (psp, servers, shopfloor, router, None)
```

Minimal pull system: jobs wait in the PSP and are released only by
`starvation_avoidance` (wired on `psp.on_arrival`) — a job is released the moment
its first server is idle. Useful as a liveness-only baseline.

### Benchmark shop environments (PJS / GFS / PFS)

The benchmark shops are now **`Scenario` presets**, not dedicated builders. Use
any `build_*_system` with `scenario=`:

```python
from simulatte.builders import build_immediate_release_system
from simulatte.scenario import Scenario

# Pure Flow Shop, immediate-release push baseline:
build_immediate_release_system(env=env, scenario=Scenario.pure_flow_shop())
```

`Scenario.pure_job_shop()` (PJS: random length `U[1,M]`, undirected),
`Scenario.general_flow_shop()` (GFS: directed/sorted, `E[L]=(M+1)/2`), and
`Scenario.pure_flow_shop()` (PFS: every job visits all machines in fixed order,
`E[L]=M`) each accept keyword overrides (e.g. `n_servers`, `target_utilization`,
`twk_allowance_factor`). The exponential arrival rate is **derived per shop
type** from `target_utilization` and `E[L]` so `rho` is held constant across
shops — the PFS therefore arrives slower (mean inter-arrival ≈1.099) than the job
shop / general flow shop (≈0.641). Reusing one arrival rate across shop types
would drive the PFS unstable (`rho > 1`). Any builder takes `scenario=`, so the
same preset composes with the workload-control methods (LumsCor, SLAR, DRACO,
…). The routing factories and rate/due-date helpers live in
`simulatte.distributions` (`pure_job_shop_routing`, `general_flow_shop_routing`,
`pure_flow_shop_routing`, `arrival_rate_for_utilization`, `twk_due_date`).

## Dispatching Rules

```python
from simulatte.dispatching_rules import (
    # Tier 1 — stateless plain functions
    shortest_processing_time,
    earliest_due_date,
    operational_due_date,
    modified_operational_due_date,
    critical_ratio,
    first_come_first_served,
    # Tier 2 — factory functions (call with allowance, get callable back)
    planned_slack_time,
    slack_per_remaining_operation,
)
```

All rules are `(job, server) -> float` callables. Lower value = served first
(matches SimPy `PriorityResource` ascending sort). Pass to
`Router(priority_policies=...)` or `ProductionJob(priority_policy=...)`.

### Tier 1 — stateless functions

| Rule | Returns | Description |
|------|---------|-------------|
| `shortest_processing_time(job, server)` | `job.routing[server]` | SPT: shorter processing time first |
| `earliest_due_date(job, server)` | `job.due_date` | EDD: earlier due date first (server-agnostic) |
| `operational_due_date(job, server)` | `float` | ODD: distributes shop-floor slack across operations |
| `modified_operational_due_date(job, server)` | `float` | MODD: `max(ODD, now + p_ij)` — switches between ODD and SPT regimes |
| `critical_ratio(job, server)` | `float` | CR: `(due_date - now) / remaining_processing_time` |
| `first_come_first_served(job, server)` | `0.0` | FCFS: tiebreaking falls to SimPy entry timestamp |

### Tier 2 — factory functions

Call the factory with a per-operation allowance to get the dispatching callable:

```python
# planned_slack_time: PST = (due_date - now) - sum(p_ik + k for k in remaining ops)
pst_rule = planned_slack_time(allowance=2.0)          # returns (job, server) -> float
router = Router(..., priority_policies=pst_rule)

# slack_per_remaining_operation: S/OPN = PST / count(remaining ops)
sopn_rule = slack_per_remaining_operation(allowance=2.0)
```

```python
planned_slack_time(allowance: float = 0.0) -> Callable[[job, Server], float]
slack_per_remaining_operation(allowance: float = 0.0) -> Callable[[job, Server], float]
```

Both raise `ValueError` if `allowance < 0`. Both return `inf` for servers not
in the job's routing or already exited (safe for `min()` comparisons).

> **Note:** `job.planned_slack_time_at(server, allowance=0)` is a job-level
> method (returns the raw slack value, or `None` for an out-of-routing/exited
> server), not a dispatching rule. The `planned_slack_time` factory above wraps
> it into a router-compatible callable (mapping `None` to `inf`).

### Tier 3 — system-state rules

```python
from simulatte.dispatching_rules import Focus, FocusContext, FocusPriorityRule
```

`Focus(weights=(w1, w2, w3, w4, w5))` — five mechanisms (pi/omega/psi/gamma/beta),
each in `[0, 1]`, weights sum to 1. A class, not a `(job, server) -> float`
callable. Key methods: `focus.build_context(shopfloor, now, *, psp=None,
compute_beta=True)` (shared per-decision aggregates) and `focus.score(job,
server, ctx, now)`. Adapt to the router with `FocusPriorityRule(focus,
shopfloor, *, psp=None)` (negates the score; lower = served first), or use
`build_focus_system`. FOCUS is also DRACO's dispatching component.

Weight order is `(π, ξ, τ, δ, β)` = `(pi, omega, psi, gamma, beta)` — the DRACO paper's Eq-9 order with `beta` 5th, **not** the FOCUS paper's Eq-12 order `(π, β, ξ, τ, δ)`; mind this when reproducing the Omega ablations. `beta` (`w5`) is off by default (Kasper et al. report it counter-productive); its `O(|O|·|J|)` entropy pass is skipped when `w5 == 0` (`compute_beta=False`).

## Distribution Helpers

```python
from simulatte.distributions import (
    Distribution, TruncatedErlang, Exponential, Erlang,
    LogNormal, Uniform, Deterministic,
    pure_job_shop_routing, RunningStats,
)
```

**Distribution built-ins** (callable variates with a `.mean` property satisfying the `Distribution` protocol):
- `TruncatedErlang(rate, shape=2, max_value=inf)` — Erlang truncated by rejection resampling; `shape=2` reproduces the classic benchmark service process. Also `Exponential(rate)`, `Erlang(rate, shape=2)`, `LogNormal(mu, sigma)`, `Uniform(low, high)`, `Deterministic(value)`.

**Routing factories** (each returns a frozen routing *description*, not a sampler; pass it
to `Router(sku_routings=...)`, which binds it to the `<router>/routing/<sku>` stream, or
bind it yourself with `env.bind(description, kind="routing", stream=..., owner=...)` to get
a `Callable[[], Sequence[Server]]`):
- `pure_job_shop_routing(servers) -> PureJobShopRouting` — Pure Job
  Shop (PJS) routing: random length `U[1, len]`, random order, without
  replacement (no re-entry).
- `general_flow_shop_routing(servers) -> GeneralFlowShopRouting` —
  General Flow Shop (GFS) routing: random length `U[1, len]`, subset sorted into
  a directed flow (ascending server index).
- `pure_flow_shop_routing(servers) -> FlowShopRouting` — Pure
  Flow Shop (PFS) routing: every job visits all servers in the same fixed
  (directed) sequence.

**Statistics:**
- `RunningStats` — Welford's algorithm for online mean/variance/std.
  Methods: `.update(x)`, `.mean`, `.variance`, `.std`, `.z_norm(x)`.

## Type Aliases

```python
from simulatte.typing import (
    Sampler,                # Callable[[], T]
    DiscreteDistribution,   # dict[K, T]
    BuiltSystem,            # NamedTuple(psp, servers, shop_floor, router, policy); generic in PolicyT
    Builder,                # Callable[..., S]
    ProcessGenerator,       # re-exported from simpy.events
)
```

`BuiltSystem[PolicyT]` is the structured result of every `build_*_system` factory.
It is a `NamedTuple`, so 5-target tuple unpacking
(`psp, servers, shop_floor, router, policy = build_lumscor_system(...)`) and
positional indexing both still work, alongside named-field access
(`result.policy`, `result.router`). `policy` is the wired policy instance
(`LumsCor`, `Slar`, `SlarLimit`, `Draco`, `ConWIP`, `ContinuousRelease`) or
`None` for the push / callback-only builders.

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
    log_level: str = "INFO",                    # Level of the per-run log files
)
```

**Method:** `runner.run(until: float) -> list[T]` — returns extracted results
in seed order, one entry per seed.

Each run creates its own `Environment(seed=seed)` (with optional per-run log file), does not
touch Python's global `random` module, calls `builder(env=env)`, runs `env.run(until=until)`,
and extracts results via `extract_fn(system)`.

## SimulatteEnv (Gymnasium Wrapper)

> **Experimental**: Part of `simulatte.experimental`.

```python
from simulatte.experimental.gymnasium import SimulatteEnv
```

Abstract base class extending `gymnasium.Env`. Subclass it and implement:

**Abstract methods (must override):**

```python
def setup(self, *, seed: int | None, options: dict[str, Any] | None) -> None:
    """Build fresh simulation for a new episode.
    Use self.np_random for numpy randomness. seed is also forwarded directly."""

def get_observation(self) -> Any:
    """Extract observation from simulation state. Must match observation_space."""

def apply_action(self, action: Any) -> None:
    """Apply action and advance simulation to next decision point."""

def compute_reward(self, action: Any) -> float:
    """Compute step reward. Receives the action for action-dependent costs."""

def is_terminated(self) -> bool:
    """Whether the episode ended naturally (e.g., all jobs processed)."""

def is_truncated(self) -> bool:
    """Whether the episode was cut short (e.g., time budget exceeded)."""
```

**Optional hooks (have safe defaults):**

```python
def teardown(self) -> None:
    """Clean up resources. Called before setup() on re-resets and from close()."""

def get_info(self) -> dict[str, Any]:
    """Return step info dict. Called last in step(). Default: {}."""
```

**Lifecycle methods (implemented by base class):**

```python
def reset(
    self, *, seed: int | None = None, options: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    # Calls: super().reset() -> teardown() (if not first) -> setup() -> get_observation()
    # Always returns (observation, {}). Does NOT call get_info().

def step(self, action: Any) -> tuple[Any, float, bool, bool, dict[str, Any]]:
    # Returns: (obs, reward, terminated, truncated, info)
    # Calls: apply_action() -> get_observation() -> compute_reward() -> is_terminated()
    #        -> is_truncated() -> get_info()
    # Raises RuntimeError if called before reset() or after episode end.

def close(self) -> None:
    # Calls teardown() if initialized, then super().close()
```

**State tracking (class-level defaults, shadowed per-instance):**

- `_is_initialized: bool = False` — set True after first `setup()`
- `_done: bool = False` — set True when `is_terminated()` or `is_truncated()` returns True

**Subclass `__init__` must set:**

- `self.observation_space: gymnasium.spaces.Space`
- `self.action_space: gymnasium.spaces.Space`
