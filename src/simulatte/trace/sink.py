"""Logical-boundary trace outputs; importing a file sink needs no worker machinery."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simulatte.execution.spool import Spool, Segment, Reservation


class TraceSink(Protocol):
    pending_limit: int | None

    def write(self, data: bytes) -> int: ...
    def tell(self) -> int: ...
    def commit(self) -> object: ...
    def close(self) -> None: ...
    def abort_waits(self, error: BaseException) -> None: ...


class FileSink:
    pending_limit: int | None = None

    def __init__(self, path: str | Path):
        self.file = open(path, "wb")

    def write(self, data: bytes) -> int:
        return self.file.write(data)

    def tell(self) -> int:
        return self.file.tell()

    def commit(self) -> None:
        self.file.flush()

    def close(self) -> None:
        self.file.close()

    def abort_waits(self, error: BaseException) -> None:
        pass


class SegmentSink:
    pending_limit: int | None = 64 << 20

    def __init__(self, spool: Spool, *, stream="trace"):
        self.spool, self.stream = spool, stream
        self._reservation: Reservation | None = None
        self._offset = 0
        self._closed = False

    def write(self, data: bytes) -> int:
        if self._closed:
            raise ValueError("Trace sink is closed")
        if not data:
            return 0
        try:
            if self._reservation is None:
                self._reservation = self.spool.reserve()
            return self._reservation.write(data)
        except BaseException:
            if self._reservation is not None:
                self._reservation.abort()
                self._reservation = None
            raise

    def tell(self) -> int:
        return self._offset + (self._reservation.written if self._reservation else 0)

    def commit(self) -> Segment | None:
        if self._closed:
            raise ValueError("Trace sink is closed")
        if self._reservation is None:
            return None
        reservation = self._reservation
        try:
            segment = self.spool.commit(reservation, stream=self.stream, offset=self._offset)
        except BaseException:
            try:
                reservation.abort()
            finally:
                if reservation.closed:
                    self._reservation = None
            raise
        self._reservation = None
        self._offset += segment.length
        return segment

    def close(self) -> None:
        if self._reservation is not None:
            self._reservation.abort()
            self._reservation = None
        self._closed = True

    def abort_waits(self, error: BaseException) -> None:
        self.spool.control.fail(error)
