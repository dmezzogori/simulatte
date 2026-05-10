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
            # pragma: no cover — unreachable with Euclidean edge weights (triangle inequality)
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
            # pragma: no cover — unreachable with Euclidean edge weights (triangle inequality)
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
