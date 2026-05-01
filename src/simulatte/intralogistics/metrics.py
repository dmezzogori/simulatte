from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from simulatte.intralogistics.agv import AGV, AGVState
from simulatte.intralogistics.order import TransferOrder
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.warehouse import Warehouse

if TYPE_CHECKING:
    from simulatte.intralogistics.fleet import FleetCoordinator  # type: ignore[import-not-found]  # module created in Task 14


# ---------------------------------------------------------------------------
# OrderMetricsCollector protocol + EMA implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class OrderMetricsCollector(Protocol):
    def record(self, order: TransferOrder) -> None: ...


@dataclass
class EMAOrderMetrics:
    """Tracks exponential moving averages of order lifecycle metrics."""

    alpha: float = 0.01

    ema_fulfillment_time: float | None = field(default=None, init=False)
    ema_dispatch_delay: float | None = field(default=None, init=False)
    ema_travel_time_empty: float | None = field(default=None, init=False)
    ema_travel_time_loaded: float | None = field(default=None, init=False)
    ema_late_orders: float | None = field(default=None, init=False)

    def _ema_update(self, current: float | None, value: float) -> float:
        if current is None:
            return value
        return current + self.alpha * (value - current)

    def record(self, order: TransferOrder) -> None:
        # Fulfillment time: created_at → delivered_at
        if order.delivered_at is not None:
            value = order.delivered_at - order.created_at
            self.ema_fulfillment_time = self._ema_update(self.ema_fulfillment_time, value)

        # Dispatch delay: created_at → dispatched_at
        if order.dispatched_at is not None:
            value = order.dispatched_at - order.created_at
            self.ema_dispatch_delay = self._ema_update(self.ema_dispatch_delay, value)

        # Travel time empty: dispatched_at → picked_at
        if order.dispatched_at is not None and order.picked_at is not None:
            value = order.picked_at - order.dispatched_at
            self.ema_travel_time_empty = self._ema_update(self.ema_travel_time_empty, value)

        # Travel time loaded: picked_at → delivered_at
        if order.picked_at is not None and order.delivered_at is not None:
            value = order.delivered_at - order.picked_at
            self.ema_travel_time_loaded = self._ema_update(self.ema_travel_time_loaded, value)

        # Late orders: 1 if late, 0 if on time or no due date
        if order.delivered_at is not None:
            late = 1.0 if order.due_date is not None and order.delivered_at > order.due_date else 0.0
            self.ema_late_orders = self._ema_update(self.ema_late_orders, late)


# ---------------------------------------------------------------------------
# IntralogisticsTimeSeriesCollector protocol + Default implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class IntralogisticsTimeSeriesCollector(Protocol):
    def on_order_submitted(self, coordinator: FleetCoordinator, order: TransferOrder) -> None: ...
    def on_order_dispatched(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None: ...
    def on_pickup_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None: ...
    def on_delivery_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None: ...
    def on_agv_state_changed(self, coordinator: FleetCoordinator, agv: AGV, old: AGVState, new: AGVState) -> None: ...


@dataclass
class DefaultIntralogisticsCollector:
    """Records time-series data for intralogistics metrics."""

    fleet_utilization_ts: list[tuple[float, float]] = field(default_factory=list)
    pending_orders_ts: list[tuple[float, int]] = field(default_factory=list)
    throughput_ts: list[tuple[float, int]] = field(default_factory=lambda: [(0.0, 0)])
    inventory_ts: dict[Warehouse, list[tuple[float, dict[SKU, float]]]] = field(default_factory=dict)

    def on_order_submitted(self, coordinator: FleetCoordinator, order: TransferOrder) -> None:
        self.pending_orders_ts.append((order.created_at, len(coordinator._pending_queue)))

    def on_order_dispatched(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None:
        if order.dispatched_at is not None:
            self.pending_orders_ts.append((order.dispatched_at, len(coordinator._pending_queue)))

    def on_pickup_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None:
        if order.picked_at is not None:
            inv_snapshot = {sku: float(container.level) for sku, container in order.origin.inventory.items()}
            self.inventory_ts.setdefault(order.origin, []).append((order.picked_at, inv_snapshot))

    def on_delivery_complete(self, coordinator: FleetCoordinator, order: TransferOrder, agv: AGV) -> None:
        if order.delivered_at is not None:
            _, prev_count = self.throughput_ts[-1]
            self.throughput_ts.append((order.delivered_at, prev_count + 1))

            inv_snapshot = {sku: float(container.level) for sku, container in order.destination.inventory.items()}
            self.inventory_ts.setdefault(order.destination, []).append((order.delivered_at, inv_snapshot))

    def on_agv_state_changed(self, coordinator: FleetCoordinator, agv: AGV, old: AGVState, new: AGVState) -> None:
        avg_util = sum(a.utilization() for a in coordinator.fleet) / len(coordinator.fleet)
        self.fleet_utilization_ts.append((agv.env.now, avg_util))

    def plot_fleet_utilization(self) -> None:  # pragma: no cover
        import matplotlib.pyplot as plt

        if not self.fleet_utilization_ts:
            return
        times, utils = zip(*self.fleet_utilization_ts)
        plt.step(times, utils, where="post")
        plt.xlabel("Time")
        plt.ylabel("Fleet Utilization")
        plt.title("Fleet Utilization Over Time")
        plt.show()

    def plot_pending_orders(self) -> None:  # pragma: no cover
        import matplotlib.pyplot as plt

        if not self.pending_orders_ts:
            return
        times, depths = zip(*self.pending_orders_ts)
        plt.step(times, depths, where="post")
        plt.xlabel("Time")
        plt.ylabel("Pending Orders")
        plt.title("Pending Orders Over Time")
        plt.show()

    def plot_throughput(self) -> None:  # pragma: no cover
        import matplotlib.pyplot as plt

        if not self.throughput_ts:
            return
        times, counts = zip(*self.throughput_ts)
        plt.step(times, counts, where="post")
        plt.xlabel("Time")
        plt.ylabel("Cumulative Completed")
        plt.title("Throughput Over Time")
        plt.show()

    def plot_inventory(self) -> None:  # pragma: no cover
        import matplotlib.pyplot as plt

        if not any(self.inventory_ts.values()):
            return
        for warehouse, snapshots in self.inventory_ts.items():
            if not snapshots:
                continue
            all_skus = {sku for _, inv in snapshots for sku in inv}
            for sku in sorted(all_skus, key=lambda s: s.id):
                times = [t for t, inv in snapshots if sku in inv]
                levels = [inv[sku] for t, inv in snapshots if sku in inv]
                plt.step(times, levels, where="post", label=f"{warehouse.name} / {sku.id}")
        plt.xlabel("Time")
        plt.ylabel("Inventory Level")
        plt.title("Inventory Levels Over Time")
        plt.legend()
        plt.show()
