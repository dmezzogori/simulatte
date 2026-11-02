"""Typed events from rich rows, without a second generic entity-state replay.

The internal publisher's registry binds kinds/ids/lifecycle only. It provides
creation and activation state, not a live model view. TraceRecorder owns the
sole full replay state; consumers seek the resulting Trace for history.
"""

from simulatte import TraceRecorder
from simulatte.events import Deltas
from simulatte._wire import FrozenMap
from simulatte.policies.events import PolicyDecision
from simulatte.psp import PspEntered, PspExited
from simulatte.server import JobQueued, JobGranted, JobReleased, ServerWorkCredited
from simulatte.shopfloor import ShopFloorEntered, ShopFloorWipUpdated, OperationStarted, OperationCompleted, JobFinished

from .rich_recording import REGISTER, GRANT, DEPART
from .studio_trace import (
    ProjectionClock,
    ProjectionKPIs,
    TraceStream,
    JobState,
    ServerState,
    FloorState,
    PoolState,
)


def _bind(self, env, fields, name=None):
    self._creation_fields = fields
    env.entities.attach(self, name=name)
    if env.activated:
        self._creation_fields = None


def _creation_snapshot(self):
    if self._creation_fields is None:
        raise RuntimeError("direct trace publisher has no live model snapshot; use Trace.state_at")
    return {**self._creation_fields, "label": self.label}


def _binding_repr(self):
    return f"{type(self).__name__}(id={self.id!r})"


class JobBinding(JobState):
    __init__, snapshot, __repr__ = _bind, _creation_snapshot, _binding_repr


class ServerBinding(ServerState):
    __init__, snapshot, __repr__ = _bind, _creation_snapshot, _binding_repr


class FloorBinding(FloorState):
    __init__, snapshot, __repr__ = _bind, _creation_snapshot, _binding_repr


class PoolBinding(PoolState):
    __init__, snapshot, __repr__ = _bind, _creation_snapshot, _binding_repr


class DirectProjection:
    clock_type = ProjectionClock
    kpi_type = ProjectionKPIs

    def bind(self, cls, fields, name=None):
        return cls(self.env, fields, name)

    def configure_observers(self):
        pass

    def make_recorder(self, path, **options):
        return TraceRecorder(self.env, path, **options)

    def __init__(
        self,
        servers,
        path,
        *,
        policy,
        provenance,
        parameters,
        limits,
        debug,
        max_pending_bytes,
        level="full",
        wip_cap=None,
    ):
        self.env = self.clock_type(seed=0, time_unit="time", provenance=provenance, parameters=parameters, debug=debug)
        self.recorder = self.make_recorder(path, level=level, chunk_limits=limits, max_pending_bytes=max_pending_bytes)
        self.jobs, self.offsets, self.phase = [], [0], []
        self.policy, self.capped = policy, wip_cap is not None
        self.stages, self.queued_at, self.waits = [], [], []
        self.queue_sizes, self.occupants = [0] * servers, [None] * servers
        self.worked, self.wip = [0.0] * servers, [0.0] * servers
        self.system = self.floor = 0
        try:
            floor = self.bind(
                FloorBinding,
                dict(wip=FrozenMap({f"server-{s}": 0.0 for s in range(servers)}), jobs_in_system=0),
                "shopfloor",
            )
            self.bind(PoolBinding, dict(jobs=(), shopfloor=floor.id), "psp")
            for _ in range(servers):
                self.bind(ServerBinding, dict(capacity=1, users=(), queue=(), worked_time=0.0))
            self.kpis = self.kpi_type(floor).attach(self.env)
            self.configure_observers()
            self.env.activate()
            for binding in self.env.entities.live():
                binding._creation_fields = None
            self.kpis.sample("system_wip", 0)
            self.kpis.sample("floor_wip", 0)
        except BaseException:
            try:
                if self.recorder is not None:
                    self.recorder.close(outcome="failed")
            finally:
                self.env.close()
            raise

    def input(self, job):
        self.jobs.append(job)
        self.offsets.append(self.offsets[-1] + len(job.route))

    def emit(self, cls, ops=(), **payload):
        self.env.emit(cls(**payload, deltas=Deltas(tuple(ops))))

    def consume(self, row):
        now, _, kind, identity, op, server = row
        if identity >= len(self.jobs):
            raise ValueError("rich record references an unregistered input")
        job = self.jobs[identity]
        jid, sid = f"job-{identity}", f"server-{server}"
        self.env.at(now)
        if kind == REGISTER:
            if identity != len(self.stages) or now != job.arrival:
                raise ValueError("full projection requires scalar registration at arrival")
            binding = self.bind(
                JobBinding,
                dict(
                    sku="native",
                    routing=tuple(f"server-{s}" for s in job.route),
                    processing_times=job.durations,
                    op_index=None,
                    location=None,
                    due_date=job.due,
                    created_at=now,
                    finished_at=None,
                    shopfloor=None,
                ),
            )
            if binding.id != jid:
                raise ValueError("projection job identity mismatch")
            self.stages.append("scheduled")
            self.waits.append(0.0)
            self.phase.extend(["new"] * len(job.route))
            self.queued_at.extend([0.0] * len(job.route))
            return
        if identity >= len(self.stages):
            raise ValueError("event precedes rich registration")
        if kind == 1:
            if self.stages[identity] != "scheduled" or now != job.arrival:
                raise ValueError("invalid native arrival")
            position = self.system - self.floor
            self.emit(
                PspEntered,
                (
                    ("insert", "psp", "jobs", position, jid),
                    ("set", jid, "location", "psp:psp"),
                    ("set", jid, "shopfloor", "shopfloor"),
                ),
                job=jid,
                psp="psp",
                position=position,
            )
            self.stages[identity] = "pool"
            self.system += 1
            self.kpis.sample("system_wip", self.system)
            return
        if kind == 6:
            if self.stages[identity] != "pool":
                raise ValueError("release without pool membership")
            if self.capped:
                self.emit(PolicyDecision, policy="ConWIP", job=jid, action="release")
            self.emit(
                PspExited,
                (("remove", "psp", "jobs", jid), ("set", jid, "location", "transit")),
                job=jid,
                psp="psp",
                reason="released",
            )
            ops = [
                ("set", "shopfloor", "jobs_in_system", self.floor + 1),
                ("set", jid, "shopfloor", "shopfloor"),
                ("set", jid, "location", "transit"),
            ]
            for target, duration in zip(job.route, job.durations):
                self.wip[target] += duration
                ops.append(("put", "shopfloor", "wip", f"server-{target}", self.wip[target]))
            self.emit(ShopFloorEntered, ops, job=jid, shopfloor="shopfloor")
            self.stages[identity] = "active"
            self.floor += 1
            self.kpis.sample("floor_wip", self.floor)
            return
        if kind == 5:
            if self.stages[identity] != "active" or any(
                p != "departed" for p in self.phase[self.offsets[identity] : self.offsets[identity + 1]]
            ):
                raise ValueError("finish before all operation departures")
            self.emit(
                JobFinished,
                (
                    ("set", jid, "location", "done"),
                    ("set", jid, "finished_at", now),
                    ("set", "shopfloor", "jobs_in_system", self.floor - 1),
                ),
                job=jid,
                shopfloor="shopfloor",
                makespan=now - job.arrival,
                lateness=now - job.due,
                total_queue_time=self.waits[identity],
            )
            self.stages[identity] = "finished"
            self.floor -= 1
            self.system -= 1
            self.kpis.sample("system_wip", self.system)
            self.kpis.sample("floor_wip", self.floor)
            self.env.entities.retire(self.env.entities.get(jid))
            return
        local = op - self.offsets[identity]
        if self.stages[identity] != "active" or not 0 <= local < len(job.route) or job.route[local] != server:
            raise ValueError("invalid native operation identity")
        expected = {2: "new", GRANT: "queued", 3: "granted", 4: "running", DEPART: "ended"}
        if self.phase[op] != expected.get(kind):
            raise ValueError("invalid native operation phase")
        duration = job.durations[local]
        if kind == 2:
            size = self.queue_sizes[server]
            self.queue_sizes[server] += 1
            self.emit(
                JobQueued,
                (("insert", sid, "queue", size, jid), ("set", jid, "location", f"queue:{sid}")),
                job=jid,
                server=sid,
                priority=duration if self.policy == "spt" else 0.0,
                queue_length=size + 1,
            )
            self.queued_at[op] = now
            self.phase[op] = "queued"
        elif kind == GRANT:
            if self.occupants[server] is not None or self.queue_sizes[server] <= 0:
                raise ValueError("native grant over capacity or without queue")
            self.queue_sizes[server] -= 1
            self.occupants[server] = op
            self.emit(
                JobGranted,
                (
                    ("remove", sid, "queue", jid),
                    ("insert", sid, "users", 0, jid),
                    ("set", jid, "location", f"server:{sid}"),
                ),
                job=jid,
                server=sid,
            )
            self.phase[op] = "granted"
        elif kind == 3:
            self.emit(
                OperationStarted,
                (("set", jid, "op_index", local),),
                job=jid,
                server=sid,
                op_index=local,
                processing_time=duration,
                planned_end=now + duration,
            )
            self.waits[identity] += now - self.queued_at[op]
            self.phase[op] = "running"
        elif kind == 4:
            self.worked[server] += duration
            self.emit(
                ServerWorkCredited,
                (("set", sid, "worked_time", self.worked[server]),),
                server=sid,
                job=jid,
                processing_time=duration,
            )
            self.emit(OperationCompleted, job=jid, server=sid, op_index=local, processing_time=duration)
            self.phase[op] = "ended"
        else:
            if self.occupants[server] != op:
                raise ValueError("native departure without reservation")
            self.occupants[server] = None
            self.emit(
                JobReleased, (("remove", sid, "users", jid), ("set", jid, "location", "transit")), job=jid, server=sid
            )
            self.wip[server] -= duration
            value = self.wip[server]
            self.emit(
                ShopFloorWipUpdated,
                (("put", "shopfloor", "wip", sid, value),),
                shopfloor="shopfloor",
                changes=FrozenMap({sid: value}),
            )
            self.phase[op] = "departed"


class DirectTraceStream(TraceStream):
    projection_type = DirectProjection
