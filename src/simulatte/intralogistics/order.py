from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

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


@dataclass
class TransferOrder:
    sku: SKU
    quantity: int
    origin: Warehouse
    destination: Warehouse
    created_at: float
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    due_date: float | None = None
    priority: float = 0.0
    status: OrderStatus = OrderStatus.PENDING

    # Lifecycle timestamps (set by FleetCoordinator)
    dispatched_at: float | None = None
    picked_at: float | None = None
    delivered_at: float | None = None
    assigned_agv: AGV | None = None
