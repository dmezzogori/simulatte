"""Run the trusted actor on a socket pair; execution remains in real children."""

import socket
import threading

from simulatte.execution.supervisor import Supervisor
from simulatte.execution.transport import FramedChannel


class WorkerHarness:
    def __init__(self):
        self.left, self.right = socket.socketpair()
        self.local = self.left.makefile("rwb", buffering=0)
        self.remote = self.right.makefile("rwb", buffering=0)
        self.channel = FramedChannel(self.local, self.local)
        self.worker_channel = FramedChannel(self.remote, self.remote)
        self.worker = Supervisor(self.worker_channel)
        self.thread = threading.Thread(target=self.worker.run_stdio)
        self.thread.start()
        self.channel.send("hello", {})

    def send(self, *args, **kwargs):
        self.channel.send(*args, **kwargs)

    def receive(self, timeout=5):
        return self.channel.receive(timeout)

    def close(self):
        self.channel.send("shutdown", {})
        self.channel.drain(1)
        self.thread.join(5)
        assert not self.thread.is_alive()
        self.left.shutdown(socket.SHUT_RDWR)
        self.channel.close()
        self.local.close()
        self.remote.close()
        self.left.close()
        self.right.close()
