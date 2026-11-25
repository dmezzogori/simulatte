# Bench and acceptance harness

These local tools are not shipped in the Python package. Run from the repository
root unless a command changes directory. See the acceptance report in
`specs/research/2026-10-11-sp3-acceptance.md` for measured results and limitations.

```sh
pnpm --dir studio build
pnpm --dir studio --filter @simulatte/scene exec vite build --config "$PWD/studio/bench/vite.config.ts"
uv run --all-extras python studio/bench/glyphs.py --workload fleet100
uv run python studio/bench/serve.py /tmp/studio-map-bench/fleet100.simtrace --port 4182
```

`vite` does not resolve from the `studio/` root, so it is run through the scene package with an absolute path to
the config, whose `root` is absolute too. The bench page is built to `studio/.build/bench`, which `serve.py` serves.
The map workloads (`--workload fleet100` or `lanes1000`, written to `/tmp/studio-map-bench/`) are real fleets that
move on a network declared on the layout: `fleet100` is 100 vehicles on a 20 x 20 `Lattice` under
`ReservationController`, `lanes1000` is 1,000 vehicles on 50 `Lanes` with the default controller. `--until` and
`--seed` override the simulated time and the seed.

In another terminal:

```sh
node studio/bench/run.mjs chromium 'http://127.0.0.1:4182/?mode=performance' /tmp/studio-map-bench/results.json --shot /tmp/studio-map-bench/scene.png
```

`--shot <png>` screenshots the scene after the measurements: a frame late in the run, fitted. The result also
reports `projectP50`/`projectP95` (the projection alone) and `restOfRenderP95` (what the scene adds to it), and splits the first seeks into `coldPrepare*` (decode and reconstruction) and `coldRender*`.
Page options: `seeks=N` (number of first seeks, default 50), `nomap=1` (the frames' layouts lose their network, so no map
model is built), `norender=1` (the seeks draw nothing), `settle=1` (time the wait for an animation frame after each
render) and `gc=1` (a full collection after each render, needs `--js-flags=--expose-gc`). `serve.py --root <dir>` serves
another build of the page.

Use `chromium`, `firefox` or `webkit`; install Playwright browsers first. Chromium
on macOS additionally samples process RSS every 100 ms. First-seek measurements
retain caches between pseudorandom positions; repeated seeks repeat each position
immediately. Long-task observations include startup. This is an isolated renderer
workload, not a fleet throughput test.

To test exports, serve `tests/fixtures/traces/viewer/viewer.simtrace` instead.
Use `?mode=video&frames=99999&width=320&height=180` for near-cap MP4,
`?mode=video&format=png&frames=10000&width=320&height=180` for near-cap PNG,
or `?mode=html` for offline HTML. The server writes the artifact to its `--output`
path with `.bin` suffix and its result to `.json`. The runner's JSON adds browser
errors, long tasks and available RSS measurements. The default timeout is 20
minutes; override `SP3_BENCH_TIMEOUT_MS` for slower machines. Keep each output
before starting the next export, because the server reuses its configured path.

Validate media independently with FFmpeg/ffprobe and Python `zipfile.testzip()`;
open HTML from `file://` with networking disabled. `zip64.mjs` is a separate format
stress test with over 65,535 entries and a >4 GiB entry. It requires substantial
temporary disk space and is not evidence of the browser export memory budget.
Actual Safari must be tested separately from Playwright WebKit.

## SP4 committed-prefix extension

Use `?mode=live` with the same fixtures and runner. The harness appends INITIAL,
roughly 4 MiB batches ending at INDEX, and the final footer, using incremental
SHA-256. Every extension asserts that metadata/hash reads do not revisit prior
bytes, prepares the new edge, and checks shared four-chunk/128 MiB cache limits.
It then runs the same seek/frame workload. `openMs` includes all extensions;
`extensionAndEdgeMs` measures browser ingestion/preparation without network or
server scheduling. It is not end-to-end Studio commit-to-display latency.
Long-task observations include startup; RSS is sampled only by the macOS
Chromium runner and is distinct from decoded cache estimates.
