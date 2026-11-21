"""The traffic controller that enforces occupancy with claims (spec §6, §7.1-§7.5, §7.7).

An AGV asks :class:`ReservationController` for a :class:`~simulatte.intralogistics.network.Claim` and gets all of it or
waits. The controller keeps the holdings of every resource, admits claims by the rules of spec §7.3, orders pending
claims by priority and request counter behind the fairness gate of §7.4, and frees resources on release (§7.5). Each
segment, zone and corridor is an entity (``segment``, ``zone``); nodes record their holders in the existing ``node``
entities. ``traffic.reserved`` and ``traffic.released`` carry every change of that state.
"""

from __future__ import annotations

import math
from bisect import insort
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas
from simulatte.layout.binding import graph_input, pending
from simulatte.intralogistics.events import TrafficReleased, TrafficReserved
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.network import ClaimItem, Corridor, Segment, TrafficNetwork, Zone
from simulatte.intralogistics.traffic import _DISPOSITIONS, ClaimRequest, Disposition, Granted

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from simulatte.environment import Environment
    from simulatte.events import DeltaBuilder
    from simulatte.layout.network import ResolvedNetwork
    from simulatte.layout.refs import GraphRef
    from simulatte.intralogistics.agv import AGV, AGVType
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import LayoutGraph
    from simulatte.intralogistics.network import Claim, Direction, Resource
    from simulatte.intralogistics.traffic import TrafficPolicy, WaitHooks

__all__ = ["ReservationController", "SegmentBinding", "ZoneBinding"]

Container = Zone | Corridor


class SegmentBinding(Entity, kind="segment"):
    """The entity of a :class:`~simulatte.intralogistics.network.Segment` (spec §11.1).

    Its id is the segment id. ``source`` and ``target`` are the node ids in sorted order; ``forward`` lists the AGVs
    holding the segment from ``source`` to ``target`` and ``backward`` the others, both in grant order.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "source": FieldSpec("str"),
            "target": FieldSpec("str"),
            "length": FieldSpec("float"),
            "forward": FieldSpec("str", collection="list"),
            "backward": FieldSpec("str", collection="list"),
        }
    )

    def __init__(self, env: Environment, segment: Segment) -> None:
        self.segment = segment
        self.forward: list[str] = []
        self.backward: list[str] = []
        env.entities.attach(self, name=segment.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: the end nodes, the length and the holders of each direction."""
        segment = self.segment
        return {
            "source": segment.a.id,
            "target": segment.b.id,
            "length": wire_float(segment.length),
            "forward": list(self.forward),
            "backward": list(self.backward),
            "label": self.label,
        }


class ZoneBinding(Entity, kind="zone"):
    """The entity of an authored zone (``rule`` ``exclusive``) or a derived corridor (``same_direction``).

    ``capacity`` is null for a corridor; ``members`` are node and segment ids; ``reserved_by`` lists the holders in
    grant order; ``orientation`` is the orientation of a corridor's oriented holders, or null.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "rule": FieldSpec("str"),
            "capacity": FieldSpec("int", nullable=True),
            "members": FieldSpec("str", collection="list"),
            "reserved_by": FieldSpec("str", collection="list"),
            "orientation": FieldSpec("str", nullable=True),
        }
    )

    def __init__(self, env: Environment, container: Container, members: Sequence[str]) -> None:
        self.container = container
        self.rule = "same_direction" if isinstance(container, Corridor) else "exclusive"
        self.capacity: int | None = None if isinstance(container, Corridor) else container.capacity
        self.members = list(members)
        self.reserved_by: list[str] = []
        self.orientation: str | None = None
        env.entities.attach(self, name=container.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: rule, capacity, members, holders and orientation."""
        return {
            "rule": self.rule,
            "capacity": self.capacity,
            "members": list(self.members),
            "reserved_by": list(self.reserved_by),
            "orientation": self.orientation,
            "label": self.label,
        }


@dataclass(eq=False)
class _Pending:
    """A pending claim: the request (with its full claim) and its queue key.

    What the claim still needs is determined from the current holdings whenever it is tested (spec §7.5).
    """

    request: ClaimRequest
    key: tuple[float, int]  # (-priority, request counter)
    stopped: bool = False
    yielding: bool = False  # set by the policy (Task 6); the gate skips yielding claims

    @property
    def agv(self) -> AGV:
        return self.request.agv


_MISSING: Any = object()


def _no_length(agv_type: AGVType) -> str:
    return f"AGVType {agv_type.name!r} has no length: ReservationController needs the extent of every vehicle"


class _ReservationView:
    """The :class:`~simulatte.intralogistics.traffic.TrafficView` of a :class:`ReservationController`."""

    def __init__(self, controller: ReservationController) -> None:
        self._controller = controller

    @property
    def graph(self) -> LayoutGraph:
        return self._controller.network.graph

    def length(self, u: Node, v: Node) -> float:
        return self.graph.length(u, v)

    def speed_limit(self, u: Node, v: Node) -> float | None:
        arc = self.graph.arc_between(u, v)
        if arc is None:
            raise ValueError(f"Nodes {u.id} and {v.id} are not connected by an arc")
        return arc.speed_limit

    def capacity(self, node: Node) -> int:
        return self._controller.network.capacity(node)

    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]:
        return tuple(item.resource for item in self._controller.network.leg(u, v))

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        return tuple(self._controller._holders.get(resource, ()))

    def pending(self, resource: Resource) -> int:
        controller = self._controller
        return sum(
            1
            for waiting in controller._queue
            if any(item.resource == resource for item in controller._needed(waiting.agv, waiting.request.claim))
        )

    def disposition(self, agv: AGV) -> Disposition:
        claim = self._controller._pending.get(agv)
        if claim is not None and claim.stopped:
            return Disposition.WAITING
        return _DISPOSITIONS[agv.state]

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        claim = self._controller._pending.get(agv)
        return () if claim is None else tuple(claim.request.hooks.route())

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        return self._controller._admissible(agv, claim)

    def mean_hold(self, resource: Resource) -> float:
        return 0.0


class ReservationController:
    """Grants claims all or nothing and enforces the occupancy of nodes, segments, zones and corridors (spec §7.7).

    The :class:`TrafficNetwork` is built by an activation initializer registered here, from the graph, the capacities
    (explicit ones, then the parking areas and charging stations of the registered fleets, then ``node_capacity``),
    the authored ``zones`` and the derivation flags; its warnings are logged once. The same initializer attaches one
    ``segment`` entity per segment and one ``zone`` entity per zone and corridor, each group in id order. Fleets must
    register before activation and their vehicle types must have a ``length``.
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
        self._policy = policy
        self._priority_fn: Callable[[AGV], float] = priority_fn or (lambda agv: 0.0)
        self._fleets: list[FleetCoordinator] = []
        self._network: TrafficNetwork | None = None
        self._bindings: dict[Resource, SegmentBinding | ZoneBinding] = {}
        self._members: dict[Container, tuple[Node | Segment, ...]] = {}
        self._containers_at: dict[Node, tuple[Container, ...]] = {}
        # Holdings, both in grant order: the holders of each resource with their direction (segments, corridors) or
        # None, and the resources each AGV holds.
        self._holders: dict[Resource, dict[AGV, Direction | None]] = {}
        self._held: dict[AGV, dict[Resource, None]] = {}
        self._placed: dict[AGV, None] = {}
        self._exits: dict[AGV, dict[Container, Node]] = {}  # corridors left onto a port, kept until the port goes
        self._queue: list[_Pending] = []  # sorted by key
        self._pending: dict[AGV, _Pending] = {}
        self._counter = 0
        self.view = _ReservationView(self)
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
        for segment in network.segments:
            self._bindings[segment] = SegmentBinding(env, segment)
        containers_at: dict[Node, list[Container]] = {}
        for container in sorted((*network.zones, *network.corridors), key=lambda c: c.id):
            members = self._container_members(network, container)
            self._members[container] = members
            for member in members:
                if isinstance(member, Node):
                    containers_at.setdefault(member, []).append(container)
            self._bindings[container] = ZoneBinding(env, container, [member.id for member in members])
        self._containers_at = {node: tuple(found) for node, found in containers_at.items()}
        self._network = network

    @staticmethod
    def _container_members(network: TrafficNetwork, container: Container) -> tuple[Node | Segment, ...]:
        """A zone's members in authored order, a corridor's segments and interior nodes along the chain."""
        if isinstance(container, Corridor):
            nodes = container.nodes
            found: list[Node | Segment] = [network.segment(nodes[0], nodes[1])]
            for node, after in zip(nodes[1:-1], nodes[2:]):
                found += [node, network.segment(node, after)]
            return tuple(found)
        members = (m if isinstance(m, Node) else network.segment(*m) for m in container.members)
        return tuple(dict.fromkeys(members))

    # Protocol

    def register_fleet(self, fleet: FleetCoordinator) -> None:
        """Take the vehicles and facilities of `fleet` into account; only before activation."""
        if self._network is not None:
            raise RuntimeError("register_fleet: the traffic network is already built; register before activation")
        for agv in fleet.fleet:
            if agv.agv_type.length is None:
                raise ValueError(_no_length(agv.agv_type))
        self._fleets.append(fleet)

    def place(self, agv: AGV, node: Node) -> None:
        """Hold `node` and every zone and corridor containing it (a corridor without orientation) for `agv`.

        Raises `RuntimeError`, naming the resource and its capacity, when one of them is full.
        """
        network = self.network
        items = [ClaimItem(container) for container in self._containers_at.get(node, ())]
        items = [item for item in (*items, ClaimItem(node)) if not self._holds(agv, item)]
        for item in items:
            if self._blockers(agv, item):
                resource = item.resource
                capacity = network.capacity(resource) if isinstance(resource, Node) else cast(Zone, resource).capacity
                raise RuntimeError(
                    f"cannot place {agv.id} at node {node.id}: {resource.id} is fully reserved (capacity {capacity})"
                )
        self._placed[agv] = None
        self._take(agv, items)
        self._scan()

    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim:
        """The claim that starts at leg `index` of `route` (spec §7.2)."""
        return self.network.claim(route, index)

    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
        """Ask for `claim` on behalf of `agv`; the returned request's event succeeds with ``Granted`` at the grant.

        What the AGV already holds is dropped from the claim, except a corridor or a segment held in another direction
        (spec §7.2), which is claimed again; if nothing is left the request is granted at once. ``priority_fn`` is read
        here, once. Raises `RuntimeError` when the AGV already has a pending request.
        """
        if agv in self._pending:
            raise RuntimeError(f"{agv.id} already has a pending traffic request")
        request = ClaimRequest(agv, claim, hooks, self._env.event())
        if not self._needed(agv, claim):
            request.event.succeed(Granted())
            return request
        waiting = _Pending(request, (-self._priority_fn(agv), self._counter))
        self._counter += 1
        insort(self._queue, waiting, key=lambda claim: claim.key)
        self._pending[agv] = waiting
        self._scan()
        return request

    def stopped(self, request: ClaimRequest) -> None:
        """Note that the AGV of `request` is at rest and waiting on it (waits and policy calls come with Task 6)."""
        waiting = self._pending.get(request.agv)
        if waiting is not None and waiting.request is request:
            waiting.stopped = True

    def withdraw(self, request: ClaimRequest) -> None:
        """Cancel `request` if it is still pending."""
        waiting = self._pending.get(request.agv)
        if waiting is None or waiting.request is not request:
            return
        self._queue.remove(waiting)
        del self._pending[request.agv]
        self._scan()

    def release(self, agv: AGV, resources: Collection[Resource]) -> None:
        """Free the given resources that `agv` holds, and each zone or corridor whose last held member goes with them.

        Resources the AGV does not hold are ignored. Three containers stay (spec §7.5): one held ahead of entry (no
        member held), one named by the AGV's pending claim, and a corridor left onto a port the AGV still holds, which
        then goes with that port.
        """
        held = self._held.get(agv, {})
        wanted = set(resources)
        gone = {resource for resource in held if resource in wanted}
        self._settle(agv, held, gone, (), drop_ahead=False)
        self._drop(agv, gone)

    def retain(self, agv: AGV, resources: Collection[Resource]) -> None:
        """Free everything `agv` holds except `resources`, the zones and corridors containing one of them, those its
        pending claim names, and a corridor left through a port that is kept."""
        held = self._held.get(agv, {})
        wanted = set(resources)
        kept = [resource for resource in held if resource in wanted]
        containers = self._containers_held(held)
        gone = {resource for resource in held if resource not in wanted and resource not in containers}
        self._settle(agv, held, gone, kept, drop_ahead=True)
        self._drop(agv, gone)

    def _settle(
        self,
        agv: AGV,
        held: Mapping[Resource, None],
        gone: set[Resource],
        kept: Collection[Resource],
        *,
        drop_ahead: bool,
    ) -> None:
        """Add to `gone` the zones and corridors that go with it (spec §7.5); `kept` are never freed.

        A container keeps while the AGV holds a member that stays, while its pending claim names it, and while it holds
        the port it left a corridor through. A corridor whose last members go while the AGV holds one of its ports is
        left onto that port: it stays and goes with the port. A container held ahead of entry goes only with
        `drop_ahead` (``retain``).
        """
        waiting = self._pending.get(agv)
        named = set() if waiting is None else {item.resource for item in waiting.request.claim.items}
        exits = self._exits.setdefault(agv, {})
        for container in self._containers_held(held):
            if container in gone or container in kept or container in named:
                continue
            members = [member for member in self._members[container] if member in held]
            if any(member not in gone for member in members):
                continue
            port = exits.get(container)
            if port is not None:
                if port in gone:
                    gone.add(container)
                continue
            if members:
                port = self._port_held(container, held, gone)
                if port is None:
                    gone.add(container)
                else:
                    exits[container] = port  # left onto the port: goes with it
            elif drop_ahead:
                gone.add(container)

    def _port_held(self, container: Container, held: Mapping[Resource, None], gone: set[Resource]) -> Node | None:
        """The port of a corridor that the AGV holds and keeps, if any; None for a zone."""
        if isinstance(container, Corridor):
            for port in (container.nodes[0], container.nodes[-1]):
                if port in held and port not in gone:
                    return port
        return None

    # Admission

    def _extent(self, agv: AGV) -> float:
        agv_type = agv.agv_type
        if agv_type.length is None:
            raise ValueError(_no_length(agv_type))
        return agv_type.length + agv_type.min_gap

    def _holds(self, agv: AGV, item: ClaimItem) -> bool:
        """Whether `agv` already holds `item`: a corridor or a segment counts only with the same direction."""
        direction = self._holders.get(item.resource, {}).get(agv, _MISSING)
        if direction is _MISSING:
            return False
        return not isinstance(item.resource, (Segment, Corridor)) or direction == item.direction

    def _blockers(self, agv: AGV, item: ClaimItem) -> list[AGV]:
        """The other holders that make `item` inadmissible for `agv` (spec §7.3); empty when it is admissible."""
        resource = item.resource
        holders = self._holders.get(resource, {})
        others = [holder for holder in holders if holder is not agv]
        if not others:
            return []
        if isinstance(resource, Segment):
            if resource.passing:  # opposite traffic passes: only the holders with the same direction count
                others = [holder for holder in others if holders[holder] == item.direction]
                opposite: list[AGV] = []
            else:
                opposite = [holder for holder in others if holders[holder] != item.direction]
            if others and math.fsum([*map(self._extent, others), self._extent(agv)]) > resource.length:
                return others
            return opposite
        if isinstance(resource, Corridor):
            if item.direction is None:  # an AGV placed inside holds the corridor without orientation
                return []
            return [holder for holder in others if holders[holder] not in (None, item.direction)]
        capacity = self.network.capacity(resource) if isinstance(resource, Node) else resource.capacity
        return others if len(others) >= capacity else []

    def _needed(self, agv: AGV, claim: Claim) -> tuple[ClaimItem, ...]:
        """The items of `claim` that `agv` does not hold yet (including a corridor or segment held the other way)."""
        return tuple(item for item in claim.items if not self._holds(agv, item))

    def _gated(self, agv: AGV, items: Iterable[ClaimItem], earlier: Iterable[_Pending]) -> bool:
        """Whether the claim of `items` by `agv` is held by the fairness gate (spec §7.4) of an earlier pending claim.

        A claim is gated on a segment or corridor ``r`` it names, and that its AGV does not hold, when an earlier,
        non-yielding claim names ``r``, is inadmissible on ``r``, and each of its other resources is admissible or
        blocked only by holders of ``r``.
        """
        others = [(claim.agv, self._needed(claim.agv, claim.request.claim)) for claim in earlier if not claim.yielding]
        for item in items:
            resource = item.resource
            if not isinstance(resource, (Segment, Corridor)):
                continue
            holders = self._holders.get(resource, {})
            if agv in holders:
                continue
            for other_agv, needed in others:
                mine = next((own for own in needed if own.resource == resource), None)
                if mine is None or not self._blockers(other_agv, mine):
                    continue
                if all(
                    all(blocker in holders for blocker in self._blockers(other_agv, other))
                    for other in needed
                    if other is not mine
                ):
                    return True
        return False

    def _admissible(self, agv: AGV, claim: Claim) -> bool:
        """Admission and gate for `claim`, as for a new request of `agv`."""
        items = self._needed(agv, claim)
        if any(self._blockers(agv, item) for item in items):
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
                items = self._needed(agv, waiting.request.claim)
                if any(self._blockers(agv, item) for item in items) or self._gated(agv, items, earlier):
                    earlier.append(waiting)
                    continue
                self._queue.remove(waiting)
                del self._pending[agv]
                self._take(agv, items)
                waiting.request.event.succeed(Granted())
                granted = True

    # Holdings

    def _containers_held(self, held: Mapping[Resource, None]) -> list[Container]:
        return [resource for resource in held if isinstance(resource, (Zone, Corridor))]

    def _orient(self, binding: ZoneBinding, corridor: Corridor, build: DeltaBuilder) -> None:
        """Set the corridor's orientation to that of its oriented holders, or None."""
        orientation = next((o for o in self._holders.get(corridor, {}).values() if o is not None), None)
        if orientation != binding.orientation:
            binding.orientation = orientation
            build.set(binding.id, "orientation", orientation)

    def _take(self, agv: AGV, items: Sequence[ClaimItem]) -> None:
        """Record `items` as held by `agv` and emit one ``traffic.reserved``."""
        held = self._held.setdefault(agv, {})
        build = Deltas.build()
        ids: list[str] = []
        for item in items:
            resource = item.resource
            holders = self._holders.setdefault(resource, {})
            previous = holders.get(agv, _MISSING)  # held: a segment or corridor claimed again in a new direction
            renewed = previous is not _MISSING
            holders[agv] = item.direction
            held[resource] = None
            if isinstance(resource, Node):
                binding = self._env.entities.node_binding(resource)
                if binding is None:
                    continue
                binding.reserved_by.append(agv.id)
                build.insert(binding.id, "reserved_by", len(binding.reserved_by) - 1, agv.id)
            elif isinstance(resource, Segment):
                segment = self._bindings[resource]
                assert isinstance(segment, SegmentBinding)
                if renewed:
                    old = "forward" if previous == "forward" else "backward"
                    getattr(segment, old).remove(agv.id)
                    build.remove(segment.id, old, agv.id)
                field = "forward" if item.direction == "forward" else "backward"
                holding = getattr(segment, field)
                holding.append(agv.id)
                build.insert(segment.id, field, len(holding) - 1, agv.id)
            else:
                zone = self._bindings[resource]
                assert isinstance(zone, ZoneBinding)
                if not renewed:
                    zone.reserved_by.append(agv.id)
                    build.insert(zone.id, "reserved_by", len(zone.reserved_by) - 1, agv.id)
                if isinstance(resource, Corridor):
                    self._orient(zone, resource, build)
            ids.append(resource.id)
        exits = self._exits.get(agv)
        if exits:  # a corridor entered again is no longer held through its exit port
            for corridor in [c for c in exits if any(member in held for member in self._members[c])]:
                del exits[corridor]
        env = self._env
        if ids and env.wants(TrafficReserved):
            env.emit(TrafficReserved(agv=agv.id, resources=tuple(ids), deltas=build.done()))

    def _drop(self, agv: AGV, gone: Collection[Resource]) -> None:
        """Free `gone` (resources `agv` holds) in holding order, emit one ``traffic.released`` and scan."""
        if not gone:
            return
        held = self._held[agv]
        build = Deltas.build()
        ids: list[str] = []
        exits = self._exits.get(agv, {})
        for resource in [resource for resource in held if resource in gone]:
            del held[resource]
            exits.pop(resource, None)
            direction = self._holders[resource].pop(agv)
            if isinstance(resource, Node):
                binding = self._env.entities.node_binding(resource)
                if binding is None:
                    continue
                binding.reserved_by.remove(agv.id)
                build.remove(binding.id, "reserved_by", agv.id)
            elif isinstance(resource, Segment):
                segment = self._bindings[resource]
                assert isinstance(segment, SegmentBinding)
                field = "forward" if direction == "forward" else "backward"
                getattr(segment, field).remove(agv.id)
                build.remove(segment.id, field, agv.id)
            else:
                zone = self._bindings[resource]
                assert isinstance(zone, ZoneBinding)
                zone.reserved_by.remove(agv.id)
                build.remove(zone.id, "reserved_by", agv.id)
                if isinstance(resource, Corridor):
                    self._orient(zone, resource, build)
            ids.append(resource.id)
        env = self._env
        if ids and env.wants(TrafficReleased):
            env.emit(TrafficReleased(agv=agv.id, resources=tuple(ids), deltas=build.done()))
        self._scan()
