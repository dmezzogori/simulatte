"""Built-in fleet collectors on the event bus (spec §12.3, D52).

They replace the old ``OrderMetricsCollector`` and ``IntralogisticsTimeSeriesCollector`` protocols:

- :class:`OrderEMACollector`: exponential moving averages of completed orders (``ema_*``); every
  ``FleetCoordinator`` attaches one as ``fleet.metrics`` unless built with ``default_metrics=False``.
- :class:`FleetTimeSeries`: fleet utilization, pending orders, cumulative throughput and warehouse inventory over
  time (``fleet_utilization_ts``, ``pending_orders_ts``, ``throughput_ts``, ``inventory_ts``) with ``plot_*``
  helpers.
- :class:`FleetKPIs`: window-aware KPIs (spec §12.1-§12.2) of a fleet.

Each collector is bound to its fleet coordinator (its scope), takes only the events of that fleet's AGVs and
orders, and is attached with ``collector.attach(env)``; ``env.collectors`` lists the attached ones. Collectors are
observers (spec §13): they read simulation objects only through pure getters, never touch AGV state, never
schedule SimPy events and never draw random numbers, so attaching them leaves the run unchanged.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

from simulatte.intralogistics.agv import _UTILIZED_STATES
from simulatte.intralogistics.events import AgvStateChanged, FleetPendingChanged, OrderStatusChanged
from simulatte.kpi import KPI, Collector, TimeWeighted

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping

    from simulatte.events import Event
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.order import TransferOrder
    from simulatte.intralogistics.warehouse import Warehouse

__all__ = ["FleetKPIs", "FleetTimeSeries", "OrderEMACollector"]

_UTILIZED: frozenset[str] = frozenset(state.name for state in _UTILIZED_STATES)
"""Names of the AGV states that count as utilized (``AGV.utilization``)."""


def _own_order(collector: Collector, event: OrderStatusChanged) -> TransferOrder | None:
    """The order of `event` when it belongs to the collector's fleet (its ``fleet`` owner field), else None."""
    order: TransferOrder = collector.env.entities.get(event.order)  # ty: ignore[invalid-assignment]  # an order id
    return order if order.fleet_id == collector._scope_id else None


# ---------------------------------------------------------------------------------------------------------
# Exponential moving averages
# ---------------------------------------------------------------------------------------------------------


class OrderEMACollector(Collector):
    """Exponential moving averages (EMA) of the orders a fleet delivers, updated when an order is delivered.

    - ``ema_fulfillment_time``: time from order creation to delivery.
    - ``ema_dispatch_delay``: time from creation to the (last) dispatch.
    - ``ema_travel_time_empty``: time from the dispatch to the pickup.
    - ``ema_travel_time_loaded``: time from the pickup to the delivery.
    - ``ema_late_orders``: share of orders delivered after their due date (orders without one are on time).

    Each is None until the first delivery, which seeds it; then it moves by ``alpha * (value - ema)`` per order.
    The update runs at ``order.status_changed`` with reason ``delivered``. The averages are not window KPIs: they
    include orders delivered during the warm-up and are not part of
    :meth:`~simulatte.environment.Environment.fingerprint`; :class:`FleetKPIs` has the windowed results.

    Example:
        A fleet coordinator attaches one by default; for another `alpha`, attach your own::

            coordinator = FleetCoordinator(..., default_metrics=False)
            metrics = OrderEMACollector(coordinator, alpha=0.05).attach(env)
            env.run(until=1000)
            print(metrics.ema_fulfillment_time)
    """

    subscribes: ClassVar = (OrderStatusChanged,)

    def __init__(self, fleet: FleetCoordinator, alpha: float = 0.01) -> None:
        super().__init__(fleet)
        self.alpha = alpha
        """Smoothing factor in ``(0, 1]``."""
        self.ema_fulfillment_time: float | None = None
        self.ema_dispatch_delay: float | None = None
        self.ema_travel_time_empty: float | None = None
        self.ema_travel_time_loaded: float | None = None
        self.ema_late_orders: float | None = None

    def _update(self, current: float | None, value: float) -> float:
        return value if current is None else current + self.alpha * (value - current)

    def on_event(self, event: OrderStatusChanged) -> None:  # ty: ignore[invalid-method-override]  # subscribes to it only
        if event.reason != "delivered":
            return
        order = _own_order(self, event)
        if order is None:
            return
        created, dispatched, picked, delivered = (
            order.created_at,
            order.dispatched_at,
            order.picked_at,
            order.delivered_at,
        )
        # A delivery always follows a dispatch and a pickup.
        assert dispatched is not None and picked is not None and delivered is not None
        due = order.due_date
        update = self._update
        self.ema_fulfillment_time = update(self.ema_fulfillment_time, delivered - created)
        self.ema_dispatch_delay = update(self.ema_dispatch_delay, dispatched - created)
        self.ema_travel_time_empty = update(self.ema_travel_time_empty, picked - dispatched)
        self.ema_travel_time_loaded = update(self.ema_travel_time_loaded, delivered - picked)
        self.ema_late_orders = update(self.ema_late_orders, 1.0 if due is not None and delivered > due else 0.0)


# ---------------------------------------------------------------------------------------------------------
# Fleet time series
# ---------------------------------------------------------------------------------------------------------


class _AgvClock:
    """Time spent by one AGV in each state, accumulated from ``agv.state_changed`` events."""

    __slots__ = ("durations", "entered_at", "state")

    def __init__(self, durations: dict[str, float], state: str, entered_at: float) -> None:
        self.durations = durations
        self.state = state
        self.entered_at = entered_at

    def utilization(self, now: float) -> float:
        """Share of the AGV's time spent in utilized states up to `now`, as ``AGV.utilization`` computes it."""
        durations = dict(self.durations)
        durations[self.state] += now - self.entered_at
        total = math.fsum(durations.values())
        if total == 0:
            return 0.0
        return math.fsum(durations[state] for state in _UTILIZED) / total


class FleetTimeSeries(Collector):
    """Fleet utilization, pending orders, throughput and inventory of a fleet over time, as ``(time, value)`` lists.

    - ``fleet_utilization_ts``: at each ``agv.state_changed`` of a fleet AGV, the mean over the fleet's AGVs of the
      share of their lifetime spent traveling, loading or unloading (what ``AGV.utilization`` returns). The state
      durations are accumulated from the events, starting from the AGVs' accounting when the collector is created;
      the AGVs are not read afterwards.
    - ``pending_orders_ts``: the pending queue length when an order is submitted, at its creation time and before
      it joins the queue, and when an order is dispatched (an order dispatched from the queue is still counted).
    - ``throughput_ts``: delivered orders, starting with ``(0.0, 0)``, at each delivery.
    - ``inventory_ts``: per warehouse id, ``(time, {SKU id: level})`` snapshots of the origin at each pickup
      (``picked_at``, levels after the load) and of the destination at each delivery.

    Create and attach it before the run. Example::

        series = FleetTimeSeries(coordinator).attach(env)
        env.run(until=1000)
        series.plot_fleet_utilization()
    """

    subscribes: ClassVar = (AgvStateChanged, OrderStatusChanged, FleetPendingChanged)
    scope_field: ClassVar = "fleet"

    def __init__(self, fleet: FleetCoordinator) -> None:
        super().__init__(fleet)
        self._fleet = fleet
        self._clocks = {
            agv.id: _AgvClock(
                {state.name: duration for state, duration in agv.state_durations.items()},
                agv.state.name,
                agv._state_entered_at,
            )
            for agv in fleet.fleet
        }
        self._pending: set[str] = {order.id for order in fleet._pending_queue}
        self.fleet_utilization_ts: list[tuple[float, float]] = []
        self.pending_orders_ts: list[tuple[float, int]] = []
        self.throughput_ts: list[tuple[float, int]] = [(0.0, 0)]
        self.inventory_ts: dict[str, list[tuple[float, dict[str, float]]]] = {}

    def on_event(self, event: Event) -> None:
        if isinstance(event, AgvStateChanged):
            self._state_changed(event)
        elif isinstance(event, FleetPendingChanged):
            if event.op == "added":
                self._pending.add(event.order)
            else:
                self._pending.discard(event.order)
        else:
            assert isinstance(event, OrderStatusChanged)
            self._status_changed(event)

    def _state_changed(self, event: AgvStateChanged) -> None:
        clocks = self._clocks
        clock = clocks.get(event.agv)
        if clock is None:  # an AGV of another fleet
            return
        now = event.t
        clock.durations[event.previous] += now - clock.entered_at
        clock.state = event.state
        clock.entered_at = now
        mean = math.fsum(c.utilization(now) for c in clocks.values()) / len(clocks)
        self.fleet_utilization_ts.append((now, mean))

    def _status_changed(self, event: OrderStatusChanged) -> None:
        reason = event.reason
        if reason not in ("no_idle_agv", "dispatched", "picked", "delivered"):
            return
        order = _own_order(self, event)
        if order is None:
            return
        if reason == "picked":
            assert order.picked_at is not None
            self._snapshot(order.origin, order.picked_at)
        elif reason == "delivered":
            assert order.delivered_at is not None
            self.throughput_ts.append((order.delivered_at, self.throughput_ts[-1][1] + 1))
            self._snapshot(order.destination, order.delivered_at)
        else:
            pending = self._fleet.pending_count
            if reason == "no_idle_agv" or order.id not in self._pending:  # submitted now
                self.pending_orders_ts.append((order.created_at, pending))
            if reason == "dispatched":
                self.pending_orders_ts.append((event.t, pending))

    def _snapshot(self, warehouse: Warehouse, t: float) -> None:
        levels = {sku.id: float(container.level) for sku, container in warehouse.inventory.items()}
        self.inventory_ts.setdefault(warehouse.id, []).append((t, levels))

    def plot_fleet_utilization(self) -> None:  # pragma: no cover
        """Step plot of the fleet utilization over time (nothing without data)."""
        import matplotlib.pyplot as plt  # lazy: keeps headless runs free of matplotlib

        if not self.fleet_utilization_ts:
            return
        times, utils = zip(*self.fleet_utilization_ts, strict=True)
        plt.step(times, utils, where="post")
        plt.xlabel("Time")
        plt.ylabel("Fleet Utilization")
        plt.title("Fleet Utilization Over Time")
        plt.show()

    def plot_pending_orders(self) -> None:  # pragma: no cover
        """Step plot of the pending orders over time (nothing without data)."""
        import matplotlib.pyplot as plt

        if not self.pending_orders_ts:
            return
        times, depths = zip(*self.pending_orders_ts, strict=True)
        plt.step(times, depths, where="post")
        plt.xlabel("Time")
        plt.ylabel("Pending Orders")
        plt.title("Pending Orders Over Time")
        plt.show()

    def plot_throughput(self) -> None:  # pragma: no cover
        """Step plot of the cumulative delivered orders over time."""
        import matplotlib.pyplot as plt

        times, counts = zip(*self.throughput_ts, strict=True)
        plt.step(times, counts, where="post")
        plt.xlabel("Time")
        plt.ylabel("Cumulative Completed")
        plt.title("Throughput Over Time")
        plt.show()

    def plot_inventory(self) -> None:  # pragma: no cover
        """Step plot of each warehouse's inventory per SKU over time (nothing without data)."""
        import matplotlib.pyplot as plt

        has_series = False
        for warehouse, snapshots in self.inventory_ts.items():
            for sku in sorted({sku for _, levels in snapshots for sku in levels}):
                points = [(t, levels[sku]) for t, levels in snapshots if sku in levels]
                times, levels = zip(*points, strict=True)
                plt.step(times, levels, where="post", label=f"{warehouse} / {sku}")
                has_series = True
        if not has_series:
            return
        plt.xlabel("Time")
        plt.ylabel("Inventory Level")
        plt.title("Inventory Levels Over Time")
        plt.legend()
        plt.show()


# ---------------------------------------------------------------------------------------------------------
# Window-aware KPIs
# ---------------------------------------------------------------------------------------------------------


def _order_kpi(name: str, unit: str, description: str) -> KPI:
    return KPI(name, unit=unit, observation="order", cohort="completed_in_window", description=description)


def _time_weighted_kpi(name: str, unit: str, description: str) -> KPI:
    return KPI(
        name,
        unit=unit,
        observation="time_weighted",
        cohort="all",
        aggregation="time_weighted_mean",
        clip="window",
        description=description,
    )


class FleetKPIs(Collector):
    """Window-aware KPIs of a fleet (spec §12.1-§12.3), keyed ``"<fleet id>/<name>"``.

    Order KPIs are means over the orders delivered in the observation window (delivery at or after the warm-up);
    orders still open at the end, cancelled or failed are excluded:

    - ``fulfillment_time``: time from creation to delivery;
    - ``dispatch_delay``: time from creation to the (last) dispatch;
    - ``travel_time_empty``: time from the dispatch to the pickup;
    - ``travel_time_loaded``: time from the pickup to the delivery;
    - ``late_fraction``: share of orders delivered after their due date (orders without one are on time).

    ``throughput`` is the number of those orders divided by the window length. Time-weighted KPIs are means over
    the window of a signal that changes with the events, clipped at the window boundaries:

    - ``utilization``: AGVs traveling, loading or unloading (``agv.state_changed``) divided by the fleet size;
    - ``pending_orders``: orders in the pending queue (``fleet.pending_changed``).

    A KPI without observations, or over an empty window, is left out of :meth:`scalars`. Attach the collector
    before the run: the time-weighted signals start from the state at activation.
    """

    kpis: ClassVar = (
        _order_kpi("fulfillment_time", "time", "Mean time from order creation to delivery."),
        _order_kpi("dispatch_delay", "time", "Mean time from order creation to dispatch."),
        _order_kpi("travel_time_empty", "time", "Mean time from dispatch to pickup."),
        _order_kpi("travel_time_loaded", "time", "Mean time from pickup to delivery."),
        _order_kpi("late_fraction", "fraction", "Share of orders delivered after their due date."),
        KPI(
            "throughput",
            unit="orders/time",
            observation="order",
            aggregation="rate",
            description="Orders delivered in the window per unit of time.",
        ),
        _time_weighted_kpi("utilization", "fraction", "Mean share of the AGVs traveling, loading or unloading."),
        _time_weighted_kpi("pending_orders", "orders", "Mean number of orders in the pending queue."),
    )
    subscribes: ClassVar = (OrderStatusChanged, AgvStateChanged, FleetPendingChanged)
    scope_field: ClassVar = "fleet"

    def __init__(self, fleet: FleetCoordinator) -> None:
        super().__init__(fleet)
        self._fleet = fleet
        self._agvs = frozenset(agv.id for agv in fleet.fleet)
        self._completed = 0
        self._busy: TimeWeighted | None = None  # built at activation, with the warm-up
        self._pending: TimeWeighted | None = None

    def on_activate(self) -> None:
        start = self.window.start
        fleet = self._fleet
        self._busy = TimeWeighted(start, sum(agv.state.name in _UTILIZED for agv in fleet.fleet))
        self._pending = TimeWeighted(start, fleet.pending_count)

    def on_event(self, event: Event) -> None:
        if isinstance(event, OrderStatusChanged):
            self._status_changed(event)
            return
        busy, pending = self._busy, self._pending
        if busy is None or pending is None:  # a prelude event: activation reads the state
            return
        if isinstance(event, FleetPendingChanged):
            pending.update(event.t, pending.value + (1 if event.op == "added" else -1))
            return
        assert isinstance(event, AgvStateChanged)
        if event.agv not in self._agvs:
            return
        change = (event.state in _UTILIZED) - (event.previous in _UTILIZED)
        if change:
            busy.update(event.t, busy.value + change)

    def _status_changed(self, event: OrderStatusChanged) -> None:
        if event.reason != "delivered":
            return
        order = _own_order(self, event)
        if order is None:
            return
        created, dispatched, picked, delivered = (
            order.created_at,
            order.dispatched_at,
            order.picked_at,
            order.delivered_at,
        )
        assert dispatched is not None and picked is not None and delivered is not None
        due = order.due_date
        self.observe("fulfillment_time", delivered - created)
        self.observe("dispatch_delay", dispatched - created)
        self.observe("travel_time_empty", picked - dispatched)
        self.observe("travel_time_loaded", delivered - picked)
        self.observe("late_fraction", 1.0 if due is not None and delivered > due else 0.0)
        if event.t >= self.env.warmup:
            self._completed += 1

    def scalar_values(self) -> Mapping[str, float | None]:
        window = self.window
        length = window.length
        busy, pending = self._busy, self._pending
        size = len(self._agvs)
        mean_busy = None if busy is None else busy.mean(window.start, window.end)
        return {
            "throughput": self._completed / length if length > 0 else None,
            "utilization": mean_busy / size if mean_busy is not None and size else None,
            "pending_orders": None if pending is None else pending.mean(window.start, window.end),
        }
