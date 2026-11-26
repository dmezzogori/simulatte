"""The default :class:`~simulatte.intralogistics.traffic.TrafficPolicy` (spec §7.9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.intralogistics.traffic import Disposition, GiveUp, KeepWaiting, Reroute, StepAside

if TYPE_CHECKING:
    from collections.abc import Sequence

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import Node
    from simulatte.intralogistics.network import Resource
    from simulatte.intralogistics.traffic import Blocker, Reaction, TrafficView, Wait

__all__ = ["StandardTrafficPolicy"]


def _avoid(blockers: Sequence[Blocker]) -> frozenset[Resource]:
    return frozenset(resource for blocker in blockers for resource in blocker.resources)


class StandardTrafficPolicy:
    """Wait, then look for a detour; break deadlocks by moving the least important member.

    ``on_blocked``: with a stranded blocker, reroute around its resources at once; on the first call otherwise keep
    waiting; at a review, reroute around every blocked resource when the detour costs at most `max_detour` times the
    remaining route. A reroute is offered only while the wait's resolutions are below `max_resolutions`, so a travel
    between standing blockers is not rerouted back and forth without end. Every sequence ends with
    ``KeepWaiting(reroute_after)``.

    ``on_deadlock``: members by priority (lowest first), unloaded before loaded, latest wait first, then id. First
    ``GiveUp`` for each unloaded member whose resolutions reached `max_resolutions`, then ``Reroute`` around what
    blocks it and then ``StepAside`` for each member below that bound. A loaded member at the bound is left out.

    ``make_way_target`` returns None, which selects the standard search.
    """

    def __init__(
        self, *, reroute_after: float | None = 30.0, max_detour: float = 1.5, max_resolutions: int = 8
    ) -> None:
        self.reroute_after = reroute_after
        self.max_detour = max_detour
        self.max_resolutions = max_resolutions

    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]:
        keep = KeepWaiting(self.reroute_after)
        if wait.resolutions >= self.max_resolutions:
            return (keep,)
        stranded = [blocker for blocker in wait.blockers if blocker.disposition is Disposition.STRANDED]
        if stranded:
            return (Reroute(_avoid(stranded)), keep)
        if wait.reviews == 0:
            return (keep,)
        return (Reroute(_avoid(wait.blockers), self.max_detour), keep)

    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]:
        ordered = sorted(waits, key=lambda wait: (wait.priority, wait.loaded, -wait.since, wait.agv.id))
        bound = self.max_resolutions
        attempts: list[tuple[AGV, Reaction]] = [
            (wait.agv, GiveUp()) for wait in ordered if not wait.loaded and wait.resolutions >= bound
        ]
        movable = [wait for wait in ordered if wait.resolutions < bound]
        attempts += [(wait.agv, Reroute(_avoid(wait.blockers))) for wait in movable]
        attempts += [(wait.agv, StepAside()) for wait in movable]
        return attempts

    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        return None
