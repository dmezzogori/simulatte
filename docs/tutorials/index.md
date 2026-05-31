# Tutorials

Small, copy/paste-friendly walkthroughs.

This section walks through the core building blocks of a Simulatte simulation — from setting up a basic manufacturing system to controlling job releases, extending the shopfloor with custom hooks, and running reproducible multi-seed experiments.

- [Basic manufacturing system](basic-manufacturing-system.md): build a tiny shopfloor, run jobs through multiple servers, read metrics.
- [Release control and dispatching](release-control-and-dispatching.md): pre-shop pool, release policies, dispatching rules, and composable triggers.
- [Comparing release policies](comparing-release-policies.md): run Immediate Release, LumsCor, and SLAR side by side and read a performance table.
- [Building an AGV system](building-an-agv-system.md): assemble a layout graph, warehouses, AGV fleet, and FleetCoordinator from scratch.
- [Gymnasium wrapper](gymnasium-wrapper.md): wrap a simulation as a Gymnasium RL environment.
- [ShopFloor extensibility](shopfloor-extensibility.md): add hooks, swap WIP strategies, customize metrics.
- [Multi-run experiments](multi-run-experiments.md): repeat runs across random seeds with `Runner`.
- [Logging](logging.md): trace events, debug behavior, and analyze simulation history.
- [Troubleshooting](../guides/troubleshooting.md): common gotchas and how to fix them.

## Building with Agents

See [Agent Skill](../development/agent-skill.md) for building simulations with the help of agents.

## Next

- [Basic manufacturing system](basic-manufacturing-system.md)
