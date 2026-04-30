from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import simpy
from simpy.resources.resource import Request

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import LayoutGraph, Node


@dataclass
class PathCheckResult:
    feasible: bool
    conflict_nodes: list[Node] | None = None
    delay_until: float | None = None


@runtime_checkable
class TrafficManager(Protocol):
    @property
    def deadlock_timeout(self) -> float | None: ...
    def place(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult: ...
    def register_intent(self, agv: AGV, path: list[Node]) -> None: ...
    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator: ...
    def leave_node(self, agv: AGV, node: Node) -> None: ...
    def cancel(self, agv: AGV) -> None: ...


class FreeTrafficManager:
    @property
    def deadlock_timeout(self) -> float | None:
        return None

    def place(self, agv: AGV, node: Node) -> ProcessGenerator:  # noqa: ARG002
        return
        yield  # make it a generator

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:  # noqa: ARG002
        return PathCheckResult(feasible=True)

    def register_intent(self, agv: AGV, path: list[Node]) -> None:  # noqa: ARG002
        pass

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:  # noqa: ARG002
        return
        yield  # make it a generator

    def leave_node(self, agv: AGV, node: Node) -> None:  # noqa: ARG002
        pass

    def cancel(self, agv: AGV) -> None:  # noqa: ARG002
        pass


class ResourceBasedTrafficManager:
    def __init__(
        self,
        *,
        graph: LayoutGraph,
        env: Environment,
        node_capacity: int = 1,
        deadlock_timeout: float = 30.0,
        priority_fn: Callable[[AGV], float] | None = None,
    ) -> None:
        self._env = env
        self._graph = graph
        self._node_capacity = node_capacity
        self._deadlock_timeout = deadlock_timeout
        self._priority_fn = priority_fn or (lambda agv: 0.0)  # noqa: ARG005
        self._node_resources: dict[Node, simpy.Resource] = {}
        self._node_requests: dict[tuple[AGV, Node], Request] = {}
        self._pending_requests: dict[AGV, Request] = {}
        self._intents: dict[AGV, list[Node]] = {}

        for node in graph._nodes:
            self._node_resources[node] = simpy.Resource(env, capacity=node_capacity)

    @property
    def deadlock_timeout(self) -> float | None:
        return self._deadlock_timeout

    def place(self, agv: AGV, node: Node) -> ProcessGenerator:
        resource = self._node_resources[node]
        req = resource.request()
        self._node_requests[(agv, node)] = req
        yield req

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:
        if len(path) < 2:
            return PathCheckResult(feasible=True)

        path_future = set(path[1:])
        conflict_nodes: list[Node] = []

        for other_agv, other_path in self._intents.items():
            if other_agv is agv:
                continue
            other_future = set(other_path[1:])
            shared = path_future & other_future
            if shared:
                conflict_nodes.extend(shared)

        if conflict_nodes:
            unique_conflicts = list(set(conflict_nodes))
            self._env.debug(
                f"Path conflict for {agv.agv_id}: {[n.id for n in unique_conflicts]}",
                component="TrafficManager",
            )
            return PathCheckResult(feasible=False, conflict_nodes=unique_conflicts)
        return PathCheckResult(feasible=True)

    def register_intent(self, agv: AGV, path: list[Node]) -> None:
        self._intents[agv] = list(path)

    def enter_node(self, agv: AGV, node: Node) -> ProcessGenerator:
        resource = self._node_resources[node]
        req = resource.request()
        self._node_requests[(agv, node)] = req
        self._pending_requests[agv] = req
        try:
            yield req
        except simpy.Interrupt:
            # Interrupted by deadlock timeout — clean up local state only.
            # The actual resource request cancellation is handled by cancel()
            # which is called from _enter_with_timeout after the interrupt.
            self._pending_requests.pop(agv, None)
            key = (agv, node)
            if key in self._node_requests and self._node_requests[key] is req:
                del self._node_requests[key]
            return
        self._pending_requests.pop(agv, None)
        self._env.debug(
            f"{agv.agv_id} entered node {node.id}",
            component="TrafficManager",
        )

    def leave_node(self, agv: AGV, node: Node) -> None:
        key = (agv, node)
        if key in self._node_requests:
            req = self._node_requests.pop(key)
            resource = self._node_resources[node]
            if req.triggered:
                resource.release(req)
            else:
                req.cancel()
        if agv in self._intents and node in self._intents[agv]:
            self._intents[agv].remove(node)
        self._env.debug(
            f"{agv.agv_id} left node {node.id}",
            component="TrafficManager",
        )

    def cancel(self, agv: AGV) -> None:
        self._intents.pop(agv, None)
        if agv in self._pending_requests:
            req = self._pending_requests.pop(agv)
            if not req.triggered:
                req.cancel()
            # Also clean up the _node_requests entry for this pending request
            stale_keys = [k for k, v in self._node_requests.items() if k[0] is agv and v is req]
            for key in stale_keys:
                del self._node_requests[key]
