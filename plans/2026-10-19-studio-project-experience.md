# Studio Project Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement the selected package task-by-task, using Davide's chosen execution method. Steps use checkbox (`- [ ]`) syntax for tracking. Read this index, the product spec and the selected package together. Planning and checkout preparation do not authorize starting implementation before plan review.

**Goal:** Deliver a persistent, multi-model Studio workspace for running studies, inspecting numerical evidence and presenting selected recordings, while keeping ordinary Python use first-class.

**Architecture:** Extend the released execution/store foundation behind a project service with independent model contexts. Add numerical datasets and immutable analysis inputs before building the research pages; saved presentations consume those outputs through a shared presentation manifest.

**Tech Stack:** Existing Python 3.11+ package, SQLite, msgpack, uv; existing TypeScript/React/Vite workspace and playback/scene/export packages. Optional Matplotlib/Seaborn and Plotly adapters. No mandatory dataframe library.

**Spec:** [Studio project experience, revision 2](../specs/2026-10-19-studio-project-experience-design.md). Reviewed source baseline: `d3e17e6`; written spec revision: `801bf37`. The focused follow-up review closed all six original findings. This plan adds technical choices for review, not claims of implemented behavior.

## Global constraints

- “No simulation starts merely because Studio opened.”
- “Model inspection, analysis and simulation code run in execution children, never in the web-serving process.”
- “Initially retain one project-wide interactive execution slot plus experiment slots; selection and execution ownership are separate.”
- “No automatic removal of results is introduced.”
- “Experiment inference remains replication-level.”
- “No automatic switch to the latest run or newly completed replications is permitted.”
- “The main analysis path must work with tables alone.”
- “Check the actual UI at laptop and workstation sizes, including 1280×800 and 1440×900.”
- “Preserve current codec fallbacks, bounded export, cancellation and offline requirements.”
- Develop and run substantial verification on bigboss. Preserve native-engine work and unrelated changes. Keep credentials and machine-specific paths out of authored project definitions.
- Preserve old request/result identities and readers. Optional extensions are omitted from legacy request serialization. Negotiate worker capabilities before dispatching new formats; do not silently downgrade requested measurements.
- Existing supported Python/platform/CI lanes, 99% branch coverage, no-subscriber overhead, authentication, fencing, observer invariance and physical/presentation layout distinctions remain gates. Release numbers and merging belong to the maintainer.

## Review focus

| Input or failure | Owning package/test |
| --- | --- |
| A project is copied, its model renamed/removed, or its entrypoint replaced; history must retain the original association and provenance. | P1 T2 `test_model_identity_survives_rename_removal_and_copy` |
| A long A run is active while B is inspected and sources change; A must retain its publication rights. | P1 T4 `test_inspect_b_preserves_active_a` |
| An experiment publishes while analysis admission races cleanup; saved inputs must remain the exact previewed set. | P2 T4 `test_snapshot_admission_races_publication_and_prune` |
| Optional plotting packages disappear or an artifact contains active content; saved safe outputs must remain viewable. | P2 T6 and P3 T3 `test_saved_outputs_without_plot_dependencies`, `artifactIsolation` |
| Exports use a different aspect ratio or selected evidence exceeds limits; preview must reveal the result and export must not omit content. | P4 T3 `targetPreviewAndEvidenceLimits` |

## Packages and sequence

| Package | Plan | First complete outcome | Gate before proceeding |
| --- | --- | --- | --- |
| P1 | [Projects, environments and first run](2026-10-19-studio-p1-projects.md) | Open/create → prepare → choose model → Run → inspect → reopen history. | Real installed-package workflow, two-model ownership and recovery viewing demonstrated. |
| P2 | [Numerical data and analysis](2026-10-19-studio-p2-analysis.md) | Headless custom analysis of immutable experiment inputs, with built-in population examples and saved artifacts. | Invariance, statistical semantics, snapshot/retention races and headless reproduction pass. |
| P3 | [Research workspace](2026-10-19-studio-p3-workspace.md) | Daily research through focused pages, useful numerical tables and readable failures. | Actual browser evidence, keyboard workflow and disconnected/error states pass. |
| P4 | [Presentation and completion](2026-10-19-studio-p4-presentation.md) | Studio, video and offline HTML preserve selected recording settings and evidence. | Capability matrix, portability/cleanup and full documented workflow pass. |

Execute P1 first. P3's shell refinement can start after P1, but its analysis page depends on P2. Keep P2 and P3 ownership sequential where both touch the server command surface. P4 depends on both. Each task includes regression checks, documentation and a conventional commit; independent package gates are review points, not automatic releases. Do not add #73–#76 fixes unless a task requires them.

Store migrations are incremental: schema 3 for project/model state and reference infrastructure (P1), 4 for analytical snapshots/executions (P2), 5 for saved presentations/exports (P4). Each migration must open prior supported fixtures with backup and identity preservation; later package schema changes do not retroactively claim earlier gates tested them.

## Verification common to the packages

Targeted tests below use `--no-cov` to avoid treating a subset as a whole-package coverage run. At each package gate on bigboss:

```bash
uv run --all-extras ruff check .
uv run --all-extras ty check src
pnpm --dir studio typecheck
pnpm --dir studio test
pnpm --dir studio build
uv run python studio/scripts/package_assets.py
uv run --all-extras pytest
pnpm --dir studio test:browser
uv build
uv run --all-extras zensical build --clean
```

Build/package assets before tests that require the installed wheel; do not alter packaged runtime files during Python execution tests. Use `studio/scripts/studio-smoke.mjs` against a fresh wheel as in CI. CI supplies Linux/Windows/PyPy coverage; macOS WebKit is not evidence of actual Safari or native Linux Firefox acceptance. Record those remaining checks explicitly. Record visual evidence and measured overhead at package gates; do not regenerate a broad suite repeatedly without a relevant change/failure.

## Spec coverage

| Product spec sections / acceptance | Owner |
| --- | --- |
| §4 opening/configuration/environment; scenarios 1, 3 | P1 T1, T3, T5–T7 |
| §5 identity/migration/history; scenarios 10–11 | P1 T2, P2 T4–T5, P4 T1/T4 |
| §6 execution, sidebar and ownership; scenarios 2, 4 | P1 T4/T6, P3 T1–T2 |
| §7 measurement/population data; scenarios 5–7 | P2 T1–T3/T6 |
| §8 analysis/provenance/adapters; scenarios 8, 11 | P2 T4–T7, P3 T3 |
| §9 chosen recordings and delivery; scenario 9 | P4 T1–T3 |
| §10 visual quality/docs; scenario 12 | P1 T6–T7, P3 T1/T4, P4 T4 |
| §11–12 gates, compatibility and handoff | This index, all package gates, handoff below |

## Bigboss execution handoff

- [x] Review these plans and select the execution method before implementation. Recommend native execution per package with an independent package review: tasks share store/runtime interfaces, so a continuous implementation context is useful; preserve separate review at risky boundaries.
- [x] Use a dedicated worktree on bigboss from the exact committed planning branch. Check `git status`, `git rev-parse HEAD` and the spec revision before starting. Preserve `/Users/davide/Developer/simulatte` and the existing native-engine worktree.
- [x] Initialize the existing Homebrew/fnm tools in non-interactive SSH; use installed Node 24 and the repository-pinned pnpm. Sync the project's Python environment and TypeScript lockfile in this worktree only.
- [x] Establish the baseline on bigboss, record commands/results and distinguish failures already present on `d3e17e6` from new work. Start P1 T1 only after plan review and baseline triage.
- [ ] At every package handoff record branch/commit, tests, browser evidence, accepted deviations and next task in Cortex. Keep plan checkboxes synchronized with actual evidence. No deployment, release or remote Herdr agent is started by this document.

P1 prerequisite evidence: Davide's approval `session-05fa3802-37e9-4cc3-a775-17bf24805322`, accepted persistent execution method `session-f4330bcd-95ba-48de-a778-c540b63823cd`, corrected w2E locator `session-f063c8f8-a419-4799-b14b-7f304669253f`, and prepared checkout/baseline `session-2c448970-eade-4135-8102-6690dd49d234`. The isolated checkout started clean at `a80ece3020562b4a700faa4fb7e6d1c0fefaadad`; Node 24.13.0, uv 0.13.1 and Python 3.14.3 were prepared there. Baseline: 437 TypeScript tests/typecheck/build and 118 targeted Python tests passed, not full coverage or platform acceptance. Saved baseline log: `/Users/davide/.local/state/simulatte/handoffs/2026-10-19-studio-a80ece3-baseline.log`, SHA-256 `f9dcda96815e121bd51c01889ffacccb2934d171d1d7d3a6bfd94e56a50ede97`.

P1's current evidence and conditional independent code approval are recorded in [its acceptance report](../specs/research/2026-10-19-studio-p1-acceptance.md). The recurring handoff obligation above remains open for future packages; no P2–P4 gate is completed by this update.
