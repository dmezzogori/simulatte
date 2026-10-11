"""Bounded durable segments and authenticated, history-free duplicate ACKs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

from simulatte._wire import MAX_SAFE_INT
from simulatte.execution.control import Control
from simulatte.execution.durability import replace_durable
from simulatte.execution.schema import _hash

_ID = re.compile(r"([0-9a-f]{16})-([0-9a-f]{16})-([0-9a-f]{8})-([0-9a-f]{64})-([0-9a-f]{64})")


class SpoolExhausted(OSError):
    code = "disk_full"
    reason = "spool_unreclaimable"


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    stream: str
    sequence: int
    offset: int
    length: int
    sha256: str
    path: Path

    def to_dict(self):
        return {
            "id": self.id,
            "stream": self.stream,
            "sequence": self.sequence,
            "offset": self.offset,
            "length": self.length,
            "sha256": self.sha256,
        }


class Reservation:
    def __init__(self, spool: Spool, path: Path, file, size: int):
        self.spool, self.path, self.file, self.size = spool, path, file, size
        self.written = 0
        self.digest = hashlib.sha256()
        self.closed = False
        self.published_path: Path | None = None

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ValueError("Reservation already closed")
        extra = max(0, self.written + len(data) - self.size)
        self.spool._grow(self, extra)
        count = self.file.write(data)
        if count != len(data):
            raise OSError("Incomplete spool write")
        self.digest.update(data)
        self.written += count
        return count

    def abort(self) -> None:
        if self.closed:
            return
        close_error = None
        try:
            self.file.close()
        except OSError as error:
            close_error = error
        self.path.unlink(missing_ok=True)
        if self.published_path is not None:
            self.published_path.unlink(missing_ok=True)
        with self.spool._condition:
            self.spool._bytes -= self.size
            self.spool._files -= 1
            self.closed = True
            self.spool._condition.notify_all()
        if close_error is not None:
            raise close_error


class Spool:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes=256 << 20,
        max_files=1024,
        max_segment_bytes=128 << 20,
        control: Control | None = None,
        clock=time.monotonic,
        ack_timeout=60,
    ):
        limits = (max_bytes, max_files, max_segment_bytes)
        if (
            any(type(value) is not int or value <= 0 for value in limits)
            or type(ack_timeout) not in (int, float)
            or not math.isfinite(ack_timeout)
            or ack_timeout <= 0
        ):
            raise ValueError("Invalid spool limit")
        if max_bytes > 256 << 20 or max_files > 1024 or max_segment_bytes > 128 << 20:
            raise ValueError("Spool limits exceed the worker protocol bounds")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.max_bytes, self.max_files = max_bytes, max_files
        self.max_segment_bytes = min(max_segment_bytes, max_bytes)
        self.control = control or Control()
        self.clock, self.ack_timeout = clock, ack_timeout
        self._condition = threading.Condition()
        self._bytes = self._files = self._peak_bytes = self._peak_files = self._waiting = 0
        self._last_ack = clock()
        self._sequence = self._acked = 0
        self._segments: dict[str, Segment] = {}
        self._secret = secrets.token_bytes(32)

    def _credit(self, size: int, files: int) -> None:
        if self.control.failure is not None:
            raise self.control.failure
        blocked_since = self.clock()
        counted = False
        try:
            while self._bytes + size > self.max_bytes or self._files + files > self.max_files:
                if not counted:
                    self._waiting += 1
                    counted = True
                self.control.check()
                if self.clock() - max(blocked_since, self._last_ack) >= self.ack_timeout:
                    raise SpoolExhausted("No durable ACK while spool quota is blocked")
                self._condition.wait(0.05)
            self._bytes += size
            self._files += files
            self._peak_bytes = max(self._peak_bytes, self._bytes)
            self._peak_files = max(self._peak_files, self._files)
        finally:
            if counted:
                self._waiting -= 1

    def reserve(self, size: int = 0) -> Reservation:
        if type(size) is not int or not 0 <= size <= self.max_segment_bytes:
            raise ValueError("Required segment exceeds spool limit")
        with self._condition:
            self._credit(size, 1)
            descriptor = None
            name = None
            try:
                descriptor, name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
                file = os.fdopen(descriptor, "wb")
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                if name is not None:
                    Path(name).unlink(missing_ok=True)
                self._bytes -= size
                self._files -= 1
                self._condition.notify_all()
                raise
            return Reservation(self, Path(name), file, size)

    def _grow(self, reservation: Reservation, extra: int) -> None:
        if reservation.size + extra > self.max_segment_bytes:
            raise ValueError("Required segment exceeds spool limit")
        with self._condition:
            self._credit(extra, 0)
            reservation.size += extra

    def _tag(self, prefix: str, digest: str) -> str:
        return hmac.digest(self._secret, (prefix + ":" + digest).encode("ascii"), "sha256").hex()

    def commit(self, reservation: Reservation, *, stream: str, offset: int) -> Segment:
        if reservation.spool is not self or reservation.closed or reservation.written != reservation.size:
            raise ValueError("Invalid spool reservation")
        if type(offset) is not int or not 0 <= offset <= MAX_SAFE_INT - reservation.size:
            raise ValueError("Invalid segment offset")
        if not isinstance(stream, str) or not stream or len(stream.encode("utf-8")) > 4096:
            raise ValueError("Invalid segment stream")
        if reservation.size == 0:
            raise ValueError("Empty segments are not published")
        destination = None
        try:
            reservation.file.flush()
            os.fsync(reservation.file.fileno())
            reservation.file.close()
            with self._condition:
                sequence = self._sequence + 1
                if sequence > MAX_SAFE_INT:
                    raise ValueError("Segment sequence exhausted")
                digest = reservation.digest.hexdigest()
                stream_hash = hashlib.sha256(stream.encode("utf-8")).hexdigest()
                prefix = f"{sequence:016x}-{offset:016x}-{reservation.size:08x}-{stream_hash}"
                identity = prefix + "-" + self._tag(prefix, digest)
                destination = self.root / (identity + ".segment")
                if destination.exists():
                    raise ValueError("Segment publication path already exists")
                reservation.published_path = destination
                replace_durable(reservation.path, destination)
                segment = Segment(identity, stream, sequence, offset, reservation.size, digest, destination)
                self._segments[identity] = segment
                self._sequence = sequence
                reservation.closed = True
                return segment
        except BaseException:
            reservation.abort()
            raise

    def ready(self) -> tuple[Segment, ...]:
        with self._condition:
            return tuple(self._segments.values())

    def ack(self, identity: str, digest: str) -> bool:
        match = _ID.fullmatch(identity) if isinstance(identity, str) else None
        try:
            _hash(digest)
        except ValueError as error:
            raise ValueError("Invalid segment ACK digest") from error
        if match is None or not hmac.compare_digest(self._tag(identity.rsplit("-", 1)[0], digest), match[5]):
            raise ValueError("Invalid segment ACK identity/digest")
        sequence = int(match[1], 16)
        with self._condition:
            if sequence <= self._acked:
                return False
            segment = self._segments.get(identity)
            if segment is None or sequence != self._acked + 1 or segment.sha256 != digest:
                raise ValueError("Unknown or out-of-order segment ACK")
            segment.path.unlink()
            del self._segments[identity]
            self._acked = sequence
            self._bytes -= segment.length
            self._files -= 1
            self._last_ack = self.clock()
            self._condition.notify_all()
            return True

    def stats(self) -> dict:
        with self._condition:
            return {
                "bytes": self._bytes,
                "files": self._files,
                "peak_bytes": self._peak_bytes,
                "peak_files": self._peak_files,
                "backpressure": self._waiting > 0,
            }
