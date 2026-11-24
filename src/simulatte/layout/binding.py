"""Constructor preflight and the ordered installation of layout graph consumers."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, NoReturn

from .errors import LayoutError, LayoutNotReady
from .lifecycle import PreparationState
from .refs import GraphRef, NodeRef, PortRef

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.warehouse import Warehouse
    from .layout import Layout


def reject_node(value: Any, *, component: str, parameter: str) -> None:
    """Raise `LayoutError` naming the fix when `value` is a concrete `Node` given for `parameter` (spec §5.3)."""
    from simulatte.intralogistics.graph import Node

    if isinstance(value, Node):
        raise LayoutError(
            f"{component}({parameter}=...) takes layout.node(id) or layout.port(...) handles, not Node objects",
            code="foreign-handle",
        )


def node_input(
    env: Environment, value: Any, *, component: str, parameter: str, optional: bool = False
) -> tuple[Node | None, NodeRef | PortRef | None]:
    """The node of a component's `parameter`: a handle of the environment's layout, resolved at once when the layout
    is finalized, else kept as pending. A concrete `Node` raises `LayoutError` naming the fix (spec §5.3); a handle of
    another layout raises too. ``None`` is accepted when `optional`."""
    if value is None and optional:
        return None, None
    reject_node(value, component=component, parameter=parameter)
    layout = env.layout
    layout._accept_reference(value, (NodeRef, PortRef))
    if layout._coordinator.state is PreparationState.FINALIZED:
        return layout._resolve_late_node(value), None
    return None, value


def graph_input(env: Environment, value: Any, *, component: str) -> tuple[LayoutGraph | None, GraphRef | None]:
    """The graph of a component: ``layout.graph()`` of the environment's layout, resolved at once when the layout is
    finalized, else kept as pending. A concrete `LayoutGraph` raises `LayoutError` naming the fix (spec §5.3)."""
    from simulatte.intralogistics.graph import LayoutGraph

    if isinstance(value, LayoutGraph):
        raise LayoutError(
            f"{component}(graph=...) takes layout.graph(); declare the graph with "
            "env.layout.set_network(Explicit(graph))",
            code="foreign-handle",
        )
    layout = env.layout
    layout._accept_reference(value, (GraphRef,))
    if layout._coordinator.state is PreparationState.FINALIZED:
        return layout._graph_of(layout._coordinator.network), None
    return None, value


def pending(component: Any, field: str) -> None:
    env = getattr(component, "env", None) or component._env
    raise LayoutNotReady(
        f"{type(component).__name__}.{field} is unresolved while {env._layout._coordinator.state.value}: it is bound "
        "when the layout is prepared; call env.layout.prepare() or env.activate() before reading it",
        code="lifecycle-error",
        target=getattr(component, "id", None),
        path=("bindings", field),
    )


def readonly(field: str) -> NoReturn:
    raise LayoutError(
        f"{field} is read-only: it is bound from the layout's handles", code="lifecycle-error", path=("bindings", field)
    )


def _loaded(module: str, name: str) -> tuple[Any, ...]:
    """The class `name` of `module` if that module is imported, else no class.

    An instance exists only once the module of its class was imported, so a model without such components needs
    neither the module nor the check: a layout without a network imports no fleet, traffic or AGV module (spec §9).
    """
    cls = getattr(sys.modules.get(module), name, None)
    return () if cls is None else (cls,)


def bind_components(layout: Layout) -> None:
    """Nodes, endpoints in attachment order, the traffic controller's resources, then fleet graphs."""
    agv: tuple[type[AGV], ...] = _loaded("simulatte.intralogistics.agv", "AGV")
    warehouse: tuple[type[Warehouse], ...] = _loaded("simulatte.intralogistics.warehouse", "Warehouse")
    stations: tuple[type[ChargingStation | ParkingArea], ...] = (
        *_loaded("simulatte.intralogistics.charging", "ChargingStation"),
        *_loaded("simulatte.intralogistics.parking", "ParkingArea"),
    )
    fleet: tuple[type[FleetCoordinator], ...] = _loaded("simulatte.intralogistics.fleet", "FleetCoordinator")
    network = layout._coordinator.network
    env = layout.env
    for node in sorted(network.graph.nodes, key=lambda node: node.id):
        layout._binding_node = node
        try:
            env.entities.bind_node(node)
        finally:
            layout._binding_node = None
    for entity in env.entities.live():
        if isinstance(entity, agv) and entity._pending_node is not None:
            ref = entity._pending_node
            node = layout._bind_node(ref, entity.id, "node")
            entity._pending_node = None
            entity.current_node = node
        elif isinstance(entity, warehouse):
            for field in ("input_bays", "output_bays"):
                refs = getattr(entity, "_pending_" + field)
                if refs is not None:
                    nodes = tuple(layout._bind_node(ref, entity.id, f"{field}/{i}") for i, ref in enumerate(refs))
                    setattr(entity, "_" + field, nodes)
                    setattr(entity, "_pending_" + field, None)
        elif isinstance(entity, stations) and entity._pending_node is not None:
            entity._node = layout._bind_node(entity._pending_node, entity.id, "node")
            entity._pending_node = None
    for controller in layout._resource_managers:
        controller._install_layout_graph(network)
        layout._coordinator.closure.bind(
            "traffic:resources", {"graph": "network", "fallback_capacity": controller._node_capacity}
        )
    for entity in env.entities.live():
        if isinstance(entity, fleet):
            entity._graph = network.graph
            entity._pending_graph = None
            layout._coordinator.closure.bind(f"{entity.id}:graph", {"graph": "network"})
