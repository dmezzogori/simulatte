# AGV traffic, part B: motion — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** approved by Davide on 2026-11-21 together with the design. No task is implemented yet.

**Goal:** An AGV carries its speed through nodes, so free-flow travel time no longer depends on how a line is split into nodes; turning costs time; an interrupt takes effect at the next node where the AGV can stop; energy follows the distance covered, plus an optional idle power.

**Architecture:** A pure planner computes one speed profile per arc for a whole run, from the AGV's current node to the last node it holds claims for, always ending at rest there. The executor of part A replans at node events, keeps claims one arc plus the braking distance ahead, and emits each arc's profile as a new `piecewise` motion curve that Studio evaluates.

**Stack:** Python 3.11+ standard library, SimPy; TypeScript (`studio/packages/scene`, vitest).

**Spec:** [`specs/2026-11-21-agv-traffic-design.md`](../specs/2026-11-21-agv-traffic-design.md), §8.3, §9, §11.3, §13. Signatures below are boundaries, not licence to weaken a contract stated there.

**Follows:** [part A, control](2026-11-21-agv-traffic-a-control.md), complete through its Task 14. **Followed by:** [part C, routing and release](2026-11-21-agv-traffic-c-routing.md).

## Global constraints

- Same branch, commit, coverage, lint, typing and determinism rules as part A.
- Kinematic limits are sampled when an AGV starts from rest and hold until it next stops (spec T14). Speed is replanned at node events only (T15). The profile of an arc is fixed when the AGV enters it.
- A plan always ends at rest at the horizon. Granted claims are never revoked, so a horizon only grows.
- Requirement: when every claim is granted at once, an AGV under `ReservationController` has exactly the travel time it has under `FreeFlowController` (spec §9.3).
- A profile without `limits` keeps today's rest-to-rest behaviour and timings exactly.
- No trace container change. `trapezoidal`, `constant`, `linear` and `keyframes` stay valid.
- Tolerances: durations sum to `t_end - t_start` within the tolerance `motion.ts` already uses for `keyframes`; integrated distance within `max(1e-9, 1e-9 * distance)`; speeds never below `-1e-9`.

## Review focus

| Risk the happy path misses | Owning test |
| --- | --- |
| Two nodes at the same coordinates give an arc of length 0 inside a run: no division by zero, no negative duration, the node event still happens. | Task 1 `test_kinematics.py::test_zero_length_arc_inside_a_run`; Task 3 `test_travel_motion.py::test_zero_length_arc_is_traversed_in_zero_time` |
| The battery factor makes `v_max` or `accel` non-positive: the AGV stalls as today (`t_end` infinite, `stalled`), it does not raise or loop. | Task 3 `test_travel_motion.py::test_non_positive_limits_stall_the_run` |
| `corner_speed` or `turn_time` returns a negative number, NaN or infinity. | Task 4 `test_turning.py::test_invalid_turning_values_raise` |
| An interrupt, a grant and a node arrival fall on the same instant. | Task 5 `test_stop_requests.py::test_interrupt_at_the_instant_of_arrival`; Task 3 `test_travel_motion.py::test_grant_at_the_instant_of_arrival_is_used` |
| The battery drops during a run: limits must not change mid-run, and the next run must use the new level. | Task 3 `test_travel_motion.py::test_limits_are_sampled_per_run` |

## Sequence and gates

Tasks run in order. Task 3 is the difficult one. Gate after Task 7. Every task ends with the full suite: `uv run --all-extras pytest`.

---

### Task 1 — Run planner

**Modify:** `src/simulatte/intralogistics/kinematics.py`, `tests/intralogistics/test_kinematics.py`.
**Interfaces (produces):**

```python
def plan_run(
    v0: float, lengths: Sequence[float], arc_caps: Sequence[float], node_caps: Sequence[float], kin: Kinematics
) -> tuple[ArcProfile, ...]: ...
```

`lengths` and `arc_caps` have one entry per arc; `node_caps` has one more, for the nodes from the current one to the horizon. `node_caps[0]` is ignored in favour of `v0`; `node_caps[-1]` must be 0. The algorithm is spec §9.2: fold the arc caps into the node caps, backward pass with `decel`, forward pass with `accel`, then `arc_profile` per arc.

- [ ] Tests, with `Kinematics(1.5, 0.8, 0.8)` unless stated:
  - `test_single_arc_equals_rest_to_rest`;
  - `test_split_line_has_the_same_duration`: lengths `[5, 5]` with no node cap give 4.270833333333333 per arc and 8.541666666666666 in total, the duration of one arc of 10; lengths `[1] * 10` give the same total within 1e-12;
  - `test_speed_limit_change`: lengths `[10, 10]`, arc caps `[1.5, 0.5]` give 8.020833333333334 and 20.3125, with speed 0.5 at the middle node;
  - `test_short_arc_is_triangular`: length 1 gives duration `2 * sqrt(0.8) / 0.8`;
  - `test_entering_at_speed`: `v0=1.5`, lengths `[5]` gives a cruise of 2.3958333333333335 and a deceleration of 1.875;
  - `test_node_cap_limits_the_pass_speed`: lengths `[10, 10]`, node caps `[0, 0.5, 0]` pass the middle node at 0.5;
  - `test_cannot_stop_in_the_horizon_raises` (`v0=1.5`, lengths `[1]`: `ValueError`);
  - `test_zero_length_arc_inside_a_run` (lengths `[5, 0, 5]`: the middle profile has no phases, total as for 10);
  - `test_last_node_cap_must_be_zero`.
- [ ] Implement. Every returned profile satisfies `profile.position(profile.duration) == length` within 1e-9 and consecutive profiles agree on the node speed.
- [ ] `uv run --all-extras pytest tests/intralogistics/test_kinematics.py --no-cov -q`, full suite, commit `feat(intralogistics): speed planner across arcs`.

### Task 2 — Motion curve `piecewise`, in Python and TypeScript

**Create:** `tests/fixtures/motion/generate.py`, `tests/fixtures/motion/piecewise.json`, `tests/intralogistics/test_motion_piecewise.py`.
**Modify:** `src/simulatte/intralogistics/kinematics.py`, `src/simulatte/intralogistics/speed.py`, `studio/packages/scene/src/motion.ts`, `studio/packages/scene/test/motion.test.ts`.
**Interfaces (produces):**

```python
class ArcProfile:
    def description(self) -> MotionDescription: ...

    # {"curve": "piecewise", "v0": float, "phases": [[duration, acceleration], ...], "distance": float}


# speed.py
def motion_fraction(description: Mapping[str, object], duration: float, elapsed: float) -> float | None: ...


# the position fraction of a valid piecewise description, or None when it is invalid
```

The fixture file is a list of cases `{"name", "t_start", "t_end", "description", "samples": [[time, fraction], ...]}` written by `generate.py` from `ArcProfile`; it includes valid cases (one, two, three and four phases, nonzero `v0`, a zero-acceleration-only case) and invalid ones with `"samples": null` (a duration mismatch, a negative speed, a distance mismatch, an empty phase list, a non-positive duration).

- [ ] Tests (pytest): the committed fixture equals a fresh generation; `motion_fraction` reproduces every sample within 1e-12 and returns `None` for every invalid case; a description with zero-duration phases is never produced by `ArcProfile.description`.
- [ ] Tests (vitest): `motionProgress` reproduces every sample of the fixture within 1e-9 with `approximate === false` and `diagnostic === null`; every invalid case falls back to linear with "Approximate motion"; existing `trapezoidal`, `keyframes`, `linear` and `constant` tests are unchanged.
- [ ] Implement the `piecewise` branch in `motion.ts` after `keyframes`, with the validity rules of spec §11.3.
- [ ] `uv run --all-extras pytest tests/intralogistics/test_motion_piecewise.py --no-cov -q`; in `studio/`: `pnpm typecheck && pnpm test`; full suite; commit `feat(motion): piecewise curve in the trace and in Studio`.

### Task 3 — Runs in the executor: look-ahead and pass-through

**Create:** `tests/intralogistics/test_travel_motion.py`.
**Modify:** `src/simulatte/intralogistics/travel.py`, `src/simulatte/intralogistics/fleet.py`, the part A tests whose timings assumed a stop at every node.
**Interfaces (produces):** `FleetCoordinator(lookahead_margin: float = 0.0)`; `AGV.heading: float | None` (radians, the direction of the last arc traversed; `None` after placement).
**Consumes:** `plan_run`, `ArcProfile.description`, `KinematicProfile.limits`, the controller protocol of part A.

Behaviour (spec §8.1 steps 2 and 4, §9.2, §9.3):

- For a profile with `limits`: sample `Kinematics` when the AGV starts from rest; at every node event request claims until the horizon lies at least `v_max ** 2 / (2 * decel) + lookahead_margin` beyond the end of the next arc; plan from the current node and speed to the horizon with `plan_run`; enter the next arc with its profile; a grant that arrives while moving chains the next request from the event's callback without waking the process.
- Arc caps are `min(v_max, speed_limit)`. Node caps are the minimum of the two adjacent arc caps (turning is Task 4).
- `agv.motion` and `agv.move_started` carry `profile.description()`. Passing through a node emits `agv.move_ended` and the next `agv.move_started` at the same instant.
- Clear points use `profile.time_at`.
- A profile without `limits` keeps the part A path unchanged.

- [ ] Tests, free flow: `test_split_line_arrival_times` (a 30 m line as 1, 3 and 30 arcs: equal arrival within 1e-9 relative); the T layout travel `STORE_OUT → LINE_IN` takes the single-profile time for 30 m; `agv.move_ended` at an intermediate node and the following `agv.move_started` share a timestamp; each `piecewise` description integrates to its arc length and duration.
- [ ] Tests, reservation: `test_lone_agv_matches_free_flow` on the T layout, the builder line and the 30-arc line (identical arrival times); while cruising, the nodes reserved ahead cover the next arc plus the braking distance and no more than one claim beyond it; a follower behind a stopped leader comes to rest at the last node it holds and restarts when granted; `test_grant_at_the_instant_of_arrival_is_used`; a claim granted after the AGV entered an arc that already brakes still ends in a stop at that node (the documented limit) and the delay is at most one deceleration plus one acceleration.
- [ ] Tests: `test_limits_are_sampled_per_run` (a depletion that halves the battery mid-run does not change the phases of the following arcs; the next run from rest uses the new limits); `test_non_positive_limits_stall_the_run`; `test_zero_length_arc_is_traversed_in_zero_time`; a profile with only `travel_time` reproduces the part A timings exactly; `lookahead_margin=5.0` extends the reserved distance by 5.
- [ ] Update the part A tests that asserted a stop at every node for kinematic profiles; keep their assertions on waiting, release and events.
- [ ] Implement. `TrafficInvariants` must hold in every test of the file, and `FullReplay` must equal live state.
- [ ] Focused run, full suite, commit `feat(intralogistics): carry speed through nodes with claim look-ahead`.

**Acceptance:** spec goal 6 holds under both controllers for a lone AGV.

### Task 4 — Turning

**Create:** `tests/intralogistics/test_turning.py`.
**Modify:** `src/simulatte/intralogistics/agv.py` (`AGVType.corner_speed`, `AGVType.turn_time`), `src/simulatte/intralogistics/travel.py`.
**Interfaces (produces):** `AGVType.corner_speed: Callable[[float], float] | None = None`, `AGVType.turn_time: Callable[[float], float] | None = None`; both receive the heading change in radians, in `[0, pi]`, computed from the chords of the incoming and outgoing arcs.

- [ ] Tests on an L-shaped route of two 10 m arcs at a right angle, `Kinematics(1.5, 0.8, 0.8)`: with `corner_speed=lambda a: 0.5` the corner is passed at 0.5 and both arcs take 8.020833333333334; with `turn_time=lambda a: 2.0 * a / (pi / 2)` the AGV stops at the corner, waits 2.0 and leaves from rest (8.541666666666666, 2.0, 8.541666666666666); a straight continuation costs nothing; the first arc after placement turns for free; reversing at a node is a turn of `pi`; a turn at the start of a travel from rest applies `turn_time` before departure when the heading is known.
- [ ] Test `test_invalid_turning_values_raise` (negative, NaN and infinite `turn_time`; negative and NaN `corner_speed`: `ValueError`). `corner_speed` returning 0 means a stop; infinity means no cap.
- [ ] Implement: node caps take `corner_speed`; a positive `turn_time` sets the node cap to 0 and adds the wait at rest before the next arc, with the AGV's disposition `serving`.
- [ ] Focused run, full suite, commit `feat(intralogistics): corner speed and turn time`.

### Task 5 — Stop requests while moving, and the energy horizon

**Create:** `tests/intralogistics/test_stop_requests.py`.
**Modify:** `src/simulatte/intralogistics/travel.py`, `src/simulatte/intralogistics/fleet.py`.

Behaviour (spec §8.3, §9.5): a stop request truncates the plan at the first node of the horizon where the AGV can be at rest given its speed and deceleration. At rest the executor withdraws its pending request, calls `retain` with what the body overlaps, and then raises the interrupt (after `agv.move_interrupted` at that node), or returns `RETRY_FROM_CURRENT_POSITION`, or runs the charging-or-stranded branch. The plan covers only arcs whose cumulative `estimate_energy(length, load_weight, arc_cap)` fits the battery level.

- [ ] Tests, interrupts: cancelling an order while its AGV cruises on arc 2 of 6 (10 m arcs): the arc's profile is fixed, so the AGV passes the end of arc 2 at speed and stops at the end of arc 3; cancelling during an arc that already ends at rest stops it at that arc's end; `agv.move_interrupted` is emitted at the stop node with the cause; the battery is depleted for exactly the arcs covered; every claim ahead is released; the mission recovers as today (order `CANCELLED`, AGV `IDLE`, cargo returned when loaded); a second interrupt before rest is dropped; `test_interrupt_at_the_instant_of_arrival`.
- [ ] Tests, battery: a level that becomes critical on arrival stops the AGV at the first feasible node and returns `RETRY_FROM_CURRENT_POSITION`; with energy for two and a half arcs the plan ends at the second node, where the charger diversion starts, or the AGV is `STRANDED` with `agv.stranded` when no charger is reachable; with a depletion function that depends on speed the horizon uses the arc cap.
- [ ] Implement.
- [ ] Focused run, full suite, commit `feat(intralogistics): stop requests and energy horizon for moving AGVs`.

### Task 6 — Idle power

**Modify:** `src/simulatte/intralogistics/agv.py` (`AGVType.idle_power: float = 0.0`), `battery.py` (`Battery.drain(amount: float) -> None`), `fleet.py`, `travel.py`, `charging.py`, `tests/intralogistics/test_battery.py`, `tests/intralogistics/test_agv.py`.

- [ ] Tests: with `idle_power=0.5` an AGV that waits 20 in traffic has 10 less energy at its next event; the level is brought up to date before every energy check, depletion, dispatch decision and charge; time spent `CHARGING` does not drain; an accrual outside a move emits `agv.battery_changed`, and one inside a move is folded into `agv.move_ended`; the level never goes below 0; with the default 0 no additional event is emitted and digests are unchanged; negative `idle_power` raises `ValueError`.
- [ ] Implement lazily: one accrual timestamp per AGV, no timer.
- [ ] Focused run, full suite, commit `feat(intralogistics): idle power`.

### Task 7 — Part B acceptance

**Modify:** `tests/intralogistics/test_traffic_scenarios.py`, `tests/intralogistics/test_motion.py`, `tests/intralogistics/test_builders.py` (the pinned digest), `specs/research/2026-11-21-agv-traffic-acceptance.md`.

- [ ] Tests, spec §17 motion and stop-request items not yet covered: the Appendix A scenarios still complete every order with through-node motion; `TrafficInvariants` holds in every scenario and in the three examples; replay equals live state; digests are identical across `PYTHONHASHSEED` values and with and without observers.
- [ ] Re-pin the digest in `test_builders.py`; regenerate fixtures with `tests/fixtures/traces/generate.py` if any changed; the frozen fixtures still load unchanged.
- [ ] Run `benchmarks/intralogistics.py`; append the numbers and the full-suite, ruff, ty and pnpm results to the acceptance file.
- [ ] Commit `test(intralogistics): motion acceptance`.

**Acceptance (gate):** part B complete; part C may start.

## Review handoff

Per task, a reviewer reads the tests against spec §9 and §8.3 and reruns the hand-computed values. Before part C: one review of the executor as a whole, checking that the plan always ends at rest at the horizon, that no claim ahead survives a stop, and that a profile without `limits` behaves exactly as at the end of part A.
