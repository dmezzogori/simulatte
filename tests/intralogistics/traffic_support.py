"""Support shared by the traffic tests: worlds declared on the layout, a full replay checker, the traffic invariants,
fake hooks, worlds of travels and the scenario of a segment claimed again twice.

Each builder declares its graph on the environment's layout with ``Explicit`` and returns a `World`; the keyword
arguments of a builder (``capacities``, ``zones``, ``block_length``, ...) go to ``Explicit``. All arcs are two-way and
keep the default chord length.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import simpy

from simulatte.environment import Environment
from simulatte.events import DomainEvent, apply_deltas
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node, NodeBinding
from simulatte.intralogistics.reservation import ReservationController, SegmentBinding, ZoneBinding
from simulatte.intralogistics.routing import Route, RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeFlowController, Reaction, StepAside, TrafficView, Wait, WaitHooks
from simulatte.intralogistics.travel import TravelOutcome
from simulatte.layout import Explicit, GraphRef, NodeRef


@dataclass
class World:
    """A graph declared on the layout of `env`: handles for constructors, canonical nodes for run time."""

    env: Environment
    graph: GraphRef

    def ref(self, node_id: str) -> NodeRef:
        """The handle of node `node_id`, for constructors."""
        return self.env.layout.node(node_id)

    def node(self, node_id: str) -> Node:
        """The canonical node `node_id` of the resolved network; prepares the layout if it is not prepared yet."""
        return self.env.layout.prepare().network.node(self.ref(node_id).address)

    def canonical_graph(self) -> LayoutGraph:
        """The graph of the resolved network, which holds the canonical nodes; prepares the layout if needed."""
        return self.env.layout.prepare().network.graph


def declare(env: Environment, graph: LayoutGraph, **network: Any) -> World:
    """Declare `graph` as the network of `env`'s layout, ``Explicit(graph, **network)``."""
    env.layout.set_network(Explicit(graph, **network))
    return World(env, env.layout.graph())


def build(
    env: Environment, points: Sequence[tuple[str, float, float]], links: Sequence[tuple[str, str]], **network: Any
) -> World:
    """A graph of the given ``(id, x, y)`` nodes in order, joined by two-way arcs, declared on `env`'s layout."""
    nodes = {name: Node(name, x, y) for name, x, y in points}
    return declare(env, LayoutGraph(nodes.values(), [Arc(nodes[a], nodes[b]) for a, b in links]), **network)


def t_layout(env: Environment, **network: Any) -> World:
    """The layout of spec Appendix A: a line ``STORE_OUT, C1, C2, LINE_IN`` with ``PARK`` hanging off ``C1``."""
    return build(
        env,
        [("STORE_OUT", 0, 0), ("C1", 10, 0), ("C2", 20, 0), ("LINE_IN", 30, 0), ("PARK", 10, 10)],
        [("STORE_OUT", "C1"), ("C1", "C2"), ("C2", "LINE_IN"), ("C1", "PARK")],
        **network,
    )


def line(env: Environment, n: int, **network: Any) -> World:
    """Nodes ``N0 ... N{n-1}`` at ``(10 * i, 0)`` joined in a row."""
    return build(
        env, [(f"N{i}", 10 * i, 0) for i in range(n)], [(f"N{i}", f"N{i + 1}") for i in range(n - 1)], **network
    )


def two_junction_corridor(env: Environment, **network: Any) -> World:
    """Junctions ``J1`` and ``J2`` with three neighbours each, joined by the chain ``J1, a, b, J2``."""
    return build(
        env,
        [
            ("J1", 0, 0),
            ("a", 10, 0),
            ("b", 20, 0),
            ("J2", 30, 0),
            ("x1", -10, 10),
            ("y1", -10, -10),
            ("x2", 40, 10),
            ("y2", 40, -10),
        ],
        [("J1", "a"), ("a", "b"), ("b", "J2"), ("J1", "x1"), ("J1", "y1"), ("J2", "x2"), ("J2", "y2")],
        **network,
    )


def loop_with_side_node(env: Environment, **network: Any) -> World:
    """Two parallel one-node chains ``J1, U, J2`` and ``J1, L, J2``, with a leaf on each junction."""
    return build(
        env,
        [("J1", 0, 0), ("U", 10, 10), ("L", 10, -10), ("J2", 20, 0), ("S1", -10, 0), ("S2", 30, 0)],
        [("J1", "U"), ("U", "J2"), ("J1", "L"), ("L", "J2"), ("J1", "S1"), ("J2", "S2")],
        **network,
    )


class StillFleet(FleetCoordinator):
    """A coordinator whose idle AGVs never make way (spec §7.10): an idle blocker stays where it stands.

    For tests of the travel executor and of waits, which hold a blocker in place on purpose or drive travels without
    taking the AGV out of ``IDLE``, as a mission does."""

    def make_way(self, agv: AGV, target: Node | None = None, *, concerned: Sequence[AGV] = ()) -> bool:
        return False


class FullReplay:
    """Applies the deltas of every domain event and compares EVERY live entity, of every kind, after each event.

    Two readers are checked (spec §17, U3): ``state`` replays from the first event seen (from ``{}`` when created
    before any entity, from the live snapshot otherwise), and ``seeded`` replays from ``env.initial_state`` as a
    reader that seeks from the start of the run does. Comparing after every event is comparing at every cursor.
    """

    def __init__(self, env: Environment, *, from_snapshot: bool = False) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = (
            {entity: dict(fields) for entity, fields in env.entities.snapshot().items()} if from_snapshot else {}
        )
        self.seeded: dict[str, dict[str, Any]] | None = None
        self.events: list[DomainEvent] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        live = self.env.entities.snapshot()
        apply_deltas(self.state, event.deltas)
        assert self.state == live, event
        initial = self.env._initial_state
        if self.seeded is None and initial is not None:
            self.seeded = {entity: dict(fields) for entity, fields in initial.items()}
        if self.seeded is not None:
            apply_deltas(self.seeded, event.deltas)
            assert self.seeded == live, event
        self.events.append(event)

    def of(self, *types: type[DomainEvent]) -> list[Any]:
        return [event for event in self.events if isinstance(event, types)]


class TrafficInvariants:
    """Asserts the invariants of spec §17 after every domain event, from the live entities and the controller.

    Node holders stay within the network capacity; a segment without ``passing`` never holds both directions, and a
    holder in its both-directions state (§7.2) is its only holder; zone holders stay within capacity; a corridor's
    holders never show both orientations; every AGV placed with the controller that has no active motion holds its
    current node; an AGV holding a member of a zone or corridor holds that zone or corridor. ``checked`` counts the
    events checked (those after the network is built).
    """

    def __init__(self, env: Environment, controller: ReservationController) -> None:
        self.env = env
        self.controller = controller
        self.checked = 0
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        controller = self.controller
        network = controller._network
        if network is None:
            return
        ledger = controller.ledger
        for entity in self.env.entities.live():
            if isinstance(entity, NodeBinding):
                assert len(entity.reserved_by) <= network.capacity(entity.node), (event, entity)
            elif isinstance(entity, SegmentBinding):
                assert entity.segment.passing or not (entity.forward and entity.backward), (event, entity.id)
                both = ledger.both_directions(entity.segment)
                assert not both or ledger.holders(entity.segment) == both == both[:1], (event, entity.id)
            elif isinstance(entity, ZoneBinding):
                if entity.rule == "exclusive":
                    capacity = entity.capacity
                    assert capacity is not None and len(entity.reserved_by) <= capacity, (event, entity.id)
                else:
                    corridor = entity.container
                    orientations = {ledger.direction(corridor, agv) for agv in ledger.holders(corridor)} - {None}
                    assert len(orientations) <= 1, (event, entity.id)
        for agv in ledger.placed():
            if agv.motion is None:
                assert agv.current_node in ledger.held(agv), (event, agv.id)
        for container in (*network.zones, *network.corridors):
            holding = ledger.holders(container)
            for member in ledger.members(container):
                for agv in ledger.holders(member):
                    assert agv in holding, (event, agv.id, container)
        self.checked += 1


@dataclass
class FakeHooks(WaitHooks):
    """Wait hooks that record their calls in ``calls`` as ``(name, args)``."""

    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)


def fake_hooks(route: Sequence[Node] = (), loaded: bool = False, resolutions: int = 0) -> FakeHooks:
    """Hooks returning `route`, `loaded` and `resolutions`; ``try_reroute``, ``try_step_aside`` and ``give_up`` stage
    nothing and return false."""
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def recording(name: str, value: Any) -> Any:
        def call(*args: Any) -> Any:
            calls.append((name, args))
            return value

        return call

    return FakeHooks(
        route=recording("route", tuple(route)),
        loaded=recording("loaded", loaded),
        resolutions=recording("resolutions", resolutions),
        try_reroute=recording("try_reroute", False),
        try_step_aside=recording("try_step_aside", False),
        give_up=recording("give_up", False),
        calls=calls,
    )


def steady() -> TrapezoidalProfile:
    """``v_max=1.5``, ``accel=decel=0.8``, not slowed by a draining battery."""
    return TrapezoidalProfile(1.5, 0.8, 0.8, battery_degradation_fn=lambda level: 1.0)


def vehicle(length: float | None = 0.0, profile: Any = None, battery: float = 1000.0) -> AGVType:
    return AGVType(
        name="cart",
        speed_profile=profile if profile is not None else steady(),
        battery_capacity=battery,
        weight_capacity=100.0,
        volume_capacity=10.0,
        length=length,
    )


@dataclass
class TravelWorld:
    """A world of travels: one fleet on a declared graph, with a full replay of its events; see :func:`world`."""

    env: Environment
    fleet: FleetCoordinator
    nodes: dict[str, Node]
    agvs: dict[str, AGV]
    replay: FullReplay
    start: int
    outcomes: dict[str, TravelOutcome] = field(default_factory=dict)
    caught: dict[str, object] = field(default_factory=dict)

    @property
    def controller(self) -> Any:
        return self.fleet.traffic_controller

    def go(self, agv: str, goal: str, *, at: float = 0.0, purpose: str = "travel") -> simpy.Process:
        """Travel `agv` to `goal` from the absolute time `at`, as a mission does: moving, then idle."""
        env, vehicle = self.env, self.agvs[agv]

        def mission() -> Any:
            if at:
                yield env.timeout(at - env.now)
            vehicle.transition_to(AGVState.TRAVELING_EMPTY)
            try:
                self.outcomes[agv] = yield from self.fleet._executor.travel(
                    vehicle, self.nodes[goal], loaded=False, purpose=purpose
                )
            except simpy.Interrupt as interrupt:
                self.caught[agv] = interrupt.cause
            vehicle.transition_to(AGVState.IDLE)

        return env.process(mission())

    def interrupt(self, process: simpy.Process, at: float, cause: object = "cancelled") -> None:
        def later() -> Any:
            yield self.env.timeout(at)
            process.interrupt(cause)

        self.env.process(later())

    @property
    def events(self) -> list[DomainEvent]:
        """The events since activation."""
        return self.replay.events[self.start :]

    def of(self, *types: type[DomainEvent], agv: str | None = None) -> list[Any]:
        return [e for e in self.events if isinstance(e, types) and (agv is None or getattr(e, "agv", None) == agv)]

    def held(self, agv: str) -> list[str]:
        return [resource.id for resource in self.controller.ledger.held(self.agvs[agv])]

    def hooks(self, agv: str) -> WaitHooks:
        return self.controller._pending[self.agvs[agv]].request.hooks


def world(
    shape: Callable[[Environment], World],
    agvs: dict[str, str],
    *,
    reserve: bool = True,
    agv_type: AGVType | None = None,
    router: Any = None,
    make_way: bool = False,
    lookahead_margin: float = 0.0,
    **options: Any,
) -> TravelWorld:
    """An activated world: `agvs` maps each AGV id to its start node id; one fleet with all the AGVs. Idle AGVs make
    way only with `make_way` (spec §7.10; tests of idle traffic have their own file). `shape` declares the graph on the
    environment's layout (a builder of this module or of a test file, its other arguments bound); `options` go to
    the ``ReservationController``. ``World.nodes`` holds the canonical nodes of the resolved network."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    declared = shape(env)
    controller: Any = (
        ReservationController(env=env, graph=declared.graph, **options) if reserve else FreeFlowController()
    )
    kind = agv_type or vehicle()
    vehicles = {
        name: AGV(env=env, agv_type=kind, agv_id=name, initial_node=declared.ref(start)) for name, start in agvs.items()
    }
    fleet = (FleetCoordinator if make_way else StillFleet)(
        env=env,
        graph=declared.graph,
        fleet=list(vehicles.values()),
        warehouses=[],
        charging_stations=[],
        traffic_controller=controller,
        router=router,
        default_metrics=False,
        lookahead_margin=lookahead_margin,
    )
    if reserve:
        TrafficInvariants(env, controller)
    env.activate()
    nodes = {node.id: node for node in declared.canonical_graph().nodes}
    return TravelWorld(env, fleet, nodes, vehicles, replay, len(replay.events))


def reverser(turn: Callable[[float], float] | None = None) -> AGVType:
    """3 m long, ``v_max=2.0``, ``accel=0.8``, ``decel=0.4``, not slowed by its battery; `turn` is its ``turn_time``."""
    profile = TrapezoidalProfile(2.0, 0.8, 0.4, battery_degradation_fn=lambda level: 1.0)
    return AGVType(
        name="cart",
        speed_profile=profile,
        battery_capacity=1e9,
        weight_capacity=100.0,
        volume_capacity=10.0,
        length=3.0,
        turn_time=turn,
    )


def turn_back(angle: float) -> float:
    """4 s to turn back; nothing otherwise."""
    return 4.0 if angle > 2.5 else 0.0


class AsideOnce:
    """Sends A aside to U the first time it is blocked; every wait keeps waiting, without a review."""

    def __init__(self) -> None:
        self.done = False

    def on_blocked(self, wait: Wait, view: TrafficView) -> tuple[Reaction, ...]:
        if wait.agv.id == "A" and not self.done:
            self.done = True
            return (StepAside(next(n for n in view.graph.nodes if n.id == "U")),)
        return ()

    def on_deadlock(self, waits: Any, view: TrafficView) -> list[tuple[AGV, Reaction]]:
        return []

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        return None


class AroundOnRequest(ShortestDistanceRouter):
    """Routes from W to V round the cycle ``W, K, U, V`` once `around` is set, otherwise by shortest distance."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[str, Node] = {}
        self.around = False

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        n = self.nodes
        if self.around and request.origin == n["W"] and request.destination == n["V"]:
            return Route((n["W"], n["K"], n["U"], n["V"]), 30.0)
        return super().route(request, view)


def claimed_again_twice() -> tuple[TravelWorld, AroundOnRequest]:
    """The world of the test of a segment claimed again twice (``test_travel``, spec §7.2, Ruling A3-n), activated,
    with its travels scheduled; the router is told to go round at 19.9."""
    router = AroundOnRequest()
    w = world(
        lambda env: build(
            env,
            [("U", 0, 0), ("V", 10, 0), ("W", 12.5, 0), ("Q", 22.5, 0), ("K", 6, -8)],
            [("U", "V"), ("V", "W"), ("W", "Q"), ("W", "K"), ("K", "U")],
            capacities={"U": 2, "V": 2},
        ),
        {"A": "U", "B": "U", "C": "Q"},
        agv_type=reverser(turn=turn_back),
        hold_back_dead_ends=False,
        policy=AsideOnce(),
        router=router,
        lookahead_margin=100.0,
    )
    router.nodes = w.nodes

    def around() -> Any:
        yield w.env.timeout(19.9)
        router.around = True

    w.interrupt(w.go("A", "Q"), 12.0)
    w.env.process(around())
    w.go("A", "V", at=20.0)
    w.go("B", "V", at=20.5)
    return w, router
