#!/usr/bin/env bash
# Build a wheel from the current source and place it where the docs site serves
# it, so `zensical serve`/`build` can offer in-browser execution against HEAD.
# The wheel keeps its real PEP 427 name (micropip rejects renamed wheels); a
# latest.json manifest records the name for the controller to fetch.
set -euo pipefail
cd "$(dirname "$0")/.."
uv build --wheel
mkdir -p docs/assets/wheels
rm -f docs/assets/wheels/simulatte-*.whl   # keep exactly one wheel in the dir
cp dist/simulatte-*.whl docs/assets/wheels/
wheel_name="$(basename "$(ls -1 dist/simulatte-*.whl | tail -1)")"
printf '{"wheel": "%s"}\n' "$wheel_name" > docs/assets/wheels/latest.json
echo "Wrote docs/assets/wheels/$wheel_name and latest.json"

# Emit the library version into a header-badge partial so the docs always show
# the version they were built from. The partial is gitignored and regenerated
# here (and in CI before `zensical build`); the header override includes it with
# `ignore missing`, so a build that skips this script simply shows no badge.
version="$(printf '%s' "$wheel_name" | sed -E 's/^simulatte-([^-]+)-.*/\1/')"
mkdir -p overrides/partials
printf '<span class="md-version-badge">v%s</span>\n' "$version" > overrides/partials/version.html
echo "Wrote overrides/partials/version.html (v$version)"
