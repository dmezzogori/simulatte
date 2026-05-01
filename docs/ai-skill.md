# AI Coding Agent Skills

Simulatte ships two skills for AI coding agents — one for production planning simulations and one for intralogistics. When installed, the agent automatically knows how to choose policies, configure systems, avoid common pitfalls, and interpret results.

## Supported agents

The skills work with any agent that supports the [Vercel Skills](https://github.com/vercel-labs/skills) format, including:

- [Claude Code](https://claude.ai/code)
- Cursor
- Windsurf
- Any agent that reads SKILL.md files

## Installation

Install using the Vercel Skills CLI:

```bash
# Production planning (job-shop, release policies, dispatching)
npx skills add https://github.com/dmezzogori/simulatte/tree/main/skills/simulatte-dev

# Intralogistics (warehouses, AGV fleets, material transport)
npx skills add https://github.com/dmezzogori/simulatte/tree/main/skills/simulatte-intralogistics
```

Install one or both depending on what you're building.

## Usage

Skills trigger automatically when the agent detects relevant imports or requests. You can also invoke them explicitly:

```
/simulatte:dev                    # production planning
/simulatte-intralogistics         # intralogistics
```

## simulatte:dev — Production Planning

Covers job-shop simulation with release control and dispatching:

- **Policy selection** — guidance on when to use Immediate Release, LumsCor, or SLAR, and which parameters to vary in experiments
- **Build workflow** — using builder functions for quick setup or manual composition for custom configurations
- **Custom dispatching** — writing priority policies (SPT, EDD, custom rules)
- **Operation hooks** — injecting setup times, breakdowns, or other logic before/after processing
- **Multi-run experiments** — configuring `Runner` with seeds, parallel execution, and result extraction
- **Result inspection** — accessing job-level, server-level, and shopfloor-level metrics
- **Gymnasium wrapper** — wrapping simulations as Gymnasium environments for RL training with `SimulatteEnv`
- **Common pitfalls** — generator hooks, absolute due dates, arrival rate semantics, lambda closures, and more

## simulatte-intralogistics — Warehouse and AGV Simulation

Covers the `simulatte.intralogistics` subpackage:

- **Layout design** — constructing warehouse graphs with nodes, arcs, and pathfinding; avoiding common topology traps (one-way dead ends, placement deadlocks)
- **Fleet configuration** — AGV types, trapezoidal speed profiles, battery lifecycle, capacity constraints
- **FleetCoordinator wiring** — the 10-step manual composition sequence for custom systems
- **Policies** — dispatch strategies (NearestIdle, RoundRobin), repositioning, replenishment (ReorderPointPolicy), and load recovery
- **Battery management** — depletion sanity checks, charging stations, automatic vs custom charging behavior
- **Capacity checks** — weight AND volume validation for orders and replenishment quantities
- **Metrics and plots** — EMA order metrics, time-series collection, fleet utilization / throughput / inventory plots
- **Tracking and debugging** — order-to-AGV mapping, diagnosing silent dispatch failures
- **Common pitfalls** — 7 specific failure modes discovered during development, with fixes

## Skill structure

```
skills/
├── simulatte-dev/
│   ├── SKILL.md                        # Production planning guidance
│   └── references/
│       └── api-reference.md            # Core API signatures
└── simulatte-intralogistics/
    ├── SKILL.md                        # Intralogistics guidance
    └── references/
        └── api-reference.md            # Intralogistics API signatures
```

The agent loads `SKILL.md` when a skill triggers, and reads `api-reference.md` on demand for exact constructor signatures and parameter types.
