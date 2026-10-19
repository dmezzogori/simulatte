# Studio P2 acceptance evidence

Date: 2026-10-19. Host: bigboss, macOS, CPython 3.14.3, uv 0.13.1, Node
24.13.0. Branch: `feature/studio-projects`, isolated worktree
`.worktrees/studio-project-experience`. P2 base `bf3bc53` (accepted P1 handoff;
P1 product/test head `6ccac36`). Original reviewed candidate
`c4c17276c475cb25e442fb47e83e68d93fe7dfd6`; product/test candidate after the
review fix pass and its recheck: **`875b88b`** (signed), fifteen signed
commits `bf3bc53..875b88b`. T1–T7 are implemented and the
independent review's Critical/Important findings are fixed. This report
records local package evidence, the review and its delta recheck; coordinator
acceptance is Davide's. It is not merge, release or P3 authorization.

The implementer changed mid-task: Tasks 1–6 and the T7 draft were written by
the Codex `studio-p2` session (Herdr w2E), which hit the Codex weekly limit
after the first gate attempt. A Claude session
(`session_01QrdYw6dQiyzb2iCeAn8nRT`) resumed T7 as sole writer, committed it,
and ran the gates below. The plan-specific ledger
`.superpowers/sdd/2026-10-19-studio-p2-analysis/progress.md` is retained with
every log named here.

## Implemented scope

Frozen measurement declarations and read-only observations (`simulatte.analysis`
types, `MeasurementPlan`, `Population`, `ObservationCollector`); the
`analytical-dataset-v1` chunked msgpack codec with exact int64/non-finite values,
bounded pages, CSV dialect and verified chunk descriptors; publication of
typed datasets as ordinary hashed run artifacts behind the
`analytical-artifacts-v1` worker capability, with `measurements=None`
preserving legacy request identity; built-in `JobOutcomeCollector` and
`AgvDurationCollector`; schema-4 analysis definitions, immutable content-addressed
input snapshots, admission/reference transactions and retention; isolated
analysis children with verified output publication; numerical reports and
optional Matplotlib/Seaborn/Plotly adapters with renderer identity; the
`simulatte analysis run|rerun|refresh|show|cancel` commands, `prepare_analysis`,
two walkthroughs (`docs/studio/measurements.md`, `docs/studio/analysis.md`) and
`examples/studio_project` with `run_numerical.py` and `run_managed.py`.
P3/P4 work has not started.

## Per-task evidence (targeted, `--no-cov`)

| Task | Commit | Final targeted run |
| --- | --- | --- |
| T1 declarations and observation API | `0340064` | `task-1-tests.log` 73 passed |
| T2 chunked datasets and publication | `a3fcf9f` | `task-2-tests.log` 187 passed; `t2-green-final.log` 278 passed incl. remote harnesses |
| T3 production/fleet measurements, examples | `a931742` | `task-3-tests.log` 23 passed; `t3-examples.log` 3 job rows, 12 AGV rows |
| T4 frozen snapshots and retention | `49cbd2a`, policy fix `a054408` | `task-4-tests.log` 38 passed; `t4-policy-green.log` 40 passed |
| T5 analysis children and publication | `bd42a2e` | `task-5-tests.log` 92 passed |
| T6 reports and adapters | `59f7548`, renderer fix `0be9402` | `task-6-tests.log` 62 passed; `t6-renderer-green.log` 63 passed |
| T7 headless CLI, docs, example | `c4c1727` | `t7-green3.log` 18 passed; `t7-artifact-layout-green2.log` and `t7-resume-targeted-green.log` 29 passed (execution, snapshots, CLI) |

Red logs for every task (`t<N>-red.log`, `t2-csv-red.log`, `t2-populations-red.log`,
`t3-late-attach-red.log`, `t4-policy-red*.log`, `t6-renderer-red.log`,
`t7-artifact-layout-red.log`) are retained alongside.

## Installed headless proof and core-only check

The first gate attempt (dirty tree on `0be9402`) failed the installed headless
example with `insufficient input: snapshot is incomplete or empty`
(`gate-headless-installed.log`): the analysis store resolved dataset artifacts
from `results/<attempt>/<path>` while the publisher indexes them relative to
`results/<attempt>/artifacts/`. Two unit fixtures had copied the wrong base path.
`t7-artifact-layout-red.log` reproduces it through the real receipt/publisher;
the fix and a real-publisher regression are in `c4c1727`.

At `c4c1727`, with a fresh `[studio]` venv of the rebuilt 0.17.0 wheel
(`gate-wheel-2.log`, `gate-install-2.log`) and a fresh copy of
`examples/studio_project` in `/tmp` (`gate-headless-installed-2.log`):
`run_numerical.py` retained 3 job rows and 12 AGV rows; `run_managed.py`
published two KPI-level runs and executed the `lateness` and `utilization`
analyses to `succeeded`. The installed CLI (`gate-headless-installed-cli.log`,
`installed-lateness-{show,rerun,run2}.json`): `show` exit 0; `rerun` exit 0 with
a new execution id, the same snapshot hash and byte-identical output
descriptors; `run utilization --run <production attempt>` exit 2
`insufficient input for dataset agv_durations`; `run lateness --run …
--require-complete` exit 0 with a different snapshot hash (the policy is part
of the snapshot) and identical outputs; `cancel` on a succeeded execution exit
0, no change. Core-only (`gate-core-only.log`): a bare-wheel venv (5 packages,
no matplotlib/plotly/seaborn/tomlkit/starlette/gymnasium) imports
`simulatte.analysis`, runs `run_numerical.py` and
`examples/intralogistics_simple.py`, lists `analysis` in `simulatte --help` and
returns a validation envelope from `simulatte analysis show`.

The same proofs were repeated on wheels built from `1121a51`
(`gate4-headless-installed.log`, `gate4-core-only.log`) and from the final
candidate `875b88b` (`gate5-headless-installed.log`, `gate5-core-only.log`,
wheel SHA-256 `8c9cd45fb75e03e163bad823851a46cab6e402ae32af4c80bb6ea1652aa143c4`):
both examples exit 0 and both analyses succeed; the bare wheel imports and runs
without optional packages.

## Package gates

### First attempt (Codex implementer, dirty tree on `0be9402`, historical)

`gate-*.log`: Ruff, ty, TS 449, typecheck, build, package assets, wheel and
docs passed. Installed headless **failed** (artifact layout, above). Full
Python (`gate-python-1.log`) and browser (`gate-browser.log`) were interrupted
on purpose when the layout bug was found: 1210 passed / KeyboardInterrupt, no
coverage; 35 passed, 1 failed, 1 interrupted, 50 not run. The browser failure,
`project.spec.ts` "installed project: starter, managed generation, explicit run
and reopen" with `ERR_CONNECTION_REFUSED` console errors, is the still-open
page's websocket reconnect during the test's intentional stop → relaunch window
while the full Python suite ran concurrently; P2 changes nothing under
`studio/` or `src/simulatte/studio/`, and the final browser gate below reran it.

### Candidate `c4c1727` (gate2)

Ruff, ty, TS typecheck/tests/build, 14 packaged assets (no tracked changes),
wheel and docs passed. Full Python: **3687 passed, 1 skipped**, coverage
**98.11%**, failing the unchanged 99% branch gate (`gate2-python.log`). The gap
was the new analysis package (`child.py` 0% because it runs under `python -I`;
store/context/command/publication/builtins 76–90%). Tests only were added
(`66dd472`, `1121a51`): no exclusion, no threshold change; the analysis package
measures 100% except `builtins.py:117->113`, argued unreachable.

### Candidate `66dd472` (gate3)

Full Python **3761 passed, 1 skipped**, 98.88% (`gate3-python.log`); the
`process.py` row was corrupted because the guardian worker edited that file
during the run; excluding it the tree measured ≈99.06%. Superseded by gate4.

### Candidate `1121a51` (gate4)

Ruff, ty, TS typecheck/tests/build, assets, wheel and docs passed. Full Python
**3788 passed, 1 failed, 1 skipped**, coverage **99.12%** (gate reached;
`gate4-python.log`). The failure,
`test_measurement_failure_reaches_admission_refusal`, passed in focused runs and
failed in the full suite because `_admit` iterated (run, configuration) groups
in hash-seeded set order and the shared fixture carries an empty second
configuration; fixed in `875b88b` (sorted groups, narrowed test, proven across
five `PYTHONHASHSEED` values). The first browser run (`gate4-browser.log`)
failed all 87 tests in 17 s: Playwright 1.64.0 needs Chromium 1248, Firefox
1555 and WebKit 2370, which the P1 gate installed under
`.superpowers/sdd/2026-10-19-studio-p1-projects/browsers`, while the default
cache holds older builds. With `PLAYWRIGHT_BROWSERS_PATH` pointing there
(`gate4-browser-2.log`) **87 passed**, 4.6 minutes, Chromium/Firefox/WebKit,
including the `project.spec.ts` journey that failed in the first attempt.
Installed proof on the candidate wheel (`gate4-headless-installed.log`): both
examples exit 0, `lateness` and `utilization` succeeded; core-only
(`gate4-core-only.log`) passed; remote-worker regression (`gate4-remote.log`)
**338 passed** across the ssh transport, staging, provisioning, transfer,
workers CLI/config, capability, supervisor dispatch, preflight, scheduler,
driver and publication suites (local fake-ssh harnesses, not a real host).

### Final candidate `875b88b` (gate5)

All paths relative to `.superpowers/sdd/2026-10-19-studio-p2-analysis/`
(`gate5-summary.log`). Tree clean except the uncommitted plan checkbox edits.

| Command / evidence | Result |
| --- | --- |
| `uv run --all-extras ruff check .` (`gate5-ruff.log`) | passed |
| `uv run --all-extras ty check src` (`gate5-ty.log`) | passed |
| `pnpm --dir studio typecheck` (`gate5-ts-typecheck.log`) | passed |
| `pnpm --dir studio test` (`gate5-ts-test.log`) | passed (viewer 93 tests / 13 files among the workspace packages) |
| `pnpm --dir studio build` and `uv run python studio/scripts/package_assets.py` (`gate5-ts-build.log`, `gate5-assets.log`) | passed; 14 assets packaged, no tracked changes |
| `uv build` (`gate5-wheel.log`) | wheel and sdist built |
| `uv run --all-extras zensical build --clean` (`gate5-docs.log`) | clean build, no issues |
| `uv run --all-extras pytest` (`gate5-python.log`, `gate5-coverage.xml`) | **3790 passed, 1 skipped, 7 warnings**, 459 s; **99.14%** branch coverage, unchanged 99% gate reached |
| `PLAYWRIGHT_BROWSERS_PATH=…/2026-10-19-studio-p1-projects/browsers pnpm --dir studio test:browser` (`gate5-browser.log`, `browser-5-results/`) | **87 passed**, 4.7 min, Chromium/Firefox/WebKit |
| Installed headless and core-only proofs (`gate5-headless-installed.log`, `gate5-core-only.log`) | passed, see above |
| Remote-worker regression, 13 execution suites `--no-cov` (`gate5-remote.log`) | **338 passed** (local fake-ssh/staging/provisioning harnesses) |
| No-subscriber overhead (`gate5-overhead.log`, `overhead-5.md`) | passed, see below |

No TypeScript source changed in P2 (`git diff --stat bf3bc53..875b88b -- studio/ src/simulatte/studio/` is empty); the browser gate exercises the unchanged Studio server and viewer against the P2 Python runtime, including the guardian change.

## Measured dataset buffers and no-subscriber overhead

`measure_buffers.py` (`gate-buffers.json`) drives the codec with a counting
writer/reader at 10,000 and 1,000,000 small rows:

| Rows | Writer buffer | Reader buffer | Page(limit=100) | tracemalloc peak writer / reader |
| --- | --- | --- | --- | --- |
| 10,000 | 4,096 rows, 16,387 B | 4,096 rows, 16,387 B | 1 chunk | 597 KB / 1.19 MB |
| 1,000,000 | 4,096 rows, 24,579 B | 4,096 rows, 24,579 B | 1 chunk | 660 KB / 1.21 MB |

Buffers never exceed one chunk; the small growth is the chunk-descriptor
manifest, not row accumulation. The codec was not changed by the layout fix.

`gate5-b2.sh` reuses the P1 benchmark environments (`bench-base` = released
0.12.0, `bench-stripped` = 0.12.0 with 12 `env.debug` calls removed) and a
fresh venv with the plain `875b88b` wheel (`bench-head-5`), running
`benchmarks/run.py --mode none --warmup 1 --repeat 5 --processes 3` per
workload after the browser gate finished, with the two review workers idle:

| Workload | Released 0.12.0 → P2 | Stripped 0.12.0 → P2 |
| --- | --- | --- |
| u90 | 0.657 s → 0.492 s, **−25.1%, pass** | 0.457 s → 0.492 s, **+7.7%, pass** |
| u95 | 0.715 s → 0.526 s, **−26.4%, pass** | 0.500 s → 0.526 s, **+5.3%, pass** |

Limits are unchanged: `--budget 0.03 --noise 0.02` against the release and
`--budget 0.065 --noise 0.02` against the stripped copy. P1 measured +7.3% /
+7.5% on the stripped comparison, so P2's measurement collectors add nothing
measurable when no plan is configured. Raw samples:
`bench5-{base,stripped,head}-none-{u90,u95}.json`. macOS measurements only.

## Rulings beyond the file lists

Recorded in the ledger with reasons and the cost if wrong:

- `ObservationCollector` overrides attachment for unscoped observations rather
  than weakening `Collector`'s entity-ownership precondition; built-ins filter
  their own event owners.
- Exported CSV uses single-line JSON cells with an 8 MiB encoded-record and
  256 KiB decoded-row bound and a small strict reader, instead of changing the
  process-global `csv.field_size_limit`.
- Persisted memberships use a reserved typed `populations` list column only when
  populations are declared.
- Typed dataset descriptors are additional files inside the unchanged hashed
  artifact/publication contract; typed validation belongs to `DatasetReader`.
- Job arrival boundary is first PSP/shop-floor admission with `created_at` as the
  retained arrival time; build-then-attach seeds outstanding jobs, missed arrival
  attributes stay null with `missing_reason`, completed-before-attachment history
  is counted in `limitations` and never reconstructed. `observe_boundary(agv)`
  is explicit for classification edits without a state change.
- Snapshots retain scalar copies and unavailable statuses; references belong to
  each admitted execution (including terminal ones); previews hold no lease.
  Chunk descriptors must match the published artifact index before admission.
- Definition and user policies combine by `max(min_replications)` and
  `OR(require_complete)` applied per required dataset to dataset-valid inputs
  (`a054408`).
- One `ThreadPoolExecutor` analysis slot per `RunStore`, independent of
  simulation/inspection actors; the child receives read-only materialized source
  and verified private dataset copies, no store reader. Output limits 1 GiB /
  100,000 files / 4 MiB per document. Success requires verified artifacts, clean
  exit, fenced durable rename and one index transaction.
- Plotly outputs record `renderer.name="plotly.js"` and the bundled
  `get_plotlyjs_version()`; the Python package version is `metadata.adapter_version`
  (`0be9402`).
- T7: `prepare_analysis` inspects under `store._inspection_lock`, now a
  `RunStore` attribute aliased by `Coordinator._inspection_lock`; an
  `analysis-control/<id>.cancel` file lets `analysis cancel` reach an execution
  whose store is owned by another process; `child.close()` cleanup errors are
  recorded as `cleanup_error` instead of escaping `wait()` (flagged for review).

## Unobserved obligations

Native Windows/Linux/PyPy lanes, real SSH remote workers (the staging, fake-ssh and
provisioning harnesses ran locally on macOS), actual Safari and native Firefox
application acceptance remain CI/platform obligations. No merge, push, release or
P3 work was performed.

## Independent review and fix pass

Reviewer: a fresh Claude (Fable 5.1) session in Herdr tab w2E:t7, read-only,
with the plan's review-focus table and six flagged areas, not the
implementer's conclusions (`review-p2-c4c1727.md`, probes in
`/tmp/studio-p2-review/`). Verdict at `c4c1727`: **NOT READY for P3**.

| Finding | Fix commit | Evidence |
| --- | --- | --- |
| C1 The child's `config.json` embedded the full snapshot (per-replication resolved manifest/request/fingerprint ≈90 KB) under the 4 MiB bound: admitted executions failed above ≈44 replications (`config_size_probe.log`) | `a999fe8`: slim `child_inputs` projection, 64 MiB parent-document bound | `test_experiment_scale_snapshot_reaches_the_child_without_resolved_manifests` (72 replications, config < 1 MiB) |
| I1 `AnalysisRunner.close()` raised `KeyError` after a finished execution was deleted; `ProjectService.close()` then skipped coordinator/reader/store | `a999fe8` | four run → delete → close regressions, runner and service |
| I2 Admission refusal named only the dataset | `6e23fab`: `InsufficientInput` with valid/required counts and per-replication missing fields, CLI `details` | extended policy tests, `test_dataset_refusal_envelope_carries_structured_details` |
| M1 cleanup diagnostics lost/replaced | `a999fe8` | merged details tests |
| M2 control-file edges | `a999fe8`, `6e23fab` | non-regular entries, `finalizing`, recover/delete |
| M3 crashed `prepare_analysis` reference | `6e23fab` | recovery test |
| M4 retention leftovers | `6e23fab` | delete/prune tests |
| M5 `failure.json` invisible | `6e23fab` (`dataset_failures`, only when present) | refusal test |
| M6 malformed manifest aborts preview | `6e23fab` | parametrized test |
| M7 tagged `figure.json`, M8 child imports the project's simulatte | documented in the docs commit | — |
| M9 seaborn `Plot` AttributeError | `6e23fab` | adapter test |

Questions 3 (combined policy never weaker), 5 (lock ordering, reference
cleanup, no project import in the parent) and 6 (artifact path validation)
were answered as sound. Not done: an admission-time bound on the child input
document (≈2.3 KB per replication after the fix; recorded as a follow-up),
`request_cancel` still writes a file for a `waiting` execution without a runner
(recover/delete clean it), `store prune` output does not report removed previews.

### Guardian race

A second Claude worker (tab w2E:t8) root-caused "Process guardian lost"
(`guardian-race-report.md`, `guardian-landed.md`): on macOS `killpg` returns
EPERM for a group whose only member is an unreaped zombie; the runner sends one
terminate from `cancel()` and one from its loop, and when the second landed in
the ≤20 ms window after the child exited the guardian crashed in `terminate()`
and again in its `finally` `child.kill()`, so the exit record was never sent.
No descendant leaked and the terminal state stayed `cancelled`; the `c4c1727`
catch hid the guardian crash. Reproductions: 9/30 (double terminate) and 8/20
(runner cancellation) before, 0/30 and 0/20 after `984951e`. Linux returns 0
for that call, so only the macOS lane could observe it.

### Delta review

Delta review of `c4c1727..1121a51` (`review-p2-delta-1121a51.md`): code
verdict **READY for P3**; C1, I1, I2, M1–M6 and M9 CLOSED with re-run probes
(child document 94,415 → 2,271 bytes per replication; 72 replications → 164 KB;
the pre-fix installed study re-previews to the same snapshot hashes, so
`dataset_failures` changes no existing identity; `prune` cannot drop a snapshot
any execution names); M7/M8 OPEN as documentation notes, now in
`docs/studio/analysis.md`. New findings: NEW-1 Important, `_record` raised
`TypeError` when the execution row was deleted while the runner was stopping
the child (fixed in `875b88b`, deterministic cancel → delete regression; `wait()`
then raises `KeyError` like any unknown identity); NEW-2 Minor, no
admission-time bound for the child document (≈29,000 plain replications or
≈850 million-row replications fit in 64 MiB; follow-up); NEW-3 Minor,
`experiments[].preparation`/`definition` reach the child unslimmed (per
experiment; follow-up). The guardian contract questions were answered: no new
leak path, no success from a cancelled or failed execution, EPERM tolerance
acceptable.

Bounded recheck of `1121a51..875b88b` (`review-p2-delta2-875b88b.md`): NEW-1
CLOSED (probe rerun: `wait()` raises `KeyError`, staging and lease clean), the
sorted group order changes no hash or stored identity, the two documentation
notes match the code, no new finding. **Final code verdict: READY for P3.**
Reviewer checkpoints: Cortex `session-6617860f-31b5-47c3-81e8-0a64ac2175e9`
(review), `session-96c8f121-e60c-46bb-8810-a50fd20b9692` (delta),
`session-4d4ceb53-d77c-4723-b465-006e8db7a0e8` (recheck); guardian worker
`session-faaee312-1a9d-4fa5-9960-99b1b05e0389` and
`session-be9624b6-19a0-4c1f-a569-9a3e5c4d8cf6`.

## Handoff status

Every local package gate passes at `875b88b`, the independent review's
Critical and Important findings are closed and rechecked, and the remaining
minor items are recorded as follow-ups: an admission-time bound for the child
input document (NEW-2), slimming `experiments[].preparation`/`definition` in
the child document (NEW-3), `request_cancel` still writing a control file for a
`waiting` execution without a runner (cleaned by recover/cancel/delete),
`store prune` not reporting removed previews, the child's `input_availability`
not showing measurement-failure text (admission does), the argued-dead branch
`builtins.py:117->113`, and `ruff format --check` on fenced code in the two
new docs pages (pre-existing pattern, not a gate). The docs commit after
`875b88b` changes only this report and the two plans.

Coordinator acceptance of P2 is Davide's decision and is not recorded here.
No merge, push, release, Herdr tab closure or P3 work was performed. All
commits are signed; no signing fallback was needed. The P2 ledger, every gate
log named above, the review copies and the Cortex fallback archive remain in
`.superpowers/sdd/2026-10-19-studio-p2-analysis/`.
