# AGV traffic, part C: routing and release — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** approved by Davide on 2026-11-21 together with the design. No task is implemented yet.

**Goal:** Routing minimises time and can take traffic into account; dispatch, bay, charger and parking choices use the router's cost; the whole AGV traffic work is documented and prepared for one release.

**Architecture:** Two routers are added behind the `Router` protocol of part A: `FastestRouter` costs arcs by time and nodes by turning, and `CongestionAwareRouter` adds an expected wait read from the `TrafficView`. Everything that used to rank by path distance now asks the fleet's router. The default router stays `ShortestDistanceRouter`, so default decisions do not change.

**Stack:** Python 3.11+ standard library; Zensical for the docs.

**Spec:** [`specs/2026-11-21-agv-traffic-design.md`](../specs/2026-11-21-agv-traffic-design.md), §10, §14, §16, §17. Signatures below are boundaries, not licence to weaken a contract stated there.

**Follows:** [part A, control](2026-11-21-agv-traffic-a-control.md) and [part B, motion](2026-11-21-agv-traffic-b-motion.md), both complete.

## Global constraints

- Same branch, commit, coverage, lint, typing and determinism rules as part A.
- With the default router, every dispatch, bay, charger and parking decision equals the one made at the end of part B.
- A router's cost is comparable only within that router. No code compares costs from two routers.
- `mean_hold` is maintained whether or not anything observes it; it must not change any result under `ShortestDistanceRouter` or `FastestRouter`.
- `CongestionAwareRouter` is a heuristic and is documented as one.
- One release for parts A, B and C: one pull request from `feature/agv-traffic`, squash-merged by the maintainer. The version bump is the last commit.

## Review focus

| Risk the happy path misses | Owning test |
| --- | --- |
| A custom router returns a route that does not start at the origin, repeats a node, or uses a missing arc. | Task 1 `test_routing.py::test_executor_rejects_invalid_routes` |
| `avoid` makes the destination unreachable: the router returns `None`, the reroute is not staged and the AGV keeps waiting. | Task 1 `test_routing.py::test_avoid_can_make_destination_unreachable` |
| `CongestionAwareRouter` under `FreeFlowController`, or on an empty network, must equal its base router. | Task 2 `test_routing.py::test_congestion_router_equals_base_without_traffic` |
| A custom router returns a negative, NaN or infinite cost. | Task 3 `test_policies.py::test_invalid_route_cost_raises` |
| A custom `DispatchStrategy` written for the old `select(order, fleet, graph)` receives a context: the migration note must say so, and `DispatchContext.graph` must be the same graph object. | Task 3 `test_policies.py::test_dispatch_context_exposes_graph`; Task 4 migration section |

## Sequence and gates

Tasks run in order. Task 5 is the release gate. Every task ends with the full suite: `uv run --all-extras pytest`.

---

### Task 1 — `FastestRouter`

**Modify:** `src/simulatte/intralogistics/routing.py`, `src/simulatte/intralogistics/__init__.py`, `tests/intralogistics/test_routing.py`.
**Interfaces (produces):** `FastestRouter()`; `Route.cost` in time units.

Cost (spec §10.2): for an arc, `length / min(v_max, speed_limit)` with `limits(request.load_weight, agv.battery.level_pct)`, or `travel_time(length, load_weight, battery_level, speed_limit)` for a profile without `limits`. For a node between two arcs, `turn_time(angle)` plus the time lost slowing from the lower adjacent arc speed `u` to `corner_speed(angle)` `c` and back: `(u - c) ** 2 / (2 * u) * (1 / accel + 1 / decel)` when `c < u`. With `request.agv is None` the router falls back to path length. With a turning model the search state is the node and the incoming arc.

- [ ] Tests: on a diamond with a 20 m arm limited to 0.5 and a 30 m arm at 1.5, `ShortestDistanceRouter` takes the short arm and `FastestRouter` the long one; the cost of a straight 30 m route is 20.0 at `v_max=1.5`; a route with a right-angle turn and `turn_time=lambda a: 4.0` costs 4.0 more than the straight one of equal length, and the router prefers a longer straight route when the difference is below 4.0; a `corner_speed` of 0.5 at `u=1.5`, `accel=decel=0.8` adds 0.8333333333333334; a loaded request with a `load_speed_factor_fn` changes the cost; ties break by node id; `test_avoid_can_make_destination_unreachable`; `test_executor_rejects_invalid_routes` (three invalid custom routes: `ValueError` naming the router).
- [ ] Implement.
- [ ] `uv run --all-extras pytest tests/intralogistics/test_routing.py --no-cov -q`, full suite, commit `feat(intralogistics): fastest router`.

### Task 2 — `mean_hold` and `CongestionAwareRouter`

**Modify:** `src/simulatte/intralogistics/reservation.py`, `src/simulatte/intralogistics/routing.py`, `src/simulatte/intralogistics/__init__.py`, `tests/intralogistics/test_reservation.py`, `tests/intralogistics/test_routing.py`.
**Interfaces (produces):** `TrafficView.mean_hold(resource) -> float`; `CongestionAwareRouter(base: Router | None = None, *, wait_weight: float = 1.0, horizon: float = 60.0)` with `base` defaulting to `FastestRouter()`.

`mean_hold`: an exponential average with factor 0.1 of the durations for which the resource was held, updated at each release. Before a resource has an observation it reports the average over the resources of the same kind, and 0.0 before any observation.

Penalty (spec §10.2): when a leg is expanded at accumulated cost `t`, add `wait_weight * w * exp(-t / horizon)`, where `w` is the largest, over the leg's resources that are not admissible for the requesting AGV now, of `(blocking holders + view.pending(resource)) * view.mean_hold(resource)`. A resource held to capacity by stranded AGVs excludes the leg. `Route.cost` includes the penalties.

- [ ] Tests, `mean_hold`: three holds of 10, 20 and 30 on one node give 10, then 11, then 12.9; an unobserved node reports the average of the observed nodes; values are identical with and without a subscriber; `FreeFlowController.view.mean_hold` is 0.0.
- [ ] Tests, router: on a two-arm loop with one arm occupied by a serving AGV and `mean_hold` 40 on its node, the router takes the free arm when its extra time is below the penalty and the occupied arm when it is above; the penalty of a leg reached at `t = horizon` is `1 / e` of its value at `t = 0`; a node filled by a stranded AGV is avoided and makes the destination unreachable when it is the only way; `test_congestion_router_equals_base_without_traffic`; `wait_weight=0` equals the base router; `horizon <= 0` and negative `wait_weight` raise `ValueError`.
- [ ] Test: a run with `CongestionAwareRouter` has identical digests across `PYTHONHASHSEED` values.
- [ ] Implement.
- [ ] Focused run, full suite, commit `feat(intralogistics): congestion-aware router and resource hold times`.

### Task 3 — Route cost for dispatch, bays, chargers and parking

**Modify:** `src/simulatte/intralogistics/policies.py`, `fleet.py`, `travel.py`, `warehouse.py`, `__init__.py`, `tests/intralogistics/test_policies.py`, `tests/intralogistics/test_coordinator.py`, `tests/intralogistics/test_charging.py`, `tests/intralogistics/test_parking.py`, and any test or example with a custom `DispatchStrategy` or `RepositioningPolicy`.
**Interfaces (produces):**

```python
@dataclass(frozen=True)
class DispatchContext:
    graph: LayoutGraph
    router: Router
    view: TrafficView

    def cost(self, agv: AGV, node: Node, *, load_weight: float = 0.0) -> float: ...  # inf when unreachable


class DispatchStrategy(Protocol):
    def select(self, order: TransferOrder, fleet: Sequence[AGV], context: DispatchContext) -> AGV | None: ...


@dataclass(frozen=True)
class RepositioningContext:
    graph: LayoutGraph
    parking_areas: Sequence[ParkingArea]
    charging_stations: Sequence[ChargingStation]
    fleet: Sequence[AGV]
    router: Router
    view: TrafficView

    def cost(self, agv: AGV, node: Node) -> float: ...


class FleetCoordinator:
    def best_bay(self, agv: AGV, bays: Sequence[Node], *, load_weight: float = 0.0) -> Node: ...
```

`Warehouse.nearest_input_bay` and `nearest_output_bay` stay as chord-distance helpers; the coordinator stops calling them.

- [ ] Tests: `NearestIdleStrategy` picks the AGV with the lowest router cost to its best output bay, ties by AGV id, and returns `None` when no idle AGV can reach it; with `FastestRouter` and a slow arm it picks a different AGV than with the default router; `RoundRobinStrategy` is unchanged apart from the signature; `NearestParkingPolicy` ranks areas by cost; the coordinator picks bays with `best_bay` and chargers by cost; the reachable-charger energy estimate sums the route's arc lengths, including an arc with an explicit `length`; `test_dispatch_context_exposes_graph`; `test_invalid_route_cost_raises`.
- [ ] Regression test: on the three intralogistics examples and on spec Appendix A, the sequence of `order.assigned` events under the default router equals the one recorded at the end of part B (compare against a run of the same model with a strategy that reimplements path-length ranking).
- [ ] Implement, and replace every remaining `graph.shortest_path` call in `fleet.py`, `policies.py` and `travel.py` with the router.
- [ ] Focused runs, full suite, commit `feat(intralogistics)!: dispatch, bays, chargers and parking rank by route cost`.

### Task 4 — Documentation, examples, changelog, spec amendments

**Create:** `examples/intralogistics_traffic.py` (a corridor layout with a passing place, a parking area, `ReservationController`, `FastestRouter` and `TrafficKPIs`; prints blocked time and deadlock counts), a test for it in `tests/intralogistics/test_examples.py`.
**Modify:** `docs/guides/intralogistics.md`, `docs/guides/layout.md`, `docs/tutorials/building-an-agv-system.md`, `docs/tutorials/logging.md`, `docs/api/intralogistics.md`, `docs/api/layout.md`, `docs/examples/intralogistics.md`, `docs/introduction/architecture.md`, `docs/intralogistics/index.md`, `docs/ai-skill.md`, `CHANGELOG.md`, `docs/development/changelog.md` if it mirrors it, `CLAUDE.md`, `specs/2026-10-08-sp1-events-trace-design.md`, `specs/2026-10-10-sp2-layout-design.md`, `specs/studio-decisions.md`, the three existing intralogistics examples where they name removed APIs or would read better with the new ones.

- [ ] `docs/guides/intralogistics.md` gains a traffic section covering: the two controllers and when to use each; nodes, segments, zones, corridors and dead ends, with one diagram in text; vehicle length and what a length of 0 means; how claims, waiting and release work; deadlocks, what "unresolved" and "stuck" mean, and how to design a layout that avoids them (passing places, capacity above 1, parking areas, one-way loops); splitting long arcs into blocks; the policy and how to write one; make-way and service nodes; motion through nodes, look-ahead, turning, the documented limit of spec §9.3; the three routers and that the congestion-aware one is a heuristic; `TrafficKPIs`.
- [ ] `docs/guides/layout.md`: replace the "Traffic reserves nodes only" paragraph; document `passing`, `block_length`, `zones` and schema 1.1.
- [ ] `docs/tutorials/logging.md`: the event table follows spec §11.2. `docs/api/*.md`: every new public name of spec §14 and none of the removed ones.
- [ ] `CHANGELOG.md`: an entry for the next release with `Added`, `Changed`, `Removed` and a `Migration` section that reproduces the table of spec §14, states that travel times and digests of intralogistics models change, that `AGVType.length` is required with `ReservationController`, and that custom dispatch strategies and repositioning policies receive a context.
- [ ] `CLAUDE.md`: the repository structure and the intralogistics architecture section.
- [ ] Spec amendments: append "AGV traffic amendment (2026-11-21)" to the SP1 spec (state schemas `segment` and `zone`; the event changes of spec §11.2; the `piecewise` curve; no container change) and to the SP2 spec (schema 1.1 fields; §7 superseded; traffic owner validation by protocol); add one line under D61 in `specs/studio-decisions.md` saying it is superseded by `specs/2026-11-21-agv-traffic-design.md`.
- [ ] `uv run --all-extras zensical build` is clean; `grep -rn "TrafficManager\|PathPlanner\|traffic_manager\|path_planner\|DijkstraPlanner\|AStarPlanner" docs examples src CLAUDE.md` finds nothing outside the changelog's migration table.
- [ ] Commit `docs(intralogistics): traffic control, motion and routing`.

### Task 5 — Release gate

**Modify:** `specs/research/2026-11-21-agv-traffic-acceptance.md`, `benchmarks/intralogistics-baseline.md`, `.github/workflows/ci.yml` only if a lane lists test files that were renamed, `pyproject.toml`.

- [ ] Walk spec §17 line by line and record, in the acceptance file, the test that covers each item; add any missing test to the file of the task that owns the code.
- [ ] Full gates, recorded in the acceptance file: `uv run --all-extras pytest` (coverage >= 99 %), `uv run --all-extras ruff check src tests`, `uv run --all-extras ruff format --check`, `uv run --all-extras ty check src`, `uv run --all-extras zensical build`; in `studio/`: `pnpm typecheck && pnpm test && pnpm build && pnpm test:browser`; the PyPy lane command from `ci.yml`.
- [ ] Run `benchmarks/intralogistics.py` on the branch and on `main` and update `benchmarks/intralogistics-baseline.md` with both sets of numbers and the reason they differ. The no-subscriber overhead gate of the core workload must still pass.
- [ ] Whole-branch review against the spec: no coordinator code reads a controller, router or policy outside its protocol; no timer survives a quiescent network; replay equals live state; the default configuration (free flow, shortest distance) makes the same dispatch decisions as `main`.
- [ ] Bump `pyproject.toml` to the next free minor version and date the changelog entry. Commit `chore(release): prepare the AGV traffic release`.
- [ ] Open the pull request from `feature/agv-traffic`; its description links the spec, the three plans and the acceptance file.

**Acceptance:** CI green on every lane. Merging, tagging and publishing are maintainer actions.

## Review handoff

Per task, a reviewer reads the tests against spec §10 and checks the default-router regression. The final review of Task 5 covers the whole branch.
