"""Small Python implementation of the five-phase prototype contract."""

from __future__ import annotations

import heapq
import math

from .model import check_options, choose_hook, flatten
from .recording import Observations

FEED, START, END, ADVANCE, RELEASE = range(5)


class ReferenceRunner:
    def __init__(self, workload, *, policy="fifo", mode="none", hook=None, trace_path=None):
        check_options(policy, mode, hook, trace_path)
        self.workload, self.policy, self.hook = workload, policy, hook
        self.offsets, self.servers, self.durations, self.owners = flatten(workload)
        self.queues = [[] for _ in range(workload.servers)]
        self.busy = [-1] * workload.servers
        self.heap = []
        self.sequence = self.events = self.callbacks = 0
        self.now = 0.0
        self.observations = Observations(workload, mode, trace_path)
        self.state = "built"
        if workload.jobs:
            self.schedule(workload.jobs[0].arrival, FEED, 0)

    def schedule(self, at, kind, value):
        heapq.heappush(self.heap, (at, self.sequence, kind, value))
        self.sequence += 1

    def grant(self, server):
        queue = self.queues[server]
        if self.busy[server] != -1 or not queue:
            return
        if self.hook is not None:
            candidates = tuple((op, self.durations[op]) for op in queue)
            self.callbacks += 1
            op = choose_hook(self.hook, self.now, server, candidates)
            queue.remove(op)
        elif self.policy == "fifo":
            op = queue.pop(0)
        else:
            index = min(range(len(queue)), key=lambda i: self.durations[queue[i]])
            op = queue.pop(index)
        self.busy[server] = op
        self.schedule(self.now, START, op)

    def enqueue(self, op):
        server = self.servers[op]
        self.observations.queue(self.now, self.owners[op], op, server)
        self.queues[server].append(op)
        self.grant(server)

    def run(self):
        if self.state != "built":
            raise RuntimeError("runner can only run once before close")
        self.state = "running"
        try:
            while self.heap:
                self.now, _, kind, value = heapq.heappop(self.heap)
                self.events += 1
                if kind == FEED:
                    first = value
                    jobs = self.workload.jobs
                    while value < len(jobs) and jobs[value].arrival == self.now:
                        self.observations.arrival(self.now, value)
                        value += 1
                    if value < len(jobs):
                        self.schedule(jobs[value].arrival, FEED, value)
                    for job in range(first, value):
                        self.enqueue(self.offsets[job])
                elif kind == RELEASE:
                    self.grant(value)
                else:
                    op = value
                    job, server = self.owners[op], self.servers[op]
                    if kind == START:
                        at = self.now + self.durations[op]
                        if not math.isfinite(at) or at <= self.now:
                            raise ValueError("completion time is nonfinite or does not advance")
                        self.observations.start(self.now, job, op, server)
                        self.schedule(at, END, op)
                    elif kind == END:
                        self.observations.end(self.now, job, op, server, self.durations[op])
                        self.schedule(self.now, ADVANCE, op)
                    else:
                        self.busy[server] = -1
                        self.schedule(self.now, RELEASE, server)
                        if op + 1 < self.offsets[job + 1]:
                            self.enqueue(op + 1)
                        else:
                            self.observations.finish(self.now, job)
            self.observations.flush()
            self.state = "done"
        except BaseException:
            self.state = "failed"
            raise

    def result(self):
        if self.state != "done":
            raise RuntimeError("result requires a successful run before close")
        return self.observations.result(self.events, self.callbacks)

    def close(self):
        if self.state == "running":
            raise RuntimeError("cannot close a running runner")
        if self.state != "closed":
            try:
                self.observations.close()
            finally:
                self.heap.clear()
                self.queues.clear()
                self.state = "closed"
