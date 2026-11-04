# Packaged native observation in experimental Studio

Step 4 adds optional bounded scalar snapshots to the managed native backend and
uses Studio's existing committed-prefix/range/KPI transport. No C kernel or
companion ABI changes. Native resolved capabilities label the backend experimental,
record the effective snapshot schedule and refuse full entity replay.

The public configuration is NativeObservation(interval=10, max_samples=1000).
Samples cover warmup through the right-open horizon; spacing grows when needed to
fit at most 4096 points per KPI. One event is pending and one scalar query occurs
per boundary. No subscriber means no scheduled observation work. Scalar estimands
and semantic/native fingerprints stay independent of cadence.

The installed gate compares off/fine/budgeted variants, fixed/dynamic admission,
FIFO/SPT and both retention modes, then deletes source and reproduces. The Studio
probe serves the actual packaged assets and preserves a real committed partial
prefix and its completed recording for the three-engine browser suite. Core
transport tests cover actual native cancellation/failure revocation and no bad
publication. Earlier drafts are evidence, not the final matrix.
