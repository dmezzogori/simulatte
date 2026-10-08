# SP1 Events and Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Simulatte stable entity ids, typed events with state deltas on an event bus, seeded RNG streams, a semantic digest, a seekable trace file readable from Python and TypeScript, bus-based KPI collectors and logging, and a CI overhead gate.

**Architecture:** New neutral core modules (`entities`, `events`, `rng`, `digest`, `provenance`, `trace`, `kpi`, `logsinks`) that depend only on `simulatte.environment` and each other; components attach themselves as entities and emit domain events behind an O(1) `env.wants` guard; observers (digest, recorder, collectors, sinks) subscribe to the bus. Work proceeds through four gates (G1 slice, G2 TypeScript reader, G3 budgets, G4 migration); a failed gate stops the plan for redesign.

**Tech Stack:** Python ≥3.11 (CPython 3.12–3.14, PyPy 3.11), SimPy, msgpack, hashlib/zlib (stdlib), pytest; TypeScript with pnpm, Vite, Vitest, `@msgpack/msgpack` for G2.

**Spec:** `specs/2026-10-08-sp1-events-trace-design.md` (parent: `specs/2026-10-08-studio-global-design.md`; inventory: `specs/research/2026-10-08-sp1-inventory.md`).

## Global Constraints

- `requires-python = ">=3.11"`; tests pass on CPython 3.12, 3.13, 3.14 and on PyPy 3.11 for `tests/core` and `tests/intralogistics` (plus new trace tests).
- Coverage gate stays `--cov-fail-under=99` with branch coverage.
- `uv run ruff check src tests` and `uv run ty check src` clean.
- Runtime dependencies: add `msgpack`; remove `loguru` (Task 18). No other runtime dependency.
- Wire integers within ±(2⁵³−1); floats float64 with explicit `+inf`, `-inf`, normalized `NaN`.
- RNG stream seed: `int.from_bytes(hashlib.blake2b(f"simulatte-rng-v1\0{seed}\0{name}".encode(), digest_size=16).digest(), "big")`; derivation id `"simulatte-rng-v1"`.
- Digest: `hashlib.blake2b(digest_size=32)`; each projection item prefixed by its byte length as u64 big-endian.
- Trace: magic `b"SIMTRACE"`, format 1.0, records `length u32 | type u8 | crc32 u32 | payload`, chunk payload zlib-compressed msgpack, trailer `u64 footer offset + b"SIMTEND\0"`.
- Chunk defaults: 10,000 events, 1 MiB uncompressed, 1.0 s wall-clock age of the oldest unpublished event, no simulated-time window.
- No-subscriber overhead ≤ 3 % median against `simulatte==0.12.0` plus a noise band calibrated at G3.
- Entity names must not contain `/` or `\0` and must not match `^<registered kind>-\d+$`.
- Gallery examples and `docs/examples/*.md` `{ .run }` blocks must stay byte-identical (`tests/test_docs_run_blocks.py`).
- Conventional commits on branch `feature/sp1-events-trace`. Signed commits; if 1Password locks, `--no-gpg-sign` and say so.

## Review Focus

- A subscriber that raises mid-delivery: the exception propagates out of `env.emit`, the nested-delivery queue is cleared, and the next `emit` works normally (Task 2).
- `env.run(until=10)` followed by `env.run(until=20)`: activation happens once, the trace continues seamlessly, and the manifest's stopping policy records the last horizon (Tasks 5, 9).
- A `TraceRecorder` or digest attached after activation: raises `RuntimeError` naming the problem, because the projection must start at activation (Tasks 7, 9).
- Names containing `/` or `\0`, which would make RNG stream names collide: rejected at attachment with `ValueError` (Task 3).
- A run that ends by exception or by `KeyboardInterrupt` (converted to `StopSimulation` in `Environment.step`): the trace footer records `failed` or `cancelled`, and the file reopens cleanly (Task 9).

---

## Gate G1: vertical slice

### Task 1: Wire values and canonical encoding

**Files:**
- Create: `src/simulatte/_wire.py`
- Modify: `pyproject.toml` (add `msgpack>=1.0.8` to `dependencies`), `uv.lock`
- Test: `tests/core/test_wire.py`

**Interfaces:**
- Produces: `freeze(value: object) -> Wire` (lists→tuples, dicts→`FrozenMap`, validates types and int range, raises `TypeError`/`OverflowError`); `class FrozenMap(Mapping[str, Wire])` (hashable, read-only); `canonical_pack(value: Wire) -> bytes` (sorted map keys by UTF-8 bytes, floats always float64, `NaN` normalized to `0x7ff8000000000000`); `unpack(data: bytes) -> Wire`; `Wire` type alias.

- [ ] **Step 1: Write failing tests**

```python
def test_freeze_converts_containers():
    v = freeze({"b": [1, 2], "a": {"x": 1.5}})
    assert isinstance(v, FrozenMap) and v["b"] == (1, 2)
    with pytest.raises(TypeError): v["c"] = 1

def test_freeze_rejects_non_wire():
    for bad in (object(), {1: "x"}, {1, 2}, b"bytes"):
        with pytest.raises(TypeError): freeze(bad)

def test_int_range():
    freeze(2**53 - 1); freeze(-(2**53 - 1))
    with pytest.raises(OverflowError): freeze(2**53)

def test_canonical_pack_sorts_keys_and_uses_float64():
    assert canonical_pack({"b": 1, "a": 2}) == canonical_pack({"a": 2, "b": 1})
    assert canonical_pack(1.0) == b"\xcb" + struct.pack(">d", 1.0)

def test_nonfinite_roundtrip_and_nan_normalized():
    assert unpack(canonical_pack(float("inf"))) == float("inf")
    assert canonical_pack(float("nan")) == canonical_pack(-float("nan"))
```

- [ ] **Step 2:** `uv run pytest tests/core/test_wire.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement with `msgpack.Packer(use_single_float=False)` over a pre-normalized structure (sort keys before packing; normalize NaN).
- [ ] **Step 4:** Tests pass; `uv sync` and `uv lock --check` pass.
- [ ] **Step 5:** Commit `feat(core): add wire value freezing and canonical encoding`.

### Task 2: Events, deltas, catalog and bus

**Files:**
- Create: `src/simulatte/events.py`
- Modify: `src/simulatte/environment.py` (own a bus; `emit`, `wants`, `seq`)
- Test: `tests/core/test_events.py`

**Interfaces:**
- Consumes: `freeze`, `FrozenMap` (Task 1).
- Produces:
  - `@dataclass(frozen=True, slots=True, kw_only=True) class Event: t: float = nan; seq: int = -1; deltas: Deltas = Deltas.EMPTY`; `class DomainEvent(Event): ordinal: int | None = None`; `class ObserverEvent(Event)`.
  - `event_type(name: str, *, version: int = 1, touches: Mapping[str, tuple[str, ...]] | None = None)` class decorator; sets `cls.type_name`, `cls.type_version`; registers in the global `CATALOG: Catalog`. `Catalog.get(name) -> type[Event]`, `Catalog.to_wire() -> Wire`, `Catalog.names() -> tuple[str, ...]`.
  - `class Deltas` (immutable tuple of ops) with `Deltas.EMPTY` and `Deltas.build() -> DeltaBuilder`; `DeltaBuilder.set/insert/remove/move/put/delete/create/retire(...) -> DeltaBuilder` and `.done() -> Deltas`; op tuples `("set", entity, field, value)`, `("insert", entity, field, index, value)`, `("remove", entity, field, value)`, `("move", entity, field, value, index)`, `("put", entity, field, key, value)`, `("delete", entity, field, key)`, `("create", entity, kind, state)`, `("retire", entity)`.
  - `apply_deltas(state: dict[str, dict], deltas: Deltas) -> None` (the reference replay used by the reader and tests).
  - `class EventBus`: `subscribe(handler: Callable[[Event], None], types: tuple[type[Event], ...] | Literal["*", "**"]) -> Subscription`; `Subscription.cancel()`; `wants(cls: type[Event]) -> bool`.
  - `Environment.emit(event: Event) -> None` stamps `t`, `seq` (and `ordinal` when `self._projection_active`) via `object.__setattr__`, then delivers; `Environment.wants(cls) -> bool`; `Environment.bus`.
  - Built-in observer types registered here: `LogEvent` (`"log"`), `KpiSample` (`"kpi.sample"`).
  - `Environment(debug: bool = False)`; debug mode validates payloads with `freeze` identity checks and raises if a subscriber changes `len(env._queue)` or an `env.rng` call counter during delivery.

- [ ] **Step 1: Write failing tests**

```python
def test_emit_stamps_time_and_seq(env): ...  # two emits at t=0 -> seq 0,1; after timeout(5) -> t==5
def test_wants_tracks_subscriptions(env):
    assert not env.wants(Ping); sub = env.bus.subscribe(lambda e: None, (Ping,)); assert env.wants(Ping)
    sub.cancel(); assert not env.wants(Ping)
def test_star_subscribes_to_domain_types_registered_later(env): ...
def test_nested_emits_delivered_fifo_after_current(env):
    # subscriber A emits Pong on Ping; B records order -> ["Ping", "Pong"], Pong.seq > Ping.seq
def test_subscriber_exception_propagates_and_bus_recovers(env):
    # raising subscriber -> pytest.raises; queue empty; a later emit reaches the other subscriber
def test_apply_deltas_all_ops(): ...  # insert/remove/move/put/delete/create/retire on a dict state
def test_duplicate_event_type_name_with_different_fields_raises(): ...
def test_debug_mode_rejects_subscriber_scheduling(env_debug): ...
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. `wants` is a `dict[type, int]` lookup; `"*"` registers in a separate set consulted for `DomainEvent` subclasses.
- [ ] **Step 4:** Run `uv run pytest tests/core/test_events.py tests/core -q` → PASS (existing tests unaffected).
- [ ] **Step 5:** Commit `feat(core): add typed events, deltas and event bus`.

### Task 3: Entities and lifecycle

**Files:**
- Create: `src/simulatte/entities.py`
- Modify: `src/simulatte/environment.py` (`env.entities`)
- Test: `tests/core/test_entities.py`

**Interfaces:**
- Consumes: Task 2 (`DomainEvent`, `event_type`, `Deltas`).
- Produces:
  - `class Entity` with `__init_subclass__(kind: str)` registering the kind; `kind: ClassVar[str]`; `state_schema: ClassVar[StateSchema]`; attributes `id: str`, `label: str`; `snapshot(self) -> dict[str, Wire]` (default reads `state_schema` fields through `_state_<field>()` methods or attributes).
  - `class StateSchema(Mapping[str, FieldSpec])`, `FieldSpec(wire_type: str, nullable: bool = False, collection: Literal[None, "list", "map"] = None)`.
  - `class EntityRegistry`: `attach(obj: Entity, *, name: str | None = None, label: str | None = None) -> str`; `retire(obj: Entity) -> None`; `get(entity_id: str) -> Entity`; `live() -> Iterator[Entity]` (attachment order); `snapshot() -> dict[str, dict[str, Wire]]` (sorted by id).
  - Events `EntityCreated` (`"entity.created"`: `entity`, `kind`, `label`) and `EntityRetired` (`"entity.retired"`: `entity`, `kind`).
  - Retired entities are held by `weakref` only.

- [ ] **Step 1: Write failing tests**

```python
def test_generated_ids_per_kind(env): ...      # two Widgets -> "widget-0", "widget-1"; a Gadget -> "gadget-0"
def test_name_becomes_id_and_duplicates_raise(env): ...
def test_reserved_pattern_rejected(env):       # name "widget-7" -> ValueError, also "gadget-0" for another kind
def test_slash_and_nul_rejected(env):          # names "a/b", "a\0b" -> ValueError
def test_attach_emits_created_with_state_delta(env): ...
def test_retire_removes_from_live_and_emits(env): ...
def test_snapshot_sorted_by_id(env): ...
```

- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): add entity registry, ids and lifecycle`.

### Task 4: RNG streams and samplers

**Files:**
- Create: `src/simulatte/rng.py`
- Modify: `src/simulatte/environment.py` (`seed`, `rng()`), `src/simulatte/distributions.py`, `src/simulatte/runner.py`, `src/simulatte/scenario.py` (shared defaults), tests that seed or call distributions: `tests/core/test_distributions.py`, `test_scenario.py`, `test_builders.py`, `test_runner.py`, `test_router.py`
- Test: `tests/core/test_rng.py`

**Interfaces:**
- Produces:
  - `derive_seed(seed: int, name: str) -> int` (Global Constraints formula); `RNG_DERIVATION = "simulatte-rng-v1"`.
  - `Environment(seed: int | None = None)`: `None` → `int.from_bytes(os.urandom(8), "big")`; `env.seed: int`; `env.rng(name: str) -> random.Random` (cached).
  - Every distribution class gains `sampler(self, rng: random.Random) -> Callable[[], float]`; `__call__` is removed. `pure_job_shop_routing(...)`/`general_flow_shop_routing(...)` return routing callables taking `rng: random.Random`.
  - `as_sampler(value: Distribution | float | int | Callable[[], float], env: Environment, stream: str, *, owner: str) -> Callable[[], float]`; marks `env.opaque_samplers = True` and records `owner` in `env.opaque_sampler_owners` for opaque callables.
  - `Runner._run_single` builds `Environment(seed=seed, ...)`; no `random.seed`.
  - Debug mode (Task 2) counts `env.rng` draws through a thin wrapper so it can reject subscribers that draw random numbers; the wrapper is installed only when `debug=True`.

- [ ] **Step 1: Write failing tests**

```python
def test_derive_seed_is_stable():
    assert derive_seed(42, "router-0/interarrival") == <value computed once and pinned>
def test_streams_independent_and_reproducible(): ...   # same seed+name -> same sequence; different names differ
def test_seed_none_draws_and_records(): ...
def test_shared_distribution_yields_independent_samplers():
    d = Exponential(1.0); a = d.sampler(env.rng("a")); b = d.sampler(env.rng("b")); assert [a() for _ in range(3)] != [b() for _ in range(3)]
def test_as_sampler_forms(env): ...    # number -> constant, managed; distribution -> managed; lambda -> opaque flagged with owner
def test_library_never_touches_global_random(monkeypatch):  # monkeypatch random.random etc. to raise; run a builder system 100 time units
def test_runner_parallel_equals_sequential(): ...
```

- [ ] **Steps 2–4:** fail, implement, migrate the listed tests to `Environment(seed=...)` and samplers, pass the full suite.
- [ ] **Step 5:** Commit `feat(core)!: replace global random with per-environment RNG streams`.

### Task 5: Activation and the command queue

**Files:**
- Modify: `src/simulatte/environment.py`
- Test: `tests/core/test_activation.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces: `env.activate() -> None` (idempotent); `env.activated: bool`; `env.on_activate(fn: Callable[[], None]) -> None` (runs immediately if already active); `env.initial_state: dict[str, dict] | None`; `deferrable` decorator in `simulatte.environment` (method's `self` must expose `env`); `env.run()` activates on first call; `env.prelude_events: list[Event]` is not stored (recorders capture the prelude themselves); hook list `env._activation_listeners` used by Task 7 and Task 9 (`on_initial_state(callback: Callable[[dict], None])`).
- Order inside `activate()`: initializers (assert `env.now` unchanged and no new entries in `env._queue` with delay > 0) → `initial_state = entities.snapshot()` → notify `on_initial_state` listeners → set `_projection_active` if any projection listener exists → drain deferred commands in order (exceptions propagate; remaining dropped).

- [ ] **Step 1: Write failing tests**

```python
def test_run_activates_once(): ...                       # run(until=1); run(until=2) -> initializer called once
def test_initializer_must_not_advance_time(): ...        # initializer scheduling timeout(1) -> RuntimeError
def test_deferred_commands_preserve_order(): ...         # submit then cancel on a toy component -> log ["submit","cancel"] at activation
def test_deferred_command_failure_stops_activation(): ...# 2nd raises -> env.run raises; 3rd never ran
def test_after_activation_commands_run_immediately(): ...
def test_initial_state_captured_after_initializers(): ...
```

- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): add preparation and activation with deferred commands`.

### Task 6: Instrument Server and ProductionJob (with the queue_length fix)

**Files:**
- Modify: `src/simulatte/server.py`, `src/simulatte/job.py`
- Test: `tests/core/test_server_events.py`; update `tests/core/test_server.py`, `test_job.py`, `test_shopfloor.py:366`, `test_component_logging.py` (server and job parts)

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `Server(env, capacity, shopfloor=None, *, name: str | None = None, label: str | None = None, ...)` is an `Entity` of kind `server`; `_idx` removed (use `id`). State schema: `capacity`, `users`, `queue`, `worked_time`.
  - `ProductionJob` is an `Entity` of kind `job` with id `job-n`; state schema per spec §5.2 (`sku`, `routing`, `op_index`, `location`, `due_date`, `created_at`, `finished_at`). `repr` uses the new id.
  - Event classes in `src/simulatte/server.py`/`job.py` (or a `production_events.py` module the implementer chooses): `JobCreated`, `JobQueued`, `JobGranted`, `OperationStarted`, `OperationCompleted`, `JobReleased`, `ServerQueueReordered` with payloads from spec §6.3.
  - `JobQueued.queue_length = len(server.queue)` after request construction (D49).
  - The `env.debug` calls in `server.py` are removed.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("capacity", [1, 2])
def test_job_queued_queue_length_counts_waiting_jobs_only(capacity):
    # submit capacity+2 jobs at t=0; queue_length sequence == [0]*capacity + [1, 2]
def test_server_queue_reordered_emits_moves_only_when_order_changes(): ...
def test_server_ids_and_names(): ...           # Server(env, 1, name="lathe") -> id "lathe"; unnamed -> "server-0"
def test_job_ids_sequential_per_env(): ...     # "job-0", "job-1"; a second env restarts at "job-0"
def test_server_events_replay_to_snapshot(): ...# apply_deltas(initial_state, all deltas) == entities.snapshot() at end
```

- [ ] **Steps 2–4:** fail, implement, pass full suite.
- [ ] **Step 5:** Commit `feat(core)!: emit server and job events; fix queue_length off-by-one`.

### Task 7: Instrument ShopFloor, PreShopPool, Router; builder prefixes

**Files:**
- Modify: `src/simulatte/shopfloor.py`, `src/simulatte/psp.py`, `src/simulatte/router.py`, `src/simulatte/builders.py`, `src/simulatte/scenario.py`
- Test: `tests/core/test_flow_events.py`; update `tests/core/test_builders.py`, `test_psp.py`, `test_router.py`, `test_component_logging.py` (remaining parts)

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces:
  - `ShopFloor`, `PreShopPool`, `Router` become entities (`shopfloor`, `psp`, `router`) with `name=`/`label=` keyword arguments.
  - `ShopFloor.jobs` becomes `dict[ProductionJob, None]` (spec §5.3); public iteration semantics unchanged.
  - Events `PspEntered`, `PspExited` (`reason`: `released`|`postponed`|`removed`), `ShopFloorEntered`, `ShopFloorWipUpdated`, `JobFinished` emitted at the phases of spec §6.3; `env.debug` calls in these files removed.
  - `Router` binds its samplers with `as_sampler` to streams `<router>/interarrival`, `<router>/sku`, `<router>/routing/<sku>`, `<router>/service/<sku>/<server>`, `<router>/due/<sku>`.
  - Every `build_*_system(..., prefix: str = "")` names entities `f"{prefix}{default}"`: servers `wc-<i>`, `psp`, `shopfloor`, `router`; `scenario: Scenario | None = None`.

- [ ] **Step 1: Write failing tests**

```python
def test_one_operation_phase_sequence():
    # single job, one server: types == ["job.created","shopfloor.entered","job.queued","job.granted",
    #   "operation.started","operation.completed","shopfloor.wip_updated","job.released","job.finished"]
def test_psp_release_sequence_same_instant(): ...  # psp.entered, psp.exited(released), shopfloor.entered at same t, increasing seq
def test_two_builders_share_env_with_prefixes(): ...  # prefixes "a-","b-" -> no id collision
def test_router_streams_named_by_ids(): ...        # env.rng cache keys include "router/interarrival"
```

- [ ] **Steps 2–4:** fail, implement, pass full suite.
- [ ] **Step 5:** Commit `feat(core)!: emit shop-floor flow events and add builder prefixes`.

### Task 8: Semantic projection, digest and provenance

**Files:**
- Create: `src/simulatte/digest.py`, `src/simulatte/provenance.py`
- Modify: `src/simulatte/environment.py` (`enable_digest`, `fingerprint`, `manifest`, stopping-policy recording in `run`, `close`)
- Test: `tests/core/test_digest.py`, `tests/core/test_provenance.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `project_event(event: DomainEvent) -> bytes` = `canonical_pack([ordinal, type_name, type_version, t, payload_map, deltas])`; `project_state(state: dict) -> bytes`.
  - `class SemanticDigest`: `attach(env) -> SemanticDigest` (raises `RuntimeError` if `env.activated`), `hexdigest() -> str`.
  - `env.enable_digest() -> SemanticDigest`; `env.fingerprint() -> Fingerprint` (`digest: str | None`, `kpis: dict[str, float]` filled by collectors from Task 19).
  - `UNAVAILABLE` sentinel; `@dataclass(frozen=True) Provenance(model=UNAVAILABLE, source=UNAVAILABLE, inputs=UNAVAILABLE, dependencies=UNAVAILABLE)`; `Environment(provenance: Provenance | None = None)`.
  - `RunManifest` with fields of spec §9.3 and `complete: bool`, `canonical() -> Wire`; `VolatileMetadata(wall_clock_start, host, durations)`; `env.manifest() -> RunManifest`; `env.run(until=x)` records `{"type": "horizon", "horizon": x}` (last call wins), `run()` records `{"type": "exhaustion"}`.

- [ ] **Step 1: Write failing tests**

```python
def test_digest_independent_of_log_and_extra_subscribers(): ...  # same seed; extra Ping subscriber and env.info calls -> same hexdigest
def test_digest_changes_when_trajectory_changes(): ...          # different seed -> different digest
def test_digest_attach_after_activation_raises(): ...
def test_digest_stable_across_hash_seeds(tmp_path):              # subprocess with PYTHONHASHSEED in (0, 1, 123) -> identical hexdigest
def test_manifest_complete_only_with_full_provenance_and_no_opaque(): ...
def test_manifest_records_last_horizon(): ...                    # run(until=10); run(until=20) -> horizon 20
```

- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): add semantic digest, provenance and run manifest`.

### Task 9: Trace writer

**Files:**
- Create: `src/simulatte/trace/__init__.py`, `src/simulatte/trace/format.py` (record framing, constants), `src/simulatte/trace/writer.py`
- Modify: `src/simulatte/environment.py` (`step` sets `self._interrupted` on `KeyboardInterrupt`; `close()` closes recorders; failure path)
- Test: `tests/core/test_trace_writer.py`

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces:
  - `format.py`: `MAGIC = b"SIMTRACE"`, `TRAILER_MAGIC = b"SIMTEND\0"`, `FORMAT = (1, 0)`, `RecordType` enum (`HEADER=1, PRELUDE=2, CATALOG_EXT=3, CHUNK=4, INDEX=5, KPI=6, FOOTER=7`), `write_record(f, rtype, payload: bytes) -> int` (returns offset), `iter_records(f) -> Iterator[Record]` (stops at short or bad-CRC record, sets `truncated`).
  - `ChunkLimits(max_events: int = 10_000, max_bytes: int = 1 << 20, max_latency_s: float = 1.0, max_sim_window: float | None = None)`.
  - `TraceRecorder(env, path, *, level: Literal["full", "kpi"] = "full", chunk_limits: ChunkLimits | None = None)`; raises `RuntimeError` if `env.activated`; enables the digest; captures prelude events; snapshot at each chunk start from its own replay state (`apply_deltas`); `close(outcome: Literal["completed","cancelled","failed"] | None = None)`; outcome inferred: exception in `env.run` → `failed`, `env._interrupted` → `cancelled`, else `completed`.
  - Header payload keys: `features`, `catalog`, `kinds`, `manifest`, `volatile`, `level`, `chunk_limits`. An event type or kind first seen after the header is written as a `CATALOG_EXT` record (with the next epoch number) before the chunk that uses it; chunks carry their `epoch`, and the footer maps epochs to record offsets. Chunk payload keys: `first`, `last`, `t_start`, `t_end`, `epoch`, `snapshot`, `events` (each event `[seq, ordinal, type, t, payload, deltas]`). Footer keys: `outcome`, `final_cursor`, `fingerprint`, `index`, `epochs`.

- [ ] **Step 1: Write failing tests**

```python
def test_chunk_limits_close_chunks(tmp_path): ...       # max_events=50 on reference shop -> >1 chunk, each <= 50 events
def test_latency_limit_uses_injected_clock(tmp_path): ...# monkeypatched time.monotonic -> chunk closed after 1.0 s age
def test_footer_and_trailer(tmp_path): ...               # last 16 bytes = u64 offset + TRAILER_MAGIC; footer outcome "completed"
def test_failed_run_footer(tmp_path): ...                # process raises -> env.run raises; footer outcome "failed"
def test_interrupted_run_footer(tmp_path): ...           # simulate KeyboardInterrupt in a process -> "cancelled"
def test_recorder_after_activation_raises(tmp_path): ...
def test_close_without_run(tmp_path): ...                # zero chunks, footer completed
def test_continues_across_multiple_run_calls(tmp_path): ...
def test_catalog_extension_for_type_registered_after_header(tmp_path): ...  # define a new event type mid-run -> CATALOG_EXT precedes its first chunk
```

- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(trace): add trace container and recorder`.

### Task 10: Trace reader and G1 acceptance

**Files:**
- Create: `src/simulatte/trace/reader.py`
- Test: `tests/core/test_trace_reader.py`, `tests/core/test_g1_acceptance.py`
- Create: `specs/research/sp1-g1-report.md`

**Interfaces:**
- Consumes: Task 9.
- Produces: `Cursor = tuple[float, int]`; `Trace.open(path) -> Trace` with `manifest`, `catalog`, `outcome: str | None` (None when no footer), `truncated: bool`, `cursor_range: tuple[Cursor, Cursor]`, `state_at(cursor: Cursor) -> dict[str, dict]`, `events(start: Cursor | None = None, end: Cursor | None = None) -> Iterator[dict]`, `kpis() -> dict`, `fingerprint: dict | None`, `verify() -> bool`.

- [ ] **Step 1: Write failing tests**

```python
def test_state_at_equals_replay_at_every_chunk_boundary(ref_trace): ...
def test_state_at_sampled_cursors_including_same_time_events(ref_trace): ...
def test_state_at_end_equals_live_snapshot(ref_trace, ref_env): ...
def test_truncated_file_reads_complete_chunks(tmp_path): ...   # cut file mid-record -> truncated True, outcome None
def test_crc_mismatch_detected(tmp_path): ...
def test_unknown_required_feature_refused(tmp_path): ...
def test_verify_recomputes_digest(ref_trace): ...
def test_seek_into_chunk_with_later_catalog_epoch(tmp_path): ...  # reader resolves the extension via footer epochs without scanning
def test_g1_digest_invariant_across_observers_and_hash_seeds(): ...  # acceptance row G1
```

- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Write `specs/research/sp1-g1-report.md`: acceptance results, trace size and event counts for the reference shop, any design deviations. **Stop and report to Davide; G2 starts only after the G1 report is accepted.**
- [ ] **Step 6:** Commit `feat(trace): add trace reader; G1 acceptance`.

## Gate G2: TypeScript conformance

### Task 11: `@simulatte/trace` package and fixtures

**Files:**
- Create: `studio/package.json`, `studio/pnpm-workspace.yaml`, `studio/tsconfig.base.json`, `studio/packages/trace/{package.json,tsconfig.json,src/index.ts,src/container.ts,src/deltas.ts,src/state.ts,test/conformance.test.ts}`
- Create: `tests/fixtures/traces/generate.py`, `tests/fixtures/traces/*.trace`, `tests/fixtures/traces/*.expected.json`; `tests/core/test_trace_fixtures.py`
- Modify: `.github/workflows/ci.yml` (job `trace-ts`: Node LTS, pnpm, `pnpm -C studio test`), `.gitignore` (`studio/node_modules`)

**Interfaces:**
- Consumes: trace format (Task 9).
- Produces (TS): `openTrace(bytes: Uint8Array): Trace`; `Trace.header`, `Trace.cursorRange`, `Trace.stateAt(cursor: [number, number]): Record<string, Record<string, unknown>>`; `applyDeltas(state, deltas): void`. Decompression via `DecompressionStream("deflate")` (Node ≥18 provides it).
- Fixtures: reference shop (small), a same-time-heavy model, a truncated copy; expected states at chunk boundaries and three sampled cursors as canonical JSON (floats as JSON numbers, non-finite as strings `"+inf"`, `"-inf"`, `"nan"`).

- [ ] **Step 1:** Write `test_trace_fixtures.py::test_fixtures_up_to_date` (regenerates into tmp and compares bytes) and the TS conformance test (each fixture: `stateAt` equals expected JSON).
- [ ] **Step 2:** Run both → FAIL.
- [ ] **Step 3:** Implement generator and TS package.
- [ ] **Step 4:** `uv run pytest tests/core/test_trace_fixtures.py` and `pnpm -C studio test` → PASS.
- [ ] **Step 5:** Commit `feat(studio): add TypeScript trace conformance reader`. **Report G2 result to Davide.**

## Gate G3: budgets

### Task 12: Benchmarks and CI overhead gate

**Files:**
- Create: `benchmarks/workloads.py`, `benchmarks/run.py`, `benchmarks/README.md`
- Modify: `.github/workflows/ci.yml` (job `bench`: two venvs, `simulatte==0.12.0` and the branch; CPython 3.14 and PyPy 3.11)
- Create: `specs/research/sp1-g3-report.md`

**Interfaces:**
- Produces: `python benchmarks/run.py --mode {none,default_logging,kpi,full} --workload {shop_small,shop_full,shop_congested,fleet} --repeat N --json out.json`; JSON keys `median_s`, `p95_s`, `peak_mb`, `trace_bytes`, `chunks`, `seek_p50_ms`, `seek_p95_ms`. Workloads detect the installed API (`hasattr(Environment, "rng")`) to seed either way. `benchmarks/compare.py base.json head.json --budget 0.03 --noise <band>` exits non-zero above budget+band in mode `none`.

- [ ] **Step 1:** Run the suite locally on CPython and PyPy for the slice; calibrate the noise band from 10 baseline-vs-baseline comparisons (report the max observed ratio).
- [ ] **Step 2:** Wire the CI job; it publishes a table to `$GITHUB_STEP_SUMMARY`.
- [ ] **Step 3:** Write `specs/research/sp1-g3-report.md` with measured values against C1.7/C1.9 hypotheses and proposed budgets for the other modes. **Stop and report to Davide; G4 starts after acceptance, and the global spec's C1.9 table is updated with the accepted budgets.**
- [ ] **Step 4:** Commit `ci: add benchmark suite and overhead gate`.

## Gate G4: migration

### Task 13: Release policies

**Files:**
- Modify: `src/simulatte/policies/{slar.py,lumscor.py,draco.py,conwip.py,continuous_release.py,slar_limit.py}`, `src/simulatte/psp.py` (`remove(job, *, reason=...)`)
- Test: `tests/core/test_policy_events.py`

**Interfaces:**
- Produces: `PolicyDecision` event (`"policy.decision"`: `policy`, `job`, `action` ∈ {`release`, `force_pin`, `postpone`}); policies pass `reason="postponed"` for postponed releases; policy ids: policies are not entities, `policy` is the class name.

- [ ] **Step 1:** Failing tests: each policy's decision events on a small shop (`test_slar_postpone_events`, which also asserts `psp.exited(reason="postponed")` and job location `transit` during the 0.001 wait, `test_lumscor_periodic_release_events`, `test_draco_force_pin_event`, `test_conwip_release_event`, `test_continuous_release_event`).
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(policies): emit release decision events`.

### Task 14: Intralogistics entities, ordering and activation

**Files:**
- Modify: `src/simulatte/intralogistics/{agv.py,order.py,fleet.py,warehouse.py,charging.py,parking.py,graph.py,traffic.py}`
- Test: `tests/intralogistics/test_entities.py`, `tests/intralogistics/test_activation.py`; update tests using `frozenset(graph.nodes)`, `agv_id`, `order.id`

**Interfaces:**
- Produces:
  - Kinds `agv`, `order`, `fleet`, `warehouse`, `charging_station`, `parking_area`, `node` (spec §5.2). `AGV(agv_id=...)` keeps the parameter as the name. `TransferOrder.id` defaults to `""` and is assigned `order-n` at attachment.
  - `FleetCoordinator` attaches its graph's nodes sorted by id; `LayoutGraph.nodes -> tuple[Node, ...]` in insertion order; `check_path` conflict nodes in path order.
  - `OrderStatus.PENDING_ACTIVATION`; `FleetCoordinator.submit`, `cancel` and the attachment part of `create_order` are `@deferrable`.
  - `ResourceBasedTrafficManager.place_now(agv, node) -> None` (raises `RuntimeError` on conflict); `FleetCoordinator` registers an initializer instead of starting `_initial_placement`; `FreeTrafficManager.place_now` is a no-op.

- [ ] **Step 1:** Failing tests: `test_submit_then_cancel_before_run_cancels`, `test_pending_activation_status_before_run`, `test_placement_conflict_raises_at_activation`, `test_node_ids_attached_sorted`, `test_graph_nodes_order_stable_across_hash_seeds` (subprocess), `test_order_ids_sequential`.
- [ ] **Steps 2–4:** fail, implement, pass `tests/intralogistics`.
- [ ] **Step 5:** Commit `feat(intralogistics)!: entities, deterministic ordering and activation`.

### Task 15: Intralogistics events and motion descriptions

**Files:**
- Modify: `src/simulatte/intralogistics/{fleet.py,agv.py,warehouse.py,charging.py,parking.py,traffic.py,speed.py}`
- Test: `tests/intralogistics/test_events.py`, `tests/intralogistics/test_motion.py`; update `tests/intralogistics/test_logging.py`

**Interfaces:**
- Produces: events of spec §6.3 intralogistics table (`OrderCreated`, `OrderStatusChanged` at every status assignment, `OrderAssigned`, `AgvStateChanged`, `AgvMoveStarted`, `AgvMoveEnded`, `AgvMoveInterrupted`, `AgvLoadChanged`, `AgvStranded`, `TrafficWaitStarted`, `TrafficWaitEnded`, `WarehouseInventoryChanged`, `WarehouseSlotChanged`, `ChargingStarted`, `ChargingEnded`, `ParkingEntered`, `ParkingLeft`); `SpeedProfile.motion(distance, load_weight=0.0, battery_level=1.0, speed_limit=None) -> dict` optional, implemented by `TrapezoidalProfile` (`curve="trapezoidal"`, `v_max`, `accel`, `decel`, `distance`) and any constant-speed profile (`curve="constant"`); fallback `{"curve": "linear", "approximate": True}`; infinite travel time → `stalled: True`. All `env.debug` calls in intralogistics removed; the nine warnings/errors kept as `env.warning`/`env.error`.

- [ ] **Step 1:** Failing tests: `test_order_status_events_cover_every_transition` (including `FAILED` after retries), `test_move_started_after_enter_permission` (traffic wait precedes move), `test_move_interrupted_keeps_previous_node`, `test_trapezoidal_motion_matches_travel_time` (integrated duration equals `travel_time` within 1e-9), `test_events_replay_to_snapshot` (fleet example).
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(intralogistics): emit fleet, traffic, warehouse and charging events`.

### Task 16: Intralogistics samplers

**Files:**
- Modify: `src/simulatte/intralogistics/{agv.py,warehouse.py,charging.py,fleet.py,builders.py}`, `examples/intralogistics_*.py` and their docs pages, intralogistics tests using `*_time_fn` lambdas
- Test: `tests/intralogistics/test_samplers.py`

**Interfaces:**
- Produces: `AGVType(load_time=..., unload_time=...)`, `Warehouse(pick_time=..., put_time=...)`, `ChargingStation(recharge_time=...)` accepting `Distribution | float | Callable` (the `*_fn` names are replaced); bound with `as_sampler` to streams `<agv>/load`, `<agv>/unload`, `<warehouse>/pick`, `<warehouse>/put`, `<station>/recharge`. `Warehouse.pick_time` callables keep their `(sku, qty)` signature when callable (opaque); distributions and numbers ignore arguments.

- [ ] **Step 1:** Failing tests: `test_numbers_are_managed`, `test_lambda_marks_opaque_with_owner`, `test_distribution_streams_per_entity`.
- [ ] **Steps 2–4:** fail, implement, migrate tests/examples/docs, pass including `tests/test_docs_run_blocks.py`.
- [ ] **Step 5:** Commit `feat(intralogistics)!: managed samplers for time parameters`.

### Task 17: Observer purity

**Files:**
- Modify: `src/simulatte/intralogistics/agv.py`
- Test: `tests/intralogistics/test_agv_purity.py`

**Interfaces:**
- Produces: `AGV.utilization()`, `state_percentage()`, `time_allocation()` compute the open interval without writing `state_durations` or `_state_entered_at`.

- [ ] **Step 1:** Failing test `test_reading_utilization_does_not_change_state` (state dict and `_state_entered_at` identical before/after; results identical whether read 0 or 1000 times during a run).
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `fix(intralogistics): make AGV utilization getters pure`.

### Task 18: Logging on the bus; remove loguru

**Files:**
- Create: `src/simulatte/logsinks.py`
- Delete: `src/simulatte/logger.py`, `tests/core/test_logger_finalizer.py`
- Modify: `src/simulatte/environment.py`, `pyproject.toml` (remove `loguru`), `uv.lock`
- Test: rewrite `tests/core/test_logger.py` → `tests/core/test_logsinks.py`, `tests/core/test_logger_sqlite.py` → `tests/core/test_logsinks_sqlite.py`, `tests/core/test_runner.py` (log_dir/format)

**Interfaces:**
- Produces: `TextSink(target: TextIO | str | Path, *, level: str = "INFO", components: Collection[str] | None = None, exclude: Collection[str] = (), render_domain: bool = True)`, `JsonSink(...)` same signature, `SQLiteSink(path, *, level="DEBUG")` with `query(level=None, component=None, since=None, until=None, type=None) -> list[dict]` and `execute_sql(sql, params=()) -> list[tuple]`, `HistorySink(maxlen: int)` with `query(level=None, component=None, since=None) -> list[LogEvent]`; all expose `attach(env)`, `close()`, `enable_component(name)`, `disable_component(name)`. `Environment(log_level=..., log_file=..., log_format=..., log_history_size=..., log_db_path=...)` attaches them; `env.log_history -> HistorySink`; `env.sinks -> tuple`. Domain events rendered as `"[t] <type> <payload k=v ...>"` at DEBUG.

- [ ] **Step 1:** Failing tests: `test_info_reaches_text_sink`, `test_debug_renders_domain_events_only_when_enabled`, `test_no_domain_subscription_at_info` (`env.wants(JobQueued)` is False with default sinks), `test_component_filters`, `test_sqlite_query_and_execute_sql`, `test_history_query`, `test_two_envs_have_independent_levels`, `test_sinks_closed_on_env_close`, `test_runner_log_dir_json`.
- [ ] **Steps 2–4:** fail, implement, delete loguru code, pass full suite on CPython and PyPy.
- [ ] **Step 5:** Commit `feat(core)!: rebuild logging on the event bus and drop loguru`.

### Task 19: KPI framework and trace KPI records

**Files:**
- Create: `src/simulatte/kpi.py`
- Modify: `src/simulatte/environment.py` (`configure_kpis(warmup: float)`, before activation only), `src/simulatte/trace/{writer.py,reader.py}` (`KPI` records), `src/simulatte/digest.py` (fingerprint KPIs)
- Test: `tests/core/test_kpi.py`

**Interfaces:**
- Produces: `KPI` dataclass (spec §12.1 fields); `class Collector` with `kpis: ClassVar[tuple[KPI, ...]]`, `subscribes: ClassVar[tuple[type[Event], ...]]`, `attach(env) -> Self`, `on_event(event) -> None`, `sample(kpi: str, value: float, scope: str | None = None) -> None`, `scalars() -> dict[str, float]`, `window -> tuple[float, float | None]`; helpers `TimeWeighted(start: float)` accumulator with `.update(t, value)`, `.mean(window_start, window_end)` clipping at the window; `env.fingerprint().kpis` collects `scalars()` of attached collectors.

- [ ] **Step 1:** Failing tests: `test_time_weighted_clipping` (hand-computed: value 2 on [0,5), 4 on [5,10), window [3,10) → mean 22/7), `test_completion_cohort_excludes_warmup_completions`, `test_kpi_samples_are_observer_events_and_not_in_digest`, `test_recorder_stores_kpi_series_and_scalars`.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(kpi): add KPI declarations, collectors and windows`.

### Task 20: Production collectors replace the old protocols

**Files:**
- Create: `src/simulatte/collectors.py`
- Modify: `src/simulatte/shopfloor.py` (remove `MetricsCollector`, `TimeSeriesCollector`, `EMAMetricsCollector`, `DefaultTimeSeriesCollector`, `CurrentWorkLoadCollector`, `metrics_collector=`, `time_series_collector=`, setters; add `default_metrics: bool = True`, `shopfloor.metrics`), `src/simulatte/server.py` (remove `collect_time_series`; keep `retain_job_history`), `src/simulatte/builders.py`, `src/simulatte/scenario.py` (flags attach new collectors)
- Test: `tests/core/test_collectors.py`, `tests/core/test_collectors_parity.py`; update `tests/core/test_shopfloor.py`, `test_builders.py`, `test_server.py`, docs examples

**Interfaces:**
- Produces: `EMACollector(alpha=0.01)` (attributes `ema_makespan`, `ema_tardy_jobs`, `ema_early_jobs`, `ema_in_window_jobs`, `ema_time_in_psp`, `ema_time_in_shopfloor`, `ema_total_queue_time`), `ShopFloorTimeSeries` (`wip_ts`, `job_count_ts`, `throughput_ts`, `lateness_ts`, `plot_wip/job_count/throughput/lateness`), `CurrentWorkloadCollector` (`wip_ts`), `ServerTimeSeries` (`qt`, `ut`, `plot_qt`, `plot_ut`, per server id), `ShopFloorKPIs` (flow time, tardiness, lateness, throughput, time-weighted WIP and utilization per server).

- [ ] **Step 1:** Write the parity test **first, against the old code**: record old `ema_*` and time-series outputs on three reference systems (LumsCor, SLAR, immediate release; seeds 1–3) into `tests/fixtures/collector_parity.json`, commit the fixture.
- [ ] **Step 2:** Failing tests for the new collectors equal to the fixture (EMA values within 1e-12; series identical).
- [ ] **Steps 3–4:** implement, delete old protocols, pass full suite and docs gate.
- [ ] **Step 5:** Commit `feat(core)!: replace collector protocols with bus collectors`.

### Task 21: Intralogistics collectors

**Files:**
- Modify: `src/simulatte/intralogistics/metrics.py` (replace protocols with `OrderEMACollector`, `FleetTimeSeries`, `FleetKPIs`), `fleet.py` (`default_metrics: bool = True`, `fleet.metrics`; remove `order_metrics_collector=`, `time_series_collector=`)
- Test: `tests/intralogistics/test_collectors.py`, parity fixture as in Task 20; update examples and docs

**Interfaces:**
- Produces: `OrderEMACollector` (`ema_fulfillment_time`, `ema_dispatch_delay`, `ema_travel_time_empty`, `ema_travel_time_loaded`, `ema_late_orders`), `FleetTimeSeries` (`fleet_utilization_ts`, `pending_orders_ts`, `throughput_ts`, `inventory_ts` keyed by warehouse id, `plot_*`), `FleetKPIs`; utilization computed from `agv.state_changed` events only.

- [ ] **Steps 1–5:** parity fixture first, failing tests, implement, pass, commit `feat(intralogistics)!: bus-based fleet collectors`.

### Task 22: Cross-cutting invariance suite

**Files:**
- Test: `tests/core/test_invariance.py`, `tests/intralogistics/test_invariance.py`

- [ ] **Step 1:** Write tests for the reference models (three production builders, the advanced intralogistics example): (a) final model state equal with no subscribers and fully instrumented; (b) digests and shared KPIs equal across default logging, KPI-only, full trace, full trace plus `ShopFloorTimeSeries`; (c) fresh-process determinism under `PYTHONHASHSEED` 0, 1, 123; (d) `Trace.verify()` true.
- [ ] **Step 2:** Run; fix any violation in the owning module (each fix its own commit with a regression test).
- [ ] **Step 3:** Commit `test: add observer invariance and determinism suite`.

### Task 23: Public surface, docs, examples, changelog

**Files:**
- Modify: `src/simulatte/__init__.py` (exports of spec §3), `docs/tutorials/logging.md`, `docs/introduction/architecture.md`, `docs/api/*`, `docs/running-on-pypy.md`, new `docs/guides/events-and-traces.md` (+ `zensical.toml` nav), `skills/`, `docs/ai-skill.md`, `CHANGELOG.md` (Unreleased 0.13 with migration section per spec §16), `CLAUDE.md`/`AGENTS.md` repository structure
- Test: `tests/core/test_public_api.py`

- [ ] **Step 1:** Failing test `test_top_level_exports` (names of spec §3 importable from `simulatte`).
- [ ] **Step 2:** Implement exports; update docs; `uv run zensical build` and `scripts/check_docs_links.py` pass.
- [ ] **Step 3:** Commit `docs: document events, traces and the 0.13 migration`.

### Task 24: Final verification

- [ ] **Step 1:** `uv run pytest` (coverage ≥ 99 %), PyPy lane command from CI, `uv run ruff check src tests`, `uv run ty check src`, `pnpm -C studio test`, `uv run zensical build`, benchmark suite against budgets.
- [ ] **Step 2:** Record results in `specs/research/sp1-final-report.md`; Cortex checkpoint; open the PR for Davide.
