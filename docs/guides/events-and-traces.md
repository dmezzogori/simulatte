# Events, traces and KPIs

Every state change in a Simulatte run is a typed event on the environment's event bus. Logging, KPI collectors, the semantic digest and the trace recorder are all subscribers of that bus. This guide explains the pieces and how they fit together; the [Logging tutorial](../tutorials/logging.md) and [ShopFloor extensibility](../tutorials/shopfloor-extensibility.md) show the hands-on side, and the [Events & traces API](../api/events-and-traces.md) lists the classes.

The stable entry points are exported from the top-level package:

```python
from simulatte import (
    Collector,
    DomainEvent,
    Environment,
    Event,
    KPI,
    ObserverEvent,
    Provenance,
    Runner,
    Trace,
    TraceRecorder,
)
```

Component classes (`Server`, `ShopFloor`, `FleetCoordinator` and so on) stay importable from their modules.

## 1) The event bus

An event is a frozen dataclass stamped with the simulation time `t` and a per-environment sequence number `seq`. There are two families:

- **Domain events** (`DomainEvent`) describe the trajectory of the simulation: a job joined a queue, an operation started, an AGV changed state. They can carry **deltas**, the changes they make to the state of entities (`set`, `insert`, `remove`, `move`, `put`, `delete`, `create`, `retire`). Applying the deltas of all events to the initial state reproduces the state of the run at any point.
- **Observer events** (`ObserverEvent`) are emitted for observers: `log` records and `kpi.sample` points. They never carry deltas and are not part of the trajectory.

Subscribe a handler to one or more event classes, to `"*"` (every domain event, including types registered later) or to `"**"` (everything). Delivery is synchronous, in subscription order:

```python
from simulatte import Environment
from simulatte.builders import build_immediate_release_system
from simulatte.scenario import Scenario
from simulatte.shopfloor import JobFinished

env = Environment(seed=7)
finished = []
subscription = env.bus.subscribe(lambda e: finished.append((e.t, e.job, e.lateness)), (JobFinished,))
build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
env.run(until=200)

print(len(finished), finished[0])
subscription.cancel()  # stop listening
```

A subscriber observes: it must not emit domain events, schedule SimPy events or draw from `env.rng`. Emitting a domain event from a subscriber raises; `Environment(debug=True)` additionally rejects subscribers that schedule events or draw random numbers.

### Guarding emission with `env.wants`

Components build an event only when somebody listens. `env.wants(EventClass)` is a constant-time lookup, so the guard costs next to nothing on runs without subscribers:

```python
from simulatte import DomainEvent
from simulatte.events import event_type


@event_type("demo.maintenance_started")
class MaintenanceStarted(DomainEvent):
    server: str
    duration: float


if env.wants(MaintenanceStarted):
    env.emit(MaintenanceStarted(server="wc-0", duration=3.0))
```

Build the payload from data you already have; building an event must not call user policies or callbacks, because attaching an observer must never change what the simulation does. `env.emit` stamps `t` and `seq`; an instance that already has a `seq` is rejected.

### The event catalog

Every `@event_type` is registered in a global catalog with its payload fields, their wire types and the entity fields its deltas may touch. The built-in types are:

| Area | Types | Defined in |
|---|---|---|
| Lifecycle | `entity.created`, `entity.retired` | `simulatte.entities` |
| Pre-shop pool | `psp.entered`, `psp.exited` | `simulatte.psp` |
| Shop floor | `shopfloor.entered`, `operation.started`, `operation.completed`, `shopfloor.wip_updated`, `job.finished` | `simulatte.shopfloor` |
| Server | `job.queued`, `job.granted`, `job.queue_left`, `job.released`, `server.queue_reordered`, `server.work_credited` | `simulatte.server` |
| Release policies | `policy.decision` | `simulatte.policies` |
| Fleet and orders | `fleet.agv_added`, `fleet.pending_changed`, `order.status_changed`, `order.assigned`, `order.unassigned` | `simulatte.intralogistics.events` |
| AGVs | `agv.state_changed`, `agv.placed`, `agv.move_started`, `agv.move_ended`, `agv.move_interrupted`, `agv.load_changed`, `agv.battery_changed`, `agv.stranded` | `simulatte.intralogistics.events` |
| Traffic | `traffic.reserved`, `traffic.released`, `traffic.wait_started`, `traffic.wait_ended` | `simulatte.intralogistics.events` |
| Facilities | `warehouse.inventory_changed`, `warehouse.slot_changed`, `charging.started`, `charging.ended`, `charging.pool_changed`, `parking.entered`, `parking.left` | `simulatte.intralogistics.events` |
| Observer events | `log`, `kpi.sample` | `simulatte.events` |

The [Logging tutorial](../tutorials/logging.md#3-built-in-events-and-component-logs) lists the payload of each production and intralogistics event.

## 2) Entities and ids

A component that appears in events is an **entity** with a string id. Ids are unique per environment: jobs are `job-0`, `job-1`, ..., servers `server-0` unless you pass `name=`, and the builders name theirs `wc-<i>`, `psp`, `shopfloor` and `router`. Pass `prefix=` to a builder to host several systems in one environment:

```python
env = Environment(seed=1)
a = build_immediate_release_system(env=env, scenario=Scenario(n_servers=2), prefix="a.")
b = build_immediate_release_system(env=env, scenario=Scenario(n_servers=2), prefix="b.")
print([s.id for s in a.servers], [s.id for s in b.servers])  # ['a.wc-0', 'a.wc-1'] ['b.wc-0', 'b.wc-1']
```

Names must not contain `/` or a NUL character, and must not look like a generated id (`<kind>-<n>`, for example `agv-0`); duplicates raise. `env.entities` is the registry: `env.entities.get(id)` returns an entity, `env.entities.snapshot()` the current state of all live entities. A job retires from the live registry once it has finished and all completion callbacks ran; an order retires at a terminal status. Your own references (`shopfloor.jobs_done`) are unaffected.

## 3) Seeds and RNG streams

`Environment(seed=...)` takes an integer in `[0, 2**63)`; `None` draws a seed from `os.urandom` and `env.seed` returns it, so any run can be reproduced. Randomness comes from **named streams**: `env.rng(name)` returns a `random.Random` whose seed is derived from the environment seed and the name, so streams are independent and adding a new stream never changes the others.

```python
env = Environment(seed=1)
print(env.rng("demo").random())  # 0.2604417134493162
print(env.rng("demo") is env.rng("demo"))  # True, cached per name
```

The library never touches Python's global `random` module, and `Runner` no longer seeds it: user code that draws from the global generator is not reproducible across runs. Draw from `env.rng("my-stream")` instead.

Distributions (`Exponential`, `Erlang`, `Uniform`, ...) and routings are *descriptions*: components bind them to named streams (`<router>/interarrival`, `<router>/service/<sku>/<server>`, `<agv id>/load`, `<warehouse>/pick`, ...). Numbers are managed too. A plain callable is accepted anywhere a sampler is, but it is **opaque**: the library cannot reproduce its draws, so its owner goes to `env.opaque_sampler_owners` and the run manifest is marked incomplete. Use `env.bind(value, kind="scalar", stream=..., owner=...)` to resolve a number or distribution in your own components; `kind` is `"scalar"` (`() -> float`), `"routing"` (`() -> Sequence[Server]`) or `"contextual"` (`(*context) -> float`).

## 4) Preparation and activation

An environment is **prepared** until it is activated. Activation runs the registered initializers, captures the initial state and starts the semantic projection. `env.run()` activates on first use; call `env.activate()` yourself to inspect the activated system first, and use `env.on_activate(fn)` to register an initializer (a plain function that must not schedule events or advance time).

Components can defer commands until activation. `FleetCoordinator.submit` and `cancel` called before activation are queued, and the order reports `OrderStatus.PENDING_ACTIVATION` until the queue drains at time 0, in call order, before any scheduled event runs. `create_order` is not deferred: the order has its id at once. A `submit` followed by a `cancel` before activation therefore dispatches the order at time 0 and then cancels it, with the full event trail of both steps; this is by design.

```python
from simulatte.intralogistics import SKU, build_simple_system

env = Environment(seed=1)
coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env)
order = coordinator.create_order(sku=SKU("A", weight=1.0, volume=0.1), quantity=5, origin=wh_a, destination=wh_b)
coordinator.submit(order)
print(order.id, order.status)  # order-0 OrderStatus.PENDING_ACTIVATION
env.activate()
print(order.status)  # OrderStatus.DISPATCHED
```

If you drive the simulation manually with `env.step()` instead of `env.run()`, call `env.activate()` first; `step()` does not activate.

## 5) Digest, fingerprint and manifest

The **semantic digest** is a BLAKE2b hash of the initial state and of every domain event (time, payload and deltas, without display-only fields such as labels). Two runs have the same digest exactly when they have the same trajectory, whatever else observes them, and across `PYTHONHASHSEED` values. Enable it with `env.enable_digest()` (before activation); a `TraceRecorder` enables it too.

`env.fingerprint()` returns the digest and the KPI scalars of every attached collector, namespaced by scope. The **manifest** records what is needed to reproduce a run: Simulatte version, Python and platform, dependencies, the RNG derivation, the seed (as a decimal string), the time unit and warm-up and, once a run finished, the stopping policy. A manifest is **complete** when nothing in it is unavailable: pass a `Provenance` with hashes of your model, source, inputs and dependencies, and use no opaque sampler.

```python
from simulatte import Provenance

provenance = Provenance(model="sha256:...", source="sha256:...", inputs="sha256:...", dependencies="sha256:...")
env = Environment(seed=7, time_unit="minute", provenance=provenance)
env.enable_digest()
build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
env.run(until=200)

print(env.fingerprint().digest)  # hex string; equal for equal trajectories
print(env.manifest().complete)  # True
```

`run(until=<simpy.Event>)` makes the manifest incomplete, because the stop cannot be described by it. CPython and PyPy give the same digest for the same seeded run (float sums are exactly rounded), but the library only guarantees it for the tested workloads; the manifest records the interpreter so a mismatch can be explained.

## 6) Recording a trace

`TraceRecorder` writes a run to a trace file. Attach it before activation:

```python
from simulatte import Environment, TraceRecorder

env = Environment(seed=7)
build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
TraceRecorder(env, "run.simtrace", level="full")
env.run(until=200)
env.close()  # seals the last chunk and writes the footer
```

Using the environment as a context manager (`with Environment(...) as env:`) closes it for you. The recording level is either:

| Level | Records | Use |
|---|---|---|
| `"full"` (default) | header, initial state, every domain event in compressed chunks, KPI records, footer | replay, seeking, verification |
| `"kpi"` | header, initial state, KPI records, footer | cheap summaries; the trace cannot be verified |

KPI sample buffers publish within the same wall-clock latency limit, even when no more simulation events arrive. Samples emitted before activation wait until the initial-state record.

Events are grouped into chunks sealed at 10,000 events, 1 MiB, 1 second of wall-clock time or an optional simulated-time window (`ChunkLimits`). A writer thread compresses and writes them; if it falls behind, the simulation waits (the pending bytes are bounded), so memory stays flat. The first such wait logs a warning, and that log event takes a sequence number, so two runs with the same seed can have different `(t, seq)` cursors when only one of them waited: compare traces by digest, not by cursor. The recorder never changes the trajectory: the digest is identical with or without it. Several `env.run()` calls continue the same trace. The footer records how the run ended (`completed`, `cancelled` for a keyboard interrupt, `failed` if `run` raised).

## 7) Reading and verifying a trace

```python
from simulatte import Trace

trace = Trace.open("run.simtrace")
print(trace.level, trace.outcome, trace.truncated)  # full completed False
start, end = trace.cursor_range  # cursors are (t, seq) pairs
print(trace.manifest["seed"], trace.fingerprint.digest[:12])

state = trace.state_at((100.0, 10**9))  # entity states after all events at t <= 100
print(state["wc-0"]["queue"])

for event in trace.events(start, (1.0, 10**9)):  # events with start < (t, seq) <= end
    print(event.seq, event.type, event.t)
```

- `state_at(cursor)` returns `{entity id: state}` and seeks directly to the right chunk, so jumping around a long run is cheap. The activation cursor `(t, -1)` is the initial state.
- `events(start, end)` yields the recorded events between two cursors.
- `kpi_declarations` maps `scope/name` to immutable metadata: unit, kind, description and observation settings. Older traces return an empty map.
- `kpis()` returns the scalars and `kpi_series()` the samples of the KPI records.
- `check()` validates the container (checksums, index, limits) and raises `TraceCorrupted` on damage.
- `verify()` recomputes the digest from the initial state and the events and compares it with the footer: `True`, `False`, or `"not_verifiable"` for `kpi` traces and traces without a footer.

A trace cut short by a crash still opens: the reader ignores the incomplete tail and shows every committed chunk. `trace.outcome` is `None` because no footer was written, which is how to recognise an unfinished run. `trace.truncated` may be `True`: it is only set when the last record was cut mid-write, so a run that died between records, or that never called `close()`, opens with `truncated` False. Damage in the middle of the file raises `TraceCorrupted`. `ReaderLimits` bounds record, chunk, nesting and collection sizes, so a hostile file cannot exhaust memory.

The format is a documented container (MessagePack records with CRC32 checksums and zlib-compressed chunks), and the repository ships a TypeScript reader, `@simulatte/trace` in `studio/`, that replays the same states in the browser.

## 8) KPIs and collectors

A **collector** subscribes to the bus, keeps its own state and exposes results as attributes. Each is bound to an owner entity (a shop floor, a server or a fleet coordinator) and sees only that owner's events, so two systems in one environment never mix results. Scalars and samples are keyed `"<scope id>/<kpi name>"`.

The built-in collectors are `EMACollector` (attached by default to every `ShopFloor` as `shopfloor.metrics`), `ShopFloorTimeSeries`, `CurrentWorkloadCollector`, `ServerTimeSeries` and `ShopFloorKPIs` in `simulatte.collectors`, and `OrderEMACollector` (default on a `FleetCoordinator`), `FleetTimeSeries` and `FleetKPIs` in `simulatte.intralogistics`. `env.collectors` lists the attached ones. Every production `build_*_system` accepts `default_metrics=False`; `FleetCoordinator(ema_alpha=0.05)` changes its default order EMA smoothing factor.

Window-aware KPIs honour the warm-up: set it before activation with `env.configure_kpis(warmup=...)`. Job KPIs average the jobs completed after the warm-up; time-weighted KPIs (utilization, jobs in the system) integrate from the start of the window and are clipped at its end.

```python
from simulatte.collectors import ShopFloorKPIs

env = Environment(seed=7)
env.configure_kpis(warmup=50.0)
system = build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
kpis = ShopFloorKPIs(system.shop_floor).attach(env)
env.run(until=200)

print(kpis.scalars()["shopfloor/throughput"])
print(env.fingerprint().kpis)  # all scalars of all collectors
```

Write your own by subclassing `Collector` and declaring the KPIs it produces. `observe` feeds a scalar aggregate (mean, sum, count, min, max); `sample` emits a point of a `series` KPI as a `kpi.sample` event, which a trace records:

```python
from typing import ClassVar

from simulatte import Collector, KPI
from simulatte.shopfloor import JobFinished


class MaxLateness(Collector):
    kpis: ClassVar = (KPI("max_lateness", unit="time", aggregation="max", description="Largest lateness"),)
    subscribes: ClassVar = (JobFinished,)
    scope_field: ClassVar = "shopfloor"  # only the owner's events

    def on_event(self, event) -> None:
        self.observe("max_lateness", event.lateness)


env = Environment(seed=7)
system = build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
MaxLateness(system.shop_floor).attach(env)
env.run(until=200)
print(env.fingerprint().kpis)  # {'shopfloor/max_lateness': ...}
```

Note that "tardy" is not the same thing in every collector. `EMACollector.ema_tardy_jobs` counts jobs that finish late *outside* the due-date window of 7 time units around the due date (jobs inside the window are counted separately), whereas `ShopFloorKPIs` `tardy_fraction` counts every job with positive lateness.

## 9) Logging sinks

`env.info(...)`, `env.warning(...)` and friends emit `log` events; **sinks** write them out. The default sinks (stderr or `log_file`, an in-memory history, and an optional SQLite database) are configured through the `log_*` arguments of `Environment`, and `simulatte.logsinks` provides `TextSink`, `JsonSink`, `SQLiteSink` and `HistorySink` to attach more. At `DEBUG` the sinks also render domain events, which makes every emitting site build its event, so a debug run is slower. `Environment(debug=True)` requires the `extra` values of log calls to be wire values (numbers, strings, booleans, `None`, lists and string-keyed maps) and records an immutable copy of them (lists become tuples). See the [Logging tutorial](../tutorials/logging.md) for the details.

Log calls below every attached sink's level are skipped without constructing an event or consuming a sequence number. An explicit bus subscription to log events (or `"**"`) still receives every level. Python evaluates arguments before the call, so guard expensive message construction with `env.bus.wants_log(10)` for DEBUG.

`env.close()` ends observation: subsequent emissions, including cancellation events from generator finalizers, are ignored. Close individual sinks when you only want to stop logging during a run. If environment setup fails, any sinks already opened are closed.

## 10) Debug mode

`Environment(debug=True)` validates every emitted event against the catalog (payload types, nullability, the fields its deltas may touch) and against the state schemas of the entities, rejects mutable containers (lists, dicts, sets) anywhere in payloads and deltas, since event contents must be immutable (tuples and `FrozenMap`), rejects subscribers that schedule events or draw random numbers, and refuses to bind two different values to one RNG stream. It is slower and meant for tests and model development; it never changes results.
