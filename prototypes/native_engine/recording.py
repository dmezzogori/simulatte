"""Prototype observations and binary records. This is not the Studio trace format."""

from __future__ import annotations

import math
import struct

MAGIC = b"NEVT0001"
RECORD = struct.Struct("<dQIIII")
UNSET = 0xFFFFFFFF
ARRIVAL, QUEUE, START, END, FINISH = range(1, 6)


class Writer:
    def __init__(self, path):
        self.stream = open(path, "wb", buffering=65536)
        self.stream.write(MAGIC)
        self.buffer = bytearray()
        self.count = 0

    def emit(self, now, kind, job, op=UNSET, server=UNSET):
        self.buffer.extend(RECORD.pack(now, self.count, kind, job, op, server))
        self.count += 1
        if len(self.buffer) >= 65536:
            self.stream.write(self.buffer)
            self.buffer.clear()

    def flush(self):
        self.stream.write(self.buffer)
        self.buffer.clear()
        self.stream.flush()

    def close(self):
        if not self.stream.closed:
            try:
                self.flush()
            finally:
                self.stream.close()


def read_records(path):
    with open(path, "rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError("invalid prototype recording header")
        seq = 0
        previous = 0.0
        while data := stream.read(RECORD.size):
            if len(data) != RECORD.size:
                raise ValueError("truncated prototype record")
            row = RECORD.unpack(data)
            t, ordinal, kind, _, op, server = row
            if not math.isfinite(t) or t < previous or ordinal != seq or kind not in range(1, 6):
                raise ValueError("invalid record time, sequence or kind")
            if kind in (ARRIVAL, FINISH) and (op != UNSET or server != UNSET):
                raise ValueError("invalid job record fields")
            if kind in (QUEUE, START, END) and (op == UNSET or server == UNSET):
                raise ValueError("invalid operation record fields")
            previous = t
            seq += 1
            yield row


class Observations:
    def __init__(self, workload, mode, trace_path):
        self.workload = workload
        count = workload.operation_count
        self.starts = [0.0] * count
        self.ends = [0.0] * count
        self.queued = [0.0] * count
        self.finished = [0.0] * len(workload.jobs)
        self.dispatch = []
        self.writer = Writer(trace_path) if mode == "record" else None
        self.metrics = mode != "none"
        self.total_flow = self.total_wait = self.total_tardiness = self.wip_area = 0.0
        self.busy = [0.0] * workload.servers
        self.wip = self.max_wip = self.completed = 0
        self.last_wip = self.makespan = 0.0

    def arrival(self, now, job):
        if self.metrics:
            self.wip_area += (now - self.last_wip) * self.wip
            self.last_wip = now
            self.wip += 1
            self.max_wip = max(self.max_wip, self.wip)
        if self.writer:
            self.writer.emit(now, ARRIVAL, job)

    def queue(self, now, job, op, server):
        self.queued[op] = now
        if self.writer:
            self.writer.emit(now, QUEUE, job, op, server)

    def start(self, now, job, op, server):
        self.starts[op] = now
        self.dispatch.append(op)
        if self.metrics:
            self.total_wait += now - self.queued[op]
        if self.writer:
            self.writer.emit(now, START, job, op, server)

    def end(self, now, job, op, server, duration):
        self.ends[op] = now
        if self.metrics:
            self.busy[server] += duration
        if self.writer:
            self.writer.emit(now, END, job, op, server)

    def finish(self, now, job):
        self.finished[job] = now
        self.makespan = now
        self.completed += 1
        if self.metrics:
            self.wip_area += (now - self.last_wip) * self.wip
            self.last_wip = now
            self.wip -= 1
            source = self.workload.jobs[job]
            self.total_flow += now - source.arrival
            self.total_tardiness += max(0.0, now - source.due)
        if self.writer:
            self.writer.emit(now, FINISH, job)

    def result(self, events, callbacks):
        kpis = None
        if self.metrics:
            kpis = dict(
                total_flow=self.total_flow,
                total_wait=self.total_wait,
                total_tardiness=self.total_tardiness,
                busy=self.busy.copy(),
                wip_area=self.wip_area,
                max_wip=self.max_wip,
            )
        return dict(
            starts=self.starts.copy(),
            ends=self.ends.copy(),
            finished=self.finished.copy(),
            dispatch=self.dispatch.copy(),
            makespan=self.makespan,
            completed=self.completed,
            events=events,
            callbacks=callbacks,
            records=self.writer.count if self.writer else 0,
            kpis=kpis,
        )

    def close(self):
        if self.writer:
            self.writer.close()

    def flush(self):
        if self.writer:
            self.writer.flush()
