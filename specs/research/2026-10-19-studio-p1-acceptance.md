# Studio P1 acceptance evidence

Date: 2026-10-19. Host: bigboss, macOS, Python 3.14.3, uv 0.13.1,
Node 24.13.0. Branch: `feature/studio-projects`, isolated worktree
`.worktrees/studio-project-experience`. Baseline `a80ece3`; implementation T1–T6
through `1378cfd`. T7 is a package-review candidate with an OPEN native reconstruction issue.
This is not a P1 completion claim, independent review or release approval.

## Implemented scope

Persistent project/model definitions, safe starters and legacy target parsing;
schema-3 associations/history with backup migration and read-only access;
immutable managed/existing runtime generations; one coordinator with separate
model inspection and explicit interactive admission; launcher handoff and recovery;
workspace setup, model forms, runs, existing experiment/recording adapters.
P2 analysis and saved presentation work has not started.

## Evidence recorded so far

| Check | Actual result |
| --- | --- |
| T1 projects and source snapshots | 72 Python tests passed |
| T2 project store/migrations/readers | 45 passed |
| T3 environments/runtime/provisioning | 34 passed, including native macOS process/probe tests |
| T4 contexts/admission/inputs/coordinator/scheduler/lifecycle | 88 passed |
| T5 CLI/project API/auth/HTTP/WebSocket | 57 passed |
| T6 live/experiment/project API/source snapshot regressions | 92 passed |
| T6 viewer unit tests | 85 passed |
| T6 real installed project workflow | 9 passed across Chromium, Firefox and WebKit |
| T7 TypeScript package tests | 441 passed |
| T7 TypeScript typechecking | passed |
| Fresh wheel standalone viewer smoke | passed; authenticated ranges/reload/export/offline playback, zero external requests and page errors |

The project browser fixture installs a fresh wheel into a separate launcher
environment. The starter's managed generation installs an explicitly declared,
project-relative development wheel, because this implementation is not released.
It does not test availability of a future PyPI release.

The workflow covers preview/apply, lock preview, managed runtime handoff, explicit
model preparation, no attempts on open, seed/draft navigation, explicit Run,
recording controls, naming and reopening history, HTML export with networking
disabled, export dialog, and recovery after deleting the test-owned managed cache
and replacing source with an import failure. A second workflow registers existing
legacy entrypoints without importing them. A third prepares B while manual A runs,
preserves B's draft on busy admission, then explicitly stops A and runs B.

Python tests additionally exercise source edits during A's publication, stale
revisions, cancellation failure/intervening owners, automatic-session ownership,
model rename/removal/copy, ambiguous legacy history, protected references,
schema backup/migration failure and unavailable historical runtimes.

## Visual and keyboard evidence

Artifacts are retained locally in the plan-specific directory:

`.superpowers/sdd/2026-10-19-studio-p1-projects/screenshots/`

For each of `chromium`, `firefox`, `webkit`, the following files exist:

- `<engine>-setup-1280x800.png` and `<engine>-setup-1440x900.png`
- `<engine>-model-1280x800.png` and `<engine>-model-1440x900.png`
- `<engine>-run-1280x800.png` and `<engine>-run-1440x900.png`

Keyboard traversal verifies the skip link and focus on main content (Tab on
Chromium/Firefox, macOS WebKit's Alt+Tab link traversal). The first starter action
is above the fold. Event filter captions and controls wrap at both required sizes.
The sidebar is the only top-level navigation. Model drafts survive back navigation.

Coordinator findings about setup order and clipped event controls were corrected.
Davide reviewed actual screenshots and accepted the direction: “looks good, but
just that there is too much padding everywhere, in buttons, in text areas ,
controls, etc. a little bit more compact”. Workspace-only spacing and control
padding were reduced moderately, with approximately 34px desktop controls and
unchanged legible font sizes/focus styles. Refreshed compact screenshots were
inspected; Davide then viewed the compact gallery in eva Safari and explicitly replied “saw them. ok.” Actual-UI acceptance is satisfied. This is screenshot review, not native Safari execution testing.

## Package checks

All paths below are relative to
`.superpowers/sdd/2026-10-19-studio-p1-projects/`. Full logs are retained, including
failed runs. The 99% branch-coverage threshold was not changed.

| Command / evidence | Result |
| --- | --- |
| `uv run --all-extras pytest` (`gate-python-final.log`) | **3515 passed, 1 skipped, 5 warnings**, 386.58s; **99.02%** coverage |
| `uv run --all-extras ruff check .` (`gate-ruff-final.log`) | passed |
| `uv run --all-extras ty check src` (`gate-ty-final.log`) | passed |
| `pnpm --dir studio typecheck` (`gate-ts-types-final.log`) | passed |
| `pnpm --dir studio test` (`gate-ts-tests-final.log`) | **441 passed** |
| `pnpm --dir studio build` and `uv run python studio/scripts/package_assets.py` (`gate-build.log`) | passed; 14 assets packaged before wheel/browser execution |
| `uv build` (`gate-wheel-final.log`) | wheel and sdist built |
| `pnpm --dir studio test:browser` (`gate-browser-final.log`) | **45 passed**, 2.1m, Chromium/Firefox/WebKit |
| Fresh-wheel `studio/scripts/studio-smoke.mjs` (`gate-installed-project.log`) | **3 passed**, 17.5s |
| Fresh-wheel `studio/scripts/installed-smoke.mjs` (`gate-installed-viewer-final.log`) | playback/reload/ranges/offline HTML passed; zero external requests/page errors |
| `uv run --all-extras zensical build --clean` (`gate-docs-final.log`) | passed; clean build, no issues, 1.58s |
| Native installed tutorial (`gate-installed-tutorial.log`) | **FAILED reconstruction**, open issue below; bare Python, full/KPI executions and copied history succeeded first |
| Native copied recovery (`gate-copied-recovery.log`) | passed: both recordings, annotations/important flags, recovery without importing deliberately broken model |

The Python gate includes store schema migrations/backups, legacy CLI flags,
two-model admission/inspection, environment ownership, unavailable-runtime recovery,
remote provisioning/staging/preflight and fake-SSH worker regressions. Those POSIX
checks ran locally; they are not a real remote-machine deployment. Native managed
uv provisioning and installed launcher handoff also passed in browser workflows.

### Resolved failures and bounded changes

- Initial full Python run (`gate-python.log`): **1 failed, 3458 passed, 1 skipped**,
  coverage **97.72%**. Pre-cancelled provisioning launched a child and cleanup could
  mask cancellation with an invalid exit status / process-group `PermissionError`.
  It now refuses before spawn and waits after killing an actively cancelled child.
  Native cancellation and failure tests plus the full gate pass.
- Meaningful missing-path coverage now exercises one-use launcher handshake,
  incompatible/missing runtime recovery, CAS races, registration/default preservation,
  lease failures, context replacement, experiment resume ownership, read-only guards,
  bounded HTTP envelopes and WebSocket resynchronization. No exclusions or threshold
  reductions were introduced.
- Interrupted SQLite backup left its destination connection open on failure.
  Regression first failed, then passed with `contextlib.closing`; full gate passed.
- Registration preserves a still-registered default model when omitted from an
  edit. Legacy layout is explicit setup data and CLI timeout reaches inspection and
  manual run admission; absent inspection timeout remains bounded at 30 seconds.
- Initial full browser run (`gate-browser.log`): **43 passed, 2 failed**. Firefox
  reported bootstrap favicon CSP violations. An inline data favicon and narrowly
  scoped `img-src data:` remove those requests; nonce scripts/default denial remain.
  Console and page error assertions remain enabled. Final full browser gate passed.
- The installed tutorial initially attempted its second run before actor cleanup
  completed. The harness now waits on explicit `busy` admission, without changing
  product scheduling or bypassing owner checks. Its subsequent reconstruction
  failure is a product portability issue, not a harness failure.

### OPEN P1-R1: native reconstruction changes installation metadata

**Requires an explicit independent reviewer/coordinator ruling. Not an accepted
deviation.** Spec §4.3, scenarios 3 and 10, and P1 T3 require reconstruction success
when available. The isolated reconstruction tests use deterministic probes; their
success does not establish native installation portability.

Reproduction used the fresh installed wheel on bigboss, CPython 3.14.3, darwin arm64,
uv 0.13.1. A production starter with a captured, project-relative Simulatte wheel
was locked, provisioned, activated, and executed at full and KPI levels. After the
writer closed, the project/store was copied without `.simulatte/cache`. Native
`EnvironmentPool.reconstruct(evidence)` failed with
`Historical environment reconstruction unavailable: Reconstructed runtime differs from captured environment`.

Both probes have identical runtime facts and package names/versions. The same
captured provisioning spec produces generation
`662c545b389ceca63233db26f236fb9b276ca41575e383a41c8191be06cb9ad4`.
Seven raw distribution `record_hash` values differ: **idna 3.20, simulatte 0.17.0,
tabulate 0.10.0, tqdm 4.70.1, uvicorn 0.54.0, watchfiles 1.3.0, websockets 17.2**.
CSV comparison of their RECORD files identifies exactly these changed entries:

1. Each package's `../../../bin/<console-script>` hash/size changes. The scripts'
   interpreter launcher contains `.../original/.simulatte/cache/environments/<id>/bin/python`
   versus `.../copied/.simulatte/cache/environments/<id>/bin/python`. Simulatte's
   script is 571 versus 569 bytes; the executable body is unchanged.
2. Simulatte's `direct_url.json` points to the captured local wheel under a new
   temporary build directory. The original is `original/.../build-7obaoauz/vendor/`;
   the reproduced path is under `copied/.../build-<random>/vendor/`. `archive_info`
   remains `{}`. The file sizes are 272 versus 270 bytes.
3. Simulatte's `uv_cache.json` has a new installation-source timestamp. Retained
   original: seconds `1792419153`, nanoseconds `143185236`; latest diagnostic rebuild:
   seconds `1792419420`, nanoseconds `104387029`. The remaining cache fields match.
   The file sizes are 127 versus 126 bytes.

Every other RECORD row matches. This shows installation-path/timestamp differences,
**not a changed dependency version or payload manifest**. RECORD comparison is
metadata evidence, not a fresh hash of every installed dependency file. The strict
raw-RECORD identity check intentionally still rejects the rebuilt runtime. No old
request/runtime IDs, probe evidence, dependency manifests or acceptance comparisons
were rewritten or normalized, and no mismatch was treated as verified reproduction.
This was the original candidate boundary. The subsequent blocking review explicitly
authorized the bounded, versioned new-generation implementation described below.

Retained evidence:

- `installed-tutorial.py`, `gate-installed-tutorial.log` (original failed tutorial).
- `installed-study-ma7qcagu/{original,copied}/` (quiescent durable stores/inputs;
  copied model subsequently changed deliberately to test no-import recovery).
- `probe-portability.py`, `portability-probe.log`, `portability-probes.json`
  (expected/actual complete probes; diagnostic wrapper preserves strict comparison).
- `portability-<package>-{original,rebuilt}.json` (exact RECORD, direct URL and uv cache
  content for all seven differing distributions).
- `portability-script-{original,rebuilt}.txt` (exact Simulatte generated scripts).
- `copied-recovery.py`, `gate-copied-recovery.log` (successful history recovery).

Saved full/KPI recordings and names/notes/important flags remain available in
read-only recovery even with a deliberately broken model. That is **history
portability evidence only**; native historical reconstruction remains unavailable
in this case and the installed tutorial is not recorded as passing.

## Measured no-subscriber overhead

`gate-overhead.sh` runs the existing benchmark runner with `--mode none --warmup 1
--repeat 5 --processes 3`, separately for `jobshop10-u90-5k.json` and
`jobshop10-u95-5k.json`. All environments use CPython 3.14.3; common dependency
versions match (`bench-{base,head}-packages.txt`). The head is the installed P1
wheel. The stripped baseline uses the existing `benchmarks/strip_debug.py` copy
with 12 debug statements removed, without changing the released installation.

| Workload | Released 0.12.0 → P1 | Stripped 0.12.0 → P1 |
| --- | --- | --- |
| u90 | 0.659s → 0.491s, **−25.4%, pass** | 0.458s → 0.491s, **+7.2%, pass** |
| u95 | 0.691s → 0.526s, **−23.9%, pass** | 0.489s → 0.526s, **+7.5%, pass** |

The unchanged comparisons use `benchmarks/compare.py --budget 0.03 --noise 0.02`
against the release, and `--budget 0.065 --noise 0.02` against the stripped copy.
Workload hashes and trajectory/count comparability checks passed. Full samples,
IQRs and commands are in `gate-overhead.log`, `overhead.md`, and
`{base,stripped,head}-none-{u90,u95}.json`. These are macOS measurements, not CI
measurements on every supported interpreter.

## Implementation rulings and review focus

The complete plan-specific ledger is retained through review in `progress.md`.
Necessary choices beyond the named file list were:

- Preserve the prepared worktree and supplied initial baseline; no new checkout or
  redundant initial broad test run. Use one persistent implementer and coordinator
  review, overriding per-task agent/reviewer defaults.
- Keep legacy history unassigned without unique source evidence. Read-only schema
  2/3 access does not migrate; live WAL requires its existing shared-memory index.
  Recovery of an unrecovered WAL needs a compatible writer.
- Lock preview is a structured `LockPreviewRequired` containing a CAS edit.
  Environment selection reports restart required; activation occurs only after
  validated launcher handoff. Managed identity captures declared source as well as
  lock/config/platform inputs, so explicit preparation after source edits can
  create another generation. SQL for generations remains with `EnvironmentPool`.
- Reuse local cross-platform process containment; leave remote POSIX policy intact.
  Reuse one shared coordinator with scoped model/store facades. Headless callers
  keep their prior supersession contract; workspace admission is explicit.
  Experiment resume requires the captured generation in its matching interpreter.
- Authenticated project transport delegates to a data-only service; no coordinator
  before explicit preparation. Existing watcher membership is reused. CLI options
  cross the one-use handoff as data; no writer spans runtime handoff.
- Keep saved artifact paths relative to the artifact directory. Route model layout
  writes through bounded authenticated HTTP, preserving layout/revision checks;
  explicit Save can create absent registered layout parents.
- Reuse recording/playback/layout/export/experiment components behind workspace
  adapters. Add `experiments-page.tsx` so existing experiment controls remain
  reachable. Saved P2 analysis and P4 presentation features remain out of scope.
- Explicit non-Git registration previews source membership and minimal package
  metadata when absent. Captured local wheel dependencies are contained bundle
  files, not import roots; other local dependency artifact types are not newly
  supported. No silent Git initialization.
- Build wheel/assets before browser fixtures; replace the obsolete implicit-start
  installed smoke with explicit project journeys. Older live tests now issue
  explicit Run. Old smoke-only deep canvas interactions are not identical coverage;
  existing viewer/layout tests and standalone smoke remain.
- Keep whitespace density scoped to the workspace. Setup appears before empty
  statistics/history, and event filters wrap with complete labels. Davide's actual
  UI acceptance is recorded separately from technical acceptance.
- T7 includes bounded cancellation, backup-close, default-model and CLI-option
  corrections justified by gate failures/regressions; no runtime identity redesign.
  Update the additional experiments guide because its old deletion advice would
  lose persistent history. Coverage additions exercise material P1 failure paths.
- Do not invoke task completion/ledger cleanup while P1-R1 is open. This latest
  explicit instruction supersedes automatic task-done/final-review skill steps.

## Remaining gate status

Measured overhead passed on both CI workloads; independent package review is
pending the coordinator. P1-R1 remains OPEN, T7's integration
acceptance checkboxes remain unchecked, and P1 is not declared complete.

Native Windows/Linux/PyPy, actual Safari and native Firefox application acceptance
are not established by these macOS Playwright runs. Existing CI lanes remain
required. The eva Safari gallery acceptance concerns actual UI screenshots only.
No merge, push, release or production publishing has been performed.

## Revised review pass: R1 implementation evidence (review remains open)

Coordinator accepted the versioned design and all four obligations in Cortex
`session-16f6fa50-3592-4878-bdd5-41b42ffccf61`. New managed generations select
`installation-v1`, generation domain `local-environment-v2`, execution kind
`local-installation-v1`, and dependency domain `installation-dependencies-v1`.
Old generation/request domains, `describe_runtime`, raw verifier and remote `uv`
contract remain unchanged. No existing request or runtime is rewritten.

Actual file bytes and canonical installation paths determine the new identity.
RECORD is the enumerating manifest, not recursively hashed generated serialization;
unhashed generated `__pycache__/*.pyc` entries alone may be omitted. Duplicate or
escaping paths, missing payload and ambiguous distributions refuse. Local file URLs
normalize only through exact bindings made from verified captured files before
provisioning; these bindings survive staging deletion and reach the independent
child verifier. Unknown external local URLs refuse. Other direct-URL provenance
and unknown uv-cache fields remain exact; only the recognized timestamp is omitted.

Generated POSIX shebang/long-path trampolines retain their body. Windows uv0.13.1
PE resources `UV_TRAMPOLINE_KIND`, `UV_PYTHON_PATH`, `UV_SCRIPT_DATA` and the archived
`__main__.py` selector are parsed; console/GUI mode, code, body and unrelated
resources remain exact. Bounds, derived sizes, nonzero PE checksums and ZIP CRC
are checked. Only selectors and derived layout/CRC bookkeeping normalize; unknown
formats refuse. Parser fixtures cover path lengths, spaces, non-ASCII and tampering.
The implementation follows uv's pinned [builder](https://github.com/astral-sh/uv/blob/0.13.1/crates/uv-trampoline-builder/src/lib.rs)
and [wheel installer](https://github.com/astral-sh/uv/blob/0.13.1/crates/uv-install-wheel/src/wheel.rs).

Capability admission occurs before inspect/run commands, including reused workers,
experiments and replay. Request/evidence tags must agree; old/new replay versions
never silently bridge. Reconstruction requests the captured exact interpreter for
new generations and retains strict comparison of all interpreter facts.

`review-r1-native-2.log`: fresh-wheel macOS CPython3.14.3/uv0.13.1 proof passed
preparation, stopped-project copy without cache, reconstruction with unchanged
selection and immutable capture/request, independent child run and verified replay,
two-configuration experiment, console/GUI script execution, payload/entrypoint-body
tamper refusal, and pre-import refusal by a reused incapable real worker.
`reconstruction-v2-native-2/` retains original/rebuilt raw probes and base64 raw
RECORD/direct_url/uv_cache/entrypoint files, original request and phase results.
The first native proof (`review-r1-native.log`, `reconstruction-v2-native-1/`)
failed at experiment expansion after reconstruction/run/replay; this exposed and
fixed the new-kind-to-local-worker mapping. Both failed and successful evidence
remain. New macOS/Windows CI reconstruction exercises were added; native Windows
results have not been observed locally and are not claimed.

Targeted compatibility checks: `review-r1-experiment-final.log` **114 passed**;
`review-r1-compatibility-final.log` **84 passed**; targeted coverage/provisioning
`review-r1-targeted-coverage.log` **105 passed**. These are scoped checks, not the
full99% package gate. R1 remains subject to independent implementation recheck;
M1 and refreshed package gates are still pending at this checkpoint.

Follow-up final R1 proof: `review-r1-native-4.log` and
`reconstruction-v2-native-4/` pass the same complete fresh-wheel journey after
exact-interpreter reconstruction selection. `review-r1-native-3.log` preserves a
proof-harness failure: manually starting an idle actor's worker raced its heartbeat
reader for the hello. The harness now completes the actual handshake before
publishing the reused worker. Product admission was not relaxed. Exact-interpreter
regression failed before the fix and the environment suite then passed10 tests;
Ruff and `ty check src` pass. Native-4 is the latest R1 portability proof.
