# Contributing to Simulatte

## Getting Started

1. Clone the repository directly (fork PRs are not accepted)
2. Create a branch from `main`:
   - `feature/<name>` for new functionality
   - `fix/<name>` for bug fixes
3. Install dependencies and pre-commit hooks:
   ```bash
   uv sync --dev --all-extras
   uv run --all-extras pre-commit install
   ```

## Pull Requests

- Open a PR against `main` with a clear description of what changed and why
- All CI checks must pass (tests across Python 3.11–3.15, linting, type checking, docs build)
- At least one approving review is required
- If your change adds or modifies functionality, update the documentation in `docs/` accordingly

## Merging

PRs are squash-merged into `main` by the maintainer ([@dmezzogori](https://github.com/dmezzogori)). Contributors should not merge their own PRs.

## Releases

New versions are published to PyPI by the maintainer via `v*` tags. Contributors should not create release tags.

## Studio and distributable builds

Python editable installs do not require Node. To develop Studio or build a wheel
from a checkout, use Node.js 24 and the pinned pnpm version:

```bash
corepack enable
pnpm --dir studio install --frozen-lockfile
pnpm --dir studio typecheck
pnpm --dir studio test
pnpm --dir studio build
uv run python studio/scripts/package_assets.py
uv build
```

The build hook rejects missing, tampered or stale assets. Rebuild after changing
frontend inputs. An sdist includes validated assets and builds offline without
Node when Python build dependencies are installed. `scripts/build_docs_wheel.sh`
also requires the assets above because the documentation embeds a complete wheel.
See [Studio](docs/development/studio.md) for browser checks and recording examples.

For SP4 execution changes, run `uv run --all-extras pytest tests/execution` and
relevant `tests/core/test_studio*.py` checks, then the full 99% branch gate.
Keep `src/simulatte` (including packaged assets) unchanged during full tests:
workers freeze that runtime, and reproduction correctly rejects changing bytes.
Run `node studio/scripts/studio-smoke.mjs /absolute/path/to/simulatte chromium required`
against a fresh installed wheel for parameter/editor/live/export integration.
Use Linux for native Firefox acceptance; Playwright WebKit does not substitute
for actual Safari manual release checks. Platform/failure evidence is maintained
in the SP4 acceptance report under `specs/research/`.
