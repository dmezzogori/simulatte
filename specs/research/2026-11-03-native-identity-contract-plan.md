# Versioned native identity contract experiment

Davide authorized the recommended separation experiment after signed
`fb55e0cc6368cddc540894dffbef39bd3ed4cb00`. Work inline; preserve every previous
file. This is a research projection, not a public API/schema migration. No push,
publication, release baseline or native event-loop change.

- Define versioned trajectory/control, reporting-state and complete reproduction
  commitments. Trajectory/control describes realized native history/live state
  plus admission-target/actual-job actions, not arbitrary future controller state.
  It must never be sufficient for reuse/reproduction. Reporting identity includes
  declared Horizon/window, KPI catalog and actual current stock snapshot.
- Preserve the original backend state digest/schema as authority. Complete
  identity binds it to the projected components/configuration. Independently
  verify the existing digest preimage using raw native bytes and the full callback
  inputs; reject inconsistent/impure snapshots. Do not remove KPI state from the
  original digest: KPI-fed controllers can have different futures.
- Strictly recognize existing NSTA0001/full and NRST0001+NBST0001/bounded codecs.
  Validate lengths/shape and canonical KPI tail, fail closed on unknown versions,
  and domain-separate projection schemas. Do not compare full/bounded digest bytes
  as if their encodings were the same. No restoration API is implied.
- Instrument byte-identical copies of both completed fixtures: fixed requirements/
  independent control and the KPI-fed adaptive producer. Maintain bounded rolling
  action hashes, checked against each fixture's actual input commitment. Capture
  initial/final components at synchronized boundaries, no domain-event changes.
- Fixed controller: warmup0/10/40 must preserve trajectory/control identity and
  change reporting/complete identities. KPI-fed controller: changed realized
  actions/inputs must change trajectory identity; different initial window or
  unconsumed seed/control state must change complete identity even when past
  trajectory identity is equal. Within a window, exact fresh reproduction remains.
- Test malformed encodings, authority mismatch, equal-report/different-history,
  future-state mutation, pure/repeated queries and partitioned component validation.
  Compare against capture-disabled original artifacts/fingerprints and independent
  action reconstruction from streamed proofs; include Python physical references.
- Clean matching installed wheels outside checkout, no runtime compiler/prototype
  imports, normal Coordinator/digest/KPI traces, source-deleted fresh reproduction.
  Serial bounded memory at4096/32768/262144 ticks,3paired seeds,2fixtures, periodic
  queries plus query-frequency/baseline controls and same-parent reproduction.
  No tests/compilers during timing; bounded child/tree/parent median ranges<16/24/16
  MiB, <=12 handles, raw bounded blob<64KiB, projected view<16KiB, final identity
  artifact<32KiB. Report resource cost rather than a speedup claim.
- Save raw evidence/audits/report, signed local commit and Cortex checkpoint.
