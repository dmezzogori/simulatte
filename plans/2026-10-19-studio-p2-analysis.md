# Studio P2: Numerical Data and Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans with Davide's selected method. Implement task-by-task; checkboxes track verified work. P1's project/store/environment interfaces must be available first.

**Goal:** Let Python and headless users retain declared numerical observations, analyze custom populations and save reproducible numerical/figure outputs without recording every animation.

**Architecture:** Observational collectors write typed chunked artifacts through existing result publication. A separate analysis job consumes an immutable input manifest and publishes its own artifact manifest; it is not a simulation with a fabricated trace. Saved definitions, input snapshots, executions and outputs have separate identities.

**Tech Stack:** Python stdlib, existing msgpack/SQLite/process machinery; optional Matplotlib/Seaborn and Plotly. Existing replication-level statistics remain authoritative. The browser-facing artifact reader uses bounded JSON pages and safe rendering added in P3.

**Spec:** [§7–8 and scenarios 5–8, 11](../specs/2026-10-19-studio-project-experience-design.md); [shared constraints](2026-10-19-studio-project-experience.md).

## Global constraints

- “No blanket capture of every entity attribute is introduced.”
- “User predicates must remain observational and cannot mutate the simulation; never invoke user predicate code during event construction.”
- “Analytical configuration changes affect request identity even when simulation parameters are unchanged.”
- “Experiment inference remains replication-level.”
- “The execution reads this manifest, never a fresh query for the experiment's latest results.”
- “Recomputing defaults to the same frozen input snapshot.”
- “Do not serialize an entire dataset into a websocket message or load it all into the browser.”
- Optional analysis packages must not become required for importing or running core Simulatte. Managed analysis initially uses one local analysis slot, independent of simulation/inspection slots; waiting work already owns its snapshot.

## Review focus

| Risk | Test/task |
| --- | --- |
| Mutable classifications move jobs/AGVs into different populations over time. | T1/T3 `test_membership_uses_declared_observation_time` |
| Missing fields, empty populations or non-finite values silently become zeros. | T2/T6 `test_missingness_and_exact_numeric_roundtrip` |
| Eight-replication inputs change when an experiment reaches 24. | T4 `test_snapshot_remains_eight_after_resume` |
| A result is pruned after preview but before analysis starts. | T4 `test_snapshot_admission_races_publication_and_prune` |
| Analysis cancellation/crash publishes incomplete output or changes original KPIs. | T5 `test_failed_analysis_never_publishes_success` |

## Contracts selected for implementation

`analysis/types.py` defines `Value` as null/bool/int64/float64/string or recursively typed lists/string-keyed records. Schema descriptors express those types and nullability; arbitrary Python objects are rejected. `Field(name, dtype, unit, nullable, observed_at)` and `DatasetSchema(name, fields, observation, cohort, clip, censoring)` are frozen records. `Observation` is a read-only mapping of copied `Value`s, never a live job/AGV. `Population(name: str, predicate: Callable[[Observation], bool], observed_at: str)` evaluates only in the execution/analysis child. Callable source belongs to captured project code; manifests retain names, field requirements, timing and source identity, never pickle/callable objects.

`MeasurementPlan(name, datasets, populations, parameters)` contains declarations and canonical serializable selection data. Add optional `measurement_plans` to model metadata/decorator and `measurements` to execution requests/experiment definitions. `measurements=None` is omitted from serialization and retains legacy hashes. A selected request contains `{schema: 1, plan: <name>, selection: <data>, definition_hash: <hash>}`; the child resolves it against captured model metadata. Include the complete declaration/selection in resolved provenance. Missing worker capability `analytical-artifacts-v1` rejects dispatch before execution, retaining existing core/headless behavior.

Dataset codec `analytical-dataset-v1` uses the existing msgpack dependency: ordered typed row arrays, at most 4,096 rows and 1 MiB encoded bytes per chunk (whichever is reached first); reject a single encoded row larger than 256 KiB with a field/data diagnostic. A manifest records schema, observation window/cohort/censoring semantics, row counts and ordered chunk descriptors (relative path, length, SHA-256). Empty datasets have a valid schema and zero chunks. Non-finite floats remain explicit numerical values in the stored codec; HTTP uses tagged non-finite values and decimal strings for integers outside JavaScript's safe range. The reader reconstructs exact Python values. No silent conversion to zero/null or unchecked bulk JSON response.

`AnalysisDefinition(id, entrypoint, source_hash, parameters: ParameterSchema, requirements: tuple[DatasetRequirement, ...])` uses the existing data-only parameter schema. `DatasetRequirement(dataset: str, fields: tuple[str, ...], min_replications: int = 0, require_complete: bool = False)` names exact required inputs. The `@analysis(parameters=..., requires=...)` decorator retains those declarations without importing Studio; inspection loads them only in a bounded child using P1's inspection capacity. It produces data-only metadata before input preview/admission. Definition requirements and user selection requirements are combined using the stricter completeness/minimum policy.

Schema 4 adds `analysis_definitions`, `analysis_input_snapshots`, `analysis_executions` and `analysis_outputs`. Snapshot content is immutable and content-addressed; each admission/execution has a separate UUID. A snapshot includes selected configuration/replication indices/seeds, successful/reused attempt/result IDs, scalar copies, artifact identities and schema/provenance references, plus unavailable statuses/reasons. It also records selection/completeness policy and planned/available counts. Snapshot references protect the evidence through P1's reference graph. Definition and annotation revisions use immutable version records under a stable named analysis ID.

Snapshot/output manifests use a canonical tagged numeric representation for exact int64 and non-finite values before hashing or passing through JSON; reuse the dataset codec's conversion rules instead of feeding unsupported numbers to the existing safe-number wire format. Test stable hashes and roundtrips across Python/HTTP consumers.

## Task 1: Measurement declarations and observation API

**Files:** Create `src/simulatte/analysis/{__init__,types,measurements,definition}.py`, `tests/core/test_measurements.py`; modify `model.py`, `environment.py`, `kpi.py`, existing model/public API tests.

**Interfaces:** `Environment.configure_measurements(plan: MeasurementPlan, *, output_dir: Path | None = None) -> None`; `ObservationCollector(schema: DatasetSchema, *, fields: Mapping[str, Callable[[object], Value]], populations: tuple[Population, ...] = ())` extends `Collector`; `record(entity: object, *, observed_at: str, values: Mapping[str, Value]) -> None` copies declared fields and evaluates predicates after the domain event, outside event construction. `MeasurementPlan.to_dict()` produces the canonical data-only contract; `definition_hash(plan) -> str` includes selection/timing/schema, with callable implementation covered by source identity. Standard model inspection exposes declarations, not executable objects.

- [x] Write tests for invalid types/duplicate fields, timing-specific copied attributes, overlapping populations and predicates on multiple retained fields. Example assertions in `test_membership_uses_declared_observation_time`: `arrival_row['family'] == 'A'`, `completion_row['family'] == 'B'`, and changing the entity afterward leaves both rows unchanged. A predicate receives no entity/environment reference through its arguments.
- [x] Test plain Python setup without Studio and no output directory (caller consumes observations), plus a disabled measurement plan that allocates no row buffers/subscriptions. Test the analysis decorator/metadata inspection and stricter combined requirements. Custom callback exceptions report a measurement failure without changing committed baseline results; mutation remains prohibited by the user API contract, not falsely claimed to be sandbox-enforced.
- [x] Run `uv run --all-extras pytest tests/core/test_measurements.py tests/core/test_model.py tests/core/test_kpi.py tests/core/test_invariance.py --no-cov` red, implement, then green. Keep the existing no-observer fast path.
- [x] Commit `feat(analysis): declare observational datasets and populations`.

## Task 2: Chunked datasets and simulation artifact publication

**Files:** Create `analysis/{datasets,codec}.py`, `tests/core/test_analytical_datasets.py`; modify `execution/{schema,driver,publication,protocol}.py`, `experiments/{definition,expansion}.py`, experiment JSON schema, and related request/protocol tests.

**Interfaces:** `DatasetWriter(root: Path, schema: DatasetSchema).append(row: Mapping[str, Value])`, `.finish() -> DatasetManifest`; `DatasetReader(root: Path, manifest: DatasetManifest).iter_rows() -> Iterator[Observation]`, `.page(cursor: str | None, *, limit: int = 100) -> DatasetPage`. Frozen `DatasetManifest(schema, window, chunks, row_count)` and `DatasetPage(rows, next_cursor)` follow the codec above; page limits are 1–500 with at most 1 MiB encoded response. `DatasetReader` verifies chunk descriptors before yielding data and refuses escaping/colliding paths.

`DatasetReader.to_csv(destination: TextIO) -> None` streams exact scalar text, with structured cells encoded as JSON and explicit null/non-finite tags documented in a companion schema. Test CSV export/reimport against typed rows; never truncate a download to the current UI page. Users can construct their preferred dataframe from `iter_rows()` without a required dataframe dependency.

- [x] Test empty dataset, chunk boundaries, a 256 KiB row limit violation, truncated/corrupt/reordered chunks, nested typed fields and `test_missingness_and_exact_numeric_roundtrip`. Assert `list(reader.iter_rows()) == expected_rows` for `2**60`, explicit nulls, strings and finite numbers; assert `math.isnan`/infinity sign for non-finite values. HTTP conversion is separately round-tripped without changing meaning.
- [x] Test a million small rows with a counting writer/reader: buffers never exceed one chunk and `.page(limit=100)` reads bounded chunks. Do not use a million-entity simulation as a codec unit test.
- [x] Test old requests serialize/hash identically; selection changes change the request ID. Numerical experiment recording publishes analytical artifacts without domain animation, using existing `env.artifacts_dir` and hashed artifact streams. Reject a worker lacking the new capability rather than discarding datasets. Preserve old trace readers and limits.
- [x] Run `uv run --all-extras pytest tests/core/test_analytical_datasets.py tests/execution/test_schema.py tests/execution/test_driver.py tests/execution/test_publication.py tests/core/test_experiments.py --no-cov` red/green; add remote transfer/publication regression coverage using existing harnesses.
- [x] Commit `feat(analysis): persist bounded typed datasets with run artifacts`.

## Task 3: Production/logistics measurements and numerical examples

**Files:** Create `analysis/builtins.py`, `tests/core/test_population_collectors.py`, `tests/intralogistics/test_population_metrics.py`; modify `collectors.py`, `intralogistics/metrics.py` only at their existing bus-subscriber extension points; add `examples/studio_project/` models, data, layouts and analysis definitions.

**Interfaces:** `JobOutcomeCollector(*, fields, populations, membership_at: Literal["arrival", "completion"] = "completion")` and `AgvDurationCollector(*, fields, populations, membership_at: Literal["interval_start"] = "interval_start")` attach through the ordinary collector API. Job rows carry job ID, arrival/completion/due times, lateness, inclusion/censoring and declared attributes. AGV rows carry AGV ID, state, start/end/duration and declared attributes; close/clip intervals to the observation window, recording classification changes at declared observation boundaries. Per-state aggregate-only mode must label unsupported posthoc temporal predicates.

- [ ] Hand-computed fixtures assert all-job and subset lateness counts/distributions, warm-up/censored jobs, overlapping and empty subsets. Observe family at arrival versus completion and verify the difference is intentional.
- [ ] AGV fixtures assert different mean-of-fractions and ratio-of-duration results for unequal observation durations; exact boundary changes, zero-duration windows and capabilities represented by retained lists are covered. No averaging of per-vehicle fractions under the wrong label.
- [ ] Compare baseline, numerical-only and full-recorded runs for identical semantic digest/shared KPIs and RNG trajectories. A new predicate succeeds on retained combined attributes; absent historical attributes return an explicit insufficiency result.
- [ ] Run `uv run --all-extras pytest tests/core/test_population_collectors.py tests/intralogistics/test_population_metrics.py tests/core/test_invariance.py --no-cov` red/green and the existing overhead benchmark at the package gate.
- [ ] Commit `feat(analysis): add production and fleet population measurements`.

## Task 4: Frozen input snapshots and retention transactions

**Files:** Create `analysis/{selection,store}.py`, `tests/execution/test_analysis_snapshots.py`; modify `execution/{store_migrations,store,project_store}.py`, `experiments/results.py` and retention tests.

**Interfaces:** `Selection(run_ids: tuple[str, ...], experiment_run_ids: tuple[str, ...], configurations: tuple[int, ...] | None, require_complete: bool = False, min_replications: int = 0)` is frozen. `AnalysisStore.preview(selection: Selection) -> InputPreview`; `admit(preview_id: str, *, definition_id: str, parameters: Mapping[str, Value], generation_id: str) -> AnalysisExecution`; `refresh(execution_id: str) -> InputPreview`. `InputPreview(id, snapshot_hash, available, planned, missing, partial)` stores immutable candidate metadata; it has no active job lease yet. Admission resolves exactly that candidate under the same write transaction that creates evidence references and the waiting execution. If evidence was removed, fail with a fresh preview; never substitute newer successful attempts. Frozen `AnalysisExecution(id, definition_id, snapshot_hash, generation_id, state)` uses states waiting/running/finalizing/succeeded/failed/cancelled/lost.

- [ ] Test schema 3→4 backup/identity preservation and old readers' refusal of unsupported versions. `test_snapshot_remains_eight_after_resume` asserts saved input attempt IDs and scalar values stay equal before/after 24 results exist; refresh changes snapshot hash while recomputation preserves it.
- [ ] Test `test_snapshot_admission_races_publication_and_prune` using transaction barriers, not timing sleeps. Successful admission protects exact scalar/schema/result/artifact records before queueing; concurrent deletion either occurs first and rejects admission or sees references and refuses. Pending/failed statuses never become synthetic zero observations. Paired selection counts use indices and missing-pair metadata.
- [ ] Test cancellation, multiple saved revisions, reused results across experiments and explicit deletion of one owner. A target remains protected until its final reference/active owner is removed. Unindexed output recovery must not delete valid referenced input evidence.
- [ ] Refactor `ExperimentReport` to allow `from_snapshot(snapshot)` for saved analysis; retain its current live-report constructor for progress displays. No saved analysis runs a new latest-results query.
- [ ] Run `uv run --all-extras pytest tests/execution/test_analysis_snapshots.py tests/core/test_experiment_results.py tests/execution/test_store_slots.py --no-cov` red/green; commit `feat(analysis): freeze input evidence and protect retained revisions`.

## Task 5: Analysis children, outputs and failure-safe publication

**Files:** Create `analysis/{execution,child,context,outputs,publication}.py`, `tests/execution/test_analysis_execution.py`; modify P1 `projects/service.py`, environment ownership and execution process/control helpers only where reusable.

**Interfaces:** registered entrypoints have signature `analyze(context: AnalysisContext, parameters: Mapping[str, Value]) -> None`. Context exposes `inputs`, `rows(dataset: str) -> Iterator[Observation]`, `scalars() -> Iterator[Mapping[str, Value]]`, `emit(name: str, output: AnalysisOutput) -> None`. `AnalysisOutput` variants: `Number(value, unit, metadata)`, `Table(schema, rows, metadata)`, `Distribution(summary, dataset_ref, metadata)`, `Figure(kind, payload, metadata)` and `Image(path, media_type, metadata)`; rows may be an iterator and are spooled immediately. Output names are unique portable identifiers. `AnalysisRunner.submit(execution_id: str)`, `.cancel(execution_id)`, `.wait(execution_id) -> AnalysisExecution` reuse one-use subprocess cleanup, bounded diagnostics and cancellation escalation, not simulation trace publication.

`OutputManifest(schema: Literal["analysis-output-v1"], execution_id: str, outputs: tuple[OutputDescriptor, ...])` is the durable data-only publication contract. `OutputDescriptor(id, name, kind, artifacts, metadata, renderer)` contains verified relative artifact descriptors, the dataset/figure schema where applicable, and optional renderer name/version. P3 consumes this manifest directly. `metadata` includes units/population, sample/missing counts, partial-snapshot state and inferential convention; adapters cannot silently discard those fields. Plain annotations are separately editable without changing numeric artifacts.

- [ ] Test the same captured source, environment, inputs and parameters through Python runner and CLI produce equal numerical artifacts. Source/input/environment hashes belong to the analysis execution separately from simulation provenance. Output cannot write outside its attempt staging directory or replace input artifacts.
- [ ] `test_failed_analysis_never_publishes_success` covers exception, child exit, late output after cancel, descendant cleanup failure, disk-full and crash before/after index visibility. Success requires verified artifacts plus clean process exit; failed retries create new execution IDs and never adopt unindexed output as success.
- [ ] Add publication stages parallel to the existing simulation publisher: verify descriptors, durable rename, then single SQLite visibility transaction; recovery removes only unindexed output and releases terminal active leases. Saved input snapshots remain until explicit deletion.
- [ ] Run `uv run --all-extras pytest tests/execution/test_analysis_execution.py tests/execution/test_process_cleanup.py tests/execution/test_publication.py --no-cov` red/green; commit `feat(analysis): execute captured analyses in isolated children`.

## Task 6: Numerical reports and optional figure adapters

**Files:** Create `analysis/{reports,adapters}.py`, `tests/core/test_analysis_outputs.py`; modify existing Matplotlib helper methods in `collectors.py` and `intralogistics/metrics.py`, `pyproject.toml`, and optional-dependency tests.

**Interfaces:** `summarize_population(rows, *, value: str, group_by: tuple[str, ...] = ()) -> Table`; `compare_replications(snapshot, *, metric: str, baseline: str | None = None, level: float = 0.95, bonferroni: bool = False) -> Table` delegates to `statistics.py`. `save_figure(figure: object, output_dir: Path, *, name: str) -> Figure` dispatches lazily to Matplotlib or Plotly. Existing `plot_*` helpers gain keyword-only `show: bool = True` and return their figure; no-argument behavior is retained. Plotly outputs store data-only figure JSON and required renderer identity, not executable HTML. Add optional `analysis-plotly` and `analysis-seaborn` extras; keep existing `plot` behavior.

- [ ] Tests pin sample size, units, population, empty/missingness and pooled/per-replication labels. A one-replication result is insufficient for an interval; failed pairs and multiplicity metadata remain visible. Exact/binned/approximate distribution summaries state which was retained.
- [ ] Test Matplotlib/Seaborn capture with a non-GUI backend and `show=False`, legacy helper behavior, Plotly JSON roundtrip and PNG/SVG metadata. `test_saved_outputs_without_plot_dependencies` removes optional imports and still reads already generated numerical/images/specification artifacts. Unsupported live figure objects fail with the adapter needed, not a generic serialization traceback.
- [ ] Run `uv run --all-extras pytest tests/core/test_analysis_outputs.py tests/core/test_optional_dependencies.py tests/core/test_statistics.py --no-cov` red/green; commit `feat(analysis): expose numerical reports and optional figure adapters`.

## Task 7: Headless API/CLI, docs and P2 gate

**Files:** Create `analysis/command.py`, `tests/execution/test_analysis_cli.py`, `docs/studio/{measurements,analysis}.md`; modify `cli.py`, `projects/config.py` analysis registration, `zensical.toml`, numerical examples; create `specs/research/2026-10-19-studio-p2-acceptance.md` from actual evidence.

- [ ] Implement `simulatte analysis run <analysis-key> --project <folder> --experiment <id>` or repeatable `--run <attempt-id>`, optional `--require-complete`, plus `analysis rerun <execution-id>` (same snapshot), `analysis refresh <execution-id>` (preview/admit new snapshot), `analysis show <execution-id>` and `analysis cancel <execution-id>`. Every subcommand accepts `--project <folder>` defaulting to cwd and `--json` for a single structured result. No implicit latest-run selection. Python exposes the same Selection/runner contracts.
- [ ] Add CLI tests asserting same-snapshot rerun, explicit refresh, insufficient-input admission failure, cancellation exit status and missing dependency messages; run `uv run --all-extras pytest tests/execution/test_analysis_cli.py --no-cov` red/green.
- [ ] Write one job-lateness and one fleet-utilization walkthrough, including custom predicates, missing retained fields, partial snapshots, repeated analyses and optional figures. Document observation timing and why within-run rows are not replications.
- [ ] Run the shared package gate on bigboss, an installed headless example, core-only import/run and current remote-worker regression checks. Record actual peak dataset buffers and observer overhead. Commit `docs(analysis): document reproducible population analysis`.
