# Intralogistics Subsystem Branch Review (Consolidated)

**Date:** 2026-04-30
**Branch:** `feature/intralogistics`
**Reviewers:** Two independent adversarial reviews against spec + all implementation/fix plans, findings merged and deduplicated
**Test suite status:** 287 tests pass, 0 failures

## Overall Status

The branch is in good shape. The subsystem is structurally complete: 16 source files (2,504 LOC) across `src/simulatte/intralogistics/`, 20 test files (7,580 LOC) under `tests/intralogistics/`, and the experimental cleanup is done. The vast majority of the original spec, the implementation plan, the fixes plan, and the follow-up plan items have been addressed. Two independent adversarial reviews surfaced a combined set of findings below — most clustered around interrupt/recovery edge paths in `fleet.py` and a few spec-compliance gaps.

## Reference Documents

- Spec: `docs/superpowers/specs/2026-04-30-intralogistics-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-30-intralogistics-implementation.md`
- Fixes plan: `docs/superpowers/plans/2026-04-30-intralogistics-fixes.md`
- Follow-up plan: `docs/superpowers/plans/2026-04-30-intralogistics-review-followups.md`

---

## Findings

### HIGH

#### H1: Cargo silently destroyed when ResumeDelivery re-travel fails

**File:** `src/simulatte/intralogistics/fleet.py:452-454, 478-480`

**What:** When `ResumeDelivery` recovery resumes loaded travel and that travel returns `BATTERY_STRANDED` or `MISSION_FAILED`, the code at line 478-480 executes:
```python
# Resume failed (STRANDED) — clear cargo
agv.current_load = None
```
The cargo is set to `None` without returning it to either the origin or destination warehouse. The inventory was already deducted from origin via `warehouse.pick()`, never delivered to destination, and never put back. It is silently destroyed.

**Why it matters:** Inventory conservation is a fundamental correctness invariant. In any simulation with battery-constrained AGVs and `ResumeDelivery`, a single stranding event during the recovery re-travel causes permanent inventory loss. The system's total inventory decreases over time.

**Test gap:** `TestResumeDeliveryStranded` (test_coordinator.py:2698) asserts `agv.current_load is None` but never verifies that inventory arrived at any warehouse. The test passes because it does not check inventory conservation.

**Violates:** Spec §8 — `ResumeDelivery` "accepts the risk of over-delivery" but not cargo deletion. When the resume itself fails, cargo must be returned somewhere.

---

#### H2: `_check_pending_queue()` retry count only increments when idle AGVs exist

**File:** `src/simulatte/intralogistics/fleet.py:815-818`

**What:** `_check_pending_queue()` wraps the retry-count increment inside `elif idle_agvs:`. When every AGV is dispatched (none idle), `select()` returns `None` but `idle_agvs` is empty, so the retry counter never advances. The `_pending_retry_loop()` spins calling `_check_pending_queue()` every 0.001s without progressing the retry counter. The order stays pending until an AGV happens to go idle.

**Why it matters:** If all AGVs are permanently occupied (or there is only 1 AGV on a long mission), an unfulfillable order never transitions to `FAILED` via the retry path. The `_pending_retry_loop` busy-waits at 0.001-second intervals, burning simulation time without progressing toward failure. Spec §8 says unfulfillable orders should eventually fail.

**Note:** The existing test `test_unfulfillable_order_fails_without_other_missions` passes only because its single AGV is IDLE — `select()` returns `None` (SKU incompatible), but `idle_agvs` is non-empty, so the retry counter advances. If the AGV were busy, the test would not exercise the bug.

**Violates:** Spec §8, fixes plan S4, follow-up plan Finding 3

---

#### H3: Critical battery does not re-queue order to a different AGV

**File:** `src/simulatte/intralogistics/fleet.py:650-652`

**What:** When battery becomes critical mid-travel, `_travel` returns `RETRY_FROM_CURRENT_POSITION`. The `_run_mission` retry loop (lines 326-340, 364-378) then charges the **same AGV** and retries. The spec (§9) explicitly says: "If `is_critical`: mission is interrupted immediately. The AGV diverts to the nearest charging station. The order is re-queued for another AGV."

**Why it matters:** In a fleet with multiple AGVs, keeping a critically-depleted AGV tied to the order wastes time (charging delay) when a closer, fully-charged AGV could complete the mission faster. The spec intentionally designed this as a fleet-level optimization.

**Test gap:** No test verifies that a critical battery event causes the order to be re-queued and potentially assigned to a different AGV.

**Violates:** Spec §9

---

#### H4: `ReturnToOrigin.recover()` puts inventory back without navigating the AGV to origin

**File:** `src/simulatte/intralogistics/policies.py:236-244`

**What:** `ReturnToOrigin.recover()` calls `yield from order.origin.put(sku, qty)` which goes through the warehouse `put` (slot acquisition + put-time timeout + inventory addition), but the AGV is not physically at the origin warehouse's input bay. The cargo is effectively teleported back.

**Why it matters:** In a simulation with material handling time modeled by `put_time_fn`, this produces incorrect time accounting. The AGV is credited with being at the warehouse when it is physically at a different node on the graph. If inventory operations are ever made location-aware, this will silently break. The recovery was meant to model a real physical return trip.

**Violates:** Spec §8 (`ReturnToOrigin` — "after the AGV recovers, it **returns** the cargo to the origin warehouse"), fixes plan S6

---

#### H5: Explicit cancellation with cargo bypasses `LoadRecoveryStrategy` and uses fire-and-forget rollback

**File:** `src/simulatte/intralogistics/fleet.py:491-496`

**What:** Three issues on this code path (and a related one at line 436):

1. **Protocol bypass:** When a mission is cancelled and the AGV has cargo, the code directly returns inventory to origin via `env.process(order.origin.put(sku, qty))` and clears the load, **regardless of the configured `LoadRecoveryStrategy`**. A user who installed `ResumeDelivery` as their recovery strategy would expect cancellation to also follow that strategy. The spec says cancellation cleanup should "handle cargo via `LoadRecoveryStrategy`."

2. **Fire-and-forget timing (line 495):** The `put` is spawned as a fire-and-forget background process. The AGV goes `IDLE` immediately and may be dispatched for a new mission before the `put` finishes. If the warehouse has limited slots, this ties up a slot unexpectedly. If the `put` fails or gets interrupted, inventory is lost. By contrast, `ReturnToOrigin.recover()` correctly `yield from`s the `put`.

3. **Same fire-and-forget pattern at line 436:** The committed-pick rollback (`self.env.process(wh.put(sku, qty))`) for the H5-from-fixes-plan case (interrupt during `warehouse.pick()` after inventory deducted but before `agv.current_load` assigned) also uses `env.process` instead of `yield from`. This one is harder to fix because it's inside an `except simpy.Interrupt` handler that must service several sub-paths, but the same consistency window exists: a replenishment policy firing before the `put` completes sees stale inventory.

**Violates:** Spec §8 (LoadRecoveryStrategy contract), best practice (fire-and-forget for critical state changes)

---

### MEDIUM

#### M1: `TrafficManager` protocol expanded beyond spec with `deadlock_timeout` and `priority`

**File:** `src/simulatte/intralogistics/traffic.py:28-30`

**What:** The spec's `TrafficManager` protocol (§4) defines 6 methods: `place`, `check_path`, `register_intent`, `enter_node`, `leave_node`, `cancel`. The implementation adds a `deadlock_timeout` property and a `priority(agv)` method to the protocol itself. `FreeTrafficManager` must implement these too (returning `None` and `0.0`).

**Why it matters:** Any third-party `TrafficManager` implementation (e.g., the "future FullTrafficController" mentioned in spec §4) must now also implement `deadlock_timeout` and `priority`, which are coordinator-level concerns, not traffic-management concerns. The follow-up plan (Batch 2) explicitly recommended keeping these extensions on the concrete class only and branching conservatively in the coordinator.

**Violates:** Spec §4 (TrafficManager protocol definition), follow-up plan Batch 2 recommendation

---

#### M2: Repositioning ignores `MISSION_FAILED` travel outcome

**File:** `src/simulatte/intralogistics/fleet.py:416-418`

**What:** After mission completion, if repositioning travel returns `MISSION_FAILED` (no path), only `BATTERY_STRANDED` is handled:
```python
if outcome is _TravelOutcome.BATTERY_STRANDED:
    pass
```
`MISSION_FAILED` falls through silently. Additionally, the `pass` on `BATTERY_STRANDED` means the AGV transitions to `IDLE` (line 421) instead of `STRANDED` — corrupting utilization metrics.

**Why it matters:** A battery-stranded AGV during repositioning should remain `STRANDED` (not `IDLE`), and `MISSION_FAILED` during repositioning should be logged or handled explicitly rather than silently swallowed.

---

#### M3: `_enter_with_timeout()` + `enter_node()` interrupt cleanup is fragile

**File:** `src/simulatte/intralogistics/fleet.py:690`, `src/simulatte/intralogistics/traffic.py:136-148`

**What:** When `_enter_with_timeout()` fires a timeout, it interrupts the `enter_proc`. In `ResourceBasedTrafficManager.enter_node()`, the interrupt handler cleans up `_pending_requests` and `_node_requests`. Then `cancel()` is called by `_enter_with_timeout()`, which also tries to clean up both maps. The `already_handled` id-set prevents double-release, but the request is stored in both `_node_requests[(agv, node)]` and `_pending_requests[agv]`. The interrupt handler cleans one, cancel cleans the other. Multiple `pragma: no cover` markers indicate acknowledged unreachable defensive code.

**Why it matters:** Potential resource leak or double-release in edge cases. Currently mitigated by defensive coding, but the complexity is high and brittle. A single cleanup owner would be safer.

---

#### M4: `_find_reachable_charger()` estimates energy with `avg_speed=0.0`

**File:** `src/simulatte/intralogistics/fleet.py:792`

**What:** `energy_needed = agv.battery._depletion_fn(total_dist, 0.0, 0.0)` passes `speed=0.0`. The default depletion function ignores speed, so this works by coincidence. A custom depletion function that depends on speed would get an incorrect energy estimate.

**Why it matters:** With a speed-dependent depletion function, the reachability check uses wrong energy estimates. The AGV might be told a charger is reachable when it isn't (leading to stranding) or unreachable when it is (leading to unnecessary failure).

---

#### M5: `_charge_agv()` post-mission uses `_find_nearest_charger` (no energy check) while pre-arc uses `_find_reachable_charger` — inconsistent

**File:** `src/simulatte/intralogistics/fleet.py:602-623` vs `fleet.py:714`

**What:** Pre-arc battery check calls `_find_reachable_charger()` (verifies the AGV can physically reach the charger with current energy). But `_charge_agv()` when called for post-mission low-battery uses `_find_nearest_charger()` (picks the closest by distance without energy check). If the nearest charger is beyond the AGV's remaining battery, the AGV will travel toward it and get stranded.

**Why it matters:** Post-mission low-battery AGV could get stranded during charging diversion — an unnecessary failure avoidable by checking reachability first.

---

#### M6: `NearestIdleStrategy` dispatches unreachable AGVs

**File:** `src/simulatte/intralogistics/policies.py:63-64`

**What:** When no path exists from an AGV to the origin warehouse, `_distance` returns `(float("inf"), agv_id)`. If this is the only candidate, `min()` still selects it. The mission is spawned, `_travel` returns `MISSION_FAILED`, and the order ends up `FAILED`.

**Why it matters:** This wastes a dispatch cycle and ties up the AGV when the order should have remained in the pending queue. For orders that are fulfillable once a closer AGV becomes idle, this causes premature `FAILED` status. The strategy should filter out candidates with infinite distance.

---

#### M7: `inventory_ts` schema in `DefaultIntralogisticsCollector` deviates from spec

**File:** `src/simulatte/intralogistics/metrics.py`

**What:** Spec §10 says `inventory_ts: dict[Warehouse, list[tuple[float, dict[SKU, float]]]]`. The implementation uses `list[tuple[float, str, str, float]]` — flat tuples with warehouse name and SKU ID as strings instead of a dict keyed by `Warehouse`.

**Why it matters:** The API shape does not match the spec. Users expecting the spec's structure will get a flat list. Querying inventory history for a specific warehouse or SKU requires manual filtering rather than dict lookup.

**Violates:** Spec §10

---

#### M8: Private attribute access across module boundaries

**Files:** `fleet.py:600,792` (`agv.battery._depletion_fn`), `policies.py:119` (`pa._resource.count`/`.capacity`), `traffic.py:88` (`graph._nodes`)

**What:** Several modules access private attributes across class boundaries:
- `Battery` has no public energy-cost estimation method — `fleet.py` directly calls `agv.battery._depletion_fn(...)`.
- `ParkingArea` has no `available_capacity` property — `NearestParkingPolicy` accesses `pa._resource.count` and `pa._resource.capacity`.
- `LayoutGraph` has no public `nodes` property — `ResourceBasedTrafficManager` accesses `graph._nodes`.

**Why it matters:** These create tight coupling to implementation details. Adding public accessors (`Battery.estimate_energy(...)`, `ParkingArea.available_capacity`, `LayoutGraph.nodes`) would be trivial and safer.

---

#### M9: `EMAOrderMetrics` initializes all EMA fields to `0.0` — first records heavily biased

**File:** `src/simulatte/intralogistics/metrics.py:32-34`

**What:** All EMA fields default to `0.0`. With `alpha=0.01`, after the first record the EMA is `0.01 * actual_value` — 99% below the true value. It takes ~100 records to converge.

**Why it matters:** Early EMA values are meaningless. Common practice is to initialize with the first observation.

---

#### M10: `_pending_retry_delay = 0.001` is hard-coded and very small

**File:** `src/simulatte/intralogistics/fleet.py:105`

**What:** The pending retry loop runs every 0.001 simulation time units. With `max_dispatch_retries=10`, an unfulfillable order fails after 0.01 sim-seconds — practically instantly. The delay is not configurable.

**Why it matters:** The interaction between retry delay and retry count is non-obvious. A user setting `max_dispatch_retries=100` expects meaningful retries, not 100 checks in 0.1 sim-seconds.

**Violates:** Fixes plan S4 ("configurable retry count or timeout")

---

#### M11: `_initial_placement()` race with early `submit()`

**File:** `src/simulatte/intralogistics/fleet.py:131`

**What:** `_initial_placement()` is spawned as a SimPy process in `__init__()`. If a user calls `coordinator.submit(order)` before `env.run()`, both the placement and the dispatched mission are queued at t=0. Under `ResourceBasedTrafficManager`, the mission's `_travel` could try to enter nodes before placement registered the AGVs' starting positions.

**Why it matters:** SimPy processes registered first typically run first at the same timestamp, so placement likely wins — but this is an implicit ordering dependency. No test exercises submit-before-run under `ResourceBasedTrafficManager`.

---

### LOW

#### L1: `ReorderPointPolicy` uses `o.sku is sku` (identity) instead of `o.sku == sku` (equality)

**File:** `src/simulatte/intralogistics/policies.py:183`

**What:** `o.sku is sku` checks object identity, not equality. Two separately-constructed `SKU("X", 1.0, 0.1)` instances are equal via `==` but not identical via `is`.

**Why it matters:** If the user constructs SKUs in different places (e.g., one for the order, one for policy thresholds), the identity check fails silently — the policy would not detect matching in-transit orders, leading to duplicate replenishment. Works in current tests only because all tests reuse the same instance.

---

#### L2: `ResourceBasedTrafficManager` accesses `graph._nodes` (private)

**File:** `src/simulatte/intralogistics/traffic.py:88`

**What:** `ResourceBasedTrafficManager.__init__` iterates `graph._nodes`. A public `nodes` property on `LayoutGraph` would be preferred.

**Note:** Already covered in M8, listed separately for traceability.

---

#### L3: `FleetCoordinator` exposes configuration as mutable public attributes

**File:** `src/simulatte/intralogistics/fleet.py:88-92`

**What:** `self.graph`, `self.fleet`, `self.warehouses`, `self.charging_stations`, `self.parking_areas` are all plain public attributes. Nothing prevents mutation after construction.

**Why it matters:** Spec says the graph is "immutable after setup." Exposing `fleet` as a mutable list means users could append/remove AGVs mid-simulation.

---

#### L4: Module named `fleet.py` instead of `coordinator.py`

**File:** `src/simulatte/intralogistics/fleet.py`

**What:** Spec §2 says `coordinator.py`. The implementation uses `fleet.py`. Acknowledged in the fixes plan as an accepted deviation. Public API exports `FleetCoordinator` from `__init__.py` regardless of filename.

**Violates:** Spec §2 (accepted deviation)

---

#### L5: `build_simple_system` does not create parking areas

**File:** `src/simulatte/intralogistics/builders.py`

**What:** No `ParkingArea` is created. The `FleetCoordinator` accepts `parking_areas` but the convenience builder doesn't provide one.

---

#### L6: `fleet.py` coverage at 97% — 12 uncovered lines remain

**File:** `src/simulatte/intralogistics/fleet.py:331-332, 418, 453-454, 555-559, 695, 740-741`

**What:** Uncovered lines include: empty-travel battery stranding (331-332), repositioning battery-stranding pass-through (418), ResumeDelivery MISSION_FAILED during re-travel (453-454), alt-path `delay_until` handling (555-559), `enter_proc.is_alive` check (695), and `_charge_agv` early return when `current_node is None` (740-741).

**Violates:** Project coverage requirement (99%)

---

#### L7: Distance helper duplicated across 5+ locations

**Files:** `fleet.py:771,789`, `policies.py:65,131`, `warehouse.py`

**What:** The pattern `sum(math.hypot(path[i+1].x - path[i].x, ...) for i in range(len(path)-1))` appears in at least 5 places. A `LayoutGraph.path_distance(path)` method would eliminate duplication.

---

#### L8: `RoundRobinStrategy` cursor doesn't reset on fleet changes

**File:** `src/simulatte/intralogistics/policies.py:87`

**What:** The internal `_cursor` is incremented monotonically. If the fleet composition changes (AGVs added/removed), the modulo arithmetic still works but cycling order becomes unpredictable. Not a bug, but a surprising edge case.

---

## Spec vs Implementation Mismatches

| Spec Section | Spec Requirement | Implementation Status | Finding |
|---|---|---|---|
| §2 | Module named `coordinator.py` | Named `fleet.py` | L4 (accepted deviation) |
| §4 | `TrafficManager` protocol has 6 methods | Protocol extended with `deadlock_timeout` + `priority` | M1 |
| §8 | Cancellation cleanup "handle cargo via `LoadRecoveryStrategy`" | Hardcodes return-to-origin, bypasses strategy | H5 |
| §8 | Unfulfillable orders → `FAILED` after retry count/timeout | Retry count gated on idle AGVs being present | H2 |
| §8 | `ReturnToOrigin` — AGV returns cargo to origin warehouse | Inventory put without AGV navigation (teleport) | H4 |
| §8 | `ResumeDelivery` accepts risk of over-delivery | Cargo deleted on re-travel failure (inventory loss) | H1 |
| §9 | Critical battery → order re-queued for another AGV | Same AGV retries after charging | H3 |
| §10 | `inventory_ts: dict[Warehouse, list[...]]` | `list[tuple[float, str, str, float]]` (flat tuples) | M7 |

---

## Plan vs Implementation Mismatches

| Plan | Item | Status | Finding |
|---|---|---|---|
| Fixes plan | S4 (unfulfillable orders fail after retries) | ⚠️ Partial — retry loop exists but counter gated on idle AGVs | H2 |
| Fixes plan | S4 (configurable retry count or timeout) | ⚠️ `max_dispatch_retries` configurable but `_pending_retry_delay` hard-coded | M10 |
| Fixes plan | S6 (ReturnToOrigin actual cargo return) | ⚠️ Returns inventory but no AGV navigation | H4 |
| Follow-up plan | Batch 2 (protocol extension recommendation) | ❌ `deadlock_timeout`/`priority` added to protocol, not just concrete class | M1 |
| Follow-up plan | Batch 3 (unfulfillable orders fail without external activity) | ⚠️ Relies on `idle_agvs` being present for counter to advance | H2 |
| Follow-up plan | Batch 4 (NearestIdleStrategy per-AGV nearest bay) | ✅ Fixed | — |
| Follow-up plan | Batch 5 (event-driven replenishment at pick time) | ✅ Fixed | — |
| Follow-up plan | Batch 6 (honor `delay_until`) | ✅ Fixed | — |

---

## Test Gaps

| Area | Gap Description | Related Finding |
|---|---|---|
| ResumeDelivery + stranding inventory conservation | `TestResumeDeliveryStranded` asserts `current_load is None` but never checks cargo was returned anywhere. Inventory silently destroyed. | H1 |
| Unfulfillable order with all AGVs busy | No test verifying retry progression when `idle_agvs` is empty. Existing test only works because the AGV is idle. | H2 |
| Critical battery → re-queue to different AGV | No test verifies a critical battery event causes the order to be re-queued and assigned to a different AGV. | H3 |
| `ReturnToOrigin` physical travel to origin | No test verifying the AGV navigates back to origin before putting inventory. | H4 |
| Cancel-with-cargo invokes `LoadRecoveryStrategy` | No test verifying cancellation delegates to the configured strategy rather than hardcoding return-to-origin. | H5 |
| Cancel-with-cargo `put` completion ordering | No test verifying the fire-and-forget `put` completes before AGV re-dispatch. | H5 |
| Custom speed-dependent `depletion_fn` in `_find_reachable_charger` | No test with a non-default depletion function that depends on speed. | M4 |
| Post-mission `_charge_agv` with insufficient energy for nearest charger | No test where nearest charger is beyond remaining range after mission. | M5 |
| `DefaultIntralogisticsCollector.inventory_ts` structure | Tests validate flat-tuple schema but don't verify (or document deviation from) spec's dict-of-lists. | M7 |
| `EMAOrderMetrics` initialization bias | No test for EMA behavior on the first record. | M9 |
| `submit()` before `env.run()` under ResourceBasedTrafficManager | No test exercises submit-before-run timing. | M11 |
| SKU identity vs equality in `ReorderPointPolicy` | No test using separately-constructed equal SKU instances. | L1 |
| Empty-travel battery stranding (first travel leg) | No test hitting `fleet.py:331-332`. | L6 |
| Repositioning battery stranding | No test hitting `fleet.py:418`. | L6 |
| ResumeDelivery `MISSION_FAILED` during re-travel | No test hitting `fleet.py:453-454`. | L6 |
| Alt-path `delay_until` handling | No test hitting `fleet.py:555-559`. | L6 |
| `_enter_with_timeout` with unstarted `enter_proc` | No test for interrupted process before SimPy scheduler starts it. | M3 |
| Concurrent cancellation of multiple orders | No test cancelling 2+ active orders simultaneously. | — |

---

## Areas With No Issues Found

- **SKU model** — Correct, frozen, hashable, `get_attribute` works. ✅
- **Graph layer** (Node, Arc, LayoutGraph) — Correct, immutable API, `shortest_path` delegates properly. ✅
- **Pathfinding** (DijkstraPlanner, AStarPlanner) — Correct, handles avoid nodes, same-node case. ✅
- **Battery** — Correct, clamping works, thresholds computed properly. ✅
- **SpeedProfile / TrapezoidalProfile** — Correct trapezoidal/triangular profile math, battery/load factor application. ✅
- **AGV state tracking / utilization** — Correct `_flush_current_state`, `time_allocation` sums to 1.0. ✅
- **Warehouse** — Correct deadlock-safe pick/put ordering (inventory first, then slot). ✅
- **ChargingStation** — Swap pool correctly guarded, `supports_swap=True` with `swap_pool_size=0` blocks correctly. ✅
- **ParkingArea** — Correct enter/leave lifecycle, per-AGV request tracking. ✅
- **OrderStatus / TransferOrder** — All 8 statuses present, correct dataclass fields. ✅
- **Import audit** — Clean, no forbidden imports from production-oriented modules. ✅
- **Experimental cleanup** — Complete, only `gymnasium.py` remains in `simulatte.experimental`. ✅
- **Logging** — Component tags present on all components, `disable_component` filtering works. ✅
- **`delay_until` handling in `_travel()`** — Correctly waits and re-checks path feasibility. ✅
- **`on_agv_state_changed` time-series integration** — `_transition_agv` wrapper fires collector on every state change. ✅
- **Charging hook signatures** — Correctly `(AGV, ChargingStation)` per spec §9. ✅
- **`on_low_battery` override semantics** — Correctly overrides default charging when callback returns a generator, falls through to default on `None`. ✅
- **Event-driven replenishment timing** — Correctly triggers after pick completion, not after delivery. ✅
- **`NearestIdleStrategy` per-AGV bay evaluation** — Correctly computes nearest output bay per candidate AGV. ✅
- **Critical battery interruption** — `is_critical` check fires after each arc, returns `RETRY_FROM_CURRENT_POSITION`, retry loop charges. ✅
- **Deadlock layers 2 and 3** — `_enter_with_timeout` implements reroute (layer 2) and priority-based backoff (layer 3). ✅
- **`_TravelOutcome` separation from `AGVState.STRANDED`** — Routing/traffic failures return `MISSION_FAILED`, only genuine battery failures return `BATTERY_STRANDED`. ✅
