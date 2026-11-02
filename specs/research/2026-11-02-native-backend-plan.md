# Explicit managed backend and native state fingerprint gate

Davide authorized the preceding recommendation with “Go with the next step you
recommended”. Scope: replace private lifecycle wrapping and include native model
state in the existing fingerprint/reproduction path. Keep the current fixed
production slice, Horizon/KPI-only capability boundary, process isolation and
result publication. No package/release, full tracing, actor expansion or push.

1. Add an opt-in managed `ExecutionBackend` contract and Environment registration.
   The managed stopping loop owns advancement/finalization; Environment owns
   cleanup. Existing None-backend models retain their flow. The backend declares
   its scalar KPIs and state-fingerprint schema; capability validation precedes
   opening resources. Research code stops replacing environment methods.
2. Represent the initial/final backend hash in a typed projection entity/event.
   Existing semantic fingerprints, KPI files and publication/reproduction checks
   then include it without a new result-file schema. This opaque projection does
   not establish full native event tracing or per-actor replay.
3. Add pure checked native state encoding: versioned little-endian binary64,
   canonical pool/calendar ordering, live inputs/trajectories/resource queues,
   control counters and pending events. Exclude pointers, capacities, observer
   buffers/modes/aggregates. Hash in Python; also cover future Python input data
   and producer position/controller state. State reads never advance the engine.
4. Verify hidden-state sensitivity with equal KPI outputs, mode/partition/query
   invariance, lifecycle/cancellation/error ownership, C allocation-failure safety,
   old default behavior/fixtures, real managed seed pairs, reproduction and reuse.
   Native/production hashes have distinct schemas; compare their shared outcomes,
   not hashes of different simulator representations.
5. Measure state capture cost and representative whole-experiment performance;
   freeze and audit source/assets/evidence, document scope, commit locally and
   checkpoint in Cortex. Timings stay in volatile metadata only.
