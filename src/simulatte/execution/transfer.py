"""Cancellable segment shipping with durable fragment credit and fixed memory bounds."""

from __future__ import annotations

import threading
import time

from simulatte.execution.control import Cancelled
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.spool import Spool, Segment

_ACK_FIELDS = frozenset(("id", "offset", "length", "sha256", "segment_complete", "duplicate"))


class SegmentSender:
    """One thread ships one ordered segment at a time, with up to 16 MiB outstanding.

    Call finish only after every producer has closed. The nonblocking send callback
    may raise BufferError for temporary channel saturation. No file handle remains
    open while sending/waiting, so an immediate Windows ACK can delete the segment.
    """

    def __init__(self, spool: Spool, send, *, window=16 << 20):
        if type(window) is not int or not MAX_FRAGMENT <= window <= 16 << 20:
            raise ValueError("Invalid transfer window")
        self.spool, self.send, self.window = spool, send, window
        self._condition = threading.Condition()
        self._current: Segment | None = None
        self._sent = self._acked = self._peak = 0
        self._finished = False
        self._done = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Segment sender already started")
        self._thread = threading.Thread(target=self._run, name="simulatte-segment-sender", daemon=True)
        self._thread.start()

    def finish(self) -> None:
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is None:
            raise RuntimeError("Segment sender has not started")
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._done.wait(0.05):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Segment transfer did not finish")
        self._thread.join()
        if self._error is not None:
            raise self._error

    def stats(self) -> dict:
        with self._condition:
            return {"in_flight": self._sent - self._acked, "peak_in_flight": self._peak}

    def ack(self, body: dict) -> bool:
        if set(body) != _ACK_FIELDS or any(type(body[name]) is not bool for name in ("segment_complete", "duplicate")):
            raise ValueError("Invalid fragment ACK fields")
        _, total, retired = self.spool.receipt_info(body["id"], body["sha256"])
        offset, length = body["offset"], body["length"]
        if (
            type(offset) is not int
            or type(length) is not int
            or not 0 <= offset < total
            or offset % MAX_FRAGMENT != 0
            or length != min(MAX_FRAGMENT, total - offset)
        ):
            raise ValueError("Invalid fragment ACK extent")
        if retired:
            return False
        complete = offset + length == total
        if body["segment_complete"] != complete:
            raise ValueError("Invalid complete-segment ACK")
        with self._condition:
            segment = self._current
            if segment is None or body["id"] != segment.id or offset + length > self._sent:
                raise ValueError("ACK for an unsent segment fragment")
            if offset < self._acked:
                return False
            if offset != self._acked:
                raise ValueError("Out-of-order fragment ACK")
            if complete:
                self.spool.ack(segment.id, segment.sha256)
                self._current = None
            else:
                self.spool.note_progress(segment.id, segment.sha256)
            self._acked += length
            self._condition.notify_all()
            return True

    def _run(self):
        try:
            while True:
                self.spool.control.check()
                ready = self.spool.ready()
                if not ready:
                    with self._condition:
                        if self._finished:
                            return
                        self._condition.wait(0.05)
                    continue
                segment = ready[0]
                with self._condition:
                    self._current = segment
                    self._sent = self._acked = 0
                offset = 0
                while offset < segment.length:
                    self.spool.control.check()
                    with segment.path.open("rb") as file:
                        file.seek(offset)
                        payload = file.read(min(MAX_FRAGMENT, segment.length - offset))
                    if len(payload) != min(MAX_FRAGMENT, segment.length - offset):
                        raise ValueError("Spool segment truncated before transfer")
                    with self._condition:
                        while self._sent - self._acked + len(payload) > self.window:
                            self.spool.control.check()
                            self._condition.wait(0.05)
                        self.spool.control.check()
                        self._sent = offset + len(payload)
                        try:
                            self.send(
                                "trace_chunk" if segment.stream == "trace" else "artifact_fragment",
                                {
                                    "segment": segment.to_dict(),
                                    "fragment_offset": offset,
                                    "payload_bytes": len(payload),
                                },
                                payload=payload,
                            )
                        except BufferError:
                            self._sent = offset
                            self._condition.wait(0.01)
                            continue
                        self._peak = max(self._peak, self._sent - self._acked, len(payload))
                    offset += len(payload)
                with self._condition:
                    while self._current is not None:
                        self.spool.control.check()
                        self._condition.wait(0.05)
        except BaseException as error:
            self._error = error
            if not isinstance(error, Cancelled):
                self.spool.control.fail(error)
        finally:
            self._done.set()
