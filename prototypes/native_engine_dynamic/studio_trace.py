"""Buffered native journal -> actual production events, digest and TraceRecorder.

This sidecar never runs model processes or feeds state back into the engine.
Its projection clock is deliberately separate from the native simulation clock.
"""

from __future__ import annotations

import hashlib
import math

from simulatte import Environment, Trace, TraceRecorder
from simulatte._wire import FrozenMap, pack
from simulatte.events import Deltas, apply_deltas
from simulatte.job import ProductionJob
from simulatte.kpi import Collector, KPI
from simulatte.psp import PreShopPool, PspEntered, PspExited
from simulatte.server import Server, JobQueued, JobGranted, JobReleased, ServerWorkCredited
from simulatte.shopfloor import (
    ShopFloor,
    ShopFloorEntered,
    ShopFloorWipUpdated,
    OperationStarted,
    OperationCompleted,
    JobFinished,
)
from simulatte.trace import ChunkLimits

from .rich_recording import Reader, REGISTER, GRANT, DEPART
from .native import _number
from prototypes.native_engine.model import Job

SCALARS = (
    "total_flow",
    "total_wait",
    "total_tardiness",
    "wip_area",
    "max_wip",
    "total_pool_wait",
    "floor_wip_area",
    "max_floor_wip",
    "completed",
    "makespan",
)


def _initialize(self, env, fields, name=None):
    self._fields = fields
    env.entities.attach(self, name=name)
    fields["label"] = self.label


def _snapshot(self):
    return {**{k: v for k, v in self._fields.items() if k != "$kind"}, "label": self.label}


# Inherit the registered production kinds/schemas, without constructing SimPy actors.
class JobState(ProductionJob):
    __init__, snapshot = _initialize, _snapshot


class ServerState(Server):
    __init__, snapshot = _initialize, _snapshot


class PoolState(PreShopPool):
    __init__, snapshot = _initialize, _snapshot


class FloorState(ShopFloor):
    __init__, snapshot = _initialize, _snapshot


class ProjectionClock(Environment):
    def at(self, now):
        if self._queue or not math.isfinite(now) or now < self.now:
            raise ValueError("invalid projection clock or unexpected SimPy work")
        # Environment.emit stamps _now directly; no model scheduling is performed.
        self._now = now


class ProjectionKPIs(Collector):
    kpis = tuple(
        KPI(name, unit="time" if "time" in name or name == "makespan" else "value", aggregation="native-total")
        for name in SCALARS
    ) + (
        KPI("system_wip", unit="jobs", kind=("series",)),
        KPI("floor_wip", unit="jobs", kind=("series",)),
    )

    def __init__(self, scope):
        super().__init__(scope)
        self.final = {}

    def scalar_values(self):
        return self.final


class Projection:
    def __init__(self, servers, path, *, policy, provenance, parameters, limits, debug, max_pending_bytes):
        self.env = ProjectionClock(seed=0, time_unit="time", provenance=provenance, parameters=parameters, debug=debug)
        self.recorder = TraceRecorder(self.env, path, chunk_limits=limits, max_pending_bytes=max_pending_bytes)
        self.state, self.jobs, self.offsets, self.phase = {}, [], [0], []
        self.policy, self.stages, self.queued_at, self.waits = policy, [], {}, []
        self.system = self.floor = 0
        try:
            floor = self._create(
                FloorState,
                {"wip": FrozenMap({f"server-{i}": 0.0 for i in range(servers)}), "jobs_in_system": 0},
                "shopfloor",
            )
            self._create(PoolState, {"jobs": (), "shopfloor": floor.id}, "psp")
            for _ in range(servers):
                self._create(ServerState, {"capacity": 1, "users": (), "queue": (), "worked_time": 0.0})
            self.kpis = ProjectionKPIs(floor).attach(self.env)
            self.env.activate()
            self.kpis.sample("system_wip", 0)
            self.kpis.sample("floor_wip", 0)
        except BaseException:
            self.recorder.close(outcome="failed")
            raise

    def _create(self, cls, fields, name=None):
        entity = cls(self.env, fields, name)
        fields["$kind"] = entity.kind
        self.state[entity.id] = fields
        return entity

    def input(self, job):
        self.jobs.append(job)
        self.offsets.append(self.offsets[-1] + len(job.route))

    def emit(self, cls, ops=(), **payload):
        deltas = Deltas(tuple(ops))
        apply_deltas(self.state, deltas)
        self.env.emit(cls(**payload, deltas=deltas))

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
            entity = self._create(
                JobState,
                {
                    "sku": "native",
                    "routing": tuple(f"server-{s}" for s in job.route),
                    "processing_times": job.durations,
                    "op_index": None,
                    "location": None,
                    "due_date": job.due,
                    "created_at": now,
                    "finished_at": None,
                    "shopfloor": None,
                },
            )
            if entity.id != jid:
                raise ValueError("projection job identity mismatch")
            self.stages.append("scheduled")
            self.waits.append(0.0)
            self.phase.extend(["new"] * len(job.route))
            return
        if identity >= len(self.stages):
            raise ValueError("event precedes rich registration")
        if kind == 1:
            if self.stages[identity] != "scheduled" or now != job.arrival:
                raise ValueError("invalid native arrival")
            position = len(self.state["psp"]["jobs"])
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
                key = f"server-{target}"
                ops.append(("put", "shopfloor", "wip", key, self.state["shopfloor"]["wip"][key] + duration))
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
            return
        local = op - self.offsets[identity]
        if self.stages[identity] != "active" or not 0 <= local < len(job.route) or job.route[local] != server:
            raise ValueError("invalid native operation identity")
        duration = job.durations[local]
        phase = self.phase[op]
        expected = {2: "new", GRANT: "queued", 3: "granted", 4: "running", DEPART: "ended"}
        if phase != expected.get(kind):
            raise ValueError("invalid native operation phase")
        if kind == 2:
            size = len(self.state[sid]["queue"])
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
            if self.state[sid]["users"]:
                raise ValueError("native grant over capacity")
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
            self.emit(
                ServerWorkCredited,
                (("set", sid, "worked_time", self.state[sid]["worked_time"] + duration),),
                server=sid,
                job=jid,
                processing_time=duration,
            )
            self.emit(OperationCompleted, job=jid, server=sid, op_index=local, processing_time=duration)
            self.phase[op] = "ended"
        else:
            self.emit(
                JobReleased, (("remove", sid, "users", jid), ("set", jid, "location", "transit")), job=jid, server=sid
            )
            value = self.state["shopfloor"]["wip"][sid] - duration
            self.emit(
                ShopFloorWipUpdated,
                (("put", "shopfloor", "wip", sid, value),),
                shopfloor="shopfloor",
                changes=FrozenMap({sid: value}),
            )
            self.phase[op] = "departed"


class TraceStream:
    def __init__(
        self,
        factory,
        servers,
        *,
        raw_path,
        trace_path,
        wip_cap=None,
        policy="fifo",
        provenance=None,
        parameters=None,
        chunk_limits=None,
        debug=False,
        max_pending_bytes=64 << 20,
    ):
        self.base = self.projection = self.reader = None
        self.servers = servers
        self.ran = self.failed = self.closed = False
        try:
            self.base = factory(servers, wip_cap=wip_cap, policy=policy, mode="studio", trace_path=raw_path)
            self.reader = Reader(raw_path)
            self.projection = Projection(
                servers,
                trace_path,
                policy=policy,
                provenance=provenance,
                parameters=parameters,
                debug=debug,
                max_pending_bytes=max_pending_bytes,
                limits=chunk_limits or ChunkLimits(max_latency_s=float("inf")),
            )
        except BaseException:
            self.close()
            raise

    @property
    def now(self):
        return self.base.now

    def _consume(self, *, final=False):
        for row in self.reader.take(final=final):
            self.projection.consume(row)

    def _open(self):
        if self.closed or self.ran or self.failed:
            raise RuntimeError("trace stream is closed, failed or sealed")

    def add(self, job):
        self._open()
        if type(job) is not Job:
            raise TypeError("add requires an exact validated Job")
        if job.arrival != self.base.now:
            raise ValueError("full projection requires registration at arrival")
        if (
            any(s >= self.servers for s in job.route)
            or len(self.projection.jobs) + 1 >= 0xFFFFFFFF
            or self.projection.offsets[-1] + len(job.route) >= 0xFFFFFFFF
        ):
            raise ValueError("invalid trace input count or route")
        try:
            handle = self.base.add(job)
            self.projection.input(job)
            return handle
        except BaseException:
            self.failed = True
            raise

    def advance(self, until):
        self._open()
        until = _number(until)
        if until < self.base.now:
            raise ValueError("cannot advance backwards")
        try:
            self.base.advance(until)
            self._consume()
        except BaseException:
            self.failed = True
            raise

    def inspect(self, handle):
        return self.base.inspect(handle)

    def run(self):
        self._open()
        try:
            self.base.run()
            self._consume(final=True)
            self.ran = True
        except BaseException:
            self.failed = True
            raise

    def result(self):
        if self.failed or self.closed:
            raise RuntimeError("trace stream is closed or failed")
        result = self.base.result()
        if self.reader.count != result["records"]:
            raise ValueError("source records not completely projected")
        self.projection.kpis.final = {
            name: float(result[name] if name in ("completed", "makespan") else result["kpis"][name]) for name in SCALARS
        }
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        error = None
        try:
            if self.ran and not self.failed:
                if not self.projection.kpis.final:
                    self.result()
                self.projection.env.at(self.base.now)
            if self.base is not None:
                self.base.close()
            if self.reader is not None and self.projection is not None:
                self._consume(final=True)
        except BaseException as exc:
            error = exc
        finally:
            if self.base is not None:
                self.base.close()
            if self.reader is not None:
                self.reader.close()
            if self.projection is not None:
                outcome = "failed" if self.failed or error else "completed" if self.ran else "cancelled"
                try:
                    self.projection.env.run()  # Empty projection calendar: declares exhaustion, no model replay.
                    self.projection.recorder.close(outcome=outcome)
                finally:
                    self.projection.env.close()
        if error:
            raise error

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def validate_trace(path, workload, result, *, wip_cap=None):
    from .validation import validate_result

    validate_result(workload, result, wip_cap=wip_cap)
    trace = Trace.open(path)
    trace.check()
    assert trace.verify() is True and not trace.truncated and trace.outcome == "completed"
    last = trace.cursor_range[1]
    state = trace.state_at(last)
    assert state["shopfloor"]["jobs_in_system"] == 0 and state["psp"]["jobs"] == ()
    for index, job in enumerate(workload.jobs):
        fields = state[f"job-{index}"]
        assert fields["location"] == "done" and fields["finished_at"] == result["finished"][index]
        assert fields["created_at"] == job.arrival and fields["processing_times"] == job.durations
        assert fields["routing"] == tuple(f"server-{s}" for s in job.route)
        assert fields["op_index"] == len(job.route) - 1
    for server in range(workload.servers):
        fields = state[f"server-{server}"]
        assert fields["queue"] == fields["users"] == ()
        assert math.isclose(fields["worked_time"], result["kpis"]["busy"][server], rel_tol=1e-11, abs_tol=1e-8)
        assert math.isclose(state["shopfloor"]["wip"][f"server-{server}"], 0.0, abs_tol=1e-8)
    offsets = [0]
    for job in workload.jobs:
        offsets.append(offsets[-1] + len(job.route))
    events_hash, count, starts, ends = hashlib.sha256(), 0, 0, 0
    for event in trace.events():
        events_hash.update(pack((event.t, event.seq, event.ordinal, event.type, event.payload, event.deltas)))
        count += 1
        if event.type in ("operation.started", "operation.completed"):
            job = int(event.payload["job"].split("-")[1])
            op = offsets[job] + event.payload["op_index"]
            expected = result["starts"] if event.type == "operation.started" else result["ends"]
            assert event.t == expected[op]
            starts += event.type == "operation.started"
            ends += event.type == "operation.completed"
    assert starts == ends == workload.operation_count
    assert count == 5 * len(workload.jobs) + 7 * workload.operation_count
    observers = sum(1 for _ in trace.observer_events())
    assert observers == 4 * len(workload.jobs) + 2
    scalars = trace.kpis()
    for name in SCALARS:
        expected = result[name] if name in ("completed", "makespan") else result["kpis"][name]
        assert scalars[f"shopfloor/{name}"] == expected
    return dict(
        digest=trace.fingerprint.digest,
        events_sha256=events_hash.hexdigest(),
        state_sha256=hashlib.sha256(pack(state)).hexdigest(),
        kpis_sha256=hashlib.sha256(pack(scalars)).hexdigest(),
        series_sha256=hashlib.sha256(pack(trace.kpi_series())).hexdigest(),
        domain_events=count,
        observer_events=observers,
        chunks=len(trace.index),
    )
