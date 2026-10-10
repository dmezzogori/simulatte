"""Constructor preflight and the ordered installation of layout graph consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import LayoutError, LayoutNotReady
from .lifecycle import PreparationState
from .refs import GraphRef, NodeRef, PortRef

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from .layout import Layout


def node_input(env: Environment, value: Any, *, optional: bool = False) -> tuple[Node | None, NodeRef | PortRef | None]:
    from simulatte.intralogistics.graph import Node

    if value is None and optional:
        return None, None
    layout = env.layout
    if layout is None:
        if not isinstance(value, Node):
            raise LayoutError("node references require their owning Layout environment")
        return value, None
    layout._accept_reference(value, (NodeRef, PortRef))
    if layout._coordinator.state is PreparationState.FINALIZED:
        return layout._resolve_node(value), None
    return None, value


def graph_input(env: Environment, value: Any) -> tuple[LayoutGraph | None, GraphRef | None]:
    from simulatte.intralogistics.graph import LayoutGraph

    layout = env.layout
    if layout is None:
        if not isinstance(value, LayoutGraph):
            raise LayoutError("graph references require their owning Layout environment")
        return value, None
    layout._accept_reference(value, (GraphRef,))
    if layout._coordinator.state is PreparationState.FINALIZED:
        return layout._graph_of(layout._coordinator.network), None
    return None, value


def pending(component: Any, field: str) -> None:
    env = getattr(component, "env", None) or component._env
    raise LayoutNotReady(
        f"{type(component).__name__}.{field} is unresolved while {env.layout._coordinator.state.value}"
    )


def readonly(env: Environment, field: str) -> None:
    if env.layout is not None:
        raise LayoutError(f"{field} is read-only in layout mode")


def check_existing_endpoints(env: Environment) -> None:
    """Reject concrete consumers, including mutable no-layout inputs changed later."""
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.warehouse import Warehouse

    for entity in env.entities.live():
        concrete = isinstance(entity, AGV) and entity._current_node is not None
        if isinstance(entity, Warehouse):
            concrete = any(
                getattr(entity, "_pending_" + field) is None and bool(getattr(entity, "_" + field))
                for field in ("input_bays", "output_bays")
            )
        if concrete:
            raise LayoutError("Layout cannot adopt existing explicit graph consumers")


def bind_components(layout: Layout) -> None:
    """Nodes, endpoints in attachment order, resources, then fleet graphs."""
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.warehouse import Warehouse

    network = layout._coordinator.network
    env = layout.env
    for node in sorted(network.graph.nodes, key=lambda node: node.id):
        layout._binding_node = node
        try:
            env.entities.bind_node(node)
        finally:
            layout._binding_node = None
    for entity in env.entities.live():
        if isinstance(entity, AGV) and entity._pending_node is not None:
            ref = entity._pending_node
            node = layout._bind_node(ref, entity.id, "node")
            entity._pending_node = None
            entity.current_node = node
        elif isinstance(entity, Warehouse):
            for field in ("input_bays", "output_bays"):
                refs = getattr(entity, "_pending_" + field)
                if refs is not None:
                    nodes = tuple(layout._bind_node(ref, entity.id, f"{field}/{i}") for i, ref in enumerate(refs))
                    setattr(entity, "_" + field, nodes)
                    setattr(entity, "_pending_" + field, None)
                else:
                    setattr(entity, "_" + field, tuple(getattr(entity, "_" + field)))
        elif isinstance(entity, (ChargingStation, ParkingArea)) and entity._pending_node is not None:
            entity._node = layout._bind_node(entity._pending_node, entity.id, "node")
            entity._pending_node = None
    for manager in layout._resource_managers:
        manager._install_layout_graph(network)
        layout._coordinator.closure.bind(
            "traffic:resources", {"graph": "network", "fallback_capacity": manager._node_capacity}
        )
    for entity in env.entities.live():
        if isinstance(entity, FleetCoordinator):
            entity._graph = network.graph
            entity._pending_graph = None
            layout._coordinator.closure.bind(f"{entity.id}:graph", {"graph": "network"})
