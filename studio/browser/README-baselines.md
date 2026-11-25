# Baselines of map.spec.ts

`map.spec.ts` compares four canvases (`corridor-symbol`, `corridor-true`, `corridor-select-agv`, `advanced-symbol`, labels hidden) with stored images, but only on Chromium on Linux, and only when the image exists. The images are `studio/browser/map.spec.ts-snapshots/<shot>-chromium-linux.png`. Without the file the test checks the palette pixels and then skips the comparison with the message "baseline not generated: see studio/browser/README-baselines.md". On every other browser or platform the palette-pixel check is the whole test.

The images must be made on Linux, in the container that matches the pinned `@playwright/test` (1.64.0), so that text and antialiasing do not depend on the machine that made them. The host `node_modules` holds macOS binaries, so the build runs on a copy inside the container, which receives only what the build reads: `studio/` without `node_modules`, `.build`, `dist`, `test-results` and `playwright-report` (the host snapshots directory is not needed either), the two JSON schemas that `studio/scripts/stamp.mjs` hashes, and `tests/fixtures`. The repository is mounted read-only; only `studio/browser` is writable. Docker must be running. Put the script below in a file (here `/tmp/baselines.sh`) and run from the repository root:

```bash
cat > /tmp/baselines.sh <<'EOF'
set -euo pipefail
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
mkdir -p /work/src/simulatte/layout /work/src/simulatte/experiments /work/tests /work/studio
tar -C /src -cf - src/simulatte/layout/schema/layout-v1.schema.json src/simulatte/experiments/schema tests/fixtures | tar -C /work -xf -
tar -C /src/studio --exclude=node_modules --exclude=.build --exclude=dist --exclude=test-results \
    --exclude=playwright-report --exclude=map.spec.ts-snapshots -cf - . | tar -C /work/studio -xf -
cd /work/studio
corepack enable
pnpm install --frozen-lockfile
pnpm build
pnpm exec playwright test map.spec.ts --project chromium --update-snapshots
mkdir -p /out/map.spec.ts-snapshots
cp browser/map.spec.ts-snapshots/*-chromium-linux.png /out/map.spec.ts-snapshots/
# check: compare again without --update-snapshots; the four shot tests must pass, not skip
pnpm exec playwright test map.spec.ts --project chromium --reporter=list
EOF
docker run --rm --platform linux/amd64 --ipc=host --init \
  -v "$PWD":/src:ro \
  -v "$PWD/studio/browser":/out \
  -v /tmp/baselines.sh:/baselines.sh:ro \
  mcr.microsoft.com/playwright:v1.64.0-noble bash /baselines.sh
```

`--ipc=host` and `--init` are the Playwright container recommendations (Chromium shared memory, zombie reaping). `COREPACK_ENABLE_DOWNLOAD_PROMPT=0` stops corepack from asking before it downloads pnpm.

On an arm64 host (Apple Silicon), `--platform linux/amd64` runs Chromium under qemu, and the GPU process crashes ("GPU process isn't usable"), so every browser test fails. The images committed for the first baselines were therefore made with `--platform linux/arm64` (same image tag, native Chromium with SwiftShader). CI is x86_64, so those images may differ from the CI rendering beyond the tolerance; see the residual risk below. Prefer an x86_64 Linux machine when one is available.

`pnpm` comes from corepack and the `packageManager` field of `studio/package.json`; the container needs network access for it and for `pnpm install`. The last step copies the PNGs back through the writable mount of `studio/browser`. Open the four images, check that they show the corridor and the advanced layout without labels, then commit them by name:

```bash
git add studio/browser/map.spec.ts-snapshots/corridor-symbol-chromium-linux.png \
        studio/browser/map.spec.ts-snapshots/corridor-true-chromium-linux.png \
        studio/browser/map.spec.ts-snapshots/corridor-select-agv-chromium-linux.png \
        studio/browser/map.spec.ts-snapshots/advanced-symbol-chromium-linux.png
```

To check them, run `pnpm test:browser` on Linux (or `pnpm exec playwright test map.spec.ts --project chromium` in the same container): the four tests then compare instead of skipping.

## Residual risk

The images are made in the Playwright container, but CI runs on the `ubuntu-latest` host, with the browsers installed by `playwright install --with-deps`. The two can differ in fonts, GPU-less WebGL rasterisation (SwiftShader) and library versions, even though the Chromium build is the same. The canvases are drawn without labels to keep text out of the comparison, and the tolerance is `maxDiffPixelRatio: 0.01`, but the first CI run after adding the images may still fail by a small margin. In that case download the `playwright-report` artifact of the run, check that the difference is antialiasing only, and refresh the images from the CI run (or widen the ratio) instead of from the container.
