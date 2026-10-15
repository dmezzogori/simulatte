"""Measured optimization 1: specialize built-in waiting queues; preserve reference events."""

from collections import deque
import heapq

from .reference import START, ReferenceRunner


class TunedRunner(ReferenceRunner):
    def __init__(self, workload, **options):
        super().__init__(workload, **options)
        self.enqueue_sequence = 0
        if self.hook is None and self.policy == "fifo":
            self.queues = [deque() for _ in range(workload.servers)]

    def enqueue(self, op):
        if self.hook is not None:
            return super().enqueue(op)
        server = self.servers[op]
        self.observations.queue(self.now, self.owners[op], op, server)
        if self.policy == "fifo":
            self.queues[server].append(op)
        else:
            heapq.heappush(self.queues[server], (self.durations[op], self.enqueue_sequence, op))
            self.enqueue_sequence += 1
        self.grant(server)

    def grant(self, server):
        if self.hook is not None:
            return super().grant(server)
        queue = self.queues[server]
        if self.busy[server] != -1 or not queue:
            return
        op = queue.popleft() if self.policy == "fifo" else heapq.heappop(queue)[2]
        self.busy[server] = op
        self.schedule(self.now, START, op)
