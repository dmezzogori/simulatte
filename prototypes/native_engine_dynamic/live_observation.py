"""Opt-in event-time views and boundary delivery for stock production collectors.

This surface supports the reads below, not model mutation or general actor APIs.
The old buffered DirectTraceStream remains a separate control.
"""

from collections.abc import Mapping
from functools import partial

from simulatte._wire import FrozenMap
from simulatte.collectors import (
    EMACollector,
    ShopFloorTimeSeries,
    CurrentWorkloadCollector,
    ServerTimeSeries,
    ShopFloorKPIs,
)
from simulatte.psp import PspEntered, PspExited
from simulatte.server import JobReleased
from simulatte.shopfloor import ShopFloorEntered, JobFinished, ShopFloorWipUpdated

from .direct_trace import DirectProjection, FloorBinding, ServerBinding, JobBinding
from .handles import JobHandle
from .native import _count, _number
from .production import ProductionStream, ProductionKPIs
from .rich_recording import DEPART
from .studio_trace import ProjectionClock, ProjectionKPIs, TraceStream


class ProjectionSeries(ProjectionKPIs):
    # Stock makespan is mean job flow time; the old benchmark's identically
    # named export is the last completion time. Register only the two series.
    kpis = tuple(kpi for kpi in ProjectionKPIs.kpis if "series" in kpi.kind)

    def scalar_values(self):
        return {}


class ProductionSeries(ProductionKPIs):
    kpis = ProjectionSeries.kpis

    def scalar_values(self):
        return {}


class CountView:
    """Read-only Sized surface: iteration/indexing deliberately unsupported."""

    def __init__(self, get):
        self._get = get

    def __len__(self):
        return self._get()


class WorkView(Mapping):
    def __init__(self, projection):
        self._p = projection

    def __iter__(self):
        return (self._p.server_views[index] for index in self._p.work_order)

    def __len__(self):
        return len(self._p.work_order)

    def __getitem__(self, server):
        if not isinstance(server, ServerView) or server._p is not self._p or server._index not in self._p.work_seen:
            raise KeyError(server)
        return self._p.wip[server._index]

    def values(self):
        return (self._p.wip[index] for index in self._p.work_order)


class FloorView(FloorBinding):
    def __init__(self, projection, fields, name=None):
        self._p = projection
        self._work = WorkView(projection)
        self._active = CountView(lambda: projection.view_floor)
        self._done = CountView(lambda: projection.view_completed)
        super().__init__(projection.env, fields, name)

    @property
    def env(self):
        return self._p.env

    @property
    def wip(self):
        return self._work

    @property
    def servers(self):
        return self._p.server_tuple

    @property
    def jobs(self):
        return self._active

    @property
    def jobs_done(self):
        return self._done


class ServerView(ServerBinding):
    def __init__(self, projection, fields, name=None):
        self._p, self._index = projection, len(projection.server_views)
        self._waiting = CountView(lambda: projection.queue_sizes[self._index])
        super().__init__(projection.env, fields, name)

    @property
    def env(self):
        return self._p.env

    @property
    def capacity(self):
        return 1

    @property
    def count(self):
        return int(self._p.occupants[self._index] is not None)

    @property
    def queue(self):
        return self._waiting

    @property
    def worked_time(self):
        return self._p.worked[self._index]


class JobView(JobBinding):
    def __init__(self, projection, fields, name=None):
        self._p, self._index = projection, len(projection.stages)
        super().__init__(projection.env, fields, name)

    @property
    def processing_times(self):
        return self._p.jobs[self._index].durations

    @property
    def created_at(self):
        return self._p.jobs[self._index].arrival

    @property
    def due_date(self):
        return self._p.jobs[self._index].due

    @property
    def psp_exit_at(self):
        return self._p.released_view[self._index]

    @property
    def finished_at(self):
        return self._p.finished_view[self._index]

    @property
    def done(self):
        return self.finished_at is not None

    @property
    def time_in_psp(self):
        if self.psp_exit_at is None:
            raise ValueError("Job has not been released from PSP. Cannot calculate time in PSP.")
        return self.psp_exit_at - self.created_at

    @property
    def time_in_system(self):
        if not self.done or self.psp_exit_at is None:
            raise ValueError("Job is not done or missing timestamps.")
        return self.finished_at - self.psp_exit_at

    time_in_shopfloor = time_in_system


def attach_stock(env, floor, servers):
    return dict(
        ema=EMACollector(floor).attach(env),
        floor=ShopFloorTimeSeries(floor).attach(env),
        work=CurrentWorkloadCollector(floor).attach(env),
        servers=[ServerTimeSeries(server).attach(env) for server in servers],
        kpis=ShopFloorKPIs(floor).attach(env),
    )


class ObservationClock(ProjectionClock):
    def schedule(self, event, priority=1, delay=0):
        raise RuntimeError("observation environment cannot schedule model work")

    def run(self, until=None):
        # TraceStream.close runs the empty sidecar clock. Preserve the native
        # stopping policy, especially a sealed right-open horizon.
        if self._queue or until is not None:
            raise RuntimeError("observation clock cannot schedule model work")
        policy = self._stopping_policy
        try:
            return super().run()
        finally:
            self._stopping_policy = policy


class ObservationProjection(DirectProjection):
    clock_type = ObservationClock
    kpi_type = ProjectionSeries

    def __init__(self, *args, warmup=0, stock=True, interest_checks=False, **kwargs):
        self.warmup, self.stock = warmup, stock
        self.interest_checks = interest_checks and kwargs.get("level", "full") == "none"
        self.server_views, self.work_order, self.work_seen = [], [], set()
        self.server_tuple = ()
        self.view_floor = self.view_completed = 0
        self.arrived_view, self.released_view, self.finished_view = [], [], []
        super().__init__(*args, **kwargs)

    def bind(self, cls, fields, name=None):
        if cls is FloorBinding:
            return FloorView(self, {**fields, "wip": FrozenMap({})}, name)
        if cls is ServerBinding:
            server = ServerView(self, fields, name)
            self.server_views.append(server)
            return server
        if cls is JobBinding:
            return JobView(self, fields, name)
        return super().bind(cls, fields, name)

    def make_recorder(self, path, *, level, **options):
        if level == "none":
            if path is not None:
                raise ValueError("untraced observation requires trace_path=None")
            return None
        return super().make_recorder(path, level=level, **options)

    def configure_observers(self):
        self.server_tuple = tuple(self.server_views)
        self.env.configure_kpis(warmup=self.warmup)
        self.bundle = (
            attach_stock(self.env, self.env.entities.get("shopfloor"), self.server_views) if self.stock else None
        )

    def input(self, job):
        super().input(job)
        self.arrived_view.append(False)
        self.released_view.append(None)
        self.finished_view.append(None)

    def emit(self, cls, ops=(), **payload):
        if cls in (PspEntered, PspExited, ShopFloorEntered, JobFinished):
            identity = int(payload["job"].split("-")[1])
            if cls is PspEntered:
                self.arrived_view[identity] = True
            elif cls is PspExited:
                self.released_view[identity] = self.env.now
            elif cls is ShopFloorEntered:
                self.view_floor += 1
                for server in self.jobs[identity].route:
                    if server not in self.work_seen:
                        self.work_seen.add(server)
                        self.work_order.append(server)
            else:
                self.finished_view[identity] = self.env.now
                self.view_floor -= 1
                self.view_completed += 1
        if self.interest_checks and not self.env.wants(cls):
            return
        super().emit(cls, ops, **payload)

    def consume(self, row):
        if row[2] != DEPART:
            return super().consume(row)
        now, _, _, identity, op, server = row
        if (
            identity >= len(self.jobs)
            or self.stages[identity] != "active"
            or not self.offsets[identity] <= op < self.offsets[identity + 1]
            or not 0 <= server < len(self.occupants)
            or self.jobs[identity].route[op - self.offsets[identity]] != server
            or self.phase[op] != "ended"
            or self.occupants[server] != op
        ):
            raise ValueError("native departure without ended reservation")
        self.env.at(now)
        jid, sid = f"job-{identity}", f"server-{server}"
        local = op - self.offsets[identity]
        self.wip[server] -= self.jobs[identity].durations[local]
        value = self.wip[server]
        # Production updates floor WIP while the server is still held.
        self.emit(
            ShopFloorWipUpdated,
            (("put", "shopfloor", "wip", sid, value),),
            shopfloor="shopfloor",
            changes=FrozenMap({sid: value}),
        )
        self.occupants[server] = None
        self.emit(
            JobReleased, (("remove", sid, "users", jid), ("set", jid, "location", "transit")), job=jid, server=sid
        )
        self.phase[op] = "departed"

    def inspect(self, identity):
        released, finished = self.released_view[identity], self.finished_view[identity]
        state = (
            "scheduled"
            if not self.arrived_view[identity]
            else "finished"
            if finished is not None
            else "active"
            if released is not None
            else "pool"
        )
        return dict(id=identity, arrival=self.jobs[identity].arrival, released=released, finished=finished, state=state)


class ProductionLiveStream(ProductionStream):
    kpi_type = ProductionSeries
    always_export_totals = True

    def __init__(self, *args, warmup=0, stock=True, **kwargs):
        self.warmup, self.stock = warmup, stock
        super().__init__(*args, **kwargs)

    def configure_observers(self):
        self.env.configure_kpis(warmup=self.warmup)
        if self.kpis is None:
            self.kpis = self.kpi_type(self.shop).attach(self.env)
        self.bundle = attach_stock(self.env, self.shop, self.servers) if self.stock else None


class LiveTraceStream(TraceStream):
    def __init__(self, *args, warmup=0, stock=True, delivery="boundary", interest_checks=False, **kwargs):
        if delivery not in ("boundary", "buffered"):
            raise ValueError("delivery must be boundary or buffered")
        self.delivery, self._delivering = delivery, False
        self.projection_type = partial(
            ObservationProjection, warmup=warmup, stock=stock, interest_checks=interest_checks
        )
        super().__init__(*args, **kwargs)

    @property
    def bundle(self):
        return self.projection.bundle

    @property
    def now(self):
        return self.projection.env.now if self._delivering else self.base.now

    def _guard(self):
        if self._delivering:
            raise RuntimeError("callbacks are observational; model control during delivery is unsupported")

    def _consume(self, *, final=False):
        if self.failed:
            return
        self._delivering = True
        try:
            super()._consume(final=final)
        finally:
            self._delivering = False

    def add(self, job):
        self._guard()
        handle = super().add(job)
        if self.delivery == "boundary":
            try:
                self.base.flush()
                self._consume()
            except BaseException:
                self.failed = True
                raise
        return handle

    def advance(self, until):
        self._guard()
        self._open()
        until = _number(until)
        previous = self.base.now
        if until < previous:
            raise ValueError("cannot advance backwards")
        try:
            self.base.advance(until)
            if until > previous:
                self.projection.env._stopping_policy = FrozenMap({"type": "horizon", "horizon": until})
            if self.delivery == "boundary":
                self.base.flush()
            self._consume()
            if self.delivery == "boundary":
                self.projection.env.at(until)
        except BaseException:
            self.failed = True
            raise

    def inspect(self, handle):
        if not self._delivering:
            return super().inspect(handle)
        if type(handle) is not JobHandle:
            raise TypeError("inspect requires a JobHandle")
        if handle._owner is not self.base._owner:
            raise ValueError("foreign job handle")
        identity = _count(handle.id, "job ID")
        if identity >= len(self.projection.jobs):
            raise ValueError("invalid job handle ID")
        return self.projection.inspect(identity)

    def run(self):
        self._guard()
        self.projection.env._stopping_policy = FrozenMap({"type": "exhaustion"})
        super().run()
        self.projection.env.at(self.base.now)

    def result(self):
        self._guard()
        return super().result()

    def _outcome(self, error):
        # Like production Environment.close, seal a successful observation
        # prefix at its requested horizon without exhausting pending jobs.
        return "failed" if self.failed or error else "completed"

    def close(self):
        self._guard()
        if not self.closed and not self.failed and self.base is not None and self.projection is not None:
            try:
                # The parent marks itself closed before its optional result
                # extraction. Complete that step while this adapter is open.
                if self.ran and not self.projection.kpis.final:
                    self.result()
                self.base.flush()
                self._consume()
                self.projection.env.at(self.base.now)
            except BaseException:
                self.failed = True
                raise
            finally:
                super().close()
        else:
            super().close()


def values(stream):
    """Copy stock state for differential checks and detached benchmark export."""
    bundle = stream.bundle
    env = stream.env if isinstance(stream, ProductionStream) else stream.projection.env
    window = bundle["kpis"].window
    return dict(
        ema={name: value for name, value in vars(bundle["ema"]).items() if name.startswith("ema_")},
        floor={
            name: getattr(bundle["floor"], name)[:]
            for name in ("wip_ts", "job_count_ts", "throughput_ts", "lateness_ts")
        },
        work=bundle["work"].wip_ts[:],
        servers=[dict(qt=c.qt[:], ut=c.ut[:]) for c in bundle["servers"]],
        scalars=dict(bundle["kpis"].scalars()),
        window=(window.start, window.end, window.closed),
        now=env.now,
    )
