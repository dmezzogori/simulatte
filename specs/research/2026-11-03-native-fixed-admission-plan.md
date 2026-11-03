# Fixed specifications and reporting-independent control

Davide authorized this experiment after signed feedback gate
`8501694ba04c80d3a0fa5af552c377ddaaac0f09`. Work inline in the isolated native
research worktree; preserve all previous files. No product/API changes, push,
release or deployment baseline selection. Native remains v3 research after v2.

- Fix routes, durations, offered times and absolute due dates by source seed/index.
  Admission controls actual plant entry only; it cannot move a deadline or shorten
  a job. Lazy bursts offer 12 jobs every 16 units, spaced by 1/8 unit. Generate
  only after credit; materialize no pending-source queue.
- Use a bounded fixed-point EWMA of observed pool fraction with hysteresis:
  q=(q+floor(256*pool/live))/2 rounded down, tighten at64, relax at16. The rule
  never reads reporting KPIs. Hard12/tight6, ConWIP4; second context hard8/floor2.
  Include this state, seed/cursor, owned identities and next wake in the callback.
- Separate control/admission and reporting proofs/commitments. Compare every
  control observation/decision/input plus all reporting values against ordinary
  Python, full native and bounded native at identical urgent right-open boundaries.
  Independently audit source generation, control law and corrupted evidence.
- Vary long-run warmup0/10/40 and partial-run warmup0/10: control proof and actual
  input trajectories must stay identical, while reporting KPIs may differ. Check
  ordinary Python semantic digest equality across windows; native state includes
  windowed KPI state, so do not require its whole fingerprint to be window-free.
- Test poisoned reporting reads, feedback-disabled timing counterfactuals, fixed
  common job requirements/deadlines, callback purity/completeness, query invariance,
  ties, partial horizons and fresh source-deleted Coordinator reproduction.
- Reuse matching clean installed wheels outside checkout. Serial memory gate:
  4096/32768/262144 ticks, 3 seeds, full/bounded, extra queries, warmup twins,
  repeated parent. Same limits: bounded median range child<16/tree<24/parent<16
  MiB; largest full child>bounded+16; <=12 owned, callback<8KiB/native<128KiB.
  Keep normal semantic digest/KPI traces/publication; no tests/compilers during
  timing. Capture sampled tree and child self peaks separately.
- Save raw proofs/hashes/report, signed local commit and Cortex checkpoint. Only
  the reporting-independent model and gate are new; earlier evidence is immutable.
