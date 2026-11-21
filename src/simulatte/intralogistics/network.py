"""The traffic resources of an AGV layout and the claims an AGV needs to travel it (spec §5, §6, §7.2).

A :class:`TrafficNetwork` is derived once from a :class:`LayoutGraph` and never changes: nodes with capacities, one
segment per pair of adjacent nodes, authored zones, derived corridors and dead-end branches. It also builds the
:class:`Claim` for the legs of a route. Granting claims is the job of a traffic controller, not of this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from simulatte.intralogistics.graph import LayoutGraph, Node

Direction = Literal["forward", "backward"]


@dataclass(frozen=True)
class Segment:
    """The lane piece between two adjacent nodes, shared by both directions. ``a.id < b.id``."""

    a: Node
    b: Node
    length: float
    passing: bool

    @property
    def id(self) -> str:
        return f"{self.a.id}~{self.b.id}"


@dataclass(frozen=True)
class Zone:
    """An authored set of nodes and segments (node pairs) with an exclusive capacity."""

    id: str
    members: tuple[Node | tuple[Node, Node], ...]
    capacity: int = 1
    hold_back: bool = False


@dataclass(frozen=True)
class Corridor:
    """A derived chain with a same-direction rule.

    ``nodes[0]`` and ``nodes[-1]`` are the ports and are not members; ``forward`` follows the tuple. The ports are two
    different nodes with ``nodes[0].id < nodes[-1].id``, except for a chain that leaves a port and returns to it: then
    both ends are that port and ``forward`` is the direction whose first interior node has the smaller id.
    """

    id: str
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Branch:
    """A dead-end branch. ``nodes`` run from the node next to the ``root`` out to the leaf; the root is not a member."""

    root: Node
    nodes: tuple[Node, ...]


Resource = Node | Segment | Zone | Corridor


@dataclass(frozen=True)
class ClaimItem:
    """One resource of a claim. ``direction`` is set for segments and corridors, ``None`` for nodes and zones."""

    resource: Resource
    direction: Direction | None = None


@dataclass(frozen=True)
class Claim:
    """The resources an AGV needs for ``legs`` consecutive legs, granted all at once."""

    items: tuple[ClaimItem, ...]
    legs: int


def _key(u: Node, v: Node) -> tuple[str, str]:
    return (u.id, v.id) if u.id < v.id else (v.id, u.id)


class TrafficNetwork:
    """Traffic resources derived from ``graph``, and the claims over them.

    Capacity of a node is the first of ``capacities`` (explicit), ``facility_capacities`` (the sum over the facilities
    on the node) and ``node_capacity``. ``zones`` are authored; corridors are derived when ``derive_corridors`` is
    true; ``hold_back_dead_ends`` turns the dead-end rule of :meth:`claim` on or off. ``warnings`` collects the
    problems found while deriving, in a fixed order.
    """

    def __init__(
        self,
        graph: LayoutGraph,
        *,
        node_capacity: int = 1,
        capacities: Mapping[Node, int] | None = None,
        facility_capacities: Mapping[Node, int] | None = None,
        zones: Sequence[Zone] = (),
        derive_corridors: bool = True,
        hold_back_dead_ends: bool = True,
    ) -> None:
        explicit = dict(capacities or {})
        facilities = dict(facility_capacities or {})
        if node_capacity < 1:
            raise ValueError(f"a node capacity must be at least 1, got {node_capacity}")
        known = set(graph.nodes)
        for name, mapping in (("capacities", explicit), ("facility_capacities", facilities)):
            for node, value in mapping.items():
                if node not in known:
                    raise ValueError(f"{name}: node {node.id!r} is not in the graph")
                if value < 1:
                    raise ValueError(f"{name}: the capacity of node {node.id!r} must be at least 1, got {value}")
        self.graph = graph
        self._hold_back_dead_ends = hold_back_dead_ends
        nodes = graph.nodes
        warnings: list[str] = []

        self._capacity: dict[Node, int] = {}
        for node in nodes:
            if node in explicit:
                self._capacity[node] = explicit[node]
                if node in facilities and explicit[node] < facilities[node]:
                    warnings.append(
                        f"node {node.id}: capacity {explicit[node]} is below the capacity {facilities[node]} "
                        "of its facilities"
                    )
            else:
                self._capacity[node] = facilities.get(node, node_capacity)

        # Neighbours ignoring direction, sorted by id.
        around: dict[Node, set[Node]] = {node: set() for node in nodes}
        for node in nodes:
            for other in graph.neighbors(node):
                around[node].add(other)
                around[other].add(node)
        self._around: dict[Node, tuple[Node, ...]] = {
            node: tuple(sorted(others, key=lambda n: n.id)) for node, others in around.items()
        }

        found: dict[tuple[str, str], Segment] = {}
        for node in nodes:
            for other in graph.neighbors(node):
                key = _key(node, other)
                if key not in found:
                    arc = graph.arc_between(node, other)
                    assert arc is not None
                    low, high = (node, other) if node.id < other.id else (other, node)
                    found[key] = Segment(low, high, graph.length(node, other), arc.passing)
        self.segments: tuple[Segment, ...] = tuple(sorted(found.values(), key=lambda s: s.id))
        self._segment: dict[tuple[str, str], Segment] = found

        seen: set[Node] = set()  # interior nodes that a walk from a leaf or a port has passed
        self.branches: tuple[Branch, ...] = self._derive_branches(seen)
        self._branch_of: dict[Node, tuple[Branch, int]] = {}
        self._branches_at: dict[Node, list[Branch]] = {}
        for branch in self.branches:
            self._branches_at.setdefault(branch.root, []).append(branch)
            for position, member in enumerate(branch.nodes):
                self._branch_of[member] = (branch, position)

        corridors: list[Corridor] = []
        if derive_corridors:
            corridors = self._derive_corridors(seen)
            warnings.extend(self._rings(seen))
        self.corridors: tuple[Corridor, ...] = tuple(sorted(corridors, key=lambda c: c.id))
        self._corridor_at: dict[tuple[str, str], tuple[Corridor, int]] = {}
        for corridor in self.corridors:
            for i in range(len(corridor.nodes) - 1):
                self._corridor_at[_key(corridor.nodes[i], corridor.nodes[i + 1])] = (corridor, i)

        self.zones: tuple[Zone, ...] = tuple(zones)
        self._zones_at_node: dict[Node, list[Zone]] = {}
        self._zones_at_segment: dict[tuple[str, str], list[Zone]] = {}
        self._check_zones()
        self._hold_back_zones: tuple[Zone, ...] = tuple(z for z in self.zones if z.hold_back)
        self.warnings: tuple[str, ...] = tuple(warnings)

    # Derivation

    def _is_leaf(self, node: Node) -> bool:
        return len(self._around[node]) == 1

    def _is_interior(self, node: Node) -> bool:
        return len(self._around[node]) == 2 and self._capacity[node] == 1

    def _chain(self, start: Node, first: Node) -> tuple[list[Node], Node]:
        """Walk from ``start`` through ``first`` over interior nodes; return ``start`` plus those nodes, and the end."""
        path = [start]
        previous, current = start, first
        while self._is_interior(current):
            path.append(current)
            one, two = self._around[current]
            previous, current = current, (two if one == previous else one)
        return path, current

    def _derive_branches(self, seen: set[Node]) -> tuple[Branch, ...]:
        branches: list[Branch] = []
        for leaf in self.graph.nodes:
            if not self._is_leaf(leaf):
                continue
            (first,) = self._around[leaf]
            path, root = self._chain(leaf, first)
            seen.update(path)
            if self._is_leaf(root):
                continue  # a plain line with no passing place
            branches.append(Branch(root, tuple(reversed(path))))
        return tuple(branches)

    def _two_way_plain(self, u: Node, v: Node) -> bool:
        return (
            self.graph.arc_between(u, v) is not None
            and self.graph.arc_between(v, u) is not None
            and not self._segment[_key(u, v)].passing
        )

    def _derive_corridors(self, seen: set[Node]) -> list[Corridor]:
        corridors: list[Corridor] = []
        for port in self.graph.nodes:
            if len(self._around[port]) < 2 or self._is_interior(port):
                continue
            for first in self._around[port]:
                path, end = self._chain(port, first)
                seen.update(path[1:])
                if len(path) == 1 or self._is_leaf(end):
                    continue
                keep = path[1].id < path[-1].id if end == port else port.id < end.id
                chain = [*path, end]
                if keep and all(self._two_way_plain(u, v) for u, v in zip(chain, chain[1:])):
                    corridors.append(Corridor(f"corridor:{min(n.id for n in path[1:])}", tuple(chain)))
        return corridors

    def _rings(self, seen: set[Node]) -> list[str]:
        """One warning for every cycle of interior nodes that no port or leaf leads to."""
        warnings: list[str] = []
        for start in self.graph.nodes:
            if start in seen or not self._is_interior(start):
                continue
            ring = [start]
            previous, current = start, self._around[start][0]
            while current != start:
                ring.append(current)
                one, two = self._around[current]
                previous, current = current, (two if one == previous else one)
            seen.update(ring)
            warnings.append(f"no passing place on the cycle {', '.join(n.id for n in ring)}: no corridor derived")
        return warnings

    def _check_zones(self) -> None:
        known = set(self.graph.nodes)
        corridor_ids = {corridor.id for corridor in self.corridors}
        ids: set[str] = set()
        for zone in self.zones:
            if zone.id in ids:
                raise ValueError(f"duplicate zone id {zone.id!r}")
            if zone.id in corridor_ids:
                raise ValueError(f"zone id {zone.id!r} equals the id of a derived corridor")
            if zone.capacity < 1:
                raise ValueError(f"zone {zone.id!r}: capacity must be at least 1, got {zone.capacity}")
            ids.add(zone.id)
            for member in zone.members:
                if isinstance(member, Node):
                    if member not in known:
                        raise ValueError(f"zone {zone.id!r}: node {member.id!r} is not in the graph")
                    self._zones_at_node.setdefault(member, []).append(zone)
                    continue
                for node in member:
                    if node not in known:
                        raise ValueError(f"zone {zone.id!r}: node {node.id!r} is not in the graph")
                key = _key(*member)
                if key not in self._segment:
                    raise ValueError(f"zone {zone.id!r}: nodes {member[0].id!r} and {member[1].id!r} are not adjacent")
                self._zones_at_segment.setdefault(key, []).append(zone)

    # Queries

    def capacity(self, node: Node) -> int:
        """The capacity of ``node``."""
        return self._capacity[node]

    def segment(self, u: Node, v: Node) -> Segment:
        """The segment between two adjacent nodes, whichever way they are given."""
        try:
            return self._segment[_key(u, v)]
        except KeyError:
            raise ValueError(f"nodes {u.id} and {v.id} are not adjacent") from None

    def direction(self, u: Node, v: Node) -> Direction:
        """The direction of the leg ``u -> v`` on its segment: forward when ``u.id < v.id``."""
        return "forward" if u.id < v.id else "backward"

    def _containers(self, u: Node, v: Node) -> dict[str, ClaimItem]:
        """The zones and the corridor holding the segment ``{u, v}`` or the node ``v``, by id."""
        key = _key(u, v)
        found: dict[str, ClaimItem] = {}
        for zone in (*self._zones_at_segment.get(key, ()), *self._zones_at_node.get(v, ())):
            found[zone.id] = ClaimItem(zone)
        # A corridor holds its interior nodes only together with the segments next to them, so the segment decides.
        entry = self._corridor_at.get(key)
        if entry is not None:
            corridor, i = entry
            found[corridor.id] = ClaimItem(corridor, "forward" if corridor.nodes[i] == u else "backward")
        return found

    def _leg_items(self, u: Node, v: Node, extra: Mapping[str, ClaimItem] | None = None) -> list[ClaimItem]:
        if self.graph.arc_between(u, v) is None:
            raise ValueError(f"nodes {u.id} and {v.id} are not connected by an arc")
        containers = self._containers(u, v)
        for name, item in (extra or {}).items():
            containers.setdefault(name, item)
        return [
            *(containers[name] for name in sorted(containers)),
            ClaimItem(self.segment(u, v), self.direction(u, v)),
            ClaimItem(v),
        ]

    def leg(self, u: Node, v: Node) -> tuple[ClaimItem, ...]:
        """The base claim of the leg ``u -> v``: its zones and corridors by id, the segment, then ``v``."""
        return tuple(self._leg_items(u, v))

    # Claims

    def _reaches_dead_end(self, route: Sequence[Node], index: int) -> bool:
        """Whether the leg at ``index`` enters a branch, or ends at a root that the next leg enters."""
        u, v = route[index], route[index + 1]
        entry = self._branch_of.get(v)
        if entry is not None:
            branch, position = entry
            if u == (branch.root if position == 0 else branch.nodes[position - 1]):
                return True
        if index + 2 < len(route):
            return any(branch.nodes[0] == route[index + 2] for branch in self._branches_at.get(v, ()))
        return False

    def _ahead(self, route: Sequence[Node], index: int) -> dict[str, ClaimItem]:
        """What the leg at ``index`` also claims for the next leg: a corridor it is about to enter, a held-back zone."""
        v, w = route[index + 1], route[index + 2]
        ahead: dict[str, ClaimItem] = {}
        entry = self._corridor_at.get(_key(v, w))
        if entry is not None:
            corridor, i = entry
            orientation: Direction = "forward" if corridor.nodes[i] == v else "backward"
            from_port = i == 0 if orientation == "forward" else i == len(corridor.nodes) - 2
            if from_port:
                ahead[corridor.id] = ClaimItem(corridor, orientation)
        for zone in self._hold_back_zones:
            if zone.id in self._containers(v, w) and not any(
                zone.id in self._containers(route[j], route[j + 1]) for j in range(index + 1)
            ):
                ahead[zone.id] = ClaimItem(zone)
        return ahead

    def claim(self, route: Sequence[Node], index: int) -> Claim:
        """The claim that starts at leg ``index`` of ``route`` (leg ``k`` is ``route[k] -> route[k + 1]``).

        It holds every resource the rules name, whether or not the AGV already holds it. A dead-end entry extends it to
        all remaining legs when ``hold_back_dead_ends`` is on. Items run leg by leg, each leg's zones and corridors by
        id first, then its segment, then its end node, and appear once.
        """
        total = len(route) - 1
        if not 0 <= index < total:
            raise ValueError(f"index {index} is outside the {total} legs of the route")
        end = total if self._hold_back_dead_ends and self._reaches_dead_end(route, index) else index + 1
        items: dict[ClaimItem, None] = {}
        for k in range(index, end):
            ahead = self._ahead(route, k) if k + 2 < len(route) else None
            items.update(dict.fromkeys(self._leg_items(route[k], route[k + 1], ahead)))
        return Claim(tuple(items), end - index)
