# SP1: events and trace (design)

- **Status:** draft for adversarial review
- **Date:** 2026-10-08
- **Release:** 0.13
- **Parent:** [`2026-10-08-studio-global-design.md`](2026-10-08-studio-global-design.md) (contracts C1.1–C1.10, §7, §8). This spec refines those contracts; it does not change them. Where it seems to, the global spec wins and this document is wrong.
- **Decisions:** [`studio-decisions.md`](studio-decisions.md), in particular D10–D12, D23, D25, D28, D31, D39, D44, D46, D48–D53.
- **Code inventory:** [`research/2026-10-08-sp1-inventory.md`](research/2026-10-08-sp1-inventory.md). References such as "inventory §2" point there.

## 1. Scope

SP1 delivers the observable core that every later sub-project builds on:

1. Entity identity, registration and lifecycle (C1.1).
2. Typed events with state deltas, the event catalog, and the event bus (C1.2, C1.3).
3. Logging rebuilt on the bus, without loguru (C1.4, D50, D51).
4. Per-environment RNG streams and samplers (C1.5).
5. Semantic projection, digest, provenance and run manifest (C1.6).
6. Trace writer and Python reader, plus a minimal TypeScript conformance reader (C1.7, D44, D53).
7. KPI declarations and bus collectors replacing the old collector protocols (C1.8, D52).
8. Preparation and activation (C1.10, D46).
9. Benchmarks and the CI overhead gate (C1.9).
10. The fixes the audits require: observer purity, iteration order, the `queue_length` off-by-one (D49).

**Out of scope:** layout and positions (SP2), viewer (SP3), execution and studio (SP4), experiments (SP5). SP1 records no coordinates except the existing `Node.x`/`Node.y` that AGV motion already uses.

## 2. Delivery: gates

SP1 ships as one release, built in four gated stages (D44). Each stage has acceptance criteria; a stage that fails them sends the design back to review before the next stage starts.

| Gate | Content | Acceptance |
|---|---|---|
| G1. Vertical slice | Entities, bus, event catalog machinery, deltas, digest, activation, trace writer and Python reader, implemented for **Server**, **ProductionJob**, **ShopFloor**, **PreShopPool** and **Router** only, and the `queue_length` fix | A reference job shop records a `full` trace; Python seek equals uninterrupted replay at every chunk boundary; digest identical across observer configurations and across `PYTHONHASHSEED` values |
| G2. TypeScript conformance | `studio/` workspace with one package, `@simulatte/trace`, that decodes G1 fixture traces and replays state | TS replay state equals Python replay state on all fixtures (compared as canonical JSON) |
| G3. Budgets | Benchmarks of §11 on the slice | No-subscriber overhead within budget; `full` trace size and seek time measured and reported against the C1.7 hypotheses |
| G4. Migration | All remaining components (policies, intralogistics), logging rebuild, collectors, RNG migration of all distributions, docs, examples, CHANGELOG | Full test suite green on CPython 3.12–3.14 and PyPy; docs build; benchmarks in budget |

## 3. Modules and public surface

New modules (names fixed here; internal structure is the plan's choice):

| Module | Contents |
|---|---|
| `simulatte.entities` | `Entity` base, registry, id rules, lifecycle, state schema |
| `simulatte.events` | `Event` base classes, `@event_type`, catalog, deltas, `EventBus` |
| `simulatte.rng` | stream derivation, `Sampler`, unmanaged-sampler detection |
| `simulatte.digest` | semantic projection, canonical encoding, digest subscriber |
| `simulatte.provenance` | `Provenance`, `RunManifest`, `UNAVAILABLE` |
| `simulatte.trace` (package) | container format, writer (`TraceRecorder`), reader (`Trace`), msgpack codec |
| `simulatte.kpi` | `KPI` declaration, `Collector` base, window and estimand machinery |
| `simulatte.logsinks` | text, JSON, SQLite and history sinks (replaces `simulatte.logger`) |

All of them are importable from intralogistics modules: they depend only on `simulatte.environment` and each other, never on production modules. The import audit in `tests/intralogistics/test_import_audit.py` therefore stays as it is; D48 allows relaxing it, but SP1 does not need to.

`simulatte/__init__.py` (empty today) exports the stable entry points (D48): `Environment`, `Runner`, `Provenance`, `TraceRecorder`, `Trace`, `KPI`, `Collector`, and the event base classes. Component classes stay importable from their modules; the top-level exports are additive.

`simulatte.logger` is removed (D50). `loguru` is removed from the dependencies; `msgpack` is added (D53).

## 4. Environment

```python
Environment(
    *,
    seed: int | None = None,          # None: a random seed is drawn from os.urandom and recorded
    time_unit: str | None = None,     # "s", "min", "h", ... or None for unitless
    provenance: Provenance | None = None,
    log_level: str = "INFO",
    log_file: str | Path | None = None,   # None: stderr
    log_format: Literal["text", "json"] = "text",
    log_history_size: int = 1000,
    log_db_path: str | Path | None = None,
)
```

- The `log_*` arguments are conveniences that attach the corresponding sinks (§7); they keep today's names so existing call sites (18 in tests, 5 in docs, inventory §8) keep working. `log_level` is new and per environment; the class-level `SimLogger.set_level`/`get_level` disappears.
- New members: `env.seed`, `env.time_unit`, `env.rng(name)`, `env.entities`, `env.bus`, `env.emit(event)`, `env.wants(event_type)`, `env.activate()`, `env.on_activate(fn)`, `env.activated`, `env.manifest()`, `env.log_history`, and the logging methods `debug`/`info`/`warning`/`error`.
- `env.run()` calls `env.activate()` on its first call if needed.
- `env.logger` (the `SimLogger`) is removed; component filtering moves to sinks (§7).

## 5. Entities

### 5.1 Base and registration

- `Entity` is a mixin with `kind: ClassVar[str]`, `id: str`, `label: str` and `state_schema: ClassVar[StateSchema]`. Attachment is explicit: `env.entities.attach(obj, name=None, label=None)`, called by each component's constructor (or attachment method) after its fields exist. Attachment emits `entity.created` with the entity's initial state as a delta.
- `env.entities.retire(obj)` emits `entity.retired` and removes the entity from the live registry. Retired entities stay resolvable by id for inspection through `env.entities.get(id, include_retired=True)` only while the Python object is alive; the registry keeps no strong reference to retired entities.
- **Ids** follow C1.1: unique across kinds; `name` gives the id; otherwise `f"{kind}-{n}"` with a per-kind counter; user names matching `^<any registered kind>-\d+$` raise; duplicates raise.
- **Builder prefixes.** Every `build_*_system` function gains `prefix: str = ""`; it passes `name=f"{prefix}{default}"` to every entity it creates (for example `wc-0`… for servers, `psp`, `router`). The shared mutable default `scenario: Scenario = Scenario()` is replaced by `None` with a fresh instance inside.

### 5.2 Kinds in SP1

| Kind | Class | Id source | State schema (viewer-visible fields) |
|---|---|---|---|
| `server` | `Server` | `name=` (new) or `server-n`; `_idx` removed (tests at inventory §1 move to `id`) | `capacity`, `users` (job ids, ordered), `queue` (job ids, ordered), `worked_time` |
| `job` | `ProductionJob` | `job-n` | `sku`, `routing` (server ids), `op_index`, `location` (`psp`, `queue:<server>`, `server:<server>`, `transit`, `done`), `due_date`, `created_at`, `finished_at` |
| `psp` | `PreShopPool` | `name=` or `psp-n` | `jobs` (ordered) |
| `shopfloor` | `ShopFloor` | `name=` or `shopfloor-n` | `wip` (map server id → load), `jobs_in_system` |
| `router` | `Router` | `name=` or `router-n` | none (a source) |
| `agv` | `AGV` | `agv_id=` (kept as the name parameter) or `agv-n` | `node`, `state`, `battery`, `load` (sku, quantity or null), `order` |
| `order` | `TransferOrder` | `order-n`, assigned when attached by `FleetCoordinator.create_order` or `submit`; the `uuid4` default factory is removed | `status`, `sku`, `quantity`, `origin`, `destination`, `agv`, timestamps |
| `fleet` | `FleetCoordinator` | `name=` or `fleet-n` | `pending` (order ids, ordered) |
| `warehouse` | `Warehouse` | `name` (already required) | `inventory` (map sku → level), `slots_in_use` |
| `charging_station` | `ChargingStation` | `name` | `slots_in_use`, `swap_pool` |
| `parking_area` | `ParkingArea` | `name` | `parked` (agv ids) |
| `node` | `Node` (definition) | `Node.id` | `occupant` (agv id or null) |

Nodes are definitions (C1.1). They become entities when a `FleetCoordinator` is constructed with a graph: the coordinator attaches every node of its graph, in sorted id order. A node id that collides with another entity's id raises. Traffic managers are not entities; node occupancy is node state.

### 5.3 Iteration-order fixes

From inventory §7, fixed in SP1 so that the semantic projection does not depend on `PYTHONHASHSEED`:

- `LayoutGraph._nodes` becomes an insertion-ordered dict; `.nodes` returns nodes in insertion order (a tuple); the frozenset API is replaced (call sites adjusted).
- `ResourceBasedTrafficManager.check_path` builds `conflict_nodes` in path order without passing through a set.
- `ShopFloor.jobs` becomes an insertion-ordered dict keyed by job (it is used as a set; membership and removal keep O(1)).

A test runs the reference models in fresh processes under several `PYTHONHASHSEED` values and requires identical canonical content.

## 6. Events

### 6.1 Classes

- `Event` is the base: frozen, slotted dataclass with `t: float`, `seq: int`, and `deltas: Deltas`. Two subclasses partition every event:
  - `DomainEvent`: emitted by simulation components; carries `ordinal: int` (the domain ordinal, C1.6); part of the semantic projection.
  - `ObserverEvent`: emitted by observers and diagnostics (`log`, `kpi.sample`, anything emitted from a subscriber); never in the projection; `deltas` is always empty.
- `@event_type("job.queued", version=1)` registers a class in the catalog with its payload fields (the dataclass fields other than the base fields), their wire types and nullability, and the entity kinds and state fields its deltas may touch. Registering the same name twice with different definitions raises.
- Payload fields hold wire values only (C1.2): ids instead of objects, tuples instead of lists, `frozendict`-style read-only mappings instead of dicts. Debug mode (`SIMULATTE_DEBUG=1` or `Environment(debug=True)` in tests) validates every emitted event against its catalog entry.
- `t` and `seq` are assigned by `env.emit`; the emitting site never sets them. `ordinal` is assigned by `env.emit` for domain events.

### 6.2 Deltas

`Deltas` is an ordered tuple of operations, each addressing `(entity_id, field)`:

| Operation | Meaning |
|---|---|
| `set(entity, field, value)` | replace a scalar or a whole small field |
| `insert(entity, field, index, value)` | insert into an ordered collection |
| `remove(entity, field, value)` | remove by value from a collection |
| `move(entity, field, value, index)` | move an element within an ordered collection |
| `put(entity, field, key, value)` / `delete(entity, field, key)` | map updates |
| `create(entity, kind, state)` / `retire(entity)` | lifecycle |

Collections are never re-sent whole (B19). A helper builds deltas at emitting sites (`d = Deltas.build(); d.set(job.id, "location", ...)`). The Python reader and the TypeScript reader apply the same operations; the conformance suite (G2) is the arbiter.

**Server queue order.** `Server.sort_queue` re-sorts requests whenever a request is put (inventory §2). It now compares the order before and after sorting and, when it changed, emits `server.queue_reordered` with `move` operations for the elements that moved. When nothing listens, the comparison is skipped.

### 6.3 Core catalog

Event names are stable strings. Payload fields are listed without the base fields; deltas are summarized. The plan may add fields; removing or renaming one after release needs a version bump.

**Lifecycle (all kinds)**

| Type | Payload | Deltas |
|---|---|---|
| `entity.created` | `kind`, `label` | `create` |
| `entity.retired` | `kind` | `retire` |

**Production** (the phases follow `ShopFloor.main`, inventory §2; one event per phase, C1.2 transition boundaries)

| Type | Emitted at | Payload | Deltas |
|---|---|---|---|
| `job.created` | `Router.generate_job`, or job construction outside a router | `sku`, `routing`, `processing_times`, `due_date` | `create` |
| `psp.entered` | `PreShopPool.add` after append | `job`, `psp`, `position` | psp `jobs` insert; job `location` |
| `psp.exited` | `PreShopPool.remove` | `job`, `psp`, `reason` (`released`, `postponed`, `removed`) | psp `jobs` remove; job `location` (`transit` for postponed releases) |
| `shopfloor.entered` | `ShopFloor.add` | `job`, `shopfloor` | shopfloor `jobs_in_system`, `wip` puts |
| `job.queued` | `Server.request`, after the request is constructed | `job`, `server`, `priority`, `queue_length` = `len(server.queue)` after insertion (0 when granted at once) **(D49 fix)** | server `queue` insert (if waiting); job `location` |
| `job.granted` | after `yield request` in `ShopFloor.main` | `job`, `server`, `waited` | server `queue` remove, `users` insert; job `location` |
| `operation.started` | after before-hooks and material ensure, just before the processing timeout | `job`, `server`, `op_index`, `processing_time`, `planned_end` | job `op_index` |
| `operation.completed` | after the timeout and `worked_time` credit, before WIP update | `job`, `server`, `op_index` | server `worked_time` |
| `shopfloor.wip_updated` | after `wip_strategy.complete_operation` | `shopfloor`, `changes` | shopfloor `wip` puts |
| `job.released` | in `Server.release` | `job`, `server` | server `users` remove; job `location` = `transit` or next |
| `job.finished` | job completion block | `job`, `makespan`, `lateness`, `total_queue_time` | job `location` = `done`, `finished_at`; shopfloor `jobs_in_system` |
| `policy.decision` | release policies and Draco when they pick or force a job | `policy`, `job`, `action` (`release`, `force_pin`, `postpone`) | none |

`ShopFloor.main` hook calls (before/after operation hooks, processing-end callbacks) stay where they are; events are emitted between them so that each cursor is a consistent state.

**Intralogistics**

| Type | Emitted at | Payload | Deltas |
|---|---|---|---|
| `order.created` | `create_order` or attachment at `submit` | `sku`, `quantity`, `origin`, `destination` | `create` |
| `order.status_changed` | every assignment of `order.status` (inventory §2 lists about 20 sites, including the unlogged `FAILED` ones) | `order`, `status`, `previous`, `reason` | order `status`; fleet `pending` insert/remove where applicable |
| `order.assigned` | `_dispatch` | `order`, `agv` | order `agv`; agv `order` |
| `agv.state_changed` | `FleetCoordinator._transition_agv` | `agv`, `state`, `previous` | agv `state` |
| `agv.move_started` | in `_travel`, after `enter_node` grants the next node and before the travel timeout | `agv`, `from`, `to`, `t_end`, `motion` (§6.4), `loaded` | none (position is derived from the motion until `move_ended`) |
| `agv.move_ended` | after the timeout, when `current_node` changes | `agv`, `node`, `battery` | agv `node`, `battery`; node `occupant` set/cleared |
| `agv.move_interrupted` | when an interrupt ends a segment early | `agv`, `node` (the node the simulation keeps, the previous one), `reason` | agv `node` |
| `agv.load_changed` | pick complete, unload complete, cargo drop or return | `agv`, `load` | agv `load` |
| `agv.stranded` | stranding sites in `_travel` | `agv`, `node`, `reason` | agv `state` |
| `traffic.wait_started` / `traffic.wait_ended` | around waits in `enter_node` and reroute delays | `agv`, `node`, `reason` | none |
| `warehouse.inventory_changed` | after container get/put in `pick`/`put` | `warehouse`, `sku`, `level`, `delta` | warehouse `inventory` put |
| `warehouse.slot_changed` | slot acquire/release | `warehouse`, `in_use` | warehouse `slots_in_use` |
| `charging.started` / `charging.ended` | `recharge` and `swap` | `station`, `agv`, `mode` (`recharge`, `swap`) | station `slots_in_use`; agv `battery` at end |
| `parking.entered` / `parking.left` | `ParkingArea.enter`/`leave` | `area`, `agv` | area `parked` |

`ParkingArea.enter/leave` are never called by `FleetCoordinator` today (inventory "facts"); SP1 instruments them but does not change that behavior.

**Observer events**

| Type | Payload |
|---|---|
| `log` | `level`, `message`, `component`, `extra` (wire map) |
| `kpi.sample` | `kpi`, `value`, `scope` (entity id or null) |

### 6.4 Motion description

`SpeedProfile` gains an optional method `motion(distance, load_weight, battery_level, speed_limit) -> MotionDescription`. `TrapezoidalProfile` implements it with the portable primitive `{"curve": "trapezoidal", "v_max", "accel", "decel", "distance"}` using the same effective values it uses for `travel_time`. A constant-speed profile returns `{"curve": "constant", "speed", "distance"}`. Profiles without the method yield `{"curve": "linear", "approximate": true}`. Non-finite travel times (inventory §1, `speed.py`) are encoded as explicit `+inf` and the motion is marked `stalled`.

## 7. Bus and logging

### 7.1 Bus

- `env.bus.subscribe(handler, types=(...))` with `types` a tuple of event classes or `"*"` (all domain events) or `"**"` (everything). Returns a `Subscription` with `.cancel()`.
- `env.wants(cls)` is an O(1) dictionary lookup maintained on subscribe and cancel. Emitting sites follow one pattern:

  ```python
  if env.wants(JobQueued):
      env.emit(JobQueued(job=job.id, server=self.id, priority=..., queue_length=len(self.queue), deltas=...))
  ```

  Arguments are evaluated only when someone listens, which removes today's eager evaluation of `job.priority()` in debug logs (inventory §3).
- **Domain ordinals.** The ordinal must not depend on which observers listen. It is assigned only while the semantic projection is active, that is while the digest or a trace recorder is attached; both subscribe to all domain types (`"*"`, which also covers types registered later), so every domain event is constructed and counted. Without them, `ordinal` is `None`; nothing else uses it. A test asserts identical ordinals across observer configurations that include the digest.
- Delivery: synchronous, in subscription order; nested emissions are queued FIFO and delivered after the current event reaches every subscriber (C1.3). Subscribers that schedule SimPy events or draw from `env.rng` raise in debug mode (detected by checking the event queue length and RNG call counters around delivery).

### 7.2 Logging (D50, D51)

- `env.debug/info/warning/error(message, *, component=None, **extra)` emit `log` observer events; nothing else in the core logs. The 42 component `env.debug(...)` calls (inventory §3) are removed and replaced by the domain events of §6.3. The nine fleet warnings and errors stay as `log` events at their levels, next to the domain events that describe the same situation (for example `agv.stranded`).
- Sinks in `simulatte.logsinks`:
  - `TextSink(stream_or_path, level, components=None, render_domain=True)`: writes `log` events at or above `level`; when `level` is `DEBUG` and `render_domain` is true it also subscribes to all domain events and renders them as lines (type, time, payload). The sink opens its file once (today the sink reopens per message).
  - `JsonSink`: the same, one JSON object per line.
  - `SQLiteSink(path)`: stores events in a table `(env_id, seq, t, kind, type, level, component, message, data_json)`; offers `query(...)` and `execute_sql(...)` with today's semantics.
  - `HistorySink(maxlen)`: the in-memory ring buffer behind `env.log_history`, with today's `query(level=, component=, since=)` API.
- Component filtering: sinks take `components=` include/exclude sets; `enable_component`/`disable_component` move from `SimLogger` to sinks.
- The PyPy finalizer and loguru re-entrancy workarounds (inventory §3) disappear with loguru. `Environment.close()` closes sinks deterministically.

### 7.3 Test and doc migration

`tests/core/test_logger.py`, `test_logger_sqlite.py`, `test_logger_finalizer.py` and `test_component_logging.py` are rewritten against sinks and events; assertions on message substrings become assertions on event types and payloads. `tests/intralogistics/test_logging.py` likewise. `docs/tutorials/logging.md` and `docs/api/utilities.md` are rewritten.

## 8. Randomness

### 8.1 Streams

- `env.rng(name)` returns a `random.Random` seeded with `int.from_bytes(blake2b(f"simulatte-rng-v1\0{seed}\0{name}".encode(), digest_size=16).digest(), "big")`. Streams are created on first use and cached per name. The derivation identifier `simulatte-rng-v1` is recorded in the manifest.
- Python guarantees that `Random.random()` reproduces across versions for the same seed, but not the derived methods (`expovariate`, `gammavariate`, …). The manifest records the Python version; cross-version differences surface as fingerprint mismatches (C1.6).

### 8.2 Distributions and samplers

- The distribution classes in `distributions.py` are already frozen dataclasses (inventory §4). Each gains `sampler(rng) -> Callable[[], float]`; `__call__` without a bound RNG is removed. Routing factories (`pure_job_shop_routing`, `general_flow_shop_routing`) take an `rng` argument at call time.
- Components bind at attachment, with stream names derived from their id:

  | Component | Stream names |
  |---|---|
  | `Router` | `<router>/interarrival`, `<router>/sku`, `<router>/routing/<sku>`, `<router>/service/<sku>/<server>`, `<router>/due/<sku>` |
  | intralogistics time functions | `<agv>/load`, `<agv>/unload`, `<warehouse>/pick`, `<warehouse>/put`, `<station>/recharge` |

  A distribution object shared across servers (inventory §4, `Scenario.build_router`) therefore yields one independent sampler per stream.
- Parameters that accept a sampler accept: a distribution description (managed), a number (constant, managed), or any zero-argument callable (opaque). Opaque callables keep working; the environment records `opaque_samplers=True` in the manifest and emits one `log` warning per component at activation. The intralogistics `*_time_fn` parameters accept the same three forms; their deterministic lambdas in tests and examples are replaced by numbers.
- `Runner` creates `Environment(seed=seed)` and no longer calls `random.seed`. The library never touches the global `random` module. Tests that seed the global module (inventory §4) move to `Environment(seed=...)`.
- Common random numbers are provided at stream level only. Per-job substreams are not part of 1.0 unless SP5 shows they are needed (C1.5, A33).

## 9. Semantic projection, digest and provenance

### 9.1 Canonical encoding

- MessagePack with: maps with keys sorted by UTF-8 bytes; floats always encoded as float64 (`+inf`, `-inf`, `NaN` as the canonical float64 bit patterns, `NaN` normalized); integers in the smallest msgpack form; strings UTF-8; tuples as arrays.
- A domain event's projection is `[ordinal, type, version, t, payload_map, deltas_array]`. `seq` and observer events are excluded (D39).
- The initial-state projection is the canonical encoding of the snapshot captured at activation (§10), entities sorted by id.

### 9.2 Digest

- `SemanticDigest` is a subscriber to all domain events. It feeds a `hashlib.blake2b(digest_size=32)` with the initial-state projection and then each event projection, each prefixed by its length as an unsigned 64-bit big-endian integer.
- Enabled by `TraceRecorder` at levels `kpi` and `full`, and by `env.enable_digest()`; off otherwise.
- `fingerprint = {digest, kpis: {name: value}}` is produced at the end of the run (`env.fingerprint()`).

### 9.3 Provenance and manifest

- `Provenance(source=..., inputs=..., dependencies=..., model=...)`: each field is a hash string or `UNAVAILABLE` (the default). Scripts may pass what they know; SP4 fills it automatically.
- `env.manifest()` returns a `RunManifest` with the C1.6 fields: simulatte version, Python implementation and version, OS and architecture, dependencies (from provenance, else the installed distribution list via `importlib.metadata`, marked as "environment listing"), RNG derivation id, provenance fields, parameters (empty in SP1 unless the caller provides them), seed, stopping policy (recorded when `env.run(until=...)` is called: `{"type": "horizon", "horizon": until}`; `run()` without `until` records `{"type": "exhaustion"}`), warm-up (from KPI configuration), time unit, `opaque_samplers`, and `complete` (true only if no field is unavailable and no opaque sampler was used).
- Volatile metadata (wall-clock start, host name, durations) is a separate object.

## 10. Preparation and activation

Implements C1.10.

- **Prelude.** Events emitted before activation are delivered normally to subscribers present at the time, recorded by a trace recorder in a prelude section, and excluded from the projection. Their effects reach the initial state through snapshots, not replay.
- **Initializers.** `env.on_activate(fn)` registers a plain function (not a generator). At activation, initializers run in registration order. After each one, the environment asserts that `env.now` is unchanged and that the function scheduled no SimPy event with a delay; violations raise.
  - `FleetCoordinator` replaces its `_initial_placement` process (inventory §2) with an initializer that places AGVs synchronously: `ResourceBasedTrafficManager.place_now(agv, node)` requests the node resource and asserts it was granted immediately; a conflict (two AGVs on one capacity-1 node) raises at activation instead of blocking.
- **Initial state.** After initializers, the environment captures a snapshot of every live entity (its `snapshot()` per state schema) and starts the projection. The domain ordinal resets to 0 for the first domain event after activation.
- **Command queue.** Components opt in with `@deferrable` on public commands. In SP1 these are `FleetCoordinator.submit`, `cancel` and `create_order`'s attachment step. Before activation, calls append `(bound method, args, kwargs)` to the environment's single queue and return immediately; affected orders report `OrderStatus.PENDING_ACTIVATION`. At activation the queue drains in order, as ordinary domain transitions at time 0, before `env.run` processes any scheduled event. An exception stops activation and propagates from `env.run`; remaining commands are dropped.
  - Production `ShopFloor.add` and `PreShopPool.add` are **not** deferrable: they only schedule processes, which SimPy already orders by insertion, so existing setup code keeps its exact semantics.
- `env.activate()` is idempotent. Calling an initializer registration or a deferrable command after activation runs it immediately.

## 11. Trace format (D53)

### 11.1 Container

A trace file is a sequence of records:

```
file   = magic ("SIMTRACE", 8 bytes) format_major(u16) format_minor(u16) record* trailer?
record = length(u32, payload bytes) type(u8) crc32(u32, of payload) payload
```

| Type | Payload (msgpack; `CHUNK` payload is zlib-deflate compressed msgpack) |
|---|---|
| `HEADER` (exactly one, first) | canonical header: format features (required, optional), catalog, kinds and state schemas, manifest, recording level, chunk limits; volatile metadata as a separate map |
| `PRELUDE` (at most one, after the header) | prelude events (inspection only) |
| `CATALOG_EXT` | event types or kinds registered after the header (C1.7), with their epoch number |
| `CHUNK` | `{first_cursor, last_cursor, t_start, t_end, epoch, snapshot, events}` |
| `INDEX` | one entry per chunk, appended right after the chunk: offset, length, cursors, times, epoch |
| `KPI` | KPI series samples and, at the end, scalars (`kpi` and `full` levels) |
| `FOOTER` (last record when complete) | outcome (`completed`, `cancelled`, `failed`), final cursor, fingerprint, full chunk index, catalog epoch offsets |

`trailer` = the footer record's offset (u64) followed by the magic `SIMTEND\0`, so a reader with random access (a browser using HTTP range requests) reads the last 16 bytes, then the footer, then any chunk directly.

- A growing file has no footer. Python readers scan records; a record with a short length or a CRC mismatch at the end is treated as truncated and ignored. While a run is in progress, the studio server (SP4) serves the index from its own scan; the browser never needs to scan.
- Chunk limits (defaults, configurable on `TraceRecorder`): 10,000 events, 1 MiB uncompressed, 1 s of wall-clock age for the oldest unpublished event (checked at each event, C1.7 B12), and an optional simulated-time window. Snapshots are taken from the recorder's replay state at chunk boundaries (safe points).
- Compression is zlib (`deflate` format), available in Python's standard library and in browsers' `DecompressionStream("deflate")`.

### 11.2 Writer and reader

- `TraceRecorder(env, path, level="full", chunk_limits=None)` subscribes to the bus (domain events with deltas, KPI samples at `kpi` and `full`) and writes synchronously in the simulation thread; no threads. `close()` writes the footer; `env.close()` closes recorders. An exception in the run writes a footer with outcome `failed`.
- `Trace.open(path)` reads header, catalog, index and footer. API: `trace.manifest`, `trace.catalog`, `trace.cursor_range`, `trace.state_at(cursor)`, `trace.events(start, end)`, `trace.kpis()`, `trace.fingerprint`. `state_at` loads the nearest chunk snapshot and applies deltas.
- `Trace.verify()` recomputes the digest from the trace (initial state plus domain events) and compares it with the footer.

### 11.3 TypeScript conformance reader (G2)

- `studio/` is created as a pnpm workspace (Vite and Vitest, TypeScript strict) with one package, `@simulatte/trace`, depending on `@msgpack/msgpack` only. It implements the container, catalog, delta application and `stateAt(cursor)`. No rendering.
- Fixture traces generated by Python tests live in `tests/fixtures/traces/` with their expected states as canonical JSON. A CI job runs the TS tests against them. SP3 extends this package; it is not throwaway.

## 12. KPIs and collectors (D52)

### 12.1 Declarations

```python
KPI(
    name="flow_time", unit="time", kind=("series", "scalar"),
    observation="job", cohort="completed_in_window",
    aggregation="mean", clip="none", censoring="exclude",
    ema_reset=False, empty=None, description="...",
)
```

The fields implement the C1.8 estimand. `Collector` is a base class: it declares its KPIs, subscribes to the domain events it needs, keeps its own state, emits `kpi.sample` events, and exposes results as attributes. Collectors never read simulation objects except through pure getters (§13).

### 12.2 Window

`Environment` holds a `warmup` set by `KPIConfig(warmup=...)` passed to collectors or the recorder, default 0. The window follows C1.8 for the stopping policy recorded in the manifest; in SP1 the only policies are `horizon` and `exhaustion` (finite-population policies arrive with SP5).

### 12.3 Replacing the old protocols

| Old | New |
|---|---|
| `MetricsCollector` protocol, `ShopFloor(metrics_collector=...)`, `set_metrics_collector` | `Collector` subclasses attached with `collector.attach(env)` |
| `EMAMetricsCollector` (default on every `ShopFloor`) | `EMACollector` with the same `ema_*` attributes and alpha; `ShopFloor` attaches one by default (`ShopFloor(default_metrics=False)` to opt out) and exposes it as `shopfloor.metrics` |
| `TimeSeriesCollector` protocol, `collect_time_series=True`, `DefaultTimeSeriesCollector` | `ShopFloorTimeSeries` collector with `wip_ts`, `job_count_ts`, `throughput_ts`, `lateness_ts` and the `plot_*` helpers |
| `CurrentWorkLoadCollector`, `collect_workload=True` | `CurrentWorkloadCollector` driven by events; its pre-release skip logic (inventory §5) disappears because `job.released` is a separate event |
| `Server(collect_time_series=...)`, `retain_job_history` | `ServerTimeSeries` collector with `qt`, `ut` and the `plot_qt`/`plot_ut` helpers; `retain_job_history` stays on `Server` (it is retention, not collection) |
| `OrderMetricsCollector`, `EMAOrderMetrics` | `OrderEMACollector` with the same attributes; default on `FleetCoordinator` (`default_metrics=False` to opt out) |
| `IntralogisticsTimeSeriesCollector`, `DefaultIntralogisticsCollector` | `FleetTimeSeries` with `fleet_utilization_ts`, `pending_orders_ts`, `throughput_ts`, `inventory_ts` (keyed by warehouse id) and the `plot_*` helpers, computed from `agv.state_changed` events instead of flushing every AGV |
| Builder flags `collect_time_series`, `collect_workload` | kept as conveniences that attach the new collectors |

New window-aware KPIs (flow time, tardiness, lateness, throughput, time-weighted WIP and utilization per server, fleet utilization) are provided by `ShopFloorKPIs` and `FleetKPIs`. Time-weighted utilization in these collectors is accumulated from `job.granted`/`job.released` and clipped at the window; the core `Server.worked_time` and `utilization_rate` keep their current crediting because dispatching rules read them (inventory §5) and changing them would change simulation results.

## 13. Observer purity

From inventory §5:

- `AGV.utilization()`, `state_percentage()` and `time_allocation()` become pure: they compute the open interval on the fly without writing `state_durations` or `_state_entered_at`. `AGV.transition_to` remains the only writer.
- `TrafficManager.check_path` stops logging.
- Debug-log argument evaluation disappears with the logging rebuild (§7.1).
- A test runs reference models under four observer configurations (none, default logging, KPI, full trace plus extra collectors) and asserts identical final model state and, among instrumented ones, identical digests and common KPIs (C1.3, B18).

## 14. Benchmarks and CI (C1.9)

- `benchmarks/` holds workloads: a 10-server job shop with LumsCor release (short CI size and full size), a congested variant at utilization 0.95, and the intralogistics advanced example scaled up.
- `benchmarks/run.py` runs each workload in modes `none`, `default_logging`, `kpi`, `full`, repeated, and reports median wall time, peak memory (`tracemalloc` off for timing runs, separate memory runs), trace size, chunk count, and seek latency percentiles (p50, p95) over random cursors.
- **Baseline in the same job.** CI installs the pre-SP1 release (`simulatte==0.12.0`) in a second virtual environment and runs the same workload there, so the overhead ratio is measured on one machine in one job. Workloads use only APIs present in both versions, adapting seeding by feature detection.
- The `none` mode fails CI above 3 % median overhead plus a noise band calibrated in G3 (reported, not hidden). Other modes are reported in the job summary; their budgets are fixed at G3 and recorded in the global spec.
- Runs on CPython 3.14 and PyPy 3.11.

## 15. The `queue_length` fix (D49)

Reproduced on `426d1a9`: `Server.request` logs `len(self.queue) + 1` after the request is constructed. SimPy's `Request` constructor already appends the request to the queue and grants it immediately when a slot is free, so the logged value is one too high in both cases (1 instead of 0 when granted at once; counts the job twice when it waits). The `job.queued` event carries `len(self.queue)` after construction. A regression test covers both cases, with capacity 1 and 2.

## 16. Migration, docs and examples

- `CHANGELOG.md` 0.13 entry with a migration section: `Environment` arguments, logging (`SimLogger` removed, sinks, per-env level, component filters), entity ids and `name=`, builder `prefix`, seeding (`Environment(seed=)`, `Runner`), distribution samplers, collectors, `LayoutGraph.nodes` ordering type, `OrderStatus.PENDING_ACTIVATION`, intralogistics time-function forms.
- Docs: rewrite `docs/tutorials/logging.md`; update `docs/introduction/architecture.md`, `docs/api/*`, `docs/running-on-pypy.md`; add a page on events and traces. Gallery examples and their doc pages change together (the docs gate requires verbatim equality, inventory §8).
- The repository's `skills/` and `docs/ai-skill.md` are updated to the new APIs.

## 17. Testing

In addition to the per-feature unit tests the plan defines:

- Determinism in fresh processes across `PYTHONHASHSEED` values (§5.3).
- Seek equals replay at every chunk boundary and at sampled cursors, including runs of same-time events (G1).
- Python and TypeScript replay equality on fixtures (G2).
- Truncated and corrupted traces: missing footer, short last record, CRC mismatch, unknown required feature.
- Observer invariance (§13).
- Ordinal independence from additional subscribers (§7.1).
- Activation: initializer violations, `submit` then `cancel` before activation, a failing queued command, AGV placement conflicts.
- RNG: stream independence, shared distribution objects, reproducibility under `Runner` parallel and sequential modes.
- KPIs: windowed estimands on hand-computed small cases; EMA collectors equal to the old ones on the reference models (a parity test written before the old code is deleted).
- Wire types: integer limits, non-finite floats, nested immutability in debug mode.

## 18. Risks specific to SP1

| Risk | Mitigation |
|---|---|
| Emitting-site boilerplate across ~60 sites makes components harder to read | One helper per component for its events; the guard pattern is short; reviewed in G1 before G4 multiplies it |
| An emitting site forgets the `wants` guard or builds payloads outside it | A lint test checks emitting sites for the guard pattern; the no-subscriber benchmark catches costly omissions |
| Parity of EMA results after the rewrite | Parity tests against the old collectors before deletion |
| CI benchmark noise hides or fakes regressions | Same-job baseline, medians, calibrated noise band, results published in the job summary |
| G2 pulls Node tooling into SP1 | Single small package, its own CI job, no frontend framework |

## 19. Open questions

None blocking. The plan decides internal structure, helper names and the exact field lists within the catalog above.
