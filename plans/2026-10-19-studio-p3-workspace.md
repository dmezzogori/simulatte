# Studio P3: Research Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans with Davide's selected method. Implement task-by-task; checkboxes track verified work. P1 is required; T3 also requires P2.

**Goal:** Make the first-run workspace useful for daily studies: navigate history, compare experiments, run custom analyses and inspect numerical evidence with clear feedback.

**Architecture:** Refine P1's focused sidebar shell and expose independent model/run/experiment/analysis routes. Keep recording playback in its own component and render analysis artifacts from immutable manifests through bounded authenticated readers. No computation is moved from Python into ad-hoc browser statistics.

**Tech Stack:** Existing React/TypeScript/Vite, uPlot and playback packages; local pinned Plotly renderer loaded only for saved interactive figures. Existing authenticated WebSocket/HTTP transport.

**Spec:** [§6, §8, §10 and scenarios 2, 4–8, 12](../specs/2026-10-19-studio-project-experience-design.md); [shared constraints](2026-10-19-studio-project-experience.md).

## Global constraints

- “The proposed extra work-tab bar duplicated navigation and was rejected.”
- “The main analysis path must work with tables alone.”
- “A completed run does not unexpectedly replace the page being read.”
- “Do not inject arbitrary figure HTML or scripts into the privileged Studio document.”
- “Keyboard navigation, visible focus, readable contrast, labels, reduced-motion behavior and desktop resizing are acceptance requirements.”
- Preserve stored results when current model code or optional plot packages break. UI selection never grants execution ownership or refreshes saved analytical inputs.

## Review focus

| Risk | Test/task |
| --- | --- |
| A completion notification navigates away from a report or discards a model draft. | T1 `completionPreservesRouteAndDraft` |
| Large histories or datasets cause unbounded browser work. | T2/T3 `boundedHistoryAndTablePages` |
| A failed replication disappears from a comparison and inflates confidence. | T2 `comparisonRetainsMissingPairs` |
| Crafted SVG/Plotly fields escape the artifact viewer or request external URLs. | T3 `artifactIsolation` |
| Reload/reconnect/keyboard navigation strands users in loading or inaccessible states. | T4 `keyboardAndRecoveryJourney` |

## Task 1: Shared interaction patterns and focused routes

**Files:** Modify P1 `studio/apps/viewer/src/workspace/{shell,overview,models,run-page,state}` and `style.css`; create `src/workspace/{feedback,forms,tables}.tsx`, `src/workspace/tokens.css`, `test/workspace-navigation.test.tsx`, `studio/browser/workspace.spec.ts`.

**Interfaces:** Preserve P1's `WorkspaceRoute`. `PageState<T>` is `{status: 'loading'}` / `{status: 'ready', value: T}` / `{status: 'empty', action?: Action}` / `{status: 'error', message: string, retry?: Action}` / `{status: 'stale', value: T, reason: string}`; `Action(label: string, invoke: () => void, disabledReason?: string)` is a presentation-only type. `Feedback` exposes status and next action without raw hashes; diagnostic details remain available. Shared table/form primitives keep labels, units, keyboard focus and compact numerical alignment consistent.

- [x] Test `completionPreservesRouteAndDraft`: while reading an analysis or editing model B, A completes; route/draft remain unchanged and a linkable completion notice appears. Browser back/forward and reopening direct result routes retain selected identities.
- [x] Implement typography, spacing, density, colors and responsive sidebar against the accepted mockup's structure. Keep the external code editor workflow; no web code editor or duplicate global tabs. Readiness failures distinguish model, dependency, missing-data and connection problems.
- [x] Run `pnpm --dir studio --filter @simulatte/viewer test` and `pnpm --dir studio typecheck` red/green; run `pnpm --dir studio exec playwright test browser/workspace.spec.ts` after build/package. Capture the overview → model → run flow at 1280×800 and 1440×900 before applying patterns to other pages.
- [x] Commit `feat(studio): refine focused navigation and shared interaction patterns`.

## Task 2: Runs, experiments and comparisons as research pages

**Files:** Create `src/workspace/{runs,experiments,comparison}.tsx`, `test/workspace-research.test.tsx`; modify existing `run-history.tsx`, `experiments.tsx`, `results-table.tsx`, `compare-panel.tsx`, `experiment-feed.ts`, server `studio/experiments.py` and P1 project history queries. Extend `studio/browser/workspace.spec.ts`.

**Interfaces:** `RunsPage({client, modelId?: string})`, `ExperimentsPage({client, experimentRunId?: string})`, `ComparisonPage({client, selection: string[]})`. Add data-only query `history.list {model_id?, cursor?, limit}` (limit 1–100), `history.annotate {attempt_id, name, text, important}` and `experiment.results` pages preserving existing estimate/pair/missingness fields. Existing compare playback remains a selected-recording feature, not the only comparison entrypoint.

- [x] Test history by model, legacy unassigned history, annotations/important flags, stable pagination while new runs arrive, and no accidental selection of latest results. Results link to configuration/seed/measurement/runtime provenance in human-readable details.
- [x] Test `comparisonRetainsMissingPairs` with one failed replication and mixed runtimes: visible counts, missing pairs, pointwise/Bonferroni convention and no interval for insufficient data. Distributions are explicitly within-run or pooled; no new browser inference code.
- [x] Expose experiment define/start/pause-or-cancel/resume and worker choices using current semantics. Source changes mark definitions stale without rewriting captured runs. Retain full versus numerical recording controls and physical-layout editing on the model page.
- [x] Run `uv run --all-extras pytest tests/core/test_studio_experiments.py tests/core/test_experiment_results.py --no-cov`, viewer unit/type checks, and the project/workspace browser specs. Test `boundedHistoryAndTablePages` on synthetic 10,000-entry history without loading it all; assert fetched/rendered pages remain capped, not a machine-specific timing guess.
- [x] Commit `feat(studio): organize run histories and experiment comparisons`.

## Task 3: Analysis workflow and isolated artifact viewers

**Files:** Create `src/workspace/analyses.tsx`, `src/artifacts/{view,table,distribution,figure-frame}.tsx`, `src/artifacts/types.ts`, `test/artifacts.test.tsx`, `studio/browser/analysis.spec.ts`, `src/simulatte/studio/{analysis_commands,analysis_http}.py`, `src/simulatte/analysis/table_query.py`, `tests/core/test_studio_analysis.py`; modify `studio-client.ts`, server dispatch, viewer package manifest/lockfile and asset packaging/notices.

**Interfaces:** commands `analysis.preview`, `analysis.admit`, `analysis.cancel`, `analysis.rerun`, `analysis.refresh`, `analysis.list`, `analysis.get` delegate to P2 store/runner. Preview responses include ID/hash/counts/missing/partial state. `ArtifactView({manifest: OutputManifest, source: ArtifactSource})` consumes P2 output descriptors; `ArtifactSource.page(outputId, cursor, limit)` returns typed rows/next cursor through capped authenticated HTTP, and `.blob(outputId)` uses verified immutable files. Table sorting/filtering operates server-side on retained artifacts with bounded reads/work and cancellable child execution when a full scan is needed; do not imply an in-browser page sort covers the whole dataset.

`TableQuery(sort: tuple[tuple[str, Literal["asc", "desc"]], ...], filters: tuple[Filter, ...])` accepts only schema field names and typed `Filter(field, op, value)` with `eq/ne/lt/le/gt/ge/in` operators. `query_table(reader: DatasetReader, query: TableQuery, cursor: str | None, *, limit: int = 100) -> DatasetPage` uses a disposable SQLite index keyed by artifact hash/query, built incrementally in a cancellable child; cap its page/cache settings and spill sorting to disk. Null/non-finite ordering and exact numeric comparison are explicit and tested, including int64 values outside JavaScript range. Custom Python predicates remain P2 analyses, not expressions evaluated in the web server. Derived query caches never replace original artifacts.

- [x] Test selecting an eight-of-24 snapshot, admission, refresh after completion and reopening both revisions. Display partial/missingness labels and original versus derived provenance. Recompute uses the same inputs; refreshed inputs require a distinct preview. Missing required fields offer a revised experiment, never synthetic values.
- [x] Implement numeric-first output cards, typed paged tables, distribution summaries and data/figure downloads. Persist analysis names/annotations; demonstrate the path with no plots at all. Failed/running/cancelled outputs remain inspectable through diagnostics without showing partial artifacts as successful.
- [x] Render PNG/SVG only as images with restrictive response headers, never inline SVG in the privileged DOM; direct artifact navigation is an attachment or isolated response. Plotly runs in a sandboxed frame without same-origin privilege, using locally packaged pinned renderer assets, no arbitrary user HTML/scripts and no network. Transfer validated figure data through a versioned, nonce-bound message channel checked against the frame window. Resource URLs and user callbacks in figure specs are rejected. Browser-generated static views support P4 without requiring a Python browser dependency.
- [x] `artifactIsolation` tests malicious SVG scripts/links and Plotly resource URLs against a network trap: no parent DOM/session storage access, no outgoing requests, no script execution beyond the trusted renderer. Offline saved artifacts remain readable with Python plot packages missing. `boundedHistoryAndTablePages` asserts exact int64 display, explicit non-finite states and bounded HTTP/rendering.
- [x] Run `uv run --all-extras pytest tests/core/test_studio_analysis.py --no-cov`, viewer unit/type checks, build/package and `pnpm --dir studio exec playwright test browser/analysis.spec.ts` red/green. Update CSP, wheel-asset manifest and third-party notices tests with the renderer bundle.
- [x] Commit `feat(studio): inspect saved numerical analyses and isolated figures`.

## Task 4: Usability, documentation and P3 gate

**Files:** Update `docs/studio/{index,projects,analysis,measurements}.md`, create `docs/studio/experiments.md`, update `zensical.toml`; extend project/workspace/analysis browser specs and `studio/scripts/studio-smoke.mjs`. Create `specs/research/2026-10-19-studio-p3-acceptance.md` with actual evidence.

- [x] Exercise `keyboardAndRecoveryJourney`: all primary actions by keyboard, visible focus and dialog return focus, readable status/labels, no hover-only controls, reduced-motion setting, resizing, disconnected/reconnecting server, expired authentication, empty project, failed model, insufficient observations and missing optional renderer. No generic blank panel or unexplained disabled action.
- [x] Complete the same documented numerical workflow through Python, headless commands and Studio using P2's consistent example. Check the actual UI with Davide; record specific visual findings and resolve them before claiming polish acceptance.
- [x] Run the shared package gate and installed-wheel workflow. Record screenshots at both required sizes and bounded-history/dataset behavior. Preserve existing playback responsiveness and overhead checks; do not assert accessibility from screenshots alone.
- [x] Commit `docs(studio): document and verify the research workspace`.

P3 evidence, independent review (READY for P4 at `38d094f`) and follow-ups are recorded in [the acceptance report](../specs/research/2026-10-19-studio-p3-acceptance.md); Davide's review of the actual UI and of the whole branch is pending.
