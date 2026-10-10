# SP1: events and trace (design)

- **Status:** revision 4, after SP1 reviews 1–3 ([`reviews/`](reviews/)); markers such as (S4), (T1) or (U1) show what each finding changed
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

**Out of scope:** layout and positions (SP2), viewer (SP3), execution and studio (SP4), experiments (SP5). SP1 records no coordinates except the existing `Node.x`/`Node.y`, which AGV motion already uses.

## 2. Delivery: gates

SP1 ships as one release, built in four gated stages (D44). A stage that fails its acceptance criteria sends the design back to review before the next stage starts.

| Gate | Content | Acceptance |
|---|---|---|
| G1. Vertical slice | Entities, bus, catalog, deltas, digest, activation, RNG streams, trace writer and Python reader, implemented for `Server`, `ProductionJob`, `ShopFloor`, `PreShopPool` and `Router`; the `queue_length` fix | A reference job shop records a `full` trace; Python seek equals uninterrupted replay at every chunk boundary and at sampled intermediate cursors, including direct `Server` use and immediate grants; digest identical across observer configurations and `PYTHONHASHSEED` values |
| G2. TypeScript conformance | `studio/` workspace with one package, `@simulatte/trace`, decoding G1 fixtures and replaying state | TS replay state equals Python replay state on all fixtures (canonical JSON), including hostile map keys and default-generated seeds |
| G3. Budgets (slice) | Benchmarks of §14 on the slice, modes `none`, `digest` and `full` | No-subscriber overhead within budget; `full` trace size and seek time measured against the C1.7 hypotheses |
| G4. Migration | Remaining components, logging rebuild, collectors, docs, examples, CHANGELOG; final benchmarks in all modes | Full suite green on CPython 3.12–3.14 and PyPy; docs build; all benchmark budgets met, including `default_logging` and `kpi`, which are accepted here because their implementations only exist after G4 (S21) |

## 3. Modules and public surface

| Module | Contents |
|---|---|
| `simulatte._wire` | wire values, key escaping, canonical encoding (private) |
| `simulatte.entities` | `Entity` base, registry, id rules, lifecycle, state schema |
| `simulatte.events` | `Event` base classes, `@event_type`, catalog, deltas, `EventBus` |
| `simulatte.rng` | stream derivation, binding protocols (§8.2) |
| `simulatte.digest` | semantic projection, digest subscriber |
| `simulatte.provenance` | `Provenance`, `RunManifest`, `UNAVAILABLE` |
| `simulatte.trace` (package) | container format, writer (`TraceRecorder`), reader (`Trace`) |
| `simulatte.kpi` | `KPI` declaration, `Collector` base, windows |
| `simulatte.collectors` | built-in production collectors |
| `simulatte.logsinks` | text, JSON, SQLite and history sinks (replaces `simulatte.logger`) |

These modules depend only on `simulatte.environment` and each other, so intralogistics modules can import them. The import audit in `tests/intralogistics/test_import_audit.py` stays (D48 allows relaxing it; SP1 does not need to).

`simulatte/__init__.py` exports the stable entry points (D48): `Environment`, `Runner`, `Provenance`, `TraceRecorder`, `Trace`, `KPI`, `Collector`, and the event base classes. Component classes stay importable from their modules.

`simulatte.logger` is removed (D50). `loguru` is removed from the dependencies; `msgpack` is added (D53).

## 4. Environment

```python
Environment(
    *,
    seed: int | None = None,          # 0 <= seed < 2**63; None: drawn from os.urandom (S12)
    time_unit: str | None = None,
    provenance: Provenance | None = None,
    debug: bool = False,
    log_level: str = "INFO",
    log_file: str | Path | None = None,
    log_format: Literal["text", "json"] = "text",
    log_history_size: int = 1000,
    log_db_path: str | Path | None = None,
)
```

- The `log_*` arguments attach the corresponding sinks (§7) and keep today's names (18 call sites in tests, 5 in docs, inventory §8). `log_level` is per environment; the class-level `SimLogger.set_level`/`get_level` disappears.
- Members: `seed`, `time_unit`, `rng(name)`, `entities`, `bus`, `emit(event)`, `wants(event_type)`, `activate()`, `on_activate(fn)`, `activated`, `configure_kpis(warmup=...)`, `enable_digest()`, `fingerprint()`, `manifest()`, `log_history`, `sinks`, and the logging methods.
- `env.run()` activates on its first call. `env.logger` is removed.
- Seeds outside `[0, 2**63)` raise `ValueError`. Manifests and traces record the seed as a canonical decimal string, so it survives JavaScript decoding (S12).

## 5. Entities

### 5.1 Base, registration, lifecycle

- `Entity` is a mixin with `kind: ClassVar[str]`, `id: str`, `label: str`, `state_schema: ClassVar[StateSchema]` and `snapshot()`. `env.entities.attach(obj, name=None, label=None)` is called by each component after its fields exist.
- **One creation owner** (S2). Attachment emits `entity.created`, the only event carrying a `create` delta, with the entity's initial state. There are no kind-specific creation events; kind-specific information is part of that initial state.
- **Retirement** (S3). `env.entities.retire(obj)` emits `entity.retired` (`retire` delta) and drops the entity from the live registry; the registry then holds only a weak reference. Built-in retirement points:
  - a job retires at the end of its completion block in `ShopFloor.main`, after `job.finished`, the metrics recording and all completion callbacks (`signal_job_finished`);
  - an order retires when it reaches a terminal status (`COMPLETED`, `CANCELLED`, or `FAILED` after retries are exhausted), after the fleet's hooks for that transition ran and its mission bookkeeping (`_active_missions`, `_agv_mission`) was cleaned up; recoverable failures that re-queue the order do not retire it.
  Python-side retention (`ShopFloor.jobs_done`, user references) is unaffected.
- **Ids** follow C1.1. Names must not contain `/` or `\0` (RNG stream names are built from ids) and must not match `^<registered kind>-\d+$`; duplicates raise.
- **Builder prefixes.** Every `build_*_system` gains `prefix: str = ""` and names its entities `f"{prefix}{default}"` (servers `wc-<i>`, `psp`, `shopfloor`, `router`). The shared default `scenario: Scenario = Scenario()` becomes `None` with a fresh instance inside.

### 5.2 State schemas

Every state field declares its wire type, whether it is a collection, and whether it is **presentation** (excluded from the semantic projection, S7). `label` is presentation everywhere.

| Kind | Class | Id source | State fields |
|---|---|---|---|
| `server` | `Server` | `name=` or `server-n` | `capacity`, `users` (job ids, ordered), `queue` (job ids, ordered), `worked_time` |
| `job` | `ProductionJob` | `job-n` | `sku`, `routing` (server ids), `processing_times`, `op_index`, `location`, `due_date`, `created_at`, `finished_at`, `shopfloor` |
| `psp` | `PreShopPool` | `name=` or `psp-n` | `jobs` (ordered), `shopfloor` |
| `shopfloor` | `ShopFloor` | `name=` or `shopfloor-n` | `wip` (map server id → load), `jobs_in_system` |
| `router` | `Router` | `name=` or `router-n` | `shopfloor` |
| `agv` | `AGV` | `agv_id=` or `agv-n` | `node`, `state`, `battery`, `load`, `order`, `motion` (map or null, S4), `fleet` |
| `order` | `TransferOrder` | `order-n` at attachment | `status`, `sku`, `quantity`, `origin`, `destination`, `agv`, `created_at`, `dispatched_at`, `picked_at`, `delivered_at`, `fleet` |
| `fleet` | `FleetCoordinator` | `name=` or `fleet-n` | `pending` (order ids, ordered) |
| `warehouse` | `Warehouse` | `name` | `inventory` (map sku → level), `slots_in_use` |
| `charging_station` | `ChargingStation` | `name` | `slots_in_use`, `swap_pool` |
| `parking_area` | `ParkingArea` | `name` | `parked` (agv ids) |
| `node` | `NodeBinding` | `Node.id` | `x`, `y`, `agvs` (agv ids located here), `reserved_by` (agv ids holding a traffic reservation) |

**Node bindings** (S5). `Node` stays an environment-free definition. Attaching a graph creates one environment-local `NodeBinding` entity per node. Attaching the same `Node` again in the same environment (two fleets sharing a graph) returns the existing binding; a different `Node` with an existing id raises. `agvs` and `reserved_by` are lists, so node capacities above one and free traffic are represented. Traffic managers update `reserved_by`; AGV movement updates `agvs`.

**Job location** (ruling R8, SP1 Task 7 review). `location` takes one of: `null` (not yet in any pool or shop floor), `psp:<psp id>`, `queue:<server id>`, `server:<server id>`, `transit` (in the system, between servers or released and not yet queued), `done`. Writers: `psp.entered` → `psp:<id>`; `psp.exited` → `transit` for `released` and `postponed`, `null` for `removed`; `shopfloor.entered` → `transit`; `job.queued` → `queue:<server>`; `job.granted` → `server:<server>`; `job.queue_left` and `job.released` → `transit`; `job.finished` → `done`. The server events of §6.3 therefore touch job `location` as well.

**Owner fields** (`shopfloor`, `fleet`) let collectors filter by system (S18). Their lifecycle (T5): they are nullable and start as null unless the owner is known at construction (a `Router` or `PreShopPool` receives its shop floor in its constructor). A job's `shopfloor` is set by `psp.entered` or `shopfloor.entered`; entering a different shop floor later overwrites it, and collectors filter on the value carried by each event, not on history. An AGV's `fleet` is set by `fleet.agv_added` when a `FleetCoordinator` takes it; an order's `fleet` is set at attachment. Creation after activation followed by attachment is covered by tests.

### 5.3 Iteration-order fixes

From inventory §7:

- `LayoutGraph._nodes` becomes an insertion-ordered dict; `.nodes` returns a tuple in insertion order.
- `ResourceBasedTrafficManager.check_path` builds `conflict_nodes` in path order.
- `ShopFloor.jobs` becomes an insertion-ordered dict keyed by job.

A test runs the reference models in fresh processes under several `PYTHONHASHSEED` values and requires identical canonical content.

## 6. Events

### 6.1 Classes and emission

- `Event` is a frozen, slotted, keyword-only dataclass with `t`, `seq` and `deltas`. `DomainEvent` adds `ordinal`; `ObserverEvent` covers `log`, `kpi.sample` and anything emitted by observers.
- `@event_type("name", version=1, touches=..., presentation=...)` registers a class in the global catalog with its payload fields, their wire types, nullability, which payload fields are **presentation**, and **`touches`**: the `(kind, state field)` pairs its deltas may change (global C1.2) (T10). Both declarations are serialized in catalog entries and catalog extensions; debug mode rejects field operations outside `touches`. **Lifecycle operations** are validated separately (U4): a `create` is checked against the state schema of the kind it names (including kinds registered later), a `retire` against the existence of the addressed live entity; only `entity.created` and `entity.retired` may carry them. Registering one name with two definitions raises.
- **Emission rules** (S8, S27):
  - `env.emit` stamps `t`, `seq` and (while the projection is active) `ordinal` on a fresh instance; an instance whose `seq` is already set is rejected, so a delivered event never changes.
  - Emitting a `DomainEvent` while subscribers are being called raises: observers cannot inject trajectory.
  - `ObserverEvent`s must have empty deltas; a non-empty delta raises.
- **Payload sources** (S9). Payload and delta values come from data the transition already computed (for example the priority stored on the request), never from calls made only to fill the event. Building an event must not call user policies or callbacks. Nor may it call methods of user-defined value types: numbers are read through the built-in conversions of `int` and `float` (a subclass's own `__float__` is never called), other types only through a conversion implemented in C (NumPy scalars), else they are recorded as `NaN` (as null for `job.queued.priority`, R14); subclasses of `str`, `list`, `tuple` and `dict` are read through the base type's methods, and other mappings are not wire values.
- Debug mode validates payloads against the catalog and rejects subscribers that schedule SimPy events or draw from `env.rng`.
- **Immutable contents** (global C1.2, ruling R30). Payload values and delta operations are deep-immutable wire values: tuples and `FrozenMap`, never lists, dicts, sets or other mutable containers, at any depth. Debug mode rejects mutable contents at `emit` (it validates immutability, not only encodability) and freezes a log call's `extra` into an immutable copy. Independently of debug mode, no consumer may depend on objects the emitter still owns: the trace writer replays the operations it encoded, keeping those that are already immutable wire values and an immutable copy of any other (§11.2).

### 6.2 Deltas

Operations address `(entity_id, field)`: `set`, `insert(index, value)`, `remove(value)`, `move(value, index)`, `put(key, value)`, `delete(key)`, `create(kind, state)`, `retire`. Collections are never re-sent whole (B19). A `set` on a field the entity's state does not yet hold creates it; readers in both languages behave the same way (ruling R12). The Python and TypeScript readers apply the same operations; the G2 conformance suite is the arbiter.

**Replay value equality** (ruling R31). Wherever replay compares wire values (the item a `remove` or `move` looks up, the chunk snapshot `check()` compares with the replay), two values are equal iff their canonical encodings (§9.1) are byte-identical: `true` differs from `1`, the integer `1` from the float `1.0`, `-0.0` from `0.0`; every NaN equals every NaN; arrays compare item by item, maps by key set and values. `remove`/`move` take the first equal item and fail when there is none. Both languages implement it once (`simulatte._wire.wire_equal`, TypeScript `wireEquals`). JavaScript decodes `1` and `1.0` to the same number, so the TypeScript reader records which decoded array and map slots held a float-encoded integral number, keeps the record through list operations, and compares encodings, not only values; the numbers it returns stay plain numbers.

An event whose encoded size exceeds `ChunkLimits.max_event_bytes` (default 256 KiB) raises in debug mode and is recorded with a warning otherwise (global B19).

### 6.3 Server resource events (S1, T1, T2)

Server events are emitted from the resource itself, each after SimPy has completed the state change it describes, so they are correct for `ShopFloor` and for direct `Server` users, and replay matches live state at every cursor:

| Transition | Hook | Event |
|---|---|---|
| A request enters the put queue | `Server._trigger_put`, on entry, for each request not seen before (SimPy's `Put.__init__` has already appended it at its sorted index) | `job.queued`: queue `insert` at the request's current index; `queue_length` = `len(server.queue)` at that moment, **including the newcomer** (D49); `priority` = the priority stored on the request |
| Queue order changes | `Server.sort_queue`, after sorting, only when relative order changed (priorities are refreshed on every put, so a newcomer may move again) | `server.queue_reordered` with the minimal `move` set (elements outside the longest increasing subsequence of old positions) |
| Requests are granted | `Server._trigger_put`, after `super()._trigger_put` returns, for each request that is now in `users` and was not before; SimPy pops granted requests from the queue only after `_do_put` returns, so this is the first point where both changes are complete | `job.granted`: queue `remove`, users `insert` |
| A waiting request is cancelled | `ServerPriorityRequest.cancel`, only when it actually removed the request from the queue (an interrupted waiting process leaving its `with` block) | `job.queue_left` (`reason`: `cancelled`): queue `remove` |
| A request is released | `Server.release`, only when SimPy's release actually removed it from `users` (releasing an ungranted or already released request changes nothing and emits nothing) | `job.released`: users `remove` |
| Processing time is credited (ruling R29) | `Server.process_job`, after the processing timeout and `worked_time += processing_time` | `server.work_credited` (`server`, `job`, `processing_time`): `worked_time` `set` |

With this boundary, `queue_length` counts the requests waiting when the job joins, itself included: a job that finds a free slot has `queue_length = 1` and is granted in the next event. The old log value (`len(queue) + 1` measured after the grant) double-counted every waiting job; that is the off-by-one D49 fixes.

The plan verifies with replay-equals-live checks after every event, including a request granted inside its constructor, a server used without a `ShopFloor`, an interrupted waiting request, and a duplicate release.

### 6.4 Core catalog

**Lifecycle (all kinds):** `entity.created` (`kind`, `label` presentation; `create` delta), `entity.retired` (`kind`; `retire`).

**Production** (phases follow `ShopFloor.main`, inventory §2):

| Type | Emitted at | Payload | Deltas |
|---|---|---|---|
| `psp.entered` | `PreShopPool.add` after append | `job`, `psp`, `position` | psp `jobs` insert; job `location` = `psp:<id>`, `shopfloor` (the PSP's shop floor) |
| `psp.exited` | `PreShopPool.remove` | `job`, `psp`, `reason` (`released`, `postponed`, `removed`) | psp `jobs` remove; job `location` (`transit` for released and postponed, null for removed) |
| `shopfloor.entered` | `ShopFloor.add` | `job`, `shopfloor` | shopfloor `jobs_in_system`, `wip` puts; job `shopfloor`, `location` = `transit` |
| `job.queued`, `job.granted`, `job.queue_left`, `job.released`, `server.queue_reordered` | §6.3 | | |
| `operation.started` | after before-hooks and material ensure, before the processing timeout | `job`, `server`, `op_index`, `processing_time`, `planned_end` | job `op_index` |
| `operation.completed` | after the timeout and the `worked_time` credit | `job`, `server`, `op_index`, `processing_time` | none (the credit is the server's own `server.work_credited`, emitted just before; R29) |
| `shopfloor.wip_updated` | after `wip_strategy.complete_operation` | `shopfloor`, `changes` | shopfloor `wip` puts |
| `job.finished` | completion block | `job`, `shopfloor`, `makespan`, `lateness`, `total_queue_time` | job `location` = `done`, `finished_at`; shopfloor `jobs_in_system` |
| `policy.decision` | release policies and Draco | `policy`, `job`, `action` (`release`, `force_pin`, `postpone`) | none |

**Intralogistics.** Every declared state field is mapped to all its mutation sites, including interruption and cleanup paths (S6); the plan carries the site-by-site table. Events:

| Type | Emitted at | Payload | Deltas |
|---|---|---|---|
| `order.status_changed` | every assignment of `order.status` (about 20 sites, including `FAILED`) | `order`, `status`, `previous`, `reason` | order `status` and the matching timestamp |
| `fleet.pending_changed` | every append to and removal from `_pending_queue`, at the mutation itself (T4) | `fleet`, `order`, `op` (`added`, `removed`), `index` | fleet `pending` insert or remove |
| `fleet.agv_added` | `FleetCoordinator` construction, per AGV | `fleet`, `agv` | agv `fleet` |
| `order.assigned` / `order.unassigned` | `_dispatch` / mission cleanup, cancellation, interruption re-queue | `order`, `agv` | order `agv`; agv `order` |
| `agv.state_changed` | `AGV.transition_to` (the only writer of `state`, so direct calls are covered) | `agv`, `state`, `previous` | agv `state` |
| `agv.move_started` | in `_travel`, after `enter_node` grants the next node, before the travel timeout | `agv`, `from`, `to`, `t_end`, `motion`, `loaded` | agv `motion` set (S4) |
| `agv.move_ended` | after the timeout, when `current_node` changes | `agv`, `node`, `battery` | agv `node`, `battery`, `motion` cleared; node `agvs` remove/insert |
| `agv.move_interrupted` | interrupt during a segment | `agv`, `node` (the node the simulation keeps), `reason` | agv `motion` cleared |
| `agv.load_changed` | pick complete, unload complete, cargo drop or return | `agv`, `load` | agv `load` |
| `agv.battery_changed` | battery changes outside movement (charging, swap) | `agv`, `battery` | agv `battery` |
| `agv.stranded` | stranding sites in `_travel` | `agv`, `node`, `reason` | none (the state change is its own `agv.state_changed`) |
| `traffic.reserved` / `traffic.released` | `place_now`, `enter_node` grant, `leave_node`, `cancel` | `agv`, `node` | node `reserved_by` |
| `traffic.wait_started` / `traffic.wait_ended` | around waits in `enter_node` and reroute delays | `agv`, `node`, `reason` | none |
| `warehouse.inventory_changed` | after container get and put | `warehouse`, `sku`, `level`, `delta` | warehouse `inventory` put |
| `warehouse.slot_changed` | slot acquire and release | `warehouse`, `in_use` | warehouse `slots_in_use` |
| `charging.started` / `charging.ended` | `recharge` and `swap` | `station`, `agv`, `mode` | station `slots_in_use`, `swap_pool` |
| `charging.pool_changed` | `_replenish_pool` | `station`, `swap_pool` | station `swap_pool` |
| `parking.entered` / `parking.left` | `ParkingArea.enter`/`leave` | `area`, `agv` | area `parked` |

`ParkingArea.enter/leave` are never called by `FleetCoordinator` today; SP1 instruments them without changing that.

**Amendments from implementation (ruling R21, SP1 Task 15):**
- `agv.placed` (`agv`, `node`, `previous`; deltas agv `node`, node `agvs` remove/insert) is emitted when an AGV is created at an already-bound node and on a direct `current_node` assignment that changes the node.
- `agv.move_started` names its payload fields `from_node` and `to_node` (`from` is a Python keyword); the AGV `motion` state map keeps `from`/`to` and gains `"stalled": true` with `t_end = +inf` for non-finite travel times.
- `order.status_changed` may also set the order's `agv` when a load-recovery strategy changed both; `order.unassigned` carries only the side actually cleared.
- `agv.load_changed` at pickup also sets the order's `picked_at`.
- "Every assignment" includes self-transitions (for example `CANCELLED` → `CANCELLED`); replay is unaffected.
- Only framework transitions emit events: direct user writes to `agv.battery.level`, `agv.current_load`, order fields or `_pending_queue` emit nothing, and load-recovery strategies are observed after `recover` returns.

**Amendments from implementation (ruling R22, SP1 Task 16):**
- `charging.started`/`charging.ended` touch only station `slots_in_use`; `charging.pool_changed` is emitted at every swap-pool change (the swap's pool get, which may wait, and `_replenish_pool`'s put). `charging.ended` is emitted at every slot release, including after an interruption.
- Warehouse slot and inventory events and charging slot events are emitted from the resource itself when SimPy completes the grant or get, before the waiting process resumes. At a slot grant the request may still sit in the resource's internal queue (SimPy pops it right after); the declared state (`slots_in_use`) is already complete.
- `cancel` is not a reservation site: `reserved_by` changes only at `place_now`, an `enter_node` grant and `leave_node`; `cancel` ends a pending wait (`traffic.wait_ended` with reason `cancelled`).
- Wait reasons: `traffic.wait_started.reason` ∈ {`node_occupied`, `path_delay`, `deadlock_backoff`}; `traffic.wait_ended.reason` ∈ {`granted`, `cancelled`, `interrupted`, `elapsed`}; `node` is the next node to enter; the fleet's path-delay and deadlock-backoff waits are emitted from the fleet.
- Re-entering a parking area emits `parking.entered` with no delta.
- Payload types: `level`, `delta`, `swap_pool` are floats; `in_use` is an integer.

**Amendment (ruling R29, Codex review fix wave):** every mutation of replayed server state carries a delta at the server level, so a server used without a `ShopFloor` replays like one inside it (§17). `Server.process_job` emits `server.work_credited` (§6.3) with the `worked_time` `set` that `operation.completed` used to carry; `operation.completed` keeps its payload and emission point and has no deltas (no `touches`). A direct user write to `Server.worked_time` emits nothing, like the direct writes of R21.

**Observer events:** `log` (`level`, `message`, `component`, `extra`), `kpi.sample` (`kpi`, `scope`, `value`).

### 6.5 Motion description

`SpeedProfile` gains an optional `motion(distance, load_weight, battery_level, speed_limit) -> MotionDescription`. `TrapezoidalProfile` returns `{"curve": "trapezoidal", "v_max", "accel", "decel", "distance"}` with the same effective values it uses for `travel_time`; constant-speed profiles return `{"curve": "constant", "speed", "distance"}`; profiles without the method yield `{"curve": "linear", "approximate": true}`. Non-finite travel times are encoded as `+inf` and marked `stalled`. The AGV's `motion` state field holds `{from, to, t_start, t_end, description}` while a segment is active, so a seek into a later chunk restores the movement (S4); node `x`, `y` resolve endpoint ids.

## 7. Bus and logging

### 7.1 Bus

- `env.bus.subscribe(handler, types)` with `types` a tuple of event classes, `"*"` (all domain events, including types registered later) or `"**"` (everything); returns a `Subscription` with `.cancel()`.
- `env.wants(cls)` is an O(1) lookup. It is a bound lookup into the bus's interest cache, set when the environment is constructed (ruling R16): it is not an overridable method, and replacing `env.bus` after construction is unsupported. Emitting sites use the guard pattern; arguments are evaluated only when someone listens, and only from captured data (§6.1).
- **Domain ordinals** are assigned only while the projection is active (digest or recorder attached); both subscribe to `"*"`, so every domain event is then built and counted. Otherwise `ordinal` is `None`.
- Delivery is synchronous, in subscription order; nested observer emissions are queued FIFO after the current event reaches every subscriber. If a subscriber raises, the exception propagates from `env.emit`, the nested queue is cleared, and later emissions work normally.

### 7.2 Logging (D50, D51)

- `env.debug/info/warning/error(message, *, component=None, **extra)` emit `log` events. The 42 component `env.debug(...)` calls are removed and replaced by domain events; the nine fleet warnings and errors stay as `log` events next to the matching domain events.
- `simulatte.logsinks`:
  - `TextSink(target, *, level="INFO", components=None, exclude=(), render_domain=True)`: writes `log` events at or above `level`; at `DEBUG` with `render_domain` it also subscribes to `"*"` and renders domain events. Opens its file once.
  - `JsonSink`: the same, one JSON object per line.
  - `SQLiteSink(path)`: table `(env_id, seq, t, kind, type, level, component, message, data_json)`; `query(...)` and `execute_sql(...)` with today's semantics.
  - `HistorySink(maxlen)`: behind `env.log_history`, with today's `query(level=, component=, since=)`.
- `enable_component`/`disable_component` move to sinks. `Environment.close()` closes sinks.

### 7.3 Test and doc migration

The logger and component-logging tests (inventory §3) are rewritten against sinks and events; assertions on message substrings become assertions on event types and payloads. `docs/tutorials/logging.md` and `docs/api/utilities.md` are rewritten.

## 8. Randomness

### 8.1 Streams

`env.rng(name)` returns a `random.Random` seeded with `int.from_bytes(blake2b(f"simulatte-rng-v1\0{seed}\0{name}".encode(), digest_size=16).digest(), "big")`, cached per name. Python reproduces `Random.random()` across versions for the same seed but not the derived methods; the manifest records the Python version and differences surface as fingerprint mismatches.

### 8.2 Binding protocols (S11)

Three binding kinds cover the callback shapes that exist today:

| Kind | Callback shape | Managed forms | Used by |
|---|---|---|---|
| scalar | `() -> float` | distribution description, number | inter-arrival, service times, due-date offsets, AGV load and unload times |
| routing | `() -> Sequence[Server]` | routing description (`PureJobShopRouting`, `GeneralFlowShopRouting`, `FlowShopRouting`), a fixed sequence of servers | `Router` routings per SKU |
| contextual | `(*context) -> float` (for example `(sku, qty)` for picks, `(current_level, target_level)` for recharge) | distribution description, number (the context is ignored) | warehouse pick and put, charging recharge |

- Distributions gain `sampler(rng)`; routing factories return descriptions with `sampler(rng)`; `__call__` without an RNG is removed.
- `env.bind(value, *, kind, stream, owner)` returns the callback. Any other callable of the right shape is **opaque**: accepted unchanged, recorded in `env.opaque_sampler_owners`, and it makes the manifest incomplete.
- Stream names: `<router>/interarrival`, `<router>/sku`, `<router>/routing/<sku>`, `<router>/service/<sku>/<server>`, `<router>/due/<sku>`, `<agv>/load`, `<agv>/unload`, `<warehouse>/pick`, `<warehouse>/put`, `<station>/recharge`. A description shared across servers yields one independent sampler per stream.
- `Runner` creates `Environment(seed=seed)` and never calls `random.seed`. The library never touches the global `random` module.
- Common random numbers are provided at stream level only; per-job substreams are not part of 1.0 unless SP5 shows they are needed.

## 9. Semantic projection, digest and provenance

### 9.1 Wire values and canonical encoding

- MessagePack. Map keys are strings and are **escaped** reversibly (S16, T8): a key that is one of `__proto__`, `constructor`, `prototype` (the keys the JavaScript decoder rejects) or starts with `~` is prefixed with `~`; readers strip one `~`. The TypeScript reader rebuilds maps as objects without a prototype and defines an own `__proto__` property safely. Tests cover nested occurrences and collisions (`~__proto__` as an original key).
- Canonical form: map keys sorted by escaped UTF-8 bytes; floats always float64 with `NaN` normalized and `-0.0` kept distinct from `0.0` (float64-faithful, ruling R12); integers in the smallest form within ±(2⁵³−1); tuples as arrays. Replay compares values by this encoding (§6.2, ruling R31).
- A domain event's projection is `[ordinal, type, version, t, payload, deltas]`, with `t` always encoded as float64 (ruling R9) with presentation payload fields removed and delta operations on presentation state fields removed (S7). `seq` and observer events are excluded.
- The initial-state projection is the canonical encoding of the activation snapshot with presentation fields removed, entities sorted by id.

### 9.2 Digest

`SemanticDigest` subscribes to `"*"` and feeds `hashlib.blake2b(digest_size=32)` with the initial-state projection, then each event projection, each prefixed by its length as u64 big-endian. Attaching it after activation raises. It is enabled by `TraceRecorder` at both levels and by `env.enable_digest()`. `env.fingerprint()` returns `{digest, kpis}` with KPI scalars namespaced by scope (§12).

### 9.3 Provenance and manifest

- `Provenance(model, source, inputs, dependencies)`: hash strings or `UNAVAILABLE` (default).
- `RunManifest` holds the C1.6 fields. It has two parts (S14):
  - **requested**, fixed by activation (traces store the fields known at attachment in the header and the rest in the `INITIAL` record, U1): versions, platform, dependencies (provenance, else the installed-distribution listing, marked as such), RNG derivation id, seed (decimal string), parameters, time unit, warm-up;
  - **final**, known at the end: stopping policy (`{"type": "horizon", "horizon": h}` from the last `run(until=h)`, `{"type": "exhaustion"}`, or `{"type": "event"}` for `run(until=<simpy.Event>)`, which cannot be reproduced from the manifest and therefore makes it incomplete, ruling R11), opaque sampler owners, `complete` (no unavailable field and no opaque sampler).
- `env.manifest()` returns the current merged view; traces store the requested part in the header and the final part in the footer (§11).
- Volatile metadata (wall-clock start, host, durations) is separate.

## 10. Preparation and activation

Implements C1.10.

- **Prelude.** Events emitted before activation are delivered to subscribers present at the time and recorded by a trace recorder in a `PRELUDE` record; they are not part of the projection.
- **Initializers** (S10, T3). `env.on_activate(fn)` registers a plain function. While an initializer runs, **scheduling any SimPy event raises** (the check is on `Environment.schedule` itself, so processes, timeouts and manually succeeded events with callbacks are all covered), and `env.now` must be unchanged afterwards. The only exception is an internal context used by `place_now`, which allows the bookkeeping event of an immediately granted node request; a test proves that bookkeeping changes no entity state.
  - `FleetCoordinator` replaces its `_initial_placement` process with an initializer calling `ResourceBasedTrafficManager.place_now(agv, node)`, which requests the node resource and requires an immediate grant (raises on conflict).
- **Initial state** (T9). Digest and recorders *request* the projection when they attach (before activation). The projection becomes *active* only after initializers ran, the snapshot was captured and listeners were notified; prelude events never get ordinals, and the first domain event after activation has ordinal 0.
- **Command queue.** Components opt in with `@deferrable`. In SP1: `FleetCoordinator.submit` and `cancel`; `create_order` is not deferred and attaches the order at once, so the order has its id immediately and its `entity.created` is a prelude event when called before activation (ruling R18). Before activation, calls append to one queue and return; submitted orders report `OrderStatus.PENDING_ACTIVATION`. At activation the queue drains in order at time 0, before `env.run` processes any scheduled event. An exception stops activation and propagates from `env.run`; remaining commands are dropped.
  - `ShopFloor.add` and `PreShopPool.add` are not deferrable; they only schedule processes, which SimPy already orders by insertion.
- `env.activate()` is idempotent; registrations and deferrable calls after activation run immediately.

## 11. Trace format (D53)

### 11.1 Container

```
file   = magic ("SIMTRACE", 8 bytes) format_major(u16) format_minor(u16) record* trailer?
record = length(u32) type(u8) crc32(u32, of payload) payload
```

| Type | Payload (msgpack; `CHUNK` is zlib-compressed msgpack) |
|---|---|
| `HEADER` (one, first, written when the recorder attaches) | features (required, optional), catalog, kinds and state schemas, the requested-manifest fields known at attachment (versions, platform, dependencies, RNG derivation, seed, provenance), recording level, chunk limits, volatile metadata (separate map) |
| `PRELUDE` (zero or more, before `INITIAL`) | prelude events, in bounded records sealed like chunks (U1) |
| `INITIAL` (one, at activation) | initial state, the activation cursor (S26), and the requested-manifest fields fixed only at activation (parameters, time unit, warm-up) (U1) |
| `CATALOG_EXT` | types or kinds first used after the header, with their epoch |
| `CHUNK` | `{first, last, t_start, t_end, epoch, snapshot, events}`; each event `[seq, ordinal, type, t, payload, deltas]` |
| `INDEX` | the committing entry for the preceding chunk: offset, length, cursors, times, epoch |
| `KPI` | KPI series samples and, at the end, scalars |
| `FOOTER` (last when complete) | outcome (`completed`, `cancelled`, `failed`), final cursor, final manifest, fingerprint, full chunk index, catalog epoch offsets |

`trailer` = footer offset (u64) + `SIMTEND\0`.

- **Cursors.** A cursor is `(t, seq)`. The activation cursor is `(t_activation, -1)` and denotes the initial state, so a trace with no domain events is still seekable.
- **Commit rule.** A chunk is visible only once its `INDEX` record follows it (S17).
- **Damage** (S17). A short or CRC-failing record that is the last record is an incomplete tail: the reader reports `truncated` and ignores it. A failing record followed by further valid records is corruption: the reader raises `TraceCorrupted`.
- **Limits** (S17), enforced by readers in both languages: record size 64 MiB, decompressed chunk 256 MiB, nesting depth 64, collection length 10⁷, index and footer entry counts consistent with the file size. All configurable for trusted local files.
- Compression is zlib (`deflate`), available in Python's standard library and in browsers through `DecompressionStream("deflate")`.

### 11.2 Writer

- `TraceRecorder(env, path, *, level="full"|"kpi", chunk_limits=None)`; attaching after activation raises.
- **Publication** (S13, T6, T7).
  - The simulation thread appends completed events to the open chunk buffer and **seals** it itself when the event-count, byte or simulated-time limit is reached; the writer thread seals it when the latency limit is reached, independently of further simulation progress.
  - Sealed chunks go to one FIFO publication queue served only by the writer thread, which writes every record of the file (header, prelude, initial, catalog extensions, chunk then its committing index, KPI, footer) in queue order. There is a single ordered path to the file.
  - The writer computes each chunk's start snapshot by applying the previous chunk's deltas to its own copy of the replay state; it never reads live simulation objects, nor objects the emitter still owns: the simulation thread hands it the delta operations as encoded, unchanged when they are a tuple of immutable wire values, else as an immutable copy taken while encoding (R30, §6.1). The digest shares its encoded tail with the recorder only in the first case.
  - **Backpressure.** Pending sealed bytes are bounded (default 64 MiB). When the bound is reached, the simulation thread blocks on its next append until the writer drains; observers cannot change results, so blocking is safe. The block is reported as a `log` warning with the time spent blocked. The header is written at attachment and prelude events are sealed into bounded `PRELUDE` records like chunks, so the writer can always drain, including before activation (U1).
  - **Oversized batches** (U2). A single sealed batch larger than the bound (for example one event above `max_pending_bytes`) is admitted only when the queue is empty, after waiting for it to drain. The effective memory bound is therefore `max(max_pending_bytes, largest single batch)`, and the largest batch is itself limited by `max_event_bytes` and the chunk limits.
  - **Failures.** An exception in the writer thread is latched and re-raised in the simulation thread at its next append and at `close()`; the trace then ends without a footer (incomplete), and `close()` raises.
  - **Close barrier.** `close()` seals the open chunk, enqueues the KPI scalars and the footer, and waits until the writer has written everything; a successful `close()` means all accepted records precede the footer. Repeated `close()` is a no-op.
- Chunk limits (`ChunkLimits`): 10,000 events, 1 MiB uncompressed, 1.0 s latency, 256 KiB per event, optional simulated-time window.
- `close()` writes the KPI scalars and the footer. The outcome is `failed` if `env.run` raised, `cancelled` if the run was interrupted (`KeyboardInterrupt` turned into `StopSimulation` by `Environment.step`), else `completed`. Several `env.run` calls continue the same trace; the footer's final manifest records the last stopping policy.

### 11.3 Reader

- `Trace.open(path, *, limits=None)`: header, initial state, index (footer, else a scan), footer. API: `manifest` (always the merged view of the requested part and, when the footer exists, the final part; `manifest_final` says which, T15), `catalog`, `outcome` (None without footer), `truncated`, `cursor_range`, `state_at(cursor)`, `events(start, end)`, `kpis()`, `fingerprint`.
- `check()` validates the container (CRCs, index, limits). `verify()` recomputes the digest from initial state and domain events for `full` traces and compares it with the footer; for `kpi` traces it returns `"not_verifiable"` (S26).

### 11.4 TypeScript conformance reader (G2)

- `studio/` is a pnpm workspace (Vite, Vitest, strict TypeScript) with one package, `@simulatte/trace`, depending only on `@msgpack/msgpack`.
- **Asynchronous API** (S15): `openTrace(source: Blob | ArrayBuffer): Promise<Trace>`; `trace.prepare(cursor): Promise<void>` loads and decompresses the chunk; `trace.stateAt(cursor)` is synchronous on a prepared chunk and throws otherwise. This matches the viewer's prepare/render split (C7.1).
- Fixtures (S23, T13): generated by `tests/fixtures/traces/generate.py` with explicit seeds, fixed volatile metadata, explicit provenance and event-count chunk limits only; regeneration compares canonical content, not bytes. A separate set of **frozen binary fixtures** is committed once and never regenerated, to catch decoder regressions. Fixtures include hostile keys (`__proto__`, `constructor`, `prototype`, `~x`, nested), non-finite floats and a truncated file; default-generated seeds appear only in the frozen fixtures.

## 12. KPIs and collectors (D52)

### 12.1 Declarations and scope

```python
KPI(name="flow_time", unit="time", kind=("series", "scalar"), observation="job",
    cohort="completed_in_window", aggregation="mean", clip="none",
    censoring="exclude", ema_reset=False, empty=None, description="...")
```

- `Collector` declares its KPIs and subscriptions, keeps its own state, emits `kpi.sample`, and exposes results as attributes. It reads simulation objects only through pure getters (§13).
- **Scope** (S18). Every collector is bound to an owner entity (`ShopFloor`, `FleetCoordinator`, or a `Server` for server series) and filters events by the owner fields (`shopfloor`, `fleet`, `server`). Scalars and samples are namespaced `"<scope id>/<kpi name>"`; fingerprints therefore never merge two systems' results.

### 12.2 Window

`env.configure_kpis(warmup=...)` before activation sets the warm-up (default 0), recorded in the requested manifest. The window follows C1.8 for the stopping policy; SP1 has `horizon` and `exhaustion` only.

### 12.3 Replacing the old protocols

| Old | New |
|---|---|
| `MetricsCollector`, `ShopFloor(metrics_collector=...)`, `set_metrics_collector` | `Collector` subclasses, `collector.attach(env)` |
| `EMAMetricsCollector` (default on every `ShopFloor`) | `EMACollector(shopfloor, alpha=0.01)` with the same `ema_*` attributes; `ShopFloor` attaches one by default (`default_metrics=False` to opt out) as `shopfloor.metrics` |
| `TimeSeriesCollector`, `collect_time_series=True`, `DefaultTimeSeriesCollector` | `ShopFloorTimeSeries(shopfloor)` with `wip_ts`, `job_count_ts`, `throughput_ts`, `lateness_ts`, `plot_*` |
| `CurrentWorkLoadCollector`, `collect_workload=True` | `CurrentWorkloadCollector(shopfloor)`: remaining processing work decreases at `operation.completed` (using the operation's processing time), not at release, so a yielding after-operation hook that holds the server does not count completed work as remaining (S19). This intentionally corrects the old collector when after-operation hooks hold the server (T12): with 5 units finishing at time 5, held until 15 by a hook, and a 3-unit job arriving at time 6, the old series is `[(0, 5), (5, 0), (6, 8)]` and the new one is `[(0, 5), (5, 0), (6, 3)]`. Without such hooks, parity with the old series holds. |
| `Server(collect_time_series=...)` | `ServerTimeSeries(server)` with `qt`, `ut`, `plot_qt`, `plot_ut`; `retain_job_history` stays on `Server` |
| `OrderMetricsCollector`, `EMAOrderMetrics` | `OrderEMACollector(fleet)`, default on `FleetCoordinator` (`default_metrics=False`) |
| `IntralogisticsTimeSeriesCollector`, `DefaultIntralogisticsCollector` | `FleetTimeSeries(fleet)` (`fleet_utilization_ts`, `pending_orders_ts`, `throughput_ts`, `inventory_ts` keyed by warehouse id, `plot_*`), from `agv.state_changed` events |
| builder flags `collect_time_series`, `collect_workload` | kept; they attach the new collectors |

`ShopFloorKPIs(shopfloor)` and `FleetKPIs(fleet)` add window-aware KPIs. Their time-weighted utilization accumulates from `job.granted`/`job.released`, clipped at the window; the core `Server.worked_time` and `utilization_rate` keep their crediting, because dispatching rules read them.

## 13. Observer purity

- `AGV.utilization()`, `state_percentage()` and `time_allocation()` become pure.
- `TrafficManager.check_path` stops logging.
- Event construction calls no user code (§6.1). A test with a **counting, stateful priority policy** confirms that recording changes neither the number of policy calls nor the schedule (S9).
- The invariance test runs reference models under four observer configurations and asserts identical final state and, among instrumented ones, identical digests and common KPIs.

## 14. Benchmarks and CI (C1.9)

- **Equivalent work across versions** (S20). The overhead comparison feeds both versions the same pre-generated workload: a list of jobs (arrival time, SKU, routing as server indices, processing times, due date) generated once and replayed by a small feeder process that calls `PreShopPool.add`/`ShopFloor.add` with constructors present in both versions. Job and operation counts are asserted equal across versions. **Sampling cost** is benchmarked separately (T14): a router-only workload generates a fixed number of jobs (no processing) in both versions, so stream binding and sampler overhead are measured with controlled draw counts on CPython and PyPy; its budget is proposed in the G3 report.
- Workloads: a 10-server job shop with LumsCor (CI size and full size), a congested variant at utilization 0.95, and the advanced intralogistics example scaled up (G4 only).
- `benchmarks/run.py` measures median and spread of wall time over repeated runs after warm-up runs (PyPy warm-up controlled explicitly), peak memory in separate runs, trace size, chunk count, and seek latency percentiles.
- CI installs `simulatte==0.12.0` in a second environment and runs the same workload in the same job. Mode `none` fails CI above 3 % median overhead plus the noise band calibrated in G3. Modes `default_logging` and `kpi` get budgets at the end of G4 (S21).
- Runs on CPython 3.14 and PyPy 3.11.

## 15. The `queue_length` fix (D49)

Reproduced on `426d1a9`: `Server.request` logs `len(self.queue) + 1` after constructing the request, but SimPy's request constructor has already appended it (and granted it when a slot was free), so the value is one too high in both cases. `job.queued` carries `len(self.queue)` after insertion (§6.3). Regression test with capacity 1 and 2.

## 16. Migration, docs and examples

- `CHANGELOG.md` 0.13 entry with a migration section: `Environment` arguments and seed range, logging, entity ids and `name=`, builder `prefix`, seeding, samplers and binding kinds, collectors and scopes, `LayoutGraph.nodes` type, `OrderStatus.PENDING_ACTIVATION`, intralogistics time parameters.
- Every task that breaks a gallery example or a docs `{ .run }` block updates both in the same change (the docs gate requires byte equality).
- Docs: rewrite `docs/tutorials/logging.md`; update architecture, API reference and the PyPy page; add a page on events and traces; update `skills/` and `docs/ai-skill.md`.

## 17. Testing

Beyond per-feature tests:

- Replay equals live state at intermediate cursors (not only at the end), for production and intralogistics, including direct `Server` use, immediate grants, active motion across chunk boundaries, and creation after activation.
- Determinism in fresh processes across `PYTHONHASHSEED` values.
- Python and TypeScript replay equality on fixtures.
- Damage handling: incomplete tail, interior corruption, chunk without its index, unknown required feature, oversized records.
- Observer invariance, including the counting priority policy.
- Emission rules: domain event from a subscriber, observer event with deltas, re-emitted instance.
- Activation: initializer scheduling, `submit` then `cancel` before activation, a failing queued command, placement conflicts.
- RNG and binding: stream independence, shared descriptions, all three shop types, legacy custom routing, contextual callbacks, `Runner` parallel versus sequential.
- KPIs: windowed estimands on hand-computed cases; collector parity with the old collectors on reference models, including a yielding after-operation hook; two systems sharing one environment.
- Manifest across several `run` calls, reopened from the trace.

## 18. Risks specific to SP1

| Risk | Mitigation |
|---|---|
| Emitting-site boilerplate across many sites | One helper per component; guard pattern reviewed in G1 before G4 multiplies it |
| An emitting site evaluates user code or forgets the guard | Emission rule §6.1, counting-policy invariance test, lint test for the guard pattern |
| Collector parity after the rewrite | Parity fixtures recorded from the old code before deletion |
| Benchmark noise | Same workload in both versions, same job, medians with spread, calibrated band |
| The writer thread adds concurrency to a single-threaded library | The thread touches only immutable buffers and its own replay state; the simulation thread waits only under backpressure (§11.2) and at `close()` |

## 19. Open questions

None blocking.
