"""Record the outputs of the pre-SP1 collectors into ``collector_parity.json`` (SP1 Task 21, spec §12.3).

Run once, at the commit that still has the old collector protocols (``EMAMetricsCollector``,
``DefaultTimeSeriesCollector``, ``CurrentWorkLoadCollector`` and ``Server(collect_time_series=True)``)::

    uv run python tests/fixtures/make_collector_parity.py

The systems are the LumsCor, SLAR and immediate-release builders with seeds 1-3, run until ``HORIZON``, plus the
yielding after-operation hook case of spec §12.3 (T12): one server, a 5-unit job at time 0 held 10 more time units
by the hook after it finishes at 5, and a 3-unit job arriving at 6. The old collectors are pure observers, so
they run side by side on one shop floor (the shop floor takes one time-series collector, hence the tee).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulatte.builders import build_immediate_release_system, build_lumscor_system, build_slar_system
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import CurrentWorkLoadCollector, DefaultTimeSeriesCollector, ShopFloor

OUT = Path(__file__).resolve().parent / "collector_parity.json"
HORIZON = 60.0
SEEDS = (1, 2, 3)
EMA_FIELDS = (
    "ema_makespan",
    "ema_tardy_jobs",
    "ema_early_jobs",
    "ema_in_window_jobs",
    "ema_time_in_psp",
    "ema_time_in_shopfloor",
    "ema_total_queue_time",
)


class _Tee:
    """Forwards the time-series callbacks to several collectors."""

    def __init__(self, *collectors: Any) -> None:
        self.collectors = collectors

    def on_job_entered(self, shopfloor: Any, job: Any) -> None:
        for c in self.collectors:
            c.on_job_entered(shopfloor, job)

    def on_operation_completed(self, shopfloor: Any, job: Any, server: Any, op_index: int) -> None:
        for c in self.collectors:
            c.on_operation_completed(shopfloor, job, server, op_index)

    def on_job_finished(self, shopfloor: Any, job: Any) -> None:
        for c in self.collectors:
            c.on_job_finished(shopfloor, job)


def _instrument(shop_floor: ShopFloor, servers: tuple[Server, ...] | list[Server]) -> tuple[Any, Any]:
    series, workload = DefaultTimeSeriesCollector(), CurrentWorkLoadCollector()
    shop_floor.set_time_series_collector(_Tee(series, workload))
    for server in servers:  # what Server(collect_time_series=True) sets up; nothing has run yet
        server._qt = []
        server._ut = [(0, 0.0)]
    return series, workload


def _pairs(ts: list[tuple[float, float]]) -> list[list[float]]:
    return [[t, v] for t, v in ts]


def _record(shop_floor: ShopFloor, servers: Any, series: Any, workload: Any) -> dict[str, Any]:
    metrics = shop_floor.metrics_collector
    return {
        "jobs_done": len(shop_floor.jobs_done),
        "ema": {name: getattr(metrics, name) for name in EMA_FIELDS},
        "wip_ts": _pairs(series.wip_ts),
        "job_count_ts": _pairs(series.job_count_ts),
        "throughput_ts": _pairs(series.throughput_ts),
        "lateness_ts": _pairs(series.lateness_ts),
        "workload_ts": _pairs(workload.wip_ts),
        "servers": {server.id: {"qt": _pairs(server._qt), "ut": _pairs(server._ut)} for server in servers},
    }


def _builder_system(kind: str, seed: int) -> dict[str, Any]:
    env = Environment(seed=seed)
    if kind == "lumscor":
        _, servers, shop_floor, _, _ = build_lumscor_system(
            env=env, check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
        )
    elif kind == "slar":
        _, servers, shop_floor, _, _ = build_slar_system(env=env, allowance_factor=3.0)
    else:
        _, servers, shop_floor, _, _ = build_immediate_release_system(env=env)
    series, workload = _instrument(shop_floor, servers)
    env.run(until=HORIZON)
    return _record(shop_floor, servers, series, workload)


def _hook_system() -> dict[str, Any]:
    env = Environment(seed=0)

    def hold(job: Any, server: Any, op_index: int, processing_time: float) -> Any:
        yield server.env.timeout(10)

    shop_floor = ShopFloor(env=env, on_after_operation=hold)
    server = Server(env=env, capacity=1, shopfloor=shop_floor)
    series, workload = _instrument(shop_floor, [server])

    def job(processing_time: float) -> ProductionJob:
        return ProductionJob(env=env, sku="A", servers=[server], processing_times=[processing_time], due_date=20)

    def arrivals() -> Any:
        shop_floor.add(job(5))
        yield env.timeout(6)
        shop_floor.add(job(3))

    env.process(arrivals())
    env.run()
    return _record(shop_floor, [server], series, workload)


def main() -> None:
    systems = {
        f"{kind}-{seed}": _builder_system(kind, seed) for kind in ("lumscor", "slar", "immediate") for seed in SEEDS
    }
    systems["yielding_hook"] = _hook_system()
    document = {"horizon": HORIZON, "systems": systems}
    OUT.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
