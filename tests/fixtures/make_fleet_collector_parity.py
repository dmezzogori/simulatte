"""Record the outputs of the pre-SP1 fleet collectors into ``fleet_collector_parity.json`` (SP1 Task 22, spec §12.3).

Run once, at the commit that still has ``EMAOrderMetrics`` and ``DefaultIntralogisticsCollector``::

    MPLBACKEND=Agg uv run python tests/fixtures/make_fleet_collector_parity.py

Systems:

- ``simple-<seed>`` (seeds 1-3): ``build_simple_system`` with three AGVs and a seeded order stream until ``HORIZON``:
  A-to-B orders with an occasional B-to-A order when B has the stock, random due dates, every sixth order
  cancelled a little later and every fourth mission interrupted (``ReturnToOrigin`` or a re-queue). Two orders are
  submitted before activation and one is submitted and cancelled before activation (ruling R19).
- ``intermediate`` and ``advanced``: the ``examples/intralogistics_*.py`` scripts, run as they are, with the
  collector classes replaced by recording subclasses.

Inventory snapshots are keyed by warehouse id and SKU id.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

import simulatte.intralogistics as intralogistics
import simulatte.intralogistics.fleet as fleet_module
from simulatte.environment import Environment
from simulatte.intralogistics import build_simple_system
from simulatte.intralogistics.metrics import DefaultIntralogisticsCollector, EMAOrderMetrics

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fleet_collector_parity.json"
HORIZON = 300.0
SEEDS = (1, 2, 3)
EMA_FIELDS = (
    "ema_fulfillment_time",
    "ema_dispatch_delay",
    "ema_travel_time_empty",
    "ema_travel_time_loaded",
    "ema_late_orders",
)


def _record(metrics: Any, series: Any) -> dict[str, Any]:
    return {
        "ema": {name: getattr(metrics, name) for name in EMA_FIELDS},
        "fleet_utilization_ts": [[t, v] for t, v in series.fleet_utilization_ts],
        "pending_orders_ts": [[t, v] for t, v in series.pending_orders_ts],
        "throughput_ts": [[t, v] for t, v in series.throughput_ts],
        "inventory_ts": {
            warehouse.id: [[t, {sku.id: level for sku, level in levels.items()}] for t, levels in snapshots]
            for warehouse, snapshots in series.inventory_ts.items()
        },
    }


def _simple_system(seed: int) -> dict[str, Any]:
    env = Environment(seed=seed)
    coordinator, _, wh_a, wh_b, _ = build_simple_system(env, n_agvs=3, agv_battery_capacity=170.0)
    series = DefaultIntralogisticsCollector()
    coordinator._time_series_collector = series  # what FleetCoordinator(time_series_collector=series) sets up
    skus = list(wh_a.inventory)

    def cancel_later(order: Any, delay: float) -> Any:
        yield env.timeout(delay)
        coordinator.cancel(order)

    def interrupt_later(order: Any, delay: float) -> Any:
        yield env.timeout(delay)
        process = coordinator._active_missions.get(order.id)
        if process is not None and process.is_alive:
            process.interrupt("breakdown")

    def stream() -> Any:
        rng = env.rng("orders")
        n = 0
        while True:
            yield env.timeout(rng.uniform(4.0, 20.0))
            n += 1
            sku, quantity = rng.choice(skus), rng.randint(1, 5)
            origin, destination = (wh_a, wh_b)
            if n % 5 == 0 and wh_b.get_inventory_level(sku) >= quantity:
                origin, destination = (wh_b, wh_a)
            order = coordinator.create_order(
                sku=sku,
                quantity=quantity,
                origin=origin,
                destination=destination,
                due_date=env.now + rng.uniform(20.0, 80.0),
            )
            coordinator.submit(order)
            if n % 6 == 0:
                env.process(cancel_later(order, rng.uniform(0.0, 10.0)))
            elif n % 4 == 1:
                env.process(interrupt_later(order, rng.uniform(2.0, 12.0)))

    for quantity in (2, 3):  # submitted before activation
        coordinator.submit(coordinator.create_order(sku=skus[0], quantity=quantity, origin=wh_a, destination=wh_b))
    early = coordinator.create_order(sku=skus[1], quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(early)
    coordinator.cancel(early)  # R19: dispatched at activation, then cancelled
    env.process(stream())
    env.run(until=HORIZON)
    return _record(coordinator._order_metrics_collector, series)


def _example(name: str) -> dict[str, Any]:
    metrics: list[EMAOrderMetrics] = []
    series: list[DefaultIntralogisticsCollector] = []

    class RecordingEMA(EMAOrderMetrics):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            metrics.append(self)

    class RecordingSeries(DefaultIntralogisticsCollector):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            series.append(self)

    patches = [
        (intralogistics, "EMAOrderMetrics", RecordingEMA),
        (fleet_module, "EMAOrderMetrics", RecordingEMA),  # the coordinator's default
        (intralogistics, "DefaultIntralogisticsCollector", RecordingSeries),
        (plt, "show", lambda *args, **kwargs: None),
    ]
    saved = [(target, attribute, getattr(target, attribute)) for target, attribute, _ in patches]
    for target, attribute, value in patches:
        setattr(target, attribute, value)
    try:
        runpy.run_path(str(ROOT / "examples" / f"intralogistics_{name}.py"), run_name="__main__")
    finally:
        for target, attribute, value in saved:
            setattr(target, attribute, value)
    assert len(metrics) == 1 and len(series) == 1, (len(metrics), len(series))
    return _record(metrics[0], series[0])


def main() -> None:
    systems = {f"simple-{seed}": _simple_system(seed) for seed in SEEDS}
    systems["intermediate"] = _example("intermediate")
    systems["advanced"] = _example("advanced")
    document = {"horizon": HORIZON, "systems": systems}
    OUT.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
