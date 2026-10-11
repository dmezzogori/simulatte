# SP3 acceptance harness

These local tools are not shipped in the Python package. Run from the repository
root unless a command changes directory. See the acceptance report in
`specs/research/2026-10-11-sp3-acceptance.md` for measured results and limitations.

```sh
pnpm --dir studio build
(cd studio && pnpm exec vite build --config bench/vite.config.ts)
uv run python studio/bench/glyphs.py
uv run python studio/bench/serve.py /tmp/sp3-glyphs1000.simtrace --port 4182
```

In another terminal:

```sh
node studio/bench/run.mjs chromium 'http://127.0.0.1:4182/?mode=performance' /tmp/glyph-results.json
```

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
