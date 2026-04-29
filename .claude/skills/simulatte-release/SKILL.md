---
name: simulatte:release
description: Use when preparing a new release of simulatte. Runs pre-flight checks (tests, lint, type check, docs build), bumps the version using semver, generates CHANGELOG entries from commits, updates all version references, and creates the git tag that triggers PyPI publishing. Use whenever the user mentions "release", "new version", "bump version", "publish to PyPI", or wants to cut a new release of the library.
disable-model-invocation: true
user-invocable: true
model: sonnet
effort: max
---

# Release Workflow

Guides the full release process for simulatte. The workflow is split into two phases: pre-flight checks catch problems before any version numbers are touched, and the release phase makes all the changes atomically.

## Pre-flight Checks

Run these in order. Stop immediately if any check fails — the point is to catch problems before modifying version numbers, since a half-bumped release is painful to unwind.

1. **Clean working tree on main**: `git status` must show a clean tree on the `main` branch. A dirty tree means uncommitted work that could accidentally get swept into the release commit.
2. **Tests**: `uv run pytest`
3. **Linting**: `uv run ruff check src tests`
4. **Type checking**: `uv run ty check src`
5. **Docs build**: `uv run zensical build` — a broken docs build means the release would ship with stale documentation on simulatte.dev.

If any step fails, report the failure clearly and stop. Do not proceed to the release phase.

## Release Phase

### Step 1: Determine version bump

Ask the user: **Is this a patch, minor, or major release?**

| Type | Example | When |
|------|---------|------|
| patch | 0.2.0 → 0.2.1 | Bug fixes only, no new features, no breaking changes |
| minor | 0.2.0 → 0.3.0 | New features, backward compatible |
| major | 0.2.0 → 1.0.0 | Breaking changes to the public API |

Read the current version from `pyproject.toml` and compute the next version.

### Step 2: Generate CHANGELOG entries

Collect commit messages since the last `v*` tag:

```bash
git log $(git describe --tags --abbrev=0)..HEAD --oneline --no-merges
```

Draft a CHANGELOG section from these commits:

```markdown
## X.Y.Z — YYYY-MM-DD

- Entry derived from commit message
- Another entry
```

Present the draft to the user and wait for them to review and edit it. The auto-generated entries are a starting point — the user decides the final wording.

### Step 3: Update version references

The version appears in multiple files. All must be updated together to avoid the drift we've seen before.

| File | Field | Example |
|------|-------|---------|
| `pyproject.toml` | `version` | `"X.Y.Z"` |
| `CITATION.cff` | `version` | `"X.Y.Z"` |
| `CITATION.cff` | `date-released` | `"YYYY-MM-DD"` (today) |
| `README.md` | bibtex `note` | `Python package version X.Y.Z` |

After updating `pyproject.toml`, run `uv lock` to sync the lockfile, then verify with `uv lock --check`.

### Step 4: Update CHANGELOG.md

Prepend the approved section to `CHANGELOG.md`.

If the file doesn't exist yet, create it:

```markdown
# Changelog

All notable changes to Simulatte are documented here.

## X.Y.Z — YYYY-MM-DD

- ...
```

### Step 5: Review and commit

Show the full diff (`git diff`) to the user. This is their chance to catch anything off before it's committed. Wait for explicit confirmation.

Commit all changes together:

```bash
git add -A
git commit -m "chore: release vX.Y.Z"
```

### Step 6: Tag and push

Create an annotated git tag:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
```

**Ask for final confirmation before pushing.** Pushing the tag triggers the PyPI publish workflow — this is the point of no return.

```bash
git push origin main --follow-tags
```
