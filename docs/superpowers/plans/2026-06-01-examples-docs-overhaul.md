# Examples Section Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the docs **Examples** section into a comprehensive, runnable reference catalog — every release policy and dispatching rule appears in an in-browser runnable themed gallery, and all intralogistics examples render their real matplotlib plots in the browser.

**Architecture:** Three sequenced phases. (1) Extend the Pyodide runner so matplotlib figures render as inline `<img>` images. (2) Add three library builders so every gallery is a clean one-call setup. (3) Author six themed gallery pages plus an intralogistics rewrite and overview, each backed by a tested `examples/*.py` whose code is embedded verbatim in a `{ .run }` fence. Each gallery inlines its own harness (decision #7 in the spec) — the existing `build_immediate_release_system` already produces a multi-stage, due-dated shop, so no scenario helper is needed.

**Tech Stack:** Python 3.12+, SimPy, matplotlib, Pyodide 0.28.3 (in-browser), Zensical/MkDocs docs, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-06-01-examples-docs-overhaul-design.md`

---

## File Structure

**Phase 1 — plot rendering (JS/CSS, no Python):**
- Modify: `docs/assets/javascripts/pyodide-worker.js` — add a one-time matplotlib capture shim + `_emit_image` bridge; post `{kind:"image"}` messages.
- Modify: `docs/assets/javascripts/pyodide-run.js` — handle `kind:"image"` by appending an `<img>` to the output panel (`ui.image()`).
- Modify: `docs/assets/stylesheets/pyodide-run.css` — style rendered images.

**Phase 2 — library builders (Python):**
- Modify: `src/simulatte/builders.py` — add `build_conwip_system`, `build_continuous_release_system`, `build_starvation_avoidance_system`.
- Create: `tests/core/test_builders_new.py` — tests for the three builders.

**Phase 3 — docs content:**
- Create gallery scripts (tested source of truth):
  - `examples/gallery_dispatching_stateless.py`
  - `examples/gallery_dispatching_parameterized.py`
  - `examples/gallery_dispatching_focus.py`
  - `examples/gallery_release_workload.py`
  - `examples/gallery_release_wip.py`
  - `examples/gallery_release_triggers.py`
- Modify (reduce horizons for in-browser budget): `examples/intralogistics_intermediate.py`, `examples/intralogistics_advanced.py`.
- Create gallery docs pages:
  - `docs/examples/dispatching-stateless.md`
  - `docs/examples/dispatching-parameterized.md`
  - `docs/examples/dispatching-focus.md`
  - `docs/examples/release-workload.md`
  - `docs/examples/release-wip.md`
  - `docs/examples/release-triggers.md`
- Modify: `docs/examples/index.md` (catalog rewrite), `docs/examples/intralogistics.md` (embed runnable scripts), `zensical.toml` (nav).
- Replace with redirect stubs: `docs/examples/draco.md`, `docs/examples/focus.md`.
- Create: `tests/core/test_gallery_examples.py` (runpy execution tests) and `tests/test_docs_run_blocks.py` (drift guard: embedded `{ .run }` block == `examples/*.py`).
- Modify (cross-links only): `docs/tutorials/release-control-and-dispatching.md`, `docs/tutorials/comparing-release-policies.md`, `docs/tutorials/building-an-agv-system.md`.

---

# PHASE 1 — In-browser plot rendering

**Phase gate:** After Task 1.4, a `{ .run }` block that draws a matplotlib figure renders a PNG image in the output panel locally via `uv run zensical serve`.

> Note: the repo has no JS unit-test harness, so Phase 1 verification is manual via a local docs serve. Tasks 1.1–1.3 are implementation; Task 1.4 is the manual gate.

### Task 1.0: Pre-flight — confirm the in-browser path works at all (do this FIRST)

This is the make-or-break unknown sitting behind the easy JS/CSS tasks: whether `micropip.install(wheel)` resolves simulatte's full dependency tree (notably **matplotlib** *and* **gymnasium**, spec §13 item 3) in-browser, and whether Agg `savefig` works. Verify in ~5 minutes before writing any JS.

- [ ] **Step 1: Build the wheel and serve the docs (current `main`, no changes yet)**

```bash
./scripts/build_docs_wheel.sh
uv run zensical serve
```

- [ ] **Step 2: Confirm the existing draco page runs in-browser**

Open the local URL → Examples → "Draco release", click **▶ Run**. Expected: the DRACO text table appears. This proves the wheel + full dep tree (including matplotlib and gymnasium) install under Pyodide. If it errors, resolve the dependency/install problem (e.g. make `gymnasium` an optional dependency) **before** Phase 1 — the plot work depends on this baseline working.

- [ ] **Step 3: Confirm Agg `savefig` works under Pyodide**

Temporarily add a `docs/examples/_agg_smoke.md` with a `{ .run }` block containing:

```python
import matplotlib
matplotlib.use("AGG")
import matplotlib.pyplot as plt, io, base64
fig, ax = plt.subplots(); ax.plot([0, 1, 2], [0, 1, 4])
buf = io.BytesIO(); fig.savefig(buf, format="png")
print("savefig bytes:", len(buf.getvalue()))
```
Run it; expected output `savefig bytes: <a few thousand>`. Then `rm docs/examples/_agg_smoke.md`. This confirms figure rendering works headless; only the JS plumbing (Tasks 1.1–1.3) remains.

If Steps 2–3 both pass, proceed. No commit needed (no tracked changes).

### Task 1.1: Controller renders image messages

**Files:**
- Modify: `docs/assets/javascripts/pyodide-run.js`

- [ ] **Step 1: Add the `image` branch to the worker message handler**

In `ensureWorker()`, the `worker.onmessage` handler currently routes `status`/`stdout`/`stderr`/`error`/`done`. Add an `image` branch. Change this block:

```javascript
        if (m.kind === "status") h.status(m.text);
        else if (m.kind === "stdout") h.append(m.text, "stdout");
        else if (m.kind === "stderr") h.append(m.text, "stderr");
        else if (m.kind === "error") h.error(m.text);
        else if (m.kind === "done") h.done();
```

to:

```javascript
        if (m.kind === "status") h.status(m.text);
        else if (m.kind === "stdout") h.append(m.text, "stdout");
        else if (m.kind === "stderr") h.append(m.text, "stderr");
        else if (m.kind === "image") h.image(m.data);
        else if (m.kind === "error") h.error(m.text);
        else if (m.kind === "done") h.done();
```

- [ ] **Step 2: Register an `image` handler in `runSource`**

In `runSource`, the `handlers.set(id, {...})` object defines `status`, `append`, `error`, `done`. Add `image`:

```javascript
        handlers.set(id, {
          status: (t) => ui.status(t),
          append: (t, cls) => ui.append(t, cls),
          image: (data) => ui.image(data),
          error: (t) => ui.append(t, "error"),
          done: () => {
            handlers.delete(id);
            ui.status("");
            ui.setBusy(false);
            resolve();
          },
        });
```

- [ ] **Step 3: Add `ui.image()` to `buildUI`**

In `buildUI`, the `ui` object defines `reset`, `setBusy`, `status`, `append`. Add an `image` method that appends an `<img>` (base64 PNG) to the same `output` panel and makes it visible:

```javascript
      append(text, cls) {
        output.classList.add("sim-run__output--visible");
        const span = document.createElement("span");
        span.className = "sim-run__line sim-run__line--" + cls;
        // Pyodide's batched stdout/stderr fires once per line with the trailing
        // newline stripped, so re-add it (the <pre> is white-space: pre-wrap).
        span.textContent = text + "\n";
        output.appendChild(span);
      },
      image(data) {
        output.classList.add("sim-run__output--visible");
        const img = document.createElement("img");
        img.className = "sim-run__img";
        img.src = "data:image/png;base64," + data;
        img.alt = "Plot output";
        output.appendChild(img);
      },
```

- [ ] **Step 4: Commit**

```bash
git add docs/assets/javascripts/pyodide-run.js
git commit -m "feat(docs): render image messages in the in-browser runner output panel"
```

### Task 1.2: Worker captures matplotlib figures

**Files:**
- Modify: `docs/assets/javascripts/pyodide-worker.js`

- [ ] **Step 1: Add a capture-shim constant**

Below the existing `PYODIDE_CDN` constant near the top of the file, add the Python shim that forces the Agg backend and monkeypatches `plt.show` to emit each open figure as a base64 PNG. The shim references a JS function `_emit_image` that we inject into each run's namespace (Step 3):

```javascript
// Run once after boot. Forces a DOM-free matplotlib backend and turns every
// plt.show() (which intralogistics plot_* helpers call internally) into a
// base64 PNG posted to the controller as an {kind:"image"} message. Example
// code stays identical to the desktop version.
const MPL_CAPTURE_SHIM = `
import matplotlib
matplotlib.use("AGG")
import matplotlib.pyplot as _plt
import base64 as _base64, io as _io

def _capture_show(*args, **kwargs):
    for _num in _plt.get_fignums():
        _fig = _plt.figure(_num)
        _buf = _io.BytesIO()
        _fig.savefig(_buf, format="png", bbox_inches="tight", dpi=110)
        _emit_image(_base64.b64encode(_buf.getvalue()).decode("ascii"))
        _plt.close(_fig)

_plt.show = _capture_show
`;
```

- [ ] **Step 2: Run the shim once after boot**

In `bootPyodide`, after `micropip.destroy();` and before `return pyodide;`, run the shim once so every subsequent run inherits the patched `plt.show`. matplotlib is a hard dependency of simulatte and is already installed by `micropip.install(wheelUrl)`:

```javascript
  micropip.destroy();
  status("Preparing plot capture…");
  await pyodide.runPythonAsync(MPL_CAPTURE_SHIM);
  return pyodide;
```

- [ ] **Step 3: Inject the `_emit_image` bridge into each run namespace**

In `self.onmessage`, the run path builds `namespace = pyodide.toPy({ __name__: "__main__" })`. After that line, register `_emit_image` into the namespace so the shim (which runs in the default global scope) and user code can both reach it. Because the shim captured `_emit_image` by name at call time from the run globals, set it on the main globals as well. Replace:

```javascript
    namespace = pyodide.toPy({ __name__: "__main__" }); // fresh, isolated per run
    await pyodide.runPythonAsync(source, { globals: namespace });
    self.postMessage({ id, kind: "done" });
```

with:

```javascript
    namespace = pyodide.toPy({ __name__: "__main__" }); // fresh, isolated per run
    // Bridge: the capture shim's plt.show calls _emit_image(base64) -> image msg.
    const emitImage = (b64) => self.postMessage({ id, kind: "image", data: b64 });
    pyodide.globals.set("_emit_image", emitImage);
    await pyodide.runPythonAsync(source, { globals: namespace });
    self.postMessage({ id, kind: "done" });
```

> Why `pyodide.globals` and not `namespace`: the shim is defined once in the default global scope, so its `_emit_image` lookup resolves against `pyodide.globals`. Setting it there each run rebinds it to the current run's `id`.

- [ ] **Step 4: Commit**

```bash
git add docs/assets/javascripts/pyodide-worker.js
git commit -m "feat(docs): capture matplotlib figures as base64 PNG in the Pyodide worker"
```

### Task 1.3: Style rendered images

**Files:**
- Modify: `docs/assets/stylesheets/pyodide-run.css`

- [ ] **Step 1: Append image styling**

Add to the end of the file so plots are responsive and visually separated from text lines:

```css
.sim-run__img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0.6rem 0;
  border-radius: 0.2rem;
  background-color: #ffffff;
}
```

- [ ] **Step 2: Commit**

```bash
git add docs/assets/stylesheets/pyodide-run.css
git commit -m "style(docs): style in-browser rendered plot images"
```

### Task 1.4: Manual verification gate

**Files:**
- Create (temporary): `docs/examples/_plot_smoke.md`

- [ ] **Step 1: Create a temporary smoke-test page**

```markdown
# Plot smoke test (temporary)

```python { .run }
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1, 2, 3], [0, 1, 4, 9], marker="o")
ax.set_title("smoke test")
print("about to show")
plt.show()
print("done")
```
```

- [ ] **Step 2: Build the wheel and serve the docs**

Run:
```bash
./scripts/build_docs_wheel.sh
uv run zensical serve
```
Open the printed local URL, navigate to the "Plot smoke test" page, click **▶ Run**.

Expected: status cycles through "Downloading Python runtime…" → "Installing simulatte…" → "Preparing plot capture…"; then the output panel shows `about to show`, a line plot image, then `done`.

- [ ] **Step 3: Remove the temporary page**

```bash
rm docs/examples/_plot_smoke.md
```

- [ ] **Step 4: Commit (gate passed)**

```bash
git add -A
git commit -m "test(docs): verify in-browser matplotlib rendering (smoke page removed)"
```

If the image does not render, debug Tasks 1.1–1.3 (check the browser console for worker errors) before proceeding to Phase 3.

---

# PHASE 2 — Library builders

**Phase gate:** After Task 2.3, `uv run pytest tests/core/test_builders_new.py -v` is green and the three builders each produce a system that completes a non-trivial number of jobs.

> All three builders follow the existing pattern in `src/simulatte/builders.py`: construct `ShopFloor`, `servers`, optional `PreShopPool`, a `Router` with the standard `sku_*` config, then wire the policy. Reuse the exact `Router` kwargs block used by `build_lumscor_system` (the `inter_arrival_distribution`, `sku_distributions`, `sku_routings`, `sku_service_times`, `due_date_offset_distribution` lines).

### Task 2.1: `build_conwip_system`

**Files:**
- Modify: `src/simulatte/builders.py`
- Test: `tests/core/test_builders_new.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_builders_new.py`:

```python
from __future__ import annotations

import random

from simulatte.builders import (
    build_conwip_system,
    build_continuous_release_system,
    build_starvation_avoidance_system,
)
from simulatte.environment import Environment


def test_build_conwip_system_runs_and_caps_wip() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
    # ConWIP caps concurrent shop jobs at wip_cap.
    assert shop_floor.maximum_shopfloor_jobs <= 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_conwip_system_runs_and_caps_wip -v`
Expected: FAIL with `ImportError: cannot import name 'build_conwip_system'`.

- [ ] **Step 3: Implement `build_conwip_system`**

Update the imports at the top of `src/simulatte/builders.py`. Add two new policy imports next to the existing `from simulatte.policies.draco import Draco` block:

```python
from simulatte.policies.conwip import ConWIP
from simulatte.policies.continuous_release import ContinuousRelease
```

and **replace** the existing line `from simulatte.policies.triggers import periodic_trigger` with:

```python
from simulatte.policies.triggers import on_completion_trigger, periodic_trigger
```

Append this function to `src/simulatte/builders.py`:

```python
def build_conwip_system(
    env: Environment,
    *,
    wip_cap: int,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a ConWIP (Constant Work-In-Process) pull system.

    Jobs wait in the Pre-Shop Pool and are released — earliest due date
    first — only while the shop holds fewer than ``wip_cap`` jobs. Release
    is re-checked on every job completion and on every PSP arrival.

    Args:
        env: The simulation environment.
        wip_cap: Maximum number of jobs allowed on the shop floor at once.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)
        >>> env.run(until=1000)
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
    )
    conwip = ConWIP(wip_cap=wip_cap)
    env.process(on_completion_trigger(shop_floor, psp, conwip.on_completion_release))
    psp.on_arrival(conwip.on_arrival_release)

    return psp, servers, shop_floor, router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_conwip_system_runs_and_caps_wip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_builders_new.py
git commit -m "feat: add build_conwip_system builder"
```

### Task 2.2: `build_continuous_release_system`

**Files:**
- Modify: `src/simulatte/builders.py`
- Test: `tests/core/test_builders_new.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_builders_new.py`:

```python
def test_build_continuous_release_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_continuous_release_system(
            env, wl_norm_level=6.0, allowance_factor=2
        )
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_continuous_release_system_runs -v`
Expected: FAIL with `ImportError: cannot import name 'build_continuous_release_system'`.

- [ ] **Step 3: Implement `build_continuous_release_system`**

`ContinuousRelease` requires `CorrectedWIPStrategy` on the shopfloor (it raises `TypeError` otherwise). Append to `src/simulatte/builders.py`:

```python
def build_continuous_release_system(
    env: Environment,
    *,
    wl_norm_level: float,
    allowance_factor: int = 2,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a Continuous Release (workload-controlled) pull system.

    Jobs are held in the Pre-Shop Pool and released continuously — on each
    job completion and on PSP arrival — when their corrected workload
    contribution keeps every server in their routing at or below
    ``wl_norm_level``. Requires ``CorrectedWIPStrategy`` on the shopfloor.

    Args:
        env: The simulation environment.
        wl_norm_level: Corrected workload norm applied uniformly to every server.
        allowance_factor: Buffer time per server for due-date planning.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_continuous_release_system(
        ...     env, wl_norm_level=6.0
        ... )
        >>> env.run(until=1000)
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    shop_floor.set_wip_strategy(CorrectedWIPStrategy())
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
    )
    cr = ContinuousRelease(
        wl_norm=dict.fromkeys(servers, float(wl_norm_level)),
        allowance_factor=int(allowance_factor),
    )
    env.process(on_completion_trigger(shop_floor, psp, cr.on_completion_release))
    psp.on_arrival(cr.on_arrival_release)

    return psp, servers, shop_floor, router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_continuous_release_system_runs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_builders_new.py
git commit -m "feat: add build_continuous_release_system builder"
```

### Task 2.3: `build_starvation_avoidance_system`

**Files:**
- Modify: `src/simulatte/builders.py`
- Test: `tests/core/test_builders_new.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_builders_new.py`:

```python
def test_build_starvation_avoidance_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    # Starvation-only release keeps the first server fed, so some jobs finish.
    assert len(shop_floor.jobs_done) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_starvation_avoidance_system_runs -v`
Expected: FAIL with `ImportError: cannot import name 'build_starvation_avoidance_system'`.

- [ ] **Step 3: Implement `build_starvation_avoidance_system`**

This is the minimal pull policy: release on PSP arrival when the job's first server is idle (`starvation_avoidance`), and re-check on every completion (release any pooled job whose first server is now idle). `starvation_avoidance` and `Server.is_idle` are already used together in `src/simulatte/policies/starvation_avoidance.py`. Append to `src/simulatte/builders.py`:

```python
def build_starvation_avoidance_system(
    env: Environment,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a starvation-avoidance-only pull system.

    The simplest pull policy: a job is released from the Pre-Shop Pool only
    when its first routing server is idle — checked on PSP arrival and again
    on every job completion. There is no workload norm or WIP cap; release is
    driven purely by first-server starvation.

    Args:
        env: The simulation environment.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        >>> env.run(until=1000)
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
    )

    def _release_idle_first_server(_triggering_job: ProductionJob, _server: Server) -> None:
        for job in list(psp.jobs):
            if job.servers[0].is_idle:
                psp.release(job)

    psp.on_arrival(starvation_avoidance)
    shop_floor.on_processing_end(_release_idle_first_server)

    return psp, servers, shop_floor, router
```

> If `psp.jobs` is not the correct accessor for pooled jobs, check `src/simulatte/psp.py` for the public iterable of current jobs and use that name. The `on_completion_trigger` docstring in `triggers.py` references `psp.jobs`, so it is the expected name.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_builders_new.py::test_build_starvation_avoidance_system_runs -v`
Expected: PASS

- [ ] **Step 5: Run the full builder test file and lint**

Run:
```bash
uv run pytest tests/core/test_builders_new.py -v
uv run ruff check src/simulatte/builders.py
uv run ty check src/simulatte/builders.py
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/builders.py tests/core/test_builders_new.py
git commit -m "feat: add build_starvation_avoidance_system builder"
```

---

# PHASE 3 — Docs content

**Phase gate:** After Task 3.10, every gallery and intralogistics page runs in-browser within a few seconds, all `examples/*.py` pass their `runpy` tests, the drift-guard test passes, nav and redirects are verified in a local build.

> **Shared gallery skeleton.** Every dispatching/policy gallery follows the same shape: a `SEED`, a small in-browser `HORIZON`, a dict/list of named members, a `run_*` helper that reseeds → builds → runs → returns metrics, and a `main()` that prints a fixed-width comparison table. The exact metric computation is identical across galleries:
> - `done = shop_floor.jobs_done`; `n = len(done)`
> - `avg_tis = shop_floor.average_time_in_system`
> - `tardiness = [max(0.0, j.lateness) for j in done]`
> - `mean_tard = sum(tardiness) / n if n else 0.0`
> - `pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0`
>
> `HORIZON` starts at `800.0`; if a page feels slow in-browser (Task 3.10), reduce it. If any member completes 0 jobs, tune that member's parameters (e.g. `wl_norm_level`, `wip_cap`) upward until the table row is non-trivial.

> **Per-page docs pattern (applies to Tasks 3.1–3.6).** Each gallery doc page contains: a short intro paragraph, a `See also` link to the relevant API page, a `## Comparison` section embedding the example script verbatim in a ` ```python { .run } ` fence, a `**Run it:**` `uv run python examples/<file>.py` block, an `## Output` block with the captured table, and a short `## Interpretation`. Mirror the prose density of the existing `docs/examples/draco.md`.

### Task 3.1: Stateless dispatching rules gallery

**Files:**
- Create: `examples/gallery_dispatching_stateless.py`
- Create: `docs/examples/dispatching-stateless.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_gallery_examples.py`:

```python
from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _run(name: str) -> str:
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runpy.run_path(str(EXAMPLES / name), run_name="__main__")
    return buf.getvalue()


def test_gallery_dispatching_stateless_runs() -> None:
    out = _run("gallery_dispatching_stateless.py")
    for rule in ("SPT", "EDD", "ODD", "MODD", "CR", "FCFS", "WINQ"):
        assert rule in out
    assert "%Tardy" in out


def test_gallery_dispatching_stateless_rows_differ() -> None:
    # Spec success criterion: comparison rows must be distinct, not degenerate ties.
    import importlib.util

    path = EXAMPLES / "gallery_dispatching_stateless.py"
    spec = importlib.util.spec_from_file_location("gds", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    spt_tis = mod.run_rule(mod.RULES["SPT"])[1]
    fcfs_tis = mod.run_rule(mod.RULES["FCFS"])[1]
    assert spt_tis != fcfs_tis, "SPT and FCFS produced identical AvgTIS — harness is degenerate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_stateless_runs -v`
Expected: FAIL with `FileNotFoundError` for `gallery_dispatching_stateless.py`.

- [ ] **Step 3: Write the example script**

Create `examples/gallery_dispatching_stateless.py`:

```python
"""Stateless dispatching rules compared on one seeded multi-stage shop.

Runs SPT, EDD, ODD, MODD, CR, FCFS, and WINQ as the queue-ordering rule of an
immediate-release (push) shop and prints a comparison table. The shop has
variable multi-operation routings and due dates, so every rule produces a
distinct ordering.

Run: uv run python examples/gallery_dispatching_stateless.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.dispatching_rules import (
    critical_ratio,
    earliest_due_date,
    first_come_first_served,
    modified_operational_due_date,
    operational_due_date,
    shortest_processing_time,
    work_in_next_queue,
)
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

RULES = {
    "SPT": shortest_processing_time,
    "EDD": earliest_due_date,
    "ODD": operational_due_date,
    "MODD": modified_operational_due_date,
    "CR": critical_ratio,
    "FCFS": first_come_first_served,
    "WINQ": work_in_next_queue,
}


def run_rule(rule) -> tuple[int, float, float, float]:
    random.seed(SEED)  # identical seeded stream for every rule -> fair comparison
    with Environment() as env:
        _, _servers, shop_floor, _ = build_immediate_release_system(env, priority_policies=rule)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("Stateless dispatching rules (immediate release, seed=42)")
    print(f"{'Rule':<6}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, rule in RULES.items():
        n, tis, mt, pt = run_rule(rule)
        print(f"{name:<6}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_stateless_runs -v`
Expected: PASS

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_dispatching_stateless.py`
Copy the printed table verbatim — it is pasted into the doc's `## Output` block in Step 6.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/dispatching-stateless.md`. Paste the script from Step 3 verbatim inside a ` ```python { .run } ` fence, and paste the Step 5 output into the `## Output` block:

```markdown
# Stateless dispatching rules (production)

The seven **stateless** dispatching rules order each server's queue using only
the candidate job and the server — no shop-wide state. They run here as the
queue-ordering rule of an immediate-release push shop with variable
multi-operation routings and due dates, so each rule yields a distinct ordering.

- **SPT** — shortest processing time first
- **EDD** — earliest due date first
- **ODD / MODD** — operational / modified operational due date
- **CR** — critical ratio (slack ÷ remaining work)
- **FCFS** — first come, first served
- **WINQ** — work in next queue (favour jobs feeding the least-loaded next server)

See also: [Dispatching Rules API](../api/dispatching-rules.md)

## Comparison

<paste the gallery_dispatching_stateless.py source here, in a python { .run } fence>

**Run it:**

```bash
uv run python examples/gallery_dispatching_stateless.py
```

## Output

<paste captured table here>

## Interpretation

SPT minimises average time in system but tends to leave a tail of late large
jobs; the due-date rules (EDD/ODD/MODD/CR) trade some flow time for fewer tardy
jobs. FCFS is the neutral baseline. None of these rules controls WIP — for that,
combine a dispatching rule with a release policy (see the
[release policy galleries](release-workload.md)).
```

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_dispatching_stateless.py docs/examples/dispatching-stateless.md tests/core/test_gallery_examples.py
git commit -m "docs: add stateless dispatching rules gallery (runnable)"
```

### Task 3.2: Parameterized dispatching rules gallery

**Files:**
- Create: `examples/gallery_dispatching_parameterized.py`
- Create: `docs/examples/dispatching-parameterized.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_gallery_examples.py`:

```python
def test_gallery_dispatching_parameterized_runs() -> None:
    out = _run("gallery_dispatching_parameterized.py")
    for rule in ("PST", "S/RO", "ATC", "COVERT", "Raghu"):
        assert rule in out
    assert "%Tardy" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_parameterized_runs -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the example script**

The five parameterized rules are **factories** — call them to get the `(job, server) -> float` callable. Create `examples/gallery_dispatching_parameterized.py`:

```python
"""Parameterized dispatching rules compared on one seeded multi-stage shop.

Runs PST, S/RO, ATC, COVERT, and Raghu-Rajendran. Each is a factory: call it
with its parameter(s) to obtain the queue-ordering callable.

Run: uv run python examples/gallery_dispatching_parameterized.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_immediate_release_system
from simulatte.dispatching_rules import (
    apparent_tardiness_cost,
    cost_over_time,
    planned_slack_time,
    raghu_rajendran,
    slack_per_remaining_operation,
)
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

RULES = {
    "PST": planned_slack_time(allowance=2.0),
    "S/RO": slack_per_remaining_operation(allowance=2.0),
    "ATC": apparent_tardiness_cost(lookahead=2.0),
    "COVERT": cost_over_time(lookahead=2.0),
    "Raghu": raghu_rajendran(),
}


def run_rule(rule) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        _, _servers, shop_floor, _ = build_immediate_release_system(env, priority_policies=rule)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("Parameterized dispatching rules (immediate release, seed=42)")
    print(f"{'Rule':<8}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, rule in RULES.items():
        n, tis, mt, pt = run_rule(rule)
        print(f"{name:<8}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_parameterized_runs -v`
Expected: PASS

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_dispatching_parameterized.py` and copy the table.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/dispatching-parameterized.md` following the per-page pattern: intro listing PST, S/RO, ATC, COVERT, Raghu-Rajendran and noting each is a tunable factory; `See also` → `../api/dispatching-rules.md`; the script embedded verbatim in a `python { .run }` fence; the `uv run` block; the captured output; a short interpretation noting that ATC/COVERT use a `lookahead` and PST/S-RO use an `allowance` that trade flow time against tardiness.

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_dispatching_parameterized.py docs/examples/dispatching-parameterized.md tests/core/test_gallery_examples.py
git commit -m "docs: add parameterized dispatching rules gallery (runnable)"
```

### Task 3.3: System-state (FOCUS) gallery

**Files:**
- Create: `examples/gallery_dispatching_focus.py`
- Create: `docs/examples/dispatching-focus.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_gallery_examples.py`:

```python
def test_gallery_dispatching_focus_runs() -> None:
    out = _run("gallery_dispatching_focus.py")
    assert "FOCUS" in out
    assert "beta-dormant" in out or "beta" in out
    assert "%Tardy" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_focus_runs -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the example script**

FOCUS needs the shopfloor at construction, so use `build_focus_system` (which wires `Focus` + `FocusPriorityRule` internally) and compare weight configurations against an FCFS baseline. Create `examples/gallery_dispatching_focus.py`:

```python
"""FOCUS system-state dispatching rule across weight configurations.

FOCUS (Kasper, Land, Teunter 2023) blends five shop-state mechanisms. Because
it needs shop-wide state, it is wired by build_focus_system. This gallery
compares a few weight vectors against an FCFS baseline on one seeded shop.

Run: uv run python examples/gallery_dispatching_focus.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_focus_system, build_immediate_release_system
from simulatte.dispatching_rules import first_come_first_served
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

# (label, focus_weights or None for the FCFS baseline)
CONFIGS = [
    ("FCFS baseline", None),
    ("FOCUS beta-dormant", (0.25, 0.25, 0.25, 0.25, 0.0)),
    ("FOCUS SPT-heavy", (0.6, 0.1, 0.1, 0.1, 0.1)),
    ("FOCUS balanced", (0.2, 0.2, 0.2, 0.2, 0.2)),
]


def metrics(shop_floor) -> tuple[int, float, float, float]:
    done = shop_floor.jobs_done
    n = len(done)
    avg_tis = shop_floor.average_time_in_system
    tardiness = [max(0.0, j.lateness) for j in done]
    mean_tard = sum(tardiness) / n if n else 0.0
    pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
    return n, avg_tis, mean_tard, pct_tardy


def run_config(weights) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        if weights is None:
            _, _s, shop_floor, _ = build_immediate_release_system(
                env, priority_policies=first_come_first_served
            )
        else:
            _, _s, shop_floor, _ = build_focus_system(env, focus_weights=weights)
        env.run(until=HORIZON)
        return metrics(shop_floor)


def main() -> None:
    print("FOCUS system-state dispatching (immediate release, seed=42)")
    print(f"{'Config':<20}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for label, weights in CONFIGS:
        n, tis, mt, pt = run_config(weights)
        print(f"{label:<20}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_dispatching_focus_runs -v`
Expected: PASS. (If FOCUS weight validation rejects a vector, adjust the vectors so each is in `[0,1]` and sums to 1.)

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_dispatching_focus.py` and copy the table.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/dispatching-focus.md`: intro explaining FOCUS is the system-state rule (mention `Focus`, `FocusContext`, `FocusPriorityRule` are the implementation pieces — `FocusContext`/`FocusPriorityRule` are support classes, not separate rules); note this is the **featured** home for FOCUS (folded from the old `focus.md`); `See also` → `../api/dispatching-rules.md`; embed the script verbatim in a `python { .run }` fence; `uv run` block; captured output; interpretation on how weight vectors shift the urgency/work/state balance.

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_dispatching_focus.py docs/examples/dispatching-focus.md tests/core/test_gallery_examples.py
git commit -m "docs: add FOCUS system-state dispatching gallery (runnable)"
```

### Task 3.4: Workload-control release policies gallery

**Files:**
- Create: `examples/gallery_release_workload.py`
- Create: `docs/examples/release-workload.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_gallery_examples.py`:

```python
def test_gallery_release_workload_runs() -> None:
    out = _run("gallery_release_workload.py")
    for name in ("Immediate", "LumsCor", "SLAR", "SLAR-Limit", "Continuous"):
        assert name in out
    assert "%Tardy" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_release_workload_runs -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the example script**

Each member uses its builder; reseed before each build for a fair comparison. Create `examples/gallery_release_workload.py`:

```python
"""Workload-control release policies compared on one seeded shop.

Immediate release (push baseline) vs LumsCor, SLAR, SLAR-Limit, and Continuous
Release — all workload-control pull policies that hold jobs in a Pre-Shop Pool
and release against load norms.

Run: uv run python examples/gallery_release_workload.py
"""

from __future__ import annotations

import random

from simulatte.builders import (
    build_continuous_release_system,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
)
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

# label -> builder thunk taking only env
SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env),
    "LumsCor": lambda env: build_lumscor_system(
        env, check_timeout=10.0, wl_norm_level=6.0, allowance_factor=2
    ),
    "SLAR": lambda env: build_slar_system(env, allowance_factor=3.0),
    "SLAR-Limit": lambda env: build_slar_limit_system(
        env, allowance_factor=3.0, wl_norm_level=6.0
    ),
    "Continuous": lambda env: build_continuous_release_system(
        env, wl_norm_level=6.0, allowance_factor=2
    ),
}


def run_system(builder) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        _psp, _servers, shop_floor, _router = builder(env)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("Workload-control release policies (seed=42)")
    print(f"{'Policy':<12}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<12}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_release_workload_runs -v`
Expected: PASS. If any pull policy completes 0 jobs at this horizon, raise its `wl_norm_level` (more permissive release) until the row is non-trivial, then re-run.

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_release_workload.py` and copy the table.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/release-workload.md`: intro contrasting push (immediate) vs workload-control pull; one line each on LumsCor, SLAR, SLAR-Limit, Continuous Release; `See also` → `../api/release-policies.md`; embed script verbatim in a `python { .run }` fence; `uv run` block; captured output; interpretation noting `average_time_in_system` excludes PSP wait while `%Tardy`/`MeanTard` (via `lateness`) include it, so pull policies can show higher tardiness but lower in-shop flow time. Link to the [Comparing release policies tutorial](../tutorials/comparing-release-policies.md).

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_release_workload.py docs/examples/release-workload.md tests/core/test_gallery_examples.py
git commit -m "docs: add workload-control release policies gallery (runnable)"
```

### Task 3.5: WIP-cap release policies gallery

**Files:**
- Create: `examples/gallery_release_wip.py`
- Create: `docs/examples/release-wip.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_gallery_examples.py`:

```python
def test_gallery_release_wip_runs() -> None:
    out = _run("gallery_release_wip.py")
    for name in ("ConWIP", "DRACO"):
        assert name in out
    assert "%Tardy" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_release_wip_runs -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the example script**

Create `examples/gallery_release_wip.py`:

```python
"""WIP-cap release policies compared on one seeded shop.

ConWIP (constant WIP, EDD release) vs DRACO (non-hierarchical WIP control that
merges release, authorisation, and dispatching). Both keep shop WIP near a
target; DRACO additionally governs dispatching with FOCUS internally.

Run: uv run python examples/gallery_release_wip.py
"""

from __future__ import annotations

import random

from simulatte.builders import build_conwip_system, build_draco_system
from simulatte.environment import Environment

SEED = 42
HORIZON = 800.0

SYSTEMS = {
    "ConWIP": lambda env: build_conwip_system(env, wip_cap=8),
    "DRACO": lambda env: build_draco_system(env, wip_target=8, loop_target=4),
}


def run_system(builder) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        _psp, _servers, shop_floor, _router = builder(env)
        env.run(until=HORIZON)
        done = shop_floor.jobs_done
        n = len(done)
        avg_tis = shop_floor.average_time_in_system
        tardiness = [max(0.0, j.lateness) for j in done]
        mean_tard = sum(tardiness) / n if n else 0.0
        pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
        return n, avg_tis, mean_tard, pct_tardy


def main() -> None:
    print("WIP-cap release policies (seed=42)")
    print(f"{'Policy':<8}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run_system(builder)
        print(f"{name:<8}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_release_wip_runs -v`
Expected: PASS

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_release_wip.py` and copy the table.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/release-wip.md`: intro on WIP-cap pull; note this is the **featured** home for DRACO (folded from the old `draco.md`); `See also` → `../api/release-policies.md`; embed script verbatim in a `python { .run }` fence; `uv run` block; captured output; interpretation contrasting ConWIP's simple count cap vs DRACO's non-hierarchical control.

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_release_wip.py docs/examples/release-wip.md tests/core/test_gallery_examples.py
git commit -m "docs: add WIP-cap release policies gallery (runnable)"
```

### Task 3.6: Triggers & starvation avoidance gallery

**Files:**
- Create: `examples/gallery_release_triggers.py`
- Create: `docs/examples/release-triggers.md`
- Test: `tests/core/test_gallery_examples.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_gallery_examples.py`:

```python
def test_gallery_release_triggers_runs() -> None:
    out = _run("gallery_release_triggers.py")
    assert "Starvation-only" in out
    assert "Periodic-release" in out
    assert "Immediate" in out


def test_all_gallery_scripts_are_listed() -> None:
    # Guard: every gallery_*.py has a runpy test above.
    scripts = sorted(p.name for p in EXAMPLES.glob("gallery_*.py"))
    assert scripts == [
        "gallery_dispatching_focus.py",
        "gallery_dispatching_parameterized.py",
        "gallery_dispatching_stateless.py",
        "gallery_release_triggers.py",
        "gallery_release_wip.py",
        "gallery_release_workload.py",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gallery_examples.py::test_gallery_release_triggers_runs -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the example script**

This gallery demonstrates the trigger primitives by composing a custom periodic release, alongside the starvation-only builder and the immediate baseline. Create `examples/gallery_release_triggers.py`:

```python
"""Release triggers and starvation avoidance.

Demonstrates the three trigger primitives (periodic_trigger, on_completion_
trigger, on_arrival_trigger) and the starvation_avoidance callback by building
three small pull systems and comparing them to the immediate-release baseline:

  - Immediate        : push baseline (no PSP).
  - Starvation-only  : release only when a job's first server is idle.
  - Periodic-release : release the whole pool every fixed interval.

Run: uv run python examples/gallery_release_triggers.py
"""

from __future__ import annotations

import random

from simulatte.builders import (
    build_immediate_release_system,
    build_starvation_avoidance_system,
)
from simulatte.environment import Environment
from simulatte.policies.triggers import periodic_trigger
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.distributions import server_sampling, truncated_2erlang

SEED = 42
HORIZON = 800.0
N_SERVERS = 6
ARRIVAL_RATE = 1 / 0.648
SERVICE_RATE = 2.0


def metrics(shop_floor) -> tuple[int, float, float, float]:
    done = shop_floor.jobs_done
    n = len(done)
    avg_tis = shop_floor.average_time_in_system
    tardiness = [max(0.0, j.lateness) for j in done]
    mean_tard = sum(tardiness) / n if n else 0.0
    pct_tardy = 100.0 * sum(1 for t in tardiness if t > 0) / n if n else 0.0
    return n, avg_tis, mean_tard, pct_tardy


def build_periodic_release(env: Environment, interval: float = 10.0):
    """A custom pull system: release the entire pool every `interval` units."""
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(N_SERVERS))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(ARRIVAL_RATE),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {s: lambda: truncated_2erlang(lam=SERVICE_RATE, max_value=4.0) for s in servers}
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},
    )

    def release_all(pool: PreShopPool) -> None:
        while not pool.empty:
            pool.release(pool.remove())

    env.process(periodic_trigger(psp, interval, release_all))
    return shop_floor


def run(builder) -> tuple[int, float, float, float]:
    random.seed(SEED)
    with Environment() as env:
        result = builder(env)
        shop_floor = result if isinstance(result, ShopFloor) else result[2]
        env.run(until=HORIZON)
        return metrics(shop_floor)


SYSTEMS = {
    "Immediate": lambda env: build_immediate_release_system(env),
    "Starvation-only": lambda env: build_starvation_avoidance_system(env),
    "Periodic-release": build_periodic_release,
}


def main() -> None:
    print("Release triggers & starvation avoidance (seed=42)")
    print(f"{'System':<18}{'Done':>6}{'AvgTIS':>9}{'MeanTard':>10}{'%Tardy':>8}")
    for name, builder in SYSTEMS.items():
        n, tis, mt, pt = run(builder)
        print(f"{name:<18}{n:>6}{tis:>9.2f}{mt:>10.2f}{pt:>7.1f}%")


if __name__ == "__main__":
    main()
```

> Verify `PreShopPool.remove()` with no arguments returns a pooled job (FIFO) — the agent report confirms `remove(*, job=None)` supports this. If the no-arg form is unavailable, iterate `list(psp.jobs)` and `psp.release(job)` instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest "tests/core/test_gallery_examples.py" -v`
Expected: all gallery tests PASS (including `test_all_gallery_scripts_are_listed`).

- [ ] **Step 5: Capture the real output**

Run: `uv run python examples/gallery_release_triggers.py` and copy the table.

- [ ] **Step 6: Write the docs page**

Create `docs/examples/release-triggers.md`: intro explaining triggers decouple *when* to release from *what* to release; show the three primitive signatures (`periodic_trigger`, `on_arrival_trigger`, `on_completion_trigger`) and `starvation_avoidance`; `See also` → `../api/release-policies.md`; embed the script verbatim in a `python { .run }` fence; `uv run` block; captured output; interpretation on how starvation-only keeps the first server fed but lets WIP grow, while periodic release batches arrivals.

- [ ] **Step 7: Commit**

```bash
git add examples/gallery_release_triggers.py docs/examples/release-triggers.md tests/core/test_gallery_examples.py
git commit -m "docs: add release triggers & starvation gallery (runnable)"
```

### Task 3.7: Make intralogistics examples runnable with plots

**Files:**
- Modify: `examples/intralogistics_intermediate.py`, `examples/intralogistics_advanced.py` (reduce horizons for the in-browser budget)
- Modify: `docs/examples/intralogistics.md` (embed the three scripts as runnable blocks)
- Modify: `tests/intralogistics/test_examples.py` (update asserted output values if horizon changes alter them)

- [ ] **Step 1: Reduce the in-browser horizons**

In `examples/intralogistics_intermediate.py`, find the `env.run(until=...)` call (currently 7200s / 2h) and reduce to `3600.0` (1h). In `examples/intralogistics_advanced.py`, find `env.run(until=...)` (currently 28800s / 8h) and reduce to `7200.0` (2h). These keep the qualitative behaviour (queuing, charging, replenishment) while fitting the Pyodide budget.

> The simple example (`intralogistics_simple.py`) is already text-only and short — leave it unchanged.

- [ ] **Step 2: Update the example tests for new horizons**

Run the two examples and update any now-changed asserted substrings in `tests/intralogistics/test_examples.py` (e.g. the `Simulation time:` line and order counts). The existing tests already monkeypatch `plt.show` to a no-op, so they keep passing structurally.

Run:
```bash
uv run python examples/intralogistics_intermediate.py | head -20
uv run python examples/intralogistics_advanced.py | head -20
uv run pytest tests/intralogistics/test_examples.py -v
```
Update the asserted values in the test file to match the new output, then re-run until green.

> **Feature-presence check (the reduced horizon must not falsify the prose).** Charging and `ReorderPointPolicy` replenishment are time-triggered; at a short horizon they may never fire, leaving a page that claims "battery lifecycle + replenishment" above output where neither happens. After reducing the advanced horizon, confirm the run still shows **at least one charging event and one replenishment** (the advanced output already reports replenishment counts; check for a non-zero charging/replenishment figure). If either is absent, raise the advanced horizon until both occur, or remove that claim from the prose. Add an assertion to `tests/intralogistics/test_examples.py` that the advanced output contains a non-zero replenishment count so a future horizon change can't silently drop it.

- [ ] **Step 3: Embed the scripts as runnable blocks in the docs**

Edit `docs/examples/intralogistics.md`. For each of the three examples, in addition to the existing prose/mermaid/config, add a `## Run it in the browser` subsection embedding the corresponding `examples/*.py` script verbatim in a ` ```python { .run } ` fence. Keep the existing `**Run it:**` `uv run` blocks. Add a one-line note under intermediate/advanced: "Click ▶ Run — the time-series plots render below the text output."

> Embed the **full** script content verbatim (the drift-guard test in Task 3.9 compares the embedded block to the file byte-for-byte). The intermediate/advanced scripts call `plot_*` helpers that call `plt.show()`, which the Phase 1 shim captures as images.

- [ ] **Step 4: Build the wheel and verify plots render in-browser**

Run:
```bash
./scripts/build_docs_wheel.sh
uv run zensical serve
```
Open the Intralogistics examples page, run each block. Expected: simple prints text; intermediate prints text + 2 plots; advanced prints text + 4 plots. Confirm each completes within a few seconds.

> If the advanced example exceeds the budget in-browser, reduce its horizon further (e.g. 3600.0) and re-run Step 2; if it still does not fit, per spec §9 keep advanced as a non-embedded `uv run`-only example (remove its `{ .run }` block, keep the prose) and note this in the page.

- [ ] **Step 5: Commit**

```bash
git add examples/intralogistics_intermediate.py examples/intralogistics_advanced.py docs/examples/intralogistics.md tests/intralogistics/test_examples.py
git commit -m "docs: make intralogistics examples runnable in-browser with plots"
```

### Task 3.8: Redirect stubs for draco.md and focus.md

**Files:**
- Modify: `docs/examples/draco.md`, `docs/examples/focus.md`

- [ ] **Step 1: Replace draco.md with a redirect stub**

Overwrite `docs/examples/draco.md` with:

```markdown
---
title: Draco Release (moved)
---

<meta http-equiv="refresh" content="0; url=release-wip/">

# This page has moved

The DRACO example now lives in the **[WIP-cap release policies gallery](release-wip.md)**.
```

- [ ] **Step 2: Replace focus.md with a redirect stub**

Overwrite `docs/examples/focus.md` with:

```markdown
---
title: Focus Dispatching (moved)
---

<meta http-equiv="refresh" content="0; url=dispatching-focus/">

# This page has moved

The FOCUS example now lives in the **[system-state dispatching gallery](dispatching-focus.md)**.
```

> The relative refresh URLs (`release-wip/`, `dispatching-focus/`) match MkDocs' directory-URL output. Verify in Step (Task 3.10) that visiting `/examples/draco/` lands on the gallery.

- [ ] **Step 3: Commit**

```bash
git add docs/examples/draco.md docs/examples/focus.md
git commit -m "docs: redirect old draco/focus example pages to their galleries"
```

### Task 3.9: Drift-guard test (embedded block == example file)

**Files:**
- Create: `tests/test_docs_run_blocks.py`

- [ ] **Step 1: Write the test**

Create `tests/test_docs_run_blocks.py`. It maps each gallery doc page to its example script and asserts the embedded `{ .run }` block exactly equals the file:

```python
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "examples"
EXAMPLES = ROOT / "examples"

# doc page -> example script that must be embedded verbatim in a { .run } block
PAIRS = {
    "dispatching-stateless.md": "gallery_dispatching_stateless.py",
    "dispatching-parameterized.md": "gallery_dispatching_parameterized.py",
    "dispatching-focus.md": "gallery_dispatching_focus.py",
    "release-workload.md": "gallery_release_workload.py",
    "release-wip.md": "gallery_release_wip.py",
    "release-triggers.md": "gallery_release_triggers.py",
}

RUN_BLOCK = re.compile(r"```python \{ \.run \}\n(.*?)\n```", re.DOTALL)


def test_run_blocks_match_example_files() -> None:
    for doc_name, script_name in PAIRS.items():
        doc = (DOCS / doc_name).read_text()
        script = (EXAMPLES / script_name).read_text()
        blocks = RUN_BLOCK.findall(doc)
        assert blocks, f"{doc_name}: no `python {{ .run }}` block found"
        # The embedded block must equal the script file (trailing newline tolerant).
        assert blocks[0].strip("\n") == script.strip("\n"), (
            f"{doc_name} embedded code has drifted from {script_name}"
        )
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_docs_run_blocks.py -v`
Expected: PASS. If a page fails, re-copy the example file content into that page's `{ .run }` fence verbatim.

- [ ] **Step 3: Commit**

```bash
git add tests/test_docs_run_blocks.py
git commit -m "test(docs): guard gallery run-blocks against drift from example files"
```

### Task 3.10: Overview, nav, and cross-links

**Files:**
- Modify: `docs/examples/index.md`, `zensical.toml`
- Modify: `docs/tutorials/release-control-and-dispatching.md`, `docs/tutorials/comparing-release-policies.md`, `docs/tutorials/building-an-agv-system.md`

- [ ] **Step 1: Update the nav in `zensical.toml`**

Replace the `Examples` nav block with the new gallery pages (keep the old draco/focus files on disk as redirect stubs but remove them from nav):

```toml
  { "Examples" = [
    { "Overview" = "examples/index.md" },
    { "Dispatching — stateless rules" = "examples/dispatching-stateless.md" },
    { "Dispatching — parameterized rules" = "examples/dispatching-parameterized.md" },
    { "Dispatching — system-state (FOCUS)" = "examples/dispatching-focus.md" },
    { "Release — workload control" = "examples/release-workload.md" },
    { "Release — WIP cap" = "examples/release-wip.md" },
    { "Release — triggers & starvation" = "examples/release-triggers.md" },
    { "Intralogistics" = "examples/intralogistics.md" },
  ]},
```

- [ ] **Step 2: Rewrite `docs/examples/index.md`**

Replace the catalog table so it lists every gallery and the mechanisms each covers, and add a short "Running examples in your browser" note explaining the ▶ Run button (Pyodide installs simulatte on first run; plots render inline). Ensure every release policy and dispatching rule named in the spec appears in exactly one gallery row. Example table:

```markdown
# Examples

Runnable reference galleries. Every release policy and dispatching rule appears
in a gallery you can run in the browser — click **▶ Run** on any code block
(the first run installs `simulatte` via Pyodide; later runs are instant). Plots
render inline beneath the text output.

| Gallery | Domain | Mechanisms covered |
|---------|--------|--------------------|
| [Dispatching — stateless](dispatching-stateless.md) | Production | SPT, EDD, ODD, MODD, CR, FCFS, WINQ |
| [Dispatching — parameterized](dispatching-parameterized.md) | Production | PST, S/RO, ATC, COVERT, Raghu-Rajendran |
| [Dispatching — system-state](dispatching-focus.md) | Production | FOCUS |
| [Release — workload control](release-workload.md) | Production | Immediate, LumsCor, SLAR, SLAR-Limit, Continuous Release |
| [Release — WIP cap](release-wip.md) | Production | ConWIP, DRACO |
| [Release — triggers & starvation](release-triggers.md) | Production | periodic / on-arrival / on-completion triggers, starvation avoidance |
| [Intralogistics](intralogistics.md) | Intralogistics | AGV fleet, warehouses, charging, replenishment, plots |

All source scripts live in [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples).
```

- [ ] **Step 3: Add cross-links from Tutorials**

In each of the three tutorial files, add a one-line callout linking to the relevant gallery (no content duplication). Suggested insertions:
- `docs/tutorials/release-control-and-dispatching.md`: after the intro, add `> See the runnable [dispatching](../examples/dispatching-stateless.md) and [release policy](../examples/release-workload.md) galleries to compare every rule and policy in your browser.`
- `docs/tutorials/comparing-release-policies.md`: add `> Prefer to run it live? The [workload-control](../examples/release-workload.md) and [WIP-cap](../examples/release-wip.md) galleries compare these policies in-browser.`
- `docs/tutorials/building-an-agv-system.md`: add `> See the runnable [intralogistics examples](../examples/intralogistics.md) for end-to-end systems with plots.`

- [ ] **Step 4: Full local build + link/redirect verification**

Run:
```bash
./scripts/build_docs_wheel.sh
uv run zensical build
```
Expected: build succeeds with no broken-link errors. Then `uv run zensical serve` and confirm: (a) the Examples nav shows the seven pages; (b) every gallery's ▶ Run produces a table within a few seconds; (c) visiting `/examples/draco/` and `/examples/focus/` redirects to the galleries; (d) intralogistics plots render.

> **In-browser ↔ documented Output parity (the core UX promise).** Each page presents its `## Output` as "what you'll see when you click Run." stdlib `random` is deterministic under Pyodide, so the browser table *should* equal the natively-captured one — but `truncated_2erlang`'s rejection loop can amplify last-bit libm differences. For **at least one gallery** (e.g. stateless dispatching), copy the in-browser table and diff it against the page's `## Output`. If they match, the determinism assumption holds for all galleries. If they diverge on any digit, switch to capturing every page's `## Output` **from the browser** rather than from `uv run` (re-do the relevant Step 5 captures), so the documented numbers are exactly what a visitor sees.

- [ ] **Step 5: Full test suite + lint**

Run:
```bash
uv run pytest
uv run ruff check .
uv run ty check
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add docs/examples/index.md zensical.toml docs/tutorials/release-control-and-dispatching.md docs/tutorials/comparing-release-policies.md docs/tutorials/building-an-agv-system.md
git commit -m "docs: rewrite Examples overview, nav, and tutorial cross-links"
```

---

## Self-Review Notes (for the executor)

- **Spec coverage:** Phase 1 ↔ spec §7; Phase 2 ↔ spec §8; Phase 3 ↔ spec §5/§6/§9; redirects ↔ decision #6; inline harness ↔ decision #7; reduced horizon ↔ §9; drift guard ↔ §9.
- **Every mechanism covered:** stateless (7) + parameterized (5) + FOCUS (1) = 13 dispatching rules; Immediate, LumsCor, SLAR, SLAR-Limit, Continuous, ConWIP, DRACO, starvation + 3 triggers = all release policies.
- **Calibration is expected, not a placeholder:** `HORIZON`, `wl_norm_level`, `wip_cap`, and intralogistics horizons are starting values; tune so tables are non-degenerate and pages fit the in-browser budget (spec §13 open items 1–2).
- **Parameter assumptions to confirm on first run:** FOCUS weight vectors must each be in `[0,1]` and sum to 1; `PreShopPool.remove()` no-arg FIFO; `psp.jobs` iterable; `Server.is_idle`.
