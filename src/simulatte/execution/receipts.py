"""Durable receiver credit; complete segment receipts alone expose trace prefixes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import threading

from simulatte._wire import MAX_SAFE_INT
from simulatte.execution.durability import replace_durable
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.schema import AttemptKey, _hash
from simulatte.execution.snapshot import _path as portable_path
from simulatte.execution.store import RunStore, _blob, _read, _directory

_FIELDS = frozenset(("id", "stream", "sequence", "offset", "length", "sha256"))


@dataclass(frozen=True, slots=True)
class FragmentReceipt:
    id: str
    offset: int
    length: int
    sha256: str
    segment_complete: bool
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class Prefix:
    key: AttemptKey
    generation: int
    length: int
    sha256: str
    path: Path


def validate_descriptor(data: dict) -> dict:
    if set(data) != _FIELDS:
        raise ValueError("Invalid segment descriptor fields")
    for name in ("id", "stream"):
        if type(data[name]) is not str or not data[name] or len(data[name].encode("utf-8")) > 4096:
            raise ValueError("Invalid segment descriptor text")
    for name in ("sequence", "offset", "length"):
        if type(data[name]) is not int or not 0 <= data[name] <= MAX_SAFE_INT:
            raise ValueError("Invalid segment descriptor integer")
    if data["sequence"] == 0 or not 0 < data["length"] <= 128 << 20:
        raise ValueError("Invalid segment descriptor size/sequence")
    if data["offset"] + data["length"] > MAX_SAFE_INT:
        raise ValueError("Invalid segment descriptor extent")
    _hash(data["sha256"])
    stream = data["stream"]
    if stream != "trace":
        portable_path(stream)
        if not (
            stream.startswith("artifact/")
            or stream
            in (
                "metadata/resolved-manifest",
                "metadata/model-info",
                "metadata/result-proposal",
            )
        ):
            raise ValueError("Invalid artifact stream path")
    return dict(data)


def _digest_file(path: Path, *, limit: int | None = None):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        remaining = limit
        while remaining is None or remaining > 0:
            part = file.read(MAX_FRAGMENT if remaining is None else min(MAX_FRAGMENT, remaining))
            if not part:
                if remaining:
                    raise ValueError("Truncated trace prefix")
                break
            digest.update(part)
            if remaining is not None:
                remaining -= len(part)
    return digest


class SegmentReceiver:
    """One receiver per attempt; data I/O does not hold the store's fencing lock.

    Fragments are canonical 1 MiB pieces except the final piece. A fragment ACK
    returns transfer-window credit only. Only segment_complete returns spool
    credit. This supports 128 MiB segments without deadlocking a 16 MiB window.
    """

    def __init__(self, store: RunStore, key: AttemptKey):
        with store._mutex:
            store.require_active(key)
            if store._receiver is not None and store._receiver.key == key:
                raise ValueError("Attempt already has a segment receiver")
            self.store, self.key = store, key
            self.root = store.root / "staging" / key.attempt_id
            _directory(self.root)
            _directory(self.root / "segments")
            self._mutex = threading.Lock()
            prefix = self.prefix()
            self._prefix_hash = hashlib.sha256() if prefix is None else _digest_file(prefix.path, limit=prefix.length)
            store._receiver = self

    def _path(self, identity: str) -> Path:
        return self.root / "segments" / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".segment")

    def prefix(self) -> Prefix | None:
        with self.store._transaction() as db:
            self.store._require_active(db, self.key)
            row = db.execute(
                """SELECT sequence,offset+length,prefix_hash FROM receipts
                WHERE attempt=? AND stream='trace' ORDER BY sequence DESC LIMIT 1""",
                (self.key.attempt_id,),
            ).fetchone()
            return None if row is None else Prefix(self.key, row[0], row[1], row[2], self.root / "trace-prefix")

    def segment_path(self, identity: str) -> Path:
        with self.store._transaction() as db:
            self.store._require_active(db, self.key)
            row = db.execute(
                "SELECT id FROM receipts WHERE attempt=? AND id=?", (self.key.attempt_id, identity)
            ).fetchone()
            if row is None:
                raise KeyError(identity)
        return self._path(identity)

    def receive(self, descriptor: dict, offset: int, payload: bytes) -> FragmentReceipt:
        descriptor = validate_descriptor(descriptor)
        if (
            type(offset) is not int
            or offset < 0
            or offset >= descriptor["length"]
            or offset % MAX_FRAGMENT != 0
            or type(payload) is not bytes
            or len(payload) != min(MAX_FRAGMENT, descriptor["length"] - offset)
        ):
            raise ValueError("Invalid fragment offset/length")
        with self._mutex:
            return self._receive(descriptor, offset, payload)

    def _writable(self, db) -> None:
        row = self.store._require_active(db, self.key)
        if row["status"] == "finalizing":
            raise ValueError("Attempt is finalizing; segment writes are closed")

    def _receive(self, desc: dict, offset: int, payload: bytes) -> FragmentReceipt:
        identity, end = desc["id"], offset + len(payload)
        receipt = FragmentReceipt(identity, offset, len(payload), desc["sha256"], end == desc["length"])
        part = self.root / "upload.part"
        with self.store._transaction() as db:
            self._writable(db)
            complete = db.execute(
                "SELECT * FROM receipts WHERE attempt=? AND id=?", (self.key.attempt_id, identity)
            ).fetchone()
            upload = db.execute("SELECT * FROM uploads WHERE attempt=?", (self.key.attempt_id,)).fetchone()
            if complete is not None:
                if {name: complete[name] for name in _FIELDS} != desc:
                    raise ValueError("Conflicting duplicate segment descriptor")
                self._compare(self._path(identity), offset, payload)
                return FragmentReceipt(identity, offset, len(payload), desc["sha256"], True, True)
            if upload is not None:
                if _read(upload["descriptor"]) != desc:
                    raise ValueError("Conflicting active segment descriptor")
                if offset < upload["written"]:
                    self._compare(part, offset, payload)
                    return FragmentReceipt(identity, offset, len(payload), desc["sha256"], False, True)
                if offset != upload["written"]:
                    raise ValueError("Fragment gap")
            else:
                previous = db.execute(
                    "SELECT COALESCE(MAX(sequence),0) FROM receipts WHERE attempt=?", (self.key.attempt_id,)
                ).fetchone()[0]
                extent = db.execute(
                    "SELECT COALESCE(SUM(length),0) FROM receipts WHERE attempt=? AND stream=?",
                    (self.key.attempt_id, desc["stream"]),
                ).fetchone()[0]
                if offset != 0 or desc["sequence"] != previous + 1 or desc["offset"] != extent:
                    raise ValueError("Segment gap or out-of-order fragment")
        candidate = self.root / "upload.new" if upload is None else part
        if candidate.is_symlink():
            raise ValueError("Upload path is a symlink")
        with candidate.open("w+b" if upload is None else "r+b") as file:
            file.seek(offset)
            file.truncate(offset)
            if file.write(payload) != len(payload):
                raise OSError("Incomplete fragment write")
            file.flush()
            os.fsync(file.fileno())
        self.store.require_active(self.key)
        if upload is None:
            replace_durable(candidate, part)
        if not receipt.segment_complete:
            with self.store._transaction() as db:
                self._writable(db)
                db.execute(
                    "INSERT OR REPLACE INTO uploads VALUES (?,?,?,?)", (self.key.attempt_id, identity, _blob(desc), end)
                )
            return receipt
        if _digest_file(part).hexdigest() != desc["sha256"]:
            raise ValueError("Segment content hash mismatch")
        destination = self._path(identity)
        if destination.exists():
            raise ValueError("Unindexed segment already exists")
        replace_durable(part, destination)
        prefix_hash = None
        next_hash = self._prefix_hash.copy()
        if desc["stream"] == "trace":
            target = self.root / "trace-prefix"
            if target.is_symlink():
                raise ValueError("Trace prefix is a symlink")
            temporary = self.root / "prefix.new"
            if temporary.is_symlink():
                raise ValueError("Trace prefix temporary path is a symlink")
            first = desc["offset"] == 0
            with (temporary if first else target).open("w+b" if first else "r+b") as output:
                output.seek(desc["offset"])
                output.truncate(desc["offset"])
                with destination.open("rb") as source:
                    while data := source.read(MAX_FRAGMENT):
                        if output.write(data) != len(data):
                            raise OSError("Incomplete trace prefix write")
                        next_hash.update(data)
                output.flush()
                os.fsync(output.fileno())
            if first:
                replace_durable(temporary, target)
            prefix_hash = next_hash.hexdigest()
        with self.store._transaction() as db:
            self._writable(db)
            db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.key.attempt_id,
                    identity,
                    desc["sequence"],
                    desc["stream"],
                    desc["offset"],
                    desc["length"],
                    desc["sha256"],
                    prefix_hash,
                ),
            )
            db.execute("DELETE FROM uploads WHERE attempt=?", (self.key.attempt_id,))
        if prefix_hash is not None:
            self._prefix_hash = next_hash
        return receipt

    @staticmethod
    def _compare(path: Path, offset: int, payload: bytes) -> None:
        with path.open("rb") as file:
            file.seek(offset)
            if file.read(len(payload)) != payload:
                raise ValueError("Conflicting duplicate fragment bytes")
