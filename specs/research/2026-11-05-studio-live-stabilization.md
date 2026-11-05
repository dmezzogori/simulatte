# Studio stabilization: live trace reads and chart refresh

Base: `196fcc97168163a26f7ab89c4c3f46701761a10c`. This follows the failed Linux
browser gate in the [representative-model study](2026-11-04-native-stabilization.md).
The native engine, ABI, workloads and host-specific native wheels are unchanged.

## Range-read failure

The experiment pauses a range response after its `206` headers, cancels the
active run through Studio, then lets the response resume. On the baseline,
the server's authorization check raises `StaleAttempt` before sending any of
the promised body. Chromium reports `ERR_CONTENT_LENGTH_MISMATCH` and WebKit
reports a lost network connection, on both macOS and Linux servers. This
reproduces both earlier error signatures. The earlier soak did not capture
request IDs, so those particular historical reads cannot be identified.

Studio now prepares and verifies responses of up to 1 MiB before sending their
headers. If revocation wins during preparation, the request can return HTTP
410. A response prepared while authorized can finish with exactly its
advertised bytes, and new requests to the revoked prefix still return 410.
Larger responses remain streamed and fail closed on revocation. The candidate
passes all four forced cancellation cases byte-for-byte, with the original
prefix digest and no browser errors.

## Live charts and attempt lifecycle

A metadata-only first prefix used to leave the default KPI selection empty
for the entire run. When series were selected, each new prefix cleared the
plots before loading replacements, which could leave a partial panel under
continuous updates. The viewer now chooses defaults once samples arrive,
prepares a complete chart set while the current set stays visible, and keeps
only the newest pending refresh. Selecting different series, changing the
source or invalidating the attempt aborts obsolete work. Explicitly clearing
series is respected.

In 100-second installed trials with 25 ms added to each prefix request and a
4,096-sample budget, the baseline never showed all eight charts in a live
sample. The candidate did in 700/793 samples on macOS (88.3%) and 695/793 on
Linux (87.6%), across Chromium, Firefox and WebKit. No candidate sample showed
a partial chart set. These are panel samples, not an engine-speed comparison.

The first long candidate run found a second ordering issue: an expected
HTTP 410 can reach the browser before the websocket announces a failed
attempt. The generic trace error then briefly tells the user to reopen the
file. The viewer now recognizes revocation, retires the live view, and uses the
run-status message for the outcome. Other read failures still surface.
The viewer also disposes pending work when a page leaves without being cached.

## Final installed gate

The final core wheel was installed afresh on both hosts, with the same native
companions as the baseline. Each host ran a 15-minute session with Chromium,
Firefox and WebKit clients, no added delay, and the three representative
workloads in both retention modes.

| Server | Duration | Succeeded | Cancelled | Injected failures | Reload / reconnect / seek-follow |
|---|---:|---:|---:|---:|---:|
| macOS | 900.70 s | 25 | 3 | 3 | 9 / 14 / 14 |
| Linux | 900.90 s | 30 | 4 | 4 | 9 / 14 / 11 |

All 55 successful traces pass structural checks and contain the planned eight
series, including the warmup and horizon endpoints and final KPI values.
Seven matching workload configurations also agree across hosts on control and
KPIs. The 14 cancelled or failed attempts publish no result; all 14 prefixes
return 410. The visible history reaches its 20-result cap.

The application reader records **668,483 exact range bodies**. The server
records no incomplete successful response. Neither original truncation
signature recurs. There are 20 browser console messages for confirmed HTTP
410 responses and three WebKit diagnostics while a deliberately reloaded
document is leaving. They are retained and tied to the revoked attempt or
the old document's fetch scope, respectively. The runner sometimes reports
`requestfailed` even after the application reader has consumed the exact
body; those events are reconciled separately. There are no unexplained
browser/read failures and no sampled user-facing trace alerts.

Eight charts are visible in 89.3% of macOS live samples and 86.4% of Linux live
samples, with zero partial panels. The empty samples include first-sample
loading and view transitions. Chromium post-GC heap peaks at 15.9 MiB on both
connections; first/last ten-sample medians are 12.2/13.8 MiB for macOS and
11.8/13.6 MiB for Linux. Server RSS peaks at 92.1 and 103.1 MiB respectively.
Persisted store size grows from 15.6 to 75.2 MiB and from 15.9 to 84.0 MiB.
These measurements do not establish multi-hour leak freedom.

The final Python suite rerun passes 3,772 tests, with one skip and 99.02%
coverage. Its first run hit a busy replay slot in unchanged coordinator code;
the focused replay/Studio rerun and full retry both pass. TypeScript passes
455 unit tests, type checking and 78 browser tests across all three engines.
Packaging, Ruff, Python type checking and the documentation build pass.

The focused stabilization gate passes. Before a substantially longer soak,
a useful next optimization is to reduce repeated reads of the same KPI
records when preparing several charts. No release or deployment baseline is
selected.

## Evidence

The installed trial harness, request correlation and publication audit are in
`prototypes/studio_stabilization/`. Its summary distinguishes browser body reads,
ASGI byte counts, expected revocation and aborts, and runner diagnostics that
can disagree with a completed body. Browser clients run on bigboss against
macOS and Linux servers; this is not a Linux desktop browser matrix.
