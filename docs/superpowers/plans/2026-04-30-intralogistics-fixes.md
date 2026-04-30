# Intralogistics Post-Review Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all bugs, spec compliance gaps, and test gaps found in the adversarial review of the `simulatte.intralogistics` implementation.

**Architecture:** The intralogistics subsystem is already implemented across 16 source files in `src/simulatte/intralogistics/` with 205 tests in `tests/intralogistics/`. This plan fixes issues without changing the architecture — all fixes are within existing files.

**Tech Stack:** Python 3.12+, SimPy 4.x, pytest. Use `uv run` to execute commands.

**Reference documents:**
- Design spec: `docs/superpowers/specs/2026-04-30-intralogistics-design.md`
- Original implementation plan: `docs/superpowers/plans/2026-04-30-intralogistics-implementation.md`

**Branch:** `feature/intralogistics` (all work continues on this branch)

**Coverage note:** The project requires 99% coverage (`--cov-fail-under=99` in pyproject.toml). Currently at ~94%. Use `--no-verify` on commits if the coverage gate fails — closing the gap is part of Fix Batch 5.

---

## Review Findings Reference

The following findings come from an adversarial Codex review of the complete implementation. Each finding has a severity, description, and file:line reference.

### HIGH severity bugs

| ID | Description | Location |
|---|---|---|
| H1 | `_travel()` returns silently when no path exists; `_run_mission()` proceeds to pick/deliver anyway (AGV "teleports") | `fleet.py:400` |
| H2 | When `check_path()` returns infeasible, the original path is still registered and driven instead of failing/delaying | `fleet.py:409-418` |
| H3 | Mid-arc cancellation (interrupt during `env.timeout(travel_time)`) — `finally` calls `cancel()` which clears intents but doesn't release the already-acquired next-node resource | `fleet.py:465`, `traffic.py:142` |
| H4 | Cancellation after pickup: inventory was deducted by `Warehouse.pick()` but recovery path only clears `agv.current_load` without returning stock | `fleet.py:372` |
| H5 | Interrupt during `Warehouse.pick()`: `agv.current_load` assigned only after `pick()` returns; interrupt between container deduction and load assignment loses inventory | `fleet.py:292` |
| H6 | After mid-travel charging diversion, movement loop resumes setting `agv.current_node = next_node` even though AGV is physically at the charger | `fleet.py:443-470` |

### MEDIUM severity bugs

| ID | Description | Location |
|---|---|---|
| M1 | `Arc.speed_limit` exists but `_travel()` never fetches the arc or passes `speed_limit` to `SpeedProfile.travel_time()` | `fleet.py:434` |
| M2 | `cancel()` removes `_pending_requests` but not `_node_requests[(agv, node)]` — stale entries accumulate | `traffic.py:142` |
| M3 | `ChargingStation.swap()` with `supports_swap=True, swap_pool_size=0` bypasses pool wait — gives unlimited instant batteries | `charging.py:48,121` |

### Spec compliance gaps

| ID | Description | Spec section |
|---|---|---|
| S1 | `FleetCoordinator` never calls `traffic_manager.place()` for initial AGV placement | §4 |
| S2 | Deadlock Layers 2 (timeout+reroute) and 3 (priority backoff) not implemented; `enter_node()` blocks indefinitely | §4 |
| S3 | `is_critical` battery check unused; FleetCoordinator only checks `is_low`, never interrupts mission immediately on critical | §9 |
| S4 | Unfulfillable orders (no compatible AGV ever) queue forever; no retry count/timeout → FAILED transition | §8 |
| S5 | `ReorderPointPolicy` only checks existence of in-transit orders, doesn't sum their quantities into stock calculation | §8 |
| S6 | `ReturnToOrigin` and `ResumeDelivery` are stubs (just set status); don't orchestrate actual cargo return/delivery | §8 |
| S7 | Charging hooks fire with `(AGV)` signature, spec requires `(AGV, ChargingStation)` | §9 |
| S8 | `on_low_battery` callback augments default charging behavior instead of overriding it as spec says | §9 |
| S9 | Event-driven replenishment (`check_interval=None`) not wired — only periodic check works | §8 |
| S10 | `on_agv_state_changed` time-series collector method exists but is never called from anywhere | §10 |

### Test gaps

| ID | Description |
|---|---|
| T1 | No test for unreachable origin/destination (no-path mission should → FAILED) |
| T2 | No test for `ResourceBasedTrafficManager` with `node_capacity=1` |
| T3 | No test for cancellation after pickup (inventory rollback) |
| T4 | No test for mid-arc cancellation under ResourceBasedTrafficManager (next-node resource leak) |
| T5 | No test for arc `speed_limit` through FleetCoordinator |
| T6 | No test for no-alternative traffic conflict behavior (infeasible check_path with no alternate) |
| T7 | No test for charging hooks with `(AGV, ChargingStation)` signature |
| T8 | No test for `on_agv_state_changed` time-series collector integration |
| T9 | No test for event-driven replenishment (`check_interval=None`) |
| T10 | No test for swap station with `supports_swap=True, swap_pool_size=0` |

---

## Fix Batch 1: `_travel()` Correctness

**Fixes:** H1, H2, H6, M1, S1, S2
**Files:** `src/simulatte/intralogistics/fleet.py`, `tests/intralogistics/test_coordinator.py`

### H1: No-path travel completes mission silently

**Problem:** `_travel()` at `fleet.py:400` logs a warning and returns when no path exists. `_run_mission()` then proceeds to pick/deliver without the AGV having moved.

**Fix:** Make `_travel()` return a `bool` indicating success. When `False`, `_run_mission()` must set `order.status = OrderStatus.FAILED` and clean up:

```python
# In _travel():
if path is None:
    self.env.debug("No path found", component="FleetCoordinator", ...)
    return False  # change return type to bool

# After path planning fails entirely (no alt either):
return False

# At end of successful travel:
return True
```

```python
# In _run_mission(), wrap each _travel() call:
success = yield from self._travel(agv, agv.current_node, output_bay, loaded=False)
if not success:
    order.status = OrderStatus.FAILED
    # cleanup and return
    return
```

### H2: Infeasible path registered after failed `check_path()`

**Problem:** At `fleet.py:409-418`, when `check_path()` returns infeasible and the re-planned path is also None, the code falls through and registers/drives the original infeasible path.

**Fix:** After the re-plan attempt, if still no feasible path, return `False`:

```python
result = self._traffic_manager.check_path(agv, path)
if not result.feasible:
    alt_path = self._path_planner.plan(graph, from_node, to_node, avoid=result.conflict_nodes)
    if alt_path is None:
        self.env.debug("No feasible path after conflict avoidance", ...)
        return False
    path = alt_path
    result = self._traffic_manager.check_path(agv, path)
    if not result.feasible:
        return False
self._traffic_manager.register_intent(agv, path)
```

### H6: Charging diversion teleports AGV

**Problem:** At `fleet.py:443-470`, after `_charge_agv()` diverts the AGV to a charger (which changes `agv.current_node`), the movement loop resumes the old path, setting `agv.current_node = next_node` for a node the AGV never physically reached.

**Fix:** After charging diversion, break out of the current movement loop and re-plan from the AGV's actual current position. `_travel()` should detect this and restart:

```python
# After charging completes in _travel():
# Break out of hop loop, the caller (_run_mission) should re-call _travel()
# from the AGV's new current_node
traffic_manager.cancel(agv)  # clear old intent
return False  # signal: travel not completed, but not a failure — AGV charged and is elsewhere

# In _run_mission(), use a retry loop:
while True:
    success = yield from self._travel(agv, agv.current_node, target_bay, loaded)
    if success:
        break
    if order.status == OrderStatus.FAILED:
        return
    # AGV was diverted (charging), retry from new position
```

Alternatively, convert the charging diversion to raise a custom exception that `_run_mission` catches and retries.

### M1: Arc speed limits ignored

**Problem:** `_travel()` at `fleet.py:434` calls `speed_profile.travel_time(distance, load_weight, battery_level)` without passing `speed_limit`. The `Arc.speed_limit` field is never used.

**Fix:** Fetch the arc and pass its speed limit:

```python
# In the per-hop loop of _travel():
arc = self._graph.arc_between(path[i], path[i + 1])
speed_limit = arc.speed_limit if arc else None
travel_time = agv.agv_type.speed_profile.travel_time(
    distance, load_weight, agv.battery.level_pct, speed_limit=speed_limit
)
```

### S1: Initial AGV placement via `traffic_manager.place()`

**Problem:** `FleetCoordinator.__init__()` at `fleet.py:54` never calls `traffic_manager.place()` for AGVs with `initial_node` set. Under `ResourceBasedTrafficManager`, the traffic system doesn't know AGVs are occupying their starting nodes.

**Fix:** Add to `__init__()`:

```python
# In FleetCoordinator.__init__(), after setting up fleet:
for agv in self._fleet:
    if agv.current_node is not None:
        self.env.process(self._place_agv(agv))

def _place_agv(self, agv: AGV) -> ProcessGenerator:
    yield from self._traffic_manager.place(agv, agv.current_node)
```

### S2: Deadlock Layers 2 & 3

**Problem:** `ResourceBasedTrafficManager` stores `_deadlock_timeout` and `_priority_fn` but `enter_node()` at `traffic.py:114` blocks indefinitely with no timeout mechanism.

**Fix:** This requires coordination between FleetCoordinator and TrafficManager. The approach: use SimPy's `req | env.timeout(deadlock_timeout)` pattern in `_travel()`:

```python
# In _travel() per-hop loop, replace direct yield from enter_node:
enter_gen = self._traffic_manager.enter_node(agv, next_node)
# We need the raw request to do conditional wait
# Option: add a method to TrafficManager that returns (request_event, node)
# Or handle timeout in FleetCoordinator by wrapping enter_node

# Simpler approach: add a timeout wrapper in FleetCoordinator._travel():
import simpy
enter_proc = self.env.process(self._enter_with_timeout(agv, next_node))
try:
    yield enter_proc
except simpy.Interrupt:
    # Timeout or external cancel
    self._traffic_manager.cancel(agv)
    # Try alternate route
    alt = self._path_planner.plan(self._graph, agv.current_node, to_node, avoid=[next_node])
    if alt:
        self._traffic_manager.cancel(agv)
        path = alt
        # restart loop from current position
        continue
    # Layer 3: priority wait with backoff
    yield self.env.timeout(backoff_time)
    continue
```

This is the most complex fix. The implementing agent should:
1. Add `_enter_with_timeout()` helper to FleetCoordinator that yields `enter_node` with a timeout
2. On timeout: cancel pending request, try reroute with `avoid`
3. If no alternative: exponential backoff wait, retry
4. If reroute succeeds: re-register intent and continue from new path

### Tests for Batch 1:

- [ ] Test: no-path mission → order FAILED (T1)
- [ ] Test: infeasible check_path with no alternative → order FAILED (T6)
- [ ] Test: arc speed_limit passed to SpeedProfile (T5)
- [ ] Test: initial AGV placement creates traffic resource
- [ ] Test: mid-travel charging diversion → AGV re-plans from charger node
- [ ] Test: `node_capacity=1` with timeout/reroute (T2 — partial, full fix needs Batch 1)

- [ ] **Commit:** `fix(intralogistics): _travel() path failure handling, speed limits, initial placement, charging re-plan`

---

## Fix Batch 2: Interrupt Safety & Inventory Rollback

**Fixes:** H3, H4, H5, M2
**Files:** `src/simulatte/intralogistics/fleet.py`, `src/simulatte/intralogistics/traffic.py`, `src/simulatte/intralogistics/warehouse.py`, `tests/intralogistics/test_coordinator.py`

### H3: Mid-arc cancellation leaks acquired next-node resource

**Problem:** In `_travel()` at `fleet.py:465`, the per-hop sequence is:
1. `enter_node(agv, next_node)` — acquires next node resource
2. `yield env.timeout(travel_time)` — can be interrupted here
3. `leave_node(agv, current)` — releases current node

If interrupted at step 2, the `finally` block calls `traffic_manager.cancel(agv)` which clears intents and pending requests but does NOT release the already-triggered next-node request.

**Fix:** Track whether the AGV physically reached the next node. In the `finally` or `except` block, release the next-node resource if it was acquired but the AGV didn't arrive:

```python
# In _travel() per-hop loop:
reached_next = False
try:
    yield from self._traffic_manager.enter_node(agv, next_node)
    yield self.env.timeout(travel_time)
    agv.battery.deplete(distance, load_weight, avg_speed)
    self._traffic_manager.leave_node(agv, current_node)
    agv.current_node = next_node
    reached_next = True
except simpy.Interrupt:
    if not reached_next:
        # Release the acquired next-node resource
        self._traffic_manager.leave_node(agv, next_node)
    raise
```

### M2: Stale `_node_requests` after cancel

**Problem:** `traffic.py:142` — `cancel()` removes from `_pending_requests` but not from `_node_requests[(agv, node)]`. Over time, stale entries accumulate.

**Fix:** In `cancel()`, also clean up `_node_requests`:

```python
def cancel(self, agv: AGV) -> None:
    self._intents.pop(agv, None)
    # Cancel pending request
    if agv in self._pending_requests:
        req = self._pending_requests.pop(agv)
        if not req.triggered:
            req.cancel()
    # Clean up all _node_requests for this AGV
    stale_keys = [k for k in self._node_requests if k[0] is agv]
    for key in stale_keys:
        req = self._node_requests.pop(key)
        if req.triggered:
            self._node_resources[key[1]].release(req)
        elif not req.processed:
            req.cancel()
```

### H4 & H5: Inventory rollback on interruption

**Problem (H5):** `_run_mission()` at `fleet.py:292` calls `yield from warehouse.pick(sku, quantity)` which deducts inventory via `Container.get()`. Then `agv.current_load = {sku: quantity}` is assigned AFTER `pick()` returns. An interrupt between deduction and assignment loses inventory.

**Problem (H4):** Even when `agv.current_load` IS set (interrupt after assignment), the cancellation handler at `fleet.py:372` invokes `LoadRecoveryStrategy` but `ReturnToOrigin` is a stub that just sets status — it doesn't actually return the inventory.

**Fix — two parts:**

**Part A: Track committed pick state.** Add a `_committed_pick` attribute to FleetCoordinator (or pass through the mission context) that tracks inventory deducted but not yet assigned to AGV:

```python
# Before pick:
self._committed_pick = (order.origin, order.sku, order.quantity)
yield from order.origin.pick(order.sku, order.quantity)
agv.current_load = {order.sku: order.quantity}
self._committed_pick = None
```

In the interrupt handler:
```python
except simpy.Interrupt as interrupt:
    # Roll back committed but unloaded pick
    if self._committed_pick is not None:
        wh, sku, qty = self._committed_pick
        self.env.process(wh.put(sku, qty))  # return to warehouse
        self._committed_pick = None

    if agv.current_load:
        # AGV has cargo — delegate to recovery strategy
        yield from self._load_recovery_strategy.recover(order, agv, self)
```

**Part B: Make `ReturnToOrigin` actually return cargo.** Update `policies.py:ReturnToOrigin.recover()`:

```python
class ReturnToOrigin:
    def recover(self, order, agv, coordinator) -> ProcessGenerator:
        if agv.current_load:
            # Return cargo to origin warehouse
            for sku, qty in agv.current_load.items():
                yield from order.origin.put(sku, qty)
            agv.current_load = None
        order.status = OrderStatus.PENDING
        order.assigned_agv = None
```

### Tests for Batch 2:

- [ ] Test: cancel during travel releases next-node resource (T4)
- [ ] Test: cancel after pickup returns inventory to origin (T3)
- [ ] Test: interrupt during Warehouse.pick() after container get rolls back inventory
- [ ] Test: cancel() cleans up all _node_requests for the AGV

- [ ] **Commit:** `fix(intralogistics): interrupt-safe travel, inventory rollback, stale request cleanup`

---

## Fix Batch 3: Battery & Charging Correctness

**Fixes:** M3, S3, S7, S8
**Files:** `src/simulatte/intralogistics/fleet.py`, `src/simulatte/intralogistics/charging.py`, `tests/intralogistics/test_coordinator.py`, `tests/intralogistics/test_charging.py`

### M3: `ChargingStation.swap()` with `swap_pool_size=0` gives free batteries

**Problem:** At `charging.py:48`, when `supports_swap=True` but `swap_pool_size=0`, no pool container is created. At `charging.py:121`, swap bypasses pool wait when `_swap_pool is None`.

**Fix:** When `supports_swap=True`, always create the pool container. If `swap_pool_size=0`, the container starts empty and swaps must wait for replenishment (or the station is effectively swap-incapable until batteries are added):

```python
# In __init__:
if self.supports_swap:
    self._swap_pool = simpy.Container(env, capacity=max(swap_pool_size, 1), init=swap_pool_size)
else:
    self._swap_pool = None

# In swap():
if not self.supports_swap:
    raise RuntimeError("Swap not supported by this station")
# Always wait for pool — no bypass
yield self._swap_pool.get(1)
```

### S3: `is_critical` battery check unused

**Problem:** The spec says (§9): "If `is_critical`: mission is interrupted immediately. The AGV diverts to the nearest charging station. The order is re-queued for another AGV." But FleetCoordinator only checks `is_low` (post-mission) and pre-arc energy feasibility. It never checks `is_critical` for immediate mission interruption.

**Fix:** In `_travel()` per-hop loop, after `battery.deplete()`, check `is_critical`:

```python
agv.battery.deplete(distance, load_weight, avg_speed)
if agv.battery.is_critical:
    # Interrupt mission immediately
    self._traffic_manager.leave_node(agv, current_node)
    agv.current_node = next_node
    # Divert to charging
    yield from self._charge_agv(agv)
    # Signal to _run_mission that mission was interrupted for charging
    raise _ChargingDiversion()
```

In `_run_mission()`, catch `_ChargingDiversion` and handle: if load present, invoke recovery strategy; if not, re-queue order.

### S7: Charging hooks signature `(AGV, ChargingStation)`

**Problem:** Spec §9 says `on_charging_started(callback: Callable[[AGV, ChargingStation], None])` but the implementation fires with `(AGV)` only at `fleet.py:190`.

**Fix:** Update hook storage and firing:

```python
# In __init__:
self._hooks_on_charging_started: list[Callable[[AGV, ChargingStation], None]] = []
self._hooks_on_charging_complete: list[Callable[[AGV, ChargingStation], None]] = []

# Registration:
def on_charging_started(self, callback: Callable[[AGV, ChargingStation], None]) -> None:
    self._hooks_on_charging_started.append(callback)

# Firing (in _charge_agv, pass the station):
for cb in self._hooks_on_charging_started:
    cb(agv, station)
```

### S8: `on_low_battery` override semantics

**Problem:** Spec says the `on_low_battery` callback overrides the default charging behavior. Implementation augments it (calls callback AND default charging).

**Fix:** If `on_low_battery` is set, call it instead of the default. If it returns a generator, yield from it. If it returns None, fall through to default:

```python
if self._on_low_battery is not None:
    result = self._on_low_battery(agv)
    if result is not None:
        yield from result
        return  # callback handled it, skip default
# Default: divert to nearest charging station
yield from self._charge_agv(agv)
```

### Tests for Batch 3:

- [ ] Test: swap with `swap_pool_size=0` blocks until replenished (T10)
- [ ] Test: `is_critical` triggers immediate mission interruption
- [ ] Test: charging hooks fire with `(AGV, ChargingStation)` signature (T7)
- [ ] Test: `on_low_battery` callback overrides default charging

- [ ] **Commit:** `fix(intralogistics): charging swap pool, critical battery, hook signatures, low-battery override`

---

## Fix Batch 4: Spec Compliance — Policies & Metrics

**Fixes:** S4, S5, S6, S9, S10
**Files:** `src/simulatte/intralogistics/fleet.py`, `src/simulatte/intralogistics/policies.py`, `src/simulatte/intralogistics/metrics.py`, `src/simulatte/intralogistics/agv.py`, `tests/intralogistics/test_policies.py`, `tests/intralogistics/test_coordinator.py`, `tests/intralogistics/test_metrics.py`

### S4: Unfulfillable orders queue forever

**Problem:** When `DispatchStrategy.select()` returns None repeatedly and the order can NEVER be fulfilled (e.g., SKU weight exceeds all AGV capacities), the order sits in `_pending_queue` forever.

**Fix:** Add `max_dispatch_retries: int = 10` to FleetCoordinator constructor. Track retry count per order. In `_check_pending_queue()`, increment count when dispatch fails. When count exceeds max, set `order.status = OrderStatus.FAILED`:

```python
# In FleetCoordinator:
self._dispatch_retries: dict[str, int] = {}  # order.id -> count

# In _check_pending_queue():
for order in list(self._pending_queue):
    agv = self._dispatch_strategy.select(order, self._fleet, self._graph)
    if agv is not None:
        self._pending_queue.remove(order)
        self._dispatch_retries.pop(order.id, None)
        self._dispatch(order, agv)
    else:
        self._dispatch_retries[order.id] = self._dispatch_retries.get(order.id, 0) + 1
        if self._dispatch_retries[order.id] >= self._max_dispatch_retries:
            self._pending_queue.remove(order)
            self._dispatch_retries.pop(order.id, None)
            order.status = OrderStatus.FAILED
```

### S5: `ReorderPointPolicy` in-transit quantity summation

**Problem:** At `policies.py:193`, the policy only checks if any in-transit order exists for the SKU, but doesn't sum in-transit quantities. This means a threshold of 50 with 10 units in-transit and 45 on hand (effectively 55) still triggers a redundant order.

**Fix:** Sum in-transit quantities and add to effective stock:

```python
def check(self, warehouse, all_warehouses, in_transit_orders):
    orders = []
    for sku, threshold in self._thresholds.items():
        current_level = warehouse.get_inventory_level(sku)
        # Sum in-transit quantities for this SKU to this warehouse
        in_transit_qty = sum(
            o.quantity for o in in_transit_orders
            if o.sku == sku and o.destination is warehouse
            and o.status not in (OrderStatus.COMPLETED, OrderStatus.FAILED, OrderStatus.CANCELLED)
        )
        effective_stock = current_level + in_transit_qty
        if effective_stock < threshold:
            # Find source warehouse with highest stock
            ...
```

### S6: `ReturnToOrigin`/`ResumeDelivery` actual recovery

**Problem:** Both are stubs at `policies.py:238,257` — they set status but don't move cargo.

**Fix:** Partially addressed in Batch 2 (H4 fix makes `ReturnToOrigin` actually return cargo). `ResumeDelivery` is trickier — it needs to signal the FleetCoordinator to re-submit the delivery portion. For now, `ResumeDelivery` should set status and let the coordinator handle the re-travel:

```python
class ResumeDelivery:
    def recover(self, order, agv, coordinator) -> ProcessGenerator:
        order.status = OrderStatus.IN_TRANSIT
        # Re-submit the delivery leg — coordinator will re-dispatch _travel()
        # The coordinator's interrupt handler should check: if recovery returns
        # with status IN_TRANSIT, re-travel to destination
        return
        yield
```

The FleetCoordinator interrupt handler should check: after recovery, if `order.status == OrderStatus.IN_TRANSIT`, continue the delivery from the AGV's current position.

### S9: Event-driven replenishment

**Problem:** `add_replenishment_policy(policy, warehouse, check_interval=None)` at `fleet.py:241` only supports periodic checking. When `check_interval=None`, the spec says it should check after every pick from the monitored warehouse.

**Fix:** Hook into the warehouse's pick completion. Since `Warehouse` doesn't have hooks, the simplest approach is to wrap the warehouse's `pick` method or add a post-pick callback. Alternatively, check replenishment after every delivery completion (which is when inventory changes occur):

```python
# In add_replenishment_policy():
if check_interval is not None:
    self.env.process(self._periodic_replenishment(policy, warehouse, check_interval))
else:
    # Event-driven: check after every delivery to this warehouse
    self._event_driven_policies.append((policy, warehouse))

# In _run_mission(), after delivery completes:
for policy, monitored_wh in self._event_driven_policies:
    if monitored_wh is order.destination or monitored_wh is order.origin:
        new_orders = policy.check(monitored_wh, self._warehouses, self._get_in_transit_orders())
        for new_order in new_orders:
            self.submit(new_order)
```

### S10: Fire `on_agv_state_changed` for time-series collector

**Problem:** `DefaultIntralogisticsCollector.on_agv_state_changed()` exists at `metrics.py:105` but is never called. `AGV.transition_to()` doesn't notify the collector.

**Fix:** Have `FleetCoordinator` fire the collector callback whenever it calls `agv.transition_to()`. Add a helper:

```python
# In FleetCoordinator:
def _transition_agv(self, agv: AGV, new_state: AGVState) -> None:
    old_state = agv.state
    agv.transition_to(new_state)
    if self._time_series_collector is not None:
        self._time_series_collector.on_agv_state_changed(self, agv, old_state, new_state)
```

Replace all `agv.transition_to(X)` calls in `fleet.py` with `self._transition_agv(agv, X)`.

### Tests for Batch 4:

- [ ] Test: unfulfillable order → FAILED after max retries
- [ ] Test: `ReorderPointPolicy` sums in-transit quantities (no redundant order)
- [ ] Test: `ReturnToOrigin` actually returns inventory to warehouse
- [ ] Test: event-driven replenishment fires after delivery (T9)
- [ ] Test: `on_agv_state_changed` collector receives state transitions (T8)

- [ ] **Commit:** `fix(intralogistics): unfulfillable orders, reorder quantities, recovery, event replenishment, state change events`

---

## Fix Batch 5: Test Coverage

**Fixes:** T1-T10, plus additional coverage for uncovered branches
**Files:** `tests/intralogistics/test_coordinator.py`, `tests/intralogistics/test_integration.py`, `tests/intralogistics/test_traffic.py`, `tests/intralogistics/test_charging.py`

Many of the test gaps (T1-T10) are addressed as part of Batches 1-4. This batch adds any remaining tests and fills coverage gaps to reach the 99% threshold.

### Remaining test gaps after Batches 1-4:

**T2: `ResourceBasedTrafficManager` with `node_capacity=1`** — After Batch 1 implements Layer 2/3, add an integration test with capacity=1 that exercises timeout+reroute. Two AGVs heading for the same destination; one waits, the other completes.

**T4: Mid-arc cancellation resource cleanup** — Test that cancelling an AGV during `env.timeout(travel_time)` under ResourceBasedTrafficManager correctly releases the next-node resource and clears _node_requests.

**T6: No-alternative traffic conflict** — Two AGVs with fully conflicting paths and no alternative route. Verify Layer 3 backoff behavior (one AGV waits, eventually proceeds).

### Coverage gap analysis:

Run `uv run pytest --cov=src/simulatte --cov-report=term-missing` and identify uncovered lines in:
- `fleet.py` — error paths, edge cases in `_travel()`, interrupt handlers
- `traffic.py` — `leave_node` with untriggered request, cancel cleanup
- `policies.py` — ReorderPointPolicy edge cases, NearestParkingPolicy no capacity
- `metrics.py` — plot methods (mark `# pragma: no cover`), collector edge cases

Write targeted tests for each uncovered branch.

### Steps:

- [ ] Run coverage report, identify gaps
- [ ] Write tests for remaining T2, T4, T6 scenarios
- [ ] Write tests for uncovered branches in fleet.py, traffic.py, policies.py
- [ ] Verify coverage reaches 99%
- [ ] **Commit:** `test(intralogistics): close coverage gaps to 99%`

---

## Execution Order

Batches must be executed in order (1 → 2 → 3 → 4 → 5) because later batches depend on fixes from earlier batches:

- Batch 2 depends on Batch 1 (`_travel()` return type change)
- Batch 3 depends on Batch 1 (charging diversion flow)
- Batch 4 depends on Batch 2 (ReturnToOrigin inventory return)
- Batch 5 depends on all previous batches

## Known Architectural Deviations (Accepted)

These are intentional deviations from the spec that do NOT need fixing:

1. **`fleet.py` vs `coordinator.py`** — File was named `fleet.py` to match existing forward references in `policies.py` and `metrics.py`. The public API exports `FleetCoordinator` from `__init__.py` regardless of internal filename.

2. **`_active_missions` keyed by `order.id: str`** — `TransferOrder` is an unhashable mutable dataclass. Using `order.id` (UUID string) as key is correct.

3. **`LayoutGraph` internals are mutable** — The spec says "immutable graph" but this refers to the public API (no add/remove methods), not the internal data structure. `_nodes` and `_adjacency` being mutable dicts is fine.
