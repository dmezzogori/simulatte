# Live stock collectors without trace recording

Authorized by Davide after the [live observation experiment](2026-11-02-native-observation.md).
Start at `d4aa1f2` in the existing native feasibility worktree; work inline.

Add opt-in `level="none"` to the live observation adapter. Do not construct a
TraceRecorder, start its worker, request semantic projection or attach a digest.
Keep the same five stock collector classes, the two existing WIP-series
declarations, read-only views, callback guards and strict-before-time boundary
delivery. The native rich journal remains the existing observation transport;
this experiment removes final trace/digest work, not native journal I/O.

Production uses its actual actors with recording disabled and the same collector
bundle. Keep detached trajectories, legacy derived model totals and stock-value
copying in both recording modes, so the comparison does not silently remove
result extraction along with recording. Defaults and old recorded output stay
unchanged. No C, binding, transport, collector or scheduler optimization.

First prove that no recorder/digest/projection is attached and that all stock
values, event-time probes and intermediate checkpoints match the recorded mode
and actual production. Include warm-up, horizons, exhaustion, idle tails,
callback errors and direct close without explicit result extraction. Repeat the
tie-heavy gate on CPython/PyPy; preserve existing fixtures and historical evidence.

Freeze sources, tests, binaries and input after verification. Measure both none
and full recording at 5k/20k jobs, independent/feedback, CPython/PyPy and
production/native-buffered/native-boundary: 48 serial shuffled fresh-process
cases, one CPython/seven PyPy warm-ups, five timed samples and a separate cold/RSS
probe. Include build/feed/run, identical detached exports, close and GC. Validation
and compression remain outside timing. Retain complete numeric collector exports
and compare every point; reuse existing exports when their exact values agree.

Run separate CPython CPU profiles for production and native boundary delivery,
none/full, both producers at 5k. Use the existing Python profiler/thread-CPU
method, with separate main/writer passes. Profiles are diagnostics and are not
timing samples. Distinguish native journal/typed delivery, collector callbacks,
result extraction and writer replay/encoding; do not add nested cumulative times.

Audit historical artifacts against `d4aa1f2`, regenerate recorded controls to
confirm byte/semantic preservation, save results and a report, checkpoint Cortex
and commit locally. No push, public integration or wider actor API requested.
