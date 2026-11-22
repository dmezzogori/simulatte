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
| Stuck wait | `test_traffic_termination.py::test_a_loaded_agv_stuck_behind_an_idle_one_that_cannot_move_leaves_one_stuck_wait_and_the_run_returns`; behind waiting blockers `test_a_standstill_behind_waiting_blockers_on_a_roomy_port_is_reported_stuck`, at unit level `test_deadlock.py::TestReviews` |
| Default builder | `test_the_default_builder_completes_twenty_transfer_orders_with_no_deadlock_and_no_stuck_wait`; `test_the_default_builder_with_orders_in_both_directions`; `tests/layout/test_builders.py::test_layout_builder_with_a_reservation_controller` |
| Two fleets on one controller | `test_two_fleets_share_a_controller`; `test_traffic.py::TestFreeFlowController::test_two_fleets_on_one_graph_share_one_controller` for free flow |
| Lone AGV | `test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_the_appendix_layout[0.0, 1.0]`, `..._on_the_builder_line`, `..._on_a_line_split_into_arcs[1, 2, 10]` |
| Giving up | `test_traffic_termination.py::test_an_agv_with_cargo_does_not_give_up_a_charging_diversion`, `::test_an_order_is_requeued_for_traffic_at_most_max_traffic_requeues_times` |
| Resolution by reroute and by step aside | `test_a_swap_with_a_second_route_is_resolved_by_reroute`; `test_a_swap_without_another_route_is_resolved_by_step_aside`; at unit level `test_deadlock.py::TestResolution` |
| Fairness | `test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside[port-1, port-2, port-3]` (service-node clearing off); the known lapse with clearing on, `test_known_lapse_a_vehicle_outside_the_corridor_on_the_waiters_port_lets_the_stream_pass_first`; the exit record on a port of capacity 2, `test_reservation.py::TestRelease::test_the_exit_record_on_a_port_of_capacity_two`; at unit level `test_reservation.py::TestGate` |
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
  enter arcs with an empty battery. On the reviewed input (a line of 40 arcs, battery 420 or 250, idle power 0.5) the
  two controllers now come to rest at the same node; in general they can still differ when idle power drains the
  battery during a run, because reservation re-evaluates the energy horizon at every node event and free flow only on
  an unforeseen deficit. With the default `idle_power` of 0 no result changes: in the random runs of the check every
  difference has positive idle power.
- `Arc.speed_limit`, when given, must be positive and finite (`ValueError` at construction). The limits a run samples
  raise `ValueError`, naming the profile and the values, when a value is not finite, when `decel` is negative, or when
  `decel` is 0 while `v_max` and `accel` are positive; a `v_max` or `accel` that is 0 or negative still stalls the run
  (§9.1).
  `plan_run` rejects NaN in the entry speed, lengths and caps. Before, a NaN speed limit was ignored, a speed limit of 0
  or below failed mid-run with an unrelated message, and a NaN deceleration made every move take zero time.
- Free-flow run time rises by 22 to 24 % over `main` on the benchmark after the whole-part check (37 to 42 % before it,
  see above).

## Whole-part check

Part B was checked as a whole after its seven tasks: the plan and holdings invariants held over 8,800 random runs, and
profiles without limits reproduce part A apart from three accounted changes. This wave changed:

- Behaviour: the O(1) critical and next-arc check at a node passed at speed, and the validation of speed limits and
  sampled limits (see "Changes in results"). Charging and stranding behave as before this work (spec §8.4).
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
  reuses assume pure `depletion_fn` and `limits` functions: the `KinematicProfile` protocol and `AGVType.depletion_fn`
  say so, and so do the executor's `_Trip._arc_cost` and `_Trip._start_run` where the results are reused.
- Small items: `AGV.battery_level_now()` and its use in `agv_report`; `motion_fraction` returns None for a number
  beyond the float range; docstrings of `_turn` (the turn wait's idle drain is not in the energy check before it) and
  of `disposition_of` (`SERVING` inside an `agv.move_ended` handler at a node passed at speed); the outcome of one stop
  test pinned; `test_replenishment_triggered` asserts the drain and the replenishment orders.

## Known limits

Two charging defects predate this work and are unchanged by it:

- A vehicle whose battery is critical and whose charger is more than one arc away stops at the first node of each
  travel towards the charger, because the level there is critical. The mission then alternates between the charging
  diversion and its own travel, and the vehicle strands without charging.
- When an arc of a mission cannot be afforded with the level a full charge leaves on arrival at the node before it,
  the vehicle diverts to the charger, charges, comes back to that node and diverts again, without end; `env.run()`
  without `until` does not return.

# Part C (routing), notes recorded per task

The release gate (Task 5) records part C as a whole; these notes are kept by the tasks that change results.

## Changes in results (Task 3, route cost for dispatch, bays, chargers and parking)

- `NearestIdleStrategy`, the bays of a mission (`FleetCoordinator.best_bay`), the nearest and the reachable charger and
  `NearestParkingPolicy` rank by the fleet's router's cost (spec §10.4); ties keep the rules of part B (the first of the
  sequence, the AGV id for dispatch). With `ShortestDistanceRouter` the cost is the path length, the sum of the arc
  lengths, where part B ranked by the sum of the chords of the shortest path: on a graph where no arc has an explicit
  `length` they are the same floats and every decision is unchanged. The full event streams of the four intralogistics
  examples (under both controllers where they run), the four Appendix A runs (scenarios 1 and 2, with and without the
  part B motion options) and 60 random worlds (grids with one-way and speed-limited arcs, several bays per warehouse,
  chargers, parking areas, `NearestParkingPolicy`, idle power, both controllers, profiles with and without limits) are
  identical before and after the change, and `test_with_the_default_router_dispatch_bays_chargers_and_parking_decide_as_path_length_did`
  compares the examples and Appendix A against the part B rankings.
- On a graph with an arc longer than its chord (an explicit `Arc.length`) the rankings now see the length the AGV
  travels: a bay, charger or parking area behind such an arc can lose to one that is farther by chord but nearer along
  the arcs (`test_an_arc_longer_than_its_chord_changes_the_charger_and_the_bay`).
  `Warehouse.nearest_input_bay` and `nearest_output_bay` keep the chord ranking and are no longer used by the coordinator.
- The reachable-charger estimate is computed on the route the router gives for the travel to the charger, the route the
  executor travels, from its arc lengths, with the battery level now (`AGV.battery_level_now()`). Before, it used the
  chords of the shortest path and the level as last accounted, so with a custom router that routes the diversion over
  a detour, or with idle power not yet accounted, a charger could count as reachable that the travel could not reach.
- `DispatchStrategy.select(order, fleet, graph)` is now `select(order, fleet, context)` with a `DispatchContext`
  (`graph`, `router`, `view`, `cost`); `RepositioningContext` gains `router`, `view` and `cost` and is frozen.

Behaviour of the routers that the tasks of part C settled and the release should state:

- `CongestionAwareRouter` with `wait_weight=0` is its base router exactly: no penalty and no exclusion of legs held by
  stranded AGVs (a leg behind stranded AGVs is excluded at every positive weight). `view.pending` counts the requesting
  AGV's own queued claim during a reroute. Under `CongestionAwareRouter` the detour bound of a reroute (`max_cost_ratio`)
  compares costs that include penalties, so it no longer bounds the detour in time.
- `FastestRouter` costs omit the start and end ramps of a travel (a constant, the same for every route) and, with a
  turning model, give the least-cost walk cut to a simple path, which is not guaranteed to be the least-cost simple path.
  It samples the limits at the AGV's battery level now (idle power accrued but not accounted).
- A ranking asks the router with the purpose and load of the travel it ranks for; the cost of a route is comparable
  only within one router, and no code compares costs of two routers.
- One dispatch decision on a 900-node grid costs about 40 ms with `ShortestDistanceRouter`, 95 ms with `FastestRouter`,
  330 ms with `FastestRouter` and a turning model and 600 ms with `CongestionAwareRouter` (each ranking is one router
  call per candidate).

## §17 items of part C and the tests that cover them

Tests are in `tests/intralogistics`. The one test marked (added) was added by the release gate; the rest existed.

| §17 item | Test |
|---|---|
| `FastestRouter` prefers a longer, faster route | `test_routing.py::TestFastestRouter::test_the_fastest_route_is_not_the_shortest`, `::test_a_longer_straight_route_wins_below_the_turn_time`, `::test_a_turn_adds_its_time`; on a real AGV `test_routing.py::test_a_travel_follows_the_fastest_route`; in a ranking `test_policies.py::TestNearestIdleStrategy::test_the_fastest_router_picks_another_agv_than_the_default_one_past_a_slow_arm`; limits at the battery level now `test_routing.py::test_the_fastest_router_samples_the_limits_at_the_battery_level_now[fastest, congestion]` |
| `CongestionAwareRouter` avoids a queue when the detour is cheaper | `test_routing.py::TestCongestionAwareRouter::test_a_penalty_makes_the_search_take_the_slower_arm`, `::test_the_arm_with_a_serving_agv_is_taken_only_when_the_other_costs_more_than_the_wait` (a real `ReservationController` view: `P` held for 40 s and held now, the other arm 6.7 s or 53.3 s longer), `::test_a_leg_held_to_capacity_by_stranded_agvs_is_excluded`, `::test_a_node_filled_by_a_stranded_agv_is_avoided_and_may_be_the_only_way`, `::test_the_largest_penalty_of_the_resources_of_the_leg_applies`, `::test_the_penalty_of_a_leg_falls_with_the_cost_at_which_it_is_reached`; with nothing to avoid, `::test_congestion_router_equals_base_without_traffic` and `::test_no_penalty_without_an_agv_or_with_a_zero_weight` |
| With the default router, dispatch and bay choices equal the baseline on the examples | `test_traffic_scenarios.py::test_with_the_default_router_dispatch_bays_chargers_and_parking_decide_as_path_length_did[intralogistics_simple, intralogistics_intermediate, intralogistics_advanced, building_an_agv_system, appendix_a_1, appendix_a_2]` (assigned orders, AGVs, times and routes equal those of a run with the rankings of the end of part B); the explicit-length exception `test_charging.py::test_an_arc_longer_than_its_chord_changes_the_charger_and_the_bay`; `test_coordinator.py::TestRouteCostRankings::test_best_bay_ranks_by_router_cost`, `::test_chargers_rank_by_router_cost`; `test_policies.py::TestContexts`, `::TestNearestIdleStrategy::test_picks_the_lowest_router_cost_to_the_best_output_bay`, `::TestNearestParkingPolicy::test_ranks_areas_by_router_cost`; `test_parking.py::test_nearest_parking_ranks_areas_by_the_fleets_router`; `test_charging.py::test_the_reachable_charger_estimate_sums_the_route_arc_lengths`; `test_stop_requests.py::test_the_charger_estimate_is_made_on_the_routers_route[both controllers]` |
| `mean_hold` is kept whether or not anything observes it, and changes no result | `test_reservation.py::TestMeanHold` (the exponential average, an unobserved resource, zones and corridors as kinds of their own, a claim in a new direction, a node without an entity, `::test_values_do_not_depend_on_what_observes_the_run`); `test_traffic.py` (the free-flow view reports an empty network, `mean_hold` 0.0); `test_routing.py::test_reading_the_traffic_view_changes_no_result[shortest, fastest]` |
| Replay equals live state, traffic invariants, digests independent of observers and of `PYTHONHASHSEED`, with the new routers | every world of `test_routing.py` built by `world()` runs `FullReplay` and, under reservation, `TrafficInvariants`; (added) `test_routing.py::test_a_run_with_a_routing_router_replays_and_keeps_the_invariants[fastest, congestion]` (three AGVs on the two-arm world under each router, replay and invariants attached, digest equal to the run without them); `::test_a_run_with_the_congestion_router_has_the_same_digest_across_hash_seeds`, `::test_the_fastest_router_does_not_depend_on_the_hash_seed`; CPython and PyPy agree through the PyPy lane (see "Gates") |
| Routes are valid and a router that answers nothing fails cleanly | `test_routing.py::test_executor_rejects_invalid_routes`, `::test_avoid_can_make_destination_unreachable`; `test_coordinator.py::test_a_router_without_route_fails_the_order_cleanly`; `test_travel.py::test_no_route`, `::test_no_route_fails_the_order_after_returning_the_cargo` |

## §17 walk of the whole release

Every item of §17, in the order of the spec. "Existing" means the test existed before the release gate; the sections
above name the tests in full.

| §17 item | Covering tests | Added or existing |
|---|---|---|
| Invariants over every scenario and the three examples | `TrafficInvariants` and `FullReplay` in `traffic_support.py`, attached in every world of `test_traffic_scenarios.py`, `test_travel.py`, `test_idle_traffic.py`, `test_travel_motion.py`, `test_turning.py`, `test_stop_requests.py`, `test_idle_power.py`; `test_traffic_scenarios.py::test_the_examples_hold_the_traffic_invariants_under_a_reservation_controller[intralogistics_simple, intralogistics_intermediate, intralogistics_advanced]` | existing |
| Baseline scenarios (Appendix A) | `test_traffic_scenarios.py::test_appendix_a_scenario_1_completes_every_order`, `::test_appendix_a_scenario_2_completes_every_order_and_the_idle_agv_makes_way_to_park`, and with corner caps, turn waits and idle power `::test_appendix_a_with_corner_caps_turn_waits_and_idle_power_completes_every_order[1, 2]` | existing |
| Following | `test_traffic_scenarios.py::test_following_on_a_subdivided_one_way_line` | existing |
| Head-on | `test_traffic_scenarios.py::test_head_on_in_a_corridor_the_second_agv_waits_before_its_port`; `test_traffic_termination.py::test_two_agvs_swapping_on_a_line_leave_one_unresolved_deadlock_and_the_run_returns` | existing |
| Stuck wait | `test_traffic_termination.py::test_a_loaded_agv_stuck_behind_an_idle_one_that_cannot_move_leaves_one_stuck_wait_and_the_run_returns` | existing |
| Default builder | `test_traffic_scenarios.py::test_the_default_builder_completes_twenty_transfer_orders_with_no_deadlock_and_no_stuck_wait` | existing |
| Lone AGV | `test_traffic_scenarios.py::test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_a_line_split_into_arcs[1, 2, 10]` and `..._on_the_appendix_layout`; `test_travel_motion.py::test_lone_agv_matches_free_flow[t_layout, builder_line, thirty_arcs]` | existing |
| Giving up | `test_traffic_termination.py::test_an_agv_with_cargo_does_not_give_up_a_charging_diversion`, `::test_an_order_is_requeued_for_traffic_at_most_max_traffic_requeues_times` | existing |
| Resolution | `test_traffic_scenarios.py::test_a_swap_with_a_second_route_is_resolved_by_reroute`, `::test_a_swap_without_another_route_is_resolved_by_step_aside` | existing |
| Fairness; gate waiver | `test_traffic_scenarios.py::test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside[port-1, port-2, port-3]`, `::test_a_gate_that_would_complete_a_cycle_is_waived` | existing |
| Idle and parking | `test_idle_traffic.py::test_an_idle_blocker_makes_way_and_goes_idle_again`, `::test_an_agv_leaves_the_delivery_bay_under_reservation`, `::test_an_area_of_two_never_lists_three_agvs`, `::test_a_full_area_is_not_a_target` | existing |
| Capacity above one, extent longer than a block, `passing` segments, authored zones with and without `hold_back` | `test_travel.py::test_extent_longer_than_arc_holds_two_segments`, `::test_two_vehicles_at_a_multi_slot_leaf_one_sent_away`; `test_reservation.py::TestAdmission::test_passing_admits_both_directions`, `::TestAdmission::test_zone_capacity`, `::TestRelease::test_hold_back_zone_is_claimed_a_leg_early` | existing |
| Vehicle length | `test_reservation.py::TestLifecycle::test_a_vehicle_type_without_length_cannot_register`; `test_travel.py::test_coordinator_registers_with_a_reservation_controller` | existing |
| Motion | `test_traffic_scenarios.py::test_a_straight_line_split_into_arcs_takes_the_single_arc_time_under_both_controllers[1, 2, 10]`; `test_kinematics.py::test_speed_limit_change`, `::test_node_cap_limits_the_pass_speed`; `test_turning.py::test_a_corner_speed_caps_the_speed_through_the_corner`, `::test_a_turn_time_stops_at_the_corner_waits_and_leaves_from_rest`; `test_motion_piecewise.py` and `studio/packages/scene/test/motion.test.ts` (shared fixture `tests/fixtures/motion/piecewise.json`); `test_travel_motion.py::test_a_profile_without_limits_keeps_the_rest_to_rest_timings[plain, no_limits x free flow, reservation]` | existing |
| Stop requests | `test_stop_requests.py::test_a_cancelled_mission_recovers_after_a_stop_at_the_first_feasible_node[loaded, empty x both controllers]`, `::test_a_level_critical_on_arrival_stops_the_agv_at_the_first_feasible_node`; `test_motion.py::test_a_cancelled_loaded_order_stops_at_the_first_feasible_node_and_the_energy_is_that_of_the_arcs_covered[both controllers]` | existing |
| Routing | the table "§17 items of part C" above | existing, with one test added |
| Replay and purity | `FullReplay` in every world; `test_invariance.py::test_final_state_is_independent_of_observers`, `::test_digest_report_and_shared_kpis_are_identical_across_instrumented_configurations`, `::test_digest_is_stable_across_hash_seeds_in_fresh_processes`; `test_traffic_scenarios.py::test_the_motion_scenario_is_the_same_with_and_without_observers`, `::test_the_motion_scenario_digest_is_the_same_across_hash_seeds`; `test_routing.py` (the part C table); CPython and PyPy agree through the PyPy lane | existing, with one test added |
| Layout | `tests/layout/test_traffic_fields.py::test_1_0_file_loads_unchanged_and_keeps_its_hashes`, `::test_1_1_file_round_trips_through_save_and_load`, `::test_passing_changes_the_physical_hash`, `::test_presentation_only_edits_keep_the_physical_hash` (its last assertion: `block_length` changes the physical hash), `::test_zones_enter_the_physical_hash_and_the_bundle`, `::test_an_explicit_graph_and_its_layout_twin_produce_the_same_event_stream`; twins on a 3.3 m pitch, where float noise used to make them differ, `::test_subdivide_and_block_length_build_the_same_network_on_a_3_3_pitch[3.3-1, 1.1-3]` and `::test_a_mission_runs_the_same_on_the_subdivided_graph_and_on_its_layout_twin` | existing; the twins on a 3.3 m pitch added by the final review wave |
| Free flow | `test_traffic_scenarios.py::test_free_flow_creates_no_segment_or_zone_entity_and_emits_no_traffic_event`; `test_travel.py::test_free_flow_arrivals_and_events` | existing |
| Pinned digest and golden traces regenerated in parts A and B | `test_builders.py::test_no_layout_builder_digest_remains_compatible` (`b62e7161...`, unchanged by part C); `tests/core/test_trace_fixtures.py::test_generated_fixtures_canonically_up_to_date`; the causes are under "Pinned values" in the sections of parts A and B | existing |

Part C re-pinned nothing: the pinned digest, `fleet_recovery_regression.json` and `fleet_collector_parity.json` are as at
the end of part B, and the part C test suite reproduces the end of part B's rankings with the default router (first row
of the part C table).

## Gates of the release (this task)

Run at the head of the branch plus the one added test, on bigboss (Apple M4 Pro, CPython 3.14.3), with each result line
as printed.

| Gate | Command | Result |
|---|---|---|
| Tests and coverage | `uv run --all-extras pytest` | 4,715 passed, 1 skipped, 3 warnings, 355.33 s; "Required test coverage of 99% reached. Total coverage: 99.17%" |
| Lint | `uv run --all-extras ruff check src tests` | All checks passed! |
| Format | `uv run --all-extras ruff format --check` | 522 files already formatted |
| Types | `uv run --all-extras ty check src` | All checks passed! |
| Docs | `uv run --all-extras zensical build` | "No issues found", build finished |
| Studio types | `pnpm typecheck` (in `studio/`) | exit 0 |
| Studio tests | `pnpm test` | exit 0; trace 319, playback 24, scene 23, export 15, viewer 82 passed (apps/player has no tests) |
| Studio build | `pnpm build` | exit 0 (a chunk-size warning of the bundler only) |
| Studio browsers | `pnpm test:browser` | 36 passed (1.2 m) on chromium, firefox and webkit; the three engines are installed on this machine |
| PyPy lane | `UV_PROJECT_ENVIRONMENT=/tmp/simulatte-pypy-venv uv sync --dev --python pypy-3.11 --frozen`, then `MPLBACKEND=Agg uv run --python pypy-3.11 pytest tests/core tests/execution tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"` (job `test-pypy` of `ci.yml`; a separate project environment keeps the checkout's `.venv` on CPython) | 4584 passed, 23 skipped in 500.90 s (PyPy 3.11.15), run twice with the same result |
| Overhead gate of the core workload | the CI `bench` step "Mode none against released and stripped 0.12.0 (gate)" on CPython 3.14, `--warmup 1 --repeat 5 --processes 3`, in environments built as the workflow builds them | pass on both workloads against both baselines: against released 0.12.0 -25.7 % and -24.1 % (limit +5.0 %), against 0.12.0 without `env.debug` calls +6.7 % and +6.7 % (limit +8.5 %); numbers in `benchmarks/intralogistics-baseline.md`. The PyPy variant of the job runs only on the CI runner |

The first pytest run of the day (4,713 passed, coverage 99.17 %) preceded the added test; every other gate was run
after it or does not read it. `ci.yml` needed no change: the PyPy lane and the Linux matrix name directories, not files,
and the `execution-native` lane lists files of the execution and Studio tests that no work of this branch renamed.

## Benchmark of the release

Branch against `main`, `benchmarks/intralogistics.py`, five alternating rounds per mode, `--warmup 1 --repeat 5
--processes 3`; the full table, the spread, the reason, and the comparison with the commit before part C are in
`benchmarks/intralogistics-baseline.md`. Branch / `main` at `9f0406f`: none 1.274 (1.267 to 1.282 over the rounds),
default 1.247 (1.240 to 1.258), default_logging 1.273 (1.252 to 1.298); peak memory 38.9 to 39.0 MB on `main`, 40.3 to
40.4 MB on the branch. Against the commit before part C: 1.027, 1.019 and 1.024. After the fix wave of the final review
(at `5939714`, the node hash): 1.197 (1.173 to 1.208), 1.186 (1.183 to 1.202) and 1.196 (1.194 to 1.207), peak memory
40.4 to 40.5 MB, the same trajectory. The free-flow ratio against `main` is therefore about 1.19 to 1.20 on this
workload, as the spec foresaw (§15, ungated); it was 0.96 to 0.99 at the end of part A and 1.22 to 1.24 after the
whole-part check of part B.

## Known limits

From spec §18 and the ledgers of the three parts, as the release leaves them.

- **Passing places defined by capacity.** The hold-back rules treat a port or a root of capacity above 1 like any other:
  a vehicle that cannot enter the corridor or branch behind it waits one node before it, so two vehicles that meet
  head-on at such a node deadlock and one steps aside, where they could have passed each other on the node. A sound
  rule (admit the waiting vehicle only while a slot remains for each oncoming one) is not part of this release. A
  `passing` segment or a siding is a passing place that works.
- **Make-way is not transitive.** An idle blocker whose only free neighbours are held by other idle AGVs stays, and the
  step-aside search excludes nodes ahead on the routes of the waits concerned even where a retreat exists. On small
  layouts without parking this produces stuck waits (`traffic.wait_stuck`, reported once, no timer left) and unresolved
  deadlocks (`traffic.deadlock` with `unresolved`); both are reported, not hidden, and `env.run()` returns. The
  advanced example under reservation ends with no stuck wait open; the simple example completes 4 of 4 orders.
- **The fairness gate's lapse.** With `clear_service_nodes` on, a vehicle outside the corridor but on the waiter's port
  lets the stream pass first (`test_known_lapse_a_vehicle_outside_the_corridor_on_the_waiters_port_lets_the_stream_pass_first`);
  the fairness bound holds with clearing off.
- **Two charging defects predate this work and are unchanged by it** (see "Known limits" of part B): a vehicle with a
  critical battery and a charger more than one arc away stops at the first node of each travel towards the charger and
  strands without charging; and an arc that cannot be afforded with the level a full charge leaves on arrival at the node
  before it makes the vehicle charge, return and divert again without end, so `env.run()` without `until` does not
  return there.
- **`CongestionAwareRouter` is a heuristic.** It can choose worse routes than `FastestRouter` in some states (in the
  task review's runs on the same seeds it completed more orders and had fewer deadlocks than `FastestRouter`, but no
  optimum is claimed), and it is opt-in. The detour bound of a reroute under it compares penalised costs.
- **Look-ahead holds resources.** A fast AGV reserves its braking distance, which lowers density; `lookahead_margin`
  tunes it.
- **Free-flow cost.** About 19 to 20 % more run time than `main` on the scaled advanced example after the node hash of
  the final review wave (25 to 27 % before it), from planning runs of arcs (part B) and ranking by router cost (part C).
- **Tie at the energy check.** At an exact float tie in the energy check some missions end `BATTERY_STRANDED` where they
  used to retry (part B, "Changes in results"); a relative slack at the check at rest is a possible follow-up.
- **Late grants** (spec §9.3, T15, T22). A claim granted late, after the AGV entered an arc whose profile already brakes
  for the end of its holdings, does not change that arc's profile: the AGV finishes the arc as planned, at worst
  stopping at the node, and plans again there. The cost is at most one deceleration and acceleration per such node,
  and it shrinks with the block length.
- **The standard search on large layouts.** It asks the router once per candidate node: about 0.6 s per search on a
  30 by 30 grid with the default router (0.85 s before the node hash of the final review wave). Under
  `ReservationController` it runs when an AGV clears a service node, makes way or steps aside;
  `clear_service_nodes=False` removes the first. A single-source search is a follow-up.

# Final whole-branch review: the fix wave

Two reviewers reviewed the branch at `cc252ed`; five items changed code, one documentation. The measurements use the
control reviewer's random mission worlds (`world.py`, `env.run()` without `until`, an independent monitor of every
event, 90 s of wall time per run): seeds 0 to 1,499 under five routers (1,452 runs each, 48 worlds cannot place
their AGVs), seeds 0 to 499 with two fleets on one controller under three routers (472 runs each), and seeds 0 to
204 on CPython 3.11 and PyPy (198 runs each), 9,270 runs in all.

1. **Endless make-way loop among idle AGVs.** On world 206 every order completed by 588 s, then three idle AGVs made
   way around a ring for ever (4,650 make-way moves in 20,000 s; `env.run()` did not return). The idle search counted
   a node as free from its own capacity: `c02`, of capacity 2, lay in the zone `Z0` of capacity 1 with hold-back, held
   by the AGV resting there, so the mover stopped at the hold-back leg and the resting AGV was asked to make way, and
   so on. Fix: the search for an AGV that will be idle on arrival skips a node whose last leg needs a zone or corridor
   that the holders make inadmissible for the AGV now (spec §7.9); nothing else in the idle rules changed. Tests:
   `test_idle_traffic.py::test_the_idle_search_does_not_take_a_node_inside_a_full_zone[1-True-P, 1-False-P, 2-True-Q]`
   and `::test_make_way_moves_into_a_full_zone_do_not_go_on_for_ever` (world 206 written out: the run returns before
   600 s with 23 make-way moves, every order completed, no wait open). Default router, 1,452 worlds:

   | | Runs that return | Open waits at the end | Orders completed | Make-way moves (without world 206) |
   |---|---:|---:|---:|---:|
   | Before | 1,451 (206 does not) | 722 | 17,924 of 27,722 | 28,710 (5,187) |
   | After | 1,452 | 731 | 17,883 of 27,722 | 5,105 (5,082) |

   175 of the 1,452 runs changed in their full event stream: 23 completed more orders (69 in all) and 30 fewer (110).
   Under the other routers every run returns as well (world 206 did not return under `astar` and `fastest` either);
   completed orders moved by -82 (`astar`), -13 (`fastest`), +13 and +8 (the congestion routers), -14, -29 and -23 (two
   fleets, orders of the first fleet). A blocker that used to start towards a node inside a full zone, and so cleared
   the way it stood in, now stays when nothing else qualifies; that is the cost of the rule. The scoped re-review pooled its worlds: over 4,388 worlds and the three
   strict routers the idle-search fix changes completed orders from 159,651 to 159,650 (bootstrap 95 % interval by
   world -305 to +310), so it is neutral on throughput.
2. **A wait behind a blocker that strands was never reviewed.** A sleeping wait woke only on a grant or release, and a
   stranding that frees nothing is neither: the waiting travel was not rerouted around the stranded blocker and not
   reported stuck. Fix: the coordinator calls `controller.idle(agv)` on the transition to `STRANDED` as well as to
   `IDLE`; `ReservationController` reviews each open wait the stranded AGV blocks at that instant (spec §7.1, §7.9,
   §7.10). Tests: `test_idle_traffic.py::test_a_wait_behind_a_blocker_that_strands_is_reviewed_at_once` (both
   reproductions, with and without a detour) and `::test_a_stranding_before_the_first_review_takes_its_timer`. Runs
   ending with an open wait that no stuck or deadlock report explains: 6 of 9,270 before (worlds 155 and 1163), 5
   after item 1, 0 after item 2. Item 2 changed only those runs, which now end with the stuck reports; no order
   changed.
3. **`subdivide` and `block_length` did not build the same network.** `subdivide` counted pieces with a bare ceiling
   (a chord of 3.3000000000000007 split by 3.3 gave two pieces, the layout one) and gave pieces the parent's length over
   the count, where the layout's pieces had the chords of their generated points. 91 of 240 random twins had different
   segment lengths and 76 different event streams. Fix: one rule, `graph.split`, for both the count and the length; the
   layout carries the piece length into `Arc.length` (spec §6.1, §12). After it the 240 twins agree event for event.
   Tests in `tests/layout/test_traffic_fields.py`: the twins on a 3.3 m pitch (see the §17 walk). No pinned value moved.
4. **Traces depended on the Python version.** `ArcProfile.duration` used the builtin `sum`, compensated from CPython
   3.12 only, so move end times differed in the last place between 3.11 or PyPy and 3.12+ (25 of 349 kinematic runs).
   Fix: `math.fsum` there and in the fallback of `ArcProfile.v_out`; the other sums of `kinematics.py`, `travel.py` and
   `speed.py` reach no event or decision or are already exact. The 25 runs now have the same streams on 3.11 and
   3.14; on CPython 3.14 the `fsum` change altered no event stream (0 of 2,904 runs), and the four seeds (134, 169, 233,
   527) that changed there changed with the idle-search fix of item 1. `tests/intralogistics` and `tests/layout` pass
   on CPython 3.11 and 3.14 with the same pinned values; nothing was re-pinned. Test: `test_kinematics.py::test_duration_and_exit_speed_are_the_same_floats_on_every_interpreter`
   (fails on 3.11 before the fix).
5. **`Node.__hash__`.** A node hashed `(id, x, y)` on every dict access; it now hashes its id. `shortest_path` corner
   to corner on a 30 by 30 grid: 1.85 ms before, 1.23 ms after. The full event streams are bit-identical before and
   after on the intralogistics examples (free flow and reservation), Appendix A, and 1,782 random worlds (600 under
   free flow, 600 under reservation of the part C task review, 582 of the control reviewer's under reservation).
6. **The arc length check failed at georeferenced coordinates.** A layout with `block_length`, far from the origin,
   raised a bare `ValueError`: the piece length passed to `Arc` fell under the chord of the generated points by more
   than the check's relative 1e-9, because the points are rounded to the spacing of the floats there (9.3e-10 at 5e6).
   `subdivide` failed identically; only edges of 5 to 60 m from `(off, off)` at a random angle with `off` of 1e6 or
   more were affected. Reproduction, 200 random edges per offset and block, failures of the layout (the same count for
   `subdivide`) before the fix: 55 at `off` 1e6 with block 0.1; 6, 110 and 194 at `off` 5e6 with blocks 1.0, 0.5 and
   0.1; none at `off` of 1e5 or less. Fix: `Arc` accepts a length below the chord by the larger of the relative 1e-9
   and four units of floating-point spacing at the largest absolute coordinate of its two nodes (the measured
   shortfall is at most 1.32 units over offsets from 1e5 to 1e8); near the origin the check is the same as before, the
   piece counts and lengths of `subdivide` and of the layout are untouched, and the message is unchanged. After the
   fix the reproduction has no failure. Tests: `test_graph.py::TestArcLength` (a length below the chord by more than
   the tolerance still raises at the origin and at 5e6; rounding at 5e6 is accepted) and
   `test_traffic_fields.py::test_subdivide_and_block_length_build_the_same_network_at_georeferenced_coordinates`
   (offsets 1e6 and 5e6, blocks 1.0, 0.5 and 0.1, 40 random edges each: the twins build without error and have the
   same nodes and arc lengths to the float). No pinned value moved.
