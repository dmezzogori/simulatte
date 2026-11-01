"""Queue-specialized compact control, retaining the independent event model."""

from collections import deque
import heapq

from .reference import ReferenceStream, START_EVENT
from .recording import QUEUE


class TunedStream(ReferenceStream):
    def __init__(self, servers, **options):
        super().__init__(servers, **options)
        self.enqueue_sequence = 0
        if self.policy == "fifo":
            self.queues = [deque() for _ in range(servers)]

    def _enqueue(self, op):
        server = self.routes[op]
        self.queued[op] = self.now
        self._record(QUEUE, self.owners[op], op, server)
        if self.policy == "fifo":
            self.queues[server].append(op)
        else:
            heapq.heappush(self.queues[server], (self.durations[op], self.enqueue_sequence, op))
            self.enqueue_sequence += 1
        self._grant(server)

    def _grant(self, server):
        queue = self.queues[server]
        if self.busy_server[server] or not queue:
            return
        op = queue.popleft() if self.policy == "fifo" else heapq.heappop(queue)[2]
        self.busy_server[server] = True
        self._schedule(self.now, START_EVENT, op)
