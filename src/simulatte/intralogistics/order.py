from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema

if TYPE_CHECKING:
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.warehouse import Warehouse


class OrderStatus(Enum):
    PENDING = auto()
    DISPATCHED = auto()
    PICKING = auto()
    IN_TRANSIT = auto()
    DELIVERING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    PENDING_ACTIVATION = auto()
    """Submitted before the environment activated; the submission runs at activation (spec §10)."""


TERMINAL_STATUSES = frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.FAILED})
"""Statuses at which an order retires (spec §5.1)."""


@dataclass
class TransferOrder(Entity, kind="order"):
    """A request to move ``quantity`` units of ``sku`` from ``origin`` to ``destination``.

    The order becomes an entity when it is attached: ``FleetCoordinator.create_order`` attaches it, and
    ``FleetCoordinator.submit`` attaches an order constructed directly. ``id`` is None until then and
    ``order-<n>`` afterwards. The order retires when it reaches a terminal status.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "status": FieldSpec("str"),
            "sku": FieldSpec("str"),
            "quantity": FieldSpec("int"),
            "origin": FieldSpec("str"),
            "destination": FieldSpec("str"),
            "agv": FieldSpec("str", nullable=True),
            "created_at": FieldSpec("float"),
            "dispatched_at": FieldSpec("float", nullable=True),
            "picked_at": FieldSpec("float", nullable=True),
            "delivered_at": FieldSpec("float", nullable=True),
            "fleet": FieldSpec("str", nullable=True),
        }
    )

    sku: SKU
    quantity: int
    origin: Warehouse
    destination: Warehouse
    created_at: float
    id: str = field(default=None, init=False)  # ty: ignore[invalid-assignment]  # set by attach
    due_date: float | None = None
    priority: float = 0.0
    status: OrderStatus = OrderStatus.PENDING

    # Lifecycle timestamps (set by FleetCoordinator)
    dispatched_at: float | None = None
    picked_at: float | None = None
    delivered_at: float | None = None
    assigned_agv: AGV | None = None

    # Id of the owning FleetCoordinator, set at attachment.
    fleet_id: str | None = field(default=None, init=False, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state; the SKU, warehouses, AGV and fleet are referenced by id."""
        agv = self.assigned_agv
        dispatched_at, picked_at, delivered_at = self.dispatched_at, self.picked_at, self.delivered_at
        return {
            "status": self.status.name,
            "sku": self.sku.id,
            "quantity": self.quantity,
            "origin": self.origin.id,
            "destination": self.destination.id,
            "agv": None if agv is None else agv.id,
            "created_at": wire_float(self.created_at),
            "dispatched_at": None if dispatched_at is None else wire_float(dispatched_at),
            "picked_at": None if picked_at is None else wire_float(picked_at),
            "delivered_at": None if delivered_at is None else wire_float(delivered_at),
            "fleet": self.fleet_id,
            "label": self.label,
        }
