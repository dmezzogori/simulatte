from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.metrics import (
    DefaultIntralogisticsCollector,
    EMAOrderMetrics,
    IntralogisticsTimeSeriesCollector,
    OrderMetricsCollector,
)
from simulatte.intralogistics.order import TransferOrder
from simulatte.intralogistics.sku import SKU


def _make_coordinator_stub(**attrs: object) -> FleetCoordinator:
    return cast(FleetCoordinator, SimpleNamespace(**attrs))


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
    """EMAOrderMetrics.record(): First record initializes EMA directly. Second record smooths."""

    def test_first_and_second_record(self) -> None:
        m = EMAOrderMetrics(alpha=0.1)

        order1 = _make_order(
            created_at=0.0,
            dispatched_at=1.0,
            picked_at=3.0,
            delivered_at=5.0,
        )
        m.record(order1)

        # First record: EMA initialized directly to value (no bias)
        assert m.ema_fulfillment_time == 5.0
        assert m.ema_dispatch_delay == 1.0
        assert m.ema_travel_time_empty == 2.0
        assert m.ema_travel_time_loaded == 2.0
        # No due_date → on-time (0), ema_late_orders initialized to 0.0
        assert m.ema_late_orders == 0.0

        order2 = _make_order(
            created_at=10.0,
            dispatched_at=12.0,
            picked_at=15.0,
            delivered_at=20.0,
        )
        m.record(order2)

        # Second record: ema = ema + alpha * (value - ema)
        expected_fulfillment = 5.0 + 0.1 * (10.0 - 5.0)
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
        assert m.ema_late_orders == 0.0  # First observation: initialized to 0.0

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
        assert m.ema_fulfillment_time == 5.0  # First observation: initialized directly
        # dispatch_delay skipped — still None
        assert m.ema_dispatch_delay is None
        # travel_time_empty skipped (needs dispatched_at and picked_at) — still None
        assert m.ema_travel_time_empty is None
        # travel_time_loaded skipped (needs picked_at) — still None
        assert m.ema_travel_time_loaded is None


class TestDefaultCollectorOnOrderSubmitted:
    """DefaultIntralogisticsCollector.on_order_submitted appends to pending_orders_ts."""

    def test_appends_to_pending_orders_ts(self) -> None:
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=5.0)
        coordinator = _make_coordinator_stub(_pending_queue=[order])

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
        agv = cast(AGV, MagicMock())
        coordinator = _make_coordinator_stub()

        c.on_delivery_complete(coordinator, order, agv)

        # throughput_ts initialized with [(0.0, 0)], so after one delivery → [(0.0, 0), (10.0, 1)]
        assert len(c.throughput_ts) == 2
        assert c.throughput_ts[-1] == (10.0, 1)


class TestDefaultCollectorOnAGVStateChanged:
    """DefaultIntralogisticsCollector.on_agv_state_changed appends to fleet_utilization_ts."""

    def test_appends_to_fleet_utilization_ts(self) -> None:
        c = DefaultIntralogisticsCollector()

        agv1_mock = MagicMock()
        agv1_mock.utilization.return_value = 0.5
        agv1_mock.env.now = 10.0
        agv2_mock = MagicMock()
        agv2_mock.utilization.return_value = 0.3
        agv1 = cast(AGV, agv1_mock)
        agv2 = cast(AGV, agv2_mock)

        coordinator = _make_coordinator_stub(fleet=[agv1, agv2])

        c.on_agv_state_changed(coordinator, agv1, AGVState.IDLE, AGVState.TRAVELING_EMPTY)

        assert len(c.fleet_utilization_ts) == 1
        time, avg_util = c.fleet_utilization_ts[0]
        assert time == 10.0
        assert avg_util == (0.5 + 0.3) / 2


class TestCollectorReceivesStateTransitions:
    """S10: DefaultIntralogisticsCollector receives state transitions from FleetCoordinator."""

    def test_fleet_utilization_ts_populated_after_mission(self) -> None:
        """Attach a DefaultIntralogisticsCollector as time_series_collector.
        Run a mission. Verify fleet_utilization_ts is populated."""
        from simulatte.environment import Environment
        from simulatte.intralogistics.agv import AGV, AGVType
        from simulatte.intralogistics.fleet import FleetCoordinator
        from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
        from simulatte.intralogistics.sku import SKU
        from simulatte.intralogistics.speed import TrapezoidalProfile
        from simulatte.intralogistics.warehouse import Warehouse

        env = Environment()
        sku = SKU(id="SKU-A", weight=5.0, volume=0.1)
        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        arcs = [Arc(source=node_a, target=node_b)]
        graph = LayoutGraph([node_a, node_b], arcs)

        wh_a = Warehouse(
            env=env,
            name="WH-A",
            input_bays=[node_a],
            output_bays=[node_a],
            n_slots=2,
            products=[sku],
            initial_inventory={sku: 100},
            pick_time_fn=lambda s, q: 1.0,
            put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env,
            name="WH-B",
            input_bays=[node_b],
            output_bays=[node_b],
            n_slots=2,
            products=[sku],
            initial_inventory={sku: 0},
            pick_time_fn=lambda s, q: 1.0,
            put_time_fn=lambda s, q: 1.0,
        )

        agv_type = AGVType(
            name="test-type",
            speed_profile=speed,
            battery_capacity=1000.0,
            weight_capacity=100.0,
            volume_capacity=10.0,
            load_time_fn=lambda: 1.0,
            unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        collector = DefaultIntralogisticsCollector()
        coordinator = FleetCoordinator(
            env=env,
            graph=graph,
            fleet=[agv],
            warehouses=[wh_a, wh_b],
            charging_stations=[],
            time_series_collector=collector,
        )

        order = coordinator.create_order(sku=sku, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == "COMPLETED" or order.status.name == "COMPLETED"
        # fleet_utilization_ts should have been populated by on_agv_state_changed
        assert len(collector.fleet_utilization_ts) > 0
        # Each entry should be a (time, utilization) tuple
        for time, util in collector.fleet_utilization_ts:
            assert isinstance(time, (int, float))
            assert isinstance(util, (int, float))


class TestEMAOrderMetricsNoDelivery:
    """EMA record with no delivered_at: skips fulfillment and late checks."""

    def test_no_delivered_at_skips_fulfillment_and_late(self) -> None:
        m = EMAOrderMetrics(alpha=0.1)
        order = _make_order(
            created_at=0.0,
            dispatched_at=1.0,
            picked_at=3.0,
            delivered_at=None,
        )
        m.record(order)

        # fulfillment_time skipped (needs delivered_at) — still None
        assert m.ema_fulfillment_time is None
        # dispatch_delay still computed — first observation initialized directly
        assert m.ema_dispatch_delay == 1.0
        # travel_time_empty still computed — first observation initialized directly
        assert m.ema_travel_time_empty == 2.0
        # travel_time_loaded skipped (needs delivered_at) — still None
        assert m.ema_travel_time_loaded is None
        # late_orders skipped — still None
        assert m.ema_late_orders is None


class TestDefaultCollectorEdgeCases:
    """DefaultIntralogisticsCollector methods with missing timestamps."""

    def test_on_order_dispatched_no_dispatched_at(self) -> None:
        """dispatched_at is None -> skip (89->exit)."""
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=0.0, dispatched_at=None)
        agv = cast(AGV, MagicMock())
        coordinator = _make_coordinator_stub(_pending_queue=[])

        c.on_order_dispatched(coordinator, order, agv)

        # No entry added to pending_orders_ts
        assert len(c.pending_orders_ts) == 0

    def test_on_pickup_complete_no_picked_at(self) -> None:
        """picked_at is None -> skip (93->exit)."""
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=0.0, picked_at=None)
        agv = cast(AGV, MagicMock())
        coordinator = _make_coordinator_stub()

        c.on_pickup_complete(coordinator, order, agv)

        # No entry added to inventory_ts
        assert len(c.inventory_ts) == 0

    def test_on_delivery_complete_no_delivered_at(self) -> None:
        """delivered_at is None -> skip (98->exit)."""
        c = DefaultIntralogisticsCollector()
        order = _make_order(created_at=0.0, delivered_at=None)
        agv = cast(AGV, MagicMock())
        coordinator = _make_coordinator_stub()

        c.on_delivery_complete(coordinator, order, agv)

        # throughput_ts should still have just the initial entry
        assert len(c.throughput_ts) == 1


class TestEMAFirstObservation:
    """M9: EMA fields initialize from first observation, not from 0.0."""

    def test_first_record_initializes_ema(self) -> None:
        metrics = EMAOrderMetrics(alpha=0.1)
        order = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1,
            origin=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order.dispatched_at = 1.0
        order.picked_at = 3.0
        order.delivered_at = 10.0
        metrics.record(order)
        assert metrics.ema_fulfillment_time == pytest.approx(10.0)
        assert metrics.ema_dispatch_delay == pytest.approx(1.0)
        assert metrics.ema_travel_time_empty == pytest.approx(2.0)
        assert metrics.ema_travel_time_loaded == pytest.approx(7.0)

    def test_uninitialized_fields_are_none(self) -> None:
        metrics = EMAOrderMetrics()
        assert metrics.ema_fulfillment_time is None

    def test_per_field_initialization_partial_order(self) -> None:
        metrics = EMAOrderMetrics(alpha=0.1)
        order1 = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1,
            origin=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order1.dispatched_at = 1.0
        metrics.record(order1)
        assert metrics.ema_dispatch_delay == pytest.approx(1.0)
        assert metrics.ema_fulfillment_time is None  # not yet seen

        order2 = TransferOrder(
            sku=SKU(id="X", weight=1.0, volume=0.1),
            quantity=1,
            origin=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            created_at=0.0,
        )
        order2.dispatched_at = 2.0
        order2.picked_at = 4.0
        order2.delivered_at = 10.0
        metrics.record(order2)
        assert metrics.ema_fulfillment_time == pytest.approx(10.0)
        assert metrics.ema_dispatch_delay == pytest.approx(1.0 + 0.1 * (2.0 - 1.0))


class TestDefaultCollectorPlotInventory:
    """DefaultIntralogisticsCollector.plot_inventory renders without error."""

    def test_plot_inventory_with_data(self, monkeypatch) -> None:
        import matplotlib.pyplot

        step_calls: list = []
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)
        monkeypatch.setattr(matplotlib.pyplot, "step", lambda *a, **kw: step_calls.append(kw.get("label")))
        monkeypatch.setattr(matplotlib.pyplot, "xlabel", lambda *a: None)
        monkeypatch.setattr(matplotlib.pyplot, "ylabel", lambda *a: None)
        monkeypatch.setattr(matplotlib.pyplot, "title", lambda *a: None)
        monkeypatch.setattr(matplotlib.pyplot, "legend", lambda: None)

        c = DefaultIntralogisticsCollector()
        sku_a = SKU(id="A", weight=1.0, volume=0.1)
        sku_b = SKU(id="B", weight=2.0, volume=0.2)
        wh = MagicMock()
        wh.name = "WH-Test"
        c.inventory_ts[wh] = [
            (0.0, {sku_a: 100.0, sku_b: 50.0}),
            (10.0, {sku_a: 90.0, sku_b: 45.0}),
            (20.0, {sku_a: 80.0, sku_b: 40.0}),
        ]
        c.plot_inventory()

        assert len(step_calls) == 2
        assert "WH-Test / A" in step_calls
        assert "WH-Test / B" in step_calls

    def test_plot_inventory_nonempty_but_no_snapshots(self, monkeypatch) -> None:
        import matplotlib.pyplot

        show_calls: list = []
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: show_calls.append(1))

        c = DefaultIntralogisticsCollector()
        wh = MagicMock()
        wh.name = "WH-Empty"
        c.inventory_ts[wh] = []
        c.plot_inventory()

        assert len(show_calls) == 0

    def test_plot_inventory_empty_data(self, monkeypatch) -> None:
        import matplotlib.pyplot

        show_calls: list = []
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: show_calls.append(1))

        c = DefaultIntralogisticsCollector()
        c.plot_inventory()

        assert len(show_calls) == 0


class TestProtocolConformance:
    """Protocol conformance: isinstance checks for both built-ins."""

    def test_ema_order_metrics_is_order_metrics_collector(self) -> None:
        assert isinstance(EMAOrderMetrics(), OrderMetricsCollector)

    def test_default_collector_is_intralogistics_time_series_collector(self) -> None:
        assert isinstance(DefaultIntralogisticsCollector(), IntralogisticsTimeSeriesCollector)
