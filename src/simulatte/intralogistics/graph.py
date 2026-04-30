from __future__ import annotations

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
        from simulatte.intralogistics.pathfinding import DijkstraPlanner

        return DijkstraPlanner().plan(self, source, target)
