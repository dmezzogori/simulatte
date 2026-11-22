from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simpy.events import ProcessGenerator

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.routing import Route, Router
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.traffic import TrafficView
    from simulatte.intralogistics.warehouse import Warehouse

from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.routing import RouteRequest

if TYPE_CHECKING:
    from simulatte.intralogistics.fleet import FleetCoordinator


# ── Route cost ────────────────────────────────────────────────────────


def ranking_route(router: Router, view: TrafficView, agv: AGV, node: Node, load_weight: float = 0.0) -> Route | None:
    """The route `router` gives `agv` from its current node to `node` for a ranking (spec §10.4), or None when there
    is none or the AGV has no node.

    The router is asked as for the travel the ranking is for: ``RouteRequest(agv, agv.current_node, node, load_weight)``
    with the purpose ``"travel"``. A route whose cost is negative, NaN or infinite raises `ValueError` naming the
    router's class. Costs are comparable only within one router.
    """
    origin = agv.current_node
    if origin is None:
        return None
    route = router.route(RouteRequest(agv, origin, node, load_weight), view)
    if route is not None and not (math.isfinite(route.cost) and route.cost >= 0):
        raise ValueError(
            f"{type(router).__name__} returned a route of cost {route.cost} from {origin.id} to {node.id}; a route "
            "cost must be finite and >= 0"
        )
    return route


def _cost(router: Router, view: TrafficView, agv: AGV, node: Node, load_weight: float) -> float:
    route = ranking_route(router, view, agv, node, load_weight)
    return math.inf if route is None else route.cost


# ── DispatchStrategy ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchContext:
    """What a :class:`DispatchStrategy` decides with (spec §10.4): the fleet's graph, router and traffic view.

    The view is live: what the router reads from it is the traffic at the instant of the decision.
    """

    graph: LayoutGraph
    router: Router
    view: TrafficView

    def cost(self, agv: AGV, node: Node, *, load_weight: float = 0.0) -> float:
        """The router's cost of the travel of `agv` from its current node to `node` with `load_weight`; ``inf`` when
        the router finds no route or the AGV has no node (see :func:`ranking_route`)."""
        return _cost(self.router, self.view, agv, node, load_weight)


@runtime_checkable
class DispatchStrategy(Protocol):
    """Chooses the AGV for an order, or None to leave it pending.

    A strategy that reads the battery finds in ``agv.battery.level`` the level as last accounted; with idle power,
    :meth:`AGV.battery_level_now` is the level now (spec §9.5). Neither accounts anything.
    """

    def select(
        self,
        order: TransferOrder,
        fleet: Sequence[AGV],
        context: DispatchContext,
    ) -> AGV | None: ...


class NearestIdleStrategy:
    """Select the idle AGV that can carry the order's SKU and quantity and reaches the origin warehouse at the least
    cost.

    An AGV's cost is the least ``context.cost`` (the router's cost of the unloaded travel) over the origin warehouse's
    output bays, its best output bay. Ties are broken by ``agv_id`` (lexicographic). None when no such AGV can reach
    an output bay.
    """

    def select(
        self,
        order: TransferOrder,
        fleet: Sequence[AGV],
        context: DispatchContext,
    ) -> AGV | None:
        candidates = [
            agv
            for agv in fleet
            if agv.state == AGVState.IDLE and agv.current_node is not None and agv.can_carry(order.sku, order.quantity)
        ]
        if not candidates:
            return None

        bays = order.origin.output_bays
        costs = {agv: min(context.cost(agv, bay) for bay in bays) for agv in candidates}
        best = min(candidates, key=lambda agv: (costs[agv], agv.agv_id))
        if costs[best] == math.inf:
            return None
        return best


class RoundRobinStrategy:
    """Cycle through compatible idle AGVs via an internal cursor.

    The cursor increments monotonically. If fleet composition changes
    mid-simulation, cycling order over the filtered candidate list
    becomes non-deterministic. This is by design -- the modulo arithmetic
    prevents errors, but strict round-robin fairness is not guaranteed
    across fleet changes.
    """

    def __init__(self) -> None:
        self._cursor: int = 0

    def select(
        self,
        order: TransferOrder,
        fleet: Sequence[AGV],
        context: DispatchContext,
    ) -> AGV | None:
        candidates = [agv for agv in fleet if agv.state == AGVState.IDLE and agv.can_carry(order.sku, order.quantity)]
        if not candidates:
            return None

        idx = self._cursor % len(candidates)
        self._cursor += 1
        return candidates[idx]


# ── RepositioningPolicy ──────────────────────────────────────────────


@dataclass(frozen=True)
class RepositioningContext:
    """What a :class:`RepositioningPolicy` decides with: the fleet's graph, parking areas, charging stations, AGVs,
    router and traffic view (spec §10.4)."""

    graph: LayoutGraph
    parking_areas: Sequence[ParkingArea]
    charging_stations: Sequence[ChargingStation]
    fleet: Sequence[AGV]
    router: Router
    view: TrafficView

    def cost(self, agv: AGV, node: Node) -> float:
        """The router's cost of the unloaded travel of `agv` from its current node to `node`, the travel a
        repositioning makes; ``inf`` when the router finds no route or the AGV has no node (see
        :func:`ranking_route`)."""
        return _cost(self.router, self.view, agv, node, 0.0)


@runtime_checkable
class RepositioningPolicy(Protocol):
    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None: ...


class StayInPlace:
    """AGV stays at its current node after completing a task."""

    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None:
        return None


class NearestParkingPolicy:
    """Send the AGV to the parking area with available capacity that it reaches at the least ``context.cost``.

    Among areas of equal cost the first in ``context.parking_areas`` wins; when none is reachable, the first area with
    available capacity is named.
    """

    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None:
        available = [pa for pa in context.parking_areas if pa.available_capacity > 0]
        if not available:
            return None

        if agv.current_node is None:
            return None

        nearest = min(available, key=lambda pa: context.cost(agv, pa.node))
        return nearest.node


# ── ReplenishmentPolicy ──────────────────────────────────────────────


@runtime_checkable
class ReplenishmentPolicy(Protocol):
    def check(
        self,
        warehouse: Warehouse,
        all_warehouses: Sequence[Warehouse],
        in_transit_orders: list[TransferOrder],
    ) -> list[TransferOrder]: ...


class ReorderPointPolicy:
    """Trigger replenishment orders when inventory drops below a reorder point.

    For each SKU whose inventory is below the configured threshold, creates a
    ``TransferOrder`` sourced from the warehouse with the highest stock of that
    SKU — unless an in-transit order for the same SKU to the same warehouse
    already exists.
    """

    def __init__(
        self,
        thresholds: dict[SKU, int],
        reorder_quantity: dict[SKU, int],
    ) -> None:
        self._thresholds = thresholds
        self._reorder_quantity = reorder_quantity

    def check(
        self,
        warehouse: Warehouse,
        all_warehouses: Sequence[Warehouse],
        in_transit_orders: list[TransferOrder],
    ) -> list[TransferOrder]:
        orders: list[TransferOrder] = []

        for sku, threshold in self._thresholds.items():
            level = warehouse.get_inventory_level(sku)

            # Sum in-transit quantities for this SKU to this warehouse
            in_transit_qty = sum(
                o.quantity
                for o in in_transit_orders
                if o.sku == sku
                and o.destination is warehouse
                and o.status not in (OrderStatus.COMPLETED, OrderStatus.FAILED, OrderStatus.CANCELLED)
            )
            effective_stock = level + in_transit_qty
            if effective_stock >= threshold:
                continue

            # Find the source warehouse with the highest stock (excluding the monitored one)
            other_warehouses = [wh for wh in all_warehouses if wh is not warehouse]
            if not other_warehouses:
                continue

            source = max(other_warehouses, key=lambda wh: wh.get_inventory_level(sku))

            orders.append(
                TransferOrder(
                    sku=sku,
                    quantity=self._reorder_quantity[sku],
                    origin=source,
                    destination=warehouse,
                    created_at=warehouse.env.now,
                )
            )

        return orders


# ── LoadRecoveryStrategy ─────────────────────────────────────────────


@runtime_checkable
class LoadRecoveryStrategy(Protocol):
    def recover(
        self,
        order: TransferOrder,
        agv: AGV,
        coordinator: FleetCoordinator,
    ) -> ProcessGenerator: ...


class ReturnToOrigin:
    """Signal the coordinator to return cargo to the origin warehouse.

    Sets order status to PENDING. Physical travel and inventory return
    are handled by the coordinator, which re-queues the order after a successful
    return. A failed return leaves the order FAILED.
    """

    def recover(
        self,
        order: TransferOrder,
        agv: AGV,
        coordinator: FleetCoordinator,
    ) -> ProcessGenerator:
        order.status = OrderStatus.PENDING
        order.assigned_agv = None
        return
        yield  # pragma: no cover – unreachable; makes this a generator


class ResumeDelivery:
    """Keep the current AGV assignment and set the order to IN_TRANSIT.

    The actual movement is orchestrated by ``FleetCoordinator``.
    """

    def recover(
        self,
        order: TransferOrder,
        agv: AGV,
        coordinator: FleetCoordinator,
    ) -> ProcessGenerator:
        order.status = OrderStatus.IN_TRANSIT
        return
        yield  # pragma: no cover – unreachable; makes this a generator
