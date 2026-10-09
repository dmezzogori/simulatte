"""Production collectors on the event bus (spec §12.1-§12.3, D52)."""

from __future__ import annotations

from typing import Any, ClassVar

from simulatte.builders import build_immediate_release_system
from simulatte.collectors import (
    CurrentWorkloadCollector,
    EMACollector,
    ServerTimeSeries,
    ShopFloorKPIs,
    ShopFloorTimeSeries,
)
from simulatte.environment import Environment
from simulatte.events import Event
from simulatte.kpi import Collector
from simulatte.job import ProductionJob
from simulatte.scenario import Scenario
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, JobFinished, OperationCompleted, ShopFloor, ShopFloorEntered
from simulatte.typing import ProcessGenerator


EMA_FIELDS = (
    "ema_makespan",
    "ema_tardy_jobs",
    "ema_early_jobs",
    "ema_in_window_jobs",
    "ema_time_in_psp",
    "ema_time_in_shopfloor",
    "ema_total_queue_time",
)


def _job(
    env: Environment, servers: list[Server], times: list[float], due: float = 20.0, sku: str = "A"
) -> ProductionJob:
    return ProductionJob(env=env, sku=sku, servers=servers, processing_times=times, due_date=due)


# ---------------------------------------------------------------------------------------------------------
# Default EMA collector
# ---------------------------------------------------------------------------------------------------------


def test_default_metrics_opt_out() -> None:
    env = Environment()
    sf = ShopFloor(env=env, default_metrics=False)
    server = Server(env=env, capacity=1, shopfloor=sf)
    sf.add(_job(env, [server], [5.0]))
    env.run()

    assert sf.metrics is None
    assert env.collectors == ()
    assert not env.wants(JobFinished)  # nothing listens: the unobserved fast path stays
    assert len(sf.jobs_done) == 1


def test_default_ema_collector() -> None:
    env = Environment()
    sf = ShopFloor(env=env, ema_alpha=0.5)
    server = Server(env=env, capacity=1, shopfloor=sf)
    metrics = sf.metrics
    assert isinstance(metrics, EMACollector)
    assert env.collectors == (metrics,)
    assert metrics.scope is sf and metrics.alpha == 0.5
    assert metrics.scalars() == {}  # the EMAs are attributes, not window KPIs

    # Finishes at 5, due 3: lateness 2, inside the 7-unit due-date window.
    sf.add(_job(env, [server], [5.0], due=3.0))
    # Queued 5, finishes at 9, due 30: early (lateness -21).
    sf.add(_job(env, [server], [4.0], due=30.0))
    env.run()

    assert metrics.ema_makespan == 0.5 * (0.5 * 5.0) + 0.5 * 9.0
    assert metrics.ema_in_window_jobs == 0.25
    assert metrics.ema_early_jobs == 0.5
    assert metrics.ema_tardy_jobs == 0.0
    assert metrics.ema_time_in_psp == 0.0
    assert metrics.ema_time_in_shopfloor == 0.5 * (0.5 * 5.0) + 0.5 * 9.0
    assert metrics.ema_total_queue_time == 0.5 * 5.0


def test_ema_collector_attached_by_hand() -> None:
    env = Environment()
    sf = ShopFloor(env=env, default_metrics=False)
    server = Server(env=env, capacity=1, shopfloor=sf)
    metrics = EMACollector(sf, alpha=1.0).attach(env)
    sf.add(_job(env, [server], [2.0], due=1.0))
    sf.add(_job(env, [server], [2.0], due=100.0))
    env.run()
    assert (metrics.ema_makespan, metrics.ema_tardy_jobs, metrics.ema_early_jobs) == (4.0, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------------------------------------


def _scoped_system(env: Environment, prefix: str) -> dict[str, Any]:
    _, servers, sf, _, _ = build_immediate_release_system(
        env=env, scenario=Scenario(n_servers=3), prefix=prefix, collect_workload=True, collect_time_series=True
    )
    return {
        "sf": sf,
        "servers": servers,
        "series": ShopFloorTimeSeries(sf).attach(env),
        "kpis": ShopFloorKPIs(sf).attach(env),
    }


def _results(env: Environment, system: dict[str, Any]) -> dict[str, Any]:
    sf = system["sf"]
    ids = {sf.id, *(s.id for s in system["servers"])}
    mine = [c for c in env.collectors if c.scope.id in ids]
    workload = next(c for c in mine if isinstance(c, CurrentWorkloadCollector))
    # Copies: dropping an environment closes its waiting processes, whose cancelled requests still reach the
    # collectors (job.queue_left) after the run.
    servers = {c.scope.id: (list(c.qt), list(c.ut)) for c in mine if isinstance(c, ServerTimeSeries)}
    series = system["series"]
    return {
        "ema": {name: getattr(sf.metrics, name) for name in EMA_FIELDS},
        "series": [list(ts) for ts in (series.wip_ts, series.job_count_ts, series.throughput_ts, series.lateness_ts)],
        "workload": list(workload.wip_ts),
        "servers": servers,
        "kpis": system["kpis"].scalars(),
        "collectors": sorted(type(c).__name__ for c in mine),
    }


def test_two_shopfloors_one_env_scoped() -> None:
    """Two prefixed systems in one environment give the results each gives alone (S18)."""
    alone = {}
    for prefix in ("a-", "b-"):
        env = Environment(seed=5)
        system = _scoped_system(env, prefix)
        env.run(until=150)
        alone[prefix] = _results(env, system)

    env = Environment(seed=5)
    systems = {prefix: _scoped_system(env, prefix) for prefix in ("a-", "b-")}
    env.run(until=150)
    for prefix, system in systems.items():
        together = _results(env, system)
        assert together == alone[prefix]
        assert len(together["series"][2]) > 10  # jobs finished
        assert together["collectors"] == [
            "CurrentWorkloadCollector",
            "EMACollector",
            "ServerTimeSeries",
            "ServerTimeSeries",
            "ServerTimeSeries",
            "ShopFloorKPIs",
            "ShopFloorTimeSeries",
        ]
        assert set(together["kpis"]) == {f"{prefix}shopfloor/{name}" for name in _KPI_NAMES}
    assert env.fingerprint().kpis == {**alone["a-"]["kpis"], **alone["b-"]["kpis"]}


# ---------------------------------------------------------------------------------------------------------
# Shop-floor time series
# ---------------------------------------------------------------------------------------------------------


def test_shopfloor_time_series_two_jobs() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    series = ShopFloorTimeSeries(sf).attach(env)
    sf.add(_job(env, [server], [5.0], due=10.0))  # finishes at 5: lateness -5
    sf.add(_job(env, [server], [3.0], due=6.0))  # finishes at 8: lateness 2
    env.run()

    assert series.wip_ts == [(0, 5.0), (0, 8.0), (5, 3.0), (8, 0.0)]
    assert series.job_count_ts == [(0, 1), (0, 2), (5, 1), (8, 0)]
    assert series.throughput_ts == [(0.0, 0), (5, 1), (8, 2)]
    assert series.lateness_ts == [(5, -5.0), (8, 2.0)]
    assert series.scalars() == {}


def test_shopfloor_time_series_multi_server_wip() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1, s2 = Server(env=env, capacity=1, shopfloor=sf), Server(env=env, capacity=1, shopfloor=sf)
    series = ShopFloorTimeSeries(sf).attach(env)
    sf.add(_job(env, [s1, s2], [3.0, 4.0]))
    env.run()
    assert series.wip_ts == [(0, 7.0), (3, 4.0), (7, 0.0)]


def test_custom_collector_replaces_the_time_series_protocol() -> None:
    """The old ``TimeSeriesCollector`` hooks map to ``shopfloor.entered``, ``operation.completed`` and
    ``job.finished``."""

    class Lifecycle(Collector):
        subscribes: ClassVar = (ShopFloorEntered, OperationCompleted, JobFinished)
        scope_field: ClassVar = "shopfloor"

        def __init__(self, shopfloor: ShopFloor) -> None:
            super().__init__(shopfloor)
            self.seen: list[tuple[str, float]] = []

        def on_event(self, event: Event) -> None:
            self.seen.append((event.type_name, event.t))

    env = Environment()
    sf = ShopFloor(env=env)
    s1, s2 = Server(env=env, capacity=1, shopfloor=sf), Server(env=env, capacity=1, shopfloor=sf)
    lifecycle = Lifecycle(sf).attach(env)
    sf.add(_job(env, [s1, s2], [2.0, 3.0]))
    env.run()
    assert lifecycle.seen == [
        ("shopfloor.entered", 0),
        ("operation.completed", 2),
        ("operation.completed", 5),
        ("job.finished", 5),
    ]


# ---------------------------------------------------------------------------------------------------------
# Remaining work
# ---------------------------------------------------------------------------------------------------------


def test_workload_decreases_at_operation_completion() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1, s2 = Server(env=env, capacity=1, shopfloor=sf), Server(env=env, capacity=1, shopfloor=sf)
    workload = CurrentWorkloadCollector(sf).attach(env)
    sf.add(_job(env, [s1, s2], [6.0, 4.0]))
    assert workload.wip_ts == [(0, 10.0)]  # the whole routing, before the run
    env.run()
    assert workload.wip_ts == [(0, 10.0), (6, 4.0), (10, 0.0)]  # nothing added when the job finishes


def test_workload_sums_jobs_and_ignores_the_wip_strategy() -> None:
    def run(strategy: CorrectedWIPStrategy | None) -> list[tuple[float, float]]:
        env = Environment()
        sf = ShopFloor(env=env, wip_strategy=strategy)
        server = Server(env=env, capacity=1, shopfloor=sf)
        other = Server(env=env, capacity=1, shopfloor=sf)
        workload = CurrentWorkloadCollector(sf).attach(env)
        sf.add(_job(env, [server, other], [5.0, 1.0]))
        sf.add(_job(env, [server], [3.0]))
        env.run()
        return workload.wip_ts

    expected = [(0, 6.0), (0, 9.0), (5, 4.0), (6, 3.0), (8, 0.0)]
    assert run(None) == expected
    assert run(CorrectedWIPStrategy()) == expected


def test_workload_hook_holding_the_server() -> None:
    """Work done stays done while an after-operation hook holds the server (spec §12.3, S19)."""
    env = Environment()

    def hold(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> ProcessGenerator:
        yield server.env.timeout(10)

    sf = ShopFloor(env=env, on_after_operation=hold)
    server = Server(env=env, capacity=1, shopfloor=sf)
    workload = CurrentWorkloadCollector(sf).attach(env)

    def arrivals() -> ProcessGenerator:
        sf.add(_job(env, [server], [5.0]))
        yield env.timeout(6)
        sf.add(_job(env, [server], [3.0]))

    env.process(arrivals())
    env.run(until=7)
    assert workload.wip_ts == [(0, 5.0), (5, 0.0), (6, 3.0)]


# ---------------------------------------------------------------------------------------------------------
# Server series
# ---------------------------------------------------------------------------------------------------------


def test_server_time_series_queue_and_utilization() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    ts = ServerTimeSeries(server).attach(env)
    assert (ts.qt, ts.ut) == ([], [(0, 0.0)])

    sf.add(_job(env, [server], [4.0]))  # granted at once: never counted as waiting
    sf.add(_job(env, [server], [2.0]))  # waits 0-4

    def late() -> ProcessGenerator:
        yield env.timeout(1)
        sf.add(_job(env, [server], [1.0]))  # waits 1-6

    env.process(late())
    env.run()

    assert ts.qt == [(0, 1), (1, 2), (4, 1), (6, 0)]
    assert ts.ut == [(0, 0.0), (0, 1.0), (4, 0.0), (4, 1.0), (6, 0.0), (6, 1.0), (7, 0.0)]


def test_server_time_series_records_a_cancelled_request() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    ts = ServerTimeSeries(server).attach(env)

    def holder() -> ProcessGenerator:
        with server.request(job=_job(env, [server], [1.0])) as request:
            yield request
            yield env.timeout(5)

    def impatient() -> ProcessGenerator:
        with server.request(job=_job(env, [server], [1.0])) as request:
            yield request | env.timeout(2)  # leaves the queue at 2 without being granted

    env.process(holder())
    env.process(impatient())
    env.run()

    assert ts.qt == [(0, 1), (2, 0)]
    assert ts.ut == [(0, 0.0), (0, 1.0), (5, 0.0)]


def test_server_time_series_scoped_to_its_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1, s2 = Server(env=env, capacity=2, shopfloor=sf), Server(env=env, capacity=1, shopfloor=sf)
    ts = ServerTimeSeries(s1).attach(env)
    sf.add(_job(env, [s2, s1], [1.0, 3.0]))
    env.run()
    assert ts.qt == [(1, 0)]
    assert ts.ut == [(0, 0.0), (1, 0.5), (4, 0.0)]


# ---------------------------------------------------------------------------------------------------------
# Window-aware KPIs
# ---------------------------------------------------------------------------------------------------------

_KPI_NAMES = (
    "makespan",
    "lateness",
    "tardiness",
    "tardy_fraction",
    "total_queue_time",
    "throughput",
    "utilization",
    "jobs_in_system",
)


def test_shopfloor_kpis_in_the_window() -> None:
    env = Environment()
    env.configure_kpis(warmup=2)
    sf = ShopFloor(env=env)
    s1, s2 = Server(env=env, capacity=1, shopfloor=sf), Server(env=env, capacity=1, shopfloor=sf)
    kpis = ShopFloorKPIs(sf).attach(env)
    sf.add(_job(env, [s1], [3.0], due=2.0))  # 0-3: lateness 1
    sf.add(_job(env, [s1], [4.0], due=10.0))  # queued 0-3, 3-7: lateness -3
    sf.add(_job(env, [s2], [1.0], due=0.0))  # 0-1, finished before the warm-up: not observed
    env.run(until=10)

    key = f"{sf.id}/"
    assert kpis.scalars() == {
        key + "makespan": 5.0,
        key + "lateness": -1.0,
        key + "tardiness": 0.5,
        key + "tardy_fraction": 0.5,
        key + "total_queue_time": 1.5,
        key + "throughput": 2 / 8,  # completions in [2, 10)
        key + "utilization": 5 / 16,  # s1 busy 5 of 8, s2 idle in the window
        key + "jobs_in_system": 6 / 8,  # 2 on [2, 3), 1 on [3, 7)
    }
    assert env.fingerprint().kpis == kpis.scalars()


def test_shopfloor_kpis_without_observations() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)
    kpis = ShopFloorKPIs(sf).attach(env)
    assert kpis.scalars() == {}  # nothing observed, empty window

    env.run(until=4)
    key = f"{sf.id}/"
    assert kpis.scalars() == {key + "throughput": 0.0, key + "utilization": 0.0, key + "jobs_in_system": 0.0}


# ---------------------------------------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------------------------------------


def test_collectors_are_pure_observers() -> None:
    """Debug mode rejects subscribers that schedule or draw; the trajectory does not depend on collectors."""

    def run(observed: bool) -> list[tuple[str, float | None]]:
        env = Environment(seed=9, debug=True)
        _, servers, sf, _, _ = build_immediate_release_system(
            env=env, scenario=Scenario(n_servers=3), collect_workload=observed, collect_time_series=observed
        )
        if observed:
            ShopFloorTimeSeries(sf).attach(env)
            ShopFloorKPIs(sf).attach(env)
        env.run(until=100)
        return [(job.sku, job.finished_at) for job in sf.jobs_done]

    assert run(True) == run(False)
