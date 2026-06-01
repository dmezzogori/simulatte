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
