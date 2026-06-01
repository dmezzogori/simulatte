# Design: Examples section overhaul (runnable, comprehensive)

**Date:** 2026-06-01
**Status:** Approved design — pending user spec review before planning
**Scope:** Documentation site (`docs/`), in-browser Pyodide runner (`docs/assets/javascripts/`), and supporting library API (`src/simulatte/`).

## 1. Problem

The web docs **Examples** section currently runs exactly two things in-browser:

- `examples/draco.md` — DRACO release policy (runnable via the `{ .run }` fence)
- `examples/focus.md` — FOCUS dispatching rule (runnable)
- `examples/intralogistics.md` — **not runnable**: prose, config snippets, mermaid diagrams, and GitHub links only.

This covers ~2 of ~21 user-facing mechanisms and leaves intralogistics non-runnable in the browser:

- **Release policies (~8):** `ImmediateRelease` baseline, `LumsCor`, `Slar`, `SlarLimit`, `Draco`, `ConWIP`, `ContinuousRelease`, `starvation_avoidance` (+ `on_arrival`/`on_completion`/`periodic` triggers). Only **Draco** has a runnable example.
- **Dispatching rules (13 user-facing):** SPT, EDD, ODD, MODD, CR, FCFS, WINQ (stateless); PST, S/RO, ATC, COVERT, Raghu-Rajendran (parameterized); Focus (system-state). Only **Focus** has a runnable example. (`FocusContext` and `FocusPriorityRule` are support classes for `Focus`, not standalone rules.)

## 2. Goal

Turn **Examples** into a **comprehensive, runnable reference catalog**: every release policy and every dispatching rule appears and runs in-browser, and all three intralogistics examples render their real matplotlib plots in the browser. Tutorials remain narrative teaching pages and cross-link into the galleries; no content is duplicated between the two sections.

## 3. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Coverage structure | **Themed galleries** — grouped runnable pages, each comparing a family of mechanisms side-by-side in one harness |
| 2 | Intralogistics in-browser | **Add real plot rendering** — extend the Pyodide runner to capture matplotlib figures and display them as images |
| 3 | Examples ↔ Tutorials | **Examples = runnable catalog**; Tutorials stay narrative and cross-link in; no duplication |
| 4 | Builder gap | **Add matching builders** for ConWIP / ContinuousRelease / starvation_avoidance so every gallery is a clean one-call setup |
| 5 | Spec shape | **One phased spec** (this document) with explicit phase ordering and gates |
| 6 | DRACO / FOCUS pages | **Fold into galleries**; old `draco.md` / `focus.md` become redirects |
| 7 | Gallery harness | **Inline the rich harness in each gallery script** — self-contained, no new library scenario helper; accept the duplication |

## 4. How in-browser execution works today (constraints)

- The `python { .run }` fence renders to `.highlight.run`; `pyodide-run.js` injects a **Run** button + a `<pre>` output panel and drives one `pyodide-worker.js` per page.
- The worker boots Pyodide, `loadPackage(["sqlite3", "micropip"])`, then `micropip.install(<simulatte wheel>)`. The wheel is built by `scripts/build_docs_wheel.sh` into `docs/assets/wheels/` with a `latest.json` manifest.
- Installing the wheel pulls its full dependency tree from PyPI, **including `matplotlib`** (a hard dependency in `pyproject.toml`). So matplotlib is already available in-browser; the only missing piece is rendering its output.
- **Output is text-only:** the worker streams `stdout`/`stderr` as `{kind:"stdout"|"stderr"}` messages into the `<pre>`. There is no image rendering. This is the sole reason intralogistics (whose payoff is plots) is not runnable.
- **Isolation constraint:** each `{ .run }` block runs in a fresh namespace and can import **only** from the installed `simulatte` wheel + the Python stdlib. It **cannot** import a sibling `examples/*.py` file. Each gallery's harness is therefore **inlined** in its own `{ .run }` block (decision #7) rather than imported from a shared module.

## 5. Page structure (~8 pages)

Mirror the API Reference's existing tier grouping so the docs stay internally consistent.

**Dispatching rules (3 galleries)**
1. *Stateless rules* — SPT, EDD, ODD, MODD, CR, FCFS, WINQ
2. *Parameterized rules* — PST, S/RO, ATC, COVERT, Raghu-Rajendran
3. *System-state rules* — Focus (with FocusContext/FocusPriorityRule explained inline, not as separate entries). **FOCUS is the featured entry here.**

**Release policies (3 galleries)**
4. *Workload-control release* — Immediate (baseline) vs LumsCor vs SLAR vs SlarLimit vs ContinuousRelease
5. *WIP-cap release* — ConWIP vs Draco. **DRACO is the featured entry here.**
6. *Triggers & starvation avoidance* — on_arrival / on_completion / periodic triggers + starvation_avoidance

**Intralogistics (1 page, 3 examples)**
7. Simple / Intermediate / Advanced — all runnable in-browser, Intermediate and Advanced rendering their real plots.

**Overview**
8. `examples/index.md` — catalog table linking every gallery, plus a short "how in-browser Run works" note.

Each gallery runs the same seeded arrival stream through every member and prints a **comparison table** (throughput, average time in system, average tardiness, % tardy). `draco.md` and `focus.md` are removed and replaced with redirects to galleries 5 and 3.

## 6. The gallery harness (must be non-degenerate)

**Risk:** in a minimal single-stage, no-due-date shop, most dispatching rules collapse to identical orderings — EDD/ODD/MODD/CR/PST/ATC/COVERT key off **due dates**, S/RO needs **multi-operation routing**, WINQ needs **downstream queues**. A comparison table over such a shop is noise.

**Resolution:** each gallery script **inlines its own self-contained harness** (decision #7) — no shared library scenario helper. Within a single gallery, the inline harness is built **once** and every member (rule or policy) runs against that same seeded, **multi-stage, due-dated** shop, then results are tabulated. This:

- makes every rule non-degenerate (due dates + multi-stage routing + multiple queues present within the inline harness),
- guarantees a **fair** comparison (identical scenario across all members *of that gallery*),
- satisfies the in-browser isolation constraint trivially (everything is inline; only `simulatte` is imported).

The cost is accepted duplication of harness setup across gallery scripts. To keep each inline harness short, prefer configuring the **existing** builders to emit the rich shop: dispatching galleries call `build_immediate_release_system(env, priority_policies=<rule>, ...)` in a loop; policy galleries call the per-policy builders. The harness "richness" lives in the arguments passed inline, not in a new helper.

**Verification task (Phase 2):** confirm `build_immediate_release_system` (and the policy builders) can be parameterized to produce multi-stage routing + due dates. If they can, the inline harness is a short builder call; if a needed knob is missing, **extend the existing builder's parameters** (not add a new scenario builder) so the inline call stays compact. Map each rule to the structural feature it needs and assert the inline harness provides all of them before authoring galleries.

## 7. In-browser plot rendering (Pyodide feature)

Add a new message kind so figures appear in the output panel.

**Worker (`pyodide-worker.js`)** — run a setup shim **once after boot** (not prepended to the user's displayed source, so the `{ .run }` code stays clean):

```python
import matplotlib
matplotlib.use("AGG")            # non-interactive; safe in a worker (no DOM)
import matplotlib.pyplot as plt
import base64, io
_orig_show = plt.show
def _capture_show(*args, **kwargs):
    for num in plt.get_fignums():
        fig = plt.figure(num)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        _emit_image(b64)         # JS callback registered into the namespace
        plt.close(fig)
plt.show = _capture_show
```

The worker registers `_emit_image` (a JS function) into the run namespace; it posts `{ id, kind: "image", data: <base64> }` to the controller.

**Controller (`pyodide-run.js`)** — on `kind:"image"`, append an `<img src="data:image/png;base64,…">` to the output panel (new branch in `worker.onmessage` + a `ui.image()` method).

**Result:** intralogistics example code remains **byte-for-byte identical** to the desktop version — `ts_collector.plot_fleet_utilization()` calls `plt.show()` internally, which the shim intercepts. This mirrors the existing test convention (`tests/intralogistics/test_examples.py` already monkeypatches `plt.show` to a no-op).

## 8. New builders

Add to `src/simulatte/builders.py` (each with unit tests, parallel to existing builders):

- `build_conwip_system(...)`
- `build_continuous_release_system(...)`
- `build_starvation_avoidance_system(...)`

so galleries 4–6 are clean one-call setups like `build_draco_system`. APIs are unstable per `CLAUDE.md`, so expanding the builder surface is acceptable, and the builders are reusable beyond the docs.

## 9. Maintenance & performance

- **Single source of truth:** every gallery is backed by a tested `examples/*.py` script run via `runpy.run_path` (the existing pattern in `tests/`). The docs embed that script **verbatim** in the `{ .run }` fence — the example file and the embedded block are identical.
- **Drift guard:** add a lightweight test asserting each embedded `{ .run }` block is an **exact match** of its `examples/*.py` source file, so docs and tested code cannot silently diverge.
- **Performance budget:** Pyodide runs several× slower than native. Target **a few seconds per page**. Because the embedded block equals the example file, each script carries a **single horizon constant sized for the in-browser budget** (calibrate empirically; expected order ~500–800 time units rather than the current 2000), and galleries cap members-per-comparison so a multi-member run does not read as a hang. Desktop users run the same (now faster) script. Where a longer full-shift run is genuinely valuable — notably the heavy intralogistics shift examples — keep it as a **separate, non-embedded** script referenced from the page, not as the runnable block.

## 10. Phased plan with gates

**Phase 1 — Plot-capture infrastructure (de-risk first).**
Build the worker shim + controller image branch + message protocol. Prove it on the **single** Simple-or-Intermediate intralogistics example end-to-end (real PNG renders in the browser) **before** authoring the other intralogistics content.
*Gate:* one intralogistics example renders a plot in-browser locally via `zensical serve`.

**Phase 2 — Library API.**
Verify/extend the demo scenario (multi-stage + due dates); add the 3 builders; unit tests for builders and scenario; confirm matplotlib imports cleanly in the runner.
*Gate:* `uv run pytest` green; scenario differentiates all 13 rules (sanity check that the comparison table has distinct rows).

**Phase 3 — Docs content.**
Author the 6 galleries + intralogistics rewrite + overview, each backed by a tested `examples/*.py`. Fold DRACO/FOCUS, add redirects, update `zensical.toml` nav, add the drift-guard test, add cross-links from Tutorials.
*Gate:* every `{ .run }` block runs in-browser within the performance budget; all example scripts pass their `runpy` tests; nav and redirects verified in a local build.

## 11. Out of scope

- Interactive/parameterized in-browser widgets (sliders to tweak weights live).
- Persisting or downloading plot images.
- Rewriting the Tutorials narrative content (only cross-links are added).
- Any change to the gymnasium/experimental examples.

## 12. Success criteria

- All ~8 example pages present; every release policy and every dispatching rule appears in a runnable gallery.
- All three intralogistics examples run in-browser and render their plots.
- Each gallery's comparison table shows **distinct** rows (no degenerate ties masking the rule differences).
- Every embedded `{ .run }` block is backed by a tested `examples/*.py`, guarded against drift.
- In-browser runs complete within a few seconds per page.
- Tutorials cross-link into the galleries; no duplicated content.

## 13. Open verification items (resolve during implementation, not blockers)

1. Whether `build_immediate_release_system` (and the policy builders) can be parameterized to emit multi-stage routing + due dates, or need a new parameter added so the inline harness stays compact (Phase 2). No new scenario builder is to be added (decision #7).
2. Exact reduced in-browser horizon that keeps every gallery within the performance budget while still differentiating mechanisms (calibrate in Phase 1/3).
3. Confirm `gymnasium` (a hard dependency, imported transitively) does not slow or break wheel install in-browser; if it does, consider making it an optional dependency.
