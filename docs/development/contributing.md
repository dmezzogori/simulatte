# Contributing

Simulatte is developed on GitHub. The full workflow is in [`CONTRIBUTING.md`](https://github.com/dmezzogori/simulatte/blob/main/CONTRIBUTING.md) in the repository root; the summary below covers the key rules.

## Workflow

1. **Clone the repository directly** — fork PRs are not accepted.
2. **Create a branch from `main`:**
   - `feature/<name>` for new functionality
   - `fix/<name>` for bug fixes
3. **Install dependencies and pre-commit hooks:**
   ```bash
   uv sync --dev
   uv run pre-commit install
   ```
4. **Open a pull request** against `main` with a clear description of what changed and why.
5. **All CI checks must pass** — CI covers CPython 3.12–3.14, a PyPy 3.11 core/intralogistics lane, linting, type checking, and the documentation build.
6. **At least one approving review is required** before merging.
7. **If your change adds or modifies functionality, update `docs/`** accordingly.

## Merging and releases

PRs are **squash-merged into `main` by the maintainer** ([@dmezzogori](https://github.com/dmezzogori)). Contributors should not merge their own PRs or create release tags. New versions are published to PyPI by the maintainer via `v*` tags.

For the complete guidelines see the [CONTRIBUTING.md](https://github.com/dmezzogori/simulatte/blob/main/CONTRIBUTING.md) file in the repository.
