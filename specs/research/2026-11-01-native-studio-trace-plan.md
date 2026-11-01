# Native production trace compatibility and cost gate

2026-11-01, starting revision `0f20924`, existing native-engine-feasibility worktree.
Davide authorized “Push, then proceed” after the compiled scalar experiment. Pushed the four completed
commits through 0f20924 to origin/feature/native-engine-feasibility before starting this milestone.
Implement inline; production runtime/writer/reader/viewer code and historical evidence stay unchanged.

## Design

The old NDYN records omit grants and resource departures, so cannot reconstruct exact intermediate
queue/user state at equal-time cursors. Add an opt-in rich raw record mode (new magic NTRC0001) to the
prototype C/reference/tuned engines. Preserve all existing modes/semantics. Record registration, grant
and departure at their actual execution points, in addition to existing arrival/release/queue/start/
end/finish. No dispatch calls skipped and no event-loop or boundary performance tuning.

A Python sidecar projects available complete raw records after scalar boundaries into actual production
Entity schemas and typed events through Environment/EventBus/TraceRecorder. The projection clock consumes
source times directly with no SimPy model processes; it never schedules or changes the native simulation.
Capture jobs, server queues/users/work, pool and shop-floor WIP, operation start/end, finish, KPI series
and scalars. Use builtin job/server/psp/shopfloor schemas, not renamed generic states. Full projection is
bounded to scalar registration at arrival (both benchmark producers); batching/future registration and
live Studio transport are not this gate. Native/reference rich raw recording can still accept future jobs.

Use production chunking/compression/CRC/digest/snapshots/index/footer/manifest and real Trace.check(),
verify(), state_at() tests. Feed immutable deltas and test states at same-time grants/departures, multiple
chunks, dynamic creation, empty/failed recording and corruption/truncation. Replay in the actual Studio
TypeScript reader/playback/scene and production browser viewer using a retained native fixture. This
proves bounded trace compatibility, not identical micro-event sequences to SimPy's own scheduler.

Intermediate raw file writes/reads and shadow-state conversion remain in the lifecycle measurement.
Both engines and the same bridge pay them; do not describe the overhead as a native serializer ceiling.
Default count/byte/pending limits, latency disabled (infinite) for repeatable chunk boundaries; all complete
writer shutdown is timed. Source record buffering can delay projection until a later scalar boundary;
the trace is not a proof of bounded live-delivery latency. No direct C MessagePack writer in this iteration.

## Evidence

Fixed 500/5,000-job prefixes of historical load .95 templates, scalar independent/feedback producers,
SPT and ConWIP cap12. Three paths: no observers, rich raw only, full production trace. Three backends:
compiled native, independent compact reference and tuned, both CPython/PyPy. 72 cases, five timed samples,
CPython1/PyPy7 warmups, separate cold/RSS probes, serial seeded shuffled order. Trace-rich event/snapshot
cost is the question; do not extrapolate moderate-size results to50k. Include model generation/feedback,
all buffers/handles/bridge/trace construction/digest/encoding/I/O/results/close/GC; exclude parsing/imports
and post-run correctness validation. Freeze all invoked Python production/prototype sources, libraries,
inputs and writer settings. Compare semantic/event/state/KPI hashes (not volatile trace bytes), full
raw bytes, exact source trajectories/generated inputs/decisions, trace sizes/chunks and variability.

Before timing: old/new suites CPython/PyPy; engine and compiled-boundary sanitizers; actual production
Python trace tests, TypeScript replay/seek/scene checks and browser smoke; source/error/owner/corruption
checks. Audit coverage, equivalence, timing sums and provenance with corrupt-evidence rejection.
Preserve all earlier results; write new trace-results and report the actual end-to-end native margin,
including a loss or parity. Stop after this bridge candidate. No broad native backend integration.

## Completed outcome

260 prototype tests pass per interpreter, one sanitizer-only skip; 134 production trace tests,
301 trace/25 playback/16 scene TypeScript tests, all three typechecks and the production build pass.
Native browser smoke passes Chromium/Firefox/WebKit. Engine scalar/rich/bulk sanitizers pass
143/143/144 allocation-failure positions, and compiled-boundary sanitizer 31 cases pass.
72 cases, 360 timed samples and 72 cold probes pass observer invariance, semantic/event/state/KPI
parity, frozen provenance and nine corruption checks. At 5k, full native CPython is 0.85x/0.94x compact
PyPy throughput and only 1.02x/1.03x compact CPython. Compatibility passes; this bridge does not justify
general native integration on full-trace performance. Preserve untraced/raw benefits and stop tuning.
The [report](2026-11-01-native-studio-trace.md) records actual results and limits.
