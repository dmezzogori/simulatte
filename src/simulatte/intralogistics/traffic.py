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
    from simulatte.intralogistics.graph import LayoutGraph, Node, NodeBinding


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
        self._priority_fn = priority_fn or (lambda agv: 0.0)
        self._node_resources: dict[Node, simpy.Resource] = {}
        self._node_requests: dict[tuple[AGV, Node], Request] = {}
        self._pending_requests: dict[AGV, Request] = {}
        self._intents: dict[AGV, list[Node]] = {}

        for node in graph.nodes:
            self._node_resources[node] = simpy.Resource(env, capacity=node_capacity)

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
        binding = self._binding(node)
        if binding is not None:
            binding.reserved_by.append(agv.id)

    def _unreserve(self, agv: AGV, node: Node) -> None:
        binding = self._binding(node)
        if binding is not None and agv.id in binding.reserved_by:
            binding.reserved_by.remove(agv.id)

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
            self._env.debug(
                f"Path conflict for {agv.agv_id}: {[n.id for n in conflict_nodes]}",
                component="TrafficManager",
            )
            return PathCheckResult(feasible=False, conflict_nodes=conflict_nodes)
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
            if (
                key in self._node_requests and self._node_requests[key] is req
            ):  # pragma: no cover - defensive; cancel() normally cleans up first
                del self._node_requests[key]
            return
        self._pending_requests.pop(agv, None)
        self._reserve(agv, node)
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
                self._unreserve(agv, node)
            else:
                req.cancel()
        if agv in self._intents and node in self._intents[agv]:
            self._intents[agv].remove(node)
        self._env.debug(
            f"{agv.agv_id} left node {node.id}",
            component="TrafficManager",
        )

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

        # Clean up stale pending entries for this AGV. Triggered node requests
        # represent physical occupancy and must be released only by leave_node().
        stale_keys = [k for k in self._node_requests if k[0] is agv]
        for key in stale_keys:
            req = self._node_requests[key]
            if not req.triggered:
                self._node_requests.pop(key)
                _cancel_request(req)
