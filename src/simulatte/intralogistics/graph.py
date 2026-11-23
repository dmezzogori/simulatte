from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from types import MappingProxyType
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simulatte.environment import Environment

_LENGTH_TOLERANCE = 1e-9
_LENGTH_ULPS = 4


def split(length: float, max_length: float, maximum: float = math.inf) -> tuple[int, float]:
    """The number of equal pieces into which an arc of `length` is split for `max_length`, and their length.

    The count is ``ceil(length / max_length)`` without the piece that float noise in the quotient would add (0.3 / 0.1,
    or a chord of 3.3000000000000007 for 3.3), and at most ``maximum + 1``; each piece is ``length / count`` long.
    ``LayoutGraph.subdivide`` and the layout's ``block_length`` both use it, so that the two build the same network
    to the float (spec §6.1, §12).
    """
    count = math.ceil(min(length / max_length * (1 - 1e-9), maximum + 1.0))
    return count, length / max(count, 1)


@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float

    def __hash__(self) -> int:
        # Equal nodes have equal ids, so the id alone is a hash consistent with equality; graphs and searches hash
        # nodes on every dict access, and hashing the coordinates as well cost about a third of a run.
        return hash(self.id)


def chord_length(ax: float, ay: float, bx: float, by: float) -> float:
    """The straight-line distance of two points: the length of an arc declared without one.

    The layout compares a declared length with it for exact equality, so the arc, the explicit declaration and the
    split of an edge all compute it here.
    """
    return math.hypot(bx - ax, by - ay)


@dataclass(frozen=True)
class Arc:
    source: Node
    target: Node
    bidirectional: bool = True
    speed_limit: float | None = None
    length: float | None = None
    passing: bool = False

    def __post_init__(self) -> None:
        limit = self.speed_limit
        if limit is not None and not (math.isfinite(limit) and limit > 0):
            raise ValueError(
                f"arc {self.source.id}->{self.target.id} speed_limit {limit} must be positive and finite when given"
            )
        chord = chord_length(self.source.x, self.source.y, self.target.x, self.target.y)
        if self.length is None:
            object.__setattr__(self, "length", chord)
        elif not math.isfinite(self.length) or self.length < min(
            chord * (1.0 - _LENGTH_TOLERANCE), chord - _LENGTH_ULPS * self._spacing()
        ):
            raise ValueError(
                f"arc {self.source.id}->{self.target.id} length {self.length} must be finite and not smaller "
                f"than the chord {chord}"
            )

    def _spacing(self) -> float:
        """The floating-point spacing at the largest absolute coordinate of the two nodes.

        Points generated far from the origin (georeferenced coordinates) are rounded to this spacing, so a length
        computed along the way can fall under the chord of its points by up to about 1.3 times it (measured over
        random edges of 5 to 60 m at offsets up to 1e8); `_LENGTH_ULPS` leaves a margin of three. Near the origin it
        is far below the relative `_LENGTH_TOLERANCE`, which then decides alone.
        """
        source, target = self.source, self.target
        return math.ulp(max(abs(source.x), abs(source.y), abs(target.x), abs(target.y)))


def _check_pair(arc: Arc, reverse: Arc | None) -> None:
    if reverse is not None and reverse is not arc and (reverse.length != arc.length or reverse.passing != arc.passing):
        raise ValueError(
            f"arcs {arc.source.id}->{arc.target.id} and {reverse.source.id}->{reverse.target.id} "
            "disagree on length or passing"
        )


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
            if entity.kind == "agv" and getattr(entity, "_current_node", None) == node
        ]
        self.reserved_by: list[str] = []
        self._uses_explicit_graph = env._layout is None
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
            _check_pair(arc, self._adjacency[arc.target].get(arc.source))
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
            _check_pair(arc, adjacency[arc.target].get(arc.source))
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

    def length(self, source: Node, target: Node) -> float:
        """The travelled length of the arc from ``source`` to ``target``."""
        arc = self.arc_between(source, target)
        if arc is None:
            raise ValueError(f"Nodes {source.id} and {target.id} are not connected by an arc")
        assert arc.length is not None
        return arc.length

    def path_length(self, path: Sequence[Node]) -> float:
        """The summed arc lengths along ``path``."""
        return math.fsum(self.length(path[i], path[i + 1]) for i in range(len(path) - 1))

    def subdivide(self, max_length: float) -> LayoutGraph:
        """A new graph in which every arc longer than ``max_length`` is split into equal pieces.

        Each arc of length ``L`` becomes ``ceil(L / max_length)`` pieces (without the piece that float noise in the
        quotient would add) of length ``L`` divided by their number, that inherit
        ``bidirectional``, ``speed_limit`` and ``passing``. The generated nodes are named ``"<a>~<b>#<k>"`` with
        ``a`` and ``b`` the node ids in sorted order and ``k`` counted from ``a``, placed on the chord; both
        directions of a node pair share them. Original nodes are kept as the same instances, followed by the
        generated ones in the order in which the graph's adjacency lists the arcs (the order of construction, so the
        result is deterministic). Graphs built by the layout package cannot be subdivided.
        """
        if self._layout_frozen:
            raise ValueError("a layout-built graph cannot be subdivided")
        if not (max_length > 0.0 and math.isfinite(max_length)):
            raise ValueError("max_length must be positive and finite")
        arcs: list[Arc] = []
        seen: set[int] = set()
        for edges in self._adjacency.values():
            for arc in edges.values():
                if id(arc) not in seen:
                    seen.add(id(arc))
                    arcs.append(arc)
        nodes = list(self._nodes)
        generated: dict[tuple[str, str], list[Node]] = {}
        pieces: list[Arc] = []
        for arc in arcs:
            assert arc.length is not None
            count, piece = split(arc.length, max_length)
            if count <= 1:
                pieces.append(arc)
                continue
            low, high = (arc.source, arc.target) if arc.source.id <= arc.target.id else (arc.target, arc.source)
            key = (low.id, high.id)
            if key not in generated:
                generated[key] = [
                    Node(
                        id=f"{low.id}~{high.id}#{k}",
                        x=low.x + (high.x - low.x) * k / count,
                        y=low.y + (high.y - low.y) * k / count,
                    )
                    for k in range(1, count)
                ]
                nodes.extend(generated[key])
            chain = [low, *generated[key], high]
            if arc.source is not low:
                chain.reverse()
            for a, b in zip(chain, chain[1:]):
                pieces.append(Arc(a, b, arc.bidirectional, arc.speed_limit, piece, arc.passing))
        return LayoutGraph(nodes, pieces)

    @staticmethod
    def path_distance(path: list[Node]) -> float:
        return math.fsum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))

    def shortest_path(self, source: Node, target: Node) -> list[Node] | None:
        from simulatte.intralogistics.routing import shortest_path

        found = shortest_path(self, source, target)
        return None if found is None else list(found[0])
