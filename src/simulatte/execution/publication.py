"""Verify private staging, then publish through one fenced SQLite visibility point."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Any

from simulatte._wire import canonical_pack, freeze
from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.schema import AttemptKey, ResolvedManifest, _hash, _mapping, _plain
from simulatte.execution.snapshot import _identity, _path as portable_path
from simulatte.execution.store import RunStore, _blob, _directory


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultProposal:
    trace_sha256: str
    simulation_manifest: Mapping[str, Any]
    resolved_manifest: ResolvedManifest
    fingerprint: Mapping[str, Any]
    artifacts: tuple[Mapping[str, Any], ...]
    final_imports: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _hash(self.trace_sha256)
        object.__setattr__(self, "final_imports", _mapping(self.final_imports))
        object.__setattr__(self, "simulation_manifest", _mapping(self.simulation_manifest))
        object.__setattr__(self, "fingerprint", _mapping(self.fingerprint))
        paths = set()
        for item in self.artifacts:
            if set(item) != {"path", "length", "sha256"}:
                raise ValueError("Invalid artifact descriptor")
            path = portable_path(item["path"])
            if path.casefold() in paths:
                raise ValueError("Duplicate/colliding artifact path")
            paths.add(path.casefold())
            if type(item["length"]) is not int or item["length"] < 0:
                raise ValueError("Invalid artifact length")
            _hash(item["sha256"])
        object.__setattr__(self, "artifacts", tuple(_mapping(dict(item)) for item in self.artifacts))


@dataclass(frozen=True, slots=True)
class PublishedResult:
    key: AttemptKey
    path: Path
    manifest: Mapping[str, Any]
    fingerprint: Mapping[str, Any]


def _copy(source: Path, output, digest) -> int:
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Artifact source is not a regular file")
    length = 0
    with source.open("rb") as file:
        opened = os.fstat(file.fileno())
        if _identity(opened) != _identity(before):
            raise ValueError("Artifact source changed before copy")
        while data := file.read(MAX_FRAGMENT):
            if output.write(data) != len(data):
                raise OSError("Incomplete artifact write")
            digest.update(data)
            length += len(data)
        after = os.fstat(file.fileno())
        if _identity(after) != _identity(before) or after.st_ctime_ns != opened.st_ctime_ns:
            raise ValueError("Artifact source changed during copy")
    if _identity(source.lstat()) != _identity(before):
        raise ValueError("Artifact source changed after copy")
    return length


def _sync(file) -> None:
    file.flush()
    os.fsync(file.fileno())


def publish(
    store: RunStore,
    key: AttemptKey,
    proposal: ResultProposal,
    *,
    execution_exited: bool,
    descendants_cleaned: bool,
    checkpoint=lambda stage: None,
) -> PublishedResult:
    """Caller must have observed child exit and successful descendant cleanup.

    Failed staging/renamed directories are intentionally left unindexed for
    recovery. No automatic retry can adopt them or overwrite immutable results.
    """
    if not execution_exited or not descendants_cleaned:
        raise ValueError("Publication requires execution exit and descendant cleanup")
    with store._transaction() as db:
        row = store._require_active(db, key)
        if row["status"] != "finalizing":
            raise ValueError("Attempt is not finalizing")
        if db.execute("SELECT 1 FROM uploads WHERE attempt=?", (key.attempt_id,)).fetchone():
            raise ValueError("Attempt has incomplete artifact uploads")
        count, first, last = db.execute(
            "SELECT COUNT(*),MIN(sequence),MAX(sequence) FROM receipts WHERE attempt=?",
            (key.attempt_id,),
        ).fetchone()
        if count and (first != 1 or last != count):
            raise ValueError("Noncontiguous segment receipts")
        streams = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT stream FROM receipts WHERE attempt=?",
                (key.attempt_id,),
            )
        }
    request = store.request(key.request_id)
    root = store.root / "staging" / key.attempt_id
    output_root = root / "publish"
    if output_root.exists() or output_root.is_symlink():
        raise ValueError("Publication staging already exists")
    _directory(output_root)
    if "trace" not in streams:
        raise ValueError("Missing trace receipt")
    expected_artifacts = {"artifact/" + str(item["path"]): item for item in proposal.artifacts}
    if set(stream for stream in streams if stream.startswith("artifact/")) - set(expected_artifacts):
        raise ValueError("Undeclared artifact stream")
    for stream, item in expected_artifacts.items():
        if stream not in streams and item["length"]:
            raise ValueError("Missing artifact stream")

    def rows(stream):
        after = 0
        extent = 0
        while True:
            with store._transaction() as db:
                store._require_active(db, key)
                batch = db.execute(
                    """SELECT * FROM receipts
                    WHERE attempt=? AND stream=? AND sequence>? ORDER BY sequence LIMIT 64""",
                    (key.attempt_id, stream, after),
                ).fetchall()
            if not batch:
                return
            for receipt in batch:
                if receipt["offset"] != extent:
                    raise ValueError("Noncontiguous segment receipts")
                extent += receipt["length"]
                after = receipt["sequence"]
                yield receipt

    def assemble(stream, destination, expected_hash, expected_length=None):
        _directory(destination.parent)
        digest = hashlib.sha256()
        length = 0
        with destination.open("xb") as file:
            for receipt in rows(stream):
                path = root / "segments" / (hashlib.sha256(receipt["id"].encode("utf-8")).hexdigest() + ".segment")
                segment_digest = hashlib.sha256()

                # Hash each immutable segment and the concatenation in the same pass.
                class Output:
                    def write(self, data):
                        digest.update(data)
                        return file.write(data)

                count = _copy(path, Output(), segment_digest)
                if count != receipt["length"] or segment_digest.hexdigest() != receipt["sha256"]:
                    raise ValueError("Artifact segment hash/length mismatch")
                length += count
            if digest.hexdigest() != expected_hash or (expected_length is not None and length != expected_length):
                raise ValueError("Artifact concatenation hash/length mismatch")
            _sync(file)
        return length

    trace_path = output_root / "run.simtrace"
    assemble("trace", trace_path, proposal.trace_sha256)
    from simulatte.trace import Trace

    trace = Trace.open(trace_path)
    trace.check()
    verification = trace.verify()
    if verification is not True and not (request.level == "kpi" and verification == "not_verifiable"):
        raise ValueError("Trace semantic fingerprint mismatch")
    if trace.outcome != "completed" or trace.level != request.level or trace.fingerprint is None:
        raise ValueError("Trace outcome/recording level mismatch")
    fingerprint = {"digest": trace.fingerprint.digest, "kpis": dict(trace.fingerprint.kpis)}
    if canonical_pack(freeze(fingerprint)) != canonical_pack(_mapping(proposal.fingerprint)):
        raise ValueError("Trace fingerprint proposal mismatch")
    if canonical_pack(trace.manifest) != canonical_pack(_mapping(proposal.simulation_manifest)):
        raise ValueError("Trace manifest proposal mismatch")
    execution: Any = trace.execution
    if execution is None or execution["header"]["request_id"] != key.request_id:
        raise ValueError("Trace execution request mismatch")
    if canonical_pack(execution["initial"]["resolved"]) != _blob(proposal.resolved_manifest.to_dict()):
        raise ValueError("Trace resolved execution manifest mismatch")
    expected_reason = "horizon" if request.stopping["type"] == "horizon" else "drained"
    if execution["footer"]["termination"] != expected_reason:
        raise ValueError("Trace execution termination mismatch")
    for name, expected in (
        ("seed", str(request.seed)),
        ("parameters", request.parameters),
        ("stopping_policy", request.stopping),
    ):
        if canonical_pack(freeze(trace.manifest.get(name))) != canonical_pack(freeze(expected)):
            raise ValueError(f"Trace requested {name} mismatch")
    for stream, item in expected_artifacts.items():
        assemble(stream, output_root / "artifacts" / item["path"], item["sha256"], item["length"])
    checkpoint("verified")
    store.require_active(key)
    manifest = {
        "schema": 1,
        "request": request.to_dict(),
        "resolved": proposal.resolved_manifest.to_dict(),
        "final_imports": _plain(proposal.final_imports),
        "simulation": _plain(proposal.simulation_manifest),
        "trace_sha256": proposal.trace_sha256,
        "verification": verification,
        "artifacts": _plain(proposal.artifacts),
    }
    with (output_root / "manifest.msgpack").open("xb") as file:
        file.write(_blob(manifest))
        _sync(file)
    sync_directory(output_root)
    checkpoint("staged")
    store.require_active(key)
    destination = store.root / "results" / key.attempt_id
    if destination.exists() or destination.is_symlink():
        raise ValueError("Immutable result already exists")
    replace_durable(output_root, destination)
    checkpoint("renamed")
    checkpoint("before_commit")
    with store._transaction() as db:
        row = store._require_active(db, key)
        if row["status"] != "finalizing":
            raise ValueError("Attempt left finalizing state")
        db.execute(
            "INSERT INTO results VALUES (?,?,?,?)", (key.attempt_id, _blob(manifest), _blob(fingerprint), time.time())
        )
        for name, value in fingerprint["kpis"].items():
            db.execute("INSERT INTO kpis VALUES (?,?,?)", (key.attempt_id, name, _blob(value)))
        for item in proposal.artifacts:
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?)", (key.attempt_id, item["path"], item["length"], item["sha256"])
            )
        db.execute("UPDATE attempts SET status='succeeded',updated=? WHERE id=?", (time.time(), key.attempt_id))
        db.execute("UPDATE slots SET active=NULL WHERE name=?", (row["slot"],))
    checkpoint("committed")
    return PublishedResult(key, destination, _mapping(manifest), _mapping(fingerprint))
