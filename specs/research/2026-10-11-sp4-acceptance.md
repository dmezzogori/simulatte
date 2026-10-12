# SP4 local execution and Studio acceptance

Target: Simulatte 0.16, unreleased. Feature integration: `020857f`; acceptance
corrections: guardian shutdown `4ed9fe8`, equivalent-layout canvas publication
`a43e868`. This report distinguishes
measured results from remaining release gates. It does not authorize merge,
tagging or publication. D71–D91 record design decisions and explicit amendments.

## Verification status

| Area | Evidence / remaining check |
| --- | --- |
| Full Python gate | Final macOS CPython 3.14.1: **3,002 passed, one native Windows skip, 99.03% branch coverage**, 264.77 s after the final canvas asset rebuild. Threshold unchanged. |
| Linux CPython 3.11–3.15 | **3,001 passed, two skips each**: 3.11.16 (99.03%, 550.70 s), 3.12.12 (99.02%, 508.05 s), 3.13.9 (99.03%, 487.96 s), 3.14.0 (99.01%, 261.05 s), 3.15.0 (99.01%, 254.60 s). |
| macOS oldest/latest | Post-guardian installed-wheel CPython 3.11.10 and 3.15.0: **58 execution/server tests each**, 42.25 s / 41.03 s. Earlier broader 3.11 run: 686 passed, one Windows skip. |
| Linux PyPy 3.11 | **2,883 passed, 23 expected skips**, 343.08 s, PyPy 3.11.16 / 8.0.0 core-only worker/core/layout/intralogistics. One earlier startup failure remains unreproduced. No web-extra promise. |
| Windows | Native Job Object/process/server lane added to CI; not executed in this session. Mock API coverage is not native containment evidence. |
| Browser | Final installed-wheel Chromium (twice), WebKit and Linux Firefox Studio flows: zero page errors. Final read-only Chromium/WebKit regression: **20 passed**, 48.4 s. Actual Safari SP4 checklist remains open. |
| Static checks / docs | Ruff lint/format, `ty check src`, TS typecheck/build and **399 TS tests** pass. Docs build reports no issues. |
| Packaging | Final core/Studio wheels pass: no core web imports, plain/managed examples, reproduce, old/new trace view, offline HTML and WebKit editor/presentation flow. Offline sdist→wheel without Node/network passed. |
| Post-SP3 CI | Final `main` f131e0fb5a3f7af1e0cf46d4f7abf3992cb60f9d: [CI 38170343729](https://github.com/dmezzogori/simulatte/actions/runs/38170343729), [docs 38170343710](https://github.com/dmezzogori/simulatte/actions/runs/38170343710), [publish 38170362338](https://github.com/dmezzogori/simulatte/actions/runs/38170362338) succeeded. SP4 CI has not been run remotely. |

Initial Linux attempts were interrupted after finding macOS AppleDouble archive
metadata (`._*.py`) in the owned temporary checkout. These files were removed
before restarting; no product source was changed during either run. The initial
PyPy environment also lacked test-only jsonschema/hatchling. Optional watcher/auth
tests now skip when their extra is absent; pure framing/manifest tests still run.
A collision fixture now creates both case variants on case-sensitive filesystems.
Concurrent full Linux runs exposed two additional fixture races: control frames
could drain into the socket during queue-cap assertions, and a revision test
waited 5 s for a preparation explicitly allowed 10 s. Queue admission is now
checked while holding the dequeue condition, and the fixture uses the actual
10 s preparation budget. Cancellation assertions exceeded 5 s in some runs. A direct native probe found
the actual defect: the guardian's daemon lifeline reader held buffered stdin's
lock during interpreter finalization. The child exited correctly, but its
guardian aborted with `_enter_buffered_busy` (return code -6), adding 2.119 s
on Linux CPython 3.15. Using the raw stdin stream removes that shutdown lock.
The same native probe then returned guardian code 0, empty stderr and 24.8 ms
termination. Regression assertions for guardian exit code/stderr failed in both
normal/cancel cases before the fix on macOS, then passed on macOS and Linux.
The 5 s cancellation assertion is unchanged; the post-fix CPython gates pass.
Python documents the [raw/buffered stream distinction](https://docs.python.org/3/library/io.html)
and [daemon-thread shutdown semantics](https://docs.python.org/3/library/threading.html#thread-objects).
This correction supersedes the earlier suspicion that contention alone explained
the failures. Old full-run results remain failed evidence, not acceptance passes.

Final Chromium acceptance reproduced a real canvas race. Pointer and component
state diagnostics showed a valid in-progress drag being cleared when the completed
run supplied an equivalent layout bundle. The editor now keys its canvas basis by
effective resolved content and target classification, excluding run provenance and
geometry shadowed by the draft. Equivalent publication preserves the gesture;
changed geometry, draft/revision or editing permission still invalidate it. A unit
regression and rebuilt-wheel Chromium/WebKit/Firefox flows pass.

A PyPy run exposed a pre-existing fixture assumption that the final published
record must be KPI. Full recording can append the same event's observer record
after that KPI. The assertion now checks INITIAL-before-KPI and the readable sample
before close, retaining the actual latency contract. An earlier schema-refresh
attempt failed with JSON decoding `Unexpected 'G'`; its cause is still unconfirmed.
The isolated refresh, 30 further complete schema-refresh scenarios, 300 native
guardian launches, 200,000 status-frame decodes under GC pressure and two further
full execution-suite passes did not reproduce it (one full run failed only the
KPI fixture above; the final whole suite passes).
This single failure remains recorded rather than attributed to contention or an
interpreter defect without evidence.

The first Python 3.15 setup selected an installed alpha; an ephemeral current uv
installed explicit 3.15.0 instead. No global uv upgrade was performed. uv documents
its [bundled download inventory](https://docs.astral.sh/uv/reference/cli/) and
[pre-release selection](https://docs.astral.sh/uv/concepts/python-versions/).

## Failure-path map

Test names below are relative to `tests/`. They cover the focused design §13
contracts; injected failures and actual process failures are identified separately.

| Contract | Automated evidence |
| --- | --- |
| Framing, noise, malformed/oversize/version/sequence | `execution/test_protocol.py::test_every_fragment_boundary_and_concatenation`, `test_oversize_truncation_unknown_version_and_sequence_fail`, `test_raw_bad_envelope_is_validated_before_dispatch` |
| Import failure/hang, infinite CPU, native output flood | `execution/test_supervisor.py::test_import_failures_and_uncooperative_cancellation` (exception/exit/cpu/native_flood/timeout, real child processes); bounded diagnostic tails and progress stall tests in the same file |
| Startup hooks / lazy editable escape | `execution/test_imports.py::test_bootstrap_skips_executable_startup_hooks`, `test_lazy_import_escape_is_blocked_before_execution`, `test_import_time_declared_data_read_is_rejected` |
| Ready barrier cancellation | `execution/test_driver.py::test_cancel_at_ready_does_not_run_processes` |
| Child/grandchild retaining stdout, supervisor death | `execution/test_process_cleanup.py::test_ordinary_descendants_reaped_on_exit_or_cancel`, `test_supervisor_hard_death_cleans_cpu_bound_child`; real PID checks and EOF, POSIX-only |
| Delayed/lost/duplicate ACK, full window | `execution/test_transfer.py::test_full_window_waits_for_credit_and_cancel_needs_no_ack`, `test_partial_durable_credit_keeps_full_spool_alive_without_reclaim`, `test_wrong_or_out_of_order_ack_does_not_release_credit` |
| Full spool / no credit / disk failure | `execution/test_spool.py::test_blocked_spool_can_cancel_or_expire_without_more_credit`, reservation/rename/fdopen/unlink fault tests; real default-limit measurements below |
| Duplicate/out-of-order fragment | `execution/test_receipts.py` ordered durable receipts, duplicate replay, changed content and active-attempt checks |
| Publication crash boundaries | `execution/test_publication.py::test_process_crash_never_adopts_an_unindexed_result`: verified/staged/rename-before-sync/renamed/before-commit/in-transaction/committed, now both immediate exit and actual POSIX SIGKILL (14 cases passed) |
| Wrong fingerprint / missing cleanup | `execution/test_publication.py::test_crc_valid_but_semantically_wrong_trace_is_rejected`, `test_publication_requires_execution_exit_and_cleanup`, `test_execution_provenance_is_bound_to_published_result` |
| Late result after replacement | `execution/test_coordinator.py::test_cancellation_and_supersession_cannot_publish_old_attempt`, publication revoke-during-finalization cases |
| Two-tab / external write / ABA | `execution/test_inputs.py` revision/file compare and unreadable-index cases; real Studio smoke preserves parameter drafts and rejects old revision writes |
| Rename/delete/missed watcher notification | `core/test_studio_watcher.py::test_native_atomic_save_and_shutdown` (native/polling), `test_periodic_rescan_recovers_missed_native_notification`, `test_manifest_scan_tracks_declared_data_and_same_mtime_source` |
| Invalid source/layout and recovery | `core/test_studio_lifecycle.py::test_invalid_initial_source_recovers_without_restarting_server`; live tests for initial invalid-layout repair; monitor last-result retention |
| Slow authenticated tab / blocked send | `core/test_studio_connections.py::test_slow_tab_closes_for_resync_and_shutdown_uses_going_away`, `test_slow_socket_closes_even_when_network_send_is_blocked` |
| Cookie-only / wrong Host/Origin | `core/test_studio_connections.py::test_cookie_alone_bad_origin_host_and_secret`; SP3 server capability/range regressions |
| Snapshot edits/races | `execution/test_snapshot.py::test_capture_retries_observed_concurrent_edits`, `test_atomic_replace_between_stat_and_open_is_rejected`, `test_inputs_share_byte_quota_and_edits_after_copy_fail` |
| Finite boundary/drain failure | `core/test_stopping.py::test_time_cutoff_rejects_arrival_at_boundary`, `test_safety_boundary_completion_succeeds`, `test_incomplete_population_is_not_success` |
| Stale browser reads / live invalidation | `studio/packages/trace/test/live.test.ts`, viewer `live-session.test.ts` and `studio-playback.test.ts`: hash/attempt/generation fences, delayed reads, cancel/dispose and retained generation caches |

## Cold execution measurements

One fresh interpreter per sample; 100 or 20,000 deterministic domain events.
`benchmarks/studio_execution.py` includes immutable capture, fresh inspection and
execution, publication and close. Ready is polled at 50 ms; inspection includes
recapture and installed-distribution discovery. These are single observations,
not warmed throughput claims or comparisons of interpreter speed: the dependency
inventories differ. JSON records include exact platform, trace size, RSS and
heartbeat samples in [sp4-data](sp4-data/).

| Host / interpreter | Events | Snapshot | Inspection | Submit→ready | Ready→publication | Close | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| eva CPython 3.14.1 | 100 | 0.0029 s | 0.5419 s | 0.7093 s | 0.2230 s | 0.0936 s | 1.7596 s |
| eva CPython 3.14.1 | 20000 | 0.0033 s | 0.5594 s | 0.7794 s | 0.4886 s | 0.1499 s | 2.1705 s |
| strangelove CPython 3.14.0 | 100 | 0.0014 s | 0.5380 s | 0.5968 s | 0.1473 s | 0.1007 s | 1.4977 s |
| strangelove CPython 3.14.0 | 20000 | 0.0016 s | 0.5201 s | 0.5977 s | 0.7926 s | 0.1028 s | 2.1306 s |
| strangelove PyPy 3.11.16 | 100 | 0.0100 s | 1.0658 s | 1.1314 s | 0.3384 s | 0.1079 s | 3.2833 s |
| strangelove PyPy 3.11.16 | 20000 | 0.0097 s | 1.0496 s | 1.1241 s | 1.0312 s | 0.1126 s | 3.9272 s |

Observed platforms were macOS 27.0.1 arm64 and Linux 7.0.0-38-generic x86_64,
glibc 2.43. Long-run parent high-water RSS: 69.1 MB macOS CPython, 59.9 MB Linux
CPython, 216.3 MB PyPy. Samples were taken while other verification work
was active; these are not idle-host speed comparisons. These are OS process high-water values, not decoded-cache
estimates. No warm spare is introduced or promised.

## Bounded spooling and control

`benchmarks/studio_backpressure.py` uses normal captured preparation and a real
local supervisor/execution child, writes a temporary 320 MiB artifact and withholds
artifact ACKs while continuing to drain control. On eva CPython 3.14.1:

| Run | Observed spool bytes / files | Heartbeat max gap | Terminal / cleanup |
| --- | --- | --- | --- |
| Cancel saturated worker | 268,435,456 / 257 | 1.112 s | cancelled in .188 s; PID 45014 absent after terminal |
| Wait for no-ACK deadline | 268,435,456 / 257 | 1.092 s | `disk_full` in 60.452 s from first withheld fragment; PID 45137 absent |

The artifact path sends 1 MiB segments, so only 1 MiB was outstanding in this
workload. A separate actual-quota probe filled 1,024 one-byte spool files and
blocked the next reservation, then cancelled it; a 17 MiB segment saturated the
sender at exactly 16,777,216 unacknowledged bytes. Existing tests cover receiver
credit and cancellation independently. No disk was filled to the filesystem's
physical capacity; ENOSPC/EDQUOT paths are injected. The 60 s no-progress timeout
is measured with the default clock, not accelerated mock time.

## Large live reader and renderer

Same SP3 fixture bytes, macOS Chromium 156.0.8078.4. `studio/bench?mode=live`
extends INITIAL, INDEX batches and footer with incremental hashes, asserts no
old-prefix scanning, prepares each new edge, then runs the SP3 seek/frame workload.

| Measurement | 50,000 jobs / 10 servers | 1,000 AGVs / 50 lanes |
| --- | ---: | ---: |
| Trace bytes | 83,828,844 | 657,444 |
| Prefix generations | 21 | 2 |
| Longest extension + edge preparation | 174.8 ms | 68.1 ms |
| First-seek p50 / p95 | 44.2 / 72.3 ms | 4.7 / 6.9 ms |
| Repeated-seek p50 / p95 | .7 / 2.6 ms | 4.7 / 6.5 ms |
| Scene-only frame p95 | 6.5 ms | 5.6 ms |
| Peak retained decoded cache | 17,794,788 bytes / 4 chunks | 0 chunks (INITIAL motion fixture) |
| Longest observed task, startup included | 494 ms | 1,422 ms |
| Sampled peak renderer RSS | 631,816,192 bytes | 390,610,944 bytes |

No browser page errors. Cache remains under 128 MiB; RSS includes engine/native
allocations and is not that cache limit. Incremental hashing plus record validation
can read new bytes more than once; the assertion concerns revisiting old prefixes.
These ingestion measurements exclude server/network scheduling. A separate
installed-wheel Chromium measurement (`benchmarks/studio_latency.mjs`) timestamps
durable receiver returns and the next animation frame after displayed-time
updates on the same host. Follow was explicitly enabled; bootstrap excluded.
The post-guardian run measured a **135.8 ms maximum** across 15 updates.
Earlier measurements were 180.8 ms while verification was active and 206.6 ms
on an idle host. All had zero page errors and met the ≤2 s target. These are not browser saturation tests.
Raw samples are retained in `sp4-data/task14-live-latency*.json`.
Frame-proportional React commits remain covered by the
read-only browser regression and imperative live adapter unit tests.

## No-subscriber gate

The unchanged `benchmarks/run.py` mode `none`, both checked-in u90/u95 workloads,
one warm-up, five measured runs in each of three fresh processes, CPython 3.14.1.
Installed branch wheel versus released 0.12.0 and its debug-stripped copy; matching
trajectory hashes required. All four comparisons passed the unchanged +5% released
and +8.5% stripped limits. u95 stripped was +8.2%, close to its accepted limit.
Raw JSON is retained in `sp4-data/g3-*.json`. Linux PyPy 3.11.16 also passes all four comparisons with its unchanged 10%
limit, eight warm-ups, five runs in three processes: u90 −33.6% / −7.5%,
u95 −28.1% / −9.2% against released / stripped. The PyPy baseline used the
benchmark's headless runtime dependencies and a no-deps 0.12.0 install because
unused plotting dependencies lacked wheels/system JPEG headers. Workload code,
trajectory hashes and budgets were unchanged; no report-only waiver.

## Actual Safari manual release checklist — outstanding

Use an installed wheel and actual Safari, not Playwright WebKit. Record OS/browser
versions, run ids, observed outcomes and any failures:

- One-use bootstrap, authenticated reload and stale/new-tab access rejection.
- Automatic/default and required-parameter startup; run and cooperative cancel.
- Cancel CPU-bound/blocked-spool work; confirm owned child/descendants gone.
- Physical/source rerun, presentation-only update without a new attempt.
- Two-tab stale save rejection with retained draft.
- Live/manual/Follow playback, invalidation and live-to-complete transition.
- Pinned MP4/PNG export cancellation/cleanup and standalone offline HTML.

Safari driver availability alone is not evidence. Prior SP3 Safari export checks
remain historical regression context, not SP4 acceptance.

## Distribution and readiness

D91 preserves raw request identity including `layout_revision`. A fresh-store
CLI/Studio comparison observed identical captured content/options/runtime and
semantic fingerprints, but revisions 3/4 and therefore distinct raw request ids.
Exact reproduction reuses the stored request. The automated regression records
this distinction; removing revision from hashing would silently change the contract.

Final installed-wheel CLI/Studio content/fingerprint comparison passes. Core
wheel has no Starlette/Uvicorn/watchfiles/websockets; plain script, horizon,
finite-count and long-interarrival cutoff examples verify, and reproduction
retains request identity with a new attempt. The finite example explicitly
abandons a pending arrival when population closure fires. Installed read-only
viewer opens old and execution-v1 recordings, reloads and exports offline HTML
with zero network requests/page errors. Fresh wheel WebKit exercises the complete
parameter/layout/presentation flow with zero page errors.

The initial sdist incorrectly picked up locally ignored agent state containing a
venv. Explicit `.superpowers/` and `.simulatte/` exclusions fix that; no package
runtime file was changed. The corrected sdist contains 182 members and excludes
specs/plans/source frontend/local run state. A wheel built with Node absent from
PATH and socket connections forbidden is 1,187,931 bytes; hashes are recorded in
`sp4-data/packaging.json`. Existing tamper/freshness tests passed in the full suite.
Version metadata remains 0.15.0 development; no 0.16 release/tag is created.

The final CPython and PyPy gates pass. The earlier unreproduced PyPy startup
failure remains a tracked investigation: if it recurs, capture the guardian's
incoming bytes and peer/startup sequence before assigning a cause or changing
retry semantics. Native Windows CI, actual Safari SP4 checks and remote SP4 CI
remain release gates. The Linux full matrix verifies Python runtime `4ed9fe8`;
subsequent `a43e868` changes only the frontend, verified by the final macOS full
gate and installed-wheel Chromium/WebKit/Linux Firefox flows.
SP5 experiments, statistical comparison and SSH workers remain excluded. Plain
scripts, Runner, read-only view and existing exports retain their paths.
