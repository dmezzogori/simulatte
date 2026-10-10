"""Parity of the bus collectors with the collectors they replace (spec §12.3, §17).

``tests/fixtures/collector_parity.json`` holds the outputs of the old ``EMAMetricsCollector``,
``DefaultTimeSeriesCollector``, ``CurrentWorkLoadCollector`` and ``Server(collect_time_series=True)`` series,
recorded at commit 85f0d8f by ``tests/fixtures/make_collector_parity.py`` (removed with the old collectors). The
same systems are rebuilt here with the new collectors.

Comparison policy (T12): times exactly, values with ``math.isclose(rel_tol=1e-12, abs_tol=1e-12)``. Two series
are compared in another way, by design:

- The workload series of the yielding after-operation hook system is the corrected one of spec §12.3, checked
  against the independently specified ``[(0, 5), (5, 0), (6, 3)]``: the old collector counted the finished
  operation as remaining work while the hook held the server.
- The queue length series ``qt`` records the length after the changes at each time; the old one sampled the
  queue at each request and again when the request was processed, so it may hold several points per time. The
  new series equals the last old point at each time.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from simulatte.builders import build_immediate_release_system, build_lumscor_system, build_slar_system
from simulatte.collectors import (
    CurrentWorkloadCollector,
    EMACollector,
    ServerTimeSeries,
    ShopFloorTimeSeries,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.typing import ProcessGenerator

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "collector_parity.json"
DOCUMENT = json.loads(FIXTURE.read_text(encoding="utf-8"))
HORIZON = DOCUMENT["horizon"]
EMA_FIELDS = (
    "ema_makespan",
    "ema_tardy_jobs",
    "ema_early_jobs",
    "ema_in_window_jobs",
    "ema_time_in_psp",
    "ema_time_in_shopfloor",
    "ema_total_queue_time",
)
BUILDER_SYSTEMS = sorted(name for name in DOCUMENT["systems"] if name != "yielding_hook")


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def _assert_series(new: Sequence[tuple[float, float]], old: Sequence[Sequence[float]], what: str) -> None:
    assert len(new) == len(old), f"{what}: {len(new)} points, expected {len(old)}"
    for i, ((t, v), (t_old, v_old)) in enumerate(zip(new, old, strict=True)):
        assert t == t_old, f"{what}[{i}]: time {t!r}, expected {t_old!r}"
        assert _close(v, v_old), f"{what}[{i}] at {t}: {v!r}, expected {v_old!r}"


def _last_per_time(series: Sequence[Sequence[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for t, v in series:
        if out and out[-1][0] == t:
            out[-1] = [t, v]
        else:
            out.append([t, v])
    return out


def _collect(shop_floor: ShopFloor, servers: Sequence[Server]) -> dict[str, Any]:
    env = shop_floor.env
    found = [c for c in env.collectors if isinstance(c, CurrentWorkloadCollector)]
    workload = found[0] if found else CurrentWorkloadCollector(shop_floor).attach(env)
    series = ShopFloorTimeSeries(shop_floor).attach(env)
    attached = {c.scope.id: c for c in env.collectors if isinstance(c, ServerTimeSeries)}
    per_server = {s.id: attached.get(s.id) or ServerTimeSeries(s).attach(env) for s in servers}
    return {"workload": workload, "series": series, "servers": per_server}


def _assert_parity(name: str, shop_floor: ShopFloor, collected: dict[str, Any], *, workload: bool = True) -> None:
    old = DOCUMENT["systems"][name]
    assert len(shop_floor.jobs_done) == old["jobs_done"]
    metrics = shop_floor.metrics
    assert isinstance(metrics, EMACollector)
    for field in EMA_FIELDS:
        assert _close(getattr(metrics, field), old["ema"][field]), field
    series = collected["series"]
    for attribute in ("wip_ts", "job_count_ts", "throughput_ts", "lateness_ts"):
        _assert_series(getattr(series, attribute), old[attribute], f"{name} {attribute}")
    if workload:
        _assert_series(collected["workload"].wip_ts, old["workload_ts"], f"{name} workload")
    assert set(collected["servers"]) == set(old["servers"])
    for server_id, server_series in collected["servers"].items():
        expected = old["servers"][server_id]
        _assert_series(server_series.ut, expected["ut"], f"{name} {server_id} ut")
        _assert_series(server_series.qt, _last_per_time(expected["qt"]), f"{name} {server_id} qt")


_BUILDERS: dict[str, Callable[[Environment], Any]] = {
    "lumscor": lambda env: build_lumscor_system(
        env=env, check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2, collect_workload=True
    ),
    "slar": lambda env: build_slar_system(env=env, allowance_factor=3.0, collect_workload=True),
    "immediate": lambda env: build_immediate_release_system(env=env, collect_workload=True, collect_time_series=True),
}


@pytest.mark.parametrize("name", BUILDER_SYSTEMS)
def test_parity_with_old_collectors(name: str) -> None:
    kind, seed = name.rsplit("-", 1)
    env = Environment(seed=int(seed))
    _, servers, shop_floor, _, _ = _BUILDERS[kind](env)
    collected = _collect(shop_floor, servers)
    env.run(until=HORIZON)
    _assert_parity(name, shop_floor, collected)


def test_yielding_hook_workload_is_corrected() -> None:
    """Five units finish at 5 and a hook holds the server until 15; a 3-unit job arrives at 6 (spec §12.3, T12)."""
    env = Environment(seed=0)

    def hold(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> ProcessGenerator:
        yield server.env.timeout(10)

    shop_floor = ShopFloor(env=env, on_after_operation=hold)
    server = Server(env=env, capacity=1, shopfloor=shop_floor)
    collected = _collect(shop_floor, [server])

    def job(processing_time: float) -> ProductionJob:
        return ProductionJob(env=env, sku="A", servers=[server], processing_times=[processing_time], due_date=20)

    def arrivals() -> ProcessGenerator:
        shop_floor.add(job(5))
        yield env.timeout(6)
        shop_floor.add(job(3))

    env.process(arrivals())
    env.run()

    workload = collected["workload"].wip_ts
    assert workload[:3] == [(0, 5), (5, 0), (6, 3)]  # spec §12.3; the old collector gave (6, 8)
    assert workload == [(0, 5), (5, 0), (6, 3), (18, 0)]  # the 3-unit job runs 15-18
    assert DOCUMENT["systems"]["yielding_hook"]["workload_ts"] == [[0, 5], [5, 0], [6, 8], [18, 0]]
    _assert_parity("yielding_hook", shop_floor, collected, workload=False)
