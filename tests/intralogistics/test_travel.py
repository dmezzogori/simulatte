"""The travel executor (spec §6.6, §8.1-§8.4): routes, claims, the trail of released resources, interrupts, outcomes,
the hooks a waiting travel gives the controller and the standard search of a step aside.

Every world runs :class:`FullReplay` over every event; worlds with a ``ReservationController`` also run
:class:`TrafficInvariants`. Travels run in a mission-like process (:meth:`World.go`) that records the outcome, or the
cause of the interrupt that the travel raised again.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import simpy

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.events import (
    AgvLoadChanged,
    AgvMoveEnded,
    AgvMoveInterrupted,
    AgvMoveStarted,
    AgvRouted,
    OrderStatusChanged,
    TrafficDeadlock,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.kinematics import ArcProfile, Kinematics, plan_run
from simulatte.intralogistics.network import Claim
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import (
    ClaimRequest,
    FreeFlowController,
    GaveUp,
    Reaction,
    Reroute,
    StepAside,
    TrafficView,
    Wait,
    WaitHooks,
)
from simulatte.intralogistics.travel import TravelExecutor, TravelOutcome, standard_target
from simulatte.intralogistics.warehouse import Warehouse
from tests.intralogistics.traffic_support import (
    FullReplay,
    StillFleet,
    TrafficInvariants,
    build,
    declare,
    fake_hooks,
    line,
    t_layout,
)
from tests.intralogistics.traffic_support import World as Declared
from tests.intralogistics.traffic_support import two_junction_corridor

ARC = 8.541666666666668
"""Rest-to-rest time of a 10 m arc at ``v_max=1.5``, ``accel=decel=0.8``: ``TrapezoidalProfile.travel_time`` exactly
(8.541666666666666 within one unit in the last place)."""

TAIL = 2.2708333333333335
"""Time after departure at which the front has covered 2 m of a 10 m arc with the same profile, from rest."""

KIN = Kinematics(1.5, 0.8, 0.8)
"""The limits of :func:`steady`, and of the default profile at a full battery."""


def run_of(arcs: int) -> tuple[ArcProfile, ...]:
    """The profiles of a run over `arcs` arcs of 10 m from rest to rest at :data:`KIN` (spec §9.2)."""
    return plan_run(0.0, [10.0] * arcs, [1.5] * arcs, [0.0] + [1.5] * (arcs - 1) + [0.0], KIN)


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


class Plain:
    """A speed profile with only ``travel_time``: 2 m/s, no limits, no motion description."""

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return distance / 2.0


class Scripted:
    """A policy with fixed reactions; ``deadlock`` names the AGV id and the reaction staged in a deadlock."""

    def __init__(self, blocked: tuple[Reaction, ...] = (), deadlock: tuple[str, Reaction] | None = None) -> None:
        self.blocked = blocked
        self.deadlock = deadlock
        self.waits: list[Wait] = []

    def on_blocked(self, wait: Wait, view: TrafficView) -> tuple[Reaction, ...]:
        self.waits.append(wait)
        return self.blocked

    def on_deadlock(self, waits: Any, view: TrafficView) -> list[tuple[AGV, Reaction]]:
        if self.deadlock is None:
            return []
        name, reaction = self.deadlock
        return [(wait.agv, reaction) for wait in waits if wait.agv.id == name]

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        return None


@dataclass
class World:
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
    shape: Callable[[Environment], Declared],
    agvs: dict[str, str],
    *,
    reserve: bool = True,
    agv_type: AGVType | None = None,
    router: Any = None,
    make_way: bool = False,
    lookahead_margin: float = 0.0,
    **options: Any,
) -> World:
    """An activated world: `agvs` maps each AGV id to its start node id; one fleet with all the AGVs. Idle AGVs make
    way only with `make_way` (spec §7.10; tests of idle traffic have their own file). `shape` declares the graph on the
    environment's layout (a builder of ``traffic_support`` or of this file, its other arguments bound); `options` go to
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
    return World(env, fleet, nodes, vehicles, replay, len(replay.events))


def diamond(env: Environment, **network: Any) -> Declared:
    """``S`` to ``E`` through ``P`` (20 m) or through ``Q`` (about 28.3 m), with ``K`` hanging off ``S`` and an
    isolated node ``Z``."""
    return build(
        env,
        [("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0), ("K", -10, 0), ("Z", 50, 50)],
        [("S", "P"), ("P", "E"), ("S", "Q"), ("Q", "E"), ("S", "K")],
        **network,
    )


# --- free flow ---------------------------------------------------------------------------------------------------


def test_free_flow_arrivals_and_events() -> None:
    profile = TrapezoidalProfile(1.5, 0.8, 0.8)  # slowed by the battery level, sampled when the run starts
    w = world(t_layout, {"A": "STORE_OUT"}, reserve=False, agv_type=vehicle(profile=profile))
    w.go("A", "LINE_IN")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert profile.travel_time(10.0) == ARC == pytest.approx(8.541666666666666, rel=1e-15)
    expected, now = [], 0.0
    for arc in run_of(3):  # one run at the limits of the full battery, through C1 and C2 at speed
        now += arc.duration
        expected.append(now)
    assert expected[-1] == pytest.approx(profile.travel_time(30.0), rel=1e-12)
    assert [e.t for e in w.of(AgvMoveEnded)] == expected
    names = [e.type_name for e in w.events if e.type_name != "agv.state_changed"]
    assert names == ["agv.routed", *["agv.move_started", "agv.move_ended"] * 3]
    (routed,) = w.of(AgvRouted)
    assert (routed.origin, routed.destination, routed.nodes, routed.cost, routed.reason) == (
        "STORE_OUT",
        "LINE_IN",
        ("STORE_OUT", "C1", "C2", "LINE_IN"),
        30.0,
        "travel",
    )
    w.go("A", "PARK", at=100.0)
    w.env.run()
    assert [e.reason for e in w.of(AgvRouted)] == ["travel", "travel"]
    assert not [e for e in w.events if e.type_name.startswith("traffic.")]


def test_purpose_make_way_and_clear_node_are_the_routed_reason() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0"}, reserve=False)
    w.go("A", "N1", purpose="make_way")
    w.go("A", "N2", at=50.0, purpose="clear_node")
    w.go("A", "N0", at=100.0, purpose="charge")
    w.env.run()
    assert [e.reason for e in w.of(AgvRouted)] == ["make_way", "clear_node", "travel"]


def test_goal_equal_to_current_node_arrives_without_events() -> None:
    w = world(lambda env: line(env, 2), {"A": "N0"})
    before = len(w.events)
    outcome = w.env.run(w.env.process(w.fleet._executor.travel(w.agvs["A"], w.nodes["N0"], loaded=False)))
    assert outcome is TravelOutcome.ARRIVED
    assert len(w.events) == before


# --- reservation and release -------------------------------------------------------------------------------------


def test_a_follower_waits_and_proceeds_when_the_leader_tail_clears() -> None:
    w = world(lambda env: line(env, 4), {"L": "N1", "F": "N0"}, agv_type=vehicle(length=2.0))
    w.go("F", "N2")
    w.go("L", "N3")
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.ARRIVED, "L": TravelOutcome.ARRIVED}
    started = w.of(TrafficWaitStarted, agv="F")[0]
    assert (started.t, started.node, started.resources, started.blockers) == (0.0, "N1", ("N0~N1", "N1"), ("L",))
    released = w.of(TrafficReleased, agv="L")[0]
    assert (released.t, released.resources) == (TAIL, ("N1",))
    ended = w.of(TrafficWaitEnded, agv="F")[0]
    assert (ended.t, ended.reason) == (TAIL, "granted")
    assert w.of(AgvMoveStarted, agv="F")[0].t == TAIL


def test_the_node_behind_is_released_one_extent_after_departure() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0"}, agv_type=vehicle(length=2.0))
    w.go("A", "N2")
    w.env.run()
    released = [(e.t, e.resources) for e in w.of(TrafficReleased)]
    # N0 clears 2 m into the first arc; N0~N1 and N1 both clear 2 m into the second, passed at speed, in one call.
    first, second = run_of(2)
    assert released == [(TAIL, ("N0",)), (first.duration + second.time_at(2.0), ("N0~N1", "N1"))]
    assert w.held("A") == ["N1~N2", "N2"]


def test_a_profile_with_only_travel_time_releases_at_the_next_arrival() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0"}, agv_type=vehicle(length=2.0, profile=Plain()))
    w.go("A", "N2")
    w.env.run()
    trace = [
        (e.type_name, e.t, getattr(e, "resources", ()))
        for e in w.events
        if isinstance(e, (AgvMoveEnded, TrafficReleased))
    ]
    assert trace == [
        ("agv.move_ended", 5.0, ()),
        ("traffic.released", 5.0, ("N0",)),
        ("agv.move_ended", 10.0, ()),
        ("traffic.released", 10.0, ("N0~N1", "N1")),
    ]


def test_extent_longer_than_arc_holds_two_segments() -> None:
    w = world(lambda env: line(env, 5), {"A": "N0"}, agv_type=vehicle(length=12.0))
    w.go("A", "N2")
    w.env.run()
    assert w.held("A") == ["N0~N1", "N1", "N1~N2", "N2"]
    first, second = run_of(2)
    assert [(e.t, e.resources) for e in w.of(TrafficReleased)] == [(first.duration + second.time_at(2.0), ("N0",))]
    # The next travel releases what the tail clears 2 m into its first arc.
    w.go("A", "N4", at=100.0)
    w.env.run()
    assert w.outcomes["A"] is TravelOutcome.ARRIVED
    assert [(e.t, e.resources) for e in w.of(TrafficReleased)][1] == (100.0 + TAIL, ("N0~N1", "N1"))


def test_reversing_claims_the_trail_again() -> None:
    """A travel back over the trail of the previous one claims those resources again: they lie ahead, not behind."""
    w = world(lambda env: line(env, 3), {"A": "N0"}, agv_type=vehicle(length=12.0))
    w.go("A", "N1")
    w.go("A", "N0", at=50.0)
    w.env.run()
    assert w.outcomes["A"] is TravelOutcome.ARRIVED
    assert w.agvs["A"].current_node == w.nodes["N0"]
    assert w.held("A") == ["N0", "N0~N1", "N1"]


def test_at_the_exit_port_the_corridor_stays_and_gates_a_later_same_direction_claim() -> None:
    w = world(two_junction_corridor, {"A": "J1", "W": "y2", "F": "x1"})
    w.go("A", "J2")
    w.go("W", "x1", at=50.0)
    w.go("F", "x2", at=60.0)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert w.agvs["A"].current_node == w.nodes["J2"]
    assert w.held("A") == ["corridor:a", "J2"]
    assert [e.blockers for e in w.of(TrafficWaitStarted)] == [("A",), ("W",)]
    pending = w.controller._pending
    assert w.agvs["W"] in pending and w.agvs["F"] in pending
    assert w.held("F") == ["x1"]


# --- interrupts --------------------------------------------------------------------------------------------------


def test_an_interrupt_during_an_arc_takes_effect_where_the_run_comes_to_rest() -> None:
    """At 3 A holds N0 to N2 (the next arc and its braking distance). The arc to N1 keeps its profile, which passes N1
    at speed, so the first node a braking distance beyond it, N2, is where A comes to rest and the interrupt takes
    effect (spec §8.3)."""
    w = world(lambda env: line(env, 4), {"A": "N0"})
    process = w.go("A", "N3")
    w.interrupt(process, 3.0)
    w.interrupt(process, 5.0, "second")
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    agv = w.agvs["A"]
    assert agv.current_node == w.nodes["N2"]
    assert agv.battery.level == 980.0
    assert w.held("A") == ["N2"]
    assert w.env.entities.node_binding(w.nodes["N2"]).reserved_by == ["A"]
    first, second = run_of(2)
    rest = first.duration + second.duration
    tail = [(e.type_name, e.t) for e in w.events if e.type_name != "agv.state_changed"][-3:]
    assert tail == [("agv.move_ended", rest), ("traffic.released", rest), ("agv.move_interrupted", rest)]
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.reason, len(interrupted.deltas)) == ("N2", "cancelled", 0)


@pytest.mark.parametrize(("cause", "reason"), [("cancelled", "cancelled"), (None, "interrupted")])
def test_cancel_while_waiting_leaves_only_current_node_reserved(cause: object, reason: str) -> None:
    w = world(lambda env: line(env, 3), {"F": "N0", "L": "N1"})
    process = w.go("F", "N2")
    w.interrupt(process, 5.0, cause)
    w.env.run()
    assert w.env.now == 5.0  # the review timer of the wait went with it
    assert w.caught == {"F": cause}
    assert [(e.t, e.reason) for e in w.of(TrafficWaitEnded)] == [(5.0, "interrupted")]
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.t, interrupted.node, interrupted.reason) == (5.0, "N0", reason)
    assert w.held("F") == ["N0"]
    assert not w.controller._pending


# --- outcomes ----------------------------------------------------------------------------------------------------


def test_no_route() -> None:
    env = Environment(log_history_size=100)
    declared = build(env, [("A", 0, 0), ("B", 10, 0)], [])
    agv = AGV(env=env, agv_type=vehicle(), agv_id="V", initial_node=declared.ref("A"))
    fleet = FleetCoordinator(env=env, graph=declared.graph, fleet=[agv], warehouses=[], charging_stations=[])
    outcome = env.run(env.process(fleet._executor.travel(agv, declared.node("B"), loaded=False)))
    assert outcome is TravelOutcome.NO_ROUTE
    (error,) = env.log_history.query(component="FleetCoordinator")
    assert error.message == "No path from A to B for V"


def test_no_route_fails_the_order_after_returning_the_cargo() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    declared = build(env, [("ORIGIN", 0, 0), ("DEST", 10, 0)], [])
    sku = SKU("S", 1.0, 0.1)
    origin = Warehouse(
        env=env,
        name="WH-O",
        input_bays=[declared.ref("ORIGIN")],
        output_bays=[declared.ref("ORIGIN")],
        n_slots=1,
        products=[sku],
        initial_inventory={sku: 5},
        pick_time=1.0,
        put_time=1.0,
    )
    dest = Warehouse(
        env=env,
        name="WH-D",
        input_bays=[declared.ref("DEST")],
        output_bays=[declared.ref("DEST")],
        n_slots=1,
        products=[sku],
        pick_time=1.0,
        put_time=1.0,
    )
    agv = AGV(env=env, agv_type=vehicle(), agv_id="V", initial_node=declared.ref("ORIGIN"))
    fleet = FleetCoordinator(
        env=env, graph=declared.graph, fleet=[agv], warehouses=[origin, dest], charging_stations=[]
    )
    order = fleet.create_order(sku=sku, quantity=2, origin=origin, destination=dest)
    fleet.submit(order)
    env.run()
    assert order.status is OrderStatus.FAILED
    assert origin.get_inventory_level(sku) == 5
    tail = [e for e in replay.events if isinstance(e, (AgvLoadChanged, OrderStatusChanged))][-2:]
    assert [(e.type_name, getattr(e, "load", None), getattr(e, "reason", None)) for e in tail] == [
        ("agv.load_changed", None, None),
        ("order.status_changed", None, "travel_failed"),
    ]


def test_a_staged_reroute_is_executed() -> None:
    policy = Scripted()
    w = world(diamond, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    policy.blocked = (Reroute(frozenset({w.nodes["P"]})),)
    w.go("A", "E")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert [(e.reason, e.nodes) for e in w.of(AgvRouted)] == [("travel", ("S", "P", "E")), ("blocked", ("S", "Q", "E"))]
    assert [e.reason for e in w.of(TrafficWaitEnded)] == ["rerouted"]
    (wait,) = policy.waits
    assert (wait.route, wait.loaded, wait.resolutions) == ((w.nodes["S"], w.nodes["P"], w.nodes["E"]), False, 0)


def test_a_staged_step_aside_is_executed_and_the_agv_still_arrives() -> None:
    w = world(t_layout, {"A": "C1", "B": "STORE_OUT"}, policy=Scripted(deadlock=("A", StepAside())))
    w.go("B", "LINE_IN")
    w.go("A", "STORE_OUT")
    w.env.run()
    assert w.outcomes == {"B": TravelOutcome.ARRIVED, "A": TravelOutcome.ARRIVED}
    assert [(e.reason, e.destination) for e in w.of(AgvRouted, agv="A")] == [
        ("travel", "STORE_OUT"),
        ("step_aside", "PARK"),
        ("travel", "STORE_OUT"),
    ]
    (deadlock,) = w.of(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), "A", "step_aside")


def test_no_route_back_after_a_step_aside() -> None:
    nodes = {name: Node(name, x, y) for name, x, y in [("C1", 10, 0), ("S", 0, 0), ("D", 10, 10), ("T", 20, 0)]}
    graph = LayoutGraph(
        nodes.values(),
        [Arc(nodes["S"], nodes["C1"]), Arc(nodes["C1"], nodes["T"]), Arc(nodes["C1"], nodes["D"], bidirectional=False)],
    )
    policy = Scripted()
    w = world(
        lambda env: declare(env, graph),
        {"A": "C1", "B": "S"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
    )
    policy.deadlock = ("A", StepAside(w.nodes["D"]))
    w.go("B", "T")
    w.go("A", "S")
    w.env.run()
    assert w.outcomes == {"B": TravelOutcome.ARRIVED, "A": TravelOutcome.NO_ROUTE}
    assert w.agvs["A"].current_node == w.nodes["D"]


def test_a_give_up_directive_ends_the_travel() -> None:
    class GivingUp(FreeFlowController):
        def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
            event = simpy.Event(agv.env)
            event.succeed(GaveUp())
            return ClaimRequest(agv, claim, hooks, event)

    w = world(lambda env: line(env, 2), {"A": "N0"}, reserve=False)
    w.fleet.traffic_controller = GivingUp()
    w.fleet.traffic_controller.register_fleet(w.fleet)
    w.go("A", "N1")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.GAVE_UP}
    assert w.agvs["A"].current_node == w.nodes["N0"]


class BadRouter:
    def __init__(self, nodes: tuple[Node, ...]) -> None:
        self.nodes = nodes

    def route(self, request: RouteRequest, view: TrafficView) -> Route:
        return Route(self.nodes, 1.0)


@pytest.mark.parametrize(
    ("ids", "problem"),
    [
        (("N1", "N2"), "it does not start at N0"),
        (("N0", "N1"), "it does not end at N2"),
        (("N0", "N1", "N0", "N1", "N2"), "it visits a node twice"),
        (("N0", "N2"), "there is no arc from N0 to N2"),
        ((), "it does not start at N0"),
    ],
)
def test_an_invalid_route_raises(ids: tuple[str, ...], problem: str) -> None:
    router = BadRouter(())
    w = world(lambda env: line(env, 3), {"A": "N0"}, reserve=False, router=router)
    router.nodes = tuple(w.nodes[i] for i in ids)
    w.go("A", "N2")
    with pytest.raises(ValueError, match=f"BadRouter returned an invalid route from N0 to N2: {problem}"):
        w.env.run()


@pytest.mark.parametrize(("copied", "problem"), [(("N1",), "N1"), (("N0", "N1", "N2"), "N0"), (("N2",), "N2")])
def test_a_route_of_nodes_equal_to_the_graphs_but_not_its_own_raises_when_it_is_returned(
    copied: tuple[str, ...], problem: str
) -> None:
    """A router that returns copies of the graph's nodes is refused when the route is returned, before any move, with
    the error naming the router; the run would otherwise fail at the first arrival, with the move started."""
    router = BadRouter(())
    w = world(lambda env: line(env, 3), {"A": "N0"}, reserve=False, router=router)
    nodes = (w.nodes[i] for i in ("N0", "N1", "N2"))
    router.nodes = tuple(Node(node.id, node.x, node.y) if node.id in copied else node for node in nodes)
    w.go("A", "N2")
    message = f"BadRouter returned an invalid route from N0 to N2: its node {problem} is not the node of that id in "
    with pytest.raises(ValueError, match=re.escape(message)):
        w.env.run()
    assert w.agvs["A"].current_node is w.nodes["N0"] and w.agvs["A"].motion is None


def test_a_route_of_one_node_is_the_agvs_node_itself() -> None:
    from simulatte.intralogistics.travel import _check_route

    w = world(lambda env: line(env, 3), {"A": "N0"}, reserve=False)
    w.env.activate()
    origin = w.nodes["N0"]
    _check_route(w.fleet, Route((origin,), 0.0), origin, origin)
    message = "returned an invalid route from N0 to N0: its node N0 is not the node of that id in fleet.graph"
    with pytest.raises(ValueError, match=re.escape(message)):
        _check_route(w.fleet, Route((Node("N0", origin.x, origin.y),), 0.0), origin, origin)


# --- hooks -------------------------------------------------------------------------------------------------------


def test_hooks_of_a_waiting_travel() -> None:
    w = world(diamond, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=Scripted())
    w.go("A", "E")
    w.env.run()
    n = w.nodes
    hooks = w.hooks("A")
    assert hooks.route() == (n["S"], n["P"], n["E"])
    assert hooks.loaded() is False
    assert hooks.give_up() is True  # a travel with no order and no cargo may give up (spec §8.2)
    assert hooks.try_reroute(frozenset({n["P"], n["Q"]}), None) is False  # no route
    assert hooks.try_reroute(frozenset(), None) is False  # the first claim is not admissible
    assert hooks.try_reroute(frozenset({n["P"]}), 1.0) is False  # too long: 28.3 > 20
    assert hooks.try_step_aside(n["Z"]) is False  # unreachable
    assert hooks.try_step_aside(n["P"]) is False  # not admissible
    assert hooks.try_step_aside(n["S"]) is False  # already there
    assert hooks.resolutions() == 0
    assert hooks.try_reroute(frozenset({n["P"]}), 2.0) is True
    assert hooks.try_step_aside(None) is True  # K, the standard target
    assert hooks.try_step_aside(n["K"]) is True
    assert hooks.resolutions() == 3


def test_step_aside_without_a_standard_target() -> None:
    w = world(lambda env: line(env, 2), {"A": "N0", "B": "N1"}, policy=Scripted())
    w.go("A", "N1")
    w.env.run()
    assert w.hooks("A").try_step_aside(None) is False


def test_resolutions_count_until_arrival_at_the_goal() -> None:
    policy = Scripted()
    w = world(diamond, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    policy.blocked = (Reroute(frozenset({w.nodes["P"]})),)
    executor = w.fleet._executor
    w.go("A", "E")
    w.env.run()
    assert executor._resolutions == {}
    assert w.of(AgvRouted)[-1].reason == "blocked"


# --- standard_target ---------------------------------------------------------------------------------------------


def star(capacity_p: int = 1) -> tuple[World, dict[str, Node]]:
    """``M`` at ``X``; ``C`` at ``R`` waits to go ``R, X, P``. ``Q`` and ``T`` are 10 m from ``X`` as ``P`` is, ``R2``
    lies behind ``R``, ``FAR`` 20 m away and ``Z`` is isolated."""
    w = world(
        lambda env: build(
            env,
            [
                ("X", 0, 0),
                ("P", 10, 0),
                ("Q", 0, 10),
                ("T", 0, -10),
                ("R", -10, 0),
                ("R2", -20, 0),
                ("FAR", 20, 0),
                ("Z", 99, 99),
            ],
            [("X", "P"), ("X", "Q"), ("X", "T"), ("X", "R"), ("R", "R2"), ("P", "FAR")],
            capacities={"P": capacity_p},
        ),
        {"M": "X", "C": "R"},
        derive_corridors=False,
        hold_back_dead_ends=False,
    )
    nodes = w.nodes
    route = (nodes["R"], nodes["X"], nodes["P"])
    c = w.agvs["C"]
    w.controller.request(c, w.controller.next_claim(c, route, 0), fake_hooks(route))
    return w, nodes


def test_standard_target_is_the_nearest_free_node_off_the_concerned_routes() -> None:
    w, nodes = star()
    m, c = w.agvs["M"], w.agvs["C"]
    assert standard_target(w.fleet, m, concerned=(c,)) == nodes["Q"]  # P is ahead of C; Q and T tie, Q by id
    assert standard_target(w.fleet, m) == nodes["P"]
    assert standard_target(w.fleet, m, concerned=(c,), idle=True) == nodes["Q"]


@pytest.mark.parametrize(("capacity", "expected"), [(1, "Q"), (2, "P")])
def test_a_node_ahead_on_a_concerned_route_needs_a_spare_slot(capacity: int, expected: str) -> None:
    w, nodes = star(capacity)
    # P, Q and T are all 10 m away: P wins on id when it qualifies.
    assert standard_target(w.fleet, w.agvs["M"], concerned=(w.agvs["C"],)) == nodes[expected]


def test_standard_target_none_when_nothing_qualifies() -> None:
    w = world(lambda env: line(env, 2), {"A": "N0", "B": "N1"})
    assert standard_target(w.fleet, w.agvs["A"]) is None


def test_standard_target_checks_the_router() -> None:
    router = BadRouter(())
    w = world(lambda env: line(env, 2), {"A": "N0"}, reserve=False, router=router)
    router.nodes = (w.nodes["N0"],)
    with pytest.raises(ValueError, match="does not end at N1"):
        standard_target(w.fleet, w.agvs["A"])


# --- coordinator ---------------------------------------------------------------------------------------------------


def test_coordinator_defaults() -> None:
    env = Environment()
    declared = line(env, 2)
    agv = AGV(env=env, agv_type=vehicle(length=None), initial_node=declared.ref("N0"))
    fleet = FleetCoordinator(env=env, graph=declared.graph, fleet=[agv], warehouses=[], charging_stations=[])
    graph = declared.canonical_graph()  # prepares the layout
    assert isinstance(fleet.traffic_controller, FreeFlowController)
    assert isinstance(fleet.router, ShortestDistanceRouter)
    assert fleet.traffic_controller.view.graph is graph  # the fleet registered with its controller
    assert isinstance(fleet._executor, TravelExecutor)
    assert fleet.clear_service_nodes is True


def test_coordinator_registers_with_a_reservation_controller() -> None:
    env = Environment()
    declared = line(env, 2)
    controller = ReservationController(env=env, graph=declared.graph)
    agv = AGV(env=env, agv_type=vehicle(length=None), initial_node=declared.ref("N0"))
    with pytest.raises(ValueError, match="has no length"):
        FleetCoordinator(
            env=env,
            graph=declared.graph,
            fleet=[agv],
            warehouses=[],
            charging_stations=[],
            traffic_controller=controller,
        )


def test_initial_placement_goes_through_the_controller() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0", "B": "N2"})
    assert w.held("A") == ["N0"] and w.held("B") == ["N2"]
    assert math.isclose(w.agvs["A"].battery.level, 1000.0)


# --- fix round 1 ---------------------------------------------------------------------------------------------------


def stalled() -> TrapezoidalProfile:
    """A profile whose load factor is 0: every arc has an infinite travel time."""
    return TrapezoidalProfile(2.0, 1.0, 1.0, load_speed_factor_fn=lambda weight: 0.0)


def test_a_stalled_arc_is_never_left_and_an_interrupt_takes_effect_at_once() -> None:
    w = world(
        lambda env: build(
            env,
            [("K", -10, 0), ("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0)],
            [("K", "N0"), ("N0", "N1"), ("N1", "N2")],
        ),
        {"A": "N0", "B": "K"},
        derive_corridors=False,
        hold_back_dead_ends=False,
    )
    a = w.agvs["A"]
    a.agv_type = vehicle(profile=stalled())  # B keeps the moving type
    process = w.go("A", "N2")
    w.go("B", "N0", at=10.0)
    w.interrupt(process, 5.0)
    w.env.run(until=200.0)

    assert w.caught == {"A": "cancelled"}
    assert a.current_node == w.nodes["N0"] and a.motion is None and a.battery.level == 1000.0
    assert w.held("A") == ["N0"]  # the claim ahead was dropped
    released = w.of(TrafficReleased, agv="A")
    assert [(e.t, e.resources) for e in released] == [(5.0, ("N0~N1", "N1"))]
    tail = [
        (e.type_name, e.t) for e in w.events if getattr(e, "agv", None) == "A" and e.type_name != "agv.state_changed"
    ]
    assert tail[-3:] == [("agv.move_ended", 5.0), ("traffic.released", 5.0), ("agv.move_interrupted", 5.0)]
    (ended,) = w.of(AgvMoveEnded, agv="A")
    assert ended.node == "N0" and ended.deltas.ops == (("set", "A", "motion", None),)
    # B is not granted N0 while A stands there, and still is not: A never left.
    assert w.agvs["B"].current_node == w.nodes["K"]
    assert w.env.entities.node_binding(w.nodes["N0"]).agvs == ["A"]


def test_a_trip_stopped_before_departing_keeps_what_the_body_still_overlaps() -> None:
    """A 12 m vehicle reverses out of the leaf N2 it reached from N1. Its claim back over ``N1~N2`` and ``N1`` lifts
    them out of its trail; when the trip comes to rest without departing onto them (the reversal stalls and is
    interrupted), they go back into the trail, so ``retain`` keeps what the body still stands on."""
    w = world(lambda env: line(env, 3), {"A": "N1"}, agv_type=vehicle(length=12.0))
    w.go("A", "N2")
    w.env.run()
    assert w.held("A") == ["N1", "N1~N2", "N2"]  # N1 clears 12 m after departure, N1~N2 at 22
    a = w.agvs["A"]
    a.agv_type = vehicle(length=12.0, profile=stalled())
    process = w.go("A", "N0", at=50.0)
    w.interrupt(process, 60.0)
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    assert a.current_node == w.nodes["N2"]
    assert sorted(w.held("A")) == ["N1", "N1~N2", "N2"]
    assert sorted(resource.id for resource in w.fleet._executor._trail(a).entries) == ["N1", "N1~N2"]


def test_a_stalled_arc_interrupted_under_free_flow() -> None:
    w = world(lambda env: line(env, 3), {"A": "N0"}, reserve=False, agv_type=vehicle(profile=stalled()))
    process = w.go("A", "N2")
    w.interrupt(process, 5.0, "breakdown")
    w.env.run()
    assert w.env.now == 5.0  # no timer at infinity is left behind
    assert w.caught == {"A": "breakdown"}
    assert w.agvs["A"].current_node == w.nodes["N0"] and w.agvs["A"].motion is None
    names = [(e.type_name, e.t) for e in w.events if e.type_name != "agv.state_changed"]
    assert names == [
        ("agv.routed", 0.0),
        ("agv.move_started", 0.0),
        ("agv.move_ended", 5.0),
        ("agv.move_interrupted", 5.0),
    ]


def leaf_line(env: Environment) -> Declared:
    """A leaf ``L`` of capacity 2 at the end of the line ``L, M, N``."""
    return build(env, [("L", 0, 0), ("M", 10, 0), ("N", 20, 0)], [("L", "M"), ("M", "N")], capacities={"L": 2})


def assert_flowing(w: World) -> None:
    assert not w.of(TrafficDeadlock) and not w.of(TrafficWaitStuck)
    assert all(outcome is TravelOutcome.ARRIVED for outcome in w.outcomes.values())


@pytest.mark.parametrize("length", [0.0, 1.0])
def test_a_head_on_meeting_at_a_passing_place_completes_with_a_length(length: float) -> None:
    """A from R to Z, B from Z to R, passing at P (capacity 2). Q and Y lead into dead ends, so each waits at the
    entry for the whole rest of its route: the deadlock is resolved by stepping A aside to P, with a length as
    without one. Before the trail cleared on P, A kept ``P~Q`` there and the second deadlock stood unresolved."""
    w = world(
        lambda env: build(
            env,
            [("R", 0, 0), ("Q", 10, 0), ("P", 20, 0), ("Y", 30, 0), ("Z", 40, 0)],
            [("R", "Q"), ("Q", "P"), ("P", "Y"), ("Y", "Z")],
            capacities={"P": 2},
        ),
        {"A": "R", "B": "Z"},
        agv_type=vehicle(length=length),
    )
    w.go("A", "Z")
    w.go("B", "R")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert [(e.victim, e.action) for e in w.of(TrafficDeadlock)] == [("A", "step_aside")]
    assert not w.of(TrafficWaitStuck)


def test_two_vehicles_at_a_multi_slot_leaf_one_sent_away() -> None:
    w = world(leaf_line, {"A": "M", "B": "N"}, agv_type=vehicle(length=1.0))
    w.go("A", "L")
    w.go("B", "L", at=30.0)
    w.env.run()
    assert w.agvs["A"].current_node == w.agvs["B"].current_node == w.nodes["L"]
    assert w.held("A") == ["L"] and w.held("B") == ["L"]  # resting on L holds the vehicles wholly
    w.go("A", "N", at=100.0)
    w.env.run()
    assert w.agvs["A"].current_node == w.nodes["N"]
    assert_flowing(w)


def test_one_vehicle_enters_a_multi_slot_leaf_while_the_other_leaves() -> None:
    w = world(leaf_line, {"A": "M", "B": "N"}, agv_type=vehicle(length=1.0))
    w.go("A", "L")
    w.env.run()
    w.go("B", "L", at=50.0)
    w.go("A", "N", at=50.0)
    w.env.run()
    assert (w.agvs["A"].current_node, w.agvs["B"].current_node) == (w.nodes["N"], w.nodes["L"])
    assert_flowing(w)


def test_arriving_at_a_single_slot_node_keeps_the_trail() -> None:
    w = world(lambda env: line(env, 3, capacities={"N0": 2}), {"A": "N0"}, agv_type=vehicle(length=2.0))
    w.go("A", "N1")
    w.env.run()
    assert w.held("A") == ["N0~N1", "N1"]  # N1 has capacity 1: the body still overlaps the segment


def test_withdraw_is_called_without_occupancy_control() -> None:
    calls: list[str] = []

    class Deferring(FreeFlowController):
        """Controls no occupancy but grants nothing at once."""

        def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
            return ClaimRequest(agv, claim, hooks, simpy.Event(agv.env))

        def stopped(self, request: ClaimRequest) -> None:
            calls.append("stopped")

        def withdraw(self, request: ClaimRequest) -> None:
            calls.append("withdraw")

    w = world(lambda env: line(env, 2), {"A": "N0"}, reserve=False)
    controller = Deferring()
    controller.register_fleet(w.fleet)
    w.fleet.traffic_controller = controller
    process = w.go("A", "N1")
    w.interrupt(process, 3.0)
    w.env.run()
    assert calls == ["stopped", "withdraw"]
    assert w.caught == {"A": "cancelled"}


def test_an_interrupt_at_the_instant_of_a_grant_drops_the_grant() -> None:
    w = world(lambda env: line(env, 3), {"W": "N0", "H": "N1"})
    process = w.go("W", "N2")
    w.go("H", "N2", at=1.0)  # H leaves N1 at 1: W is granted N1 ...
    w.interrupt(process, 1.0)  # ... and interrupted before its travel resumes
    w.env.run()
    assert w.caught == {"W": "cancelled"}
    assert [(e.type_name, e.t, e.resources) for e in w.of(TrafficReserved, TrafficReleased, agv="W")][-2:] == [
        ("traffic.reserved", 1.0, ("N0~N1", "N1")),
        ("traffic.released", 1.0, ("N0~N1", "N1")),
    ]
    assert w.held("W") == ["N0"] and not w.controller._pending
    (interrupted,) = w.of(AgvMoveInterrupted)
    assert (interrupted.t, interrupted.node) == (1.0, "N0")


def test_an_interrupt_during_a_multi_leg_claim_drops_the_legs_ahead() -> None:
    """The battery affords one arc, so the run ends at rest at C1 inside the dead-end claim; there the interrupt
    takes effect and the legs ahead go."""
    w = world(t_layout, {"A": "STORE_OUT"}, agv_type=vehicle(battery=15.0))
    process = w.go("A", "LINE_IN")
    w.interrupt(process, 3.0)
    w.env.run()
    (reserved,) = w.of(TrafficReserved, agv="A")  # the dead-end claim covers the whole route
    assert reserved.resources == ("C1~STORE_OUT", "C1", "C1~C2", "C2", "C2~LINE_IN", "LINE_IN")
    assert [e.resources for e in w.of(TrafficReleased, agv="A")][-1] == ("C1~C2", "C2", "C2~LINE_IN", "LINE_IN")
    assert w.held("A") == ["C1"]


def test_a_staged_reroute_of_a_deadlock_is_executed() -> None:
    policy = Scripted()
    w = world(
        lambda env: build(env, [("A0", 0, 0), ("A1", 10, 0), ("X", 5, 5)], [("A0", "A1"), ("A0", "X"), ("X", "A1")]),
        {"A": "A0", "B": "A1"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
    )
    policy.deadlock = ("A", Reroute(frozenset({w.controller.network.segment(w.nodes["A0"], w.nodes["A1"])})))
    w.go("A", "A1")
    w.go("B", "A0")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert [(e.reason, e.nodes) for e in w.of(AgvRouted, agv="A")] == [
        ("travel", ("A0", "A1")),
        ("deadlock", ("A0", "X", "A1")),
    ]


class ThroughTheGoal(ShortestDistanceRouter):
    """Routes the step aside from `S` to `T` along ``S, Y, G, T``, whatever the shortest route says."""

    def __init__(self, nodes: dict[str, Node]) -> None:
        super().__init__()
        self.nodes = nodes

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        n = self.nodes
        if request.origin == n["S"] and request.destination == n["T"]:
            return Route((n["S"], n["Y"], n["G"], n["T"]), 30.0)
        return super().route(request, view)


def test_a_reroute_asked_of_an_agv_on_its_goal_ends_the_travel_as_arrived() -> None:
    """A step aside from ``S`` to ``T`` passes through the goal ``G``; at ``G`` the way on is blocked by C and the
    policy asks for a reroute. The AGV has arrived (spec §7.9): the reroute succeeds with the one-node route and the
    travel ends, holding only the node it stands on."""
    policy = Scripted()
    router = ThroughTheGoal({})
    w = world(
        lambda env: build(
            env,
            [("S", 0, 0), ("X", 10, 8), ("Y", 10, -9), ("G", 20, 0), ("T", 30, 0)],
            [("S", "X"), ("X", "G"), ("S", "Y"), ("Y", "G"), ("G", "T")],
        ),
        {"A": "S", "B": "X", "C": "T"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
        router=router,
    )
    n = router.nodes = w.nodes
    policy.blocked = (StepAside(n["T"]), Reroute(frozenset({n["T"]}), 0.5))
    w.go("A", "G")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert w.agvs["A"].current_node == n["G"]
    assert [(e.reason, e.nodes, e.cost) for e in w.of(AgvRouted, agv="A")] == [
        ("travel", ("S", "X", "G"), pytest.approx(w.controller.view.graph.path_length((n["S"], n["X"], n["G"])))),
        ("step_aside", ("S", "Y", "G", "T"), 30.0),
        ("blocked", ("G",), 0.0),  # no admissibility or cost test for a route without legs
    ]
    assert [e.reason for e in w.of(TrafficWaitEnded, agv="A")] == ["stepped_aside", "rerouted"]
    assert w.held("A") == ["G"]
    assert w.controller.view.holders(n["G"]) == (w.agvs["A"],)
    assert not w.fleet._executor._resolutions  # the count of the goal is dropped, as on any arrival


# --- a segment claimed again in a new direction (spec §6.6, §7.2 "Both directions") -------------------------------


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


def turn_at_a_bend(angle: float) -> float:
    """4 s to turn through a bend; nothing for a straight on or a reversal."""
    return 4.0 if 0.5 < angle < 2.5 else 0.0


def turn_back(angle: float) -> float:
    """4 s to turn back; nothing otherwise."""
    return 4.0 if angle > 2.5 else 0.0


def short_line(env: Environment, vw: float, **network: Any) -> Declared:
    """``U - V - W`` on a line, ``U~V`` 10 m and ``V~W`` `vw` m; ``U`` and ``V`` have capacity 2."""
    return build(
        env,
        [("U", 0, 0), ("V", 10, 0), ("W", 10 + vw, 0)],
        [("U", "V"), ("V", "W")],
        capacities={"U": 2, "V": 2},
        **network,
    )


def ends(w: World) -> list[tuple[float, str, tuple[str, ...]]]:
    """Wrap the controller's ``cleared`` to record, for each call that ended a both-directions state, the time, the
    AGV and the segments whose state ended."""
    found: list[tuple[float, str, tuple[str, ...]]] = []
    controller = w.controller
    ledger = controller.ledger
    cleared = controller.cleared

    def recorded(agv: AGV, resources: Any) -> None:
        ending = tuple(sorted(r.id for r in resources if agv in ledger.both_directions(r)))
        cleared(agv, resources)
        if ending:
            found.append((float(w.env.now), agv.id, ending))

    controller.cleared = recorded
    return found


def traffic_at(w: World, t: float) -> list[tuple[str, str]]:
    """The traffic events at time `t`: type and AGV."""
    return [(e.type_name, e.agv) for e in w.events if e.t == t and e.type_name.startswith("traffic.")]


def back_at_50(vw: float, **network: Any) -> World:
    """Scenario ``base`` of audit (ii) with ``V~W`` `vw` m: A drives from U to W and rests there, with ``13 - (10 +
    vw)`` m of its body on ``U~V``; B rests on V. At 50 A travels back to U and B asks for ``V -> U``."""
    w = world(
        lambda env: short_line(env, vw, **network), {"A": "U", "B": "V"}, agv_type=reverser(), hold_back_dead_ends=False
    )
    w.go("A", "W")
    w.env.run(until=49.0)
    assert {"U~V", "V", "V~W", "W"} <= set(w.held("A"))
    w.go("A", "U", at=50.0)
    w.go("B", "U", at=50.0)
    return w


def test_a_vehicle_waits_while_a_reversing_body_still_lies_on_the_segment_and_goes_when_it_has_left() -> None:
    """Scenario ``base``: at rest on W, A claims ``U~V`` again, backward, with 0.5 m of its body still on it as it
    arrived. Without the state B was granted ``U~V`` at once and the two bodies lay head-on on it until 51.1. B waits,
    blocked by A, and is granted at the instant A's odometer reaches the old clear point of ``U~V``, 0.5 m into the arc
    ``W -> V``: no release and no other traffic event at that instant."""
    w = back_at_50(2.5)
    ended = ends(w)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.resources, started.blockers) == (50.0, ("U~V", "U"), ("A",))
    (granted,) = w.of(TrafficWaitEnded, agv="B")
    assert granted.reason == "granted"
    assert granted.t == pytest.approx(50.0 + math.sqrt(1.25), rel=1e-12)  # 0.5 m from rest at 0.8 m/s²
    assert traffic_at(w, granted.t) == [("traffic.wait_ended", "B"), ("traffic.reserved", "B")]
    assert ended == [(50.0, "A", ("V~W",)), (granted.t, "A", ("U~V",))]  # V~W by A's own leg, U~V at its clear point
    assert w.of(AgvMoveStarted, agv="B")[0].t == granted.t


def test_a_zone_of_capacity_two_over_the_segment_does_not_hide_the_reversing_body() -> None:
    """Scenario ``zone2``: the same with a zone of capacity 2 over ``U~V``; the zone admits B, the segment does not."""
    w = back_at_50(2.5, zones={"Z": {"members": [["U", "V"]], "capacity": 2}})
    w.env.run()
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.resources, started.blockers) == (50.0, ("Z", "U~V", "U"), ("A",))
    (granted,) = w.of(TrafficWaitEnded, agv="B")
    assert granted.t == pytest.approx(50.0 + math.sqrt(1.25), rel=1e-12)
    assert traffic_at(w, granted.t) == [("traffic.wait_ended", "B"), ("traffic.reserved", "B")]


def test_the_state_ends_when_the_holder_starts_its_own_leg_over_the_segment() -> None:
    """Scenario ``short_same``, end (a) (Ruling A3-d): ``V~W`` is 1.0 m, so the old clear point of ``U~V`` lies 2.0 m
    beyond V. A passes V at speed and starts its own leg ``V -> U`` 1.0 m after departing: from that instant its body
    on ``U~V`` is taken in the new direction, and B is granted then, behind it."""
    w = back_at_50(1.0)
    ended = ends(w)
    w.env.run()
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.blockers) == (50.0, ("A",))
    leg = next(e for e in w.of(AgvMoveStarted, agv="A") if e.from_node == "V" and e.t > 50.0)
    assert leg.t == pytest.approx(50.0 + math.sqrt(2.5), rel=1e-12)  # 1.0 m from rest at 0.8 m/s²
    (granted,) = w.of(TrafficWaitEnded, agv="B")
    assert granted.t == leg.t
    assert traffic_at(w, leg.t) == [("traffic.wait_ended", "B"), ("traffic.reserved", "B")]
    assert ended == [(50.0, "A", ("V~W",)), (leg.t, "A", ("U~V",))]


def test_the_state_ends_when_the_holder_comes_to_rest_on_a_node_with_room() -> None:
    """Scenario ``roomy_stop``, end (c): A drives ``U -> V -> W`` without stopping on V and rests on W, 1.0 m off V at
    a right angle, with 2.0 m of its body on ``U~V``. Back from W at 50 it claims ``U~V`` again and must stop on V to
    turn: at rest there, on a node of capacity 2, its whole trail clears and the body as it arrived lies on nothing.
    B, asking for ``V -> U`` during the turn, is admitted at once, as without the state."""
    w = world(
        lambda env: build(
            env, [("U", 0, 0), ("V", 10, 0), ("W", 10, 1.0)], [("U", "V"), ("V", "W")], capacities={"U": 2, "V": 2}
        ),
        {"A": "U", "B": "V"},
        agv_type=reverser(),
        hold_back_dead_ends=False,
    )
    w.go("A", "W")
    w.env.run(until=49.0)
    w.agvs["A"].agv_type = reverser(turn=turn_at_a_bend)  # it turns on the way back only
    ended = ends(w)
    w.go("A", "U", at=50.0)
    w.go("B", "U", at=53.0)
    segment = w.controller.network.segment(w.nodes["U"], w.nodes["V"])
    w.env.run(until=52.0)
    assert w.controller.ledger.both_directions(segment) == (w.agvs["A"],)
    w.env.run()
    rest = w.of(AgvMoveEnded, agv="A")[-2]  # on V, where A turns
    assert (rest.node, rest.t) == ("V", pytest.approx(52.7386, abs=1e-4))
    assert ended == [(50.0, "A", ("V~W",)), (rest.t, "A", ("U~V",))]
    assert not w.of(TrafficWaitStarted, agv="B")
    assert [(e.t, e.resources) for e in w.of(TrafficReserved, agv="B")] == [(53.0, ("U~V", "U"))]


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


def test_a_reversal_staged_before_a_turn_holds_the_state_through_the_turn() -> None:
    """Scenario ``turn_staged``: A, blocked on W by C, is sent aside to U. The new route starts with a turn back of
    4 s, and its claims are asked for before the turn with the look-ahead of the run before, so ``U~V`` is claimed
    again at 10.0 while 0.5 m of A's body lies on it. B, asking for ``V -> U`` at 10.5, waits through the turn and is
    granted 0.5 m into A's arc ``W -> V``, which starts at 14.0."""
    policy = AsideOnce()
    w = world(
        lambda env: build(
            env,
            [("U", 0, 0), ("V", 10, 0), ("W", 12.5, 0), ("Y", 22.5, 0)],
            [("U", "V"), ("V", "W"), ("W", "Y")],
            capacities={"U": 2, "V": 2},
        ),
        {"A": "U", "B": "V", "C": "Y"},
        agv_type=reverser(turn=turn_back),
        hold_back_dead_ends=False,
        policy=policy,
    )
    w.go("A", "Y")
    w.go("B", "U", at=10.5)
    w.env.run()
    assert [(e.t, e.reason) for e in w.of(AgvRouted, agv="A")][:2] == [(0.0, "travel"), (10.0, "step_aside")]
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.blockers) == (10.5, ("A",))
    assert w.of(AgvMoveStarted, agv="A")[2].t == 14.0
    (granted,) = w.of(TrafficWaitEnded, agv="B")
    assert granted.t == pytest.approx(14.0 + math.sqrt(1.25), rel=1e-12)
    assert traffic_at(w, granted.t) == [("traffic.wait_ended", "B"), ("traffic.reserved", "B")]


def stop_line(env: Environment, far: str) -> Declared:
    """``... - b - R - X - W``: R has capacity 2, ``R~X`` is 1.0 m and ``X~W`` 0.6 m at a right angle. With `far`
    ``"corridor"`` the corridor ``J1 - a - b - R`` (ports J1 and R) lies beyond R, with x1 and y1 off J1; with
    ``"branch"`` the dead-end branch ``b, L`` with root R."""
    points = [("b", 20, 0), ("R", 30, 0), ("X", 31, 0), ("W", 31, 0.6)]
    links = [("b", "R"), ("R", "X"), ("X", "W")]
    if far == "corridor":
        points += [("J1", 0, 0), ("a", 10, 0), ("x1", -10, 10), ("y1", -10, -10)]
        links += [("J1", "a"), ("a", "b"), ("J1", "x1"), ("J1", "y1")]
    else:
        points += [("L", 10, 0)]
        links += [("L", "b")]
    return build(env, points, links, capacities={"R": 2})


@pytest.mark.parametrize(
    ("far", "start", "goal", "resources"),
    [
        ("corridor", "x1", "y1", ("corridor:a", "R~b", "b")),
        ("branch", "L", "L", ("R~b", "b", "L~b", "L")),
    ],
)
def test_a_reversal_that_outlives_a_stop_keeps_the_segment_closed(
    far: str, start: str, goal: str, resources: tuple[str, ...]
) -> None:
    """Scenarios ``corr_stop`` and ``branch_stop``: A rests on W, 1.6 m beyond R, with 1.4 m of its body on ``R~b``.
    At 50 it travels back; at rest on W it claims ``R~b`` again with the far node, moves 0.6 m to X and stops there to
    turn. Its travel is interrupted during the turn: it keeps what its body overlaps, ``R~b`` among it with 0.8 m of
    the body as it arrived, recorded in the new direction, and gives the far node back. B, on R, asks at 56 for the
    way past b, which the far node no longer holds. B waits behind A, which never moves again, and its wait is reported
    stuck at its first review: nothing is admitted against A's body."""
    w = world(lambda env: stop_line(env, far), {"A": start, "B": "R"}, agv_type=reverser(turn=turn_at_a_bend))
    w.go("A", "W")
    back = w.go("A", start, at=50.0)
    w.interrupt(back, 53.0)
    w.go("B", goal, at=56.0)
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    assert w.agvs["A"].current_node == w.nodes["X"]
    segment = w.controller.network.segment(w.nodes["R"], w.nodes["b"])
    assert w.controller.ledger.direction(segment, w.agvs["A"]) == "forward"  # R -> b, the body lies b -> R
    assert w.controller.ledger.both_directions(segment) == (w.agvs["A"],)
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.resources, started.blockers) == (56.0, resources, ("A",))
    (stuck,) = w.of(TrafficWaitStuck)
    assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (86.0, "B", {"A": "IDLE"})
    assert not w.of(AgvMoveStarted, agv="B")
    assert w.env.now == 86.0


def test_an_idle_holder_in_the_state_is_asked_to_make_way() -> None:
    """``corr_stop`` with a fleet that makes way: B's wait names the idle A, which makes way along the corridor. Its
    own leg ends the state of ``R~X``; its odometer reaches the old clear point of ``R~b`` 0.8 m into the arc ``X ->
    R``; B is granted ``R~b`` when A's tail releases it."""
    w = world(
        lambda env: stop_line(env, "corridor"),
        {"A": "x1", "B": "R"},
        agv_type=reverser(turn=turn_at_a_bend),
        make_way=True,
    )
    ended = ends(w)
    w.go("A", "W")
    back = w.go("A", "x1", at=50.0)
    w.interrupt(back, 53.0)
    w.go("B", "y1", at=56.0)
    w.env.run()
    started = w.of(TrafficWaitStarted, agv="B")[0]
    assert (started.t, started.blockers) == (56.0, ("A",))
    assert [(e.t, e.reason) for e in w.of(AgvRouted, agv="A")][-1] == (56.0, "make_way")
    assert ended[-2:] == [(60.0, "A", ("R~X",)), (pytest.approx(60.0 + math.sqrt(2.0), rel=1e-12), "A", ("R~b",))]
    granted = w.of(TrafficWaitEnded, agv="B")[0]
    released = next(e for e in w.of(TrafficReleased, agv="A") if "R~b" in e.resources and e.t > 56.0)
    assert (granted.t, granted.reason) == (released.t, "granted")
    assert w.agvs["A"].current_node == w.nodes["x1"] and w.agvs["B"].current_node == w.nodes["y1"]


def test_the_state_of_a_reclaim_granted_while_moving_ends_at_the_old_clear_point_inside_the_arc() -> None:
    """End (b) for a re-claim granted while the holder moves: A leaves W at 50 with its claim back over ``U~V`` and U
    still pending, U being held by C. C leaves U at 50.5 (C has no extent, so U is released as it departs), and A is
    granted ``U~V`` while moving, 0.1 m into the arc ``W -> V``. The old clear point of ``U~V`` lies 0.5 m into that
    arc: the state ends there, at the instant the release scheduled at A's departure would have freed ``U~V``, with no
    release."""
    w = world(
        lambda env: build(
            env,
            [("Z", -10, 0), ("U", 0, 0), ("V", 10, 0), ("W", 12.5, 0)],
            [("Z", "U"), ("U", "V"), ("V", "W")],
            capacities={"V": 2},
        ),
        {"A": "U", "C": "Z"},
        agv_type=reverser(),
        hold_back_dead_ends=False,
        derive_corridors=False,
    )
    w.go("A", "W")
    w.go("C", "U", at=20.0)
    w.env.run(until=49.0)
    w.agvs["C"].agv_type = vehicle(length=0.0, profile=reverser().speed_profile)
    ended = ends(w)
    w.go("A", "U", at=50.0)
    w.go("C", "Z", at=50.5)
    w.env.run()
    granted = [e for e in w.of(TrafficReserved, agv="A") if e.resources == ("U~V", "U")]
    assert [e.t for e in granted] == [50.5]
    at = 50.0 + math.sqrt(1.25)
    ended = [end for end in ended if end[1] == "A"]  # C, back from U, claimed Z~U again
    assert ended == [
        (50.0, "A", ("V~W",)),
        (pytest.approx(at, rel=1e-12), "A", ("U~V",)),
    ]
    assert not [e for e in w.events if e.t == ended[1][0] and e.type_name.startswith("traffic.")]
    assert w.outcomes["A"] is TravelOutcome.ARRIVED


def test_without_kinematic_limits_the_state_ends_at_the_arrival_that_ends_the_arc() -> None:
    """End (b) for a profile without limits: its clear points inside an arc are released at the next arrival (spec
    §6.6). V is the root of the branches U and W, so A's claim back from W covers both legs and claims ``U~V`` again
    at rest; its old clear point lies 0.5 m into the arc ``W -> V`` of 2.5 m, and the state ends at the arrival on V,
    1.25 s after departure."""
    w = world(
        lambda env: build(
            env, [("U", 0, 0), ("V", 10, 0), ("W", 12.5, 0), ("X", 10, 10)], [("U", "V"), ("V", "W"), ("V", "X")]
        ),
        {"A": "U"},
        agv_type=vehicle(length=3.0, profile=Plain()),
    )
    w.go("A", "W")
    w.env.run(until=49.0)
    assert w.held("A") == ["U~V", "V", "V~W", "W"]
    ended = ends(w)
    w.go("A", "U", at=50.0)
    w.env.run()
    assert [e.resources for e in w.of(TrafficReserved, agv="A")][-1] == ("V~W", "U~V", "U")
    assert ended == [(50.0, "A", ("V~W",)), (51.25, "A", ("U~V",))]
    assert [e.t for e in w.of(AgvMoveEnded, agv="A")][-2] == 51.25


def test_no_timer_of_the_state_outlives_the_arc_or_the_travel() -> None:
    """The old clear point inside an arc is timed with the arc's own releases, inside the arc. An interrupt received
    during the arc takes effect where the AGV comes to rest, after the arc, so the state ends at the same instant as
    without it and the run returns at the last arrival. A travel that comes to rest before the old clear point (an
    interrupt during a turn) or on a stalled arc schedules nothing for it: the segment stays held in the new direction
    with the state, and the run returns at the rest."""
    w = world(lambda env: short_line(env, 2.5), {"A": "U"}, agv_type=reverser(), hold_back_dead_ends=False)
    w.go("A", "W")
    w.env.run(until=49.0)
    ended = ends(w)
    back = w.go("A", "U", at=50.0)
    w.interrupt(back, 50.5 - w.env.now)
    w.env.run()
    assert w.caught == {"A": "cancelled"}
    assert ended == [(50.0, "A", ("V~W",)), (pytest.approx(50.0 + math.sqrt(1.25), rel=1e-12), "A", ("U~V",))]
    assert w.env.now == w.of(AgvMoveEnded, agv="A")[-1].t

    w = world(lambda env: stop_line(env, "corridor"), {"A": "x1"}, agv_type=reverser(turn=turn_at_a_bend))
    w.go("A", "W")
    back = w.go("A", "x1", at=50.0)
    w.interrupt(back, 53.0)
    w.env.run()
    assert w.events[-1].t == 53.0  # (the run ends at 56.12, when the timeout of the interrupted turn wait expires)
    segment = w.controller.network.segment(w.nodes["R"], w.nodes["b"])
    assert w.controller.ledger.both_directions(segment) == (w.agvs["A"],)

    w = world(lambda env: line(env, 3), {"A": "N1"}, agv_type=vehicle(length=12.0))
    w.go("A", "N2")
    w.env.run()
    a = w.agvs["A"]
    a.agv_type = vehicle(length=12.0, profile=stalled())
    process = w.go("A", "N0", at=50.0)
    w.interrupt(process, 60.0 - w.env.now)
    w.env.run()
    assert w.env.now == 60.0
    segment = w.controller.network.segment(w.nodes["N1"], w.nodes["N2"])
    assert w.controller.ledger.both_directions(segment) == (a,)
    assert w.controller.ledger.direction(segment, a) == "backward"


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


def claimed_again_twice() -> tuple[World, AroundOnRequest]:
    """The world of :func:`test_a_holder_granted_the_segment_again_in_the_direction_of_its_body_stays_in_the_state`,
    activated, with its travels scheduled; the router is told to go round at 19.9."""
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


def test_a_holder_granted_the_segment_again_in_the_direction_of_its_body_stays_in_the_state() -> None:
    """A segment claimed again twice (spec §7.2, Ruling A3-n). A, blocked on W by C, is sent aside to U: at 10.0 it
    claims ``U~V`` again, backward, with 0.5 m of its body on it, and is interrupted in its turn at 12.0, keeping the
    segment in the state. At 20.0 it travels to V round the cycle ``W, K, U, V`` and, through look-ahead, is granted
    ``U~V`` forward, the direction its body lies in: it stays in the state, with the record equal to the body, until its
    odometer reaches the old clear point 0.5 m into ``W -> K``. B, on U, asks for ``U -> V`` at 20.5: it waits behind
    A, named as its blocker, and is granted at that end."""
    w, _ = claimed_again_twice()
    ledger, a = w.controller.ledger, w.agvs["A"]
    segment = w.controller.network.segment(w.nodes["U"], w.nodes["V"])
    ended = ends(w)
    w.env.run(until=19.0)
    assert w.caught == {"A": "cancelled"} and w.agvs["A"].current_node == w.nodes["W"]
    assert (ledger.both_directions(segment), ledger.direction(segment, a)) == ((a,), "backward")
    w.env.run(until=20.2)
    granted = [e.t for e in w.of(TrafficReserved, agv="A") if e.resources == ("U~V",)]
    assert granted == [20.0]
    assert (ledger.both_directions(segment), ledger.direction(segment, a)) == ((a,), "forward")
    w.env.run()
    end = 20.0 + math.sqrt(1.25)  # 0.5 m from rest at 0.8 m/s²
    assert ended == [(pytest.approx(end, rel=1e-12), "A", ("U~V",))]
    (started,) = w.of(TrafficWaitStarted, agv="B")
    assert (started.t, started.resources, started.blockers) == (20.5, ("U~V", "V"), ("A",))
    (granted_b,) = w.of(TrafficWaitEnded, agv="B")
    assert (granted_b.t, granted_b.reason) == (ended[0][0], "granted")
    assert traffic_at(w, granted_b.t) == [("traffic.wait_ended", "B"), ("traffic.reserved", "B")]
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
