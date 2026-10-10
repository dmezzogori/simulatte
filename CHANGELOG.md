# Changelog

All notable changes to Simulatte are documented here.

## 0.13.0 — Unreleased

Simulatte now records everything that happens in a run as typed events on a per-environment bus. Logging, KPI
collectors, the semantic digest and the new trace recorder are all subscribers of that bus. This release changes
many APIs and, because RNG streams are now derived from the seed and entity names, **seeded results differ from
0.12**. Read [Migrating from 0.12](#migrating-from-012) before upgrading.

### Added

- Event bus: `env.bus.subscribe(handler, types)` (event classes, `"*"` for domain events, `"**"` for everything),
  `env.emit`, `env.wants`. Typed `DomainEvent`s with state deltas and `ObserverEvent`s (`log`, `kpi.sample`),
  registered with `@event_type` in a catalog, in `simulatte.events`. Every component now publishes its transitions:
  servers, pre-shop pool, shop floor, release policies (`policy.decision`), fleet, orders, AGVs, traffic,
  warehouses, charging stations and parking areas. A server credits its own `worked_time` with
  `server.work_credited` (from `Server.process_job`), so a server used without a `ShopFloor` replays like one inside
  it.
- Entities (`simulatte.entities`): every component has a stable id (`job-<n>`, `server-<n>` or `name=`, `agv-<n>`,
  `order-<n>`, ...) and a declared state schema. `env.entities` is the registry; `name=` and `label=` arguments on
  `Server`, `ShopFloor`, `PreShopPool`, `Router`, `FleetCoordinator`, `Warehouse`, `ChargingStation` and
  `ParkingArea`; `NodeBinding` entities for graph nodes. Jobs and orders retire from the live registry when they
  finish.
- Builder `prefix`: every `build_*_system`, `build_simple_system`, `Scenario.build_floor` and
  `Scenario.build_router` take `prefix: str = ""`, so several systems can share one environment.
- Seeds and RNG streams: `Environment(seed=...)` (an integer in `[0, 2**63)`, or drawn from `os.urandom` and
  readable as `env.seed`), `env.rng(name)` and `env.bind(value, kind=..., stream=..., owner=...)` with three binding
  kinds: `scalar` (`() -> float`), `routing` (`() -> Sequence[Server]`) and `contextual` (`(*context) -> float`, for
  example `(sku, qty)` for warehouse picks). Distributions and routings are descriptions with `sampler(rng)`;
  `env.opaque_sampler_owners` lists callables the library cannot reproduce.
- Activation (`env.activate()`, `env.on_activate(fn)`, `env.activated`, `env.initial_state`):
  `env.run()` activates on first use. `FleetCoordinator.submit` and `cancel` called before activation are
  deferred and the order reports `OrderStatus.PENDING_ACTIVATION`.
- Semantic digest and manifest: `env.enable_digest()`, `env.fingerprint()` (digest plus KPI scalars),
  `env.manifest()`, `Provenance` and `RunManifest` (`simulatte.provenance`). The digest is identical across observer
  configurations, `PYTHONHASHSEED` values and, for the tested workloads, CPython and PyPy. A run stopped with
  `run(until=<simpy.Event>)` makes the manifest incomplete, because the stop cannot be reproduced from it.
- Traces: `TraceRecorder(env, path, level="full" | "kpi", chunk_limits=...)` writes a seekable, checksummed trace
  file; `Trace.open(path)` reads it (`state_at`, `events`, `kpis`, `kpi_series`, `manifest`, `fingerprint`,
  `check`, `verify`) and tolerates incomplete tails. Format 1.0, MessagePack with zlib-compressed chunks. Replay
  compares values by their canonical encoding in both readers: `True` is not `1`, `1` is not `1.0`, `-0.0` is not
  `0.0`, and NaN equals NaN, so a NaN in a state or a delta round-trips.
- `studio/`: a pnpm workspace with `@simulatte/trace`, a TypeScript reader that replays the same states as the Python
  reader (new `trace-ts` CI job). It is not part of the Python package.
- KPI framework (`simulatte.kpi`): `KPI` declarations, the `Collector` base class bound to an owner entity,
  `env.configure_kpis(warmup=...)`, window-aware aggregation, `env.collectors`.
- Built-in collectors: `simulatte.collectors` (`EMACollector`, `ShopFloorTimeSeries`, `CurrentWorkloadCollector`,
  `ServerTimeSeries`, `ShopFloorKPIs`) and, in `simulatte.intralogistics`, `OrderEMACollector`, `FleetTimeSeries`
  and `FleetKPIs`. `ShopFloor(default_metrics=True)` and `FleetCoordinator(default_metrics=True)` attach the EMA
  collector as `.metrics`.
- Log sinks (`simulatte.logsinks`): `TextSink`, `JsonSink`, `SQLiteSink`, `HistorySink`, `LogSink`.
  `Environment(log_level=...)`, `env.sinks`, `env.log_db` and `Runner(log_level=...)`. At `DEBUG` the sinks also
  render domain events.
- `Environment(debug=True)` validates events against the catalog and the entity state schemas, rejects subscribers
  that schedule events or draw random numbers, rejects two different values bound to one RNG stream, requires
  wire-valued `extra` in log calls (recorded as an immutable copy), accepts only `float` (not `int`) in float payload
  and state fields, and rejects lists, dicts, sets and other mutable containers anywhere in event payloads and deltas
  (use tuples and `FrozenMap`). Independently of debug mode, the trace recorder keeps an immutable copy of delta
  values, so changing a list after emitting it no longer changes the recorded trace.
- Intralogistics: `AGV.sample_load_time()` / `sample_unload_time()`; `SpeedProfile.motion(...)` describes AGV motion
  for traces; `OrderStatus.PENDING_ACTIVATION`.
- Top-level exports: `from simulatte import Environment, Runner, Provenance, TraceRecorder, Trace, KPI, Collector,
  Event, DomainEvent, ObserverEvent`. Component classes stay importable from their modules.
- Benchmarks (`benchmarks/`) and a `bench` CI job that gates the no-subscriber overhead against
  `simulatte==0.12.0`. Modes: `none` (no subscribers, gated), `default` (the default EMA collector), `digest` and
  `full` (ratios against `none`).
- Docs: the [Events, Traces & KPIs guide](docs/guides/events-and-traces.md) and an Events & Traces API page.

### Changed

- **Breaking:** logging is rebuilt on the bus and `loguru` is no longer a dependency; `msgpack` is a new
  dependency. `simulatte.logger` is removed (see Removed). `Environment(log_level=...)` is per environment.
  `log_format` accepts only `"text"` and `"json"`. History records are `simulatte.events.LogEvent` (`.timestamp` is
  now `.t`, plus `.seq`; `.extra` is a read-only map). JSON lines gain `seq`, `kind` and `type`. The SQLite table
  `log_events(id, env_id, timestamp, level, message, component, extra, wall_time)` is now
  `events(env_id, seq, t, kind, type, level, component, message, data_json)`; use `rowid` for insertion order.
  The production components (`Server`, `ShopFloor`, `PreShopPool`, `Router`) and the facilities (`Warehouse`,
  `TrafficManager`, `ChargingStation`, `ParkingArea`) no longer write DEBUG log messages; subscribe to their events
  instead. Fleet warnings and errors stay `log` events.
- Event construction calls no user code (observers cannot change results through it) for the value types it
  supports: `bool`, `int`, `float`, `str`, `None`, tuples and maps of them, subclasses of `int`, `float` and `str`
  (read through the base type, so a subclass's own `__float__` is never called), `Fraction` and `Decimal`, and NumPy
  scalar numbers. Anything else (user classes, NumPy arrays, `MappingProxyType`, user mappings) is not a wire value:
  a priority is recorded as null, another number as `NaN`, and debug mode rejects it. Values derived only for an
  event (`job.finished`'s `makespan`, `lateness`, `total_queue_time`) are computed from converted floats, not
  through model properties. Digests and traces read the simulation time the same way and never record a NaN time: a
  time of another type raises `TypeError`, a NaN time `ValueError`, at the first recorded event. Wire values are
  made of exact built-in types (an `IntEnum` or `StrEnum` in a delta becomes `int` or `str`).
- **Breaking:** seeding. `Runner` creates `Environment(seed=seed)` and no longer calls `random.seed(seed)`, so model
  code that draws from the global `random` module loses its reproducibility; draw from `env.rng(name)`.
  `Distribution.__call__` is removed: distributions are descriptions with `sampler(rng)`, bound to named streams
  (`<router>/interarrival`, `<router>/service/<sku>/<server>`, `<agv id>/load`, `<warehouse>/pick`, ...). Plain
  callables are still accepted where a distribution was, but are opaque (they make the manifest incomplete).
  `Environment(seed=...)` raises `ValueError` outside `[0, 2**63)` and `TypeError` for a `bool`. Because stream names
  derive from entity names, every seeded result changes: the numbers in the docs and the gallery were regenerated.
- **Breaking:** identity. Job ids are `job-<n>` (a per-environment counter) instead of UUIDs; AGV ids default to
  `agv-<n>` and `agv-<n>` is reserved (`AGV(agv_id="agv-0")` raises); order ids are `order-<n>` and `TransferOrder.id`
  is `None` until the order is attached. Entity names share one namespace per environment (graph node ids,
  warehouse, charging station and parking area names, and component names included), must not contain `/` or NUL,
  must not match `^<kind>-\d+$` for a registered kind, and duplicates raise. An entity is attached once.
- **Breaking:** builders and `Scenario.build_floor` / `Scenario.build_router` now name their entities (`wc-<i>`,
  `shopfloor`, `router`, `psp`), so building a second system in the same environment without `prefix=` raises a
  duplicate-id error. The `scenario` default of the builders is `None` (a fresh `Scenario()`) instead of a shared
  instance.
- **Breaking:** collectors. `ShopFloor(metrics_collector=...)`, `collect_time_series`, `time_series_collector`,
  `Server(collect_time_series=...)` and `FleetCoordinator(order_metrics_collector=..., time_series_collector=...)` are
  replaced by `default_metrics=` and bus collectors attached with `collector.attach(env)`; see the table below.
  Builder flags are kept and attach the new collectors: every `build_*_system` and `Scenario.build_floor` take `collect_workload`; `collect_time_series` exists only on `build_immediate_release_system` and `Scenario.build_floor`. `inventory_ts`
  is keyed by warehouse id with SKU-id keys instead of `Warehouse` and `SKU` objects.
- `Router` binds its distributions, routings and service times at construction: changing the arguments afterwards has
  no effect.
- **Breaking:** `TrafficManager.place` is renamed `place_now` (a runtime-checkable protocol). It reserves the node
  during activation, requires an immediate grant and raises on conflict instead of waiting, so two AGVs on one node of
  capacity 1 now raise.
- **Breaking:** `FleetCoordinator.create_order` attaches the order at once and no longer accepts `id=`. `submit` and
  `cancel` before activation are deferred until activation (queued in call order, run at time 0).
- **Breaking:** `LayoutGraph.nodes` returns a tuple in insertion order instead of a `frozenset`.
  `ShopFloor.jobs` is an insertion-ordered `dict[ProductionJob, None]` instead of a set (`shopfloor.jobs[job] = None`
  and `del shopfloor.jobs[job]` replace `.add` and `.remove`). `Server._idx` is removed.
- **Breaking:** intralogistics time parameters are managed bindings: `AGVType.load_time_fn` / `unload_time_fn`
  become `load_time` / `unload_time`, `Warehouse(pick_time_fn=, put_time_fn=)` become `pick_time=` / `put_time=`,
  `ChargingStation(recharge_fn=)` becomes `recharge_time=`. Each takes a number, a distribution or a callable (opaque).
  `warehouse.pick_time_fn` and `put_time_fn` attributes are gone. (`AGVType.recharge_fn` and `Battery(recharge_fn=)`
  are unchanged.)
- **Breaking:** `ProductionJob.planned_release_date` (and the values derived from it) is always a `float`; before, an
  integer routing time and allowance gave an `int`.
- Numeric changes (D58): sums of floating-point values that feed events, dispatching priorities, arrival rates, due
  dates, planned release dates, samplers, fleet paths and loads, or KPIs use `math.fsum`. Results can differ from 0.12
  in the last bit; CPython (all versions) and PyPy now agree for the tested workloads.
- `psp.remove(job=..., reason=...)`: release policies pass `released` or `postponed`; the job's location and the
  `psp.exited` event record it.
- `job.queued` carries `queue_length`, counted when the job joins and including the job itself (see Fixed).
- Log and KPI events consume the global event sequence number, so the `seq` values of domain events in a trace can have
  gaps. The digest excludes `seq`, and readers must not assume contiguity.
- Driving the simulation manually with `env.step()` requires `env.activate()` first; `env.run()` activates by itself.
- Three behaviors of the fleet collectors differ from the old ones: the destination inventory snapshot of
  `FleetTimeSeries` is taken when the order reaches `COMPLETED`, before the user delivery hooks run; orders are counted
  by the fleet that created them (an order created on fleet A and submitted to fleet B counts for A); and a direct
  `AGV.transition_to` call now adds a utilization point.
- `ShopFloorTimeSeries`, `ServerTimeSeries` and `CurrentWorkloadCollector` behave as the old collectors except:
  `CurrentWorkloadCollector` subtracts work when an operation completes, so a yielding after-operation hook that holds
  the server does not count finished work as remaining (this corrects the old series); `ServerTimeSeries.qt` has one
  point per time and records cancelled requests; `ut` starts at the collector's creation time. The EMAs are not part of
  `env.fingerprint()`; `ShopFloorKPIs` and `FleetKPIs` scalars are. `EMACollector.ema_tardy_jobs` counts late jobs outside
  the due-date window, while the `tardy_fraction` KPI counts every job with positive lateness.
- The advanced intralogistics example draws from `Environment(seed=42)` streams instead of a private
  `random.Random(42)`; its printed output changed (65 orders, average outbound fulfillment 219.6 s).
- Docs prose that claimed more than the regenerated numbers support was reworded in the benchmark shops, dispatching
  (stateless, focus), release (WIP, workload, triggers) and release-policy comparison pages.

### Fixed

- `job.queued`'s queue length (previously the logged `queue_length` of `Server.request`) was one too high: SimPy appends
  a request to the queue before the logging call ran. It now counts the waiting requests including the newcomer.
- Observer purity: `AGV.utilization()`, `state_percentage()` and `time_allocation()` no longer write to the AGV, and
  `TrafficManager.check_path` no longer logs, so reading them cannot change a run.
- Iteration order: `LayoutGraph` nodes, `check_path` conflict nodes and `ShopFloor.jobs` have a defined order, so runs
  are identical across `PYTHONHASHSEED` values.

### Removed

- `simulatte.logger` and loguru: `SimLogger`, the old `LogEvent`, `EventHistoryBuffer`, `SQLiteEventStore`,
  `env.logger`, `SimLogger.set_level` / `get_level`. Use `Environment(log_level=)`, the sinks (`env.sinks`,
  `env.log_history`, `env.log_db`) and `sink.enable_component` / `disable_component`.
- From `simulatte.shopfloor`: `MetricsCollector`, `EMAMetricsCollector`, `TimeSeriesCollector`,
  `DefaultTimeSeriesCollector`, `CurrentWorkLoadCollector`, `ShopFloor.metrics_collector`, `set_metrics_collector`,
  `time_series_collector`, `set_time_series_collector`. `Server(collect_time_series=)`; `Server.plot_qt` / `plot_ut` moved to `ServerTimeSeries`.
- From `simulatte.intralogistics`: `OrderMetricsCollector`, `EMAOrderMetrics`, `IntralogisticsTimeSeriesCollector`,
  `DefaultIntralogisticsCollector`, `FleetCoordinator(order_metrics_collector=, time_series_collector=)`.
- `FleetCoordinator.create_order(id=)`, `TrafficManager.place`, `Distribution.__call__`, the `*_time_fn` parameters
  listed above.

### Migrating from 0.12

**Seeds and randomness.** Pass a seed to the environment; draw from named streams.

```python
# 0.12
random.seed(42)
env = Environment()
service = lambda: random.expovariate(0.5)

# 0.13
env = Environment(seed=42)
service = Exponential(rate=0.5)       # managed: reproducible, recorded in the manifest
rng = env.rng("my-stream")            # for your own draws
```

**Logging.**

```python
# 0.12
env.logger.disable_component("Server")
rows = env.logger.query_sql(level="ERROR", component="Server")
raw = env.logger.execute_sql("SELECT ...")
if env.logger.db_enabled: ...
db_env_id = env.logger.env_id
SimLogger.set_level("DEBUG")

# 0.13
for sink in env.sinks:
    sink.disable_component("Server")
rows = env.log_db.query(level="ERROR", component="Server")  # Environment(log_db_path=...); returns LogEvents
raw = env.log_db.execute_sql("SELECT ...")
try:                                       # db_enabled: env.log_db raises RuntimeError without log_db_path
    env.log_db
except RuntimeError:
    ...
db_env_id = env.log_db.env_id
env = Environment(log_level="DEBUG")       # per environment
```

For built-in components, replace message-substring checks by subscribing to event classes (`JobQueued`,
`JobFinished`, ...) with `env.bus.subscribe`.

**Collectors.**

| 0.12 | 0.13 |
|---|---|
| `ShopFloor(metrics_collector=None)` | `ShopFloor(default_metrics=False)` |
| `shopfloor.metrics_collector.ema_makespan` | `shopfloor.metrics.ema_makespan` |
| `ShopFloor(collect_time_series=True)`, `shopfloor.time_series_collector.plot_wip()` | `ts = ShopFloorTimeSeries(shopfloor).attach(env)`, `ts.plot_wip()` |
| `Server(collect_time_series=True)`, `server.plot_qt()` | `ServerTimeSeries(server).attach(env).plot_qt()` |
| custom `MetricsCollector.record(job)` / `TimeSeriesCollector` | subclass `simulatte.kpi.Collector`, subscribe to `JobFinished`, `OperationCompleted`, ... |
| `FleetCoordinator(order_metrics_collector=EMAOrderMetrics(alpha=0.05))` | `default_metrics=False` and `OrderEMACollector(coordinator, alpha=0.05).attach(env)` |
| `FleetCoordinator(time_series_collector=DefaultIntralogisticsCollector())` | `FleetTimeSeries(coordinator).attach(env)` |

**Two systems in one environment.**

```python
# 0.12: ids were generated, two builders could share an environment
build_lumscor_system(env=env, ...)
build_slar_system(env=env, ...)

# 0.13: entity ids are unique; give each system a prefix
build_lumscor_system(env=env, prefix="a.", ...)
build_slar_system(env=env, prefix="b.", ...)
```

**Intralogistics.**

```python
# 0.12
AGVType(..., load_time_fn=lambda: 5.0)
Warehouse(..., pick_time_fn=lambda sku, qty: 2.0, put_time_fn=lambda sku, qty: 2.0)
ChargingStation(..., recharge_fn=fast)
order = coordinator.create_order(id="o-1", ...)
traffic.place(agv, node)
graph.nodes            # frozenset

# 0.13
AGVType(..., load_time=5.0)             # number, Uniform(...), or a callable (opaque)
Warehouse(..., pick_time=2.0, put_time=2.0)
ChargingStation(..., recharge_time=fast)
order = coordinator.create_order(...)   # order.id == "order-0", assigned at once
traffic.place_now(agv, node)
graph.nodes            # tuple, insertion order
```

Orders submitted before the first `env.run()` report `OrderStatus.PENDING_ACTIVATION` until activation. Name AGVs
without `agv-<n>` ids (or let the library generate them), and avoid node ids that equal another entity's name.

**Job and shop-floor details.** Replace `shopfloor.jobs.add(job)` / `.remove(job)` by item assignment and `del`;
read job ids as `job-<n>` strings; expect `planned_release_date` to be a `float`.

**New tooling you may want.** Record a run with `TraceRecorder(env, "run.simtrace")` before the first `env.run()` and
`env.close()` (or `with Environment(...) as env:`), then `Trace.open("run.simtrace")`. Compare runs with
`env.enable_digest()` and `env.fingerprint()`. See the [guide](docs/guides/events-and-traces.md).

## 0.12.0 — 2026-06-11

### Added

- `Scenario` value object: shop type (PJS/GFS/PFS), preset configurations, and
  derived arrival rate, with `build_floor`/`build_router` assembly as the
  de-duplicated core.
- Benchmark shop environments (PJS/GFS/PFS) accessible via Scenario presets.
- `SkuFamily` value object with per-family SKU mix and pluggable
  distributions/arrival process.
- `Distribution` protocol with built-in variates; `TruncatedErlang` distribution.
- `build_*_system` builders now return the wired policy.

### Changed

- **Breaking:** builder signatures are now keyword-only (`*, env, scenario`).
- **Breaking:** renamed the `Distribution` type alias to `Sampler`.
- **Breaking:** removed `truncated_2erlang` in favor of `TruncatedErlang`.
- Policies (`LumsCor`, `Slar`, `ConWIP`, `ContinuousRelease`, `slar_limit`) now
  self-wire in `__init__` and accept a scalar norm.
- `build_router` validates server count and documents the `arrival_process`
  mean=1/rate contract.
- Shared workload-norm validation and corrected-load fit check across policies.

### Fixed

- `logger`: defer finalizer handler removal to avoid loguru lock self-deadlock.
- examples: run all release-trigger systems at the Scenario-derived arrival rate.

### Docs

- Align installation page Python floor with `requires-python` (>=3.11).
- Numerous tutorial/example/api-reference updates for the Scenario refactor.

### CI

- Add `pytest-timeout` guard; force Agg backend in the Pyodide smoke test.

## 0.11.0 — 2026-06-04

### New

- Three new builder functions in `simulatte.builders`:
  `build_conwip_system`, `build_continuous_release_system`,
  `build_starvation_avoidance_system` — one-call setup for ConWIP,
  workload-controlled continuous release, and starvation-avoidance policies.
- PyPy 3.11 compatibility: simulatte now runs under
  [PyPy 3.11](https://pypy.org/) with identical results to CPython for a given
  seed. The CI matrix includes a PyPy 3.11 lane.
- In-browser runnable code blocks on simulatte.dev: examples tagged `# run`
  execute via Pyodide directly in the browser, with plot output rendered inline.

### Changed

- `requires-python` floor lowered from `>=3.12` to `>=3.11`, enabling use on
  CPython 3.11 and PyPy 3.11.
- `import simulatte` no longer eagerly imports `matplotlib`; the import is
  deferred to first use, reducing startup time.
- `specs/` and `plans/` directories excluded from the sdist.

### Fixed

- SQLite logger now finalizes cursors before committing, fixing a correctness
  issue on PyPy where deferred cursor cleanup caused errors.

### Documentation

- Documentation completely restructured into a layered, tab-first information
  architecture: Introduction, Guides, Tutorials, Examples, API Reference,
  Development. All example sections are now runnable in-browser.

## 0.10.0 — 2026-05-31

### New

- **Four dispatching rules** added to `simulatte.dispatching_rules`, extending
  the catalog with three new scheduling families:

  - **`work_in_next_queue`** (`dispatching_rules.work_content`) — Work In Next
    Queue (WINQ): orders by the total processing time queued at a job's next
    machine, feeding soon-to-starve downstream stations (queue-only; a job on
    its last operation → 0). Blackstone, Phillips & Hogg (1982, *IJPR* 20(1),
    27–45).
  - **`apparent_tardiness_cost(lookahead, *, avg_processing=None, weight=None)`**
    (`dispatching_rules.tardiness_cost`) — Apparent Tardiness Cost (ATC):
    `(w/p)·exp(−max(0, d−p−t)/(k·p̄))`. The average processing time `p̄`
    defaults to live computation from the server's queue, with an optional
    fixed override. Vepsäläinen & Morton (1987, *Management Science* 33(8),
    1035–1047).
  - **`cost_over_time(lookahead, *, weight=None)`**
    (`dispatching_rules.tardiness_cost`) — Cost Over Time (COVERT):
    `max(0, 1−max(0, slack)/(k·RPT))/p`, reducing to WSPT when tardy. Carroll
    (1965); job-shop form Russell, Dar-El & Taylor (1987, *IJPR* 25(10)).
  - **`raghu_rajendran(*, utilization=None)`** (`dispatching_rules.composite`) —
    Raghu & Rajendran (RR): `exp(u)·p + (s/RPT)·exp(−u)·p + WINQ`, a
    minimum-index composite weighted by the current machine's utilization (live
    by default, fixed override accepted); the raw slack `s` may be negative.
    Raghu & Rajendran (1993, *IJPE* 32(3), 301–313).

  ATC and COVERT return a negated cost index (higher cost → served first); WINQ
  and RR return their index directly. All are `(job, server) → float` callables
  usable as `Router(priority_policies=…)` or `ProductionJob(priority_policy=…)`,
  grouped into the new `dispatching_rules.work_content`,
  `dispatching_rules.tardiness_cost`, and `dispatching_rules.composite` modules.

## 0.9.0 — 2026-05-29

### New

- **`Draco` release policy** (`simulatte.policies.Draco`) — non-hierarchical WIP
  control merging release, authorization, and dispatching into a single
  per-server decision on each job completion. Implements Kasper, Land &
  Teunter (2023, *IJPE* 257, 108768) in full-DRACO configuration
  (`w^R=0.25, w^A=0.25, w^D=0.5`). `Draco.__init__` uses the
  active-construction pattern: pass `shopfloor`, `router`, and optionally
  `psp` and it self-wires all hooks on construction (mirroring `Slar`).
  Builder: `build_draco_system()`.

- **`Focus` dispatching rule** (`simulatte.dispatching_rules.Focus`) — five
  composable scheduling mechanisms (SPT, starvation avoidance, slack,
  pacing, WIP-balance entropy) unified into a single `(job, server) →
  float` priority callable. Implements Thürer, Land & Stevenson (2023,
  *Omega* 114, 102726). Builder: `build_focus_system()`.

### Changed

- **`Draco` constructor** is active (like `Slar`): pass `shopfloor`,
  `router`, and optionally `psp`, and hook registration happens on
  construction. No manual wiring required.

## 0.8.0 — 2026-05-28

### New

- **Dispatching-rule catalog** in `simulatte.dispatching_rules`. Adds six stateless rules — `shortest_processing_time`, `earliest_due_date`, `operational_due_date` (Land, Stevenson & Thürer, 2014), `modified_operational_due_date` (Baker & Kanet, 1983), `critical_ratio` (Berry & Rao, 1975) and `first_come_first_served` — plus a parameterized factory `slack_per_remaining_operation(allowance)` (Kanet, 1982) alongside the existing `planned_slack_time`. All are `(job, server) → float` callables usable as `Router(priority_policies=…)` or `ProductionJob(priority_policy=…)`. Rules are grouped by scheduling family across `dispatching_rules.processing`, `dispatching_rules.due_date`, and `dispatching_rules.slack`.
- **`BaseJob.unfinished_routing`** — property returning the servers whose operations have not yet completed (servers not yet exited), including the in-progress one. Used by `critical_ratio` and `slack_per_remaining_operation` to compute remaining processing time and the remaining-operation count.

### Changed (breaking)

- `builders.spt_priority_policy` removed; use `simulatte.dispatching_rules.shortest_processing_time` instead.
- `BaseJob.planned_slack_time` property removed; use `BaseJob.planned_slack_time_at(server, allowance=…)` or the `simulatte.dispatching_rules.planned_slack_time(allowance)` rule factory instead.

## 0.7.0 — 2026-05-26

### New

- **`SlarLimit` release policy** — SLAR with per-server workload-norm upper bounds, preventing superfluous load even when SLAR's base rule would allow a release. Derived from Thürer & Stevenson (2021), *Int. J. Production Economics*, 231, 107881. Available via `simulatte.policies.SlarLimit` and `build_slar_limit_system()`.
- **`simulatte.dispatching_rules` package** — new home for dispatching-rule callables. Introduces `planned_slack_time(allowance)`, a factory producing `(job, server) → float` PST priority callbacks (Land & Gaalman, 1998).

### Changed (breaking)

- **`Slar` constructor** now accepts `shopfloor`, `psp`, and optional `router` and self-registers its completion trigger and starvation-avoidance hook on construction. Previous pattern of `Slar(allowance_factor=k)` + manual `env.process(on_completion_trigger(…))` no longer works as-is; replace with `Slar(shopfloor=sf, psp=psp, router=router, allowance_factor=k)`.
- `Slar.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.
- `LumsCor.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.

### Documentation

- Auto-generated API reference page (`/reference`) powered by mkdocstrings.
- New tutorial section covering SLAR-Limit usage and dispatching rules.
- Updated builder comparison table in the release-control tutorial.

## 0.6.1 — 2026-05-26

### Fixed
- **Dynamic priorities in server queues.** `Server.sort_queue` now re-evaluates `job.priority_policy(job, server)` for every queued request before sorting, so priorities computed from `env.now`, external state, or runtime policy reassignment take effect immediately. Previously, `req.key` was frozen at request-construction time and never refreshed.
- **Automatic refresh at every dispatch decision.** `Server._trigger_put` is now overridden to call `sort_queue` automatically on both the new-arrival and release dispatch paths. Dynamic priority changes no longer require an explicit `sort_queue()` call from user code.

### Documentation
- Added a *Dynamic priorities* tutorial section to the release-control-and-dispatching guide, covering time-dependent policies, runtime policy reassignment, mutable external state, the `priority_policy` purity contract, and cost model.
- Updated docstrings on `Server`, `ServerPriorityRequest`, and `Server.sort_queue` to describe the snapshot-vs-live distinction between `req.priority` and `req.key`, and the mechanism of the automatic refresh.

## 0.6.0 — 2026-05-10

### Added
- **Intralogistics subsystem** (`simulatte.intralogistics`): full warehouse-to-warehouse material transport via AGV fleets, including:
  - `LayoutGraph` with `Node`/`Arc` and Dijkstra/A* pathfinding
  - `Warehouse` with per-SKU inventory, finite pick/put slots, and deadlock-safe operations
  - `AGV` with state machine, trapezoidal speed profiles, and battery lifecycle
  - `FleetCoordinator` orchestrating dispatch, travel, pick, deliver, reposition, and charge
  - `TrafficManager` protocol with `FreeTrafficManager` and `ResourceBasedTrafficManager` (node capacity enforcement and deadlock resolution)
  - `ChargingStation` (recharge and swap) and `ParkingArea`
  - Pluggable policies: `NearestIdleStrategy`, `RoundRobinStrategy`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`
  - `EMAOrderMetrics` and `DefaultIntralogisticsCollector` with `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`
  - `build_simple_system()` builder for quick setup
- **ConWIP release policy** (`simulatte.policies.conwip`): Constant Work-In-Process release with shop-wide job count cap and EDD selection
- **ContinuousRelease policy** (`simulatte.policies.continuous_release`): workload-controlled continuous release using corrected aggregate load norms
- Three progressive intralogistics examples: simple, intermediate (manufacturing plant floor), and advanced (multi-warehouse distribution hub)
- AI coding agent skill for intralogistics (`simulatte-intralogistics`)
- Intralogistics documentation section with overview and examples walkthrough

### Removed
- Experimental AGV, Warehouse, and MaterialCoordinator modules (`simulatte.experimental.agv`, `simulatte.experimental.warehouse`, `simulatte.experimental.materials`, `simulatte.experimental.builders`, `simulatte.experimental.job`, `simulatte.experimental.typing`) — replaced by the `simulatte.intralogistics` subsystem

### Fixed
- Fleet pending-queue starvation: retry counter now increments even when capable AGVs exist but none are idle
- Default `pending_retry_delay` changed from 0.001 to 1.0 for realistic retry behavior
- `DefaultIntralogisticsCollector` no longer accesses private `_pending_queue` (uses `pending_count` property)

## 0.5.0 — 2026-04-29

- Add `SimulatteEnv` Gymnasium wrapper for RL integration (`simulatte.experimental.gymnasium`)
- Improve web documentation structure and content
- Bump `actions/deploy-pages` from 4 to 5

## 0.4.0 — 2026-04-29

- Add `is_idle` and `current_jobs` properties to `Server`
- Add `release()`, `jobs_starting_at()`, and `on_arrival()` callback to `PSP`
- Support sync callbacks in `OperationHook` protocol
- Add post-init hook registration: `on_before_operation()` / `on_after_operation()`
- Add `on_processing_end()` callback to `ShopFloor`
- Add `Dispatcher` protocol and `attach_dispatcher()` for one-call hook wiring
- Rewrite `starvation_avoidance` as a `psp.on_arrival()` callback
- Bump CI dependencies (actions/download-artifact, upload-artifact, upload-pages-artifact, configure-pages, codecov)

## 0.3.0 — 2026-04-28

- feat: add simulatte:dev agent skill for Claude Code integration
- fix: SLAR policy refactor and type safety improvements (#4)
