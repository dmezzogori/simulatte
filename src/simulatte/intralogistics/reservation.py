"""The traffic controller that enforces occupancy with claims (spec §6, §7).

An AGV asks :class:`ReservationController` for a :class:`~simulatte.intralogistics.network.Claim` and gets all of it or
waits. The controller orders pending claims by priority and request counter behind the fairness gate of §7.4 and grants
those that are admissible (§7.3). Its :class:`~simulatte.intralogistics.traffic_holdings.HoldingsLedger` keeps the
holdings of every resource, frees them on release (§7.5) and records them in the entities: each segment, zone and
corridor is an entity (``segment``, ``zone``, in :mod:`~simulatte.intralogistics.traffic_entities`); nodes record their
holders in the existing ``node`` entities. ``traffic.reserved`` and ``traffic.released`` carry every change of that
state.

An AGV that stops on a pending claim opens a wait (``traffic.wait_started``). The controller then asks idle blockers
to make way (§7.10), looks for a deadlock among the waiting AGVs (§7.8) and asks the policy how the AGV reacts
(§7.9), staging the first reaction that the travel's hooks accept; reviews follow on a timer only while the network
changes (T23).
"""

from __future__ import annotations

from bisect import insort
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from simulatte._wire import FrozenMap, freeze
from simulatte.layout.binding import graph_input, pending
from simulatte.intralogistics.events import (
    TrafficDeadlock,
    TrafficWaitEnded,
    TrafficWaitStarted,
    TrafficWaitStuck,
)
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.network import Claim, ClaimItem, Corridor, Segment, TrafficNetwork, Zone
from simulatte.intralogistics.traffic import (
    Blocker,
    ClaimRequest,
    Disposition,
    GaveUp,
    GiveUp,
    Granted,
    KeepWaiting,
    Reroute,
    Rerouted,
    SteppedAside,
    Wait,
)
from simulatte.intralogistics.traffic_entities import SegmentBinding, ZoneBinding
from simulatte.intralogistics.traffic_holdings import HoldingsLedger, extent, no_length
from simulatte.intralogistics.traffic_policy import StandardTrafficPolicy
from simulatte.intralogistics.traffic_view import ReservationView

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from simpy.events import Timeout

    from simulatte.environment import Environment
    from simulatte.layout.network import ResolvedNetwork
    from simulatte.layout.refs import GraphRef
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import LayoutGraph
    from simulatte.intralogistics.network import Resource
    from simulatte.intralogistics.traffic import Reaction, TrafficPolicy, WaitHooks

__all__ = ["ReservationController", "SegmentBinding", "ZoneBinding"]


@dataclass(eq=False)
class _Pending:
    """A pending claim: the request (with its full claim), its queue key and, once the AGV stopped, its wait.

    What the claim still needs is determined from the current holdings whenever it is tested (spec §7.5).
    """

    request: ClaimRequest
    key: tuple[float, int]  # (-priority, request counter)
    stopped: bool = False  # the wait is open
    yielding: bool = False  # set by deadlock detection; the gate skips yielding claims
    since: float = 0.0
    node: str = ""  # the next node, as in traffic.wait_started
    reviews: int = 0
    timer: Timeout | None = None  # the scheduled review
    seen: int = 0  # the change count at the opening of the wait or at its last review
    dormant: float | None = None  # the delay of a review that waits for the next grant or release
    stuck: bool = False  # traffic.wait_stuck was emitted

    @property
    def agv(self) -> AGV:
        return self.request.agv

    @property
    def priority(self) -> float:
        return -self.key[0]


class ReservationController:
    """Grants claims all or nothing and enforces the occupancy of nodes, segments, zones and corridors (spec §7.7).

    The :class:`TrafficNetwork` is built by an activation initializer registered here, from the graph, the capacities
    (explicit ones, then the parking areas and charging stations of the registered fleets, then ``node_capacity``),
    the authored ``zones`` and the derivation flags; its warnings are logged once. The same initializer attaches one
    ``segment`` entity per segment and one ``zone`` entity per zone and corridor, each group in id order. Fleets must
    register before activation and their vehicle types must have a ``length``. ``policy`` defaults to
    :class:`~simulatte.intralogistics.traffic_policy.StandardTrafficPolicy`. ``ledger`` (a
    :class:`~simulatte.intralogistics.traffic_holdings.HoldingsLedger`) holds who holds what. It is the read API of
    the holdings for views and test helpers; only the controller changes it, and nothing outside the controller may.
    """

    controls_occupancy = True

    def __init__(
        self,
        *,
        env: Environment,
        graph: LayoutGraph | GraphRef,
        node_capacity: int = 1,
        capacities: Mapping[Node, int] | None = None,
        zones: Sequence[Zone] = (),
        derive_corridors: bool = True,
        hold_back_dead_ends: bool = True,
        policy: TrafficPolicy | None = None,
        priority_fn: Callable[[AGV], float] | None = None,
    ) -> None:
        self._env = env
        self._graph, self._pending_graph = graph_input(env, graph)
        if env.layout is not None:
            env.layout._register_manager(self)
        else:
            env._register_graph_use()
        self._node_capacity = node_capacity
        self._capacities: dict[Node, int] = dict(capacities or {})
        self._zones = tuple(zones)
        self._derive_corridors = derive_corridors
        self._hold_back_dead_ends = hold_back_dead_ends
        self._policy: TrafficPolicy = StandardTrafficPolicy() if policy is None else policy
        self._priority_fn: Callable[[AGV], float] = priority_fn or (lambda agv: 0.0)
        self._fleets: list[FleetCoordinator] = []
        self._network: TrafficNetwork | None = None
        self.ledger = HoldingsLedger(env)
        self._queue: list[_Pending] = []  # sorted by key
        self._pending: dict[AGV, _Pending] = {}
        self._counter = 0
        self._changes = 0  # grants and releases so far: a review is due again only after one (T23)
        self._unresolved: list[tuple[str, ...]] = []  # deadlocked sets reported unresolved that still stand
        self.view = ReservationView(self)
        env.on_activate(self._finalize)

    # Layout and finalization

    @property
    def graph(self) -> LayoutGraph:
        if self._pending_graph is not None:
            pending(self, "graph")
        assert self._graph is not None
        return self._graph

    def _install_layout_graph(self, network: ResolvedNetwork) -> None:
        self._graph = network.graph
        self._pending_graph = None
        layout = {node: capacity for node, capacity in network.capacities.items() if capacity is not None}
        self._capacities = {**layout, **self._capacities}
        known = {zone.id for zone in self._zones}
        for zone in network.zones:
            if zone.id in known:
                raise ValueError(f"zone id {zone.id!r} is defined both by the controller and by the layout")
            known.add(zone.id)
        self._zones = (*self._zones, *network.zones)

    @property
    def network(self) -> TrafficNetwork:
        """The traffic network; available once the environment is activated."""
        if self._network is None:
            raise RuntimeError("the traffic network is built at activation; activate the environment first")
        return self._network

    def _finalize(self) -> None:
        facilities: dict[str, tuple[Node, int]] = {}  # by entity id: a facility shared by two fleets counts once
        for fleet in self._fleets:
            for area in fleet.parking_areas:
                facilities[area.id] = (area.node, area.capacity)
            for station in fleet.charging_stations:
                facilities[station.id] = (station.node, station.n_slots)
        facility_capacities: dict[Node, int] = {}
        for node, capacity in facilities.values():
            facility_capacities[node] = facility_capacities.get(node, 0) + capacity
        network = TrafficNetwork(
            self.graph,
            node_capacity=self._node_capacity,
            capacities=self._capacities,
            facility_capacities=facility_capacities,
            zones=self._zones,
            derive_corridors=self._derive_corridors,
            hold_back_dead_ends=self._hold_back_dead_ends,
        )
        env = self._env
        for warning in network.warnings:
            env.warning(warning, component="ReservationController")
        self.ledger.build(network)
        self._network = network

    # Protocol

    def register_fleet(self, fleet: FleetCoordinator) -> None:
        """Take the vehicles and facilities of `fleet` into account; only before activation."""
        if self._network is not None:
            raise RuntimeError("register_fleet: the traffic network is already built; register before activation")
        for agv in fleet.fleet:
            if agv.agv_type.length is None:
                raise ValueError(no_length(agv.agv_type))
        self._fleets.append(fleet)

    def place(self, agv: AGV, node: Node) -> None:
        """Hold `node` and every zone and corridor containing it (a corridor without orientation) for `agv`.

        Raises `RuntimeError`, naming the resource and its capacity, when one of them is full, and `ValueError` when the
        AGV's type has no length.
        """
        extent(agv)
        network = self.network
        ledger = self.ledger
        items = [ClaimItem(container) for container in ledger.containers_at(node)]
        items = [item for item in (*items, ClaimItem(node)) if not ledger.holds(agv, item)]
        for item in items:
            if ledger.blockers(agv, item):
                resource = item.resource
                capacity = network.capacity(resource) if isinstance(resource, Node) else cast(Zone, resource).capacity
                raise RuntimeError(
                    f"cannot place {agv.id} at node {node.id}: {resource.id} is fully reserved (capacity {capacity})"
                )
        ledger.place(agv)
        self._take(agv, items)
        self._scan()

    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim:
        """The claim that starts at leg `index` of `route` (spec §7.2)."""
        return self.network.claim(route, index)

    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
        """Ask for `claim` on behalf of `agv`; the returned request's event succeeds with ``Granted`` at the grant.

        What the AGV already holds is dropped from the claim, except a corridor or a segment held in another direction
        (spec §7.2), which is claimed again; if nothing is left the request is granted at once. An item named twice
        counts once. ``priority_fn`` is read here, once. Raises `RuntimeError` when the AGV already has a pending
        request and `ValueError` when its type has no length; the queue is then unchanged.
        """
        extent(agv)
        if agv in self._pending:
            raise RuntimeError(f"{agv.id} already has a pending traffic request")
        items = tuple(dict.fromkeys(claim.items))
        if len(items) < len(claim.items):
            claim = Claim(items, claim.legs)
        request = ClaimRequest(agv, claim, hooks, self._env.event())
        if not self.ledger.needed(agv, claim):
            request.event.succeed(Granted())
            return request
        waiting = _Pending(request, (-self._priority_fn(agv), self._counter))
        self._counter += 1
        insort(self._queue, waiting, key=lambda claim: claim.key)
        self._pending[agv] = waiting
        self._scan()
        return request

    def stopped(self, request: ClaimRequest) -> None:
        """Open the wait of `request`, whose AGV is now at rest; nothing happens unless the request is still pending.

        Emits ``traffic.wait_started``, asks idle blockers to make way (spec §7.10), runs deadlock detection (§7.8)
        and, when the wait is still open and the AGV is not left in an unresolved deadlock, asks the policy how it
        reacts (§7.9).
        """
        agv = request.agv
        waiting = self._pending.get(agv)
        if waiting is None or waiting.request is not request or waiting.stopped:
            return
        waiting.stopped = True
        waiting.since = float(self._env.now)
        waiting.seen = self._changes
        route = request.hooks.route()
        node = route[1] if len(route) > 1 else agv.current_node
        assert node is not None
        waiting.node = node.id
        blockers = self._blocking(waiting)
        env = self._env
        if env.wants(TrafficWaitStarted):
            env.emit(
                TrafficWaitStarted(
                    agv=agv.id,
                    node=waiting.node,
                    resources=tuple(item.resource.id for item in self.ledger.needed(agv, request.claim)),
                    blockers=tuple(blocker.agv.id for blocker in blockers),
                    reason="occupied",
                )
            )
        self._make_way(self._wait(waiting, blockers))
        unresolved = self._detect()
        if waiting in unresolved or self._pending.get(agv) is not waiting:
            return
        kept = self._react(waiting, self._wait(waiting, self._blocking(waiting)))
        if kept is not None and kept.review_after is not None:
            self._schedule(waiting, kept.review_after)

    def idle(self, agv: AGV) -> None:
        """`agv` has just become ``IDLE`` where it stands (spec §7.10): ask it to make way for the open waits it blocks.

        The waits are taken in request order. If there are none, nothing happens. Otherwise the AGV's fleet is asked
        once, with the policy's ``make_way_target`` for the first wait and the AGVs of all of them as the concerned
        ones. When the AGV does not move (it has no fleet, or the fleet refused) each of those waits is checked for the
        stuck condition (spec §7.9) now, with its blockers recomputed, since a sleeping wait has no review left to
        report it. This is not a review: ``on_blocked`` is not called, no review is counted and no timer is scheduled.
        """
        waits = self._waits_blocked_by(agv)
        if not waits:
            return
        fleet = agv.fleet
        first = waits[0]
        if fleet is not None:
            target = self._policy.make_way_target(agv, self._wait(first, self._blocking(first)), self.view)
            if fleet.make_way(agv, target, concerned=tuple(waiting.agv for waiting in waits)):
                return
        for waiting in waits:
            self._report_stuck(waiting, self._blocking(waiting))

    def _waits_blocked_by(self, agv: AGV) -> list[_Pending]:
        """The open waits blocked by a resource `agv` holds, in request order."""
        return [
            waiting
            for waiting in sorted((waiting for waiting in self._queue if waiting.stopped), key=lambda w: w.key[1])
            if any(
                agv in self.ledger.blockers(waiting.agv, item)
                for item in self.ledger.needed(waiting.agv, waiting.request.claim)
            )
        ]

    def withdraw(self, request: ClaimRequest) -> None:
        """Cancel `request` if it is still pending; an open wait ends with ``interrupted``."""
        waiting = self._pending.get(request.agv)
        if waiting is None or waiting.request is not request:
            return
        self._queue.remove(waiting)
        del self._pending[request.agv]
        self._end_wait(waiting, "interrupted")
        self._scan()

    def release(self, agv: AGV, resources: Collection[Resource]) -> None:
        """Free the given resources that `agv` holds, and each zone or corridor whose last held member goes with them.

        Resources the AGV does not hold are ignored, and so is a zone or corridor of which the AGV keeps a member. Three
        containers stay (spec §7.5): one held ahead of entry (no member held), one named by the AGV's pending claim,
        and a corridor left onto a port the AGV still holds, which then goes with that port.
        """
        self._drop(agv, self.ledger.releasing(agv, resources, self._named(agv)))

    def retain(self, agv: AGV, resources: Collection[Resource]) -> None:
        """Free everything `agv` holds except `resources`, the zones and corridors containing one of them, those its
        pending claim names, and a corridor with an exit record whose port is kept, unless the AGV rests on that port
        and its capacity is above 1: the travel ends there (spec §7.5)."""
        self._drop(agv, self.ledger.retaining(agv, resources, self._named(agv)))

    def _named(self, agv: AGV) -> set[Resource]:
        """The resources named by the pending claim of `agv`, if any."""
        waiting = self._pending.get(agv)
        return set() if waiting is None else {item.resource for item in waiting.request.claim.items}

    # Admission

    def _gating(
        self,
        agv: AGV,
        items: Iterable[ClaimItem],
        earlier: Iterable[_Pending],
        among: Collection[AGV] | None = None,
    ) -> Iterator[tuple[_Pending, ClaimItem]]:
        """The earlier pending claims whose fairness gate (spec §7.4) holds the claim of `items` by `agv`, each with the
        item it is gated on.

        A claim is gated on a segment or corridor ``r`` it names, and that its AGV does not hold, when an earlier,
        non-yielding claim names ``r``, is inadmissible on ``r``, and each of its other resources is admissible or
        blocked only by holders of ``r``. With `among`, only the holdings of those AGVs exist.
        """
        others = [
            (claim, self.ledger.needed(claim.agv, claim.request.claim)) for claim in earlier if not claim.yielding
        ]
        for item in items:
            resource = item.resource
            if not isinstance(resource, (Segment, Corridor)):
                continue
            holders = self.ledger.holders(resource)
            if agv in holders:
                continue
            for other, needed in others:
                mine = next((own for own in needed if own.resource == resource), None)
                if mine is None or not self.ledger.blockers(other.agv, mine, among):
                    continue
                if all(
                    all(blocker in holders for blocker in self.ledger.blockers(other.agv, own, among))
                    for own in needed
                    if own is not mine
                ):
                    yield other, item

    def _gated(
        self,
        agv: AGV,
        items: Iterable[ClaimItem],
        earlier: Iterable[_Pending],
        among: Collection[AGV] | None = None,
    ) -> bool:
        """Whether the claim of `items` by `agv` is held by the fairness gate of an earlier pending claim."""
        return next(self._gating(agv, items, earlier, among), None) is not None

    def _admissible(self, agv: AGV, claim: Claim) -> bool:
        """Admission and gate for `claim`, as for a new request of `agv`."""
        items = self.ledger.needed(agv, claim)
        if any(self.ledger.blockers(agv, item) for item in items):
            return False
        key = (-self._priority_fn(agv), self._counter)
        earlier = [waiting for waiting in self._queue if waiting.key < key and waiting.agv is not agv]
        return not self._gated(agv, items, earlier)

    def _scan(self) -> None:
        """Grant, in queue order, every pending claim that is admissible and not gated, until none is."""
        granted = True
        while granted:
            granted = False
            earlier: list[_Pending] = []
            for waiting in list(self._queue):
                agv = waiting.agv
                items = self.ledger.needed(agv, waiting.request.claim)
                if any(self.ledger.blockers(agv, item) for item in items) or self._gated(agv, items, earlier):
                    earlier.append(waiting)
                    continue
                self._queue.remove(waiting)
                del self._pending[agv]
                self._end_wait(waiting, "granted")
                self._take(agv, items)
                waiting.request.event.succeed(Granted())
                granted = True
        for waiting in self._queue:  # dormant waits are reviewed once the network has changed (spec §7.9)
            if waiting.dormant is not None and waiting.seen != self._changes:
                delay, waiting.dormant = waiting.dormant, None
                waiting.seen = self._changes  # the wake-up consumes the change
                self._schedule(waiting, delay)

    # Waits, reviews and deadlocks (spec §7.8-§7.10)

    def _blocking(self, waiting: _Pending) -> tuple[Blocker, ...]:
        """The blockers of a pending claim: the AGVs whose holdings make one of its resources inadmissible, by the
        claim's resource order and then grant order, followed by the AGVs whose earlier claims gate it."""
        agv = waiting.agv
        items = self.ledger.needed(agv, waiting.request.claim)
        found: dict[AGV, dict[Resource, None]] = {}
        for item in items:
            for holder in self.ledger.blockers(agv, item):
                found.setdefault(holder, {})[item.resource] = None
        earlier = self._queue[: self._queue.index(waiting)]
        for gate, item in self._gating(agv, items, earlier):
            found.setdefault(gate.agv, {})[item.resource] = None
        view = self.view
        return tuple(Blocker(holder, view.disposition(holder), tuple(resources)) for holder, resources in found.items())

    def _wait(self, waiting: _Pending, blockers: tuple[Blocker, ...]) -> Wait:
        """The policy's picture of an open wait."""
        hooks = waiting.request.hooks
        return Wait(
            agv=waiting.agv,
            claim=waiting.request.claim,
            since=waiting.since,
            blockers=blockers,
            reviews=waiting.reviews,
            route=tuple(hooks.route()),
            loaded=hooks.loaded(),
            resolutions=hooks.resolutions(),
            priority=waiting.priority,
        )

    def _make_way(self, wait: Wait) -> None:
        """Ask each idle blocker of a fleet to move out of the way, once (spec §7.10)."""
        view = self.view
        for blocker in wait.blockers:
            agv = blocker.agv
            if blocker.disposition is not Disposition.IDLE or agv.fleet is None:
                continue
            target = self._policy.make_way_target(agv, wait, view)
            concerned = (wait.agv, *(other for other in view.waiting_on(agv) if other is not wait.agv))
            agv.fleet.make_way(agv, target, concerned=concerned)

    def _react(self, waiting: _Pending, wait: Wait) -> KeepWaiting | None:
        """Try the reactions of ``on_blocked`` in order (spec §7.9): None when one was staged, otherwise the
        ``KeepWaiting`` that ended the sequence (``KeepWaiting()`` when it ran out)."""
        for reaction in self._policy.on_blocked(wait, self.view):
            if isinstance(reaction, KeepWaiting):
                return reaction
            if self._attempt(waiting, reaction, "blocked") is not None:
                self._scan()
                return None
        return KeepWaiting()

    def _attempt(self, waiting: _Pending, reaction: Reaction, cause: str) -> str | None:
        """Try `reaction` through the request's hooks; when they accept, end the wait and the request with the
        directive and return the ``traffic.deadlock`` action name. The caller scans."""
        hooks = waiting.request.hooks
        if isinstance(reaction, Reroute):
            staged = hooks.try_reroute(reaction.avoid, reaction.max_cost_ratio)
            action, reason, outcome = "reroute", "rerouted", Rerouted(cause)
        elif isinstance(reaction, GiveUp):
            staged = hooks.give_up()
            action, reason, outcome = "give_up", "gave_up", GaveUp()
        else:
            assert not isinstance(reaction, KeepWaiting)
            staged = hooks.try_step_aside(reaction.target)
            action, reason, outcome = "step_aside", "stepped_aside", SteppedAside()
        if not staged:
            return None
        self._queue.remove(waiting)
        del self._pending[waiting.agv]
        self._end_wait(waiting, reason)
        waiting.request.event.succeed(outcome)
        return action

    def _schedule(self, waiting: _Pending, delay: float) -> None:
        """Schedule the next review of the wait `delay` from now."""
        timer = self._env.timeout(delay)
        timer.callbacks.append(lambda _: self._review(waiting))
        waiting.timer = timer

    def _review(self, waiting: _Pending) -> None:
        """A review: make way and ``on_blocked`` again, with the blockers recomputed before and after the make-way step
        (spec §7.9).

        When it keeps waiting with a delay, the next review is scheduled if a grant or release happened since the
        previous review (or the opening, or the wake-up); otherwise the wait is dormant until the next one (T23).
        """
        waiting.timer = None
        if self._pending.get(waiting.agv) is not waiting:  # the wait ended meanwhile
            return
        waiting.reviews += 1
        changed = waiting.seen != self._changes
        waiting.seen = self._changes
        self._make_way(self._wait(waiting, self._blocking(waiting)))
        if self._pending.get(waiting.agv) is not waiting:  # making way granted or withdrew the claim
            return
        wait = self._wait(waiting, self._blocking(waiting))  # a blocker that accepted a make-way move is moving now
        kept = self._react(waiting, wait)
        if kept is None:
            return
        if kept.review_after is not None:
            if changed:
                self._schedule(waiting, kept.review_after)
            else:
                waiting.dormant = kept.review_after
        self._report_stuck(waiting, wait.blockers)

    def _report_stuck(self, waiting: _Pending, blockers: tuple[Blocker, ...]) -> None:
        """Emit ``traffic.wait_stuck`` and log a warning, once per wait, when the wait is stuck (spec §7.9).

        A wait whose AGV is a member of a deadlocked set left unresolved is not reported: the deadlock report stands
        for it.
        """
        if waiting.stuck or any(waiting.agv.id in ids for ids in self._unresolved):
            return
        found = self._stuck(waiting, blockers)
        if found is None:
            return
        waiting.stuck = True
        agv = waiting.agv
        env = self._env
        if env.wants(TrafficWaitStuck):
            env.emit(TrafficWaitStuck(agv=agv.id, blockers=cast("FrozenMap", freeze(found))))
        named = ", ".join(f"{blocker} ({state})" for blocker, state in found.items())
        env.warning(f"wait of {agv.id} is stuck: blockers {named}", component="ReservationController")

    def _stuck(self, waiting: _Pending, blockers: tuple[Blocker, ...]) -> dict[str, str] | None:
        """The disposition of each of the given blockers of `waiting` by AGV id when the wait is stuck, otherwise None.

        A wait is stuck when every blocker is stalled, idle or stranded, or is waiting in a wait that is stuck by the
        same rule; a cycle of such waits is stuck. The waits reached through waiting blockers are taken as they stand
        now, with their blockers recomputed: one blocker among them that is none of these (moving, serving, charging)
        makes every wait that reaches it not stuck, so the wait is stuck exactly when no wait it reaches has one.
        """
        found: dict[str, str] = {}
        reached = {waiting}
        todo = [(waiting, blockers)]
        while todo:
            current, current_blockers = todo.pop()
            for blocker in current_blockers:
                motion = blocker.agv.motion
                if motion is not None and motion.get("stalled"):
                    state = "STALLED"
                elif blocker.disposition in (Disposition.IDLE, Disposition.STRANDED, Disposition.WAITING):
                    state = blocker.disposition.name
                else:
                    return None
                if current is waiting:
                    found[blocker.agv.id] = state
                if state == "WAITING":
                    behind = self._pending[blocker.agv]
                    if behind not in reached:
                        reached.add(behind)
                        todo.append((behind, self._blocking(behind)))
        return found

    def _end_wait(self, waiting: _Pending, reason: str) -> None:
        """Close the open wait of a claim that left the queue: drop its review and emit ``traffic.wait_ended``."""
        if not waiting.stopped:
            return
        timer = waiting.timer
        if timer is not None:  # no timer survives its wait (T23)
            waiting.timer = None
            self._env.unschedule(timer)
        agv = waiting.agv
        self._unresolved = [ids for ids in self._unresolved if agv.id not in ids]
        env = self._env
        if env.wants(TrafficWaitEnded):
            env.emit(TrafficWaitEnded(agv=agv.id, node=waiting.node, reason=reason))

    def _reduce(self, waiters: list[_Pending], *, gates: bool) -> list[_Pending]:
        """The waiters that cannot proceed even if every AGV outside them released everything (spec §7.8).

        Repeatedly removes a waiter whose claim is admissible given only the holdings of the remaining ones (and,
        with `gates`, not gated by an earlier remaining claim); the queue order is kept.
        """
        remaining = list(waiters)
        removed = True
        while removed:
            removed = False
            among = {waiting.agv for waiting in remaining}
            for index, waiting in enumerate(remaining):
                agv = waiting.agv
                items = self.ledger.needed(agv, waiting.request.claim)
                if any(self.ledger.blockers(agv, item, among) for item in items):
                    continue
                if gates and self._gated(agv, items, remaining[:index], among):
                    continue
                del remaining[index]
                removed = True
                break
        return remaining

    def _detect(self) -> list[_Pending]:
        """Deadlock detection and resolution among the stopped waiters (spec §7.8); returns the deadlocked set left
        unresolved, empty when there is none.

        Gating claims among the remainder of the reduction with gates are marked yielding and the queue is rescanned,
        without an event. A non-empty ``D`` goes to the policy; after a staged attempt and its rescan the reduction
        runs again, and the resolution repeats for the new ``D`` until it is empty or no attempt succeeds. Each round
        removes one waiter from the queue, so the rounds are bounded by their number.
        """
        waiters = [waiting for waiting in self._queue if waiting.stopped]
        deadlocked = self._reduce(waiters, gates=False)
        remainder = self._reduce(waiters, gates=True)
        among = {waiting.agv for waiting in remainder}
        yielded = False
        for index, waiting in enumerate(remainder):
            items = self.ledger.needed(waiting.agv, waiting.request.claim)
            for gate, _ in self._gating(waiting.agv, items, remainder[:index], among):
                gate.yielding = yielded = True
        if yielded:
            self._scan()
        while deadlocked:
            if not self._resolve(deadlocked):
                return deadlocked
            deadlocked = self._reduce([waiting for waiting in self._queue if waiting.stopped], gates=False)
        return deadlocked

    def _resolve(self, deadlocked: list[_Pending]) -> bool:
        """Stage the first attempt of ``on_deadlock`` that succeeds and rescan, or report the set unresolved once (spec
        §7.8); whether an attempt was staged."""
        env = self._env
        ids = tuple(sorted(waiting.agv.id for waiting in deadlocked))
        members = {waiting.agv: waiting for waiting in deadlocked}
        waits = [self._wait(waiting, self._blocking(waiting)) for waiting in deadlocked]
        for agv, reaction in self._policy.on_deadlock(waits, self.view):
            waiting = members.get(agv)
            if waiting is None or isinstance(reaction, KeepWaiting):
                continue
            action = self._attempt(waiting, reaction, "deadlock")
            if action is not None:
                if env.wants(TrafficDeadlock):
                    env.emit(TrafficDeadlock(agvs=ids, victim=agv.id, action=action))
                self._scan()
                return True
        if ids in self._unresolved:
            return False
        self._unresolved.append(ids)
        if env.wants(TrafficDeadlock):
            env.emit(TrafficDeadlock(agvs=ids, victim=None, action="unresolved"))
        env.error(f"deadlock among {', '.join(ids)}: no resolution succeeded", component="ReservationController")
        return False

    # Holdings

    def _take(self, agv: AGV, items: Sequence[ClaimItem]) -> None:
        """Record `items` as held by `agv` (one ``traffic.reserved``)."""
        self._changes += 1
        self.ledger.take(agv, items)

    def _drop(self, agv: AGV, gone: Collection[Resource]) -> None:
        """Free `gone` (resources `agv` holds; one ``traffic.released``) and scan."""
        if not gone:
            return
        self._changes += 1
        self.ledger.drop(agv, gone)
        self._scan()
