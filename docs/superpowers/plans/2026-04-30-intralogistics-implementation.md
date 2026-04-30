# Intralogistics Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `simulatte.intralogistics` subpackage — a standalone discrete-event simulation subsystem for warehouse-to-warehouse AGV transport, as specified in `docs/superpowers/specs/2026-04-30-intralogistics-design.md`.

**Architecture:** The subsystem is a new `simulatte.intralogistics.*` subpackage that shares only `Environment` and `SimLogger` from core simulatte. It provides a layered architecture: spatial graph → traffic control → AGV fleet → facilities → orders/policies → orchestration (FleetCoordinator). All extensibility points use `typing.Protocol`. The existing experimental AGV/Warehouse/MaterialCoordinator modules are deleted.

**Tech Stack:** Python 3.12+, SimPy 4.x, pytest, dataclasses, typing.Protocol. No new dependencies.

---

## File Map

### New files (create)

| File | Responsibility |
|---|---|
| `src/simulatte/intralogistics/__init__.py` | Public API exports |
| `src/simulatte/intralogistics/graph.py` | `Node`, `Arc`, `LayoutGraph` |
| `src/simulatte/intralogistics/pathfinding.py` | `PathPlanner` protocol, `DijkstraPlanner`, `AStarPlanner` |
| `src/simulatte/intralogistics/traffic.py` | `PathCheckResult`, `TrafficManager` protocol, `FreeTrafficManager`, `ResourceBasedTrafficManager` |
| `src/simulatte/intralogistics/sku.py` | `SKU` frozen dataclass |
| `src/simulatte/intralogistics/agv.py` | `AGVState` enum, `AGVType` frozen dataclass, `AGV` class |
| `src/simulatte/intralogistics/speed.py` | `SpeedProfile` protocol, `TrapezoidalProfile` |
| `src/simulatte/intralogistics/battery.py` | `Battery` class |
| `src/simulatte/intralogistics/warehouse.py` | `Warehouse` class |
| `src/simulatte/intralogistics/charging.py` | `ChargingStation` class |
| `src/simulatte/intralogistics/parking.py` | `ParkingArea` class |
| `src/simulatte/intralogistics/order.py` | `OrderStatus` enum, `TransferOrder` dataclass |
| `src/simulatte/intralogistics/policies.py` | `DispatchStrategy`, `ReplenishmentPolicy`, `RepositioningPolicy`, `LoadRecoveryStrategy` protocols + built-in implementations |
| `src/simulatte/intralogistics/coordinator.py` | `FleetCoordinator` orchestrator |
| `src/simulatte/intralogistics/metrics.py` | `OrderMetricsCollector`, `IntralogisticsTimeSeriesCollector` protocols + built-in implementations |
| `src/simulatte/intralogistics/builders.py` | Convenience factory functions |
| `tests/intralogistics/__init__.py` | Test package marker |
| `tests/intralogistics/conftest.py` | Shared fixtures |
| `tests/intralogistics/test_graph.py` | Tests for Node, Arc, LayoutGraph |
| `tests/intralogistics/test_pathfinding.py` | Tests for PathPlanner implementations |
| `tests/intralogistics/test_sku.py` | Tests for SKU |
| `tests/intralogistics/test_battery.py` | Tests for Battery |
| `tests/intralogistics/test_speed.py` | Tests for SpeedProfile, TrapezoidalProfile |
| `tests/intralogistics/test_agv.py` | Tests for AGVState, AGVType, AGV |
| `tests/intralogistics/test_traffic.py` | Tests for TrafficManager implementations |
| `tests/intralogistics/test_warehouse.py` | Tests for Warehouse |
| `tests/intralogistics/test_charging.py` | Tests for ChargingStation |
| `tests/intralogistics/test_parking.py` | Tests for ParkingArea |
| `tests/intralogistics/test_order.py` | Tests for OrderStatus, TransferOrder |
| `tests/intralogistics/test_policies.py` | Tests for policy protocols + built-in implementations |
| `tests/intralogistics/test_coordinator.py` | Tests for FleetCoordinator |
| `tests/intralogistics/test_metrics.py` | Tests for metrics collectors |
| `tests/intralogistics/test_integration.py` | End-to-end integration tests |

### Files to modify

| File | Change |
|---|---|
| `src/simulatte/experimental/__init__.py` | Remove AGV, Warehouse, MaterialCoordinator, etc. exports. Keep only `SimulatteEnv`. |

### Files to delete

| File | Reason |
|---|---|
| `src/simulatte/experimental/agv.py` | Replaced by `intralogistics/agv.py` |
| `src/simulatte/experimental/warehouse.py` | Replaced by `intralogistics/warehouse.py` |
| `src/simulatte/experimental/materials.py` | Replaced by `intralogistics/coordinator.py` |
| `src/simulatte/experimental/builders.py` | Replaced by `intralogistics/builders.py` |
| `src/simulatte/experimental/job.py` | Replaced by `intralogistics/order.py` |
| `src/simulatte/experimental/typing.py` | Removed entirely |
| `tests/experimental/test_warehouse_agv.py` | Replaced by `tests/intralogistics/test_*.py` |
| `tests/experimental/test_materials.py` | Replaced by `tests/intralogistics/test_coordinator.py` |
| `tests/experimental/test_integration.py` | Replaced by `tests/intralogistics/test_integration.py` |
| `tests/experimental/test_builders.py` | Replaced by `tests/intralogistics/test_integration.py` |
| `tests/experimental/test_job.py` | Replaced by `tests/intralogistics/test_order.py` |

---

## Dependency Order

Tasks must be implemented in this order. Each task's tests can import only from tasks that precede it.

```
Task 1: SKU ─────────────────────────────────────────┐
Task 2: Graph (Node, Arc, LayoutGraph) ──────────────┤
Task 3: Pathfinding ─── depends on Task 2            │
Task 4: Battery ─────────────────────────────────────┤
Task 5: Speed Profile ───────────────────────────────┤
Task 6: AGV ─── depends on Tasks 1, 2, 4, 5         │
Task 7: Traffic ─── depends on Tasks 2, 6            │
Task 8: Warehouse ─── depends on Tasks 1, 2          │
Task 9: Charging Station ─── depends on Tasks 2, 6   │
Task 10: Parking Area ─── depends on Tasks 2, 6      │
Task 11: Order ─── depends on Tasks 1, 6, 8          │
Task 12: Policies ─── depends on Tasks 2, 6, 8, 9, 10, 11 ─┤
Task 13: Metrics ─── depends on Tasks 6, 11          │
Task 14: FleetCoordinator ─── depends on ALL above ──┤
Task 15: Builders + __init__.py ─── depends on ALL   │
Task 16: Cleanup experimental/ ───────────────────────┤
Task 17: Integration Tests ───────────────────────────┘
```

---

## Task 1: SKU Model

**Files:**
- Create: `src/simulatte/intralogistics/sku.py`
- Create: `tests/intralogistics/__init__.py`
- Create: `tests/intralogistics/test_sku.py`
- Create: `src/simulatte/intralogistics/__init__.py` (empty for now)

- [ ] **Step 1: Create package markers**

Create `src/simulatte/intralogistics/__init__.py` and `tests/intralogistics/__init__.py` as empty files (just the `from __future__ import annotations` line to satisfy the ruff required-imports rule).

- [ ] **Step 2: Write SKU tests**

```python
# tests/intralogistics/test_sku.py
from __future__ import annotations

from simulatte.intralogistics.sku import SKU


class TestSKU:
    def test_creation(self) -> None:
        sku = SKU(id="STEEL-01", weight=10.0, volume=0.5)
        assert sku.id == "STEEL-01"
        assert sku.weight == 10.0
        assert sku.volume == 0.5
        assert sku.attributes == ()

    def test_with_attributes(self) -> None:
        sku = SKU(id="FRAG-01", weight=1.0, volume=0.1, attributes=(("fragile", True), ("temp_class", "cold")))
        assert sku.get_attribute("fragile") is True
        assert sku.get_attribute("temp_class") == "cold"
        assert sku.get_attribute("missing") is None

    def test_get_attribute_with_default(self) -> None:
        sku = SKU(id="X", weight=1.0, volume=0.1)
        assert sku.get_attribute("missing", default=42) == 42

    def test_frozen(self) -> None:
        sku = SKU(id="X", weight=1.0, volume=0.1)
        import pytest
        with pytest.raises(AttributeError):
            sku.id = "Y"  # type: ignore[misc]

    def test_hashable(self) -> None:
        sku1 = SKU(id="A", weight=1.0, volume=0.1)
        sku2 = SKU(id="A", weight=1.0, volume=0.1)
        assert hash(sku1) == hash(sku2)
        assert sku1 == sku2
        assert len({sku1, sku2}) == 1

    def test_different_skus_not_equal(self) -> None:
        sku1 = SKU(id="A", weight=1.0, volume=0.1)
        sku2 = SKU(id="B", weight=1.0, volume=0.1)
        assert sku1 != sku2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_sku.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'simulatte.intralogistics.sku'`

- [ ] **Step 4: Implement SKU**

```python
# src/simulatte/intralogistics/sku.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SKU:
    id: str
    weight: float
    volume: float
    attributes: tuple[tuple[str, Any], ...] = ()

    def get_attribute(self, key: str, default: Any = None) -> Any:
        for k, v in self.attributes:
            if k == key:
                return v
        return default
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_sku.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/intralogistics/__init__.py src/simulatte/intralogistics/sku.py tests/intralogistics/__init__.py tests/intralogistics/test_sku.py
git commit -m "feat(intralogistics): add SKU frozen dataclass"
```

---

## Task 2: Graph Layer (Node, Arc, LayoutGraph)

**Files:**
- Create: `src/simulatte/intralogistics/graph.py`
- Create: `tests/intralogistics/test_graph.py`

- [ ] **Step 1: Write Node and Arc tests**

```python
# tests/intralogistics/test_graph.py
from __future__ import annotations

import math

import pytest

from simulatte.intralogistics.graph import Arc, LayoutGraph, Node


class TestNode:
    def test_creation(self) -> None:
        node = Node(id="N1", x=0.0, y=0.0)
        assert node.id == "N1"
        assert node.x == 0.0
        assert node.y == 0.0

    def test_frozen(self) -> None:
        node = Node(id="N1", x=0.0, y=0.0)
        with pytest.raises(AttributeError):
            node.x = 1.0  # type: ignore[misc]

    def test_hashable(self) -> None:
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N1", x=0.0, y=0.0)
        assert n1 == n2
        assert len({n1, n2}) == 1


class TestArc:
    def test_bidirectional_default(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        arc = Arc(source=n1, target=n2)
        assert arc.bidirectional is True
        assert arc.speed_limit is None

    def test_unidirectional(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        arc = Arc(source=n1, target=n2, bidirectional=False)
        assert arc.bidirectional is False

    def test_speed_limit(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        arc = Arc(source=n1, target=n2, speed_limit=2.5)
        assert arc.speed_limit == 2.5


class TestLayoutGraph:
    def _make_line_graph(self) -> tuple[list[Node], LayoutGraph]:
        nodes = [Node(id=f"N{i}", x=float(i), y=0.0) for i in range(4)]
        arcs = [Arc(source=nodes[i], target=nodes[i + 1]) for i in range(3)]
        return nodes, LayoutGraph(nodes, arcs)

    def test_neighbors_bidirectional(self) -> None:
        nodes, graph = self._make_line_graph()
        assert set(graph.neighbors(nodes[1])) == {nodes[0], nodes[2]}

    def test_neighbors_unidirectional(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2, bidirectional=False)])
        assert graph.neighbors(n1) == [n2]
        assert graph.neighbors(n2) == []

    def test_distance(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=3.0, y=4.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        assert graph.distance(n1, n2) == pytest.approx(5.0)

    def test_distance_not_connected_raises(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        with pytest.raises(ValueError, match="not connected"):
            graph.distance(n1, n2)

    def test_arc_between(self) -> None:
        nodes, graph = self._make_line_graph()
        arc = graph.arc_between(nodes[0], nodes[1])
        assert arc is not None
        assert arc.source == nodes[0]
        assert arc.target == nodes[1]

    def test_arc_between_bidirectional_reverse(self) -> None:
        nodes, graph = self._make_line_graph()
        arc = graph.arc_between(nodes[1], nodes[0])
        assert arc is not None

    def test_arc_between_none(self) -> None:
        nodes, graph = self._make_line_graph()
        assert graph.arc_between(nodes[0], nodes[2]) is None

    def test_shortest_path(self) -> None:
        nodes, graph = self._make_line_graph()
        path = graph.shortest_path(nodes[0], nodes[3])
        assert path == [nodes[0], nodes[1], nodes[2], nodes[3]]

    def test_shortest_path_no_route(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        assert graph.shortest_path(n1, n2) is None

    def test_shortest_path_same_node(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        assert graph.shortest_path(n1, n1) == [n1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Node, Arc, LayoutGraph**

```python
# src/simulatte/intralogistics/graph.py
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float


@dataclass(frozen=True)
class Arc:
    source: Node
    target: Node
    bidirectional: bool = True
    speed_limit: float | None = None


class LayoutGraph:
    def __init__(self, nodes: Iterable[Node], arcs: Iterable[Arc]) -> None:
        self._nodes: set[Node] = set(nodes)
        self._adjacency: dict[Node, dict[Node, Arc]] = defaultdict(dict)
        for arc in arcs:
            self._adjacency[arc.source][arc.target] = arc
            if arc.bidirectional:
                self._adjacency[arc.target][arc.source] = arc

    def neighbors(self, node: Node) -> list[Node]:
        return list(self._adjacency[node].keys())

    def arc_between(self, source: Node, target: Node) -> Arc | None:
        return self._adjacency[source].get(target)

    def distance(self, source: Node, target: Node) -> float:
        if self.arc_between(source, target) is None:
            raise ValueError(f"Nodes {source.id} and {target.id} are not connected by an arc")
        return math.hypot(target.x - source.x, target.y - source.y)

    def shortest_path(self, source: Node, target: Node) -> list[Node] | None:
        if source == target:
            return [source]
        dist: dict[Node, float] = {source: 0.0}
        prev: dict[Node, Node] = {}
        heap: list[tuple[float, str, Node]] = [(0.0, source.id, source)]
        while heap:
            d, _, node = heapq.heappop(heap)
            if node == target:
                path = []
                current = target
                while current in prev:
                    path.append(current)
                    current = prev[current]
                path.append(source)
                return list(reversed(path))
            if d > dist.get(node, float("inf")):
                continue
            for neighbor in self.neighbors(node):
                edge_dist = math.hypot(neighbor.x - node.x, neighbor.y - node.y)
                new_dist = d + edge_dist
                if new_dist < dist.get(neighbor, float("inf")):
                    dist[neighbor] = new_dist
                    prev[neighbor] = node
                    heapq.heappush(heap, (new_dist, neighbor.id, neighbor))
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_graph.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/graph.py tests/intralogistics/test_graph.py
git commit -m "feat(intralogistics): add Node, Arc, LayoutGraph spatial layer"
```

---

## Task 3: Pathfinding

**Files:**
- Create: `src/simulatte/intralogistics/pathfinding.py`
- Create: `tests/intralogistics/test_pathfinding.py`

- [ ] **Step 1: Write pathfinding tests**

```python
# tests/intralogistics/test_pathfinding.py
from __future__ import annotations

import pytest

from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.pathfinding import AStarPlanner, DijkstraPlanner


def _make_grid() -> tuple[dict[str, Node], LayoutGraph]:
    """2x2 grid: A(0,0) -- B(1,0)
                  |          |
                 C(0,1) -- D(1,1)"""
    nodes = {
        "A": Node(id="A", x=0.0, y=0.0),
        "B": Node(id="B", x=1.0, y=0.0),
        "C": Node(id="C", x=0.0, y=1.0),
        "D": Node(id="D", x=1.0, y=1.0),
    }
    arcs = [
        Arc(source=nodes["A"], target=nodes["B"]),
        Arc(source=nodes["A"], target=nodes["C"]),
        Arc(source=nodes["B"], target=nodes["D"]),
        Arc(source=nodes["C"], target=nodes["D"]),
    ]
    return nodes, LayoutGraph(nodes.values(), arcs)


class TestDijkstraPlanner:
    def test_shortest_path(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"])
        assert path is not None
        assert path[0] == nodes["A"]
        assert path[-1] == nodes["D"]

    def test_no_path(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        planner = DijkstraPlanner()
        assert planner.plan(graph, n1, n2) is None

    def test_avoid_nodes(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"]])
        assert path is not None
        assert nodes["B"] not in path
        assert path == [nodes["A"], nodes["C"], nodes["D"]]

    def test_avoid_makes_unreachable(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"], nodes["C"]])
        assert path is None

    def test_same_node(self) -> None:
        nodes, graph = _make_grid()
        planner = DijkstraPlanner()
        path = planner.plan(graph, nodes["A"], nodes["A"])
        assert path == [nodes["A"]]


class TestAStarPlanner:
    def test_shortest_path(self) -> None:
        nodes, graph = _make_grid()
        planner = AStarPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"])
        assert path is not None
        assert path[0] == nodes["A"]
        assert path[-1] == nodes["D"]

    def test_avoid_nodes(self) -> None:
        nodes, graph = _make_grid()
        planner = AStarPlanner()
        path = planner.plan(graph, nodes["A"], nodes["D"], avoid=[nodes["B"]])
        assert path is not None
        assert nodes["B"] not in path

    def test_no_path(self) -> None:
        n1 = Node(id="A", x=0.0, y=0.0)
        n2 = Node(id="B", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [])
        planner = AStarPlanner()
        assert planner.plan(graph, n1, n2) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_pathfinding.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement DijkstraPlanner and AStarPlanner**

```python
# src/simulatte/intralogistics/pathfinding.py
from __future__ import annotations

import heapq
import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simulatte.intralogistics.graph import LayoutGraph, Node


@runtime_checkable
class PathPlanner(Protocol):
    def plan(
        self,
        graph: LayoutGraph,
        origin: Node,
        destination: Node,
        avoid: list[Node] | None = None,
    ) -> list[Node] | None: ...


class DijkstraPlanner:
    def plan(
        self,
        graph: LayoutGraph,
        origin: Node,
        destination: Node,
        avoid: list[Node] | None = None,
    ) -> list[Node] | None:
        if origin == destination:
            return [origin]
        avoid_set = set(avoid) if avoid else set()
        dist: dict[Node, float] = {origin: 0.0}
        prev: dict[Node, Node] = {}
        heap: list[tuple[float, str, Node]] = [(0.0, origin.id, origin)]
        while heap:
            d, _, node = heapq.heappop(heap)
            if node == destination:
                path: list[Node] = []
                current = destination
                while current in prev:
                    path.append(current)
                    current = prev[current]
                path.append(origin)
                return list(reversed(path))
            if d > dist.get(node, float("inf")):
                continue
            for neighbor in graph.neighbors(node):
                if neighbor in avoid_set:
                    continue
                edge_dist = math.hypot(neighbor.x - node.x, neighbor.y - node.y)
                new_dist = d + edge_dist
                if new_dist < dist.get(neighbor, float("inf")):
                    dist[neighbor] = new_dist
                    prev[neighbor] = node
                    heapq.heappush(heap, (new_dist, neighbor.id, neighbor))
        return None


class AStarPlanner:
    def plan(
        self,
        graph: LayoutGraph,
        origin: Node,
        destination: Node,
        avoid: list[Node] | None = None,
    ) -> list[Node] | None:
        if origin == destination:
            return [origin]
        avoid_set = set(avoid) if avoid else set()

        def heuristic(node: Node) -> float:
            return math.hypot(destination.x - node.x, destination.y - node.y)

        g_score: dict[Node, float] = {origin: 0.0}
        prev: dict[Node, Node] = {}
        heap: list[tuple[float, float, str, Node]] = [(heuristic(origin), 0.0, origin.id, origin)]
        while heap:
            _, g, _, node = heapq.heappop(heap)
            if node == destination:
                path: list[Node] = []
                current = destination
                while current in prev:
                    path.append(current)
                    current = prev[current]
                path.append(origin)
                return list(reversed(path))
            if g > g_score.get(node, float("inf")):
                continue
            for neighbor in graph.neighbors(node):
                if neighbor in avoid_set:
                    continue
                edge_dist = math.hypot(neighbor.x - node.x, neighbor.y - node.y)
                new_g = g + edge_dist
                if new_g < g_score.get(neighbor, float("inf")):
                    g_score[neighbor] = new_g
                    prev[neighbor] = node
                    f = new_g + heuristic(neighbor)
                    heapq.heappush(heap, (f, new_g, neighbor.id, neighbor))
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_pathfinding.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/pathfinding.py tests/intralogistics/test_pathfinding.py
git commit -m "feat(intralogistics): add PathPlanner protocol with Dijkstra and A* implementations"
```

---

## Task 4: Battery

**Files:**
- Create: `src/simulatte/intralogistics/battery.py`
- Create: `tests/intralogistics/test_battery.py`

- [ ] **Step 1: Write battery tests**

```python
# tests/intralogistics/test_battery.py
from __future__ import annotations

import pytest

from simulatte.intralogistics.battery import Battery


class TestBattery:
    def test_creation_defaults(self) -> None:
        b = Battery(capacity=100.0)
        assert b.capacity == 100.0
        assert b.level == 100.0
        assert b.level_pct == pytest.approx(1.0)
        assert b.is_low is False
        assert b.is_critical is False

    def test_creation_partial_charge(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        assert b.level == 50.0
        assert b.level_pct == pytest.approx(0.5)

    def test_deplete_default_fn(self) -> None:
        b = Battery(capacity=100.0)
        b.deplete(distance=10.0, load_weight=0.0, speed=1.0)
        assert b.level < 100.0

    def test_deplete_custom_fn(self) -> None:
        def custom_depletion(distance: float, load_weight: float, speed: float) -> float:
            return distance * 2.0 + load_weight * 0.1

        b = Battery(capacity=100.0, depletion_fn=custom_depletion)
        b.deplete(distance=10.0, load_weight=50.0, speed=1.0)
        assert b.level == pytest.approx(100.0 - 25.0)

    def test_deplete_clamps_at_zero(self) -> None:
        b = Battery(capacity=10.0)
        b.deplete(distance=1000.0, load_weight=0.0, speed=1.0)
        assert b.level == 0.0

    def test_is_low_threshold(self) -> None:
        b = Battery(capacity=100.0, initial_level=20.0, low_threshold=0.2)
        assert b.is_low is True
        assert b.is_critical is False

    def test_is_critical_threshold(self) -> None:
        b = Battery(capacity=100.0, initial_level=5.0, critical_threshold=0.05)
        assert b.is_critical is True

    def test_recharge_time_default(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        t = b.recharge_time(target_pct=1.0)
        assert t > 0

    def test_recharge_time_custom_fn(self) -> None:
        def custom_recharge(current: float, target: float) -> float:
            return (target - current) * 0.5

        b = Battery(capacity=100.0, initial_level=60.0, recharge_fn=custom_recharge)
        t = b.recharge_time(target_pct=1.0)
        assert t == pytest.approx(20.0)

    def test_recharge(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        b.recharge(amount=30.0)
        assert b.level == pytest.approx(80.0)

    def test_recharge_clamps_at_capacity(self) -> None:
        b = Battery(capacity=100.0, initial_level=90.0)
        b.recharge(amount=50.0)
        assert b.level == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_battery.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Battery**

```python
# src/simulatte/intralogistics/battery.py
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def _default_depletion(distance: float, load_weight: float, speed: float) -> float:  # noqa: ARG001
    return distance * 1.0


def _default_recharge(current_level: float, target_level: float) -> float:
    return (target_level - current_level) * 1.0


class Battery:
    def __init__(
        self,
        capacity: float,
        initial_level: float | None = None,
        depletion_fn: Callable[[float, float, float], float] | None = None,
        recharge_fn: Callable[[float, float], float] | None = None,
        low_threshold: float = 0.2,
        critical_threshold: float = 0.05,
    ) -> None:
        self.capacity = capacity
        self.level = initial_level if initial_level is not None else capacity
        self._depletion_fn = depletion_fn or _default_depletion
        self._recharge_fn = recharge_fn or _default_recharge
        self.low_threshold = low_threshold
        self.critical_threshold = critical_threshold

    @property
    def level_pct(self) -> float:
        return self.level / self.capacity if self.capacity > 0 else 0.0

    @property
    def is_low(self) -> bool:
        return self.level_pct <= self.low_threshold

    @property
    def is_critical(self) -> bool:
        return self.level_pct <= self.critical_threshold

    def deplete(self, distance: float, load_weight: float, speed: float) -> None:
        consumed = self._depletion_fn(distance, load_weight, speed)
        self.level = max(0.0, self.level - consumed)

    def recharge_time(self, target_pct: float = 1.0) -> float:
        target_level = target_pct * self.capacity
        if target_level <= self.level:
            return 0.0
        return self._recharge_fn(self.level, target_level)

    def recharge(self, amount: float) -> None:
        self.level = min(self.capacity, self.level + amount)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_battery.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/battery.py tests/intralogistics/test_battery.py
git commit -m "feat(intralogistics): add Battery with depletion/recharge functions"
```

---

## Task 5: Speed Profile

**Files:**
- Create: `src/simulatte/intralogistics/speed.py`
- Create: `tests/intralogistics/test_speed.py`

- [ ] **Step 1: Write speed profile tests**

```python
# tests/intralogistics/test_speed.py
from __future__ import annotations

import pytest

from simulatte.intralogistics.speed import TrapezoidalProfile


class TestTrapezoidalProfile:
    def test_long_arc_trapezoidal(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        t = profile.travel_time(distance=10.0)
        # Accel phase: 2s to reach 2m/s, covers 2m
        # Decel phase: 2s from 2m/s to 0, covers 2m
        # Cruise phase: 6m at 2m/s = 3s
        # Total: 2 + 3 + 2 = 7s
        assert t == pytest.approx(7.0)

    def test_short_arc_triangular(self) -> None:
        profile = TrapezoidalProfile(max_speed=10.0, acceleration=1.0, deceleration=1.0)
        t = profile.travel_time(distance=2.0)
        # Can't reach max_speed. Triangular profile.
        # v_peak = sqrt(2 * d * a * dec / (a + dec)) = sqrt(2 * 2 * 1 * 1 / 2) = sqrt(2)
        # t = v_peak/a + v_peak/dec = 2*sqrt(2) ≈ 2.828
        assert t == pytest.approx(2 * (2.0**0.5), rel=1e-6)

    def test_zero_distance(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        assert profile.travel_time(distance=0.0) == 0.0

    def test_speed_limit_caps(self) -> None:
        profile = TrapezoidalProfile(max_speed=10.0, acceleration=1.0, deceleration=1.0)
        t_limited = profile.travel_time(distance=20.0, speed_limit=2.0)
        t_unlimited = profile.travel_time(distance=20.0)
        assert t_limited > t_unlimited

    def test_battery_degradation(self) -> None:
        profile = TrapezoidalProfile(
            max_speed=2.0,
            acceleration=1.0,
            deceleration=1.0,
            battery_degradation_fn=lambda lvl: lvl,
        )
        t_full = profile.travel_time(distance=10.0, battery_level=1.0)
        t_half = profile.travel_time(distance=10.0, battery_level=0.5)
        assert t_half > t_full

    def test_load_speed_factor(self) -> None:
        profile = TrapezoidalProfile(
            max_speed=2.0,
            acceleration=1.0,
            deceleration=1.0,
            load_speed_factor_fn=lambda w: max(0.1, 1.0 - w / 100.0),
        )
        t_empty = profile.travel_time(distance=10.0, load_weight=0.0)
        t_loaded = profile.travel_time(distance=10.0, load_weight=50.0)
        assert t_loaded > t_empty

    def test_protocol_conformance(self) -> None:
        from simulatte.intralogistics.speed import SpeedProfile
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        assert isinstance(profile, SpeedProfile)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_speed.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement SpeedProfile and TrapezoidalProfile**

```python
# src/simulatte/intralogistics/speed.py
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class SpeedProfile(Protocol):
    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float: ...


class TrapezoidalProfile:
    def __init__(
        self,
        max_speed: float,
        acceleration: float,
        deceleration: float,
        battery_degradation_fn: Callable[[float], float] | None = None,
        load_speed_factor_fn: Callable[[float], float] | None = None,
    ) -> None:
        self._max_speed = max_speed
        self._acceleration = acceleration
        self._deceleration = deceleration
        self._battery_degradation_fn = battery_degradation_fn or (lambda _: 1.0)
        self._load_speed_factor_fn = load_speed_factor_fn or (lambda _: 1.0)

    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float:
        if distance <= 0:
            return 0.0

        battery_factor = self._battery_degradation_fn(battery_level)
        load_factor = self._load_speed_factor_fn(load_weight)
        factor = battery_factor * load_factor

        if factor <= 0:
            return float("inf")

        v_max = self._max_speed * factor
        if speed_limit is not None:
            v_max = min(v_max, speed_limit)

        accel = self._acceleration * factor
        decel = self._deceleration * factor

        d_accel = v_max**2 / (2 * accel)
        d_decel = v_max**2 / (2 * decel)

        if d_accel + d_decel <= distance:
            t_accel = v_max / accel
            t_decel = v_max / decel
            t_cruise = (distance - d_accel - d_decel) / v_max
            return t_accel + t_cruise + t_decel
        else:
            v_peak = math.sqrt(2 * distance * accel * decel / (accel + decel))
            return v_peak / accel + v_peak / decel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_speed.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/speed.py tests/intralogistics/test_speed.py
git commit -m "feat(intralogistics): add SpeedProfile protocol and TrapezoidalProfile"
```

---

## Task 6: AGV Layer (AGVState, AGVType, AGV)

**Files:**
- Create: `src/simulatte/intralogistics/agv.py`
- Create: `tests/intralogistics/test_agv.py`
- Create: `tests/intralogistics/conftest.py`

- [ ] **Step 1: Write conftest with shared fixtures**

```python
# tests/intralogistics/conftest.py
from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def simple_speed_profile() -> TrapezoidalProfile:
    return TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)


@pytest.fixture
def node_a() -> Node:
    return Node(id="A", x=0.0, y=0.0)


@pytest.fixture
def node_b() -> Node:
    return Node(id="B", x=10.0, y=0.0)


@pytest.fixture
def steel_sku() -> SKU:
    return SKU(id="STEEL", weight=10.0, volume=0.5)
```

- [ ] **Step 2: Write AGV tests**

```python
# tests/intralogistics/test_agv.py
from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile


class TestAGVState:
    def test_all_states_exist(self) -> None:
        assert len(AGVState) == 7
        assert AGVState.IDLE
        assert AGVState.TRAVELING_EMPTY
        assert AGVState.WAITING_LOAD
        assert AGVState.TRAVELING_LOADED
        assert AGVState.WAITING_UNLOAD
        assert AGVState.CHARGING
        assert AGVState.STRANDED


class TestAGVType:
    def test_frozen(self, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="standard",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        with pytest.raises(AttributeError):
            agv_type.name = "other"  # type: ignore[misc]

    def test_defaults(self, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="standard",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        sku = SKU(id="any", weight=1.0, volume=0.1)
        assert agv_type.compatibility_fn(sku) is True
        assert agv_type.load_time_fn() == 0.0
        assert agv_type.unload_time_fn() == 0.0


class TestAGV:
    def _make_agv(self, env: Environment, profile: TrapezoidalProfile, node: Node | None = None) -> AGV:
        agv_type = AGVType(
            name="test",
            speed_profile=profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        return AGV(env=env, agv_type=agv_type, initial_node=node)

    def test_creation(self, env: Environment, simple_speed_profile: TrapezoidalProfile, node_a: Node) -> None:
        agv = self._make_agv(env, simple_speed_profile, node_a)
        assert agv.state == AGVState.IDLE
        assert agv.current_node == node_a
        assert agv.current_load is None
        assert agv.battery.level == 100.0

    def test_auto_id(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.agv_id is not None
        assert len(agv.agv_id) > 0

    def test_custom_id(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="test",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="my-agv-1")
        assert agv.agv_id == "my-agv-1"

    def test_can_carry_compatible(self, env: Environment, simple_speed_profile: TrapezoidalProfile, steel_sku: SKU) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.can_carry(steel_sku, quantity=10) is True

    def test_can_carry_exceeds_weight(self, env: Environment, simple_speed_profile: TrapezoidalProfile, steel_sku: SKU) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.can_carry(steel_sku, quantity=100) is False

    def test_can_carry_incompatible_sku(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="test",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
            compatibility_fn=lambda sku: sku.id != "HAZMAT",
        )
        agv = AGV(env=env, agv_type=agv_type)
        hazmat = SKU(id="HAZMAT", weight=1.0, volume=0.1)
        assert agv.can_carry(hazmat, quantity=1) is False

    def test_transition_to(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.state == AGVState.IDLE

        env.run(until=5.0)
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        assert agv.state == AGVState.TRAVELING_EMPTY
        assert agv.state_durations[AGVState.IDLE] == pytest.approx(5.0)

        env.run(until=8.0)
        agv.transition_to(AGVState.WAITING_LOAD)
        assert agv.state_durations[AGVState.TRAVELING_EMPTY] == pytest.approx(3.0)

    def test_utilization(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=5.0)
        agv.transition_to(AGVState.TRAVELING_LOADED)
        env.run(until=10.0)
        agv.transition_to(AGVState.IDLE)
        assert agv.utilization() == pytest.approx(0.5)

    def test_time_allocation(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=10.0)
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        env.run(until=20.0)
        agv.transition_to(AGVState.IDLE)
        alloc = agv.time_allocation()
        assert alloc[AGVState.IDLE] == pytest.approx(0.5)
        assert alloc[AGVState.TRAVELING_EMPTY] == pytest.approx(0.5)

    def test_state_percentage(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=10.0)
        agv.transition_to(AGVState.CHARGING)
        env.run(until=20.0)
        agv.transition_to(AGVState.IDLE)
        assert agv.state_percentage(AGVState.CHARGING) == pytest.approx(0.5)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_agv.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement AGVState, AGVType, AGV**

```python
# src/simulatte/intralogistics/agv.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from simulatte.intralogistics.battery import Battery

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import Node
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.speed import SpeedProfile


class AGVState(Enum):
    IDLE = auto()
    TRAVELING_EMPTY = auto()
    WAITING_LOAD = auto()
    TRAVELING_LOADED = auto()
    WAITING_UNLOAD = auto()
    CHARGING = auto()
    STRANDED = auto()


_UTILIZED_STATES = frozenset({
    AGVState.TRAVELING_EMPTY,
    AGVState.WAITING_LOAD,
    AGVState.TRAVELING_LOADED,
    AGVState.WAITING_UNLOAD,
})


@dataclass(frozen=True)
class AGVType:
    name: str
    speed_profile: SpeedProfile
    battery_capacity: float
    weight_capacity: float
    volume_capacity: float
    compatibility_fn: Callable[[Any], bool] = field(default=lambda sku: True)  # noqa: ARG005
    depletion_fn: Callable[[float, float, float], float] | None = None
    recharge_fn: Callable[[float, float], float] | None = None
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    load_time_fn: Callable[[], float] = field(default=lambda: 0.0)
    unload_time_fn: Callable[[], float] = field(default=lambda: 0.0)


class AGV:
    def __init__(
        self,
        *,
        env: Environment,
        agv_type: AGVType,
        agv_id: str | None = None,
        initial_node: Node | None = None,
    ) -> None:
        self.env = env
        self.agv_type = agv_type
        self.agv_id = agv_id or f"agv-{uuid.uuid4().hex[:8]}"
        self.current_node = initial_node
        self.current_load: dict[SKU, int] | None = None

        self.battery = Battery(
            capacity=agv_type.battery_capacity,
            depletion_fn=agv_type.depletion_fn,
            recharge_fn=agv_type.recharge_fn,
            low_threshold=agv_type.low_battery_threshold,
            critical_threshold=agv_type.critical_battery_threshold,
        )

        self._state = AGVState.IDLE
        self._state_entered_at: float = env.now
        self.state_durations: dict[AGVState, float] = {s: 0.0 for s in AGVState}

    @property
    def state(self) -> AGVState:
        return self._state

    def transition_to(self, new_state: AGVState) -> None:
        elapsed = self.env.now - self._state_entered_at
        self.state_durations[self._state] += elapsed
        self._state = new_state
        self._state_entered_at = self.env.now

    def can_carry(self, sku: SKU, quantity: int) -> bool:
        if not self.agv_type.compatibility_fn(sku):
            return False
        total_weight = sku.weight * quantity
        total_volume = sku.volume * quantity
        return total_weight <= self.agv_type.weight_capacity and total_volume <= self.agv_type.volume_capacity

    def utilization(self) -> float:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return 0.0
        utilized = sum(self.state_durations[s] for s in _UTILIZED_STATES)
        return utilized / total

    def state_percentage(self, state: AGVState) -> float:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return 0.0
        return self.state_durations[state] / total

    def time_allocation(self) -> dict[AGVState, float]:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return {s: 0.0 for s in AGVState}
        return {s: self.state_durations[s] / total for s in AGVState}

    def _flush_current_state(self) -> None:
        elapsed = self.env.now - self._state_entered_at
        if elapsed > 0:
            self.state_durations[self._state] += elapsed
            self._state_entered_at = self.env.now

    def __repr__(self) -> str:
        return f"AGV(id={self.agv_id!r}, state={self._state.name})"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_agv.py -v`
Expected: All 13 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/intralogistics/agv.py tests/intralogistics/conftest.py tests/intralogistics/test_agv.py
git commit -m "feat(intralogistics): add AGVState, AGVType, AGV with utilization tracking"
```

---

## Task 7: Traffic Layer

**Files:**
- Create: `src/simulatte/intralogistics/traffic.py`
- Create: `tests/intralogistics/test_traffic.py`

- [ ] **Step 1: Write traffic tests**

```python
# tests/intralogistics/test_traffic.py
from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeTrafficManager, PathCheckResult, ResourceBasedTrafficManager


def _make_agv(env: Environment, node: Node | None = None) -> AGV:
    profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
    agv_type = AGVType(
        name="test", speed_profile=profile, battery_capacity=100.0,
        weight_capacity=500.0, volume_capacity=2.0,
    )
    return AGV(env=env, agv_type=agv_type, initial_node=node)


class TestPathCheckResult:
    def test_feasible(self) -> None:
        r = PathCheckResult(feasible=True)
        assert r.feasible is True
        assert r.conflict_nodes is None

    def test_infeasible_with_conflicts(self) -> None:
        n = Node(id="X", x=0.0, y=0.0)
        r = PathCheckResult(feasible=False, conflict_nodes=[n])
        assert r.feasible is False
        assert r.conflict_nodes == [n]


class TestFreeTrafficManager:
    def test_check_path_always_feasible(self) -> None:
        tm = FreeTrafficManager()
        agv = _make_agv(Environment())
        result = tm.check_path(agv, [])
        assert result.feasible is True

    def test_place_is_noop(self) -> None:
        env = Environment()
        tm = FreeTrafficManager()
        node = Node(id="A", x=0.0, y=0.0)
        agv = _make_agv(env, node)

        def run():
            yield from tm.place(agv, node)

        env.process(run())
        env.run()

    def test_enter_leave_are_noops(self) -> None:
        env = Environment()
        tm = FreeTrafficManager()
        node = Node(id="A", x=0.0, y=0.0)
        agv = _make_agv(env, node)

        def run():
            yield from tm.enter_node(agv, node)
            tm.leave_node(agv, node)

        env.process(run())
        env.run()


class TestResourceBasedTrafficManager:
    def test_place_acquires_node(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        def run():
            yield from tm.place(agv, n1)

        env.process(run())
        env.run()
        assert tm._node_resources[n1].count == 1

    def test_enter_blocks_when_full(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n1)
        entered = []

        def occupy():
            yield from tm.place(agv1, n2)
            yield env.timeout(10.0)
            tm.leave_node(agv1, n2)

        def try_enter():
            yield from tm.enter_node(agv2, n2)
            entered.append(env.now)

        env.process(occupy())
        env.process(try_enter())
        env.run()

        assert entered[0] == pytest.approx(10.0)

    def test_register_intent_and_cancel(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        tm.register_intent(agv, [n1, n2])
        assert agv in tm._intents
        tm.cancel(agv)
        assert agv not in tm._intents

    def test_check_path_detects_head_on_conflict(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=2.0, y=0.0)
        graph = LayoutGraph([n1, n2, n3], [
            Arc(source=n1, target=n2),
            Arc(source=n2, target=n3),
        ])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)

        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n3)

        tm.register_intent(agv1, [n1, n2, n3])
        result = tm.check_path(agv2, [n3, n2, n1])
        assert result.feasible is False
        assert result.conflict_nodes is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_traffic.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement TrafficManager protocol and both implementations**

```python
# src/simulatte/intralogistics/traffic.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import simpy

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import LayoutGraph, Node


@dataclass
class PathCheckResult:
    feasible: bool
    conflict_nodes: list[Node] | None = None
    delay_until: float | None = None


@runtime_checkable
class TrafficManager(Protocol):
    def place(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult: ...
    def register_intent(self, agv: AGV, path: list[Node]) -> None: ...
    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def leave_node(self, agv: AGV, node: Node) -> None: ...
    def cancel(self, agv: AGV) -> None: ...


class FreeTrafficManager:
    def place(self, agv: AGV, node: Node) -> ProcessGenerator:  # noqa: ARG002
        return
        yield  # make it a generator

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:  # noqa: ARG002
        return PathCheckResult(feasible=True)

    def register_intent(self, agv: AGV, path: list[Node]) -> None:  # noqa: ARG002
        pass

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:  # noqa: ARG002
        return
        yield  # make it a generator

    def leave_node(self, agv: AGV, node: Node) -> None:  # noqa: ARG002
        pass

    def cancel(self, agv: AGV) -> None:  # noqa: ARG002
        pass


class ResourceBasedTrafficManager:
    def __init__(
        self,
        *,
        graph: LayoutGraph,
        env: Environment,
        node_capacity: int = 1,
        deadlock_timeout: float = 30.0,
        priority_fn: Callable[[AGV], float] | None = None,
    ) -> None:
        self._env = env
        self._graph = graph
        self._node_capacity = node_capacity
        self._deadlock_timeout = deadlock_timeout
        self._priority_fn = priority_fn or (lambda agv: 0.0)
        self._node_resources: dict[Node, simpy.Resource] = {}
        self._node_requests: dict[tuple[AGV, Node], simpy.resources.resource.Request] = {}
        self._intents: dict[AGV, list[Node]] = {}

        for node in graph._nodes:
            self._node_resources[node] = simpy.Resource(env, capacity=node_capacity)

    def place(self, agv: AGV, node: Node) -> ProcessGenerator:
        resource = self._node_resources[node]
        req = resource.request()
        self._node_requests[(agv, node)] = req
        yield req

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:
        if len(path) < 2:
            return PathCheckResult(feasible=True)

        path_set = set(path[1:])
        conflict_nodes: list[Node] = []

        for other_agv, other_path in self._intents.items():
            if other_agv is agv:
                continue
            other_set = set(other_path)
            shared = path_set & other_set
            if not shared:
                continue
            for node in path:
                if node in other_set:
                    other_idx = other_path.index(node) if node in other_path else -1
                    path_idx = path.index(node)
                    if other_idx >= 0 and path_idx >= 0:
                        if self._is_head_on(path, other_path, node):
                            conflict_nodes.append(node)

        if conflict_nodes:
            return PathCheckResult(feasible=False, conflict_nodes=conflict_nodes)
        return PathCheckResult(feasible=True)

    def _is_head_on(self, path_a: list[Node], path_b: list[Node], shared_node: Node) -> bool:
        try:
            idx_a = path_a.index(shared_node)
            idx_b = path_b.index(shared_node)
        except ValueError:
            return False
        if idx_a > 0 and idx_b > 0:
            if path_a[idx_a - 1] in path_b[idx_b:] or path_b[idx_b - 1] in path_a[idx_a:]:
                return True
        return False

    def register_intent(self, agv: AGV, path: list[Node]) -> None:
        self._intents[agv] = list(path)

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:
        resource = self._node_resources[node]
        req = resource.request()
        self._node_requests[(agv, node)] = req
        yield req

    def leave_node(self, agv: AGV, node: Node) -> None:
        key = (agv, node)
        if key in self._node_requests:
            resource = self._node_resources[node]
            resource.release(self._node_requests.pop(key))

    def cancel(self, agv: AGV) -> None:
        self._intents.pop(agv, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_traffic.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/traffic.py tests/intralogistics/test_traffic.py
git commit -m "feat(intralogistics): add TrafficManager protocol with Free and ResourceBased implementations"
```

---

## Task 8: Warehouse

**Files:**
- Create: `src/simulatte/intralogistics/warehouse.py`
- Create: `tests/intralogistics/test_warehouse.py`

- [ ] **Step 1: Write warehouse tests**

```python
# tests/intralogistics/test_warehouse.py
from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.warehouse import Warehouse


class TestWarehouse:
    def _make_warehouse(self, env: Environment) -> tuple[Warehouse, list[Node]]:
        in_bay = Node(id="IN", x=0.0, y=0.0)
        out_bay = Node(id="OUT", x=1.0, y=0.0)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        bolts = SKU(id="BOLTS", weight=0.1, volume=0.01)
        wh = Warehouse(
            env=env,
            name="WH-A",
            input_bays=[in_bay],
            output_bays=[out_bay],
            n_slots=2,
            products=[steel, bolts],
            initial_inventory={steel: 100, bolts: 500},
            pick_time_fn=lambda sku, qty: 2.0,
            put_time_fn=lambda sku, qty: 1.0,
        )
        return wh, [in_bay, out_bay]

    def test_creation(self, env: Environment) -> None:
        wh, nodes = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        assert wh.name == "WH-A"
        assert wh.get_inventory_level(steel) == 100

    def test_pick_success(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def do_pick():
            yield from wh.pick(steel, 10)

        env.process(do_pick())
        env.run()
        assert wh.get_inventory_level(steel) == 90
        assert wh.total_picks == 1
        assert env.now == pytest.approx(2.0)

    def test_pick_blocks_on_empty(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        events: list[tuple[str, float]] = []

        def do_pick():
            yield from wh.pick(steel, 200)
            events.append(("picked", env.now))

        def restock():
            yield env.timeout(5.0)
            yield from wh.put(steel, 200)
            events.append(("restocked", env.now))

        env.process(do_pick())
        env.process(restock())
        env.run()

        assert events[0] == ("restocked", pytest.approx(6.0))
        assert events[1] == ("picked", pytest.approx(8.0))

    def test_put(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def do_put():
            yield from wh.put(steel, 50)

        env.process(do_put())
        env.run()
        assert wh.get_inventory_level(steel) == 150
        assert wh.total_puts == 1

    def test_pick_does_not_deadlock_with_put(self, env: Environment) -> None:
        in_bay = Node(id="IN", x=0.0, y=0.0)
        out_bay = Node(id="OUT", x=1.0, y=0.0)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        wh = Warehouse(
            env=env,
            name="WH",
            input_bays=[in_bay],
            output_bays=[out_bay],
            n_slots=1,
            products=[steel],
            initial_inventory={steel: 0},
            pick_time_fn=lambda sku, qty: 1.0,
            put_time_fn=lambda sku, qty: 1.0,
        )
        completed: list[str] = []

        def pick_task():
            yield from wh.pick(steel, 10)
            completed.append("pick")

        def put_task():
            yield env.timeout(1.0)
            yield from wh.put(steel, 10)
            completed.append("put")

        env.process(pick_task())
        env.process(put_task())
        env.run()

        assert "put" in completed
        assert "pick" in completed

    def test_nearest_output_bay(self, env: Environment) -> None:
        out1 = Node(id="OUT1", x=0.0, y=0.0)
        out2 = Node(id="OUT2", x=10.0, y=0.0)
        in1 = Node(id="IN1", x=5.0, y=5.0)
        agv_pos = Node(id="AGV", x=9.0, y=0.0)
        steel = SKU(id="S", weight=1.0, volume=0.1)
        nodes = [out1, out2, in1, agv_pos]
        arcs = [
            Arc(source=agv_pos, target=out1),
            Arc(source=agv_pos, target=out2),
            Arc(source=agv_pos, target=in1),
        ]
        graph = LayoutGraph(nodes, arcs)
        wh = Warehouse(
            env=env, name="WH", input_bays=[in1], output_bays=[out1, out2],
            n_slots=1, products=[steel], pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        nearest = wh.nearest_output_bay(agv_pos, graph)
        assert nearest == out2

    def test_metrics(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def ops():
            yield from wh.pick(steel, 5)
            yield from wh.pick(steel, 5)
            yield from wh.put(steel, 5)

        env.process(ops())
        env.run()
        assert wh.total_picks == 2
        assert wh.total_puts == 1
        assert wh.average_pick_time == pytest.approx(2.0)
        assert wh.average_put_time == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/intralogistics/test_warehouse.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Warehouse**

```python
# src/simulatte/intralogistics/warehouse.py
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import simpy

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.sku import SKU


class Warehouse:
    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        input_bays: list[Node],
        output_bays: list[Node],
        n_slots: int,
        products: list[SKU],
        initial_inventory: dict[SKU, int] | None = None,
        pick_time_fn: Callable[[SKU, int], float],
        put_time_fn: Callable[[SKU, int], float],
    ) -> None:
        self.env = env
        self.name = name
        self.input_bays = list(input_bays)
        self.output_bays = list(output_bays)
        self.pick_time_fn = pick_time_fn
        self.put_time_fn = put_time_fn
        self._slots = simpy.Resource(env, capacity=n_slots)

        initial = initial_inventory or {}
        self.inventory: dict[SKU, simpy.Container] = {
            product: simpy.Container(env, capacity=float("inf"), init=initial.get(product, 0))
            for product in products
        }

        self.total_picks: int = 0
        self.total_puts: int = 0
        self._total_pick_time: float = 0.0
        self._total_put_time: float = 0.0

    def get_inventory_level(self, sku: SKU) -> float:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        return self.inventory[sku].level

    def pick(self, sku: SKU, quantity: int) -> ProcessGenerator:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        yield self.inventory[sku].get(quantity)
        with self._slots.request() as req:
            yield req
            pick_time = self.pick_time_fn(sku, quantity)
            yield self.env.timeout(pick_time)
            self.total_picks += 1
            self._total_pick_time += pick_time

    def put(self, sku: SKU, quantity: int) -> ProcessGenerator:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        with self._slots.request() as req:
            yield req
            put_time = self.put_time_fn(sku, quantity)
            yield self.env.timeout(put_time)
            yield self.inventory[sku].put(quantity)
            self.total_puts += 1
            self._total_put_time += put_time

    def nearest_input_bay(self, from_node: Node, graph: LayoutGraph) -> Node:
        return self._nearest_bay(from_node, self.input_bays, graph)

    def nearest_output_bay(self, from_node: Node, graph: LayoutGraph) -> Node:
        return self._nearest_bay(from_node, self.output_bays, graph)

    @staticmethod
    def _nearest_bay(from_node: Node, bays: list[Node], graph: LayoutGraph) -> Node:  # noqa: ARG004
        return min(bays, key=lambda bay: math.hypot(bay.x - from_node.x, bay.y - from_node.y))

    @property
    def average_pick_time(self) -> float:
        return self._total_pick_time / self.total_picks if self.total_picks > 0 else 0.0

    @property
    def average_put_time(self) -> float:
        return self._total_put_time / self.total_puts if self.total_puts > 0 else 0.0

    def __repr__(self) -> str:
        return f"Warehouse(name={self.name!r})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/intralogistics/test_warehouse.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/intralogistics/warehouse.py tests/intralogistics/test_warehouse.py
git commit -m "feat(intralogistics): add Warehouse with deadlock-safe pick/put"
```

---

## Task 9: ChargingStation

**Files:**
- Create: `src/simulatte/intralogistics/charging.py`
- Create: `tests/intralogistics/test_charging.py`

Implementation: `ChargingStation` with `recharge()` and `swap()` methods, SimPy resource for slots, swap pool modeled as a `simpy.Container`. `swap()` raises `RuntimeError` if `supports_swap` is False. Tests cover: basic recharge timing, slot blocking, swap with available pool, swap with empty pool wait, swap unsupported raises, metrics tracking.

- [ ] **Step 1: Write charging station tests** (full test code for recharge, swap, slot blocking, error cases, metrics)
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement ChargingStation**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add ChargingStation with recharge and swap"`

---

## Task 10: ParkingArea

**Files:**
- Create: `src/simulatte/intralogistics/parking.py`
- Create: `tests/intralogistics/test_parking.py`

Implementation: Simple SimPy resource wrapper. Tests cover: enter/leave, blocking when full, capacity tracking.

- [ ] **Step 1: Write parking area tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement ParkingArea**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add ParkingArea"`

---

## Task 11: Order & OrderStatus

**Files:**
- Create: `src/simulatte/intralogistics/order.py`
- Create: `tests/intralogistics/test_order.py`

Implementation: `OrderStatus` enum (8 values), `TransferOrder` dataclass with auto-generated UUID, status tracking, lifecycle timestamps. Tests cover: creation with defaults, auto-id generation, status transitions, all OrderStatus values exist.

- [ ] **Step 1: Write order tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement OrderStatus and TransferOrder**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add OrderStatus and TransferOrder"`

---

## Task 12: Policies

**Files:**
- Create: `src/simulatte/intralogistics/policies.py`
- Create: `tests/intralogistics/test_policies.py`

Implementation: Protocol definitions for `DispatchStrategy`, `ReplenishmentPolicy`, `RepositioningPolicy`, `LoadRecoveryStrategy`. `RepositioningContext` dataclass. Built-in implementations: `NearestIdleStrategy`, `RoundRobinStrategy`, `StayInPlace`, `NearestParkingPolicy`, `ReorderPointPolicy`, `ReturnToOrigin`, `ResumeDelivery`. Tests cover: each built-in strategy's core behavior, protocol conformance, edge cases (no idle AGV, no parking with capacity, in-transit deduplication for reorder point).

- [ ] **Step 1: Write policy tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement policy protocols and built-in implementations**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add dispatch, repositioning, replenishment, and load recovery policies"`

---

## Task 13: Metrics

**Files:**
- Create: `src/simulatte/intralogistics/metrics.py`
- Create: `tests/intralogistics/test_metrics.py`

Implementation: `OrderMetricsCollector` protocol, `EMAOrderMetrics` built-in. `IntralogisticsTimeSeriesCollector` protocol, `DefaultIntralogisticsCollector` built-in. Tests cover: EMA computation correctness, time-series recording, protocol conformance.

- [ ] **Step 1: Write metrics tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement metrics protocols and built-in collectors**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add metrics collectors with EMA and time-series support"`

---

## Task 14: FleetCoordinator

**Files:**
- Create: `src/simulatte/intralogistics/coordinator.py`
- Create: `tests/intralogistics/test_coordinator.py`

This is the largest and most complex task. Implementation includes:
- Constructor with all policy/strategy defaults
- `submit()` and `cancel()` with process ownership
- `create_order()` factory
- `_run_mission()` SimPy process implementing the full mission lifecycle
- `_travel()` helper implementing the movement loop with pre-arc battery check
- `_handle_interruption()` for interrupt cleanup
- Battery management (low/critical/stranded detection)
- Lifecycle hook registration and firing
- `add_replenishment_policy()` with periodic/event-driven checking
- Pending order queue with wake-on-idle
- Fleet-level convenience properties

Tests should cover:
- Simple end-to-end mission (submit order → AGV travels → picks → travels → delivers)
- Order queuing when no AGV available
- Mission cancellation
- Battery depletion during travel
- Low battery → charge after mission
- Pre-arc battery check → divert to charging
- Lifecycle hook firing order
- Replenishment policy triggering
- `create_order()` factory
- Fleet utilization and time allocation

- [ ] **Step 1: Write coordinator tests** (start with the simplest end-to-end test)
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement FleetCoordinator core** (constructor, submit, _run_mission with basic lifecycle)
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add FleetCoordinator with mission lifecycle"`
- [ ] **Step 6: Write tests for cancellation and battery management**
- [ ] **Step 7: Run tests to verify they fail**
- [ ] **Step 8: Implement cancel(), battery management, interrupt handling**
- [ ] **Step 9: Run tests to verify they pass**
- [ ] **Step 10: Commit** — `git commit -m "feat(intralogistics): add mission cancellation and battery management"`
- [ ] **Step 11: Write tests for hooks, replenishment, pending queue, fleet metrics**
- [ ] **Step 12: Run tests to verify they fail**
- [ ] **Step 13: Implement hooks, replenishment policy wiring, pending queue, fleet convenience**
- [ ] **Step 14: Run tests to verify they pass**
- [ ] **Step 15: Commit** — `git commit -m "feat(intralogistics): add lifecycle hooks, replenishment policies, fleet metrics"`

---

## Task 15: Package __init__.py and Builders

**Files:**
- Modify: `src/simulatte/intralogistics/__init__.py`
- Create: `src/simulatte/intralogistics/builders.py`

Implementation: `__init__.py` exports all public types. `builders.py` provides convenience factory functions for common configurations (e.g., `build_simple_system()` that creates a graph, warehouses, AGVs, and coordinator with sensible defaults).

- [ ] **Step 1: Write builder tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement builders and __init__.py exports**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit** — `git commit -m "feat(intralogistics): add builders and public API exports"`

---

## Task 16: Cleanup experimental/

**Files:**
- Delete: `src/simulatte/experimental/agv.py`, `warehouse.py`, `materials.py`, `builders.py`, `job.py`, `typing.py`
- Modify: `src/simulatte/experimental/__init__.py`
- Delete: `tests/experimental/test_warehouse_agv.py`, `test_materials.py`, `test_integration.py`, `test_builders.py`, `test_job.py`

- [ ] **Step 1: Delete experimental source files**

```bash
rm src/simulatte/experimental/agv.py
rm src/simulatte/experimental/warehouse.py
rm src/simulatte/experimental/materials.py
rm src/simulatte/experimental/builders.py
rm src/simulatte/experimental/job.py
rm src/simulatte/experimental/typing.py
```

- [ ] **Step 2: Update experimental __init__.py**

```python
# src/simulatte/experimental/__init__.py
from __future__ import annotations

from simulatte.experimental.gymnasium import SimulatteEnv

__all__ = [
    "SimulatteEnv",
]
```

- [ ] **Step 3: Delete experimental tests**

```bash
rm tests/experimental/test_warehouse_agv.py
rm tests/experimental/test_materials.py
rm tests/experimental/test_integration.py
rm tests/experimental/test_builders.py
rm tests/experimental/test_job.py
```

- [ ] **Step 4: Verify all tests pass**

Run: `uv run pytest -v`
Expected: All tests pass (gymnasium tests still pass, new intralogistics tests pass, no import errors)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove experimental AGV/Warehouse/MaterialCoordinator (replaced by intralogistics)"
```

---

## Task 17: Integration Tests

**Files:**
- Create: `tests/intralogistics/test_integration.py`

End-to-end tests that exercise the full system:
1. Two warehouses, one AGV, one transfer order — complete mission lifecycle
2. Multiple AGVs, concurrent orders, verify FIFO completion
3. AGV battery runs low during mission — charges then completes
4. Replenishment policy triggers automatic transfer orders
5. Order cancellation mid-mission
6. Traffic management with two AGVs on shared corridor

- [ ] **Step 1: Write integration tests**
- [ ] **Step 2: Run tests to verify they pass** (everything is already implemented)
- [ ] **Step 3: Commit** — `git commit -m "test(intralogistics): add end-to-end integration tests"`

---

## Coverage Note

The project requires 99% code coverage (`--cov-fail-under=99` in pyproject.toml). Every module must have thorough tests. Tasks 9-13 use abbreviated step descriptions but must include complete test code when implemented — no placeholders allowed.
