# Intralogistics Review Follow-up Patch Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the remaining correctness and spec-compliance issues found during branch review of `feature/intralogistics`.

**Scope:** This plan covers the unresolved issues identified after comparing the implementation in `src/simulatte/intralogistics/` against:
- `docs/superpowers/specs/2026-04-30-intralogistics-design.md`
- `docs/superpowers/plans/2026-04-30-intralogistics-implementation.md`
- `docs/superpowers/plans/2026-04-30-intralogistics-fixes.md`

## Findings This Plan Will Tackle

The current branch is in good shape overall and the existing intralogistics test suite passes, but six important issues remain.

### 1. Healthy AGVs are incorrectly marked `STRANDED` for routing/traffic failures
`FleetCoordinator._travel()` currently transitions AGVs to `AGVState.STRANDED` not only for battery-related cases, but also when:
- no path exists,
- a traffic conflict has no alternative path,
- deadlock timeout retries are exhausted.

This is too broad. Per the design spec, `STRANDED` is a battery-health state: it means the AGV cannot reach any charging station with the energy it has left. A missing path or temporary traffic blockage should fail or defer the mission, but should not permanently poison the AGV’s lifecycle state. As implemented, one bad order or one blocked route can remove an otherwise healthy AGV from future use.

### 2. Deadlock timeout handling is still below spec
The current timeout path retries entering the same blocked node a fixed number of times with exponential backoff, then gives up. This does not implement the spec’s intended layered behavior:
- Layer 2: reroute around the blocked node when possible
- Layer 3: priority-based waiting/backoff when rerouting is impossible

The code also stores `priority_fn` on `ResourceBasedTrafficManager` but never uses it. This means congestion scenarios can still degrade into hard failures instead of controlled waiting/recovery.

### 3. Some unfulfillable orders can remain pending forever
`max_dispatch_retries` is only advanced when `_check_pending_queue()` runs, and that method is only called after mission cleanup. If an order is unfulfillable immediately at submit time and no later mission completion occurs, the order can remain in `_pending_queue` forever. This violates the intended behavior that unfulfillable work should eventually become `FAILED` rather than linger indefinitely.

### 4. `NearestIdleStrategy` uses the wrong comparison point for multi-bay warehouses
The strategy currently computes a single origin output bay based on the first candidate AGV, then measures all AGVs against that same bay. For warehouses with multiple output bays, each AGV should instead be evaluated against its own nearest output bay. The current approach can select a farther AGV and degrade dispatch quality.

### 5. Event-driven replenishment is triggered too late
The spec says `add_replenishment_policy(..., check_interval=None)` should check the monitored warehouse after every pick from that warehouse. The current implementation performs the event-driven check only after delivery completion involving that warehouse. That delays replenishment generation by an entire transport cycle and can distort inventory behavior.

### 6. `delay_until` from `TrafficManager.check_path()` is ignored
The traffic protocol explicitly allows `check_path()` to report an infeasible path together with a suggested `delay_until`. The current `_travel()` logic only reacts to `conflict_nodes`; when `delay_until` is provided without reroute candidates, it falls through incorrectly instead of waiting. This makes the coordinator incompatible with traffic managers that rely on delay-based coordination.

---

## Implementation Strategy

Execute in this order:
1. Fix mission/AGV state semantics for routing failures
2. Bring deadlock handling closer to spec
3. Fix pending-order failure progression
4. Fix dispatch strategy distance evaluation
5. Rewire event-driven replenishment to pick-time
6. Add support for `delay_until`
7. Add/adjust tests for each scenario

---

## Batch 1: Separate Mission Failure From Battery Stranding

**Fixes:** Finding 1  
**Files:**
- `src/simulatte/intralogistics/fleet.py`
- `tests/intralogistics/test_coordinator.py`
- `tests/intralogistics/test_integration.py`

### Objective
Ensure routing/traffic failures fail or defer the mission without incorrectly setting the AGV to `STRANDED`.

### Required changes

- [ ] Introduce explicit travel outcomes in `fleet.py` instead of overloading `AGVState.STRANDED` as the only failure signal.

Recommended shape:
```python
class _TravelOutcome(Enum):
    ARRIVED = auto()
    RETRY_FROM_CURRENT_POSITION = auto()
    MISSION_FAILED = auto()
    BATTERY_STRANDED = auto()
```

Alternative: return a small dataclass/NamedTuple with `success`, `retry`, `battery_stranded`, `reason`.

- [ ] Update `_travel()` to return the richer outcome instead of `bool`.

Cases:
- same node → `ARRIVED`
- pre-arc charging diversion → `RETRY_FROM_CURRENT_POSITION`
- no path / infeasible path / deadlock unresolved → `MISSION_FAILED`
- cannot reach charger due to battery → `BATTERY_STRANDED`
- arrival at destination → `ARRIVED`

- [ ] Restrict `AGVState.STRANDED` transitions to genuine battery-stranding situations only.

Spec-aligned cases:
- insufficient battery for next movement
- no reachable charger from current node
- battery-related failure after charging attempt

- [ ] Update `_run_mission()` retry loops to interpret the richer travel outcome.

Expected handling:
- `ARRIVED` → continue mission
- `RETRY_FROM_CURRENT_POSITION` → rerun `_travel()` from new `agv.current_node`
- `MISSION_FAILED` → mark order `FAILED`, restore AGV to `IDLE`
- `BATTERY_STRANDED` → mark order `FAILED`, AGV remains `STRANDED`

- [ ] Ensure a no-path order does not permanently disable the AGV.

### Tests

- [ ] Add test: unreachable destination causes `order.status == FAILED` but AGV returns to `IDLE`
- [ ] Add test: infeasible traffic path with no alternative fails order but AGV is not `STRANDED`
- [ ] Add test: battery-only unreachable charger still yields `AGVState.STRANDED`

- [ ] **Commit:** `fix(intralogistics): separate mission routing failure from battery stranding`

---

## Batch 2: Bring Deadlock Handling Up To Spec

**Fixes:** Finding 2  
**Files:**
- `src/simulatte/intralogistics/fleet.py`
- `src/simulatte/intralogistics/traffic.py`
- `tests/intralogistics/test_coordinator.py`
- `tests/intralogistics/test_traffic.py`

### Objective
Implement spec-aligned timeout resolution: reroute first, then priority/backoff waiting when rerouting is impossible.

### Required changes

- [ ] Add a way for the coordinator to obtain/use priority information from the traffic manager.

Recommended minimal extension in `traffic.py`:
```python
class TrafficManager(Protocol):
    @property
    def deadlock_timeout(self) -> float | None: ...
    def priority(self, agv: AGV) -> float: ...
```

Implementation:
- `FreeTrafficManager.priority()` → return `0.0`
- `ResourceBasedTrafficManager.priority()` → delegate to stored `_priority_fn`

If you prefer not to extend the protocol, expose a helper only on `ResourceBasedTrafficManager` and branch conservatively in the coordinator.

- [ ] Replace `_enter_with_timeout()` fixed retry behavior with a two-stage resolution loop.

Recommended behavior:
1. Try to enter next node with timeout
2. On timeout:
   - cancel pending request
   - try reroute with `avoid=[blocked_node]`
   - if alternative route exists: cancel old intent, return a signal that travel should restart from current node on a new path
   - if no alternative exists: compare priorities and wait/back off instead of failing immediately

- [ ] Introduce an explicit result from `_enter_with_timeout()`.

Suggested shape:
```python
class _EnterOutcome(Enum):
    ENTERED = auto()
    REROUTE = auto()
    WAIT_AND_RETRY = auto()
    GAVE_UP = auto()
```

- [ ] Implement layer-2 rerouting in `_travel()`.

When blocked on `next_node` and timeout expires:
- cancel pending request
- compute alternate path from `agv.current_node` to final destination using `avoid=[next_node]`
- if found, cancel current intent and restart travel planning from current node

- [ ] Implement layer-3 priority-based wait/backoff.

Recommended conservative behavior:
- lower-priority AGV waits
- tie-break with AGV id for determinism
- use exponential backoff or `delay_until` if available
- do not mark mission failed unless an explicit terminal condition is reached

- [ ] Make use of `priority_fn` in a real scenario.

- [ ] Preserve interrupt safety and request cleanup while adding the above.

### Tests

- [ ] Add test: blocked next node triggers reroute when alternate path exists
- [ ] Add test: no alternate path causes lower-priority AGV to wait and later proceed
- [ ] Add test: `priority_fn` affects which AGV yields
- [ ] Add test: deadlock handling no longer strands a healthy AGV solely due to congestion

- [ ] **Commit:** `fix(intralogistics): implement reroute and priority backoff for deadlock resolution`

---

## Batch 3: Ensure Unfulfillable Orders Eventually Fail

**Fixes:** Finding 3  
**Files:**
- `src/simulatte/intralogistics/fleet.py`
- `tests/intralogistics/test_coordinator.py`

### Objective
Prevent permanently pending orders when no compatible dispatch is possible.

### Required changes

- [ ] Introduce an immediate or scheduled retry mechanism for pending orders, independent of mission completion.

Recommended approach:
```python
self._pending_retry_interval = 0.0  # or small configurable delay
self._pending_retry_scheduled = False
```

When an order is queued in `submit()`:
- schedule a pending-queue evaluation process if one is not already scheduled

- [ ] Add a helper process such as `_pending_retry_loop()` or `_schedule_pending_check()`.

Behavior options:
1. **Immediate next-tick retry**: `yield self.env.timeout(0)` then `_check_pending_queue()`
2. **Configurable polling retry**: e.g. every simulated time unit until queue empties

Option 2 is safer for truly static systems where nothing else changes.

- [ ] Ensure retries increment even if no active mission completes.

- [ ] Keep current successful cleanup-triggered `_check_pending_queue()` calls; they are still useful.

- [ ] Mark order `FAILED` once retry limit is exceeded, even in an otherwise idle simulation.

### Tests

- [ ] Add test: oversize/incompatible order fails after retry limit even if no other mission runs
- [ ] Add test: pending queue self-drives retry progression without requiring another order to complete

- [ ] **Commit:** `fix(intralogistics): make unfulfillable pending orders fail without external activity`

---

## Batch 4: Fix `NearestIdleStrategy` For Multi-Bay Warehouses

**Fixes:** Finding 4  
**Files:**
- `src/simulatte/intralogistics/policies.py`
- `tests/intralogistics/test_policies.py`

### Objective
Evaluate each AGV against its own nearest origin output bay.

### Required changes

- [ ] Update `NearestIdleStrategy.select()` so `_distance(agv)` computes:
```python
output_bay = order.origin.nearest_output_bay(agv.current_node, graph)
```
inside the AGV-specific scoring function, not once before the function.

- [ ] Keep current tie-break behavior by `agv_id`.

- [ ] Ensure no regression for single-bay warehouses.

### Tests

- [ ] Add test: with two output bays, strategy selects AGV nearest to its own nearest bay
- [ ] Add regression test: single-bay warehouse behavior unchanged
- [ ] Add tie-break test if needed to preserve deterministic ordering

- [ ] **Commit:** `fix(intralogistics): evaluate nearest idle agvs against per-agv nearest output bay`

---

## Batch 5: Trigger Event-Driven Replenishment After Picks

**Fixes:** Finding 5  
**Files:**
- `src/simulatte/intralogistics/fleet.py`
- `tests/intralogistics/test_coordinator.py`
- `tests/intralogistics/test_integration.py`

### Objective
Make `check_interval=None` replenishment react to pick events, as specified.

### Required changes

- [ ] Move event-driven replenishment evaluation from the post-delivery block to immediately after a successful pick/load from the origin warehouse.

Best insertion point: after
- `yield from order.origin.pick(...)`
- `agv.current_load = ...`
- pickup bookkeeping/hook emission

- [ ] Restrict the event-driven check to policies registered for the picked-from warehouse.

Expected shape:
```python
for policy, monitored_wh in self._event_driven_policies:
    if monitored_wh is order.origin:
        new_orders = policy.check(...)
        for new_order in new_orders:
            self.submit(new_order)
```

- [ ] Build `in_transit_orders` consistently using currently active missions excluding terminal states.

- [ ] Remove or narrow the current delivery-based trigger to avoid duplicate policy firing.

### Tests

- [ ] Add test: event-driven replenishment order is created immediately after pick, before delivery completes
- [ ] Add test: monitored destination-only warehouse does not trigger on unrelated delivery
- [ ] Add test: no duplicate replenishment order from one pick event

- [ ] **Commit:** `fix(intralogistics): trigger event-driven replenishment on pick completion`

---

## Batch 6: Honor `delay_until` From Traffic Checks

**Fixes:** Finding 6  
**Files:**
- `src/simulatte/intralogistics/fleet.py`
- `tests/intralogistics/test_coordinator.py`
- `tests/intralogistics/test_traffic.py`

### Objective
Make `FleetCoordinator` compatible with delay-oriented traffic managers.

### Required changes

- [ ] Expand `_travel()` path-feasibility handling.

Current behavior only branches on:
- `result.feasible`
- `result.conflict_nodes`

New behavior should support:
1. `feasible=True` → register intent and proceed
2. `feasible=False` + `conflict_nodes` → attempt reroute
3. `feasible=False` + `delay_until` → wait until that simulation time, then re-check
4. `feasible=False` + both → choose reroute first or delay first consistently; document behavior in code comments
5. `feasible=False` + neither → mission-level failure or controlled retry, but never silent fallthrough to register infeasible intent

- [ ] Add a helper for delay semantics if needed:
```python
def _wait_for_path_delay(self, delay_until: float) -> ProcessGenerator:
    wait = max(0.0, delay_until - self.env.now)
    if wait > 0:
        yield self.env.timeout(wait)
```

- [ ] Re-run `check_path()` after waiting; do not assume the old result is still valid.

- [ ] Ensure infeasible/no-guidance results do not fall through to `register_intent()`.

### Tests

- [ ] Add test with custom traffic manager returning `delay_until` only; coordinator should wait then proceed
- [ ] Add test with infeasible result lacking both delay and conflict nodes; mission should fail cleanly without registering the infeasible path
- [ ] Add test combining `delay_until` and `conflict_nodes` if you choose to support precedence explicitly

- [ ] **Commit:** `fix(intralogistics): honor delay-based traffic coordination results`

---

## Final Validation

- [ ] Run targeted tests for each modified area
```bash
uv run pytest \
  tests/intralogistics/test_coordinator.py \
  tests/intralogistics/test_policies.py \
  tests/intralogistics/test_traffic.py \
  tests/intralogistics/test_integration.py -v
```

- [ ] Run full intralogistics suite
```bash
uv run pytest tests/intralogistics -v
```

- [ ] Run repo test suite if practical
```bash
uv run pytest
```

- [ ] If coverage gates interfere with iterative commits, use `--no-verify` as already documented in the fixes plan.

- [ ] **Final commit:** `fix(intralogistics): address remaining review follow-ups`
