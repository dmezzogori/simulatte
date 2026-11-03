# Linux declared-native-bundle validation

Davide authorized the Linux validation gate after the managed native-asset
integration at `0bc4d39`. Run inline on strangelove in an isolated checkout,
using a clean Linux C/extension build and a locked local Python environment.
This is local managed execution on Linux, not support for SSH execution slots.

- Freeze and verify production/research/test sources, compiler/interpreter/ABI,
  environment and freshly compiled ELF bytes. Preserve all macOS evidence.
- Repeat the 96-run Python/native and reference/declared-bundle correctness gate
  over producers, policies, stopping windows and seeds. Preserve exact KPI/window,
  trajectory/proof, native state, fingerprint and artifact checks.
- Verify actual Linux mapped dependency locations in fresh children, including
  rejection of a genuine preloaded same-name dependency from the source directory.
- Exercise real native replications over two concurrent local slots, observe
  simultaneous leases and test pruning while leased. Run preview cancellation,
  corrupt-entry rejection and reconstruction/reproduction after cache eviction and
  deletion of live source. Run existing publication-fault and invalidation tests.
- Run relevant Python execution/core and native prototype regressions on Linux.
  Keep raw logs, deterministic results, negative probes and a replayable audit.
  No timing/speedup claim is required by this correctness/platform gate.
- Fix only defects exposed by the gate, then rerun affected validation from frozen
  final sources. No provider packaging/release, actor expansion, full tracing,
  Windows/remote support, push or merge is included.
- Copy evidence back, verify hashes and unchanged source/history, write the report,
  commit locally and checkpoint Cortex. Keep the remote validation checkout as an
  explicitly identified reproducibility artifact.
