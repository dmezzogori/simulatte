"""Opt-in owned numeric callback data; the default tuple contract is unchanged."""

from array import array
import operator

from .reference import START, ReferenceRunner


def buffer_fifo(now, server, ids, durations):
    return ids[0]


def buffer_spt(now, server, ids, durations):
    return ids[min(range(len(ids)), key=durations.__getitem__)]


class BufferReferenceRunner(ReferenceRunner):
    """Equivalent owned-buffer policy boundary in the compact Python baseline."""

    def grant(self, server):
        queue = self.queues[server]
        if self.busy[server] != -1 or not queue:
            return
        if self.hook is None:
            return super().grant(server)
        ids = memoryview(array("I", queue).tobytes()).cast("I")
        lengths = memoryview(array("d", (self.durations[op] for op in queue)).tobytes()).cast("d")
        self.callbacks += 1
        choice = self.hook(self.now, server, ids, lengths)
        if isinstance(choice, bool):
            raise TypeError("policy returned bool instead of operation ID")
        choice = operator.index(choice)
        if choice not in ids:
            raise ValueError("policy returned an operation outside its candidates")
        queue.remove(choice)
        self.busy[server] = choice
        self.schedule(self.now, START, choice)
