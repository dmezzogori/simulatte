"""Pure layered-v1/layered-v2 presentation defaults and deterministic cell packing.

``layered-v2`` is ``layered-v1`` plus two inputs a target may carry (studio map spec §6.5): a vehicle's ``size``, its
default footprint, and a facility's ``node_ids``, which place it at its node instead of in the grid (§6.3). One
implementation serves both names; a bundle is named ``layered-v2`` exactly when a target carries one of them, so every
other bundle keeps its name, its output and its hashes.

Graph positions and node-bound AGV locations are returned separately from auto
leaves: neither may accidentally acquire auto-origin physical provenance.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import Any

from .errors import LayoutDiagnostic, LayoutError
from .identity import AUTO_ALGORITHMS
from .layers import ResolvedValues, _thaw
from .topology import StaticTopology, id_key
from .types import LayoutLimits, Point, _freeze, normalize_rotation, number, placement_defaults

_BOUND_KINDS = frozenset({"warehouse", "parking_area", "charging_station"})
"""The kinds whose target may carry ``node_ids``."""
_TARGET_FIELDS = frozenset({"kind", "explicitly_placed", "graph_position", "node_id", "size", "node_ids"})
_CENTRED = frozenset({"parking_area", "charging_station"})
"""Bound kinds centred on their node with a default footprint of 3 m by 3 m (studio map spec §6.3, M21)."""
_NOT_OBSTACLES = frozenset({"node", *_CENTRED})
"""Explicit placements a node-placed warehouse may overlap."""
_ZERO_MEAN = 1e-9
"""Below this length the mean of the unit vectors to a node's neighbours is zero: they cancel."""


@dataclass(frozen=True)
class TargetMetadata:
    """Serializable static membership; only the facade inspects live entities.

    explicitly_placed is author intent (never inferred from a previous auto layer).
    graph_position is authoritative for node kinds; node_id identifies an AGV's
    canonical bound node. An unbound AGV has no derived world position.
    size is an AGV's ``(length, width)``, its default footprint, present when its
    type declares a length; node_ids are the nodes a warehouse, parking area or
    charging station is bound to, in binding order. Either names the algorithm
    ``layered-v2`` (studio map spec §6.5).
    """

    kind: str
    explicitly_placed: bool = False
    graph_position: Point | None = None
    node_id: str | None = None
    size: tuple[float, float] | None = None
    node_ids: tuple[str, ...] | None = None

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
        if self.size is not None:
            if self.kind != "agv" or not isinstance(self.size, tuple) or len(self.size) != 2:
                raise LayoutError("size requires an agv target and a (length, width) pair")
            object.__setattr__(self, "size", tuple(number(value, "size", positive=True) for value in self.size))
        if self.node_ids is not None:
            if self.kind not in _BOUND_KINDS or not isinstance(self.node_ids, tuple) or not self.node_ids:
                raise LayoutError("node_ids requires a warehouse, parking_area or charging_station target and ids")
            for node in self.node_ids:
                id_key(node)

    @property
    def versioned(self) -> bool:
        """Whether the target carries an input of ``layered-v2``."""
        return self.size is not None or self.node_ids is not None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "explicitly_placed": self.explicitly_placed}
        if self.graph_position is not None:
            result["graph_position"] = [self.graph_position.x, self.graph_position.y, 0.0]
        if self.node_id is not None:
            result["node_id"] = self.node_id
        if self.size is not None:
            result["size"] = list(self.size)
        if self.node_ids is not None:
            result["node_ids"] = list(self.node_ids)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TargetMetadata:
        copied = dict(data)
        if not {"kind", "explicitly_placed"} <= copied.keys() <= _TARGET_FIELDS:
            raise LayoutError("invalid layout target fields")
        if "graph_position" in copied:
            copied["graph_position"] = Point(*copied["graph_position"])
        for name in ("size", "node_ids"):
            if name in copied and copied[name] is None:
                raise LayoutError(f"layout target {name} must be omitted, not null")
            if isinstance(copied.get(name), (list, tuple)):
                copied[name] = tuple(copied[name])
        return cls(**copied)


@dataclass(frozen=True)
class NetworkGeometry:
    """The positions of the network's nodes and the neighbours of each, the inputs of the facility rule (§6.3).

    A neighbour is joined to the node by an arc in either direction and is listed once, in id order. Preparation builds
    it from the resolved graph; re-resolution builds it from the bundle's saved ``network.graph`` record, which holds
    the same node positions and arcs, so a saved bundle reproduces the placement.
    """

    positions: Mapping[str, Point]
    neighbours: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_arcs(cls, positions: Mapping[str, Point], arcs: Iterable[tuple[str, str]]) -> NetworkGeometry:
        near: dict[str, set[str]] = {key: set() for key in positions}
        for source, target in arcs:
            if source != target:
                near[source].add(target)
                near[target].add(source)
        return cls(dict(positions), {key: tuple(sorted(items, key=id_key)) for key, items in near.items()})

    @classmethod
    def from_record(cls, graph: Mapping[str, Any]) -> NetworkGeometry:
        """The geometry of a bundle's ``network.graph`` record."""
        return cls.from_arcs(
            {node["id"]: Point(node["position"][0], node["position"][1]) for node in graph["nodes"]},
            ((arc["source"], arc["target"]) for arc in graph["arcs"]),
        )


def auto_algorithm(targets: Iterable[TargetMetadata]) -> str:
    """``layered-v2`` exactly when a target carries ``size`` or ``node_ids``, else ``layered-v1``."""
    return AUTO_ALGORITHMS[any(target.versioned for target in targets)]


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


@lru_cache(maxsize=256)  # most targets share a few sizes
def _padding(size: tuple[float, float]) -> tuple[Fraction, Fraction]:
    return Fraction(size[0]) / 2 + 1, Fraction(size[1]) / 2 + 1


def _bounds(point: Point, size: tuple[float, float]) -> Bounds:
    x, y = Fraction(point.x), Fraction(point.y)
    dx, dy = _padding(size)
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


def _direction(anchor: str, network: NetworkGeometry) -> tuple[float, float]:
    """Away from the network at `anchor`: the opposite of the mean of the unit vectors to its neighbours, else +y."""
    origin = network.positions[anchor]
    xs, ys = [], []
    for other in network.neighbours[anchor]:
        point = network.positions[other]
        dx, dy = Fraction(point.x) - Fraction(origin.x), Fraction(point.y) - Fraction(origin.y)
        scale = max(abs(dx), abs(dy))
        if not scale:  # a neighbour on the anchor gives no direction
            continue
        x, y = float(dx / scale), float(dy / scale)  # scaled into [-1, 1] first: a far neighbour cannot overflow
        length = math.hypot(x, y)
        xs.append(x / length)
        ys.append(y / length)
    if not xs:
        return 0.0, 1.0
    # fsum rounds once, so the mean does not depend on the order of the neighbours
    mean_x, mean_y = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    length = math.hypot(mean_x, mean_y)
    if length < _ZERO_MEAN:
        return 0.0, 1.0
    return -mean_x / length, -mean_y / length


def _extent(placement: Mapping[str, Any], default: Mapping[str, Any], ux: float, uy: float) -> Fraction:
    """The extent of the (rotated) footprint along the unit direction ``(ux, uy)``: its support width."""
    footprint = {**default, **placement.get("footprint", {})}
    angle = normalize_rotation(placement.get("rotation", 0))
    if angle in (0, 180):
        along, across = ux, uy
    elif angle in (90, 270):
        along, across = uy, ux
    else:
        cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        along, across = ux * cosine + uy * sine, uy * cosine - ux * sine
    return abs(Fraction(along)) * Fraction(footprint["width"]) + abs(Fraction(across)) * Fraction(footprint["depth"])


def _raw(point: Point, size: tuple[float, float]) -> Bounds:
    """The footprint's bounds without the padding of :func:`_bounds`."""
    x, y = Fraction(point.x), Fraction(point.y)
    dx, dy = Fraction(size[0]) / 2, Fraction(size[1]) / 2
    return x - dx, y - dy, x + dx, y + dy


def _clearing_step(
    obstacle: Bounds,
    half: tuple[Fraction, Fraction],
    origin: tuple[Fraction, Fraction],
    direction: tuple[Fraction, Fraction],
    start: Fraction,
    step: Fraction,
) -> int:
    """The first `k` at which a box of half size `half` centred at ``origin + direction * (start + k * step)`` is clear
    of `obstacle`.

    Moving along a fixed direction, the box leaves the obstacle on an axis once and for all, so the first clear `k` is
    the earlier of the two axes'. A unit direction has a nonzero component on at least one axis.
    """
    steps = []
    for axis in (0, 1):
        component = direction[axis]
        if component > 0:  # its near edge reaches the obstacle's far edge
            distance = (obstacle[axis + 2] + half[axis] - origin[axis]) / component
        elif component < 0:
            distance = (obstacle[axis] - half[axis] - origin[axis]) / component
        else:
            continue
        steps.append(math.ceil((distance - start) / step))
    return min(steps)


def _place_facilities(
    keys: list[str],
    targets: Mapping[str, TargetMetadata],
    network: NetworkGeometry,
    placements: dict[str, dict[str, Any]],
    explicit: Mapping[str, Any],
    sizes: Mapping[str, tuple[float, float]],
    obstacles: Mapping[str, Bounds],
) -> None:
    """Studio map spec §6.3: each of `keys`, in id order, at the first node it is bound to.

    A parking area or a charging station is centred on its node. A warehouse is moved from its node along the
    direction away from the network by half its extent along it plus 1 m, then further by its own extent while its raw
    footprint overlaps an obstacle (the raw footprint of an explicit placement other than a node, a parking area or a
    charging station) or a warehouse placed before it. Positions are exact until a single conversion to float.
    """
    index = _BoundsIndex({key: box for key, box in obstacles.items() if targets[key].kind not in _NOT_OBSTACLES})
    placed: list[Bounds] = []
    for key in keys:
        target = targets[key]
        assert target.node_ids is not None
        anchor = network.positions[target.node_ids[0]]
        if target.kind in _CENTRED:
            placements[key]["position"] = (anchor.x, anchor.y, 0.0)
            continue
        ux, uy = _direction(target.node_ids[0], network)
        extent = _extent(explicit.get(key, {}), placements[key]["footprint"], ux, uy)
        origin, direction = (Fraction(anchor.x), Fraction(anchor.y)), (Fraction(ux), Fraction(uy))
        half = (Fraction(sizes[key][0]) / 2, Fraction(sizes[key][1]) / 2)
        start, k = extent / 2 + 1, 0
        while True:
            distance = start + k * extent
            try:
                point = Point(float(origin[0] + direction[0] * distance), float(origin[1] + direction[1] * distance))
            except OverflowError as exc:
                raise LayoutError("auto position exceeds finite float64 range") from exc
            box = _raw(point, sizes[key])
            hits = [hit for _, hit in index.collisions(box)] + [other for other in placed if _overlap(box, other)]
            if not hits:
                break
            if math.ulp(point.x) > extent / 4 or math.ulp(point.y) > extent / 4:
                # a step must move the float position, or the search would not end
                raise LayoutError("auto placement exceeds float64 precision for its extent")
            k = max(k + 1, *(_clearing_step(other, half, origin, direction, start, extent) for other in hits))
        placements[key]["position"] = (point.x, point.y, 0.0)
        placed.append(box)


def auto_layout(
    targets: Mapping[str, TargetMetadata],
    topology: StaticTopology,
    explicit_values: ResolvedValues,
    *,
    limits: LayoutLimits = LayoutLimits(),
    network: NetworkGeometry | None = None,
) -> AutoLayer:
    """Compute once after all stages; `.data` is passed to resolve_layers.

    Only eligible static targets participate in topology; missing/excluded flow
    entries break adjacency. Explicit positions are never copied into auto data.
    Node visual positions are ignored in favor of metadata graph coordinates;
    consumers must use `.derived_positions` as the authoritative display location.
    `network` holds the node positions and neighbours that a target with ``node_ids`` is placed from; it is required
    when a target carries them.
    """
    if len(targets) > limits.max_placements:
        raise LayoutError(
            f"{len(targets)} layout targets exceed max_placements={limits.max_placements}: every entity created "
            "before activation is a target; raise the limit with Layout(env, limits=...)"
        )
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
        if target.size is not None:  # layered-v2: the vehicle's own size (the placement's x axis is its heading)
            default_footprint["width"], default_footprint["depth"] = target.size
        if target.node_ids is not None:
            # layered-v2: placed from its node, which the network must hold (studio map spec §6.3)
            if network is None or any(node not in network.positions for node in target.node_ids):
                raise LayoutError(f"node_ids of {key} name a node that is not in the network")
            if target.kind in _CENTRED:
                default_footprint["width"] = default_footprint["depth"] = 3.0
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
    # A facility bound to a node is placed there instead of in the grid (studio map spec §6.3).
    facing = [key for key in ordered if key in auto_ids and ordered[key].node_ids is not None]
    if facing:
        assert network is not None
        _place_facilities(facing, ordered, network, placements, explicit, sizes, unpadded)
        auto_ids.difference_update(facing)
    if auto_ids:
        cell_width = number(max(sizes[key][0] for key in auto_ids) + 2, "auto cell width", positive=True)
        cell_depth = number(max(sizes[key][1] for key in auto_ids) + 2, "auto cell depth", positive=True)
        exact_width, exact_depth = Fraction(cell_width), Fraction(cell_depth)
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
                    point = Point(float(column * exact_width), float(row * exact_depth))
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
                            math.ceil((other[3] + Fraction(sizes[key][1]) / 2 + 1) / exact_depth)
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
    return AutoLayer(
        data,
        ordered,
        topology,
        derived,
        tuple(unique[key] for key in sorted(unique)),
        algorithm=auto_algorithm(ordered.values()),
    )
