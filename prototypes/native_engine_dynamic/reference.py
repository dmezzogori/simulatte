"""Independent compact Python model of streamed admission and cap/EDD release."""

import heapq
import math

from prototypes.native_engine.model import Job, finite
from prototypes.native_engine.recording import UNSET

from .handles import JobHandle
from .recording import ARRIVAL, END, FINISH, QUEUE, RELEASE, START, Writer

FEED_EVENT, START_EVENT, END_EVENT, ADVANCE_EVENT, DISPATCH_EVENT = range(5)


def count(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 < value < 0xFFFFFFFF:
        raise ValueError(f"{name} must be a positive native count")
    return value


class ReferenceStream:
    def __init__(self, servers, *, wip_cap=None, policy="fifo", mode="none", trace_path=None):
        self.servers = count(servers, "servers")
        self.cap = None if wip_cap is None else count(wip_cap, "wip_cap")
        if policy not in ("fifo", "spt") or mode not in ("none", "kpi", "record"):
            raise ValueError("invalid policy or mode")
        if (mode == "record") != (trace_path is not None):
            raise ValueError("trace path required exactly in record mode")
        self.policy, self.mode, self.now = policy, mode, 0.0
        self._owner, self.state = object(), "open"
        self.jobs, self.offsets, self.routes, self.durations, self.owners = [], [0], [], [], []
        self.starts, self.ends, self.queued, self.finished, self.released, self.dispatch = [], [], [], [], [], []
        self.status, self.heap, self.pool = [], [], []
        self.queues = [[] for _ in range(servers)]
        self.busy_server = [False] * servers
        self.busy = [0.0] * servers
        self.floor = self.wip = self.max_floor = self.max_wip = 0
        self.floor_area = self.wip_area = self.last_floor = self.last_wip = 0.0
        self.total_flow = self.total_wait = self.total_tardiness = self.pool_wait = 0.0
        self.completed = self.events = self.sequence = 0
        self.makespan = 0.0
        self.writer = Writer(trace_path) if mode == "record" else None

    def _open(self):
        if self.state != "open":
            raise RuntimeError("stream is closed, failed or sealed")

    def _schedule(self, at, kind, value):
        if not math.isfinite(at) or at < self.now:
            raise ValueError("invalid event time")
        heapq.heappush(self.heap, (at, int(kind != FEED_EVENT), self.sequence, kind, value))
        self.sequence += 1

    def add(self, job):
        self._open()
        if type(job) is not Job:
            raise TypeError("add requires an exact validated Job")
        if any(server >= self.servers for server in job.route):
            raise ValueError("server outside stream")
        if job.arrival < self.now or (self.jobs and job.arrival < self.jobs[-1].arrival):
            raise ValueError("arrival is past or decreasing")
        if len(self.jobs) + 1 >= 0xFFFFFFFF or len(self.routes) + len(job.route) >= 0xFFFFFFFF:
            raise ValueError("job/operation IDs exceed native limit")
        try:
            identity = len(self.jobs)
            self.jobs.append(job)
            self.routes.extend(job.route)
            self.durations.extend(job.durations)
            self.owners.extend([identity] * len(job.route))
            self.offsets.append(len(self.routes))
            self.starts.extend([0.0] * len(job.route))
            self.ends.extend([0.0] * len(job.route))
            self.queued.extend([0.0] * len(job.route))
            self.finished.append(None)
            self.released.append(None)
            self.status.append("scheduled")
            self._schedule(job.arrival, FEED_EVENT, identity)
            return JobHandle(self._owner, identity)
        except BaseException:
            self.state = "failed"
            raise

    def _record(self, kind, job, op=UNSET, server=UNSET):
        if self.writer:
            self.writer.emit(self.now, kind, job, op, server)

    def _system(self, delta):
        if self.mode != "none":
            self.wip_area += (self.now - self.last_wip) * self.wip
            self.last_wip = self.now
            self.wip += delta
            self.max_wip = max(self.max_wip, self.wip)

    def _floor(self, delta):
        if self.mode != "none":
            self.floor_area += (self.now - self.last_floor) * self.floor
            self.last_floor = self.now
        self.floor += delta
        self.max_floor = max(self.max_floor, self.floor)

    def _release(self, job):
        self._floor(1)
        self.released[job] = self.now
        self.status[job] = "active"
        if self.mode != "none":
            self.pool_wait += self.now - self.jobs[job].arrival
        self._record(RELEASE, job)
        self._enqueue(self.offsets[job])

    def _grant(self, server):
        queue = self.queues[server]
        if self.busy_server[server] or not queue:
            return
        index = 0 if self.policy == "fifo" else min(range(len(queue)), key=lambda i: self.durations[queue[i]])
        op = queue.pop(index)
        self.busy_server[server] = True
        self._schedule(self.now, START_EVENT, op)

    def _enqueue(self, op):
        server = self.routes[op]
        self.queued[op] = self.now
        self._record(QUEUE, self.owners[op], op, server)
        self.queues[server].append(op)
        self._grant(server)

    def _process(self, kind, value):
        if kind == FEED_EVENT:
            job = value
            self.status[job] = "pool"
            self._record(ARRIVAL, job)
            self._system(1)
            if self.cap is None or self.floor < self.cap:
                self._release(job)
            else:
                heapq.heappush(self.pool, (self.jobs[job].due, job))
            return
        if kind == DISPATCH_EVENT:
            self._grant(value)
            return
        op = value
        job, server = self.owners[op], self.routes[op]
        if kind == START_EVENT:
            end = self.now + self.durations[op]
            if not math.isfinite(end) or end <= self.now:
                raise ValueError("operation end overflow or lost precision")
            self.starts[op] = self.now
            self.dispatch.append(op)
            self._record(START, job, op, server)
            if self.mode != "none":
                self.total_wait += self.now - self.queued[op]
            self._schedule(end, END_EVENT, op)
        elif kind == END_EVENT:
            self.ends[op] = self.now
            if self.mode != "none":
                self.busy[server] += self.durations[op]
            self._record(END, job, op, server)
            self._schedule(self.now, ADVANCE_EVENT, op)
        else:
            self.busy_server[server] = False
            self._schedule(self.now, DISPATCH_EVENT, server)
            if op + 1 < self.offsets[job + 1]:
                self._enqueue(op + 1)
            else:
                self.finished[job] = self.now
                self.makespan = self.now
                self.status[job] = "finished"
                self.completed += 1
                if self.mode != "none":
                    self.total_flow += self.now - self.jobs[job].arrival
                    self.total_tardiness += max(0, self.now - self.jobs[job].due)
                self._record(FINISH, job)
                self._system(-1)
                self._floor(-1)
                while self.pool and (self.cap is None or self.floor < self.cap):
                    self._release(heapq.heappop(self.pool)[1])

    def _drive(self, until=None):
        while self.heap and (until is None or self.heap[0][0] < until):
            self.now, _, _, kind, value = heapq.heappop(self.heap)
            self.events += 1
            self._process(kind, value)

    def advance(self, until):
        self._open()
        until = finite(until, "until")
        if until < self.now:
            raise ValueError("cannot advance backward")
        try:
            self._drive(until)
            self.now = until
        except BaseException:
            self.state = "failed"
            raise

    def inspect(self, handle):
        if self.state not in ("open", "done"):
            raise RuntimeError("stream is closed or failed")
        if type(handle) is not JobHandle:
            raise TypeError("inspect requires a JobHandle")
        if isinstance(handle.id, bool) or not isinstance(handle.id, int):
            raise TypeError("job ID must be an integer")
        if handle._owner is not self._owner or not 0 <= handle.id < len(self.jobs):
            raise ValueError("foreign or invalid handle")
        return dict(
            id=handle.id,
            arrival=self.jobs[handle.id].arrival,
            released=self.released[handle.id],
            finished=self.finished[handle.id],
            state=self.status[handle.id],
        )

    def run(self):
        self._open()
        try:
            self._drive()
            if self.completed != len(self.jobs) or len(self.dispatch) != len(self.routes):
                raise ValueError("incomplete simulation")
            if self.writer:
                self.writer.flush()
            self.state = "done"
        except BaseException:
            self.state = "failed"
            raise

    def result(self):
        if self.state != "done":
            raise RuntimeError("result requires successful open stream")
        kpis = (
            None
            if self.mode == "none"
            else dict(
                total_flow=self.total_flow,
                total_wait=self.total_wait,
                total_tardiness=self.total_tardiness,
                busy=self.busy.copy(),
                wip_area=self.wip_area,
                max_wip=self.max_wip,
                total_pool_wait=self.pool_wait,
                floor_wip_area=self.floor_area,
                max_floor_wip=self.max_floor,
            )
        )
        return dict(
            starts=self.starts.copy(),
            ends=self.ends.copy(),
            finished=self.finished.copy(),
            released=self.released.copy(),
            dispatch=self.dispatch.copy(),
            makespan=self.makespan,
            completed=self.completed,
            events=self.events,
            callbacks=0,
            records=self.writer.count if self.writer else 0,
            kpis=kpis,
        )

    def close(self):
        if self.state != "closed":
            try:
                if self.writer:
                    self.writer.close()
            except OSError:
                pass
            finally:
                self.heap.clear()
                self.pool.clear()
                self.state = "closed"
