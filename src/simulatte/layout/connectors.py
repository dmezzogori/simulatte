"""Port geometry and target selection against one immutable base network."""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from .errors import LayoutError
from .geometry import Rectangle, _point_segment_distance, exact, materialize, point_distance_squared, transform
from .layers import ResolvedValues
from .network import EdgeDefinition, NetworkDefinitions, NodeDefinition, _limit
from .refs import Address
from .types import LayoutLimits, Point


def _outward(position: Point, direction: Point, width: float, depth: float) -> None:
    p, d = exact(position), exact(direction)
    half = (Fraction(width) / 2, Fraction(depth) / 2)
    if not any(d):
        raise LayoutError("port approach must be nonzero")
    boundary = [i for i in range(2) if abs(p[i]) == half[i]]
    if not boundary or any(abs(p[i]) > half[i] for i in range(2)):
        raise LayoutError("transport port must lie on its local footprint boundary")
    # At a corner an outward component suffices, including rays along one face.
    if not (any(p[i] * d[i] > 0 for i in boundary) or (len(boundary) == 2 and any(d[i] == 0 for i in boundary))):
        raise LayoutError("port approach must point outward from its local footprint")


def _approach(port: Point, vector: Point, owner: Rectangle, clearance: float, margin: float) -> Point:
    direction = exact(vector)
    scale = max(map(abs, direction))
    if not scale:
        raise LayoutError("geometry precision: transformed approach vector is zero")
    direction = (direction[0] / scale, direction[1] / scale)
    start = exact(port)
    threshold = (Fraction(clearance) + Fraction(margin)) ** 2
    vertices = tuple(map(exact, owner.vertices))
    step = Fraction(margin)
    for _ in range(2048):
        candidate = (start[0] + step * direction[0], start[1] + step * direction[1])
        # Materialize every attempted candidate so overflow is diagnosed immediately.
        stored = materialize(candidate)
        distance = (
            Fraction(0)
            if owner._contains(candidate)
            else min(_point_segment_distance(candidate, vertices[i], vertices[(i + 1) % 4]) for i in range(4))
        )
        if distance >= threshold and stored != port and owner.point_distance_squared(stored) >= threshold:
            return stored
        step *= 2
    raise LayoutError("geometry precision: approach exhausted 2048 candidate steps")


def _address(kind: str, entity: str, port: str, **extra: str) -> Address:
    return Address({"kind": kind, "entity": entity, "port": port, **extra})


def _target(base: NetworkDefinitions, approach: Point, connector: Mapping[str, Any], port: Address) -> Address:
    if "target" in connector:
        requested = Address(connector["target"])
        canonical = base.aliases.get(requested)
        candidates = [canonical] if canonical is not None else []
    else:
        candidates = list(base.nodes)
    ranked = sorted(
        candidates,
        key=lambda a: (point_distance_squared(approach, base.nodes[a].position), a._json),
    )
    failures = []
    for address in ranked:
        node = base.nodes[address]
        distance = point_distance_squared(approach, node.position)
        blockers = tuple(o.id for o in base.obstacles if o.blocks_segment(approach, node.position, base.clearance))
        reason = (
            "disabled"
            if not node.enabled
            else "max_distance"
            if "max_distance" in connector and distance > Fraction(connector["max_distance"]) ** 2
            else ",".join(blockers)
        )
        if not reason:
            return address
        failures.append(f"{address.to_dict()}: squared_distance={distance}, blockers={reason}")
    raise LayoutError(f"blocked connector {port.to_dict()}: no valid base candidate; {'; '.join(failures)}")


def add_connectors(values: ResolvedValues, base: NetworkDefinitions, *, limits: LayoutLimits) -> NetworkDefinitions:
    """Expand ports without allowing earlier connectors to become target candidates."""
    nodes, edges, aliases = dict(base.nodes), dict(base.edges), dict(base.aliases)
    arc_count = sum(2 if e.direction == "both" else 1 for e in edges.values())
    capacity = values.explicit_data.get("network", {}).get("capacity")
    for entity, placement in sorted(values.explicit_data.get("placements", {}).items()):
        for name, port_data in sorted(placement.get("ports", {}).items()):
            if "connector" not in port_data:
                continue
            path = ("placements", entity)
            position = Point(*values.require((*path, "position"), explicit=True))
            rotation = values.require((*path, "rotation"), explicit=True)
            width = values.require((*path, "footprint", "width"), explicit=True)
            depth = values.require((*path, "footprint", "depth"), explicit=True)
            port_path = (*path, "ports", name)
            local = Point(*values.require((*port_path, "position"), explicit=True))
            vector = Point(*values.require((*port_path, "approach"), explicit=True))
            _outward(local, vector, width, depth)
            owner = next((o for o in base.obstacles if o.owner == entity), None)
            if owner is None:
                owner = Rectangle.create(f"placement:{entity}", position, width, depth, rotation, owner=entity)
            port = transform(local, position, rotation)
            vector = transform(vector, Point(0, 0), rotation)
            connector = port_data["connector"]
            margin = values.require((*port_path, "connector", "approach_margin"), explicit=True)
            enabled = connector.get("enabled")
            direction = connector.get("direction")
            approach = _approach(port, vector, owner, base.clearance, margin)
            port_address = _address("port", entity, name)
            approach_address = _address("approach", entity, name)
            target = _target(base, approach, connector, port_address)
            inner_blockers = tuple(
                o.id for o in base.obstacles if o.owner != entity and o.blocks_segment(port, approach, base.clearance)
            )
            # Connector geometry is validated even if disabled, as target construction
            # cannot be repaired by property overrides.
            if inner_blockers:
                raise LayoutError(f"blocked connector {port_address.to_dict()}: interior blockers {inner_blockers}")
            nodes[port_address] = NodeDefinition(port_address, port_address.encoded_id, port, capacity=capacity)
            aliases[port_address] = port_address
            shared = approach == base.nodes[target].position
            canonical_approach = target if shared else approach_address
            aliases[approach_address] = canonical_approach
            if not shared:
                nodes[approach_address] = NodeDefinition(
                    approach_address, approach_address.encoded_id, approach, capacity=capacity
                )
            segments = [("interior", port_address, canonical_approach)]
            if not shared:
                segments.append(("outer", canonical_approach, target))
            for segment, source, destination in segments:
                address = _address("connector", entity, name, segment=segment)
                edges[address] = EdgeDefinition(
                    address, source, destination, enabled, direction, connector.get("speed_limit")
                )
            arc_count += len(segments) * (2 if direction == "both" else 1)
            _limit(len(nodes), limits.max_nodes, "max_nodes")
            _limit(
                arc_count,
                limits.max_directed_arcs,
                "max_directed_arcs",
            )
    return NetworkDefinitions(nodes, edges, aliases, base.lineage, obstacles=base.obstacles, clearance=base.clearance)
