# Jobshop Simulation Port Plan

**Context**  
- Source of concepts: `rl-ppc` (must remain untouched).  
- Target: `simulatte` codebase.  
- Goal: reuse the clean Job/Server/ShopFloor/PSP/Router abstractions for warehouse/picking simulations, skipping all RL parts.

**Guiding principles**  
- Do not edit `rl-ppc` at all.  
- Keep implementation small and readable; prefer straight ports with minimal adaptation to simulatte utilities (Singleton, Environment).  
- Avoid breaking existing simulatte modules; add a new namespaced package (proposed: `simulatte/jobshop`).  
- Reuse one global simpy environment (simulatte’s Environment singleton) to prevent divergent clocks.

## Deliverables (initial)
1. New package `simulatte/jobshop/` with:
   - `environment.py` (alias/wrapper to simulatte Environment)
   - `job.py`
   - `server/` (`server.py`, optional `inspection_server.py`, `faulty_server.py`)
   - `shopfloor.py`
   - `psp/` (`psp.py`, policies: base, lumscor, slar, starvation_avoidance)
   - `router.py`, `distributions.py`
   - `builders.py`, `runner.py` for quick experiments
2. Tests mirroring rl-ppc’s simulation-only coverage (no RL): queue/WIP, router generation, PSP release, corrected WIP.
3. Short usage note/example in docs.

## Scope boundaries
- Out: RL/Gym/training/agents/wrappers from rl-ppc.  
- Out (for now): deep integration with simulatte’s AGV/picking controllers; keep the jobshop layer self-contained.  
- Metrics/plots kept minimal (queue/utilization already in Server); can integrate with simulatte logger later.

## Mapping of concepts
- **Job** ↔ customer order or pallet build request; routing ↔ sequence of warehouse/picking/packing stations.  
- **Server** ↔ workstation/picking cell/packing lane; capacity>1 allows parallel handling.  
- **ShopFloor** ↔ overall processing area; tracks WIP, completions, EMA stats.  
- **PreShopPool** ↔ backlog buffer; release policies emulate wave/continuous release.  
- **Router** ↔ demand generator (inter-arrival + routing + service & due-date sampling).

## Work plan & milestones
1. Package skeleton & environment alias.  
2. Port core flow classes: Job, Server, ShopFloor.  
3. Port flow control: PSP + policies; Router + distributions; builders/runner.  
4. Tests: adapt key rl-ppc simulation tests into `tests/jobshop/`.  
5. Docs touch-up and commit.

## Testing strategy
- Recreate rl-ppc unit scenarios with deterministic distributions to assert timings/WIP.  
- Run subset quickly in CI/local; no RL dependencies required.

## Open decisions
- Package name: `simulatte.jobshop` (default) vs `simulatte.manufacturing`.  
- Whether to include `faulty_server`/`inspection_server` in first drop; default: include (useful, low dependency).  
- DRACO policy optional later; start with base/LUMS-COR/SLAR + starvation avoidance.

## Progress log
- [x] Create package skeleton and environment alias.  
- [x] Port Job/Server/ShopFloor.  
- [x] Add PSP/Router/policies/distributions/builders/runner.  
- [x] Add/tests for core flow.  
- [x] Final doc update + commit.

**Current status (Dec 9, 2025)**  
- Jobshop package scaffolded with core flow objects, PSP/policies, router, builders, runner.  
- Added jobshop-focused pytest suite (`tests/jobshop/`) passing locally.  
- No changes made to `rl-ppc` repository.
