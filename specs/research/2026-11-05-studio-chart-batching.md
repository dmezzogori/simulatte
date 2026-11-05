# Studio KPI chart batching

The [15-minute stabilization gate](2026-11-05-studio-live-stabilization.md)
passed, but preparing several charts still fetched and decoded the same KPI
records once per series. This follow-up compares the prior viewer at
`3ae758903e1337bcf490ced237754e7a2e69274e` with a viewer that makes one scan
per refresh.

The reader skips records without a selected key and routes samples from
each matching record to separate chart accumulators. Each series keeps
its cursor validation, same-time sample selection, held values, gap
handling, and display envelope. The scan holds one decoded record and
bounded accumulators for at most eight series, with up to 4,000 chart
points each. Live refreshes keep the current complete panel visible and
abort obsolete work as before. Sample lookup, trace format, and execution
are unchanged.

## Chart query

We used three successful recordings from the previous macOS native study,
with 1,000 samples per series for Flow and Mixed and 4,096 for Burst. For
each, we selected one, two, four, and eight charts. All 12 selections had
identical point counts and SHA-256 digests of the full chart data, including
NaN gap markers. A single chart read the same number of bytes in both
versions. With eight charts:

| Recording | Source reads | Bytes read | Median query time |
|---|---:|---:|---:|
| Flow | 512 → 64 | 2,834,320 → 354,290 | 33.7 → 4.4 ms |
| Mixed | 496 → 62 | 2,834,152 → 354,269 | 35.0 → 4.4 ms |
| Burst | 512 → 64 | 11,602,192 → 1,450,274 | 95.5 → 15.8 ms |

The 87.5% reduction counts only the chart query. Times are from an
in-memory Node source, after one warmup and five measured queries per
selection. They show the cost of the repeated scan without HTTP latency.

## Packaged viewer

The baseline and candidate wheels contain 153 byte-identical Python files.
We served the same Burst recording through installed wheels on macOS and
Linux, with 5 ms added to each range GET in both versions. Chromium,
Firefox, and WebKit ran on macOS, with SSH forwarding for the Linux
servers. There were three paired fresh-context opens for each browser
and server, 36 opens total.

Every open displayed the same eight charts. All started range reads
completed with exactly their declared bytes, with no viewer alerts or
page errors. Opening the viewer, including indexing and initial playback,
went from **712 to 264 range reads (62.9% fewer)** and **14,982,303 to
4,830,385 bytes (67.8% fewer)** in every pair. The 448 reads and 10,151,918
bytes saved match the savings in the Burst chart query.

| Server | Chromium | Firefox | WebKit |
|---|---:|---:|---:|
| macOS | 5.40 → 2.37 s | 6.43 → 2.41 s | 6.47 → 2.36 s |
| Linux | 5.90 → 2.38 s | 6.97 → 2.94 s | 6.92 → 2.90 s |

Times are medians from navigation to the viewer's paused status under
the study's latency. Savings in a continuously updating live session
depend on the number of selected series, prefix sizes, and refresh rate.
This gate measures fewer reads per refresh; it does not measure engine
speed or sustained memory behavior.

## Verification and next gate

Regression coverage checks shared and unrelated records, independent
series behavior, ordering across records, and cancellation. The first
browser suite run found that two live tests could hold a read of the
initial playback frame after the charts had already rendered. The tests
now wait for playback to finish loading before delaying the next
refresh. The corrected slow-refresh and revocation tests pass 18
repeated trials across the three browsers.

The final suite passes 458 TypeScript unit tests, 78 browser tests, and
51 focused Python tests for Studio packaging, native observation, live
execution, and HTTP serving. Type checking, lint for the study harness,
and the documentation build pass.

The final rebuild after that test-only change has the same runnable
assets and Python files as the measured candidate. Only the build
stamp, asset manifest, and wheel record differ. Fresh installations on
both hosts verify all 14 packaged assets against their manifest.

The longer Studio gate can now use batching and track browser heap,
server RSS, persisted storage, and chart visibility under repeated run
and reconnect cycles. The multi-hour soak has not been run.

The [reproduction instructions and evidence](../../prototypes/studio_chart_batch/README.md)
include the wheel and recording hashes, all chart query digests, and all
36 viewer openings.
