"""Parity of the fleet bus collectors with the collectors they replace (spec §12.3, §17).

``tests/fixtures/fleet_collector_parity.json`` holds the outputs of the old ``EMAOrderMetrics`` and
``DefaultIntralogisticsCollector``, recorded at commit 0c55c8d by ``tests/fixtures/make_fleet_collector_parity.py``
(removed with the old collectors). The same systems are rebuilt here with :class:`OrderEMACollector` and
:class:`FleetTimeSeries`; the examples run as they are, with :class:`FleetTimeSeries` replaced by a recording
subclass. Inventory snapshots were recorded keyed by warehouse id and SKU id, as the new series keys them.

The historical example series and EMAs are compared exactly. The seeded simple systems include
breakdowns and cancellations: their corrected outputs live in ``fleet_recovery_regression.json`` because
#47/#49/#50/#51 intentionally change mission recovery. The original parity fixture remains unchanged.

The event mapping that reproduces the old callbacks:

- ``fleet_utilization_ts``: one point per ``agv.state_changed`` of a fleet AGV (the old callback ran after every
  state change the coordinator made, which are all the state changes in these systems), the mean utilization of
  the fleet's AGVs computed from the state durations the events accumulate.
- ``pending_orders_ts``: at submission, ``(created_at, pending count)``, where the submission is the
  ``order.status_changed`` with reason ``no_idle_agv``, or ``dispatched`` for an order outside the pending queue;
  at each dispatch, ``(dispatched_at, pending count)``.
- ``throughput_ts`` and the destination inventory: at ``order.status_changed`` with reason ``delivered``. The old
  callback ran after the delivery hooks, which none of these systems has.
- the origin inventory: at ``order.status_changed`` with reason ``picked``, at ``picked_at``.
- the EMAs: at ``order.status_changed`` with reason ``delivered``.
"""

from __future__ import annotations

import json
import runpy
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

import simulatte.intralogistics as intralogistics
from simulatte.environment import Environment
from simulatte.intralogistics import FleetTimeSeries, OrderEMACollector, build_simple_system
from simulatte.intralogistics.fleet import FleetCoordinator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "fleet_collector_parity.json"
DOCUMENT = json.loads(FIXTURE.read_text(encoding="utf-8"))
RECOVERY = json.loads((FIXTURE.parent / "fleet_recovery_regression.json").read_text(encoding="utf-8"))
HORIZON = DOCUMENT["horizon"]
EMA_FIELDS = (
    "ema_fulfillment_time",
    "ema_dispatch_delay",
    "ema_travel_time_empty",
    "ema_travel_time_loaded",
    "ema_late_orders",
)


def _record(metrics: OrderEMACollector, series: FleetTimeSeries) -> dict[str, Any]:
    return {
        "ema": {name: getattr(metrics, name) for name in EMA_FIELDS},
        "fleet_utilization_ts": [[t, v] for t, v in series.fleet_utilization_ts],
        "pending_orders_ts": [[t, v] for t, v in series.pending_orders_ts],
        "throughput_ts": [[t, v] for t, v in series.throughput_ts],
        "inventory_ts": {
            warehouse: [[t, dict(levels)] for t, levels in snapshots]
            for warehouse, snapshots in series.inventory_ts.items()
        },
    }


def _assert_parity(name: str, recorded: dict[str, Any], expected: dict[str, Any] = DOCUMENT) -> None:
    old = expected["systems"][name]
    assert recorded["ema"] == old["ema"]
    for attribute in ("fleet_utilization_ts", "pending_orders_ts", "throughput_ts", "inventory_ts"):
        assert recorded[attribute] == old[attribute], f"{name} {attribute}"


def _simple_system(seed: int) -> dict[str, Any]:
    """The generator's ``simple-<seed>`` system, built with the new collectors."""
    env = Environment(seed=seed)
    coordinator, _, wh_a, wh_b, _ = build_simple_system(env, n_agvs=3, agv_battery_capacity=170.0)
    series = FleetTimeSeries(coordinator).attach(env)
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
    assert isinstance(coordinator.metrics, OrderEMACollector)
    return _record(coordinator.metrics, series)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recovery_regression_simple_system(seed: int) -> None:
    _assert_parity(f"simple-{seed}", _simple_system(seed), RECOVERY)


@pytest.mark.parametrize("name", ["intermediate", "advanced"])
def test_parity_examples(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    plotting = find_spec("matplotlib") is not None
    if plotting:
        import matplotlib.pyplot

    attached: list[FleetTimeSeries] = []

    class RecordingSeries(FleetTimeSeries):
        def __init__(self, fleet: FleetCoordinator) -> None:
            super().__init__(fleet)
            attached.append(self)

    if plotting:
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda *args, **kwargs: None)
    monkeypatch.setattr(intralogistics, "FleetTimeSeries", RecordingSeries)
    example_globals = runpy.run_path(
        str(ROOT / "examples" / f"intralogistics_{name}.py"),
        run_name="__main__" if plotting else "__headless__",
    )
    if not plotting:
        example_globals["main"](plot=False)

    [series] = attached
    [metrics] = [c for c in series.env.collectors if isinstance(c, OrderEMACollector)]
    assert metrics.scope is series.scope
    _assert_parity(name, _record(metrics, series))
