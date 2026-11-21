"""Routing: the router protocol (spec §10.1) and the shortest-distance router (§10.2)."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.network import Resource
    from simulatte.intralogistics.traffic import TrafficView


@dataclass(frozen=True)
class RouteRequest:
    agv: AGV | None
    origin: Node
    destination: Node
    load_weight: float = 0.0
    avoid: frozenset[Resource] = frozenset()
    purpose: str = "travel"


@dataclass(frozen=True)
class Route:
    nodes: tuple[Node, ...]  # a simple path from origin to destination
    cost: float  # the router's own unit; comparable only within one router


@runtime_checkable
class Router(Protocol):
    def route(self, request: RouteRequest, view: TrafficView) -> Route | None: ...


def shortest_path(
    graph: LayoutGraph,
    origin: Node,
    destination: Node,
    *,
    allowed: Callable[[Node, Node], bool] | None = None,
    heuristic: bool = False,
) -> tuple[tuple[Node, ...], float] | None:
    """The path of least summed arc length, or ``None``; ties break by node id.

    ``allowed(u, v)`` returning false excludes the leg from ``u`` to ``v``. With ``heuristic`` the search is A* with
    the chord distance to the destination, which never overestimates because an arc is never shorter than its chord.
    """
    if origin == destination:
        return (origin,), 0.0

    def estimate(node: Node) -> float:
        return math.hypot(destination.x - node.x, destination.y - node.y) if heuristic else 0.0

    best: dict[Node, float] = {origin: 0.0}
    previous: dict[Node, Node] = {}
    heap: list[tuple[float, str, float, Node]] = [(estimate(origin), origin.id, 0.0, origin)]
    while heap:
        _, _, cost, node = heapq.heappop(heap)
        if node == destination:
            path = [node]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            path.reverse()
            return tuple(path), cost
        if cost > best[node]:
            continue
        for neighbor in graph.neighbors(node):
            if allowed is not None and not allowed(node, neighbor):
                continue
            new_cost = cost + graph.length(node, neighbor)
            if new_cost < best.get(neighbor, math.inf):
                best[neighbor] = new_cost
                previous[neighbor] = node
                heapq.heappush(heap, (new_cost + estimate(neighbor), neighbor.id, new_cost, neighbor))
    return None


class ShortestDistanceRouter:
    """Cost is the path length; a leg that needs an avoided resource, or enters an avoided node, is excluded."""

    def __init__(self, algorithm: Literal["dijkstra", "astar"] = "dijkstra") -> None:
        if algorithm not in ("dijkstra", "astar"):
            raise ValueError(f"unknown algorithm {algorithm!r}")
        self.algorithm = algorithm

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        avoid = request.avoid

        def allowed(u: Node, v: Node) -> bool:
            return v not in avoid and not any(resource in avoid for resource in view.resources(u, v))

        found = shortest_path(
            view.graph,
            request.origin,
            request.destination,
            allowed=allowed if avoid else None,
            heuristic=self.algorithm == "astar",
        )
        return None if found is None else Route(*found)
