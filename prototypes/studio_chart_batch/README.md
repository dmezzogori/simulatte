# Studio KPI chart read study

Use the `flow`, `mixed`, and `burst` committed KPI recordings listed in
`results/inputs.json`. They are `run.simtrace` files from the
[macOS stabilization audit](../studio_stabilization/results/final-macos-audit.json).
Check their SHA-256 values before running the study. Keep the traces, wheels,
and private access files under `.build/`.

For the chart query, use the same `studio/packages/trace/benchmark/charts.ts`
in both worktrees. In the baseline worktree at `3ae7589`, copy it to
`studio/packages/trace/.build/charts.ts` (the import resolves to the
baseline trace reader). From each trace package directory, build with
`pnpm exec vite build --ssr <entry> --outDir .build/chart-study`. Run
`node .build/chart-study/charts.js serial <traces...>` for the baseline
and use `batch` for the candidate. This excludes opening/indexing the trace,
and records reads, bytes, point counts, point digests, and five-run medians
after one warmup for each selection.

For the packaged comparison, build and package the Studio assets in each
worktree (`pnpm --dir studio build`, `python studio/scripts/package_assets.py`,
`uv build --wheel`). Install each wheel with `[studio]` into its own
environment. Match the server dependencies within each pair. With the
installed Python, run `python -I serve.py --trace <burst.simtrace> --port
<port> --access <private.json> --metadata <public.json> --delay-ms 5`.
Each server listens on loopback. Forward the Linux ports to the same local
ports on the browser host. Copy access files privately when needed; they
contain session credentials and must never be committed.

Run `node prototypes/studio_chart_batch/viewer.mjs <targets.json> <output.json>`.
Targets is an array of objects with `host`, `baseline`, and `candidate`
fields; the last two are paths to the private access files. The probe opens
each recording in a fresh browser context, waits for the paused viewer and
all eight charts, and counts completed `/trace` GET bodies. It checks each
body's actual byte length against the server's declared length. The reported
ready time is navigation to the viewer's paused status. There are three
pairs per browser and server, with the second pair in reverse order.

The [study report](../../specs/research/2026-11-05-studio-chart-batching.md)
summarizes the paired evidence in `results/`.
