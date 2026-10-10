"""Fleet collectors on the event bus (spec §12.1-§12.3, §13, D52)."""

from __future__ import annotations

from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics import (
    AGV,
    AGVState,
    FleetCoordinator,
    FleetKPIs,
    FleetTimeSeries,
    OrderEMACollector,
    TransferOrder,
    build_simple_system,
)
from simulatte.intralogistics.events import AgvStateChanged, OrderStatusChanged
from simulatte.typing import ProcessGenerator

EMA_FIELDS = (
    "ema_fulfillment_time",
    "ema_dispatch_delay",
    "ema_travel_time_empty",
    "ema_travel_time_loaded",
    "ema_late_orders",
)
KPI_NAMES = (
    "fulfillment_time",
    "dispatch_delay",
    "travel_time_empty",
    "travel_time_loaded",
    "late_fraction",
    "throughput",
    "utilization",
    "pending_orders",
)


def _two_orders(env: Environment, **options: Any) -> tuple[FleetCoordinator, list[TransferOrder]]:
    """One AGV and two orders submitted before activation: the second waits in the pending queue."""
    coordinator, _, wh_a, wh_b, _ = build_simple_system(env, n_agvs=1, **options)
    sku_a, sku_b = list(wh_a.inventory)
    orders = [
        coordinator.create_order(sku=sku_a, quantity=2, origin=wh_a, destination=wh_b),
        coordinator.create_order(sku=sku_b, quantity=3, origin=wh_a, destination=wh_b, due_date=1.0),
    ]
    for order in orders:
        coordinator.submit(order)
    return coordinator, orders


def _times(order: TransferOrder) -> tuple[float, float, float, float]:
    """``(created_at, dispatched_at, picked_at, delivered_at)`` of a completed order."""
    assert order.dispatched_at is not None and order.picked_at is not None and order.delivered_at is not None
    return order.created_at, order.dispatched_at, order.picked_at, order.delivered_at


def _stream(env: Environment, coordinator: FleetCoordinator, stream: str) -> ProcessGenerator:
    wh_a, wh_b = coordinator.warehouses
    skus = list(wh_a.inventory)
    rng = env.rng(stream)
    while True:
        yield env.timeout(rng.uniform(3.0, 12.0))
        order = coordinator.create_order(
            sku=rng.choice(skus),
            quantity=rng.randint(1, 4),
            origin=wh_a,
            destination=wh_b,
            due_date=env.now + rng.uniform(10.0, 60.0),
        )
        coordinator.submit(order)


# ---------------------------------------------------------------------------------------------------------
# Default EMA collector
# ---------------------------------------------------------------------------------------------------------


def test_default_metrics_opt_out() -> None:
    env = Environment()
    graph = build_simple_system(Environment())[4]
    fleet = FleetCoordinator(env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[], default_metrics=False)
    assert fleet.metrics is None
    assert env.collectors == ()
    assert not env.wants(OrderStatusChanged)  # nothing listens: the unobserved fast path stays


def test_default_order_ema_collector() -> None:
    env = Environment()
    coordinator, (first, second) = _two_orders(env)
    metrics = coordinator.metrics
    assert isinstance(metrics, OrderEMACollector)
    assert metrics.scope is coordinator and metrics.alpha == 0.01
    assert env.collectors == (metrics,)
    assert all(getattr(metrics, name) is None for name in EMA_FIELDS)  # no order completed yet
    env.run()

    c1, d1, p1, v1 = _times(first)
    c2, d2, p2, v2 = _times(second)
    assert d2 == v1  # the second order waited for the only AGV

    def ema(a: float, b: float) -> float:
        return a + 0.01 * (b - a)  # the first order seeds the average

    assert metrics.ema_fulfillment_time == ema(v1 - c1, v2 - c2)
    assert metrics.ema_dispatch_delay == ema(d1 - c1, d2 - c2)
    assert metrics.ema_travel_time_empty == ema(p1 - d1, p2 - d2)
    assert metrics.ema_travel_time_loaded == ema(v1 - p1, v2 - p2)
    assert metrics.ema_late_orders == ema(0.0, 1.0)  # the second is late, the first has no due date
    assert metrics.scalars() == {}  # the EMAs are attributes, not window KPIs


def test_order_ema_collector_attached_by_hand() -> None:
    env = Environment()
    coordinator, _, wh_a, wh_b, graph = build_simple_system(env, n_agvs=1)
    other = FleetCoordinator(
        env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[], default_metrics=False, name="other"
    )
    mine = OrderEMACollector(other, alpha=1.0).attach(env)
    sku = next(iter(wh_a.inventory))
    order = coordinator.create_order(sku=sku, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    env.run()

    assert order.delivered_at is not None
    assert all(getattr(mine, name) is None for name in EMA_FIELDS)  # another fleet's order
    metrics = OrderEMACollector(coordinator, alpha=1.0).attach(env)
    late = coordinator.create_order(sku=sku, quantity=1, origin=wh_a, destination=wh_b, due_date=env.now)
    coordinator.submit(late)
    env.run()
    created, dispatched, picked, delivered = _times(late)
    assert metrics.ema_fulfillment_time == delivered - created
    assert metrics.ema_dispatch_delay == dispatched - created
    assert metrics.ema_travel_time_empty == picked - dispatched
    assert metrics.ema_travel_time_loaded == delivered - picked
    assert metrics.ema_late_orders == 1.0


# ---------------------------------------------------------------------------------------------------------
# Fleet time series
# ---------------------------------------------------------------------------------------------------------


def test_fleet_time_series_two_orders() -> None:
    env = Environment()
    coordinator, (first, second) = _two_orders(env)
    series = FleetTimeSeries(coordinator).attach(env)
    wh_a, wh_b = coordinator.warehouses
    env.run()

    _, d1, p1, v1 = _times(first)
    _, d2, p2, v2 = _times(second)
    # Submissions at creation (pending count before the order joins), dispatches with the count at dispatch:
    # the second order is still in the queue when the pending check dispatches it.
    assert series.pending_orders_ts == [(0.0, 0), (d1, 0), (0.0, 0), (d2, 1)]
    assert series.throughput_ts == [(0.0, 0), (v1, 1), (v2, 2)]
    a, b = (sku.id for sku in wh_a.inventory)
    assert series.inventory_ts == {
        wh_a.id: [(p1, {a: 98.0, b: 100.0}), (p2, {a: 98.0, b: 97.0})],
        wh_b.id: [(v1, {a: 2.0, b: 0.0}), (v2, {a: 2.0, b: 3.0})],
    }
    # One AGV: busy from each dispatch to its delivery, idle at the end.
    times = [t for t, _ in series.fleet_utilization_ts]
    assert times == sorted(times) and times[0] == d1 and times[-1] == v2
    assert series.fleet_utilization_ts[0] == (0.0, 0.0)  # nothing elapsed yet
    assert series.fleet_utilization_ts[-1] == (v2, 1.0)  # busy throughout
    assert len(series.fleet_utilization_ts) == 10  # 4 states per mission, idle at each delivery


def test_fleet_utilization_from_events_without_touching_agv_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The utilization series is computed from ``agv.state_changed`` events (spec §12.3, §13)."""

    def run(patched: bool) -> list[tuple[float, float]]:
        env = Environment(seed=3)
        coordinator, *_ = build_simple_system(env, n_agvs=3, agv_battery_capacity=80.0)
        series = FleetTimeSeries(coordinator).attach(env)
        reference: list[tuple[float, float]] = []
        if patched:

            def untouchable(self: AGV, *args: object) -> None:
                raise AssertionError("the collector read the AGV's accounting")

            for method in ("utilization", "state_percentage", "time_allocation", "_durations_now"):
                monkeypatch.setattr(AGV, method, untouchable)
        else:  # the AGVs' own accounting, read at each state change
            env.bus.subscribe(lambda e: reference.append((e.t, coordinator.fleet_utilization)), (AgvStateChanged,))
        env.process(_stream(env, coordinator, "orders"))
        env.run(until=250)
        assert any(agv.state_durations[AGVState.CHARGING] > 0 for agv in coordinator.fleet)
        return series.fleet_utilization_ts if patched else reference

    reference = run(False)
    assert len(reference) > 50
    assert run(True) == reference  # exactly, value by value


def test_fleet_time_series_counts_direct_transitions() -> None:
    """An ``AGV.transition_to`` outside the coordinator also changes the series; other AGVs do not."""
    env = Environment()
    coordinator, agvs, *_ = build_simple_system(env, n_agvs=2)
    series = FleetTimeSeries(coordinator).attach(env)
    outsider = build_simple_system(env, n_agvs=1, prefix="x-")[1][0]
    env.run(until=4)
    agvs[0].transition_to(AGVState.TRAVELING_EMPTY)
    outsider.transition_to(AGVState.TRAVELING_EMPTY)
    env.run(until=10)
    agvs[0].transition_to(AGVState.IDLE)
    assert series.fleet_utilization_ts == [(4, 0.0), (10, 0.3)]


# ---------------------------------------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------------------------------------


def _scoped_system(env: Environment, prefix: str) -> dict[str, Any]:
    coordinator, *_ = build_simple_system(env, n_agvs=2, agv_battery_capacity=80.0, prefix=prefix)
    env.process(_stream(env, coordinator, f"{prefix}orders"))
    return {
        "fleet": coordinator,
        "series": FleetTimeSeries(coordinator).attach(env),
        "kpis": FleetKPIs(coordinator).attach(env),
    }


def _results(system: dict[str, Any]) -> dict[str, Any]:
    fleet, series = system["fleet"], system["series"]
    return {
        "ema": {name: getattr(fleet.metrics, name) for name in EMA_FIELDS},
        "series": [list(ts) for ts in (series.fleet_utilization_ts, series.pending_orders_ts, series.throughput_ts)],
        "inventory": {wh: list(snapshots) for wh, snapshots in series.inventory_ts.items()},
        "kpis": system["kpis"].scalars(),
        "collectors": sorted(type(c).__name__ for c in fleet.env.collectors if c.scope is fleet),
    }


def test_two_fleets_one_env_scoped() -> None:
    """Two prefixed systems in one environment give the results each gives alone (S18)."""
    alone = {}
    for prefix in ("a-", "b-"):
        env = Environment(seed=5)
        system = _scoped_system(env, prefix)
        env.run(until=300)
        alone[prefix] = _results(system)

    env = Environment(seed=5)
    systems = {prefix: _scoped_system(env, prefix) for prefix in ("a-", "b-")}
    env.run(until=300)
    for prefix, system in systems.items():
        together = _results(system)
        assert together == alone[prefix]
        assert len(together["series"][2]) > 5  # orders delivered
        assert together["collectors"] == ["FleetKPIs", "FleetTimeSeries", "OrderEMACollector"]
        assert set(together["kpis"]) == {f"{prefix}fleet/{name}" for name in KPI_NAMES}
    assert env.fingerprint().kpis == {**alone["a-"]["kpis"], **alone["b-"]["kpis"]}


# ---------------------------------------------------------------------------------------------------------
# Window-aware KPIs
# ---------------------------------------------------------------------------------------------------------


def test_fleet_kpis_in_the_window() -> None:
    env = Environment()
    env.configure_kpis(warmup=1.0)
    coordinator, (first, second) = _two_orders(env)
    kpis = FleetKPIs(coordinator).attach(env)
    env.run(until=200)

    c1, d1, p1, v1 = _times(first)
    c2, d2, p2, v2 = _times(second)
    assert 1.0 < v1 < v2 < 200
    length = 200 - 1.0
    key = f"{coordinator.id}/"
    scalars = kpis.scalars()
    assert scalars == {
        key + "fulfillment_time": ((v1 - c1) + (v2 - c2)) / 2,
        key + "dispatch_delay": ((d1 - c1) + (d2 - c2)) / 2,
        key + "travel_time_empty": ((p1 - d1) + (p2 - d2)) / 2,
        key + "travel_time_loaded": ((v1 - p1) + (v2 - p2)) / 2,
        key + "late_fraction": 0.5,
        key + "throughput": 2 / length,
        key + "utilization": pytest.approx((v2 - 1.0) / length, rel=1e-12),  # busy from 0 to v2
        key + "pending_orders": pytest.approx((d2 - 1.0) / length, rel=1e-12),  # the second waits until d2
    }
    assert env.fingerprint().kpis == scalars


def test_fleet_kpis_window_excludes_warmup_orders() -> None:
    env = Environment()
    coordinator, (first, second) = _two_orders(env)
    env.configure_kpis(warmup=60.0)
    kpis = FleetKPIs(coordinator).attach(env)
    env.run(until=150)

    _, _, _, v1 = _times(first)
    c2, d2, _, v2 = _times(second)
    assert v1 < 60.0 <= v2 < 150 and d2 < 60.0
    key = f"{coordinator.id}/"
    scalars = kpis.scalars()
    assert scalars[key + "fulfillment_time"] == v2 - c2
    assert scalars[key + "late_fraction"] == 1.0
    assert scalars[key + "throughput"] == 1 / 90
    assert scalars[key + "utilization"] == pytest.approx((v2 - 60.0) / 90, rel=1e-12)
    assert scalars[key + "pending_orders"] == 0.0


def test_fleet_kpis_without_observations() -> None:
    env = Environment()
    coordinator, *_ = build_simple_system(env, n_agvs=2)
    kpis = FleetKPIs(coordinator).attach(env)
    assert kpis.scalars() == {}  # nothing observed, empty window

    env.run(until=4)
    key = f"{coordinator.id}/"
    assert kpis.scalars() == {key + "throughput": 0.0, key + "utilization": 0.0, key + "pending_orders": 0.0}


def test_fleet_kpis_prelude_events() -> None:
    """State changes before activation reach the collector first; the accumulators start from the state then."""
    env = Environment()
    coordinator, agvs, *_ = build_simple_system(env, n_agvs=2)
    kpis = FleetKPIs(coordinator).attach(env)
    agvs[0].transition_to(AGVState.TRAVELING_EMPTY)  # a prelude event, before the accumulators exist
    env.run(until=10)
    assert kpis.scalars()[f"{coordinator.id}/utilization"] == 0.5  # one of two AGVs busy throughout


def test_fleet_kpis_need_agvs() -> None:
    env = Environment()
    graph = build_simple_system(env)[4]
    empty = FleetCoordinator(env=env, graph=graph, fleet=[], warehouses=[], charging_stations=[], name="empty")
    kpis = FleetKPIs(empty).attach(env)
    env.run(until=5)
    assert kpis.scalars() == {"empty/throughput": 0.0, "empty/pending_orders": 0.0}


# ---------------------------------------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------------------------------------


def test_collectors_are_pure_observers() -> None:
    """Debug mode rejects subscribers that schedule or draw; the trajectory does not depend on collectors."""

    def run(observed: bool) -> list[tuple[str, str, float | None]]:
        env = Environment(seed=9, debug=True)
        coordinator, _, wh_a, _, _ = build_simple_system(env, n_agvs=2, agv_battery_capacity=80.0)
        if observed:
            FleetTimeSeries(coordinator).attach(env)
            FleetKPIs(coordinator).attach(env)
        orders: list[TransferOrder] = []
        coordinator.on_order_submitted(orders.append)
        env.process(_stream(env, coordinator, "orders"))
        env.run(until=200)
        return [(o.id, o.status.name, o.delivered_at) for o in orders]

    assert run(True) == run(False)
