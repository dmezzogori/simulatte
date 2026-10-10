# SP2 acceptance raw evidence

See [the acceptance report](../2026-10-10-sp2-acceptance.md) for interpretation and bounds.
Measured library source is `6a3f131`; package metadata remains 0.13.0.

- `commands.json`: exact acceptance commands, working directories, environment overrides, individual exit
  statuses, elapsed seconds, original local log paths and saved log paths. Failed intermediate attempts are
  retained alongside their corrected checks. `logs/initial-red.txt` predates this command logger.
- `logs/`: raw outputs, including all individual fresh-process fixture exports and preparation probes.
  Durable text logs only normalize trailing whitespace and final newlines; originals remain at ledger paths.
- `environment.json`: interpreter, package/tool versions and hardware/OS capture.
- `runtime-fixtures.json`: full canonical output per runtime/angle, tested hash seeds and cross-runtime comparisons.
- `preparation.json`: all three timing samples and separate allocation probe for each deterministic graph size.
- `packages.json`: artifact/schema SHA-256s, archive member names, installed import paths, snippet counts,
  absent optional packages and exact core-only package freezes.

- `benchmarks/`: all 60 canonical measurement JSONs, exact command/status ledger, historical execution
  harness, constraints/freezes/import checks, parsed report outputs and raw reports. See
  [the benchmark report](benchmarks/report.md). Original `.log` files use `.txt` in this durable copy;
  the ledger retains original local paths. The metadata copy omits unrelated process inventory, retained
  locally in `/tmp/simulatte-sp2-bench.jsKjEr/metadata.log`.

All eight existing benchmark gates and every report-only command passed. Final signed-hook evidence and
review disposition are added by the root agent after execution and review. No pending result is represented
as already successful.
