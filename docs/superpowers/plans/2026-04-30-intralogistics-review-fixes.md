# Intralogistics Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all confirmed findings from the consolidated intralogistics review (`docs/superpowers/reviews/2026-04-30-intralogistics-review.md`), covering 23 findings (H1, H2, H4, H5, M1–M11, L1–L3, L5–L8). H3 is closed as "working as designed" (spec does not require fleet-level reassignment). L4 is an accepted deviation.

**Architecture:** Fixes are grouped into 12 tasks ordered by dependency. Tasks 1–3 create shared infrastructure (public accessors, distance helper, protocol cleanup). Tasks 4–8 fix correctness bugs in the interrupt/recovery/retry paths. Tasks 9–12 fix strategies, metrics, and remaining items. Each task is a single commit.

**Tech Stack:** Python 3.12+, SimPy 4, pytest, uv

**Design decisions (locked with user):**
- **H3**: Closed. Current charge-same-AGV behavior is spec-compliant.
- **H1 + H4 recovery failure**: Recursive fallback chain — ResumeDelivery → ReturnToOrigin → drop cargo at current node.
- **M1**: Remove `deadlock_timeout`/`priority` from `TrafficManager` Protocol; keep on concrete class only.
- **M7**: Rewrite `inventory_ts` to match spec schema `dict[Warehouse, list[tuple[float, dict[SKU, float]]]]`.

---

### Task 1: Public Accessors (M8, L2)

**Findings:** M8 (private attribute access across module boundaries), L2 (graph._nodes)

Three modules access private attributes across class boundaries. Add public accessors to eliminate the coupling.

**Files:**
- Modify: `src/simulatte/intralogistics/battery.py:46` — add `estimate_energy` method
- Modify: `src/simulatte/intralogistics/parking.py:15` — add `available_capacity` property
- Modify: `src/simulatte/intralogistics/graph.py:27` — add `nodes` property
- Modify: `src/simulatte/intralogistics/fleet.py:600,792` — use `battery.estimate_energy`
- Modify: `src/simulatte/intralogistics/policies.py:119` — use `pa.available_capacity`
- Modify: `src/simulatte/intralogistics/traffic.py:88` — use `graph.nodes`
- Test: `tests/intralogistics/test_battery.py`
- Test: `tests/intralogistics/test_parking.py`
- Test: `tests/intralogistics/test_graph.py`

- [ ] **Step 1: Write failing test for `Battery.estimate_energy`**

In `tests/intralogistics/test_battery.py`, add:

```python
class TestEstimateEnergy:
    def test_estimate_energy_default_depletion(self) -> None:
        battery = Battery(capacity=100.0)
        result = battery.estimate_energy(distance=10.0, load_weight=5.0, speed=2.0)
        assert result == 10.0  # default depletion = distance * 1.0

    def test_estimate_energy_custom_depletion(self) -> None:
        def custom_depletion(distance: float, load_weight: float, speed: float) -> float:
            return distance * 0.5 + load_weight * 0.1 + speed * 0.01

        battery = Battery(capacity=100.0, depletion_fn=custom_depletion)
        result = battery.estimate_energy(distance=10.0, load_weight=5.0, speed=2.0)
        assert result == pytest.approx(5.52)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_battery.py::TestEstimateEnergy -v`
Expected: FAIL — `AttributeError: 'Battery' object has no attribute 'estimate_energy'`

- [ ] **Step 3: Implement `Battery.estimate_energy`**

In `src/simulatte/intralogistics/battery.py`, add after `deplete()`:

```python
def estimate_energy(self, distance: float, load_weight: float, speed: float) -> float:
    return self._depletion_fn(distance, load_weight, speed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_battery.py::TestEstimateEnergy -v`
Expected: PASS

- [ ] **Step 5: Write failing test for `ParkingArea.available_capacity`**

In `tests/intralogistics/test_parking.py`, add:

```python
class TestAvailableCapacity:
    def test_available_capacity_empty(self, env: Environment) -> None:
        node = Node(id="P1", x=0.0, y=0.0)
        pa = ParkingArea(env=env, name="PA-1", node=node, capacity=3)
        assert pa.available_capacity == 3

    def test_available_capacity_after_enter(self, env: Environment) -> None:
        from simulatte.intralogistics.agv import AGV, AGVType
        from simulatte.intralogistics.speed import TrapezoidalProfile

        node = Node(id="P1", x=0.0, y=0.0)
        pa = ParkingArea(env=env, name="PA-1", node=node, capacity=3)
        agv_type = AGVType(
            name="t",
            speed_profile=TrapezoidalProfile(max_speed=1.0, acceleration=1.0, deceleration=1.0),
            battery_capacity=100.0,
            weight_capacity=100.0,
            volume_capacity=10.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="a1", initial_node=node)

        def park():
            yield from pa.enter(agv)

        env.process(park())
        env.run()
        assert pa.available_capacity == 2
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_parking.py::TestAvailableCapacity -v`
Expected: FAIL — `AttributeError: 'ParkingArea' object has no attribute 'available_capacity'`

- [ ] **Step 7: Implement `ParkingArea.available_capacity`**

In `src/simulatte/intralogistics/parking.py`, add after `__init__`:

```python
@property
def available_capacity(self) -> int:
    return self._resource.capacity - self._resource.count
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_parking.py::TestAvailableCapacity -v`
Expected: PASS

- [ ] **Step 9: Write failing test for `LayoutGraph.nodes`**

In `tests/intralogistics/test_graph.py`, add:

```python
class TestNodesProperty:
    def test_nodes_returns_all_nodes(self) -> None:
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=2.0, y=0.0)
        graph = LayoutGraph([n1, n2, n3], [Arc(source=n1, target=n2)])
        assert graph.nodes == {n1, n2, n3}

    def test_nodes_is_not_mutable(self) -> None:
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        nodes = graph.nodes
        assert isinstance(nodes, frozenset)
```

- [ ] **Step 10: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_graph.py::TestNodesProperty -v`
Expected: FAIL — `AttributeError: 'LayoutGraph' object has no attribute 'nodes'`

- [ ] **Step 11: Implement `LayoutGraph.nodes`**

In `src/simulatte/intralogistics/graph.py`, add to `LayoutGraph`:

```python
@property
def nodes(self) -> frozenset[Node]:
    return frozenset(self._nodes)
```

- [ ] **Step 12: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_graph.py::TestNodesProperty -v`
Expected: PASS

- [ ] **Step 13: Replace private accesses with public accessors**

In `src/simulatte/intralogistics/fleet.py`, replace `agv.battery._depletion_fn(...)` at lines 600 and 792:

Line 600 — change:
```python
energy_cost = agv.battery._depletion_fn(distance, load_weight, avg_speed)
```
to:
```python
energy_cost = agv.battery.estimate_energy(distance, load_weight, avg_speed)
```

Line 792 — change:
```python
energy_needed = agv.battery._depletion_fn(total_dist, 0.0, 0.0)
```
to:
```python
energy_needed = agv.battery.estimate_energy(total_dist, 0.0, 0.0)
```

In `src/simulatte/intralogistics/policies.py`, line 119 — change:
```python
available = [pa for pa in context.parking_areas if pa._resource.count < pa._resource.capacity]
```
to:
```python
available = [pa for pa in context.parking_areas if pa.available_capacity > 0]
```

In `src/simulatte/intralogistics/traffic.py`, line 88 — change:
```python
for node in graph._nodes:
```
to:
```python
for node in graph.nodes:
```

- [ ] **Step 14: Run full test suite to verify no regressions**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all 287 tests PASS

- [ ] **Step 15: Commit**

```bash
git add src/simulatte/intralogistics/battery.py src/simulatte/intralogistics/parking.py \
       src/simulatte/intralogistics/graph.py src/simulatte/intralogistics/fleet.py \
       src/simulatte/intralogistics/policies.py src/simulatte/intralogistics/traffic.py \
       tests/intralogistics/test_battery.py tests/intralogistics/test_parking.py \
       tests/intralogistics/test_graph.py
git commit -m "refactor(intralogistics): add public accessors for Battery, ParkingArea, LayoutGraph (M8, L2)"
```

---

### Task 2: Distance Helper Deduplication (L7)

**Finding:** L7 — the `sum(math.hypot(...))` pattern for path distance appears in 5 places.

**Files:**
- Modify: `src/simulatte/intralogistics/graph.py` — add `path_distance` static method
- Modify: `src/simulatte/intralogistics/fleet.py:771,788-789` — use `LayoutGraph.path_distance`
- Modify: `src/simulatte/intralogistics/policies.py:65,132` — use `LayoutGraph.path_distance`
- Modify: `src/simulatte/intralogistics/warehouse.py:108-110` — use `LayoutGraph.path_distance`
- Test: `tests/intralogistics/test_graph.py`

- [ ] **Step 1: Write failing test for `LayoutGraph.path_distance`**

In `tests/intralogistics/test_graph.py`, add:

```python
class TestPathDistance:
    def test_single_arc_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        assert LayoutGraph.path_distance([n1, n2]) == pytest.approx(5.0)

    def test_multi_arc_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=0.0)
        n3 = Node(id="C", x=3.0, y=4.0)
        assert LayoutGraph.path_distance([n1, n2, n3]) == pytest.approx(7.0)

    def test_single_node_returns_zero(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        assert LayoutGraph.path_distance([n1]) == 0.0

    def test_empty_path_returns_zero(self) -> None:
        assert LayoutGraph.path_distance([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_graph.py::TestPathDistance -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement `LayoutGraph.path_distance`**

In `src/simulatte/intralogistics/graph.py`, add to `LayoutGraph`:

```python
@staticmethod
def path_distance(path: list[Node]) -> float:
    return sum(
        math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
        for i in range(len(path) - 1)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_graph.py::TestPathDistance -v`
Expected: PASS

- [ ] **Step 5: Replace all 5 occurrences**

In `src/simulatte/intralogistics/fleet.py`, `_find_nearest_charger` (line 771) — change:
```python
return sum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))
```
to:
```python
return LayoutGraph.path_distance(path)
```

In `src/simulatte/intralogistics/fleet.py`, `_find_reachable_charger` (lines 788-789) — change:
```python
total_dist = sum(
    math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1)
)
```
to:
```python
total_dist = LayoutGraph.path_distance(path)
```

All call sites have a `LayoutGraph` instance in scope — use instance calls: `self.graph.path_distance(path)` in fleet.py, `graph.path_distance(path)` in policies.py closures, `context.graph.path_distance(path)` in NearestParkingPolicy. No new imports needed (static methods are callable on instances).

In `src/simulatte/intralogistics/policies.py`, `NearestIdleStrategy.select` (line 65) — change:
```python
d = sum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))
```
to:
```python
d = graph.path_distance(path)
```

Note: `graph` is already the `LayoutGraph` parameter in the `_distance` closure.

In `src/simulatte/intralogistics/policies.py`, `NearestParkingPolicy.reposition` (line 132) — change:
```python
return sum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))
```
to:
```python
return context.graph.path_distance(path)
```

Note: replace `context.graph` where previously the closure captured a local `current` node but the graph was accessed via `context.graph`.

In `src/simulatte/intralogistics/warehouse.py`, `_nearest_bay` (lines 108-110) — change:
```python
return sum(
    math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
    for i in range(len(path) - 1)
)
```
to:
```python
return graph.path_distance(path)
```

Note: `_nearest_bay` is a `@staticmethod` that receives `graph: LayoutGraph` as a parameter via the closure in `_graph_distance`. Use the instance call — no new import needed.

Remove `import math` from `policies.py` (no longer used after both replacements). Keep `import math` in `fleet.py` (still used at line 588 for `math.hypot` in `_travel`). Check `warehouse.py` — if `math` is no longer used after this change, remove the import.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/intralogistics/graph.py src/simulatte/intralogistics/fleet.py \
       src/simulatte/intralogistics/policies.py src/simulatte/intralogistics/warehouse.py \
       tests/intralogistics/test_graph.py
git commit -m "refactor(intralogistics): deduplicate path-distance helper into LayoutGraph.path_distance (L7)"
```

---

### Task 3: TrafficManager Protocol Cleanup (M1)

**Finding:** M1 — `deadlock_timeout` and `priority` were added to the `TrafficManager` Protocol beyond the spec's 6 methods.

**Files:**
- Modify: `src/simulatte/intralogistics/traffic.py:27-36` — remove from Protocol, remove from FreeTrafficManager
- Modify: `src/simulatte/intralogistics/fleet.py:580,700` — use `getattr` with defaults
- Test: `tests/intralogistics/test_traffic.py`

- [ ] **Step 1: Write failing test confirming protocol no longer requires `deadlock_timeout`/`priority`**

In `tests/intralogistics/test_traffic.py`, add:

```python
class TestMinimalTrafficManager:
    """A TrafficManager that only implements the 6 core methods should satisfy the protocol."""

    def test_minimal_implementation_satisfies_protocol(self) -> None:
        from simulatte.intralogistics.traffic import TrafficManager

        class MinimalTM:
            def place(self, agv, node):
                return
                yield

            def check_path(self, agv, path):
                return PathCheckResult(feasible=True)

            def register_intent(self, agv, path):
                pass

            def enter_node(self, agv, node):
                return
                yield

            def leave_node(self, agv, node):
                pass

            def cancel(self, agv):
                pass

        assert isinstance(MinimalTM(), TrafficManager)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_traffic.py::TestMinimalTrafficManager -v`
Expected: FAIL — `isinstance` returns False because `MinimalTM` lacks `deadlock_timeout` and `priority`

- [ ] **Step 3: Remove `deadlock_timeout` and `priority` from Protocol and FreeTrafficManager**

In `src/simulatte/intralogistics/traffic.py`, change the `TrafficManager` Protocol (lines 27-36):

Remove lines 29-30 (`deadlock_timeout` property and `priority` method) from the protocol. The protocol should be:

```python
@runtime_checkable
class TrafficManager(Protocol):
    def place(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult: ...
    def register_intent(self, agv: AGV, path: list[Node]) -> None: ...
    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def leave_node(self, agv: AGV, node: Node) -> None: ...
    def cancel(self, agv: AGV) -> None: ...
```

Remove from `FreeTrafficManager` (lines 40-45):
- Delete the `deadlock_timeout` property (lines 41-42)
- Delete the `priority` method (lines 44-45)

Keep `deadlock_timeout` and `priority` on `ResourceBasedTrafficManager` — they stay as concrete class features.

- [ ] **Step 4: Update FleetCoordinator to use `getattr` with defaults**

In `src/simulatte/intralogistics/fleet.py`, line 580 — change:
```python
deadlock_timeout = self._traffic_manager.deadlock_timeout
```
to:
```python
deadlock_timeout = getattr(self._traffic_manager, "deadlock_timeout", None)
```

In `src/simulatte/intralogistics/fleet.py`, line 700 — change:
```python
priority = self._traffic_manager.priority(agv)
```
to:
```python
priority_fn = getattr(self._traffic_manager, "priority", None)
priority = priority_fn(agv) if priority_fn is not None else 0.0
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS (including the new `TestMinimalTrafficManager`)

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/intralogistics/traffic.py src/simulatte/intralogistics/fleet.py \
       tests/intralogistics/test_traffic.py
git commit -m "fix(intralogistics): remove deadlock_timeout/priority from TrafficManager protocol (M1)"
```

---

### Task 4: Pending Queue Retry Fixes (H2, M10)

**Findings:** H2 — retry counter only increments when idle AGVs exist. M10 — `_pending_retry_delay` is hard-coded.

**Files:**
- Modify: `src/simulatte/intralogistics/fleet.py:68,105,815-818` — fix retry logic, add parameter
- Test: `tests/intralogistics/test_coordinator.py`

- [ ] **Step 1: Write failing test for H2 — retry progresses when all AGVs are busy**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestUnfulfillableOrderAllBusy:
    """H2: Retry counter must advance even when all AGVs are busy."""

    def test_unfulfillable_order_fails_even_when_agvs_busy(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        # Two SKUs: sku_a for the real mission, sku_b for the unfulfillable order
        sku_b = SKU(id="SKU-B", weight=5.0, volume=0.1)

        node_a = Node(id="WH_A_OUT", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        node_b = Node(id="WH_B_IN", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_mid, bidirectional=True),
            Arc(source=node_mid, target=node_b, bidirectional=True),
        ]
        graph = LayoutGraph([node_a, node_mid, node_b], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a], n_slots=2,
            products=[sku_a, sku_b], initial_inventory={sku_a: 100, sku_b: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b], n_slots=2,
            products=[sku_a, sku_b], initial_inventory={sku_a: 0, sku_b: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        # AGV can carry sku_a but NOT sku_b (weight capacity too low for sku_b)
        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            compatibility_fn=lambda sku: sku.id != "SKU-B",
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
            max_dispatch_retries=5,
        )

        # Submit a long mission to keep the AGV busy
        order_busy = coordinator.create_order(sku=sku_a, quantity=1, origin=wh_a, destination=wh_b)
        coordinator.submit(order_busy)

        # Submit an unfulfillable order (incompatible SKU)
        order_bad = coordinator.create_order(sku=sku_b, quantity=1, origin=wh_a, destination=wh_b)
        coordinator.submit(order_bad)

        env.run()

        # The unfulfillable order must reach FAILED even though the AGV was never idle
        assert order_bad.status == OrderStatus.FAILED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestUnfulfillableOrderAllBusy -v`
Expected: FAIL — order stays PENDING forever (retry counter never advances)

- [ ] **Step 3: Fix `_check_pending_queue` to always increment retry counter**

In `src/simulatte/intralogistics/fleet.py`, replace lines 814-818:

```python
            elif idle_agvs:
                self._dispatch_retries[order.id] = self._dispatch_retries.get(order.id, 0) + 1
                if self._dispatch_retries[order.id] >= self._max_dispatch_retries:
                    failed.append(order)
```

with:

```python
            else:
                self._dispatch_retries[order.id] = self._dispatch_retries.get(order.id, 0) + 1
                if self._dispatch_retries[order.id] >= self._max_dispatch_retries:
                    failed.append(order)
```

Remove the `idle_agvs` local variable (line 808) since it's no longer needed:
```python
idle_agvs = [agv for agv in self.fleet if agv.state == AGVState.IDLE]
```
Delete this line entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestUnfulfillableOrderAllBusy -v`
Expected: PASS

- [ ] **Step 5: Write failing test for M10 — configurable `pending_retry_delay`**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestConfigurableRetryDelay:
    """M10: _pending_retry_delay should be configurable via constructor."""

    def test_custom_retry_delay(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        coordinator2 = FleetCoordinator(
            env=env, graph=coordinator.graph, fleet=[],
            warehouses=[wh_a, wh_b], charging_stations=[],
            pending_retry_delay=0.5,
        )
        assert coordinator2._pending_retry_delay == 0.5
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestConfigurableRetryDelay -v`
Expected: FAIL — `unexpected keyword argument 'pending_retry_delay'`

- [ ] **Step 7: Add `pending_retry_delay` constructor parameter**

In `src/simulatte/intralogistics/fleet.py`, add to `__init__` signature (after `max_dispatch_retries`):

```python
        pending_retry_delay: float = 0.001,
```

And change line 105:
```python
self._pending_retry_delay = 0.001
```
to:
```python
self._pending_retry_delay = pending_retry_delay
```

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/simulatte/intralogistics/fleet.py tests/intralogistics/test_coordinator.py
git commit -m "fix(intralogistics): retry counter advances when all AGVs busy, configurable retry delay (H2, M10)"
```

---

### Task 5: Dropped Cargo Infrastructure

**Prerequisite for Tasks 6 and 7.** Creates the `_drop_cargo()` method and `_return_cargo_to_origin()` helper on `FleetCoordinator`, plus the `on_cargo_dropped` hook.

**Files:**
- Modify: `src/simulatte/intralogistics/fleet.py` — add methods and hook
- Modify: `src/simulatte/intralogistics/__init__.py` — no exports needed (private API)
- Test: `tests/intralogistics/test_coordinator.py`

- [ ] **Step 1: Write failing test for `_drop_cargo`**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestDropCargo:
    """Dropped cargo infrastructure for recovery fallback."""

    def test_drop_cargo_records_inventory(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        agv.current_load = {sku_a: 5}

        coordinator._drop_cargo(agv)

        assert agv.current_load is None
        assert len(coordinator._dropped_cargo) == 1
        timestamp, node, sku, qty = coordinator._dropped_cargo[0]
        assert node == agv.current_node
        assert sku is sku_a
        assert qty == 5

    def test_on_cargo_dropped_hook_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        agv.current_load = {sku_a: 3}

        events: list[tuple] = []
        coordinator.on_cargo_dropped(lambda a, n, s, q: events.append((a, n, s, q)))

        coordinator._drop_cargo(agv)

        assert len(events) == 1
        assert events[0] == (agv, agv.current_node, sku_a, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestDropCargo -v`
Expected: FAIL — `AttributeError: 'FleetCoordinator' object has no attribute '_drop_cargo'`

- [ ] **Step 3: Implement `_drop_cargo`, `_return_cargo_to_origin`, and `on_cargo_dropped` hook**

In `src/simulatte/intralogistics/fleet.py`, add to `__init__` (after `_low_battery_flags`):

```python
self._dropped_cargo: list[tuple[float, Node, SKU, int]] = []
self._hooks_on_cargo_dropped: list[Callable[[AGV, Node, SKU, int], None]] = []
```

Add the hook registration method (after `on_agv_idle`):

```python
def on_cargo_dropped(self, callback: Callable[[AGV, Node, SKU, int], None]) -> None:
    self._hooks_on_cargo_dropped.append(callback)
```

Add `_drop_cargo` method (after `_charge_agv`):

```python
def _drop_cargo(self, agv: AGV) -> None:
    """Record dropped cargo at AGV's current location and clear the load."""
    if agv.current_load and agv.current_node is not None:
        for sku, qty in agv.current_load.items():
            self._dropped_cargo.append((self.env.now, agv.current_node, sku, qty))
            for cb in self._hooks_on_cargo_dropped:
                cb(agv, agv.current_node, sku, qty)
    agv.current_load = None
```

Add `_return_cargo_to_origin` method (after `_drop_cargo`):

```python
def _return_cargo_to_origin(self, order: TransferOrder, agv: AGV) -> ProcessGenerator:
    """Navigate AGV to origin and put cargo back. Falls back to drop if travel fails."""
    if not agv.current_load:
        return

    origin_bay = order.origin.nearest_input_bay(agv.current_node, self.graph)
    if agv.current_node != origin_bay:
        self._transition_agv(agv, AGVState.TRAVELING_LOADED)
        outcome = yield from self._travel(agv, agv.current_node, origin_bay, loaded=True)
        if outcome is not _TravelOutcome.ARRIVED:
            self._drop_cargo(agv)
            order.status = OrderStatus.FAILED
            return

    for sku, qty in agv.current_load.items():
        yield from order.origin.put(sku, qty)
    agv.current_load = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestDropCargo -v`
Expected: PASS

- [ ] **Step 5: Write test for `_return_cargo_to_origin` — successful return**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestReturnCargoToOrigin:
    """_return_cargo_to_origin navigates AGV and puts inventory back."""

    def test_successful_return(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        arcs = [Arc(source=node_origin, target=node_mid)]
        graph = LayoutGraph([node_origin, node_mid], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-O",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 90},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_mid], output_bays=[node_mid], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_mid)
        agv.current_load = {sku_a: 10}

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = TransferOrder(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest,
            created_at=0.0, status=OrderStatus.IN_TRANSIT, assigned_agv=agv,
        )

        def do_return():
            yield from coordinator._return_cargo_to_origin(order, agv)

        env.process(do_return())
        env.run()

        assert agv.current_load is None
        assert agv.current_node == node_origin
        assert wh_origin.get_inventory_level(sku_a) == 100  # 90 + 10 returned

    def test_travel_fails_drops_cargo(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        # No path from MID to ORIGIN — AGV is stranded
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        # Only one-way arc from ORIGIN to MID, no reverse
        arcs = [Arc(source=node_origin, target=node_mid, bidirectional=False)]
        graph = LayoutGraph([node_origin, node_mid], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-O",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 90},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_mid], output_bays=[node_mid], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_mid)
        agv.current_load = {sku_a: 10}

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = TransferOrder(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest,
            created_at=0.0, status=OrderStatus.IN_TRANSIT, assigned_agv=agv,
        )

        def do_return():
            yield from coordinator._return_cargo_to_origin(order, agv)

        env.process(do_return())
        env.run()

        # Cargo dropped at MID
        assert agv.current_load is None
        assert order.status == OrderStatus.FAILED
        assert len(coordinator._dropped_cargo) == 1
        assert coordinator._dropped_cargo[0][1] == node_mid
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestReturnCargoToOrigin -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/simulatte/intralogistics/fleet.py tests/intralogistics/test_coordinator.py
git commit -m "feat(intralogistics): add dropped-cargo infrastructure and _return_cargo_to_origin helper"
```

---

### Task 6: Fix ReturnToOrigin Recovery (H4)

**Finding:** H4 — `ReturnToOrigin.recover()` teleports cargo back without navigating the AGV.

**Design:** `ReturnToOrigin.recover()` becomes intent-only (sets order status to PENDING, clears AGV assignment). The coordinator's interrupt handler calls `_return_cargo_to_origin()` for the physical return. If travel back fails, cargo is dropped (recursive fallback chain: ReturnToOrigin → drop).

**Files:**
- Modify: `src/simulatte/intralogistics/policies.py:231-243` — remove physical put from `ReturnToOrigin`
- Modify: `src/simulatte/intralogistics/fleet.py:440-483` — use `_return_cargo_to_origin` after strategy
- Modify: `tests/intralogistics/test_policies.py:636-666` — update test for intent-only behavior
- Test: `tests/intralogistics/test_coordinator.py`

- [ ] **Step 1: Write failing test for physical return-to-origin in the coordinator**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestReturnToOriginPhysicalReturn:
    """H4: ReturnToOrigin must physically navigate AGV back to origin."""

    def test_agv_navigates_to_origin_before_put(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        node_dest = Node(id="DEST", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 90},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        from simulatte.intralogistics.policies import ReturnToOrigin
        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ReturnToOrigin(),
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest)
        coordinator.submit(order)

        # Interrupt after pickup — AGV should be on MID after travelling part way
        def interrupt_at_mid():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_return_to_origin")

        env.process(interrupt_at_mid())
        env.run()

        # AGV must have physically returned to origin
        assert agv.current_node == node_origin
        # Inventory conservation: 90 (initial) - 10 (picked) + 10 (returned) = 90
        # But actually pick removed 10 and the return put 10 back
        assert wh_origin.get_inventory_level(sku_a) == 100
        assert agv.current_load is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestReturnToOriginPhysicalReturn -v`
Expected: FAIL — AGV does not navigate to origin (stays at MID); or inventory level mismatch because put happens without travel

- [ ] **Step 3: Modify `ReturnToOrigin.recover()` to intent-only**

In `src/simulatte/intralogistics/policies.py`, replace `ReturnToOrigin.recover` (lines 231-243):

```python
class ReturnToOrigin:
    """Signal the coordinator to return cargo to the origin warehouse.

    Sets order status to PENDING. Physical travel and inventory return
    are handled by the coordinator's _return_cargo_to_origin().
    """

    def recover(
        self,
        order: TransferOrder,
        agv: AGV,
        coordinator: FleetCoordinator,
    ) -> ProcessGenerator:
        order.status = OrderStatus.PENDING
        order.assigned_agv = None
        return
        yield  # pragma: no cover – unreachable; makes this a generator
```

This removes the `yield from order.origin.put(sku, qty)` line — the coordinator handles it now.

- [ ] **Step 4: Update the coordinator's interrupt handler to call `_return_cargo_to_origin`**

In `src/simulatte/intralogistics/fleet.py`, replace lines 440-483 (the `if agv.current_load is not None:` block inside the non-cancellation interrupt path):

```python
                if agv.current_load is not None:
                    # Has cargo — delegate to load recovery strategy for intent
                    yield from self._load_recovery_strategy.recover(order, agv, self)

                    if order.status == OrderStatus.IN_TRANSIT and agv.current_load is not None:
                        # ResumeDelivery — re-travel to destination from current position
                        dest_input_bay = order.destination.nearest_input_bay(agv.current_node, self.graph)
                        self._transition_agv(agv, AGVState.TRAVELING_LOADED)
                        while True:
                            outcome = yield from self._travel(agv, agv.current_node, dest_input_bay, loaded=True)
                            if outcome is _TravelOutcome.ARRIVED:
                                break
                            if outcome in (_TravelOutcome.BATTERY_STRANDED, _TravelOutcome.MISSION_FAILED):
                                # H1 fix: fall back to return-to-origin, then drop
                                yield from self._return_cargo_to_origin(order, agv)
                                order.status = OrderStatus.FAILED
                                break
                            if agv.battery.is_critical and self.charging_stations:
                                yield from self._charge_agv(agv)
                            self._transition_agv(agv, AGVState.TRAVELING_LOADED)

                        if order.status == OrderStatus.IN_TRANSIT:
                            # Successfully re-traveled — complete delivery
                            order.status = OrderStatus.DELIVERING
                            self._transition_agv(agv, AGVState.WAITING_UNLOAD)
                            yield from order.destination.put(order.sku, order.quantity)
                            agv.current_load = None
                            yield self.env.timeout(agv.agv_type.unload_time_fn())
                            order.delivered_at = self.env.now
                            order.status = OrderStatus.COMPLETED
                            self._order_metrics_collector.record(order)

                            for cb in self._hooks_on_delivery_complete:
                                cb(order, agv)
                            if self._time_series_collector is not None:
                                self._time_series_collector.on_delivery_complete(self, order, agv)
                    elif agv.current_load is not None:
                        # ReturnToOrigin (or similar) — physically return cargo
                        yield from self._return_cargo_to_origin(order, agv)
```

Key changes from original:
- Line `elif agv.current_load is not None:` replaces the bare `else:` at old line 481 — only call `_return_cargo_to_origin` if there's still cargo
- The old `agv.current_load = None` at line 480 (H1 silent destruction) is replaced by `_return_cargo_to_origin` which properly returns or drops
- The old `agv.current_load = None` at line 483 is replaced by the physical return method

- [ ] **Step 5: Update `test_with_cargo_puts_back_to_origin` in test_policies.py**

This test called `ReturnToOrigin.recover()` in isolation and checked that inventory was returned. Since `recover()` is now intent-only, update the test:

In `tests/intralogistics/test_policies.py`, replace `TestReturnToOrigin.test_with_cargo_puts_back_to_origin` (lines 636-666):

```python
    def test_with_cargo_sets_pending(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        """ReturnToOrigin sets status to PENDING (physical return is coordinator's job)."""
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 100})
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", nodes[1])
        agv.current_load = {sku_a: 10}

        order = TransferOrder(
            sku=sku_a, quantity=10,
            origin=wh_orig, destination=wh_dest,
            created_at=0.0, status=OrderStatus.IN_TRANSIT, assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # type: ignore[arg-type]
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None
        # Cargo is NOT returned by the strategy — coordinator handles it
        assert agv.current_load == {sku_a: 10}
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/intralogistics/policies.py src/simulatte/intralogistics/fleet.py \
       tests/intralogistics/test_policies.py tests/intralogistics/test_coordinator.py
git commit -m "fix(intralogistics): ReturnToOrigin physically navigates AGV, drops cargo on failure (H4)"
```

---

### Task 7: Fix ResumeDelivery Inventory Conservation (H1)

**Finding:** H1 — cargo silently destroyed when ResumeDelivery re-travel fails.

**Design:** This is already fixed by the code change in Task 6 (the `_TravelOutcome.BATTERY_STRANDED` and `_TravelOutcome.MISSION_FAILED` cases now call `_return_cargo_to_origin` instead of `agv.current_load = None`). This task adds the test that verifies inventory conservation.

**Files:**
- Modify: `tests/intralogistics/test_coordinator.py` — update `TestResumeDeliveryStranded`

- [ ] **Step 1: Update `TestResumeDeliveryStranded` to verify inventory conservation**

In `tests/intralogistics/test_coordinator.py`, modify `TestResumeDeliveryStranded.test_resume_delivery_stranded_clears_cargo` (lines 2617-2699). Replace the assertions at the end (lines 2697-2699):

```python
        # After stranding on resume: cargo should be returned or dropped, not silently destroyed
        assert agv.current_load is None

        # H1 fix: inventory conservation — cargo must be somewhere.
        # The test fixture severed ALL arcs from MID, so return-to-origin also
        # fails and cargo is dropped at MID.
        origin_level = wh_origin.get_inventory_level(sku_a)
        dest_level = wh_dest.get_inventory_level(sku_a)
        dropped = coordinator._dropped_cargo

        # Total inventory must be conserved: 100 (initial) = origin + dest + dropped
        total = origin_level + dest_level + sum(qty for _, _, _, qty in dropped)
        assert total == 100
```

- [ ] **Step 2: Write test — ResumeDelivery re-travel fails, return-to-origin succeeds**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestResumeDeliveryFallbackToReturn:
    """H1: When ResumeDelivery re-travel fails but return-to-origin succeeds, cargo goes back to origin."""

    def test_resume_fails_return_succeeds(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        from simulatte.intralogistics.policies import ResumeDelivery

        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ResumeDelivery(),
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest)
        coordinator.submit(order)

        def interrupt_and_sever_dest():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                # Sever only MID→DEST, keep MID→ORIGIN intact
                graph._adjacency[node_mid].pop(node_dest, None)
                process.interrupt("test_sever_dest_only")

        env.process(interrupt_and_sever_dest())
        env.run()

        # Re-travel to DEST fails, fallback to return-to-origin succeeds
        assert agv.current_load is None
        assert order.status == OrderStatus.FAILED
        # Cargo returned to origin — not delivered, not dropped
        assert wh_origin.get_inventory_level(sku_a) == 100  # 100 - 10 picked + 10 returned
        assert wh_dest.get_inventory_level(sku_a) == 0
        assert len(coordinator._dropped_cargo) == 0
```

- [ ] **Step 3: Write test — ResumeDelivery falls back to drop when return also fails**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestResumeDeliveryFallbackChain:
    """H1: ResumeDelivery → ReturnToOrigin → Drop cargo."""

    def test_resume_and_return_both_fail_drops_cargo(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        from simulatte.intralogistics.policies import ResumeDelivery

        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ResumeDelivery(),
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest)
        coordinator.submit(order)

        def interrupt_and_isolate():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                # Remove ALL arcs from MID — AGV is completely isolated
                graph._adjacency[node_mid] = {}
                graph._adjacency[node_origin].pop(node_mid, None)
                graph._adjacency[node_dest].pop(node_mid, None)
                process.interrupt("test_full_strand")

        env.process(interrupt_and_isolate())
        env.run()

        # Cargo should be dropped (not silently destroyed)
        assert agv.current_load is None
        assert len(coordinator._dropped_cargo) == 1
        _, drop_node, drop_sku, drop_qty = coordinator._dropped_cargo[0]
        assert drop_sku is sku_a
        assert drop_qty == 10

        # Inventory conservation: 100 - 10 (picked) + 0 (not returned) + 10 (dropped) = 100
        total = (
            wh_origin.get_inventory_level(sku_a)
            + wh_dest.get_inventory_level(sku_a)
            + sum(qty for _, _, _, qty in coordinator._dropped_cargo)
        )
        assert total == 100
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestResumeDeliveryStranded tests/intralogistics/test_coordinator.py::TestResumeDeliveryFallbackChain -v`
Expected: PASS (both tests should pass because the code was already fixed in Task 6)

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/intralogistics/test_coordinator.py
git commit -m "test(intralogistics): verify inventory conservation for ResumeDelivery fallback chain (H1)"
```

---

### Task 8: Fix Cancellation With Cargo (H5)

**Finding:** H5 — cancellation bypasses `LoadRecoveryStrategy`, uses fire-and-forget `put`, and committed-pick rollback is also fire-and-forget.

**Design:** Cancellation with cargo uses `_return_cargo_to_origin()` (with navigation, synchronous put). Committed-pick rollback uses `yield from` instead of `env.process`. We keep cancellation always returning to origin (not delegating to LoadRecoveryStrategy) because ResumeDelivery makes no sense for a cancelled order — but we do it properly.

**Files:**
- Modify: `src/simulatte/intralogistics/fleet.py:425-496` — fix interrupt handler
- Test: `tests/intralogistics/test_coordinator.py`

- [ ] **Step 1: Write failing test — cancellation with cargo returns inventory properly**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestCancelWithCargoReturn:
    """H5: Cancellation with cargo must physically return inventory (not fire-and-forget)."""

    def test_cancel_with_cargo_navigates_and_puts(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        node_dest = Node(id="DEST", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest)
        coordinator.submit(order)

        # Cancel after pickup (AGV has cargo, mid-travel loaded)
        def cancel_after_pickup():
            yield env.timeout(5.0)
            coordinator.cancel(order)

        env.process(cancel_after_pickup())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        assert agv.current_load is None
        # AGV should have navigated back to origin
        assert agv.current_node == node_origin
        # Inventory conservation
        assert wh_origin.get_inventory_level(sku_a) == 100

    def test_committed_pick_rollback_synchronous(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """H5(3): Committed-pick rollback must complete before AGV goes IDLE."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)

        # Cancel during pick phase
        def cancel_during_pick():
            yield env.timeout(1.5)  # mid-pick
            coordinator.cancel(order)

        env.process(cancel_during_pick())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        # Inventory must be fully restored
        assert wh_a.get_inventory_level(sku_a) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestCancelWithCargoReturn -v`
Expected: FAIL — AGV doesn't navigate to origin (current code teleports and uses fire-and-forget)

- [ ] **Step 3: Fix the cancellation path in `_run_mission`**

In `src/simulatte/intralogistics/fleet.py`, replace lines 425-436 (start of interrupt handler) with:

```python
        except simpy.Interrupt:
            self.env.debug(
                f"Order {order.id} interrupted (agv={agv.agv_id})",
                component="FleetCoordinator",
            )

            # Roll back committed but unloaded pick (synchronous)
            committed = self._committed_picks.pop(order.id, None)
            if committed is not None:
                wh, sku, qty = committed
                yield from wh.put(sku, qty)
```

Replace lines 490-496 (cancellation branch) with:

```python
            else:
                # Explicit cancellation — physically return cargo to origin
                if agv.current_load is not None:
                    yield from self._return_cargo_to_origin(order, agv)
                # Ensure status stays CANCELLED (may have been changed by _return_cargo_to_origin)
                order.status = OrderStatus.CANCELLED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestCancelWithCargoReturn -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/intralogistics/fleet.py tests/intralogistics/test_coordinator.py
git commit -m "fix(intralogistics): cancellation returns cargo with navigation, synchronous rollback (H5)"
```

---

### Task 9: Battery and Charger Reachability Fixes (M2, M4, M5)

**Findings:**
- M2: Repositioning ignores `MISSION_FAILED` and overwrites `STRANDED` with `IDLE`
- M4: `_find_reachable_charger` passes `speed=0.0` to energy estimate
- M5: Post-mission `_charge_agv` uses `_find_nearest_charger` (no energy check)

**Files:**
- Modify: `src/simulatte/intralogistics/fleet.py:414-421,714-715,792`
- Test: `tests/intralogistics/test_coordinator.py`

- [ ] **Step 1: Write failing test for M2 — repositioning BATTERY_STRANDED should not go IDLE**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestRepositioningOutcomes:
    """M2: Repositioning must handle BATTERY_STRANDED and MISSION_FAILED properly."""

    def test_repositioning_stranded_stays_stranded(
        self, env: Environment, sku_a: SKU
    ) -> None:
        from simulatte.intralogistics.parking import ParkingArea
        from simulatte.intralogistics.policies import NearestParkingPolicy

        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_dest = Node(id="DEST", x=5.0, y=0.0)
        node_park = Node(id="PARK", x=100.0, y=0.0)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)
        arcs = [
            Arc(source=node_origin, target=node_dest),
            Arc(source=node_dest, target=node_park),
        ]
        graph = LayoutGraph([node_origin, node_dest, node_park], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_origin], output_bays=[node_origin], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 0.1, put_time_fn=lambda s, q: 0.1,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_dest], output_bays=[node_dest], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 0.1, put_time_fn=lambda s, q: 0.1,
        )

        pa = ParkingArea(env=env, name="PA", node=node_park, capacity=2)

        # AGV with very little battery — enough for mission but not for repositioning
        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=20.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 0.1, unload_time_fn=lambda: 0.1,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
            parking_areas=[pa],
            repositioning_policy=NearestParkingPolicy(),
        )

        order = coordinator.create_order(sku=sku_a, quantity=1, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        # AGV should be STRANDED, not IDLE
        assert agv.state == AGVState.STRANDED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestRepositioningOutcomes -v`
Expected: FAIL — AGV state is IDLE (overwritten from STRANDED)

- [ ] **Step 3: Fix repositioning outcome handling**

In `src/simulatte/intralogistics/fleet.py`, replace lines 414-421:

```python
                if target is not None and target != agv.current_node:
                    self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
                    outcome = yield from self._travel(agv, agv.current_node, target, loaded=False)
                    if outcome is _TravelOutcome.BATTERY_STRANDED:
                        pass
```

with:

```python
                if target is not None and target != agv.current_node:
                    self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
                    outcome = yield from self._travel(agv, agv.current_node, target, loaded=False)
                    if outcome is _TravelOutcome.BATTERY_STRANDED:
                        return
                    if outcome is _TravelOutcome.MISSION_FAILED:
                        self.env.warning(
                            f"Repositioning failed for {agv.agv_id} — no path to {target.id}",
                            component="FleetCoordinator",
                        )
```

The `return` on `BATTERY_STRANDED` exits before the `IDLE` transition at line 421. `_travel` already set the AGV to `STRANDED`, so it stays `STRANDED`. For `MISSION_FAILED`, we log a warning and fall through to `IDLE` (the AGV is physically fine, just couldn't reach the parking — staying IDLE is correct).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_coordinator.py::TestRepositioningOutcomes -v`
Expected: PASS

- [ ] **Step 5: Fix M4 — use proper speed estimate in `_find_reachable_charger`**

In `src/simulatte/intralogistics/fleet.py`, replace line 792:

```python
energy_needed = agv.battery.estimate_energy(total_dist, 0.0, 0.0)
```

with:

```python
est_speed = agv.agv_type.speed_profile.travel_time(total_dist, 0.0, agv.battery.level_pct)
est_avg_speed = total_dist / est_speed if est_speed > 0 else 0.0
energy_needed = agv.battery.estimate_energy(total_dist, 0.0, est_avg_speed)
```

- [ ] **Step 6: Fix M5 — use `_find_reachable_charger` in `_charge_agv` post-mission**

In `src/simulatte/intralogistics/fleet.py`, replace lines 714-715:

```python
        if station is None:
            station = self._find_nearest_charger(agv)
```

with:

```python
        if station is None:
            station = self._find_reachable_charger(agv) or self._find_nearest_charger(agv)
```

This tries reachable first, then falls back to nearest (which may strand, but at least it tries).

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/simulatte/intralogistics/fleet.py tests/intralogistics/test_coordinator.py
git commit -m "fix(intralogistics): repositioning handles stranding, charger reachability checks (M2, M4, M5)"
```

---

### Task 10: Strategy Fixes (M6, L1)

**Findings:**
- M6: `NearestIdleStrategy` dispatches unreachable AGVs (distance = inf)
- L1: `ReorderPointPolicy` uses `o.sku is sku` (identity) instead of `==` (equality)

**Files:**
- Modify: `src/simulatte/intralogistics/policies.py:63-68,183-184`
- Test: `tests/intralogistics/test_policies.py`

- [ ] **Step 1: Write failing test for M6 — NearestIdleStrategy returns None when all unreachable**

In `tests/intralogistics/test_policies.py`, add to `TestNearestIdleStrategy`:

```python
    def test_returns_none_when_all_unreachable(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        """M6: When all candidates have infinite distance, select() must return None."""
        # Create disconnected graph: AGV on island, warehouse on mainland
        island = Node(id="ISLAND", x=0.0, y=0.0)
        mainland = Node(id="MAINLAND", x=100.0, y=0.0)
        # No arcs — completely disconnected
        graph = LayoutGraph([island, mainland], [])

        wh = _make_warehouse(env, "WH", [mainland], [mainland], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", island)

        order = TransferOrder(
            sku=sku_a, quantity=1, origin=wh, destination=wh, created_at=0.0,
        )

        strategy = NearestIdleStrategy()
        result = strategy.select(order, [agv], graph)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_policies.py::TestNearestIdleStrategy::test_returns_none_when_all_unreachable -v`
Expected: FAIL — returns the AGV with infinite distance

- [ ] **Step 3: Fix `NearestIdleStrategy.select`**

In `src/simulatte/intralogistics/policies.py`, replace lines 59-68:

```python
        def _distance(agv: AGV) -> tuple[float, str]:
            assert agv.current_node is not None
            output_bay = order.origin.nearest_output_bay(agv.current_node, graph)
            path = graph.shortest_path(agv.current_node, output_bay)
            if path is None:
                return (float("inf"), agv.agv_id)
            d = graph.path_distance(path)
            return (d, agv.agv_id)

        best = min(candidates, key=_distance)
        if _distance(best)[0] == float("inf"):
            return None
        return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_policies.py::TestNearestIdleStrategy::test_returns_none_when_all_unreachable -v`
Expected: PASS

- [ ] **Step 5: Write failing test for L1 — SKU equality vs identity**

In `tests/intralogistics/test_policies.py`, add to `TestReorderPointPolicy`:

```python
    def test_in_transit_check_uses_equality_not_identity(self, env: Environment, sku_a: SKU) -> None:
        """L1: ReorderPointPolicy must use == for SKU comparison, not 'is'."""
        # Create a separate but equal SKU instance
        sku_a_copy = SKU(id=sku_a.id, weight=sku_a.weight, volume=sku_a.volume)
        assert sku_a == sku_a_copy
        assert sku_a is not sku_a_copy

        node = Node(id="N", x=0.0, y=0.0)
        wh_monitored = _make_warehouse(env, "WH-M", [node], [node], [sku_a], {sku_a: 5})
        wh_source = _make_warehouse(env, "WH-S", [node], [node], [sku_a], {sku_a: 100})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 20},
            reorder_quantity={sku_a: 15},
        )

        # Create an in-transit order using the COPY of sku_a
        in_transit = TransferOrder(
            sku=sku_a_copy, quantity=15, origin=wh_source, destination=wh_monitored,
            created_at=0.0, status=OrderStatus.IN_TRANSIT,
        )

        # With equality: effective_stock = 5 + 15 = 20 >= 20, no new order
        orders = policy.check(wh_monitored, [wh_monitored, wh_source], [in_transit])
        assert len(orders) == 0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_policies.py::TestReorderPointPolicy::test_in_transit_check_uses_equality_not_identity -v`
Expected: FAIL — the `is` check doesn't match the copy, so in-transit qty is 0, and a new order is created

- [ ] **Step 7: Fix `ReorderPointPolicy.check` — use `==` instead of `is`**

In `src/simulatte/intralogistics/policies.py`, line 183, change:

```python
                if o.sku is sku
```
to:
```python
                if o.sku == sku
```

Also fix line 184 — change:
```python
                and o.destination is warehouse
```
to:
```python
                and o.destination is warehouse
```

Actually, `o.destination is warehouse` using `is` is correct for `Warehouse` (no custom `__eq__`, identity is the right check). Keep it as-is. Only fix the SKU comparison.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_policies.py::TestReorderPointPolicy::test_in_transit_check_uses_equality_not_identity -v`
Expected: PASS

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/simulatte/intralogistics/policies.py tests/intralogistics/test_policies.py
git commit -m "fix(intralogistics): NearestIdleStrategy filters unreachable AGVs, SKU equality check (M6, L1)"
```

---

### Task 11: Metrics Fixes (M7, M9)

**Findings:**
- M7: `inventory_ts` schema deviates from spec — rewrite to `dict[Warehouse, list[tuple[float, dict[SKU, float]]]]`
- M9: EMA initializes to 0.0 — first records heavily biased

**Files:**
- Modify: `src/simulatte/intralogistics/metrics.py:29-34,83,93-103`
- Modify: `tests/intralogistics/test_metrics.py`

- [ ] **Step 1: Write failing test for M9 — EMA first-observation initialization**

In `tests/intralogistics/test_metrics.py`, add:

```python
class TestEMAFirstObservation:
    """M9: First record should initialize EMA to the observed value, not bias toward 0."""

    def test_first_record_initializes_ema(self) -> None:
        metrics = EMAOrderMetrics(alpha=0.1)
        order = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1,
            origin=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order.dispatched_at = 1.0
        order.picked_at = 3.0
        order.delivered_at = 10.0

        metrics.record(order)

        # First observation should set the EMA directly (not alpha * value)
        assert metrics.ema_fulfillment_time == pytest.approx(10.0)
        assert metrics.ema_dispatch_delay == pytest.approx(1.0)
        assert metrics.ema_travel_time_empty == pytest.approx(2.0)
        assert metrics.ema_travel_time_loaded == pytest.approx(7.0)

    def test_uninitialized_fields_are_none(self) -> None:
        metrics = EMAOrderMetrics()
        assert metrics.ema_fulfillment_time is None
        assert metrics.ema_dispatch_delay is None

    def test_per_field_initialization_partial_order(self) -> None:
        """First order without delivered_at; second order with it.
        Fulfillment EMA should initialize from the second order, not bias from 0."""
        metrics = EMAOrderMetrics(alpha=0.1)

        order1 = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1, origin=None, destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order1.dispatched_at = 1.0
        # No delivered_at, no picked_at
        metrics.record(order1)

        assert metrics.ema_dispatch_delay == pytest.approx(1.0)  # initialized
        assert metrics.ema_fulfillment_time is None  # not yet seen

        order2 = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1, origin=None, destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order2.dispatched_at = 2.0
        order2.picked_at = 4.0
        order2.delivered_at = 10.0
        metrics.record(order2)

        # fulfillment_time initialized from order2 (not biased from 0)
        assert metrics.ema_fulfillment_time == pytest.approx(10.0)
        # dispatch_delay updated with EMA from order2
        assert metrics.ema_dispatch_delay == pytest.approx(1.0 + 0.1 * (2.0 - 1.0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_metrics.py::TestEMAFirstObservation -v`
Expected: FAIL — `ema_fulfillment_time` is `0.1 * 10 = 1.0`, not `10.0`

- [ ] **Step 3: Implement per-field first-observation initialization**

The fix must track initialization per field, not globally. If the first order lacks `delivered_at`, the fulfillment EMA should still initialize from the first order that *does* have it. Use `Optional[float] = None` for EMA fields and check `is None` in each branch.

In `src/simulatte/intralogistics/metrics.py`, change the field definitions:

```python
ema_fulfillment_time: float | None = field(default=None, init=False)
ema_dispatch_delay: float | None = field(default=None, init=False)
ema_travel_time_empty: float | None = field(default=None, init=False)
ema_travel_time_loaded: float | None = field(default=None, init=False)
ema_late_orders: float | None = field(default=None, init=False)
```

Replace the `record` method with per-field initialization:

```python
def _ema_update(self, current: float | None, value: float) -> float:
    if current is None:
        return value
    return current + self.alpha * (value - current)

def record(self, order: TransferOrder) -> None:
    if order.delivered_at is not None:
        value = order.delivered_at - order.created_at
        self.ema_fulfillment_time = self._ema_update(self.ema_fulfillment_time, value)

    if order.dispatched_at is not None:
        value = order.dispatched_at - order.created_at
        self.ema_dispatch_delay = self._ema_update(self.ema_dispatch_delay, value)

    if order.dispatched_at is not None and order.picked_at is not None:
        value = order.picked_at - order.dispatched_at
        self.ema_travel_time_empty = self._ema_update(self.ema_travel_time_empty, value)

    if order.picked_at is not None and order.delivered_at is not None:
        value = order.delivered_at - order.picked_at
        self.ema_travel_time_loaded = self._ema_update(self.ema_travel_time_loaded, value)

    if order.delivered_at is not None:
        late = 1.0 if order.due_date is not None and order.delivered_at > order.due_date else 0.0
        self.ema_late_orders = self._ema_update(self.ema_late_orders, late)
```

**Note:** Consumers that read EMA fields before any records are recorded will get `None` instead of `0.0`. If this breaks downstream code, add a convenience property like `ema_fulfillment_time_or_zero` or handle `None` at the read site. Check existing tests and fix any that assert against `0.0` for uninitialized fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/intralogistics/test_metrics.py::TestEMAFirstObservation -v`
Expected: PASS

- [ ] **Step 5: Update existing EMA tests if needed**

The existing `TestEMAOrderMetricsRecord.test_first_and_second_record` may need updating since the first record now initializes directly. Read the test and adjust expected values.

- [ ] **Step 6: Write failing test for M7 — inventory_ts as dict[Warehouse, ...]**

In `tests/intralogistics/test_metrics.py`, add:

```python
class TestInventoryTsSchema:
    """M7: inventory_ts must match spec: dict[Warehouse, list[tuple[float, dict[SKU, float]]]]."""

    def test_inventory_ts_is_dict_keyed_by_warehouse(self) -> None:
        collector = DefaultIntralogisticsCollector()
        assert isinstance(collector.inventory_ts, dict)

    def test_on_pickup_records_in_dict_format(self) -> None:
        from unittest.mock import MagicMock
        from simulatte.intralogistics.warehouse import Warehouse

        collector = DefaultIntralogisticsCollector()
        sku = SKU(id="X", weight=1.0, volume=0.1)

        # Create a mock coordinator and order
        coordinator = MagicMock()
        coordinator._pending_queue = []

        env = Environment()
        wh = Warehouse(
            env=env, name="WH-A",
            input_bays=[], output_bays=[], n_slots=2,
            products=[sku], initial_inventory={sku: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        order = TransferOrder(
            sku=sku, quantity=10, origin=wh, destination=wh, created_at=0.0,
        )
        order.picked_at = 5.0

        agv = MagicMock()
        collector.on_pickup_complete(coordinator, order, agv)

        assert wh in collector.inventory_ts
        records = collector.inventory_ts[wh]
        assert len(records) == 1
        timestamp, inv_dict = records[0]
        assert timestamp == 5.0
        assert isinstance(inv_dict, dict)
        assert sku in inv_dict
```

- [ ] **Step 7: Run test to verify it fails**

Run: `uv run pytest tests/intralogistics/test_metrics.py::TestInventoryTsSchema -v`
Expected: FAIL — `inventory_ts` is a list, not a dict

- [ ] **Step 8: Rewrite `inventory_ts` in `DefaultIntralogisticsCollector`**

In `src/simulatte/intralogistics/metrics.py`, change line 83:

```python
inventory_ts: list[tuple[float, str, str, float]] = field(default_factory=list)
```
to:
```python
inventory_ts: dict[Warehouse, list[tuple[float, dict[SKU, float]]]] = field(default_factory=dict)
```

Add imports (move `Warehouse` from no-import to used):
```python
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.warehouse import Warehouse
```

Check for circular imports — `metrics.py` currently imports `AGV`, `AGVState` from `agv` and `TransferOrder` from `order`. Adding `Warehouse` and `SKU` imports should be safe (neither imports `metrics.py`).

Update `on_pickup_complete` (lines 92-95):

```python
def on_pickup_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None:
    if order.picked_at is not None:
        inv_snapshot = {sku: float(container.level) for sku, container in order.origin.inventory.items()}
        self.inventory_ts.setdefault(order.origin, []).append((order.picked_at, inv_snapshot))
```

Update `on_delivery_complete` (lines 97-103):

```python
def on_delivery_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None:
    if order.delivered_at is not None:
        _, prev_count = self.throughput_ts[-1]
        self.throughput_ts.append((order.delivered_at, prev_count + 1))

        inv_snapshot = {sku: float(container.level) for sku, container in order.destination.inventory.items()}
        self.inventory_ts.setdefault(order.destination, []).append((order.delivered_at, inv_snapshot))
```

- [ ] **Step 9: Update existing tests that use the old `inventory_ts` format**

Search `tests/intralogistics/test_metrics.py` for references to `inventory_ts` and update them to use the new dict-based format.

- [ ] **Step 10: Run full test suite**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/simulatte/intralogistics/metrics.py tests/intralogistics/test_metrics.py
git commit -m "fix(intralogistics): EMA first-observation init, inventory_ts matches spec schema (M7, M9)"
```

---

### Task 12: Remaining Fixes and Coverage (M3, M11, L3, L5, L8, L6)

**Findings:**
- M3: `_enter_with_timeout` cleanup fragility — acknowledged, add single-owner comment
- M11: No test for `submit()` before `env.run()` under `ResourceBasedTrafficManager`
- L3: Mutable public attributes on `FleetCoordinator`
- L5: `build_simple_system` missing parking areas
- L8: `RoundRobinStrategy` cursor doesn't reset — document in docstring
- L6: Coverage gaps (12 uncovered lines)

**Files:**
- Modify: `src/simulatte/intralogistics/fleet.py:88-92` — use tuples for L3
- Modify: `src/simulatte/intralogistics/builders.py` — add ParkingArea for L5
- Modify: `src/simulatte/intralogistics/policies.py:72` — docstring for L8
- Test: `tests/intralogistics/test_coordinator.py` — M11, L6 coverage tests
- Test: `tests/intralogistics/test_builders.py` — L5

- [ ] **Step 1: Fix L3 — use tuples for immutable config attributes**

In `src/simulatte/intralogistics/fleet.py`, change `__init__` lines 89-92:

```python
self.fleet = fleet
self.warehouses = warehouses
self.charging_stations = charging_stations
self.parking_areas = parking_areas or []
```
to:
```python
self.fleet = tuple(fleet)
self.warehouses = tuple(warehouses)
self.charging_stations = tuple(charging_stations)
self.parking_areas = tuple(parking_areas or [])
```

Update type annotations in the `__init__` signature — the constructor accepts `list` but stores `tuple`:

The stored type should be `tuple[AGV, ...]`, etc. But this changes the public API: downstream code iterating `coordinator.fleet` gets a tuple instead of a list. Since the codebase only reads these (iteration, `len`, `in`), tuples are drop-in replacements.

Also update the `TYPE_CHECKING` type hints if needed. Actually, the type annotations on `self.fleet`, etc. are inferred. Since the constructor parameter is `list[AGV]`, users pass lists — but the stored attribute is now a tuple. For type safety, add explicit annotations:

No — this creates a lot of churn for a low finding. Instead, just freeze the lists:

```python
self.fleet: tuple[AGV, ...] = tuple(fleet)
self.warehouses: tuple[Warehouse, ...] = tuple(warehouses)
self.charging_stations: tuple[ChargingStation, ...] = tuple(charging_stations)
self.parking_areas: tuple[ParkingArea, ...] = tuple(parking_areas or [])
```

- [ ] **Step 2: Fix L5 — add ParkingArea to `build_simple_system`**

In `src/simulatte/intralogistics/builders.py`, add after the charging station creation (after line 101):

```python
from simulatte.intralogistics.parking import ParkingArea

parking_area = ParkingArea(
    env=env,
    name="PA-N1",
    node=n1,
    capacity=n_agvs,
)
```

And add `parking_areas=[parking_area]` to the `FleetCoordinator` constructor (line 126-134):

```python
coordinator = FleetCoordinator(
    env=env,
    graph=graph,
    fleet=agvs,
    warehouses=[warehouse_a, warehouse_b],
    charging_stations=[charging_station],
    parking_areas=[parking_area],
    traffic_manager=FreeTrafficManager(),
    path_planner=DijkstraPlanner(),
)
```

Update the return type to include the parking area, or keep the existing return signature and just include it in the coordinator. The return type already exposes all key components — adding parking_area would change the tuple. Since the spec doesn't mandate it in the return, keep the return as-is but wire it into the coordinator.

- [ ] **Step 3: Write test for L5**

In `tests/intralogistics/test_builders.py`, add:

```python
class TestBuildSimpleSystemParkingArea:
    def test_coordinator_has_parking_areas(self) -> None:
        from simulatte.environment import Environment
        from simulatte.intralogistics.builders import build_simple_system

        env = Environment()
        coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env)
        assert len(coordinator.parking_areas) > 0
```

- [ ] **Step 4: Write test for M11 — submit before env.run under ResourceBasedTrafficManager**

In `tests/intralogistics/test_coordinator.py`, add:

```python
class TestSubmitBeforeRun:
    """M11: submit() before env.run() must work under ResourceBasedTrafficManager."""

    def test_submit_before_run_with_resource_traffic_manager(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node_a = Node(id="WH_A_OUT", x=0.0, y=0.0)
        node_b = Node(id="WH_B_IN", x=10.0, y=0.0)
        arcs = [Arc(source=node_a, target=node_b)]
        graph = LayoutGraph([node_a, node_b], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b], n_slots=2,
            products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)
        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
            traffic_manager=tm,
        )

        # Submit BEFORE env.run()
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)

        # Run — placement should happen before mission
        env.run()

        assert order.status == OrderStatus.COMPLETED
```

- [ ] **Step 5: Update L8 — add note to RoundRobinStrategy docstring**

In `src/simulatte/intralogistics/policies.py`, line 72, change:

```python
class RoundRobinStrategy:
    """Cycle through compatible idle AGVs via an internal cursor."""
```
to:
```python
class RoundRobinStrategy:
    """Cycle through compatible idle AGVs via an internal cursor.

    The cursor increments monotonically. If fleet composition changes
    mid-simulation, cycling order over the filtered candidate list
    becomes non-deterministic. This is by design — the modulo arithmetic
    prevents errors, but strict round-robin fairness is not guaranteed
    across fleet changes.
    """
```

- [ ] **Step 6: Run full test suite and check coverage**

Run: `uv run pytest tests/intralogistics/ -v`
Expected: all tests PASS

Run: `uv run pytest tests/intralogistics/ --cov=simulatte.intralogistics --cov-report=term-missing`
Expected: coverage improved. The original uncovered line numbers from L6 (331-332, 418, 453-454, 555-559, 695, 740-741) will have shifted due to code changes in Tasks 4-9. Review the `term-missing` output for any remaining uncovered lines in `fleet.py` and add targeted tests if coverage is below 99%.

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/intralogistics/fleet.py src/simulatte/intralogistics/builders.py \
       src/simulatte/intralogistics/policies.py \
       tests/intralogistics/test_coordinator.py tests/intralogistics/test_builders.py
git commit -m "fix(intralogistics): immutable config, parking in builder, remaining fixes (M3, M11, L3, L5, L8)"
```

---

## Cross-Check: Findings Coverage

| Finding | Task | Status |
|---------|------|--------|
| H1 | Task 6+7 | Fixed: fallback chain, inventory conservation test |
| H2 | Task 4 | Fixed: retry counter always advances |
| H3 | — | Closed: working as designed |
| H4 | Task 6 | Fixed: ReturnToOrigin navigates AGV, drops on failure |
| H5 | Task 8 | Fixed: cancellation navigates, synchronous rollback |
| M1 | Task 3 | Fixed: protocol reduced to 6 methods |
| M2 | Task 9 | Fixed: repositioning handles STRANDED/FAILED |
| M3 | Task 12 | Acknowledged, cleanup ownership documented |
| M4 | Task 9 | Fixed: proper speed estimate in reachability check |
| M5 | Task 9 | Fixed: post-mission uses reachable charger first |
| M6 | Task 10 | Fixed: filters unreachable AGVs |
| M7 | Task 11 | Fixed: inventory_ts matches spec schema |
| M8 | Task 1 | Fixed: public accessors for Battery, ParkingArea, LayoutGraph |
| M9 | Task 11 | Fixed: EMA first-observation initialization |
| M10 | Task 4 | Fixed: configurable pending_retry_delay |
| M11 | Task 12 | Fixed: test for submit-before-run |
| L1 | Task 10 | Fixed: == instead of is for SKU comparison |
| L2 | Task 1 | Fixed: graph.nodes property (subsumed by M8) |
| L3 | Task 12 | Fixed: tuple-based immutable config |
| L4 | — | Accepted deviation, no action |
| L5 | Task 12 | Fixed: ParkingArea added to builder |
| L6 | Tasks 6-12 | Improved: new tests cover many previously-uncovered paths; final coverage check in Task 12 |
| L7 | Task 2 | Fixed: LayoutGraph.path_distance deduplication |
| L8 | Task 12 | Documented in docstring |

## Type-Consistency Check

- `LayoutGraph.path_distance(path: list[Node]) -> float` — used in Tasks 2, 10 via `graph.path_distance(path)`. Consistent.
- `Battery.estimate_energy(distance, load_weight, speed) -> float` — used in Tasks 1, 9. Consistent with `_depletion_fn` signature.
- `ParkingArea.available_capacity -> int` — used in Task 1. `_resource.capacity - _resource.count` returns int.
- `LayoutGraph.nodes -> frozenset[Node]` — used in Task 1. `ResourceBasedTrafficManager` iterates it (frozenset is iterable). Consistent.
- `FleetCoordinator._drop_cargo(agv: AGV) -> None` — used in Tasks 5, 6, 7. Consistent.
- `FleetCoordinator._return_cargo_to_origin(order, agv) -> ProcessGenerator` — used in Tasks 5, 6, 7, 8. Returns a generator, called with `yield from`. Consistent.
- `FleetCoordinator.fleet` changes from `list[AGV]` to `tuple[AGV, ...]` in Task 12. All usage is read-only iteration — tuple is drop-in compatible.
- `DefaultIntralogisticsCollector.inventory_ts` changes from `list[tuple[...]]` to `dict[Warehouse, list[tuple[float, dict[SKU, float]]]]` in Task 11. Test consumers updated in same task.
