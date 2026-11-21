"""Giving up, bounded traffic re-queues and a quiescent pending loop (spec §8.2, §8.4, §17).

Give-ups are provoked by :class:`Quitter`, a ``ReservationController`` that offers every claim of a travel to the hooks'
``give_up`` and ends the travel with ``GaveUp`` when the coordinator accepts, so that the decision under test is the
coordinator's. Every world runs ``FullReplay`` and ``TrafficInvariants`` (see ``mission_world``).
"""

from __future__ import annotations

from typing import Any

import pytest
import simpy

from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.events import (
    AgvRouted,
    ChargingStarted,
    FleetPendingChanged,
    OrderAssigned,
    OrderStatusChanged,
    TrafficDeadlock,
    TrafficWaitStuck,
)
from simulatte.intralogistics.graph import LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import (
    Claim,
    ClaimRequest,
    GaveUp,
    GiveUp,
    KeepWaiting,
    TrafficView,
    Wait,
    WaitHooks,
)
from simulatte.intralogistics.traffic_policy import StandardTrafficPolicy
from tests.intralogistics.test_idle_traffic import (
    SKU_A,
    Always,
    Mission,
    idle_times,
    lane,
    mission_world,
)
from tests.intralogistics.test_travel import vehicle, world
from tests.intralogistics.traffic_support import build, line


class Quitter(ReservationController):
    """Gives up whenever the coordinator accepts, for the travels whose goal id is in `quits`.

    `asked` lists ``(agv id, goal id, answer of give_up)`` for each claim of such a travel; a refused claim goes on to
    the real controller."""

    ask_hook = True  # a subclass may end travels with GaveUp without asking the coordinator

    def __init__(
        self,
        *args: Any,
        quits: frozenset[str] = frozenset(),
        agvs: frozenset[str] | None = None,
        **options: Any,
    ) -> None:
        super().__init__(*args, **options)
        self.quits = quits
        self.agvs = agvs  # the AGVs that give up; None: all
        self.asked: list[tuple[str, str, bool]] = []

    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
        goal = hooks.route()[-1]
        if goal.id in self.quits and (self.agvs is None or agv.id in self.agvs):
            answer = hooks.give_up() if self.ask_hook else True
            self.asked.append((agv.id, goal.id, answer))
            if answer:
                event = simpy.Event(agv.env)
                event.succeed(GaveUp())
                return ClaimRequest(agv, claim, hooks, event)
        return super().request(agv, claim, hooks)


def quit_world(
    graph_nodes: tuple[LayoutGraph, dict[str, Node]],
    agvs: dict[str, str],
    *goals: str,
    only: frozenset[str] | None = None,
    controller_type: type[Quitter] = Quitter,
    **kw: Any,
) -> Mission:
    options = {"quits": frozenset(goals), "agvs": only}
    return mission_world(graph_nodes, agvs, controller_type=controller_type, controller_options=options, **kw)


def asked(m: Mission) -> list[tuple[str, str, bool]]:
    controller = m.controller
    assert isinstance(controller, Quitter)
    return controller.asked


def reasons(m: Mission, order: TransferOrder) -> list[str]:
    return [e.reason for e in m.events(OrderStatusChanged) if e.order == order.id]


class Snapshots:
    """What an ``on_agv_idle`` hook sees: the AGV, its order and the pending queue at that instant."""

    def __init__(self, m: Mission) -> None:
        self.seen: list[
            tuple[float, AGVState, str | None, OrderStatus, AGV | None, list[str], TransferOrder | None]
        ] = []
        self.m = m
        m.fleet.on_agv_idle(self)

    def __call__(self, agv: AGV) -> None:
        (order,) = self.m.orders
        node = agv.current_node
        queue = [o.id for o in self.m.fleet._pending_queue]
        self.seen.append(
            (
                float(self.m.env.now),
                agv.state,
                None if node is None else node.id,
                order.status,
                order.assigned_agv,
                queue,
                agv.order,
            )
        )


# --- giving up before pickup (spec §8.2, T24) -----------------------------------------------------------------------


@pytest.mark.parametrize("limit", [None, 0, 1])
def test_an_order_is_requeued_for_traffic_at_most_max_traffic_requeues_times(limit: int | None) -> None:
    options = {} if limit is None else {"max_traffic_requeues": limit}
    bound = 3 if limit is None else limit
    m = quit_world(lane(), {"A": "M"}, "S", bays={"WH-S": "S", "WH-D": "D"}, **options)
    order = m.order("WH-S", "WH-D")
    snapshots = Snapshots(m)
    m.env.run()

    assert asked(m) == [("A", "S", True)] * bound + [("A", "S", False)]  # the next one is refused
    assert reasons(m, order).count("traffic_requeued") == bound
    assert order.status is OrderStatus.COMPLETED  # after the last refusal the AGV waits for its claim and goes on
    # PENDING, unassigned and queued, the AGV idle in place and without the order; the pending-queue check of the
    # instant does not offer the order back, so each new attempt is the next retry tick (spec §8.2)
    for tick, seen in enumerate(snapshots.seen[:bound]):
        assert seen == (float(tick), AGVState.IDLE, "M", OrderStatus.PENDING, None, [order.id], None)
    statuses = [e.status for e in m.events(OrderStatusChanged) if e.order == order.id]
    assert statuses[:3] == (
        ["DISPATCHED", "PENDING", "DISPATCHED"] if bound else ["DISPATCHED", "PICKING", "IN_TRANSIT"]
    )


def test_a_give_up_through_the_standard_machinery_requeues_and_the_run_returns() -> None:
    """A blocked unloaded travel with a policy that gives up: the real controller, the real wait, the real hook."""

    class GivesUp(StandardTrafficPolicy):
        def on_blocked(self, wait: Wait, view: TrafficView) -> tuple[Any, ...]:
            return (GiveUp(), KeepWaiting())

    graph_nodes = line(3)
    m = mission_world(
        graph_nodes,
        {"A": "N0", "B": "N1"},
        bays={"WH-0": "N0", "WH-2": "N2"},
        controller_options={"policy": GivesUp()},
        dispatch_strategy=_OnlyA(),
    )
    order = m.order("WH-2", "WH-0")
    m.env.run(until=50)  # the custom strategy keeps polling; the order is requeued three times, then waits
    assert reasons(m, order).count("traffic_requeued") == 3
    assert order.status is OrderStatus.DISPATCHED
    assert (m.agvs["A"].state, m.agvs["A"].current_node) == (AGVState.TRAVELING_EMPTY, m.nodes["N0"])


class _OnlyA:
    """A custom dispatch strategy that sends every order to the AGV ``A`` when it is idle."""

    def select(self, order: TransferOrder, fleet: Any, graph: Any) -> AGV | None:
        return next((agv for agv in fleet if agv.id == "A" and agv.state is AGVState.IDLE), None)


def charge_lane() -> tuple[LayoutGraph, dict[str, Node]]:
    """``M`` and ``D`` 10 m apart, with the charger node ``CH`` 4 m from ``M``."""
    return build([("M", 0, 0), ("D", 10, 0), ("CH", 0, 4)], [("M", "D"), ("M", "CH")])


def test_a_charging_diversion_before_pickup_is_part_of_the_order_mission() -> None:
    """A at M cannot cover the 10 m to the origin bay on 8 units but reaches the charger 4 m away."""
    m = quit_world(
        charge_lane(),
        {"A": "M"},
        "CH",
        bays={"WH-D": "D", "WH-M": "M"},
        chargers={"CS": ("CH", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 8.0},
    )
    order = m.order("WH-D", "WH-M")
    snapshots = Snapshots(m)
    m.env.run()
    assert asked(m) == [("A", "CH", True)] * 3 + [("A", "CH", False)]
    assert reasons(m, order).count("traffic_requeued") == 3
    for tick, seen in enumerate(snapshots.seen[:3]):  # one attempt per retry tick
        assert seen == (float(tick), AGVState.IDLE, "M", OrderStatus.PENDING, None, [order.id], None)
    assert len(m.events(ChargingStarted)) == 1  # the fourth time the AGV charges
    assert order.status is OrderStatus.COMPLETED


def test_a_charging_diversion_after_a_critical_arrival_gives_up_too() -> None:
    """A arrives critical at M, which is not its goal, and the mission charges first: that travel gives up."""
    graph_nodes = build([("S", 0, 0), ("M", 10, 0), ("D", 20, 0), ("CH", 10, 2)], [("S", "M"), ("M", "D"), ("M", "CH")])
    m = quit_world(
        graph_nodes,
        {"A": "S"},
        "CH",
        bays={"WH-D": "D", "WH-S": "S"},
        chargers={"CS": ("CH", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 14.0},
    )
    order = m.order("WH-D", "WH-S")
    snapshots = Snapshots(m)
    m.env.run()
    assert asked(m) == [("A", "CH", True)] * 3 + [("A", "CH", False)]
    assert snapshots.seen[0][1:4] == (AGVState.IDLE, "M", OrderStatus.PENDING)  # idle where it stopped, not at S
    assert reasons(m, order).count("traffic_requeued") == 3
    assert order.status is OrderStatus.COMPLETED


# --- refusals ---------------------------------------------------------------------------------------------------


def test_an_agv_with_cargo_does_not_give_up_a_delivery() -> None:
    m = quit_world(lane(), {"A": "S"}, "D", bays={"WH-S": "S", "WH-D": "D"})
    order = m.order("WH-S", "WH-D")
    m.env.run()
    assert asked(m) == [("A", "D", False)]
    assert order.status is OrderStatus.COMPLETED
    assert "traffic_requeued" not in reasons(m, order)


def test_an_agv_with_cargo_does_not_give_up_a_charging_diversion() -> None:
    """The diversion travels unloaded, but the cargo is on board: 8 units do not cover the loaded 10 m to D."""
    m = quit_world(
        charge_lane(),
        {"A": "M"},
        "CH",
        bays={"WH-M": "M", "WH-D": "D"},
        chargers={"CS": ("CH", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 8.0},
    )
    order = m.order("WH-M", "WH-D")
    m.env.run()
    assert asked(m) == [("A", "CH", False)]
    assert len(m.events(ChargingStarted)) == 1
    assert order.status is OrderStatus.COMPLETED
    assert "traffic_requeued" not in reasons(m, order)


# --- travels with no order --------------------------------------------------------------------------------------


def test_a_repositioning_travel_that_gives_up_ends_idle_in_place(caplog: pytest.LogCaptureFixture) -> None:
    m = quit_world(lane(), {"A": "S"}, "END", bays={"WH-S": "S", "WH-D": "D"})
    m.fleet._repositioning_policy = Always(m.nodes["END"])
    order = m.order("WH-S", "WH-D")
    m.env.run()
    a = m.agvs["A"]
    assert asked(m) == [("A", "END", True)]
    assert (a.state, a.current_node, order.status) == (AGVState.IDLE, m.nodes["D"], OrderStatus.COMPLETED)
    assert [r for r in caplog.records if r.levelname in {"WARNING", "ERROR"}] == []  # giving up is no failure
    assert m.routed("A")[-1] == ("travel", "D", "END")


def test_a_clearing_travel_that_gives_up_ends_idle_in_place() -> None:
    m = quit_world(lane(), {"A": "S"}, "FAR", bays={"WH-S": "S", "WH-D": "D"})
    m.order("WH-S", "WH-D")
    m.env.run()
    a = m.agvs["A"]
    assert asked(m) == [("A", "FAR", True)]
    assert (a.state, a.current_node) == (AGVState.IDLE, m.nodes["D"])
    assert m.routed("A")[-1] == ("clear_node", "D", "FAR")


def test_a_charging_travel_after_the_mission_that_gives_up_ends_idle_in_place() -> None:
    m = quit_world(
        lane(),
        {"A": "S"},
        "FAR",
        bays={"WH-S": "S", "WH-D": "D"},
        chargers={"CS": ("FAR", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 38.0},  # low after the delivery
    )
    m.order("WH-S", "WH-D")
    m.env.run()
    a = m.agvs["A"]
    assert asked(m) == [("A", "FAR", True)]
    assert (a.state, a.current_node) == (AGVState.IDLE, m.nodes["D"])  # not moved on to clear the bay either
    assert m.events(ChargingStarted) == []
    assert "clear_node" not in [reason for reason, _, _ in m.routed("A")]


class QuittingX(StandardTrafficPolicy):
    """The AGV ``X`` gives up when it waits; it is asked to make way to `target`, the others to the standard search."""

    def __init__(self, target: Node) -> None:
        super().__init__()
        self.target = target

    def on_blocked(self, wait: Wait, view: TrafficView) -> tuple[Any, ...]:
        return (GiveUp(), KeepWaiting()) if wait.agv.id == "X" else (KeepWaiting(),)

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        return self.target if blocker.id == "X" else None


def test_a_make_way_move_that_gives_up_at_once_is_not_repeated_at_the_same_instant() -> None:
    """W waits for X, which makes way towards N3 but is blocked by Y at N2 and gives up on the spot. X idles where it
    started and still blocks W: without the guard of spec §7.10 the idle notification would ask it again, endlessly."""
    graph, nodes = line(4)
    w = world((graph, nodes), {"W": "N0", "X": "N1", "Y": "N2"}, make_way=True, policy=QuittingX(nodes["N3"]))
    w.go("W", "N1")
    w.env.run()  # returns: nothing repeats
    x = w.agvs["X"]
    assert (x.state, x.current_node) == (AGVState.IDLE, nodes["N1"])
    assert [e.reason for e in w.of(AgvRouted, agv="X")] == ["make_way"]  # one move only
    assert float(w.env.now) == 0.0
    assert [e.agv for e in w.of(TrafficWaitStuck)] == ["W"]  # the wait of W is reported, once


# --- termination (spec §8.4, T23) ---------------------------------------------------------------------------------


def swap() -> Mission:
    """Two AGVs on ``line(2)`` that pick up at their own end and carry to the other: a deadlock with no way out. A third
    order finds no idle AGV."""
    m = mission_world(line(2), {"A": "N0", "B": "N1"}, bays={"W0": "N0", "W1": "N1"})
    m.order("W1", "W0")  # nearest idle AGV: B
    m.order("W0", "W1")  # then A
    return m


def test_two_agvs_swapping_on_a_line_leave_one_unresolved_deadlock_and_the_run_returns() -> None:
    m = swap()
    m.env.run()
    (deadlock,) = m.events(TrafficDeadlock)
    assert (deadlock.agvs, deadlock.victim, deadlock.action) == (("A", "B"), None, "unresolved")
    assert [o.status for o in m.orders] == [OrderStatus.IN_TRANSIT, OrderStatus.IN_TRANSIT]  # none failed


def test_a_serviceable_pending_order_does_not_keep_the_run_alive() -> None:
    m = swap()
    third = m.order("W1", "W0")  # both AGVs are busy
    m.env.run()
    assert third.status is OrderStatus.PENDING  # still waiting, not failed
    assert m.fleet.pending_count == 1
    assert OrderStatus.FAILED not in [o.status for o in m.orders]
    assert m.fleet._pending_retry_scheduled is False
    assert len(m.events(TrafficDeadlock)) == 1


def test_a_loaded_agv_stuck_behind_an_idle_one_that_cannot_move_leaves_one_stuck_wait_and_the_run_returns() -> None:
    """spec §17: ``N0 - N1 - N2`` with a bay at each end, capacity 1: B idles on N1 with no node to move to."""
    m = mission_world(line(3), {"A": "N0", "B": "N1"}, bays={"W0": "N0", "W2": "N2"})
    order = m.order("W0", "W2")
    m.env.run()
    (stuck,) = m.events(TrafficWaitStuck)
    assert stuck.agv == "A"
    assert order.status is OrderStatus.IN_TRANSIT


def diamond() -> tuple[LayoutGraph, dict[str, Node]]:
    """Two parallel chains ``J, a1, b1, K`` and ``J, a2, b2, K`` between the bays ``s`` (off ``J``) and ``t`` (off
    ``K``)."""
    return build(
        [("s", -10, 0), ("J", 0, 0), ("a1", 10, 10), ("b1", 20, 10), ("a2", 10, -10), ("b2", 20, -10)]
        + [("K", 30, 0), ("t", 40, 0)],
        [("s", "J"), ("J", "a1"), ("a1", "b1"), ("b1", "K"), ("J", "a2"), ("a2", "b2"), ("b2", "K"), ("K", "t")],
    )


@pytest.mark.parametrize("corridors", [False, True], ids=["plain", "corridors"])
def test_a_travel_between_standing_blockers_is_rerouted_at_most_max_resolutions_times(corridors: bool) -> None:
    """spec §7.9: stranded AGVs on b1 and b2; the loaded AGV is rerouted between the two chains until its resolutions
    reach the bound, then waits, reported stuck once, and the run returns."""
    m = mission_world(
        diamond(),
        {"X": "s", "S1": "b1", "S2": "b2"},
        bays={"A": "s", "B": "t"},
        agv_type=vehicle(battery=1e7),
        controller_options={"derive_corridors": corridors},
    )
    for name in ("S1", "S2"):
        m.fleet._transition_agv(m.agvs[name], AGVState.STRANDED)
    order = m.order("A", "B", at=1.0)
    m.env.run()
    rerouted = [reason for reason, _, _ in m.routed("X") if reason == "blocked"]
    assert len(rerouted) == StandardTrafficPolicy().max_resolutions
    (stuck,) = m.events(TrafficWaitStuck)
    assert (stuck.agv, dict(stuck.blockers)) == ("X", {"S1": "STRANDED"})
    assert m.agvs["X"].current_node == m.nodes["a1"]
    assert order.status is OrderStatus.IN_TRANSIT
    assert m.env.peek() == float("inf")


def test_no_retry_timer_while_a_serviceable_order_waits_and_dispatch_resumes_when_an_agv_is_idle() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"})
    first, second = m.order("WH-S", "WH-D"), m.order("WH-S", "WH-D")
    checks: list[float] = []
    original = m.fleet._check_pending_queue

    def check() -> None:
        checks.append(float(m.env.now))
        original()

    m.fleet._check_pending_queue = check  # type: ignore[method-assign]
    m.env.run(until=1.5)
    assert second.status is OrderStatus.PENDING
    assert m.fleet._pending_retry_scheduled is False
    m.env.run()
    assert (first.status, second.status) == (OrderStatus.COMPLETED, OrderStatus.COMPLETED)
    assert set(checks) <= set(idle_times(m, "A"))  # the pending queue was looked at only when the AGV became idle


def test_an_unserviceable_order_still_exhausts_its_retries_while_a_serviceable_one_waits() -> None:
    m = mission_world(lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, max_dispatch_retries=3)
    first, second = m.order("WH-S", "WH-D"), m.order("WH-S", "WH-D")
    too_heavy = m.fleet.create_order(
        sku=first.sku, quantity=1000, origin=m.warehouses["WH-S"], destination=m.warehouses["WH-D"]
    )
    m.at(0.0, lambda: m.fleet.submit(too_heavy))
    checks: list[float] = []
    original = m.fleet._check_pending_queue

    def check() -> None:
        checks.append(float(m.env.now))
        original()

    m.fleet._check_pending_queue = check  # type: ignore[method-assign]
    m.env.run()
    failed = [e for e in m.events(OrderStatusChanged) if e.order == too_heavy.id and e.status == "FAILED"]
    assert [(e.t, e.reason) for e in failed] == [(3.0, "retries_exhausted")]
    assert (first.status, second.status) == (OrderStatus.COMPLETED, OrderStatus.COMPLETED)
    ticks = [t for t in checks if t not in idle_times(m, "A")]
    assert ticks == [1.0, 2.0, 3.0]  # the loop stops with the last unserviceable order


def test_an_order_an_idle_agv_cannot_reach_still_exhausts_its_retries() -> None:
    graph_nodes = build([("S", 0, 0), ("D", 10, 0), ("Z", 50, 50)], [("S", "D")])
    m = mission_world(graph_nodes, {"A": "Z"}, bays={"WH-S": "S", "WH-D": "D"}, max_dispatch_retries=3)
    order = m.order("WH-S", "WH-D")
    m.env.run()
    assert (order.status, m.env.now) == (OrderStatus.FAILED, 3.0)
    assert [e.reason for e in m.events(OrderStatusChanged) if e.order == order.id][-1] == "retries_exhausted"


class _Never:
    """A custom dispatch strategy that never selects an AGV."""

    def select(self, order: TransferOrder, fleet: Any, graph: Any) -> AGV | None:
        return None


def test_a_custom_dispatch_strategy_keeps_the_polling_loop() -> None:
    m = mission_world(
        lane(), {"A": "S"}, bays={"WH-S": "S", "WH-D": "D"}, dispatch_strategy=_Never(), max_dispatch_retries=3
    )
    order = m.order("WH-S", "WH-D")
    m.env.run(until=1.5)
    assert m.fleet._pending_retry_scheduled is True
    m.env.run()
    assert (order.status, m.env.now) == (OrderStatus.FAILED, 3.0)


# --- fix round 1: the AGV that gave up makes way before the order is offered again ------------------------------------


def head_on() -> tuple[LayoutGraph, dict[str, Node]]:
    """``N0 - N1 - N2 - N3`` along a row with the side node ``SIDE`` off ``N1``."""
    return build(
        [("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("N3", 30, 0), ("SIDE", 10, 10)],
        [("N0", "N1"), ("N1", "N2"), ("N2", "N3"), ("N1", "SIDE")],
    )


def test_a_deadlock_give_up_frees_the_way_before_the_order_is_offered_again() -> None:
    """A carries a load from N3 to N0 and B, unloaded, goes to N3 for the same pickup: head on, with no resolution left
    but giving up. B gives up once, at the first give-up instant, makes way to SIDE, and A delivers."""
    m = mission_world(
        head_on(),
        {"A": "N3", "B": "N0"},
        bays={"W3": "N3", "W0": "N0"},
        controller_options={"policy": StandardTrafficPolicy(max_resolutions=0)},
    )
    first = m.order("W3", "W0")
    second = m.order("W3", "W0")
    m.env.run()  # returns
    requeued = [e for e in m.events(OrderStatusChanged) if e.reason == "traffic_requeued"]
    assert len(requeued) == 1  # not spent three times at one instant
    (deadlock,) = [e for e in m.events(TrafficDeadlock) if e.action == "give_up"]
    assert (deadlock.victim, deadlock.t) == ("B", requeued[0].t)
    (way,) = [e for e in m.events(AgvRouted, agv="B") if e.reason == "make_way"]
    assert (way.t, way.origin) == (requeued[0].t, "N0")  # B, idle where it stopped, makes way at that instant
    assert first.status is OrderStatus.COMPLETED  # A delivered
    assert second.status is OrderStatus.COMPLETED  # and the order went to an AGV later


def test_the_requeued_order_goes_to_another_idle_agv_at_once() -> None:
    m = quit_world(
        lane(),
        {"A": "M", "C": "SIDE"},
        "S",
        only=frozenset({"A"}),
        bays={"WH-S": "S", "WH-D": "D"},
    )
    order = m.order("WH-S", "WH-D")
    log: list[str] = []
    controller, fleet = m.controller, m.fleet
    idle, check = controller.idle, fleet._check_pending_queue
    controller.idle = lambda agv: (log.append(f"idle:{agv.id}"), idle(agv))[-1]  # type: ignore[method-assign]
    fleet._check_pending_queue = lambda: (log.append("check"), check())[-1]  # type: ignore[method-assign]
    m.env.run()
    assigned = [(e.agv, e.t) for e in m.events(OrderAssigned) if e.order == order.id]
    assert assigned[:2] == [("A", 0), ("C", 0)]  # the same instant, the other AGV
    assert log[:2] == ["idle:A", "check"]  # A is told first, then the pending queue is checked
    assert "traffic_requeued" in reasons(m, order)
    assert m.agvs["A"].state is AGVState.IDLE
    assert order.status is OrderStatus.COMPLETED


def test_the_requeued_order_waits_behind_another_pending_one_which_the_agv_may_take_at_once() -> None:
    m = quit_world(
        lane(),
        {"A": "M"},
        "S",
        bays={"WH-S": "S", "WH-D": "D", "WH-F": "FAR"},
        only=frozenset({"A"}),
    )
    first = m.fleet.create_order(sku=SKU_A, quantity=1, origin=m.warehouses["WH-S"], destination=m.warehouses["WH-D"])
    second = m.fleet.create_order(sku=SKU_A, quantity=1, origin=m.warehouses["WH-F"], destination=m.warehouses["WH-D"])
    m.orders += [first, second]
    # One step: the first order takes the only AGV, the second finds none and is pending when the first gives up.
    m.at(0.0, lambda: (m.fleet.submit(first), m.fleet.submit(second)))
    dispatched: list[tuple[float, str, list[str]]] = []
    m.fleet.on_order_dispatched(
        lambda o, a: dispatched.append((float(m.env.now), o.id, [p.id for p in m.fleet._pending_queue]))
    )
    m.env.run()
    added = [(e.order, e.index) for e in m.events(FleetPendingChanged) if e.op == "added"]
    assert added[:2] == [(second.id, 0), (first.id, 1)]  # pending first, then the re-queued order behind it
    # at the instant of the give-up the AGV takes the other order, the re-queued one waits behind it
    assert [(t, o) for t, o, _ in dispatched[:2]] == [(0.0, first.id), (0.0, second.id)]
    assert dispatched[1][2] == [second.id, first.id]
    assert (first.status, second.status) == (OrderStatus.COMPLETED, OrderStatus.COMPLETED)
    later = [t for t, o, _ in dispatched if o == first.id]
    assert later[1] > 0.0  # the re-queued order goes to the same AGV again only at a later time


# --- fix round 1: a give-up with cargo on board is a controller error ------------------------------------------------


class Bypass(Quitter):
    """Ends the travels it is told to with ``GaveUp`` without asking the coordinator."""

    ask_hook = False


def test_a_loaded_travel_ended_with_gave_up_is_a_controller_error() -> None:
    m = quit_world(lane(), {"A": "S"}, "D", bays={"WH-S": "S", "WH-D": "D"}, controller_type=Bypass)
    order = m.order("WH-S", "WH-D")
    with pytest.raises(RuntimeError, match=r"Bypass.*A.*cargo"):
        m.env.run()
    assert order.status is not OrderStatus.FAILED  # not mapped like a missing route


def test_a_charging_diversion_with_cargo_after_a_critical_arrival_is_a_controller_error() -> None:
    """A arrives critical at M with its load, M is not its goal, and the mission charges first: that travel carries
    cargo, so ending it with ``GaveUp`` is an error, as for any other loaded travel."""
    graph_nodes = build([("S", 0, 0), ("M", 10, 0), ("D", 12, 0), ("CH", 10, 2)], [("S", "M"), ("M", "D"), ("M", "CH")])
    m = quit_world(
        graph_nodes,
        {"A": "S"},
        "CH",
        bays={"WH-S": "S", "WH-D": "D"},
        chargers={"CS": ("CH", 1)},
        agv_type=vehicle(battery=100.0),
        battery={"A": 14.0},
        controller_type=Bypass,
    )
    order = m.order("WH-S", "WH-D")
    with pytest.raises(RuntimeError, match=r"Bypass.*A.*cargo"):
        m.env.run()
    assert order.status is not OrderStatus.FAILED
    assert m.events(ChargingStarted) == []
