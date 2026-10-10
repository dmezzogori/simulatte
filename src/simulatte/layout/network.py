"""Pure base-network generation. Effective overrides and runtime binding live downstream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .dependencies import DependencyClosure
from .errors import LayoutDiagnostic, LayoutError
from .geometry import ExactPoint, Rectangle, exact, materialize, segment_intersection
from .layers import ResolvedValues
from .refs import Address
from .types import Direction, LayoutLimits, Point


if TYPE_CHECKING:
    from simulatte.intralogistics.graph import LayoutGraph, Node


@dataclass(frozen=True)
class NodeDefinition:
    address: Address
    id: str
    position: Point
    enabled: bool = True
    capacity: int | None = None
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeDefinition:
    address: Address
    source: Address
    target: Address
    enabled: bool | None = True
    direction: Direction | None = "both"
    speed_limit: float | None = None
    lineage: tuple[Address, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class NetworkDefinitions:
    """Immutable definitions, including disabled explicit elements.

    Adjacency contains only base-enabled traversals (before overrides); it is not a
    runtime graph. Explicit blockers are retained for validation after overrides.
    Pruned lattice candidates are absent, so no override can resurrect them.
    Lineage maps each source lane segment to all of its split child edges.
    """

    nodes: Mapping[Address, NodeDefinition] = field(default_factory=dict)
    edges: Mapping[Address, EdgeDefinition] = field(default_factory=dict)
    aliases: Mapping[Address, Address] = field(default_factory=dict)
    lineage: Mapping[Address, tuple[Address, ...]] = field(default_factory=dict)
    adjacency: Mapping[Address, tuple[tuple[Address, Address], ...]] = field(default_factory=dict)
    obstacles: tuple[Rectangle, ...] = ()
    clearance: float = 0

    def __post_init__(self) -> None:
        for name in ("nodes", "edges", "aliases", "lineage", "adjacency"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


def _limit(count: int, maximum: int, name: str) -> None:
    if count > maximum:
        raise LayoutError(f"{name} exceeded: estimated {count}, limit {maximum}")


def _point(values: Any) -> Point:
    return Point(*values)


def _obstacles(values: ResolvedValues, network: Mapping[str, Any]) -> tuple[Rectangle, ...]:
    result = []
    for id, blocked in sorted(network.get("blocked", {}).items()):
        for name in ("position", "width", "depth", "rotation"):
            values.require(("network", "blocked", id, name), explicit=True)
        result.append(
            Rectangle.create(
                f"blocked:{id}", _point(blocked["position"]), blocked["width"], blocked["depth"], blocked["rotation"]
            )
        )
    for id, placement in sorted(values.explicit_data.get("placements", {}).items()):
        if not placement.get("obstacle", False):
            continue
        if values.targets[id] == "agv":
            raise LayoutError(f"placements/{id}: AGVs cannot be obstacles")
        position = values.require(("placements", id, "position"), explicit=True)
        rotation = values.require(("placements", id, "rotation"), explicit=True)
        width = values.require(("placements", id, "footprint", "width"), explicit=True)
        depth = values.require(("placements", id, "footprint", "depth"), explicit=True)
        # Required leaves are read individually: a partial footprint is not physical geometry.
        result.append(Rectangle.create(f"placement:{id}", _point(position), width, depth, rotation, owner=id))
    return tuple(result)


class _Builder:
    def __init__(self, network: Mapping[str, Any], obstacles: tuple[Rectangle, ...], limits: LayoutLimits) -> None:
        self.network = network
        self.obstacles = obstacles
        self.clearance = network.get("clearance", 0)
        self.limits = limits
        self.nodes: dict[Address, NodeDefinition] = {}
        self.edges: dict[Address, EdgeDefinition] = {}
        self.aliases: dict[Address, Address] = {}
        self.lineage: dict[Address, list[Address]] = {}
        self.arc_count = 0

    def point_blockers(self, point: Point) -> tuple[str, ...]:
        return tuple(o.id for o in self.obstacles if o.blocks_point(point, self.clearance))

    def segment_blockers(self, a: Point, b: Point) -> tuple[str, ...]:
        return tuple(o.id for o in self.obstacles if o.blocks_segment(a, b, self.clearance))

    def node(
        self,
        address: Address,
        point: Point,
        *,
        id: str | None = None,
        enabled: bool = True,
        capacity: int | None = None,
        inherit_capacity: bool = True,
    ) -> None:
        _limit(len(self.nodes) + 1, self.limits.max_nodes, "max_nodes")
        self.nodes[address] = NodeDefinition(
            address,
            id or address.encoded_id,
            point,
            enabled,
            capacity if capacity is not None or not inherit_capacity else self.network.get("capacity"),
            self.point_blockers(point),
        )
        self.aliases[address] = address

    def edge(
        self,
        address: Address,
        source: Address,
        target: Address,
        attributes: Mapping[str, Any],
        *,
        lineage: tuple[Address, ...] = (),
        default_direction: Direction = "both",
    ) -> None:
        a, b = self.nodes[source].position, self.nodes[target].position
        if a == b:
            raise LayoutError(
                f"{address.to_dict()}: geometry precision: zero-length edge after float64 materialization"
            )
        direction = attributes.get("direction", default_direction)
        self.arc_count += 2 if direction == "both" else 1
        _limit(self.arc_count, self.limits.max_directed_arcs, "max_directed_arcs")
        self.edges[address] = EdgeDefinition(
            address,
            source,
            target,
            attributes.get("enabled", True),
            direction,
            attributes.get("speed_limit"),
            lineage,
            self.segment_blockers(a, b),
        )
        for parent in lineage:
            self.lineage.setdefault(parent, []).append(address)

    def finish(self) -> NetworkDefinitions:
        adjacency: dict[Address, list[tuple[Address, Address]]] = {a: [] for a in self.nodes}
        for address, edge in self.edges.items():
            if not edge.enabled or not self.nodes[edge.source].enabled or not self.nodes[edge.target].enabled:
                continue
            if edge.direction in ("both", "forward"):
                adjacency[edge.source].append((edge.target, address))
            if edge.direction in ("both", "backward"):
                adjacency[edge.target].append((edge.source, address))
        return NetworkDefinitions(
            self.nodes,
            self.edges,
            self.aliases,
            {a: tuple(children) for a, children in self.lineage.items()},
            {a: tuple(neighbors) for a, neighbors in adjacency.items()},
            self.obstacles,
            self.clearance,
        )


def _lattice(values: ResolvedValues, builder: _Builder) -> None:
    spec = builder.network
    bounds = values.require(("network", "bounds"), explicit=True)
    dx, dy = (Fraction(values.require(("grid", name), explicit=True)) for name in ("dx", "dy"))
    origin = _point(values.require(("grid", "origin"), explicit=True))
    xmin, ymin, xmax, ymax = (Fraction(value) for value in bounds)
    if xmin > xmax or ymin > ymax:
        raise LayoutError("lattice bounds must be ordered")
    ilo, ihi = -(-(xmin - Fraction(origin.x)) // dx), (xmax - Fraction(origin.x)) // dx
    jlo, jhi = -(-(ymin - Fraction(origin.y)) // dy), (ymax - Fraction(origin.y)) // dy
    nx, ny = max(0, ihi - ilo + 1), max(0, jhi - jlo + 1)
    if not nx or not ny:
        return
    _limit(nx * ny, builder.limits.max_nodes, "max_nodes")
    neighbors = spec.get("neighbors", 4)
    edge_estimate = max(0, nx - 1) * ny + nx * max(0, ny - 1)
    if neighbors == 8:
        edge_estimate += 2 * max(0, nx - 1) * max(0, ny - 1)
    _limit(
        edge_estimate * (2 if spec.get("direction") == "both" else 1),
        builder.limits.max_directed_arcs,
        "max_directed_arcs",
    )
    indices = {}
    for i in range(ilo, ihi + 1):
        for j in range(jlo, jhi + 1):
            point = materialize((Fraction(origin.x) + i * dx, Fraction(origin.y) + j * dy))
            if not builder.point_blockers(point):
                address = Address({"kind": "lattice", "i": i, "j": j})
                indices[i, j] = address
                builder.node(address, point)
    offsets = ((0, 1), (1, 0)) if neighbors == 4 else ((0, 1), (1, -1), (1, 0), (1, 1))
    for (i, j), source in indices.items():
        for di, dj in offsets:
            target = indices.get((i + di, j + dj))
            if target is None or (di and dj and ((i + di, j) not in indices or (i, j + dj) not in indices)):
                continue
            if not builder.segment_blockers(builder.nodes[source].position, builder.nodes[target].position):
                address = Address({"kind": "edge", "source": source.to_dict(), "target": target.to_dict()})
                builder.edge(
                    address,
                    source,
                    target,
                    {**spec, "enabled": spec.get("enabled"), "direction": spec.get("direction")},
                )


@dataclass(frozen=True)
class _Segment:
    address: Address
    source: Address
    target: Address
    a: Point
    b: Point
    attributes: Mapping[str, Any]


def _lanes(values: ResolvedValues, builder: _Builder) -> None:
    lanes = values.require(("network", "lanes"), explicit=True)
    count = sum(max(0, len(lane.get("vertices", ())) - 1) for lane in lanes.values())
    _limit(count * (count - 1) // 2, builder.limits.max_lane_segment_pairs, "max_lane_segment_pairs")
    _limit(sum(len(lane.get("vertices", ())) for lane in lanes.values()), builder.limits.max_nodes, "max_nodes")
    arc_estimate = sum(
        max(0, len(lane.get("vertices", ())) - 1) * (2 if lane.get("direction") == "both" else 1)
        for lane in lanes.values()
    )
    _limit(arc_estimate, builder.limits.max_directed_arcs, "max_directed_arcs")
    segments = []
    incidents: dict[ExactPoint, set[Address]] = {}
    for id, lane in sorted(lanes.items()):
        vertices = values.require(("network", "lanes", id, "vertices"), explicit=True)
        if len({v["id"] for v in vertices}) != len(vertices):
            raise LayoutError(f"lane {id!r}: duplicate vertex ids")
        for a, b in zip(vertices, vertices[1:]):
            source = Address({"kind": "lane_vertex", "lane": id, "vertex": a["id"]})
            target = Address({"kind": "lane_vertex", "lane": id, "vertex": b["id"]})
            address = Address({"kind": "lane_segment", "lane": id, "source": a["id"], "target": b["id"]})
            p, q = _point(a["position"]), _point(b["position"])
            if p == q:
                raise LayoutError(f"lane {id!r} vertices {a['id']!r}, {b['id']!r}: zero-length segment")
            if builder.segment_blockers(p, q):
                raise LayoutError(f"lane {id!r} segment {address.to_dict()}: intersects obstacle")
            segments.append(
                _Segment(
                    address,
                    source,
                    target,
                    p,
                    q,
                    {**lane, "enabled": lane.get("enabled"), "direction": lane.get("direction")},
                )
            )
            incidents.setdefault(exact(p), set()).add(source)
            incidents.setdefault(exact(q), set()).add(target)
    splits = [{exact(s.a), exact(s.b)} for s in segments]
    for i, first in enumerate(segments):
        for j in range(i + 1, len(segments)):
            second = segments[j]
            try:
                intersection = segment_intersection(first.a, first.b, second.a, second.b)
            except LayoutError as exc:
                raise LayoutError(f"{first.address.to_dict()} and {second.address.to_dict()}: {exc}") from exc
            if intersection is None:
                continue
            if intersection not in incidents:
                _limit(len(incidents) + 1, builder.limits.max_nodes, "max_nodes")
            incident = incidents.setdefault(intersection, set())
            for index, segment in ((i, first), (j, second)):
                if intersection not in splits[index]:
                    arc_estimate += 2 if segment.attributes.get("direction") == "both" else 1
                    _limit(arc_estimate, builder.limits.max_directed_arcs, "max_directed_arcs")
                    splits[index].add(intersection)
                incident.add(
                    segment.source
                    if intersection == exact(segment.a)
                    else segment.target
                    if intersection == exact(segment.b)
                    else segment.address
                )
    canonical = {}
    for point, incident in incidents.items():
        address = (
            next(iter(incident))
            if len(incident) == 1
            else Address(
                {"kind": "intersection", "incidents": [a.to_dict() for a in sorted(incident, key=lambda a: a._json)]}
            )
        )
        canonical[point] = address
    for point, address in sorted(canonical.items(), key=lambda item: item[1]._json):
        builder.node(address, materialize(point))
        for incident in incidents[point]:
            if incident.to_dict()["kind"] == "lane_vertex":
                builder.aliases[incident] = address
    for segment, points in zip(segments, splits):
        ordered = sorted(points, key=lambda p: (p[0] - exact(segment.a)[0]) ** 2 + (p[1] - exact(segment.a)[1]) ** 2)
        for a, b in zip(ordered, ordered[1:]):
            source, target = canonical[a], canonical[b]
            address = Address(
                {
                    "kind": "lane_child",
                    "segment": segment.address.to_dict(),
                    "source": source.to_dict(),
                    "target": target.to_dict(),
                }
            )
            builder.edge(address, source, target, segment.attributes, lineage=(segment.address,))
            if builder.edges[address].blockers:
                raise LayoutError(
                    f"{address.to_dict()}: stored split segment intersects obstacle after float64 materialization"
                )


def _explicit(values: ResolvedValues, builder: _Builder) -> None:
    nodes = values.require(("network", "nodes"), explicit=True)
    arcs = values.require(("network", "arcs"), explicit=True)
    _limit(len(nodes), builder.limits.max_nodes, "max_nodes")
    _limit(
        sum(2 if arc.get("direction", "forward") == "both" else 1 for arc in arcs),
        builder.limits.max_directed_arcs,
        "max_directed_arcs",
    )
    ids = {}
    for i, node in enumerate(nodes):
        id = node["id"]
        if id in ids:
            raise LayoutError(f"explicit graph contains duplicate node id {id!r}")
        address = Address({"kind": "explicit", "id": id})
        ids[id] = address
        builder.node(
            address,
            _point(node["position"]),
            id=id,
            enabled=node.get("enabled", True),
            capacity=node.get("capacity"),
            # Unset removes the node's capacity instead of restoring the network
            # default; ordinary absence still inherits that declared default.
            inherit_capacity=values.at(("network", "nodes", str(i), "capacity"), explicit=True).state != "unset",
        )
    directed = {}
    for arc in arcs:
        source, target = arc["source"], arc["target"]
        if source not in ids or target not in ids:
            raise LayoutError(f"explicit arc {source!r} -> {target!r}: foreign endpoint")
        direction = arc.get("direction", "forward")
        pairs = (
            [(source, target)]
            if direction == "forward"
            else [(target, source)]
            if direction == "backward"
            else [(source, target), (target, source)]
        )
        attributes = (arc.get("enabled"), arc.get("speed_limit"))
        for pair in pairs:
            if pair in directed:
                if directed[pair] != attributes:
                    raise LayoutError(f"conflicting repeated directed explicit edge {pair!r}")
                continue
            directed[pair] = attributes
            a, b = pair
            address = Address({"kind": "explicit_arc", "source": a, "target": b})
            builder.edge(
                address,
                ids[a],
                ids[b],
                {"enabled": attributes[0], "speed_limit": attributes[1]},
                default_direction="forward",
            )


def generate_base_network(values: ResolvedValues, *, limits: LayoutLimits) -> NetworkDefinitions:
    """Generate immutable address-owned definitions without callbacks or runtime objects.

    Caller records dependencies and validates enabled explicit geometry after overrides.
    Missing network yields empty definitions. Missing required generator inputs fail here.
    """
    network = values.explicit_data.get("network")
    if network is None:
        return NetworkDefinitions()
    variant = values.require(("network", "type"), explicit=True)
    builder = _Builder(network, _obstacles(values, network), limits)
    {"lattice": _lattice, "lanes": _lanes, "explicit": _explicit}[variant](values, builder)
    return builder.finish()


@dataclass(frozen=True)
class RequiredPathResult:
    source: Address
    target: Address
    path: tuple[Address, ...]


@dataclass(frozen=True)
class ResolvedNetwork:
    """The sole runtime graph plus immutable canonical metadata.

    ``nodes`` contains enabled canonical addresses only. ``aliases`` and
    ``definitions`` also retain disabled elements. Capacity keys are the same
    concrete Node instances consumed by binders and traffic managers. Handle owner
    checks belong to the facade; this pure layer accepts only logical Addresses.
    """

    graph: LayoutGraph
    definitions: NetworkDefinitions
    nodes: Mapping[Address, Node]
    capacities: Mapping[Node, int | None]
    diagnostics: tuple[LayoutDiagnostic, ...]
    weak_components: tuple[tuple[Address, ...], ...]
    strong_components: tuple[tuple[Address, ...], ...]

    def __post_init__(self) -> None:
        for name in ("nodes", "capacities"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def aliases(self) -> Mapping[Address, Address]:
        return self.definitions.aliases

    def node(self, address: Address) -> Node:
        canonical = self.aliases.get(address)
        if canonical is None or canonical not in self.nodes:
            raise LayoutError(f"missing or disabled required node: {address.to_dict()}")
        return self.nodes[canonical]

    def validate(
        self,
        *,
        required_nodes: Iterable[Address] = (),
        required_paths: Iterable[tuple[Address, Address]] = (),
        existing_entity_ids: Iterable[str] = (),
    ) -> tuple[RequiredPathResult, ...]:
        """Validate late-stage requirements without regenerating the graph.

        Returns canonical address paths suitable for saved binding metadata.
        Call before any NodeBinding is attached; pass all existing entity ids.
        """
        existing = set(existing_entity_ids)
        for definition in self.definitions.nodes.values():
            if definition.id in existing:
                raise LayoutError(f"network node id collision with existing entity: {definition.id!r}")
        for address in required_nodes:
            self.node(address)
        addresses = {node: address for address, node in self.nodes.items()}
        results = []
        for source, target in required_paths:
            start, end = self.node(source), self.node(target)
            # Reachability is topological; no floating path-cost arithmetic needed.
            parents: dict[Node, Node | None] = {start: None}
            pending = [start]
            for current in pending:
                if current == end:
                    break
                for neighbor in self.graph.neighbors(current):
                    if neighbor not in parents:
                        parents[neighbor] = current
                        pending.append(neighbor)
            if end not in parents:
                raise LayoutError(f"unreachable required path: {source.to_dict()} -> {target.to_dict()}")
            path = []
            cursor: Node | None = end
            while cursor is not None:
                path.append(addresses[cursor])
                cursor = parents[cursor]
            results.append(RequiredPathResult(source, target, tuple(reversed(path))))
        return tuple(results)


def _components(
    adjacency: Mapping[Address, tuple[tuple[Address, Address], ...]],
) -> tuple[tuple[tuple[Address, ...], ...], tuple[tuple[Address, ...], ...]]:
    reverse: dict[Address, list[Address]] = {a: [] for a in adjacency}
    for source, neighbors in adjacency.items():
        for target, _ in neighbors:
            reverse[target].append(source)
    seen: set[Address] = set()
    weak = []
    for start in adjacency:
        if start in seen:
            continue
        seen.add(start)
        pending = [start]
        for node in pending:
            for neighbor in [*(a for a, _ in adjacency[node]), *reverse[node]]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        weak.append(tuple(sorted(pending, key=lambda a: a._json)))
    seen.clear()
    finished = []
    for start in adjacency:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(adjacency[start]))]
        while stack:
            node, neighbors = stack[-1]
            child = next(neighbors, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child[0] not in seen:
                seen.add(child[0])
                stack.append((child[0], iter(adjacency[child[0]])))
    seen.clear()
    strong = []
    for start in reversed(finished):
        if start in seen:
            continue
        seen.add(start)
        pending = [start]
        for node in pending:
            for neighbor in reverse[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        strong.append(tuple(sorted(pending, key=lambda a: a._json)))
    return tuple(weak), tuple(strong)


def _physical_inputs(values: ResolvedValues, dependencies: DependencyClosure) -> None:
    # Structural absence is physical even when no graph has been declared.
    dependencies.record(values, ("network",), kind="presence")
    dependencies.record(values, ("network",), kind="variant")
    dependencies.record(values, ("grid",), kind="presence")
    if "network" in values.explicit_data:
        dependencies.read(values, ("network",))
        values.require(("network", "clearance"), explicit=True)
        network = values.explicit_data["network"]
        if network.get("type") == "lattice":
            dependencies.read(values, ("grid",))
            values.require(("network", "neighbors"), explicit=True)
        elif network.get("type") == "explicit":
            # Base node eligibility and raw arc expansion determine identities before
            # override application, so they cannot defer an unresolved input.
            for i in range(len(network.get("nodes", ()))):
                values.require(("network", "nodes", str(i), "enabled"), explicit=True)
            for i in range(len(network.get("arcs", ()))):
                values.require(("network", "arcs", str(i), "direction"), explicit=True)
    obstacles = dependencies.select(values, ("placements",), "obstacle")
    connectors = dependencies.select(values, ("placements",), "connector")
    for entity in sorted(set(obstacles) | set(connectors)):
        for leaf in ("kind", "position", "rotation", "footprint"):
            dependencies.read(values, ("placements", entity, leaf))
        if entity in connectors:
            for port, record in values.explicit_data["placements"][entity].get("ports", {}).items():
                if "connector" in record:
                    dependencies.read(values, ("placements", entity, "ports", port))


def resolve_network(
    values: ResolvedValues, dependencies: DependencyClosure, *, limits: LayoutLimits
) -> ResolvedNetwork:
    """Compile once, recording explicit physical inputs and negative predicates.

    The preparation coordinator tags these queries as network-owned. Direct callers
    needing late candidate extension should use ``dependencies.network_queries()``.
    Requirements declared by later stages use ``result.validate``; no callbacks,
    environment, binding, RNG or second graph are involved.
    """
    from simulatte.intralogistics.graph import Arc, LayoutGraph, Node

    from .connectors import add_connectors
    from .overrides import _matches, apply_overrides

    _physical_inputs(values, dependencies)
    definitions = add_connectors(values, generate_base_network(values, limits=limits), limits=limits)
    dependencies.record(values, ("overrides",), kind="membership")
    for i, record in enumerate(values.explicit_data.get("overrides", ())):
        path = ("overrides", str(i))
        dependencies.read(values, (*path, "target"))
        selected_nodes, selected_edges = _matches(definitions, record["target"])
        if selected_nodes or selected_edges:
            dependencies.read(values, (*path, "properties"))
    definitions, override_diagnostics = apply_overrides(values, definitions)
    nodes = {}
    ids = set()
    for address, definition in definitions.nodes.items():
        if definition.id in ids:
            raise LayoutError(f"network node id collision: {definition.id!r}")
        ids.add(definition.id)
        if definition.enabled:
            if definition.blockers:
                raise LayoutError(f"enabled node {address.to_dict()} intersects blockers {definition.blockers}")
            nodes[address] = Node(definition.id, definition.position.x, definition.position.y)
    arcs = {}
    adjacency: dict[Address, list[tuple[Address, Address]]] = {a: [] for a in nodes}
    for address, edge in definitions.edges.items():
        if not edge.enabled or edge.source not in nodes or edge.target not in nodes:
            continue
        if edge.blockers:
            raise LayoutError(f"enabled edge {address.to_dict()} intersects blockers {edge.blockers}")
        pairs = []
        if edge.direction in ("both", "forward"):
            pairs.append((edge.source, edge.target))
        if edge.direction in ("both", "backward"):
            pairs.append((edge.target, edge.source))
        for source, target in pairs:
            arc = Arc(nodes[source], nodes[target], False, edge.speed_limit)
            if (source, target) in arcs:
                if arcs[source, target] != arc:
                    raise LayoutError(f"conflicting effective directed edges: {source.to_dict()} -> {target.to_dict()}")
                continue
            arcs[source, target] = arc
            adjacency[source].append((target, address))
    _limit(len(arcs), limits.max_directed_arcs, "max_directed_arcs")
    final_adjacency = {a: tuple(neighbors) for a, neighbors in adjacency.items()}
    definitions = NetworkDefinitions(
        definitions.nodes,
        definitions.edges,
        definitions.aliases,
        definitions.lineage,
        final_adjacency,
        definitions.obstacles,
        definitions.clearance,
    )
    weak, strong = _components(final_adjacency)
    diagnostics = list(values.diagnostics) + list(override_diagnostics)
    if len(weak) > 1:
        diagnostics.append(
            LayoutDiagnostic(
                "disconnected-network",
                "warning",
                ("network",),
                None,
                None,
                f"optional network has {len(weak)} weak and {len(strong)} strong components",
            )
        )
    stage_order = {name: i for i, (name, _) in enumerate(values.bundle.code_stages)}
    diagnostics.sort(key=lambda d: (stage_order.get(d.stage, len(stage_order)), d.path, d.code, d.target or ""))
    return ResolvedNetwork(
        LayoutGraph._from_layout(nodes.values(), arcs.values()),
        definitions,
        nodes,
        {node: definitions.nodes[a].capacity for a, node in nodes.items()},
        tuple(diagnostics),
        weak,
        strong,
    )
