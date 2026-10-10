from __future__ import annotations

import math
from collections import defaultdict
from types import MappingProxyType
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simulatte.environment import Environment


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


class NodeBinding(Entity, kind="node"):
    """The entity of a graph :class:`Node` in one environment (spec §5.2).

    `Node` stays an environment-free definition; ``env.entities.bind_node(node)`` creates its binding, whose id is
    ``node.id``. ``agvs`` lists the AGVs located at the node and ``reserved_by`` the AGVs holding a traffic
    reservation on it, both in arrival order.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "x": FieldSpec("float"),
            "y": FieldSpec("float"),
            "agvs": FieldSpec("str", collection="list"),
            "reserved_by": FieldSpec("str", collection="list"),
        }
    )

    def __init__(self, env: Environment, node: Node) -> None:
        self.node = node
        # AGVs placed here before the binding existed; later moves update the list (AGV.current_node).
        self.agvs: list[str] = [
            entity.id
            for entity in env.entities.live()
            if entity.kind == "agv" and getattr(entity, "current_node", None) == node
        ]
        self.reserved_by: list[str] = []
        env.entities.attach(self, name=node.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: coordinates and the ids of the AGVs located at and reserving the node."""
        node = self.node
        return {
            "x": wire_float(node.x),
            "y": wire_float(node.y),
            "agvs": list(self.agvs),
            "reserved_by": list(self.reserved_by),
            "label": self.label,
        }

    def __repr__(self) -> str:
        return f"NodeBinding(id={self.id!r})"


class LayoutGraph:
    _layout_frozen: bool = False

    def __init__(self, nodes: Iterable[Node], arcs: Iterable[Arc]) -> None:
        self._nodes: dict[Node, None] = dict.fromkeys(nodes)  # insertion-ordered (spec §5.3)
        self._adjacency: dict[Node, dict[Node, Arc]] = defaultdict(dict)
        for arc in arcs:
            self._adjacency[arc.source][arc.target] = arc
            if arc.bidirectional:
                self._adjacency[arc.target][arc.source] = arc

    @classmethod
    def _from_layout(cls, nodes: Iterable[Node], arcs: Iterable[Arc]) -> LayoutGraph:
        """Seal already-resolved directed traversals without changing legacy construction.

        The resolver owns geometry and enabled validation. This boundary checks unique
        canonical nodes and directed endpoint identity, preserving supplied iteration order.
        NodeBinding's dynamic occupancy remains independent of this frozen topology.
        """
        ordered = tuple(nodes)
        by_id = {node.id: node for node in ordered}
        if len(by_id) != len(ordered):
            raise ValueError("layout graph contains duplicate node ids")
        adjacency: dict[Node, dict[Node, Arc]] = {node: {} for node in ordered}
        for arc in arcs:
            if by_id.get(arc.source.id) is not arc.source or by_id.get(arc.target.id) is not arc.target:
                raise ValueError("layout graph endpoint is not a canonical node")
            if arc.bidirectional:
                raise ValueError("layout graph factory requires directed arcs")
            if arc.target in adjacency[arc.source]:
                raise ValueError("layout graph contains duplicate directed arcs")
            adjacency[arc.source][arc.target] = arc
        graph = cls((), ())
        object.__setattr__(graph, "_nodes", MappingProxyType(dict.fromkeys(ordered)))
        object.__setattr__(
            graph, "_adjacency", MappingProxyType({node: MappingProxyType(edges) for node, edges in adjacency.items()})
        )
        graph._layout_frozen = True
        return graph

    @property
    def nodes(self) -> tuple[Node, ...]:
        """The nodes in insertion order (the first occurrence of each)."""
        return tuple(self._nodes)

    def neighbors(self, node: Node) -> list[Node]:
        return list(self._adjacency.get(node, {})) if self._layout_frozen else list(self._adjacency[node])

    def arc_between(self, source: Node, target: Node) -> Arc | None:
        return (
            self._adjacency.get(source, {}).get(target) if self._layout_frozen else self._adjacency[source].get(target)
        )

    def distance(self, source: Node, target: Node) -> float:
        if self.arc_between(source, target) is None:
            raise ValueError(f"Nodes {source.id} and {target.id} are not connected by an arc")
        return math.hypot(target.x - source.x, target.y - source.y)

    @staticmethod
    def path_distance(path: list[Node]) -> float:
        return math.fsum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))

    def shortest_path(self, source: Node, target: Node) -> list[Node] | None:
        from simulatte.intralogistics.pathfinding import DijkstraPlanner

        return DijkstraPlanner().plan(self, source, target)
