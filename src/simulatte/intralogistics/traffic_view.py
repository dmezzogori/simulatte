"""The :class:`~simulatte.intralogistics.traffic.TrafficView` of a
:class:`~simulatte.intralogistics.reservation.ReservationController` (spec §10.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.intralogistics.traffic import Disposition, disposition_of

if TYPE_CHECKING:
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.network import Claim, Resource
    from simulatte.intralogistics.reservation import ReservationController

__all__ = ["ReservationView"]


class ReservationView:
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
        return disposition_of(agv)

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        claim = self._controller._pending.get(agv)
        return () if claim is None else tuple(claim.request.hooks.route())

    def waiting_on(self, agv: AGV) -> tuple[AGV, ...]:
        controller = self._controller
        waits = sorted(
            (waiting for waiting in controller._queue if waiting.stopped), key=lambda waiting: waiting.key[1]
        )
        return tuple(
            waiting.agv
            for waiting in waits
            if any(
                agv in controller._blockers(waiting.agv, item)
                for item in controller._needed(waiting.agv, waiting.request.claim)
            )
        )

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        return self._controller._admissible(agv, claim)

    def mean_hold(self, resource: Resource) -> float:
        return 0.0
