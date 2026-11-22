"""Travel execution (spec §8, §9): moving an AGV along a route through the claims of its traffic controller.

:class:`TravelExecutor` replaces the coordinator's old ``_travel``. For one travel it asks the fleet's router for a
route, emits ``agv.routed``, requests claims from the controller and waits, at rest, while the claim it needs next is
pending. A vehicle whose profile reports kinematic limits moves in runs: it keeps claims one arc plus its braking
distance ahead, plans its speeds from its node to the last node it holds and passes nodes at speed (§9.2, §9.3). A
profile without limits traverses each arc rest to rest with its travel time and motion description. At a node the
AGV type's ``corner_speed`` caps the speed and its ``turn_time`` makes the vehicle stop and wait at rest before it
leaves (§9.4). A stop request received while moving (an interrupt, a battery found critical at a node passed at speed)
truncates the plan at the first node of the horizon where the vehicle can be at rest; the energy horizon cuts the plan
at the arcs whose estimate at the arc cap the battery affords (§8.3, §9.5).
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

from simulatte._wire import FrozenMap, freeze, frozen_map, wire_float
from simulatte.events import Deltas
from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveInterrupted, AgvMoveStarted, AgvRouted
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.kinematics import backward_pass, plan_run
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

__all__ = ["TravelExecutor", "TravelOutcome", "estimate_arc_energy", "standard_target"]


class TravelOutcome(Enum):
    """How a travel ended (spec §8.2)."""

    ARRIVED = auto()
    RETRY_FROM_CURRENT_POSITION = auto()
    BATTERY_STRANDED = auto()
    NO_ROUTE = auto()
    GAVE_UP = auto()


_OWN_REASONS = frozenset({"make_way", "clear_node"})
"""Purposes that ``agv.routed`` reports as its reason; every other purpose is reported as ``travel``."""

_GOES_ON_CRITICAL = frozenset({"charge", "after_charge"})
"""Purposes on which a critical level is not a stop request (spec §8.3): ``charge``, a travel to a charger, and
``after_charge``, the travel a mission resumes after a completed charge."""

_TOLERANCE = 1e-9
"""Relative slack when comparing a clear point with the odometer at the end of an arc."""


_HEADING_TOLERANCE = 1e-9
"""Heading changes below this many radians are taken as 0 (spec §9.4)."""

_ENERGY_SLACK = 1e-12
"""Relative slack (of the battery capacity) on the level a plan expects at a node: a level lower by more than this makes
the energy horizon worth recomputing."""

_STOP_SLACK = 1e-12
"""Relative slack on the braking distance when a stop request looks for the first node at which the AGV can be at rest,
the slack :func:`~simulatte.intralogistics.kinematics.plan_run` allows an entry speed."""


def estimate_arc_energy(
    battery: Battery, kin: Kinematics, length: float, load_weight: float, speed_limit: float | None
) -> float:
    """The energy estimated for an arc of `length` under the limits `kin` (spec §9.5): the battery's depletion function
    at the arc cap ``u = min(v_max, speed_limit)``, at speed 0 for a vehicle that cannot move. The check at rest, the
    energy horizon and the reachability of a charger all use it, so that they cannot disagree."""
    cap = kin.v_max if speed_limit is None else min(kin.v_max, speed_limit)
    return battery.estimate_energy(length, load_weight, max(cap, 0.0))


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


def _described(profile: ArcProfile) -> MotionDescription:
    """:meth:`ArcProfile.description` as the canonical map :func:`~simulatte._wire.freeze` makes of it.

    When the entry speed, the length and every phase value are exact floats, which ``freeze`` keeps as they are, the
    map is built directly with the phases as they are (tuples), without ``freeze``'s recursion over them; otherwise
    (a profile without phases, or numbers of another type) it is the plain description, which ``freeze`` converts.
    """
    phases, v_in, length = profile.phases, profile.v_in, profile.length
    if not phases or type(v_in) is not float or type(length) is not float:
        return profile.description()
    for duration, accel in phases:
        if type(duration) is not float or type(accel) is not float:
            return profile.description()
    return frozen_map({"curve": "piecewise", "distance": length, "phases": phases, "v0": v_in})


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


def _heading_changes(nodes: Sequence[Node], heading: float | None) -> tuple[float | None, ...]:
    """The heading change in ``[0, pi]`` at each node of `nodes` except the last, between the arc that enters it and the
    arc that leaves it (spec §9.4).

    The arc that enters the first node is the one that gave the AGV `heading`; None means it is unknown, and so is the
    change at every node until an arc with a direction has been traversed. An arc whose chord has length 0 has no
    direction: it leaves the heading unchanged, so the change at its ends is measured from the last known one. A change
    below :data:`_HEADING_TOLERANCE` is 0: rounding in the coordinates of collinear nodes is not a turn.
    """
    changes: list[float | None] = []
    for source, target in zip(nodes, nodes[1:]):
        if source.x == target.x and source.y == target.y:
            changes.append(None if heading is None else 0.0)
            continue
        direction = math.atan2(target.y - source.y, target.x - source.x)
        if heading is None:
            changes.append(None)
        else:
            change = abs(direction - heading)
            change = min(change, 2 * math.pi - change)
            changes.append(0.0 if change < _HEADING_TOLERANCE else change)
        heading = direction
    return tuple(changes)


class _Trail:
    """The odometer of an AGV and the clear points of the nodes and segments behind it (spec §6.6).

    It lives as long as the AGV: what a travel leaves held behind the vehicle is released by the next one.
    """

    def __init__(self) -> None:
        self.odometer = 0.0
        self.entries: dict[Resource, float] = {}


@dataclass(slots=True)
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
        self._diverting: dict[AGV, Node] = {}  # AGVs on a charging diversion, with the node it started from

    def travel(self, agv: AGV, goal: Node, *, loaded: bool, purpose: str = "travel") -> ProcessGenerator:
        """Move `agv` to `goal` and return a :class:`TravelOutcome`.

        `loaded` is passed to ``agv.move_started`` and selects the load weight of the arcs; `purpose` goes to the
        router and, for ``make_way`` and ``clear_node``, is the reason of ``agv.routed``. On a travel with purpose
        ``charge`` (to a charger) or ``after_charge`` (the travel a mission resumes after a completed charge) a
        critical level is not a stop request, neither at a node nor as the end of the plan, so the AGV goes on for as
        long as the energy horizon allows and strands only before an arc it cannot afford (spec §8.3). An AGV already
        at the goal
        arrives at once, without events. An interrupt while waiting for a claim takes effect at once; one received
        while moving takes effect where the AGV comes to rest: the end of the arc for a profile without limits, the
        first node of the horizon at which a run can be at rest given its speed and deceleration (spec §8.3). The
        arc being traversed keeps its profile, so the AGV passes its end at the speed planned for it. Either way the
        AGV is at rest at a node and ``agv.move_interrupted`` is emitted before the interrupt is raised again. A turn
        wait is a wait at rest, so an interrupt received during it takes effect at once.
        """
        if agv.current_node == goal:
            return TravelOutcome.ARRIVED
        trip = _Trip(self, agv, goal, loaded, purpose)
        try:
            return (yield from trip.run())
        finally:
            # The hooks' bound methods hold the trip: without this the finished trip, its plans and its profiles are
            # cyclic garbage that only a full collection frees.
            del trip.hooks

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
        # To a charger, or resumed after a charge: a critical level is not a stop request (spec §8.3).
        self.goes_on_critical = purpose in _GOES_ON_CRITICAL
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
        # The heading change at each node of the route before the arc leaving it (None: the heading is unknown, which
        # turns for free), the position of the node whose turn was served already, and the values the type's turning
        # callables gave for the nodes of this route (spec §9.4).
        self.turns: tuple[float | None, ...] = ()
        agv_type = agv.agv_type
        self.turning = agv_type.corner_speed is not None or agv_type.turn_time is not None
        self.turned = -1
        self.corner_caps: dict[int, float] = {}
        self.turn_waits: dict[int, float] = {}
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
        self.stop_at: int | None = None  # the first node where the AGV can be at rest after a stop request while moving
        self.arrival_speed = 0.0  # the planned speed at the end of the arc being traversed
        self.expected: tuple[float, ...] = ()  # the level the estimate foresees at each node of the plan
        # The levels the last energy-horizon scan of this plan carried, from the node at position `floor_from` on, when
        # it found the plan affordable: a level not below them would find the same (:meth:`_energy_stop`).
        self.floor: list[float] | None = None
        self.floor_from = 0
        self.load_weight = 0.0  # the load weight of the run, read when it starts from rest
        # Per route and run: the arc caps ``min(v_max, speed_limit)`` under the run's limits, and the energy estimates
        # of the arcs, filled when first asked for and dropped when the route or the limits change.
        self.arc_caps: list[float] | None = None
        self.costs: dict[int, float] = {}
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
            if self.speed == 0.0:  # at rest: the energy check before the next arc, the turn, then a run starts here
                agv.accrue_idle_power()  # idle power over the waits at rest is accounted before the level is read
                self.load_weight = self._load()
                kin: Kinematics | None = None
                if self.kinematic:
                    kin = self._limits()
                    energy_cost = self._estimate(self.index, kin)
                else:
                    plan, energy_cost = self._rest_plan(current, next_node)
                if agv.battery.level < energy_cost:
                    return (yield from self._lacking_energy(current, energy_cost))
                if self.turned != self.index:  # spec §8.1 step 5, after the energy check of step 4
                    self.turned = self.index
                    wait = self._turn_wait(self.index)
                    if wait > 0.0:
                        self._claim_ahead()  # the claim is asked for on arrival and stays pending during the turn
                        yield from self._turn(wait)
                        agv.accrue_idle_power()  # the limits and the plan of the run read the level after the turn
                        kin = None
                self._start_run(kin)

            if self.request is not None or (self.stop_at is None and self.horizon < len(self.route) - 1):
                self._claim_ahead()  # otherwise there is nothing to take and nothing to ask for
            if self.horizon == self.index:  # at rest at the horizon with a pending request (spec §8.1 step 3)
                outcome = yield from self._wait()
                if outcome is not None:
                    return outcome
                continue  # granted, or the route changed: claim again from rest

            if plan is None:
                plan = self._run_plan()
            yield from self._traverse(current, next_node, plan)
            if self.speed > 0.0:  # passed at speed: a stop request takes effect where the run ends at rest
                self._energy_stop()
                continue
            if self.interrupt is not None:
                self._settle()
                self.executor._interrupted(agv, self.interrupt.cause)
                raise self.interrupt
            if agv.battery.is_critical and next_node != self.goal and not self.goes_on_critical:
                self._settle()
                return TravelOutcome.RETRY_FROM_CURRENT_POSITION

    def _rest_plan(self, current: Node, next_node: Node) -> tuple[_ArcPlan, float]:
        """The rest-to-rest plan of the arc from the profile's ``travel_time`` and motion description, with the energy
        the executor estimates for it (spec §9.1)."""
        agv = self.agv
        graph = self.fleet.graph
        distance = graph.length(current, next_node)
        load_weight = self.load_weight
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

    # Turning (spec §9.4)

    def _turning(self, name: str, node: int) -> float | None:
        """What the AGV type's callable `name` says for the heading change at position `node` of the route, validated;
        None when the type has none or the heading is unknown."""
        agv_type = self.agv.agv_type
        function = getattr(agv_type, name)
        if function is None:
            return None
        angle = self.turns[node]
        if angle is None:
            return None
        value = float(function(angle))
        if math.isnan(value) or value < 0 or (name == "turn_time" and math.isinf(value)):
            allowed = "finite and >= 0" if name == "turn_time" else "not NaN and >= 0"
            raise ValueError(f"AGVType {agv_type.name!r}: {name}({angle}) returned {value}; it must be {allowed}")
        return value

    def _turn_wait(self, node: int) -> float:
        """The time the AGV waits at rest at position `node` before it leaves: ``turn_time`` of the heading change."""
        wait = self.turn_waits.get(node)
        if wait is None:
            wait = self.turn_waits[node] = self._turning("turn_time", node) or 0.0
        return wait

    def _node_cap(self, node: int) -> float:
        """The cap on the speed through the interior position `node` of the route that the turn imposes: 0 where the AGV
        waits to turn, else ``corner_speed`` of the heading change (infinity for none)."""
        cap = self.corner_caps.get(node)
        if cap is None:
            if self._turn_wait(node) > 0.0:
                cap = 0.0
            else:
                cap = self._turning("corner_speed", node)
                cap = math.inf if cap is None else cap
            self.corner_caps[node] = cap
        return cap

    def _turn(self, wait: float) -> ProcessGenerator:
        """Wait `wait` at rest on the AGV's node. It is not a traffic wait: the controller is not told, and the AGV
        holds what its body overlaps and the claims it holds ahead. An interrupt takes effect at once.

        A turn is served once per node and route. A reroute or a step aside gives a new route: its first arc is a new
        direction, so the AGV turns again towards it, measured from the heading it still has (a wait does not change
        it), even if it had turned already towards the first arc of the route it left.

        The idle power drained during the wait is accounted after it, so it is not in the energy check before the next
        arc, which precedes the turn (spec §9.4): the AGV can leave with a level below that arc's estimate, at most one
        arc per turn node, and the depletion floors at 0.
        """
        agv = self.agv
        try:
            yield self.env.timeout(wait)
        except simpy.Interrupt as interrupt:
            self._settle()
            self.executor._interrupted(agv, interrupt.cause)
            raise

    # Runs (spec §9.2, §9.3)

    def _limits(self) -> Kinematics:
        """The limits a run that starts now would sample: the profile's at the load and the battery level now.

        Raises `ValueError`, naming the profile and the values, when a value is not finite, when ``decel`` is negative,
        or when ``decel`` is 0 for a vehicle that can move (a stop cannot be planned). A ``v_max`` or ``accel`` that is
        not positive is a vehicle that cannot move: its run stalls (spec §9.1).
        """
        profile = cast("KinematicProfile", self.agv.agv_type.speed_profile)
        load_weight, battery_level = self.load_weight, self.agv.battery.level_pct
        kin = profile.limits(load_weight, battery_level)
        v_max, accel, decel = kin.v_max, kin.accel, kin.decel
        if not (math.isfinite(v_max) and math.isfinite(accel) and math.isfinite(decel)):
            problem = "every value must be finite"
        elif decel < 0:
            problem = "decel must not be negative"
        elif decel == 0 and v_max > 0 and accel > 0:
            problem = "decel must be positive for a vehicle that can move, else a stop cannot be planned"
        else:
            return kin
        raise ValueError(
            f"{type(profile).__name__}.limits({load_weight!r}, {battery_level!r}) returned {kin!r}: {problem}"
        )

    def _estimate(self, k: int, kin: Kinematics) -> float:
        """The energy estimated for arc `k` of the route under the limits `kin` (spec §9.5): the depletion function at
        the arc cap ``u = min(v_max, speed_limit)``, 0 for a vehicle that cannot move."""
        return estimate_arc_energy(self.agv.battery, kin, self.lengths[k], self.load_weight, self.speed_limits[k])

    def _arc_cost(self, k: int) -> float:
        """:meth:`_estimate` of arc `k` under the run's limits, computed once per route and run.

        The reuse assumes that the battery's depletion function is pure: the same arguments give the same estimate.
        """
        cost = self.costs.get(k)
        if cost is None:
            kin = self.kin
            assert kin is not None
            cost = self.costs[k] = self._estimate(k, kin)
        return cost

    def _arc_caps(self) -> list[float]:
        """The cap ``min(v_max, speed_limit)`` of every arc of the route under the run's limits, computed once per route
        and run (spec §9.2)."""
        caps = self.arc_caps
        if caps is None:
            kin = self.kin
            assert kin is not None
            v_max = kin.v_max
            caps = self.arc_caps = [v_max if limit is None else min(v_max, limit) for limit in self.speed_limits]
        return caps

    def _forget_route_costs(self) -> None:
        """Drop the arc caps and estimates of :meth:`_arc_caps` and :meth:`_arc_cost`: the route or the limits
        changed."""
        self.arc_caps = None
        self.costs = {}

    def _affordable_end(self, limit: int, levels: list[float] | None = None) -> int:
        """The furthest node, up to position `limit`, such that the estimated energy of the arcs from the AGV's node to
        it fits the battery level, and not beyond the first node other than the goal at which the estimated level is
        critical (the energy horizon of spec §9.5, with the estimate of :meth:`_estimate` under the run's limits). On
        a travel to a charger or resumed after a charge a critical level does not end it (spec §8.3).

        The level is carried arc by arc as the depletion on arrival carries it, so that the check at rest, which
        compares the actual level with an arc's estimate, agrees with this horizon to the last bit when the estimate is
        exact. The estimate does not include the idle power of the time the run ahead takes (spec §9.5): the level it
        starts from is the one after the idle power accounted so far, and a battery that idle power empties during the
        run is met by the energy check at rest.

        With `levels`, the level carried to each node it reaches is appended to it.
        """
        battery = self.agv.battery
        level, end = battery.level, self.index
        costs, route, goal, goes_on = self.costs, self.route, self.goal, self.goes_on_critical
        while end < limit:
            cost = costs.get(end)
            if cost is None:
                cost = self._arc_cost(end)
            if level < cost:
                break
            level = max(0.0, level - cost)
            end += 1
            if levels is not None:
                levels.append(level)
            if route[end] != goal and not goes_on and _critical(battery, level):
                break  # the AGV comes to rest where the critical-battery branch runs
        return end

    def _start_run(self, kin: Kinematics | None = None) -> None:
        """A run starts from rest at the AGV's node: sample the limits of a kinematic profile for the whole run, set the
        look-ahead (its braking distance plus the fleet's margin, 0 when the limits cannot move the AGV or for a
        profile without limits) and drop the previous plan (spec §9.2, §9.3).

        `kin` are limits the profile gave for the same load and battery level just before (the energy check at rest,
        with no turn wait since): they are taken as the run's instead of asking the profile again, which assumes that
        its ``limits`` is pure.
        """
        self.end = self.plan_from = self.index
        self.profiles = ()
        self.stop_at = None
        if not self.kinematic:
            return
        kin = self.kin = self._limits() if kin is None else kin
        self._forget_route_costs()
        self.stalled = not (kin.v_max > 0 and kin.accel > 0)
        self.lookahead = 0.0 if self.stalled else kin.v_max**2 / (2 * kin.decel) + self.fleet.lookahead_margin

    def _run_plan(self) -> _ArcPlan:
        """The plan of the next arc of a run (spec §9.2).

        The run is planned from the AGV's node and planned speed to the end node, which is the horizon as far as the
        energy horizon allows, and never nearer than the end of the plan in force; after a stop request it is the node
        where the AGV can be at rest (:meth:`_stop`), whatever the plan in force was. The plan is kept while that end
        does not move, so under free flow it is computed once per run. Arc caps are ``min(v_max, speed_limit)``, node
        caps the smaller cap of the two arcs at the node. A stalled run gets an arc that is never left.
        """
        index, agv = self.index, self.agv
        length, load_weight, speed_limit = self.lengths[index], self.load_weight, self.speed_limits[index]
        kin = self.kin
        assert kin is not None
        if self.stalled:
            profile = agv.agv_type.speed_profile
            description = describe_motion(profile, length, load_weight, agv.battery.level_pct, speed_limit)
            return _ArcPlan(length, load_weight, math.inf, description, lambda covered: math.inf)
        if self.stop_at is not None:
            end = self.stop_at
        else:
            end = max(self.end, index + 1)
            if end < self.horizon:
                end = max(end, self._affordable_end(self.horizon))
        if end != self.end or not self.profiles:
            caps = self._arc_caps()[index:end]
            node_caps = [self.speed, *self._interior_caps(index + 1, caps), 0.0]
            self.profiles = plan_run(self.speed, self.lengths[index:end], caps, node_caps, kin)
            self.plan_from, self.end = index, end
            self.floor = None
            level = agv.battery.level
            idle_power = agv.agv_type.idle_power
            expected = [level]
            for k in range(index, end):  # arc by arc, as the depletion on arrival and the fold of idle power compute it
                level = max(0.0, level - self._arc_cost(k))  # the scan above has estimated most of these arcs
                if idle_power:
                    level = max(0.0, level - idle_power * self.profiles[k - index].duration)
                expected.append(level)
            self.expected = tuple(expected)
        profile = self.profiles[index - self.plan_from]
        return _ArcPlan(length, load_weight, profile.duration, _described(profile), profile.time_at, profile.v_out)

    def _interior_caps(self, first: int, arcs: Sequence[float]) -> list[float]:
        """The caps of the nodes between consecutive arcs of `arcs`, which starts with the arc that enters position
        `first`: the smaller cap of the two arcs and the turn's (spec §9.2). A type that does not turn has no turn cap
        (it would be infinity)."""
        if not self.turning:
            return [min(before, after) for before, after in zip(arcs, arcs[1:])]
        return [min(before, after, self._node_cap(first + i)) for i, (before, after) in enumerate(zip(arcs, arcs[1:]))]

    def _energy_stop(self) -> None:
        """At a node passed at speed, a level that is critical, or below the estimate of the next arc, is a stop request
        whatever level the plan expected there (spec §8.3): the plan is cut at the first node where the AGV can be at
        rest. This check is O(1) and scans nothing. (At the goal the plan ends at rest anyway.) On a travel to a
        charger or resumed after a charge only the estimate counts: a critical level is not a stop request there.

        Otherwise the energy horizon is a stop request when it is nearer than the end of the plan in force (spec §8.3,
        §9.5): the level is lower than the estimate foresaw. The plan is cut at the later of the affordable node and the
        first node where the AGV can be at rest, with either controller.

        The plan keeps the level it expects at each node, computed arc by arc as the depletion on arrival computes it
        (``expected``), with the idle power of the arc's planned duration taken off as the fold at ``agv.move_ended``
        takes it: with a depletion that ignores the speed the actual level is the expected one bit for bit, with or
        without idle power.
        The affordable end is recomputed only when the level is below it by more than a relative slack: nothing is
        scanned at a node where the level is as expected. A scan that finds the plan still affordable keeps the levels
        it carried to each node (``floor``); at a later node of the same plan a level not below the floor's is not
        scanned again, since the scan would carry levels at least as high over the same estimates and find the same
        end (a level and a critical test that only grow with the level). After a deficit that does not grow, as for a
        depletion the estimate under-predicts only on the arcs from and to rest, nothing is scanned again.
        """
        battery, index = self.agv.battery, self.index
        level = battery.level
        if self.route[index] != self.goal and (
            (battery.is_critical and not self.goes_on_critical) or level < self._arc_cost(index)
        ):
            self._stop(index, self.speed)
            return
        if self.expected[index - self.plan_from] - level > _ENERGY_SLACK * battery.capacity:
            floor = self.floor
            if floor is not None and level >= floor[index - self.floor_from]:
                return
            levels = [level]
            affordable = self._affordable_end(self.end, levels)
            if affordable < self.end:
                self._stop(index, self.speed, affordable)
            else:
                self.floor, self.floor_from = levels, index

    def _stop(self, node: int, speed: float, floor: int = 0) -> None:
        """A stop request received while moving (an interrupt, a level found critical or below the next arc's estimate
        at a node passed at speed, or the energy horizon), spec §8.3. `node` is the position of the next node event,
        where the AGV passes at the planned speed `speed`: the end of the arc being traversed, or the node it has just
        reached.

        No further claim is requested. The plan is cut at the first node from `node` on, within the plan in force, whose
        distance from `node` is at least the braking distance ``speed ** 2 / (2 * decel)``, or at `node` for an AGV
        that arrives at rest, and not before position `floor`. The next node event replans to it; the profile of the arc
        being traversed is not changed. Requests combine: the stop node is the nearest of the nodes they ask for, each
        taken no nearer than the first node where the AGV can be at rest when it is made, and it never moves farther.
        """
        stop = node
        if speed > 0.0:
            kin = self.kin
            assert kin is not None  # a vehicle that is moving has sampled limits
            braking = speed**2 / (2 * kin.decel) * (1 - _STOP_SLACK)
            while stop < self.end and self.offsets[stop] - self.offsets[node] < braking:
                stop += 1
        stop = max(stop, floor)
        if self.stop_at is None or stop < self.stop_at:
            self.stop_at = stop

    def _lacking_energy(self, current: Node, energy_cost: float) -> ProcessGenerator:
        """The charging-or-stranded branch before an arc the battery cannot cover, as before the executor.

        A charging diversion that cannot leave the node it started from (the route it takes has a first arc the battery
        cannot cover, though the charger counted as reachable) does not divert again: the AGV is stranded there.
        """
        fleet, agv, env = self.fleet, self.agv, self.env
        self._settle()
        diverting = self.executor._diverting
        charger = None if diverting.get(agv) is current else fleet._find_reachable_charger(agv)
        if charger is not None:
            prior_state = agv.state
            outer = diverting.get(agv)  # a diversion that diverts again from another node restores the guard it found
            diverting[agv] = current
            try:
                outcome = yield from fleet._charge_agv(agv, charger)
            finally:
                if outer is None:
                    del diverting[agv]
                else:
                    diverting[agv] = outer
            if outcome is TravelOutcome.GAVE_UP:
                return TravelOutcome.GAVE_UP  # the diversion gave up: this travel ends where the AGV stopped
            if agv.state is AGVState.STRANDED:
                return TravelOutcome.BATTERY_STRANDED  # the diversion stranded the AGV where it stands
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
            assert arc is not None and arc.length is not None
            lengths.append(arc.length)
            limits.append(arc.speed_limit)
            offsets.append(offsets[-1] + arc.length)
        self.lengths, self.speed_limits, self.offsets = tuple(lengths), tuple(limits), tuple(offsets)
        self._forget_route_costs()
        # Heading changes matter only to a type that turns (spec §9.4); for any other they are never read.
        self.turns = _heading_changes(nodes, self.agv.heading) if self.turning else ()
        self.turned = -1
        self.corner_caps, self.turn_waits = {}, {}
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
        one claim more. It uses the planner's own :func:`backward_pass`, so the two cannot disagree. True for a profile
        without limits and for a stalled run, whose look-ahead is 0.
        """
        kin = self.kin
        if kin is None or self.stalled:
            return True
        horizon = self.horizon
        arcs = self._arc_caps()[first - 1 : horizon]
        caps = self._interior_caps(first, arcs) + [0.0]  # the node caps of plan_run
        bounds = backward_pass(self.lengths[first:horizon], caps, kin.decel)
        return any(bound == cap for bound, cap in zip(bounds[:-1], caps[:-1]))

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
        self.arrival_speed = plan.exit_speed
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
        agv.fold_idle_power(travel_time)  # the move's idle drain is part of the level ``agv.move_ended`` reports
        executor._end_segment(agv, next_node, source=current)
        self.index += 1
        self.speed = plan.exit_speed
        if trail is not None:
            trail.odometer += distance
            if self.speed == 0.0 and self.controller.view.capacity(next_node) > 1:  # at rest with room: all on it
                at_arrival = list(trail.entries)
                self.lifted.clear()  # claims ahead are not overlapped either: nothing to put back into the trail
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
                    self._stop(self.index + 1, self.arrival_speed)

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
