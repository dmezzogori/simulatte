"""Built-in production collectors on the event bus (spec §12.3, D52).

They replace the old collector protocols of ``ShopFloor`` and ``Server``:

- :class:`EMACollector`: exponential moving averages of job results (``ema_*``); every ``ShopFloor`` attaches one
  as ``shopfloor.metrics`` unless built with ``default_metrics=False``.
- :class:`ShopFloorTimeSeries`: WIP, job count, cumulative throughput and lateness over time (``wip_ts``,
  ``job_count_ts``, ``throughput_ts``, ``lateness_ts``) with ``plot_*`` helpers.
- :class:`CurrentWorkloadCollector`: remaining processing work over time (``wip_ts``).
- :class:`ServerTimeSeries`: queue length and utilization of one server (``qt``, ``ut``) with ``plot_qt`` and
  ``plot_ut``.
- :class:`ShopFloorKPIs`: window-aware KPIs (spec §12.1-§12.2) of a shop floor.

Each collector is bound to its owner (the shop floor or the server, its scope), takes only the events of that
owner, and is attached with ``collector.attach(env)``; ``env.collectors`` lists the attached ones. Collectors are
observers (spec §13): they read simulation objects only through pure getters, never schedule SimPy events and
never draw random numbers, so attaching them leaves the run unchanged.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

from simulatte.kpi import KPI, Collector, TimeWeighted, _ExactSum
from simulatte.server import JobGranted, JobQueued, JobQueueLeft, JobReleased
from simulatte.shopfloor import JobFinished, OperationCompleted, ShopFloorEntered, ShopFloorWipUpdated

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping

    from simulatte.events import Event
    from simulatte.job import ProductionJob
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor

__all__ = [
    "CurrentWorkloadCollector",
    "EMACollector",
    "ServerTimeSeries",
    "ShopFloorKPIs",
    "ShopFloorTimeSeries",
]


# ---------------------------------------------------------------------------------------------------------
# Exponential moving averages
# ---------------------------------------------------------------------------------------------------------


class EMACollector(Collector):
    """Exponential moving averages (EMA) of the jobs a shop floor completes, updated at each ``job.finished``.

    - ``ema_makespan``: time from job creation to completion.
    - ``ema_tardy_jobs``: share of jobs finishing late, outside the due-date window.
    - ``ema_early_jobs``: share of jobs finishing early, outside the due-date window.
    - ``ema_in_window_jobs``: share of jobs finishing within the due-date window (±7 time units).
    - ``ema_time_in_psp``: time spent in the pre-shop pool.
    - ``ema_time_in_shopfloor``: time spent on the shop floor.
    - ``ema_total_queue_time``: total time spent waiting in queues.

    Each starts at 0 and moves by ``alpha * (value - ema)`` per job, so a smaller `alpha` weighs history more.
    The averages are not window KPIs: they include jobs finished during the warm-up and are not part of
    :meth:`~simulatte.environment.Environment.fingerprint`; :class:`ShopFloorKPIs` has the windowed results.

    Example:
        A shop floor attaches one by default::

            shop_floor = ShopFloor(env=env, ema_alpha=0.05)
            env.run(until=1000)
            print(shop_floor.metrics.ema_makespan)
    """

    subscribes: ClassVar = (JobFinished,)
    # No scope_field: on_event checks the shop floor itself, which spares the default collector of every shop
    # floor one call per job.

    def __init__(self, shopfloor: ShopFloor, alpha: float = 0.01) -> None:
        super().__init__(shopfloor)
        self._entities = shopfloor.env.entities
        self.alpha = alpha
        """Smoothing factor in ``(0, 1]``."""
        self.ema_makespan: float = 0.0
        self.ema_tardy_jobs: float = 0.0
        self.ema_early_jobs: float = 0.0
        self.ema_in_window_jobs: float = 0.0
        self.ema_time_in_psp: float = 0.0
        self.ema_time_in_shopfloor: float = 0.0
        self.ema_total_queue_time: float = 0.0

    def on_event(self, event: JobFinished) -> None:  # ty: ignore[invalid-method-override]  # subscribes to it only
        if event.shopfloor != self._scope_id:
            return
        job: ProductionJob = self._entities.get(event.job)  # ty: ignore[invalid-assignment]  # a job id
        alpha = self.alpha
        lateness = event.lateness
        in_window = job.is_finished_in_due_date_window()

        self.ema_makespan += alpha * (event.makespan - self.ema_makespan)
        tardy = 1 if not in_window and lateness > 0 else 0
        self.ema_tardy_jobs += alpha * (tardy - self.ema_tardy_jobs)
        early = 1 if not in_window and lateness < 0 else 0
        self.ema_early_jobs += alpha * (early - self.ema_early_jobs)
        self.ema_in_window_jobs += alpha * ((1 if in_window else 0) - self.ema_in_window_jobs)
        self.ema_time_in_psp += alpha * (job.time_in_psp - self.ema_time_in_psp)
        self.ema_time_in_shopfloor += alpha * (job.time_in_shopfloor - self.ema_time_in_shopfloor)
        self.ema_total_queue_time += alpha * (event.total_queue_time - self.ema_total_queue_time)


# ---------------------------------------------------------------------------------------------------------
# Shop-floor time series
# ---------------------------------------------------------------------------------------------------------


class ShopFloorTimeSeries(Collector):
    """WIP, job count, throughput and lateness of a shop floor over time, as ``(time, value)`` lists.

    - ``wip_ts``: total WIP (the sum of ``shopfloor.wip``, as counted by its WIP strategy) when a job enters
      and after each operation.
    - ``job_count_ts``: jobs on the shop floor when a job enters or finishes.
    - ``throughput_ts``: completed jobs, starting with ``(0.0, 0)``, at each completion.
    - ``lateness_ts``: lateness of each job at its completion.

    Example:
        ::

            series = ShopFloorTimeSeries(shop_floor).attach(env)
            env.run(until=1000)
            series.plot_wip()
    """

    subscribes: ClassVar = (ShopFloorEntered, ShopFloorWipUpdated, JobFinished)
    scope_field: ClassVar = "shopfloor"

    def __init__(self, shopfloor: ShopFloor) -> None:
        super().__init__(shopfloor)
        self._shopfloor = shopfloor
        self.wip_ts: list[tuple[float, float]] = []
        self.job_count_ts: list[tuple[float, int]] = []
        self.throughput_ts: list[tuple[float, int]] = [(0.0, 0)]
        self.lateness_ts: list[tuple[float, float]] = []

    def on_event(self, event: Event) -> None:
        shopfloor = self._shopfloor
        now = event.t
        if isinstance(event, ShopFloorWipUpdated):
            self.wip_ts.append((now, math.fsum(shopfloor.wip.values())))
        elif isinstance(event, ShopFloorEntered):
            self.wip_ts.append((now, math.fsum(shopfloor.wip.values())))
            self.job_count_ts.append((now, len(shopfloor.jobs)))
        else:
            assert isinstance(event, JobFinished)
            self.job_count_ts.append((now, len(shopfloor.jobs)))
            self.throughput_ts.append((now, len(shopfloor.jobs_done)))
            self.lateness_ts.append((now, event.lateness))

    def plot_wip(self) -> None:  # pragma: no cover
        """Step plot of the total WIP over time (`RuntimeError` without data)."""
        import matplotlib.pyplot as plt  # lazy: keeps headless runs free of matplotlib

        if not self.wip_ts:
            raise RuntimeError("No WIP data collected.")
        x, y = zip(*self.wip_ts, strict=True)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=0.3)
        plt.title("Total WIP over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("WIP (total processing time)")
        plt.show()

    def plot_job_count(self) -> None:  # pragma: no cover
        """Step plot of the jobs on the shop floor over time (`RuntimeError` without data)."""
        import matplotlib.pyplot as plt

        if not self.job_count_ts:
            raise RuntimeError("No job count data collected.")
        x, y = zip(*self.job_count_ts, strict=True)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=0.3)
        plt.title("Jobs in system over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Job Count")
        plt.show()

    def plot_throughput(self) -> None:  # pragma: no cover
        """Step plot of the cumulative throughput over time (`RuntimeError` before the first completion)."""
        import matplotlib.pyplot as plt

        if len(self.throughput_ts) <= 1:
            raise RuntimeError("No throughput data collected.")
        x, y = zip(*self.throughput_ts, strict=True)
        plt.step(x, y, where="post")
        plt.title("Cumulative throughput over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Completed Jobs")
        plt.show()

    def plot_lateness(self) -> None:  # pragma: no cover
        """Scatter plot of job lateness at completion, tardy jobs red and early ones green (`RuntimeError` without
        data)."""
        import matplotlib.pyplot as plt

        if not self.lateness_ts:
            raise RuntimeError("No lateness data collected.")
        x, y = zip(*self.lateness_ts, strict=True)
        colors = ["red" if lateness > 0 else "green" for lateness in y]
        plt.scatter(x, y, c=colors, alpha=0.6)
        plt.axhline(y=0, color="black", linestyle="--", linewidth=0.5)
        plt.title("Job lateness over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Lateness (positive = tardy)")
        plt.show()


# ---------------------------------------------------------------------------------------------------------
# Remaining work
# ---------------------------------------------------------------------------------------------------------


class CurrentWorkloadCollector(Collector):
    """Remaining processing work on a shop floor over time (``wip_ts``, ``(time, work)`` pairs).

    The work is the sum of the processing times of the operations not yet completed by the jobs on the shop floor:
    queued, in progress or not yet reached. A job adds its whole routing when it enters (a point is recorded) and
    each ``operation.completed`` subtracts that operation's processing time (a point is recorded). Unlike
    :class:`ShopFloorTimeSeries` ``wip_ts``, the values do not depend on the WIP strategy.

    The work drops when the operation completes, not when the server is released: an after-operation hook that
    holds the server does not keep completed work in the total (spec §12.3). The sums are exactly rounded.
    """

    subscribes: ClassVar = (ShopFloorEntered, OperationCompleted, JobFinished)
    scope_field: ClassVar = "shopfloor"

    def __init__(self, shopfloor: ShopFloor) -> None:
        super().__init__(shopfloor)
        self.wip_ts: list[tuple[float, float]] = []
        self._work = _ExactSum()
        self._jobs: set[str] = set()  # ids of this shop floor's unfinished jobs

    def on_event(self, event: Event) -> None:
        work = self._work
        if isinstance(event, OperationCompleted):
            if event.job not in self._jobs:  # an operation of another shop floor's job
                return
            work.add(-event.processing_time)
        elif isinstance(event, ShopFloorEntered):
            self._jobs.add(event.job)
            job: ProductionJob = self.env.entities.get(event.job)  # ty: ignore[invalid-assignment]  # a job id
            for processing_time in job.processing_times:
                work.add(processing_time)
        else:
            assert isinstance(event, JobFinished)
            self._jobs.discard(event.job)
            return
        self.wip_ts.append((event.t, work.value()))


# ---------------------------------------------------------------------------------------------------------
# Server series
# ---------------------------------------------------------------------------------------------------------


class ServerTimeSeries(Collector):
    """Queue length and utilization of one server over time.

    - ``qt``: ``(time, queue length)``, one point per time at which a request joined or left the queue (granted
      or cancelled), holding the length after that time's changes. A request granted on arrival never counts as
      waiting.
    - ``ut``: ``(time, busy slots / capacity)``, starting with the value when the collector is created, then one
      point per change (a request granted or released).
    """

    subscribes: ClassVar = (JobQueued, JobGranted, JobQueueLeft, JobReleased)
    scope_field: ClassVar = "server"

    def __init__(self, server: Server) -> None:
        super().__init__(server)
        self._server = server
        self.qt: list[tuple[float, int]] = []
        self.ut: list[tuple[float, float]] = [(server.env.now, self._utilization())]

    def _utilization(self) -> float:
        server = self._server
        return server.count / server.capacity

    def on_event(self, event: Event) -> None:
        now = event.t
        if not isinstance(event, JobReleased):  # the queue changed
            qt = self.qt
            point = (now, len(self._server.queue))
            if qt and qt[-1][0] == now:
                qt[-1] = point
            else:
                qt.append(point)
        if isinstance(event, (JobGranted, JobReleased)):  # the busy slots changed
            self.ut.append((now, self._utilization()))

    def plot_qt(self) -> None:  # pragma: no cover
        """Step plot of the queue length over time (`RuntimeError` without data)."""
        import matplotlib.pyplot as plt  # lazy: keeps headless runs free of matplotlib

        if not self.qt:
            raise RuntimeError("No queue length data collected.")
        x, y = zip(*self.qt, strict=True)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=1.0)
        plt.title(f"Q(t): {self._server} queue length over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Queue Length")
        plt.show()

    def plot_ut(self) -> None:  # pragma: no cover
        """Step plot of the utilization over time, up to the current time."""
        import matplotlib.pyplot as plt

        ut = [*self.ut, (self._server.env.now, self.ut[-1][1])]
        x, y = zip(*ut, strict=True)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=1.0)
        plt.title(f"U(t): {self._server} utilization over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Utilization rate")
        plt.show()


# ---------------------------------------------------------------------------------------------------------
# Window-aware KPIs
# ---------------------------------------------------------------------------------------------------------


def _job_kpi(name: str, unit: str, description: str) -> KPI:
    return KPI(name, unit=unit, observation="job", cohort="completed_in_window", description=description)


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


class ShopFloorKPIs(Collector):
    """Window-aware KPIs of a shop floor (spec §12.1-§12.3), keyed ``"<shop floor id>/<name>"``.

    Job KPIs are means over the jobs completed in the observation window (completion at or after the warm-up);
    jobs still on the shop floor at the end are excluded:

    - ``makespan``: time from job creation to completion;
    - ``lateness``: completion time minus due date;
    - ``tardiness``: positive lateness, 0 for jobs on time;
    - ``tardy_fraction``: share of jobs with positive lateness;
    - ``total_queue_time``: total time spent waiting in queues.

    ``throughput`` is the number of those jobs divided by the window length. Time-weighted KPIs are means over the
    window of a signal that changes with the events, clipped at the window boundaries:

    - ``utilization``: busy slots of the shop floor's servers (``job.granted`` and ``job.released``) divided by
      their total capacity at the end of the window. ``Server.worked_time`` and ``Server.utilization_rate``,
      which dispatching rules read, keep crediting work at operation completion;
    - ``jobs_in_system``: jobs on the shop floor.

    A KPI without observations, or over an empty window, is left out of :meth:`scalars`. Attach the collector
    before the run: the time-weighted signals start from the state at activation.
    """

    kpis: ClassVar = (
        _job_kpi("makespan", "time", "Mean time from job creation to completion."),
        _job_kpi("lateness", "time", "Mean completion time minus due date."),
        _job_kpi("tardiness", "time", "Mean positive lateness."),
        _job_kpi("tardy_fraction", "fraction", "Share of jobs completed after their due date."),
        _job_kpi("total_queue_time", "time", "Mean total time spent in queues."),
        KPI(
            "throughput",
            unit="jobs/time",
            observation="job",
            aggregation="rate",
            description="Jobs completed in the window per unit of time.",
        ),
        _time_weighted_kpi("utilization", "fraction", "Mean share of the servers' capacity in use."),
        _time_weighted_kpi("jobs_in_system", "jobs", "Mean number of jobs on the shop floor."),
    )
    subscribes: ClassVar = (ShopFloorEntered, JobFinished, JobGranted, JobReleased)
    scope_field: ClassVar = "shopfloor"

    def __init__(self, shopfloor: ShopFloor) -> None:
        super().__init__(shopfloor)
        self._shopfloor = shopfloor
        self._completed = 0
        self._busy: TimeWeighted | None = None  # built at activation, with the warm-up
        self._jobs: TimeWeighted | None = None
        self._server_ids: frozenset[str] = frozenset()
        self._servers_seen = -1  # len(shopfloor.servers) when _server_ids was built

    def _servers(self) -> frozenset[str]:
        servers = self._shopfloor.servers
        if len(servers) != self._servers_seen:
            self._server_ids = frozenset(server.id for server in servers)
            self._servers_seen = len(servers)
        return self._server_ids

    def on_activate(self) -> None:
        start = self.window.start
        shopfloor = self._shopfloor
        self._busy = TimeWeighted(start, sum(server.count for server in shopfloor.servers))
        self._jobs = TimeWeighted(start, len(shopfloor.jobs))

    def on_event(self, event: Event) -> None:
        if isinstance(event, (JobGranted, JobReleased)):
            busy = self._busy
            if busy is not None and event.server in self._servers():
                busy.update(event.t, busy.value + (1 if isinstance(event, JobGranted) else -1))
            return
        jobs = self._jobs
        if jobs is not None:
            jobs.update(event.t, len(self._shopfloor.jobs))
        if isinstance(event, JobFinished):
            lateness = event.lateness
            self.observe("makespan", event.makespan)
            self.observe("lateness", lateness)
            self.observe("tardiness", max(lateness, 0.0))
            self.observe("tardy_fraction", 1.0 if lateness > 0 else 0.0)
            self.observe("total_queue_time", event.total_queue_time)
            if event.t >= self.env.warmup:
                self._completed += 1

    def scalar_values(self) -> Mapping[str, float | None]:
        window = self.window
        length = window.length
        busy, jobs = self._busy, self._jobs
        capacity = sum(server.capacity for server in self._shopfloor.servers)
        mean_busy = None if busy is None else busy.mean(window.start, window.end)
        return {
            "throughput": self._completed / length if length > 0 else None,
            "utilization": mean_busy / capacity if mean_busy is not None and capacity else None,
            "jobs_in_system": None if jobs is None else jobs.mean(window.start, window.end),
        }
