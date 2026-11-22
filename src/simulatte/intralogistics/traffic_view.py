"""The :class:`~simulatte.intralogistics.traffic.TrafficView` of a
:class:`~simulatte.intralogistics.reservation.ReservationController` (spec §10.1)."""

from __future__ import annotations

from collections import Counter
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
        self._pending_counts: Counter[Resource] = Counter()
        self._pending_revision = -1  # the controller revision that `_pending_counts` was counted at

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

    def branch_root(self, node: Node) -> bool:
        return self._controller.network.branch_root(node)

    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]:
        return tuple(item.resource for item in self._controller.network.leg(u, v))

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        return self._controller.ledger.holders(resource)

    def pending(self, resource: Resource) -> int:
        """The queued claims that still need `resource`. One pass over the queue is kept until the queue or the holdings
        change, so that a router reading many resources does not scan the queue once for each."""
        controller = self._controller
        if self._pending_revision != controller._revision:
            counts: Counter[Resource] = Counter()
            for waiting in controller._queue:
                counts.update({item.resource for item in controller.ledger.needed(waiting.agv, waiting.request.claim)})
            self._pending_counts, self._pending_revision = counts, controller._revision
        return self._pending_counts.get(resource, 0)

    def disposition(self, agv: AGV) -> Disposition:
        claim = self._controller._pending.get(agv)
        if claim is not None and claim.stopped:
            return Disposition.WAITING
        return disposition_of(agv)

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        claim = self._controller._pending.get(agv)
        return () if claim is None else tuple(claim.request.hooks.route())

    def waiting_on(self, agv: AGV) -> tuple[AGV, ...]:
        return tuple(waiting.agv for waiting in self._controller._waits_blocked_by(agv))

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        return self._controller._admissible(agv, claim)

    def blockers(self, agv: AGV, u: Node, v: Node) -> tuple[tuple[Resource, tuple[AGV, ...]], ...]:
        controller = self._controller
        ledger = controller.ledger
        found: list[tuple[Resource, tuple[AGV, ...]]] = []
        for item in controller.network.leg(u, v):
            if not ledger.holds(agv, item) and (holders := ledger.blockers(agv, item)):
                found.append((item.resource, tuple(holders)))
        return tuple(found)

    def mean_hold(self, resource: Resource) -> float:
        return self._controller.ledger.mean_hold(resource)
