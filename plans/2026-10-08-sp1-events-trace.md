# SP1 Events and Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 2**, after SP1 review 1 (`specs/reviews/2026-10-08-sp1-review-1.md`); markers such as (S6) show what each finding changed.

**Goal:** Give Simulatte stable entity ids, typed events with state deltas on an event bus, seeded RNG streams, a semantic digest, a seekable trace file readable from Python and TypeScript, bus-based KPI collectors and logging, and a CI overhead gate.

**Architecture:** New neutral core modules (`_wire`, `entities`, `events`, `rng`, `digest`, `provenance`, `trace`, `kpi`, `collectors`, `logsinks`) that depend only on `simulatte.environment` and each other. Components attach as entities and emit domain events behind an O(1) `env.wants` guard, from data the transition already computed; observers (digest, recorder, collectors, sinks) subscribe to the bus. Four gates (G1 slice, G2 TypeScript reader, G3 budgets, G4 migration); a failed gate stops the plan for redesign.

**Tech Stack:** Python ≥3.11 (CPython 3.12–3.14, PyPy 3.11), SimPy, msgpack, hashlib/zlib/threading (stdlib), pytest; TypeScript with pnpm, Vite, Vitest, `@msgpack/msgpack` for G2.

**Spec:** `specs/2026-10-08-sp1-events-trace-design.md` revision 2 (parent: `specs/2026-10-08-studio-global-design.md`; inventory: `specs/research/2026-10-08-sp1-inventory.md`).

## Global Constraints

- `requires-python = ">=3.11"`; tests pass on CPython 3.12, 3.13, 3.14 and PyPy 3.11 for `tests/core`, `tests/intralogistics` and the trace tests.
- Coverage gate `--cov-fail-under=99` with branch coverage applies to **full-suite runs** (gate tasks and the final task). Targeted red/green commands use `uv run pytest <paths> --no-cov -q` (S25).
- `uv run ruff check src tests` and `uv run ty check src` clean at every commit.
- Runtime dependencies: add `msgpack`; remove `loguru` (Task 19). Nothing else.
- Seeds: `0 <= seed < 2**63`; recorded as decimal strings.
- Wire: integers within ±(2⁵³−1); float64 with explicit `+inf`, `-inf`, normalized `NaN`; map keys escaped (`__proto__` or leading `~` → prefixed `~`).
- RNG stream seed: `int.from_bytes(hashlib.blake2b(f"simulatte-rng-v1\0{seed}\0{name}".encode(), digest_size=16).digest(), "big")`.
- Digest: `hashlib.blake2b(digest_size=32)`; each item prefixed by its byte length as u64 big-endian; presentation fields excluded.
- Trace: magic `b"SIMTRACE"`, format 1.0, record `length u32 | type u8 | crc32 u32 | payload`, `CHUNK` payload zlib-compressed msgpack, trailer `u64 footer offset + b"SIMTEND\0"`.
- Chunk defaults: 10,000 events, 1 MiB uncompressed, 1.0 s latency, 256 KiB per event. Reader limits: record 64 MiB, decompressed chunk 256 MiB, depth 64, collection length 10⁷.
- No-subscriber overhead ≤ 3 % median against `simulatte==0.12.0` on identical pre-generated workloads, plus a noise band calibrated at G3.
- Entity names must not contain `/` or `\0` and must not match `^<registered kind>-\d+$`.
- Any task that breaks a gallery example or a docs `{ .run }` block updates both in the same commit (`tests/test_docs_run_blocks.py`).
- Conventional commits on `feature/sp1-events-trace`, signed; if 1Password locks, `--no-gpg-sign` and say so.

## Review Focus

- A subscriber that raises mid-delivery: the exception propagates out of `env.emit`, the nested queue is cleared, and the next `emit` works (Task 2).
- `env.run(until=10)` then `env.run(until=20)`: activation once, one continuous trace, reopened manifest records horizon 20 (Tasks 5, 8, 9).
- A recorder or digest attached after activation: `RuntimeError` naming the reason (Tasks 8, 9).
- Names containing `/` or `\0`: `ValueError` at attachment (Task 3).
- A run ending by exception or `KeyboardInterrupt`: footer `failed` or `cancelled`; the file reopens cleanly (Task 9).

---

## Gate G1: vertical slice

### Task 1: Wire values and canonical encoding

**Files:** Create `src/simulatte/_wire.py`; modify `pyproject.toml` (`msgpack>=1.0.8`), `uv.lock`. Test `tests/core/test_wire.py`.

**Interfaces — Produces:** `Wire` alias; `freeze(value) -> Wire` (lists→tuples, dicts→`FrozenMap`, validates; `TypeError`/`OverflowError`); `FrozenMap(Mapping[str, Wire])` hashable and read-only; `escape_key(k: str) -> str`, `unescape_key(k: str) -> str`; `canonical_pack(value: Wire) -> bytes` (escaped keys sorted by UTF-8 bytes, float64 always, NaN normalized); `pack(value) -> bytes` (non-canonical, same escaping); `unpack(data: bytes, *, max_depth: int = 64, max_len: int = 10**7) -> Wire`.

- [ ] **Step 1: Failing tests**

```python
def test_freeze_converts_containers(): v = freeze({"b": [1, 2]}); assert v["b"] == (1, 2); pytest.raises(TypeError, v.__setitem__, "c", 1)
def test_freeze_rejects_non_wire(): [pytest.raises(TypeError, freeze, bad) for bad in (object(), {1: "x"}, {1, 2}, b"x")]
def test_int_range(): freeze(2**53 - 1); pytest.raises(OverflowError, freeze, 2**53)
def test_canonical_sorting_and_float64(): assert canonical_pack({"b": 1, "a": 2}) == canonical_pack({"a": 2, "b": 1}); assert canonical_pack(1.0) == b"\xcb" + struct.pack(">d", 1.0)
def test_nan_normalized_and_inf_roundtrip(): assert canonical_pack(float("nan")) == canonical_pack(-float("nan")); assert unpack(pack(float("-inf"))) == float("-inf")
def test_hostile_keys_roundtrip(): v = {"__proto__": 1, "~x": 2, "x": 3}; assert unpack(pack(v)) == v
def test_unpack_limits(): pytest.raises(ValueError, unpack, pack(nested_depth(65)))
```

- [ ] **Step 2:** `uv run pytest tests/core/test_wire.py --no-cov -q` → FAIL.
- [ ] **Step 3:** Implement on `msgpack.Packer(use_single_float=False)`/`Unpacker` with `object_pairs_hook` for unescaping and depth tracking.
- [ ] **Step 4:** PASS; `uv lock --check` passes.
- [ ] **Step 5:** Commit `feat(core): add wire values and canonical encoding`.

### Task 2: Events, deltas, catalog and bus

**Files:** Create `src/simulatte/events.py`; modify `src/simulatte/environment.py`. Test `tests/core/test_events.py`.

**Interfaces — Consumes:** Task 1. **Produces:**
- `@dataclass(frozen=True, slots=True, kw_only=True) class Event: t: float = nan; seq: int = -1; deltas: Deltas = Deltas.EMPTY`; `DomainEvent(Event)` with `ordinal: int | None = None`; `ObserverEvent(Event)`.
- `event_type(name: str, *, version: int = 1, presentation: frozenset[str] = frozenset())` decorator → `cls.type_name`, `cls.type_version`, `cls.presentation_fields`; global `CATALOG` with `get(name)`, `names()`, `to_wire()`.
- `Deltas` (immutable) with `EMPTY`, `Deltas.build() -> DeltaBuilder` (`set`, `insert`, `remove`, `move`, `put`, `delete`, `create`, `retire`, `done()`); ops as tuples per spec §6.2.
- `apply_deltas(state: dict[str, dict], deltas: Deltas) -> None`.
- `EventBus.subscribe(handler, types: tuple[type[Event], ...] | Literal["*", "**"]) -> Subscription`; `Subscription.cancel()`; `wants(cls) -> bool`.
- `Environment.emit(event)`: rejects `event.seq != -1` (S27); rejects `DomainEvent` while delivering (S8); rejects `ObserverEvent` with deltas (S8); stamps `t`, `seq`, and `ordinal` when `self._projection_active`; delivers FIFO. `Environment.wants(cls)`, `Environment.bus`, `Environment(debug: bool = False)`.
- Observer types `LogEvent` (`"log"`: `level`, `message`, `component`, `extra`) and `KpiSample` (`"kpi.sample"`: `kpi`, `scope`, `value`).

- [ ] **Step 1: Failing tests**

```python
def test_emit_stamps_time_and_seq(env): ...                     # seq 0,1 at t=0; t==5 after timeout(5)
def test_wants_tracks_subscriptions(env): ...
def test_star_covers_types_registered_later(env): ...
def test_nested_observer_emits_fifo(env): ...                   # order ["Ping","Pong"], Pong.seq > Ping.seq
def test_subscriber_exception_propagates_and_bus_recovers(env): ...
def test_domain_event_from_subscriber_rejected(env): ...
def test_observer_event_with_deltas_rejected(env): ...
def test_reemitted_instance_rejected_and_first_unchanged(env): ...
def test_apply_deltas_all_ops(): ...
def test_duplicate_type_name_different_fields_raises(): ...
```

- [ ] **Steps 2–4:** fail, implement (`wants` is a `dict[type, int]`; `"*"` checked for `DomainEvent` subclasses), pass with `--no-cov`.
- [ ] **Step 5:** Commit `feat(core): add typed events, deltas and event bus`.

### Task 3: Entities and lifecycle

**Files:** Create `src/simulatte/entities.py`; modify `environment.py` (`env.entities`). Test `tests/core/test_entities.py`.

**Interfaces — Consumes:** Task 2. **Produces:** `Entity` (`__init_subclass__(kind=...)`, `kind`, `state_schema`, `id`, `label`, `snapshot()`); `StateSchema(Mapping[str, FieldSpec])`; `FieldSpec(wire_type: str, nullable=False, collection: None | Literal["list", "map"] = None, presentation=False)`; `EntityRegistry.attach(obj, *, name=None, label=None) -> str`, `retire(obj)`, `get(id)`, `live()` (attachment order), `snapshot(*, include_presentation=True) -> dict[str, dict]` (sorted by id); events `EntityCreated` (`"entity.created"`: `entity`, `kind`, `label` presentation) and `EntityRetired`.

- [ ] **Step 1: Failing tests:** `test_generated_ids_per_kind`, `test_name_becomes_id_and_duplicates_raise`, `test_reserved_pattern_rejected`, `test_slash_and_nul_rejected`, `test_created_is_only_create_owner` (one `create` delta per attach), `test_retire_drops_strong_reference` (weakref dies after `del`), `test_snapshot_excludes_presentation_when_asked`.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): add entity registry, ids and lifecycle`.

### Task 4: RNG streams, binding protocols and Router migration

**Files:** Create `src/simulatte/rng.py`; modify `environment.py`, `distributions.py`, `router.py` (becomes an entity of kind `router`, binds its streams), `scenario.py`, `runner.py`, `builders.py` (pass descriptions, not callables), gallery examples using `random` and their docs blocks; migrate `tests/core/test_distributions.py`, `test_scenario.py`, `test_builders.py`, `test_runner.py`, `test_router.py`. Test `tests/core/test_rng.py`.

**Interfaces — Consumes:** Task 3. **Produces:**
- `derive_seed(seed: int, name: str) -> int`; `RNG_DERIVATION = "simulatte-rng-v1"`.
- `Environment(seed: int | None = None)` (range check; `None` → `int.from_bytes(os.urandom(8), "big") >> 1`); `env.seed`; `env.rng(name) -> random.Random` (cached; debug mode wraps it to count draws).
- Distributions: `sampler(rng) -> Callable[[], float]`; `__call__` removed. Routing descriptions `PureJobShopRouting(servers)`, `GeneralFlowShopRouting(servers)`, `FlowShopRouting(servers)` with `sampler(rng) -> Callable[[], Sequence[Server]]`; the old factory names return these descriptions.
- `env.bind(value, *, kind: Literal["scalar", "routing", "contextual"], stream: str, owner: str) -> Callable`; numbers and fixed server sequences are managed constants; other callables are opaque and recorded in `env.opaque_sampler_owners: list[str]`.
- `Router` stream names per spec §8.2; `Runner` uses `Environment(seed=seed)`.

- [ ] **Step 1: Failing tests:** `test_derive_seed_pinned` (value computed once and pinned), `test_streams_independent_and_reproducible`, `test_seed_range_and_default`, `test_shared_description_independent_samplers`, `test_bind_scalar_routing_contextual_forms`, `test_all_three_shop_types_route` (pure job shop, general flow shop, flow shop), `test_legacy_custom_routing_callable_is_opaque`, `test_library_never_touches_global_random` (monkeypatch `random.*` to raise; run a built system 100 time units), `test_runner_parallel_equals_sequential`.
- [ ] **Steps 2–4:** fail, implement, migrate listed tests and examples, pass `tests/core` with `--no-cov`, and `tests/test_docs_run_blocks.py`.
- [ ] **Step 5:** Commit `feat(core)!: per-environment RNG streams and binding protocols`.

### Task 5: Activation and the command queue

**Files:** Modify `environment.py`. Test `tests/core/test_activation.py`.

**Interfaces — Produces:** `env.activate()` (idempotent), `env.activated`, `env.on_activate(fn)`, `env.initial_state`, `env.on_initial_state(cb: Callable[[dict], None])` (for digest and recorders; also sets `_projection_active`), decorator `deferrable` (method's `self.env`), `env.run()` auto-activates. During initializers `env.process(...)` and `env.timeout(...)` raise `RuntimeError`.

- [ ] **Step 1: Failing tests:** `test_run_activates_once`, `test_initializer_cannot_create_process` (helper process → `RuntimeError`), `test_initializer_cannot_schedule_timeout`, `test_initializer_may_trigger_immediate_resource_grant`, `test_deferred_commands_preserve_order`, `test_deferred_command_failure_stops_activation`, `test_after_activation_commands_run_immediately`, `test_initial_state_captured_after_initializers`.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): add preparation and activation with deferred commands`.

### Task 6: Server and ProductionJob as entities; resource events; queue_length fix

**Files:** Modify `server.py`, `job.py`; update `tests/core/test_server.py`, `test_job.py`, server and job parts of `test_component_logging.py`. Test `tests/core/test_server_events.py`.

**Interfaces — Consumes:** Tasks 2–5. **Produces:**
- `Server(..., name=None, label=None)` kind `server` (schema spec §5.2); `_idx` **kept** as an internal alias until Task 7 (S22).
- `ProductionJob` kind `job`, id `job-n`.
- Events `JobQueued`, `JobGranted`, `JobReleased`, `ServerQueueReordered` emitted from `Server._trigger_put` (newcomers on entry), `Server.sort_queue` (minimal moves: elements outside the longest increasing subsequence of old positions), `Server._do_put` (granted), `Server._do_get` (released) per spec §6.3. `priority` comes from `request.priority`; `queue_length = len(self.queue)`.
- `env.debug` calls in `server.py` removed.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("capacity", [1, 2])
def test_queue_length_counts_waiting_only(capacity): ...   # capacity+2 requests at t=0 -> [0]*capacity + [1, 2]
def test_immediate_grant_inside_constructor_ordering(): ... # types for first request: ["job.queued", "job.granted"]
def test_direct_server_use_without_shopfloor(): ...         # request/release in a plain process emits queued/granted/released
def test_replay_equals_live_at_every_event(): ...           # subscriber snapshots live state after each event; apply_deltas from initial equals it
def test_reorder_emits_minimal_moves(): ...                 # priority change moving one job -> exactly one move
def test_counting_priority_policy_unaffected_by_recording(): ... # call count and schedule identical with and without a full subscriber (S9)
def test_job_ids_sequential_per_env(): ...
```

- [ ] **Steps 2–4:** fail, implement, pass `tests/core --no-cov`.
- [ ] **Step 5:** Commit `feat(core)!: server resource events and job entities; fix queue_length off-by-one`.

### Task 7: ShopFloor, PreShopPool flow events; retirement; builder prefixes

**Files:** Modify `shopfloor.py`, `psp.py`, `builders.py`, `scenario.py`, `server.py` (remove `_idx`); update `tests/core/test_shopfloor.py:366`, `test_builders.py`, `test_psp.py`, rest of `test_component_logging.py`. Test `tests/core/test_flow_events.py`.

**Interfaces — Produces:** kinds `shopfloor`, `psp` (with owner field `shopfloor`); `ShopFloor.jobs` as ordered dict; events `PspEntered`, `PspExited`, `ShopFloorEntered`, `OperationStarted`, `OperationCompleted`, `ShopFloorWipUpdated`, `JobFinished` (spec §6.4); job retirement at the end of the completion block; `build_*_system(..., prefix: str = "", scenario: Scenario | None = None)`; `env.debug` calls removed from these files.

- [ ] **Step 1: Failing tests:** `test_one_operation_phase_sequence` (types in order: `entity.created`, `shopfloor.entered`, `job.queued`, `job.granted`, `operation.started`, `operation.completed`, `shopfloor.wip_updated`, `job.released`, `job.finished`, `entity.retired`), `test_psp_release_same_instant_sequence`, `test_job_retired_after_completion_callbacks` (a callback sees the job still live), `test_live_registry_shrinks_python_history_kept`, `test_two_builders_share_env_with_prefixes`, `test_replay_equals_live_at_every_event_shop` (reference shop).
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core)!: shop-floor flow events, job retirement and builder prefixes`.

### Task 8: Semantic projection, digest, provenance and manifest

**Files:** Create `src/simulatte/digest.py`, `src/simulatte/provenance.py`; modify `environment.py`. Tests `tests/core/test_digest.py`, `tests/core/test_provenance.py`.

**Interfaces — Produces:** `project_event(event) -> bytes` and `project_state(state) -> bytes` (presentation removed, S7); `SemanticDigest.attach(env)` (raises after activation), `.hexdigest()`; `env.enable_digest()`; `env.fingerprint() -> Fingerprint(digest: str | None, kpis: dict[str, float])`; `UNAVAILABLE`; `Provenance(model, source, inputs, dependencies)`; `RunManifest(requested: Wire, final: Wire | None)` with `.complete`, `.merged()`; `env.manifest()`; `env.run(until=)` updates the final stopping policy (S14).

- [ ] **Step 1: Failing tests:** `test_digest_ignores_logs_and_extra_subscribers`, `test_digest_ignores_label_changes` (S7), `test_digest_changes_with_trajectory`, `test_digest_attach_after_activation_raises`, `test_digest_stable_across_hash_seeds` (subprocesses with `PYTHONHASHSEED` 0, 1, 123), `test_manifest_seed_is_decimal_string`, `test_manifest_complete_rules`, `test_manifest_final_records_last_horizon`.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(core): semantic digest, provenance and run manifest`.

### Task 9: Trace writer

**Files:** Create `src/simulatte/trace/__init__.py`, `trace/format.py`, `trace/writer.py`; modify `environment.py` (`_interrupted` flag in `step`; `close()` closes recorders; failure path in `run`). Test `tests/core/test_trace_writer.py`.

**Interfaces — Produces:** constants and `RecordType` (`HEADER=1, PRELUDE=2, INITIAL=3, CATALOG_EXT=4, CHUNK=5, INDEX=6, KPI=7, FOOTER=8`); `write_record(f, rtype, payload) -> int`; `ChunkLimits(max_events=10_000, max_bytes=1 << 20, max_latency_s=1.0, max_event_bytes=256 << 10, max_sim_window=None)`; `TraceRecorder(env, path, *, level="full", chunk_limits=None, clock=time.monotonic)` with a writer thread that publishes on any limit including latency; `close(outcome=None)`. Payload keys per spec §11.1.

- [ ] **Step 1: Failing tests:** `test_chunks_respect_event_limit`, `test_latency_publishes_without_further_events` (fake clock advanced past 1.0 s while the simulation thread is blocked in a callback; chunk + index appear on disk), `test_chunk_snapshot_from_replay_state_not_live`, `test_initial_record_written_at_activation`, `test_footer_trailer_and_final_manifest`, `test_failed_run_footer`, `test_interrupted_run_footer`, `test_recorder_after_activation_raises`, `test_close_without_run`, `test_continues_across_run_calls`, `test_catalog_extension_before_first_use`, `test_oversized_event_warns_or_raises_in_debug`.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(trace): trace container and threaded recorder`.

### Task 10: Trace reader and G1 acceptance

**Files:** Create `src/simulatte/trace/reader.py`. Tests `tests/core/test_trace_reader.py`, `tests/core/test_g1_acceptance.py`. Create `specs/research/sp1-g1-report.md`.

**Interfaces — Produces:** `Cursor = tuple[float, int]`; `ReaderLimits(max_record=64 << 20, max_chunk=256 << 20, max_depth=64, max_len=10**7)`; `TraceCorrupted(Exception)`; `Trace.open(path, *, limits=None)` with `manifest`, `catalog`, `outcome`, `truncated`, `cursor_range`, `state_at(cursor)`, `events(start=None, end=None)`, `kpis()`, `fingerprint`, `check()`, `verify() -> bool | Literal["not_verifiable"]`.

- [ ] **Step 1: Failing tests:** `test_state_at_equals_replay_at_chunk_boundaries`, `test_state_at_sampled_and_same_time_cursors`, `test_activation_cursor_returns_initial_state`, `test_trace_without_domain_events_is_seekable`, `test_incomplete_tail_is_truncated`, `test_interior_corruption_raises`, `test_chunk_without_index_invisible`, `test_unknown_required_feature_refused`, `test_limits_enforced`, `test_verify_full_and_kpi_not_verifiable`, `test_reopened_manifest_after_two_runs`, `test_g1_acceptance` (spec §2 G1 row).
- [ ] **Steps 2–4:** fail, implement, pass. Run the **full suite** (coverage gate) and ruff/ty.
- [ ] **Step 5:** Write `specs/research/sp1-g1-report.md` (acceptance results, trace size and event counts, deviations). Commit `feat(trace): trace reader; G1 acceptance`. **Stop and report to Davide; G2 starts after acceptance.**

## Gate G2: TypeScript conformance

### Task 11: `@simulatte/trace` package and fixtures

**Files:** Create `studio/package.json`, `studio/pnpm-workspace.yaml`, `studio/tsconfig.base.json`, `studio/packages/trace/{package.json,tsconfig.json,src/index.ts,src/container.ts,src/wire.ts,src/deltas.ts,src/trace.ts,test/conformance.test.ts}`; `tests/fixtures/traces/generate.py`, `tests/fixtures/traces/generated/*`, `tests/fixtures/traces/frozen/*` (committed once, never regenerated); `tests/core/test_trace_fixtures.py`. Modify `.github/workflows/ci.yml` (job `trace-ts`), `.gitignore`.

**Interfaces — Produces (TS):** `openTrace(source: Blob | ArrayBuffer): Promise<Trace>`; `Trace.header`, `Trace.cursorRange`, `Trace.prepare(cursor: [number, number]): Promise<void>`, `Trace.stateAt(cursor): Record<string, Record<string, unknown>>` (throws `NotPreparedError`); `applyDeltas(state, deltas)`; key unescaping and reader limits as in Python.

- [ ] **Step 1:** Failing tests: `test_generated_fixtures_canonically_up_to_date` (regenerate with fixed volatile metadata, explicit provenance, event-count limits; compare canonical content) and TS `conformance.test.ts` (for generated and frozen fixtures: `stateAt` after `prepare` equals expected canonical JSON; hostile keys, decimal seed string, non-finite floats, truncated file).
- [ ] **Steps 2–4:** fail, implement, `uv run pytest tests/core/test_trace_fixtures.py --no-cov` and `pnpm -C studio test` pass.
- [ ] **Step 5:** Commit `feat(studio): TypeScript trace conformance reader`. **Report G2 to Davide.**

## Gate G3: budgets

### Task 12: Benchmarks and CI overhead gate

**Files:** Create `benchmarks/workload_gen.py`, `benchmarks/feeder.py`, `benchmarks/run.py`, `benchmarks/compare.py`, `benchmarks/README.md`, `benchmarks/workloads/*.json` (pre-generated); modify `.github/workflows/ci.yml` (job `bench`); create `specs/research/sp1-g3-report.md`.

**Interfaces — Produces:** `workload_gen.py --servers 10 --jobs N --util U --seed S > workload.json` (jobs: arrival, sku, routing indices, processing times, due date); `feeder.py` builds a shop with the version-agnostic constructors and feeds jobs from the JSON, asserting job and operation counts; `run.py --mode {none,digest,full} --workload PATH --warmup K --repeat N --json out.json` (keys `median_s`, `iqr_s`, `peak_mb`, `trace_bytes`, `chunks`, `seek_p50_ms`, `seek_p95_ms`); `compare.py base.json head.json --budget 0.03 --noise BAND` (non-zero exit above budget+band for `none`).

- [ ] **Step 1:** Calibrate the noise band from 10 baseline-vs-baseline runs on CPython 3.14 and PyPy 3.11; record the maximum ratio.
- [ ] **Step 2:** Wire the CI job (two environments: `simulatte==0.12.0` and the branch; summary table to `$GITHUB_STEP_SUMMARY`).
- [ ] **Step 3:** Write `specs/research/sp1-g3-report.md` with measurements against C1.7/C1.9. Commit `ci: benchmark suite and overhead gate`. **Stop and report to Davide; G4 starts after acceptance.**

## Gate G4: migration

### Task 13: Release policies

**Files:** Modify `src/simulatte/policies/{slar.py,lumscor.py,draco.py,conwip.py,continuous_release.py,slar_limit.py}`, `psp.py` (`remove(job, *, reason="removed")`). Test `tests/core/test_policy_events.py`.

**Interfaces — Produces:** `PolicyDecision` (`"policy.decision"`: `policy` (class name), `job`, `action`); postponed releases pass `reason="postponed"`.

- [ ] **Steps 1–5:** failing tests `test_slar_postpone_events` (also `psp.exited(reason="postponed")` and job `location == "transit"` during the 0.001 wait), `test_lumscor_periodic_release_events`, `test_draco_force_pin_event`, `test_conwip_release_event`, `test_continuous_release_event`; implement; pass; commit `feat(policies): release decision events`.

### Task 14: Intralogistics entities, node bindings, ordering, activation, retirement

**Files:** Modify `src/simulatte/intralogistics/{agv.py,order.py,fleet.py,warehouse.py,charging.py,parking.py,graph.py,traffic.py}`; update tests using `frozenset(graph.nodes)`, `agv_id`, `order.id`. Tests `tests/intralogistics/test_entities.py`, `tests/intralogistics/test_activation.py`.

**Interfaces — Produces:** kinds `agv`, `order`, `fleet`, `warehouse`, `charging_station`, `parking_area`; `NodeBinding` kind `node` (`x`, `y`, `agvs`, `reserved_by`), created per environment by `env.entities.bind_node(node) -> NodeBinding` (idempotent for the same `Node`, raises for a different `Node` with the same id); `FleetCoordinator` binds its graph's nodes sorted by id; `LayoutGraph.nodes -> tuple`; ordered `check_path`; `OrderStatus.PENDING_ACTIVATION`; `@deferrable` `submit`, `cancel`, attachment step of `create_order`; `place_now`; order retirement at terminal statuses after hooks and bookkeeping cleanup (S3).

- [ ] **Step 1: Failing tests:** `test_submit_then_cancel_before_run_cancels`, `test_pending_activation_status`, `test_placement_conflict_raises_at_activation`, `test_shared_graph_two_fleets_one_binding_per_node`, `test_same_id_different_node_raises`, `test_graph_nodes_order_stable_across_hash_seeds`, `test_order_retired_on_terminal_status_only` (re-queued after recoverable failure stays live).
- [ ] **Steps 2–4:** fail, implement, pass `tests/intralogistics --no-cov`.
- [ ] **Step 5:** Commit `feat(intralogistics)!: entities, node bindings, ordering and activation`.

### Task 15: Fleet, AGV and order events (S6)

**Files:** Modify `fleet.py`, `agv.py`, `order.py`, `speed.py`; update `tests/intralogistics/test_logging.py`. Tests `tests/intralogistics/test_fleet_events.py`, `tests/intralogistics/test_motion.py`.

**Interfaces — Produces:** `OrderStatusChanged`, `OrderAssigned`, `OrderUnassigned`, `AgvStateChanged` (from `AGV.transition_to`), `AgvMoveStarted`, `AgvMoveEnded`, `AgvMoveInterrupted`, `AgvLoadChanged`, `AgvBatteryChanged`, `AgvStranded`; AGV `motion` state; `SpeedProfile.motion(...)` and `TrapezoidalProfile.motion`; intralogistics `env.debug` calls in these files removed (warnings and errors kept).

- [ ] **Step 1:** Write the **mutation-site table** in the test module docstring: for each state field of `agv` and `order`, every line in `fleet.py`/`agv.py` that mutates it (from inventory §2) and the event covering it.
- [ ] **Step 2: Failing tests:** `test_every_order_status_assignment_emits` (parametrized over the table, including `FAILED` after retries and interruption re-queue), `test_agv_unassigned_on_cleanup`, `test_direct_transition_to_emits`, `test_move_started_after_enter_permission`, `test_interrupted_move_keeps_previous_node`, `test_trapezoidal_motion_integrates_to_travel_time` (within 1e-9), `test_replay_equals_live_at_every_event_fleet` (intermediate cursors).
- [ ] **Steps 3–4:** implement, pass.
- [ ] **Step 5:** Commit `feat(intralogistics): fleet, AGV and order events`.

### Task 16: Traffic, warehouse, charging and parking events

**Files:** Modify `traffic.py`, `warehouse.py`, `charging.py`, `parking.py`. Test `tests/intralogistics/test_resource_events.py`.

**Interfaces — Produces:** `TrafficReserved`, `TrafficReleased`, `TrafficWaitStarted`, `TrafficWaitEnded`, `WarehouseInventoryChanged`, `WarehouseSlotChanged`, `ChargingStarted`, `ChargingEnded`, `ChargingPoolChanged`, `ParkingEntered`, `ParkingLeft`; node `agvs`/`reserved_by` deltas; `check_path` logs nothing.

- [ ] **Steps 1–5:** mutation-site table for `node`, `warehouse`, `charging_station`, `parking_area` fields; failing tests `test_node_capacity_two_reservations`, `test_free_traffic_multiple_agvs_on_node`, `test_swap_updates_pool_and_battery`, `test_inventory_levels_replay`, `test_replay_equals_live_at_every_event_resources`; implement; pass; commit `feat(intralogistics): traffic, warehouse, charging and parking events`.

### Task 17: Intralogistics time parameters as bindings

**Files:** Modify `agv.py`, `warehouse.py`, `charging.py`, `fleet.py`, `builders.py`; `examples/intralogistics_*.py` and their docs pages; intralogistics tests using `*_fn` lambdas. Test `tests/intralogistics/test_bindings.py`.

**Interfaces — Produces:** `AGVType(load_time=..., unload_time=...)` (scalar), `Warehouse(pick_time=..., put_time=...)` (contextual `(sku, qty)`), `ChargingStation(recharge_time=...)` (contextual `(current_level, target_level)`), bound with `env.bind` to the stream names of spec §8.2; the `*_fn` parameters are removed.

- [ ] **Steps 1–5:** failing tests `test_numbers_are_managed`, `test_contextual_callable_keeps_signature_and_is_opaque`, `test_distribution_streams_per_entity`; implement; migrate tests, examples and docs blocks; pass including `tests/test_docs_run_blocks.py`; commit `feat(intralogistics)!: managed time parameters`.

### Task 18: Observer purity

**Files:** Modify `agv.py`. Test `tests/intralogistics/test_agv_purity.py`.

- [ ] **Steps 1–5:** failing test `test_reading_utilization_does_not_change_state` (state identical before/after; results identical whether read 0 or 1000 times during a run); implement pure getters; pass; commit `fix(intralogistics): pure AGV utilization getters`.

### Task 19: Logging on the bus; remove loguru

**Files:** Create `src/simulatte/logsinks.py`; delete `src/simulatte/logger.py`, `tests/core/test_logger_finalizer.py`; modify `environment.py`, `pyproject.toml`, `uv.lock`; rewrite `tests/core/test_logger.py` → `test_logsinks.py`, `test_logger_sqlite.py` → `test_logsinks_sqlite.py`; update `test_runner.py`.

**Interfaces — Produces:** `TextSink`, `JsonSink`, `SQLiteSink`, `HistorySink` per spec §7.2, each with `attach(env)`, `close()`, `enable_component(name)`, `disable_component(name)`; `Environment(log_level=..., log_file=..., log_format=..., log_history_size=..., log_db_path=...)`; `env.log_history`; `env.sinks`.

- [ ] **Steps 1–5:** failing tests `test_info_reaches_text_sink`, `test_debug_renders_domain_events`, `test_default_sinks_want_no_domain_events`, `test_component_filters`, `test_sqlite_query_and_execute_sql`, `test_history_query`, `test_independent_levels_per_env`, `test_sinks_closed_on_env_close`, `test_runner_log_dir_json`; implement; remove loguru; pass full suite on CPython and PyPy; commit `feat(core)!: logging on the event bus, drop loguru`.

### Task 20: KPI framework

**Files:** Create `src/simulatte/kpi.py`; modify `environment.py` (`configure_kpis`), `trace/writer.py` and `trace/reader.py` (`KPI` records), `digest.py` (fingerprint KPIs). Test `tests/core/test_kpi.py`.

**Interfaces — Produces:** `KPI` dataclass (spec §12.1); `Collector(scope: Entity)` with `kpis`, `subscribes`, `attach(env) -> Self`, `on_event(event)`, `sample(kpi, value)` (emits `kpi.sample` with `scope=self.scope.id`), `scalars() -> dict[str, float]` keyed `"<scope id>/<kpi>"`, `window`; `TimeWeighted(start)` with `update(t, value)` and `mean(window_start, window_end)`.

- [ ] **Steps 1–5:** failing tests `test_time_weighted_clipping` (value 2 on [0,5), 4 on [5,10), window [3,10) → mean **24/7**, S24), `test_completion_cohort_excludes_warmup_completions`, `test_kpi_samples_not_in_digest`, `test_scalars_namespaced_by_scope`, `test_recorder_stores_kpis`; implement; pass; commit `feat(kpi): KPI declarations, scoped collectors and windows`.

### Task 21: Production collectors replace the old protocols

**Files:** Create `src/simulatte/collectors.py`; modify `shopfloor.py` (remove old protocols and wiring; `default_metrics: bool = True`; `shopfloor.metrics`), `server.py` (remove `collect_time_series`), `builders.py`, `scenario.py`; update `tests/core/test_shopfloor.py`, `test_builders.py`, `test_server.py`, docs and examples. Tests `tests/core/test_collectors.py`, `tests/core/test_collectors_parity.py`.

**Interfaces — Produces:** `EMACollector(shopfloor, alpha=0.01)`, `ShopFloorTimeSeries(shopfloor)`, `CurrentWorkloadCollector(shopfloor)` (decrements at `operation.completed`), `ServerTimeSeries(server)`, `ShopFloorKPIs(shopfloor)`, with the attributes and plot helpers of spec §12.3.

- [ ] **Step 1:** **Before changing code**, record parity fixtures from the old collectors into `tests/fixtures/collector_parity.json`: LumsCor, SLAR and immediate-release systems, seeds 1–3, plus a system with a yielding after-operation hook that holds the server 10 time units (S19). Commit the fixture.
- [ ] **Step 2: Failing tests:** parity (EMA within 1e-12, series identical), `test_two_shopfloors_one_env_scoped` (S18), `test_default_metrics_opt_out`.
- [ ] **Steps 3–4:** implement, delete old protocols, pass full suite and docs gate.
- [ ] **Step 5:** Commit `feat(core)!: bus collectors replace collector protocols`.

### Task 22: Intralogistics collectors

**Files:** Modify `intralogistics/metrics.py`, `fleet.py` (`default_metrics`, `fleet.metrics`; old parameters removed); update examples and docs. Test `tests/intralogistics/test_collectors.py`.

- [ ] **Steps 1–5:** parity fixture from the old collectors first; failing tests (parity, `test_two_fleets_one_env_scoped`, utilization computed from events without touching AGV state); implement `OrderEMACollector(fleet)`, `FleetTimeSeries(fleet)`, `FleetKPIs(fleet)`; pass; commit `feat(intralogistics)!: bus-based fleet collectors`.

### Task 23: Invariance suite

**Files:** Tests `tests/core/test_invariance.py`, `tests/intralogistics/test_invariance.py`.

- [ ] **Step 1:** For the three production builders and the advanced intralogistics example: (a) final model state equal with no subscribers and fully instrumented; (b) digests and shared KPIs equal across default logging, KPI-only, full trace, full trace plus extra collectors; (c) fresh-process determinism under `PYTHONHASHSEED` 0, 1, 123; (d) `Trace.verify()` true; (e) the counting priority policy (S9).
- [ ] **Step 2:** Fix any violation in its owning module, each with a regression test and its own commit.
- [ ] **Step 3:** Commit `test: observer invariance and determinism suite`.

### Task 24: Public surface, docs, changelog

**Files:** Modify `src/simulatte/__init__.py`, docs pages of spec §16, new `docs/guides/events-and-traces.md` (+ `zensical.toml` nav), `skills/`, `docs/ai-skill.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`. Test `tests/core/test_public_api.py`.

- [ ] **Steps 1–3:** failing `test_top_level_exports`; implement; `uv run zensical build` and the docs link checker pass; commit `docs: events, traces and the 0.13 migration`.

### Task 25: Final verification and remaining budgets

- [ ] **Step 1:** Full suite with coverage, the PyPy lane command, ruff, ty, `pnpm -C studio test`, `uv run zensical build`.
- [ ] **Step 2:** Extend `benchmarks/run.py` with modes `default_logging` and `kpi`; measure; propose their budgets; update the global spec C1.9 table after Davide accepts (S21).
- [ ] **Step 3:** Write `specs/research/sp1-final-report.md`; Cortex checkpoint; open the PR for Davide.
