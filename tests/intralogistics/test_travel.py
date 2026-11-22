"""The travel executor (spec §6.6, §8.1-§8.4): routes, claims, the trail of released resources, interrupts, outcomes,
the hooks a waiting travel gives the controller and the standard search of a step aside.

Every world runs :class:`FullReplay` over every event; worlds with a ``ReservationController`` also run
:class:`TrafficInvariants`. Travels run in a mission-like process (:meth:`World.go`) that records the outcome, or the
cause of the interrupt that the travel raised again.
"""

from __future__ import annotations

import math
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
    fake_hooks,
    line,
    t_layout,
)
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
        return [resource.id for resource in self.controller._held.get(self.agvs[agv], {})]

    def hooks(self, agv: str) -> WaitHooks:
        return self.controller._pending[self.agvs[agv]].request.hooks


def world(
    graph_nodes: tuple[LayoutGraph, dict[str, Node]],
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
    way only with `make_way` (spec §7.10; tests of idle traffic have their own file). `options` go to the
    ``ReservationController``."""
    graph, nodes = graph_nodes
    env = Environment(debug=True)
    replay = FullReplay(env)
    controller: Any = ReservationController(env=env, graph=graph, **options) if reserve else FreeFlowController()
    kind = agv_type or vehicle()
    vehicles = {
        name: AGV(env=env, agv_type=kind, agv_id=name, initial_node=nodes[start]) for name, start in agvs.items()
    }
    fleet = (FleetCoordinator if make_way else StillFleet)(
        env=env,
        graph=graph,
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
    return World(env, fleet, nodes, vehicles, replay, len(replay.events))


def diamond() -> tuple[LayoutGraph, dict[str, Node]]:
    """``S`` to ``E`` through ``P`` (20 m) or through ``Q`` (about 28.3 m), with ``K`` hanging off ``S`` and an
    isolated node ``Z``."""
    return build(
        [("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0), ("K", -10, 0), ("Z", 50, 50)],
        [("S", "P"), ("P", "E"), ("S", "Q"), ("Q", "E"), ("S", "K")],
    )


# --- free flow ---------------------------------------------------------------------------------------------------


def test_free_flow_arrivals_and_events() -> None:
    profile = TrapezoidalProfile(1.5, 0.8, 0.8)  # slowed by the battery level, sampled when the run starts
    w = world(t_layout(), {"A": "STORE_OUT"}, reserve=False, agv_type=vehicle(profile=profile))
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
    w = world(line(3), {"A": "N0"}, reserve=False)
    w.go("A", "N1", purpose="make_way")
    w.go("A", "N2", at=50.0, purpose="clear_node")
    w.go("A", "N0", at=100.0, purpose="charge")
    w.env.run()
    assert [e.reason for e in w.of(AgvRouted)] == ["make_way", "clear_node", "travel"]


def test_goal_equal_to_current_node_arrives_without_events() -> None:
    w = world(line(2), {"A": "N0"})
    before = len(w.events)
    outcome = w.env.run(w.env.process(w.fleet._executor.travel(w.agvs["A"], w.nodes["N0"], loaded=False)))
    assert outcome is TravelOutcome.ARRIVED
    assert len(w.events) == before


# --- reservation and release -------------------------------------------------------------------------------------


def test_a_follower_waits_and_proceeds_when_the_leader_tail_clears() -> None:
    w = world(line(4), {"L": "N1", "F": "N0"}, agv_type=vehicle(length=2.0))
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
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(length=2.0))
    w.go("A", "N2")
    w.env.run()
    released = [(e.t, e.resources) for e in w.of(TrafficReleased)]
    # N0 clears 2 m into the first arc; N0~N1 and N1 both clear 2 m into the second, passed at speed, in one call.
    first, second = run_of(2)
    assert released == [(TAIL, ("N0",)), (first.duration + second.time_at(2.0), ("N0~N1", "N1"))]
    assert w.held("A") == ["N1~N2", "N2"]


def test_a_profile_with_only_travel_time_releases_at_the_next_arrival() -> None:
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(length=2.0, profile=Plain()))
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
    w = world(line(5), {"A": "N0"}, agv_type=vehicle(length=12.0))
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
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(length=12.0))
    w.go("A", "N1")
    w.go("A", "N0", at=50.0)
    w.env.run()
    assert w.outcomes["A"] is TravelOutcome.ARRIVED
    assert w.agvs["A"].current_node == w.nodes["N0"]
    assert w.held("A") == ["N0", "N0~N1", "N1"]


def test_at_the_exit_port_the_corridor_stays_and_gates_a_later_same_direction_claim() -> None:
    w = world(two_junction_corridor(), {"A": "J1", "W": "y2", "F": "x1"})
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
    w = world(line(4), {"A": "N0"})
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
    w = world(line(3), {"F": "N0", "L": "N1"})
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
    graph, nodes = build([("A", 0, 0), ("B", 10, 0)], [])
    agv = AGV(env=env, agv_type=vehicle(), agv_id="V", initial_node=nodes["A"])
    fleet = FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[])
    outcome = env.run(env.process(fleet._executor.travel(agv, nodes["B"], loaded=False)))
    assert outcome is TravelOutcome.NO_ROUTE
    (error,) = env.log_history.query(component="FleetCoordinator")
    assert error.message == "No path from A to B for V"


def test_no_route_fails_the_order_after_returning_the_cargo() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    graph, nodes = build([("ORIGIN", 0, 0), ("DEST", 10, 0)], [])
    sku = SKU("S", 1.0, 0.1)
    origin = Warehouse(
        env=env,
        name="WH-O",
        input_bays=[nodes["ORIGIN"]],
        output_bays=[nodes["ORIGIN"]],
        n_slots=1,
        products=[sku],
        initial_inventory={sku: 5},
        pick_time=1.0,
        put_time=1.0,
    )
    dest = Warehouse(
        env=env,
        name="WH-D",
        input_bays=[nodes["DEST"]],
        output_bays=[nodes["DEST"]],
        n_slots=1,
        products=[sku],
        pick_time=1.0,
        put_time=1.0,
    )
    agv = AGV(env=env, agv_type=vehicle(), agv_id="V", initial_node=nodes["ORIGIN"])
    fleet = FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[origin, dest], charging_stations=[])
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
    graph_nodes = diamond()
    policy = Scripted(blocked=(Reroute(frozenset({graph_nodes[1]["P"]})),))
    w = world(graph_nodes, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    w.go("A", "E")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert [(e.reason, e.nodes) for e in w.of(AgvRouted)] == [("travel", ("S", "P", "E")), ("blocked", ("S", "Q", "E"))]
    assert [e.reason for e in w.of(TrafficWaitEnded)] == ["rerouted"]
    (wait,) = policy.waits
    assert (wait.route, wait.loaded, wait.resolutions) == ((w.nodes["S"], w.nodes["P"], w.nodes["E"]), False, 0)


def test_a_staged_step_aside_is_executed_and_the_agv_still_arrives() -> None:
    w = world(t_layout(), {"A": "C1", "B": "STORE_OUT"}, policy=Scripted(deadlock=("A", StepAside())))
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
    policy = Scripted(deadlock=("A", StepAside(nodes["D"])))
    w = world((graph, nodes), {"A": "C1", "B": "S"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    w.go("B", "T")
    w.go("A", "S")
    w.env.run()
    assert w.outcomes == {"B": TravelOutcome.ARRIVED, "A": TravelOutcome.NO_ROUTE}
    assert w.agvs["A"].current_node == nodes["D"]


def test_a_give_up_directive_ends_the_travel() -> None:
    class GivingUp(FreeFlowController):
        def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
            event = simpy.Event(agv.env)
            event.succeed(GaveUp())
            return ClaimRequest(agv, claim, hooks, event)

    w = world(line(2), {"A": "N0"}, reserve=False)
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
    graph, nodes = line(3)
    w = world((graph, nodes), {"A": "N0"}, reserve=False, router=BadRouter(tuple(nodes[i] for i in ids)))
    w.go("A", "N2")
    with pytest.raises(ValueError, match=f"BadRouter returned an invalid route from N0 to N2: {problem}"):
        w.env.run()


# --- hooks -------------------------------------------------------------------------------------------------------


def test_hooks_of_a_waiting_travel() -> None:
    w = world(diamond(), {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=Scripted())
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
    w = world(line(2), {"A": "N0", "B": "N1"}, policy=Scripted())
    w.go("A", "N1")
    w.env.run()
    assert w.hooks("A").try_step_aside(None) is False


def test_resolutions_count_until_arrival_at_the_goal() -> None:
    graph_nodes = diamond()
    policy = Scripted(blocked=(Reroute(frozenset({graph_nodes[1]["P"]})),))
    w = world(graph_nodes, {"A": "S", "B": "P"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
    executor = w.fleet._executor
    w.go("A", "E")
    w.env.run()
    assert executor._resolutions == {}
    assert w.of(AgvRouted)[-1].reason == "blocked"


# --- standard_target ---------------------------------------------------------------------------------------------


def star(capacity_p: int = 1) -> tuple[World, dict[str, Node]]:
    """``M`` at ``X``; ``C`` at ``R`` waits to go ``R, X, P``. ``Q`` and ``T`` are 10 m from ``X`` as ``P`` is, ``R2``
    lies behind ``R``, ``FAR`` 20 m away and ``Z`` is isolated."""
    graph, nodes = build(
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
    )
    w = world(
        (graph, nodes),
        {"M": "X", "C": "R"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        capacities={nodes["P"]: capacity_p},
    )
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
    w = world(line(2), {"A": "N0", "B": "N1"})
    assert standard_target(w.fleet, w.agvs["A"]) is None


def test_standard_target_checks_the_router() -> None:
    graph, nodes = line(2)
    w = world((graph, nodes), {"A": "N0"}, reserve=False, router=BadRouter((nodes["N0"],)))
    with pytest.raises(ValueError, match="does not end at N1"):
        standard_target(w.fleet, w.agvs["A"])


# --- coordinator ---------------------------------------------------------------------------------------------------


def test_coordinator_defaults() -> None:
    env = Environment()
    graph, nodes = line(2)
    agv = AGV(env=env, agv_type=vehicle(length=None), initial_node=nodes["N0"])
    fleet = FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[])
    assert isinstance(fleet.traffic_controller, FreeFlowController)
    assert isinstance(fleet.router, ShortestDistanceRouter)
    assert fleet.traffic_controller.view.graph is graph  # the fleet registered with its controller
    assert isinstance(fleet._executor, TravelExecutor)
    assert fleet.clear_service_nodes is True


def test_coordinator_registers_with_a_reservation_controller() -> None:
    env = Environment()
    graph, nodes = line(2)
    controller = ReservationController(env=env, graph=graph)
    agv = AGV(env=env, agv_type=vehicle(length=None), initial_node=nodes["N0"])
    with pytest.raises(ValueError, match="has no length"):
        FleetCoordinator(
            env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[], traffic_controller=controller
        )


def test_initial_placement_goes_through_the_controller() -> None:
    w = world(line(3), {"A": "N0", "B": "N2"})
    assert w.held("A") == ["N0"] and w.held("B") == ["N2"]
    assert math.isclose(w.agvs["A"].battery.level, 1000.0)


# --- fix round 1 ---------------------------------------------------------------------------------------------------


def stalled() -> TrapezoidalProfile:
    """A profile whose load factor is 0: every arc has an infinite travel time."""
    return TrapezoidalProfile(2.0, 1.0, 1.0, load_speed_factor_fn=lambda weight: 0.0)


def test_a_stalled_arc_is_never_left_and_an_interrupt_takes_effect_at_once() -> None:
    graph_nodes = build(
        [("K", -10, 0), ("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0)], [("K", "N0"), ("N0", "N1"), ("N1", "N2")]
    )
    w = world(graph_nodes, {"A": "N0", "B": "K"}, derive_corridors=False, hold_back_dead_ends=False)
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
    w = world(line(3), {"A": "N1"}, agv_type=vehicle(length=12.0))
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
    w = world(line(3), {"A": "N0"}, reserve=False, agv_type=vehicle(profile=stalled()))
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


def leaf_line() -> tuple[LayoutGraph, dict[str, Node]]:
    """A leaf ``L`` of capacity 2 at the end of the line ``L, M, N``."""
    return build([("L", 0, 0), ("M", 10, 0), ("N", 20, 0)], [("L", "M"), ("M", "N")])


def assert_flowing(w: World) -> None:
    assert not w.of(TrafficDeadlock) and not w.of(TrafficWaitStuck)
    assert all(outcome is TravelOutcome.ARRIVED for outcome in w.outcomes.values())


@pytest.mark.parametrize("length", [0.0, 1.0])
def test_a_head_on_meeting_at_a_passing_place_completes_with_a_length(length: float) -> None:
    """A from R to Z, B from Z to R, passing at P (capacity 2). Q and Y lead into dead ends, so each waits at the
    entry for the whole rest of its route: the deadlock is resolved by stepping A aside to P, with a length as
    without one. Before the trail cleared on P, A kept ``P~Q`` there and the second deadlock stood unresolved."""
    graph_nodes = build(
        [("R", 0, 0), ("Q", 10, 0), ("P", 20, 0), ("Y", 30, 0), ("Z", 40, 0)],
        [("R", "Q"), ("Q", "P"), ("P", "Y"), ("Y", "Z")],
    )
    nodes = graph_nodes[1]
    w = world(graph_nodes, {"A": "R", "B": "Z"}, agv_type=vehicle(length=length), capacities={nodes["P"]: 2})
    w.go("A", "Z")
    w.go("B", "R")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert [(e.victim, e.action) for e in w.of(TrafficDeadlock)] == [("A", "step_aside")]
    assert not w.of(TrafficWaitStuck)


def test_two_vehicles_at_a_multi_slot_leaf_one_sent_away() -> None:
    graph_nodes = leaf_line()
    w = world(graph_nodes, {"A": "M", "B": "N"}, agv_type=vehicle(length=1.0), capacities={graph_nodes[1]["L"]: 2})
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
    graph_nodes = leaf_line()
    w = world(graph_nodes, {"A": "M", "B": "N"}, agv_type=vehicle(length=1.0), capacities={graph_nodes[1]["L"]: 2})
    w.go("A", "L")
    w.env.run()
    w.go("B", "L", at=50.0)
    w.go("A", "N", at=50.0)
    w.env.run()
    assert (w.agvs["A"].current_node, w.agvs["B"].current_node) == (w.nodes["N"], w.nodes["L"])
    assert_flowing(w)


def test_arriving_at_a_single_slot_node_keeps_the_trail() -> None:
    w = world(line(3), {"A": "N0"}, agv_type=vehicle(length=2.0), capacities={line(3)[1]["N0"]: 2})
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

    w = world(line(2), {"A": "N0"}, reserve=False)
    controller = Deferring()
    controller.register_fleet(w.fleet)
    w.fleet.traffic_controller = controller
    process = w.go("A", "N1")
    w.interrupt(process, 3.0)
    w.env.run()
    assert calls == ["stopped", "withdraw"]
    assert w.caught == {"A": "cancelled"}


def test_an_interrupt_at_the_instant_of_a_grant_drops_the_grant() -> None:
    w = world(line(3), {"W": "N0", "H": "N1"})
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
    w = world(t_layout(), {"A": "STORE_OUT"}, agv_type=vehicle(battery=15.0))
    process = w.go("A", "LINE_IN")
    w.interrupt(process, 3.0)
    w.env.run()
    (reserved,) = w.of(TrafficReserved, agv="A")  # the dead-end claim covers the whole route
    assert reserved.resources == ("C1~STORE_OUT", "C1", "C1~C2", "C2", "C2~LINE_IN", "LINE_IN")
    assert [e.resources for e in w.of(TrafficReleased, agv="A")][-1] == ("C1~C2", "C2", "C2~LINE_IN", "LINE_IN")
    assert w.held("A") == ["C1"]


def test_a_staged_reroute_of_a_deadlock_is_executed() -> None:
    graph_nodes = build([("A0", 0, 0), ("A1", 10, 0), ("X", 5, 5)], [("A0", "A1"), ("A0", "X"), ("X", "A1")])
    policy = Scripted()
    w = world(graph_nodes, {"A": "A0", "B": "A1"}, derive_corridors=False, hold_back_dead_ends=False, policy=policy)
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
    graph_nodes = build(
        [("S", 0, 0), ("X", 10, 8), ("Y", 10, -9), ("G", 20, 0), ("T", 30, 0)],
        [("S", "X"), ("X", "G"), ("S", "Y"), ("Y", "G"), ("G", "T")],
    )
    n = graph_nodes[1]
    policy = Scripted(blocked=(StepAside(n["T"]), Reroute(frozenset({n["T"]}), 0.5)))
    w = world(
        graph_nodes,
        {"A": "S", "B": "X", "C": "T"},
        derive_corridors=False,
        hold_back_dead_ends=False,
        policy=policy,
        router=ThroughTheGoal(n),
    )
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
