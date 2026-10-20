# Studio P3 acceptance evidence

Date: 2026-10-20. Host: bigboss, macOS, CPython 3.14.3, uv 0.13.1, Node
24.13.0, Playwright 1.64.0 (browsers under
`.superpowers/sdd/2026-10-19-studio-p1-projects/browsers`). Branch
`feature/studio-projects`, isolated worktree `.worktrees/studio-project-experience`.
P3 base `d3881e3` (P2 docs commit; P2 product head `875b88b`, provisionally
accepted by Davide with the whole-branch review deferred to the end). P3
reviewed candidate `7d89b08`; product/test candidate after the review fix pass
**`38d094f`** (signed), fourteen signed commits `d3881e3..38d094f`. T1–T4 are implemented. This report records local package
evidence, the independent review and the follow-ups; acceptance is Davide's.
It is not merge, release or P4 authorization.

Execution model: a Claude coordinator (`session_01QrdYw6dQiyzb2iCeAn8nRT`,
Herdr w2E:p7) as sole committer; two Herdr Fable workers for the viewer
(`p3-shell`: T1, T2, T4A/C; `p3-artifacts`: T3 viewer, T4B/D) and in-session
implementers for the Python (analysis commands/HTTP, `table_query`, run_state
normalization, null-origin/frame/preview fixes, measurement-plan selection);
each on disjoint files, every product change with a failing-then-passing
test. The ledger `.superpowers/sdd/2026-10-19-studio-p3-workspace/progress.md`,
`contracts.md`, `code-map.md`, `journey.md`, `workflow.md`, worker logs and 54
screenshots are retained there.

## Commits

| Commit | Task | Content |
| --- | --- | --- |
| `00a7fb1` | T3 server | `analysis.*` commands, rows/csv/artifact readers with isolation headers, `table_query`, CSP `frame-src 'self'`, `store prune` cache removal |
| `fc5deaf` | T1 | PageState/Action/Feedback, forms/tables, tokens, route extensions, completion notices, `fetchApi`, `workspace.spec.ts` |
| `bb8ce86` | T1 follow-up | run lifecycle events announced as `run_state` |
| `bf27492` | T2 | runs, experiments and comparison pages, bounded history paging, missing-pair comparison |
| `63e1131` | T3 viewer | analyses page, artifact viewers, tagged-JSON codec, sandboxed Plotly frame, packaging/notices tests |
| `b32522e` | T3 follow-up | `Origin: null` for packaged assets (Firefox frame), `form-action 'none'`, preview without parameters |
| `6ec769f` | T4B | Studio docs: experiments guide, In-Studio analysis/measurements sections, navigation and states |
| `7ab557b` | T4A | keyboard and recovery journey spec, defects fixed, installed smoke wiring |
| `b9879fd` | T4C server | measurement-plan selection for `run.start`, `run.stop_and_run`, `experiment.define` |
| `8112db4` | T4C viewer | Measurements selects on the model page and experiment form, two deferred journey cases |
| `7d89b08` | T4D | disabled annotation button explains itself |
| `99c7293` | review fixes, server | paged `experiment.list`/`experiment.get`, reuse of unchanged inspected definitions, shared and cancellable index builds |
| `ead683f` | review fixes, viewer | experiments page pages older runs, `experiment.get` fallback for bookmarks, stale measurement plan reset |
| `38d094f` | review fixes, viewer | analysis selection pages older runs and experiment runs without duplicates, frame/validation/subscription nits |

## Per-task evidence (worker reports and coordinator reruns, before the gate)

| Task | Evidence |
| --- | --- |
| T1 | viewer 14 files/106 tests, typecheck clean; chromium workspace+project+project-history 7 passed; firefox/webkit workspace 1 each; `completionPreservesRouteAndDraft` unit + browser; 18 screenshots overview/model/run |
| T2 | viewer 125 tests; chromium workspace/project-history/project-adapters/compare 14 passed; firefox/webkit workspace 2 each; `boundedHistoryAndTablePages` (10,000 entries → 3 pages, 300 rows, ≤8 in flight), `comparisonRetainsMissingPairs`; 18 screenshots runs/experiment/comparison |
| T3 server | `tests/core/test_studio_analysis.py` + `test_table_query.py`: 173 passed in the coordinator's integration run; both new Studio modules and `table_query` at 100% branch |
| T3 viewer | viewer 17 files/127 tests, typecheck clean; packaging + Studio analysis 57 passed; `analysis.spec.ts` (`artifactIsolation`, eight-of-24 journey) chromium 2/2, webkit 2/2, firefox 2/2 after `b32522e`; 16–18 screenshots analyses/analysis/figure |
| T4A | `journey.spec.ts` chromium 10.6 s, firefox 11.0 s, webkit 10.3 s; `journey.md` lists every item with assertions |
| T4B | `workflow.md`: `run_managed.py`, `simulatte analysis run lateness --run …` and Studio produce the same `lateness_by_family` table (snapshot `f4076bc8…`, descriptor `2787482b…`, chunk `31af4d4d…`; A n=2 mean 1.5; B n=1 mean −1); docs build clean |
| T4C | viewer 129 tests; chromium journey 2/2, workspace 3/3, analysis 2/2, P1 specs 11/11; firefox/webkit journey+workspace 5/5 each; model screenshots refreshed with the Measurements select |
| T4D | viewer 130 tests; allow-list entry removed from the journey audit |

## Package gate

### Candidate `7d89b08` (gate6, historical)

Run while the review-fix implementer was editing Python and the viewer
workers were building, so its Python result is not evidence: 3864 passed,
one scheduler test failed against a mid-edit `scheduler.py`, and coverage
measured 97.22% with the misses concentrated in the files being edited.
The rest passed: ruff, ty, TS typecheck/tests/build, assets, wheel, docs;
the full browser suite **108 passed** in 7.5 minutes across Chromium,
Firefox and WebKit (`gate6-browser.log`); installed headless and core-only
proofs; the installed Studio smoke (`studio-smoke.mjs`, seven project specs)
**18 passed**; remote-worker regression **338 passed**; overhead within
limits with wide baseline IQRs from the concurrent load.

### Final candidate `38d094f` (gate7, then a quiet rerun gate8)

All paths relative to `.superpowers/sdd/2026-10-19-studio-p3-workspace/`
(`gate7-summary.log`, `gate8-summary.log`). Tree clean at `38d094f`; wheel
SHA-256 `73179e2725a2b463…`.

| Command / evidence | Result |
| --- | --- |
| `uv run --all-extras ruff check .` (`gate7-ruff.log`) | passed |
| `uv run --all-extras ty check src` (`gate7-ty.log`) | passed |
| `pnpm --dir studio typecheck` / `test` / `build` (`gate7-ts-*.log`) | passed; viewer 18 files / 138 tests |
| `uv run python studio/scripts/package_assets.py` (`gate7-assets.log`) | 16 assets packaged (incl. `plotly-frame.html` and the Plotly bundle), no tracked changes |
| `uv build` (`gate7-wheel.log`) | wheel and sdist built |
| `uv run --all-extras zensical build --clean` (`gate7-docs.log`) | clean build |
| `uv run --all-extras pytest` (gate7: `gate7-python.log`; quiet rerun: `gate8-python.log`, `gate8-coverage.xml`) | gate7 under the reviewer's concurrent load: 3871 passed, 1 failed (`test_replay::test_kpi_replication_replayed_at_full_is_verified`, a 20 s replay timeout in a 603 s run), 99.14%; **quiet rerun: 3872 passed, 1 skipped, 7 warnings, 509 s, 99.15%**, unchanged 99% gate reached |
| `PLAYWRIGHT_BROWSERS_PATH=… pnpm --dir studio test:browser` (`gate7-browser.log`, `browser-7-results/`) | **114 passed**, 8.3 min, Chromium/Firefox/WebKit (P1 specs, compare, export, viewer, workspace, journey, analysis) |
| Installed headless and core-only proofs on the candidate wheel (`gate7-headless-installed.log`, `gate7-core-only.log`) | both examples exit 0, both analyses succeed; bare wheel imports and runs without optional packages |
| `node studio/scripts/studio-smoke.mjs <venv>/bin/simulatte chromium` (`gate7-installed-smoke.log`) | **20 passed**, 2.1 min: the seven project specs against the installed wheel |
| Remote-worker regression, 13 execution suites `--no-cov` (`gate7-remote.log`) | **338 passed** (local fake-ssh/staging/provisioning harnesses) |
| No-subscriber overhead (gate7 `overhead-7.md` under load: u95 stripped +9.5% with 0.5 s IQRs, failed; quiet rerun `overhead-8.md`, `bench8-*.json`) | **quiet rerun passes**: u90 −26.8% vs release / +6.9% vs stripped; u95 −23.0% / +8.5% (at the +8.5% limit; P2 measured +5.3%, P1 +7.5%) |

P3 changes no file on the simulation hot path (`environment.py`, `events.py`,
`entities.py`, `shopfloor.py`, `server.py`, `job.py`, `psp.py`, `router.py`,
`kpi.py`, `collectors.py` are untouched in `d3881e3..38d094f`), so the u95
stripped figure at the limit is measurement variance on this host, not a
regression; the comparison is recorded as it was measured.

## Independent review

Reviewer: the Claude (Fable 5.1) session that reviewed P2 and implemented
nothing in P3 (Herdr tab w2E:t7, renamed `p3-reviewer`), read-only, with the
plan's review-focus table, the contracts and the worker evidence as inputs
(`review-p3-38d094f.md`; probes under `/tmp/studio-p3-review/`). Main
review at `b9879fd` with a delta for `8112db4..7d89b08`, then a delta for
the fix pass `7d89b08..38d094f`.

Verdict: **READY for P4 at `38d094f`** (code verdict). No Critical finding.
The five review-focus risks were verified against code and re-run tests
(`completionPreservesRouteAndDraft`, `boundedHistoryAndTablePages`,
`comparisonRetainsMissingPairs`, `artifactIsolation`,
`keyboardAndRecoveryJourney`); the isolation design (sandboxed frame,
validator, `allow_null_origin` scope, blob images, `run_state`
normalization, measurement-plan selection) was judged sound.

| Finding | Status | Fix |
| --- | --- | --- |
| I1 Experiment runs beyond the newest 20 unreachable from Studio | CLOSED | `99c7293` (`experiment.list`/`experiment.get`), `ead683f`, `38d094f` |
| M1 New-analysis form duplicated the newest 50 runs | CLOSED | `38d094f` |
| M2 Identical index builds not coalesced, no disconnect cancel | CLOSED | `99c7293` (probe: six concurrent requests → one build) |
| M3 Every preview re-captured and re-inspected in a child | CLOSED | `99c7293` (stored definition reused for an unchanged source) |
| M6 Robustness nits | PARTIAL | `38d094f`; the parked CSV producer thread after an abandoned download remains (bounded, harmless) |
| M7 Remembered plan no longer declared still sent | CLOSED | `ead683f` |
| M4 Exact renderer pinning (first renderer bump labels old figures) | OPEN | follow-up: compatibility rule before P4 consumes figures |
| M5 Search covers loaded rows only | OPEN | follow-up: server-side filter on `history.get` |

Reviewer checkpoints: Cortex `session-121322e5-cedf-4bfa-b1ef-ecf300fb4c3b`
(main), `session-b035a4ba-e919-4062-bdf4-8f8667ddace4` (4C/4D delta),
`session-9d967365-b3e5-46a8-85de-08edcb1d87fb` (fix-pass delta).

## Rulings beyond the plan's file lists

- No jsdom: unit tests are pure-logic or static markup; interaction is proven in Playwright, keeping the plan's test names.
- Existing `history.get` (1–200), `history.annotate` and `comparison.get`/results HTTP serve the pages; no duplicate `history.list`/`experiment.results`.
- Routes extended, not replaced: `#/runs[/<model>]`, `#/experiments[/<run>]`, `#/compare/<ids>`, `#/analyses[/<execution>]`, `#/analyses/new/<key>`.
- Analysis commands are project commands with a nullable revision; `analysis_state` is polled by the pump (intermediate states may be skipped, the final state is announced once); admission names live in `details.name`; preview dry-runs every admission rule and skips parameter validation only when no parameters are supplied.
- `api/*` requires the session header, so images load through `fetchApi` and blob URLs (never inline SVG); every analysis artifact response carries `Content-Security-Policy: sandbox; default-src 'none'`, nosniff and attachment disposition.
- Plotly figures render in `<iframe sandbox="allow-scripts">` (opaque origin) loading the packaged `plotly-frame.html` with a self-hosted classic script pinned to plotly.js-dist-min 4.1.1 (Python plotly 7.1.0's bundle; a Python test guards the pin); the parent validates specs (`data`/`layout`/`frames` only; no `config`, prototype keys, `href`, `*src`, templates, URL-like sources, scripts, map/tile traces), binds one nonce to the frame URL hash and checks `event.source`; the frame's CSP has no network, `form-action 'none'`, `frame-ancestors 'self'`; Firefox needs `Origin: null` accepted for packaged GET/HEAD assets only.
- `table_query` builds a disposable SQLite index per artifact and query in a contained child: exact int64, `-inf < finite < +inf < NaN < null` (nulls last in both directions), code-point strings, three-valued null filters, bounded cache, disk-spilled sorting, durable publication, cursors bound to the index; `store prune` removes the cache; sort/filter are refused on read-only services.
- `experiment.cancel` is terminal (no pause command exists); resume applies only to server-paused runs; completion notices cover the interactive attempt only.
- Measurement-plan selection was added for interactive runs and in-Studio definitions after the docs pass found that only experiment files and the Python API selected plans.

## Follow-ups recorded

- Replication ↔ experiment linkage on the Runs page uses one `comparison.get` per listed experiment (≤20); a history-item field would remove them.
- `project-history.spec.ts` passes through a closed footnote under the runs table.
- Index builds are not cancelled on client disconnect (60 s timeout); `table_query` has no cache eviction or in-process build lock and reads `reader._identity`.
- The single-model `StudioServer` (`app.py`) `run.start` and snapshot do not carry the measurement plan; auto-runs stay unmeasured; plans already inside a definition are not checked against metadata at define time.
- The viewer server (`server.py`) still rejects `Origin: null` (frame unused there).
- `ruff format --check` still flags fenced code in docs pages (pre-existing pattern).

## Unobserved obligations

Native Windows/Linux/PyPy CI lanes, real SSH workers, actual Safari and native
Firefox application acceptance (Playwright WebKit/Firefox engines on macOS are
not those), and Davide's own review of the actual UI (screenshots are
retained for it). No merge, push, release or P4 work.
