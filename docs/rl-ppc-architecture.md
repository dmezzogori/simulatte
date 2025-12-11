# RL-PPC Architecture (Simulation Core, excluding RL)

Analysis date: 2025-12-11  
Scope: `/Users/davide/Developer/rl-ppc/src/rl_ppc` discrete-event manufacturing simulation components (excludes `agents/`, `training/`, Gym wrappers)

## Overview
- SimPy-based job-shop DES built around a singleton environment shared by routers, PSP, servers, and shop floor.
- Models make-to-order flow: stochastic arrivals → pre-shop pool → release/dispatch policy → 6 single-capacity servers → completion metrics.
- Release layer offers workload/slack/projected-impact policies (LUMS-COR, SLAR, DRACO) plus a push baseline.
- Server resources capture queue/utilization histories, breakdowns, and rework paths to support KPI tracking (WIP, throughput, lateness).
- Builders + runner provide reproducible, multi-seed experiment execution independent of RL agents.

## Core Runtime & Utilities
- `Environment` (`environment.py`): singleton `simpy.Environment` subclass; `step` traps `KeyboardInterrupt` and raises `StopSimulation` for graceful pause.
- `Singleton` (`utils.py`): metaclass backing shared instances; `clear()` resets cached singletons between simulations.
- `Runner` (`runner.py`): rebuilds system per seed, clears singletons, seeds `random`, runs env until target time; optional multiprocessing.
- Distributions (`distributions.py`): `truncated_2erlang`, `server_sampling`, `RunningStats` utilities used to parameterize arrivals/service and analyze streams.
- Typing aliases (`typing.py`): `System`, `PushSystem`, `PullSystem`, `Builder` keep builder signatures consistent.

## Domain Components
### Flow Generation & Routing (`router.py`)
- `Router` spawns a SimPy process generating jobs with stochastic inter-arrivals, family mix, routing, service times, and due dates; sends to `PreShopPool` or directly to `ShopFloor`.
- Accepts optional `priority_policies` injected into jobs to drive server queue ordering.

### Jobs (`job.py`)
- `Job` encapsulates routing (servers + processing times), due date, creation/release timestamps, per-server entry/exit, rework flag, completion state.
- Exposes KPIs: `makespan`, `time_in_system/shopfloor`, `total_queue_time`, `slack_time`, planned slack maps per server, virtual earliness/tardiness, `priority` hook.

### Shop Floor (`shopfloor.py`)
- Singleton managing in-process jobs, WIP per server (optionally "corrected" by operation index), and completion history.
- Emits events: `job_processing_end` per operation and `job_finished_event` per job, which policies listen to.
- Maintains EMAs for makespan, tardy/early/in-window shares, time in PSP/shopfloor, total queue time; tracks hourly throughput snapshots.
- `add` schedules `main` generator: sequentially requests servers, updates WIP, signals processing end after each step, finalizes metrics on completion.

### Servers (`server/`)
- Base `Server` extends `simpy.PriorityResource`; `ServerPriorityRequest` carries job reference and priority key (job priority, timestamp, preempt flag).
- Records queue length history and utilization timeline (`_queue_history`, `_qt`, `_ut`); reports average queue length, utilization, idle time; `sort_queue` reorders by priority.
- `FaultyServer`: adds breakdown/repair cycles via time-between-failure and repair distributions; resumes remaining processing time and accumulates downtime.
- `InspectionServer`: builds on `Server`, adding `rework` hook to send items back through routing when required.

### Pre-Shop Pool (`psp/`)
- `PreShopPool` stores incoming jobs (deque); optional periodic `main` loop invokes a configured `PSPReleasePolicy` every `check_timeout`; raises `new_job` event on insert.
- `PSPReleasePolicy` base defines `release_condition` / `release`; concrete policies plug into the pool without altering shop-floor logic.

### Release & Dispatch Policies (`policies/`)
- `LumsCor`: workload-norm periodic release ordered by planned release date; enables corrected WIP on `ShopFloor`; `lumscor_starvation_trigger` pulls earliest-start job when a server empties.
- `Slar`: event-driven release on `job_processing_end`; if server idle/near-idle release job with earliest planned slack starting there; otherwise pull urgent jobs when queue holds only non-urgent; relies on job priority policy for queue sorting.
- `Draco`: unified release/authorization/dispatch scorer; on each processing end evaluates PSP + queue candidates using WIP-based projected release, next-server authorization, and four-part dispatching impact (SPT, starvation response, slack timing, pacing) to pick the next job; persists custom priorities for queue resorting.
- `starvation_avoidance_process`: generic helper releasing a newly arrived PSP job immediately if its first server is idle; composed with all policies.

### System Builders (`builders.py`)
- `build_push_system`: M/M/c-style push flow with no PSP; exponential arrivals/service, random routing over servers.
- `LiteratureSystemBuilder`: presets six single-capacity servers, arrival rate 1/0.648, truncated 2-Erlang service capped at 4, due-date allowance U[30,45]; variants:
  - `build_system_push` baseline,
  - `build_system_lumscor` (periodic release + starvation triggers),
  - `build_system_slar` (event-driven release, planned-slack priorities),
  - `build_system_draco` (projected-impact dispatch); each registers policy processes on the shared `Environment`.

## Control & Policy Surface
- Release layer is pluggable via `PSPReleasePolicy` implementations (periodic, event-driven, projected-impact) plus starvation helpers.
- Dispatch priorities injected through job `priority_policy` functions and enforced by `ServerPriorityRequest.key` / `sort_queue`.
- Routing/service/arrival/due-date distributions provided as callables, enabling scenario-level parameterization without touching core classes.
- Builder knobs expose WIP targets, workload norms, allowance factors, release check cadence, arrival/service rates.

## Design Patterns & Architectural Intent
- Singleton event loop (Environment/ShopFloor/Router/PSP) simplifies wiring; `Singleton.clear()` prevents cross-run leakage (used by `Runner`).
- Event-driven triggers (`job_processing_end`, `new_job`) decouple release/dispatch policies from processing logic.
- Strategy/template style: release policies implement `release_condition`; routers accept injected distributions; servers honor injected priority policies; specialized servers subclass `Server`.
- Instrumentation baked into resources (queue/utilization histories, EMAs, throughput snapshots) to surface KPIs without external observers.
- Composition-first: policy processes are registered on the environment rather than subclassing ShopFloor/PSP, keeping core simulation lean.

## High-Level Capabilities
- Simulates make-to-order job shops with PSP gating, stochastic arrivals/service/routing, and per-operation processing across multiple servers.
- Produces KPIs: WIP per server, queue lengths, utilization/idle time, hourly throughput, EMAs for makespan and lateness/earliness windows.
- Captures reliability and quality effects through breakdown-aware and inspection/rework server variants.
- Supports multi-seed, multi-policy experiments via builder + runner, serving as a standalone DES backbone and as a substrate for downstream RL studies.
