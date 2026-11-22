from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import simpy

from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.network import Claim

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.network import Resource


class Disposition(Enum):
    """What an AGV is doing, as far as traffic is concerned."""

    MOVING = auto()
    WAITING = auto()
    SERVING = auto()
    IDLE = auto()
    STRANDED = auto()


@dataclass(frozen=True)
class Granted:
    """The request was granted."""


@dataclass(frozen=True)
class Rerouted:
    """The request was withdrawn because the controller staged a reroute through the hooks.

    `reason` is ``"blocked"`` for a reaction of ``on_blocked`` and ``"deadlock"`` for a deadlock resolution.
    """

    reason: str = "blocked"


@dataclass(frozen=True)
class SteppedAside:
    """The request was withdrawn because the controller staged a step aside through the hooks."""


@dataclass(frozen=True)
class GaveUp:
    """The request was withdrawn because the controller staged a give-up through the hooks."""


@dataclass
class WaitHooks:
    """What the controller and the policy may ask of the waiting travel (spec §7.1)."""

    route: Callable[[], tuple[Node, ...]]  # remaining route, current node first
    loaded: Callable[[], bool]  # cargo on board
    resolutions: Callable[[], int]
    try_reroute: Callable[[frozenset[Resource], float | None], bool]
    try_step_aside: Callable[[Node | None], bool]
    give_up: Callable[[], bool]


class ClaimRequest:
    """A claim an AGV has asked for. ``event`` succeeds with ``Granted`` or a directive staged through the hooks."""

    def __init__(self, agv: AGV, claim: Claim, hooks: WaitHooks, event: simpy.Event) -> None:
        self.agv = agv
        self.claim = claim
        self.hooks = hooks
        self.event = event

    @property
    def granted(self) -> bool:
        """Whether the event has succeeded with ``Granted``."""
        return self.event.triggered and self.event.ok and isinstance(self.event.value, Granted)


@dataclass(frozen=True)
class Blocker:
    """An AGV that holds resources a waiting AGV needs."""

    agv: AGV
    disposition: Disposition
    resources: tuple[Resource, ...]


@dataclass(frozen=True)
class Wait:
    """A waiting AGV as the policy sees it; `priority` is the one the controller read when the request was made."""

    agv: AGV
    claim: Claim
    since: float
    blockers: tuple[Blocker, ...]
    reviews: int
    route: tuple[Node, ...]
    loaded: bool
    resolutions: int
    priority: float = 0.0


@dataclass(frozen=True)
class KeepWaiting:
    """Reaction: keep waiting; with `review_after`, ``on_blocked`` is asked again after that delay (spec §7.9)."""

    review_after: float | None = None


@dataclass(frozen=True)
class Reroute:
    """Reaction: take the fleet's router's route that avoids `avoid`, if its first claim is admissible now and, with
    `max_cost_ratio`, its cost is at most that ratio times the cost of the route without `avoid` (spec §7.9)."""

    avoid: frozenset[Resource] = frozenset()
    max_cost_ratio: float | None = None


@dataclass(frozen=True)
class StepAside:
    """Reaction: move to `target`, or to the node of the standard search when None, then route to the goal again."""

    target: Node | None = None


@dataclass(frozen=True)
class GiveUp:
    """Reaction: end the travel with ``GAVE_UP``, when the travel's ``give_up`` hook accepts (spec §8.2)."""


Reaction = KeepWaiting | Reroute | StepAside | GiveUp


@runtime_checkable
class TrafficView(Protocol):
    """Read-only view of the network and its traffic (spec §10.1).

    On Python 3.11 an ``isinstance`` check against this ``runtime_checkable`` protocol reads every member of the object,
    so for the built-in view of a ``ReservationController`` that is not activated yet (its ``graph`` needs the network)
    it raises; the library itself makes no such check.
    """

    @property
    def graph(self) -> LayoutGraph: ...
    def length(self, u: Node, v: Node) -> float: ...
    def speed_limit(self, u: Node, v: Node) -> float | None: ...
    def capacity(self, node: Node) -> int:
        """The capacity of `node` (spec §6.2)."""
        ...

    def branch_root(self, node: Node) -> bool:
        """Whether `node` is the root of a dead-end branch (spec §6.5); false when ``hold_back_dead_ends`` is off."""
        ...

    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]:
        """What the leg from `u` to `v` needs: its segment, its end node and the zones and corridors containing them."""
        ...

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        """The AGVs that hold `resource` now."""
        ...

    def pending(self, resource: Resource) -> int:
        """The number of queued claims that still need `resource`, the claim of the AGV that asks included."""
        ...

    def disposition(self, agv: AGV) -> Disposition:
        """What `agv` is doing as traffic sees it; ``WAITING`` for an AGV at rest with a pending claim."""
        ...

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        """The remaining route of the travel of `agv` while it has a pending claim, else empty."""
        ...

    def waiting_on(self, agv: AGV) -> tuple[AGV, ...]:
        """The AGVs whose waits a resource held by `agv` blocks."""
        ...

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        """Whether `claim` would be granted to `agv` now as a new request, the fairness gate included."""
        ...

    def blockers(self, agv: AGV, u: Node, v: Node) -> tuple[tuple[Resource, tuple[AGV, ...]], ...]:
        """For each resource of the leg from `u` to `v` that its holders make inadmissible for `agv` now, the
        holders that block it, in the leg's resource order. The queue and the gate are ignored."""
        ...

    def mean_hold(self, resource: Resource) -> float:
        """The exponential average (factor 0.1) of the observed hold durations of `resource`; before the first one,
        the average over resources of the same kind, else 0.0 (spec §10.2)."""
        ...


@runtime_checkable
class TrafficPolicy(Protocol):
    """Decides how a waiting AGV reacts (spec §7.9).

    On Python 3.11 an ``isinstance`` check against a ``runtime_checkable`` protocol of this module reads every member of
    the object, so a check of the built-in view of a ``ReservationController`` that is not activated yet against
    :class:`TrafficView` raises (its ``graph`` needs the network); the library itself makes no such check.
    """

    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]: ...
    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]: ...
    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None:
        """Where the idle `blocker` moves to clear the way for `wait`; None selects the standard search (spec §7.10).

        A target the fleet's router cannot reach, or a parking area without a free slot, is refused: the blocker
        stays, even where the standard search would have found a node, and the wait can then be reported stuck.
        """
        ...


@runtime_checkable
class TrafficController(Protocol):
    """Grants claims to AGVs (spec §7.1).

    On Python 3.11 an ``isinstance`` check against a ``runtime_checkable`` protocol of this module reads every member of
    the object, so a check of the built-in view of a ``ReservationController`` that is not activated yet against
    :class:`TrafficView` raises (its ``graph`` needs the network); the library itself makes no such check.
    """

    controls_occupancy: bool
    view: TrafficView

    def register_fleet(self, fleet: FleetCoordinator) -> None: ...
    def place(self, agv: AGV, node: Node) -> None: ...
    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim: ...
    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest: ...
    def stopped(self, request: ClaimRequest) -> None: ...
    def idle(self, agv: AGV) -> None:
        """`agv` has just come to rest without a travel where it stands: idle or stranded. The coordinator calls it on
        every transition to ``IDLE`` and to ``STRANDED``.

        ``ReservationController`` asks an idle AGV to make way for the waits it blocks, and reviews at once the waits a
        stranded AGV blocks (spec §7.10); ``FreeFlowController`` does nothing.
        """
        ...

    def withdraw(self, request: ClaimRequest) -> None: ...
    def release(self, agv: AGV, resources: Collection[Resource]) -> None: ...
    def retain(self, agv: AGV, resources: Collection[Resource]) -> None: ...


_DISPOSITIONS = {
    AGVState.IDLE: Disposition.IDLE,
    AGVState.STRANDED: Disposition.STRANDED,
    AGVState.WAITING_LOAD: Disposition.SERVING,
    AGVState.WAITING_UNLOAD: Disposition.SERVING,
    AGVState.CHARGING: Disposition.SERVING,
    AGVState.TRAVELING_EMPTY: Disposition.MOVING,
    AGVState.TRAVELING_LOADED: Disposition.MOVING,
}


def disposition_of(agv: AGV) -> Disposition:
    """The disposition that the AGV's state implies; a controller reports ``WAITING`` for an open wait instead.

    A travelling AGV without an active motion is at rest between two arcs and not in a traffic wait: it is turning
    (spec §9.4, the only such rest that lasts), so it is ``SERVING``. Inside an ``agv.move_ended`` handler at a node the
    AGV passes at speed it also reads ``SERVING``: its motion is cleared at that event and set again for the next arc
    at the same instant.
    """
    disposition = _DISPOSITIONS[agv.state]
    if disposition is Disposition.MOVING and agv.motion is None:
        return Disposition.SERVING
    return disposition


class _FreeFlowView:
    """The view of a network without traffic: the graph is real, everything dynamic is empty."""

    def __init__(self, controller: FreeFlowController) -> None:
        self._controller = controller

    @property
    def graph(self) -> LayoutGraph:
        fleet = self._controller._fleet
        if fleet is None:
            raise RuntimeError("no fleet registered with the traffic controller")
        return fleet.graph

    def length(self, u: Node, v: Node) -> float:
        return self.graph.length(u, v)

    def speed_limit(self, u: Node, v: Node) -> float | None:
        arc = self.graph.arc_between(u, v)
        if arc is None:
            raise ValueError(f"Nodes {u.id} and {v.id} are not connected by an arc")
        return arc.speed_limit

    def capacity(self, node: Node) -> int:
        return 1

    def branch_root(self, node: Node) -> bool:
        return False

    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]:
        return ()

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        return ()

    def pending(self, resource: Resource) -> int:
        return 0

    def disposition(self, agv: AGV) -> Disposition:
        return disposition_of(agv)

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        return ()

    def waiting_on(self, agv: AGV) -> tuple[AGV, ...]:
        return ()

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        return True

    def blockers(self, agv: AGV, u: Node, v: Node) -> tuple[tuple[Resource, tuple[AGV, ...]], ...]:
        return ()

    def mean_hold(self, resource: Resource) -> float:
        return 0.0


def _graph_of(fleet: FleetCoordinator) -> object:
    """The fleet's graph, or its layout reference while the layout is not prepared."""
    pending = getattr(fleet, "_pending_graph", None)
    return fleet.graph if pending is None else pending


class FreeFlowController:
    """Grants every claim at once: AGVs never wait for one another. It creates no entities and emits no events."""

    controls_occupancy = False

    def __init__(self) -> None:
        self._fleet: FleetCoordinator | None = None
        self.view: TrafficView = _FreeFlowView(self)

    def register_fleet(self, fleet: FleetCoordinator) -> None:
        """Serve `fleet`; it must be on the graph of the fleets already registered (spec §7.6).

        Raises `ValueError` for a fleet on another graph: the view exposes one graph to the routers, so each graph
        needs its own controller. A fleet whose graph is still a layout reference is compared by that reference.
        """
        if self._fleet is None:
            self._fleet = fleet
        elif _graph_of(fleet) != _graph_of(self._fleet):
            raise ValueError(
                "register_fleet: this FreeFlowController already serves another graph; "
                "each graph needs its own FreeFlowController"
            )

    def place(self, agv: AGV, node: Node) -> None:
        pass

    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim:
        return Claim((), len(route) - 1 - index)

    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest:
        event = simpy.Event(agv.env)
        event.succeed(Granted())
        return ClaimRequest(agv, claim, hooks, event)

    def stopped(self, request: ClaimRequest) -> None:
        pass

    def idle(self, agv: AGV) -> None:
        pass

    def withdraw(self, request: ClaimRequest) -> None:
        pass

    def release(self, agv: AGV, resources: Collection[Resource]) -> None:
        pass

    def retain(self, agv: AGV, resources: Collection[Resource]) -> None:
        pass
