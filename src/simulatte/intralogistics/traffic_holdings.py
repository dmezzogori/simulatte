"""The holdings ledger of :class:`~simulatte.intralogistics.reservation.ReservationController` (spec §7.3, §7.5).

The ledger records who holds which node, segment, zone and corridor, with the direction of each segment or corridor
holding, the corridors left onto a port (exit records) and the AGVs placed at activation. It answers the admission test
of spec §7.3 for one resource, records grants and releases in the ``node``, ``segment`` and ``zone`` entities with one
``traffic.reserved`` or ``traffic.released`` per change, and works out which zones and corridors go with a release
(§7.5). It never decides a grant: the queue, the fairness gate, waits and deadlocks belong to the controller.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from simulatte.events import Deltas
from simulatte.intralogistics.events import TrafficReleased, TrafficReserved
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.network import Corridor, Segment, Zone
from simulatte.intralogistics.traffic_entities import Container, SegmentBinding, ZoneBinding

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from simulatte.environment import Environment
    from simulatte.events import DeltaBuilder
    from simulatte.intralogistics.agv import AGV, AGVType
    from simulatte.intralogistics.network import Claim, ClaimItem, Direction, Resource, TrafficNetwork

__all__ = ["HoldingsLedger"]

_MISSING: Any = object()

_HOLD_FACTOR = 0.1
"""The weight of the latest hold in the exponential average of the hold durations of a resource."""

_KINDS = (Node, Segment, Zone, Corridor)


def no_length(agv_type: AGVType) -> str:
    """The message of the error raised for a vehicle type without a length."""
    return f"AGVType {agv_type.name!r} has no length: ReservationController needs the extent of every vehicle"


def extent(agv: AGV) -> float:
    """The extent of `agv` (``length + min_gap``, spec §6.6); `ValueError` when its type has no length."""
    agv_type = agv.agv_type
    if agv_type.length is None:
        raise ValueError(no_length(agv_type))
    return agv_type.length + agv_type.min_gap


class HoldingsLedger:
    """Who holds what, for one :class:`~simulatte.intralogistics.reservation.ReservationController`.

    Holdings are kept in grant order, both per resource (with the direction of a segment or corridor holding, None
    otherwise) and per AGV. :meth:`build` attaches the entities of the network once it exists; before that the ledger
    is empty. The read methods return copies; views and test helpers use only those. The methods that change the
    ledger belong to the controller, and nothing else may call them.
    """

    def __init__(self, env: Environment) -> None:
        self._env = env
        self._network: TrafficNetwork | None = None
        self._bindings: dict[Resource, SegmentBinding | ZoneBinding] = {}
        self._members: dict[Container, tuple[Node | Segment, ...]] = {}
        self._containers_at: dict[Node, tuple[Container, ...]] = {}
        self._holders: dict[Resource, dict[AGV, Direction | None]] = {}
        self._held: dict[AGV, dict[Resource, None]] = {}
        self._placed: dict[AGV, None] = {}
        self._exits: dict[AGV, dict[Container, Node]] = {}  # corridors left onto a port, kept until the port goes
        self._since: dict[Resource, dict[AGV, float]] = {}  # when each holder's hold of a resource began
        self._mean_hold: dict[Resource, float] = {}  # the average hold duration of each resource released so far
        self._kind_mean: dict[type, float] = {}  # the average of those averages by kind, until the next release

    def build(self, network: TrafficNetwork) -> None:
        """Attach one ``segment`` entity per segment, then one ``zone`` entity per zone and corridor in id order."""
        env = self._env
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

    # Reading

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        """The AGVs holding `resource`, in grant order."""
        return tuple(self._holders.get(resource, ()))

    def held(self, agv: AGV) -> tuple[Resource, ...]:
        """The resources `agv` holds, in grant order."""
        return tuple(self._held.get(agv, ()))

    def direction(self, resource: Resource, agv: AGV) -> Direction | None:
        """The direction of `agv`'s holding of a segment, or the orientation of its holding of a corridor (None for a
        corridor held without orientation and for every other resource); `KeyError` when `agv` does not hold it."""
        return self._holders.get(resource, {})[agv]

    def exits(self, agv: AGV) -> dict[Container, Node]:
        """The exit records of `agv`: each corridor it left onto a port and still holds through it, with the port."""
        return dict(self._exits.get(agv, {}))

    def placed(self) -> tuple[AGV, ...]:
        """The AGVs placed at activation, in placement order."""
        return tuple(self._placed)

    def mean_hold(self, resource: Resource) -> float:
        """The exponential average (factor 0.1) of the durations for which `resource` was held, one per release (a
        segment or corridor claimed again in a new direction is the same hold); the first release sets it. A resource
        never released reports the average of those of its kind (node, segment, zone or corridor) that were, and 0.0
        when there are none. Kept whatever observes the run."""
        known = self._mean_hold.get(resource)
        if known is not None:
            return known
        kind = next(kind for kind in _KINDS if isinstance(resource, kind))
        average = self._kind_mean.get(kind)
        if average is None:
            values = [value for other, value in self._mean_hold.items() if isinstance(other, kind)]
            average = self._kind_mean[kind] = math.fsum(values) / len(values) if values else 0.0
        return average

    def members(self, container: Container) -> tuple[Node | Segment, ...]:
        """The members of a zone or corridor: a zone's in authored order, a corridor's along the chain."""
        return self._members[container]

    def containers_at(self, node: Node) -> tuple[Container, ...]:
        """The zones and corridors that contain `node`, in id order."""
        return self._containers_at.get(node, ())

    # Admission (spec §7.3)

    def holds(self, agv: AGV, item: ClaimItem) -> bool:
        """Whether `agv` already holds `item`: a corridor or a segment counts only with the same direction."""
        direction = self._holders.get(item.resource, {}).get(agv, _MISSING)
        if direction is _MISSING:
            return False
        return not isinstance(item.resource, (Segment, Corridor)) or direction == item.direction

    def blockers(self, agv: AGV, item: ClaimItem, among: Collection[AGV] | None = None) -> list[AGV]:
        """The other holders that make `item` inadmissible for `agv` (spec §7.3); empty when it is admissible.

        With `among`, only the holdings of those AGVs exist (deadlock detection, spec §7.8).
        """
        resource = item.resource
        holders = self._holders.get(resource, {})
        others = [holder for holder in holders if holder is not agv and (among is None or holder in among)]
        if not others:
            return []
        if isinstance(resource, Segment):
            if resource.passing:  # opposite traffic passes: only the holders with the same direction count
                others = [holder for holder in others if holders[holder] == item.direction]
                opposite: list[AGV] = []
            else:
                opposite = [holder for holder in others if holders[holder] != item.direction]
            if others and math.fsum([*map(extent, others), extent(agv)]) > resource.length:
                return others
            return opposite
        if isinstance(resource, Corridor):
            if item.direction is None:  # an AGV placed inside holds the corridor without orientation
                return []
            return [holder for holder in others if holders[holder] not in (None, item.direction)]
        assert self._network is not None
        capacity = self._network.capacity(resource) if isinstance(resource, Node) else resource.capacity
        return others if len(others) >= capacity else []

    def needed(self, agv: AGV, claim: Claim) -> tuple[ClaimItem, ...]:
        """The items of `claim` that `agv` does not hold yet (including a corridor or segment held the other way)."""
        return tuple(item for item in claim.items if not self.holds(agv, item))

    # Release (spec §7.5)

    def releasing(self, agv: AGV, resources: Collection[Resource], named: Collection[Resource]) -> set[Resource]:
        """What a ``release`` of `resources` by `agv` frees, with the zones and corridors that go with them; `named` are
        the resources of the AGV's pending claim. Updates the exit records; frees nothing (see :meth:`drop`)."""
        held = self._held.get(agv, {})
        wanted = set(resources)
        gone = {resource for resource in held if resource in wanted}
        for container in self._containers_held(held):
            if container in gone and any(member in held and member not in gone for member in self._members[container]):
                gone.discard(container)
        self._settle(agv, held, gone, (), named, retaining=False)
        return gone

    def retaining(self, agv: AGV, resources: Collection[Resource], named: Collection[Resource]) -> set[Resource]:
        """What a ``retain`` of `resources` by `agv`, at rest on its current node, frees; `named` are the resources of
        the AGV's pending claim. Updates the exit records; frees nothing (see :meth:`drop`)."""
        held = self._held.get(agv, {})
        wanted = set(resources)
        kept = [resource for resource in held if resource in wanted]
        containers = self._containers_held(held)
        gone = {resource for resource in held if resource not in wanted and resource not in containers}
        self._settle(agv, held, gone, kept, named, retaining=True, resting=agv.current_node)
        return gone

    def _settle(
        self,
        agv: AGV,
        held: Mapping[Resource, None],
        gone: set[Resource],
        kept: Collection[Resource],
        named: Collection[Resource],
        *,
        retaining: bool,
        resting: Node | None = None,
    ) -> None:
        """Add to `gone` the zones and corridors that go with it (spec §7.5); `kept` are never freed.

        A container stays while the AGV keeps one of its members or its pending claim names it. An exit record (a
        corridor left onto a port) lives only while the AGV holds that port: freeing the port deletes the record and
        frees the corridor unless a member or the pending claim keeps it. A ``retain`` at rest on a port of capacity
        above 1 (`resting`) deletes that port's records in the same way: the travel ends there. Otherwise, in
        ``release``, a container whose last held members go is freed, except a corridor whose port the AGV keeps, which
        gets an exit record; one held ahead of entry stays. ``retain`` creates no exit record and frees every container
        nothing keeps.
        """
        network = self._network
        assert network is not None
        exits = self._exits.setdefault(agv, {})
        lost = [
            corridor
            for corridor, port in exits.items()
            if port in gone or (port == resting and network.capacity(port) > 1)
        ]
        for corridor in lost:
            del exits[corridor]
        for container in self._containers_held(held):
            if container in gone or container in kept or container in named:
                continue
            members = [member for member in self._members[container] if member in held]
            if any(member not in gone for member in members) or container in exits:
                continue
            if retaining or container in lost:
                gone.add(container)
            elif members:
                port = self._port_held(container, held, gone)
                if port is None:
                    gone.add(container)
                else:
                    exits[container] = port  # left onto the port: goes with it

    @staticmethod
    def _port_held(container: Container, held: Mapping[Resource, None], gone: set[Resource]) -> Node | None:
        """The port of a corridor that the AGV holds and keeps, if any; None for a zone."""
        if isinstance(container, Corridor):
            for port in (container.nodes[0], container.nodes[-1]):
                if port in held and port not in gone:
                    return port
        return None

    # Changes

    def place(self, agv: AGV) -> None:
        """Record that `agv` was placed at activation."""
        self._placed[agv] = None

    def _containers_held(self, held: Mapping[Resource, None]) -> list[Container]:
        return [resource for resource in held if isinstance(resource, (Zone, Corridor))]

    def _orient(self, binding: ZoneBinding, corridor: Corridor, build: DeltaBuilder) -> None:
        """Set the corridor's orientation to that of its oriented holders, or None."""
        orientation = next((o for o in self._holders.get(corridor, {}).values() if o is not None), None)
        if orientation != binding.orientation:
            binding.orientation = orientation
            build.set(binding.id, "orientation", orientation)

    def _observe(self, resource: Resource, duration: float) -> None:
        """Fold a hold of `duration` into the average of `resource`."""
        mean = self._mean_hold.get(resource)
        self._mean_hold[resource] = duration if mean is None else mean + _HOLD_FACTOR * (duration - mean)
        self._kind_mean.clear()

    def take(self, agv: AGV, items: Sequence[ClaimItem]) -> None:
        """Record `items` as held by `agv` and emit one ``traffic.reserved``."""
        held = self._held.setdefault(agv, {})
        build = Deltas.build()
        ids: list[str] = []
        now = float(self._env.now)
        for item in items:
            resource = item.resource
            holders = self._holders.setdefault(resource, {})
            previous = holders.get(agv, _MISSING)  # held: a segment or corridor claimed again in a new direction
            renewed = previous is not _MISSING
            holders[agv] = item.direction
            held[resource] = None
            if not renewed:
                self._since.setdefault(resource, {})[agv] = now
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

    def drop(self, agv: AGV, gone: Collection[Resource]) -> None:
        """Free `gone` (resources `agv` holds, not empty) in holding order and emit one ``traffic.released``."""
        held = self._held[agv]
        build = Deltas.build()
        ids: list[str] = []
        exits = self._exits.get(agv, {})
        now = float(self._env.now)
        for resource in [resource for resource in held if resource in gone]:
            del held[resource]
            exits.pop(resource, None)
            direction = self._holders[resource].pop(agv)
            self._observe(resource, now - self._since[resource].pop(agv))
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
