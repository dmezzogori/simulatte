# API Reference

This section is auto-generated from docstrings using [mkdocstrings](https://mkdocstrings.github.io/).
For narrative walkthroughs of how the pieces fit together, see the
[Tutorials](../tutorials/index.md) and [Guides](../guides/index.md).

- [**Core**](core.md) — Core simulation objects: `Environment`, `ShopFloor`, `Server`, `ProductionJob`, `Router`, `PreShopPool`, `Runner`, and the `ShopFloor` extension points.
- [**Release Policies**](release-policies.md) — Release policies that control when jobs are admitted to the shop floor: `LumsCor`, `Slar`, `SlarLimit`, `Draco`, `ConWIP`, `ContinuousRelease`, starvation avoidance, and event triggers.
- [**Dispatching Rules**](dispatching-rules.md) — Priority rules applied at each workstation queue: stateless rules (SPT, EDD, …), parameterized rules, and system-state rules (Focus).
- [**Intralogistics**](intralogistics.md) — Warehouse, AGV fleet, and material-transport API: layout graph, pathfinding, traffic management, vehicles, storage, orders, fleet coordination, policies, and metrics.
- [**Utilities**](utilities.md) — Builder functions for quick system setup, statistical distribution helpers, and the simulation logger.
- [**Experimental**](experimental.md) — Unstable Gymnasium RL wrapper (`SimulatteEnv`); API subject to change without notice.
