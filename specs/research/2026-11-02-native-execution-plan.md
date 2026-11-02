# Native scalar execution integration gate

Davide authorized this gate with “Proceed with next result”, following the
native scalar KPI experiment and its proposed real-experiment integration.

Keep production APIs and the managed driver unchanged. A research model adapter
owns a separate native/production stream, advances it from the execution
environment's step boundaries and exports its scalars through a pure Collector.
Managed close owns cleanup. The ordinary coordinator, ready barrier, child
isolation, store, KPI result file and ExperimentReport remain in use. A KPI file
is required by the current driver; this is scalar result integration, not native
domain tracing. Full tracing, finite-population stopping and nonempty layouts
are explicitly unsupported. No source/package/CI changes or push.

1. Implement/test a restricted adapter: fixed unit-capacity servers, FIFO/SPT,
   EDD ConWIP, independent/feedback input, Horizon and warm-up semantics. Reads
   never advance the backend. Include cancellation/failure/close behavior.
2. Build relocatable, hash-verified native assets inside a small source-snapshotted
   project. No dependency on mutable worktree libraries from isolated children.
3. Run real paired replicated experiments and verify seeds, generated inputs,
   feedback, partial states, scalars, trace footers, stored results and zero
   paired differences. Check reuse, exact reproduction of fingerprints/artifacts, rejected features
   and child failure cleanup. Timing belongs only to volatile metadata.
4. Measure fresh-process execution for 5k/20k jobs and both producers, with input
   generation, preparation, ready handshake, result transport/publication,
   statistics and cleanup included. Separate model timing from total API/CLI
   wall time; never compare these totals directly to the preceding kernel timings.
5. Freeze code/assets, save raw evidence and an audit, run relevant regressions,
   document implications, commit locally and checkpoint in Cortex.
