"""Travel execution (spec §8, §9): moving an AGV along a route through the claims of its traffic controller.

:class:`TravelExecutor` replaces the coordinator's old ``_travel``. For one travel it asks the fleet's router for a
route, emits ``agv.routed``, requests claims from the controller and waits, at rest, while the claim it needs next is
pending. A vehicle whose profile reports kinematic limits moves in runs: it keeps claims one arc plus its braking
distance ahead, plans its speeds from its node to the last node it holds and passes nodes at speed (§9.2, §9.3). A
profile without limits traverses each arc rest to rest with its travel time and motion description.
With a controller that controls occupancy it keeps a trail of the nodes and segments behind the vehicle and
releases each when the tail clears it (§6.6). A waiting travel answers the controller through its
:class:`~simulatte.intralogistics.traffic.WaitHooks`: it accepts a reroute or a step aside by staging the new route.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

import simpy

from simulatte._wire import FrozenMap, freeze, wire_float
from simulatte.events import Deltas
from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveInterrupted, AgvMoveStarted, AgvRouted
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.kinematics import plan_run
from simulatte.intralogistics.network import Segment
from simulatte.intralogistics.routing import Route, RouteRequest
from simulatte.intralogistics.speed import KinematicProfile, describe_motion
from simulatte.intralogistics.traffic import GaveUp, Granted, Rerouted, WaitHooks

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from simpy.events import ProcessGenerator, Timeout

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.battery import Battery
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.kinematics import ArcProfile, Kinematics
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


def _critical(battery: Battery, level: float) -> bool:
    """Whether `level` is critical for `battery`, as :attr:`Battery.is_critical` judges its current level."""
    return (level / battery.capacity if battery.capacity > 0 else 0.0) <= battery.critical_threshold


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
    load_weight: float = 0.0,
    exclude: Collection[Node] = (),
    roomy: bool = False,
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
    `purpose` and `load_weight` are those of the move the search is for; the router is asked with them, so that the
    search and the move make the same request apart from the destination. Nodes in `exclude` are never
    taken (a make-way move does not go back to a node the AGV made way from). With `roomy` (the fallback of a make-way
    move, spec §7.9) the candidates are the service nodes and roots of capacity above 1, under every other rule except
    one: the segment of the last leg may be named by a pending claim, since the AGV comes to rest on a node with room,
    where its trail clears (§6.6).
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
        excluded = bool(rank) and (node in service or (idle and view.branch_root(node)))
        if roomy:
            if not excluded or view.capacity(node) < 2:
                continue  # the fallback looks only at service nodes and roots with room
        elif excluded:
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
        route = fleet.router.route(RouteRequest(agv, origin, node, load_weight, purpose=purpose), view)
        if route is None:
            continue
        _check_route(fleet, route, origin, node)
        if idle and any(
            view.pending(resource) > 0
            for resource in view.resources(route.nodes[-2], node)
            if not isinstance(resource, Node) and not (roomy and isinstance(resource, Segment))
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


@dataclass(frozen=True)
class _ArcPlan:
    """How one arc is traversed: its length and load, the duration (``inf`` when stalled), the motion description,
    the elapsed time at which the front has covered a distance into the arc, and the planned speed at its end node."""

    length: float
    load_weight: float
    duration: float
    description: MotionDescription
    time_at: Callable[[float], float]
    exit_speed: float = 0.0


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
        while moving takes effect where the AGV comes to rest: the end of the arc for a profile without limits, the
        last node it held when the interrupt arrived for a run (spec §8.3, interim rule). Either way the AGV is at
        rest at a node and ``agv.move_interrupted`` is emitted before the interrupt is raised again.
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

    def _end_segment(self, agv: AGV, node: Node, *, moved: bool = True, source: Node | None = None) -> None:
        """Move the AGV to `node` at the end of a segment, clear its motion and emit ``agv.move_ended``.

        With `source`, the node the segment started from, the AGV's heading becomes the direction of the chord from
        `source` to `node` (unchanged for a chord of length 0). With `moved` false (a stalled segment stopped at its
        departure node `node`, spec §8.3) only the motion is cleared: no relocation, and the battery is unchanged.
        """
        agv._validate_node(node)
        agv.motion = None
        relocated = agv._relocate(node) if moved else None
        if relocated is not None and source is not None and (node.x != source.x or node.y != source.y):
            agv.heading = math.atan2(node.y - source.y, node.x - source.x)
        env = self.fleet.env
        if env.wants(AgvMoveEnded):
            battery = wire_float(agv.battery.level)
            if relocated is None:
                build = Deltas.build().set(agv.id, "motion", None)
            else:
                build = agv._node_deltas(node, *relocated).set(agv.id, "battery", battery).set(agv.id, "motion", None)
                heading = agv.heading
                build.set(agv.id, "heading", None if heading is None else wire_float(heading))
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
        # Trail entries a grant took out of the trail (they lie ahead), with their clear points: those the body still
        # overlaps go back into the trail when the trip comes to rest without having reached them.
        self.lifted: dict[Resource, float] = {}
        # The route's arcs: lengths, speed limits and the distance from the route's start to each node.
        self.lengths: tuple[float, ...] = ()
        self.speed_limits: tuple[float | None, ...] = ()
        self.offsets: tuple[float, ...] = ()
        # The run (spec §9.2, §9.3). A profile with limits moves in runs that start and end at rest; one without them
        # moves rest to rest, arc by arc, with a look-ahead of 0.
        self.kinematic = isinstance(agv.agv_type.speed_profile, KinematicProfile)
        self.kin: Kinematics | None = None  # the limits sampled when the run started from rest
        self.stalled = False  # the sampled limits cannot move the AGV
        self.lookahead = 0.0  # the distance claimed beyond the end of the next arc
        self.speed = 0.0  # the planned speed at the AGV's node: 0 at rest
        self.moving = False  # on an arc, between its departure and its arrival
        self.end = 0  # position of the node where the plan ends at rest; it never moves back within a run
        self.plan_from = 0  # position of the node the profiles start from
        self.profiles: tuple[ArcProfile, ...] = ()
        self.stop_at: int | None = None  # the horizon held when a stop request arrived while moving
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
                self._settle()
                here = self.route[-1]
                if here == self.goal:
                    self.executor._resolutions.pop((agv, self.goal), None)
                    return TravelOutcome.ARRIVED
                # Arrived where a step aside led: route to the goal again.
                if not self._adopt(here, self.goal, "travel"):
                    return TravelOutcome.NO_ROUTE
                continue

            current, next_node = self.route[self.index], self.route[self.index + 1]
            plan: _ArcPlan | None = None
            if self.speed == 0.0:  # at rest: the energy check before the next arc, then a run starts here
                if self.kinematic:
                    energy_cost = self._estimate(self.index)
                else:
                    plan, energy_cost = self._rest_plan(current, next_node)
                if agv.battery.level < energy_cost:
                    return (yield from self._lacking_energy(current, energy_cost))
                self._start_run()

            self._claim_ahead()
            if self.horizon == self.index:  # at rest at the horizon with a pending request (spec §8.1 step 3)
                outcome = yield from self._wait()
                if outcome is not None:
                    return outcome
                continue  # granted, or the route changed: claim again from rest

            if plan is None:
                plan = self._run_plan()
            yield from self._traverse(current, next_node, plan)
            if self.speed > 0.0:  # passed at speed: a stop request takes effect where the run ends at rest
                if agv.battery.is_critical and next_node != self.goal:
                    self._stop()
                continue
            if self.interrupt is not None:
                self._settle()
                self.executor._interrupted(agv, self.interrupt.cause)
                raise self.interrupt
            if agv.battery.is_critical and next_node != self.goal:
                self._settle()
                return TravelOutcome.RETRY_FROM_CURRENT_POSITION

    def _rest_plan(self, current: Node, next_node: Node) -> tuple[_ArcPlan, float]:
        """The rest-to-rest plan of the arc from the profile's ``travel_time`` and motion description, with the energy
        the executor estimates for it (spec §9.1)."""
        agv = self.agv
        graph = self.fleet.graph
        distance = graph.length(current, next_node)
        load_weight = self._load()
        arc = graph.arc_between(current, next_node)
        assert arc is not None
        speed_limit = arc.speed_limit
        battery_pct = agv.battery.level_pct
        profile = agv.agv_type.speed_profile
        travel_time = profile.travel_time(distance, load_weight, battery_pct, speed_limit=speed_limit)
        # Described now, with the arguments the travel time was just computed from (spec §6.5).
        description = describe_motion(profile, distance, load_weight, battery_pct, speed_limit)
        avg_speed = distance / travel_time if travel_time > 0 else 0.0
        energy_cost = agv.battery.estimate_energy(distance, load_weight, avg_speed)
        return _ArcPlan(distance, load_weight, travel_time, description, lambda covered: travel_time), energy_cost

    # Runs (spec §9.2, §9.3)

    def _estimate(self, k: int) -> float:
        """The energy estimated for arc `k` of the route, as the executor estimated it before each arc in part A: the
        depletion at the average speed of the profile's ``travel_time`` at the current battery level."""
        agv = self.agv
        distance, load_weight = self.lengths[k], self._load()
        profile = agv.agv_type.speed_profile
        travel_time = profile.travel_time(
            distance, load_weight, agv.battery.level_pct, speed_limit=self.speed_limits[k]
        )
        avg_speed = distance / travel_time if travel_time > 0 else 0.0
        return agv.battery.estimate_energy(distance, load_weight, avg_speed)

    def _affordable_end(self, limit: int) -> int:
        """The furthest node, up to position `limit`, such that the estimated energy of the arcs from the AGV's node to
        it fits the battery level, and not beyond the first node other than the goal at which the estimated level is
        critical (the energy horizon of spec §9.5, with the estimate of :meth:`_estimate`)."""
        battery = self.agv.battery
        level, total, end = battery.level, 0.0, self.index
        while end < limit:
            total += self._estimate(end)
            if total > level:
                break
            end += 1
            if self.route[end] != self.goal and _critical(battery, level - total):
                break  # the AGV comes to rest where the critical-battery branch runs
        return end

    def _start_run(self) -> None:
        """A run starts from rest at the AGV's node: sample the limits of a kinematic profile for the whole run, set the
        look-ahead (its braking distance plus the fleet's margin, 0 when the limits cannot move the AGV or for a
        profile without limits) and drop the previous plan (spec §9.2, §9.3)."""
        self.end = self.plan_from = self.index
        self.profiles = ()
        self.stop_at = None
        if not self.kinematic:
            return
        profile = cast("KinematicProfile", self.agv.agv_type.speed_profile)
        kin = self.kin = profile.limits(self._load(), self.agv.battery.level_pct)
        self.stalled = not (kin.v_max > 0 and kin.accel > 0)
        self.lookahead = 0.0 if self.stalled else kin.v_max**2 / (2 * kin.decel) + self.fleet.lookahead_margin

    def _run_plan(self) -> _ArcPlan:
        """The plan of the next arc of a run (spec §9.2).

        The run is planned from the AGV's node and planned speed to the end node, which is the horizon (or, after a stop
        request, the horizon held when it arrived) as far as the energy horizon allows, and never nearer than the end
        of the plan in force. The plan is kept while that end does not move, so under free flow it is computed once
        per run. Arc caps are ``min(v_max, speed_limit)``, node caps the smaller cap of the two arcs at the node. A
        stalled run gets an arc that is never left.
        """
        index, agv = self.index, self.agv
        length, load_weight, speed_limit = self.lengths[index], self._load(), self.speed_limits[index]
        kin = self.kin
        assert kin is not None
        if self.stalled:
            profile = agv.agv_type.speed_profile
            description = describe_motion(profile, length, load_weight, agv.battery.level_pct, speed_limit)
            return _ArcPlan(length, load_weight, math.inf, description, lambda covered: math.inf)
        limit = self.horizon if self.stop_at is None else self.stop_at
        end = max(self.end, index + 1)
        if end < limit:
            end = max(end, self._affordable_end(limit))
        if end != self.end or not self.profiles:
            caps = [kin.v_max if cap is None else min(kin.v_max, cap) for cap in self.speed_limits[index:end]]
            node_caps = [self.speed, *(min(a, b) for a, b in zip(caps, caps[1:])), 0.0]
            self.profiles = plan_run(self.speed, self.lengths[index:end], caps, node_caps, kin)
            self.plan_from, self.end = index, end
        profile = self.profiles[index - self.plan_from]
        return _ArcPlan(length, load_weight, profile.duration, profile.description(), profile.time_at, profile.v_out)

    def _stop(self) -> None:
        """A stop request received while moving (an interrupt, or a battery found critical at a node other than the goal
        that the estimate did not foresee): no further claim is requested, and the run ends at rest at the horizon held
        now (interim rule: spec §8.3 truncates the plan to the first node where the AGV can be at rest)."""
        if self.stop_at is None:
            self.stop_at = self.horizon

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

    def _load(self) -> float:
        """The load weight of this travel's arcs and route requests: the cargo's when the travel is loaded, else 0."""
        return _load_weight(self.agv) if self.loaded else 0.0

    def _route(
        self, origin: Node, destination: Node, avoid: frozenset[Resource] = frozenset(), purpose: str | None = None
    ) -> Route | None:
        """The router's route, checked, or None; asked with the travel's purpose unless `purpose` is given."""
        fleet = self.fleet
        request = RouteRequest(
            self.agv,
            origin,
            destination,
            self._load(),
            avoid=avoid,
            purpose=self.purpose if purpose is None else purpose,
        )
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
        """Make `route` the current route, nothing granted ahead, and emit ``agv.routed``. The AGV is at rest."""
        nodes = self.route = route.nodes
        self.index = self.horizon = self.end = self.plan_from = 0
        self.profiles = ()
        graph = self.fleet.graph
        lengths: list[float] = []
        limits: list[float | None] = []
        offsets = [0.0]
        for u, v in zip(nodes, nodes[1:]):
            arc = graph.arc_between(u, v)
            assert arc is not None
            lengths.append(graph.length(u, v))
            limits.append(arc.speed_limit)
            offsets.append(offsets[-1] + lengths[-1])
        self.lengths, self.speed_limits, self.offsets = tuple(lengths), tuple(limits), tuple(offsets)
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

    def _claim_ahead(self) -> None:
        """Step 2 of spec §8.1: request the next claim while none is pending and :meth:`_wants_claim`.

        A pending request already granted (at the instant of a node event, before its callback ran) is taken first,
        at speed as at rest. A grant at once extends the horizon and the step repeats. A request that is not granted at
        once stays pending; its grant is taken by the event's callback, which repeats the step if the AGV is moving
        then, without waking the travel process. A directive is left pending for :meth:`_wait`.
        """
        controller, agv = self.controller, self.agv
        request = self.request
        if request is not None and request.granted:  # granted at this instant, its callback not run yet: take it now
            self._granted(request)
        while self.request is None and self._wants_claim():
            request = self.request = controller.request(
                agv, controller.next_claim(agv, self.route, self.horizon), self.hooks
            )
            if not request.event.triggered:
                self._listen(request)
                return
            self._granted(request)

    def _listen(self, request: ClaimRequest) -> None:
        request.event.callbacks.append(lambda _: self._granted_later(request))

    def _granted_later(self, request: ClaimRequest) -> None:
        """The callback of a request not granted at once: take the grant and, while moving, claim on (spec §8.1)."""
        if self.request is not request:
            return
        self._granted(request)
        if self.moving:
            self._claim_ahead()

    def _wants_claim(self) -> bool:
        """Whether the route has legs beyond the horizon and the horizon still shapes the speed at the end of the next
        arc, the one entered at the next node event (spec §9.3): the horizon lies less than the look-ahead beyond that
        arc's end, or the backward pass from it does not yet give a cap there (:meth:`_capped`). Not after a stop
        request, and not over an arc beyond the energy horizon (the next arc from rest has passed the energy check
        already)."""
        horizon = self.horizon
        if self.stop_at is not None or horizon >= len(self.route) - 1:
            return False
        node = self.index + 1 if self.moving else self.index  # where the next arc starts
        if horizon > node:
            beyond = self.offsets[horizon] - self.offsets[node + 1]  # held beyond the end of the next arc
            if beyond >= self.lookahead and self._capped(node + 1):
                return False
        return horizon == self.index or self._affordable_end(horizon + 1) == horizon + 1

    def _capped(self, first: int) -> bool:
        """Whether the backward pass of :func:`plan_run` over the arcs held, from rest at the horizon back to node
        `first`, reaches the cap of some node at or after `first`.

        From such a node back, the backward pass gives what it gives with any farther horizon, so the plan of the next
        arc is the plan the AGV would have if it held the whole route: under free flow it does (spec §9.3). A rounding
        of the braking distance can leave the pass one unit in the last place below ``v_max``; this test then asks for
        one claim more. True for a profile without limits and for a stalled run, whose look-ahead is 0.
        """
        kin = self.kin
        if kin is None or self.stalled:
            return True
        caps = [kin.v_max if limit is None else min(kin.v_max, limit) for limit in self.speed_limits]
        speed = 0.0
        for j in range(self.horizon - 1, first - 1, -1):
            cap = min(caps[j - 1], caps[j])  # the node cap of plan_run: the smaller cap of the arcs at node j
            length = self.lengths[j]
            speed = min(cap, speed if length == 0 else math.sqrt(speed * speed + 2 * kin.decel * length))
            if speed == cap:
                return True
        return False

    def _wait(self) -> ProcessGenerator:
        """Step 3 of spec §8.1: at rest at the horizon with a pending request, call ``stopped`` (unless the request was
        resolved meanwhile) and wait for its event; None when granted or when the route changed, else the outcome."""
        controller, agv = self.controller, self.agv
        request = self.request
        assert request is not None
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
        value = event.value
        if isinstance(value, Granted):
            self._granted(request)  # nothing left to do when the event's callback took the grant already
            return None
        self.request = None
        self._settle()
        if isinstance(value, GaveUp):
            return TravelOutcome.GAVE_UP
        staged = self.staged
        assert staged is not None
        self.staged = None
        self._follow(staged, value.reason if isinstance(value, Rerouted) else "step_aside")
        return None

    def _granted(self, request: ClaimRequest) -> None:
        """Take the grant of `request` if it is still the trip's pending request: extend the horizon by the claim's
        legs and lift the claimed resources out of the trail (they lie ahead now, no longer behind the vehicle).

        Both the travel process and the request's event callback call it; the second call finds nothing to do, and so
        does a call for a request the trip has withdrawn or that ended with a directive.
        """
        if self.request is not request or not request.granted:
            return
        self.request = None
        claim = request.claim
        self.horizon += claim.legs
        if self.occupancy:
            entries = self.executor._trail(self.agv).entries
            for item in claim.items:
                clear = entries.pop(item.resource, None)
                if clear is not None:
                    self.lifted[item.resource] = clear

    def _settle(self) -> None:
        """Withdraw a pending request and keep only what the body overlaps."""
        request = self.request
        if request is not None:
            self.controller.withdraw(request)
        self.request = None
        self._retain()

    def _retain(self) -> None:
        """Keep only what the body overlaps at rest: its node and the trail entries not yet cleared.

        Entries a grant lifted out of the trail that the odometer has not reached go back into it first: the trip came
        to rest before departing onto them, so the body still overlaps them (spec §6.6).
        """
        lifted, self.lifted = self.lifted, {}
        if self.occupancy:
            agv = self.agv
            here = self.fleet._require_current_node(agv)
            trail = self.executor._trail(agv)
            for resource, clear in lifted.items():
                if resource != here and resource not in trail.entries and not _reached(clear, trail.odometer):
                    trail.entries[resource] = clear
            self.controller.retain(agv, [here, *trail.entries])

    # One arc (spec §6.6, §8.1 step 6-7)

    def _traverse(self, current: Node, next_node: Node, plan: _ArcPlan) -> ProcessGenerator:
        """Traverse the arc as `plan` says, releasing each trail entry where the tail clears it."""
        self.executor._start_segment(self.agv, current, next_node, plan.duration, plan.description, self.loaded)
        if math.isfinite(plan.duration):
            yield from self._move(current, next_node, plan)
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

    def _move(self, current: Node, next_node: Node, plan: _ArcPlan) -> ProcessGenerator:
        """The finite part of :meth:`_traverse`: timers, releases at their clear points, the arrival.

        A clear point is released when the odometer reaches it, at the time the arc's plan gives, also on a later arc of
        the run. On arrival at rest on a node of capacity above 1 the whole trail clears with the arrival releases
        (spec §6.6); a node passed at speed keeps the ordinary clear points.
        """
        executor, agv, env = self.executor, self.agv, self.env
        distance, travel_time = plan.length, plan.duration
        if current != self.goal:  # departing from a parking node gives its slot back, unless the trip returns to it
            self.fleet._release_slots(agv, current)
        self.moving = True
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
            for resource, clear in trail.entries.items():
                if _reached(clear, start):
                    at_departure.append(resource)
                elif not _reached(clear, end):
                    continue  # still held at the end of this arc
                elif _reached(end, clear):
                    at_arrival.append(resource)
                else:
                    elapsed = plan.time_at(clear - start)
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
        self.moving = False
        avg_speed = distance / travel_time if travel_time > 0 else 0.0
        agv.battery.deplete(distance, plan.load_weight, avg_speed)
        executor._end_segment(agv, next_node, source=current)
        self.index += 1
        self.speed = plan.exit_speed
        if trail is not None:
            trail.odometer += distance
            if self.speed == 0.0 and self.controller.view.capacity(next_node) > 1:  # at rest with room: all on it
                at_arrival = list(trail.entries)
            self._release(trail, at_arrival)

    def _extent(self) -> float:
        """``length + min_gap``; a type without a length counts as length 0 (``ReservationController`` requires one)."""
        agv_type = self.agv.agv_type
        return (0.0 if agv_type.length is None else agv_type.length) + agv_type.min_gap

    def _sleep(self, timer: Timeout) -> ProcessGenerator:
        """Wait for `timer`; an interrupt meanwhile is remembered (the first one) as a stop request and the wait goes on
        (spec §8.3)."""
        while True:
            try:
                yield timer
                return
            except simpy.Interrupt as interrupt:
                if self.interrupt is None:
                    self.interrupt = interrupt
                    self._stop()

    def _release(self, trail: _Trail, resources: list[Resource]) -> None:
        """Release the given trail entries that are still in the trail. A grant taken while the AGV moves may have
        lifted one of them since the departure scheduled its release: it lies ahead again and stays held."""
        resources = [resource for resource in resources if resource in trail.entries]
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
            target = standard_target(
                self.fleet, self.agv, concerned=tuple(concerned), purpose="step_aside", load_weight=self._load()
            )
        if target is None or target == here:
            return False
        route = self._route(here, target, purpose="step_aside")  # as the search asked
        if route is None or not self._admissible_now(route):
            return False
        self._stage(route)
        return True

    def _give_up(self) -> bool:
        return self.fleet._may_give_up(self.agv)
