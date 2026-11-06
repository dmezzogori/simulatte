# Studio endurance gate after KPI chart batching

**The gate did not pass.** The macOS session completed the planned duration
but lost two worker attempts. The Linux browser driver stopped at about
114 minutes. Its failure caused the supervisor to skip both audits; the
recorded reads, resource samples, and result stores were recovered on
November 6.

The sessions used the installed viewer at `fa96cbd`, the verified native
companions from the [15-minute gate](2026-11-05-studio-live-stabilization.md),
and the same Flow, Mixed, and Burst workloads in full and bounded retention.
Chromium stayed on one document per host; Firefox and WebKit reloaded.
All three ran on macOS, with one set connected to the Linux server via SSH.
There was no added HTTP latency.

| Observation | macOS server | Linux server |
|---|---:|---:|
| Browser duration | 120m 27.6s | 114m 13.1s sampled, interrupted |
| Successful / cancelled / injected failure / lost | 186 / 29 / 26 / 2 | 223 / 36 / 32 / 0 |
| Reload / reconnect / seek-follow | 79 / 114 / 98 | 74 / 110 / 86 |
| Live samples showing all eight charts | 90.5% | 87.0% |
| Byte-exact application range reads | 1,354,070 | 1,026,833 |
| Post-GC Chromium heap, early → late median | 15.11 → 19.08 MiB | 14.58 → 18.52 MiB |
| Server RSS, early → late median | 81.81 → 49.86 MiB | 108.01 → 116.41 MiB |
| Store size, first → last in-session sample | 15.46 → 462.03 MiB | 15.47 → 531.86 MiB |

The Linux duration is the conservative span from the first to the last
DOM sample; the normal completion record was never written. The memory
comparison uses minutes 10–20 and the last full ten-minute window
(110–120 on macOS, 100–110 on Linux). None of the predefined sustained
growth thresholds fired. The final macOS window had 87 server samples
against the minimum of 96, so the resource sampling check did not pass.
Store size grew as results were retained. These observations do not
establish leak freedom.

The stopped-store audit checked all 409 browser-observed successful traces,
including their KPI endpoints and control state. Seven shared
configurations matched across hosts. None of the 125 observed unsuccessful
attempts published a result. The installed HTTP route returned 410 for all
125 in a fresh ASGI process against the stopped stores. This retrospective
check is distinct from checking every prefix on the still-running server.

There were no partial chart panels, sampled trace alerts, or incomplete
successful server responses. The reconciliation accounts for 124 browser
console diagnostics from confirmed revocation. Seven other WebKit
diagnostics fell within reloads, but the logs did not establish the
old-document read and abort for each, so they remain outside the strict
transport pass.

## What stopped the study

The Linux driver read the timeline maximum and later filled a midpoint
calculated from it. The viewer could reset the timeline to `[0, 0]`
between those operations; Playwright then rejected the stale midpoint as
`Malformed value`. Setting up that transition reproduced the error on
all three engines. The driver now checks the current bounds and sends the
input in one browser operation. The replacement skips collapsed and
disabled ranges and moves a valid Studio playback frame in all three
browsers. Interrupted sessions also save partial summaries, and the
supervisor collects the other host's audit if a browser driver fails.
These changes are confined to the study harness.

The two macOS losses are separate from the seek error. Both occurred on
ordinary bounded native runs (one Mixed, one Flow), and both stores report
`Worker heartbeat lost`. They coincide with the two longest gaps in
macOS server sampling, 47 and 40 seconds. Later runs completed, but the
Chromium client connected to Linux continued taking samples from the
same Mac during both gaps. The logs cannot distinguish a stalled
heartbeat sender, transport, or receiver. A targeted macOS probe that
times those stages is the next useful check before repeating the gate.

The [recovered evidence](../../prototypes/studio_stabilization/results/endurance-recovery.json)
retains the host checks, wheel identities, resource windows, and log and
publication audit hashes.
