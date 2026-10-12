"""Local run index with one OS owner and transactional revision/attempt fences.

No model code is imported here. Receipt and result publication extend this index;
only indexed results are visible, never an arbitrary directory found on disk.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import threading
import time
from typing import Any

from simulatte._wire import canonical_pack, freeze, unpack
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.durability import sync_directory
from simulatte.execution.schema import (
    AttemptKey,
    ExecutionRequest,
    InspectionRequest,
    Revision,
    request_id,
    inspection_id,
    _plain,
)


class StoreBusy(RuntimeError):
    pass


class RevisionConflict(ValueError):
    pass


class StaleAttempt(ValueError):
    pass


_ACTIVE = ("queued", "preparing", "ready", "running", "finalizing")
_TERMINAL = ("validation_failed", "failed", "cancelled", "lost", "superseded", "disk_full")
_DDL = (
    "CREATE TABLE revisions (number INTEGER PRIMARY KEY, digest TEXT NOT NULL, inputs BLOB NOT NULL)",
    "CREATE TABLE requests (id TEXT PRIMARY KEY, body BLOB NOT NULL)",
    """CREATE TABLE attempts (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
        revision INTEGER NOT NULL REFERENCES revisions(number), generation INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, details BLOB NOT NULL)""",
    """CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        generation INTEGER NOT NULL, active TEXT REFERENCES attempts(id))""",
    "INSERT INTO state VALUES (1, 0, NULL)",
    """CREATE TABLE snapshots (id TEXT PRIMARY KEY, kind TEXT NOT NULL, manifest BLOB NOT NULL)""",
    """CREATE TABLE receipts (attempt TEXT NOT NULL REFERENCES attempts(id), id TEXT NOT NULL,
        sequence INTEGER NOT NULL, stream TEXT NOT NULL, offset INTEGER NOT NULL,
        length INTEGER NOT NULL, sha256 TEXT NOT NULL, prefix_hash TEXT,
        PRIMARY KEY(attempt,id), UNIQUE(attempt,sequence))""",
    "CREATE INDEX receipt_stream ON receipts(attempt,stream,sequence)",
    """CREATE TABLE uploads (attempt TEXT PRIMARY KEY REFERENCES attempts(id),
        id TEXT NOT NULL, descriptor BLOB NOT NULL, written INTEGER NOT NULL)""",
    """CREATE TABLE results (attempt TEXT PRIMARY KEY REFERENCES attempts(id),
        manifest BLOB NOT NULL, fingerprint BLOB NOT NULL, created REAL NOT NULL)""",
    """CREATE TABLE kpis (attempt TEXT NOT NULL REFERENCES results(attempt),
        name TEXT NOT NULL, value BLOB NOT NULL, PRIMARY KEY(attempt,name))""",
    """CREATE TABLE artifacts (attempt TEXT NOT NULL REFERENCES results(attempt),
        path TEXT NOT NULL, length INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(attempt,path))""",
    """CREATE TABLE views (revision INTEGER PRIMARY KEY REFERENCES revisions(number),
        attempt TEXT NOT NULL REFERENCES attempts(id), layout BLOB NOT NULL)""",
)


def _acquire(file) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(file) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _blob(value: Any) -> bytes:
    return canonical_pack(freeze(value))


def _read(value: bytes) -> Any:
    return _plain(unpack(value))


def _directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Store directory is a symlink: {path.name}")
    if not path.parent.exists():
        _directory(path.parent)
    try:
        path.mkdir()
    except FileExistsError:
        if not path.is_dir():
            raise ValueError(f"Store directory is not a directory: {path.name}") from None
    else:
        sync_directory(path.parent)


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        # Detached snapshots are deliberately read-only. Recovery owns these
        # trees; make ordinary entries removable without following symlinks.
        path.chmod(0o700)
        for directory, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                item = Path(directory) / name
                if not item.is_symlink():
                    item.chmod(0o700 if item.is_dir() else 0o600)
        shutil.rmtree(path)


class RunStore:
    schema_version = 1

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self._mutex = threading.RLock()
        self._lock: Any = None
        self._db: sqlite3.Connection | None = None
        self._closed = False
        self._receiver: Any = None

    @classmethod
    def open(cls, root: Path) -> RunStore:
        store = cls(root)
        _directory(store.root)
        try:
            lock_path = store.root / "coordinator.lock"
            if lock_path.is_symlink():
                raise ValueError("Store lock is a symlink")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                file = os.fdopen(fd, "r+b", buffering=0)
            except BaseException:
                os.close(fd)
                raise
            try:
                _acquire(file)
            except OSError as error:
                file.close()
                raise StoreBusy("Another coordinator owns this .simulatte store") from error
            store._lock = file
            index = store.root / "index.sqlite"
            if index.is_symlink():
                raise ValueError("Store index is a symlink")
            store._db = db = sqlite3.connect(index, isolation_level=None, check_same_thread=False)
            db.row_factory = sqlite3.Row
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, cls.schema_version):
                raise ValueError(f"Unsupported run store schema {version}")
            db.execute("PRAGMA foreign_keys=ON")
            # This PRAGMA returns a row. Consume it before BEGIN/COMMIT;
            # PyPy need not finalize a discarded cursor immediately.
            db.execute("PRAGMA journal_mode=WAL").fetchall()
            db.execute("PRAGMA synchronous=FULL")
            if version == 0:
                with store._transaction():
                    for statement in _DDL:
                        db.execute(statement)
                    db.execute("PRAGMA user_version=1")
            for name in ("snapshots", "staging", "results"):
                _directory(store.root / name)
            store.recover()
            return store
        except BaseException:
            store.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._mutex:
            if self._closed or self._db is None:
                raise RuntimeError("Run store is closed")
            db = self._db
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def settings(self) -> dict:
        with self._mutex:
            assert self._db is not None
            return {
                name: self._db.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("journal_mode", "synchronous", "foreign_keys")
            }

    def _current_revision(self, db) -> Revision | None:
        row = db.execute("SELECT number,digest FROM revisions ORDER BY number DESC LIMIT 1").fetchone()
        return Revision(row[0], row[1]) if row is not None else None

    def current_revision(self) -> Revision | None:
        with self._transaction() as db:
            return self._current_revision(db)

    def create_revision(
        self,
        inputs: dict,
        *,
        expected: Revision | None = None,
        revoke_active: bool = False,
        view=None,
        preparation=None,
    ) -> Revision:
        if revoke_active and view is not None:
            raise ValueError("Cannot revoke an attempt and associate its presentation in one change")
        data, digest = _blob(inputs), canonical_hash("revision-inputs-v1", inputs)
        with self._transaction() as db:
            current = self._current_revision(db)
            if expected is not None and expected != current:
                raise RevisionConflict("Project revision changed; reload before editing")
            number = 1 if current is None else current.number + 1
            revision = Revision(number, digest)
            db.execute("INSERT INTO revisions VALUES (?,?,?)", (number, digest, data))
            if revoke_active:
                active = db.execute("SELECT active FROM state WHERE singleton=1").fetchone()[0]
                if active is not None:
                    db.execute(
                        "UPDATE attempts SET status='superseded',updated=?,details=? WHERE id=?",
                        (time.time(), _blob({"message": "Inputs changed"}), active),
                    )
                    db.execute("UPDATE state SET active=NULL WHERE singleton=1")
            if view is not None:
                self._put_view(db, revision, *view)
            if preparation is not None:
                request = ExecutionRequest.from_dict(preparation["request"])
                if request.layout_revision != number:
                    raise RevisionConflict("Preparation does not match the new revision")
                db.execute(
                    "INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                    (request_id(request), "preparation", _blob(preparation)),
                )
            return revision

    def _put_view(self, db, revision, key, bundle):
        bundle.check()
        row = db.execute(
            "SELECT status FROM attempts WHERE id=? AND request_id=? AND generation=?",
            (key.attempt_id, key.request_id, key.generation),
        ).fetchone()
        if row is None:
            raise StaleAttempt("View assignment does not exist")
        if row[0] == "succeeded":
            if db.execute("SELECT 1 FROM results WHERE attempt=?", (key.attempt_id,)).fetchone() is None:
                raise ValueError("View requires an indexed simulation result")
        else:
            self._require_active(db, key)
            if row[0] not in ("ready", "running", "finalizing"):
                raise ValueError("View requires a durable ready bundle")
        db.execute(
            "INSERT OR REPLACE INTO views VALUES (?,?,?)", (revision.number, key.attempt_id, _blob(bundle.to_dict()))
        )

    def bind_view(self, revision, key, bundle):
        with self._transaction() as db:
            if revision != self._current_revision(db):
                raise RevisionConflict("Project revision changed before view binding")
            self._put_view(db, revision, key, bundle)

    def bind_ready_view(self, key, bundle):
        """Associate the first verified INITIAL only with its assigned inputs."""
        with self._transaction() as db:
            self._require_active(db, key)
            number = db.execute("SELECT revision FROM attempts WHERE id=?", (key.attempt_id,)).fetchone()[0]
            revision = self._current_revision(db)
            if revision is None or revision.number != number:
                raise StaleAttempt("Ready bundle belongs to obsolete inputs")
            self._put_view(db, revision, key, bundle)

    def presentation_view(self, *, revision: Revision | None = None, committed_only=False):
        from simulatte.execution.revisions import PresentationView
        from simulatte.layout.bundle import LayoutBundle

        with self._transaction() as db:
            row = db.execute(
                "SELECT v.revision,r.digest,a.id,a.request_id,a.generation,v.layout,"
                "result.attempt IS NOT NULL AS committed FROM views v "
                "JOIN revisions r ON r.number=v.revision JOIN attempts a ON a.id=v.attempt "
                "JOIN state s ON s.singleton=1 LEFT JOIN results result ON result.attempt=a.id "
                "WHERE ((a.status='succeeded' AND result.attempt IS NOT NULL) OR "
                "(?=0 AND s.active=a.id AND s.generation=a.generation "
                "AND a.status IN ('ready','running','finalizing'))) "
                "AND (? IS NULL OR (v.revision=? AND r.digest=?)) ORDER BY v.revision DESC LIMIT 1",
                (
                    int(committed_only),
                    None if revision is None else revision.number,
                    None if revision is None else revision.number,
                    None if revision is None else revision.digest,
                ),
            ).fetchone()
        if row is None:
            return None
        return PresentationView(
            Revision(row[0], row[1]),
            AttemptKey(row[3], row[2], row[4]),
            LayoutBundle.from_dict(_read(row[5])),
            bool(row[6]),
        )

    def assign(self, request: ExecutionRequest, revision: Revision) -> AttemptKey:
        return self._assign(request_id(request), request.to_dict(), revision)

    def assign_inspection(self, request: InspectionRequest, revision: Revision) -> AttemptKey:
        return self._assign(inspection_id(request), request.to_dict(), revision)

    def _assign(self, identity: str, body: dict, revision: Revision) -> AttemptKey:
        with self._transaction() as db:
            if revision != self._current_revision(db):
                raise RevisionConflict("Cannot assign an obsolete revision")
            row = db.execute("SELECT generation,active FROM state WHERE singleton=1").fetchone()
            key = AttemptKey(identity, secrets.token_hex(16), row[0] + 1)
            now = time.time()
            if row[1] is not None:
                db.execute("UPDATE attempts SET status='superseded',updated=? WHERE id=?", (now, row[1]))
            db.execute("INSERT OR IGNORE INTO requests VALUES (?,?)", (identity, _blob(body)))
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
                (
                    key.attempt_id,
                    identity,
                    revision.number,
                    key.generation,
                    "queued",
                    now,
                    now,
                    _blob({}),
                ),
            )
            db.execute("UPDATE state SET generation=?,active=? WHERE singleton=1", (key.generation, key.attempt_id))
            return key

    def _require_active(self, db, key: AttemptKey):
        row = db.execute(
            """SELECT a.* FROM attempts a JOIN state s ON s.active=a.id
            WHERE a.id=? AND a.request_id=? AND a.generation=? AND s.generation=?""",
            (key.attempt_id, key.request_id, key.generation, key.generation),
        ).fetchone()
        if row is None or row["status"] not in _ACTIVE:
            raise StaleAttempt("Attempt assignment was revoked or replaced")
        return row

    def require_active(self, key: AttemptKey) -> None:
        with self._transaction() as db:
            self._require_active(db, key)

    def transition(self, key: AttemptKey, status: str, *, details: dict | None = None) -> None:
        with self._transaction() as db:
            row = self._require_active(db, key)
            index = _ACTIVE.index(row["status"])
            if index + 1 >= len(_ACTIVE) or status != _ACTIVE[index + 1]:
                raise ValueError("Invalid attempt state transition")
            db.execute(
                "UPDATE attempts SET status=?,updated=?,details=? WHERE id=?",
                (status, time.time(), _blob(details or {}), key.attempt_id),
            )

    def revoke(self, key: AttemptKey, reason: str, *, details: dict | None = None) -> None:
        if reason not in _TERMINAL:
            raise ValueError("Invalid terminal attempt state")
        with self._transaction() as db:
            self._require_active(db, key)
            db.execute(
                "UPDATE attempts SET status=?,updated=?,details=? WHERE id=?",
                (reason, time.time(), _blob(details or {}), key.attempt_id),
            )
            db.execute("UPDATE state SET active=NULL WHERE singleton=1")

    def attempt(self, key: AttemptKey) -> dict:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM attempts WHERE id=? AND request_id=? AND generation=?",
                (key.attempt_id, key.request_id, key.generation),
            ).fetchone()
            if row is None:
                raise KeyError(key.attempt_id)
            result = dict(row)
            result["details"] = _read(result["details"])
            return result

    def request(self, identity: str) -> ExecutionRequest:
        with self._transaction() as db:
            row = db.execute("SELECT body FROM requests WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            request = ExecutionRequest.from_dict(_read(row[0]))
            if request_id(request) != identity:
                raise ValueError("Stored execution request identity mismatch")
            return request

    def inspection_request(self, identity: str) -> InspectionRequest:
        with self._transaction() as db:
            row = db.execute("SELECT body FROM requests WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            request = InspectionRequest.from_dict(_read(row[0]))
            if inspection_id(request) != identity:
                raise ValueError("Stored inspection request identity mismatch")
            return request

    def complete_inspection(
        self,
        key: AttemptKey,
        reference: dict,
        *,
        execution_exited: bool,
        descendants_cleaned: bool,
        document: dict | None = None,
    ) -> None:
        from simulatte.execution.receipts import validate_descriptor

        if not execution_exited or not descendants_cleaned:
            raise ValueError("Inspection completion requires exit and cleanup")
        reference = validate_descriptor(reference)
        if reference["stream"] != "metadata/model-info" or reference["offset"] != 0:
            raise ValueError("Invalid inspection metadata reference")
        self.inspection_request(key.request_id)
        if document is not None:
            from simulatte.execution.references import read_document

            read_document(document, "model-info")
        with self._transaction() as db:
            row = self._require_active(db, key)
            if row["status"] != "preparing":
                raise ValueError("Inspection is not preparing")
            receipt = db.execute(
                "SELECT * FROM receipts WHERE attempt=? AND id=?", (key.attempt_id, reference["id"])
            ).fetchone()
            if receipt is None or any(receipt[name] != value for name, value in reference.items()):
                raise ValueError("Inspection metadata receipt mismatch")
            if db.execute("SELECT 1 FROM uploads WHERE attempt=?", (key.attempt_id,)).fetchone():
                raise ValueError("Inspection has incomplete metadata uploads")
            db.execute(
                "UPDATE attempts SET status='succeeded',updated=?,details=? WHERE id=?",
                (
                    time.time(),
                    _blob({"kind": "inspection", "metadata": reference, "document": document}),
                    key.attempt_id,
                ),
            )
            db.execute("UPDATE state SET active=NULL WHERE singleton=1")

    def result(self, attempt_id: str):
        from simulatte.execution.publication import PublishedResult
        from simulatte.execution.schema import _mapping

        with self._transaction() as db:
            row = db.execute(
                """SELECT r.*,a.request_id,a.generation FROM results r
                JOIN attempts a ON a.id=r.attempt WHERE r.attempt=?""",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            key = AttemptKey(row["request_id"], row["attempt"], row["generation"])
            return PublishedResult(
                key,
                self.root / "results" / key.attempt_id,
                _mapping(_read(row["manifest"])),
                _mapping(_read(row["fingerprint"])),
            )

    def recover(self) -> None:
        """Only called by the exclusive owner before scheduling work."""
        with self._transaction() as db:
            db.execute(
                "UPDATE attempts SET status='lost',updated=? WHERE status IN (?,?,?,?,?)", (time.time(), *_ACTIVE)
            )
            db.execute("UPDATE state SET active=NULL WHERE singleton=1")
            indexed = {row[0] for row in db.execute("SELECT attempt FROM results")}
        for path in (self.root / "staging").iterdir():
            _remove(path)
        for path in (self.root / "results").iterdir():
            if path.name not in indexed:
                _remove(path)

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            try:
                if self._db is not None:
                    self._db.close()
            finally:
                if self._lock is not None:
                    try:
                        _release(self._lock)
                    finally:
                        self._lock.close()

    def __enter__(self) -> RunStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
