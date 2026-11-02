# Native scalar KPI experiment

Davide authorized this gate with “Ok proceed” after the native KPI fast-path proposal.
Keep Python authoring and the existing Python-observer path. Change only the research
prototype; no public backend, packaging, push, or PyPy delivery diagnostics.

1. Implement the eight `ShopFloorKPIs` in opt-in C state, with exactly rounded
   accumulation, production queue-time arithmetic, independent busy/floor clocks,
   warm-up clipping and completed-job cohorts. No event journal or Python publisher.
2. Expose detached snapshots at successful external boundaries, including partial
   horizons, unfinished jobs, empty windows and idle tails. Preserve lifecycle and
   allocation-failure safety. Snapshot queries must not change later results.
3. Compare against unmodified production `ShopFloorKPIs`, including tied events,
   FIFO/SPT, capped release, continuous times and repeated boundary snapshots.
   Re-run existing prototype regressions and recorded fixtures; check native memory
   safety and allocation failures.
4. Measure CPython with the same eight scalar outputs on both sides. Use independent
   and feedback producers, 5k/20k jobs, warm-up zero/nonzero, no recording. Separate
   construction, simulation, scalar export and close/collection; extract trajectories
   outside timings. Save source, binary and input hashes, raw repetitions and audit.

Success means production-equivalent snapshots and unchanged trajectories, with a
measured end-to-end gain for this scalar-only workload. It does not establish native
history/EMA/custom collectors, trace throughput, or a general replacement backend.
