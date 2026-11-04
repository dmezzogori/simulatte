# Native stabilization: representative models and longer Studio sessions

Base: signed local commit `bdeb5cf003b8a6bc6b5902bef59ce4f2457b3b4f`.
The study uses the reviewed step-4 core wheel and unchanged capacity companion
wheels. Native remains an optional experimental v3 backend; no release or portable
deployment baseline is selected.

## Models and correctness gates

| Model | Capacities | ConWIP | Arrival and control behavior |
|---|---|---|---|
| Balanced flow | (2, 2, 2) | 12 | Three fixed operations; two offers per tick |
| Mixed job shop | (3, 1, 2, 1, 2) | 9 | Seeded unique routes of length 2–5 and heterogeneous durations |
| Bursty feedback | (2, 1, 3) | 8 | Six offers every three ticks; reduce offers when outstanding work reaches 32 |

Every producer caps outstanding jobs at 64. Job specifications and due dates are
fixed functions of seed/index; admission uses lifetime admitted/completed counts.
Reporting warmup never enters the controller. The Python oracle uses production
ShopFloor, Server, ProductionJob, ConWIP and ShopFloorKPIs actors. External
admission occurs at the right-open time boundary, before completions exactly at
that time, matching the established native coupling contract.

Each host passes 168 installed managed requests: three cases, FIFO/SPT, two seeds,
two warmups, a Python baseline and six native variants (full/bounded with observation
off or budgets 257/4096). Native final KPIs and control artifacts match Python
exactly. Admission is unchanged by warmup; native fingerprints are unchanged by
observation cadence within each retention/modeling cell. Twelve requests per host
reproduce in new children after both model source files have been deleted.

A separate 36-case physical oracle per host compares full and bounded native
execution with Python at every admission boundary, then drains the admitted work.
Operation starts, ends, dispatch order, release times and job completions match
exactly. All 36 physical trajectory digests agree across hosts.

The endurance gate adds 18 installed requests per host at horizon 16,384, with up
to 32,768 admitted jobs. Both native retention modes again match Python control
state and all final KPIs exactly, including across hosts. Bounded native allocated
storage is 4,748 bytes for flow, 20,460 for the mixed shop and 12,476 for bursty
feedback, for both dispatch policies. Completed jobs are forgotten; retained jobs
equal admitted minus completed and remain within the producer bound. These native
counters exclude Python objects, trace buffers, identity composition and Studio.

## Real Studio sessions

The timed gate uses real installed Studio servers, packaged frontend assets,
native children and the official bounded WebSocket transport. Chromium, Firefox
and WebKit clients run on bigboss against each server, with the Linux connection
forwarded through SSH to the same loopback port. These are macOS browser clients
against macOS and Linux servers, rather than a Linux desktop-browser matrix.

Runs use horizon 4,096, warmup 100, all three cases, both dispatch policies, both
retention modes and observation budgets 257/1,000/4,096. Deliberate wall pacing
keeps progress observable. Sessions repeatedly change parameters, complete runs,
cancel after native progress, inject producer failure halfway through execution,
close/reconnect WebSockets, reload, seek and resume following. These elapsed
times are not performance benchmarks.

Both sessions last slightly over 900 seconds. Each forces 15 WebSocket reconnects
and nine reloads; macOS completes 13 seek/follow actions, Linux 12. The visible
history reaches its 20-result cap. The observed terminal outcomes are:

| Server | Succeeded | Cancelled | Injected producer failure | Browser-error gate |
|---|---:|---:|---:|---|
| macOS | 27 | 3 | 3 | Passed |
| Linux | 30 | 4 | 4 | Failed: two console/network errors |

All fourteen failed/cancelled prefix endpoints return HTTP 410. None publishes a
successful result. Completed traces pass structural checks and contain all eight
series, with the planned count and both window endpoints; final sample values
equal the published final KPIs. After shutting down Studio and deleting both
source files, six captured requests per host reproduce exactly, covering every
case/retention pair. These are additional to the matrix's twelve reproductions.

The Linux console reports are WebKit's “The network connection was lost” and
Chromium's `net::ERR_CONTENT_LENGTH_MISMATCH`. Their root cause is unresolved.
The timed harness did not record a timestamp/URL for each console error, which
limits correlation with cancellation, reload or a particular range request.
A controlled probe prepares and validates a live range source, waits for
successful publication and reads the same source again: it succeeds on both
hosts with exact bytes, as does a newly resolved source. Thus this study does
**not** establish a successful-publication race. Cancellation deliberately revokes
active ranges; whether these messages represent expected aborted reads or an
unhandled transport defect needs an instrumented follow-up.

Live samples also frequently catch partially rebuilt chart panels. Code inspection
shows that every new live recording clears/rebuilds the charts and fetches each
series in turn. This is a candidate source of refresh starvation under continuous
updates, rather than a confirmed cause. After updates stop, all six browser clients
render all eight charts with no errors or nonempty alerts. The chart phase after
metadata takes 0.82–0.85 seconds locally and 1.33–1.85 seconds over the Linux
connection. Preserve completed charts while preparing replacements and coalesce
refresh work as the next UI experiment, after measuring request timings.

## Memory observations

| Metric | macOS server | Linux server |
|---|---:|---:|
| Server RSS range | 74.6–91.5 MiB | 82.9–103.9 MiB |
| First/last-third median RSS | 86.1 / 89.0 MiB | 93.9 / 103.7 MiB |
| Last-third RSS range width | 3.3 MiB | 0.4 MiB |
| Chromium post-GC heap, first/last ten median | 11.1 / 11.8 MiB | 11.1 / 12.1 MiB |
| Store bytes, start/end | 36.0 / 98.7 MiB | 36.3 / 103.8 MiB |

The observed runtime growth is much smaller than persisted-store growth, and
Linux server RSS levels off late in this window. This supports bounded retention
for these workloads, but is not a proof of leak freedom. The store already holds
preflight results when the timer starts; store cleanup remains manual. Browser
process-tree diagnostics cover the latter portion of the sessions, not the whole
15-minute interval. Reloads and explicit Chromium GC affect these observations.

## Outcome and next stabilization gate

The modeling, retention and publication/reproduction gates pass. The overall
Studio browser gate remains **failed** because the Linux run is not error-free.
This is a completed stabilization study with an unresolved UI/transport finding;
it does not promote the backend or change production code.

Next, record each range request's timing, attempt/prefix identity, outcome and
cancellation/reload context; correlate browser errors with those records. Use a
controlled slow connection to reproduce the refresh behavior, then evaluate
coalesced, atomic chart refresh while preserving generation and cancellation
fences. Repeat this timed gate after a justified fix, before widening modeling
scope or attempting a multi-hour soak.

## Scope and evidence

Fresh environments use CPython 3.14.3 on macOS 27.2 arm64 and CPython 3.14.0 on
Linux x86-64 (glibc 2.43). Both install simulatte/simulatte-native 0.17.0. The earlier
CPython 3.11–3.15 packaging and ABI evidence remains separate; this study does not
repeat it or establish portable wheels.

The harness and raw evidence live under `prototypes/native_stabilization/`.
`environment.py` records installed source/binary and workload hashes; matrix and
endurance artifacts retain requests, fingerprints, controls and KPIs. Browser
actions and memory observations are timestamped. Server/store growth is measured
separately from runtime retention. Browser heap accounting is available only for
Chromium; process-tree RSS is diagnostic and can include shared pages or omit
reparented helpers. A 15-minute session cannot establish multi-hour leak freedom.

Source-deleted reproduction verifies managed native requests and their artifacts.
KPI traces pass structural checks but cannot verify an entity-event digest or
provide full native entity animation, event stepping or replay.
