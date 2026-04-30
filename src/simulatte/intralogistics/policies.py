from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simpy.events import ProcessGenerator

    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.warehouse import Warehouse

from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.order import OrderStatus, TransferOrder

if TYPE_CHECKING:
    from simulatte.intralogistics.fleet import FleetCoordinator  # type: ignore[import-not-found]  # module created in Task 14


# ── DispatchStrategy ──────────────────────────────────────────────────


@runtime_checkable
class DispatchStrategy(Protocol):
    def select(
        self,
        order: TransferOrder,
        fleet: list[AGV],
        graph: LayoutGraph,
    ) -> AGV | None: ...


class NearestIdleStrategy:
    """Select the closest idle AGV that can carry the order's SKU and quantity.

    Distance is measured as the sum of Euclidean segment lengths along the
    shortest graph path from the AGV's current node to the origin warehouse's
    nearest output bay.  Ties are broken by ``agv_id`` (lexicographic).
    """

    def select(
        self,
        order: TransferOrder,
        fleet: list[AGV],
        graph: LayoutGraph,
    ) -> AGV | None:
        candidates = [
            agv
            for agv in fleet
            if agv.state == AGVState.IDLE
            and agv.current_node is not None
            and agv.can_carry(order.sku, order.quantity)
        ]
        if not candidates:
            return None

        output_bay = order.origin.nearest_output_bay(candidates[0].current_node, graph)

        def _distance(agv: AGV) -> tuple[float, str]:
            assert agv.current_node is not None
            path = graph.shortest_path(agv.current_node, output_bay)
            if path is None:
                return (float("inf"), agv.agv_id)
            d = sum(
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
                for i in range(len(path) - 1)
            )
            return (d, agv.agv_id)

        return min(candidates, key=_distance)


class RoundRobinStrategy:
    """Cycle through compatible idle AGVs via an internal cursor."""

    def __init__(self) -> None:
        self._cursor: int = 0

    def select(
        self,
        order: TransferOrder,
        fleet: list[AGV],
        graph: LayoutGraph,
    ) -> AGV | None:
        candidates = [
            agv
            for agv in fleet
            if agv.state == AGVState.IDLE and agv.can_carry(order.sku, order.quantity)
        ]
        if not candidates:
            return None

        idx = self._cursor % len(candidates)
        self._cursor += 1
        return candidates[idx]


# ── RepositioningPolicy ──────────────────────────────────────────────


@dataclass
class RepositioningContext:
    graph: LayoutGraph
    parking_areas: list[ParkingArea]
    charging_stations: list[ChargingStation]
    fleet: list[AGV]


@runtime_checkable
class RepositioningPolicy(Protocol):
    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None: ...


class StayInPlace:
    """AGV stays at its current node after completing a task."""

    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None:
        return None


class NearestParkingPolicy:
    """Send the AGV to the nearest parking area that has available capacity."""

    def reposition(self, agv: AGV, context: RepositioningContext) -> Node | None:
        available = [
            pa
            for pa in context.parking_areas
            if pa._resource.count < pa._resource.capacity
        ]
        if not available:
            return None

        if agv.current_node is None:
            return None

        current = agv.current_node

        def _distance(pa: ParkingArea) -> float:
            path = context.graph.shortest_path(current, pa.node)
            if path is None:
                return float("inf")
            return sum(
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
                for i in range(len(path) - 1)
            )

        nearest = min(available, key=_distance)
        return nearest.node


# ── ReplenishmentPolicy ──────────────────────────────────────────────


@runtime_checkable
class ReplenishmentPolicy(Protocol):
    def check(
        self,
        warehouse: Warehouse,
        all_warehouses: list[Warehouse],
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
        all_warehouses: list[Warehouse],
        in_transit_orders: list[TransferOrder],
    ) -> list[TransferOrder]:
        orders: list[TransferOrder] = []

        for sku, threshold in self._thresholds.items():
            level = warehouse.get_inventory_level(sku)
            if level >= threshold:
                continue

            # Check if there is already an in-transit order for this SKU to this warehouse
            already_in_transit = any(
                o.destination is warehouse and o.sku is sku for o in in_transit_orders
            )
            if already_in_transit:
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
    """Reset the order to PENDING and clear the AGV assignment.

    The actual navigation back to the origin is orchestrated by
    ``FleetCoordinator``.
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
