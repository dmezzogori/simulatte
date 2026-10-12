"""Bounded immutable JSON references and verified virtual trace byte ranges."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading

from simulatte.execution.references import MAX_DOCUMENT, _identity
from simulatte.execution.schema import _hash
from simulatte.studio.websocket import json_values


class References:
    def __init__(self, *, max_bytes=256 << 20, max_items=256):
        if type(max_bytes) is not int or type(max_items) is not int or min(max_bytes, max_items) < 1:
            raise ValueError("Reference bounds must be positive integers")
        self.max_bytes, self.max_items = max_bytes, max_items
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    @property
    def bytes(self):
        with self._lock:
            return self._bytes

    def put(self, value):
        data = bytearray()
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        for chunk in encoder.iterencode(json_values(value)):
            raw = chunk.encode("utf-8")
            if len(data) + len(raw) > min(self.max_bytes, MAX_DOCUMENT):
                raise ValueError("HTTP reference exceeds byte limit")
            data.extend(raw)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            if digest not in self._items:
                while self._items and (len(self._items) >= self.max_items or self._bytes + len(data) > self.max_bytes):
                    _, old = self._items.popitem(last=False)
                    self._bytes -= len(old)
                self._items[digest] = bytes(data)
                self._bytes += len(data)
            self._items.move_to_end(digest)
        return {"id": digest, "url": f"/api/references/{digest}", "length": len(data), "sha256": digest}

    def get(self, identity):
        with self._lock:
            value = self._items[identity]
            self._items.move_to_end(identity)
            return value


def parse_range(raw, size):
    if raw is None:
        return 0, size - 1, 200
    if not raw.startswith("bytes=") or "," in raw:
        raise ValueError("Invalid range")
    first, last = raw[6:].split("-")
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("Invalid range")
        start, end = max(0, size - suffix), size - 1
    if start < 0 or start > end or start >= size:
        raise ValueError("Invalid range")
    return start, end, 206


_verified = OrderedDict()
_verified_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class ImmutableFile:
    path: Path
    length: int
    sha256: str

    def __post_init__(self):
        _hash(self.sha256)
        if type(self.length) is not int or self.length < 0:
            raise ValueError("Invalid immutable file length")

    def _unchanged(self, file, before):
        identity, changed = before
        if self.path.resolve() != self.path or _identity(self.path.lstat()) != identity:
            raise ValueError("Immutable file path changed")
        current = os.fstat(file.fileno())
        if _identity(current) != identity or current.st_ctime_ns != changed:
            raise ValueError("Immutable file changed during read")

    @contextmanager
    def open(self):
        path = self.path
        if path.resolve() != path:
            raise ValueError("Immutable file path contains symlink components")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != self.length:
            raise ValueError("Immutable file is not regular or has wrong length")
        before = _identity(info)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as file:
            before = (before, os.fstat(file.fileno()).st_ctime_ns)
            self._unchanged(file, before)
            key = (str(path), before, self.sha256)
            with _verified_lock:
                known = key in _verified
            if not known:
                digest = hashlib.sha256()
                while data := file.read(1 << 20):
                    digest.update(data)
                self._unchanged(file, before)
                if digest.hexdigest() != self.sha256:
                    raise ValueError("Immutable file hash mismatch")
                with _verified_lock:
                    _verified[key] = True
                    while len(_verified) > 1024:
                        _verified.popitem(last=False)
            file.seek(0)
            yield file, before
            self._unchanged(file, before)


class RangeSource:
    def __init__(self, files, length, *, authorize=lambda: None):
        self.files = tuple(files)
        self.length = length
        self.authorize = authorize
        if type(length) is not int or length < 0 or sum(file.length for file in self.files) < length:
            raise ValueError("Trace prefix has a gap or invalid length")

    def _ranges(self, start, end):
        if not 0 <= start <= end < self.length:
            raise ValueError("Invalid prefix range")
        offset = 0
        for file in self.files:
            if offset > end:
                break
            if offset + file.length > start:
                yield file, max(0, start - offset), min(file.length - 1, end - offset)
            offset += file.length

    def validate(self, start, end):
        for file, _, _ in self._ranges(start, end):
            self.authorize()
            with file.open():
                pass

    def chunks(self, start, end):
        for descriptor, first, last in self._ranges(start, end):
            self.authorize()
            with descriptor.open() as (file, before):
                file.seek(first)
                remaining = last - first + 1
                while remaining:
                    self.authorize()
                    data = file.read(min(1 << 20, remaining))
                    descriptor._unchanged(file, before)
                    if not data:
                        raise ValueError("Short immutable range read")
                    self.authorize()
                    remaining -= len(data)
                    yield data
