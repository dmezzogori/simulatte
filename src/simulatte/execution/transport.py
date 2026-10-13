"""Bounded bidirectional worker framing with a reserved control queue.

Reader and writer have independent threads so a saturated outbound pipe cannot
prevent inbound cancellation. The owner must still close its process/socket on
transport loss; threads never claim to make a blocked OS write interruptible.
"""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol

from simulatte.execution.protocol import FrameCodec, MAGIC, Message, encode_message
from simulatte.execution.schema import AttemptKey

if TYPE_CHECKING:
    from simulatte.execution.diagnostics import Diagnostics


def envelope(kind, body, sequence, key: AttemptKey | None):
    return {
        "v": [1, 0],
        "type": kind,
        "seq": sequence,
        "request_id": key.request_id if key else None,
        "attempt_id": key.attempt_id if key else None,
        "generation": key.generation if key else None,
        "body": body,
    }


class FramedChannel:
    CONTROL_BYTES = 4 << 20
    DATA_BYTES = 16 << 20

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self._condition = threading.Condition()
        self._out = (deque(), deque())
        self._sizes = [0, 0]
        self._incoming = deque()
        self._in_size = 0
        self._error: BaseException | None = None
        self._closed = False
        self.last_heartbeat = time.monotonic()
        self.handshake_complete = False
        self._threads = [
            threading.Thread(target=self._read, daemon=True),
            threading.Thread(target=self._write, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    @property
    def error(self) -> BaseException | None:
        """The first reader/writer failure (EOF, protocol violation, pipe error), if any."""
        with self._condition:
            return self._error

    @property
    def pending_bytes(self):
        with self._condition:
            return sum(self._sizes)

    def _fail(self, error):
        with self._condition:
            self._error = self._error or error
            self._condition.notify_all()

    def send(self, kind, body, *, key: AttemptKey | None = None, payload: bytes = b""):
        # Freeze before enqueueing so caller mutation cannot change transmitted
        # meaning. Leave sequence headroom; priority ordering assigns seq at send.
        from simulatte._wire import freeze

        body = freeze(body)
        frame = encode_message(envelope(kind, body, 0, key), payload)
        size = len(frame) + 8
        lane = int(kind in ("trace_chunk", "artifact_fragment"))
        with self._condition:
            if self._error or self._closed:
                raise OSError("Worker channel closed") from self._error
            limit = self.DATA_BYTES if lane else self.CONTROL_BYTES
            if self._sizes[lane] + size > limit or len(self._out[lane]) >= (32 if lane else 64):
                raise BufferError("Worker outbound queue is full")
            self._out[lane].append((kind, body, key, payload, size))
            self._sizes[lane] += size
            self._condition.notify_all()

    def receive(self, timeout: float = 5) -> Message:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._incoming:
                if self._error or self._closed:
                    raise OSError("Worker channel closed") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Worker receive timeout")
                self._condition.wait(remaining)
            message, size = self._incoming.popleft()
            self._in_size -= size
            return message

    def _read(self):
        codec = FrameCodec()
        read = getattr(self.reader, "read1", self.reader.read)
        try:
            while not self._closed:
                data = read(65536)
                if not data:
                    codec.finish()
                    raise EOFError("Worker stream ended")
                messages = codec.feed(data)
                with self._condition:
                    for message in messages:
                        if message.type in ("hello", "heartbeat"):
                            self.last_heartbeat = time.monotonic()
                            self.handshake_complete = True
                        size = len(encode_message(message.envelope, message.payload))
                        if self._in_size + size > self.DATA_BYTES + self.CONTROL_BYTES or len(self._incoming) >= 128:
                            raise BufferError("Worker inbound queue is full")
                        self._incoming.append((message, size))
                        self._in_size += size
                    self._condition.notify_all()
        except (OSError, ValueError, EOFError, BufferError) as error:
            self._fail(error)

    def _write_all(self, data):
        view = memoryview(data)
        while view:
            written = self.writer.write(view)
            if not written:
                raise OSError("Worker writer made no progress")
            view = view[written:]
        self.writer.flush()

    def _write(self):
        sequence = 0
        try:
            self._write_all(MAGIC)
            while True:
                with self._condition:
                    while not any(self._out):
                        if self._closed or self._error:
                            return
                        self._condition.wait()
                    lane = 0 if self._out[0] else 1
                    kind, body, key, payload, size = self._out[lane].popleft()
                self._write_all(encode_message(envelope(kind, body, sequence, key), payload))
                sequence += 1
                with self._condition:
                    self._sizes[lane] -= size
                    self._condition.notify_all()
        except (OSError, ValueError) as error:
            self._fail(error)

    def drain(self, timeout: float = 5):
        deadline = time.monotonic() + timeout
        with self._condition:
            while any(self._sizes):
                if self._error or self._closed:
                    raise OSError("Worker channel closed") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Worker send timeout")
                self._condition.wait(remaining)

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        # Process/socket ownership stays with the caller. It must unblock I/O
        # before joining, so shutdown never waits on an inherited child pipe.
        for thread in self._threads:
            thread.join(timeout=0.2)


class Transport(Protocol):
    """A coordinator-side supervisor session: framed channel, bounded diagnostics, owned process."""

    channel: FramedChannel
    diagnostics: Diagnostics

    def send(self, kind: str, body: Any, **kwargs: Any) -> None: ...

    def receive(self, timeout: float = 5) -> Message: ...

    def close(self) -> None: ...


class _PipeTransport:
    """A supervisor process speaking the framed protocol on its stdin/stdout pipes."""

    def __init__(self, process, channel):
        self.process, self.channel = process, channel
        from simulatte.execution.diagnostics import Diagnostics

        self.diagnostics = Diagnostics()

        def drain_errors():
            try:
                while data := process.stderr.read(65536):
                    self.diagnostics.append("stderr", data)
            except (OSError, ValueError):
                pass

        self._errors = threading.Thread(target=drain_errors, daemon=True)
        self._errors.start()
        self._closed = False

    def send(self, kind, body, **kwargs):
        self.channel.send(kind, body, **kwargs)

    def receive(self, timeout=5):
        remaining = (5 if self.channel.handshake_complete else 10) - (time.monotonic() - self.channel.last_heartbeat)
        if remaining <= 0:
            raise TimeoutError("Worker heartbeat lost")
        try:
            return self.channel.receive(min(timeout, remaining))
        except OSError as error:
            if isinstance(error, TimeoutError):
                raise
            raise self._closed_error() from error

    def _closed_error(self) -> OSError:
        detail = self.diagnostics.tail("stderr")[-4096:].decode("utf-8", errors="replace")
        return OSError("Worker channel closed: " + detail)

    def _stop(self):
        """Reap a process that did not exit within 5 s of shutdown and stdin EOF."""
        import subprocess

        self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # A worker stuck past SIGKILL must not take the coordinator loop down with it.
            pass

    def close(self):
        if self._closed:
            return
        import subprocess

        try:
            try:
                self.channel.send("shutdown", {})
                self.channel.drain(0.2)
            except (OSError, TimeoutError, BufferError):
                pass
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._stop()
        finally:
            self._closed = True
            self.channel.close()
            self.process.stdout.close()
            self._errors.join(timeout=0.2)
            self.process.stderr.close()


class LocalTransport(_PipeTransport):
    """Trusted supervisor process; its imports use only the frozen runtime roots."""

    @classmethod
    def start(cls, runtime, *, external_roots=()):
        import subprocess
        import sys

        roots = [str(runtime), *map(str, external_roots)]
        code = (
            "import sys,os; sys.path[:0]="
            + repr(roots)
            + "; from simulatte.cli import main; os._exit(main(['_worker']))"
        )
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        channel = FramedChannel(process.stdout, process.stdin)
        channel.send("hello", {"protocol": [1, 0]})
        return cls(process, channel)
