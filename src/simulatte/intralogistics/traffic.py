from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import simpy
from simpy.resources.resource import Request

from simulatte.events import Deltas
from simulatte.layout.binding import graph_input, pending, readonly
from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.network import Claim
from simulatte.intralogistics.events import TrafficReleased, TrafficReserved, TrafficWaitEnded, TrafficWaitStarted

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.layout.refs import GraphRef
    from simulatte.layout.network import ResolvedNetwork
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import LayoutGraph, Node, NodeBinding
    from simulatte.intralogistics.network import Resource


@dataclass
class PathCheckResult:
    feasible: bool
    conflict_nodes: list[Node] | None = None
    delay_until: float | None = None


@runtime_checkable
class TrafficManager(Protocol):
    def place_now(self, agv: AGV, node: Node) -> None: ...
    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult: ...
    def register_intent(self, agv: AGV, path: list[Node]) -> None: ...
    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def leave_node(self, agv: AGV, node: Node) -> None: ...
    def cancel(self, agv: AGV) -> None: ...


class FreeTrafficManager:
    def place_now(self, agv: AGV, node: Node) -> None:
        pass

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:
        return PathCheckResult(feasible=True)

    def register_intent(self, agv: AGV, path: list[Node]) -> None:
        pass

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:
        return
        yield  # make it a generator

    def leave_node(self, agv: AGV, node: Node) -> None:
        pass

    def cancel(self, agv: AGV) -> None:
        pass


class ResourceBasedTrafficManager:
    """Node reservations backed by one ``simpy.Resource`` per node, with `node_capacity` slots each.

    Events (spec §6.4): ``traffic.reserved`` when a reservation is recorded in the node's ``reserved_by``
    (``place_now``, ``enter_node`` after the grant) and ``traffic.released`` when ``leave_node`` removes it;
    ``traffic.wait_started`` / ``traffic.wait_ended`` around an ``enter_node`` that has to wait for the node. A
    node without a binding in the environment (no fleet coordinator bound the graph) records no reservation and
    emits no reservation event. ``cancel`` withdraws waiting requests and releases a grant that ``enter_node`` has
    not recorded yet, so it never changes ``reserved_by``; it ends a pending wait.
    """

    def __init__(
        self,
        *,
        graph: LayoutGraph | GraphRef,
        env: Environment,
        node_capacity: int = 1,
        deadlock_timeout: float | None = 30.0,
        priority_fn: Callable[[AGV], float] | None = None,
    ) -> None:
        self._env = env
        self._graph, self._pending_graph = graph_input(env, graph)
        if node_capacity <= 0:
            raise ValueError('"capacity" must be > 0.')
        if env.layout is not None:
            env.layout._register_manager(self)
        self._node_capacity = node_capacity
        self._deadlock_timeout = deadlock_timeout
        self._priority_fn = priority_fn or (lambda agv: 0.0)
        self._node_resources: dict[Node, simpy.Resource] = {}
        self._node_requests: dict[tuple[AGV, Node], Request] = {}
        self._pending_requests: dict[AGV, Request] = {}
        self._intents: dict[AGV, list[Node]] = {}
        self._waiting: dict[Request, Node] = {}  # enter_node requests with an open traffic.wait_started

        if self._graph is not None:
            for node in self._graph.nodes:
                self._node_resources[node] = simpy.Resource(env, capacity=node_capacity)
        if env.layout is None:
            env._register_graph_use()

    @property
    def graph(self) -> LayoutGraph:
        if self._pending_graph is not None:
            pending(self, "graph")
        assert self._graph is not None
        return self._graph

    @graph.setter
    def graph(self, value: LayoutGraph) -> None:
        readonly(self._env, "graph")
        self._graph = value

    def _install_layout_graph(self, network: ResolvedNetwork) -> None:
        self._graph = network.graph
        self._pending_graph = None
        for node in self._graph.nodes:
            capacity = network.capacities[node]
            self._node_resources[node] = simpy.Resource(
                self._env, capacity=self._node_capacity if capacity is None else capacity
            )

    @property
    def deadlock_timeout(self) -> float | None:
        return self._deadlock_timeout

    def priority(self, agv: AGV) -> float:
        return self._priority_fn(agv)

    def place_now(self, agv: AGV, node: Node) -> None:
        """Reserve `node` for `agv` immediately; raise `RuntimeError` if the node is not free.

        Used for the initial placement of AGVs, from an activation initializer (spec §10): only the node request
        is allowed to schedule its (bookkeeping) grant event.
        """
        resource = self._node_resources[node]
        with self._env._internal_scheduling():
            req = resource.request()
        if not req.triggered:
            req.cancel()
            raise RuntimeError(
                f"cannot place {agv.agv_id} at node {node.id}: the node is fully reserved "
                f"(capacity {resource.capacity})"
            )
        self._node_requests[(agv, node)] = req
        self._reserve(agv, node)

    def _binding(self, node: Node) -> NodeBinding | None:
        return self._env.entities.node_binding(node)

    def _reserve(self, agv: AGV, node: Node) -> None:
        """Record the reservation in the node's ``reserved_by`` and emit ``traffic.reserved``."""
        binding = self._binding(node)
        if binding is not None:
            reserved_by = binding.reserved_by
            reserved_by.append(agv.id)
            env = self._env
            if env.wants(TrafficReserved):
                env.emit(
                    TrafficReserved(
                        agv=agv.id,
                        resources=(binding.id,),
                        deltas=Deltas.build().insert(binding.id, "reserved_by", len(reserved_by) - 1, agv.id).done(),
                    )
                )

    def _unreserve(self, agv: AGV, node: Node) -> None:
        """Remove the reservation from the node's ``reserved_by`` and emit ``traffic.released``."""
        binding = self._binding(node)
        if binding is not None and agv.id in binding.reserved_by:
            binding.reserved_by.remove(agv.id)
            env = self._env
            if env.wants(TrafficReleased):
                env.emit(
                    TrafficReleased(
                        agv=agv.id,
                        resources=(binding.id,),
                        deltas=Deltas.build().remove(binding.id, "reserved_by", agv.id).done(),
                    )
                )

    def _start_wait(self, agv: AGV, node: Node, req: Request) -> None:
        """Open the wait of `req`, which was not granted at once, and emit ``traffic.wait_started``."""
        self._waiting[req] = node
        env = self._env
        if env.wants(TrafficWaitStarted):
            env.emit(TrafficWaitStarted(agv=agv.id, node=node.id, reason="node_occupied"))

    def _end_wait(self, agv: AGV, req: Request, reason: str) -> None:
        """Close the wait of `req`, if it has one open, and emit ``traffic.wait_ended`` with `reason`."""
        node = self._waiting.pop(req, None)
        if node is None:
            return
        env = self._env
        if env.wants(TrafficWaitEnded):
            env.emit(TrafficWaitEnded(agv=agv.id, node=node.id, reason=reason))

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:
        if len(path) < 2:
            return PathCheckResult(feasible=True)

        others_future: set[Node] = set()
        for other_agv, other_path in self._intents.items():
            if other_agv is not agv:
                others_future.update(other_path[1:])

        # Conflicts in path order, each once (spec §5.3): independent of hash seeds.
        conflict_nodes = list(dict.fromkeys(node for node in path[1:] if node in others_future))
        if conflict_nodes:
            return PathCheckResult(feasible=False, conflict_nodes=conflict_nodes)
        return PathCheckResult(feasible=True)

    def register_intent(self, agv: AGV, path: list[Node]) -> None:
        self._intents[agv] = list(path)

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:
        resource = self._node_resources[node]
        req = resource.request()
        self._node_requests[(agv, node)] = req
        self._pending_requests[agv] = req
        if not req.triggered:
            self._start_wait(agv, node, req)
        try:
            yield req
        except simpy.Interrupt as interrupt:
            # An interrupt can arrive after the grant but before this generator resumes.
            # Withdraw only this entry request; the AGV still occupies its previous node.
            if req.triggered:
                resource.release(req)
            elif req in resource.queue:
                req.cancel()
            if self._pending_requests.get(agv) is req:
                self._pending_requests.pop(agv)
            self._end_wait(agv, req, "interrupted")
            key = (agv, node)
            if self._node_requests.get(key) is req:
                del self._node_requests[key]
            # The timeout helper intentionally abandons its child process; ordinary
            # mission interrupts must reach the caller instead of implying entry.
            if interrupt.cause == "deadlock_timeout":
                return
            raise
        self._pending_requests.pop(agv, None)
        self._end_wait(agv, req, "granted")
        self._reserve(agv, node)

    def leave_node(self, agv: AGV, node: Node) -> None:
        key = (agv, node)
        if key in self._node_requests:
            req = self._node_requests.pop(key)
            resource = self._node_resources[node]
            if req.triggered:
                resource.release(req)
                self._unreserve(agv, node)
            else:
                req.cancel()
        if agv in self._intents and node in self._intents[agv]:
            self._intents[agv].remove(node)

    def cancel(self, agv: AGV) -> None:
        def _cancel_request(req: Request) -> None:
            try:
                req.cancel()
            except ValueError:
                # SimPy removes the request from the queue when an interrupted
                # enter_node process resumes; stale cleanup may arrive after that.
                pass

        self._intents.pop(agv, None)

        if agv in self._pending_requests:
            req = self._pending_requests.pop(agv)
            if req.triggered:
                stale_keys = [k for k, v in self._node_requests.items() if k[0] is agv and v is req]
                for key in stale_keys:
                    if key[1] == agv.current_node:
                        continue
                    self._node_requests.pop(key, None)
                    # Granted while enter_node was not resumed yet: never recorded in reserved_by.
                    self._node_resources[key[1]].release(req)
            else:
                _cancel_request(req)
            self._end_wait(agv, req, "cancelled")

        # Clean up stale pending entries for this AGV. Triggered node requests
        # represent physical occupancy and must be released only by leave_node().
        stale_keys = [k for k in self._node_requests if k[0] is agv]
        for key in stale_keys:
            req = self._node_requests[key]
            if not req.triggered:
                self._node_requests.pop(key)
                _cancel_request(req)
                self._end_wait(agv, req, "cancelled")


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
    """The request was withdrawn because the controller staged a reroute through the hooks."""


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
    """A waiting AGV as the policy sees it."""

    agv: AGV
    claim: Claim
    since: float
    blockers: tuple[Blocker, ...]
    reviews: int
    route: tuple[Node, ...]
    loaded: bool
    resolutions: int


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
    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]: ...
    def holders(self, resource: Resource) -> tuple[AGV, ...]: ...
    def pending(self, resource: Resource) -> int: ...
    def disposition(self, agv: AGV) -> Disposition: ...
    def route_of(self, agv: AGV) -> tuple[Node, ...]: ...
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

    def resources(self, u: Node, v: Node) -> tuple[Resource, ...]:
        return ()

    def holders(self, resource: Resource) -> tuple[AGV, ...]:
        return ()

    def pending(self, resource: Resource) -> int:
        return 0

    def disposition(self, agv: AGV) -> Disposition:
        return _DISPOSITIONS[agv.state]

    def route_of(self, agv: AGV) -> tuple[Node, ...]:
        return ()

    def admissible(self, agv: AGV, claim: Claim) -> bool:
        return True

    def mean_hold(self, resource: Resource) -> float:
        return 0.0


class FreeFlowController:
    """Grants every claim at once: AGVs never wait for one another. It creates no entities and emits no events."""

    controls_occupancy = False

    def __init__(self) -> None:
        self._fleet: FleetCoordinator | None = None
        self.view: TrafficView = _FreeFlowView(self)

    def register_fleet(self, fleet: FleetCoordinator) -> None:
        if self._fleet is None:
            self._fleet = fleet

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

    def withdraw(self, request: ClaimRequest) -> None:
        pass

    def release(self, agv: AGV, resources: Collection[Resource]) -> None:
        pass

    def retain(self, agv: AGV, resources: Collection[Resource]) -> None:
        pass
