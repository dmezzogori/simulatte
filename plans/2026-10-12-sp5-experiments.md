# SP5 experiments implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** approved by Davide on 2026-10-12 together with the design (all §3 recommendations, D92–D105). No task is implemented yet.

**Goal:** Simulatte 0.17 runs experiments (configurations × replications) on local and SSH workers through the SP4 coordinator, reports replication-level statistics and paired comparisons, compares and replays results in Studio, and provisions remote hosts with uv from verified source bundles.

**Architecture:** The SP4 coordinator becomes a multi-slot scheduler over the unchanged worker protocol; replications are ordinary execution requests with pinned snapshots and index-derived seeds; a core statistics module computes t intervals and paired differences from stored KPI scalars; `SshTransport` plus two protocol-1.1 message families (staging, provisioning) extend the supervisor to remote POSIX hosts; Studio adds experiment, results, charts and compare panels on the existing websocket and viewer packages.

**Stack:** Python 3.11+ stdlib (`sqlite3`, `tomllib`, `subprocess`, `math`), existing `msgpack`; `uv` on remote hosts only; React 19, dockview, uPlot, `@simulatte/playback`/`scene`/`trace`; pytest with 99 % branch coverage; vitest and Playwright.

**Spec:** [`specs/2026-10-12-sp5-experiments-design.md`](../specs/2026-10-12-sp5-experiments-design.md). Signatures below are boundaries, not licence to weaken a contract stated there or in the global design C4–C5.

## Global constraints

- No model imports in coordinator, scheduler, supervisor or statistics; one-use execution children; `warm_spares: 0` until Task 1's numbers say otherwise (D76, D102).
- Request identity unchanged for local requests: an `ExecutionRequest.runtime` without `kind` hashes exactly as in 0.16; remote requests add `kind: "uv"`.
- Store: forward migration 1 → 2 in one transaction; version 1 fixtures must open, migrate and keep every request id, attempt row and result. Newer versions refuse to open. No automatic deletion.
- Protocol 1.1 is a minor bump: a 1.0 supervisor still serves local runs; `staging-v1` and `uv-provisioning-v1` are optional capabilities required only for remote slots. Frame/envelope/fragment caps, 16 MiB window, spool quotas, heartbeat 1 s / loss 5 s / stalled 10 s, cancel escalation unchanged.
- Never ship credentials: worker config rejects secret-like keys; no environment variables are forwarded over SSH; no uv installation except `workers setup --install-uv` after an interactive confirmation.
- Remote hosts: POSIX only; Windows coordinator keeps local experiments, SSH untested and documented as such. teban is never used for testing.
- Statistics: pure Python, `math.fsum`, replication-level observations only; `n < 2` is `insufficient`, never an interval; pairs by replication index only; intervals labelled pointwise unless Bonferroni is on.
- Product limits from the design: 10 000 configurations, 1 000 000 replications, 64 grid parameters, 8 overlaid recordings, remote bundle cap 64 MiB, newest 20 experiment runs in `state.get`.
- Core install must not import Starlette, watchfiles or websockets; `simulatte experiment run` and `workers` work on core installs and PyPy.
- Coverage ≥ 99 % branch; ruff, `ty check src`, pnpm typecheck/tests/browser; docs build clean. Do not bump the version until the release task.

## Review focus

| Risk the happy path misses | Owning test |
| --- | --- |
| A revision change in Studio while an experiment runs must supersede only the interactive slot; experiment attempts keep publishing. | Task 2 `test_store_slots.py::test_revision_revokes_interactive_slot_only`; Task 4 `test_scheduler.py::test_interactive_edit_does_not_cancel_experiment` |
| A replication re-dispatched after `lost` must not let the earlier attempt's late result publish; the third loss is terminal. | Task 4 `test_scheduler.py::test_lost_redispatch_fences_old_attempt_and_stops_after_two` |
| Paired comparison when a replication failed on one side only: the pair is listed as missing, n drops, and no interval is reported with one pair. | Task 5 `test_statistics.py::test_paired_lists_missing_indices_and_insufficient_single_pair` |
| SSH session that prints a login banner, then hangs before `hello`, or dies mid-fragment: bounded noise, `lost` within the loss timeout, ssh client terminated, slot reusable. | Task 8 `test_ssh_transport.py::test_banner_then_hang_is_lost_and_slot_recovers`, `test_eof_mid_fragment_marks_lost` |
| Remote environment mutated after provisioning (file added to site-packages): the child's evidence check fails the attempt with `runtime_changed`; the environment is never republished. | Task 9 `test_provisioning.py::test_mutated_environment_fails_child_verification` |
| A replay that succeeds with a different digest must be published, labelled `unverified`, and excluded from side-by-side use by default. | Task 6 `test_replay.py::test_digest_mismatch_is_unverified_not_hidden` |

## Sequence and gates

Tasks run in order; 1 and 2 are independent of the open questions. Per task: read the current code (not the SP4 plan's wording), write the failing test, implement, run the stated checks, commit only that task's files with a conventional message. Gates: after Task 4 (local experiments end to end, headless), after Task 10 (remote acceptance on strangelove/otacon), after Task 13 (browser acceptance), then Task 14 (docs, changelog, release prep). Line numbers are deliberately omitted; symbols are given.

---

### Task 1 — Throughput benchmark (before any scheduling promise)

**Create:** `benchmarks/studio_throughput.py`, `specs/research/2026-10-xx-sp5-throughput.md`.
**Modify:** `benchmarks/README.md`.
**Interfaces:** `main()` with `--replications 32 --events {100,20000} --slots {1,2,4} --worker NAME --json PATH`; before Task 4 exists it drives `Coordinator.submit` sequentially per slot count using the SP4 API, and is re-run after Task 4 with the real scheduler.

- [ ] Measure on eva (CPython 3.14) and strangelove (CPython 3.14, PyPy 3.11): replications/min, per-replication overhead versus `bench_replications.py` at the same horizon, submit→ready, ready→publication, store commit, coordinator RSS. Record raw JSON under `specs/research/sp5-data/`.
- [ ] Write the report: numbers, the proposed default local slot count, and a yes/no recommendation on a one-per-slot warm spare with the expected saving derived from submit→ready. No implementation of spares in this task.
- [ ] Commit `bench(studio): measure coordinator replication throughput`.

**Acceptance:** report exists; design Q8 can be answered from it.

### Task 2 — Store schema 2: slots, experiment tables, migration

**Create:** `tests/execution/test_store_slots.py`, `tests/fixtures/stores/v1/` (a committed version-1 store with two requests, one result, one view).
**Modify:** `src/simulatte/execution/store.py` (`_DDL`, `schema_version = 2`, `_MIGRATIONS`, `assign`, `_require_active`, `revoke`, `create_revision(revoke_active=…)`, `recover`), `tests/execution/test_store.py`.
**Interfaces:** `RunStore.assign(request, revision, *, slot="interactive", worker=None, pinned=False) -> AttemptKey` (`pinned=True` skips the current-revision check; the fence compares the attempt with its slot's `(active, generation)`); `RunStore.slots() -> dict[str, dict]`; `RunStore.create_revision(..., revoke_active=True)` revokes only `slots['interactive'].active`; `bind_ready_view` raises for non-interactive slots; `requests.content_id` column backfilled by the migration (design Q11(b)) with `RunStore.committed_by_content(content_id) -> str | None`; `RunStore.put_experiment(definition) -> str`; `RunStore.create_experiment_run(experiment_id, preparation, configurations, replications) -> str`; `RunStore.replications(run_id, *, status=None) -> list[dict]`; `RunStore.bind_replication(run_id, configuration, index, key)`; `RunStore.set_replication_status(run_id, configuration, index, status, *, attempt=None)`; `RunStore.set_run_status(run_id, status, details=None)`; `RunStore.put_replay(request_id, original)`/`replay(request_id)`; `RunStore.prune(dry_run=False) -> dict`.

- [ ] Test: opening the v1 fixture migrates to `user_version 2`, keeps request ids, attempt rows, result and view; opening a `user_version 3` file raises. `uv run --all-extras pytest tests/execution/test_store_slots.py -k migrat --no-cov`.
- [ ] Test: two attempts active in `interactive` and `local-1` at once; `_require_active` accepts each by its own slot after a third assignment advanced the global generation; `revoke` clears only its slot; `create_revision(revoke_active=True)` supersedes the interactive attempt and leaves `local-1` active; `assign(pinned=True)` at an old revision succeeds for `local-1` and fails for `interactive`; `test_revision_revokes_interactive_slot_only`.
- [ ] Test: migration backfills `content_id` for the fixture's requests; `committed_by_content` finds the fixture result from a request that differs only in `layout_revision`.
- [ ] Test: experiment run rows round-trip; replication status transitions reject `pending → succeeded` without an attempt; `recover()` marks running runs `paused` and lost replications `pending` with `dispatches` unchanged.
- [ ] Test: `prune(dry_run=True)` lists results referenced by nothing; a result referenced by a replay or a view is kept; an interrupted prune (simulated crash after the index transaction) leaves only unindexed directories that the next `recover()` removes.
- [ ] Implement; run `uv run --all-extras pytest tests/execution -q --no-cov` and the full suite with coverage.
- [ ] Commit `feat(store): schema 2 with slots, experiments and replays`.

**Acceptance:** SP4 publication, fencing and crash tests pass unchanged against schema 2.

### Task 3 — Experiment definitions, expansion and statistics-free results model

**Create:** `src/simulatte/experiments/__init__.py`, `definition.py`, `expansion.py`, `schema/experiment-v1.schema.json`, `tests/core/test_experiments.py`.
**Modify:** `src/simulatte/execution/schema.py` (accept optional `runtime["kind"] == "uv"` with the fields of design §9; no change for mappings without `kind`; add `content_id(request) -> str` over the dict minus `layout_revision`, domain `request-content-v1`), `src/simulatte/execution/snapshot.py` (`_members` excludes `*.experiment.json` and the files listed in `[tool.simulatte.bundle] layouts`, design Q12(a)), `src/simulatte/studio/watcher.py` (those paths form a new `experiments` change class that never triggers `owner.refresh`), `pyproject.toml` (package data for the schema).
**Interfaces:** `Experiment`, `Grid`, `Explicit`, `LayoutVariant`, `Replications` (frozen dataclasses); `Experiment.to_dict()`/`from_dict()`; `load_experiment(path) -> Experiment`; `experiment_id(definition) -> str`; `expand(experiment, prepared: PreparedModel, layouts: Mapping[str, (layout_hash, document_ref)], runtime_by_kind: Mapping[str, Mapping]) -> ExperimentRun(run_id, configurations: tuple[Configuration], requests: dict[(cfg_idx, rep_idx, kind), ExecutionRequest])` with `run_id = canonical_hash("experiment-run-v1", {experiment_id, content_ids})`; `experiment_type(stopping) -> Literal["steady_state", "terminating_horizon", "terminating_finite"]`.

- [ ] Test: grid order and labels (`servers=2,wip_norm=10/base`), explicit labels, parameter validation against the model schema, limits (10 001 configurations, 65 grid parameters, seed overflow) raise `ValueError` with the design's messages; the JSON schema validates the design's example and rejects an unknown key.
- [ ] Test: seeds are `base_seed + index` for every configuration; `request_id` for a local-kind request equals the id of the same request built through `Coordinator._build_request` (identity unchanged); a `uv` runtime mapping hashes differently and round-trips through `from_dict`.
- [ ] Test: `experiment_type` for `Horizon(100, 10)`, `Horizon(100, 0)`, `FinitePopulation(...)`.
- [ ] Test: a tracked `sweep.experiment.json` and a configured `layouts/wide.json` are absent from `capture_source` membership and the source hash is unchanged when they are edited; `run_id` is identical for two expansions from two separate `prepare()` calls on unchanged content.
- [ ] Implement; `uv run --all-extras pytest tests/core/test_experiments.py tests/execution/test_schema.py --no-cov`.
- [ ] Commit `feat(experiments): definitions, identity and request expansion`.

**Acceptance:** an experiment dict expands to deterministic request ids without touching a worker.

### Task 4 — Multi-slot scheduler and headless `experiment` CLI

**Create:** `src/simulatte/execution/scheduler.py`, `src/simulatte/execution/slots.py`, `src/simulatte/experiments/runner.py`, `tests/execution/test_scheduler.py`, `tests/execution/test_experiment_cli.py`.
**Modify:** `src/simulatte/execution/coordinator.py` (`_loop`/`_run_job` become `SlotActor`; `Coordinator(project, *, slots=SlotConfig(local=1))`; `submit(..., slot="interactive")`; `start_experiment(experiment, *, workers=None, timeout=None) -> str`; `cancel_experiment(run_id)`, `resume_experiment(run_id)`, `wait_experiment(run_id, timeout=None) -> ExperimentReport`), `src/simulatte/execution/command.py`, `src/simulatte/cli.py`.
**Interfaces:** `Scheduler.next(slot) -> _Job | None` (replication-major order, kind matching the slot); `Scheduler.on_terminal(key, status)` applies the lost/redispatch rule (≤ 2) and run completion; `SlotActor(coordinator, slot, transport_factory)`; `Coordinator.events()` additionally emits `experiment_progress {run_id, counts}` coalescible by run id.

- [ ] Test (real local children, `tests/execution/test_coordinator.py::project` fixture): a 2-configuration × 3-replication experiment on `local=2` slots completes with 6 committed results, replication-major dispatch order recorded in `attempts.created`, and `wait_experiment` returns per-configuration counts.
- [ ] Test: `test_interactive_edit_does_not_cancel_experiment` (an `inputs.apply` during the run supersedes only the interactive attempt); `test_lost_redispatch_fences_old_attempt_and_stops_after_two` (inject transport EOF twice, then a third time; the first attempt's late `result` is rejected by fencing; replication ends `lost` with `dispatches == 3`); a `failed` model error is not re-dispatched.
- [ ] Test: cancel mid-run cancels active attempts and marks pending replications `cancelled` within 5 s; restart (`Coordinator` reopened on the same store) shows the run `paused` and `resume_experiment` finishes it; running the same file again after a fresh `prepare()` reuses every committed replication by content id and executes nothing; the report flags `mixed_runtime` when two replications of one configuration carry different resolved dependency hashes.
- [ ] Test CLI: `simulatte experiment run FILE --json` prints one envelope with `run_id`, counts and exit 0; `status`, `cancel`, `resume`, `delete` and `store prune --dry-run`; unknown worker name exits 2 before scheduling.
- [ ] Re-run Task 1's benchmark with the real scheduler; update the report and set the slot default accordingly (document the number in `slots.py`).
- [ ] Commit `feat(execution): multi-slot scheduler and experiment CLI`.

**Acceptance:** headless experiments run end to end locally with durable, restart-safe state.

### Task 5 — Statistics module and results reports

**Create:** `src/simulatte/statistics.py`, `src/simulatte/experiments/results.py`, `tests/core/test_statistics.py`, `tests/core/test_experiment_results.py`.
**Modify:** `src/simulatte/execution/command.py` (`experiment results`), `src/simulatte/__init__.py` (export `statistics` helpers if the public surface lists them).
**Interfaces:** `summarize(values, *, level=0.95) -> Estimate`; `t_quantile(p, df) -> float`; `paired(a, b, *, level=0.95) -> Paired`; `bonferroni(level, m) -> float`; `welch(series, *, window, grid) -> list[tuple[float, float]]`; `ExperimentReport.table(level, *, bonferroni=False) -> list[Row]`; `ExperimentReport.paired(baseline_label, level, *, bonferroni=False) -> list[PairedRow]`; `ExperimentReport.missing() -> list[Missing]`; JSON rendering `report.to_dict()` with `pointwise: bool`, `effective_level`.

- [ ] Test: `t_quantile(0.975, df)` matches reference values for df 1, 2, 5, 10, 30, 60, 120 within 1e-6 and approaches 1.959964 as df → 10⁶; `summarize` states `ok`/`degenerate`/`insufficient` on `[1.0, 2.0]`, `[3.0, 3.0]`, `[3.0]`, `[]`.
- [ ] Test: `test_paired_lists_missing_indices_and_insufficient_single_pair`; Bonferroni with m = 3 lowers the level and clears `pointwise`; `welch` on two step series with window 3 reproduces a hand-computed average.
- [ ] Test: `ExperimentReport` built from a store with one failed replication reports n per configuration, lists the failed index with its attempt status, and omits intervals where n < 2; KPI absent in one replication is `missing` for that replication only.
- [ ] Implement; `uv run --all-extras pytest tests/core/test_statistics.py tests/core/test_experiment_results.py --no-cov`; PyPy lane includes `tests/core`.
- [ ] Commit `feat(statistics): t intervals, paired comparisons and experiment reports`.

**Acceptance:** `simulatte experiment results RUN --baseline LABEL` prints the design's table with the pointwise label.

### Task 6 — Replays

**Create:** `src/simulatte/execution/replay.py`, `tests/execution/test_replay.py`.
**Modify:** `coordinator.py` (`replay(request_id, *, worker=None, timeout=None) -> ReplayResult`), `store.py` (replay status/verification fields), `command.py`/`cli.py` (`simulatte replay`).
**Interfaces:** `verify_replay(original: PublishedResult, replay: PublishedResult) -> Verification(verified: bool, reason: str | None)` comparing `fingerprint.digest`, common KPI scalars, `resolved.runtime.facts`, `resolved.dependencies` hash and physical layout hash; `ReplayResult(key, verification)`.

- [ ] Test: a `kpi` replication replayed at `full` is `verified`; `test_digest_mismatch_is_unverified_not_hidden` (inject a differing stored digest); KPI mismatch and runtime-facts mismatch reasons; `no_compatible_worker` when the original worker kind is absent from the pool; replay of a non-committed request is a `ValueError`.
- [ ] Test CLI: `simulatte replay REQUEST --json` prints verification and exit 0 when verified, 1 when unverified.
- [ ] Commit `feat(execution): full-level replays with fingerprint verification`.

**Acceptance:** replays are linked `replay-of`, labelled, and never alter the original result.

### Task 7 — Worker configuration and `simulatte workers`

**Create:** `src/simulatte/execution/workers.py`, `src/simulatte/execution/workers_cli.py`, `tests/execution/test_workers_config.py`, `tests/execution/test_workers_cli.py`.
**Modify:** `cli.py`.
**Interfaces:** `load_workers(path=None) -> dict[str, WorkerSpec]` (`SIMULATTE_WORKERS` override; `WorkerSpec(name, host, slots, ssh_args, uv, cache)`); `check_worker(spec, *, ssh=run_ssh) -> WorkerCheck(reachable, platform, shell, uv_version, cache_writable, messages)`; `setup_worker(spec, *, install_uv=False, confirm=input) -> WorkerCheck`; the uv installer command is a module constant run only after `confirm()` returns `y`.

- [ ] Test: TOML parsing and strict validation; keys `password`, `token`, `key`, `identity_file`, `secret` rejected with "credentials are not accepted"; unknown worker name error message lists known names.
- [ ] Test: `check_worker` with a fake `ssh` executable on PATH reporting Linux/uv, Linux/no-uv, Windows (refused), connection failure; `setup_worker(install_uv=True)` with `confirm` returning `n` runs no installer; with `y` runs exactly the constant command once.
- [ ] Commit `feat(workers): user worker configuration, check and setup commands`.

**Acceptance:** `simulatte workers check bigboss` reports missing uv without installing anything.

### Task 8 — SSH transport and remote bootstrap

**Create:** `src/simulatte/execution/ssh.py`, `src/simulatte/execution/bootstrap_remote.py` (the `sh`/Python bootstrap text, ≤ 8 KiB), `tests/execution/test_ssh_transport.py`, `tests/execution/fake_ssh.py` (fixture executable).
**Modify:** `transport.py` (`Transport` protocol shared by `LocalTransport` and `SshTransport`), `snapshot.py` (`freeze_runtime` includes a pure-Python `msgpack` copy and an archive form `runtime.tar` + `sha256`), `supervisor.py` (`worker_hello` protocol `[1, 1]`, capabilities), `coordinator.py` (`_start_worker` per slot kind, 1.0 supervisors accepted for local slots only).
**Interfaces:** `SshTransport.start(spec: WorkerSpec, *, runtime: RuntimeSnapshot, requires_python: str) -> SshTransport` (stages the runtime by a separate `ssh … sh -c STAGE_SCRIPT < runtime.tar` when the hash is absent, then starts the supervisor session); `.send/.receive/.close` as `LocalTransport`; `.diagnostics`.

- [ ] Test with `fake_ssh.py` scenarios: banner then valid hello; `test_banner_then_hang_is_lost_and_slot_recovers`; `test_eof_mid_fragment_marks_lost`; stderr flood is bounded; noise above 64 KiB before magic is a protocol error; the staging step verifies the SHA-256 and is skipped when the fake host reports the hash present; environment of the spawned `ssh` contains no `UV_*` or `SIMULATTE_*` variables from the parent beyond PATH/HOME/SSH_AUTH_SOCK.
- [ ] Test: a frozen runtime archive imports `simulatte.execution.supervisor` from a `python -I -S` child with only the archive root on `sys.path` (pure-Python msgpack fallback).
- [ ] Commit `feat(execution): SSH transport with staged trusted runtime`.

**Acceptance:** a real `ssh strangelove` session reaches `hello` with protocol 1.1 (manual, recorded in Task 10).

### Task 9 — Staging and uv provisioning protocol

**Create:** `src/simulatte/execution/staging.py` (coordinator side), `src/simulatte/execution/provisioning.py` (supervisor side), `src/simulatte/execution/preflight.py`, `tests/execution/test_staging.py`, `tests/execution/test_provisioning.py`, `tests/execution/test_preflight.py`, `tests/execution/fake_uv.py`.
**Modify:** `protocol.py` (`_TYPES` += `stage`, `stage_fragment`, `staged`, `provision`, `provisioned`), `supervisor.py` (`_command` handles the new types; blob store under `<cache>/blobs`), `runtime.py` (`verify_runtime` branches on `kind`), `child.py`/`driver.py` (remote config paths), `coordinator.py` (`SlotActor` runs stage → provision → run for remote slots; `_build_request` picks the `uv` runtime for remote kinds).
**Interfaces:** `preflight(project, lock_path, *, cap=64 << 20, coordinator_python: str) -> Preflight(spec: dict, warnings: list[str])` raising `PreflightError` for path deps outside root, missing lock, `missing_simulatte_dependency`, `incompatible_simulatte`, a `.python-version` outside `requires-python`; `spec["python"]` is the exact pin or the coordinator's `major.minor.patch`; `stage_blob(transport, kind, path, sha256, key)`; `Provisioner(cache).provision(spec, source_blob, *, timeout) -> dict` running the exact `uv sync` argument list of design §9 (including `--python <spec["python"]>`) under `fcntl` lock, killing uv's process group at `timeout` and answering `provisioning_failed {reason: "timeout"}`; environments published read-only with a `published` marker and `evidence.msgpack`; `WorkerSpec.provision_timeout` default 600.

- [ ] Test staging: fragments ≤ 1 MiB under the window with `ack` credit; hash mismatch fails the attempt; duplicate blob answers `cached: true` with zero fragments.
- [ ] Test provisioning with `fake_uv.py`: argument list exact (including every `--no-install-package`); `uv_missing`; failing sync publishes nothing; a second provision with the same spec runs no uv; `test_mutated_environment_fails_child_verification`.
- [ ] Test preflight: path dependency outside root, private registry warning text, lock absent, lock without `simulatte`, locked `simulatte==0.16.0` versus coordinator 0.17 rejected, `.python-version` outside `requires-python`, bundle over cap.
- [ ] Test provisioning timeout: a `fake_uv.py` that sleeps is killed at `timeout`, the attempt ends `failed` with `provisioning_failed/timeout`, nothing is published and the replication is not re-dispatched; heartbeats kept arriving during the wait.
- [ ] Test end to end with `LocalTransport` plus the staging/provisioning path (same supervisor, local cache dir, fake uv providing the current site-packages): a remote-kind request publishes a result whose `resolved.runtime` carries `kind: "uv"`.
- [ ] Commit `feat(execution): remote staging and uv provisioning`.

**Acceptance:** a remote-kind replication publishes through the unchanged publication path.

### Task 10 — Remote acceptance on personal hosts

**Create:** `specs/research/2026-10-xx-sp5-remote-acceptance.md`, raw data under `specs/research/sp5-data/`.
**Create (fixture project):** `examples/uv_project/` with `pyproject.toml`, `uv.lock`, a decorated model, a path-dependency package inside the root, and `experiment.json`.

- [ ] From eva: `workers check` on strangelove, otacon, bigboss; `setup --install-uv` refusal path on bigboss (answer `n`); experiment with `workers = ["local", "strangelove", "otacon"]`, 2 × 4 replications at `kpi`, then a replay on the original host and one forced to a different host (`no_compatible_worker` or `unverified` as designed).
- [ ] Record: bundle size, staging time first/second replication, provisioning time, per-host throughput, cancellation latency over SSH, a deliberate `ssh` kill mid-run producing `lost` and re-dispatch, and `workers prune`.
- [ ] Commit `docs(research): SP5 remote acceptance evidence`.

**Acceptance:** report distinguishes measured results from remaining gates; teban untouched.

### Task 11 — Studio server: experiment, comparison, replay and worker commands

**Create:** `src/simulatte/studio/experiments.py`, `tests/core/test_studio_experiments.py`.
**Modify:** `studio/websocket.py` (`COMMANDS` += design §7 list), `studio/app.py` (`_command` branches, `snapshot()` fields `experiments`, `workers`, `replays`; `_pump` routes events for non-interactive attempts; `GET api/experiments/<run>/results`), `studio/lifecycle.py` (worker pool from config at start), `tests/core/test_studio_websocket.py` (the `experiment.start` rejection assertion becomes acceptance).
**Interfaces:** TS `Command`/`Notification` unions in `studio/apps/viewer/src/studio-client.ts` updated in the same task to keep typecheck green.

- [ ] Test (Starlette `TestClient`, pattern of `test_studio_live.py`): define → start → progress notifications coalesced per run → `comparison.get` returns the Task 5 report dict → cancel; `experiment.start` with an unknown worker returns `validation_failed`; `replay.start` returns the replay key and a later `replay_state` carries `verified`; `state.get` lists at most 20 runs; results endpoint requires auth and is bounded.
- [ ] Commit `feat(studio): experiment, comparison and replay protocol`.

**Acceptance:** Studio protocol exposes experiments without changing SP4 commands.

### Task 12 — Viewer: experiments, results, charts panels and overlays

**Create:** `studio/apps/viewer/src/experiments.tsx`, `results-table.tsx`, `overlay-charts.ts`, `welch.ts`, tests under `studio/apps/viewer/test/`.
**Modify:** `studio-panel.tsx`, `main.tsx`, `controller.ts` (`charts()` accepts several `Recording`s with a shared x-scale; 8-recording cap), `studio-client.ts`.

- [ ] Test (vitest): results table renders n, mean, half-width, state badges and the "pointwise 95 % t intervals" / "Bonferroni-adjusted (m = …)" label; missing pairs list; overlay chart merges two recordings' series on one x-scale; Welch window control recomputes from fetched series.
- [ ] Test: experiment form validates against the schema before `experiment.define`; progress counts update from coalesced notifications; selecting a replication opens it in the normal viewer.
- [ ] `pnpm typecheck && pnpm test && pnpm build`; commit `feat(viewer): experiment, results and chart panels`.

**Acceptance:** panels work against the Task 11 server in Chromium.

### Task 13 — Compare playback (two sessions, one clock) and browser acceptance

**Create:** `studio/apps/viewer/src/compare-playback.ts`, `compare-panel.tsx`, `studio/apps/viewer/test/compare-playback.test.ts`, `studio/browser/compare.spec.ts`.
**Modify:** `controller.ts` (host registration for a second scene container), `packages/playback` (export a `SharedClock` usable by two `Playback`s if the existing `Clock` interface is insufficient), `studio/scripts/studio-smoke.mjs`.

- [ ] Test: two `Session`s advance on one clock; seek on either seeks both; disposing the compare view leaves the main scene's renderer intact (`liveScenes` accounting); a KPI-only result shows the replay action and no scene; an `unverified` replay shows its badge and is excluded unless the user opts in.
- [ ] Browser: Chromium, Firefox, WebKit `compare.spec.ts` with zero page errors; extend the Safari manual checklist in the acceptance report.
- [ ] Commit `feat(viewer): synchronized side-by-side playback`.

**Acceptance:** two full recordings play in lockstep; disposal is clean in all three engines.

### Task 14 — Docs, changelog, platform lanes and release preparation

**Create:** `docs/guides/experiments.md`, `docs/guides/remote-workers.md`.
**Modify:** `docs/development/studio.md`, `zensical.toml` nav, `CHANGELOG.md` (`## Unreleased` → 0.17.0 entries), `.github/workflows/ci.yml` (new test paths in PyPy and native lanes; Windows lane runs a 2-slot local experiment and asserts the SSH-unsupported message), `CLAUDE.md` (SP5 modules), `pyproject.toml` version bump as the final commit.

- [ ] Docs cover: experiment file and Python API, seeds and common random numbers, the three experiment types, statistics semantics (replication-level, insufficient, pointwise, Bonferroni, missing pairs), Welch aid, replays and verification labels, worker configuration without credentials, `workers check/setup`, provisioning requirements (uv project, lock, path deps, private indexes), bundle contents and the 64 MiB cap, cache layout and `workers prune`, retention commands, Windows limits.
- [ ] Full gates: `uv run --all-extras pytest`, ruff, `ty check src`, pnpm typecheck/test/build/test:browser, docs build, wheel/sdist checks; record in the acceptance report.
- [ ] Commit `chore(release): prepare 0.17.0`.

**Acceptance:** CI green on CPython 3.11–3.15, PyPy, native macOS/Windows lanes, trace-ts, bench; remote acceptance report complete; release is a separate maintainer action.

## Review handoff

Per task: a reviewer reads the task's tests against the design section it implements, runs the stated commands, and checks that the SP4 guarantees in design §12 still hold (fencing, publication, bounds, cancellation, no credentials). Final whole-branch review before PR: identity stability of local requests, migration safety on a 0.16 store, and that no path installs uv or forwards environment variables.
