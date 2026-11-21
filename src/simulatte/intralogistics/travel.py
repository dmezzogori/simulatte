"""Travel execution (spec §8): moving an AGV along a route through the claims of its traffic controller.

:class:`TravelExecutor` replaces the coordinator's old ``_travel``. For one travel it asks the fleet's router for a
route, emits ``agv.routed``, requests one claim at a time from the controller (the look-ahead is 0), waits while a
claim is pending, and traverses each arc rest to rest with the speed profile's travel time and motion description.
With a controller that controls occupancy it keeps a trail of the nodes and segments behind the vehicle and
releases each when the tail clears it (§6.6). A waiting travel answers the controller through its
:class:`~simulatte.intralogistics.traffic.WaitHooks`: it accepts a reroute or a step aside by staging the new route.
"""

from __future__ import annotations

import math
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

import simpy

from simulatte._wire import FrozenMap, freeze, wire_float
from simulatte.events import Deltas
from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveInterrupted, AgvMoveStarted, AgvRouted
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.kinematics import rest_to_rest
from simulatte.intralogistics.network import Segment
from simulatte.intralogistics.routing import Route, RouteRequest
from simulatte.intralogistics.speed import KinematicProfile, describe_motion
from simulatte.intralogistics.traffic import GaveUp, Granted, Rerouted, WaitHooks

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from simpy.events import ProcessGenerator, Timeout

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.network import Resource
    from simulatte.intralogistics.speed import MotionDescription
    from simulatte.intralogistics.traffic import ClaimRequest, TrafficController

__all__ = ["TravelExecutor", "TravelOutcome", "standard_target"]


class TravelOutcome(Enum):
    """How a travel ended (spec §8.2)."""

    ARRIVED = auto()
    RETRY_FROM_CURRENT_POSITION = auto()
    BATTERY_STRANDED = auto()
    NO_ROUTE = auto()
    GAVE_UP = auto()


_OWN_REASONS = frozenset({"make_way", "clear_node"})
"""Purposes that ``agv.routed`` reports as its reason; every other purpose is reported as ``travel``."""

_TOLERANCE = 1e-9
"""Relative slack when comparing a clear point with the odometer at the end of an arc."""


def _reached(clear: float, odometer: float) -> bool:
    """Whether the odometer value `odometer` has reached the clear point `clear`, allowing for rounding in the summed
    lengths."""
    return clear <= odometer + _TOLERANCE * max(1.0, abs(odometer))


def _check_route(fleet: FleetCoordinator, route: Route, origin: Node, destination: Node) -> None:
    """Raise `ValueError`, naming the router's class, unless `route` is a simple path of arcs from `origin` to
    `destination`."""
    nodes = route.nodes
    problem = None
    if not nodes or nodes[0] != origin:
        problem = f"it does not start at {origin.id}"
    elif nodes[-1] != destination:
        problem = f"it does not end at {destination.id}"
    elif len(set(nodes)) != len(nodes):
        problem = "it visits a node twice"
    else:
        graph = fleet.graph
        for u, v in zip(nodes, nodes[1:]):
            if graph.arc_between(u, v) is None:
                problem = f"there is no arc from {u.id} to {v.id}"
                break
    if problem is not None:
        raise ValueError(
            f"{type(fleet.router).__name__} returned an invalid route from {origin.id} to {destination.id}: {problem}"
        )


def _load_weight(agv: AGV) -> float:
    load = agv.current_load
    return math.fsum(sku.weight * quantity for sku, quantity in load.items()) if load else 0.0


def standard_target(
    fleet: FleetCoordinator,
    agv: AGV,
    *,
    concerned: Sequence[AGV] = (),
    idle: bool = False,
    purpose: str = "step_aside",
    exclude: Collection[Node] = (),
) -> Node | None:
    """The node of the standard search (spec §7.9) for `agv`, or None when no node qualifies.

    A candidate is any node other than the AGV's that has a free slot now, that the fleet's router reaches with a
    first claim admissible now, and that does not lie ahead on the remaining route of an AGV in `concerned` unless it
    would still have a free slot for each of them after the move. The cheapest by router cost wins, ties by node id.

    With `idle` (the AGV will be idle on arrival) the node of a parking area with a free slot is taken first, cheapest
    first; failing that, the node is not a service node, not the root of a dead-end branch (an idle AGV there stops
    every vehicle that holds back for the branch; members stay valid) and not the node of a parking area that is full.
    A node that a pending claim names is not taken either unless it keeps a free slot for each of those claims after
    the move, and neither is a node whose route ends with a leg that needs a segment, zone or corridor named by a
    pending claim: an idle AGV moved out of one wait's way must not fill what another wait needs.
    `purpose` is the purpose of the move the search is for; the router is asked with it. Nodes in `exclude` are never
    taken (a make-way move does not go back to a node the AGV made way from).
    """
    controller = fleet.traffic_controller
    view = controller.view
    origin = fleet._require_current_node(agv)
    ahead = [view.route_of(other)[1:] for other in concerned]
    parking: set[Node] = set()  # nodes of parking areas with a free slot
    full: set[Node] = set()  # nodes of parking areas that are all full
    service: frozenset[Node] = frozenset()
    if idle:
        for area in fleet.parking_areas:
            (parking if area.available_capacity > 0 else full).add(area.node)
        full -= parking
        service = fleet.service_nodes
    best: Node | None = None
    best_key: tuple[int, float, str] | None = None
    for node in view.graph.nodes:
        if node == origin or node in full or node in exclude:
            continue
        rank = 0 if node in parking else 1
        if rank and (node in service or (idle and view.branch_root(node))):
            continue
        free = view.capacity(node) - len(view.holders(node))
        if free < 1:
            continue
        on_routes = sum(1 for route in ahead if node in route)
        if on_routes and free - 1 < on_routes:
            continue
        if idle:
            named = view.pending(node)
            if named and free - 1 < named:
                continue
        route = fleet.router.route(RouteRequest(agv, origin, node, _load_weight(agv), purpose=purpose), view)
        if route is None:
            continue
        _check_route(fleet, route, origin, node)
        if idle and any(
            view.pending(resource) > 0
            for resource in view.resources(route.nodes[-2], node)
            if not isinstance(resource, Node)
        ):
            continue
        if not view.admissible(agv, controller.next_claim(agv, route.nodes, 0)):
            continue
        key = (rank, route.cost, node.id)
        if best_key is None or key < best_key:
            best, best_key = node, key
    return best


class _Trail:
    """The odometer of an AGV and the clear points of the nodes and segments behind it (spec §6.6).

    It lives as long as the AGV: what a travel leaves held behind the vehicle is released by the next one.
    """

    def __init__(self) -> None:
        self.odometer = 0.0
        self.entries: dict[Resource, float] = {}


class TravelExecutor:
    """Runs the travels of the AGVs of one fleet through its traffic controller and router (spec §8.1)."""

    def __init__(self, fleet: FleetCoordinator) -> None:
        self.fleet = fleet
        self._trails: dict[AGV, _Trail] = {}
        self._resolutions: dict[tuple[AGV, Node], int] = {}

    def travel(self, agv: AGV, goal: Node, *, loaded: bool, purpose: str = "travel") -> ProcessGenerator:
        """Move `agv` to `goal` and return a :class:`TravelOutcome`.

        `loaded` is passed to ``agv.move_started`` and selects the load weight of the arcs; `purpose` goes to the
        router and, for ``make_way`` and ``clear_node``, is the reason of ``agv.routed``. An AGV already at the goal
        arrives at once, without events. An interrupt while waiting for a claim takes effect at once; one received
        during an arc takes effect at the arc's end node (spec §8.3). Either way the AGV is at rest at a node and
        ``agv.move_interrupted`` is emitted before the interrupt is raised again.
        """
        if agv.current_node == goal:
            return TravelOutcome.ARRIVED
        trip = _Trip(self, agv, goal, loaded, purpose)
        return (yield from trip.run())

    def _trail(self, agv: AGV) -> _Trail:
        trail = self._trails.get(agv)
        if trail is None:
            trail = self._trails[agv] = _Trail()
        return trail

    # Motion of one arc

    def _start_segment(
        self,
        agv: AGV,
        source: Node,
        target: Node,
        travel_time: float,
        description: MotionDescription,
        loaded: bool,
    ) -> None:
        """Set the AGV's active motion (spec §6.5) and emit ``agv.move_started``.

        `description` is the speed profile's description of the segment. A non-finite travel time ends at ``+inf``
        and marks the segment ``stalled``.
        """
        env = self.fleet.env
        t_start = float(env.now)
        motion: dict[str, object] = {"from": source.id, "to": target.id, "t_start": t_start}
        if math.isfinite(travel_time):
            t_end = motion["t_end"] = t_start + travel_time
        else:
            t_end = motion["t_end"] = math.inf
            motion["stalled"] = True
        frozen_description = cast("FrozenMap", freeze(description))
        motion["description"] = frozen_description
        frozen = cast("FrozenMap", freeze(motion))
        agv.motion = frozen
        if env.wants(AgvMoveStarted):
            env.emit(
                AgvMoveStarted(
                    agv=agv.id,
                    from_node=source.id,
                    to_node=target.id,
                    t_end=t_end,
                    motion=frozen_description,
                    loaded=loaded,
                    deltas=Deltas.build().set(agv.id, "motion", frozen).done(),
                )
            )

    def _end_segment(self, agv: AGV, node: Node, *, moved: bool = True) -> None:
        """Move the AGV to `node` at the end of a segment, clear its motion and emit ``agv.move_ended``.

        With `moved` false (a stalled segment stopped at its departure node `node`, spec §8.3) only the motion is
        cleared: no relocation, and the battery is unchanged.
        """
        agv._validate_node(node)
        agv.motion = None
        relocated = agv._relocate(node) if moved else None
        env = self.fleet.env
        if env.wants(AgvMoveEnded):
            battery = wire_float(agv.battery.level)
            if relocated is None:
                build = Deltas.build().set(agv.id, "motion", None)
            else:
                build = agv._node_deltas(node, *relocated).set(agv.id, "battery", battery).set(agv.id, "motion", None)
            env.emit(AgvMoveEnded(agv=agv.id, node=node.id, battery=battery, deltas=build.done()))

    def _interrupted(self, agv: AGV, cause: object) -> None:
        """Emit ``agv.move_interrupted`` at the node where the interrupted AGV is at rest."""
        env = self.fleet.env
        if env.wants(AgvMoveInterrupted):
            node = agv.current_node
            env.emit(
                AgvMoveInterrupted(
                    agv=agv.id,
                    node=None if node is None else node.id,
                    reason=cause if isinstance(cause, str) else "interrupted",
                )
            )


class _Trip:
    """One travel of one AGV: its current route, the claims it holds ahead and the hooks it gives the controller."""

    def __init__(self, executor: TravelExecutor, agv: AGV, goal: Node, loaded: bool, purpose: str) -> None:
        fleet = executor.fleet
        self.executor = executor
        self.fleet = fleet
        self.env = fleet.env
        self.controller: TrafficController = fleet.traffic_controller
        self.occupancy = bool(self.controller.controls_occupancy)
        self.agv = agv
        self.goal = goal
        self.loaded = loaded
        self.purpose = purpose
        self.route: tuple[Node, ...] = ()
        self.index = 0  # position of the AGV's node in the route
        self.horizon = 0  # position of the last node the granted claims cover
        self.request: ClaimRequest | None = None
        self.staged: Route | None = None
        self.interrupt: simpy.Interrupt | None = None
        self.hooks = WaitHooks(
            route=self._remaining,
            loaded=self._carrying,
            resolutions=self._resolutions,
            try_reroute=self._try_reroute,
            try_step_aside=self._try_step_aside,
            give_up=self._give_up,
        )

    # The loop (spec §8.1)

    def run(self) -> ProcessGenerator:
        fleet, agv = self.fleet, self.agv
        origin = fleet._require_current_node(agv)
        if not self._adopt(origin, self.goal, self.purpose if self.purpose in _OWN_REASONS else "travel"):
            return TravelOutcome.NO_ROUTE
        while True:
            if self.index == len(self.route) - 1:
                self._retain()
                here = self.route[-1]
                if here == self.goal:
                    self.executor._resolutions.pop((agv, self.goal), None)
                    return TravelOutcome.ARRIVED
                # Arrived where a step aside led: route to the goal again.
                if not self._adopt(here, self.goal, "travel"):
                    return TravelOutcome.NO_ROUTE
                continue

            current, next_node = self.route[self.index], self.route[self.index + 1]
            distance = fleet.graph.length(current, next_node)
            load_weight = _load_weight(agv) if self.loaded else 0.0
            arc = fleet.graph.arc_between(current, next_node)
            assert arc is not None
            speed_limit = arc.speed_limit
            battery_pct = agv.battery.level_pct
            profile = agv.agv_type.speed_profile
            travel_time = profile.travel_time(distance, load_weight, battery_pct, speed_limit=speed_limit)
            # Described now, with the arguments the travel time was just computed from (spec §6.5).
            description = describe_motion(profile, distance, load_weight, battery_pct, speed_limit)
            avg_speed = distance / travel_time if travel_time > 0 else 0.0
            energy_cost = agv.battery.estimate_energy(distance, load_weight, avg_speed)

            if agv.battery.level < energy_cost:
                return (yield from self._lacking_energy(current, energy_cost))

            if self.horizon == self.index:
                outcome = yield from self._claim()
                if outcome is not None:
                    return outcome
                if self.horizon == self.index:  # the route changed: a reroute or a step aside
                    continue

            yield from self._traverse(current, next_node, distance, travel_time, description, load_weight)
            if self.interrupt is not None:
                self._retain()
                self.executor._interrupted(agv, self.interrupt.cause)
                raise self.interrupt
            if agv.battery.is_critical and next_node != self.goal:
                self._retain()
                return TravelOutcome.RETRY_FROM_CURRENT_POSITION

    def _lacking_energy(self, current: Node, energy_cost: float) -> ProcessGenerator:
        """The charging-or-stranded branch before an arc the battery cannot cover, as before the executor."""
        fleet, agv, env = self.fleet, self.agv, self.env
        self._settle()
        charger = fleet._find_reachable_charger(agv)
        if charger is not None:
            prior_state = agv.state
            if (yield from fleet._charge_agv(agv, charger)) is TravelOutcome.GAVE_UP:
                return TravelOutcome.GAVE_UP  # the diversion gave up: this travel ends where the AGV stopped
            fleet._transition_agv(agv, prior_state)
            if agv.battery.level < energy_cost:
                fleet._transition_agv(agv, AGVState.STRANDED)
                fleet._stranded(agv, current, "insufficient_after_charging")
                env.error(
                    f"{agv.agv_id} STRANDED at {current.id} — insufficient energy even after charging",
                    component="FleetCoordinator",
                )
                return TravelOutcome.BATTERY_STRANDED
            return TravelOutcome.RETRY_FROM_CURRENT_POSITION

        fleet._transition_agv(agv, AGVState.STRANDED)
        fleet._stranded(agv, current, "no_reachable_charger")
        env.error(f"{agv.agv_id} STRANDED at {current.id} — no reachable charger", component="FleetCoordinator")
        return TravelOutcome.BATTERY_STRANDED

    # Routes

    def _route(self, origin: Node, destination: Node, avoid: frozenset[Resource] = frozenset()) -> Route | None:
        """The router's route, checked, or None."""
        fleet = self.fleet
        load_weight = _load_weight(self.agv) if self.loaded else 0.0
        request = RouteRequest(self.agv, origin, destination, load_weight, avoid=avoid, purpose=self.purpose)
        route = fleet.router.route(request, self.controller.view)
        if route is not None:
            _check_route(fleet, route, origin, destination)
        return route

    def _adopt(self, origin: Node, destination: Node, reason: str) -> bool:
        """Route from `origin` to `destination` and follow that route; false, with the error logged, when there is
        none."""
        route = self._route(origin, destination)
        if route is None:
            self.env.error(
                f"No path from {origin.id} to {destination.id} for {self.agv.agv_id}", component="FleetCoordinator"
            )
            return False
        self._follow(route, reason)
        return True

    def _follow(self, route: Route, reason: str) -> None:
        """Make `route` the current route, nothing granted ahead, and emit ``agv.routed``."""
        self.route = route.nodes
        self.index = self.horizon = 0
        env = self.env
        if env.wants(AgvRouted):
            nodes = route.nodes
            env.emit(
                AgvRouted(
                    agv=self.agv.id,
                    origin=nodes[0].id,
                    destination=nodes[-1].id,
                    nodes=tuple(node.id for node in nodes),
                    cost=wire_float(route.cost),
                    reason=reason,
                )
            )

    # Claims

    def _claim(self) -> ProcessGenerator:
        """Request the next claim and wait for it; None when granted or when the route changed, else the outcome."""
        controller, agv = self.controller, self.agv
        claim = controller.next_claim(agv, self.route, self.index)
        request = self.request = controller.request(agv, claim, self.hooks)
        event = request.event
        if not event.triggered:
            controller.stopped(request)
        if not event.triggered:
            try:
                yield event
            except simpy.Interrupt as interrupt:
                self._settle()
                self.executor._interrupted(agv, interrupt.cause)
                raise
        self.request = None
        value = event.value
        if isinstance(value, Granted):
            self.horizon = self.index + claim.legs
            if self.occupancy:  # what is claimed again lies ahead now, no longer behind the vehicle
                entries = self.executor._trail(agv).entries
                for item in claim.items:
                    entries.pop(item.resource, None)
            return None
        self._retain()
        if isinstance(value, GaveUp):
            return TravelOutcome.GAVE_UP
        staged = self.staged
        assert staged is not None
        self.staged = None
        self._follow(staged, value.reason if isinstance(value, Rerouted) else "step_aside")
        return None

    def _settle(self) -> None:
        """Withdraw a pending request and keep only what the body overlaps."""
        request = self.request
        if request is not None:
            self.controller.withdraw(request)
        self.request = None
        self._retain()

    def _retain(self) -> None:
        """Keep only what the body overlaps at rest: its node and the trail entries not yet cleared."""
        if self.occupancy:
            agv = self.agv
            here = self.fleet._require_current_node(agv)
            self.controller.retain(agv, [here, *self.executor._trail(agv).entries])

    # One arc (spec §6.6, §8.1 step 6-7)

    def _traverse(
        self,
        current: Node,
        next_node: Node,
        distance: float,
        travel_time: float,
        description: MotionDescription,
        load_weight: float,
    ) -> ProcessGenerator:
        """Traverse the arc rest to rest, releasing each trail entry where the tail clears it."""
        self.executor._start_segment(self.agv, current, next_node, travel_time, description, self.loaded)
        if math.isfinite(travel_time):
            yield from self._move(current, next_node, distance, travel_time, load_weight)
        else:
            yield from self._stall(current)

    def _stall(self, current: Node) -> ProcessGenerator:
        """A segment with an infinite travel time is never left (spec §8.3): nothing is released and no timer runs.

        An interrupt takes effect at once at the departure node: ``agv.move_ended`` there clears the motion, the
        claim ahead goes with ``retain``, then ``agv.move_interrupted`` and the interrupt is raised again.
        """
        executor, agv = self.executor, self.agv
        try:
            yield self.env.event()  # never triggered
        except simpy.Interrupt as interrupt:
            executor._end_segment(agv, current, moved=False)
            self._retain()
            executor._interrupted(agv, interrupt.cause)
            raise

    def _move(
        self, current: Node, next_node: Node, distance: float, travel_time: float, load_weight: float
    ) -> ProcessGenerator:
        """The finite part of :meth:`_traverse`: timers, releases at their clear points, the arrival.

        On arrival at a node of capacity above 1 the whole trail clears with the arrival releases (spec §6.6).
        """
        executor, agv, env = self.executor, self.agv, self.env
        self.fleet._release_slots(agv, current)  # departing from a parking node gives its slot back
        arrival = env.timeout(travel_time)
        at_departure: list[Resource] = []
        inside: dict[float, list[Resource]] = {}
        at_arrival: list[Resource] = []
        trail = executor._trail(agv) if self.occupancy else None
        if trail is not None:
            start, end = trail.odometer, trail.odometer + distance
            extent = self._extent()
            trail.entries.pop(current, None)
            trail.entries[current] = start + extent
            for resource in self.controller.view.resources(current, next_node):
                if isinstance(resource, Segment):
                    trail.entries.pop(resource, None)
                    trail.entries[resource] = end + extent
            times: Callable[[float], float] | None = None  # computed for the first clear point inside the arc
            for resource, clear in trail.entries.items():
                if _reached(clear, start):
                    at_departure.append(resource)
                elif not _reached(clear, end):
                    continue  # still held at the end of this arc
                elif _reached(end, clear):
                    at_arrival.append(resource)
                else:
                    if times is None:
                        times = self._clear_times(distance, travel_time, load_weight, current, next_node)
                    elapsed = times(clear - start)
                    if elapsed >= travel_time:
                        at_arrival.append(resource)
                    else:
                        inside.setdefault(elapsed, []).append(resource)
            self._release(trail, at_departure)
            timers = [(env.timeout(elapsed), inside[elapsed]) for elapsed in sorted(inside)]
            for timer, resources in timers:
                yield from self._sleep(timer)
                self._release(trail, resources)
        yield from self._sleep(arrival)
        avg_speed = distance / travel_time if travel_time > 0 else 0.0
        agv.battery.deplete(distance, load_weight, avg_speed)
        executor._end_segment(agv, next_node)
        self.index += 1
        if trail is not None:
            trail.odometer += distance
            if self.controller.view.capacity(next_node) > 1:  # room for whole vehicles: the body is all on it
                at_arrival = list(trail.entries)
            self._release(trail, at_arrival)

    def _extent(self) -> float:
        """``length + min_gap``; a type without a length counts as length 0 (``ReservationController`` requires one)."""
        agv_type = self.agv.agv_type
        return (0.0 if agv_type.length is None else agv_type.length) + agv_type.min_gap

    def _clear_times(
        self, distance: float, travel_time: float, load_weight: float, current: Node, next_node: Node
    ) -> Callable[[float], float]:
        """The elapsed time at which the front has covered a distance into the arc: exact for kinematic profiles
        (spec §9.1), the arc's arrival for any other."""
        profile = self.agv.agv_type.speed_profile
        if not isinstance(profile, KinematicProfile):
            return lambda covered: travel_time
        limits = profile.limits(load_weight, self.agv.battery.level_pct)
        speed_limit = self.controller.view.speed_limit(current, next_node)
        cap = limits.v_max if speed_limit is None else min(limits.v_max, speed_limit)
        return rest_to_rest(distance, cap, limits).time_at

    def _sleep(self, timer: Timeout) -> ProcessGenerator:
        """Wait for `timer`; an interrupt meanwhile is remembered (the first one) and the wait goes on (spec §8.3)."""
        while True:
            try:
                yield timer
                return
            except simpy.Interrupt as interrupt:
                if self.interrupt is None:
                    self.interrupt = interrupt

    def _release(self, trail: _Trail, resources: list[Resource]) -> None:
        if resources:
            for resource in resources:
                del trail.entries[resource]
            self.controller.release(self.agv, resources)

    # Hooks (spec §7.1)

    def _remaining(self) -> tuple[Node, ...]:
        return self.route[self.index :]

    def _carrying(self) -> bool:
        return self.agv.current_load is not None

    def _resolutions(self) -> int:
        return self.executor._resolutions.get((self.agv, self.goal), 0)

    def _stage(self, route: Route) -> None:
        self.staged = route
        key = (self.agv, self.goal)
        resolutions = self.executor._resolutions
        resolutions[key] = resolutions.get(key, 0) + 1

    def _admissible_now(self, route: Route) -> bool:
        controller = self.controller
        return controller.view.admissible(self.agv, controller.next_claim(self.agv, route.nodes, 0))

    def _try_reroute(self, avoid: frozenset[Resource], max_cost_ratio: float | None) -> bool:
        here = self.route[self.index]
        # Standing on the goal in the middle of a step aside means having arrived: nothing to claim (spec §7.9).
        if here == self.goal:
            self._stage(Route((here,), 0.0))
            return True
        route = self._route(here, self.goal, avoid)
        if route is None or not self._admissible_now(route):
            return False
        if max_cost_ratio is not None:
            base = self._route(here, self.goal)
            if base is None or route.cost > max_cost_ratio * base.cost:
                return False
        self._stage(route)
        return True

    def _try_step_aside(self, target: Node | None) -> bool:
        here = self.route[self.index]
        if target is None:
            view = self.controller.view
            concerned: dict[AGV, None] = dict.fromkeys(view.waiting_on(self.agv))
            request = self.request
            for item in () if request is None else request.claim.items:
                concerned.update(dict.fromkeys(view.holders(item.resource)))
            concerned.pop(self.agv, None)
            target = standard_target(self.fleet, self.agv, concerned=tuple(concerned), purpose="step_aside")
        if target is None or target == here:
            return False
        route = self._route(here, target)
        if route is None or not self._admissible_now(route):
            return False
        self._stage(route)
        return True

    def _give_up(self) -> bool:
        return self.fleet._may_give_up(self.agv)
