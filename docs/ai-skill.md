# AI Coding Agent Skill

Simulatte ships **simulatte:dev**, a skill for AI coding agents that teaches them how to write correct Simulatte simulations. When installed, the agent automatically knows how to choose release policies, configure Router distributions, compose triggers, avoid common pitfalls, and set up multi-run experiments.

## Supported agents

The skill works with any agent that supports the [Vercel Skills](https://github.com/vercel-labs/skills) format, including:

- [Claude Code](https://claude.ai/code)
- Cursor
- Windsurf
- Any agent that reads SKILL.md files

## Installation

Install the skill using the Vercel Skills CLI:

```bash
npx skills add https://github.com/dmezzogori/simulatte/tree/main/skills/simulatte-dev
```

This downloads the skill into your project's agent skills directory.

## Usage

Once installed, the skill triggers automatically when the agent detects Simulatte-related code or requests. You can also invoke it explicitly:

```
/simulatte:dev
```

### What the skill covers

- **Policy selection** — guidance on when to use Immediate Release, LumsCor, or SLAR, and which parameters to vary in experiments
- **Build workflow** — using builder functions for quick setup or manual composition for custom configurations
- **Custom dispatching** — writing priority policies (SPT, EDD, custom rules)
- **Operation hooks** — injecting setup times, breakdowns, or other logic before/after processing
- **Multi-run experiments** — configuring `Runner` with seeds, parallel execution, and result extraction
- **Result inspection** — accessing job-level, server-level, and shopfloor-level metrics
- **Gymnasium wrapper** — wrapping simulations as Gymnasium environments for RL training with `SimulatteEnv`
- **Common pitfalls** — generator hooks, absolute due dates, arrival rate semantics, lambda closures, and more

### What the skill does NOT cover

The skill excludes most experimental modules (Warehouse, AGV, MaterialCoordinator) since they are unstable and subject to breaking changes. The Gymnasium wrapper (`SimulatteEnv`) is an exception — it is covered because RL integration is a common use case.

## Skill structure

```
skills/simulatte-dev/
├── SKILL.md                        # Decision-making guidance and workflow
└── references/
    └── api-reference.md            # Exact API signatures and configuration patterns
```

The agent loads `SKILL.md` when the skill triggers, and reads `api-reference.md` on demand when it needs exact constructor signatures or parameter types.
