# SP1 inventory (branch feature/studio-global-spec). All paths under /Users/davide/Developer/simulatte; "src/" = src/simulatte.

Facts that change the SP1 framing
- src/__init__.py is EMPTY (0 bytes). There is no top-level export surface. Public API is the submodule paths (`simulatte.environment`, `.server`, ...). Only `simulatte.policies.__init__`, `simulatte.dispatching_rules.__init__` and `simulatte.intralogistics.__init__` export names.
- tests/intralogistics/test_import_audit.py forbids src/intralogistics/*.py from importing simulatte.server/shopfloor/job/typing/policies/psp/router/builders. `simulatte.environment` and `simulatte.logger` are allowed. The event bus and entity registry must therefore sit in a neutral module such as events.py or environment.py, not in a core-only module.
- FleetCoordinator never calls `ParkingArea.enter/leave` or `ChargingStation.swap` (grep shows only the definitions). Parking occupancy exists only if user code calls them. `NearestParkingPolicy` (policies.py:128) reads `available_capacity` without reserving.
- loguru is used only in src/logger.py. It is a hard dependency (pyproject.toml:32).

## 1. Entities
| Class | Kind | Id today | Constructed with env? | Viewer state |
|---|---|---|---|---|
| Server (server.py:~98) | resource | `_idx` int = index in `shopfloor.servers`, or -1 without shopfloor (server.py:126-130); no name; repr `Server(id=n)` | yes; auto-appends to `shopfloor.servers` | capacity, `users` (current_jobs), `queue` ordered by `req.key` (re-sorted in `sort_queue` 288, so "move" ops are needed), worked_time, qt/ut series |
| ProductionJob/BaseJob (job.py:26,366) | transient | `str(uuid.uuid4())` job.py:79; logs use `id[:8]` | yes (`env=`); created in Router.generate_job (router.py:153) and directly in tests | sku, routing and processing_times, due_date, current_server, created_at, psp_exit_at, servers_entry_at/exit_at, done, finished_at, priority (policy callable) |
| PreShopPool (psp.py:18) | container | none | yes (+shopfloor) | ordered deque of jobs |
| ShopFloor (shopfloor.py:650) | orchestrator | none | yes | jobs (a set), `wip` dict per server, jobs_done, total_time_in_system, EMA values |
| Router (router.py:26) | generator | none | yes; starts a process in ctor (:114) | none (a source) |
| AGV (agv.py:55) | vehicle | `agv_id` param, else `agv-{uuid4 hex[:8]}` (:66) | yes | current_node, state, battery.level, current_load, state_durations, assigned order |
| Warehouse (warehouse.py:17) | facility | `name` (required) | yes | inventory levels (simpy.Container per SKU), slots in use and queue, totals; no location of its own (input/output bay Nodes) |
| ChargingStation (charging.py:16) | facility | `name` | yes | slots in use and queue, swap-pool level, totals; `node` |
| ParkingArea (parking.py:15) | facility | `name` | yes | parked AGVs (`_agv_requests`), available_capacity; `node` |
| TransferOrder (order.py:25) | transient | `uuid4` default_factory (:32); a dataclass, so unhashable | NO env (`created_at` passed in); made by `FleetCoordinator.create_order` (fleet.py:158) or directly (`ReorderPointPolicy` policies.py:208) | status, assigned_agv, dispatched/picked/delivered_at, sku, qty, origin, destination |
| FleetCoordinator (fleet.py:73) | orchestrator | none | yes | pending queue, active missions |
| ResourceBasedTrafficManager (traffic.py:58) | manager | none | yes; its ctor creates one simpy.Resource per `graph.nodes` (:78) | node occupancy, intents, pending requests |

Definitions rather than entities (no env):
- Node, Arc (frozen dataclasses, graph.py:12,19). `Node.id` is a user string. Node x,y are the only coordinates in the codebase.
- LayoutGraph, SKU (id str), AGVType (frozen, holds `load_time_fn`/`unload_time_fn`, agv.py:40), Battery (no env, no id), speed profiles.

Not entities: policies (Slar, LumsCor, Draco, ConWIP, ContinuousRelease), dispatching rules, Scenario, SkuFamily.

Gaps:
- Server and PSP have no name or position (SP2 will add these).
- `Server._idx` is used in logs (`server_id`) and in tests (tests/core/test_shopfloor.py:366, tests/core/test_component_logging.py:39).
- Builders (builders.py, 9 `build_*_system` functions) have no `prefix` argument. `scenario: Scenario = Scenario()` is a shared default argument (builders.py:~30, 75, ...).

## 2. State transitions (candidate events)
Per-operation sequence in `ShopFloor.main` (shopfloor.py:1004-1127). Each step is a separate phase:
1. Log "queued" (:1036), then `server.request` (server.py:196). The `ServerPriorityRequest` ctor runs `Put.__init__`, which calls `_trigger_put` and `sort_queue`, so it may be granted before `request()` logs "entered queue" (:218). The `request()` body sets `servers_entry_at` (:215) and `current_server` (:216).
2. `yield request`: the server slot is granted.
3. Before hooks, then `material_coordinator.ensure`.
4. `server.process_job` (server.py:261): the timeout, then `worked_time += pt` at the END (:286).
5. `wip_strategy.complete_operation` (:1065), then ts-collector `on_operation_completed` (:1069). This happens BEFORE `servers_exit_at` is stamped.
6. After hooks, log "completed op" (:1081), `job_processing_end.succeed` (:1091). The server is still held.
7. `with` exit calls `Server.release` (server.py:234), stamping `servers_exit_at` (:246) and scheduling the grant to the next queued request.
8. `_fire_processing_end_callbacks` (:1095). Slar, Draco and LumsCor decide here, before the Release event grants the next request.
9. Job finish (:1098-1127): `finished_at`, `done`, `jobs.remove`, `jobs_done.append`, EMA `record` (:1117), ts `on_job_finished` (:1121), user callbacks, `signal_job_finished` (:1127).

Other sources:
- **Router** (router.py:131-187): inter-arrival draw, then timeout, then SKU, routing, service times, due date. Job created (:153), then psp.add or shopfloor.add, in the same instant.
- **PSP.add** (psp.py:70): append, log, `_signal_new_job` (:154). Arrival callbacks run synchronously (starvation_avoidance, ConWIP and ContinuousRelease `on_arrival_release`, Draco). They can call `psp.release` (remove, then `shopfloor.add`) before `new_job.succeed`. One instant therefore holds: created, psp_enter, psp_exit, shopfloor_enter, process spawn.
- **PSP.remove** (psp.py:93) writes `psp_exit_at` (:116). **ShopFloor.add** (:927, writes at :961) overwrites it. Slar `_postponed_release` (slar.py:175) and LumsCor `_postponed_release` (lumscor.py:155) remove the job from the PSP, wait 0.001, then add it. A job is in neither pool nor floor in between, and `psp_exit_at` is written twice.
- **Draco.decide_next_job** (draco.py:240) mutates `_forced_at_server` (force-pin). A PSP winner is removed, then added to the shopfloor.
- **Policies**: ConWIP, ContinuousRelease, LumsCor periodic/starvation and Slar `_consider_release` only call psp.release/remove and shopfloor.add. Triggers (triggers.py:34,67,97) are SimPy processes with no state of their own.
- **Dispatching rules**: pure `(job, server) -> float` callables, plus `_StateMemo` (focus.py:168, a cache) and `Draco._forced_at_server`. No transitions.
- **FleetCoordinator**:
  - `submit` (fleet.py:176): status PENDING, `_pending_queue.append` (:194-195).
  - `cancel` (:201): status CANCELLED, interrupt.
  - `_dispatch` (:325): status DISPATCHED, AGV to TRAVELING_EMPTY, `_active_missions`, `_agv_mission`.
  - `_run_mission` (:350-545) sets the following. `order.status` is set in about 20 places, with no log for FAILED (:362,405,411,826,901).
    - status PICKING and WAITING_LOAD (:374-375)
    - `pick` with `_committed_picks`
    - `current_load` set (:381), `picked_at`, load_time wait (:384)
    - IN_TRANSIT and TRAVELING_LOADED (:393-395)
    - DELIVERING and WAITING_UNLOAD (:420-421)
    - `put`, `current_load = None`, unload wait, `delivered_at`, COMPLETED (:426)
    - repositioning travel
    - IDLE (:465)
    - the Interrupt branch (:470-545): cargo recovery, re-queue PENDING, IDLE
  - `_travel` (:550-700) sets the following. Position changes only at segment end: `current_node = next_node` at :689, after `timeout(travel_time)` at :686. During a segment the AGV is still at the previous node, which matches the C1.2 motion model.
    - reroute and delay waits (:599, 611)
    - `register_intent` (:621)
    - `enter_node` (:684) or `_enter_with_timeout` (:705, with its own sub-process and backoff)
    - battery `deplete` (:687)
    - `leave_node` (:688)
    - STRANDED (:651, 660)
    - `traffic.cancel` (:657, 693, 700)
  - `_charge_agv` (:757): `_low_battery_flags` set (:766), travel, CHARGING (:794), `station.recharge`.
  - `_drop_cargo` (:805), `_return_cargo_to_origin` (:814), `_check_pending_queue` (:874, dispatch or FAILED after retries), `_pending_retry_loop` (:910).
  - `_initial_placement` (:749) is a process started in the ctor (:148). It asks the traffic manager for node placement at t=0. This is the "initializer" case of C1.10.
- **AGV.transition_to** (agv.py:86): state, `state_durations`, `_state_entered_at`. It is always called through `FleetCoordinator._transition_agv` (fleet.py:317), which also notifies the ts collector.
- **Warehouse**:
  - `pick` (:54): waits on the container get (inventory deducted), `on_committed`, slot request, timeout, `total_picks`.
  - `put` (:83): slot, timeout, container put.
  - Both are multi-phase (inventory level changes before the slot is held).
- **ChargingStation**: `recharge` (:57) takes a slot, a timeout, then `battery.recharge`, then updates metrics. `swap` (:101) takes a slot, `_swap_pool.get`, a timeout, then sets `battery.level = capacity` directly, then starts the `_replenish_pool` process.
- **ParkingArea**: `enter` (:41) and `leave` (:55). Never called by core code.
- **Traffic**: `FreeTrafficManager` (traffic.py:36) is a no-op. `ResourceBasedTrafficManager`:
  - `place` (:88)
  - `enter_node` (:121, which handles Interrupt)
  - `leave_node` (:145)
  - `register_intent` (:118)
  - `cancel` (:161)
  - `check_path` (:94, a query that logs)

## 3. Logging today
- Env API: `Environment.debug/info/warning/error/log_history/logger/close/__enter__` (environment.py:70-137). Constructor args `log_file, log_format, log_history_size, log_db_path` (:25-30).
- src call sites: 42 `env.*` log calls. Debug calls: server 3, psp 2, shopfloor 4, router 3, agv 1, parking 3, warehouse 4, charging 4, traffic 3, fleet 15. Of the fleet calls, 2 are warning (:459, 760) and 7 are error (:573, 587, 602, 614, 652, 661, 678). All others are debug.
- The default global level is INFO and is class-level (`SimLogger._global_level`, logger.py:~380), not per environment. Only the 9 fleet warnings and errors fire by default. Debug call arguments are evaluated eagerly anyway:
  - `job.priority(self)` at server.py:~224. This is an extra policy call.
  - `job.total_queue_time`, which can raise ValueError, at shopfloor.py:1105-1113.
  - `sum(wip.values())` at shopfloor.py:~957.
  - `server.py:~226` computes `queue_length=len(self.queue)+1` after insertion. This looks off by one (unverified).
- logger.py (700 lines):
  - LogEvent (:118), EventHistoryBuffer (:128, `query` :150)
  - SQLiteEventStore (:182, `query` :264, `execute_sql` :331)
  - SimLogger (:369): per-env loguru handler filtered on `env_id` (uuid4, :417), `set_level` / `get_level` classmethods, `enable_component` / `disable_component`, `query_sql` (:592), `execute_sql` (:633), `close`
  - weakref finalizer plus a loguru lock re-entrancy guard (:26-68, 678). This exists for a PyPy deadlock.
  - `_patch_loguru_default_sink` (:71) mutates loguru's global default handler at import.
  - the sink opens the file in append mode per message
- Tests depending on log content (message substrings and `extra` keys):
  - tests/core/test_component_logging.py (28 SimLogger uses, 12 log_history). Asserts "entered queue", "processing started", "released", "entered shopfloor", "finished", "queued at server", "completed op" and `extra` keys job_id, server_id, queue_length, priority, processing_time, time_at_server, wip_total, jobs_count, makespan, lateness, total_queue_time, op_index, sku.
  - tests/intralogistics/test_logging.py (component presence for FleetCoordinator, Warehouse, AGV; `disable_component`).
  - tests/core/test_logger.py (447 lines: 32 SimLogger, 19 LogEvent, 9 EventHistoryBuffer, 27 loguru, file text and json output at :300 and :324, history size at :355).
  - tests/core/test_logger_sqlite.py (550 lines: 22 SimLogger, 20 SQLiteEventStore, 13 query_sql, 11 execute_sql, 13 `log_db_path`).
  - tests/core/test_logger_finalizer.py (66 lines, loguru/finalizer internals).
  - tests/core/test_runner.py (log_dir, json log format, :207-290).
- Docs: docs/tutorials/logging.md (6 SimLogger, 6 log_history, 9 log calls), docs/api/utilities.md, docs/introduction/architecture.md, docs/running-on-pypy.md:41 (SQLite), CHANGELOG.md (loguru).
- examples/: no log API use. The `Environment(...)` kwargs (log_*) appear in 3 test files (18 uses) and 2 doc files (5 uses).

## 4. Randomness
Only the module-level `random` is used. There is no numpy or `numpy.random` in src/ (gymnasium's `np_random` appears only in a docstring and in tests/experimental).
- Draws:
  - distributions.py: `Exponential` :64, `Erlang` :87 (gammavariate), `TruncatedErlang` :120 (rejection loop, so the draw count varies), `LogNormal` :146, `Uniform` :166, `pure_job_shop_routing` :224-225 (randint, sample), `general_flow_shop_routing` :264-265.
  - router.py:135 (`random.choices` for the SKU).
  - runner.py:80 (`random.seed(seed)`).
- Router draw order per job (router.py:132-151): inter-arrival (before the timeout), SKU, routing, one service time per op, due-date offset. The inter-arrival draw happens at the start of each loop, so the order is a fixed sequence.
- Distributions are frozen dataclasses, i.e. already descriptions. Their `__call__` hits global state.
- Sharing:
  - `Scenario.build_router` (scenario.py:304) passes the SAME `family.service_time` object to every server (`{server: f.service_time ...}`, :312). It uses `self.arrival_process(rate)` for arrivals and the same `due_date_offset` for all families without their own.
  - Module-level shared objects: `_DEFAULT_SERVICE_TIME` (scenario.py:53), `Scenario.due_date_offset = Uniform(30,45)` (:~150), `SkuFamily()` default, `Scenario()` as a default argument in all 9 builders.
  - Routing factories close over a `servers` tuple and draw from global `random`.
- Intralogistics has no built-in stochasticity. Randomness enters only through user lambdas:
  - `AGVType.load_time_fn` / `unload_time_fn` (zero-argument, agv.py:51-52)
  - `Warehouse.pick_time_fn` / `put_time_fn` (`(sku, qty)`)
  - `ChargingStation.recharge_fn`
  - these are called at fleet.py:384, 424, 515 and warehouse.py:74, 92.
  - Examples and tests pass deterministic lambdas. examples/intralogistics_advanced.py uses `random` (1 use).
- Tests seeding global random: tests/core/test_builders.py (8 `random.seed(42)` calls), test_distributions.py (about 13), test_scenario.py (7), test_runner.py (7 `random` uses; the test builders read `random.random()`). Of the tests/examples/docs files, 27 total use random seeding or draws, including all gallery examples (2 each).
- Runner (runner.py:70-94): `random.seed(seed)` runs per task in the worker. Parallel mode uses a `spawn` Pool.
- Non-random id sources to remove: job.py:79, order.py:32, agv.py:66, logger.py:417 (env_id).

## 5. Observational reads that mutate, and accounting read by behavior
- `AGV.utilization()` (agv.py:104), `state_percentage` (:112), `time_allocation` (:119) all call `_flush_current_state` (:126), which mutates `state_durations` and `_state_entered_at`. This splits float accumulation, so totals can differ in the last bits depending on how often it is read.
  - Callers: fleet.py:263 (`fleet_utilization` property), :272 (`fleet_time_allocation`), :287 (`agv_report`), and `DefaultIntralogisticsCollector.on_agv_state_changed` (metrics.py:113). That last one flushes EVERY AGV on every state change, so attaching or removing the collector changes AGV state accounting.
  - Examples and docs also call these (39 test, 12 example, 24 doc uses of utilization / time_allocation / agv_report).
- `Server.utilization_rate` (server.py:158) is pure, but behavior depends on it: `raghu_rajendran` reads it live (dispatching_rules/composite.py:66, unless the `utilization` override is given). `worked_time` is credited only at operation end (:286), so in-progress work is not counted. C1.8 plans to change this accounting.
- `Server.sort_queue` (:288) mutates `req.key`. It is public and is called from `_trigger_put` (:314).
- Debug-log argument evaluation (see section 3): `job.priority(self)` at server.py:~224 runs a user policy even when the level filters the message. Draco's `priority_policy` goes through `_StateMemo.get` (a cache, keyed on a fingerprint, so semantically pure).
- Collectors read by behavior? No. Policies read `shopfloor.wip` (`fits_norms`, norms.py:73), `psp.jobs`, `server.queue`, `len(shopfloor.jobs)` and `job.previous_server`. These are core state.
- `TrafficManager.check_path` (traffic.py:94) is a query but logs.
- `CurrentWorkLoadCollector._snapshot` (shopfloor.py:588) reads jobs and `servers_exit_at` and compensates for the pre-release ordering with `skip_job` / `skip_server` (:611-630). It has a TODO to move the notification after the `with` block.

## 6. Existing collectors and metrics
| Item | Records | Wiring |
|---|---|---|
| `MetricsCollector` (shopfloor.py:131) | `record(job)` on finish | `ShopFloor(metrics_collector=...)`, `set_metrics_collector`; default `EMAMetricsCollector` (:371), called inline at :1117 |
| `EMAMetricsCollector` | ema_makespan, ema_tardy_jobs, ema_early_jobs, ema_in_window_jobs, ema_time_in_psp, ema_time_in_shopfloor, ema_total_queue_time (alpha 0.01) | default on every ShopFloor |
| `TimeSeriesCollector` (:165) | 3 hooks: on_job_entered, on_operation_completed, on_job_finished | `time_series_collector=` or `collect_time_series=True`; called inline at :950, :1069, :1121 |
| `DefaultTimeSeriesCollector` (:429) | `wip_ts`, `job_count_ts`, `throughput_ts`, `lateness_ts`; `plot_wip/job_count/throughput/lateness` (:493-546, matplotlib) | |
| `CurrentWorkLoadCollector` (:569) | true remaining work; `wip_ts` | `build_floor(collect_workload=True)` (scenario.py:250); every builder has the `collect_workload` flag |
| Server series | `_qt` / `_ut` (server.py:117-118), `_queue_history`, `plot_qt` / `plot_ut` | `Server(collect_time_series=...)`, `retain_job_history` -> `_jobs` |
| `OrderMetricsCollector`, `EMAOrderMetrics` (metrics.py:21,26) | ema_fulfillment_time, ema_dispatch_delay, ema_travel_time_empty, ema_travel_time_loaded, ema_late_orders | `FleetCoordinator(order_metrics_collector=...)`, default EMA; recorded at fleet.py:428, 519 |
| `IntralogisticsTimeSeriesCollector`, `DefaultIntralogisticsCollector` (metrics.py:75,84) | `fleet_utilization_ts`, `pending_orders_ts`, `throughput_ts`, `inventory_ts` (dict keyed by Warehouse objects); `plot_fleet_utilization/throughput/pending_orders/inventory` | `FleetCoordinator(time_series_collector=...)`; hooks at fleet.py:186, 342, 389, 433, 522, 321 |

Also: `Dispatcher` protocol / `attach_dispatcher` (shopfloor.py:259, 851) and the fleet lifecycle hook registries (fleet.py:135-143).

Usage counts (tests / examples / docs, lines): ts collectors and flags 98 / 2 / 24 (test_shopfloor.py 68, test_builders.py 21); EMA 41 / 6 / 14 (test_shopfloor.py 12, test_metrics.py 29); intralogistics collectors 45 / 12 / 27. Examples: examples/intralogistics_advanced.py and intralogistics_intermediate.py use the collector and plot helpers (:359-362, :223-224).

## 7. Iteration-order hazards
- `Node` is a frozen dataclass with a str `id`, so its hash depends on PYTHONHASHSEED. `LayoutGraph._nodes` is a set (graph.py:29). `.nodes` returns a frozenset (:38). `ResourceBasedTrafficManager.__init__` iterates it (traffic.py:78), which fixes `_node_resources` dict order.
- traffic.py:98-110: `check_path` uses `set(path[1:]) & ...`, `conflict_nodes.extend(shared)` and `list(set(conflict_nodes))`. The logged `[n.id ...]` (:112) and `PathCheckResult.conflict_nodes` vary across hash seeds. They flow to `avoid=` in `_travel` (fleet.py:593), which re-sets them, so path choice is unaffected but the log and result order are not.
- `ShopFloor.jobs` is a `set[ProductionJob]` (shopfloor.py:771), hashed by identity. The only iteration is `CurrentWorkLoadCollector._snapshot` (:593-599), a float `sum` over jobs. Order can change float rounding.
- `FleetCoordinator._low_battery_flags: set[AGV]` (fleet.py:129) is only added to or discarded from, never iterated.
- `Focus` builds `frozenset` of servers (focus.py:338); only membership is tested (:428).
- Dijkstra and A* sort ties by `node.id` (pathfinding.py:35,78), so they are deterministic. `avoid_set` is membership only.
- `DefaultIntralogisticsCollector.inventory_ts` is keyed by Warehouse (insertion order, fine). `plot_inventory` sorts by `sku.id`.
- Dict-ordered containers (wip, `routing`, `servers_entry_at`) follow job routing order, so they are deterministic.

## 8. Public API surface affected (counts: tests / examples / docs lines)
- `Environment(` 486 / 14 / 36; with arguments 18 / 0 / 5 (all `log_*` kwargs). A new `seed` / `time_unit` argument touches only the constructor.
- `Runner(`: 13 / 0 / 4. It is the only code that seeds (`random.seed`, runner.py:80). Its `log_dir` / `log_format` args are used in 17 test lines.
- `Server(`: 492 / 1 / 10. A `name` arg and id changes touch `_idx` (7 / 0 / 1).
- `ProductionJob(`: 496 / 0 / 10. `.id` use is in 27 / 5 / 10 lines. Tests: test_job.py:105 (repr contains the id), test_component_logging.py (job_id in extra).
- `ShopFloor(`: 368 / 1 / 18. `PreShopPool(`: 106 / 1 / 3. `Router(`: 4 / 1 / 3.
- `build_*_system`: 125 / 41 / 94 (a `prefix` arg touches all of them).
- `Scenario`: 47 / 13 / 20. Distributions and routing: 56 / 5 / 12 for the distribution classes, 21 / 2 / 5 for the routing factories.
- Intralogistics: `AGV(` 80 / 3 / 3; `agv_id` 99 / 14 / 13; `TransferOrder(` 27 / 0 / 0; `create_order` 98 / 4 / 6; `Warehouse(` 125 / 7 / 7; `ChargingStation(` 28 / 1 / 1; `ParkingArea(` 17 / 3 / 3; `ResourceBasedTrafficManager(` 32 / 0 / 0; `FleetCoordinator(` 65 / 3 / 3; `AGVType(` 36 / 3 / 4; `*_time_fn` 304 / 22 / 23.
- Docs gates: tests/test_docs_run_blocks.py requires docs/examples/*.md `{ .run }` blocks to equal the examples/gallery_*.py files verbatim (7 pairs, plus intralogistics.md embeds 3 scripts). tests/core/test_gallery_examples.py runs the galleries. Any API change in them needs the example and its doc page updated together.
- SimLogger class-level API used by tests: `SimLogger.set_level` / `get_level` (many calls, global state; tests save and restore it).
