# Studio project experience: P4 and complete acceptance evidence

Date: 2026-10-20. Host: bigboss, macOS, CPython 3.14.3, uv 0.13.1, Node
24.13.0, pnpm 10.27.0, Playwright 1.64.0 (browsers under
`.superpowers/sdd/2026-10-19-studio-p1-projects/browsers`). Branch
`feature/studio-projects`, isolated worktree `.worktrees/studio-project-experience`.
P4 base `3e321cd` (P3 docs commit; P3 product head `38d094f`). P4 gate
candidate **`bd9bd6f`**; final product/test head **`098bfbd`**, which changes only
the scenario 8 browser check after the gate (rerun on all three engines,
below). Twenty-five signed commits `3e321cd..098bfbd`. P4 Tasks 1–4 are
implemented, and the twelve acceptance scenarios of spec §11 have installed-wheel
evidence. This report records local package evidence, the independent reviews
and the follow-ups. Acceptance, integration and release are Davide's decisions;
this is not merge, release or tag authorization and claims no 1.0 readiness.

Execution model: a Claude coordinator as sole committer (first
`session_01QrdYw6dQiyzb2iCeAn8nRT` up to the handoff at `21dbd4b`, then
`session_016vWEy7BDrSx7uyPZndjRTK`, Herdr w2E:p7). Implementation and
verification ran on Herdr workers (Claude Opus 5.5 until Davide switched to
Codex on 2026-10-20 ~15:55: `gpt-6.1-sol` high for implementation and
verification, `gpt-6-astra` medium for review, Cortex `capture-7feef053`) and
in-session Opus implementers for Python; each worker owned disjoint files and
every product change has a failing-then-passing test. The ledger
`.superpowers/sdd/2026-10-19-studio-p4-presentation/progress.md`, `contracts.md`
(R1–R7 with the R3 amendment), `code-map.md`, `scenarios.md`,
`scenarios-browser.md`, both review reports, gate logs, 258 screenshots and 24
samples are retained there.

## Commits

| Commit | Task | Content |
| --- | --- | --- |
| `a9c4a65` | T2 viewer | presentation manifest (validate/apply/capture), shared fixtures, Scene `fitBounds`/`worldBounds`/`worldToScreen`/`setVisibility`, presentations page, editor, evidence panel, presentation mode |
| `221cb80` | T1 + T2 Python | `presentations/recording.py` (clone a captured request, modes `replay`/`new_seed`/`edited`, verification only for replay), schema 5 (`presentations`, immutable `presentation_revisions`, `export_jobs`), `PresentationStore`, export jobs with reference leases and `recover()`, `store prune --recordings`, Studio `presentation.*`/`export.*` commands |
| `4f4c29a` | T3 | export canonical hashing (byte parity with Python), `prepareExport`, shared `renderPresentationFrame` with the fixed evidence panel, offline HTML version-2 block, player presentation mode, static capture, export dialog with target preview |
| `fcb5dd2` | T4 docs | `docs/studio/{presentations,storage,cli}.md`, index/projects/README/nav |
| `68cefb4` | T4 portability | `store check`, prune owner chains in dry runs, `test_project_portability.py` |
| `ca9acc6` | T3 mount | export button on the presentation page, editor refinements, `presentation.spec.ts` |
| `88adf63` | T4 scenarios | scenario drivers (draft) |
| `21dbd4b` | T4 docs | `store check` and prune protections |
| `d82d620` | D1 | a missing `MODEL.py` target is refused instead of created as a folder |
| `7c2952c` | browser pass | a hinted checkbox is named by its label alone (hint is its description) |
| `5cc5a4d` | T4 scenarios | headless drivers for scenarios 1–3, 8–12 |
| `890f9cd` | D2/D3 | `simulatte run`, `run --reproduce`, `replay` adopt the selected managed generation's installation evidence and bindings |
| `66f6801` | browser pass | rate kept on "Use current view", export notifications no longer unmount the dialog, evidence artifact paths and untagged values, stale picker outputs, inert workspace in presentation mode |
| `3a34074` | browser pass | project state pushed to a tab only when it changed (was every 250 ms since P1) |
| `02e0874` | browser pass | presentation mode covers the viewport; export tests use a persistent WebKit context |
| `8c8fc55` | T4 scenarios | browser acceptance scenarios on all engines |
| `b594a43` | scenario defects | default-template Plotly figures accepted, KPI-level runs labelled, original-run picker labels, number/table metadata, read-only recovery hint, registration key errors, readable quantiles |
| `c164a32` | test | resynchronization test expects the state once (follows `3a34074`) |
| `631e43f` | review I3/M10/docs | a same-machine copy never runs from the original project's interpreter; `experiment run/resume` adopt the generation; reviewed docs |
| `bf7f941` | review minors, V2 | player replay at the saved end, paged row excerpts, single trace read, descriptor-based evidence paths, paged presentations list, analysis observation windows |
| `25440c6` | T4 scenarios | blocking browser scenario checks |
| `5855506` | delta review | "Create presentation from this run" on run pages and replication rows, cached compact window aggregate, fenced paging, cancellable row walks |
| `389ec40` | gate | scenarios in the installed smoke |
| `bd9bd6f` | delta 2 | a window cache of the wrong shape is a cache miss; bounded cache read |
| `098bfbd` | gate | scenario 8 checks console errors before stopping Studio |

## Per-task evidence

- T1 (`221cb80`): `test_presentation_request_clones_complete_capture` (horizon 10,000 / warm-up 1,000 and a nondefault measurement selection survive; only seed and level differ) and `test_explicit_edits_create_distinct_presentation_run` (edited requests never inherit verification) with finite-population stopping, invalid seeds, busy slots and remote reconstruction; 308 passed in the focused presentation/recording/replay/Studio/migration/packaging suites.
- T2 (`a9c4a65`, `221cb80`, `66f6801`): schema 4→5 migration with backup, stale revisions, reference-aware deletion, export leases until terminal states, shared Python/TypeScript fixtures accepted and rejected identically; `capture → save → reopen → apply` roundtrip in `presentation.spec.ts`.
- T3 (`4f4c29a`, `66f6801`, `02e0874`, `bf7f941`): `exportPinsRevisionAndEvidence` (a revision saved during an export does not change its snapshot hash or evidence), `targetPreviewAndEvidenceLimits` (aspect ratios, long annotations, large excerpts, refusals naming the item), offline HTML with CSP `connect-src 'none'`, script-safe JSON embedding and hashed statics; `presentation.spec.ts` 3/3 and `export.spec.ts` 5/5 on Chromium, Firefox and WebKit (`p4-presentation-<engine>.log`, `p4-export-<engine>.log`). Samples: `<engine>-frame-000000.png`, `-presentation.mp4`, `-presentation.html` for all three engines.
- T4 (`68cefb4`, `890f9cd`, `631e43f`, docs commits): `test_quiescent_copy_has_no_source_machine_dependency`, copied-project replay in a rebuilt generation (D2/D3), same-machine copy (I3), cache cleanup and dry-run owner chains; the twelve scenarios below.

## Package gate

All paths relative to `.superpowers/sdd/2026-10-19-studio-p4-presentation/`
(`gate1-summary.log`). Scripts `gate1-{a,b1,b2}.sh` are derived from the P3
gate7 scripts; every worker was idle. Candidate `bd9bd6f`, tree clean; wheel
SHA-256 `3727ca3760369a20…`.

| Command / evidence | Result |
| --- | --- |
| `uv run --all-extras ruff check .` / `ty check src` | passed |
| `pnpm --dir studio typecheck` / `test` / `build` | passed; trace 301, playback 30, scene 18, player 5, export 35, viewer 173 |
| `uv run python studio/scripts/package_assets.py` | 16 assets packaged, no tracked changes |
| `uv build` | wheel and sdist built |
| `uv run --all-extras zensical build --clean` | no issues |
| `uv run --all-extras pytest` (`gate1-python.log`, `gate1-coverage.xml`) | **4031 passed, 1 skipped**, 517 s, branch coverage **99.18%** (99% required) |
| `pnpm --dir studio test:browser` with a plot interpreter of the candidate wheel (`gate1-browser.log`, `browser-p4-results/`) | 149 passed, **1 failed**: WebKit scenario 8 recorded the page's reconnect console error after the test itself stopped Studio. The check now runs before the stop (`098bfbd`); scenario 8 then passed on WebKit, Chromium and Firefox (`gate1-scenario8-<engine>.log`) |
| Installed headless and core-only proofs | both examples exit 0; the bare wheel imports and runs without optional packages |
| `node studio/scripts/studio-smoke.mjs <venv>/bin/simulatte chromium` | **31 passed** (project specs, presentation and scenarios against the installed wheel) |
| Remote-worker regression, 13 execution suites | **338 passed** |
| No-subscriber overhead vs 0.12.0 (`overhead-p4-run1.md`, then `overhead-p4.md`) | first run: u95 head median 0.879 s with a 0.783 s IQR (one disturbed sample), failed; immediate quiet rerun passes: u90 −24.8% vs release / +7.5% vs stripped; u95 −25.2% / +8.0% (limit +8.5%) |

P4 changes no file on the simulation hot path (`environment.py`, `events.py`,
`entities.py`, `shopfloor.py`, `server.py`, `job.py`, `psp.py`, `router.py`,
`kpi.py`, `collectors.py` untouched in `3e321cd..098bfbd`).

## Acceptance scenarios (installed wheel)

Headless drivers: `studio/scripts/scenarios.py`, evidence
`scenarios-headless/scenario<N>.json` and `scenarios.md`. Browser journeys:
`studio/browser/scenarios.spec.ts`, evidence `scenarios-browser.md`,
`screenshots/<engine>-scenario<N>-*`, `samples/scenario9-*`; final rerun with
blocking checks **8/8 on Chromium, Firefox and WebKit** (`p4-scenarios-<engine>.log`).

| # | Headless | Browser | Notes |
| --- | --- | --- | --- |
| 1 | pass | pass | D1 fixed (`d82d620`); O1 SIGINT traceback remains (follow-up) |
| 2 | pass | pass | two models, blocked Run keeps B's draft, auto-run ownership, stop and run |
| 3 | pass | pass | environment update during active work, failed update keeps selection, restart wait, recovery viewer, saved figures without plot packages, broken model |
| 4 | — | pass | full recording with entity inspection; KPI-only run shows numerical state (B1 fixed) |
| 5–7 | 7 headless half pass | pass | populations, overlapping/empty subsets, distributions, intervals, AGV aggregations, post-run subset, actionable missing field; windows and metadata shown (V2–V4 fixed) |
| 8 | pass | pass | Python and Studio give the same snapshot and outputs; figures reopen offline; partial 8 of 24 then resumed; refresh; incomplete inputs refused |
| 9 | pass | pass | original seed → verified replay, new seed labelled; horizon 60 / warm-up 10 / plan `outcomes` survive both paths; every §9 row in Studio, video and offline HTML; target preview at 1920×1080; refusal names the item; revision 2 saved during export, exports stay at revision 1 |
| 10 | pass | pass | quiescent copy, rebuild from evidence, verified replay in the copy (D2/D3 fixed), cache cleanup keeps durable results |
| 11 | pass | pass | interrupted run and analysis become `lost`, failed publication, admission race, cleanup with several analysis/presentation revisions |
| 12 | pass with D4 | via 1 and the P3 journey | documented workflow; docs mismatches fixed (`890f9cd`, `631e43f`); D4 below |

## Independent reviews

1. Claude Fable 5.1 reviewer, `3e321cd..02e0874` (`review.md`, Cortex
   `session-d74b6676`): READY subject to a delta, 0 Critical, 4 Important, 10
   Minor. Offline HTML isolation, export leases and recovery, recording modes,
   schema 5 and limits judged sound.
2. Codex `gpt-6-astra` delta reviewer, `02e0874..bf7f941` (`delta-review.md`,
   `session-9877ce71`): NOT READY; I2 partial, new DI1 Important, DM1, DM2.
3. Same reviewer, `bf7f941..5855506` (`delta2-review.md`, `session-9fb3e379`):
   **READY at `5855506`**, all four closed, one Minor (D2M1) fixed in `bd9bd6f`.

| Finding | Status | Fix |
| --- | --- | --- |
| I1 default-template Plotly figures refused | CLOSED | `b594a43` |
| I2 original run by experiment/configuration/replication | CLOSED | `b594a43`, `5855506` |
| I3 same-machine copy uses the original's interpreter | CLOSED | `631e43f` |
| I4 version 0.17.0 already on PyPI (D4) | OPEN | maintainer release decision |
| M1–M10 | CLOSED | `631e43f`, `bf7f941`, `b594a43` (M4, M8) |
| DI1 `analysis.get` decoded whole snapshot for windows | CLOSED | `5855506` |
| DM1 paging race / DM2 uncancellable row walks | CLOSED | `5855506` |
| D2M1 wrong-shape window cache | CLOSED | `bd9bd6f` |

## Rulings beyond the plan's file lists

- Export bytes are not uploaded to the store; the job records the client-reported name, size and SHA-256 (R2).
- Recording snapshot rows are keyed by the new attempt id; references `attempt:<new>` and `result:<original>` at admission, `result:<new>` on success (R3 amendment).
- CLI coordinators adopt the selected managed generation only when the running interpreter's environment directory is that generation's; any other interpreter keeps raw evidence and old requests keep their stored identity.
- A folder counts as "another project" for I3 only if it holds `.simulatte/`; plain shared development venvs keep working.
- The window summary is a disposable derived cache keyed by snapshot hash (`analysis-cache/windows-v1`), with a SQLite projection for legacy snapshots; no schema bump, no identity change.
- Project state is pushed per connection only when it changed.
- Ephemeral Playwright WebKit contexts have no origin private file system; export tests use persistent contexts (probe `p4-opfs-probe.mjs`).

## Follow-ups recorded

- D4/I4: bump the version and the starter's `simulatte[studio]>=` bound before any release; the documented `uv run` commands otherwise resolve PyPI 0.17.0 (`Unsupported run store schema 5`).
- O1: SIGINT on `simulatte studio --no-browser` with non-TTY stdin prints a `KeyboardInterrupt` traceback (`studio/command.py:99`); O2 Uvicorn `websockets` deprecation warning on every invocation; O3 `Coordinator.wait()` returns ~60 ms before the interactive slot is released.
- CLI requests recorded before `890f9cd` stay unverifiable after a move; reconstructed but unselected generations are not adopted.
- `Coordinator.replay` falling back to the interactive slot still supersedes a manual run (recordings use `keep_interactive=True`); Studio's recording form changes the seed only; seeds above 2^53 lose precision as JSON numbers in the recording wire (also noted by the Codex implementer); one unreferenced empty-inputs snapshot on fresh projects; no single cache-cleanup command; absolute source paths are stored only as evidence.
- `docs/development/studio.md` "Share a recording" does not mention presentation export.
- P3 follow-ups remain (`specs/research/2026-10-19-studio-p3-acceptance.md`: M4 renderer compatibility rule, M5 server-side history search and others).

## Unobserved obligations

Native Windows/Linux/PyPy CI lanes, real SSH workers, actual Safari and native
Firefox application acceptance (Playwright WebKit/Firefox on macOS are not
those; real Safari OPFS behavior was not tested), cross-architecture
reproduction, documentation publication after an authorized release, and
Davide's own review of the whole branch and the actual UI (screenshots and
samples are retained for it). No push, merge, release or tag.
