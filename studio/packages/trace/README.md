# TypeScript trace reader

Run the conformance and hostile-input tests and TypeScript checks from the repository root:

```sh
pnpm --dir studio test
pnpm --dir studio typecheck
```

The wire decoder implements the trace's MessagePack subset directly. It preserves integer versus float encoding for replay equality, rejects unsupported binary/extension values and invalid UTF-8, and checks nesting, container size and input bounds before reading nested data. It has no runtime package dependency.

`Trace.header`, `level`, `index`, `truncated`, `outcome`, `cursorRange` and `kpiDeclarations` are getter-only. Metadata maps, arrays, index entries and cursors are frozen, including nested values. `kpiDeclarations` maps `scope/name` to the recorded KPI declaration (unit, kind, description and observation settings). Older traces return an empty map. The optional `kpi-declarations-v1` feature adds these declarations through KPI records; each key is declared once per file.

## Performance measurements

From the repository root, benchmark the Python-generated `shop_small` fixture:

```sh
pnpm --dir studio/packages/trace benchmark --iterations 30 --cursors 8
```

Pass one or more absolute trace paths to measure larger workloads. Relative paths resolve from `studio/packages/trace`, where pnpm runs the script:

```sh
pnpm --dir studio/packages/trace benchmark /absolute/path/run.simtrace --iterations 30 --cursors 8
```

The command builds the benchmark under ignored `.benchmark/` and prints a JSON report. For JSON-only output after building, run `node studio/packages/trace/.benchmark/trace.js /absolute/path/run.simtrace --iterations 30 > report.json`.

The report measures opening an in-memory source, decoding the first uncompressed chunk (or the header for a chunkless trace), cold seeks using a fresh reader, and warm seeks immediately repeating the same cursor with its chunk cached. Seek timings include `prepare` and `stateAt`; opening is excluded. Up to `--cursors` chunk-end cursors are spread across the file. Each timing includes sample count, median, p95, minimum and maximum in milliseconds.

File reading is outside the measurements. Cold seeks mean an empty reader chunk cache, not cold OS or JavaScript runtime caches. Decode timings exclude framing and decompression. These measurements are reports, not calibrated CI performance gates; use a production-sized trace and repeat on the target runtime for release conclusions.
