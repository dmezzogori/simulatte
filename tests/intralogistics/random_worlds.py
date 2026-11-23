"""Random intralogistics worlds described as pure data, built two ways, and an independent monitor of their runs.

`world_spec(seed)` draws a world: a grid with removed links, chains, spurs, one-way arcs, passing segments, speed
limits, arcs with explicit lengths, block lengths, zones with and without hold-back, nodes of capacity 2,
warehouses, chargers, parking areas, low batteries, cancellations and interruptions, and sometimes a second fleet on
the same traffic controller. No two nodes share a position. `build_world` builds it with concrete graph objects
(``mode="explicit"``) or through ``Explicit`` and the handles of the environment's layout (``mode="layout"``); AGVs
are constructed with ``agv_id=`` and every component is named, so ids are equal in both modes. `run_world` runs a
world with a `Monitor` and a `StreamRecorder` and returns a `RunRecord`, which compares equal for equal runs.
`present` and `exercised` name the features a spec contains and those a recorded run used.

The `Monitor` takes its geometry from the spec (`reference_graph`), not from the graph the run built, and reports a
built graph that differs from it. It keeps its own body model of every vehicle, driven by the motion events only, and
checks after every domain event that the replayed deltas equal the live entities (from the first event and from the
initial state), and, under a `ReservationController`, that the bodies respect node, segment, zone and corridor
admission, that the holdings cover the bodies (exactly so for a vehicle at rest and at every exit of a travel), and
that the entities agree with the holdings ledger. It records violations instead of raising, so a run always ends. One
behaviour of the fleet, `KNOWN_LIMIT`, is recognised from its own events and recorded apart as a known limit.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Literal

from simulatte.environment import Environment
from simulatte.events import Event, apply_deltas
from simulatte.intralogistics import (
    AGV,
    SKU,
    AGVType,
    ChargingStation,
    CongestionAwareRouter,
    FastestRouter,
    FleetCoordinator,
    FreeFlowController,
    NearestIdleStrategy,
    NearestParkingPolicy,
    ParkingArea,
    ReservationController,
    ResumeDelivery,
    ReturnToOrigin,
    RoundRobinStrategy,
    ShortestDistanceRouter,
    TrapezoidalProfile,
    Warehouse,
)
from simulatte.intralogistics.events import (
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvMoveStarted,
    AgvRouted,
    AgvStranded,
    ChargingEnded,
    ChargingStarted,
    OrderStatusChanged,
    ParkingEntered,
    ParkingLeft,
    TrafficDeadlock,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.network import Corridor, Segment, Zone
from simulatte.intralogistics.order import TransferOrder

Mode = Literal["explicit", "layout"]
ROUTERS = ("shortest", "fastest", "congestion")
"""The three routers; ``"default"`` passes none, which is ``ShortestDistanceRouter()``."""
CONTROLLERS = ("reservation", "free")
EXCLUDED_FIELDS = frozenset({"seq", "ordinal"})
"""Envelope fields left out of a recorded event, as the parity tests of ``tests/layout`` leave them out."""
TRAVELING = ("TRAVELING_EMPTY", "TRAVELING_LOADED")


# --- the description --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArcSpec:
    source: str
    target: str
    bidirectional: bool = True
    speed_limit: float | None = None
    passing: bool = False
    length: float | None = None
    """The travelled length when it is not the chord; ``None`` leaves the chord."""


@dataclass(frozen=True)
class ZoneSpec:
    id: str
    nodes: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]
    """Adjacent node pairs, each in id order; with a block length a pair stands for its pieces and their nodes."""
    capacity: int
    hold_back: bool


@dataclass(frozen=True)
class WarehouseSpec:
    name: str
    input_bays: tuple[str, ...]
    output_bays: tuple[str, ...]
    pick_time: float
    put_time: float


@dataclass(frozen=True)
class VehicleSpec:
    """An AGV type; ``profile`` is ``("trapezoidal", max_speed, acceleration, deceleration)`` or ``("plain", v)``."""

    name: str
    profile: tuple[Any, ...]
    battery_capacity: float
    length: float
    min_gap: float
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    corner_speed: bool = False
    turn_time: bool = False
    idle_power: float = 0.0


@dataclass(frozen=True)
class OrderSpec:
    time: float
    sku: int
    quantity: int
    origin: int
    destination: int
    """Indices into the fleet's warehouses."""


@dataclass(frozen=True)
class FleetSpec:
    name: str
    vehicle: VehicleSpec
    agvs: tuple[tuple[str, str], ...]
    """``(agv id, start node id)``."""
    warehouses: tuple[WarehouseSpec, ...]
    chargers: bool
    parking: bool
    """Whether the fleet uses the world's chargers and parking areas."""
    orders: tuple[OrderSpec, ...]
    strategy: str = "nearest"
    parking_policy: bool = False
    recovery: str = "origin"
    lookahead_margin: float = 0.0
    clear_service_nodes: bool = True
    disturbances: tuple[tuple[float, float, bool], ...] = ()
    """``(delay, choice in [0, 1), cancel)``: after the delay, cancel or interrupt one of the live missions."""


@dataclass(frozen=True)
class WorldSpec:
    """A world as plain data: what `build_world` builds in either mode."""

    seed: int
    nodes: tuple[tuple[str, float, float], ...]
    arcs: tuple[ArcSpec, ...]
    block_length: float | None
    capacities: tuple[tuple[str, int], ...]
    zones: tuple[ZoneSpec, ...]
    derive_corridors: bool
    hold_back_dead_ends: bool
    priority: bool
    skus: tuple[float, ...]
    chargers: tuple[tuple[str, str, int], ...]
    """``(name, node id, slots)``."""
    parking: tuple[tuple[str, str, int], ...]
    """``(name, node id, capacity)``."""
    fleets: tuple[FleetSpec, ...]


class _Draw:
    """The node pool of one world: positions kept distinct, nodes handed out without repetition while they last."""

    def __init__(self, rnd: random.Random) -> None:
        self.rnd = rnd
        self.nodes: dict[str, tuple[float, float]] = {}
        self.taken: set[tuple[float, float]] = set()
        self.pool: list[str] = []

    def node(self, node_id: str, x: float, y: float) -> str:
        while (x, y) in self.taken:
            y += 0.013
        self.taken.add((x, y))
        self.nodes[node_id] = (x, y)
        return node_id

    def pick(self) -> str:
        return self.pool.pop() if self.pool else self.rnd.choice(sorted(self.nodes))


def _network(rnd: random.Random, draw: _Draw) -> tuple[list[ArcSpec], list[str], float, int]:
    """Grid with removed links, chains, spurs, passing segments, one-way arcs; the arcs, the leaves, spacing, size."""
    width, height = rnd.randint(2, 5), rnd.randint(2, 4)
    spacing = rnd.choice([10.0, 10.0, 4.0, 2.0])
    names = [f"g{i}_{j}" for i in range(width) for j in range(height)]
    alias = dict(zip(names, rnd.sample(names, len(names)))) if rnd.random() < 0.5 else {n: n for n in names}
    grid = {
        (i, j): draw.node(alias[f"g{i}_{j}"], spacing * i, spacing * j) for i in range(width) for j in range(height)
    }
    arcs: list[ArcSpec] = []
    extra = 0

    def link(a: str, b: str) -> None:
        limit = rnd.choice([None, None, None, 0.8, 0.5])
        passing = rnd.random() < 0.12
        length = None
        if rnd.random() < 0.1:
            (ax, ay), (bx, by) = draw.nodes[a], draw.nodes[b]
            length = math.hypot(ax - bx, ay - by) * 1.3
        if rnd.random() < 0.05:
            source, target = (a, b) if rnd.random() < 0.5 else (b, a)
            arcs.append(ArcSpec(source, target, False, limit, passing, length))
        else:
            arcs.append(ArcSpec(a, b, True, limit, passing, length))

    for (i, j), node in sorted(grid.items()):
        for di, dj in ((1, 0), (0, 1)):
            other = grid.get((i + di, j + dj))
            if other is None or rnd.random() < 0.18:
                continue
            if rnd.random() < 0.25:  # a chain of new nodes on the chord, slightly off it
                (ax, ay), (bx, by) = draw.nodes[node], draw.nodes[other]
                previous, k = node, rnd.randint(1, 3)
                for m in range(1, k + 1):
                    extra += 1
                    point = (ax + (bx - ax) * m / (k + 1), ay + (by - ay) * m / (k + 1) + 0.01 * extra)
                    chained = draw.node(f"c{extra:02d}", *point)
                    arcs.append(ArcSpec(previous, chained))
                    previous = chained
                arcs.append(ArcSpec(previous, other))
            else:
                link(node, other)
    leaves = []
    for _ in range(rnd.randint(1, 4)):  # spurs: dead ends hanging off a grid node
        root = rnd.choice(list(grid.values()))
        rx, ry = draw.nodes[root]
        previous = root
        for m in range(rnd.randint(1, 3)):
            extra += 1
            spur = draw.node(
                f"s{extra:02d}", rx - 0.3 * spacing * (m + 1) + 0.07 * extra, ry - 0.45 * spacing * (m + 1)
            )
            arcs.append(ArcSpec(previous, spur))
            previous = spur
        leaves.append(previous)
    return arcs, leaves, spacing, width + height


def _zones(rnd: random.Random, draw: _Draw, arcs: Sequence[ArcSpec]) -> list[ZoneSpec]:
    around: dict[str, set[str]] = {n: set() for n in draw.nodes}
    for arc in arcs:
        around[arc.source].add(arc.target)
        around[arc.target].add(arc.source)
    everything = sorted(draw.nodes)
    zones = []
    for z in range(rnd.randint(0, 2)):
        start = rnd.choice(everything)
        members, frontier = [start], [start]
        while frontier and len(members) < rnd.randint(1, 4):
            current = frontier.pop()
            for neighbour in sorted(around[current]):
                if neighbour not in members and len(members) < 4:
                    members.append(neighbour)
                    frontier.append(neighbour)
        pairs = [(a, b) for a in members for b in members if a < b and b in around[a] and rnd.random() < 0.7]
        only_pairs = bool(pairs) and rnd.random() < 0.2
        zones.append(
            ZoneSpec(
                f"Z{z}",
                () if only_pairs else tuple(sorted(members)),
                tuple(sorted(pairs)),
                rnd.choice([1, 1, 2]),
                rnd.random() < 0.5,
            )
        )
    return zones


def world_spec(seed: int) -> WorldSpec:
    """The world of `seed`; the same seed always gives an equal spec.

    The AGVs take their start nodes first, so every world has at least two AGVs in its first fleet and at least one in
    its second fleet when it has one; warehouses, chargers and parking areas take the nodes left, then any node.
    """
    rnd = random.Random(seed)
    draw = _Draw(rnd)
    arcs, leaves, spacing, size = _network(rnd, draw)
    block_length = rnd.choice([None, None, None, 0.45 * spacing, 0.7 * spacing])
    everything = sorted(draw.nodes)
    draw.pool = list(everything)
    rnd.shuffle(draw.pool)
    capacities = {rnd.choice(everything): 2 for _ in range(rnd.randint(0, 3))}
    zones = _zones(rnd, draw, arcs)
    skus = tuple(rnd.choice([[1.0, 5.0], [0.5, 12.0, 20.0], [3.0]]))
    if rnd.random() < 0.6:
        profile: tuple[Any, ...] = (
            "trapezoidal",
            rnd.choice([1.5, 2.0]),
            rnd.choice([0.8, 0.3]),
            rnd.choice([0.8, 0.4]),
        )
    else:
        profile = ("plain", rnd.uniform(0.8, 1.6))
    starts = _starts(draw, zones, rnd.randint(2, 6))
    second = _second_fleet(seed, draw, skus, profile[0] == "trapezoidal", zones, starts) if rnd.random() < 0.3 else None
    warehouses = []
    for w in range(rnd.randint(3, 4)):
        inputs = [draw.pick() for _ in range(rnd.randint(1, 2))]
        outputs = rnd.sample(inputs, rnd.randint(0, 1)) + [draw.pick() for _ in range(rnd.randint(1, 2))]
        if leaves and rnd.random() < 0.4:
            outputs.append(rnd.choice(leaves))
        warehouses.append(
            WarehouseSpec(
                f"W{w}",
                tuple(dict.fromkeys(inputs)),
                tuple(dict.fromkeys(outputs)),
                rnd.uniform(1, 5),
                rnd.uniform(1, 5),
            )
        )
    chargers = tuple((f"C{c}", draw.pick(), rnd.randint(1, 2)) for c in range(rnd.randint(1, 2)))
    parking = tuple((f"P{p}", draw.pick(), rnd.randint(1, 2)) for p in range(rnd.randint(0, 3)))
    corner_speed, turn_time = rnd.random() < 0.25, rnd.random() < 0.2
    # A low battery holds 1 to 5 times the span of the grid: missions drain it, so AGVs charge and some strand. The
    # span is at least four grid spacings, so a full battery always covers the longest arc (1.3 spacings).
    low = rnd.random() < 0.5
    vehicle = VehicleSpec(
        "t",
        profile,
        rnd.uniform(1.0, 5.0) * spacing * size if low else 1e9,
        rnd.choice([0.0, 0.0, 1.0, 3.0, 6.0]),
        rnd.choice([0.0, 0.0, 0.5]),
        rnd.choice([0.2, 0.35, 0.5]),
        rnd.choice([0.02, 0.05, 0.15]),
        corner_speed,
        turn_time,
        rnd.choice([0.0, 0.0, 0.02, 0.05]) if low else 0.0,
    )
    ids = rnd.sample(["a", "b", "c", "d", "e", "f", "g"], len(starts))
    options = {
        "capacities": tuple(sorted(capacities.items())),
        "zones": tuple(zones),
        "derive_corridors": rnd.random() < 0.9,
        "hold_back_dead_ends": rnd.random() < 0.9,
        "priority": rnd.random() < 0.2,
    }
    strategy = rnd.choice(["nearest", "nearest", "nearest", "round"])
    parking_policy = rnd.choice([True, True, False])
    recovery = rnd.choice(["origin", "resume"])
    margin = rnd.choice([0.0, 0.0, 2.0])
    clear = rnd.random() < 0.85
    orders = []
    for time in sorted(rnd.uniform(0, 600) for _ in range(rnd.randint(8, 30))):
        origin, destination = rnd.sample(range(len(warehouses)), 2)
        orders.append(OrderSpec(time, rnd.randrange(len(skus)), rnd.randint(1, 3), origin, destination))
    disturbances: tuple[tuple[float, float, bool], ...] = ()
    if rnd.random() < 0.5:
        disturbances = tuple((rnd.uniform(5, 150), rnd.random(), rnd.random() < 0.5) for _ in range(rnd.randint(0, 6)))
    fleets = [
        FleetSpec(
            "fleet",
            vehicle,
            tuple((f"agv-{i}", start) for i, start in zip(ids, starts)),
            tuple(warehouses),
            True,
            True,
            tuple(orders),
            strategy,
            parking_policy,
            recovery,
            margin,
            clear,
            disturbances,
        )
    ]
    if second is not None:
        fleets.append(second)
    return WorldSpec(
        seed,
        tuple((node, x, y) for node, (x, y) in draw.nodes.items()),
        tuple(arcs),
        block_length,
        options["capacities"],
        options["zones"],
        options["derive_corridors"],
        options["hold_back_dead_ends"],
        options["priority"],
        skus,
        chargers,
        parking,
        tuple(fleets),
    )


def _starts(draw: _Draw, zones: Sequence[ZoneSpec], count: int, placed: Sequence[str] = ()) -> list[str]:
    """Up to `count` start nodes from the pool, never more in one zone than its capacity (with those `placed`)."""
    taken = list(placed)
    starts: list[str] = []
    while len(starts) < count and draw.pool:
        node = draw.pool.pop()
        if any(node in z.nodes and sum(n in z.nodes for n in taken) >= z.capacity for z in zones):
            continue
        starts.append(node)
        taken.append(node)
    return starts


def _second_fleet(
    seed: int, draw: _Draw, skus: Sequence[float], trapezoidal: bool, zones: Sequence[ZoneSpec], placed: list[str]
) -> FleetSpec | None:
    rnd = random.Random(seed * 7919 + 1)
    everything = sorted(draw.nodes)
    warehouses = tuple(
        WarehouseSpec(f"V{k}", (rnd.choice(everything),), (rnd.choice(everything),), 2.0, 2.0) for k in range(2)
    )
    vehicle = VehicleSpec(
        "u",
        ("plain", 1.1) if trapezoidal else ("trapezoidal", 1.2, 0.5, 0.5),
        1e9,
        rnd.choice([0.0, 2.0, 5.0]),
        rnd.choice([0.0, 0.3]),
    )
    starts = _starts(draw, zones, rnd.randint(1, 3), placed)
    if not starts:  # every node left is in a full zone
        return None
    chargers, parking = rnd.random() < 0.5, rnd.random() < 0.5
    parking_policy = rnd.random() < 0.5
    orders = tuple(
        OrderSpec(time, rnd.randrange(len(skus)), 1, 0, 1)
        for time in sorted(rnd.uniform(0, 600) for _ in range(rnd.randint(4, 15)))
    )
    return FleetSpec(
        "second",
        vehicle,
        tuple((f"bot-{chr(120 + k)}", start) for k, start in enumerate(starts)),
        warehouses,
        chargers,
        parking,
        orders,
        parking_policy=parking_policy,
    )


# --- building ---------------------------------------------------------------------------------------------------


class Plain:
    """A speed profile without limits: linear motion at a constant speed."""

    def __init__(self, speed: float) -> None:
        self.speed = speed

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        speed = self.speed if speed_limit is None else min(self.speed, speed_limit)
        return distance / speed


def _corner_speed(angle: float) -> float:
    return 0.4 if angle > 0.5 else math.inf


def _turn_time(angle: float) -> float:
    return 2.0 if angle > 1.0 else 0.0


def _priority(agv: AGV) -> float:
    return float(ord(agv.id[-1]) % 3)


def make_router(name: str) -> Any:
    """``"shortest"``, ``"fastest"``, ``"congestion"``, or ``"default"`` for no router argument (``None``)."""
    if name == "default":
        return None
    return {"shortest": ShortestDistanceRouter, "fastest": FastestRouter, "congestion": CongestionAwareRouter}[name]()


def vehicle_type(spec: VehicleSpec) -> AGVType:
    if spec.profile[0] == "trapezoidal":
        _, top, acceleration, deceleration = spec.profile
        profile: Any = TrapezoidalProfile(max_speed=top, acceleration=acceleration, deceleration=deceleration)
    else:
        profile = Plain(spec.profile[1])
    return AGVType(
        name=spec.name,
        speed_profile=profile,
        battery_capacity=spec.battery_capacity,
        weight_capacity=50.0,
        volume_capacity=2.0,
        load_time=1.0,
        unload_time=1.0,
        length=spec.length,
        min_gap=spec.min_gap,
        low_battery_threshold=spec.low_battery_threshold,
        critical_battery_threshold=spec.critical_battery_threshold,
        corner_speed=_corner_speed if spec.corner_speed else None,
        turn_time=_turn_time if spec.turn_time else None,
        idle_power=spec.idle_power,
    )


def graph_of(spec: WorldSpec) -> LayoutGraph:
    """The world's graph as written, before any block length."""
    nodes = {node: Node(node, x, y) for node, x, y in spec.nodes}
    arcs = []
    for arc in spec.arcs:
        options: dict[str, Any] = {"speed_limit": arc.speed_limit, "passing": arc.passing}
        if arc.length is not None:
            options["length"] = arc.length
        arcs.append(Arc(nodes[arc.source], nodes[arc.target], arc.bidirectional, **options))
    return LayoutGraph(list(nodes.values()), arcs)


def reference_graph(spec: WorldSpec) -> LayoutGraph:
    """The graph `spec` describes, built without the layout: `graph_of`, subdivided by the block length if any."""
    graph = graph_of(spec)
    return graph if spec.block_length is None else graph.subdivide(spec.block_length)


def zone_members(spec: WorldSpec, zone: ZoneSpec, graph: LayoutGraph) -> tuple[Any, ...]:
    """The members of `zone` in `graph` (the world's graph, subdivided when the world has a block length).

    A pair stands for its pieces and the nodes generated between them. Nodes come first in id order, then pairs in
    id order, the order in which the layout resolves a zone.
    """
    by_id = {node.id: node for node in graph.nodes}
    nodes = {by_id[node] for node in zone.nodes}
    pairs = set()
    for low, high in zone.pairs:
        generated = sorted(
            (node for node in graph.nodes if node.id.startswith(f"{low}~{high}#")),
            key=lambda node: int(node.id.rsplit("#", 1)[1]),
        )
        nodes.update(generated)
        chain = [by_id[low], *generated, by_id[high]]
        pairs.update(tuple(sorted(piece, key=lambda node: node.id)) for piece in zip(chain, chain[1:]))
    return (*sorted(nodes, key=lambda node: node.id), *sorted(pairs, key=lambda pair: (pair[0].id, pair[1].id)))


def _layout_zones(spec: WorldSpec) -> dict[str, Any] | None:
    """The zones as ``Explicit`` takes them: node ids and pairs of ids as members."""
    if not spec.zones:
        return None
    return {
        zone.id: {"members": [*zone.nodes, *zone.pairs], "capacity": zone.capacity, "hold_back": zone.hold_back}
        for zone in spec.zones
    }


@dataclass
class BuiltWorld:
    env: Environment
    spec: WorldSpec
    mode: Mode
    controller: Any
    fleets: tuple[FleetCoordinator, ...]
    agvs: dict[str, AGV]
    """By id, in construction order."""
    chargers: tuple[ChargingStation, ...]
    parking: tuple[ParkingArea, ...]
    orders: list[TransferOrder] = field(default_factory=list)
    """Every order submitted so far, of every fleet."""


def build_world(
    env: Environment,
    spec: WorldSpec,
    *,
    mode: Mode,
    router: str = "default",
    controller: Literal["reservation", "free"] = "reservation",
) -> BuiltWorld:
    """Build `spec` in `env` with concrete objects (``"explicit"``) or through ``Explicit`` and handles (``"layout"``).

    Per-node capacities and zones go to the controller's ``capacities=`` and ``zones=`` in explicit mode and to
    ``Explicit(capacities=..., zones=...)`` in layout mode. The order and disturbance processes are started here.
    """
    graph = graph_of(spec)
    if mode == "layout":
        from simulatte.layout import Explicit, Layout

        layout = Layout(env)
        layout.set_network(
            Explicit(graph, block_length=spec.block_length, capacities=dict(spec.capacities), zones=_layout_zones(spec))
        )
        supplied_graph: Any = layout.graph()
        node: Callable[[str], Any] = layout.node
        capacities: dict[Node, int] = {}
        zones: tuple[Zone, ...] = ()
    elif mode == "explicit":
        graph = reference_graph(spec)
        supplied_graph = graph
        values = {n.id: n for n in graph.nodes}
        node = values.__getitem__
        capacities = {values[n]: capacity for n, capacity in spec.capacities}
        zones = tuple(
            Zone(zone.id, zone_members(spec, zone, graph), zone.capacity, zone.hold_back) for zone in spec.zones
        )
    else:
        raise ValueError(f"unknown mode {mode!r}")
    if controller == "reservation":
        traffic: Any = ReservationController(
            env=env,
            graph=supplied_graph,
            capacities=capacities,
            zones=zones,
            derive_corridors=spec.derive_corridors,
            hold_back_dead_ends=spec.hold_back_dead_ends,
            priority_fn=_priority if spec.priority else None,
        )
    elif controller == "free":
        traffic = FreeFlowController()
    else:
        raise ValueError(f"unknown controller {controller!r}")
    skus = [SKU(id=f"s{k}", weight=weight, volume=0.1) for k, weight in enumerate(spec.skus)]
    chargers = tuple(ChargingStation(env=env, name=name, node=node(at), n_slots=n) for name, at, n in spec.chargers)
    parking = tuple(ParkingArea(env=env, name=name, node=node(at), capacity=n) for name, at, n in spec.parking)
    world = BuiltWorld(env, spec, mode, traffic, (), {}, chargers, parking)
    fleets = []
    for fleet_spec in spec.fleets:
        warehouses = [
            Warehouse(
                env=env,
                name=w.name,
                input_bays=[node(bay) for bay in w.input_bays],
                output_bays=[node(bay) for bay in w.output_bays],
                n_slots=2,
                products=skus,
                initial_inventory={sku: 500 for sku in skus},
                pick_time=w.pick_time,
                put_time=w.put_time,
            )
            for w in fleet_spec.warehouses
        ]
        kind = vehicle_type(fleet_spec.vehicle)
        agvs = [AGV(env=env, agv_type=kind, agv_id=agv_id, initial_node=node(at)) for agv_id, at in fleet_spec.agvs]
        world.agvs.update((agv.id, agv) for agv in agvs)
        fleet = FleetCoordinator(
            env=env,
            graph=supplied_graph,
            fleet=agvs,
            warehouses=warehouses,
            charging_stations=list(chargers) if fleet_spec.chargers else [],
            parking_areas=list(parking) if fleet_spec.parking else [],
            traffic_controller=traffic,
            router=make_router(router),
            dispatch_strategy=NearestIdleStrategy() if fleet_spec.strategy == "nearest" else RoundRobinStrategy(),
            repositioning_policy=NearestParkingPolicy() if fleet_spec.parking_policy else None,
            load_recovery_strategy=ResumeDelivery() if fleet_spec.recovery == "resume" else ReturnToOrigin(),
            lookahead_margin=fleet_spec.lookahead_margin,
            clear_service_nodes=fleet_spec.clear_service_nodes,
            default_metrics=False,
            name=fleet_spec.name,
        )
        fleets.append(fleet)
        env.process(_submit(world, fleet, fleet_spec, warehouses, skus))
        if fleet_spec.disturbances:
            env.process(_disturb(world, fleet, fleet_spec))
    world.fleets = tuple(fleets)
    return world


def _submit(world: BuiltWorld, fleet: FleetCoordinator, spec: FleetSpec, warehouses: list[Warehouse], skus: list[SKU]):
    env = world.env
    for order in spec.orders:
        yield env.timeout(order.time - env.now)
        created = fleet.create_order(
            sku=skus[order.sku],
            quantity=order.quantity,
            origin=warehouses[order.origin],
            destination=warehouses[order.destination],
        )
        world.orders.append(created)
        fleet.submit(created)


def _disturb(world: BuiltWorld, fleet: FleetCoordinator, spec: FleetSpec):
    env = world.env
    for delay, choice, cancel in spec.disturbances:
        yield env.timeout(delay)
        live = [order for order in world.orders if order.id in fleet._active_missions]
        if not live:
            continue
        order = live[int(choice * len(live))]
        if cancel:
            fleet.cancel(order)
        else:
            process = fleet._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("breakdown")


# --- recording --------------------------------------------------------------------------------------------------


def normalize(event: Event) -> tuple[type, tuple[tuple[str, Any], ...]]:
    """An event as ``(type, ((field, value), ...))`` without its envelope fields ``seq`` and ``ordinal``."""
    return type(event), tuple((f.name, getattr(event, f.name)) for f in fields(event) if f.name not in EXCLUDED_FIELDS)


class StreamRecorder:
    """Records every event (domain and observer) emitted after the prelude, normalized.

    The prelude is what is emitted before the initial state is captured at activation; it is not recorded, because
    its attachment counts depend on when nodes are bound. ``initial_state`` is a copy of the environment's.
    """

    def __init__(self, env: Environment) -> None:
        self.events: list[tuple[type, tuple[tuple[str, Any], ...]]] = []
        self.initial_state: dict[str, dict[str, Any]] | None = None
        env.request_projection(self._start)
        env.bus.subscribe(self._record, "**")

    def _start(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        self.initial_state = {entity: dict(fields) for entity, fields in state.items()}

    def _record(self, event: Event) -> None:
        if self.initial_state is not None:
            self.events.append(normalize(event))


@dataclass(frozen=True)
class RunRecord:
    """What a run of a world shows: equal for runs that cannot be told apart from their events and states."""

    events: tuple[tuple[type, tuple[tuple[str, Any], ...]], ...]
    initial_state: Mapping[str, Mapping[str, Any]]
    final_state: Mapping[str, Mapping[str, Any]]
    now: float
    quiescent: bool
    """Whether the event queue was empty when the run ended (always true without ``until``)."""
    violations: tuple[tuple[Any, ...], ...]
    known_limits: tuple[tuple[Any, ...], ...]
    """The cases of `KNOWN_LIMIT` the monitor recognised, apart from the violations."""
    fingerprint: str
    """``env.fingerprint()`` at the end of the run, as the parity tests of ``tests/layout`` compare it."""


def run_world(
    spec: WorldSpec,
    *,
    mode: Mode,
    router: str = "default",
    controller: Literal["reservation", "free"] = "reservation",
    until: float | None = None,
) -> RunRecord:
    """Build and run `spec` with a `Monitor`; the final checks run when the run ended with an empty queue."""
    env = Environment(seed=spec.seed, log_level="CRITICAL")
    with env:
        world = build_world(env, spec, mode=mode, router=router, controller=controller)
        monitor = Monitor(world)
        recorder = StreamRecorder(env)
        env.enable_digest()
        # The horizon is an event that can be recognised: SimPy puts it back into the queue when it stops the run, so
        # the run ended on its own when nothing else is left there.
        horizon = None if until is None else env.timeout(until)
        env.run(until=horizon)
        quiescent = all(entry[-1] is horizon for entry in env._queue)
        monitor.finish()
        if quiescent:
            monitor.final()
        return RunRecord(
            tuple(recorder.events),
            recorder.initial_state or {},
            {entity: dict(fields) for entity, fields in env.entities.snapshot().items()},
            float(env.now),
            quiescent,
            tuple(monitor.violations),
            tuple(monitor.known_limits),
            env.fingerprint(),
        )


# --- what a run exercised ----------------------------------------------------------------------------------------

FEATURES = (
    "an AGV moves",
    "the second fleet moves",
    "a recorded length traversed",
    "a block piece traversed",
    "a one-way arc traversed",
    "a passing segment traversed",
    "a passing segment held by two",
    "a node of capacity 2 held by two",
    "a zone reserved",
    "a zone waited for",
    "a zone held by two",
    "a hold-back zone reserved",
    "a corridor reserved",
    "parking entered",
    "an order cancelled",
    "a breakdown",
    "charging",
    "stranding",
    "a traffic wait",
    "a stuck wait",
    "a deadlock",
    "a make-way move",
)
"""What `present` and `exercised` name; waits, deadlocks and moves of the fleet have no counterpart in a spec."""


def _recorded(spec: WorldSpec, graph: LayoutGraph) -> set[frozenset[str]]:
    """The node pairs of `graph` (the reference graph) that carry a recorded length: the arcs declared with one, or
    their pieces under a block length."""
    pairs = set()
    for arc in spec.arcs:
        if arc.length is None:
            continue
        low, high = sorted((arc.source, arc.target))
        inner = sorted(
            (node.id for node in graph.nodes if node.id.startswith(f"{low}~{high}#")),
            key=lambda node: int(node.rsplit("#", 1)[1]),
        )
        chain = [low, *inner, high]
        pairs.update(frozenset(piece) for piece in zip(chain, chain[1:]))
    return pairs


def present(spec: WorldSpec) -> frozenset[str]:
    """The features of `FEATURES` that `spec` contains, whether or not a run uses them."""
    found = {"an AGV moves"}
    graph = reference_graph(spec)
    if _recorded(spec, graph):
        found.add("a recorded length traversed")
    for node in graph.nodes:
        for other in graph.neighbors(node):
            arc = graph.arc_between(node, other)
            if "#" in node.id or "#" in other.id:
                found.add("a block piece traversed")
            if graph.arc_between(other, node) is None:
                found.add("a one-way arc traversed")
            if arc.passing:
                found.update(("a passing segment traversed", "a passing segment held by two"))
    if len(spec.fleets) > 1:
        found.add("the second fleet moves")
    if spec.capacities:
        found.add("a node of capacity 2 held by two")
    if spec.zones:
        found.update(("a zone reserved", "a zone waited for", "a zone held by two"))
    if any(zone.hold_back for zone in spec.zones):
        found.add("a hold-back zone reserved")
    if spec.derive_corridors:
        found.add("a corridor reserved")
    if spec.parking:
        found.add("parking entered")
    disturbances = [cancel for fleet in spec.fleets for _, _, cancel in fleet.disturbances]
    if any(disturbances):
        found.add("an order cancelled")
    if not all(disturbances):
        found.add("a breakdown")
    if any(fleet.vehicle.battery_capacity < 1e9 for fleet in spec.fleets):
        found.update(("charging", "stranding"))
    return frozenset(found)


def exercised(spec: WorldSpec, events: Sequence[tuple[type, tuple[tuple[str, Any], ...]]]) -> frozenset[str]:
    """The features of `FEATURES` that the recorded `events` of a run of `spec` show."""
    graph = reference_graph(spec)
    nodes = {node.id: node for node in graph.nodes}
    recorded = _recorded(spec, graph)
    capacities = dict(spec.capacities)
    recovered: set[str] = set()  # orders that went through mission recovery: after a breakdown or a cancellation
    cancelled: set[str] = set()
    zones = {zone.id: zone for zone in spec.zones}
    holders: dict[str, set[str]] = {}
    found: set[str] = set()
    for kind, payload in events:
        e = dict(payload)
        if kind is AgvMoveStarted:
            found.add("an AGV moves")
            if e["agv"].startswith("bot-"):
                found.add("the second fleet moves")
            a, b = nodes[e["from_node"]], nodes[e["to_node"]]
            if frozenset((a.id, b.id)) in recorded:
                found.add("a recorded length traversed")
            if "#" in a.id or "#" in b.id:
                found.add("a block piece traversed")
            if graph.arc_between(b, a) is None:
                found.add("a one-way arc traversed")
            if graph.arc_between(a, b).passing:
                found.add("a passing segment traversed")
        elif kind is TrafficReserved:
            for resource in e["resources"]:
                held = holders.setdefault(resource, set())
                held.add(e["agv"])
                if resource in zones:
                    found.add("a zone reserved")
                    if zones[resource].hold_back:
                        found.add("a hold-back zone reserved")
                    if len(held) > 1:
                        found.add("a zone held by two")
                elif resource.startswith("corridor:"):
                    found.add("a corridor reserved")
                elif len(held) > 1 and capacities.get(resource, 1) > 1:
                    found.add("a node of capacity 2 held by two")
                elif len(held) > 1 and resource not in nodes:
                    a, b = (nodes[end] for end in _segment_ends(resource, nodes))
                    arc = graph.arc_between(a, b) or graph.arc_between(b, a)
                    if arc is not None and arc.passing:
                        found.add("a passing segment held by two")
        elif kind is TrafficReleased:
            for resource in e["resources"]:
                holders.get(resource, set()).discard(e["agv"])
        elif kind is TrafficWaitStarted:
            found.add("a traffic wait")
            if any(resource in zones for resource in e["resources"]):
                found.add("a zone waited for")
        elif kind is OrderStatusChanged:
            if e["reason"] == "cancelled":
                found.add("an order cancelled")
                cancelled.add(e["order"])
            elif e["reason"] in ("interrupted", "load_recovery"):
                recovered.add(e["order"])
        elif kind is AgvMoveInterrupted and e["reason"] == "breakdown":
            found.add("a breakdown")
        elif kind is ParkingEntered:
            found.add("parking entered")
        elif kind is ChargingStarted:
            found.add("charging")
        elif kind is AgvStranded:
            found.add("stranding")
        elif kind is TrafficWaitStuck:
            found.add("a stuck wait")
        elif kind is TrafficDeadlock:
            found.add("a deadlock")
        elif kind is AgvRouted and e["reason"] == "make_way":
            found.add("a make-way move")
    if recovered - cancelled:
        found.add("a breakdown")
    return frozenset(found)


def _segment_ends(segment: str, nodes: Mapping[str, Node]) -> tuple[str, str]:
    """The two node ids of a segment id ``"<a>~<b>"`` whose ends may themselves contain ``~`` (block nodes)."""
    parts = segment.split("~")
    for cut in range(1, len(parts)):
        a, b = "~".join(parts[:cut]), "~".join(parts[cut:])
        if a in nodes and b in nodes:
            return a, b
    raise KeyError(segment)


# --- the monitor ------------------------------------------------------------------------------------------------


def covered(motion: Mapping[str, Any], duration: float, tau: float, length: float) -> float:
    """Distance covered `tau` after the start of an arc of `length`, from the motion description of its move."""
    if tau <= 0:
        return 0.0
    if tau >= duration:
        return length
    curve = motion.get("curve")
    if curve == "piecewise":
        s, v = 0.0, motion["v0"]
        for d, a in motion["phases"]:
            if tau <= d:
                return min(length, s + v * tau + 0.5 * a * tau * tau)
            s += v * d + 0.5 * a * d * d
            v = max(0.0, v + a * d)
            tau -= d
        return length
    if curve == "trapezoidal":
        top, a, d, distance = motion["v_max"], motion["accel"], motion["decel"], motion["distance"]
        da, dd = top * top / (2 * a), top * top / (2 * d)
        if da + dd > distance:
            top = math.sqrt(2 * distance * a * d / (a + d))
            da, dd = top * top / (2 * a), top * top / (2 * d)
        ta, tc = top / a, (distance - da - dd) / top
        if tau <= ta:
            return 0.5 * a * tau * tau
        if tau <= ta + tc:
            return da + top * (tau - ta)
        t3 = tau - ta - tc
        return min(distance, da + top * tc + top * t3 - 0.5 * d * t3 * t3)
    return length * tau / duration


def end_speed(motion: Mapping[str, Any]) -> float:
    if motion.get("curve") != "piecewise":
        return 0.0
    v = motion["v0"]
    for d, a in motion["phases"]:
        v = max(0.0, v + a * d)
    return v


def seg_id(a: str, b: str) -> str:
    low, high = sorted((a, b))
    return f"{low}~{high}"


class Body:
    """The independent model of one vehicle: an odometer of its front and the clear-odometer of what it covers."""

    def __init__(self, agv: AGV, extent: float) -> None:
        self.extent = extent
        self.odometer = 0.0
        self.at: str | None = agv.current_node.id if agv.current_node is not None else None
        self.arc: tuple[str, str, float, float, Mapping[str, Any], float, bool] | None = None
        self.nodes: dict[str, float] = {}  # node id -> odometer at which the body clears it
        self.segs: dict[str, tuple[float, tuple[str, str]]] = {}  # segment id -> (clear odometer, direction)

    def front(self, t: float) -> float:
        if self.arc is None:
            return self.odometer
        _, _, t0, t1, motion, length, stalled = self.arc
        if stalled:
            return self.odometer
        return self.odometer + covered(motion, t1 - t0, t - t0, length)

    def occupancy(self, t: float) -> tuple[set[str], set[str], dict[str, Any], dict[str, Any]]:
        """``(sure nodes, maybe nodes, sure segments {id: direction}, maybe segments)``; maybe is within 1e-6."""
        front = self.front(t)
        tol = 1e-6 * max(1.0, abs(front))
        sure_n: set[str] = set()
        maybe_n: set[str] = set()
        sure_s: dict[str, Any] = {}
        maybe_s: dict[str, Any] = {}
        if self.at is not None:
            sure_n.add(self.at)
        elif self.arc is not None and self.arc[6]:
            sure_n.add(self.arc[0])
        for n, clear in self.nodes.items():
            if front < clear - tol:
                sure_n.add(n)
            elif front <= clear + tol and n not in sure_n:
                maybe_n.add(n)
        for s, (clear, direction) in self.segs.items():
            if front < clear - tol:
                sure_s[s] = direction
            elif front <= clear + tol:
                maybe_s[s] = direction
        return sure_n, maybe_n - sure_n, sure_s, maybe_s


@dataclass
class _Slot:
    """A parking slot an AGV took, with what the monitor saw of its trip since (for the known limit)."""

    entered: float
    to_park: bool = False  # routed to the area's node
    arrived: bool = False  # reached the area's node since
    diverted: bool = False  # started a charge on the way, before arriving
    charge_ended: float | None = None
    flagged: list[tuple[Any, ...]] = field(default_factory=list)  # "resting elsewhere" while charging after a diversion


KNOWN_LIMIT = "parking slot kept while charging on the way to park"
"""The known limit: an AGV takes a parking slot when it sets off to park, starts a charge before it gets there (at a
charger it turned to, or one on its route), and keeps the slot while it charges, until the charge ends (fleet behaviour,
the same in both modes)."""


class Monitor:
    """Checks a run of a built world after every domain event; see the module docstring. ``violations`` lists
    ``(time, what, *details)``; ``known_limits`` lists the cases of `KNOWN_LIMIT` as ``(time, KNOWN_LIMIT, area, agv,
    entered, released)``; ``events`` counts the domain events seen and ``checked`` those checked against the body
    model."""

    def __init__(self, world: BuiltWorld, *, replay: bool = True) -> None:
        self.world = world
        self.env = world.env
        self.controller = world.controller
        self.reservation = isinstance(world.controller, ReservationController)
        self.replay = replay
        self.violations: list[tuple[Any, ...]] = []
        self.known_limits: list[tuple[Any, ...]] = []
        self.events = 0
        self.checked = 0
        self.state = {entity: dict(fields) for entity, fields in self.env.entities.snapshot().items()}
        self.seeded: dict[str, dict[str, Any]] | None = None
        self.bodies: dict[str, Body] | None = None
        self._ready = False
        self._park_seen: dict[tuple[str, str], float] = {}
        self._slots: dict[tuple[str, str], _Slot] = {}
        self._chargers = {node for _, node, _ in world.spec.chargers}
        self._areas = {name: node for name, node, _ in world.spec.parking}
        self.env.bus.subscribe(self.on, "*")
        if self.reservation:
            self._patch_exits()

    # The model, made once the network is bound

    def _setup(self) -> None:
        world = self.world
        graph = reference_graph(world.spec)
        self.graph = graph
        self._compare_geometry(world.fleets[0].graph)
        self.nodes = {node.id: node for node in graph.nodes}
        facilities: dict[str, int] = {}
        for area in world.parking:
            facilities[area.node.id] = facilities.get(area.node.id, 0) + area.capacity
        for station in world.chargers:
            facilities[station.node.id] = facilities.get(station.node.id, 0) + station.n_slots
        declared = dict(world.spec.capacities)
        self.cap = {n: declared.get(n, facilities.get(n, 1)) for n in self.nodes}
        self.around: dict[str, set[str]] = {n: set() for n in self.nodes}
        self.passing: dict[str, bool] = {}
        for node in graph.nodes:
            for other in graph.neighbors(node):
                self.around[node.id].add(other.id)
                self.around[other.id].add(node.id)
                self.passing[seg_id(node.id, other.id)] = graph.arc_between(node, other).passing
        self.zone_members: dict[str, tuple[set[str], int]] = {}
        for zone in world.spec.zones:
            members = zone_members(world.spec, zone, graph)
            ids = {m.id if isinstance(m, Node) else seg_id(m[0].id, m[1].id) for m in members}
            self.zone_members[zone.id] = (ids, zone.capacity)
        self.corridors = self._corridors() if world.spec.derive_corridors else []
        if self.reservation:
            self._compare_traffic()
        self._ready = True

    def _compare_geometry(self, built: LayoutGraph) -> None:
        """The graph the mode under test built against the one the spec describes, node by node and arc by arc."""

        def arcs(graph: LayoutGraph) -> dict[tuple[str, str], tuple[float, float | None, bool]]:
            found = {}
            for node in graph.nodes:
                for other in graph.neighbors(node):
                    arc = graph.arc_between(node, other)
                    found[node.id, other.id] = (graph.length(node, other), arc.speed_limit, arc.passing)
            return found

        expected = {(n.id, n.x, n.y) for n in self.graph.nodes}
        found = {(n.id, n.x, n.y) for n in built.nodes}
        if found != expected:
            self.violation("built nodes differ from the spec", sorted(found ^ expected)[:4])
        expected_arcs, found_arcs = arcs(self.graph), arcs(built)
        if found_arcs != expected_arcs:
            keys = sorted(
                k for k in expected_arcs.keys() | found_arcs.keys() if expected_arcs.get(k) != found_arcs.get(k)
            )
            self.violation(
                "built arcs differ from the spec", [(k, expected_arcs.get(k), found_arcs.get(k)) for k in keys[:4]]
            )

    def _compare_traffic(self) -> None:
        """The capacities and zones the controller's network resolved against the ones the spec describes.

        A layout that resolves a capacity or a zone wrongly is noticed here even when the run does not misbehave.
        """
        network = self.controller.network
        found_caps = {node.id: network.capacity(node) for node in network.graph.nodes}
        if found_caps != self.cap:
            keys = sorted(k for k in found_caps.keys() | self.cap.keys() if found_caps.get(k) != self.cap.get(k))
            self.violation(
                "built node capacities differ from the spec",
                [(k, self.cap.get(k), found_caps.get(k)) for k in keys[:4]],
            )
        expected = {
            zone.id: (frozenset(self.zone_members[zone.id][0]), zone.capacity, zone.hold_back)
            for zone in self.world.spec.zones
        }
        found = {
            zone.id: (
                frozenset(m.id if isinstance(m, Node) else seg_id(m[0].id, m[1].id) for m in zone.members),
                zone.capacity,
                zone.hold_back,
            )
            for zone in network.zones
        }
        if found != expected:
            keys = sorted(k for k in expected.keys() | found.keys() if expected.get(k) != found.get(k))
            self.violation("built zones differ from the spec", [(k, expected.get(k), found.get(k)) for k in keys[:4]])

    def _corridors(self) -> list[tuple[list[str], set[str]]]:
        """Maximal two-way chains of interior nodes (two neighbours, capacity 1) between two ports, without passing."""

        def interior(n: str) -> bool:
            return len(self.around[n]) == 2 and self.cap[n] == 1

        def port(n: str) -> bool:
            return len(self.around[n]) >= 3 or (self.cap[n] > 1 and len(self.around[n]) >= 2)

        seen: set[str] = set()
        found = []
        for n in sorted(self.around):
            if not interior(n) or n in seen:
                continue
            ends = []
            for first in sorted(self.around[n]):
                previous, current = n, first
                side = []
                while interior(current) and current != n:
                    side.append(current)
                    (following,) = self.around[current] - {previous} or {previous}
                    previous, current = current, following
                ends.append((side, current))
            (side1, end1), (side2, end2) = ends
            chain = [end1, *reversed(side1), n, *side2, end2]
            seen.update(side1)
            seen.update(side2)
            seen.add(n)
            if end1 == n or end2 == n or not (port(end1) and port(end2)):
                continue  # a ring, or a chain that does not join two ports
            segs = [seg_id(a, b) for a, b in zip(chain, chain[1:])]
            two_way = all(
                self.graph.arc_between(self.nodes[a], self.nodes[b]) is not None
                and self.graph.arc_between(self.nodes[b], self.nodes[a]) is not None
                for a, b in zip(chain, chain[1:])
            )
            if two_way and not any(self.passing[s] for s in segs):
                found.append((chain, set(segs) | set(chain[1:-1])))
        return found

    def _patch_exits(self) -> None:
        """Check the holdings exactly whenever a travel of any fleet returns or raises."""
        monitor = self
        for fleet in self.world.fleets:
            executor = fleet._executor
            original = executor.travel

            def travel(agv, goal, *, loaded, purpose="travel", original=original):
                try:
                    outcome = yield from original(agv, goal, loaded=loaded, purpose=purpose)
                except BaseException as exc:
                    if not isinstance(exc, GeneratorExit):
                        monitor._exit(agv, "raise:" + type(exc).__name__)
                    raise
                monitor._exit(agv, outcome.name)
                return outcome

            executor.travel = travel

    def violation(self, *details: Any) -> None:
        self.violations.append((float(self.env.now), *details))

    # Per event

    def on(self, event: Any) -> None:
        env = self.env
        self.events += 1
        if self.replay:
            self._replay(event)
        if not env._activated:
            return
        if not self._ready:
            self._setup()
        self._track_slots(event, float(env.now))
        if self.bodies is None:
            if self.reservation and len(self.controller.ledger.placed()) < len(self.world.agvs):
                return
            self.bodies = {
                agv.id: Body(agv, agv.agv_type.length + agv.agv_type.min_gap) for agv in self.world.agvs.values()
            }
            if self.reservation and self.world.spec.derive_corridors:
                mine = sorted(sorted(members) for _, members in self.corridors)
                ledger = self.controller.ledger
                theirs = sorted(sorted(m.id for m in ledger.members(c)) for c in self.controller.network.corridors)
                if mine != theirs:
                    self.violation("corridor derivation differs", mine, theirs)
        t = float(env.now)
        if isinstance(event, AgvMoveStarted):
            self._move_started(event, t)
        elif isinstance(event, AgvMoveEnded):
            self._move_ended(event, t)
        self.checked += 1
        self._check_parking(t)
        if self.reservation:
            self._check(event, t)

    def _replay(self, event: Any) -> None:
        env = self.env
        live = env.entities.snapshot()
        apply_deltas(self.state, event.deltas)
        if self.state != live:
            self.violation("replay differs", event.type_name)
            self.state = {entity: dict(fields) for entity, fields in live.items()}
        initial = env._initial_state
        if self.seeded is None and initial is not None:
            self.seeded = {entity: dict(fields) for entity, fields in initial.items()}
        if self.seeded is not None:
            apply_deltas(self.seeded, event.deltas)
            if self.seeded != live:
                self.violation("seeded replay differs", event.type_name)
                self.seeded = {entity: dict(fields) for entity, fields in live.items()}

    def _move_started(self, event: AgvMoveStarted, t: float) -> None:
        assert self.bodies is not None
        body = self.bodies[event.agv]
        try:
            length = self.graph.length(self.nodes[event.from_node], self.nodes[event.to_node])
        except (KeyError, ValueError):  # a move the spec's graph cannot make: report it, and let the run go on
            self.violation("move on an arc the spec does not have", event.agv, event.from_node, event.to_node)
            return
        stalled = event.t_end == math.inf
        if body.at != event.from_node:
            self.violation("move started from a node the body is not at", event.agv, body.at, event.from_node)
        body.arc = (event.from_node, event.to_node, t, event.t_end, event.motion, length, stalled)
        body.at = None
        if not stalled:
            body.nodes.pop(event.from_node, None)
            body.nodes[event.from_node] = body.odometer + body.extent
            sid = seg_id(event.from_node, event.to_node)
            body.segs.pop(sid, None)
            body.segs[sid] = (body.odometer + length + body.extent, (event.from_node, event.to_node))

    def _move_ended(self, event: AgvMoveEnded, t: float) -> None:
        assert self.bodies is not None
        body = self.bodies[event.agv]
        if body.arc is None:
            self.violation("move ended without a move", event.agv, event.node)
            body.at = event.node
            return
        u, v, _, t1, motion, length, stalled = body.arc
        if event.node == v and not stalled:
            if abs(t - t1) > 1e-6 * max(1.0, abs(t)):
                self.violation("move ended off its t_end", event.agv, t, t1)
            body.odometer += length
            if end_speed(motion) < 1e-9 and self._ready and self.cap[v] > 1:
                body.nodes.clear()
                body.segs.clear()
        elif event.node != u:
            self.violation("move ended at an unexpected node", event.agv, event.node, u, v)
        body.arc = None
        body.at = event.node
        front = body.odometer
        tol = 1e-6 * max(1.0, front)
        for n in [n for n, clear in body.nodes.items() if clear <= front - tol]:
            del body.nodes[n]
        for s in [s for s, (clear, _) in body.segs.items() if clear <= front - tol]:
            del body.segs[s]

    def _track_slots(self, event: Any, t: float) -> None:
        """Follow each parking slot from ``parking.entered`` to ``parking.left`` for the evidence of the known limit."""
        if isinstance(event, ParkingEntered):
            self._slots[event.area, event.agv] = _Slot(t)
        elif isinstance(event, AgvRouted):
            for (area, agv), slot in self._slots.items():
                if agv == event.agv and event.destination == self._areas.get(area):
                    slot.to_park = True
        elif isinstance(event, AgvMoveEnded):
            for (area, agv), slot in self._slots.items():
                if agv == event.agv and event.node == self._areas.get(area):
                    slot.arrived = True
        elif isinstance(event, ChargingStarted):
            for (_, agv), slot in self._slots.items():
                if agv == event.agv and slot.to_park and not slot.arrived:
                    slot.diverted = True
        elif isinstance(event, ChargingEnded):
            for (_, agv), slot in self._slots.items():
                if agv == event.agv:
                    slot.charge_ended = t
        elif isinstance(event, ParkingLeft):
            slot = self._slots.pop((event.area, event.agv), None)
            if slot is not None and slot.flagged:
                if slot.charge_ended == t:  # released as the charge ends: the known limit
                    self.known_limits.append((slot.flagged[0][0], KNOWN_LIMIT, event.area, event.agv, slot.entered, t))
                else:
                    self.violations.extend(slot.flagged)

    def finish(self) -> None:
        """At the end of any run: a slot still held after a charge it was kept through is a violation after all."""
        for slot in self._slots.values():
            self.violations.extend(slot.flagged)
            slot.flagged.clear()

    def _check_parking(self, t: float) -> None:
        for area in self.world.parking:
            parked = list(area._agv_requests)
            if len(parked) > area.capacity:
                self.violation("parking over capacity", area.name)
            for agv in parked:
                key = (area.name, agv.id)
                if agv.current_node != area.node and agv.state.name not in TRAVELING:
                    first = self._park_seen.setdefault(key, t)
                    if t > first:
                        found = ("parking slot of an AGV resting elsewhere", area.name, agv.id, agv.state.name)
                        slot = self._slots.get(key)
                        charging = agv.state.name == "CHARGING" and agv.current_node.id in self._chargers
                        if slot is not None and slot.diverted and charging:
                            slot.flagged.append((t, *found))  # the known limit if released as the charge ends
                        else:
                            self.violation(*found)
                else:
                    self._park_seen.pop(key, None)

    # Reservation checks

    def held_ids(self, agv: AGV) -> tuple[set[str], dict[str, Any], set[str]]:
        ledger = self.controller.ledger
        nodes: set[str] = set()
        segs: dict[str, Any] = {}
        containers: set[str] = set()
        for resource in ledger.held(agv):
            if isinstance(resource, Node):
                nodes.add(resource.id)
            elif isinstance(resource, Segment):
                segs[resource.id] = ledger.direction(resource, agv)
            else:
                containers.add(resource.id)
        return nodes, segs, containers

    def _exact(self, agv: AGV, where: str) -> None:
        """At rest or at the end of a travel the holdings are exactly the body (up to the 1e-6 band)."""
        assert self.bodies is not None
        sure_n, maybe_n, sure_s, maybe_s = self.bodies[agv.id].occupancy(float(self.env.now))
        nodes, segs, containers = self.held_ids(agv)
        if not (sure_n <= nodes <= sure_n | maybe_n):
            self.violation("holdings != body (nodes)", where, agv.id, sorted(nodes), sorted(sure_n), sorted(maybe_n))
        if not (set(sure_s) <= set(segs) <= set(sure_s) | set(maybe_s)):
            self.violation("holdings != body (segments)", where, agv.id, sorted(segs), sorted(sure_s), sorted(maybe_s))
        held = nodes | set(segs)
        for zone, (members, _) in self.zone_members.items():
            if (zone in containers) != bool(members & held):
                self.violation("zone holding != members", where, agv.id, zone, sorted(held))
        ledger = self.controller.ledger
        for corridor in self.controller.network.corridors:
            members = {m.id for m in ledger.members(corridor)}
            ports = {corridor.nodes[0].id, corridor.nodes[-1].id}
            if corridor.id in containers and not (members & held) and not (ports & nodes):
                self.violation("corridor held without member or port", where, agv.id, corridor.id)
            if corridor.id not in containers and members & held:
                self.violation("member held without corridor", where, agv.id, corridor.id)
        if agv in self.controller._pending:
            self.violation("pending request while not travelling", where, agv.id)

    def _exit(self, agv: AGV, outcome: str) -> None:
        if self.bodies is not None:
            self._exact(agv, "exit:" + outcome)

    def _check(self, event: Any, t: float) -> None:
        assert self.bodies is not None
        occupancy = {}
        for agv_id, body in self.bodies.items():
            if body.at is not None or body.arc is not None:
                occupancy[agv_id] = body.occupancy(t)
        on_node: dict[str, list[str]] = {}
        on_seg: dict[str, list[tuple[str, Any]]] = {}
        for agv_id, (sure_n, _, sure_s, _) in occupancy.items():
            for n in sure_n:
                on_node.setdefault(n, []).append(agv_id)
            for s, direction in sure_s.items():
                on_seg.setdefault(s, []).append((agv_id, direction))
        for n, who in on_node.items():
            if len(who) > self.cap[n]:
                self.violation("PHYSICAL node over capacity", n, who, self.cap[n])
        for s, who in on_seg.items():
            if not self.passing[s] and len({direction for _, direction in who}) > 1:
                self.violation("PHYSICAL opposite directions on a segment", s, who)
        for zone, (members, capacity) in self.zone_members.items():
            inside = [
                agv_id for agv_id, (sure_n, _, sure_s, _) in occupancy.items() if members & (sure_n | set(sure_s))
            ]
            if len(inside) > capacity:
                self.violation("PHYSICAL zone over capacity", zone, inside, capacity)
        for chain, members in self.corridors:
            steps = {}
            for k in range(len(chain) - 1):
                steps[chain[k], chain[k + 1]] = True
                steps[chain[k + 1], chain[k]] = False
            directions = {}
            for agv_id, body in self.bodies.items():  # moving vehicles only: one at rest may be about to reverse
                arc = body.arc
                if arc is not None and not arc[6] and (arc[0], arc[1]) in steps and seg_id(arc[0], arc[1]) in members:
                    directions[agv_id] = steps[arc[0], arc[1]]
            if len(set(directions.values())) > 1:
                self.violation("PHYSICAL opposite directions in a corridor", chain, directions)
        agvs = self.world.agvs
        for agv_id, (sure_n, _, sure_s, _) in occupancy.items():
            agv = agvs[agv_id]
            nodes, segs, _ = self.held_ids(agv)
            if not sure_n <= nodes:
                self.violation("body not covered (nodes)", agv_id, sorted(sure_n - nodes), event.type_name)
            for s in sure_s:
                if s not in segs:
                    self.violation("body not covered (segment)", agv_id, s, event.type_name)
            if agv.state.name not in TRAVELING:
                self._exact(agv, "state:" + agv.state.name)
        self._check_ledger()

    def _check_ledger(self) -> None:
        """The ledger against the admission rules, and the entities against the ledger."""
        ledger = self.controller.ledger
        network = self.controller.network
        entities = self.env.entities
        for node in self.controller.graph.nodes:
            holders = ledger.holders(node)
            if len(holders) > self.cap.get(node.id, 1):
                self.violation("LEDGER node over capacity", node.id)
            binding = entities.node_binding(node)
            if binding is not None and binding.reserved_by != [h.id for h in holders]:
                self.violation("node entity != ledger", node.id, binding.reserved_by, [h.id for h in holders])
        for segment in network.segments:
            holders = ledger.holders(segment)
            binding = ledger._bindings[segment]
            by: dict[str, list[AGV]] = {"forward": [], "backward": []}
            for holder in holders:
                by[ledger.direction(segment, holder)].append(holder)
            if not segment.passing and by["forward"] and by["backward"]:
                self.violation("LEDGER both directions", segment.id)
            for group in by.values():
                together = group if segment.passing else holders
                extents = math.fsum(a.agv_type.length + a.agv_type.min_gap for a in together)
                if len(together) > 1 and extents > segment.length + 1e-12:
                    self.violation("LEDGER extents exceed the segment", segment.id, [a.id for a in together])
            if sorted(binding.forward) != sorted(h.id for h in by["forward"]) or sorted(binding.backward) != sorted(
                h.id for h in by["backward"]
            ):
                self.violation("segment entity != ledger", segment.id)
        for container in (*network.zones, *network.corridors):
            holders = ledger.holders(container)
            binding = ledger._bindings[container]
            if binding.reserved_by != [h.id for h in holders]:
                self.violation("zone entity != ledger", container.id)
            if isinstance(container, Corridor):
                orientations = {ledger.direction(container, h) for h in holders} - {None}
                if len(orientations) > 1:
                    self.violation("LEDGER corridor two orientations", container.id)
                if binding.orientation != (next(iter(orientations)) if orientations else None):
                    self.violation("corridor entity orientation != ledger", container.id)
            elif len(holders) > container.capacity:
                self.violation("LEDGER zone over capacity", container.id)
            for member in ledger.members(container):
                for holder in ledger.holders(member):
                    if holder not in holders:
                        self.violation("member held without container", container.id, holder.id)

    # At the end

    def final(self) -> None:
        """Checks for a run that ended on its own, with nothing left to process."""
        waits = {}
        if self.reservation:
            controller = self.controller
            unresolved = {agv_id for ids in controller._unresolved for agv_id in ids}
            waits = {w.agv.id: w for w in controller._queue}
            for agv_id, wait in waits.items():
                if not wait.stopped:
                    self.violation("pending claim of a moving AGV at quiescence", agv_id)
                if wait.timer is not None:
                    self.violation("review timer left", agv_id)
            explained = {agv_id for agv_id, wait in waits.items() if wait.stuck or agv_id in unresolved}
            changed = True
            while changed:
                changed = False
                for agv_id, wait in waits.items():
                    if agv_id not in explained and any(b.agv.id in explained for b in controller._blocking(wait)):
                        explained.add(agv_id)
                        changed = True
            if set(waits) - explained:
                self.violation("open wait with no report", sorted(set(waits) - explained))
        for agv in self.world.agvs.values():
            if agv.motion is not None and not agv.motion.get("stalled"):
                self.violation("motion left at quiescence", agv.id)
        for area in self.world.parking:
            for agv in area._agv_requests:
                if agv.current_node != area.node and agv.id not in waits:
                    self.violation("parking slot held away from the node at quiescence", area.name, agv.id)
