from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.metrics import (
    DefaultIntralogisticsCollector,
    EMAOrderMetrics,
    IntralogisticsTimeSeriesCollector,
    OrderMetricsCollector,
)
from simulatte.intralogistics.order import TransferOrder


def _make_order(
    *,
    created_at: float = 0.0,
    dispatched_at: float | None = None,
    picked_at: float | None = None,
    delivered_at: float | None = None,
    due_date: float | None = None,
) -> TransferOrder:
    origin = MagicMock()
    origin.name = "WH-A"
    destination = MagicMock()
    destination.name = "WH-B"
    sku = MagicMock()
    sku.id = "STEEL"

    order = TransferOrder(
        sku=sku,
        quantity=1,
        origin=origin,
        destination=destination,
        created_at=created_at,
        due_date=due_date,
    )
    order.dispatched_at = dispatched_at
    order.picked_at = picked_at
    order.delivered_at = delivered_at
    return order


class TestEMAOrderMetricsRecord:
    """EMAOrderMetrics.record(): First record sets EMA to alpha * value. Second record smooths further."""

    def test_first_and_second_record(self) -> None:
        m = EMAOrderMetrics(alpha=0.1)

        order1 = _make_order(
            created_at=0.0,
            dispatched_at=1.0,
            picked_at=3.0,
            delivered_at=5.0,
        )
        m.record(order1)

        # First record: ema = 0.0 + 0.1 * (value - 0.0) = 0.1 * value
        assert m.ema_fulfillment_time == 0.1 * 5.0  # 0.5
        assert m.ema_dispatch_delay == 0.1 * 1.0  # 0.1
        assert m.ema_travel_time_empty == 0.1 * 2.0  # 0.2
        assert m.ema_travel_time_loaded == 0.1 * 2.0  # 0.2
        # No due_date → on-time (0), ema_late_orders = 0.1 * 0 = 0.0
        assert m.ema_late_orders == 0.0

        order2 = _make_order(
            created_at=10.0,
            dispatched_at=12.0,
            picked_at=15.0,
            delivered_at=20.0,
        )
        m.record(order2)

        # Second record: ema = ema + alpha * (value - ema)
        expected_fulfillment = 0.5 + 0.1 * (10.0 - 0.5)
        assert m.ema_fulfillment_time == expected_fulfillment


class TestEMAOrderMetricsLate:
    """EMA with late order: order with due_date < delivered_at → ema_late_orders increases."""

    def test_late_order_increases_ema(self) -> None:
        m = EMAOrderMetrics(alpha=0.1)

        # On-time order first
        order_on_time = _make_order(
            created_at=0.0,
            dispatched_at=1.0,
            picked_at=2.0,
            delivered_at=3.0,
            due_date=10.0,
        )
        m.record(order_on_time)
        assert m.ema_late_orders == 0.0  # 0.1 * 0 = 0.0

        # Late order
        order_late = _make_order(
            created_at=10.0,
            dispatched_at=11.0,
            picked_at=12.0,
            delivered_at=20.0,
            due_date=15.0,
        )
        m.record(order_late)
        # ema = 0.0 + 0.1 * (1.0 - 0.0) = 0.1
        assert m.ema_late_orders == 0.1


class TestEMAOrderMetricsIncomplete:
    """EMA with incomplete timestamps: order missing dispatched_at → skips that EMA update."""

    def test_skips_dispatch_delay_when_missing(self) -> None:
        m = EMAOrderMetrics(alpha=0.1)

        order = _make_order(
            created_at=0.0,
            dispatched_at=None,  # missing
            picked_at=None,
            delivered_at=5.0,
        )
        m.record(order)

        # fulfillment_time still computed (created_at and delivered_at present)
        assert m.ema_fulfillment_time == 0.1 * 5.0
        # dispatch_delay skipped
        assert m.ema_dispatch_delay == 0.0
        # travel_time_empty skipped (needs dispatched_at and picked_at)
        assert m.ema_travel_time_empty == 0.0
        # travel_time_loaded skipped (needs picked_at)
        assert m.ema_travel_time_loaded == 0.0


class TestDefaultCollectorOnOrderSubmitted:
    """DefaultIntralogisticsCollector.on_order_submitted appends to pending_orders_ts."""

    def test_appends_to_pending_orders_ts(self) -> None:
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=5.0)
        coordinator = SimpleNamespace(_pending_queue=[order])

        c.on_order_submitted(coordinator, order)

        assert len(c.pending_orders_ts) == 1
        time, depth = c.pending_orders_ts[0]
        assert time == 5.0
        assert depth == 1


class TestDefaultCollectorOnDeliveryComplete:
    """DefaultIntralogisticsCollector.on_delivery_complete appends to throughput_ts."""

    def test_appends_to_throughput_ts(self) -> None:
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=0.0, delivered_at=10.0)
        agv = MagicMock()
        coordinator = SimpleNamespace()

        c.on_delivery_complete(coordinator, order, agv)

        # throughput_ts initialized with [(0.0, 0)], so after one delivery → [(0.0, 0), (10.0, 1)]
        assert len(c.throughput_ts) == 2
        assert c.throughput_ts[-1] == (10.0, 1)


class TestDefaultCollectorOnAGVStateChanged:
    """DefaultIntralogisticsCollector.on_agv_state_changed appends to fleet_utilization_ts."""

    def test_appends_to_fleet_utilization_ts(self) -> None:
        c = DefaultIntralogisticsCollector()

        agv1 = MagicMock()
        agv1.utilization.return_value = 0.5
        agv1.env.now = 10.0
        agv2 = MagicMock()
        agv2.utilization.return_value = 0.3

        coordinator = SimpleNamespace(fleet=[agv1, agv2])

        c.on_agv_state_changed(coordinator, agv1, AGVState.IDLE, AGVState.TRAVELING_EMPTY)

        assert len(c.fleet_utilization_ts) == 1
        time, avg_util = c.fleet_utilization_ts[0]
        assert time == 10.0
        assert avg_util == (0.5 + 0.3) / 2


class TestProtocolConformance:
    """Protocol conformance: isinstance checks for both built-ins."""

    def test_ema_order_metrics_is_order_metrics_collector(self) -> None:
        assert isinstance(EMAOrderMetrics(), OrderMetricsCollector)

    def test_default_collector_is_intralogistics_time_series_collector(self) -> None:
        assert isinstance(DefaultIntralogisticsCollector(), IntralogisticsTimeSeriesCollector)
