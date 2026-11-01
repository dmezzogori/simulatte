# Direct event emission experiment

Davide authorized the next experiment after lifecycle alignment at 27316bf.
Preserve prior results/fixtures. Test one opt-in emitter against contemporary
aligned/native and actual production controls; keep C/bindings/journal constant.

The existing rich rows plus immutable Job inputs suffice. The emitter needs
pool/floor counts, queue sizes/occupancy, server WIP/work totals, queued times and
per-job waits; keep these as small arrays/counters, not a full entity-state map.
Emit production events/deltas directly through the actual Environment/EventBus,
semantic digest and TraceRecorder. The writer alone applies generic deltas to
reconstruct history/snapshots. No C serializer, callback transport or production
writer change in this candidate.

Registry bindings carry identity/kind/lifecycle and provide initial/creation
snapshots only. Afterwards they explicitly reject live snapshot requests rather
than return stale state. This publisher is an internal observation adapter, not
a model Environment. Historical state and snapshots come from Trace as before.
Preserve ConWIP decisions/retirement, stable native handles and result semantics.

Before timing, prove exact aligned/direct event/digest/state/KPI/series/normalized
wire parity, including both debug modes, FIFO/SPT, cap/no-cap, independent/feedback,
empty/error cases and completed-handle ownership. Verify main-thread generic
apply_deltas calls disappear while writer replay remains. Add direct.* fixture;
keep old fixtures and test all three through TS seek/scene and browser smoke.

Freeze sources then 36 cases: production/aligned-native/direct, two interpreters,
two 5k producers, none/KPI/full; five samples, CPython 1/PyPy 7 warmups, separate
cold/RSS probes, serial shuffled order. None native/direct deliberately use the
same compiled engine as controls. Save direct-results; preserve historical
27316bf lifecycle-results and verify its sources/evidence against Git blobs.
Repeat 18 CPython diagnostics, keep profile overhead outside speed samples.
Audit coverage/provenance/parity/corruption, record controls' drift, actual margins
and publisher limitation. Stop after this emitter candidate and report findings.

Completed: 36 preflight cases, 36 cases/180 timed samples/36 cold probes and 18
diagnostics pass the audit, with 11 corrupted-evidence checks and 233 historical
files verified. Direct/aligned output remains exact; direct main generic replay
is zero, writer replay retained. Full throughput gains 1.087–1.105x and KPI
1.193–1.235x. Best native full is near production PyPy parity (0.6%/3.9% longer),
with a metadata-only publisher limitation. The
[report](2026-11-01-native-direct.md) recommends scaling/contract validation next.
