# AGV traffic control, part A acceptance

Branch `feature/agv-traffic`, spec `specs/2026-11-21-agv-traffic-design.md` (§17 and Appendix A). Part A replaced node
reservation with claim-based traffic control: the traffic network of segments, zones and corridors, the
`ReservationController` with waits, deadlock detection and the standard policy, the router, the travel executor, idle
behaviour, bounded re-queues, the traffic KPI collector, layout schema 1.1 and the Studio support. Parts B (motion) and
C (routing) are not covered here, so the §17 items "Motion", "Stop requests" beyond the behaviour already ruled for
part A, and "Routing" are out of scope.

## Result

The end-to-end scenarios of Appendix A and of §17 behave as the spec says. The scenarios found one crash, since fixed
(see "Findings"). The Appendix A scenarios complete every order, the head-on,
swap, fairness and gate-waiver cases end to end resolve as specified, a lone AGV matches free flow, and the invariants
hold in every scenario and in the three intralogistics examples run under `ReservationController`.

## §17 items of part A and the tests that cover them

`tests/intralogistics/test_traffic_scenarios.py` (this task) runs complete worlds through `FleetCoordinator` with real
orders. Every world there runs `FullReplay` (replayed deltas equal the live state of every entity after every event,
from the first event and from the initial state) and `TrafficInvariants`. Earlier tasks pin the unit level; where they
already cover an item end to end, the table names them instead of repeating them.

| §17 item | Test |
|---|---|
| Invariants over every scenario and the three examples | `TrafficInvariants` in `traffic_support.py`, attached in every world of `test_traffic_scenarios.py`, `test_idle_traffic.py` and `test_travel.py`; `test_the_examples_hold_the_traffic_invariants_under_a_reservation_controller[intralogistics_simple, intralogistics_intermediate, intralogistics_advanced]` |
| Baseline scenarios (Appendix A) | `test_appendix_a_scenario_1_completes_every_order`; `test_appendix_a_scenario_2_completes_every_order_and_the_idle_agv_makes_way_to_park` (the later AGV first waits while standing on `PARK`; each idle AGV left at `C2` makes way to `PARK`) |
| Following | `test_following_on_a_subdivided_one_way_line`; at unit level `test_travel.py::test_a_follower_waits_and_proceeds_when_the_leader_tail_clears` |
| Head-on in a corridor | `test_head_on_in_a_corridor_the_second_agv_waits_before_its_port`; at a passing place `test_travel.py::test_a_head_on_meeting_at_a_passing_place_completes_with_a_length` and `test_two_agvs_head_on_at_a_node_of_capacity_two_complete_through_one_resolved_deadlock[corridors, dead_ends]` |
| Swap on a two-node line | `test_traffic_termination.py::test_two_agvs_swapping_on_a_line_leave_one_unresolved_deadlock_and_the_run_returns` |
| Stuck wait | `test_traffic_termination.py::test_a_loaded_agv_stuck_behind_an_idle_one_that_cannot_move_leaves_one_stuck_wait_and_the_run_returns` |
| Default builder | `test_the_default_builder_completes_twenty_transfer_orders_with_no_deadlock_and_no_stuck_wait`; `test_the_default_builder_with_orders_in_both_directions`; `tests/layout/test_builders.py::test_layout_builder_with_a_reservation_controller` |
| Two fleets on one controller | `test_two_fleets_share_a_controller`; `test_traffic.py::TestFreeFlowController::test_two_fleets_on_one_graph_share_one_controller` for free flow |
| Lone AGV | `test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_the_appendix_layout[0.0, 1.0]`, `..._on_the_builder_line`, `..._on_a_line_split_into_arcs[1, 2, 10]` |
| Giving up | `test_traffic_termination.py::test_an_agv_with_cargo_does_not_give_up_a_charging_diversion`, `::test_an_order_is_requeued_for_traffic_at_most_max_traffic_requeues_times` |
| Resolution by reroute and by step aside | `test_a_swap_with_a_second_route_is_resolved_by_reroute`; `test_a_swap_without_another_route_is_resolved_by_step_aside`; at unit level `test_deadlock.py::TestResolution` |
| Fairness | `test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside[port-1, port-2]`; at unit level `test_reservation.py::TestGate` |
| Gate waiver | `test_a_gate_that_would_complete_a_cycle_is_waived`; at unit level `test_deadlock.py::TestDetection::test_a_cycle_only_through_a_gate_is_waived` |
| Idle and parking | `test_idle_traffic.py`: `test_an_idle_blocker_makes_way_and_goes_idle_again`, `test_an_agv_idle_on_a_roomy_exit_port_of_a_corridor_does_not_block_the_opposite_traffic`, `test_an_idle_agv_is_not_shuttled_between_two_zones`, `test_an_agv_leaves_the_delivery_bay_under_reservation`, `test_an_area_of_two_never_lists_three_agvs`, `test_a_full_area_is_not_a_target` |
| Capacity above one, extent longer than a block, `passing` segments, authored zones | `test_travel.py::test_two_vehicles_at_a_multi_slot_leaf_one_sent_away`, `::test_extent_longer_than_arc_holds_two_segments`; `test_reservation.py::TestAdmission::test_passing_admits_both_directions`, `::TestAdmission::test_zone_capacity`, `::TestRelease::test_hold_back_zone_is_claimed_a_leg_early`; `test_network.py::TestCorridors::test_passing_segment_removes_the_corridor`, `::TestClaims::test_plain_zone_only_with_the_leg_that_takes_a_member`, `::TestClaims::test_a_root_of_capacity_two_is_held_back_like_any_root`, `::TestClaims::test_a_port_of_capacity_two_carries_the_corridor_like_any_port`; `test_an_agv_parked_on_a_roomy_port_is_dispatched_through_the_empty_corridor_at_once`, `test_a_vehicle_does_not_wait_on_a_roomy_port_where_a_resting_agv_holds_the_other_slot`, `test_a_swap_past_a_resting_agv_on_a_roomy_root_is_a_reported_deadlock` |
| Vehicle length | `test_travel.py::test_coordinator_registers_with_a_reservation_controller`; `test_reservation.py::TestLifecycle::test_a_vehicle_type_without_length_cannot_register` |
| Replay and purity | `FullReplay` in every world; `test_invariance.py`, which runs the advanced example under free flow and under `ReservationController` with no subscriber at all, with the digest only, with a key-performance-indicator trace, with a full trace and with everything on, and compares the final state, the digest, the report and the trace, and reproduces the digest in fresh processes with different `PYTHONHASHSEED` values; CPython and PyPy agree through the PyPy lane of CI |
| Layout | `tests/layout/test_traffic_fields.py` (schema 1.0 loads unchanged, 1.1 round-trips, `block_length` and zones change the physical hash, twins) |
| Free flow | `test_free_flow_creates_no_segment_or_zone_entity_and_emits_no_traffic_event`; `test_travel.py::test_free_flow_arrivals_and_events` |

Choices the scenarios make where the spec leaves them open:

- The builder scenario needs a `Layout` because `ReservationController` takes its graph at construction; the controller
  is made from `layout.graph()` before `build_simple_system(env, traffic_controller=..., agv_length=1.0)`. The twenty
  orders go from A to B with the builder's defaults; the second test sends them in both directions with a large battery,
  because with the default battery of 100 an AGV runs out of charge on the way to the charger under `FreeFlowController`
  as well (`AgvStranded`, `no_reachable_charger`), which has nothing to do with traffic.
- A lone AGV is compared with `clear_service_nodes=False`: under reservation an idle AGV leaves a bay by default, and
  that move is the one deliberate difference between the controllers. With it off, the arrivals and order times are
  equal to the last bit.
- The three examples run with each coordinator on its own `ReservationController` and vehicle length 0. Since the
  whole-part review the test also asserts that no deadlock is left unresolved, that no stuck wait is still open at the
  end, and that the intermediate and advanced examples complete as many orders (outbound orders for the advanced one)
  as the same example under free flow. The numbers are under "The examples under both controllers".
- The gate waiver was found by searching for runs in which the claims waiting form a cycle only through gates: a
  corridor with three AGVs (`long_corridor` in the test file). The test observes the waiver with a controller subclass
  that reads the controller's queue, as `TrafficInvariants` reads its holdings. In about 2,500 random order sets on a similar
  corridor graph none had a waiver without a real deadlock elsewhere in the run; the scenario of the test has one real deadlock later, which the
  standard policy resolves by step aside.

## Findings

1. **A reroute asked of an AGV that stands on its goal raised.** The scenarios found it: an AGV sent aside along a
   route that passes through its goal stood on the goal, was asked for a reroute, and `_Trip._try_reroute` asked the
   router for a route from the goal to itself, a route of zero legs, whose first claim raised
   `ValueError: index 0 is outside the 0 legs of the route`. Fixed under the amended spec §7.9: the AGV has arrived,
   the reroute succeeds with the one-node route and the travel ends with `ARRIVED`. Covered by
   `test_travel.py::test_a_reroute_asked_of_an_agv_on_its_goal_ends_the_travel_as_arrived` and, end to end,
   `test_traffic_scenarios.py::test_a_reroute_asked_of_an_agv_on_its_goal_during_a_step_aside_does_not_raise`.
2. Under the default builder battery (100), orders in both directions strand an AGV under free flow and under
   reservation alike (see above). It is energy, not traffic.

## Benchmark

`benchmarks/intralogistics.py`, the advanced example scaled to 20 AGVs, one shift, intervals 30 to 60, one warm-up and
three measured repeats in each of two fresh processes. Apple M4 Pro, CPython 3.14.3, the same interpreter and
dependencies for both sides, run one after the other with nothing else running. The branch ran in this checkout at
`53ba6ff`; `main` (`ea1292c`) was exported with `git archive main | tar -x -C /tmp/t14-main/src` and run there after
its own `uv sync --all-extras`. Commands, from `benchmarks/` of each tree:

```bash
for mode in none default default_logging; do
  uv run --all-extras python intralogistics.py --mode "$mode" --shifts 1 \
    --warmup 1 --repeat 3 --processes 2 --json "/tmp/t14-out/il-<side>-$mode.json"
done
```

| Mode | `main` median (IQR), s | Branch median (IQR), s | Branch / `main` | Peak memory `main` / branch, MB |
|---|---:|---:|---:|---:|
| none | 0.19021 (0.00722) | 0.18441 (0.00740) | 0.970 | 39.0 / 41.8 |
| default | 0.21161 (0.00582) | 0.20325 (0.00641) | 0.960 | 39.3 / 41.8 |
| default_logging | 0.18565 (0.01297) | 0.18387 (0.01077) | 0.990 | 38.9 / 41.8 |

Under free flow the branch is not slower than `main` within the noise of these short runs (IQR about 4 %); peak
memory rises by about 2.8 MB. The two sides do not run the same trajectory, so `compare.py` rejects the pair as not
comparable (different lifecycle hashes); the numbers above are the medians of the JSON files. `main` reproduces the
documented hashes of `benchmarks/intralogistics-baseline.md` (input `3278fbdc...`, trajectory `b2a2ca3d...`, 1,018
orders, 1,011 completed). The branch has 1,018 submitted orders and 1,012 completed, input hash `a4174de4...`
and trajectory hash `c4ae2694...`. The inputs differ because replenishment orders are submitted when stock crosses a
reorder point, which moves with the trajectory; the trajectory differs for the reasons under "Changes in results".

## Verification

| Check | Result |
|---|---|
| `uv run --all-extras pytest` | 3,930 passed, 1 skipped, 3 warnings, 338 s; branch coverage 99.14 % (gate 99 %, previous 99.05 %) |
| `uv run --all-extras ruff check src tests` | all checks passed |
| `uv run --all-extras ruff format --check` | 514 files already formatted |
| `uv run --all-extras ty check src` | all checks passed |
| `pnpm typecheck && pnpm test` (from `studio/`) | typecheck passed; tests passed: trace 319, playback 24, scene 20, export 15, viewer 82 |

## Pinned values that changed in part A

| Value | Change | Cause |
|---|---|---|
| `tests/intralogistics/test_builders.py::test_no_layout_builder_digest_remains_compatible` | `59489157...` to `7f761d74b0cbe8441e56de7b181fd7cc58c39900510eab9e6671e531256870ca` | the new `agv.routed` event enters the digest; with `env.wants(AgvRouted)` forced false the old digest returns exactly. Unchanged since, checked again in this task. |
| `tests/fixtures/fleet_recovery_regression.json` (`simple-1` to `simple-3`) | re-recorded | interrupts take effect at the end of the arc (spec §8.3); with the old interrupt semantics the old fixture is reproduced exactly |
| `tests/fixtures/fleet_collector_parity.json`, entry `systems.advanced` | re-recorded (EMA, utilization, pending, throughput, inventory series) | the coordinator takes parking slots; the advanced example, which left its parking area empty, now fills it and no longer repositions to a full area |

Nothing was re-pinned in this task, nor in the whole-part review wave: no free-flow pin moved, the generated trace
fixtures are not stale (`tests/core/test_trace_fixtures.py` passes) and the frozen fixtures load without change.

## The examples under both controllers

Measured after the whole-part review wave, at its last commit, by running each example as the test does (each
coordinator on its own `ReservationController`, vehicle length 0) and as written (free flow). Completed orders are
those the example prints: all orders for the simple and intermediate examples, outbound and replenishment for the
advanced one. Fulfillment is the example's own average (the simple example prints none). Waits are
`traffic.wait_started` events; no wait and no stuck report was open at the end of any run.

| Example | Controller | Completed orders | Waits | Deadlocks by action | Stuck waits | Avg fulfillment, s |
|---|---|---|---:|---|---:|---:|
| simple (horizon 120) | free flow | 4 of 4 | 0 | none | 0 | - |
| simple (horizon 120) | reservation | 3 of 4 | 7 | none | 0 | - |
| intermediate | free flow | 8 of 8 | 0 | none | 0 | 151.2 |
| intermediate | reservation | 8 of 8 | 20 | reroute 1, step aside 5 | 0 | 229.5 |
| advanced | free flow | 63 outbound, 2 replenishment | 0 | none | 0 | 217.8 (outbound) |
| advanced | reservation | 63 outbound, 2 replenishment | 126 | step aside 38 | 1 | 239.6 (outbound) |

Before the wave the advanced example under reservation had 249 waits, 75 deadlocks (all step aside), 19 stuck reports
(one still open at the end of the shift), 62 outbound orders completed and an average outbound fulfillment of about
700 s. The simple example completes 3 of 4 orders by its horizon of 120, as before the wave: its AGVs take turns on a
line they shared freely before, and its last order is in transit at the horizon.

In the advanced run after the wave, the victim of every one of the 38 deadlocks is an idle AGV on a make-way move whose
route passes `R2`, the node where the waiter it makes way for stands: 37 moves `B1 → PARK` and 1 `BULK_OUT → PARK`.
The move meets its own waiter head-on and steps aside. The make-way search checks the first claim of the route and the
routes of the waits concerned for the target, not for the nodes the route passes. The one stuck report (461.3 s, AGV-3
behind AGV-5 idle on its starting node) ends at the next review, 30 s later, with a grant. Idle AGVs made 51 make-way
moves and 57 service-node clearances.

## Changes in results

- Free-flow results move for the three reasons above (the `agv.routed` event in digests, interrupts at the end of the
  arc, parking slots). The benchmark's trajectory difference from `main` is caused by the parking slots being taken:
  the whole-part review reproduced `main`'s input and trajectory hashes exactly on the branch with slot-taking
  disabled, and `NearestParkingPolicy` now sees the area full and leaves AGVs at the delivery bay.
- A critical battery found on arrival at the goal returns `ARRIVED` (spec §8.3), so the mission goes on at the goal
  before it charges. It does not occur in the benchmark workload; in a constructed free-flow case the pickup moves from
  251.5 on `main` to 73.0, with the same delivery time.
- A stalled AGV (an arc with an infinite travel time) leaves no timer at infinity, so `env.run()` without `until` ends
  at the last finite event.
- One `FreeFlowController` serves one graph: registering a fleet on another graph raises `ValueError`; fleets on
  different graphs each take their own controller, as they do by default.
- A route's cost is the `fsum` of its arc lengths (`graph.path_length`), as every other user of route cost computes
  it, instead of the running sum of the search; this can change `agv.routed.cost` by one unit in the last place. No
  pinned value moved.
- The retry loop of the pending queue stops when nothing needs it, so `env.run()` without `until` returns. In free flow
  this can move the time of a `retries_exhausted` failure, because the loop restarts when it is needed again.

## Whole-part review

Two reviewers examined part A as a whole. Safety held in every run they made; the findings were about liveness. This
wave changed:

- `StandardTrafficPolicy.on_blocked` offers a reroute only while the wait's resolutions are below `max_resolutions`, so
  a travel between standing blockers is not rerouted back and forth without end (spec §7.9).
- A corridor left onto a port of capacity above 1 keeps no exit record and goes with its last member (§7.5), so an AGV
  resting on such a port no longer holds the corridor oriented. The hold-back at ports and roots of capacity above 1
  stays as for capacity 1 (§7.2): letting a vehicle wait on such a node was tried and withdrawn, because with one slot
  held by a resting AGV the waiting vehicle takes the slot the oncoming one needs and neither moves, with nothing
  reported. Passing at a passing place defined by capacity is a stated limit (§18): two vehicles that meet head-on
  there deadlock and one steps aside.
- An idle AGV does not rest on the root of a dead-end branch unless it is a parking node (`TrafficView.branch_root`);
  members stay valid targets (§7.9, §10.1).
- A review judges a stuck wait by the blockers after its make-way step, so a blocker that has just started to move is
  not reported.
- The idle search does not take a node whose last leg needs a segment, zone or corridor a pending claim names, and an
  AGV does not make way to a node it has made way from in the same chain of make-way moves; the chain ends when the
  AGV starts a mission or becomes idle where it blocks no wait (§7.9, §7.10).
- Small defects: the parking slot of the goal is kept during a step-aside excursion through it; a stranded AGV is not
  cleared off a service node; the step-aside search and its move ask the router alike; `_validate_fleet` no longer
  compares classes; the protocol value types are exported; the route cost is `graph.path_length`; a one-way ring is not
  reported; the unresolved-deadlock test checks `env.peek()`.
- The examples test asserts no unresolved deadlock, no stuck wait open at the end and, for the intermediate and
  advanced examples, as many completed orders as under free flow.
