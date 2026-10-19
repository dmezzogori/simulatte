# Studio P1: Projects and First Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans with Davide's selected execution method. Implement task-by-task; checkboxes track verified work. Read the shared plan index and product spec first.

**Goal:** Make one command open a persistent project and complete the first multi-model run workflow safely.

**Architecture:** Introduce data-only workspace definitions and a project service. One coordinator/store owns project execution; immutable per-model contexts replace implicit reliance on the selected model. Preparation/inspection, simulation admission and runtime generation ownership are distinct responsibilities.

**Tech Stack:** Python stdlib/SQLite/uv and existing execution transport; existing React viewer and authenticated Studio protocol. Use `tomlkit` for TOML-preserving editing in the Studio extra; parsing stays stdlib.

**Spec:** [§4–6 and scenarios 1–4, 10–11](../specs/2026-10-19-studio-project-experience-design.md). [Shared constraints and gates](2026-10-19-studio-project-experience.md) apply throughout.

## Global constraints

- “No simulation starts merely because Studio opened.”
- “Existing user files are never replaced implicitly.”
- “Initially retain one project-wide interactive execution slot plus experiment slots; selection and execution ownership are separate.”
- “Updates never install into an environment used by active work, and cleanup cannot remove a generation while it has active owners.”
- “Migrations preserve existing request/result identities.”
- Preserve headless file-based APIs; workspace UUIDs are association metadata, not a reason to rehash old requests. All execution contexts capture bytes before admission.

## Review focus

| Risk | Test/task |
| --- | --- |
| A colon-qualified model is mistaken for a new folder. | T1 `test_legacy_reference_never_creates_directory` |
| Copy/rename/remove/re-register silently assigns old results to a new model. | T2 `test_model_identity_survives_rename_removal_and_copy` |
| Inspection or a source change fences another model's active publication. | T4 `test_inspect_b_preserves_active_a` |
| Runtime activation restarts Studio while jobs still own its environment. | T3/T5 `test_generation_activation_waits_for_owners` |
| Broken project dependencies prevent opening already saved work. | T5/T6 `test_recovery_viewer_never_imports_project` |

## Contracts selected for implementation

Use `[tool.simulatte.project]` with `schema = 1`, persistent UUID `id` and optional `default_model` (registration key). Register `[tool.simulatte.models.<key>]` with persistent UUID `id`, `name`, `entrypoint = "relative/path.py:build"`, and `layout`. Registration keys are human-readable; stable UUIDs survive renaming keys/labels. `[tool.simulatte.analyses.<key>]` reserves the same registration pattern for P2, with `entrypoint`. Existing `[tool.simulatte.bundle]` remains intact. Reject duplicate IDs, escaping paths, unknown schema versions and invalid defaults; a removed ID is tombstoned in history. Entrypoint changes keep history but create new captured source/preparation identity.

`projects/types.py` defines frozen `ModelRegistration(id: str, key: str, name: str, entrypoint: str, layout: str)`, `AnalysisRegistration(id: str, key: str, name: str, entrypoint: str)`, `WorkspaceDefinition(root: Path, id: str, models: tuple[ModelRegistration, ...], analyses: tuple[AnalysisRegistration, ...], default_model: str | None)`, and `StudioTarget(root: Path, legacy_reference: str | None)`. UUIDs validate with `uuid.UUID`; paths use existing portable-path rules. Configuration editing uses a compare-and-swap digest of `pyproject.toml`; show proposed changes before applying. Non-Git starters include explicit bundle membership; do not silently initialize Git or capture every input/output file.

Do not create one `Coordinator` per model: each currently opens the same store lock. Add `Coordinator.for_workspace(root: Path, store: RunStore, environments: EnvironmentPool) -> Coordinator`, with no required default model. Existing constructor/API delegates to a default context for old headless callers. `ModelContext` in `execution/model_context.py` owns registration, model-specific `ProjectSpec`, current `Revision`, `InputOwner` and latest `PreparedModel`. A submitted job captures its context; selection is UI state only. `EnvironmentPool` and generation records are defined in T3. One project service closes the shared store exactly once.

Schema 3 adds `project_meta`, `models` (including tombstones), `model_revisions`, `attempt_models`, `history_annotations`, `environment_generations`, and a general `references` table (`owner_kind`, `owner_id`, `target_kind`, `target_id`, unique tuple). Use foreign keys for concrete result associations and validated target kinds for references. Global monotonically numbered revisions remain; each model has its own latest pointer. Existing requests/attempts/results retain bytes and IDs. The technical implementation must place SQL in `execution/project_store.py`/`store_migrations.py` rather than continuing to grow unrelated store methods.

## Task 1: Workspace definitions, target parsing and safe starters

**Files:** Create `src/simulatte/projects/{__init__,types,config,targets,starters}.py`, `tests/core/test_projects.py`. Modify `src/simulatte/execution/snapshot.py`, `tests/execution/test_snapshot.py` and `pyproject.toml` only for the Studio TOML editor dependency.

**Interfaces:** `resolve_target(value: str | None, *, cwd: Path) -> StudioTarget`; `load_workspace(root: Path) -> WorkspaceDefinition | None`; `preview_config(root: Path, definition: WorkspaceDefinition) -> ConfigEdit` and `apply_config(edit: ConfigEdit) -> None`, where `ConfigEdit(path, original_sha256: str | None, proposed_bytes: bytes)` is frozen. `starter_files(kind: Literal["production", "logistics"]) -> dict[str, bytes]`; `install_starter(root: Path, files: Mapping[str, bytes]) -> None` refuses existing target files and rolls back only files it created on failure. Reuse current runnable domain examples without requiring Studio inside model code.

- [x] Add tests: no argument resolves cwd; missing nested folder is created; existing directory ending `.py` opens as a directory; existing `MODEL.py:alternate` preserves the reference; missing explicit entrypoint source raises and creates nothing; unsupported regular files fail. Resolve symlinks consistently with existing root containment checks.
- [x] Add configuration tests for duplicate UUID/default errors, preservation of unrelated TOML/comments and stale digest. Pin the core assertions:
  ```python
  assert resolve_target(None, cwd=tmp_path).root == tmp_path
  assert load_workspace(project).models[0].id == original_model_id
  assert unrelated_toml_before == unrelated_toml_after
  ```
  `project` fixtures in `test_projects.py` create both Git and explicit non-Git bundle variants; no model import is needed.
- [x] Run `uv run --all-extras pytest tests/core/test_projects.py tests/execution/test_snapshot.py --no-cov` and confirm new cases fail before implementing the contracts. Preserve explicit capture rules and ensure project history/cache files are excluded.
- [x] Implement the modules; rerun the same tests to pass. Add a runnable tiny starter for each domain with explicit source/input/layout declarations.
- [x] Commit `feat(studio): define persistent projects and resolve launch targets`.

## Task 2: Store schema 3, model history and read-only access

**Files:** Create `src/simulatte/execution/{project_store,store_migrations,store_reader}.py`, `tests/execution/test_project_store.py`; modify `execution/store.py`, `execution/command.py`, existing store/prune tests. Create schema-2 fixtures under `tests/fixtures/stores/project-v2/`.

**Interfaces:** `ProjectStore(store: RunStore)` exposes `register(workspace)`, `associate_attempt(key: AttemptKey, model_id: str)`, `history(model_id: str | None, *, after: str | None, limit: int = 50) -> HistoryPage`, `annotate(attempt_id: str, *, name: str, text: str, important: bool)` and `link(owner_kind, owner_id, target_kind, target_id)`. `HistoryPage(items: tuple[dict, ...], next_cursor: str | None)` is data-only. `StoreReader.open(root: Path) -> StoreReader` never migrates/writes and supports read-only history/result/artifact queries; close it explicitly. Backup uses SQLite's backup API under exclusive writer ownership before migration, with a manifest of prior schema/version; fail without touching the original on backup failure.

- [x] Test schema 1→2→3 and 2→3 preserve requests, attempts, results and view identities; schema 4 refuses write-open. Test backup failure and interrupted migration. Test reading schema 2 with `StoreReader` does not change bytes or create a writer.
- [x] Test `test_model_identity_survives_rename_removal_and_copy`: rename key/label, remove registration, copy quiescent project and register a different UUID at the old key; `history(old_id)` still returns the original attempt. Ambiguous legacy source/entrypoint matches remain unassigned; explicit association never rewrites original provenance.
- [x] Test annotations round-trip and `important`/references block prune. Test cursor pagination is stable while new runs arrive; do not use OFFSET over a changing history.
- [x] Run `uv run --all-extras pytest tests/execution/test_project_store.py tests/execution/test_store.py tests/execution/test_store_slots.py --no-cov` red, implement migration/reader/reference checks, then rerun green.
- [x] Commit `feat(store): preserve project model history and migrate with backup`.

## Task 3: Immutable local environment generations

**Files:** Create `src/simulatte/projects/environments.py`, `tests/execution/test_project_environments.py`; modify `execution/provisioning.py`, `runtime.py`, `transport.py`, `bootstrap.py`, `project_store.py`. Reuse fake-uv patterns without importing POSIX-only fake SSH tests on Windows.

**Interfaces:** frozen `EnvironmentGeneration(id: str, interpreter: Path, evidence: Mapping[str, Any], state: Literal["prepared", "selected", "retired"])`; `EnvironmentPool.prepare(workspace: WorkspaceDefinition, *, lock_update: bool = False) -> EnvironmentGeneration`, `acquire(generation_id: str, owner_id: str) -> EnvironmentGeneration`, `release(owner_id: str)`, `select(generation_id: str) -> Literal["selected", "restart_required", "busy"]`, `reconstruct(evidence: Mapping[str, Any]) -> EnvironmentGeneration`. Generation identity includes lock bytes, Python requirements, platform/runtime identity and provisioning inputs. Store durable evidence separately from `.simulatte/cache/environments/<id>`. Provision at its final content-addressed generation path under a lock and unpublished marker; validate before atomically publishing the marker/selection. A virtualenv can contain absolute interpreter paths, so do not rename an installed environment to promote it. Failure removes only an unpublished generation with no owners. Do not point uv at an active generation for updates.

- [ ] Test concurrent prepares converge on one verified generation; failure/cancellation removes only temporary state; locked install does not change `uv.lock`. An explicit lock update is previewed before promotion, using T1's file-CAS pattern.
- [ ] Test `test_generation_activation_waits_for_owners`: acquire old generation for simulation, analysis-shaped owner and experiment; prepare a new one; old interpreter/evidence remain unchanged, cleanup refuses owned generations, and runtime restart remains pending until all owners release. Experiment resume retains captured evidence even across coordinator restarts.
- [ ] Test reconstruction success and unavailable interpreter/package cases; current project selection remains unchanged. Existing-environment mode never installs on open; changed evidence refuses new work. Follow the existing documented limits of metadata-based drift detection, not a claim of continuous byte monitoring.
- [ ] Run `uv run --all-extras pytest tests/execution/test_project_environments.py tests/execution/test_runtime.py tests/execution/test_provisioning.py --no-cov` red/green. Native macOS and Windows checks cover local provisioning; preserve remote POSIX policy.
- [ ] Commit `feat(studio): manage immutable project environment generations`.

## Task 4: Model contexts, separate inspection and explicit admission

**Files:** Create `src/simulatte/execution/{model_context,admission}.py`, `tests/execution/test_project_execution.py`; modify `coordinator.py`, `inputs.py`, `store.py`, `slots.py`, `scheduler.py`, `studio/lifecycle.py`, `studio/watcher.py`, and relevant supersession tests.

**Interfaces:** `prepare_model(model_id: str, *, timeout: float | None = None) -> PreparedModel`; `inputs_for(model_id: str) -> InputOwner`; `admit_run(model_id: str, request: ExecutionRequest, revision: Revision, *, auto_session: str | None = None) -> Admission`; `stop_and_run(owner: AttemptKey, model_id: str, request: ExecutionRequest, revision: Revision) -> Admission`. `Admission` is a frozen tagged record: `status: Literal["started", "busy", "stale"]`, `key: AttemptKey | None`, `owner: AttemptKey | None`. Caller-provided owner and revision are checked atomically; cancellation waits outside store locks, then rechecks ownership. Never run a new job if another client won the slot in the meantime. Inspection uses one separate local slot named `inspection`, has no autorun side effect and retains existing child timeout/cleanup. Add model-scoped revision access rather than comparing every input to the project's global latest revision.

- [ ] Add `test_inspect_b_preserves_active_a`: a barrier holds A before publication; prepare/configure B and edit both models; release A and assert `store.result(a.attempt_id) is not None`. Check stale preparation for B is rejected without changing A. Updating the old global revision counter must not fence A.
- [ ] Test busy Run returns `status == "busy"` and owner A, keeps B's draft and schedules no work. Stop-and-run starts B only after A's descendants exit; cancellation failure and an intervening owner prevent B admission. Old A output remains fenced after an intentional replacement.
- [ ] Test auto-session token ownership, disabling/re-enabling auto-run, selection change and source edits: only the same active auto-session can replace its attempt. Manual attempts and experiments remain intact. Update old tests that deliberately expected startup supersession to assert the newly explicit behavior; preserve transport/fencing tests.
- [ ] Run `uv run --all-extras pytest tests/execution/test_project_execution.py tests/execution/test_inputs.py tests/execution/test_coordinator.py tests/execution/test_scheduler.py tests/core/test_studio_lifecycle.py --no-cov` red/green around implementation.
- [ ] Commit `feat(execution): isolate model preparation and enforce run ownership`.

## Task 5: Launcher handoff, setup service and recovery viewer

**Files:** Create `src/simulatte/projects/{service,bootstrap}.py`, `src/simulatte/studio/project_commands.py`, `tests/core/test_studio_project_commands.py`; modify `cli.py`, `studio/{command,app,lifecycle,websocket,server}.py`, `execution/command.py`, existing CLI/auth/HTTP tests.

**Interfaces:** `ProjectService.open(target: StudioTarget, *, read_only: bool = False) -> ProjectService` owns the store, environment pool and optional coordinator; `state() -> dict`, `close()`. Setup works without a selected model/coordinator. `launch_project(target: StudioTarget, *, port: int, no_browser: bool) -> None` starts launcher setup/recovery or hands off to the matching selected runtime with a one-use local handshake; the new process validates protocol/assets/runtime capability before taking writer ownership. Never run two writers during handoff. Add `project.get`, `project.configure`, `model.prepare`, `environment.prepare`, `environment.select`; carry `model_id` and expected model revision on model commands. Existing authenticated envelopes, idempotent command IDs, limits and reconnect resync remain.

- [ ] Test no-argument/path/legacy CLI calls and applicable flags. Hide the internal worker command from normal help while keeping the internal entrypoints. Invalid flags/schema errors precede simulation admission.
- [ ] Test `test_recovery_viewer_never_imports_project` with a project module that raises on import and an unusable project interpreter. History, supported traces and generic saved artifacts remain readable using launcher assets; mutation/execute endpoints reject. Unknown store version is explained without migration.
- [ ] Test missing Studio in the selected environment, cancelled setup, mismatched server/assets, handoff failure and locked store: all produce actionable states without fallback execution in the wrong interpreter. Reuse HTTP auth/origin/CSP/range tests for setup/recovery routes.
- [ ] Run `uv run --all-extras pytest tests/core/test_cli.py tests/core/test_studio_command.py tests/core/test_studio_project_commands.py tests/core/test_studio_http.py tests/core/test_studio_websocket.py --no-cov` red/green.
- [ ] Commit `feat(studio): open projects with setup and recovery flows`.

## Task 6: Focused shell and first usable vertical slice

**Files:** Create viewer `src/workspace/{shell,overview,models,run-page,setup}.tsx`, `src/workspace/state.ts`, `test/workspace.test.tsx`, `studio/browser/project.spec.ts`, `studio/browser/project-fixture.ts` (starts and cleans up a real installed-package project server); modify viewer `main.tsx`, `studio-client.ts`, `studio-playback.ts`, `style.css`, `parameters.tsx`, `run-history.tsx`. Keep standalone recording viewing operational.

**Interfaces:** `WorkspaceRoute` is a discriminated union for overview, model(id), run(attempt), experiments, analyses and presentations. Hash navigation carries IDs and browser back/forward works without another routing dependency. `WorkspaceShell({client, state})` owns navigation; `RecordingPane` owns/disposes the existing Controller and playback listeners when mounted. `ProjectState` mirrors the validated project API and separates `selectedModelId`, `activeRun` and per-model drafts. Unsupported later-package actions are clearly unavailable; do not show fabricated data.

- [ ] Test that selecting B never sends `run.start`, losing/reconnecting the socket restores authoritative ownership, and navigating away preserves parameter drafts and active progress. Add component assertions that one sidebar owns top-level navigation and Run is the primary model-page action.
- [ ] Browser test the installed service: empty folder → starter preview → prepare environment → select model → explicit Run → inspect → close/reopen history. Repeat existing-project registration and legacy launch; assert zero attempts immediately after opening. Exercise busy-owner feedback and explicit stop.
- [ ] Implement shared typography/spacing/controls and the selected focused structure; retain domain layout editing via model context and existing recording entity/KPI controls. Do not defer all UX quality until P3.
- [ ] Run `pnpm --dir studio --filter @simulatte/viewer test`, `pnpm --dir studio typecheck`, then build/package and `pnpm --dir studio exec playwright test browser/project.spec.ts`. Capture screenshots at 1280×800 and 1440×900 and keyboard traversal. Expected: no console errors, usable first-run flow, no duplicate navigation.
- [ ] Commit `feat(studio): add project workspace and explicit first-run flow`.

## Task 7: P1 documentation and integration gate

**Files:** Create `docs/studio/{index,projects,environments}.md`, update `docs/development/studio.md`, `zensical.toml`, `README.md`, and `specs/studio-decisions.md` with approved amendments; create `specs/research/2026-10-19-studio-p1-acceptance.md` when evidence exists. Link the global design to the superseding project-first/explicit-run contracts without rewriting historical decisions.

- [ ] Document installed-package commands, non-Git membership, model registration, saved history, environment update/recovery and busy-slot behavior. Keep old deep links with short redirects/navigation pointers. Replace affected “safe to delete .simulatte” advice now; do not wait for P4.
- [ ] Run the shared package gate and installed-wheel tutorial on bigboss. Record schema migration, legacy CLI, two-model ownership, broken-environment recovery and remote-worker regression evidence. Preserve supported headless and bare-Python paths.
- [ ] Review actual UI/screenshots and failures with Davide, resolve P1 defects, and commit `docs(studio): document and verify project workflows`. P1 is complete only with evidence for product scenarios 1–4 and the P1 portions of 10–11.

## Primary implementation references

Use [TOML Kit](https://tomlkit.readthedocs.io/en/latest/) to preserve unrelated formatting/comments during configuration edits. Provision isolated generations through the [uv project environment path](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path), with an explicit target per operation.
