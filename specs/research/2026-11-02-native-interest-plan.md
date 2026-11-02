# Native publisher subscriber-interest experiment

Authorized by Davide after the [untraced observation study](2026-11-02-native-observation-untraced.md).
Start at `0bcaa5c` in the existing native feasibility worktree. Work inline.

Add an opt-in `interest_checks=True` to the live adapter. Apply the interest
guard only in untraced mode, after mandatory event-time view updates and before
typed event/delta construction and publication. Use the existing bus interest
query so base-class/wildcard subscribers and dynamic subscription changes work.
Keep the original publisher as a control; no C/binding, journal, collector,
production actor or global event-bus changes.

Prove that unrequested events are not constructed, while PSP timing, completion,
WIP, queues and worked time still advance. Compare stock values and callback
reads with production and the original publisher; test exact catch-all streams,
specific subscriptions, subscription changes, failure/control guards and empty
subscriber sets. Untraced sequence numbers count emitted events and may compress
when unrequested events are omitted; payloads/times and requested delivery remain
the contract. Recorded modes bypass filtering and must preserve exact output.

Run the existing 1,536-configuration none/full gate on each interpreter with the
flag enabled and compare all rows with `0bcaa5c`. Add targeted filtered/control
comparisons, including continuous inputs. Recheck recorded fixtures, ordinary
prototype tests and relevant sanitizer/production checks.

Freeze sources/tests/binaries/input after verification. Measure 40 none-mode
cases: two interpreters, two producers, 5k/20k jobs and production plus both
native delivery modes with filtering off/on. Use fresh serial shuffled processes,
one CPython/seven PyPy warm-ups, five samples and a separate cold/RSS probe.
Keep full stock/legacy result export, journal I/O and close/GC timed. Capture the
existing environment emission counter once per sample to verify publication
counts without per-event instrumentation. Retain and compare every collector
value against current controls and historical exports.

Run six separate CPython CPU diagnostics: production and native live filtering
off/on for both producers at 5k. Reuse the established per-thread CPU profiler;
these are not speed samples. Explain event-count reduction separately from time
reduction. No recorded performance claim is planned; full/KPI compatibility is
covered by correctness and fixed-provenance trace regression checks.

Audit historical evidence against `0bcaa5c`, preserve native source/binary hashes,
write the results/limits/recommendation, checkpoint Cortex and commit locally.
No push, public integration or additional optimization requested.
