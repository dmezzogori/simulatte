# Changelog

All notable changes to Simulatte are documented here.

## 0.9.0 — 2026-05-29

### New

- **`Draco` release policy** (`simulatte.policies.Draco`) — non-hierarchical WIP
  control merging release, authorization, and dispatching into a single
  per-server decision on each job completion. Implements Kasper, Land &
  Teunter (2023, *IJPE* 257, 108768) in full-DRACO configuration
  (`w^R=0.25, w^A=0.25, w^D=0.5`). `Draco.__init__` uses the
  active-construction pattern: pass `shopfloor`, `router`, and optionally
  `psp` and it self-wires all hooks on construction (mirroring `Slar`).
  Builder: `build_draco_system()`.

- **`Focus` dispatching rule** (`simulatte.dispatching_rules.Focus`) — five
  composable scheduling mechanisms (SPT, starvation avoidance, slack,
  pacing, WIP-balance entropy) unified into a single `(job, server) →
  float` priority callable. Implements Thürer, Land & Stevenson (2023,
  *Omega* 114, 102726). Builder: `build_focus_system()`.

### Changed

- **`Draco` constructor** is active (like `Slar`): pass `shopfloor`,
  `router`, and optionally `psp`, and hook registration happens on
  construction. No manual wiring required.

## 0.8.0 — 2026-05-28

### New

- **Dispatching-rule catalog** in `simulatte.dispatching_rules`. Adds six stateless rules — `shortest_processing_time`, `earliest_due_date`, `operational_due_date` (Land, Stevenson & Thürer, 2014), `modified_operational_due_date` (Baker & Kanet, 1983), `critical_ratio` (Berry & Rao, 1975) and `first_come_first_served` — plus a parameterized factory `slack_per_remaining_operation(allowance)` (Kanet, 1982) alongside the existing `planned_slack_time`. All are `(job, server) → float` callables usable as `Router(priority_policies=…)` or `ProductionJob(priority_policy=…)`. Rules are grouped by scheduling family across `dispatching_rules.processing`, `dispatching_rules.due_date`, and `dispatching_rules.slack`.
- **`BaseJob.unfinished_routing`** — property returning the servers whose operations have not yet completed (servers not yet exited), including the in-progress one. Used by `critical_ratio` and `slack_per_remaining_operation` to compute remaining processing time and the remaining-operation count.

### Changed (breaking)

- `builders.spt_priority_policy` removed; use `simulatte.dispatching_rules.shortest_processing_time` instead.
- `BaseJob.planned_slack_time` property removed; use `BaseJob.planned_slack_time_at(server, allowance=…)` or the `simulatte.dispatching_rules.planned_slack_time(allowance)` rule factory instead.

## 0.7.0 — 2026-05-26

### New

- **`SlarLimit` release policy** — SLAR with per-server workload-norm upper bounds, preventing superfluous load even when SLAR's base rule would allow a release. Derived from Thürer & Stevenson (2021), *Int. J. Production Economics*, 231, 107881. Available via `simulatte.policies.SlarLimit` and `build_slar_limit_system()`.
- **`simulatte.dispatching_rules` package** — new home for dispatching-rule callables. Introduces `planned_slack_time(allowance)`, a factory producing `(job, server) → float` PST priority callbacks (Land & Gaalman, 1998).

### Changed (breaking)

- **`Slar` constructor** now accepts `shopfloor`, `psp`, and optional `router` and self-registers its completion trigger and starvation-avoidance hook on construction. Previous pattern of `Slar(allowance_factor=k)` + manual `env.process(on_completion_trigger(…))` no longer works as-is; replace with `Slar(shopfloor=sf, psp=psp, router=router, allowance_factor=k)`.
- `Slar.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.
- `LumsCor.pst_priority_policy` removed; use `simulatte.dispatching_rules.planned_slack_time` instead.

### Documentation

- Auto-generated API reference page (`/reference`) powered by mkdocstrings.
- New tutorial section covering SLAR-Limit usage and dispatching rules.
- Updated builder comparison table in the release-control tutorial.

## 0.6.1 — 2026-05-26

### Fixed
- **Dynamic priorities in server queues.** `Server.sort_queue` now re-evaluates `job.priority_policy(job, server)` for every queued request before sorting, so priorities computed from `env.now`, external state, or runtime policy reassignment take effect immediately. Previously, `req.key` was frozen at request-construction time and never refreshed.
- **Automatic refresh at every dispatch decision.** `Server._trigger_put` is now overridden to call `sort_queue` automatically on both the new-arrival and release dispatch paths. Dynamic priority changes no longer require an explicit `sort_queue()` call from user code.

### Documentation
- Added a *Dynamic priorities* tutorial section to the release-control-and-dispatching guide, covering time-dependent policies, runtime policy reassignment, mutable external state, the `priority_policy` purity contract, and cost model.
- Updated docstrings on `Server`, `ServerPriorityRequest`, and `Server.sort_queue` to describe the snapshot-vs-live distinction between `req.priority` and `req.key`, and the mechanism of the automatic refresh.

## 0.6.0 — 2026-05-10

### Added
- **Intralogistics subsystem** (`simulatte.intralogistics`): full warehouse-to-warehouse material transport via AGV fleets, including:
  - `LayoutGraph` with `Node`/`Arc` and Dijkstra/A* pathfinding
  - `Warehouse` with per-SKU inventory, finite pick/put slots, and deadlock-safe operations
  - `AGV` with state machine, trapezoidal speed profiles, and battery lifecycle
  - `FleetCoordinator` orchestrating dispatch, travel, pick, deliver, reposition, and charge
  - `TrafficManager` protocol with `FreeTrafficManager` and `ResourceBasedTrafficManager` (node capacity enforcement and deadlock resolution)
  - `ChargingStation` (recharge and swap) and `ParkingArea`
  - Pluggable policies: `NearestIdleStrategy`, `RoundRobinStrategy`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`
  - `EMAOrderMetrics` and `DefaultIntralogisticsCollector` with `plot_fleet_utilization()`, `plot_throughput()`, `plot_pending_orders()`, `plot_inventory()`
  - `build_simple_system()` builder for quick setup
- **ConWIP release policy** (`simulatte.policies.conwip`): Constant Work-In-Process release with shop-wide job count cap and EDD selection
- **ContinuousRelease policy** (`simulatte.policies.continuous_release`): workload-controlled continuous release using corrected aggregate load norms
- Three progressive intralogistics examples: simple, intermediate (manufacturing plant floor), and advanced (multi-warehouse distribution hub)
- AI coding agent skill for intralogistics (`simulatte-intralogistics`)
- Intralogistics documentation section with overview and examples walkthrough

### Removed
- Experimental AGV, Warehouse, and MaterialCoordinator modules (`simulatte.experimental.agv`, `simulatte.experimental.warehouse`, `simulatte.experimental.materials`, `simulatte.experimental.builders`, `simulatte.experimental.job`, `simulatte.experimental.typing`) — replaced by the `simulatte.intralogistics` subsystem

### Fixed
- Fleet pending-queue starvation: retry counter now increments even when capable AGVs exist but none are idle
- Default `pending_retry_delay` changed from 0.001 to 1.0 for realistic retry behavior
- `DefaultIntralogisticsCollector` no longer accesses private `_pending_queue` (uses `pending_count` property)

## 0.5.0 — 2026-04-29

- Add `SimulatteEnv` Gymnasium wrapper for RL integration (`simulatte.experimental.gymnasium`)
- Improve web documentation structure and content
- Bump `actions/deploy-pages` from 4 to 5

## 0.4.0 — 2026-04-29

- Add `is_idle` and `current_jobs` properties to `Server`
- Add `release()`, `jobs_starting_at()`, and `on_arrival()` callback to `PSP`
- Support sync callbacks in `OperationHook` protocol
- Add post-init hook registration: `on_before_operation()` / `on_after_operation()`
- Add `on_processing_end()` callback to `ShopFloor`
- Add `Dispatcher` protocol and `attach_dispatcher()` for one-call hook wiring
- Rewrite `starvation_avoidance` as a `psp.on_arrival()` callback
- Bump CI dependencies (actions/download-artifact, upload-artifact, upload-pages-artifact, configure-pages, codecov)

## 0.3.0 — 2026-04-28

- feat: add simulatte:dev agent skill for Claude Code integration
- fix: SLAR policy refactor and type safety improvements (#4)
