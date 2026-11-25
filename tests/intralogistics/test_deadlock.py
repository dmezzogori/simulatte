"""``ReservationController`` waits (spec §7.1, §11.2), deadlock detection (§7.8), policy calls and reviews (§7.9),
idle blockers (§7.10) and ``StandardTrafficPolicy``.

Every test builds its world with :func:`world`, which runs :class:`FullReplay` and :class:`TrafficInvariants` over
every event. Tests drive the controller directly with hand-made claims of node ids; hooks accept or refuse to script
each case, and a scripted policy records what the controller asks of it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from simpy.events import Timeout

from simulatte._wire import FrozenMap, freeze
from simulatte.environment import Environment
from simulatte.events import Deltas
from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.events import (
    AgvMoveStarted,
    FleetAgvAdded,
    TrafficDeadlock,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.graph import LayoutGraph, Node
from simulatte.intralogistics.network import Claim, ClaimItem, Segment
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import (
    Blocker,
    ClaimRequest,
    Disposition,
    FreeFlowController,
    GaveUp,
    GiveUp,
    KeepWaiting,
    Reaction,
    Reroute,
    Rerouted,
    StepAside,
    SteppedAside,
    TrafficView,
    Wait,
    disposition_of,
)
from simulatte.intralogistics.traffic_policy import StandardTrafficPolicy
from tests.intralogistics.test_reservation import cart
from tests.intralogistics.traffic_support import FakeHooks, FullReplay, TrafficInvariants, build, fake_hooks, line
from tests.intralogistics.traffic_support import World as Declared

ACTIONS = ("try_reroute", "try_step_aside", "give_up")


def hooks(
    log: list[tuple[str, str, tuple[Any, ...]]] | None = None,
    *,
    name: str = "",
    route: Sequence[Node] = (),
    loaded: bool = False,
    resolutions: int = 0,
    accept: Sequence[str] = (),
) -> FakeHooks:
    """Fake hooks whose actions in `accept` succeed; every action call is also appended to `log` with `name`."""
    made = fake_hooks(route, loaded, resolutions)
    for action in ACTIONS:

        def call(*args: Any, action: str = action) -> bool:
            made.calls.append((action, args))
            if log is not None:
                log.append((name, action, args))
            return action in accept

        setattr(made, action, call)
    return made


@dataclass
class StubFleet:
    """What the controller reads of a fleet, with a recording ``make_way`` that starts a move when it accepts."""

    id: str
    graph: LayoutGraph
    fleet: tuple[AGV, ...] = ()
    parking_areas: tuple[Any, ...] = ()
    charging_stations: tuple[Any, ...] = ()
    accept: bool = True
    moves: list[tuple[str, Node | None, tuple[str, ...]]] = field(default_factory=list)

    def make_way(self, agv: AGV, target: Node | None, *, concerned: Sequence[AGV] = ()) -> bool:
        self.moves.append((agv.id, target, tuple(other.id for other in concerned)))
        if self.accept:
            agv.transition_to(AGVState.TRAVELING_EMPTY)
        return self.accept


class Scripted:
    """A policy returning scripted reactions and recording its calls; deadlocks fall back to the standard policy."""

    def __init__(
        self,
        blocked: Sequence[Reaction] | Callable[[Wait], Sequence[Reaction]] = (KeepWaiting(),),
        deadlock: Callable[[Sequence[Wait]], Sequence[tuple[AGV, Reaction]]] | None = None,
        target: Node | None = None,
    ) -> None:
        self.blocked = blocked
        self.deadlock = deadlock
        self.target = target
        self.calls: list[tuple[str, Any]] = []

    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]:
        self.calls.append(("on_blocked", wait))
        return self.blocked(wait) if callable(self.blocked) else self.blocked

    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]:
        self.calls.append(("on_deadlock", tuple(waits)))
        if self.deadlock is None:
            return StandardTrafficPolicy().on_deadlock(waits, view)
        return self.deadlock(waits)

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        self.calls.append(("make_way_target", (blocker.id, wait.agv.id)))
        return self.target

    def of(self, name: str) -> list[Any]:
        return [args for called, args in self.calls if called == name]


@dataclass
class World:
    env: Environment
    controller: ReservationController
    nodes: dict[str, Node]
    agvs: dict[str, AGV]
    fleets: dict[str, StubFleet]
    replay: FullReplay
    invariants: TrafficInvariants
    logs: list[Any]

    def item(self, resource: Any) -> ClaimItem:
        if isinstance(resource, str):
            return ClaimItem(self.nodes[resource])
        if isinstance(resource, tuple):
            return ClaimItem(*resource)
        return ClaimItem(resource)

    def ask(self, agv: str, *resources: Any, with_hooks: FakeHooks | None = None) -> ClaimRequest:
        """Request a hand-made claim of node ids, resources or ``(resource, direction)`` pairs.

        The default hooks route the AGV from its node to the first node of the claim (or stay where it is).
        """
        vehicle = self.agvs[agv]
        items = tuple(self.item(resource) for resource in resources)
        if with_hooks is None:
            first = next((item.resource for item in items if isinstance(item.resource, Node)), None)
            here = vehicle.current_node
            assert here is not None
            with_hooks = hooks(name=agv, route=(here,) if first is None else (here, first))
        return self.controller.request(vehicle, Claim(items, 1), with_hooks)

    def wait(self, agv: str, *resources: Any, with_hooks: FakeHooks | None = None) -> ClaimRequest:
        """Request the claim, check that it pends, and tell the controller the AGV stopped."""
        request = self.ask(agv, *resources, with_hooks=with_hooks)
        assert not request.event.triggered, agv
        self.controller.stopped(request)
        return request

    def seg(self, a: str, b: str) -> Segment:
        return self.controller.network.segment(self.nodes[a], self.nodes[b])

    def leave(self, agv: str, to: str) -> None:
        """Take `to`, move there and release the node the AGV left."""
        vehicle = self.agvs[agv]
        here = vehicle.current_node
        assert self.ask(agv, to).granted
        vehicle.current_node = self.nodes[to]
        self.controller.release(vehicle, [here])

    def release(self, agv: str, *resources: Any) -> None:
        found = [self.nodes[r] if isinstance(r, str) else r for r in resources]
        self.controller.release(self.agvs[agv], found)

    def state(self, agv: str, state: AGVState) -> None:
        self.agvs[agv].transition_to(state)

    def stall(self, agv: str) -> None:
        """Give the AGV a stalled motion, as the coordinator does for a segment with a non-finite travel time."""
        vehicle = self.agvs[agv]
        here = vehicle.current_node
        assert here is not None
        motion = cast("FrozenMap", freeze({"from": here.id, "to": here.id, "t_start": 0.0, "stalled": True}))
        vehicle.motion = motion
        self.env.emit(
            AgvMoveStarted(
                agv=vehicle.id,
                from_node=here.id,
                to_node=here.id,
                t_end=math.inf,
                motion=FrozenMap({}),
                loaded=False,
                deltas=Deltas.build().set(vehicle.id, "motion", motion).done(),
            )
        )

    def waits(self) -> list[tuple[str, str, str]]:
        """``(type, agv, reason)`` of every wait event."""
        return [(e.type_name, e.agv, e.reason) for e in self.replay.of(TrafficWaitStarted, TrafficWaitEnded)]

    def deadlocks(self) -> list[tuple[tuple[str, ...], str | None, str]]:
        return [(e.agvs, e.victim, e.action) for e in self.replay.of(TrafficDeadlock)]

    def logged(self, level: str) -> list[tuple[str, str | None]]:
        return [(e.message, e.component) for e in self.logs if e.level == level]


def world(
    agvs: dict[str, str | tuple[str, str]],
    *,
    shape: Callable[[Environment], Declared] | None = None,
    refuse: Sequence[str] = (),
    **options: Any,
) -> World:
    """An activated world on the graph `shape` declares on the environment's layout (default ``line(5)``), with the
    capacities and zones of the network: `agvs` maps each AGV id to its start node id, or to ``(node id, fleet id)``
    for an AGV of a stub fleet (fleets in `refuse` refuse ``make_way``). The layout binds every node; ``World.nodes``
    holds the canonical nodes."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    declared = line(env, 5) if shape is None else shape(env)
    controller = ReservationController(env=env, graph=declared.graph, derive_corridors=False, **options)
    vehicles: dict[str, AGV] = {}
    fleets: dict[str, StubFleet] = {}
    members: dict[str, list[AGV]] = {}
    for name, start in agvs.items():
        node, fleet_id = (start, "") if isinstance(start, str) else start
        agv = vehicles[name] = AGV(env=env, agv_type=cart(), agv_id=name, initial_node=declared.ref(node))
        members.setdefault(fleet_id, []).append(agv)
    graph = declared.canonical_graph()  # prepares the layout: the stub fleets' graph is the resolved one
    nodes = {node.id: node for node in graph.nodes}
    for fleet_id, group in members.items():
        fleet = StubFleet(fleet_id or "F", graph, tuple(group), accept=fleet_id not in refuse)
        controller.register_fleet(fleet)  # type: ignore[arg-type]
        if fleet_id:
            fleets[fleet_id] = fleet
            for agv in group:
                agv.fleet = fleet  # type: ignore[assignment]
                env.emit(
                    FleetAgvAdded(
                        fleet=fleet.id, agv=agv.id, deltas=Deltas.build().set(agv.id, "fleet", fleet.id).done()
                    )
                )
    invariants = TrafficInvariants(env, controller)
    logs: list[Any] = []
    env.bus.subscribe(lambda event: logs.append(event) if event.type_name == "log" else None, "**")
    env.on_activate(lambda: [controller.place(agv, agv.current_node) for agv in vehicles.values()])
    env.activate()
    return World(env, controller, nodes, vehicles, fleets, replay, invariants, logs)


def timers(w: World) -> list[float]:
    """The times of the scheduled timeouts: the reviews (request events succeed without a delay)."""
    return sorted(t for t, _, _, event in w.env._queue if isinstance(event, Timeout))


def run_to(w: World, until: float) -> None:
    w.env.run(until=until)


# --- waits (spec §7.1, §11.2) ------------------------------------------------------------------------------


class TestWaits:
    def test_stopped_emits_wait_started_and_a_grant_ends_it(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted())
        request = w.wait("B", (w.seg("N0", "N1"), "forward"), "N1")
        (started,) = w.replay.of(TrafficWaitStarted)
        assert (started.agv, started.node, started.resources, started.blockers, started.reason) == (
            "B",
            "N1",
            ("N0~N1", "N1"),
            ("A",),
            "occupied",
        )
        w.leave("A", "N2")
        assert request.granted
        tail = [(e.type_name, e.agv) for e in w.replay.events[-3:]]
        assert tail == [("traffic.released", "A"), ("traffic.wait_ended", "B"), ("traffic.reserved", "B")]
        ended = w.replay.of(TrafficWaitEnded)[-1]
        assert (ended.node, ended.reason) == ("N1", "granted")
        assert w.invariants.checked > 0

    def test_the_wait_node_is_the_current_node_for_a_one_node_route(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted())
        w.wait("B", "N1", with_hooks=hooks(route=(w.nodes["N0"],)))
        assert w.replay.of(TrafficWaitStarted)[0].node == "N0"

    def test_resources_are_those_the_agv_does_not_hold(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted())
        w.wait("B", "N0", "N1")
        assert w.replay.of(TrafficWaitStarted)[0].resources == ("N1",)

    def test_withdraw_ends_an_open_wait_as_interrupted(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted())
        request = w.wait("B", "N1")
        w.controller.withdraw(request)
        assert w.waits() == [("traffic.wait_started", "B", "occupied"), ("traffic.wait_ended", "B", "interrupted")]
        assert not request.event.triggered

    @pytest.mark.parametrize("granted", [True, False], ids=["granted", "withdrawn"])
    def test_a_request_never_stopped_emits_no_wait_event(self, granted: bool) -> None:
        policy = Scripted()
        w = world({"A": "N1", "B": "N0"}, policy=policy)
        request = w.ask("B", "N1")
        if granted:
            w.leave("A", "N2")
            assert request.granted
        else:
            w.controller.withdraw(request)
        assert w.waits() == []
        assert policy.calls == []

    def test_stopped_on_a_granted_or_stopped_request_does_nothing(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted())
        granted = w.ask("B", "N2")
        w.controller.stopped(granted)
        waiting = w.wait("B", "N1")
        w.controller.stopped(waiting)
        assert w.waits() == [("traffic.wait_started", "B", "occupied")]

    def test_blockers_by_claim_order_then_grant_order(self) -> None:
        policy = Scripted()
        w = world(
            {"D": "N1", "A": "N1", "C": "N2", "B": "N3"},
            shape=lambda env: line(env, 5, capacities={"N1": 2}),
            policy=policy,
        )
        w.wait("B", "N2", "N1")
        (wait,) = policy.of("on_blocked")
        assert [(b.agv.id, [r.id for r in b.resources]) for b in wait.blockers] == [
            ("C", ["N2"]),
            ("D", ["N1"]),
            ("A", ["N1"]),
        ]
        assert w.replay.of(TrafficWaitStarted)[0].blockers == ("C", "D", "A")

    def test_the_wait_carries_the_hooks_and_the_priority_read_at_the_request(self) -> None:
        policy = Scripted()
        w = world({"A": "N1", "B": "N0"}, policy=policy, priority_fn=lambda agv: 2.5)
        made = hooks(route=(w.nodes["N0"], w.nodes["N1"]), loaded=True, resolutions=3)
        request = w.wait("B", "N1", with_hooks=made)
        (wait,) = policy.of("on_blocked")
        assert wait == Wait(
            agv=w.agvs["B"],
            claim=request.claim,
            since=0.0,
            blockers=(Blocker(w.agvs["A"], Disposition.IDLE, (w.nodes["N1"],)),),
            reviews=0,
            route=(w.nodes["N0"], w.nodes["N1"]),
            loaded=True,
            resolutions=3,
            priority=2.5,
        )

    def test_view_reports_waiting_on_and_the_waiting_disposition(self) -> None:
        w = world({"A": "N1", "B": "N0", "C": "N2", "M": "N3"}, policy=Scripted())
        view = w.controller.view
        w.ask("M", "N1")  # pending, not stopped: not a wait
        w.wait("C", "N1")
        w.wait("B", "N1")
        assert [agv.id for agv in view.waiting_on(w.agvs["A"])] == ["C", "B"]
        assert view.waiting_on(w.agvs["B"]) == ()
        assert view.disposition(w.agvs["B"]) is Disposition.WAITING
        assert view.disposition(w.agvs["M"]) is Disposition.IDLE
        assert FreeFlowController().view.waiting_on(w.agvs["A"]) == ()
        w.state("A", AGVState.WAITING_LOAD)
        assert disposition_of(w.agvs["A"]) is Disposition.SERVING


# --- policy reactions and directives (spec §7.9) ------------------------------------------------------------


class TestReactions:
    @pytest.mark.parametrize(
        ("refused", "reaction", "accepted", "value", "reason"),
        [
            (GiveUp(), Reroute(frozenset(), 1.5), "try_reroute", Rerouted("blocked"), "rerouted"),
            (Reroute(), StepAside(), "try_step_aside", SteppedAside(), "stepped_aside"),
            (StepAside(), GiveUp(), "give_up", GaveUp(), "gave_up"),
        ],
    )
    def test_the_first_reaction_that_succeeds_is_staged(
        self, refused: Reaction, reaction: Reaction, accepted: str, value: Any, reason: str
    ) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted(blocked=(refused, reaction, KeepWaiting(30.0))))
        made = hooks(accept=(accepted,))
        request = w.wait("B", "N1", with_hooks=made)
        assert request.event.triggered and request.event.value == value
        assert not request.granted
        assert [name for name, _ in made.calls if name in ACTIONS][1:] == [accepted]
        assert w.waits() == [("traffic.wait_started", "B", "occupied"), ("traffic.wait_ended", "B", reason)]
        assert w.controller.view.pending(w.nodes["N1"]) == 0
        assert timers(w) == []  # the KeepWaiting after the staged reaction was never reached
        w.leave("A", "N2")
        assert w.controller.view.holders(w.nodes["N1"]) == ()

    def test_a_reroute_passes_avoid_and_ratio(self) -> None:
        avoid = frozenset({Node("X", 0, 0)})
        w = world({"A": "N1", "B": "N0"}, policy=Scripted(blocked=(Reroute(avoid, 1.25),)))
        made = hooks()
        w.wait("B", "N1", with_hooks=made)
        assert ("try_reroute", (avoid, 1.25)) in made.calls

    def test_step_aside_passes_its_target(self) -> None:
        w = world({"A": "N1", "B": "N0"})
        target = w.nodes["N4"]
        w.controller._policy = Scripted(blocked=(StepAside(target),))
        made = hooks()
        w.wait("B", "N1", with_hooks=made)
        assert ("try_step_aside", (target,)) in made.calls

    def test_empty_reaction_sequence_keeps_waiting(self) -> None:
        policy = Scripted(blocked=())
        w = world({"A": "N1", "B": "N0"}, policy=policy)
        request = w.wait("B", "N1")
        assert not request.event.triggered
        assert timers(w) == []
        w.leave("A", "N2")
        assert request.granted

    def test_reactions_that_all_fail_keep_waiting_without_a_review(self) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted(blocked=(Reroute(), StepAside(), GiveUp())))
        made = hooks()
        request = w.wait("B", "N1", with_hooks=made)
        assert [name for name, _ in made.calls if name in ACTIONS] == list(ACTIONS)
        assert not request.event.triggered
        assert timers(w) == []

    def test_default_policy_is_standard(self) -> None:
        w = world({"A": "N1"})
        assert isinstance(w.controller._policy, StandardTrafficPolicy)


# --- reviews and stuck waits (spec §7.9, T23) ---------------------------------------------------------------


class TestReviews:
    @pytest.mark.parametrize("later", [False, True], ids=["one-change", "change-after-wake-up"])
    def test_no_change_no_timer_and_a_later_change_restarts_the_delay(self, later: bool) -> None:
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N1", "B": "N0", "C": "N3"}, policy=policy)
        w.state("A", AGVState.TRAVELING_EMPTY)
        w.wait("B", "N1")
        assert timers(w) == [30.0]
        w.env.run()
        assert w.env.now == 30.0
        assert [wait.reviews for wait in policy.of("on_blocked")] == [0, 1]
        assert timers(w) == []  # dormant: nothing changed since the wait opened
        run_to(w, 50.0)
        assert w.ask("C", "N4").granted  # wakes the wait and is consumed by the wake-up
        assert timers(w) == [80.0]
        if later:
            run_to(w, 60.0)
            assert w.ask("C", "N2").granted  # a change after the wake-up: one more review
        w.env.run()
        assert w.env.now == (110.0 if later else 80.0)
        assert [wait.reviews for wait in policy.of("on_blocked")] == ([0, 1, 2, 3] if later else [0, 1, 2])
        assert timers(w) == []

    def test_a_change_during_the_delay_schedules_the_next_review(self) -> None:
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N1", "B": "N0", "C": "N3"}, policy=policy)
        w.state("A", AGVState.TRAVELING_EMPTY)
        w.wait("B", "N1")
        run_to(w, 10.0)
        w.leave("C", "N4")
        assert timers(w) == [30.0]  # a running timer is not moved
        w.env.run()
        assert [(wait.reviews, wait.since) for wait in policy.of("on_blocked")] == [(0, 0.0), (1, 0.0), (2, 0.0)]
        assert w.env.now == 60.0
        assert timers(w) == []

    def test_a_grant_counts_as_a_change(self) -> None:
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N1", "B": "N0", "C": "N3"}, policy=policy)
        w.state("A", AGVState.TRAVELING_EMPTY)
        w.wait("B", "N1")
        run_to(w, 10.0)
        assert w.ask("C", "N4").granted
        w.env.run()
        assert w.env.now == 60.0

    def test_a_review_may_stage_a_reaction(self) -> None:
        def react(wait: Wait) -> Sequence[Reaction]:
            return (KeepWaiting(30.0),) if wait.reviews == 0 else (StepAside(), KeepWaiting(30.0))

        w = world({"A": "N1", "B": "N0"}, policy=Scripted(blocked=react))
        w.state("A", AGVState.TRAVELING_EMPTY)
        request = w.wait("B", "N1", with_hooks=hooks(accept=("try_step_aside",)))
        w.env.run()
        assert w.env.now == 30.0
        assert request.event.value == SteppedAside()
        assert w.waits()[-1] == ("traffic.wait_ended", "B", "stepped_aside")
        assert timers(w) == []

    @pytest.mark.parametrize("end", ["granted", "withdrawn"])
    def test_a_wait_that_ends_leaves_no_timer(self, end: str) -> None:
        w = world({"A": "N1", "B": "N0"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
        request = w.wait("B", "N1")
        assert timers(w) == [30.0]
        run_to(w, 10.0)
        if end == "granted":
            w.leave("A", "N2")
            assert request.granted
        else:
            w.controller.withdraw(request)
        assert timers(w) == []
        w.env.run()
        assert w.env.now == 10.0

    @pytest.mark.parametrize("end", ["granted", "withdrawn"])
    def test_a_wait_that_ends_leaves_unrelated_timeouts_alone(self, end: str) -> None:
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N1", "B": "N0"}, policy=policy)
        fired: list[float] = []

        def unrelated(delay: float) -> None:
            w.env.timeout(delay).callbacks.append(lambda _: fired.append(float(w.env.now)))

        unrelated(20.0)
        unrelated(30.0)  # same time as the review, scheduled before it
        request = w.wait("B", "N1")
        unrelated(30.0)  # same time as the review, scheduled after it
        unrelated(40.0)
        assert timers(w) == [20.0, 30.0, 30.0, 30.0, 40.0]
        run_to(w, 10.0)
        if end == "granted":
            w.leave("A", "N2")
            assert request.granted
        else:
            w.controller.withdraw(request)
        assert timers(w) == [20.0, 30.0, 30.0, 40.0]
        w.env.run()
        assert fired == [20.0, 30.0, 30.0, 40.0]
        assert w.env.now == 40.0
        assert len(policy.of("on_blocked")) == 1  # the review never ran

    def test_a_dormant_wait_that_is_granted_is_not_woken(self) -> None:
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N1", "B": "N0"}, policy=policy)
        w.state("A", AGVState.TRAVELING_EMPTY)
        request = w.wait("B", "N1")
        w.env.run()
        w.leave("A", "N2")
        assert request.granted
        assert timers(w) == []

    def test_a_stuck_wait_is_reported_once(self) -> None:
        w = world({"A": "N1", "B": "N0", "C": "N3"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
        w.wait("B", "N1")  # A is idle and has no fleet: nobody moves it
        assert w.replay.of(TrafficWaitStuck) == []  # an opening is not a review
        w.env.run()
        run_to(w, 40.0)
        w.leave("C", "N4")  # wakes the wait: more reviews, still stuck
        w.env.run()
        assert w.env.now == 100.0
        (stuck,) = w.replay.of(TrafficWaitStuck)
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (30.0, "B", {"A": "IDLE"})
        assert w.logged("WARNING") == [("wait of B is stuck: blockers A (IDLE)", "ReservationController")]

    def test_a_blocker_that_makes_way_at_a_review_is_not_reported_stuck(self) -> None:
        """The idle blocker refuses to move when the wait opens and accepts at the first review: that review reports
        nothing. Back to idle and refusing, it makes the wait stuck at the next review, which is reported."""
        w = world(
            {"A": ("N1", "F1"), "B": "N0", "C": "N3"}, refuse=("F1",), policy=Scripted(blocked=(KeepWaiting(30.0),))
        )
        w.wait("B", "N1")
        w.fleets["F1"].accept = True
        w.env.run()
        assert w.env.now == 30.0
        assert w.fleets["F1"].moves[-1][0] == "A" and w.agvs["A"].state is AGVState.TRAVELING_EMPTY
        assert w.replay.of(TrafficWaitStuck) == []
        w.fleets["F1"].accept = False
        w.state("A", AGVState.IDLE)  # the stub's move goes nowhere
        w.leave("C", "N4")  # a change: the dormant wait is reviewed again 30 s later
        w.env.run()
        (stuck,) = w.replay.of(TrafficWaitStuck)
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (60.0, "B", {"A": "IDLE"})

    def test_stranded_and_stalled_blockers_make_a_stuck_wait(self) -> None:
        w = world({"A": "N1", "C": "N2", "B": "N0"}, policy=Scripted(blocked=(KeepWaiting(5.0),)))
        w.state("A", AGVState.STRANDED)
        w.state("C", AGVState.TRAVELING_LOADED)
        w.stall("C")
        w.wait("B", "N1", "N2")
        w.env.run()
        (stuck,) = w.replay.of(TrafficWaitStuck)
        assert dict(stuck.blockers) == {"A": "STRANDED", "C": "STALLED"}

    @pytest.mark.parametrize("waiting", [False, True], ids=["moving", "waiting"])
    def test_a_moving_or_waiting_blocker_is_not_stuck(self, waiting: bool) -> None:
        w = world({"A": "N1", "M": "N2", "B": "N0"}, policy=Scripted(blocked=(KeepWaiting(5.0),)))
        w.state("M", AGVState.TRAVELING_LOADED)
        if waiting:
            w.wait("A", "N2")  # A waits on M, which moves
        else:
            w.state("A", AGVState.TRAVELING_EMPTY)
        w.wait("B", "N1")
        w.env.run()
        assert w.env.now == 5.0
        assert w.replay.of(TrafficWaitStuck) == []

    def test_a_chain_of_waits_ending_at_a_moving_agv_is_not_stuck(self) -> None:
        """Every wait of the chain C, B, A is blocked by a waiting AGV or by M, which moves: none is stuck, however
        long the chain."""
        w = world({"M": "N4", "A": "N3", "B": "N2", "C": "N1"}, policy=Scripted(blocked=(KeepWaiting(5.0),)))
        w.state("M", AGVState.TRAVELING_LOADED)
        w.wait("A", "N4")
        w.wait("B", "N3")
        w.wait("C", "N2")
        w.env.run()
        assert w.env.now == 5.0
        assert w.replay.of(TrafficWaitStuck) == []

    def test_a_chain_of_three_waits_behind_one_idle_agv_reports_all_three(self) -> None:
        """A rests idle and nobody moves it; B waits on A, C on B, D on C (spec §7.9, stuck waits). Each review
        reports its wait, once, with its blocker's disposition: ``WAITING`` for a waiting one."""
        w = world({"A": "N4", "B": "N3", "C": "N2", "D": "N1"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
        w.wait("B", "N4")
        w.wait("C", "N3")
        w.wait("D", "N2")
        w.env.run()
        assert w.env.now == 30.0
        assert [(e.t, e.agv, dict(e.blockers)) for e in w.replay.of(TrafficWaitStuck)] == [
            (30.0, "B", {"A": "IDLE"}),
            (30.0, "C", {"B": "WAITING"}),
            (30.0, "D", {"C": "WAITING"}),
        ]
        assert w.logged("WARNING") == [
            ("wait of B is stuck: blockers A (IDLE)", "ReservationController"),
            ("wait of C is stuck: blockers B (WAITING)", "ReservationController"),
            ("wait of D is stuck: blockers C (WAITING)", "ReservationController"),
        ]
        assert w.deadlocks() == []

    def test_a_wait_behind_a_member_of_an_unresolved_deadlock_is_reported_stuck_once(self) -> None:
        """A (holding ``N1~N2`` forward) and B block each other: an unresolved deadlock. C waits for a slot of ``N1``
        (capacity 2), held by A and by X, which is idle: C is not in the deadlocked set, and its wait is stuck. It is
        reported once, at its review, and not again when X becomes idle; the members of the set are never reported
        stuck, and the deadlock is reported as before, once."""
        w = world(
            {"A": "N1", "X": "N1", "B": "N2", "C": "N0"},
            shape=lambda env: line(env, 5, capacities={"N1": 2}),
            policy=Scripted(blocked=(KeepWaiting(30.0),)),
        )
        assert w.ask("A", (w.seg("N1", "N2"), "forward")).granted
        w.wait("A", "N2")
        w.wait("B", (w.seg("N1", "N2"), "backward"), "N1")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        w.wait("C", "N1")
        w.env.run()
        assert w.env.now == 30.0
        w.controller.idle(w.agvs["X"])  # X blocks B and C: neither is reported again
        (stuck,) = w.replay.of(TrafficWaitStuck)
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (30.0, "C", {"A": "WAITING", "X": "IDLE"})
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]

    def test_a_stuck_report_reaches_the_waits_behind_it_when_the_blocker_becomes_idle(self) -> None:
        """C waits on B, B on A. A is serving during both first reviews, so neither is stuck and both sleep without a
        timer. When A becomes idle where it stands (nobody moves it), B is reported, and the report reaches C at the
        same instant, B first (spec §7.9, stuck waits)."""
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N2", "B": "N1", "C": "N0"}, policy=policy)
        w.state("A", AGVState.WAITING_UNLOAD)
        w.wait("B", "N2")
        w.wait("C", "N1")
        w.env.run(until=50.0)
        assert w.replay.of(TrafficWaitStuck) == []
        assert timers(w) == []
        w.state("A", AGVState.IDLE)
        w.controller.idle(w.agvs["A"])
        assert [(e.t, e.agv, dict(e.blockers)) for e in w.replay.of(TrafficWaitStuck)] == [
            (50.0, "B", {"A": "IDLE"}),
            (50.0, "C", {"B": "WAITING"}),
        ]
        assert timers(w) == []  # no review is scheduled
        assert len(policy.of("on_blocked")) == 4  # two openings and two first reviews: the reports are not reviews

    def test_a_former_member_of_a_dissolved_deadlock_behind_a_reported_wait_is_reported(self) -> None:
        """A and B form an unresolved deadlock, which A's stop completes: A's wait has no review and no timer. B
        withdraws, which dissolves the set: A gets the on_blocked call and the review it did not have. B then waits
        for N3, held by X, idle. At 30 A's review comes first (it was scheduled first) and reports A, whose chain runs
        through B to X; B's review reports B."""
        w = world({"A": "N1", "B": "N2", "X": "N3"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
        assert w.ask("A", (w.seg("N1", "N2"), "forward")).granted
        swap = w.wait("B", (w.seg("N1", "N2"), "backward"), "N1")
        w.wait("A", "N2")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert timers(w) == [30.0]  # B's review only
        w.controller.withdraw(swap)
        assert timers(w) == [30.0]  # A's first review; B's went with its wait
        w.wait("B", "N3")
        w.env.run()
        assert w.env.now == 30.0
        assert [(e.t, e.agv, dict(e.blockers)) for e in w.replay.of(TrafficWaitStuck)] == [
            (30.0, "A", {"B": "WAITING"}),
            (30.0, "B", {"X": "IDLE"}),
        ]

    def test_a_stuck_report_does_not_reach_a_wait_with_a_moving_blocker_of_its_own(self) -> None:
        w = world({"A": "N2", "B": "N1", "C": "N0", "M": "N4"}, policy=Scripted(blocked=(KeepWaiting(30.0),)))
        w.state("M", AGVState.TRAVELING_LOADED)
        w.wait("C", "N1", "N4")  # behind B and behind M, which moves
        w.wait("B", "N2")  # behind A, idle
        w.env.run()
        assert [(e.agv, dict(e.blockers)) for e in w.replay.of(TrafficWaitStuck)] == [("B", {"A": "IDLE"})]

    def test_two_chains_that_meet_report_the_wait_where_they_meet_once(self) -> None:
        """D waits on B and on C; B waits on A0 and C on A4, both serving during the first reviews. A0 becomes idle:
        B is reported, but D is not stuck while C's blocker serves. A4 becomes idle: C is reported, and the report
        reaches D. A0 becoming idle again reports nothing more."""
        w = world(
            {"A0": "N0", "B": "N1", "D": "N2", "C": "N3", "A4": "N4"}, policy=Scripted(blocked=(KeepWaiting(30.0),))
        )
        for name in ("A0", "A4"):
            w.state(name, AGVState.WAITING_UNLOAD)
        w.wait("B", "N0")
        w.wait("C", "N4")
        w.wait("D", "N1", "N3")
        w.env.run(until=50.0)
        assert w.replay.of(TrafficWaitStuck) == []
        w.state("A0", AGVState.IDLE)
        w.controller.idle(w.agvs["A0"])
        assert [e.agv for e in w.replay.of(TrafficWaitStuck)] == ["B"]
        w.state("A4", AGVState.IDLE)
        w.controller.idle(w.agvs["A4"])
        w.controller.idle(w.agvs["A0"])
        assert [(e.agv, dict(e.blockers)) for e in w.replay.of(TrafficWaitStuck)] == [
            ("B", {"A0": "IDLE"}),
            ("C", {"A4": "IDLE"}),
            ("D", {"B": "WAITING", "C": "WAITING"}),
        ]

    def test_a_review_with_keep_waiting_none_ends_the_reviews(self) -> None:
        policy = Scripted(blocked=lambda wait: (KeepWaiting(30.0),) if wait.reviews == 0 else (KeepWaiting(),))
        w = world({"A": "N1", "B": "N0", "C": "N3"}, policy=policy)
        w.state("A", AGVState.TRAVELING_EMPTY)
        w.wait("B", "N1")
        run_to(w, 10.0)
        w.leave("C", "N4")
        w.env.run()
        assert w.env.now == 30.0
        assert timers(w) == []
        assert w.ask("C", "N3").granted  # a change wakes nothing
        assert timers(w) == []


# --- idle blockers (spec §7.10) -----------------------------------------------------------------------------


class TestMakeWay:
    def test_an_idle_blocker_is_asked_once_with_the_policys_target(self) -> None:
        policy = Scripted(target=None)
        w = world({"A": ("N1", "F1"), "B": "N0", "C": "N2"}, policy=policy)
        w.wait("B", "N1")
        assert w.fleets["F1"].moves == [("A", None, ("B",))]
        assert policy.of("make_way_target") == [("A", "B")]
        w.wait("C", "N1")  # A is moving now: no second call
        assert len(w.fleets["F1"].moves) == 1

    def test_the_target_and_the_concerned_waiters(self) -> None:
        w = world({"A": ("N1", "F1"), "B": "N0", "C": "N2"}, refuse=("F1",))
        target = w.nodes["N4"]
        w.controller._policy = Scripted(target=target)
        w.wait("C", "N1")
        w.wait("B", "N1")
        assert w.fleets["F1"].moves == [("A", target, ("C",)), ("A", target, ("B", "C"))]

    def test_once_per_blocker_per_opening_even_for_two_resources(self) -> None:
        w = world({"A": ("N1", "F1"), "B": "N0"}, refuse=("F1",), policy=Scripted())
        assert w.ask("A", "N2").granted
        w.wait("B", "N1", "N2")
        assert w.fleets["F1"].moves == [("A", None, ("B",))]

    def test_a_review_asks_again(self) -> None:
        w = world({"A": ("N1", "F1"), "B": "N0"}, refuse=("F1",), policy=Scripted(blocked=(KeepWaiting(30.0),)))
        w.wait("B", "N1")
        w.env.run()
        assert [move[0] for move in w.fleets["F1"].moves] == ["A", "A"]

    def test_no_fleet_or_not_idle_means_no_call(self) -> None:
        policy = Scripted()
        w = world({"A": ("N1", "F1"), "B": "N0", "C": "N3", "D": "N2"}, policy=policy)
        w.state("A", AGVState.CHARGING)
        w.wait("B", "N1")
        w.wait("D", "N3")  # C is idle without a fleet
        assert w.fleets["F1"].moves == []
        assert policy.of("make_way_target") == []


# --- deadlock detection (spec §7.8) -------------------------------------------------------------------------


class TestDetection:
    def test_two_agvs_swapping_are_deadlocked_when_the_second_stops(self) -> None:
        policy = Scripted()
        w = world({"A": "N0", "B": "N1"}, shape=lambda env: line(env, 2), policy=policy)
        w.wait("A", (w.seg("N0", "N1"), "forward"), "N1")
        assert policy.of("on_deadlock") == []
        w.wait("B", (w.seg("N0", "N1"), "backward"), "N0")
        (waits,) = policy.of("on_deadlock")
        assert [wait.agv.id for wait in waits] == ["A", "B"]
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["A"]  # skipped for the members

    def test_a_three_agv_cycle(self) -> None:
        w = world({"A": "N0", "B": "N1", "C": "N2"}, policy=Scripted())
        w.wait("A", "N1")
        w.wait("B", "N2")
        assert w.deadlocks() == []
        w.wait("C", "N0")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved")]

    def test_a_waiter_behind_a_cycle_belongs_to_it(self) -> None:
        w = world({"A": "N0", "B": "N1", "C": "N2"}, policy=Scripted())
        w.wait("C", "N1")
        w.wait("A", "N1")
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved")]

    def test_a_free_slot_held_by_a_moving_agv_is_not_a_deadlock(self) -> None:
        policy = Scripted()
        w = world(
            {"A": "N1", "M": "N1", "B": "N2"}, shape=lambda env: line(env, 5, capacities={"N1": 2}), policy=policy
        )
        w.state("M", AGVState.TRAVELING_EMPTY)
        w.wait("A", "N2")
        w.wait("B", "N1")
        assert policy.of("on_deadlock") == []
        assert w.deadlocks() == []

    def test_agvs_that_are_not_stopped_are_ignored(self) -> None:
        policy = Scripted()
        w = world({"A": "N0", "B": "N1"}, policy=policy)
        late = w.ask("B", "N0")  # B's look-ahead: pending, but B is not at rest
        w.wait("A", "N1")
        assert policy.of("on_deadlock") == []
        w.controller.stopped(late)
        assert len(policy.of("on_deadlock")) == 1

    def test_a_cycle_only_through_a_gate_is_waived(self) -> None:
        policy = Scripted()
        w = world({"X": "N1", "E": "N2", "C": "N4"}, policy=policy)
        seg = w.seg("N1", "N2")
        assert w.ask("X", (seg, "forward")).granted
        w.wait("X", "N4")  # X waits on C
        w.wait("E", (seg, "backward"))  # E waits on X
        gated = w.ask("C", (seg, "forward"))  # admissible, but held by E's gate
        assert not gated.event.triggered
        w.controller.stopped(gated)
        assert gated.granted  # E yields: C goes, and the cycle through the gate is gone
        assert w.replay.of(TrafficWaitStarted)[-1].blockers == ("E",)
        assert w.waits()[-1] == ("traffic.wait_ended", "C", "granted")
        assert w.deadlocks() == []
        assert policy.of("on_deadlock") == []
        assert w.controller._pending[w.agvs["E"]].yielding

    def test_an_unresolved_deadlock_is_reported_once_per_set(self) -> None:
        w = world({"A": "N0", "B": "N1", "C": "N3", "M": "N4", "D": "N2"}, policy=Scripted())
        w.state("M", AGVState.TRAVELING_EMPTY)
        w.wait("A", "N1")
        w.wait("B", "N0")
        w.wait("C", "N4")  # waits on a moving AGV: the deadlocked set is still {A, B}
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert w.logged("ERROR") == [("deadlock among A, B: no resolution succeeded", "ReservationController")]
        w.wait("D", "N1")  # waits on B: the set grows
        assert w.deadlocks()[-1] == (("A", "B", "D"), None, "unresolved")
        assert len(w.deadlocks()) == 2
        w.env.run()
        assert timers(w) == []

    def test_a_set_that_broke_and_formed_again_is_reported_again(self) -> None:
        w = world({"A": "N0", "B": "N1"}, policy=Scripted())
        w.wait("A", "N1")
        request = w.wait("B", "N0")
        w.controller.withdraw(request)
        w.wait("B", "N0")
        assert len(w.deadlocks()) == 2

    def test_what_is_left_of_a_set_when_a_members_wait_is_withdrawn_is_examined_again(self) -> None:
        """C waits behind the cycle of A and B, and the set {A, B, C} stands unresolved. C's withdrawal ends the set
        the controller remembered, but A and B still block each other: detection runs again and reports them, since
        neither has a review left that would."""
        policy = Scripted()  # KeepWaiting() with no review: nothing but detection looks at these waits again
        w = world({"A": "N0", "B": "N1", "C": "N2"}, policy=policy)
        behind = w.wait("C", "N1")
        w.wait("A", "N1")
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved")]
        w.controller.withdraw(behind)
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved"), (("A", "B"), None, "unresolved")]
        assert [[wait.agv.id for wait in waits] for waits in policy.of("on_deadlock")] == [["C", "A", "B"], ["A", "B"]]
        assert w.controller._unresolved == [("A", "B")]
        # B opened into the set; detection comes first and finds it in {A, B} again, so it gets no on_blocked call
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["C", "A"]
        w.env.run()
        assert timers(w) == []
        assert w.env.now == 0.0

    def test_a_members_withdrawal_that_leaves_no_deadlock_asks_the_policy_nothing(self) -> None:
        policy = Scripted()
        w = world({"A": "N0", "B": "N1"}, policy=policy)
        w.wait("A", "N1")
        request = w.wait("B", "N0")
        assert len(policy.of("on_deadlock")) == 1
        w.controller.withdraw(request)  # A still waits on B's node, but B no longer waits: no deadlock
        assert len(policy.of("on_deadlock")) == 1
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["A"]  # A had its on_blocked call and keeps it
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert w.controller._unresolved == []

    def test_the_withdrawal_of_a_waiter_outside_every_unresolved_set_does_not_detect(self) -> None:
        policy = Scripted()
        w = world({"A": "N0", "B": "N1", "M": "N3", "D": "N2"}, policy=policy)
        w.state("M", AGVState.TRAVELING_EMPTY)
        w.wait("A", "N1")
        w.wait("B", "N0")
        outside = w.wait("D", "N3")  # waits on a moving AGV; its stop asks the policy about {A, B} once more
        assert len(policy.of("on_deadlock")) == 2
        w.controller.withdraw(outside)
        assert len(policy.of("on_deadlock")) == 2
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert w.controller._unresolved == [("A", "B")]

    def test_what_is_left_of_a_set_when_a_review_stages_a_members_reaction_is_examined_again(self) -> None:
        """As above, but C's wait ends because its own review reroutes it (a member that was already waiting keeps
        its reviews); the end of the wait comes from the reaction, which also only scans."""

        def blocked(wait: Wait) -> Sequence[Reaction]:
            if wait.agv.id != "C":
                return (KeepWaiting(),)
            return (KeepWaiting(1.0),) if wait.reviews == 0 else (Reroute(frozenset()), KeepWaiting())

        policy = Scripted(blocked=blocked, deadlock=lambda waits: [])
        w = world({"A": "N0", "B": "N1", "C": "N2"}, policy=policy)
        behind = w.wait("C", "N1", with_hooks=hooks(name="C", accept=("try_reroute",)))
        w.wait("A", "N1")
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved")]
        run_to(w, 2.0)
        assert behind.event.value == Rerouted("blocked")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved"), (("A", "B"), None, "unresolved")]
        assert w.controller._unresolved == [("A", "B")]
        w.env.run()
        assert timers(w) == []

    def test_the_withdrawal_scans_before_it_detects_again(self) -> None:
        """C's pending claim gates W, which is moving; the withdrawal of C first grants what C's claim held back, then
        looks at the deadlock left, as a stop does after its request's scan."""
        w = world({"A": "N0", "B": "N1", "C": "N2", "W": "N3"}, policy=Scripted())
        seg = w.seg("N1", "N2")
        assert w.ask("B", (seg, "forward")).granted
        w.state("W", AGVState.TRAVELING_EMPTY)
        behind = w.wait("C", (seg, "backward"), "N1")
        w.wait("A", "N1")
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B", "C"), None, "unresolved")]
        late = w.ask("W", (seg, "forward"))  # gated by C's claim
        assert not late.event.triggered
        count = len(w.replay.events)
        w.controller.withdraw(behind)
        assert late.granted
        tail = [(e.type_name, getattr(e, "agv", None) or e.agvs) for e in w.replay.events[count:]]
        assert tail == [("traffic.wait_ended", "C"), ("traffic.reserved", "W"), ("traffic.deadlock", ("A", "B"))]

    def test_a_staged_reaction_of_a_waiter_outside_every_unresolved_set_does_not_detect(self) -> None:
        def blocked(wait: Wait) -> Sequence[Reaction]:
            if wait.agv.id != "D":
                return (KeepWaiting(),)
            return (KeepWaiting(1.0),) if wait.reviews == 0 else (Reroute(frozenset()), KeepWaiting())

        policy = Scripted(blocked=blocked)
        w = world({"A": "N0", "B": "N1", "M": "N3", "D": "N2"}, policy=policy)
        w.state("M", AGVState.TRAVELING_EMPTY)
        w.wait("A", "N1")
        w.wait("B", "N0")
        outside = w.wait("D", "N3", with_hooks=hooks(name="D", accept=("try_reroute",)))  # waits on a moving AGV
        assert len(policy.of("on_deadlock")) == 2
        run_to(w, 2.0)
        assert outside.event.value == Rerouted("blocked")
        assert len(policy.of("on_deadlock")) == 2
        assert w.controller._unresolved == [("A", "B")]

    @staticmethod
    def two_opened_into_sets(
        policy: Scripted, q_hooks: FakeHooks | None = None
    ) -> tuple[World, dict[str, ClaimRequest]]:
        """On ``line(6)``: X stops first (on_blocked, outside a deadlock), then Q closes {Q, X}, then P, which requested
        before Q, stops behind X and closes {P, Q, X}. Q and P opened into a set: no on_blocked, no review. Z, on N5,
        is free to move."""
        w = world({"P": "N1", "X": "N2", "Q": "N3", "Z": "N5"}, shape=lambda env: line(env, 6), policy=policy)
        requests = {"X": w.wait("X", "N3")}
        late = w.ask("P", "N2")  # P requests before Q and stops after it
        requests["Q"] = w.wait("Q", "N2", with_hooks=q_hooks)
        w.controller.stopped(late)
        requests["P"] = late
        assert w.deadlocks() == [(("Q", "X"), None, "unresolved"), (("P", "Q", "X"), None, "unresolved")]
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X"]
        return w, requests

    def test_former_members_left_outside_a_deadlock_get_on_blocked_and_a_review_in_the_order_their_waits_opened(
        self,
    ) -> None:
        policy = Scripted(blocked=(KeepWaiting(5.0),))
        w, requests = self.two_opened_into_sets(policy)
        run_to(w, 1.0)
        w.leave("Z", "N4")  # a change before the sets are forgotten
        w.controller.withdraw(requests["X"])  # both sets are forgotten; P and Q no longer block each other
        assert w.controller._unresolved == []
        assert len(policy.of("on_deadlock")) == 2  # nothing is asked of the policy: the waits left are no deadlock
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X", "Q", "P"]  # Q opened before P
        assert timers(w) == [6.0, 6.0]  # their first reviews, from the instant the sets were forgotten
        w.env.run()  # nothing changed since then: one review each, then they sleep (T23)
        assert w.env.now == 6.0
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X", "Q", "P", "Q", "P"]

    def test_a_former_member_whose_on_blocked_call_stages_a_reaction_gets_no_review(self) -> None:
        def blocked(wait: Wait) -> Sequence[Reaction]:
            return (Reroute(frozenset()), KeepWaiting()) if wait.agv.id == "Q" else (KeepWaiting(5.0),)

        policy = Scripted(blocked=blocked, deadlock=lambda waits: [])
        w, requests = self.two_opened_into_sets(policy, hooks(name="Q", accept=("try_reroute",)))
        w.controller.withdraw(requests["X"])
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X", "Q", "P"]
        assert requests["Q"].event.value == Rerouted("blocked")
        assert w.waits()[-1] == ("traffic.wait_ended", "Q", "rerouted")
        assert timers(w) == [5.0]  # P's first review only

    def test_a_former_member_left_by_a_resolution_at_a_later_stop_gets_on_blocked_before_the_stopping_wait(
        self,
    ) -> None:
        """{A, B} stands unresolved, B's wait opened into it. X stops behind B and the policy now reroutes A: the set is
        forgotten, B is in no deadlock and gets its on_blocked call and a review, before X gets its own."""
        calls = {"n": 0}

        def deadlock(waits: Sequence[Wait]) -> Sequence[tuple[AGV, Reaction]]:
            calls["n"] += 1
            return [] if calls["n"] == 1 else [(wait.agv, Reroute(frozenset())) for wait in waits if wait.agv.id == "A"]

        policy = Scripted(blocked=(KeepWaiting(5.0),), deadlock=deadlock)
        w = world({"A": "N0", "B": "N1", "X": "N2"}, policy=policy)
        rerouted = w.wait("A", "N1", with_hooks=hooks(name="A", accept=("try_reroute",)))
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["A"]
        w.wait("X", "N1")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved"), (("A", "B", "X"), "A", "reroute")]
        assert rerouted.event.value == Rerouted("deadlock")
        assert w.controller._unresolved == []
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["A", "B", "X"]
        assert w.controller._pending[w.agvs["B"]].timer is not None
        assert timers(w) == [5.0, 5.0]  # B's and X's first reviews; A's went with its wait

    def test_a_former_member_with_a_review_or_still_in_a_remembered_set_gets_no_new_one(self) -> None:
        policy = Scripted(blocked=(KeepWaiting(5.0),))
        w, requests = self.two_opened_into_sets(policy)
        w.controller.withdraw(requests["P"])  # forgets {P, Q, X}; {Q, X} stands, and Q is in it
        assert w.controller._unresolved == [("Q", "X")]
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X"]
        w.controller.withdraw(requests["Q"])  # forgets {Q, X}: X had its review, and keeps it
        assert w.controller._unresolved == []
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["X"]
        assert timers(w) == [5.0]

    def test_a_former_member_reviewed_since_its_wait_opened_gets_no_new_on_blocked_call(self) -> None:
        """A's wait opens into {A, B}; S, which also blocks A, strands and A gets a review then (spec §7.10). When B's
        wait is withdrawn, A already had its on_blocked call and keeps its own review schedule."""
        policy = Scripted(blocked=(KeepWaiting(30.0),))
        w = world({"A": "N0", "B": "N1", "S": "N2"}, policy=policy)
        withdrawn = w.wait("B", "N0")
        w.wait("A", "N1", "N2")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        w.state("S", AGVState.STRANDED)
        w.controller.idle(w.agvs["S"])
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["B", "A"]
        w.controller.withdraw(withdrawn)
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["B", "A"]

    @pytest.mark.parametrize("rest", [AGVState.STRANDED, AGVState.IDLE])
    def test_a_former_member_whose_blocker_comes_to_rest_elsewhere_is_reported_stuck(self, rest: AGVState) -> None:
        """The reviewer's case: A and B deadlocked, A also blocked by S at rest; B's wait is withdrawn and B makes way.
        A opened into the set and had no review; it gets one when the set is forgotten, and is reported stuck."""

        def tee(env: Environment) -> Declared:
            nodes = [("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("N3", 10, 10)]
            return build(env, nodes, [("N0", "N1"), ("N1", "N2"), ("N1", "N3")])

        w = world({"A": "N0", "B": ("N1", "F1"), "S": "N2"}, shape=tee, policy=StandardTrafficPolicy())
        if rest is AGVState.STRANDED:
            w.state("S", rest)
        w.state("B", AGVState.TRAVELING_EMPTY)
        w.state("A", AGVState.TRAVELING_EMPTY)
        withdrawn = w.wait("B", "N0")
        w.wait("A", "N1", "N2")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        assert timers(w) == [30.0]  # B's review; A has none
        w.controller.withdraw(withdrawn)
        assert timers(w) == [30.0]  # B's review went with its wait; this is A's first
        w.state("B", AGVState.IDLE)
        w.controller.idle(w.agvs["B"])
        assert w.fleets["F1"].moves == [("B", None, ("A",))]
        w.leave("B", "N3")
        w.state("B", AGVState.IDLE)
        w.controller.idle(w.agvs["B"])
        w.env.run()  # B's move is a change after A's review started: one more review, then A's review sleeps (T23)
        assert w.env.now == 60.0
        (stuck,) = w.replay.of(TrafficWaitStuck)
        assert (stuck.t, stuck.agv, dict(stuck.blockers)) == (30.0, "A", {"S": rest.name})
        assert timers(w) == []


# --- deadlock resolution (spec §7.8, §7.9) ------------------------------------------------------------------


class TestResolution:
    def test_the_victim_is_rerouted(self) -> None:
        w = world({"A": "N0", "B": "N1"}, shape=lambda env: line(env, 2), policy=Scripted())
        seg = w.seg("N0", "N1")
        w.wait("A", (seg, "forward"), "N1")
        run_to(w, 5.0)
        made = hooks(accept=("try_reroute",))
        request = w.wait("B", (seg, "backward"), "N0", with_hooks=made)
        assert w.deadlocks() == [(("A", "B"), "B", "reroute")]
        assert request.event.value == Rerouted("deadlock")
        assert ("try_reroute", (frozenset({w.nodes["N0"]}), None)) in made.calls
        assert w.waits()[-1] == ("traffic.wait_ended", "B", "rerouted")
        assert w.replay.events[-1].type_name == "traffic.deadlock"
        assert w.agvs["B"] not in w.controller._pending

    def test_victims_are_tried_in_the_standard_order(self) -> None:
        log: list[tuple[str, str, tuple[Any, ...]]] = []
        priorities = {"A": 1.0}
        w = world(
            {"A": "N0", "B": "N1", "C": "N2", "D": "N3"},
            policy=Scripted(),
            priority_fn=lambda agv: priorities.get(agv.id, 0.0),
        )
        w.wait("C", "N3", with_hooks=hooks(log, name="C"))
        run_to(w, 1.0)
        w.wait("B", "N2", with_hooks=hooks(log, name="B", loaded=True))
        run_to(w, 2.0)
        w.wait("A", "N1", with_hooks=hooks(log, name="A"))
        run_to(w, 3.0)
        w.wait("D", "N0", with_hooks=hooks(log, name="D"))
        tried = [(name, action) for name, action, _ in log]
        expected = [(name, "try_reroute") for name in "DCBA"] + [(name, "try_step_aside") for name in "DCBA"]
        assert tried[-8:] == expected
        assert w.deadlocks() == [(("A", "B", "C", "D"), None, "unresolved")]

    def test_deadlock_across_fleets_uses_each_fleets_hooks(self) -> None:
        log: list[tuple[str, str, tuple[Any, ...]]] = []
        w = world({"A": ("N0", "F1"), "B": ("N1", "F2")}, policy=Scripted())
        w.state("A", AGVState.TRAVELING_EMPTY)
        w.state("B", AGVState.TRAVELING_LOADED)
        first = w.wait("A", "N1", with_hooks=hooks(log, name="A", accept=("try_step_aside",)))
        run_to(w, 1.0)
        second = w.wait("B", "N0", with_hooks=hooks(log, name="B"))
        assert [(name, action) for name, action, _ in log] == [
            ("B", "try_reroute"),
            ("A", "try_reroute"),
            ("B", "try_step_aside"),
            ("A", "try_step_aside"),
        ]
        assert first.event.value == SteppedAside()
        assert not second.event.triggered
        assert w.deadlocks() == [(("A", "B"), "A", "step_aside")]
        assert w.fleets["F1"].moves == w.fleets["F2"].moves == []

    @staticmethod
    def behind_a_cycle(
        policy: Any, accept_a: Sequence[str] = (), accept_b: Sequence[str] = ()
    ) -> tuple[World, dict[str, ClaimRequest]]:
        """The reviewer's case on ``line(5)``: C (unloaded, accepts a reroute) waits behind the cycle of A and B (both
        loaded); B's stop closes the set {A, B, C}."""
        w = world({"A": "N0", "B": "N1", "C": "N2"}, policy=policy)
        requests = {
            "C": w.wait("C", "N1", with_hooks=hooks(name="C", accept=("try_reroute",))),
            "A": w.wait("A", "N1", with_hooks=hooks(name="A", loaded=True, accept=accept_a)),
            "B": w.wait("B", "N0", with_hooks=hooks(name="B", loaded=True, accept=accept_b)),
        }
        return w, requests

    def test_a_victim_that_does_not_break_the_set_is_followed_by_another_round(self) -> None:
        policy = Scripted()
        w, requests = self.behind_a_cycle(policy)
        assert requests["C"].event.value == Rerouted("deadlock")
        assert w.deadlocks() == [(("A", "B", "C"), "C", "reroute"), (("A", "B"), None, "unresolved")]
        assert [[wait.agv.id for wait in waits] for waits in policy.of("on_deadlock")] == [["C", "A", "B"], ["A", "B"]]
        assert w.logged("ERROR") == [("deadlock among A, B: no resolution succeeded", "ReservationController")]
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["C", "A"]  # B is left deadlocked: no on_blocked
        tail = [(e.type_name, getattr(e, "agv", None)) for e in w.replay.events[-3:]]
        assert tail == [("traffic.wait_ended", "C"), ("traffic.deadlock", None), ("traffic.deadlock", None)]
        assert not requests["A"].event.triggered and not requests["B"].event.triggered
        w.env.run()
        assert timers(w) == []
        assert len(w.deadlocks()) == 2

    @pytest.mark.parametrize("victim", ["A", "B"])
    def test_a_second_round_can_resolve_what_is_left(self, victim: str) -> None:
        policy = Scripted()
        accept = ("try_reroute",)
        w, requests = self.behind_a_cycle(
            policy, accept_a=accept if victim == "A" else (), accept_b=accept if victim == "B" else ()
        )
        assert w.deadlocks() == [(("A", "B", "C"), "C", "reroute"), (("A", "B"), victim, "reroute")]
        assert requests[victim].event.value == Rerouted("deadlock")
        other = "B" if victim == "A" else "A"
        assert not requests[other].event.triggered
        assert w.logged("ERROR") == []
        assert w.controller._unresolved == []
        assert w.controller._reduce([p for p in w.controller._queue if p.stopped], gates=False) == []
        # B closed the set: it is reviewed as usual only if its wait is still open
        assert [wait.agv.id for wait in policy.of("on_blocked")] == ["C", "A", *(["B"] if victim == "A" else [])]
        w.env.run()
        assert w.env.now == 0.0
        assert w.invariants.checked > 0

    def test_the_closer_left_waiting_outside_a_deadlock_gets_on_blocked(self) -> None:
        w, requests = self.behind_a_cycle(StandardTrafficPolicy(), accept_a=("try_reroute",))
        assert w.deadlocks() == [(("A", "B", "C"), "C", "reroute"), (("A", "B"), "A", "reroute")]
        assert not requests["B"].event.triggered
        assert w.replay.of(TrafficWaitStarted)[-1].agv == "B"
        assert timers(w) == [30.0]  # on_blocked kept B waiting with a review
        w.env.run()
        assert w.env.now == 30.0
        (stuck,) = w.replay.of(TrafficWaitStuck)  # A is rerouted but idle on N0, and nobody moves it
        assert (stuck.agv, dict(stuck.blockers)) == ("B", {"A": "IDLE"})

    def test_keep_waiting_attempts_are_skipped_and_give_up_is_staged(self) -> None:
        def attempts(waits: Sequence[Wait]) -> Sequence[tuple[AGV, Reaction]]:
            return [(waits[0].agv, KeepWaiting(1.0)), (waits[1].agv, GiveUp())]

        w = world({"A": "N0", "B": "N1"}, policy=Scripted(deadlock=attempts))
        w.wait("A", "N1")
        request = w.wait("B", "N0", with_hooks=hooks(accept=("give_up",)))
        assert request.event.value == GaveUp()
        assert w.deadlocks() == [(("A", "B"), "B", "give_up")]
        assert timers(w) == []

    def test_an_unresolved_deadlock_stands_and_schedules_nothing(self) -> None:
        w = world({"A": "N0", "B": "N1"})  # the standard policy, hooks that refuse everything
        w.wait("A", "N1")
        w.wait("B", "N0")
        assert w.deadlocks() == [(("A", "B"), None, "unresolved")]
        w.env.run()  # A's first review, then nothing
        assert w.env.now == 30.0
        assert timers(w) == []
        assert w.env.peek() == math.inf  # nothing left to happen (T23)
        assert len(w.deadlocks()) == 1
        assert len(w.logged("ERROR")) == 1

    def test_without_observers_the_outcomes_are_the_same(self) -> None:
        env = Environment()
        declared = line(env, 3)
        controller = ReservationController(env=env, graph=declared.graph, derive_corridors=False)
        a, b = (
            AGV(env=env, agv_type=cart(), agv_id=name, initial_node=declared.ref(node))
            for name, node in (("A", "N0"), ("B", "N1"))
        )
        graph = declared.canonical_graph()  # prepares the layout
        controller.register_fleet(StubFleet("F", graph, (a, b)))  # type: ignore[arg-type]
        env.on_activate(lambda: [controller.place(agv, agv.current_node) for agv in (a, b)])
        env.activate()
        nodes = {node.id: node for node in graph.nodes}
        claim = Claim((ClaimItem(nodes["N1"]),), 1)
        controller.stopped(waiting := controller.request(a, claim, fake_hooks()))
        refused = controller.request(b, Claim((ClaimItem(nodes["N0"]),), 1), fake_hooks())
        controller.stopped(refused)
        assert controller._unresolved == [("A", "B")]
        controller.withdraw(refused)
        assert controller._unresolved == []
        rerouted = controller.request(b, Claim((ClaimItem(nodes["N0"]),), 1), hooks(accept=("try_reroute",)))
        controller.stopped(rerouted)
        assert rerouted.event.value == Rerouted("deadlock")
        env.run()
        assert controller._pending[a].stuck  # B is idle and nobody moves it
        assert not waiting.event.triggered


# --- StandardTrafficPolicy (spec §7.9) ----------------------------------------------------------------------


def a_wait(
    agv: AGV,
    *blockers: Blocker,
    reviews: int = 0,
    since: float = 0.0,
    loaded: bool = False,
    resolutions: int = 0,
    priority: float = 0.0,
) -> Wait:
    return Wait(agv, Claim((), 1), since, blockers, reviews, (), loaded, resolutions, priority)


class TestStandardTrafficPolicy:
    @pytest.fixture
    def w(self) -> World:
        return world({"A": "N0", "B": "N1", "C": "N2", "D": "N3", "E": "N4"})

    def test_first_block_and_review(self, w: World) -> None:
        policy = StandardTrafficPolicy()
        blocker = Blocker(w.agvs["B"], Disposition.MOVING, (w.nodes["N1"], w.seg("N0", "N1")))
        other = Blocker(w.agvs["C"], Disposition.IDLE, (w.nodes["N2"],))
        view = w.controller.view
        assert policy.on_blocked(a_wait(w.agvs["A"], blocker, other), view) == (KeepWaiting(30.0),)
        assert policy.on_blocked(a_wait(w.agvs["A"], blocker, other, reviews=1), view) == (
            Reroute(frozenset({w.nodes["N1"], w.seg("N0", "N1"), w.nodes["N2"]}), 1.5),
            KeepWaiting(30.0),
        )

    def test_a_stranded_blocker_is_avoided_at_once(self, w: World) -> None:
        policy = StandardTrafficPolicy(reroute_after=None, max_detour=2.0)
        stranded = Blocker(w.agvs["B"], Disposition.STRANDED, (w.nodes["N1"],))
        other = Blocker(w.agvs["C"], Disposition.MOVING, (w.nodes["N2"],))
        assert policy.on_blocked(a_wait(w.agvs["A"], other, stranded), w.controller.view) == (
            Reroute(frozenset({w.nodes["N1"]})),
            KeepWaiting(None),
        )

    @pytest.mark.parametrize("resolutions", [7, 8, 9])
    def test_reroutes_are_offered_only_below_the_resolution_bound(self, w: World, resolutions: int) -> None:
        policy = StandardTrafficPolicy()
        view = w.controller.view
        stranded = Blocker(w.agvs["B"], Disposition.STRANDED, (w.nodes["N1"],))
        moving = Blocker(w.agvs["C"], Disposition.MOVING, (w.nodes["N2"],))
        keep = KeepWaiting(30.0)
        below = resolutions < policy.max_resolutions
        assert policy.on_blocked(a_wait(w.agvs["A"], stranded, resolutions=resolutions), view) == (
            (Reroute(frozenset({w.nodes["N1"]})), keep) if below else (keep,)
        )
        assert policy.on_blocked(a_wait(w.agvs["A"], moving, reviews=2, resolutions=resolutions), view) == (
            (Reroute(frozenset({w.nodes["N2"]}), 1.5), keep) if below else (keep,)
        )
        assert policy.on_blocked(a_wait(w.agvs["A"], moving, resolutions=resolutions), view) == (keep,)

    def test_deadlock_order_and_the_resolution_bound(self, w: World) -> None:
        policy = StandardTrafficPolicy(max_resolutions=2)
        a, b, c, d, e = (w.agvs[name] for name in "ABCDE")
        res = w.nodes["N0"]
        blocked = Blocker(a, Disposition.WAITING, (res,))
        waits = [
            a_wait(a, blocked, priority=1.0),
            a_wait(b, blocked, loaded=True),
            a_wait(c, blocked, since=1.0),
            a_wait(d, blocked, since=2.0, resolutions=2),  # at the bound, unloaded: offered GiveUp only
            a_wait(e, blocked, since=1.0, loaded=True, resolutions=5),  # at the bound and loaded: left out
        ]
        avoid = frozenset({res})
        assert policy.on_deadlock(waits, w.controller.view) == [
            (d, GiveUp()),
            (c, Reroute(avoid)),
            (b, Reroute(avoid)),
            (a, Reroute(avoid)),
            (c, StepAside()),
            (b, StepAside()),
            (a, StepAside()),
        ]

    def test_deadlock_ties_break_by_id(self, w: World) -> None:
        policy = StandardTrafficPolicy()
        b, c = w.agvs["B"], w.agvs["C"]
        assert [agv.id for agv, _ in policy.on_deadlock([a_wait(c), a_wait(b)], w.controller.view)] == [
            "B",
            "C",
            "B",
            "C",
        ]

    def test_make_way_target_is_the_standard_search(self, w: World) -> None:
        policy = StandardTrafficPolicy()
        assert policy.make_way_target(w.agvs["B"], a_wait(w.agvs["A"]), w.controller.view) is None
        assert (policy.reroute_after, policy.max_detour, policy.max_resolutions) == (30.0, 1.5, 8)

    def test_with_the_controller_a_review_reroutes_with_the_detour_bound(self) -> None:
        w = world({"A": "N1", "B": "N0"})
        w.state("A", AGVState.TRAVELING_EMPTY)
        made = hooks(accept=("try_reroute",))
        request = w.wait("B", "N1", with_hooks=made)
        assert [name for name, _ in made.calls if name in ACTIONS] == []
        w.env.run()
        assert made.calls[-1] == ("try_reroute", (frozenset({w.nodes["N1"]}), 1.5))
        assert request.event.value == Rerouted("blocked")


# --- minor rulings on request, place and release -------------------------------------------------------------


class TestRequestAndRelease:
    def test_request_and_place_check_the_extent_first(self) -> None:
        w = world({"A": "N1", "B": "N0"})
        ghost = cart(length=None, name="ghost")
        vehicle = AGV(env=w.env, agv_type=ghost, agv_id="G", initial_node=w.env.layout.node("N3"))
        with pytest.raises(ValueError, match="'ghost'"):
            w.controller.request(vehicle, Claim((ClaimItem(w.nodes["N1"]),), 1), fake_hooks())
        assert w.controller._queue == []
        with pytest.raises(ValueError, match="'ghost'"):
            w.controller.place(vehicle, w.nodes["N3"])
        assert w.controller.view.holders(w.nodes["N3"]) == ()

    def test_a_claim_is_deduplicated(self) -> None:
        w = world({"A": "N1", "B": "N0"})
        request = w.ask("B", "N2", "N2")
        assert request.granted
        assert request.claim.items == (ClaimItem(w.nodes["N2"]),)
        assert [e.resources for e in w.replay.of(TrafficReserved)][-1] == ("N2",)

    def test_release_ignores_a_container_with_held_members(self) -> None:
        w = world({"A": "N0"}, shape=lambda env: line(env, 4, zones={"Z": {"members": ["N1", "N2"]}}))
        (zone,) = w.controller.network.zones
        assert w.ask("A", zone, "N1").granted
        w.release("A", zone)
        assert w.controller.view.holders(zone) == (w.agvs["A"],)
        w.release("A", zone, "N1")
        assert w.controller.view.holders(zone) == ()
        assert w.replay.of(TrafficReleased)[-1].resources == ("Z", "N1")
