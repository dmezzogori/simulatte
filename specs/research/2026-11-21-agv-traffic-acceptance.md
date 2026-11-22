# AGV traffic control, part A acceptance

Branch `feature/agv-traffic`, spec `specs/2026-11-21-agv-traffic-design.md` (§17 and Appendix A). Part A replaced node
reservation with claim-based traffic control: the traffic network of segments, zones and corridors, the
`ReservationController` with waits, deadlock detection and the standard policy, the router, the travel executor, idle
behaviour, bounded re-queues, the traffic KPI collector, layout schema 1.1 and the Studio support. Parts B (motion) and
C (routing) are not covered in the sections up to "Whole-part review", so the §17 items "Motion", "Stop requests" beyond
the behaviour already ruled for part A, and "Routing" are out of scope there; part B is recorded in its own section at
the end.

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

## Pinned values that changed in part B (Tasks 1 to 3)

| Value | Change | Cause and check |
|---|---|---|
| `tests/intralogistics/test_builders.py::test_no_layout_builder_digest_remains_compatible` | `7f761d74...` to `b62e716120b67b3b5561d4d298b6fb455cf8447abc2e5386362299563aed5c7f` | through-node motion (Task 3) and the new `heading` field of the AGV state; with every profile treated as one without limits and `heading` left out, the run's event stream and initial state are the previous ones |
| `tests/fixtures/fleet_recovery_regression.json` (`simple-1` to `simple-3`) | re-recorded twice | first for the cancellation fix (a mission whose order was cancelled before it ran ends at once; the fixture passed on the commit before the fix), then for through-node motion (with every profile treated as one without limits the previous values return exactly) |
| `tests/fixtures/fleet_collector_parity.json`, entries `systems.intermediate` and `systems.advanced` | re-recorded | through-node motion; with every profile treated as one without limits the previous values return exactly |

The generated trace fixtures hold no AGV and did not change; the motion fixture of Task 2 is unchanged.

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

After part B, Task 3 (kinematic vehicles move in runs, pass nodes at speed and claim one arc plus the braking distance
ahead; the make-way fallback of §7.9; an order cancelled at the instant of its dispatch stays cancelled), measured the
same way:

| Example | Controller | Completed orders | Waits | Deadlocks by action | Stuck waits | Avg fulfillment, s |
|---|---|---|---:|---|---:|---:|
| simple (horizon 120) | free flow | 4 of 4 | 0 | none | 0 | - |
| simple (horizon 120) | reservation | 4 of 4 | 4 | none | 0 | - |
| intermediate | free flow | 8 of 8 | 0 | none | 0 | 144.6 |
| intermediate | reservation | 8 of 8 | 15 | reroute 1, step aside 2 | 0 | 230.1 |
| advanced | free flow | 63 outbound, 2 replenishment | 0 | none | 0 | 204.4 (outbound) |
| advanced | reservation | 63 outbound, 2 replenishment | 94 | step aside 38 | 1 | 226.0 (outbound) |

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

Part B so far (Tasks 1 to 3):

- A vehicle whose profile reports kinematic limits moves in runs: it passes nodes at speed, so every travel time of a
  `TrapezoidalProfile` vehicle over more than one arc changes, under both controllers, and with it every digest and
  recorded result of such runs. A lone AGV has the same times under `ReservationController` as under free flow, and a
  straight line has the same free-flow time however many nodes split it. An interrupt received while moving takes
  effect where the run comes to rest (the last node held when it arrived); the plan ends at the first node where the
  estimated battery turns critical. A profile without limits keeps the rest-to-rest timings exactly.
- The AGV state has a new field, `heading` (radians, the direction of the last arc traversed, None after placement),
  carried by `agv.move_ended` and `agv.placed`.
- An order cancelled at the instant of its dispatch to an AGV already on its origin stays cancelled: before, the
  mission picked it, re-queued it and the retired order was dispatched again (a `KeyError` in a replay). `main` gets it
  wrong too, worse: the cancelled order is dispatched again and completes.
- The make-way fallback of §7.9 takes a service node or root of capacity above 1, and may arrive over a segment that a
  pending claim names. It ends the stuck wait the simple example otherwise left open on the builder's line.

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

# Part B acceptance (motion)

Branch `feature/agv-traffic` at `d5d22f0` plus the acceptance tests of this section. Part B made AGVs carry their speed
through nodes: the run planner (`kinematics.plan_run`), the `piecewise` motion curve in Python and in Studio, runs in
the travel executor with claim look-ahead, corner speed and turn time, stop requests that take effect at the first node
where the vehicle can be at rest, the energy horizon, and idle power. Routing (part C) is not covered.

## Result

The Appendix A scenarios complete every order with through-node motion, with and without corner caps, turn waits and
idle power; the replay holds in every scenario and in the three examples under both controllers, and the traffic invariants (which
exist under `ReservationController` only) hold in every reservation run; the
digest does not depend on observers or on `PYTHONHASHSEED`; a lone AGV has the free-flow times under reservation. The
scenarios of this task found no defect and no source file changed. The benchmark shows a cost, reported below.

## §17 items of part B and the tests that cover them

Tests added in this task are marked (new). The others are earlier tests of part B, named so that nothing is pinned twice.

| §17 item | Test |
|---|---|
| Appendix A with through-node motion: every order completes | `test_traffic_scenarios.py::test_appendix_a_scenario_1_completes_every_order`, `::test_appendix_a_scenario_2_completes_every_order_and_the_idle_agv_makes_way_to_park` (unchanged since Task 3, see below); (new) `::test_appendix_a_with_corner_caps_turn_waits_and_idle_power_completes_every_order[1, 2]`, the same worlds with `corner_speed`, `turn_time` and `idle_power` |
| Invariants and replay over every scenario and the three examples | `TrafficInvariants` and `FullReplay` in every world of `test_traffic_scenarios.py`, `test_travel_motion.py`, `test_turning.py`, `test_stop_requests.py` and `test_idle_power.py`; `test_traffic_scenarios.py::test_the_examples_hold_the_traffic_invariants_under_a_reservation_controller[intralogistics_simple, intralogistics_intermediate, intralogistics_advanced]` (unchanged; its assertions hold) |
| A straight line split into 1, 2 and 10 arcs has equal free-flow time within 1e-9 relative | (new) `test_traffic_scenarios.py::test_a_straight_line_split_into_arcs_takes_the_single_arc_time_under_both_controllers[1, 2, 10 x free flow, reservation]` (100 m, 68.5416666... s, nodes passed at speed); also `test_travel_motion.py::test_split_line_arrival_times` (1, 3, 30 arcs, free flow), `test_kinematics.py::test_split_line_has_the_same_duration`, and the lone-AGV tests of part A on lines of 1, 2 and 10 arcs, which compare the two controllers |
| Speed limit changes, corner caps and turn waits match hand-computed values | `test_kinematics.py::test_speed_limit_change`, `::test_node_cap_limits_the_pass_speed`, `::test_split_line_has_the_same_duration`; `test_turning.py::test_a_corner_speed_caps_the_speed_through_the_corner`, `::test_a_turn_time_stops_at_the_corner_waits_and_leaves_from_rest`, `::test_the_first_arc_after_placement_turns_for_free_and_reversing_is_a_turn_of_pi`, `::test_a_turn_at_the_start_of_a_travel_from_rest_waits_before_departure`; (new) the corner and reversal values of the Appendix A variant: 1.5 m/s straight through a node, 0.6 m/s through a right-angle corner, at least 3 s at a reversal |
| `piecewise` descriptions integrate to the arc length and the duration, in both languages | Python: `test_motion_piecewise.py` (the shared fixture `tests/fixtures/motion/piecewise.json`, large event times, planned arcs), `test_travel_motion.py::assert_valid_descriptions` over every `agv.move_started` of its runs (`test_the_t_layout_travel_takes_the_single_profile_time_and_passes_its_nodes_at_speed`, `test_descriptions_are_valid_at_a_large_simulation_time`); TypeScript: `studio/packages/scene/test/motion.test.ts`, which reads the same fixture |
| A profile without `limits` reproduces the rest-to-rest hop times | `test_travel_motion.py::test_a_profile_without_limits_keeps_the_rest_to_rest_timings[plain, no_limits x free flow, reservation]`; `test_kinematics.py::test_rest_to_rest_matches_travel_time`; the pinned digest and the recorded fixtures of part A are reproduced exactly when every profile is treated as one without limits (see "Pinned values") |
| Lone AGV: same times as free flow | `test_travel_motion.py::test_lone_agv_matches_free_flow[t_layout, builder_line, thirty_arcs]`, `::test_a_horizon_at_the_braking_distance_keeps_the_free_flow_times_exactly`, `::test_lone_agv_matches_free_flow_over_blocks_at_fractions_of_the_braking_distance`; `test_turning.py::test_a_lone_vehicle_has_the_same_times_under_both_controllers`, `::test_seeded_lone_vehicles_with_turning_match_free_flow_exactly`; `test_stop_requests.py::test_a_lone_agv_has_the_same_arrivals_under_both_controllers_with_a_falling_depletion` |
| A cancelled order stops the AGV at the next feasible node, energy matches the arcs covered, the mission recovers as before | `test_stop_requests.py::test_a_cancelled_mission_recovers_after_a_stop_at_the_first_feasible_node[loaded, empty x both controllers]`, `::test_an_interrupt_on_a_cruising_arc_passes_its_end_at_speed_and_stops_at_the_end_of_the_next`, `::test_an_interrupt_releases_every_claim_ahead_at_the_stop`; (new) `test_motion.py::test_a_cancelled_loaded_order_stops_at_the_first_feasible_node_and_the_energy_is_that_of_the_arcs_covered[both controllers]`: the battery at the stop node is 985 after 3 arcs of 5, the cargo goes back over 3 more arcs, the end level is 970. The recovery paths are also in `test_coordinator.py` (`TestResumeDeliveryRecovery`, `TestResumeDeliveryStranded`, the cancellation regression) |
| A critical battery during a run | `test_stop_requests.py::test_a_level_critical_on_arrival_stops_the_agv_at_the_first_feasible_node`, `::test_a_battery_stop_and_an_interrupt_share_the_first_stop_node`, `::test_a_critical_level_found_at_the_goal_arrives`, `::test_the_energy_horizon_shortens_the_plan_at_a_node_event_under_both_controllers`, `::test_a_charger_on_the_node_where_the_estimate_turns_critical_is_not_passed_at_speed`; `test_travel_motion.py::test_the_plan_ends_where_the_estimated_battery_turns_critical` |
| Idle power | `test_idle_power.py` (19 tests, each world with `FullReplay`), `test_agv.py::TestIdlePower`, `test_battery.py::TestDrain` |
| Replay equals live state with the new kinds | `FullReplay` in every world above (it compares every live entity after every event, from the first event and from the initial state), including `heading` and `battery`; (new) the Appendix A variant |
| Capacity above one and vehicle extent, with runs | `test_travel_motion.py::test_clear_points_follow_the_planned_profiles_and_a_roomy_node_passed_at_speed_keeps_them`, `::test_a_grant_taken_while_moving_keeps_the_trail_entries_it_lifts_held`; `test_stop_requests.py::test_an_agv_stopped_on_a_roomy_node_holds_only_that_node`, `::test_a_later_travel_over_the_old_trail_holds_nothing_ahead_of_it` (the `long_vehicle_line` world, with a vehicle longer than a block); the part A row above still holds |
| Digests identical with and without observers and across `PYTHONHASHSEED` values | `test_invariance.py`, both variants, `reservation` and `free_flow`: the advanced example, whose `TrapezoidalProfile` has `limits`, so its reservation variant runs through-node motion (`test_final_state_is_independent_of_observers`, `test_digest_report_and_shared_kpis_are_identical_across_instrumented_configurations`, `test_recorded_traces_verify`, `test_digest_is_stable_across_hash_seeds_in_fresh_processes`); (new) `test_traffic_scenarios.py::test_the_motion_scenario_is_the_same_with_and_without_observers` (the digest alone against the digest with replay, invariants and a full trace attached; same digest, same final state, a verified trace with that digest; the subscriber-free case is the `none` configuration of `test_invariance.py`) and `::test_the_motion_scenario_digest_is_the_same_across_hash_seeds` (fresh processes with seeds 0, 1 and 123) on the Appendix A variant, which runs corner caps, turn waits and idle power (what it pins of them is under "Choices of the new scenarios"; the unit tests `test_turning.py` and `test_idle_power.py` pin the values). CPython and PyPy agree through the PyPy lane of CI |

Choices of the new scenarios:

- What the variant pins: the corner cap (1.5 m/s straight, 0.6 m/s through a right angle, with the cap removed both
  scenarios fail); the reversal wait in scenario 1 (the `PARK` reversal of `agv-0` waits exactly 3.0 s; with `turn_time`
  forced to 0 scenario 1 fails); idle power in both scenarios (the same world with `idle_power` 0 has the same delivery
  times, and each AGV's level is lower by exactly `idle_power` times the time accounted to it, which is zero when
  idle power is 0). Scenario 2 does not discriminate for the turn wait: its reversals sit on service stops and long
  traffic waits, each longer than 3 s without any turn wait. `test_turning.py` and `test_idle_power.py` pin the wait and
  the drain themselves.
- The Appendix A variant (`MOTION_OPTIONS` in `test_traffic_scenarios.py`) keeps the appendix's graph, warehouses, fleet
  and orders and changes the vehicle type: the same limits (1.5 m/s, 0.8 m/s^2) without the battery slowing the
  vehicle, so that a speed through a node is the hand-computable 1.5 m/s; right-angle corners at 0.6 m/s; a 3 s wait for
  a reversal (a heading change above 2 rad); idle power 0.01. A turn wait for right angles was tried first and
  abandoned: it makes the corner cap unobservable, because every corner then stops.
- A node is "passed at speed" when the AGV starts the next arc at the instant it arrived and its exit speed from the
  previous arc is positive. An AGV that stops at its claim horizon and restarts at the same instant is not one.
- The cancellation test builds its own world because the existing one uses a vehicle type without a depletion
  function, which can only assert that the level fell.

## Appendix A under `ReservationController` with through-node motion

The assertions of part A still describe what happens, so they are kept. With the appendix's profile:

- Scenario 1: `order-0` is delivered at 47.61 (51.52 when every arc ended at rest), `order-1` at 93.61 and `order-2` at
  141.25; every order completes. `agv-1` first waits at `PARK` at 0.0 for the way out through `C1` (blocker `agv-0`).
  The two AGVs meet head-on on the dead-end branch at 56.39; one deadlock, resolved by `agv-0` stepping aside.
  `agv-1` makes way from `C2` to `PARK` at 102.46.
- Scenario 2: `order-0` at 47.61, `order-1` at 93.61, `order-2` at 1573.94; every order completes, no deadlock. `agv-1`
  waits at `PARK` at 9.0 under the dead-end rule while `agv-0` picks (blocker `agv-0`, standing on `PARK`, asking for
  `C1`); `agv-0` makes way `C2 -> PARK` at 56.39 and `agv-1` `C2 -> PARK` at 1535.15, each when the other needs `C2`.
- The variant with corner caps, turn waits and idle power changes the times (scenario 1: `order-0` at 50.42, the
  deadlock at 61.96, `order-2` at 147.13; scenario 2: `order-2` at 1577.70) and keeps the same sequence: every order
  completes, scenario 1 has one step-aside deadlock, and in scenario 2 `agv-1` first waits at `PARK` at 9.0 and both
  AGVs make way to `PARK`.

## The examples under both controllers

Measured at `d5d22f0` as in part A (each coordinator on its own `ReservationController`, vehicle length 0, and as
written). The assertions of `test_traffic_scenarios.py` hold: no unresolved deadlock, no stuck wait open at the end,
equal completed orders for the intermediate and advanced examples. The numbers are those of Task 3; Tasks 4 to 6 changed
none of them, because the examples have no corner speed, no turn time, no idle power and no interrupts, and their
depletion ignores speed.

| Example | Controller | Completed orders | Waits | Deadlocks by action | Stuck waits | Avg fulfillment, s | After part A: waits, fulfillment |
|---|---|---|---:|---|---:|---:|---|
| simple (horizon 120) | free flow | 4 of 4 | 0 | none | 0 | - | 0 |
| simple (horizon 120) | reservation | 4 of 4 | 4 | none | 0 | - | 7, 3 of 4 |
| intermediate | free flow | 8 of 8 | 0 | none | 0 | 144.6 | 0, 151.2 |
| intermediate | reservation | 8 of 8 | 15 | reroute 1, step aside 2 | 0 | 230.1 | 20, 229.5 |
| advanced | free flow | 63 outbound, 2 replenishment | 0 | none | 0 | 204.4 (outbound) | 0, 217.8 |
| advanced | reservation | 63 outbound, 2 replenishment | 94 | step aside 38 | 1 (closed before the end) | 226.0 (outbound) | 126, 239.6 |

## Benchmark

`benchmarks/intralogistics.py`, the advanced example scaled to 20 AGVs, one shift, intervals 30 to 60, one warm-up and
three measured repeats in each of two fresh processes, as for part A. Apple M4 Pro, CPython 3.14.3, the same interpreter
and dependencies for both sides, run one after the other with nothing else running. The branch ran in this checkout at
`d5d22f0`; `main` (`ea1292c`) was exported with `git archive main | tar -x -C /tmp/t7/main/src` and run there after its
own `uv sync --all-extras` (no branch switch, no worktree). Commands, from `benchmarks/` of each tree:

```bash
for mode in none default default_logging; do
  uv run --all-extras python intralogistics.py --mode "$mode" --shifts 1 \
    --warmup 1 --repeat 3 --processes 2 --json "/tmp/t7/out/il-<side>-$mode.json"
done
```

| Mode | `main` median (IQR), s | Branch median (IQR), s | Branch / `main` | Peak memory `main` / branch, MB |
|---|---:|---:|---:|---:|
| none | 0.17746 (0.00275) | 0.24776 (0.00146) | 1.396 | 39.0 / 50.0 |
| default | 0.20360 (0.00251) | 0.26618 (0.00330) | 1.307 | 38.9 / 49.9 |
| default_logging | 0.17877 (0.00109) | 0.24542 (0.00244) | 1.373 | 39.0 / 49.9 |

At the end of part A the same comparison gave 0.970, 0.960 and 0.990 and 41.8 MB: at `d5d22f0` part B cost about 31 to
40 % of run time and 8 MB of peak memory over part A under `FreeFlowController`, the controller the benchmark uses. The
spec leaves this benchmark ungated (§15). As in part A the two sides do not run the same trajectory (the inputs differ
because replenishment orders depend on the trajectory), so `compare.py` would reject the pair; the numbers are the
medians of the JSON files. `main` reproduces the documented hashes (input `3278fbdc...`, trajectory `b2a2ca3d...`, 1,018
orders, 1,011 completed). The branch has 1,018 submitted orders and 1,012 completed, input hash `1a5ad97c...` and
trajectory hash `633394be...`, before and after the whole-part check.

Where the cost lies, from the whole-part check (part A at `822803a` against the branch at `bb81f30`, mode `none`, one
shift, in-process; the two trajectories do the same amount of work, 9,160 against 8,961 arcs). The lookups of
`graph.length` and `arc_between` are not part B's cost: they are called as often at the end of part A, and most of them
come from route search (89,348 of the 107,486 `length` calls). Measured with low-overhead wrappers, the 72 ms difference
is the planning of a run (`_run_plan`, 48 ms, of which `plan_run` 24 ms, the affordable-end scan 5 ms and the interior
node caps 4 ms, against about 12 ms for part A's travel time and description per arc), the freezing of the larger motion
descriptions in `_start_segment` (7 ms more), the new route state in `_follow` (8 ms more), the claim step at every node
event (6 ms), the two `limits` calls of a run (4 ms), the larger trip (3 ms more), the arrival (2 ms more) and the
per-node energy hooks (2 ms). About half of it was bookkeeping that the whole-part check removed without
changing any result (see "Whole-part check" below); the rest is what runs require. Peak memory was one reference cycle:
a finished trip, which keeps its profiles, expected levels and plans, was freed only by a full collection. Route search
remains the largest single cost on both sides, about a quarter of the run.

After the whole-part check, run one after the other in the same way: `main` (`ea1292c`) and the branch before the
performance changes (`8903fdc`), each exported with `git archive` and synced in its own directory, and the branch after
them (`fbf3dd3`) in this checkout:

| Mode | `main` median, s | Before, s | After, s | Before / `main` | After / `main` | Peak memory `main` / before / after, MB |
|---|---:|---:|---:|---:|---:|---|
| none | 0.18071 | 0.25693 | 0.22442 | 1.422 | 1.242 | 39.0 / 49.9 / 40.3 |
| default | 0.19948 | 0.27406 | 0.24366 | 1.374 | 1.221 | 38.9 / 50.0 / 40.2 |
| default_logging | 0.18112 | 0.25617 | 0.22517 | 1.414 | 1.243 | 38.9 / 50.0 / 40.2 |

The traced peak of one run (`tracemalloc`, mode `none`) falls from 9.85 MB to 0.71 MB (`main` 0.65 MB), and the maximum
RSS after one run from 47.6 MB to 37.1 MB (`main` 37.3 MB).

## Verification

| Check | Result |
|---|---|
| `uv run --all-extras pytest` | 4,547 passed, 1 skipped, 3 warnings, 352 s; branch coverage 99.15 % (gate 99 %; 4,535 passed and 99.15 % before this task) |
| `uv run --all-extras ruff check src tests` | all checks passed |
| `uv run --all-extras ruff format --check` | 520 files already formatted |
| `uv run --all-extras ty check src` | all checks passed |
| `pnpm typecheck && pnpm test` (from `studio/`) | typecheck passed; tests passed: trace 319, playback 24, scene 23, export 15, viewer 82 |

## Pinned values that changed in part B

| Value | Change | Cause and check |
|---|---|---|
| `tests/intralogistics/test_builders.py::test_no_layout_builder_digest_remains_compatible` | `7f761d74...` to `b62e716120b67b3b5561d4d298b6fb455cf8447abc2e5386362299563aed5c7f`, at Task 3 | through-node motion and the new `heading` field of the AGV state; with every profile treated as one without limits and `heading` left out, the event stream and the initial state are those of the end of part A. Unchanged by Tasks 4 to 6 and by this task (checked: the test passes, and `git log` shows its last change in `58ef553`) |
| `tests/fixtures/fleet_recovery_regression.json` (`simple-1` to `simple-3`) | re-recorded three times | the cancellation fix (the fixture passed on the commit before it); through-node motion (the previous values return exactly with every profile treated as one without limits); the stop rule of Task 5 (breakdowns and cancellations rest at the first node the vehicle can stop at; with the old stop logic and everything else kept, the old fixture passed) |
| `tests/fixtures/fleet_collector_parity.json`, entries `systems.intermediate` and `systems.advanced` | re-recorded at Task 3 | through-node motion; the previous values return exactly with every profile treated as one without limits |
| Generated trace fixtures (`tests/fixtures/traces`), the frozen fixtures and the motion fixture of Task 2 | unchanged | `tests/core/test_trace_fixtures.py` reports them current in this task; they hold no AGV |

New fields and kinds in part B: the AGV state field `heading` (Task 3), the `piecewise` motion curve (Task 2),
`AGVType.corner_speed`, `turn_time` and `idle_power`, and `FleetCoordinator(lookahead_margin=)`. The trace container is
unchanged.

## Changes in results

- A vehicle whose profile reports kinematic limits (`TrapezoidalProfile`) passes nodes at speed, so every travel time of
  such a vehicle over more than one arc is shorter than at the end of part A, under both controllers, and every digest
  and recorded result of such runs changes. The appendix's first delivery moves from 51.52 to 47.61. A lone AGV has the
  free-flow times under reservation, and a straight line has the same time however many nodes split it. A profile without
  `limits` keeps its rest-to-rest timings exactly.
- An AGV under `ReservationController` holds, ahead of itself, the next arc and its braking distance, and so more
  claims than the one arc of part A; the examples under reservation show fewer waits than at the end of part A (simple
  7 to 4, intermediate 20 to 15, advanced 126 to 94).
- A corner speed caps the speed through a node and a positive turn time stops the AGV there and adds the wait; the
  energy check at rest comes before the turn wait, which comes before the traffic wait (spec §9.4).
- An interrupt received while moving takes effect at the first node where the vehicle can be at rest (the braking
  distance beyond the arc being traversed), not at the end of the arc, and the energy of the arcs covered is charged. A
  critical battery found on arrival at a node other than the goal and the energy horizon are stop requests of the same
  kind; one found at the goal returns `ARRIVED`.
- The plan ends at the last affordable node and at the first node where the estimated battery turns critical, so a
  vehicle does not pass a charger at speed with a battery it knows will be critical. The estimate uses the arc cap and
  excludes idle power.
- Idle power drains lazily at the events that read or change the battery, with no timer. Collectors see the level as
  last accounted, not the level now (Task 6); `FleetCoordinator.agv_report()` shows the level now, which
  `AGV.battery_level_now()` reads without accounting it (whole-part check).
- The `heading` field is part of the AGV state, carried by `agv.move_ended` and `agv.placed`.
- An order cancelled at the instant of its dispatch to an AGV already on its origin stays cancelled. `main` gets it
  wrong too: there the cancelled order is dispatched again and completes.
- The make-way fallback of §7.9 takes a service node or root of capacity above 1 and may arrive over a segment a pending
  claim names; it ends the stuck wait the simple example left open on the builder's line.
- At an exact float tie in the energy check (a battery sized exactly for k arcs whose lengths are not exact in binary),
  the plan records the level it expects at each node, computed arc by arc as the depletion computes it, so the
  affordable end and the level agree. The two controllers now end alike in all 1,500 tie shapes of the review (34
  disagreed before). Against the commit before that change, 144 of 3,000 reviewer runs differ, all exact ties, and 87 of
  them that ended `RETRY_FROM_CURRENT_POSITION` now end `BATTERY_STRANDED`, because the level after the k-th arc is one
  unit in the last place below the next arc's cost, which the check at rest has always refused. A relative slack in the
  check at rest is a possible follow-up. Results with binary-exact arc lengths are unchanged.
- At a node passed at speed, a level that is critical, or below the estimate of the next arc, is a stop request
  whatever level the plan expected there (spec §8.3; whole-part check). With positive idle power the plan's expected
  levels include the idle drain, so before this a vehicle under free flow could pass nodes at speed while critical and
  enter arcs with an empty battery, and the two controllers came to rest at different nodes; now they agree. With the
  default `idle_power` of 0 no result changes: of 1,300 random runs before and after the change, the 3 that differ all
  have positive idle power.
- On a travel to a charger (the diversion of the low or critical battery branch and the charge after a mission, which
  now have the purpose `charge`) a critical level is not a stop request, neither at a node nor as the end of the plan:
  the vehicle goes on while the energy horizon allows and strands only before an arc it cannot afford (spec §8.3;
  whole-part check). Before, and at the end of part A, a critical vehicle whose charger was more than one arc away
  stopped at the first node of each travel to it, alternated between diversion and mission and stranded without
  charging. A custom router now sees the purpose `charge` for such travels, where it saw `travel`.
- The travel a mission resumes after a completed charge (a charging diversion inside the previous travel, or the
  charge the mission ran after its retry) does not treat a critical level as a stop request either, neither at a node
  nor as the end of the plan; it has the purpose `after_charge`, and only that travel: the next leg of the mission
  checks the critical level as usual (spec §8.3; whole-part check). Where the charger is farther from the goal than the
  battery holds above the critical threshold, a vehicle now charges once and goes on to its goal, or strands before an
  arc it cannot afford, instead of going back and forth between the charger and the node where it turns critical for
  ever (`env.run()` without `until` did not return). In the 1,800 random runs of the check one mission run changes: a
  resumed travel strands where the old one stopped critical, tried a charge it could not afford and stranded at the
  same node with the same order outcome.
- `Arc.speed_limit`, when given, must be positive and finite (`ValueError` at construction). The limits a run samples
  raise `ValueError`, naming the profile and the values, when a value is not finite, when `decel` is negative, or when
  `decel` is 0 for a vehicle that can move; a `v_max` or `accel` that is not positive still stalls the run (§9.1).
  `plan_run` rejects NaN in the entry speed, lengths and caps. Before, a NaN speed limit was ignored, a speed limit of 0
  or below failed mid-run with an unrelated message, and a NaN deceleration made every move take zero time.
- Free-flow run time rises by 22 to 24 % over `main` on the benchmark after the whole-part check (37 to 42 % before it,
  see above).

## Whole-part check

Part B was checked as a whole after its seven tasks: the plan and holdings invariants held over 8,800 random runs, and
profiles without limits reproduce part A apart from three accounted changes. This wave changed:

- Behaviour: the O(1) critical and next-arc check at a node passed at speed, a critical vehicle that keeps going to its
  charger and, after a completed charge, to its goal, and the validation of speed limits and sampled limits (see
  "Changes in results").
- Run time and memory, with event streams bit-identical (the full event stream of 1,800 random runs, kinematic with
  turning, idle power, speed-dependent depletion and interrupts, without limits, and missions with chargers, cancels and
  breakdowns, under both controllers; the three examples under both controllers; the pinned digest and fixtures): the
  piecewise description built as a canonical map without `freeze`'s recursion; the route state of `_follow` read from
  the arc already looked up and heading changes only for a type that turns; plain loops and a direct cruise phase in the
  planner; a slotted arc plan; arc caps and energy estimates cached per route and run for the plan, the claim
  look-ahead, the claim gate and the node-event check; the limits of the check at rest reused by the run when no turn
  wait came between them; the load weight read once per run; a scan at a node event that finds the plan affordable
  kept as a floor, so that a level not below it is not scanned again (a depletion the estimate under-predicts only on
  the arcs from and to rest no longer scans at every node: 2,000 arcs under free flow, 0.563 s to 0.015 s); the claim
  step skipped when nothing can be claimed; and the reference cycle between a finished trip and its hooks broken. The
  reuses assume pure `depletion_fn` and `limits` functions, as their docstrings say.
- Small items: `AGV.battery_level_now()` and its use in `agv_report`; `motion_fraction` returns None for a number
  beyond the float range; docstrings of `_turn` (the turn wait's idle drain is not in the energy check before it) and
  of `disposition_of` (`SERVING` inside an `agv.move_ended` handler at a node passed at speed); the outcome of one stop
  test pinned; `test_replenishment_triggered` asserts the drain and the replenishment orders.
