"""Pure layered-v1 presentation defaults and deterministic cell packing.

Graph positions and node-bound AGV locations are returned separately from auto
leaves: neither may accidentally acquire auto-origin physical provenance.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .errors import LayoutDiagnostic, LayoutError
from .layers import ResolvedValues, _thaw
from .topology import StaticTopology, id_key
from .types import LayoutLimits, Point, _freeze, normalize_rotation, number, placement_defaults


@dataclass(frozen=True)
class TargetMetadata:
    """Serializable static membership; only the facade inspects live entities.

    explicitly_placed is author intent (never inferred from a previous auto layer).
    graph_position is authoritative for node kinds; node_id identifies an AGV's
    canonical bound node. An unbound AGV has no derived world position.
    """

    kind: str
    explicitly_placed: bool = False
    graph_position: Point | None = None
    node_id: str | None = None

    def __post_init__(self) -> None:
        id_key(self.kind)
        if type(self.explicitly_placed) is not bool:
            raise LayoutError("explicitly_placed must be bool")
        if self.graph_position is not None and (self.kind != "node" or not isinstance(self.graph_position, Point)):
            raise LayoutError("graph_position requires a node target and Point")
        if self.node_id is not None:
            id_key(self.node_id)
            if self.kind != "agv":
                raise LayoutError("node_id requires an agv target")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "explicitly_placed": self.explicitly_placed}
        if self.graph_position is not None:
            result["graph_position"] = [self.graph_position.x, self.graph_position.y, 0.0]
        if self.node_id is not None:
            result["node_id"] = self.node_id
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TargetMetadata:
        copied = dict(data)
        if "graph_position" in copied:
            copied["graph_position"] = Point(*copied["graph_position"])
        return cls(**copied)


@dataclass(frozen=True)
class AutoLayer:
    data: Mapping[str, Any]
    targets: Mapping[str, TargetMetadata]
    topology: StaticTopology
    derived_positions: Mapping[str, Point]
    diagnostics: tuple[LayoutDiagnostic, ...]
    algorithm: str = "layered-v1"

    def __post_init__(self) -> None:
        for name in ("data", "targets", "derived_positions"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        """Complete JSON-safe result, including callback-free replay inputs."""
        return {
            "algorithm": self.algorithm,
            "data": _thaw(self.data),
            "targets": {key: item.to_dict() for key, item in self.targets.items()},
            "topology": self.topology.to_dict(),
            "derived_positions": {key: [point.x, point.y, 0.0] for key, point in self.derived_positions.items()},
        }


def _cells(ids: list[str], edges: set[tuple[str, str]]) -> list[tuple[str, int, int]]:
    if not edges:
        columns = math.isqrt(len(ids))
        columns += columns * columns < len(ids)
        return [(key, i % columns, i // columns) for i, key in enumerate(ids)]
    adjacency: dict[str, list[str]] = {key: [] for key in ids}
    reverse: dict[str, list[str]] = {key: [] for key in ids}
    for a, b in sorted(edges, key=lambda edge: (id_key(edge[0]), id_key(edge[1]))):
        adjacency[a].append(b)
        reverse[b].append(a)
    # Iterative Kosaraju avoids Python recursion limits for large chains/SCCs.
    seen, finish = set(), []
    for key in ids:
        if key in seen:
            continue
        seen.add(key)
        stack = [(key, iter(adjacency[key]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                finish.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(adjacency[child])))
    membership, components = {}, []
    for key in reversed(finish):
        if key in membership:
            continue
        index = len(components)
        component, pending = [], [key]
        membership[key] = index
        while pending:
            node = pending.pop()
            component.append(node)
            for child in reverse[node]:
                if child not in membership:
                    membership[child] = index
                    pending.append(child)
        components.append(sorted(component, key=id_key))
    outgoing = [set() for _ in components]
    indegree = [0] * len(components)
    for a, b in edges:
        source, target = membership[a], membership[b]
        if source != target and target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1
    ready = [(id_key(component[0]), i) for i, component in enumerate(components) if not indegree[i]]
    heapq.heapify(ready)
    layers = [0] * len(components)
    while ready:
        _, source = heapq.heappop(ready)
        for target in outgoing[source]:
            layers[target] = max(layers[target], layers[source] + 1)
            indegree[target] -= 1
            if not indegree[target]:
                heapq.heappush(ready, (id_key(components[target][0]), target))
    ordered = sorted(range(len(components)), key=lambda i: (layers[i], id_key(components[i][0])))
    rows: dict[int, int] = {}
    result = []
    for index in ordered:
        column = layers[index]
        for key in components[index]:
            row = rows.get(column, 0)
            result.append((key, column, row))
            rows[column] = row + 1
    return result


# Exact AABBs padded by 1 m on each side yield a 2 m separation. Touching
# padded edges is allowed. Fractions avoid losing padding at large coordinates.
Bounds = tuple[Fraction, Fraction, Fraction, Fraction]


def _bounds(point: Point, size: tuple[float, float]) -> Bounds:
    x, y = Fraction(point.x), Fraction(point.y)
    dx, dy = Fraction(size[0]) / 2 + 1, Fraction(size[1]) / 2 + 1
    return x - dx, y - dy, x + dx, y + dy


def _overlap(a: Bounds, b: Bounds) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _BoundsIndex:
    """Balanced static rectangle tree for pinned occupancy and overlap warnings."""

    def __init__(self, bounds: Mapping[str, Bounds]) -> None:
        self.nodes: list[tuple[Bounds, str | None, int, int]] = []

        def build(items: list[tuple[str, Bounds]], axis: int = 0) -> int:
            if len(items) == 1:
                key, box = items[0]
                index = len(self.nodes)
                self.nodes.append((box, key, -1, -1))
                return index
            items.sort(key=lambda item: (item[1][axis] + item[1][axis + 2], id_key(item[0])))
            middle = len(items) // 2
            left, right = build(items[:middle], 1 - axis), build(items[middle:], 1 - axis)
            a, b = self.nodes[left][0], self.nodes[right][0]
            index = len(self.nodes)
            self.nodes.append(((min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])), None, left, right))
            return index

        self.root = build(list(bounds.items())) if bounds else -1

    def collisions(self, box: Bounds):
        pending = [self.root] if self.root >= 0 else []
        while pending:
            index = pending.pop()
            candidate, key, left, right = self.nodes[index]
            if _overlap(box, candidate):
                if key is not None:
                    yield key, candidate
                else:
                    pending.extend((right, left))


def _size(placement: Mapping[str, Any], default: Mapping[str, Any]) -> tuple[float, float]:
    footprint = {**default, **placement.get("footprint", {})}
    width, depth = footprint["width"], footprint["depth"]
    angle = normalize_rotation(placement.get("rotation", 0))
    if angle in (0, 180):
        return width, depth
    if angle in (90, 270):
        return depth, width
    cosine, sine = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    return number(width * cosine + depth * sine, "rotated width"), number(
        width * sine + depth * cosine, "rotated depth"
    )


def auto_layout(
    targets: Mapping[str, TargetMetadata],
    topology: StaticTopology,
    explicit_values: ResolvedValues,
    *,
    limits: LayoutLimits = LayoutLimits(),
) -> AutoLayer:
    """Compute once after all stages; `.data` is passed to resolve_layers.

    Only eligible static targets participate in topology; missing/excluded flow
    entries break adjacency. Explicit positions are never copied into auto data.
    Node visual positions are ignored in favor of metadata graph coordinates;
    consumers must use `.derived_positions` as the authoritative display location.
    """
    if len(targets) > limits.max_placements:
        raise LayoutError("max_placements exceeded by auto-layout targets")
    ordered = {key: targets[key] for key in sorted(targets, key=id_key)}
    placements, sizes, pinned, eligible, derived = {}, {}, {}, set(), {}
    diagnostics = []

    def warn(code: str, key: str, message: str, path: tuple[str, ...] | None = None) -> None:
        diagnostics.append(LayoutDiagnostic(code, "warning", path or ("placements", key), None, key, message))

    explicit = explicit_values.explicit_data.get("placements", {})
    for key, target in ordered.items():
        defaults = placement_defaults(target.kind, explicitly_placed=target.explicitly_placed)
        if not defaults.placeable or (not defaults.auto_position and target.kind not in ("agv", "node")):
            continue
        footprint = defaults.footprint
        assert footprint is not None
        default_footprint = {"width": footprint.width, "depth": footprint.depth, "height": footprint.height}
        placements[key] = {
            "kind": target.kind,
            "rotation": 0.0,
            "footprint": default_footprint,
            "ports": {},
            "obstacle": False,
            "visual": {},
        }
        placement = explicit.get(key, {})
        sizes[key] = _size(placement, default_footprint)
        if target.kind == "agv":
            node = ordered.get(target.node_id) if target.node_id is not None else None
            if node is not None and node.graph_position is not None:
                derived[key] = node.graph_position
            continue
        eligible.add(key)
        if target.kind == "node":
            if target.graph_position is not None:
                derived[key] = target.graph_position
                pinned[key] = _bounds(target.graph_position, sizes[key])
                if "position" in placement and Point(*placement["position"]) != target.graph_position:
                    warn("node-position-ignored", key, "graph coordinates override the visual placement position")
            continue
        if "position" in placement:
            pinned[key] = _bounds(Point(*placement["position"]), sizes[key])
    pinned_index = _BoundsIndex(pinned)
    unpadded = {key: (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1) for key, box in pinned.items()}
    overlap_index = _BoundsIndex(unpadded)
    for key, bounds in unpadded.items():
        if any(other != key for other, _ in overlap_index.collisions(bounds)):
            warn("placement-overlap", key, "explicit static bounds overlap another explicit placement")
    edges = set()
    for sequences, is_flow in ((topology.flows, True), (topology.shopfloors, False), (topology.routings, False)):
        for sequence in sequences:
            if is_flow:
                for key in sorted(set(sequence) - eligible, key=id_key):
                    warn(
                        "unknown-flow-target",
                        key,
                        "flow target is absent or not an auto-layout static placeable",
                        ("flows",),
                    )
            for a, b in zip(sequence, sequence[1:]):
                if a != b and a in eligible and b in eligible:
                    edges.add((a, b))
    cells = _cells(sorted(eligible, key=id_key), edges)
    auto_ids = {
        key
        for key in eligible - pinned.keys()
        if ordered[key].kind != "node"
        and explicit_values.at(("placements", key, "position"), explicit=True).state != "unset"
    }
    if auto_ids:
        cell_width = number(max(sizes[key][0] for key in auto_ids) + 2, "auto cell width", positive=True)
        cell_depth = number(max(sizes[key][1] for key in auto_ids) + 2, "auto cell depth", positive=True)
        used: dict[int, dict[int, int]] = {}
        packed: dict[tuple[int, int], Bounds] = {}
        for key, column, assigned in cells:
            if key not in auto_ids:
                continue
            occupied = used.setdefault(column, {})
            row = assigned
            while True:
                # Path compression makes already occupied row runs inexpensive.
                visited = []
                while row in occupied:
                    visited.append(row)
                    row = occupied[row]
                for item in visited:
                    occupied[item] = row
                try:
                    point = Point(float(column * Fraction(cell_width)), float(row * Fraction(cell_depth)))
                except OverflowError as exc:
                    raise LayoutError("auto position exceeds finite float64 range") from exc
                if math.ulp(point.x) > cell_width / 2 or math.ulp(point.y) > cell_depth / 2:
                    raise LayoutError("auto placement exceeds float64 cell precision")
                box = _bounds(point, sizes[key])
                hits = list(pinned_index.collisions(box))
                # Float64 rounding can make adjacent nominal cells fractionally
                # too close. Check their stored bounds as well, not just row ids.
                neighbors = [
                    packed[(x, y)]
                    for x in range(column - 1, column + 2)
                    for y in range(row - 1, row + 2)
                    if (x, y) in packed
                ]
                collisions = [other for other in neighbors if _overlap(box, other)]
                if not hits and not collisions:
                    break
                row = max(
                    row + 1,
                    max(
                        (
                            math.ceil((other[3] + Fraction(sizes[key][1]) / 2 + 1) / Fraction(cell_depth))
                            for other in [*(hit[1] for hit in hits), *collisions]
                        ),
                        default=row + 1,
                    ),
                )
            placements[key]["position"] = (point.x, point.y, point.z)
            occupied[row] = row + 1
            packed[(column, row)] = box
    # Presentation order follows topology, while non-packed defaults remain id-sorted.
    order = [key for key, _, _ in cells]
    order.extend(key for key in placements if key not in eligible)
    data = {"schema_version": "1.0", "placements": {key: placements[key] for key in order}}
    unique = {(item.code, item.path, item.target): item for item in diagnostics}
    return AutoLayer(data, ordered, topology, derived, tuple(unique[key] for key in sorted(unique)))
