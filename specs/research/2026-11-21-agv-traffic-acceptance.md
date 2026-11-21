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
| Head-on in a corridor | `test_head_on_in_a_corridor_the_second_agv_waits_before_its_port`; at a passing place `test_travel.py::test_a_head_on_meeting_at_a_passing_place_completes_with_a_length` |
| Swap on a two-node line | `test_traffic_termination.py::test_two_agvs_swapping_on_a_line_leave_one_unresolved_deadlock_and_the_run_returns` |
| Stuck wait | `test_traffic_termination.py::test_a_loaded_agv_stuck_behind_an_idle_one_that_cannot_move_leaves_one_stuck_wait_and_the_run_returns` |
| Default builder | `test_the_default_builder_completes_twenty_transfer_orders_with_no_deadlock_and_no_stuck_wait`; `test_the_default_builder_with_orders_in_both_directions`; `tests/layout/test_builders.py::test_layout_builder_with_a_reservation_controller` |
| Two fleets on one controller | `test_two_fleets_share_a_controller`; `test_traffic.py::TestFreeFlowController::test_two_fleets_on_one_graph_share_one_controller` for free flow |
| Lone AGV | `test_a_lone_agv_arrives_at_the_same_times_under_both_controllers_on_the_appendix_layout[0.0, 1.0]`, `..._on_the_builder_line`, `..._on_a_line_split_into_arcs[1, 2, 10]` |
| Giving up | `test_traffic_termination.py::test_an_agv_with_cargo_does_not_give_up_a_charging_diversion`, `::test_an_order_is_requeued_for_traffic_at_most_max_traffic_requeues_times` |
| Resolution by reroute and by step aside | `test_a_swap_with_a_second_route_is_resolved_by_reroute`; `test_a_swap_without_another_route_is_resolved_by_step_aside`; at unit level `test_deadlock.py::TestResolution` |
| Fairness | `test_a_stream_does_not_delay_an_opposite_waiter_beyond_the_vehicles_already_inside`; at unit level `test_reservation.py::TestGate` |
| Gate waiver | `test_a_gate_that_would_complete_a_cycle_is_waived`; at unit level `test_deadlock.py::TestDetection::test_a_cycle_only_through_a_gate_is_waived` |
| Idle and parking | `test_idle_traffic.py`: `test_an_idle_blocker_makes_way_and_goes_idle_again`, `test_an_agv_idle_on_the_exit_port_of_a_corridor_makes_way_for_the_opposite_traffic`, `test_an_agv_leaves_the_delivery_bay_under_reservation`, `test_an_area_of_two_never_lists_three_agvs`, `test_a_full_area_is_not_a_target` |
| Capacity above one, extent longer than a block, `passing` segments, authored zones | `test_travel.py::test_two_vehicles_at_a_multi_slot_leaf_one_sent_away`, `::test_extent_longer_than_arc_holds_two_segments`; `test_reservation.py::TestAdmission::test_passing_admits_both_directions`, `::TestAdmission::test_zone_capacity`, `::TestRelease::test_hold_back_zone_is_claimed_a_leg_early`; `test_network.py::TestCorridors::test_passing_segment_removes_the_corridor`, `::TestClaims::test_plain_zone_only_with_the_leg_that_takes_a_member` |
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
- The three examples run with each coordinator on its own `ReservationController` and vehicle length 0. Their orders
  are not asserted to complete: only the invariants and the replay are. (The advanced example completes 62 outbound and
  2 replenishment orders with none failed; the simple example has one order in transit at its horizon, 3 of 4 completed,
  because the AGVs now take turns on a line they shared freely before.)
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

Nothing was re-pinned in this task. The generated trace fixtures are not stale (`tests/core/test_trace_fixtures.py`
passes) and the frozen fixtures load without change.

## Changes in results

- Free-flow results move for the three reasons above (the `agv.routed` event in digests, interrupts at the end of the
  arc, parking slots), which is why the benchmark trajectories of `main` and the branch differ.
- The retry loop of the pending queue stops when nothing needs it, so `env.run()` without `until` returns. In free flow
  this can move the time of a `retries_exhausted` failure, because the loop restarts when it is needed again.
