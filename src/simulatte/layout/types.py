"""Immutable, environment-free layout definitions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from .errors import LayoutError
from .refs import Address, NodeRef

if TYPE_CHECKING:
    from simulatte.intralogistics.graph import LayoutGraph

MAX_SAFE_INT = 2**53 - 1
Direction = Literal["both", "forward", "backward"]


def number(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutError(f"{name} must be a number (not bool)")
    try:
        result = float(value)
    except OverflowError as exc:
        raise LayoutError(f"{name} exceeds finite float64 range") from exc
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise LayoutError(
            f"{name} must be finite" + (" and positive" if positive else " and nonnegative" if nonnegative else "")
        )
    return result if result else 0.0


def normalize_rotation(value: float) -> float:
    return number(value, "rotation") % 360


_SCALARS = frozenset({str, int, float, bool, type(None)})


def _freeze(value: Any) -> Any:
    if type(value) in _SCALARS:  # most leaves; spares the slower check against the Mapping ABC
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _definition(value: Mapping[str, Any], name: str, *, patch: bool = False) -> Any:
    from .schema import validate_definition

    copied = validate_definition(dict(value), name)
    if not patch:
        _reject_unset(copied)
        required = {"lanes": {"vertices"}, "blocked": {"position", "width", "depth", "rotation"}}.get(name)
        if required is not None:
            for key, item in copied.items():
                if not required <= item.keys():
                    raise LayoutError(f"{name}/{key}: complete code definition requires {sorted(required)}")
    return _freeze(copied)


def _reject_unset(value: Any) -> None:
    if isinstance(value, dict):
        if value == {"$unset": True}:
            raise LayoutError("unset is a patch sentinel, not a complete code definition")
        for item in value.values():
            _reject_unset(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unset(item)


def _capacity(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 0 < value <= MAX_SAFE_INT):
        raise LayoutError("capacity must be a positive safe integer")


def _node_capacities(capacities: Mapping[str, int] | None, known: set[str]) -> Mapping[str, int]:
    """The per-node capacities of an explicit graph, by node id: each node must exist and its capacity be valid."""
    if capacities is None:
        return {}
    if not isinstance(capacities, Mapping) or not all(type(node_id) is str for node_id in capacities):
        raise LayoutError("capacities must be a mapping from node id to capacity")
    for node_id, value in capacities.items():
        if node_id not in known:
            raise LayoutError(f"capacities names {node_id!r}, which is not a node of the explicit graph")
        if type(value) is not int or not 0 < value <= MAX_SAFE_INT:
            raise LayoutError(f"capacities of node {node_id!r}: capacity must be a positive safe integer")
    return capacities


def _zone_members(
    zones: Mapping[str, Any] | None, ids: set[str], arcs: set[tuple[str, str]]
) -> Mapping[str, Any] | None:
    """The zones of an explicit graph with node ids and id pairs turned into the addresses the schema has.

    A ``str`` member is the node of that id. A pair of ids (a 2-tuple or 2-list of ``str``) is the segment between the
    two nodes, as the pair of a controller ``Zone`` is, whichever way the arc runs: the address of ``a -> b`` when the
    graph has that arc, else of ``b -> a``. Address mappings pass through, so the forms may be mixed.
    """
    if zones is None:
        return zones
    if isinstance(zones, (list, tuple)) and not zones:
        return {}  # an empty sequence declares no zones, as an empty mapping does
    if not isinstance(zones, Mapping) or not all(isinstance(zone, Mapping) for zone in zones.values()):
        raise LayoutError(
            "Explicit(zones=...) takes a mapping of zone id to a zone declaration, not Zone objects: for example "
            "zones={'z': {'members': ['A', ('A', 'B')], 'capacity': 1}}, with node ids and pairs of ids as members"
        )
    from simulatte.intralogistics.graph import Node

    converted = {}
    for zone_id, zone in zones.items():
        members = zone.get("members")
        if not isinstance(members, (list, tuple)):
            converted[zone_id] = zone
            continue
        found: list[Any] = []
        for member in members:
            if isinstance(member, str):
                if member not in ids:
                    raise LayoutError(f"zones/{zone_id}: member {member!r} is not a node of the explicit graph")
                found.append({"kind": "explicit", "id": member})
            elif isinstance(member, (list, tuple)) and len(member) == 2 and all(type(end) is str for end in member):
                a, b = member
                if (a, b) not in arcs and (b, a) not in arcs:
                    raise LayoutError(f"zones/{zone_id}: member {tuple(member)!r} is not an arc of the explicit graph")
                source, target = (a, b) if (a, b) in arcs else (b, a)
                found.append({"kind": "explicit_arc", "source": source, "target": target})
            elif isinstance(member, Node):
                raise LayoutError(f"zones/{zone_id}: member {member!r} is a Node: write its id {member.id!r}")
            elif isinstance(member, (list, tuple)) and len(member) == 2 and all(isinstance(e, Node) for e in member):
                raise LayoutError(
                    f"zones/{zone_id}: member {tuple(member)!r} is a pair of Node objects: write the pair of ids "
                    f"{(member[0].id, member[1].id)!r}"
                )
            else:
                found.append(member)
        converted[zone_id] = {**zone, "members": found}
    return converted


def _passing(value: bool | None) -> None:
    if value is not None and type(value) is not bool:
        raise LayoutError("passing must be a bool")


def _block_length(value: float | None) -> float | None:
    return None if value is None else number(value, "block_length", positive=True)


def _zones(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Validated, frozen authored zones; every zone of a code declaration is complete."""
    if value is None:
        return None
    zones = _definition(dict(value), "zones")
    for zone_id, zone in zones.items():
        if "members" not in zone:
            raise LayoutError(f"zones/{zone_id}: complete code definition requires ['members']")
    return zones


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float = 0

    def __post_init__(self) -> None:
        for name in ("x", "y", "z"):
            object.__setattr__(self, name, number(getattr(self, name), name))
        if self.z != 0:
            raise LayoutError("z must be zero in layout 1.0")


@dataclass(frozen=True)
class Footprint:
    width: float
    depth: float
    height: float = 0

    def __post_init__(self) -> None:
        for name in ("width", "depth", "height"):
            object.__setattr__(
                self, name, number(getattr(self, name), name, positive=name != "height", nonnegative=True)
            )


@dataclass(frozen=True)
class Grid:
    dx: float
    dy: float
    origin: Point = Point(0, 0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dx", number(self.dx, "dx", positive=True))
        object.__setattr__(self, "dy", number(self.dy, "dy", positive=True))
        if not isinstance(self.origin, Point):
            raise LayoutError("grid origin must be a Point")

    def snap(self, point: Point) -> Point:
        def coordinate(value: float, origin: float, spacing: float) -> float:
            origin_q, spacing_q = Fraction(origin), Fraction(spacing)
            q = (Fraction(value) - origin_q) / spacing_q
            index = q.numerator // q.denominator
            index += q - index >= Fraction(1, 2)
            try:
                return float(origin_q + index * spacing_q)
            except OverflowError as exc:
                raise LayoutError("snapped coordinate exceeds finite float64 range") from exc

        return Point(coordinate(point.x, self.origin.x, self.dx), coordinate(point.y, self.origin.y, self.dy))


@dataclass(frozen=True)
class Connector:
    target: NodeRef | None = None
    max_distance: float | None = field(default=None, kw_only=True)
    approach_margin: float = field(default=0.01, kw_only=True)
    enabled: bool = field(default=True, kw_only=True)
    direction: Direction = field(default="both", kw_only=True)
    speed_limit: float | None = field(default=None, kw_only=True)
    passing: bool | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.target is not None and not isinstance(self.target, NodeRef):
            raise LayoutError("connector target must be a NodeRef")
        _passing(self.passing)
        object.__setattr__(self, "approach_margin", number(self.approach_margin, "approach_margin", positive=True))
        for name in ("max_distance", "speed_limit"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, number(value, name, positive=True))
        if type(self.enabled) is not bool or self.direction not in ("both", "forward", "backward"):
            raise LayoutError("invalid connector enabled or direction")


@dataclass(frozen=True)
class Port:
    position: Point
    approach: Point
    connector: Connector | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position, Point) or not isinstance(self.approach, Point):
            raise LayoutError("port position and approach must be Points")
        if self.approach.x == self.approach.y == 0:
            raise LayoutError("port approach must be nonzero")
        if self.connector is not None and not isinstance(self.connector, Connector):
            raise LayoutError("port connector must be a Connector")


@dataclass(frozen=True)
class Lattice:
    bounds: tuple[float, float, float, float]
    neighbors: Literal[4, 8] = 4
    clearance: float = 0
    blocked: Mapping[str, Any] = field(default_factory=dict)
    capacity: int | None = None
    enabled: bool = True
    direction: Direction = "both"
    speed_limit: float | None = None
    passing: bool | None = None
    block_length: float | None = None
    zones: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _capacity(self.capacity)
        _passing(self.passing)
        data = {
            "type": "lattice",
            "bounds": list(self.bounds),
            "neighbors": self.neighbors,
            "clearance": self.clearance,
            "blocked": dict(self.blocked),
            "enabled": self.enabled,
            "direction": self.direction,
        }
        if self.capacity is not None:
            data["capacity"] = self.capacity
        if self.speed_limit is not None:
            data["speed_limit"] = self.speed_limit
        if self.passing is not None:
            data["passing"] = self.passing
        if self.block_length is not None:
            data["block_length"] = self.block_length
        if self.zones is not None:
            data["zones"] = dict(self.zones)
        _definition(data, "network")
        if self.bounds[0] > self.bounds[2] or self.bounds[1] > self.bounds[3]:
            raise LayoutError("lattice bounds must be ordered")
        object.__setattr__(self, "bounds", tuple(number(x, "bounds") for x in self.bounds))
        object.__setattr__(self, "clearance", number(self.clearance, "clearance", nonnegative=True))
        object.__setattr__(self, "blocked", _definition(dict(self.blocked), "blocked"))
        object.__setattr__(self, "block_length", _block_length(self.block_length))
        object.__setattr__(self, "zones", _zones(self.zones))


@dataclass(frozen=True)
class Lanes:
    lanes: Mapping[str, Any]
    clearance: float = 0
    blocked: Mapping[str, Any] = field(default_factory=dict)
    capacity: int | None = None
    block_length: float | None = None
    zones: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lanes", _definition(dict(self.lanes), "lanes"))
        object.__setattr__(self, "blocked", _definition(dict(self.blocked), "blocked"))
        object.__setattr__(self, "clearance", number(self.clearance, "clearance", nonnegative=True))
        _capacity(self.capacity)
        object.__setattr__(self, "block_length", _block_length(self.block_length))
        object.__setattr__(self, "zones", _zones(self.zones))


@dataclass(frozen=True)
class ExplicitNode:
    id: str
    position: Point
    capacity: int | None = None
    """The node's own capacity, which wins over the network's ``capacity``; ``None`` leaves the default."""


@dataclass(frozen=True)
class ExplicitArc:
    source: str
    target: str
    speed_limit: float | None = None
    enabled: bool = True
    passing: bool | None = None
    length: float | None = None
    """The travelled length when it is not the chord of the endpoints; ``None`` leaves the chord."""


@dataclass(frozen=True, init=False)
class Explicit:
    nodes: tuple[ExplicitNode, ...]
    arcs: tuple[ExplicitArc, ...]
    clearance: float
    blocked: Mapping[str, Any]
    capacity: int | None
    block_length: float | None
    zones: Mapping[str, Any] | None

    def __init__(
        self,
        graph: LayoutGraph,
        *,
        clearance: float = 0,
        blocked: Mapping[str, Any] | None = None,
        capacity: int | None = None,
        capacities: Mapping[str, int] | None = None,
        block_length: float | None = None,
        zones: Mapping[str, Any] | None = None,
    ) -> None:
        from simulatte.intralogistics.graph import chord_length

        nodes = tuple(graph.nodes)
        node_set = set(nodes)
        ids = [node.id for node in nodes]
        if len(set(ids)) != len(ids):
            raise LayoutError("explicit graph contains duplicate node ids")
        own = _node_capacities(capacities, set(ids))
        definitions = tuple(ExplicitNode(node.id, Point(node.x, node.y), own.get(node.id)) for node in nodes)
        positions = {node.id: node.position for node in definitions}
        for node in definitions:
            _definition({"id": node.id, "position": [node.position.x, node.position.y, 0]}, "explicitNode")
        arcs = []
        for source in nodes:
            for target, arc in graph._adjacency.get(source, {}).items():
                if target not in node_set:
                    raise LayoutError("explicit graph contains a foreign endpoint")
                if positions[source.id] == positions[target.id]:
                    raise LayoutError(
                        f"explicit arc {source.id!r} -> {target.id!r} is zero-length after float64 normalization"
                    )
                speed = None if arc.speed_limit is None else number(arc.speed_limit, "speed_limit", positive=True)
                # An arc cannot tell whether its length was given, so it is recorded when it is not the chord, which
                # the resolver would otherwise use (an exact comparison: it computes the chord the same way).
                length = number(arc.length, "length", positive=True)
                if length == chord_length(source.x, source.y, target.x, target.y):
                    length = None
                arcs.append(
                    ExplicitArc(source.id, target.id, speed, passing=True if arc.passing else None, length=length)
                )
        # Observable adjacency can contain sources absent from graph.nodes as well.
        if any(source not in node_set for source, edges in graph._adjacency.items() if edges):
            raise LayoutError("explicit graph contains a foreign endpoint")
        _capacity(capacity)
        object.__setattr__(self, "nodes", definitions)
        object.__setattr__(self, "arcs", tuple(arcs))
        object.__setattr__(self, "clearance", number(clearance, "clearance", nonnegative=True))
        object.__setattr__(self, "blocked", _definition(dict(blocked or {}), "blocked"))
        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "block_length", _block_length(block_length))
        object.__setattr__(self, "zones", _zones(_zone_members(zones, set(ids), {(a.source, a.target) for a in arcs})))


@dataclass(frozen=True)
class NetworkOverride:
    id: str
    target: Address | Mapping[str, Any]
    properties: Mapping[str, Any]

    def __post_init__(self) -> None:
        data = _definition(
            {
                "id": self.id,
                "target": self.target.to_dict() if isinstance(self.target, Address) else dict(self.target),
                "properties": dict(self.properties),
            },
            "override",
            patch=True,
        )
        object.__setattr__(self, "target", data["target"])
        object.__setattr__(self, "properties", data["properties"])


@dataclass(frozen=True)
class LayoutLimits:
    max_file_bytes: int = 16 * 1024 * 1024
    max_nesting: int = 64
    max_placements: int = 100_000
    max_nodes: int = 100_000
    max_directed_arcs: int = 400_000
    max_lane_segment_pairs: int = 1_000_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if type(value) is not int or not 0 < value <= MAX_SAFE_INT:
                raise LayoutError(f"{name} must be a positive safe integer")


@dataclass(frozen=True)
class PlacementDefaults:
    placeable: bool
    auto_position: bool
    footprint: Footprint | None
    rotation: float = 0
    ports: Mapping[str, Port] = field(default_factory=lambda: MappingProxyType({}))
    obstacle: bool = False
    visual: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@lru_cache(maxsize=256)  # a pure function of its arguments, called once per target and per placement
def placement_defaults(kind: str, *, explicitly_placed: bool = False) -> PlacementDefaults:
    if kind in ("shopfloor", "router", "fleet", "job", "order"):
        return PlacementDefaults(False, False, None)
    return PlacementDefaults(
        True,
        kind in ("server", "psp", "warehouse", "charging_station", "parking_area")
        or (explicitly_placed and kind not in ("agv", "node")),
        Footprint(0.1, 0.1) if kind == "node" else Footprint(1, 1),
    )
