"""Idle AGVs (spec §7.9 standard search, §7.10): make-way moves, clearing of service nodes and parking slots.

Every world runs :class:`FullReplay` over every event and, with a ``ReservationController``, :class:`TrafficInvariants`.
Worlds with missions are built by :func:`mission_world` (warehouses, parking areas and charging stations on named
nodes, orders submitted by :meth:`Mission.order`); worlds that only drive travels use ``world`` of ``test_travel``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import (
    AgvMoveInterrupted,
    AgvRouted,
    AgvStateChanged,
    AgvStranded,
    ChargingStarted,
    ParkingEntered,
    ParkingLeft,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import LayoutGraph, Node
from simulatte.intralogistics.network import Zone
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.policies import NearestParkingPolicy, RepositioningContext
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.routing import Route, RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.traffic import FreeFlowController, KeepWaiting, StepAside, TrafficView, Wait
from simulatte.intralogistics.traffic_policy import StandardTrafficPolicy
from simulatte.intralogistics.travel import TravelOutcome, standard_target
from simulatte.intralogistics.warehouse import Warehouse
from tests.intralogistics.test_travel import Plain, stalled, vehicle, world
from tests.intralogistics.traffic_support import (
    FullReplay,
    TrafficInvariants,
    build,
    fake_hooks,
    line,
    t_layout,
    two_junction_corridor,
)

SKU_A = SKU(id="SKU-A", weight=5.0, volume=0.1)


# --- worlds with missions --------------------------------------------------------------------------------------------


class Always:
    """A repositioning policy that always names the node `node`."""

    def __init__(self, node: Node) -> None:
        self.node = node

    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None:
        return self.node


class Unreachable(ShortestDistanceRouter):
    """A router that finds no route to the nodes in `nodes`."""

    def __init__(self, *nodes: Node) -> None:
        super().__init__()
        self.nodes = nodes

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        if request.destination in self.nodes:
            return None
        return super().route(request, view)


class Scripted(ShortestDistanceRouter):
    """A router that answers its requests in turn: ``found[i]`` says whether the i-th request has a route."""

    def __init__(self, *found: bool) -> None:
        super().__init__()
        self.found = list(found)

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        return super().route(request, view) if self.found.pop(0) else None


@dataclass
class Mission:
    env: Environment
    fleet: FleetCoordinator
    nodes: dict[str, Node]
    agvs: dict[str, AGV]
    warehouses: dict[str, Warehouse]
    areas: dict[str, ParkingArea]
    stations: dict[str, ChargingStation]
    replay: FullReplay
    orders: list[TransferOrder] = field(default_factory=list)

    @property
    def controller(self) -> Any:
        return self.fleet.traffic_controller

    def order(self, origin: str, destination: str, *, at: float = 0.0) -> TransferOrder:
        """Create an order now and submit it at the absolute time `at`."""
        order = self.fleet.create_order(
            sku=SKU_A, quantity=1, origin=self.warehouses[origin], destination=self.warehouses[destination]
        )
        self.orders.append(order)
        self.at(at, lambda: self.fleet.submit(order))
        return order

    def at(self, time: float, action: Any) -> None:
        def later() -> Any:
            yield self.env.timeout(time - self.env.now)
            action()

        self.env.process(later())

    def events(self, *types: type, agv: str | None = None) -> list[Any]:
        return [e for e in self.replay.of(*types) if agv is None or getattr(e, "agv", None) == agv]

    def states(self, agv: str) -> list[tuple[float, str]]:
        return [(e.t, e.state) for e in self.events(AgvStateChanged, agv=agv)]

    def routed(self, agv: str) -> list[tuple[str, str, str]]:
        """``(reason, origin, destination)`` of each route of `agv`."""
        return [(e.reason, e.origin, e.destination) for e in self.events(AgvRouted, agv=agv)]


def mission_world(
    graph_nodes: tuple[LayoutGraph, dict[str, Node]],
    agvs: dict[str, str],
    *,
    bays: dict[str, str] | None = None,
    parking: dict[str, tuple[str, int]] | None = None,
    chargers: dict[str, tuple[str, int]] | None = None,
    reserve: bool = True,
    agv_type: AGVType | None = None,
    battery: dict[str, float] | None = None,
    controller_options: dict[str, Any] | None = None,
    controller_type: type[ReservationController] = ReservationController,
    **options: Any,
) -> Mission:
    """An activated world with missions: one warehouse per entry of `bays` (name to node id, input and output bay),
    parking areas and charging stations by name to ``(node id, size)``, one fleet with the AGVs. `battery` sets the
    level of some AGVs before the replay starts."""
    graph, nodes = graph_nodes
    env = Environment(debug=True)
    controller: Any = (
        controller_type(env=env, graph=graph, **(controller_options or {})) if reserve else FreeFlowController()
    )
    kind = agv_type or vehicle()
    vehicles = {
        name: AGV(env=env, agv_type=kind, agv_id=name, initial_node=nodes[start]) for name, start in agvs.items()
    }
    warehouses = {
        name: Warehouse(
            env=env,
            name=name,
            input_bays=[nodes[node]],
            output_bays=[nodes[node]],
            n_slots=4,
            products=[SKU_A],
            initial_inventory={SKU_A: 100},
            pick_time=1.0,
            put_time=1.0,
        )
        for name, node in (bays or {}).items()
    }
    areas = {
        name: ParkingArea(env=env, name=name, node=nodes[node], capacity=size)
        for name, (node, size) in (parking or {}).items()
    }
    stations = {
        name: ChargingStation(env=env, name=name, node=nodes[node], n_slots=size)
        for name, (node, size) in (chargers or {}).items()
    }
    fleet = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=list(vehicles.values()),
        warehouses=list(warehouses.values()),
        charging_stations=list(stations.values()),
        parking_areas=list(areas.values()),
        traffic_controller=controller,
        default_metrics=False,
        **options,
    )
    for name, level in (battery or {}).items():
        vehicles[name].battery.level = level
    replay = FullReplay(env, from_snapshot=True)
    if reserve:
        TrafficInvariants(env, controller)
    env.activate()
    return Mission(env, fleet, nodes, vehicles, warehouses, areas, stations, replay)


def lane() -> tuple[LayoutGraph, dict[str, Node]]:
    """``S - M - D - FAR - END`` along a row with ``SIDE`` hanging off ``M``."""
    return build(
        [("S", 0, 0), ("M", 10, 0), ("D", 20, 0), ("SIDE", 10, 10), ("FAR", 35, 0), ("END", 50, 0)],
        [("S", "M"), ("M", "D"), ("M", "SIDE"), ("D", "FAR"), ("FAR", "END")],
    )


# --- make way ------------------------------------------------------------------------------------------------------


def test_an_idle_blocker_makes_way_and_goes_idle_again(monkeypatch: pytest.MonkeyPatch) -> None:
    w = world(t_layout(), {"A": "C1", "B": "STORE_OUT"}, make_way=True)
    idle: list[tuple[float, AGVState]] = []
    w.fleet.on_agv_idle(lambda agv: idle.append((float(w.env.now), agv.state)))
    checked: list[AGVState] = []
    original = w.fleet._check_pending_queue
    monkeypatch.setattr(w.fleet, "_check_pending_queue", lambda: (checked.append(w.agvs["A"].state), original())[-1])
    w.go("B", "LINE_IN")
    w.env.run()

    a = w.agvs["A"]
    routed = w.of(AgvRouted, agv="A")
    assert [(e.reason, e.origin, e.destination, e.nodes) for e in routed] == [
        ("make_way", "C1", "PARK", ("C1", "PARK"))
    ]
    assert [(e.t, e.state) for e in w.of(AgvStateChanged, agv="A")] == [(0, "TRAVELING_EMPTY"), (_arrival(w), "IDLE")]
    assert (a.state, a.current_node) == (AGVState.IDLE, w.nodes["PARK"])
    assert w.outcomes == {"B": TravelOutcome.ARRIVED}
    assert idle == [(_arrival(w), AGVState.IDLE)]  # on_agv_idle fired once, with the AGV idle
    assert checked == [AGVState.IDLE]  # the pending-queue check ran after the move, with the AGV idle
    assert [e.reason for e in w.of(AgvRouted, agv="B")] == ["travel"]
    (started,) = w.of(TrafficWaitStarted)
    assert (started.agv, started.blockers) == ("B", ("A",))


def _arrival(w: Any) -> float:
    """The time A ends its 10 m move to PARK."""
    (ended,) = [e for e in w.of(AgvStateChanged, agv="A") if e.state == "IDLE"]
    return float(ended.t)


def test_make_way_refuses_what_cannot_move() -> None:
    w = world(t_layout(), {"A": "C1", "B": "STORE_OUT"}, make_way=True)
    fleet, a, b, nodes = w.fleet, w.agvs["A"], w.agvs["B"], w.nodes
    b.transition_to(AGVState.TRAVELING_EMPTY)
    assert fleet.make_way(b, nodes["C2"]) is False  # not idle
    assert fleet.make_way(a, nodes["C1"]) is False  # its own node
    assert fleet.make_way(a, concerned=(b,)) is True  # the standard search: C2 and PARK tie, C2 comes first
    assert a.state is AGVState.TRAVELING_EMPTY  # out of IDLE before make_way returned
    assert fleet.make_way(a, nodes["C2"]) is False  # one move at a time
    assert fleet.make_way(a) is False
    w.env.run()
    assert [e.destination for e in w.of(AgvRouted, agv="A")] == ["C2"]


def test_make_way_without_a_target_leaves_the_agv_idle() -> None:
    w = world(line(2), {"A": "N0", "B": "N1"}, make_way=True)
    assert standard_target(w.fleet, w.agvs["A"], idle=True) is None
    assert w.fleet.make_way(w.agvs["A"]) is False
    assert w.agvs["A"].state is AGVState.IDLE
    assert w.of(AgvStateChanged) == []


def test_make_way_takes_the_given_target() -> None:
    w = world(t_layout(), {"A": "C1"}, make_way=True)
    assert w.fleet.make_way(w.agvs["A"], w.nodes["C2"]) is True
    w.env.run()
    assert w.agvs["A"].current_node is w.nodes["C2"]
    assert [(e.reason, e.destination) for e in w.of(AgvRouted)] == [("make_way", "C2")]


def test_a_make_way_move_that_fails_ends_idle_where_it_stands() -> None:
    w = world(t_layout(), {"A": "C1"}, make_way=True)
    w.fleet.router = Scripted(True, False)  # the check finds a route, the move does not
    idle: list[str] = []
    w.fleet.on_agv_idle(lambda agv: idle.append(agv.id))
    assert w.fleet.make_way(w.agvs["A"], w.nodes["PARK"]) is True
    w.env.run()
    assert w.agvs["A"].state is AGVState.IDLE
    assert w.agvs["A"].current_node is w.nodes["C1"]
    assert idle == ["A"]


def test_a_stranded_make_way_stays_stranded() -> None:
    w = world(t_layout(), {"A": "C1"}, make_way=True, agv_type=vehicle(battery=0.5))
    assert w.fleet.make_way(w.agvs["A"], w.nodes["PARK"]) is True
    w.env.run()
    assert w.agvs["A"].state is AGVState.STRANDED


def test_a_make_way_move_that_must_retry_ends_idle() -> None:
    """A travel that arrives with a critical battery before its goal ends with ``RETRY_FROM_CURRENT_POSITION``; a
    make-way move does not retry, the AGV is idle where it stopped."""
    m = mission_world(lane(), {"A": "M"}, agv_type=vehicle(battery=100.0), battery={"A": 12.0})
    a = m.agvs["A"]
    idle: list[str] = []
    m.fleet.on_agv_idle(lambda agv: idle.append(agv.id))
    assert m.fleet.make_way(a, m.nodes["END"]) is True
    m.env.run()
    assert (a.state, a.current_node) == (AGVState.IDLE, m.nodes["D"])
    assert idle == ["A"]


def test_agv_making_way_is_not_dispatched() -> None:
    m = mission_world(lane(), {"A": "M"}, bays={"WH-S": "S", "WH-D": "D"}, clear_service_nodes=False)
    a = m.agvs["A"]
    assert m.fleet.make_way(a, m.nodes["SIDE"]) is True
    order = m.order("WH-S", "WH-D")
    m.env.run(until=1.0)
    assert (order.status, order.assigned_agv, a.state) == (OrderStatus.PENDING, None, AGVState.TRAVELING_EMPTY)
    m.env.run()
    assert order.status is OrderStatus.COMPLETED  # dispatched when the move ended and the AGV was idle again
    assert order.dispatched_at is not None and order.dispatched_at > 1.0
    states = [state for _, state in m.states("A")]
    assert states[:3] == ["TRAVELING_EMPTY", "IDLE", "TRAVELING_EMPTY"]


def test_an_agv_that_becomes_idle_on_a_blocking_node_makes_way() -> None:
    """A mission ends at M with B already waiting for it: the coordinator asks the idle AGV to make way."""
    m = mission_world(lane(), {"A": "S", "B": "D"}, bays={"WH-S": "S", "WH-M": "M"}, clear_service_nodes=False)
    m.order("WH-S", "WH-M")  # A carries from S to M
    m.at(2.0, lambda: m.env.process(_travel(m, "B", "S")))  # B wants to pass through M to S
    m.env.run()
    a, b = m.agvs["A"], m.agvs["B"]
    assert b.current_node is m.nodes["S"]
    reasons = [reason for reason, _, _ in m.routed("A")]
    assert reasons[0] == "travel" and "make_way" in reasons
    assert a.state is AGVState.IDLE


def _travel(m: Mission, name: str, goal: str) -> Any:
    agv = m.agvs[name]
    agv.transition_to(AGVState.TRAVELING_EMPTY)
    yield from m.fleet._executor.travel(agv, m.nodes[goal], loaded=False)
    agv.transition_to(AGVState.IDLE)


@pytest.mark.parametrize("make_way", [True, False])
def test_an_agv_idle_on_a_roomy_exit_port_of_a_corridor_does_not_block_the_opposite_traffic(make_way: bool) -> None:
    """A leaves the corridor onto J2 (capacity 2) and idles there. A port of capacity above 1 keeps no exit record
    (spec §7.5): the corridor goes with A's last member, so B, coming from the other end, passes J2 and the corridor
    without waiting, and A is not asked to move."""
    graph, nodes = two_junction_corridor()
    w = world(
        (graph, nodes), {"A": "J1", "B": "x2"}, make_way=make_way, capacities={nodes["J2"]: 2}, derive_corridors=True
    )
    w.go("A", "J2")
    w.go("B", "x1", at=40.0)
    w.env.run()

    assert w.outcomes == {"A": TravelOutcome.ARRIVED, "B": TravelOutcome.ARRIVED}
    assert w.of(TrafficWaitStarted) == []
    assert (w.agvs["A"].current_node, w.agvs["B"].current_node) == (nodes["J2"], nodes["x1"])
    assert [e.reason for e in w.of(AgvRouted, agv="A")] == ["travel"]


# --- the controller guards its review -----------------------------------------------------------------------------


def test_a_review_stops_when_its_wait_ended() -> None:
    w = world(t_layout(), {"A": "C1", "B": "STORE_OUT"})
    w.go("B", "LINE_IN")
    w.env.run(until=1.0)
    waiting = w.controller._pending[w.agvs["B"]]
    w.controller.withdraw(waiting.request)
    assert w.controller._pending == {}
    w.controller._review(waiting)  # a review of a wait that is gone changes nothing
    assert waiting.reviews == 0
    assert w.of(TrafficWaitEnded)[-1].reason == "interrupted"


def test_a_review_stops_when_making_way_ended_the_wait() -> None:
    """Making way at a review moves the blocker out of the way synchronously, so the claim is granted inside the
    review: the review must not go on to react, to schedule another review or to report the wait stuck."""
    w = world(t_layout(), {"A": "C1", "B": "STORE_OUT"})
    controller, a, park = w.controller, w.agvs["A"], w.nodes["PARK"]
    w.go("B", "LINE_IN")
    w.env.run(until=1.0)
    waiting = controller._pending[w.agvs["B"]]

    def moved_at_once(agv: AGV, target: Node | None = None, *, concerned: Any = ()) -> bool:
        controller.place(agv, park)
        agv.current_node = park  # directly, as a fleet that moves its AGV in no time would
        controller.release(agv, [w.nodes["C1"]])
        return True

    w.fleet.__dict__["make_way"] = moved_at_once  # shadows the method of this instance
    w.env.run(until=31.0)  # the review is due at 30
    assert waiting.reviews == 1 and waiting.timer is None  # no further review was scheduled
    assert controller._pending == {} and w.of(TrafficWaitStuck) == []
    assert [e.reason for e in w.of(TrafficWaitEnded)] == ["granted"]
    w.env.run()
    assert w.outcomes == {"B": TravelOutcome.ARRIVED}
    assert a.current_node is park


# --- service nodes -------------------------------------------------------------------------------------------------


def test_service_nodes_are_the_bays_and_the_charger_nodes() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, chargers={"CS": ("SIDE", 1)})
    assert m.fleet.service_nodes == frozenset(m.nodes[name] for name in ("S", "D", "SIDE"))


def test_an_agv_leaves_the_delivery_bay_under_reservation() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    order = m.order("WH-S", "WH-D")
    m.env.run()
    a = m.agvs["A"]
    assert order.status is OrderStatus.COMPLETED
    # the cheapest node that is not a service node nor a branch root (M roots the dead ends of the lane)
    assert m.routed("A")[-1] == ("clear_node", "D", "FAR")
    assert (a.state, a.current_node) == (AGVState.IDLE, m.nodes["FAR"])
    assert m.states("A")[-2:][0][1] == "TRAVELING_EMPTY"
    assert m.states("A")[-1][1] == "IDLE"


def test_clearing_is_the_last_step_before_the_idle_hooks() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    seen: list[tuple[float, str]] = []
    m.fleet.on_agv_idle(lambda agv: seen.append((float(m.env.now), agv.current_node.id)))  # type: ignore[union-attr]
    m.order("WH-S", "WH-D")
    m.env.run()
    ((time, node),) = seen
    assert node == "FAR" and time == m.states("A")[-1][0]


def test_an_agv_stays_on_the_bay_without_clear_service_nodes() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, clear_service_nodes=False)
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.agvs["A"].current_node is m.nodes["D"]
    assert "clear_node" not in [reason for reason, _, _ in m.routed("A")]


def test_an_agv_stays_on_the_bay_under_free_flow() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, reserve=False)
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.agvs["A"].current_node is m.nodes["D"]
    assert [reason for reason, _, _ in m.routed("A")] == ["travel"]  # to D; it starts on the origin bay


def test_an_agv_stays_when_the_search_finds_no_node() -> None:
    m = mission_world(line(2), {"A": "N0"}, bays={"WH-0": "N0", "WH-1": "N1"})
    order = m.order("WH-0", "WH-1")
    m.env.run()
    assert order.status is OrderStatus.COMPLETED
    assert m.agvs["A"].current_node is m.nodes["N1"]  # the only other node is a bay too
    assert "clear_node" not in [reason for reason, _, _ in m.routed("A")]


def test_a_repositioning_target_prevents_clearing() -> None:
    graph_nodes = lane()
    m = mission_world(graph_nodes, {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    m.fleet._repositioning_policy = Always(m.nodes["FAR"])
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.agvs["A"].current_node is m.nodes["FAR"]
    assert [reason for reason, _, _ in m.routed("A")] == ["travel", "travel"]  # to D, then to FAR


def test_a_repositioning_policy_that_names_the_current_node_keeps_the_agv_there() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    m.fleet._repositioning_policy = Always(m.nodes["D"])
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.agvs["A"].current_node is m.nodes["D"]


def test_a_charged_agv_leaves_the_charger() -> None:
    m = mission_world(
        lane(),
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        chargers={"CS": ("FAR", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 38.0},  # 18 left after the delivery: low, and enough to reach the charger
    )
    a = m.agvs["A"]
    m.order("WH-S", "WH-D")
    m.env.run()
    assert len(m.events(ChargingStarted)) == 1
    assert m.routed("A")[-1] == ("clear_node", "FAR", "END")  # off the charger, to the nearest node that is no bay
    assert a.current_node is m.nodes["END"]


def test_an_agv_that_a_low_battery_callback_moved_off_the_bays_is_not_cleared() -> None:
    """The user's low-battery callback replaces the charge and here moves the AGV to M, which is no service node."""
    holder: dict[str, Mission] = {}

    def move_to_m(agv: AGV) -> Any:
        m = holder["m"]
        yield from m.fleet._executor.travel(agv, m.nodes["M"], loaded=False)

    m = mission_world(
        lane(),
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        chargers={"CS": ("FAR", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 38.0},
        on_low_battery=move_to_m,
    )
    holder["m"] = m
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.agvs["A"].current_node is m.nodes["M"]
    assert "clear_node" not in [reason for reason, _, _ in m.routed("A")]


def test_a_clearing_move_that_is_stranded_ends_the_mission_stranded() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    a = m.agvs["A"]
    original = m.fleet._executor.travel

    def stranded(agv: AGV, goal: Node, *, loaded: bool, purpose: str = "travel") -> Any:
        if purpose == "clear_node":
            agv.transition_to(AGVState.STRANDED)
            return TravelOutcome.BATTERY_STRANDED
            yield  # pragma: no cover
        return (yield from original(agv, goal, loaded=loaded, purpose=purpose))

    m.fleet._executor.travel = stranded  # type: ignore[method-assign]
    m.order("WH-S", "WH-D")
    m.env.run()
    assert a.state is AGVState.STRANDED


def test_an_agv_stranded_on_its_way_to_the_charger_after_a_mission_is_not_cleared() -> None:
    """After the delivery on the bay D the battery is low and the only charger, on END, is out of reach: the charging
    travel ends stranded at D. The stranded AGV does not travel on to clear the bay and is stranded once."""
    m = mission_world(
        lane(),
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        chargers={"CS": ("END", 1)},
        parking={"P": ("SIDE", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 30.0},
    )
    m.order("WH-S", "WH-D")
    m.env.run()
    assert [(e.node, e.reason) for e in m.events(AgvStranded)] == [("D", "no_reachable_charger")]
    assert "clear_node" not in [reason for reason, _, _ in m.routed("A")]
    assert m.events(ParkingEntered) == []
    assert m.agvs["A"].current_node is m.nodes["D"]


# --- parking -------------------------------------------------------------------------------------------------------


class StepAsideTo(StandardTrafficPolicy):
    """Steps a blocked AGV aside to `target` (None: the standard search) at once."""

    def __init__(self, target: Node | None) -> None:
        super().__init__()
        self.target = target

    def on_blocked(self, wait: Wait, view: TrafficView) -> Any:
        return (StepAside(self.target), KeepWaiting(None))


def parked(m: Mission, area: str) -> list[str]:
    return list(m.areas[area].snapshot()["parked"])


def test_a_parking_node_gets_its_capacity_from_its_area() -> None:
    m = mission_world(t_layout(), {"A": "C1"}, parking={"P": ("PARK", 2)})
    assert m.controller.network.capacity(m.nodes["PARK"]) == 2


def test_agvs_placed_on_a_parking_node_take_no_slot() -> None:
    m = mission_world(t_layout(), {"A": "PARK", "B": "PARK"}, parking={"P": ("PARK", 2)})
    assert m.areas["P"].available_capacity == 2
    assert m.events(ParkingEntered) == []


def test_repositioning_takes_the_slot_at_the_decision_and_releases_it_at_departure() -> None:
    m = mission_world(
        lane(),
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        parking={"P": ("FAR", 2)},
        repositioning_policy=NearestParkingPolicy(),
    )
    area = m.areas["P"]
    seen: list[tuple[float, int]] = []
    m.env.bus.subscribe(lambda e: seen.append((float(e.t), area.available_capacity)), (AgvRouted,))
    m.order("WH-S", "WH-D")
    m.env.run(until=60.0)
    a = m.agvs["A"]
    assert a.current_node is m.nodes["FAR"]
    (entered,) = m.events(ParkingEntered)
    routed = [e for e in m.events(AgvRouted, agv="A")][-1]
    assert (entered.agv, entered.area) == ("A", "P")
    assert entered.t == routed.t  # decided together with the route
    assert [available for t, available in seen if t == entered.t] == [1]  # taken before the first event of the travel
    assert parked(m, "P") == ["A"] and area.available_capacity == 1
    assert m.events(ParkingLeft) == []
    # the next departure from the parking node gives the slot back
    m.order("WH-S", "WH-D", at=m.env.now)
    m.env.run(until=m.env.now + 1.0)
    (left,) = m.events(ParkingLeft)
    assert (left.agv, left.area) == ("A", "P")
    assert left.t == [e for e in m.events(AgvRouted, agv="A") if e.origin == "FAR"][0].t
    assert parked(m, "P") == [] and area.available_capacity == 2


def test_a_repositioning_travel_that_does_not_arrive_gives_the_slot_back() -> None:
    graph_nodes = lane()
    m = mission_world(
        graph_nodes,
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        parking={"P": ("FAR", 1)},
        repositioning_policy=NearestParkingPolicy(),
    )
    m.fleet.router = Unreachable(m.nodes["FAR"])
    m.order("WH-S", "WH-D")
    m.env.run()
    (entered,) = m.events(ParkingEntered)
    (left,) = m.events(ParkingLeft)
    assert entered.t == left.t
    assert m.areas["P"].available_capacity == 1
    assert m.agvs["A"].current_node is m.nodes["D"]


@pytest.mark.parametrize(("interrupt_at", "kept"), [(0.5, False), (1.5, True)])
def test_an_interrupted_idle_move_gives_the_slot_back_unless_it_ended_on_the_target(
    interrupt_at: float, kept: bool
) -> None:
    """With a rest-to-rest profile (no limits) the interrupt takes effect at the end of the arc: in the first arc (D to
    FAR) that is short of END, the parking node, so the slot goes back; in the second (FAR to END) the AGV is on the
    parking node. (A kinematic vehicle passes a node at speed: see the test below.)"""
    m = mission_world(
        lane(),
        {"A": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        parking={"P": ("END", 1)},
        agv_type=vehicle(profile=Plain()),
    )
    m.fleet._repositioning_policy = Always(m.nodes["END"])
    order = m.order("WH-S", "WH-D")

    def interrupt() -> Any:
        while not m.events(ParkingEntered):
            yield m.env.timeout(0.25)
        yield m.env.timeout(12.0 if kept else 0.5)
        m.fleet._active_missions[order.id].interrupt("breakdown")

    m.env.process(interrupt())
    m.env.run()
    a = m.agvs["A"]
    assert a.state is AGVState.IDLE
    assert (a.current_node is m.nodes["END"]) is kept
    assert (m.areas["P"].available_capacity == 0) is kept
    assert [e.agv for e in m.events(ParkingLeft)] == ([] if kept else ["A"])


@pytest.mark.parametrize(("interrupt_after", "kept"), [(0.5, False), (12.0, True)])
def test_an_interrupted_idle_move_of_a_kinematic_vehicle_gives_the_slot_back_unless_it_ended_on_the_target(
    interrupt_after: float, kept: bool
) -> None:
    """The parking node END lies beyond FAR and F2 (15 m, then 7.5 m each). Interrupted in the first arc, the vehicle
    passes FAR at speed and rests at F2, the first node a braking distance (1.40625 m) beyond it: short of END, so the
    slot goes back. Interrupted in the second arc it passes F2 at speed and rests at END, and keeps the slot (spec
    §8.3)."""
    graph_nodes = build(
        [("S", 0, 0), ("M", 10, 0), ("D", 20, 0), ("SIDE", 10, 10), ("FAR", 35, 0), ("F2", 42.5, 0), ("END", 50, 0)],
        [("S", "M"), ("M", "D"), ("M", "SIDE"), ("D", "FAR"), ("FAR", "F2"), ("F2", "END")],
    )
    m = mission_world(graph_nodes, {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, parking={"P": ("END", 1)})
    m.fleet._repositioning_policy = Always(m.nodes["END"])
    order = m.order("WH-S", "WH-D")

    def interrupt() -> Any:
        while not m.events(ParkingEntered):
            yield m.env.timeout(0.25)
        yield m.env.timeout(interrupt_after)
        m.fleet._active_missions[order.id].interrupt("breakdown")

    m.env.process(interrupt())
    m.env.run()
    a = m.agvs["A"]
    (interrupted,) = [e for e in m.events(AgvMoveInterrupted) if e.t >= m.events(ParkingEntered)[0].t]
    assert interrupted.node == ("END" if kept else "F2")
    assert a.state is AGVState.IDLE
    assert (a.current_node is m.nodes["END"]) is kept
    assert (m.areas["P"].available_capacity == 0) is kept
    assert [e.agv for e in m.events(ParkingLeft)] == ([] if kept else ["A"])


def test_a_full_area_is_not_a_target() -> None:
    m = mission_world(lane(), {"A": "S", "B": "SIDE"}, bays={"WH-S": "S", "WH-D": "D"}, parking={"P": ("FAR", 1)})
    for _ in m.areas["P"].enter(m.agvs["B"]):  # B holds the only slot
        pass
    assert m.areas["P"].available_capacity == 0
    m.fleet._repositioning_policy = Always(m.nodes["FAR"])  # a policy that ignores the slots
    order = m.order("WH-S", "WH-D")
    m.env.run()
    assert order.status is OrderStatus.COMPLETED
    assert [e.agv for e in m.events(ParkingEntered)] == ["B"]  # A took none
    assert m.routed("A")[-1] == ("clear_node", "D", "END")  # a full area is no target: the service node is cleared
    assert m.fleet.make_way(m.agvs["A"], m.nodes["FAR"]) is False
    assert standard_target(m.fleet, m.agvs["A"], idle=True) is not m.nodes["FAR"]


class Via(ShortestDistanceRouter):
    """The shortest-distance router, except for the routes named in `fixed` by ``(origin id, destination id)``."""

    def __init__(self, nodes: dict[str, Node], fixed: dict[tuple[str, str], tuple[str, ...]]) -> None:
        super().__init__()
        self.fixed = {key: tuple(nodes[name] for name in path) for key, path in fixed.items()}

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        path = self.fixed.get((request.origin.id, request.destination.id))
        if path is None:
            return super().route(request, view)
        return Route(path, view.graph.path_length(path))


def test_a_step_aside_through_the_goal_keeps_the_parking_slot_of_the_goal() -> None:
    """A travels from S to the parking node G, with its slot taken. X, stranded on X1, blocks the way; A is sent aside
    to T along S, Y, G, T, departs from G, comes back to G and arrives: the slot is still its own (spec §7.10)."""
    graph, nodes = build(
        [("S", 0, 0), ("X1", 10, 0), ("G", 20, 0), ("Y", 10, -10), ("T", 30, 0)],
        [("S", "X1"), ("X1", "G"), ("S", "Y"), ("Y", "G"), ("G", "T")],
    )
    m = mission_world(
        (graph, nodes),
        {"A": "S", "X": "X1"},
        parking={"P": ("G", 1)},
        router=Via(nodes, {("S", "T"): ("S", "Y", "G", "T")}),
        controller_options={"policy": StepAsideTo(nodes["T"]), "derive_corridors": False},
    )
    a = m.agvs["A"]
    m.fleet._transition_agv(m.agvs["X"], AGVState.STRANDED)
    assert m.fleet._take_slot(a, nodes["G"])
    m.env.process(_travel(m, "A", "G"))
    m.env.run()
    assert [(reason, origin, destination) for reason, origin, destination in m.routed("A")] == [
        ("travel", "S", "G"),
        ("step_aside", "S", "T"),
        ("travel", "T", "G"),
    ]
    assert a.current_node is nodes["G"]
    assert parked(m, "P") == ["A"] and m.areas["P"].available_capacity == 0
    assert m.events(ParkingLeft) == []


def test_an_area_of_two_never_lists_three_agvs() -> None:
    m = mission_world(
        lane(),
        {"A": "S", "B": "S", "C": "S"},
        bays={"WH-S": "S", "WH-D": "D"},
        parking={"P": ("FAR", 2)},
        repositioning_policy=NearestParkingPolicy(),
        controller_options={"node_capacity": 3},
    )
    listed: list[int] = []
    m.env.bus.subscribe(lambda e: listed.append(len(parked(m, "P"))), (ParkingEntered, ParkingLeft))
    for _ in range(3):
        m.order("WH-S", "WH-D")
    m.env.run()
    assert all(order.status is OrderStatus.COMPLETED for order in m.orders)
    assert max(listed) == 2
    assert len(parked(m, "P")) == 2
    elsewhere = [agv for agv in m.agvs.values() if agv.current_node is not m.nodes["FAR"]]
    assert len(elsewhere) == 1


def test_clearing_takes_a_parking_slot() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, parking={"P": ("FAR", 1)})
    m.order("WH-S", "WH-D")
    m.env.run()
    assert m.routed("A")[-1] == ("clear_node", "D", "FAR")  # the parking node comes first, though M is nearer
    (entered,) = m.events(ParkingEntered)
    assert entered.agv == "A" and m.areas["P"].available_capacity == 0


def test_make_way_takes_a_parking_slot_and_gives_it_back_when_it_does_not_arrive() -> None:
    m = mission_world(t_layout(), {"A": "C1"}, parking={"P": ("PARK", 1)})
    m.fleet.router = Scripted(True, False)  # the check finds a route, the move does not
    assert m.fleet.make_way(m.agvs["A"], m.nodes["PARK"]) is True
    assert m.areas["P"].available_capacity == 0  # taken at the decision, before the move runs
    m.env.run()
    assert m.areas["P"].available_capacity == 1
    assert [e.type_name for e in m.events(ParkingEntered, ParkingLeft)] == ["parking.entered", "parking.left"]


def test_a_parked_agv_that_makes_way_departs_from_its_slot() -> None:
    m = mission_world(t_layout(), {"A": "C1"}, parking={"P": ("PARK", 1)})
    assert m.fleet.make_way(m.agvs["A"], m.nodes["PARK"]) is True
    m.env.run()
    assert parked(m, "P") == ["A"]
    assert m.fleet.make_way(m.agvs["A"], m.nodes["C2"]) is True
    m.env.run()
    assert parked(m, "P") == []
    assert m.areas["P"].available_capacity == 1


def test_standard_target_for_an_idle_agv_prefers_the_cheapest_parking_area_with_a_free_slot() -> None:
    graph, nodes = build(
        [("O", 0, 0), ("N", 10, 0), ("P1", 20, 0), ("P2", 30, 0), ("P3", 40, 0), ("SB", 0, 10)],
        [("O", "N"), ("N", "P1"), ("P1", "P2"), ("P2", "P3"), ("O", "SB")],
    )
    m = mission_world(
        (graph, nodes),
        {"A": "O", "X": "P1"},
        bays={"WH": "SB"},
        parking={"A1": ("P1", 1), "A2": ("P2", 1), "A3": ("P3", 1)},
    )
    agv = m.agvs["A"]
    assert standard_target(m.fleet, agv) is nodes["N"]  # the generic search takes the nearest node
    assert standard_target(m.fleet, agv, idle=True) is nodes["P2"]  # P1 is held by X? X took no slot: P1 is nearest
    for _ in m.areas["A1"].enter(m.agvs["X"]):
        pass
    assert standard_target(m.fleet, agv, idle=True) is nodes["P2"]
    for _ in m.areas["A2"].enter(agv):
        pass
    assert standard_target(m.fleet, m.agvs["X"], idle=True) is nodes["P3"]
    assert standard_target(m.fleet, agv, idle=True) is nodes["P3"]


def test_standard_target_for_an_idle_agv_avoids_service_nodes_without_parking() -> None:
    m = mission_world(lane(), {"A": "M"}, bays={"WH-S": "S", "WH-D": "D"})
    agv = m.agvs["A"]
    assert standard_target(m.fleet, agv) is m.nodes["D"]  # the generic search takes the first of the nearest
    assert standard_target(m.fleet, agv, idle=True) is m.nodes["SIDE"]  # S and D are bays


def test_standard_target_for_an_idle_agv_skips_the_node_of_a_full_area() -> None:
    m = mission_world(lane(), {"A": "M", "B": "S"}, parking={"P": ("SIDE", 1)})
    for _ in m.areas["P"].enter(m.agvs["B"]):
        pass
    assert standard_target(m.fleet, m.agvs["A"], idle=True) is m.nodes["D"]


def ring_with_a_tail() -> tuple[LayoutGraph, dict[str, Node]]:
    """The ring ``A0, B0, C0, D0`` with the dead end ``T1, T2`` rooted at ``B0``."""
    return build(
        [("A0", 0, 0), ("B0", 10, 0), ("C0", 10, 10), ("D0", 0, 10), ("T1", 20, 0), ("T2", 30, 0)],
        [("A0", "B0"), ("B0", "C0"), ("C0", "D0"), ("D0", "A0"), ("B0", "T1"), ("T1", "T2")],
    )


@pytest.mark.parametrize(
    ("others", "parking", "expected"),
    [
        ({"C": "C0"}, None, "D0"),  # B0, as near and first by id, roots the dead end
        ({"C": "C0", "D": "D0"}, None, "T1"),  # members of the branch stay valid
        ({"C": "C0"}, ("B0", 1), "B0"),  # a parking node on the root is taken
    ],
    ids=["root", "member", "parking-root"],
)
def test_standard_target_for_an_idle_agv_skips_a_branch_root_unless_it_is_a_parking_node(
    others: dict[str, str], parking: tuple[str, int] | None, expected: str
) -> None:
    """spec §7.9: an idle AGV does not rest on the root of a dead-end branch unless on a parking node; members of the
    branch are valid targets."""
    graph, nodes = ring_with_a_tail()
    m = mission_world((graph, nodes), {"A": "A0", **others}, parking={"P": parking} if parking else None)
    agv = m.agvs["A"]
    assert standard_target(m.fleet, agv) is nodes["B0"]  # the generic search takes the nearest node, root or not
    assert standard_target(m.fleet, agv, idle=True) is nodes[expected]


# --- the make-way fallback (spec §7.9) ----------------------------------------------------------------------------


def charger_fork() -> tuple[LayoutGraph, dict[str, Node]]:
    """``P - X - Q`` along a row with ``CH`` hanging off ``X``."""
    return build([("P", 0, 0), ("X", 10, 0), ("Q", 20, 0), ("CH", 10, 10)], [("P", "X"), ("X", "Q"), ("X", "CH")])


def pending_claim(m: Mission, agv: str, *route: str) -> Any:
    """Request the first claim of `route` for `agv`, with hooks that report that route."""
    nodes = tuple(m.nodes[name] for name in route)
    vehicle = m.agvs[agv]
    return m.controller.request(vehicle, m.controller.next_claim(vehicle, nodes, 0), fake_hooks(nodes))


@pytest.mark.parametrize(("slots", "expected"), [(2, "CH"), (1, None)])
def test_make_way_falls_back_to_a_charging_node_with_room(slots: int, expected: str | None) -> None:
    """Idle A on X blocks B's claim from P. Q is a bay, P is taken and the charger node CH is a service node: the
    search finds nothing, and the fallback takes CH when it has two slots, not when it has one."""
    m = mission_world(
        charger_fork(),
        {"A": "X", "B": "P"},
        bays={"WQ": "Q"},
        chargers={"CS": ("CH", slots)},
        controller_options={"hold_back_dead_ends": False},
    )
    request = pending_claim(m, "B", "P", "X", "Q")
    a, b = m.agvs["A"], m.agvs["B"]
    assert standard_target(m.fleet, a, concerned=(b,), idle=True, purpose="make_way") is None
    assert m.fleet.make_way(a, concerned=(b,)) is (expected is not None)
    m.env.run()
    if expected is None:
        assert a.current_node is m.nodes["X"] and not m.events(AgvRouted) and not request.granted
    else:
        assert m.routed("A") == [("make_way", "X", "CH")]
        assert a.current_node is m.nodes["CH"] and request.granted


@pytest.mark.parametrize(("capacity", "expected"), [(2, "R"), (1, None)])
def test_make_way_falls_back_to_a_root_with_room(capacity: int, expected: str | None) -> None:
    """Idle A on X blocks B's dead-end claim from P to Q. R is the root of the bays L1 and L2: the fallback takes it
    when its capacity is above 1."""
    graph, nodes = build(
        [("P", 0, 0), ("X", 10, 0), ("Q", 20, 0), ("R", 10, 10), ("L1", 0, 20), ("L2", 20, 20)],
        [("P", "X"), ("X", "Q"), ("X", "R"), ("R", "L1"), ("R", "L2")],
    )
    m = mission_world(
        (graph, nodes),
        {"A": "X", "B": "P"},
        bays={"W1": "L1", "W2": "L2"},
        controller_options={"capacities": {nodes["R"]: capacity}},
    )
    pending_claim(m, "B", "P", "X", "Q")
    a, b = m.agvs["A"], m.agvs["B"]
    assert m.controller.view.branch_root(nodes["R"])
    assert standard_target(m.fleet, a, concerned=(b,), idle=True, purpose="make_way") is None
    assert m.fleet.make_way(a, concerned=(b,)) is (expected is not None)
    m.env.run()
    assert a.current_node is nodes[expected or "X"]


@pytest.mark.parametrize(("goal", "expected"), [("Q", "CH"), ("CH", None)])
def test_make_way_does_not_fall_back_to_a_node_whose_last_slot_a_concerned_agv_needs(
    goal: str, expected: str | None
) -> None:
    """C rests on the two-slot charger node CH. When B's route leads to CH, the slot left there is B's, so A does not
    take it; when B goes to Q, A does."""
    m = mission_world(
        charger_fork(),
        {"A": "X", "B": "P", "C": "CH"},
        bays={"WQ": "Q"},
        chargers={"CS": ("CH", 2)},
        controller_options={"hold_back_dead_ends": False},
    )
    pending_claim(m, "B", "P", "X", goal)
    a, b = m.agvs["A"], m.agvs["B"]
    assert m.fleet.make_way(a, concerned=(b,)) is (expected is not None)
    m.env.run()
    assert a.current_node is m.nodes[expected or "X"]


def bay_line() -> tuple[LayoutGraph, dict[str, Node]]:
    """``W - X - CH - Y - BAY`` along a row: two bays at the ends; CH is the root of the dead end ``Y, BAY``."""
    return build(
        [("W", 0, 0), ("X", 10, 0), ("CH", 20, 0), ("Y", 30, 0), ("BAY", 40, 0)],
        [("W", "X"), ("X", "CH"), ("CH", "Y"), ("Y", "BAY")],
    )


@pytest.mark.parametrize("zone", [False, True])
def test_the_fallback_may_arrive_over_a_segment_a_pending_claim_names(zone: bool) -> None:
    """Idle A on BAY blocks B's dead-end claim ``X -> BAY``, which names ``CH~Y``, the last segment of A's way to the
    two-slot charger node CH. The fallback takes CH all the same (A comes to rest there and its trail clears), unless a
    zone on that last leg is named too: a zone or corridor of the last leg still rules a node out."""
    graph, nodes = bay_line()
    options: dict[str, Any] = {"zones": [Zone("z", ((nodes["CH"], nodes["Y"]),))]} if zone else {}
    m = mission_world(
        (graph, nodes),
        {"A": "BAY", "B": "X"},
        bays={"WW": "W", "WB": "BAY"},
        chargers={"CS": ("CH", 2)},
        controller_options=options,
    )
    request = pending_claim(m, "B", "X", "CH", "Y", "BAY")
    a, b = m.agvs["A"], m.agvs["B"]
    assert m.controller.view.pending(m.controller.network.segment(nodes["CH"], nodes["Y"])) == 1
    assert standard_target(m.fleet, a, concerned=(b,), idle=True, purpose="make_way") is None
    assert m.fleet.make_way(a, concerned=(b,)) is not zone
    m.env.run()
    if zone:
        assert a.current_node is nodes["BAY"] and not request.granted
    else:
        assert m.routed("A") == [("make_way", "BAY", "CH")]
        assert a.current_node is nodes["CH"] and request.granted


def test_the_strict_search_still_refuses_a_last_segment_a_pending_claim_names() -> None:
    """CH is a parking node with two slots, a place the strict search may take; the last segment of the way there is
    named by B's pending claim, so the strict search refuses it, and the fallback, which looks only at service nodes and
    roots, does not take it either."""
    m = mission_world(bay_line(), {"A": "BAY", "B": "X"}, bays={"WW": "W", "WB": "BAY"}, parking={"PA": ("CH", 2)})
    pending_claim(m, "B", "X", "CH", "Y", "BAY")
    a, b = m.agvs["A"], m.agvs["B"]
    assert standard_target(m.fleet, a, concerned=(b,), idle=True, purpose="make_way") is None
    assert standard_target(m.fleet, a, concerned=(b,), idle=True, purpose="make_way", roomy=True) is None
    assert m.fleet.make_way(a, concerned=(b,)) is False


def test_clearing_a_service_node_has_no_fallback() -> None:
    """After its delivery A stands on the bay Q. The bay P, the root X and the charger node CH are not places to idle,
    so A stays on Q: the roomy charger node is a fallback of make-way moves only."""
    m = mission_world(charger_fork(), {"A": "P"}, bays={"WP": "P", "WQ": "Q"}, chargers={"CS": ("CH", 2)})
    (order,) = [m.order("WP", "WQ")]
    m.env.run()
    a = m.agvs["A"]
    assert order.status is OrderStatus.COMPLETED
    assert (a.state, a.current_node) == (AGVState.IDLE, m.nodes["Q"])
    assert [reason for reason, _, _ in m.routed("A")] == ["travel"]
    assert standard_target(m.fleet, a, idle=True, purpose="clear_node") is None
    assert standard_target(m.fleet, a, idle=True, purpose="clear_node", roomy=True) is m.nodes["CH"]


# --- passing a full charging node -------------------------------------------------------------------------------


def bypass_layout(*, bypass: bool) -> tuple[LayoutGraph, dict[str, Node]]:
    """``A - C - B`` along a row with the charger on ``C``, and with `bypass` also ``A - D - B`` around it."""
    points = [("A", 0, 0), ("C", 10, 0), ("B", 20, 0), ("D", 10, 3)]
    links = [("A", "C"), ("C", "B")] + ([("A", "D"), ("D", "B")] if bypass else [])
    if not bypass:
        points = points[:3]
    return build(points, links)


@pytest.mark.parametrize(("bypass", "level"), [(False, 80.0), (False, 5.0), (True, 5.0)])
def test_transit_through_full_charging_node(bypass: bool, level: float) -> None:
    """X charges on C (one slot) for 100 - `level` seconds. P, travelling from A to B through C, waits while the slot
    is busy. At its review, at 30, it reroutes when a route around exists. Without one it is reported stuck, at the
    instant X has finished charging and sits idle where it cannot make way, whether that is before or after the
    review."""
    m = mission_world(
        bypass_layout(bypass=bypass),
        {"X": "C", "P": "A"},
        chargers={"CS": ("C", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"X": level},
        clear_service_nodes=False,
    )
    x, p = m.agvs["X"], m.agvs["P"]
    station = m.stations["CS"]

    def charge() -> Any:
        yield from m.fleet._charge_agv(x, station)
        m.fleet._transition_agv(x, AGVState.IDLE)
        m.controller.idle(x)

    m.env.process(charge())
    m.at(1.0, lambda: m.env.process(_travel(m, "P", "B")))
    m.env.run(until=10.0)
    # the slot is busy: P waits, X is not asked to move, nobody is stuck
    (started,) = m.events(TrafficWaitStarted, agv="P")
    assert (started.blockers, started.t) == (("X",), 1.0)
    assert x.state is AGVState.CHARGING
    assert m.events(TrafficWaitStuck) == [] and m.events(AgvRouted, agv="X") == []
    m.env.run(until=29.0)
    assert (x.state is AGVState.CHARGING) is (level == 5.0)  # the long charge is still going at 29
    m.env.run()
    reasons = [e.reason for e in m.events(AgvRouted, agv="P")]
    stuck = m.events(TrafficWaitStuck)
    if bypass:
        assert reasons == ["travel", "blocked"]  # rerouted around the full node at the review, X still charging
        assert p.current_node is m.nodes["B"]
        assert stuck == []
    else:
        assert reasons == ["travel"]
        assert p.current_node is m.nodes["A"]  # no route around and X cannot move: P keeps waiting
        idle_at = [e.t for e in m.events(AgvStateChanged, agv="X") if e.state == "IDLE"]
        assert [(e.t, e.agv) for e in stuck] == [(idle_at[0], "P")]  # reported once, when X became idle
        assert x.state is AGVState.IDLE and x.current_node is m.nodes["C"]
        assert m.events(AgvRouted, agv="X") == []  # X found no node to go to


# --- fix round 1 ---------------------------------------------------------------------------------------------------


def fork() -> tuple[LayoutGraph, dict[str, Node]]:
    """``B0 - S - M - D`` along a row with the leaf ``SL`` off ``S``."""
    return build(
        [("B0", -10, 0), ("S", 0, 0), ("M", 10, 0), ("D", 20, 0), ("SL", 0, 10)],
        [("B0", "S"), ("S", "M"), ("M", "D"), ("S", "SL")],
    )


def idle_times(m: Mission, agv: str) -> list[float]:
    return [float(e.t) for e in m.events(AgvStateChanged, agv=agv) if e.state == "IDLE"]


def make_way_times(m: Mission, agv: str) -> list[float]:
    return [float(e.t) for e in m.events(AgvRouted, agv=agv) if e.reason == "make_way"]


# Finding 1: the idle search does not land where another wait needs room


def twin_roots() -> tuple[LayoutGraph, dict[str, Node]]:
    """Two roots ``R1``, ``R2`` joined by an arc, each with a waiting AGV on one side and a dead-end branch on the other
    whose leaf holds a stranded AGV."""
    return build(
        [
            ("w1", -10, 0),
            ("R1", 0, 0),
            ("n1", 10, 0),
            ("d1", 20, 0),
            ("w2", -10, 20),
            ("R2", 0, 20),
            ("n2", 10, 20),
            ("d2", 20, 20),
        ],
        [("w1", "R1"), ("R1", "n1"), ("n1", "d1"), ("w2", "R2"), ("R2", "n2"), ("n2", "d2"), ("R1", "R2")],
    )


def test_an_idle_agv_is_not_shuttled_between_two_waits() -> None:
    w = world(
        twin_roots(), {"A": "R1", "W1": "w1", "W2": "w2", "S1": "d1", "S2": "d2"}, make_way=True, derive_corridors=False
    )
    for name in ("S1", "S2"):
        w.agvs[name].transition_to(AGVState.STRANDED)
    w.go("W1", "d1")
    w.go("W2", "d2")
    w.env.run()  # returns: nothing keeps the simulation going
    a = w.agvs["A"]
    # W1's wait opens before W2 has asked, so A may make one move, to R2. There W2's claim names the node it stands on
    # and R1 is named by W1's: it finds nothing, stays, and is not sent back (before the fix: 101 moves, then stranded).
    assert len(w.of(AgvRouted, agv="A")) <= 1
    assert a.state is AGVState.IDLE
    assert sorted(e.agv for e in w.of(TrafficWaitStuck)) == ["W1", "W2"]


def test_an_idle_agv_is_not_shuttled_between_two_zones() -> None:
    """The reviewer's shuttle: the line ``P1, A1, A, B, B1, P2`` with the zones ``{A1, A}`` and ``{B, B1}`` of capacity
    2, stranded AGVs on A1 and B1, X idle on A, and W1, W2 travelling to A1 and B1. Each arrival of X fills the zone a
    waiter needs; the claims name the zone, not the node X would land on (before the fix: 101 make-way moves until the
    battery was empty)."""
    graph, nodes = build(
        [("P1", 0, 0), ("A1", 10, 0), ("A", 20, 0), ("B", 30, 0), ("B1", 40, 0), ("P2", 50, 0)],
        [("P1", "A1"), ("A1", "A"), ("A", "B"), ("B", "B1"), ("B1", "P2")],
    )
    zones = [Zone("Z1", (nodes["A1"], nodes["A"]), capacity=2), Zone("Z2", (nodes["B"], nodes["B1"]), capacity=2)]
    w = world(
        (graph, nodes),
        {"X": "A", "S1": "A1", "S2": "B1", "W1": "P1", "W2": "P2"},
        make_way=True,
        zones=zones,
        derive_corridors=False,
        hold_back_dead_ends=False,
    )
    for name in ("S1", "S2"):
        w.agvs[name].transition_to(AGVState.STRANDED)
    w.go("W1", "A1")
    w.go("W2", "B1")
    w.env.run()  # returns
    x = w.agvs["X"]
    assert len(w.of(AgvRouted, agv="X")) <= 1
    assert x.state is AGVState.IDLE
    assert w.env.peek() == float("inf")
    assert sorted(e.agv for e in w.of(TrafficWaitStuck)) == ["W1", "W2"]
    assert not w.outcomes  # both waits stand, reported


def test_an_agv_does_not_make_way_back_within_a_chain_of_moves_until_its_next_mission() -> None:
    """spec §7.10: X made way from A to B, where it blocks W2's wait: the chain goes on, and a move back to A is
    refused although A is free and reachable. A mission ends the chain."""
    graph, nodes = build(
        [("P1", 0, 0), ("A1", 10, 0), ("A", 20, 0), ("B", 30, 0), ("B1", 40, 0), ("P2", 50, 0)],
        [("P1", "A1"), ("A1", "A"), ("A", "B"), ("B", "B1"), ("B1", "P2")],
    )
    zones = (Zone("Z1", (nodes["A1"], nodes["A"]), capacity=2), Zone("Z2", (nodes["B"], nodes["B1"]), capacity=2))
    m = mission_world(
        (graph, nodes),
        {"X": "A", "S1": "A1", "S2": "B1", "W1": "P1", "W2": "P2"},
        bays={"WP1": "P1", "WP2": "P2"},
        controller_options={"zones": zones, "derive_corridors": False, "hold_back_dead_ends": False},
    )
    x = m.agvs["X"]
    for name in ("S1", "S2"):
        m.fleet._transition_agv(m.agvs[name], AGVState.STRANDED)
    m.env.process(_travel(m, "W1", "A1"))
    m.env.process(_travel(m, "W2", "B1"))
    m.env.run()
    assert m.routed("X") == [("make_way", "A", "B")]
    assert m.controller.view.waiting_on(x) == (m.agvs["W2"],)
    assert m.fleet.make_way(x, nodes["A"]) is False  # A made way from A in this chain
    m.order("WP1", "WP2", at=m.env.now)  # X is dispatched: the chain ends
    m.env.run(until=m.env.now + 1.0)
    assert x.state is AGVState.TRAVELING_EMPTY
    assert x not in m.fleet._made_way_from


def test_a_chain_of_make_way_moves_ends_where_the_agv_blocks_no_wait() -> None:
    """spec §7.10: A made way from M to SIDE and blocks no wait there; the chain ends, and M is a target again."""
    m = mission_world(lane(), {"A": "M"})
    a = m.agvs["A"]
    assert m.fleet.make_way(a, m.nodes["SIDE"]) is True
    m.env.run()
    assert a.current_node is m.nodes["SIDE"]
    assert m.fleet.make_way(a, m.nodes["M"]) is True
    m.env.run()
    assert a.current_node is m.nodes["M"]
    assert m.fleet.make_way(a, m.nodes["SIDE"]) is True  # a new chain: a move from M is allowed again
    m.env.run()
    assert a.current_node is m.nodes["SIDE"]


def test_a_chain_of_make_way_moves_cut_short_by_giving_up_ends() -> None:
    """spec §7.10, §8.4: the reduced stress seed 2241 of part B (a comb of 2 m arcs, vehicles 8 m long, a profile
    without limits). A1 waits at N1 for N2, A3 (making way) waits at N4 for N3, and A2, idle and without an order,
    stands between them. Each make-way move of A2 goes one arc towards the bay behind the other group, stops in a
    deadlock and gives up, which a travel without an order always does: A2 is idle again on a node it made way from.
    It is not asked to start another move from there in the same chain, so the moves are bounded, both waits are
    reported stuck behind A2, and the run returns."""
    graph, nodes = build(
        [(f"N{i}", 2 * i, 0) for i in range(6)] + [(f"B{i}", 2 * i, 2) for i in range(6)],
        [(f"N{i}", f"N{i + 1}") for i in range(5)] + [(f"N{i}", f"B{i}") for i in range(6)],
    )
    w = world(
        (graph, nodes),
        {"A1": "B0", "A2": "B2", "A3": "B5"},
        make_way=True,
        agv_type=vehicle(length=8.0, profile=Plain(), battery=1e6),
    )
    fleet = w.fleet

    def travel(name: str, goal: str) -> Any:  # as the stress ran it: idle and told the controller afterwards
        agv = w.agvs[name]
        fleet._transition_agv(agv, AGVState.TRAVELING_EMPTY)
        yield from fleet._executor.travel(agv, nodes[goal], loaded=False)
        fleet._transition_agv(agv, AGVState.IDLE)
        fleet.traffic_controller.idle(agv)

    w.env.process(travel("A1", "N5"))
    w.env.process(travel("A2", "B5"))
    w.env.run()  # returns: before the rule the moves went on, one per second, for ever
    assert w.env.now == 68.0
    moves = [(e.agv, e.origin, e.destination) for e in w.of(AgvRouted) if e.reason == "make_way"]
    assert len(moves) == 4
    assert [(e.agv, dict(e.blockers)) for e in w.of(TrafficWaitStuck)] == [
        ("A3", {"A2": "IDLE"}),
        ("A1", {"A2": "IDLE"}),
    ]
    a2 = w.agvs["A2"]
    assert a2.state is AGVState.IDLE and a2.current_node in fleet._made_way_from[a2]
    assert fleet.make_way(a2) is False  # the chain has come back to a node it left


def test_a_spare_agv_keeps_making_way_on_a_ring() -> None:
    """The reviewer's spare AGV: a ring of six nodes with three bays, one AGV of one fleet carrying 60 orders and an
    idle AGV of a second fleet on the same controller, which makes way again and again. Each chain ends where it blocks
    no wait, so it is never left without a target: every order completes and no stuck wait is open at the end."""
    import random

    graph, nodes = build(
        [("r0", 0, 0), ("r1", 10, 0), ("r2", 20, 0), ("r3", 20, 10), ("r4", 10, 10), ("r5", 0, 10)]
        + [("L", -10, 0), ("R", 30, 0), ("T", 10, 20)],
        [("r0", "r1"), ("r1", "r2"), ("r2", "r3"), ("r3", "r4"), ("r4", "r5"), ("r5", "r0")]
        + [("L", "r0"), ("R", "r2"), ("T", "r4")],
    )
    env = Environment(debug=True)
    controller = ReservationController(env=env, graph=graph)
    kind = vehicle(length=0.0, battery=1e9)
    bays = ["L", "R", "T"]
    sites = {
        bay: Warehouse(
            env=env,
            name=f"WH-{bay}",
            input_bays=[nodes[bay]],
            output_bays=[nodes[bay]],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: 1000},
            pick_time=1.0,
            put_time=1.0,
        )
        for bay in bays
    }
    common: dict[str, Any] = {"env": env, "graph": graph, "traffic_controller": controller, "default_metrics": False}
    working = FleetCoordinator(
        fleet=[AGV(env=env, agv_type=kind, agv_id="A", initial_node=nodes["L"])],
        warehouses=list(sites.values()),
        charging_stations=[],
        name="working",
        **common,
    )
    spare = FleetCoordinator(
        fleet=[AGV(env=env, agv_type=kind, agv_id="I", initial_node=nodes["r1"])],
        warehouses=[],
        charging_stations=[],
        name="spare",
        **common,
    )
    replay = FullReplay(env, from_snapshot=True)
    invariants = TrafficInvariants(env, controller)
    rng = random.Random(0)
    orders = []
    for _ in range(60):
        origin, destination = rng.sample(bays, 2)
        order = working.create_order(sku=SKU_A, quantity=1, origin=sites[origin], destination=sites[destination])
        orders.append(order)
        working.submit(order)
    env.run()
    assert invariants.checked > 0
    assert [order.status for order in orders] == [OrderStatus.COMPLETED] * 60
    assert len([e for e in replay.of(AgvRouted) if e.agv == "I" and e.reason == "make_way"]) > 5
    events = replay.events
    for index, stuck in enumerate(events):
        if isinstance(stuck, TrafficWaitStuck):
            assert any(isinstance(e, TrafficWaitEnded) and e.agv == stuck.agv for e in events[index:]), stuck
    assert spare.fleet[0].state is AGVState.IDLE


@pytest.mark.parametrize(("capacity", "expected"), [(2, "X"), (1, None)])
def test_a_node_named_by_a_pending_claim_is_taken_when_a_slot_remains_for_each(
    capacity: int, expected: str | None
) -> None:
    """W, on its way to g through X, holds back for the zone of g, which the stranded G holds: W's pending claim
    names X. The layout has no dead end; Y is taken by another stranded AGV."""
    graph, nodes = build(
        [("w1", -10, 0), ("X", 0, 0), ("g", 10, 0), ("e", 0, 10), ("Y", 0, 30)],
        [("w1", "X"), ("X", "g"), ("X", "e"), ("w1", "Y"), ("g", "Y"), ("e", "Y")],
    )
    w = world(
        (graph, nodes),
        {"A": "e", "W": "w1", "G": "g", "H": "Y"},
        make_way=True,
        capacities={nodes["X"]: capacity},
        zones=(Zone("Z", (nodes["g"],), hold_back=True),),
        derive_corridors=False,
    )
    w.agvs["G"].transition_to(AGVState.STRANDED)
    w.agvs["H"].transition_to(AGVState.STRANDED)
    w.go("W", "g")
    w.env.run(until=1.0)
    assert w.controller.view.pending(nodes["X"]) == 1  # W's claim names X
    found = standard_target(w.fleet, w.agvs["A"], idle=True)
    assert found is (None if expected is None else nodes[expected])
    generic = standard_target(w.fleet, w.agvs["A"])  # without idle the rule does not apply
    assert generic is nodes["X"]


class Recording(ShortestDistanceRouter):
    """A router that records the purpose of every request."""

    def __init__(self) -> None:
        super().__init__()
        self.purposes: list[str] = []

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        self.purposes.append(request.purpose)
        return super().route(request, view)


def test_the_search_and_the_move_use_the_same_purpose() -> None:
    w = world(t_layout(), {"A": "C1"}, make_way=True, router=Recording())
    router = w.fleet.router
    assert w.fleet.make_way(w.agvs["A"]) is True
    w.env.run()
    assert set(router.purposes) == {"make_way"}  # the search for the node and the move, not "step_aside"

    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, router=Recording())
    router = m.fleet.router
    m.order("WH-S", "WH-D")
    m.env.run()
    delivered = router.purposes.index("clear_node")
    assert set(router.purposes[delivered:]) == {"clear_node"}  # the whole search and the move, nothing else
    assert "step_aside" not in router.purposes


class Requests(ShortestDistanceRouter):
    """A router that records ``(origin id, destination id, purpose, load weight)`` of every request."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str, str, float]] = []

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        self.asked.append((request.origin.id, request.destination.id, request.purpose, request.load_weight))
        return super().route(request, view)


def test_the_step_aside_search_and_its_move_ask_the_router_alike() -> None:
    """A carries cargo on a travel that is not loaded in name (as a charging diversion does) and is sent aside by the
    standard search: the search and the staged move ask with the same purpose and load weight (spec §7.9)."""
    graph, nodes = build(
        [("S", 0, 0), ("X1", 10, 0), ("G", 20, 0), ("Y", 10, -10), ("T", 30, 0)],
        [("S", "X1"), ("X1", "G"), ("S", "Y"), ("Y", "G"), ("G", "T")],
    )
    m = mission_world(
        (graph, nodes),
        {"A": "S", "X": "X1"},
        router=Requests(),
        controller_options={"policy": StepAsideTo(None), "derive_corridors": False},
    )
    a = m.agvs["A"]
    m.fleet._transition_agv(m.agvs["X"], AGVState.STRANDED)
    m.fleet._set_load(a, {SKU_A: 2})
    m.env.process(_travel(m, "A", "G"))
    m.env.run()
    router = m.fleet.router
    assert isinstance(router, Requests)
    aside = [step for step in m.routed("A") if step[0] == "step_aside"]
    assert len(aside) == 1
    from_s = [(purpose, load) for origin, _, purpose, load in router.asked[1:] if origin == "S"]
    assert set(from_s) == {("step_aside", 0.0)}  # every request of the search and the one of the move


def test_a_purpose_sensitive_router_is_asked_with_the_purpose_of_the_move() -> None:
    class NoStepAside(ShortestDistanceRouter):
        def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
            return None if request.purpose == "step_aside" else super().route(request, view)

    w = world(t_layout(), {"A": "C1"}, make_way=True, router=NoStepAside())
    assert w.fleet.make_way(w.agvs["A"]) is True
    w.env.run()
    assert w.agvs["A"].current_node is not w.nodes["C1"]  # the search found a node: the move reached it


# Finding 2: the idle transition on each path asks the AGV to make way at that instant


def test_a_mission_that_ends_idle_on_a_blocking_node_makes_way_at_once() -> None:
    m = mission_world(fork(), {"A": "S", "B": "D"}, bays={"WH-S": "S", "WH-M": "M"}, clear_service_nodes=False)
    m.order("WH-S", "WH-M")  # A ends on M
    m.at(3.0, lambda: m.env.process(_travel(m, "B", "S")))  # B waits on M, held by A while it moves and serves
    m.env.run()
    (opened,) = m.events(TrafficWaitStarted, agv="B")
    assert opened.t < idle_times(m, "A")[0]
    assert make_way_times(m, "A") == [idle_times(m, "A")[0]]  # at the transition, not at the review 30 s later


def test_a_failed_travel_that_ends_idle_on_a_blocking_node_makes_way_at_once() -> None:
    m = mission_world(
        fork(),
        {"A": "S", "B": "M"},
        bays={"WH-S": "S", "WH-D": "D"},
        clear_service_nodes=False,
    )
    m.fleet.router = Unreachable(m.nodes["D"])
    order = m.order("WH-S", "WH-D")
    m.at(0.5, lambda: m.env.process(_travel(m, "B", "S")))  # B waits on S while A loads
    m.env.run()
    assert order.status is OrderStatus.FAILED
    assert make_way_times(m, "A") == [idle_times(m, "A")[0]]


def test_a_cancelled_mission_that_ends_idle_on_a_blocking_node_makes_way_at_once() -> None:
    m = mission_world(fork(), {"A": "S", "B": "M"}, bays={"WH-S": "S", "WH-D": "D"}, clear_service_nodes=False)
    order = m.order("WH-S", "WH-D")
    m.at(0.5, lambda: m.env.process(_travel(m, "B", "S")))
    m.at(1.0, lambda: m.fleet.cancel(order))
    m.env.run()
    assert order.status is OrderStatus.CANCELLED
    assert make_way_times(m, "A") == [idle_times(m, "A")[0]]


def test_the_end_of_a_make_way_move_that_blocks_another_wait_makes_way_at_once() -> None:
    graph, nodes = build(
        [("P", -10, 0), ("Y", 0, 0), ("Z", 10, 0), ("C0", 0, 10), ("E", 0, -10)],
        [("P", "Y"), ("Y", "Z"), ("C0", "Y"), ("Y", "E")],
    )
    m = mission_world((graph, nodes), {"A": "P", "C": "C0", "S": "Z"}, clear_service_nodes=False)
    m.agvs["S"].transition_to(AGVState.STRANDED)
    m.at(0.5, lambda: m.env.process(_travel(m, "C", "Z")))  # C waits for Y and Z, which S holds
    m.at(2.0, lambda: m.fleet.make_way(m.agvs["A"], nodes["Y"]))
    m.env.run()
    arrival = idle_times(m, "A")[0]
    assert arrival > 2.0
    assert [e.destination for e in m.events(AgvRouted, agv="A")][0] == "Y"
    assert make_way_times(m, "A")[1:] == [arrival]  # the second make-way move starts when the first ends


# Finding 3: the idle case belongs to the controller


class Choosing(StandardTrafficPolicy):
    """The standard policy, but with the make-way target `node` (recorded)."""

    def __init__(self, node: Node | None) -> None:
        super().__init__()
        self.node = node
        self.asked: list[tuple[str, str]] = []

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        self.asked.append((blocker.id, wait.agv.id))
        return self.node


def test_the_policys_make_way_target_is_used_on_the_idle_path() -> None:
    graph_nodes = fork()
    policy = Choosing(graph_nodes[1]["SL"])  # the standard search would take B0
    m = mission_world(
        graph_nodes,
        {"A": "S", "B": "D"},
        bays={"WH-S": "S", "WH-M": "M"},
        clear_service_nodes=False,
        controller_options={"policy": policy},
    )
    m.order("WH-S", "WH-M")
    m.at(3.0, lambda: m.env.process(_travel(m, "B", "S")))
    m.env.run()
    assert ("A", "B") in policy.asked
    assert [e.destination for e in m.events(AgvRouted, agv="A") if e.reason == "make_way"] == ["SL"]


def test_free_flow_idle_does_nothing() -> None:
    m = mission_world(lane(), {"A": "S"}, reserve=False)
    before = len(m.replay.events)
    assert FreeFlowController().idle(m.agvs["A"]) is None
    assert m.controller.idle(m.agvs["A"]) is None
    assert len(m.replay.events) == before


def test_idle_on_an_agv_that_blocks_nobody_emits_nothing_and_asks_nothing() -> None:
    m = mission_world(lane(), {"A": "S", "B": "D"})
    calls: list[AGV] = []
    m.fleet.make_way = lambda agv, target=None, *, concerned=(): calls.append(agv) or False  # type: ignore[method-assign]
    before = len(m.replay.events)
    m.controller.idle(m.agvs["A"])
    assert calls == [] and len(m.replay.events) == before


class Counting(StandardTrafficPolicy):
    def __init__(self) -> None:
        super().__init__()
        self.blocked = 0

    def on_blocked(self, wait: Wait, view: TrafficView) -> Any:
        self.blocked += 1
        return super().on_blocked(wait, view)


def test_idle_does_not_review_or_schedule_and_reports_a_stuck_wait_once() -> None:
    """An idle notification is not a review: no ``on_blocked``, no review count, no timer. A (idle on S) cannot move,
    so the wait it blocks is reported stuck at once, and only once."""
    policy = Counting()
    m = mission_world(
        fork(),
        {"A": "S", "B": "M", "G1": "B0", "G2": "SL"},
        clear_service_nodes=False,
        controller_options={"policy": policy},
    )
    for name in ("G1", "G2"):
        m.agvs[name].transition_to(AGVState.STRANDED)
    m.at(0.5, lambda: m.env.process(_travel(m, "B", "S")))
    m.env.run(until=1.0)
    waiting = m.controller._pending[m.agvs["B"]]
    reviews, timer, blocked = waiting.reviews, waiting.timer, policy.blocked
    assert m.events(TrafficWaitStuck) == []  # the first review is due at 30
    m.controller.idle(m.agvs["A"])
    (stuck,) = m.events(TrafficWaitStuck)
    assert (stuck.agv, stuck.t) == ("B", 1.0)
    m.controller.idle(m.agvs["A"])
    assert len(m.events(TrafficWaitStuck)) == 1
    assert (waiting.reviews, waiting.timer, policy.blocked) == (reviews, timer, blocked)


# Finding 4: no clearing while an order is pending


def test_no_clearing_while_an_order_is_pending() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    first, second = m.order("WH-S", "WH-D"), m.order("WH-S", "WH-D")
    m.env.run()
    assert first.status is second.status is OrderStatus.COMPLETED
    assert m.routed("A") == [
        ("travel", "S", "D"),
        ("travel", "D", "S"),  # the second order is dispatched where A stands: no detour in between
        ("travel", "S", "D"),
        ("clear_node", "D", "FAR"),  # after the last delivery A still clears
    ]
    assert second.dispatched_at == first.delivered_at


# Finding 5: the slot goes only when the AGV really leaves the node


def test_a_stalled_first_arc_keeps_the_parking_slot() -> None:
    m = mission_world(lane(), {"A": "M"}, parking={"P": ("SIDE", 1)})
    a = m.agvs["A"]
    assert m.fleet.make_way(a, m.nodes["SIDE"]) is True
    m.env.run()
    assert parked(m, "P") == ["A"]
    a.agv_type = vehicle(profile=stalled())  # every arc has an infinite travel time from now on
    assert m.fleet.make_way(a, m.nodes["D"]) is True  # not M, which it made way from; the first arc is SIDE -> M
    m.env.run(until=500.0)
    assert (a.state, a.current_node) == (AGVState.TRAVELING_EMPTY, m.nodes["SIDE"])  # it never left
    assert parked(m, "P") == ["A"] and m.events(ParkingLeft) == []


def test_a_travel_that_ends_on_the_target_node_keeps_the_slot_whatever_its_outcome() -> None:
    m = mission_world(lane(), {"A": "M"}, parking={"P": ("SIDE", 1)}, reserve=False)
    a, target = m.agvs["A"], m.nodes["SIDE"]

    def gives_up_on_the_target(agv: AGV, goal: Node, *, loaded: bool, purpose: str = "travel") -> Any:
        agv.current_node = goal  # the AGV stands on the target, though the travel did not report an arrival
        return TravelOutcome.GAVE_UP
        yield  # pragma: no cover

    m.fleet._executor.travel = gives_up_on_the_target  # type: ignore[method-assign]
    assert m.fleet.make_way(a, target) is True
    m.env.run()
    assert (a.state, a.current_node) == (AGVState.IDLE, target)
    assert parked(m, "P") == ["A"] and m.events(ParkingLeft) == []


def test_idle_for_an_agv_without_a_fleet_reports_the_stuck_wait() -> None:
    m = mission_world(fork(), {"B": "D"}, clear_service_nodes=False)
    outsider = AGV(env=m.env, agv_type=vehicle(), agv_id="X", initial_node=m.nodes["M"])
    m.controller.place(outsider, m.nodes["M"])  # an AGV that no fleet owns holds M
    m.env.process(_travel(m, "B", "S"))
    m.env.run(until=1.0)
    assert m.events(TrafficWaitStuck) == []
    m.controller.idle(outsider)  # nobody can ask it to move
    (stuck,) = m.events(TrafficWaitStuck)
    assert (stuck.agv, stuck.t) == ("B", 1.0)


# --- fix round 2: a make-way move that fails at once is not repeated at the same instant --------------------------


def run_bounded(m: Mission, steps: int = 5000) -> int:
    """Step the environment until its queue is empty or `steps` is reached; the number of steps taken."""
    taken = 0
    while m.env.peek() != float("inf") and taken < steps:
        m.env.step()
        taken += 1
    return taken


def test_a_policy_target_that_the_router_cannot_reach_does_not_loop() -> None:
    graph_nodes = fork()
    policy = Choosing(graph_nodes[1]["SL"])
    m = mission_world(
        graph_nodes,
        {"A": "S", "B": "D"},
        bays={"WH-S": "S", "WH-M": "M"},
        clear_service_nodes=False,
        controller_options={"policy": policy},
    )
    m.fleet.router = Unreachable(m.nodes["SL"])
    m.order("WH-S", "WH-M")
    m.at(3.0, lambda: m.env.process(_travel(m, "B", "S")))
    assert run_bounded(m) < 5000  # the run ends: nothing repeats without end at one instant
    assert len(policy.asked) < 10
    assert [e.reason for e in m.events(AgvRouted, agv="A")] == ["travel"]  # no make-way move was started
    assert [e.agv for e in m.events(TrafficWaitStuck)] == ["B"]  # the wait is reported stuck, once


def test_make_way_refuses_a_target_the_router_cannot_reach(caplog: pytest.LogCaptureFixture) -> None:
    w = world(t_layout(), {"A": "C1"}, make_way=True)
    w.fleet.router = Unreachable(w.nodes["PARK"])
    before = len(w.replay.events)
    assert w.fleet.make_way(w.agvs["A"], w.nodes["PARK"]) is False
    assert w.agvs["A"].state is AGVState.IDLE
    assert len(w.replay.events) == before  # no travel, no state change
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []  # and no error is logged


def test_a_make_way_move_that_ends_at_once_is_not_repeated_at_the_same_instant() -> None:
    m = mission_world(fork(), {"A": "S"})
    a, sl = m.agvs["A"], m.nodes["SL"]
    m.fleet.router = Scripted(True, False, True, True)  # the check finds a route, the move does not; then both do
    assert m.fleet.make_way(a, sl) is True
    while a.state is not AGVState.IDLE:
        m.env.step()
    assert (m.env.now, a.current_node) == (0, m.nodes["S"])  # the move failed at once, where it started
    assert m.fleet.make_way(a, sl) is False  # not again at this instant
    assert a.state is AGVState.IDLE
    m.env.run(until=2.0)
    assert m.fleet.make_way(a, sl) is True  # a later time: allowed again
    m.env.run()
    assert (a.state, a.current_node) == (AGVState.IDLE, sl)
    assert make_way_times(m, "A") == [2.0]


# --- final review: a node inside a full zone is not a free rest (spec §7.9) -----------------------------------------


@pytest.mark.parametrize(("capacity", "hold_back", "expected"), [(1, True, "P"), (1, False, "P"), (2, True, "Q")])
def test_the_idle_search_does_not_take_a_node_inside_a_full_zone(capacity: int, hold_back: bool, expected: str) -> None:
    """``A - B - H - Q`` along a row and ``P`` off ``A``, farther than ``Q``; B and H are bays. Q has two slots and lies
    in the zone Z, held by R resting on Q. With a zone of one the last leg to Q needs Z, which R makes inadmissible,
    so Q is no free rest although its node has a slot (the first claim, from A to B, does not name Z, even with
    `hold_back`): the search takes P. A zone of two has room, and Q, the cheaper, is taken."""
    graph, nodes = build(
        [("A", 0, 0), ("B", 10, 0), ("H", 20, 0), ("Q", 30, 0), ("P", 0, 35)],
        [("A", "B"), ("B", "H"), ("H", "Q"), ("A", "P")],
    )
    m = mission_world(
        (graph, nodes),
        {"M": "A", "R": "Q"},
        bays={"WB": "B", "WH": "H"},
        controller_options={
            "capacities": {nodes["Q"]: 2},
            "zones": (Zone("Z", (nodes["Q"],), capacity=capacity, hold_back=hold_back),),
            "hold_back_dead_ends": False,
        },
    )
    m_agv = m.agvs["M"]
    assert standard_target(m.fleet, m_agv, idle=True, purpose="make_way") is nodes[expected]
    assert m.fleet.make_way(m_agv) is True
    m.env.run()
    assert m.routed("M") == [("make_way", "A", expected)]
    assert m_agv.current_node is nodes[expected]


class Steady:
    """A speed profile with only ``travel_time``, at the speed the random world 206 drew."""

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return distance / 1.3105759301850175


def test_make_way_moves_into_a_full_zone_do_not_go_on_for_ever() -> None:
    """spec §7.9, §7.10: the control reviewer's random world 206, written out. Three AGVs with vehicles 3 m long on arcs
    of 1 to 4 m, a node c02 of capacity 2 inside the zone Z0 of capacity 1 with hold-back, few nodes where an idle AGV
    may rest. Before the rule every order completed by about 588 s and then the idle AGVs made way to c02 and around
    the ring for ever (4,650 make-way moves in 20,000 s): the mover stopped at the hold-back leg, the AGV resting on c02
    was asked to make way, and so on. With the rule the run returns."""
    points = [
        ("g0_0", 0.0, 0.0),
        ("g0_1", 0.0, 4.0),
        ("g1_0", 4.0, 0.0),
        ("g1_1", 4.0, 4.0),
        ("c01", 2.0, 0.01),
        ("c02", 0.0, 1.3533333333333333),
        ("c03", 0.0, 2.6966666666666663),
        ("c04", 1.0, 4.04),
        ("c05", 2.0, 4.05),
        ("c06", 3.0, 4.06),
        ("s07", 3.29, 2.2),
        ("s08", 3.36, -1.8),
        ("s09", 3.4299999999999997, -1.8),
        ("s10", 2.3000000000000003, -3.6),
        ("s11", 3.57, -1.8),
        ("s12", 2.4400000000000004, -3.6),
        ("s13", 1.3100000000000005, -5.4),
    ]
    links = [
        ("g0_0", "c01"),
        ("c01", "g1_0"),
        ("g0_0", "c02"),
        ("c02", "c03"),
        ("c03", "g0_1"),
        ("g0_1", "c04"),
        ("c04", "c05"),
        ("c05", "c06"),
        ("c06", "g1_1"),
        ("g1_0", "g1_1"),
        ("g1_1", "s07"),
        ("g1_0", "s08"),
        ("g1_0", "s09"),
        ("s09", "s10"),
        ("g1_0", "s11"),
        ("s11", "s12"),
        ("s12", "s13"),
    ]
    graph, nodes = build(points, links)
    env = Environment(seed=206)
    skus = [SKU(id="s0", weight=1.0, volume=0.1), SKU(id="s1", weight=5.0, volume=0.1)]
    sites = {
        name: Warehouse(
            env=env,
            name=name,
            input_bays=[nodes[node] for node in inputs],
            output_bays=[nodes[node] for node in outputs],
            n_slots=2,
            products=skus,
            initial_inventory={sku: 500 for sku in skus},
            pick_time=pick,
            put_time=put,
        )
        for name, inputs, outputs, pick, put in [
            ("W0", ["g1_1", "c03"], ["c03", "c05", "c04"], 4.087572233010311, 2.1525753034549364),
            ("W1", ["s13"], ["g0_1", "g0_0"], 3.620136737085658, 3.8710510403947316),
            ("W2", ["s10", "s11"], ["s08"], 2.8339490391814226, 2.142061978885512),
        ]
    }
    kind = AGVType(
        name="t",
        speed_profile=Steady(),
        battery_capacity=1e9,
        weight_capacity=50.0,
        volume_capacity=2.0,
        load_time=1.0,
        unload_time=1.0,
        length=3.0,
        low_battery_threshold=0.35,
        critical_battery_threshold=0.02,
        corner_speed=lambda angle: 0.4 if angle > 0.5 else float("inf"),
    )
    starts = {"agv-c": "s07", "agv-e": "c02", "agv-g": "s12"}
    controller = ReservationController(
        env=env,
        graph=graph,
        capacities={nodes["s09"]: 2, nodes["c02"]: 2},
        zones=(
            Zone("Z0", (nodes["c02"],), capacity=1, hold_back=True),
            Zone("Z1", (nodes["s07"], nodes["g1_1"]), capacity=2),
        ),
        derive_corridors=False,
        priority_fn=lambda agv: float(ord(agv.id[-1]) % 3),
    )
    fleet = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=[AGV(env=env, agv_type=kind, agv_id=name, initial_node=nodes[node]) for name, node in starts.items()],
        warehouses=list(sites.values()),
        charging_stations=[ChargingStation(env=env, name="C0", node=nodes["c01"], n_slots=1)],
        traffic_controller=controller,
        repositioning_policy=NearestParkingPolicy(),
        lookahead_margin=2.0,
        default_metrics=False,
    )
    plan = [
        (33.358343711731784, 1, 3, "W2", "W1"),
        (45.123767045916495, 0, 2, "W2", "W0"),
        (56.52421342593519, 0, 1, "W2", "W0"),
        (94.39082497889825, 0, 2, "W0", "W2"),
        (95.95357124882808, 1, 3, "W0", "W1"),
        (127.98471978422121, 1, 3, "W2", "W0"),
        (128.0357659140218, 0, 1, "W2", "W1"),
        (163.39789378019483, 0, 1, "W0", "W2"),
        (175.88048954060568, 1, 3, "W1", "W0"),
        (196.2717215539972, 0, 2, "W1", "W0"),
        (224.79032654852284, 1, 3, "W2", "W0"),
        (226.12947217008522, 0, 3, "W2", "W0"),
        (331.55905753142736, 0, 1, "W0", "W1"),
        (339.5536548922958, 1, 1, "W0", "W2"),
        (379.2489150787415, 1, 1, "W0", "W1"),
        (433.1794211729264, 1, 3, "W2", "W1"),
        (513.7088749845988, 0, 3, "W0", "W1"),
        (538.2031740496097, 0, 3, "W2", "W0"),
        (571.0609561555615, 0, 2, "W2", "W0"),
    ]
    orders: list[TransferOrder] = []

    def submit() -> Any:
        for time, sku, quantity, origin, destination in plan:
            yield env.timeout(time - env.now)
            order = fleet.create_order(
                sku=skus[sku], quantity=quantity, origin=sites[origin], destination=sites[destination]
            )
            orders.append(order)
            fleet.submit(order)

    env.process(submit())
    replay = FullReplay(env, from_snapshot=True)
    TrafficInvariants(env, controller)
    env.run(until=5000.0)
    last = replay.events[-1].t
    assert last < 600.0  # before the rule the moves went on to the end, more than a thousand of them
    assert [order.status for order in orders] == [OrderStatus.COMPLETED] * len(plan)
    assert len([e for e in replay.of(AgvRouted) if e.reason == "make_way"]) == 23
    assert controller._queue == []  # no wait is left open


# --- final review: a blocker that strands wakes the waits it blocks (spec §7.1, §7.10) -------------------------------


class Slow:
    """A speed profile with only ``travel_time``: 0.2 m/s."""

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return distance / 0.2


@dataclass
class Stranding:
    env: Environment
    replay: FullReplay
    nodes: dict[str, Node]
    x: AGV
    w: AGV
    carried: TransferOrder
    later: TransferOrder


def stranding_world(*, moving: bool, detour: bool, policy: StandardTrafficPolicy | None = None) -> Stranding:
    """The control reviewer's reproductions: ``N0 - N1 - N2 - N3`` along a row, with `detour` also ``N0 - D1 - D2 -
    N3``. X carries an order from its bay N1 to N3 with a battery that lasts for one arc; W's order, submitted at 5 s,
    sends it from N0 to N3, the short way through N1 and N2, and back to N0. Either X, 12 m long on arcs of 10 m, leaves
    N1 and strands at N2 at 51 s, or X, without length, strands on N1 at 40 s before it moves; in both it releases
    nothing then. The world is built and not run."""
    points = [("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("N3", 30, 0), ("D1", 0, 10), ("D2", 30, 10)]
    links = [("N0", "N1"), ("N1", "N2"), ("N2", "N3")] + ([("N0", "D1"), ("D1", "D2"), ("D2", "N3")] if detour else [])
    graph, nodes = build(points if detour else points[:4], links)
    env = Environment(debug=True)
    site = {
        name: Warehouse(
            env=env,
            name=name,
            input_bays=[nodes[node]],
            output_bays=[nodes[node]],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: 100},
            pick_time=1.0 if moving else 40.0,
            put_time=1.0,
        )
        for name, node in (("WA", "N1"), ("WB", "N3"), ("WC", "N0"))
    }
    slow = AGVType(
        name="slow",
        speed_profile=Slow(),
        battery_capacity=15.0 if moving else 5.0,
        weight_capacity=50.0,
        volume_capacity=2.0,
        length=12.0 if moving else 0.0,
    )
    x = AGV(env=env, agv_type=slow, agv_id="X", initial_node=nodes["N1"])
    w = AGV(env=env, agv_type=vehicle(battery=1e9), agv_id="W", initial_node=nodes["N0"])
    controller = ReservationController(env=env, graph=graph, policy=policy)
    fleet = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=[x, w],
        warehouses=list(site.values()),
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
        clear_service_nodes=False,
    )
    replay = FullReplay(env, from_snapshot=True)
    TrafficInvariants(env, controller)
    carried = fleet.create_order(sku=SKU_A, quantity=1, origin=site["WA"], destination=site["WB"])
    fleet.submit(carried)
    later = fleet.create_order(sku=SKU_A, quantity=1, origin=site["WB"], destination=site["WC"])

    def submit() -> Any:
        yield env.timeout(5.0)
        fleet.submit(later)

    env.process(submit())
    return Stranding(env, replay, nodes, x, w, carried, later)


@pytest.mark.parametrize("detour", [True, False])
@pytest.mark.parametrize("moving", [True, False], ids=["strands-on-its-way", "strands-on-its-bay"])
def test_a_wait_behind_a_blocker_that_strands_is_reviewed_at_once(moving: bool, detour: bool) -> None:
    """In both reproductions W's wait is asleep after its first review at 35 s (no grant or release since), so before
    the fix nothing woke it: W stayed at N0 with its order dispatched and nothing reported the wait. Now the stranding
    reviews it at once: W reroutes around X and completes its order when the detour exists, and its wait is reported
    stuck at that instant when it does not."""
    s = stranding_world(moving=moving, detour=detour)
    s.env.run()
    stranded_at = 51.0 if moving else 40.0
    (stranded, *_) = [e for e in s.replay.of(AgvStranded) if e.agv == "X"]
    assert (stranded.t, stranded.node, s.x.state) == (stranded_at, "N2" if moving else "N1", AGVState.STRANDED)
    assert s.carried.status is OrderStatus.FAILED
    (started, *_) = [e for e in s.replay.of(TrafficWaitStarted) if e.agv == "W"]
    assert started.t == 5.0  # W stopped at N0 behind X
    routed = [(e.t, e.reason) for e in s.replay.of(AgvRouted) if e.agv == "W"]
    if detour:
        assert routed[:2] == [(5.0, "travel"), (stranded_at, "blocked")]  # rerouted around X when X stranded
        assert s.later.status is OrderStatus.COMPLETED
        assert s.replay.of(TrafficWaitStuck) == []
    else:
        assert routed == [(5.0, "travel")]
        assert [(e.t, e.agv, dict(e.blockers)) for e in s.replay.of(TrafficWaitStuck)] == [
            (stranded_at, "W", {"X": "STRANDED"})
        ]
        assert (s.w.current_node, s.later.status) == (s.nodes["N0"], OrderStatus.DISPATCHED)
    assert s.env.peek() == float("inf")


def test_a_stranding_before_the_first_review_takes_its_timer() -> None:
    """With reviews 100 s apart, W's first review is due at 105 s when X strands on N1 at 40 s. The stranding reviews
    the wait at once and drops the timer: the wait is reported stuck at 40 s, it has had one review, and the run
    returns at 41 s (X's failed order ends then), so nothing fired at 105 s."""
    s = stranding_world(moving=False, detour=False, policy=StandardTrafficPolicy(reroute_after=100.0))
    s.env.run()
    assert [(e.t, e.agv) for e in s.replay.of(TrafficWaitStuck)] == [(40.0, "W")]
    controller = cast(ReservationController, s.w.fleet.traffic_controller)  # type: ignore[union-attr]
    waiting = controller._pending[s.w]
    assert (waiting.reviews, waiting.timer, waiting.dormant) == (1, None, 100.0)
    assert s.env.now == 41.0
