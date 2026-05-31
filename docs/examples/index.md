# Examples

Runnable scenarios that demonstrate Simulatte from simple to advanced. Every script in the [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples) folder is self-contained, requires no extra data files, and can be executed with `uv run python examples/<name>.py`.

The examples are organised in two domains — **production planning and control** and **intralogistics** — and are graded from minimal setups (immediate release, single SKU, two AGVs) through to full-shift warehouse operations with battery management, automatic replenishment, and EMA metrics.

| Example | Domain | What it demonstrates | Page |
|---------|--------|----------------------|------|
| Draco release | Production | Non-hierarchical WIP control with DRACO policy (`build_draco_system`) | [draco.md](draco.md) |
| Focus dispatching | Production | Standalone FOCUS dispatching rule with immediate release (`build_focus_system`) | [focus.md](focus.md) |
| Intralogistics simple | Intralogistics | Minimal AGV setup with `build_simple_system`; four transfer orders | [intralogistics.md](intralogistics.md#simple) |
| Intralogistics intermediate | Intralogistics | Custom graph, three SKUs, dispatch strategy, parking, staggered batches | [intralogistics.md](intralogistics.md#intermediate) |
| Intralogistics advanced | Intralogistics | Three warehouses, battery lifecycle, charging, replenishment, EMA metrics | [intralogistics.md](intralogistics.md#advanced) |

---

All source files live in the `examples/` folder of the repository. Browse them on GitHub: <https://github.com/dmezzogori/simulatte/tree/main/examples>
