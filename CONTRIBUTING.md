# Contributing to Simulatte

## Getting Started

1. Clone the repository directly (fork PRs are not accepted)
2. Create a branch from `main`:
   - `feature/<name>` for new functionality
   - `fix/<name>` for bug fixes
3. Install dependencies and pre-commit hooks:
   ```bash
   uv sync --dev
   uv run pre-commit install
   ```

## Pull Requests

- Open a PR against `main` with a clear description of what changed and why
- All CI checks must pass (tests across Python 3.12–3.14, linting, type checking, docs build)
- At least one approving review is required
- If your change adds or modifies functionality, update the documentation in `docs/` accordingly

## Merging

PRs are squash-merged into `main` by the maintainer ([@dmezzogori](https://github.com/dmezzogori)). Contributors should not merge their own PRs.

## Releases

New versions are published to PyPI by the maintainer via `v*` tags. Contributors should not create release tags.
