# Guides

Guides explain the concepts and architecture behind each domain in Simulatte. They are not step-by-step tutorials — those live in [Tutorials](../tutorials/index.md) — but conceptual explanations of how the pieces fit together and why they are designed the way they are.

- [Production Planning & Control](production-planning.md): how jobs flow through the job-shop model — PreShopPool, release policies, Server dispatching, WIP tracking, and the ShopFloor orchestrator.
- [Intralogistics](intralogistics.md): warehouse-to-warehouse material transport — LayoutGraph, Warehouse inventory, AGV fleets, FleetCoordinator, and traffic management.
- [Events, Traces & KPIs](events-and-traces.md): the event bus and catalog, RNG streams and activation, the semantic digest and run manifest, trace recording and reading, KPI collectors and logging sinks.
- [Experiments & Comparisons](experiments.md): experiment files and the Python API, seeds and common random numbers, replication-level statistics, paired comparisons, replays and retention.
- [Remote Workers](remote-workers.md): `workers.toml`, `simulatte workers`, uv provisioning, bundles, the remote cache and their limits.
- [Reinforcement Learning](reinforcement-learning.md): the experimental Gymnasium wrapper for wrapping Simulatte simulations as RL environments.
- [Troubleshooting](troubleshooting.md): solutions to common gotchas when building and running Simulatte simulations.
