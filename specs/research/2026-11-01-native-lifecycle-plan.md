# Native trace lifecycle alignment experiment

Davide authorized the proposed follow-up to the direct production baseline.
Start at 1b234fc; preserve all earlier reports, fixtures and measurement evidence.

1. On release under a configured cap, emit the actual ConWIP policy decision
   before pool exit. Uncapped immediate release must not claim ConWIP.
2. After finish/KPI samples, retire the shadow job through EntityRegistry and
   remove its projection state. Retain native handles/results and input/control
   history. No native C, binding, scheduling or production runtime changes.
3. Check pre/post-retirement seeks, released shadow-object references, completed
   handle inspection, cap/no-cap behavior, both recorder levels and interpreters.
   Update event-count/retirement validation; validate historical job fields from
   creation/finish events instead of retaining them in final state. Keep bounded
   trajectory equivalence distinct from identical SimPy micro-event semantics.
4. Keep old fixture bytes intact; add a lifecycle fixture and test Python and
   TypeScript seeks/scene/KPIs plus browser smoke with retired jobs.
5. Freeze source, repeat all 24 correctness preflight cases and the same 24-case
   production/native none/KPI/full matrix,5 samples, CPython 1/PyPy 7 warmups and
   cold/RSS probes. Write lifecycle-results. Repeat separate CPython profiles
   and snapshot/output-span diagnostics to check the proposed mechanism.
6. Audit provenance, coverage, invariance and corrupt-evidence rejection; compare
   new native/production controls with historical production-results. Report
   drift, file/snapshot/memory changes and actual end-to-end margins. Stop after
   this candidate; no direct serializer or journal bypass in this experiment.

Completed: all 24 preflight cases, 24 cases/120 timed samples/24 cold probes,
12 separate CPython profiles and the current/historical audit pass. Native full
throughput improves 1.14–1.21x; snapshot bytes/file size/memory fall, but production
PyPy remains 17–20% faster in throughput than native PyPy. No-recording results
remain equivalent; native CPython KPI time rises 3–5%. The
[report](2026-11-01-native-lifecycle.md) records full results and limitations.
