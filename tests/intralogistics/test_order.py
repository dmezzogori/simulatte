from __future__ import annotations

from unittest.mock import MagicMock

from simulatte.environment import Environment
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.sku import SKU


def _make_order(**overrides: object) -> TransferOrder:
    defaults: dict[str, object] = {
        "sku": SKU(id="STEEL", weight=10.0, volume=0.05),
        "quantity": 5,
        "origin": MagicMock(name="warehouse-origin"),
        "destination": MagicMock(name="warehouse-dest"),
        "created_at": 100.0,
    }
    defaults.update(overrides)
    return TransferOrder(**defaults)  # ty: ignore[invalid-argument-type]


class TestOrderStatus:
    def test_all_nine_values_exist(self) -> None:
        assert len(OrderStatus) == 9

    def test_values(self) -> None:
        expected = {
            "PENDING",
            "DISPATCHED",
            "PICKING",
            "IN_TRANSIT",
            "DELIVERING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "PENDING_ACTIVATION",
        }
        assert {s.name for s in OrderStatus} == expected


class TestTransferOrder:
    def test_creation_with_defaults(self) -> None:
        order = _make_order()
        assert order.id is None  # assigned at attachment
        assert order.status is OrderStatus.PENDING
        assert order.dispatched_at is None
        assert order.picked_at is None
        assert order.delivered_at is None
        assert order.assigned_agv is None

    def test_attachment_assigns_sequential_ids(self) -> None:
        env = Environment()
        order1 = _make_order()
        order2 = _make_order()
        env.entities.attach(order1)
        env.entities.attach(order2)
        assert (order1.id, order2.id) == ("order-0", "order-1")
        assert order1 != order2

    def test_status_can_be_updated(self) -> None:
        order = _make_order()
        order.status = OrderStatus.DISPATCHED
        assert order.status is OrderStatus.DISPATCHED

    def test_lifecycle_timestamps_default_to_none(self) -> None:
        order = _make_order()
        assert order.dispatched_at is None
        assert order.picked_at is None
        assert order.delivered_at is None

    def test_priority_defaults_to_zero(self) -> None:
        order = _make_order()
        assert order.priority == 0.0

    def test_repr(self) -> None:
        order = _make_order()
        Environment().entities.attach(order)
        r = repr(order)
        assert "TransferOrder" in r
        assert order.id in r

    def test_due_date_defaults_to_none(self) -> None:
        order = _make_order()
        assert order.due_date is None
