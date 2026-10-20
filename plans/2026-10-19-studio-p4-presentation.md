# Studio P4: Presentation and Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans with Davide's selected method. Implement task-by-task; checkboxes track verified work. P1–P3 are required.

**Goal:** Turn a selected study configuration into a saved Studio presentation and faithful video/standalone HTML deliverables, then verify portability and the complete documented workflow.

**Architecture:** Clone captured execution specifications for presentation recordings and retain the established verified-replay path. A versioned presentation manifest references immutable recordings/evidence and drives all three render targets. Export admission pins the manifest and all evidence until terminal cleanup.

**Tech Stack:** Existing Python execution/store, TypeScript playback/scene/export/player, WebCodecs/media fallbacks, local static artifact rendering. No slide authoring engine or live Python backend in HTML.

**Spec:** [§9–12 and scenarios 9–12](../specs/2026-10-19-studio-project-experience-design.md); [shared constraints](2026-10-19-studio-project-experience.md).

## Global constraints

- “Change only the chosen seed and recording level to full unless the user explicitly edits other settings.”
- “A mismatch remains visible and does not substitute for the original experiment result.”
- “Each export freezes the presentation revision and evidence references at admission; later edits cannot change an in-progress export.”
- “The first delivery uses a fixed evidence panel, not a slide/timeline authoring system.”
- “Never silently omit or truncate selected evidence.”
- “Package the standalone player's assets and selected evidence locally without project imports, network access or weakening the existing content-security/isolation contract.”
- Preserve existing frame/end-state rules and export ceilings: HTML trace source 100 MiB/output 150 MiB; media 100,000 frames, PNG fallback 10,000 frames, supported 24/30/60 fps and dimensions up to 3840×2160. Evidence/assets count toward total output limits, not an unbounded side channel.

## Review focus

| Risk | Test/task |
| --- | --- |
| New-seed recording silently substitutes current model stopping/measurement defaults. | T1 `test_presentation_request_clones_complete_capture` |
| Changed runtime or measurement selection receives an inappropriate verified label. | T1 `test_explicit_edits_create_distinct_presentation_run` |
| Editing/removing evidence during export changes the deliverable. | T2/T3 `exportPinsRevisionAndEvidence` |
| Aspect-ratio changes or oversized tables remove selected evidence without consent. | T3 `targetPreviewAndEvidenceLimits` |
| Copied history opens but references point back to eva or cleanup removes durable data. | T4 `test_quiescent_copy_has_no_source_machine_dependency` |

## Contracts selected for implementation

Create Python `presentation-v1` JSON schema and matching TypeScript types under `studio/packages/playback/src/presentation-manifest.ts`. Preserve existing layout-only `freezePresentation` behavior for old exports. `PresentationManifest` carries `schema: 1`, stable presentation ID/revision, recording attempt ID and trace SHA-256, saved `LayoutBundle`, camera world bounds `[xmin, ymin, xmax, ymax]`, visible entity/label IDs, selected measurement keys, finite `[start, end]`, playback rate, annotations and `evidence: EvidenceSelection[]`. Apply the saved world bounds using containment/letterboxing within the target scene area so a changed aspect ratio does not crop the saved framing. `EvidenceSelection` carries analysis execution/output IDs, content hash, bounded selected table rows/columns or figure view, and immutable labels (units, population, counts, interval convention, partial status, source identity). Static image bytes have their own verified descriptors. Temporary viewing changes do not alter the manifest until saved.

Schema 5 adds `presentations`, immutable `presentation_revisions` and `export_jobs`. References include every recording, saved analysis/input snapshot and selected artifact needed to interpret evidence. An `ExportSnapshot` is the frozen manifest plus verified static views and target options (`studio` preview, `video`, `html`). Its content hash identifies exactly what preview/export used. Metadata annotations are plain text, not arbitrary HTML. A fixed right-side evidence panel appears in all targets when evidence is selected; target preview shows its actual dimensions and complete chosen excerpts. The user can select fewer rows or change the framing, not silently accept truncated content.

## Task 1: Captured configuration to recorded presentation run

**Files:** Create `src/simulatte/presentations/{__init__,recording}.py`, `tests/execution/test_presentation_recording.py`; modify `execution/coordinator.py`, `studio/experiments.py`, relevant replay tests and P3 experiment/analysis page actions.

**Interfaces:** `make_presentation_request(original: ExecutionRequest, *, seed: int, overrides: Mapping[str, Any] | None = None) -> ExecutionRequest` copies the request and changes only seed/level by default; allowed explicit overrides are validated using the captured schemas and produce a shown diff. `record_presentation(original_attempt: str, *, seed: int, overrides=None) -> RecordingJob` resolves captured artifacts/runtime using P1 reconstruction and ordinary safe admission. `RecordingJob(key: AttemptKey, original_attempt: str, mode: Literal["replay", "new_seed", "edited"], verification: Verification | None)` distinguishes new-seed/edited runs from verified replay. Same seed is necessary but not sufficient: only unchanged captured execution semantics use the established verification path.

- [x] `test_presentation_request_clones_complete_capture` uses horizon 10,000/warm-up 1,000 and nondefault measurement selection while current model defaults are 1,000/zero. Assert `target.stopping == original.stopping`, `target.measurements == original.measurements`, `target.source_hash == original.source_hash`, `target.runtime == original.runtime`, and only seed/level differ in serialized requests.
- [x] Test finite-population stopping, explicit edited sources/measurements/runtime, insufficient historical environment and invalid seed. `test_explicit_edits_create_distinct_presentation_run` asserts edited requests cannot inherit original verification merely because their seed matches.
- [x] Run `uv run --all-extras pytest tests/execution/test_presentation_recording.py tests/execution/test_replay.py tests/core/test_studio_experiments.py --no-cov` red/green. Include busy-slot ownership and remote-request reconstruction in regression coverage; preserve worker compatibility rules.
- [x] Commit `feat(studio): create presentation recordings from captured studies`.

## Task 2: Saved presentation revisions and direct Studio delivery

**Files:** Create `presentations/{schema,store}.py`, `presentations/schema/presentation-v1.schema.json`, `tests/execution/test_presentation_store.py`, playback `src/presentation-manifest.ts`, viewer `src/workspace/presentations.tsx`, `src/presentation/{editor,view,evidence-panel}.tsx`; modify playback/scene exports, P3 routes/client/server and packaging schema lists.

**Interfaces:** `PresentationStore.create(manifest: Mapping[str, Any]) -> str`, `.revise(id: str, expected_revision: int, manifest) -> int`, `.get(id: str, revision: int | None = None) -> Mapping`, `.admit_export(id: str, revision: int, target: str, options: Mapping) -> ExportJob`. `ExportJob(id, snapshot_hash, state, references)` uses waiting/running/succeeded/failed/cancelled/lost, with active reference leases until terminal cleanup. TS `validatePresentation(value: unknown): PresentationManifest`, `applyPresentation(scene, session, manifest): void`, `capturePresentation(scene, session, selection): PresentationManifest` use one shared type for view and export. Reuse Scene's existing typed camera getter/setter and convert viewport pixels to/from saved world bounds rather than scraping DOM transforms.

- [x] Test schema 4→5 migration/backup, missing/corrupt recording/evidence references, stale revision edits and reference-aware deletion. Saving a new view leaves simulation/analysis results byte-identical. Pending/active exports preserve references even if the named presentation is later revised/deleted.
- [x] Implement presentation selection, saved framing/visibility/measurement/time settings, annotations, bounded evidence picker and a clean presentation mode with reliable keyboard exit. Show single-run values separately from cross-replication evidence. The evidence picker preserves partial/missingness/count/interval labels from P2, not user-editable replacements for numerical provenance.
- [x] Test roundtrip `capture → save → reopen → apply`, including reset after temporary navigation. Add `studio/browser/presentation.spec.ts` for Studio matrix rows and evidence selection. Schema fixtures shared between Python and TypeScript must accept/reject the same documents.
- [x] Run `uv run --all-extras pytest tests/execution/test_presentation_store.py --no-cov`, playback/viewer tests, typecheck, build/package and the presentation browser spec; commit `feat(studio): save presentation views and supporting evidence`.

## Task 3: Target preview, video and standalone HTML parity

**Files:** Create export `src/presentation.ts`, `test/presentation-manifest.test.ts`, viewer `src/presentation/export-preview.tsx`; modify `packages/export/src/{index,media,sink}.ts`, viewer `export-dialog.tsx`, player `src/{index.ts,player.css}`, browser `export.spec.ts` and presentation tests. Extend server export-job commands and artifact ingestion with existing authenticated/hashed upload rules.

**Interfaces:** `prepareExport(manifest: PresentationManifest, target: 'video' | 'html', options: TargetOptions, source: EvidenceSource, signal: AbortSignal): Promise<ExportSnapshot>` validates all selected content and freezes verified static representations. `TargetOptions` extends current `MediaOptions` or HTML limits, retaining existing codec choices. `renderPresentationFrame(snapshot, simulationTime, destination): Promise<void>` is shared by preview/video composition; HTML consumes the same manifest/settings and embedded evidence. `EvidenceSource` provides validated bytes through P3's artifact reader. `finish_export(job_id, outcome)` releases leases only when the exporter is terminated; abandoned jobs are marked lost on recovery, never assumed successful.

- [x] Extend exact frame/end-state and cancellation tests with manifest camera/visibility/measurement settings. `exportPinsRevisionAndEvidence` starts export then edits the presentation and finishes another analysis; final snapshot hash and selected evidence remain the original ones. Disconnect/cancel leaves referenced data protected until termination is confirmed.
- [x] `targetPreviewAndEvidenceLimits` covers differing aspect ratios, long annotations, large table excerpts, missing static images and unsupported figures. Assert refusal names the offending item; no selected annotation/row/qualifier disappears. Preview uses actual target dimensions and same render path. Preserve the 100 MiB/150 MiB HTML caps including evidence, 100,000-frame media cap and 10,000 PNG frames.
- [x] Generate PNG/SVG views of Matplotlib/generic images directly from saved artifacts. Plotly static capture uses the sandboxed local browser renderer and its supported image-export API; no mandatory Kaleido/Python browser install. Reject capture if referenced data/assets cannot render offline. Include plain-text numerical provenance on video frames and local immutable references in HTML.
- [x] HTML packages the existing player with selected evidence, permits initial navigation/reset and time controls, and performs no fetch to the source project/network. Preserve CSP and script-safe JSON embedding. `artifactIsolation` cases also run against downloaded HTML opened offline; full analysis editing/recomputation remains Studio-only.
- [x] Run export/player/viewer unit tests, typecheck, build/package, `pnpm --dir studio exec playwright test browser/export.spec.ts browser/presentation.spec.ts browser/analysis.spec.ts` and installed-wheel smoke. Verify all §9 matrix rows across all three targets with real sample output and readable screenshots/video frames.
- [x] Commit `feat(export): preserve presentation settings and evidence across formats`.

## Task 4: Portability, retention, docs and completion gate

**Files:** Create `tests/execution/test_project_portability.py`, `docs/studio/{presentations,storage,cli}.md`, `specs/research/2026-10-19-studio-project-experience-acceptance.md`; modify project reader/retention CLI only for the required copy/cleanup workflows, `docs/studio/index.md`, `zensical.toml`, README and release documentation when authorized.

- [x] `test_quiescent_copy_has_no_source_machine_dependency`: stop the writer, copy a project with runs, partial/full analyses and presentation revisions, remove original access, open on another path/machine, and verify all result/artifact/reference IDs. A rebuilt environment does not rewrite historical provenance. Missing partial-transfer artifacts are diagnosed, never rebound by filename.
- [x] Exercise cache cleanup, explicit history deletion and dry-run dependency explanations. Attempt to delete evidence shared by several analyses/presentations; references survive until the final owner is removed. Environment generation cleanup respects active owners and preserves durable reconstruction evidence. Document offline viewing versus cross-architecture execution verification.
- [x] Complete all twelve product acceptance scenarios through the installed wheel. Include model errors, publication interruption, dataset limits, environment recovery, original-seed verification and new-seed presentation. Run the shared full gate, platform CI, packaging, overhead and browser acceptance; record actual Safari/Linux Firefox checks separately where required.
- [x] Build the docs, check links and rehearse first project → experiment → population analysis → presentation with the same example. Preserve bare-Python tutorials and old deep links. Final report lists exact commit, commands, UI/export artifacts and unresolved limits. Verify documentation publication only after an authorized release; do not create a tag or claim 1.0 readiness here.
- [x] Commit `docs(studio): verify complete project research and presentation workflows` and request maintainer integration/release decisions with the evidence.

## Primary implementation references

- Local figure snapshots can use [Plotly's browser image export](https://plotly.com/javascript/static-image-export/); renderer capability and offline behavior must still be tested with the pinned version.
- Generation provisioning uses a separate [uv project environment path](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path); never sync into an active generation.

## Execution record

Checked against the evidence in [the acceptance report](../specs/research/2026-10-19-studio-project-experience-acceptance.md) at product/test head `098bfbd` (gate candidate `bd9bd6f`). Deviations: T1 and the T2 Python half landed together in `221cb80` (`feat(studio): record presentations from captured studies and save presentation revisions`), the T2 viewer half in `a9c4a65`, T3 in `4f4c29a`; recordings are verified through `test_presentation_recording.py`, the store through `test_presentation_store.py`. Of Task 4's gate, platform CI lanes, real Safari and Linux Firefox were not observed locally and are listed as unobserved obligations; documentation publication waits for an authorized release.
