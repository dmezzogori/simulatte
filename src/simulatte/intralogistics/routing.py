"""Routing: the router protocol (spec §10.1) and the shortest-distance, fastest and congestion-aware routers (§10.2)."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from simulatte.intralogistics.speed import KinematicProfile
from simulatte.intralogistics.traffic import Disposition

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from simulatte.intralogistics.agv import AGV, AGVType
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.kinematics import Kinematics
    from simulatte.intralogistics.network import Resource
    from simulatte.intralogistics.traffic import TrafficView


@dataclass(frozen=True)
class RouteRequest:
    agv: AGV | None
    origin: Node
    destination: Node
    load_weight: float = 0.0
    avoid: frozenset[Resource] = frozenset()
    purpose: str = "travel"


@dataclass(frozen=True)
class Route:
    nodes: tuple[Node, ...]  # a simple path from origin to destination
    cost: float  # the router's own unit; comparable only within one router


@runtime_checkable
class Router(Protocol):
    def route(self, request: RouteRequest, view: TrafficView) -> Route | None: ...


_HEADING_TOLERANCE = 1e-9
"""Heading changes below this many radians are taken as 0 (spec §9.4)."""


def arc_direction(source: Node, target: Node) -> float | None:
    """The direction of the chord from `source` to `target` in radians; None for a chord of length 0, which has none."""
    if source.x == target.x and source.y == target.y:
        return None
    return math.atan2(target.y - source.y, target.x - source.x)


def heading_change(heading: float, direction: float) -> float:
    """The change in ``[0, pi]`` from `heading` to `direction`. A change below :data:`_HEADING_TOLERANCE` is 0: rounding
    in the coordinates of collinear nodes is not a turn."""
    change = abs(direction - heading)
    change = min(change, 2 * math.pi - change)
    return 0.0 if change < _HEADING_TOLERANCE else change


def heading_changes(nodes: Sequence[Node], heading: float | None) -> tuple[float | None, ...]:
    """The heading change in ``[0, pi]`` at each node of `nodes` except the last, between the arc that enters it and the
    arc that leaves it (spec §9.4).

    The arc that enters the first node is the one that gave the AGV `heading`; None means it is unknown, and so is the
    change at every node until an arc with a direction has been traversed. An arc whose chord has length 0 has no
    direction: it leaves the heading unchanged, so the change at its ends is measured from the last known one.
    """
    changes: list[float | None] = []
    for source, target in zip(nodes, nodes[1:]):
        direction = arc_direction(source, target)
        if direction is None:
            changes.append(None if heading is None else 0.0)
            continue
        changes.append(None if heading is None else heading_change(heading, direction))
        heading = direction
    return tuple(changes)


def shortest_path(
    graph: LayoutGraph,
    origin: Node,
    destination: Node,
    *,
    allowed: Callable[[Node, Node], bool] | None = None,
    heuristic: bool = False,
    weight: Callable[[Node, Node], float] | None = None,
    penalty: Callable[[Node, Node, float], float] | None = None,
) -> tuple[tuple[Node, ...], float] | None:
    """The path of least summed arc length, or ``None``; ties break by node id.

    ``allowed(u, v)`` returning false excludes the leg from ``u`` to ``v``. With ``heuristic`` the search is A* with
    the chord distance to the destination, which never overestimates because an arc is never shorter than its chord.
    ``weight(u, v)``, non-negative, replaces the arc length as the cost of a leg; it is not meant for ``heuristic``,
    whose estimate is a length. ``penalty(u, v, t)``, non-negative, is added to the cost of the leg from ``u`` to ``v``
    when it is expanded at the accumulated cost ``t``; infinite excludes the leg. A penalty makes the answer a
    heuristic: a leg costs differently at different ``t``, which a shortest-path search does not account for.
    """
    if origin == destination:
        return (origin,), 0.0

    def estimate(node: Node) -> float:
        return math.hypot(destination.x - node.x, destination.y - node.y) if heuristic else 0.0

    best: dict[Node, float] = {origin: 0.0}
    previous: dict[Node, Node] = {}
    heap: list[tuple[float, str, float, Node]] = [(estimate(origin), origin.id, 0.0, origin)]
    while heap:
        _, _, cost, node = heapq.heappop(heap)
        if node == destination:
            path = [node]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            path.reverse()
            return tuple(path), cost
        if cost > best[node]:
            continue
        for neighbor in graph.neighbors(node):
            if allowed is not None and not allowed(node, neighbor):
                continue
            extra = 0.0 if penalty is None else penalty(node, neighbor, cost)
            if extra == math.inf:
                continue
            new_cost = cost + (graph.length(node, neighbor) if weight is None else weight(node, neighbor)) + extra
            if new_cost < best.get(neighbor, math.inf):
                best[neighbor] = new_cost
                previous[neighbor] = node
                heapq.heappush(heap, (new_cost + estimate(neighbor), neighbor.id, new_cost, neighbor))
    return None


class ShortestDistanceRouter:
    """Cost is the path length (``graph.path_length`` of the route); a leg that needs an avoided resource, or enters an
    avoided node, is excluded."""

    def __init__(self, algorithm: Literal["dijkstra", "astar"] = "dijkstra") -> None:
        if algorithm not in ("dijkstra", "astar"):
            raise ValueError(f"unknown algorithm {algorithm!r}")
        self.algorithm = algorithm

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        return self._route(request, view, None)

    def _route(
        self, request: RouteRequest, view: TrafficView, penalty: Callable[[Node, Node, float], float] | None
    ) -> Route | None:
        """The route with `penalty` added to the cost of each leg (see :func:`shortest_path`), for
        :class:`CongestionAwareRouter`; its cost is the path length plus the penalties of its legs."""
        graph = view.graph
        found = shortest_path(
            graph,
            request.origin,
            request.destination,
            allowed=_leg_filter(request.avoid, view),
            heuristic=self.algorithm == "astar",
            penalty=penalty,
        )
        if found is None:
            return None
        path = found[0]
        cost = graph.path_length(path)  # an fsum, as every other user of route cost computes it
        if penalty is not None:
            extras, reached = [], 0.0
            for u, v in zip(path, path[1:]):
                extras.append(extra := penalty(u, v, reached))
                reached = reached + graph.length(u, v) + extra  # as the search accumulated it
            cost += math.fsum(extras)
        return Route(path, cost)


def _leg_filter(avoid: frozenset[Resource], view: TrafficView) -> Callable[[Node, Node], bool] | None:
    """The `allowed` argument of :func:`shortest_path` for a request's `avoid`: a leg is excluded when its end node or
    any resource of ``view.resources(u, v)`` is avoided; None when nothing is."""
    if not avoid:
        return None

    def allowed(u: Node, v: Node) -> bool:
        return v not in avoid and not any(resource in avoid for resource in view.resources(u, v))

    return allowed


_State = tuple["Node", "Node | None"]
"""A state of the turning search: a node and the node the arc that entered it came from (None at the origin)."""


class _Timing:
    """The times a request's AGV needs for a leg and for a turn (spec §10.2).

    The limits of a profile that has them are sampled once, at the request's load and the AGV's battery level now. A
    leg costs ``length / min(v_max, speed_limit)`` with them, else ``travel_time`` of the profile. A turn costs the
    ``turn_time`` of the heading change, plus, for a profile with limits, the time lost slowing from the lower cap ``u``
    of the two arcs to the speed ``c`` the turn allows, and regaining ``u``: ``(u - c) ** 2 / (2 * u) * (1 / accel + 1 /
    decel)`` when ``c < u``. As in the executor, ``c`` is 0 where ``turn_time`` is positive (the vehicle stops) and
    ``corner_speed`` of the heading change otherwise. A profile without limits is at rest at every node: a turn costs
    its ``turn_time`` only. An AGV at the origin is at rest too: the first turn costs ``turn_time`` only.
    """

    def __init__(self, agv: AGV, load_weight: float, view: TrafficView) -> None:
        self.agv_type: AGVType = agv.agv_type
        self.profile = self.agv_type.speed_profile
        self.load_weight = load_weight
        capacity = agv.battery.capacity
        # the level now (the pure read, without accounting), as a share of the capacity like `Battery.level_pct`
        self.level = agv.battery_level_now() / capacity if capacity > 0 else 0.0
        self.view = view
        self.kin = (
            checked_limits(self.profile, load_weight, self.level)
            if isinstance(self.profile, KinematicProfile)
            else None
        )
        self.stalled = self.kin is not None and cannot_move(self.kin)
        self.turn_time = self.agv_type.turn_time
        self.corner_speed = self.agv_type.corner_speed if self.kin is not None else None
        self.turning = self.turn_time is not None or self.corner_speed is not None
        self._legs: dict[tuple[Node, Node], tuple[float, float]] = {}

    def leg(self, u: Node, v: Node) -> tuple[float, float]:
        """The time of the arc from `u` to `v` and its speed cap ``min(v_max, speed_limit)`` (infinite if no limits)."""
        leg = self._legs.get((u, v))
        if leg is None:
            length, limit = self.view.length(u, v), self.view.speed_limit(u, v)
            kin = self.kin
            if kin is not None:
                cap = kin.v_max if limit is None else min(kin.v_max, limit)
                leg = (length / cap, cap)
            else:
                time = float(self.profile.travel_time(length, self.load_weight, self.level, speed_limit=limit))
                if not math.isfinite(time) or time < 0:
                    name = type(self.profile).__name__
                    raise ValueError(
                        f"{name}.travel_time({length!r}, {self.load_weight!r}, {self.level!r}, speed_limit={limit!r}) "
                        f"returned {time}; it must be finite and >= 0"
                    )
                leg = (time, math.inf)
            self._legs[(u, v)] = leg
        return leg

    def turn(self, angle: float | None, cap_in: float | None, cap_out: float) -> float:
        """The time a turn of `angle` costs between arcs of caps `cap_in` (None: at rest) and `cap_out`; an unknown
        angle turns for free."""
        if angle is None:
            return 0.0
        wait = 0.0 if self.turn_time is None else checked_turning(self.agv_type, "turn_time", angle)
        if self.kin is None or cap_in is None:
            return wait
        u = min(cap_in, cap_out)
        if wait > 0.0:
            c = 0.0
        else:
            c = math.inf if self.corner_speed is None else checked_turning(self.agv_type, "corner_speed", angle)
        if c >= u:
            return wait
        return wait + (u - c) ** 2 / (2 * u) * (1 / self.kin.accel + 1 / self.kin.decel)

    def step(self, u: Node, v: Node, heading: float | None, cap_in: float | None) -> tuple[float, float | None, float]:
        """The cost of leaving `u` for `v` after entering it with `heading` and speed cap `cap_in` (None at rest), and
        the heading and cap after the leg. An arc without direction has heading change 0 and leaves the heading
        unknown."""
        time, cap = self.leg(u, v)
        direction = arc_direction(u, v)
        if heading is None:
            angle = None
        elif direction is None:
            angle = 0.0
        else:
            angle = heading_change(heading, direction)
        return self.turn(angle, cap_in, cap) + time, direction, cap

    def path_cost(
        self,
        nodes: Sequence[Node],
        heading: float | None,
        penalty: Callable[[Node, Node, float], float] | None = None,
    ) -> float:
        """The cost of the walk `nodes`, as the search accumulates it: each leg's time, then its `penalty`."""
        cost, cap_in = 0.0, None
        for u, v in zip(nodes, nodes[1:]):
            before = cost
            time, heading, cap_in = self.step(u, v, heading, cap_in)
            cost += time
            if penalty is not None:
                cost += penalty(u, v, before)
        return cost

    def search(
        self,
        graph: LayoutGraph,
        origin: Node,
        destination: Node,
        heading: float | None,
        allowed: Callable[[Node, Node], bool] | None,
        penalty: Callable[[Node, Node, float], float] | None = None,
    ) -> tuple[tuple[Node, ...], float] | None:
        """Dijkstra over the states (node, node the entering arc came from), for a turn costs by the arc that enters the
        node. Ties break by cost, then node id, then the id of the node the entering arc came from. `penalty` is as for
        :func:`shortest_path`; an excluded leg is not costed at all."""
        start: _State = (origin, None)
        best: dict[_State, float] = {start: 0.0}
        previous: dict[_State, _State] = {}
        heap: list[tuple[float, str, str, _State]] = [(0.0, origin.id, "", start)]
        while heap:
            cost, _, _, state = heapq.heappop(heap)
            node, before = state
            if node == destination:
                path = [node]
                while state in previous:
                    state = previous[state]
                    path.append(state[0])
                nodes = tuple(reversed(path))
                if len(set(nodes)) != len(nodes):  # a perverse turn time made a loop pay: cut it, as a route is simple
                    nodes = _without_loops(nodes)
                    cost = self.path_cost(nodes, heading, penalty)
                return nodes, cost
            if cost > best[state]:
                continue
            if before is None:
                entering, cap_in = heading, None
            else:
                entering, cap_in = arc_direction(before, node), self.leg(before, node)[1]
            for neighbor in graph.neighbors(node):
                if neighbor == before or (allowed is not None and not allowed(node, neighbor)):
                    continue  # no simple route goes back to the node it came from
                extra = 0.0 if penalty is None else penalty(node, neighbor, cost)
                if extra == math.inf:
                    continue
                time, _, _ = self.step(node, neighbor, entering, cap_in)
                new_cost = cost + time + extra
                key: _State = (neighbor, node)
                if new_cost < best.get(key, math.inf):
                    best[key] = new_cost
                    previous[key] = state
                    heapq.heappush(heap, (new_cost, neighbor.id, node.id, key))
        return None


def _without_loops(path: Sequence[Node]) -> tuple[Node, ...]:
    """`path` with every loop cut at the first visit of the node it returns to."""
    kept: list[Node] = []
    for node in path:
        if node in kept:
            del kept[kept.index(node) + 1 :]
        else:
            kept.append(node)
    return tuple(kept)


def checked_limits(profile: KinematicProfile, load_weight: float, battery_level: float) -> Kinematics:
    """``profile.limits`` for a load and a battery level, checked: ``ValueError``, naming the profile and the values,
    when a value is not finite, when ``decel`` is negative, or when ``decel`` is 0 for a vehicle that can move (a stop
    cannot be planned). The executor and :class:`FastestRouter` both sample limits through this function."""
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
    raise ValueError(f"{type(profile).__name__}.limits({load_weight!r}, {battery_level!r}) returned {kin!r}: {problem}")


def cannot_move(kin: Kinematics) -> bool:
    """Whether the limits `kin` make a vehicle that cannot move: ``v_max`` or ``accel`` is not positive (spec §9.1)."""
    return not (kin.v_max > 0 and kin.accel > 0)


def checked_turning(agv_type: AGVType, name: str, angle: float) -> float:
    """What the AGV type's callable `name` (``turn_time`` or ``corner_speed``) says for the heading change `angle`:
    ``ValueError``, naming the type, for NaN or a negative value, and for an infinite ``turn_time``. An infinite
    ``corner_speed`` is no cap."""
    value = float(getattr(agv_type, name)(angle))
    if math.isnan(value) or value < 0 or (name == "turn_time" and math.isinf(value)):
        allowed = "finite and >= 0" if name == "turn_time" else "not NaN and >= 0"
        raise ValueError(f"AGVType {agv_type.name!r}: {name}({angle}) returned {value}; it must be {allowed}")
    return value


class FastestRouter:
    """Cost is the time the request's AGV needs: its legs at ``min(v_max, speed_limit)``, or by ``travel_time`` for a
    profile without limits, and its turns (spec §10.2); see :class:`_Timing`. The limits are those of the request's load
    and the AGV's battery level now. A vehicle that cannot move (``v_max`` or ``accel`` not positive) has no route.

    The AGV's heading is taken as its heading at the origin: the first arc pays the turn from it, at rest. With a
    turning model (``corner_speed`` for a profile with limits, or ``turn_time``) the search state is the node and the
    arc that entered it, and a node is never left for the node the AGV came from. The route is the least-cost walk
    cut to a simple path: where a turn time makes a walk that returns to a node cheaper, it is cut at the first visit
    and its cost is that of the cut route. It is therefore not guaranteed to be the least-cost simple path. An arc
    whose chord has length 0 has no direction: the heading change into it is 0 and the heading after it is unknown
    here (the next turn is free), whereas the executor measures the next turn from the last known heading.

    A request without an AGV is routed by length, as :class:`ShortestDistanceRouter` does, and its cost is a length:
    a cost is comparable only within one router and one kind of request. A leg is excluded when it enters an avoided
    node or needs an avoided resource, as for :class:`ShortestDistanceRouter`. Ties break by node id.
    """

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        return self._route(request, view, None)

    def _route(
        self, request: RouteRequest, view: TrafficView, penalty: Callable[[Node, Node, float], float] | None
    ) -> Route | None:
        """The route with `penalty` added to the cost of each leg (see :func:`shortest_path`), for
        :class:`CongestionAwareRouter`; its cost is the time plus the penalties of its legs."""
        agv, origin, destination = request.agv, request.origin, request.destination
        if agv is None or origin == destination:
            return _BY_LENGTH.route(request, view)
        timing = _Timing(agv, request.load_weight, view)
        if timing.stalled:
            return None
        allowed = _leg_filter(request.avoid, view)
        if timing.turning:
            found = timing.search(view.graph, origin, destination, agv.heading, allowed, penalty)
        else:
            found = shortest_path(
                view.graph,
                origin,
                destination,
                allowed=allowed,
                weight=lambda u, v: timing.leg(u, v)[0],
                penalty=penalty,
            )
        return None if found is None else Route(*found)


_BY_LENGTH = ShortestDistanceRouter()


class _Congestion:
    """The penalty of the legs for one request of :class:`CongestionAwareRouter`:
    ``wait_weight * w * exp(-t / horizon)``, with `w` of a leg read from the view once, however many times the search
    expands it, and infinite where the leg is excluded."""

    def __init__(self, agv: AGV, view: TrafficView, wait_weight: float, horizon: float) -> None:
        self.agv = agv
        self.view = view
        self.wait_weight = wait_weight
        self.horizon = horizon
        self._weights: dict[tuple[Node, Node], float] = {}

    def weight(self, u: Node, v: Node) -> float:
        """The largest, over the resources of the leg from `u` to `v` that are inadmissible for the AGV now, of
        ``(blocking holders + pending claims) * mean_hold``; 0.0 when there are none, infinity when every blocking
        holder of one of them is stranded."""
        weight = self._weights.get((u, v))
        if weight is None:
            view = self.view
            weight = 0.0
            for resource, holders in view.blockers(self.agv, u, v):
                if all(view.disposition(holder) is Disposition.STRANDED for holder in holders):
                    weight = math.inf
                    break
                weight = max(weight, (len(holders) + view.pending(resource)) * view.mean_hold(resource))
            self._weights[(u, v)] = weight
        return weight

    def __call__(self, u: Node, v: Node, reached: float) -> float:
        weight = self.weight(u, v)
        return weight if weight == math.inf else self.wait_weight * weight * math.exp(-reached / self.horizon)


class CongestionAwareRouter:
    """A heuristic that steers a route away from legs that are busy now (spec §10.2); it does not claim an optimum.

    The cost is that of `base` (:class:`FastestRouter`, the default, or :class:`ShortestDistanceRouter`; any other base
    raises ``TypeError``) plus, for each leg, ``wait_weight * w * exp(-t / horizon)``. `t` is the cost accumulated when
    the search expands the leg, in the unit of `base`, so a leg far ahead counts less: it will have changed by the time
    the AGV gets there. `w` is the largest, over the resources of the leg that the holders make inadmissible for the
    requesting AGV now (``view.blockers``, which ignores the queue and the gate), of ``(blocking holders + pending
    claims) * mean_hold``. A leg with such a resource whose blocking holders are all stranded is excluded: no wait for
    it ends. ``Route.cost`` includes the penalties.

    The penalty depends on the cost at which a leg is reached, which a shortest-path search does not model, and `w` is
    a snapshot of the traffic now: the route is a good one, not the best one. Without an AGV in the request no penalty
    applies, and with ``wait_weight`` 0 the result is the base router's (a leg held by stranded AGVs is then not
    excluded). Where no penalty applies the route and its cost equal the base router's exactly.
    """

    def __init__(
        self,
        base: ShortestDistanceRouter | FastestRouter | None = None,
        *,
        wait_weight: float = 1.0,
        horizon: float = 60.0,
    ) -> None:
        if base is None:
            base = FastestRouter()
        if type(base) not in (ShortestDistanceRouter, FastestRouter):
            raise TypeError(f"base must be a ShortestDistanceRouter or a FastestRouter, not {type(base).__name__}")
        if not (math.isfinite(wait_weight) and wait_weight >= 0):
            raise ValueError(f"wait_weight must be finite and >= 0, not {wait_weight!r}")
        if not horizon > 0:
            raise ValueError(f"horizon must be positive, not {horizon!r}")
        self.base = base
        self.wait_weight = wait_weight
        self.horizon = horizon

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        agv = request.agv
        if agv is None or self.wait_weight == 0.0:
            return self.base.route(request, view)
        return self.base._route(request, view, _Congestion(agv, view, self.wait_weight, self.horizon))
