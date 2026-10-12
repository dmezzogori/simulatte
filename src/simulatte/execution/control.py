"""Cooperative attempt controls, shared by preparation, stepping and spool waits."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time


class Cancelled(Exception):
    pass


class AttemptTimeout(Exception):
    pass


class Control:
    def __init__(self, *, deadline: float | None = None, clock: Callable[[], float] = time.monotonic):
        self.phase = "preparing"
        self.deadline = deadline
        self.clock = clock
        self.cancelled = threading.Event()
        self.ready = threading.Event()
        self.changed = threading.Condition()
        self.failure: BaseException | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self.changed:
            self.changed.notify_all()

    def ack_ready(self) -> None:
        self.ready.set()
        with self.changed:
            self.changed.notify_all()

    def fail(self, error: BaseException) -> None:
        self.failure = error
        self.cancel()

    def check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if self.cancelled.is_set():
            raise Cancelled("Attempt cancelled")
        if self.deadline is not None and self.clock() >= self.deadline:
            raise AttemptTimeout("Attempt wall deadline exceeded")

    __call__ = check

    def wait_ready(self) -> None:
        with self.changed:
            while not self.ready.is_set():
                self.check()
                self.changed.wait(0.05)
            self.check()
