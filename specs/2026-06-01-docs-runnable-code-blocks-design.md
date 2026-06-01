# Design: Copy buttons + in-browser execution for the simulatte docs

- **Date:** 2026-06-01
- **Status:** Approved (pending spec review)
- **Scope:** `docs/` (simulatte.dev), `zensical.toml`, `.github/workflows/docs.yml`
- **Author:** brainstorming session

## 1. Summary

Add two features to every code example across the simulatte documentation site
(built with Zensical, a Material-for-MkDocs port):

1. **Copy button** on all code blocks — one-click copy without manual selection.
2. **"Run in browser"** on *author-tagged* code blocks — execute the example
   directly in the browser via [Pyodide](https://pyodide.org) (CPython compiled
   to WebAssembly), showing captured output inline.

Feasibility for both was confirmed empirically before this design (see §9).

## 2. Goals / Non-goals

### Goals
- Copy button on **all** code blocks, site-wide, with zero per-block authoring.
- A clean, opt-in marker that makes a **self-contained** code block runnable.
- In-browser execution that matches the exact `simulatte` source on `main`
  (not just the latest PyPI release).
- A CI guarantee that every block tagged runnable actually runs without error.
- A UI that does not regress page load: nothing heavy loads until a user asks.

### Non-goals (v1)
- **Plot rendering.** matplotlib needs a DOM-bound backend marshalled across the
  worker boundary; deferred to a later iteration. Only one tutorial uses plots,
  and it simply won't be tagged runnable.
- **Running incremental tutorial snippets.** Many tutorial pages split one
  program across several stateful blocks (block 1 defines `env`/`s1`/`s2`,
  block 3 runs them). These are not self-contained and will not be tagged in v1.
- **Offline / fully self-hosted wheels.** v1 relies on the pinned Pyodide CDN for
  the distribution packages (numpy, matplotlib) and PyPI for the pure-Python deps,
  exactly as the feasibility spike did.

## 3. Locked decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Which blocks are runnable | **Curated, author-tagged, each self-contained** | Every Run button is guaranteed to work in isolation; no notebook-style cross-block state. |
| simulatte build the examples run against | **Wheel built from source in CI** | API is explicitly unstable; docs on `main` may use symbols newer than the latest PyPI release. Examples must match `main`. |
| Pyodide host thread | **Web Worker** | Keeps the UI responsive during the multi-MB first load and during longer simulations. |
| When the runtime loads | **Lazy, on first Run click** | No cost to readers who don't run anything. |

## 4. Feature 1 — Copy button

### Mechanism
Zensical bundles the Material for MkDocs clipboard implementation (JS + CSS +
i18n strings already present in the installed package). The feature is gated
behind the `content.code.copy` theme feature flag, which the default Zensical
scaffold even ships enabled — it is simply absent from this project's
`theme.features`.

### Change
In `zensical.toml`, add to `[project.theme].features`:

```toml
"content.code.copy",
```

That is the entire change. It applies to every code block site-wide (Python,
Bash, mermaid-excluded fences, API reference, etc.). No new assets, no custom JS.

### Verification
Confirmed by **building** the site and visually checking that the hover copy
button renders and copies (not by grep) — see §9 note from review.

## 5. Feature 2 — Run in browser (Pyodide)

### 5.1 Tagging (opt-in marker)

A runnable block carries a `.run` class on its fence:

````markdown
```python { .run }
# a self-contained example that prints its results
```
````

A small controller script augments **only** `.run` blocks. Every other block is
untouched (highlight + copy as today).

**Marker syntax is validated against the live build during implementation.**
This project overrides `markdown_extensions` (it lists only `pymdownx.highlight`
and `pymdownx.superfences`), so the full Material default extension set is **not**
assumed active. Resolution order:
1. Preferred: `{ .run }` brace attribute on the fence (superfences native).
2. If that does not emit a targetable class, enable `attr_list` in
   `zensical.toml` and use `{ .run }`.
3. Last-resort fallback: an HTML-comment sentinel (`<!-- run -->`) immediately
   before the fence, which the controller matches to the following code block.

Whichever mechanism is chosen, the contract for the rest of the system is:
**"the controller can find each runnable block and read its source text."**

### 5.2 Runtime architecture

Two scripts plus one stylesheet, injected via `extra_javascript` / `extra_css`:

- **`docs/assets/javascripts/pyodide-run.js`** — main-thread controller.
  - On `DOMContentLoaded`, find every `.run` block; inject a **Run** button and a
    collapsible **output panel** beneath each.
  - Owns the lifecycle of a single Web Worker per page (created lazily).
  - Marshals `{ id, source }` to the worker on Run; receives
    `{ id, kind: 'status'|'stdout'|'stderr'|'result'|'error', text }` messages and
    renders them into the matching output panel.
  - Handles re-entrancy: a second Run while the runtime is still booting queues
    behind the same init promise; concurrent runs are serialized per page.
- **`docs/assets/javascripts/pyodide-worker.js`** — the worker.
  - Boot sequence (proven by the spike, §9):
    1. `importScripts(<pinned pyodide.js>)` / `loadPyodide({ indexURL })`.
    2. `await pyodide.loadPackage(["sqlite3", "micropip"])`
       — `sqlite3` is **required**: `simulatte/logger.py` does a top-level
       `import sqlite3`, which is unvendored in Pyodide, and `environment.py`
       imports the logger, so the whole core is otherwise unimportable.
    3. `micropip.install(<simulatte wheel URL>)` — pulls the transitive tree
       (numpy + matplotlib from the Pyodide distribution; simpy, loguru,
       tabulate, tqdm, gymnasium from PyPI).
  - Initialized **once per page**, reused across that page's Run buttons.
  - Per Run: execute the block's source in a **fresh** namespace (each tagged
    block is self-contained), redirecting `stdout`/`stderr` back to the
    controller; post a final `result` or `error` (with traceback) message.
- **`docs/assets/stylesheets/pyodide-run.css`** — Run button + output panel,
  styled with Material CSS custom properties (`--md-*`) so it tracks the
  light/dark palette toggle.

### 5.3 Pyodide version pinning
Pin Pyodide to a specific release (spike used `0.28.3`). The distribution wheels
(numpy/matplotlib) are fetched from that same versioned CDN path
(`https://cdn.jsdelivr.net/pyodide/v<VERSION>/full/`). Pinning avoids silent
breakage from upstream changes.

### 5.4 Package source — wheel built in CI
`.github/workflows/docs.yml` gains a step **before** the Zensical build:

1. `uv build --wheel`
2. Copy `dist/simulatte-*.whl` into `docs/assets/wheels/` **keeping its real PEP 427
   filename** (`simulatte-<ver>-py3-none-any.whl`), and write a manifest
   `docs/assets/wheels/latest.json` = `{"wheel": "<that filename>"}`.

The wheel must **not** be renamed to a "stable" name: `micropip.install(url)` parses
the filename as a PEP 427 name *before* reading the wheel, so a renamed
`simulatte.whl` raises `InvalidWheelFilename` (verified empirically against Pyodide
0.28.3). The controller therefore fetches `latest.json` to discover the real filename,
then installs from `assets/wheels/<filename>`. Examples always match `main`.

> Note: `.gitignore` ignores any directory named `wheels/`, so a built wheel under
> `docs/assets/wheels/` is naturally untracked — correct, since it is a CI build
> artifact, not source. Local dev (§5.5) regenerates it on demand.

### 5.5 Local development
`zensical serve` does not run CI. Provide a one-line helper (`scripts/` entry),
e.g. `scripts/build_docs_wheel.sh`, that runs `uv build --wheel`, copies the
result into `docs/assets/wheels/` under its real name, and writes `latest.json`,
so Run works locally. CI calls the same helper.

## 6. Data flow (one Run click)

```
[reader clicks Run]
  controller --{id, source}--> worker
    worker: (first time) boot Pyodide + sqlite3 + micropip + install wheel
            --{status:"Downloading Python runtime…"}--> controller (progress UI)
    worker: exec source in fresh globals, capture stdout/stderr
            --{stdout|stderr chunks}--> controller (append to output panel)
    worker --{result | error+traceback}--> controller (finalize panel)
```

## 7. Error handling
- **Runtime boot failure** (network/CDN/PyPI down): worker posts `error`; the
  panel shows a friendly "Couldn't load the Python runtime — check your
  connection and retry" with a Retry affordance. The page itself never breaks.
- **User-code exception**: caught in the worker; full Python traceback rendered
  in the output panel in an error style. Subsequent runs still work (fresh
  namespace each time).
- **Marker found but block not self-contained** (author error): surfaced by the
  CI smoke test (§8), not at runtime.
- **No JS / very old browser**: blocks degrade to plain highlighted code with a
  copy button; the Run button is only injected when the controller runs.

## 8. Testing
- **CI smoke test (the runnable-block invariant).** Reuse the feasibility spike
  harness: a Node + Pyodide script that extracts every `.run`-tagged block from
  `docs/`, installs the freshly built wheel, and executes each block, failing the
  build if any raises. This makes "tagged ⇒ runnable" an enforced invariant.
  Runs in `docs.yml` (and is independently runnable locally).
- **Manual/visual:** build the site; confirm (a) copy button renders and copies,
  (b) a tagged block runs and shows correct stdout, (c) light/dark theming of the
  injected UI, (d) an untagged block is visually unchanged.

## 9. Feasibility evidence (already gathered)

A throwaway Node + Pyodide (`0.28.3`) spike:
- Installed `simulatte 0.10.0` (PyPI has a `py3-none-any` wheel) + full transitive
  tree under WASM.
- Found and fixed the one real blocker: top-level `import sqlite3` in
  `simulatte/logger.py` (unvendored in Pyodide) → resolved with
  `loadPackage("sqlite3")`.
- After the fix, **every** import path loaded cleanly: `environment`,
  `shopfloor`, `logger`, `intralogistics.fleet`, `experimental.gymnasium`.
- Ran the actual `docs/introduction/basic-usage.md` block to correct output:
  `Job makespan: 5.0` / `Server utilization: 100.0%`.
- First load fetched ~15 wheels (cacheable); warm runs ~2.5s.

## 10. Files touched
- `zensical.toml` — add `content.code.copy`; add `extra_javascript` /
  `extra_css`; add marker extension (`attr_list`) only if §5.1 requires it.
- `docs/assets/javascripts/pyodide-run.js` — **new** (controller).
- `docs/assets/javascripts/pyodide-worker.js` — **new** (worker).
- `docs/assets/stylesheets/pyodide-run.css` — **new** (styling).
- `.github/workflows/docs.yml` — add wheel-build + copy step; add smoke-test step.
- A local dev helper (Makefile target or `scripts/`) to build the wheel for
  `zensical serve`.
- An initial set of self-contained blocks tagged `.run` (starting with
  `basic-usage.md`), each verified by the smoke test.
- `docs/` content update per CONTRIBUTING ("update docs when changing
  functionality") describing the Run feature if a user-facing note is warranted.

## 11. Rollout / sequencing
1. Copy button (one-line flag) + build to confirm.
2. Controller + worker + CSS; pin Pyodide; wire `extra_javascript`/`extra_css`.
3. Local wheel helper; tag `basic-usage.md`; verify Run end-to-end locally.
4. CI: wheel build + copy into site; smoke test over tagged blocks.
5. Tag the remaining self-contained examples; smoke test enforces correctness.

## 12. Housekeeping note
This spec lives in top-level `specs/` (outside the published `docs/` tree) on
purpose: internal planning docs placed under `docs/` have repeatedly had to be
removed from the published site. Consider adding `specs/` to the sdist
`exclude` list in `pyproject.toml` during implementation so it is not shipped in
PyPI source distributions.
