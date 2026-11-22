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
    review_after: float | None = None


@dataclass(frozen=True)
class Reroute:
    avoid: frozenset[Resource] = frozenset()
    max_cost_ratio: float | None = None


@dataclass(frozen=True)
class StepAside:
    target: Node | None = None


@dataclass(frozen=True)
class GiveUp:
    pass


Reaction = KeepWaiting | Reroute | StepAside | GiveUp


@runtime_checkable
class TrafficView(Protocol):
    """Read-only view of the network and its traffic (spec §10.1)."""

    @property
    def graph(self) -> LayoutGraph: ...
    def length(self, u: Node, v: Node) -> float: ...
    def speed_limit(self, u: Node, v: Node) -> float | None: ...
    def capacity(self, node: Node) -> int: ...
    def branch_root(self, node: Node) -> bool: ...
    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]: ...
    def holders(self, resource: Resource) -> tuple[AGV, ...]: ...
    def pending(self, resource: Resource) -> int: ...
    def disposition(self, agv: AGV) -> Disposition: ...
    def route_of(self, agv: AGV) -> tuple[Node, ...]: ...
    def waiting_on(self, agv: AGV) -> tuple[AGV, ...]: ...
    def admissible(self, agv: AGV, claim: Claim) -> bool: ...
    def mean_hold(self, resource: Resource) -> float: ...


@runtime_checkable
class TrafficPolicy(Protocol):
    """Decides how a waiting AGV reacts (spec §7.9)."""

    def on_blocked(self, wait: Wait, view: TrafficView) -> Sequence[Reaction]: ...
    def on_deadlock(self, waits: Sequence[Wait], view: TrafficView) -> Sequence[tuple[AGV, Reaction]]: ...
    def make_way_target(self, blocker: AGV, wait: Wait, view: TrafficView) -> Node | None: ...


@runtime_checkable
class TrafficController(Protocol):
    """Grants claims to AGVs (spec §7.1)."""

    controls_occupancy: bool
    view: TrafficView

    def register_fleet(self, fleet: FleetCoordinator) -> None: ...
    def place(self, agv: AGV, node: Node) -> None: ...
    def next_claim(self, agv: AGV, route: Sequence[Node], index: int) -> Claim: ...
    def request(self, agv: AGV, claim: Claim, hooks: WaitHooks) -> ClaimRequest: ...
    def stopped(self, request: ClaimRequest) -> None: ...
    def idle(self, agv: AGV) -> None: ...
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
    (spec §9.4, the only such rest that lasts), so it is ``SERVING``.
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
