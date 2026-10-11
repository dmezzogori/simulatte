# Post-review acceptance evidence

Measured source: `addf0fa1729077fa618305efcde496c116ae55f8`. See the
[acceptance report](../../2026-10-10-sp2-acceptance.md) for methods, results and bounds.

- `commands.json`: exact fresh commands, statuses, durations, environment overrides and saved logs.
- `runtime-fixtures.json`: all exported values per runtime/angle, hash seeds and cross-runtime comparisons.
- `preparation.json`: three pure timing samples and separate allocation probe per graph size.
- `packages.json`: newly built archive/schema hashes, installed paths, optional dependency absence and freezes.
- `full-gate.json`: actual signed fix hook and coverage evidence, independently checked from its output/XML.
- `logs/`: raw outputs with trailing whitespace/newlines normalized. Original local paths remain in the ledger.
- `*.py.txt`: exact temporary orchestration scripts, preserved as historical command evidence, not maintained helpers.
  Reproduction uses `benchmarks/layout_acceptance.py`; substitute fresh output/interpreter paths in orchestration.

The original `6a3f131` benchmark/acceptance artifacts remain in the parent directory. No no-layout benchmark
or full CPython suite was rerun for this refresh. The full suite ran once in the unchanged signed fix hook;
the original Task 10 signed-hook output is also preserved here to close its earlier pending evidence row.
