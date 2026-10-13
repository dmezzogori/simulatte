"""Local run index with one OS owner and transactional revision/attempt fences.

No model code is imported here. Receipt and result publication extend this index;
only indexed results are visible, never an arbitrary directory found on disk.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
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
    content_id,
    request_id,
    inspection_id,
    _hash,
    _plain,
)


class StoreBusy(RuntimeError):
    pass


class RevisionConflict(ValueError):
    pass


class StaleAttempt(ValueError):
    pass


_WINDOWS = os.name == "nt"
_ACTIVE = ("queued", "preparing", "ready", "running", "finalizing")
_TERMINAL = ("validation_failed", "failed", "cancelled", "lost", "superseded", "disk_full")
INTERACTIVE = "interactive"
# D97: a replication whose attempt was lost is dispatched again at most twice.
MAX_DISPATCHES = 3
_REPLICATION = {
    "pending": ("reused", "cancelled"),
    "dispatched": ("succeeded", "failed", "cancelled", "lost"),
    # A lost replication is queued again within the bound, or cancelled with its run.
    "lost": ("pending", "cancelled"),
    "succeeded": (),
    "failed": (),
    "cancelled": (),
    "reused": (),
}
# `completed` needs one succeeded or reused replication (a run of reused results only
# completes); `failed` is a run whose replications all ended without a result.
_RUN = {
    "running": ("completed", "failed", "cancelled", "paused"),
    "paused": ("running", "cancelled"),
    "completed": (),
    "failed": (),
    "cancelled": (),
}
_SNAPSHOT = re.compile("[0-9a-f]{64}")
# Schema 2 tables shared by fresh stores and the 1 -> 2 migration. `state` only
# allocates unique generations; each slot fences its own active attempt.
_STATE = """CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL)"""
_SCHEMA_2 = (
    """CREATE TABLE slots (name TEXT PRIMARY KEY, kind TEXT NOT NULL, worker TEXT,
        active TEXT REFERENCES attempts(id), generation INTEGER NOT NULL)""",
    "CREATE INDEX request_content ON requests(content_id)",
    """CREATE TABLE experiments (id TEXT PRIMARY KEY, name TEXT NOT NULL, definition BLOB NOT NULL,
        created REAL NOT NULL)""",
    """CREATE TABLE experiment_runs (id TEXT PRIMARY KEY, experiment TEXT NOT NULL REFERENCES experiments(id),
        preparation BLOB NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
        details BLOB NOT NULL)""",
    """CREATE TABLE configurations (run TEXT NOT NULL REFERENCES experiment_runs(id), idx INTEGER NOT NULL,
        label TEXT NOT NULL, parameters BLOB NOT NULL, layout_hash TEXT NOT NULL, layout_ref BLOB NOT NULL,
        PRIMARY KEY(run,idx))""",
    """CREATE TABLE replications (run TEXT NOT NULL, configuration INTEGER NOT NULL, idx INTEGER NOT NULL,
        seed TEXT NOT NULL, worker_kind TEXT, request_id TEXT REFERENCES requests(id), status TEXT NOT NULL,
        attempt TEXT REFERENCES attempts(id), dispatches INTEGER NOT NULL,
        PRIMARY KEY(run,configuration,idx), FOREIGN KEY(run,configuration) REFERENCES configurations(run,idx))""",
    "CREATE INDEX replication_attempt ON replications(attempt)",
    """CREATE TABLE replays (request_id TEXT PRIMARY KEY, original TEXT NOT NULL REFERENCES requests(id),
        attempt TEXT REFERENCES attempts(id), status TEXT NOT NULL, verified INTEGER, details BLOB NOT NULL)""",
)
_DDL = (
    "CREATE TABLE revisions (number INTEGER PRIMARY KEY, digest TEXT NOT NULL, inputs BLOB NOT NULL)",
    # Columns added by schema 2 come last, in the order the migration appends them.
    "CREATE TABLE requests (id TEXT PRIMARY KEY, body BLOB NOT NULL, content_id TEXT)",
    """CREATE TABLE attempts (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
        revision INTEGER NOT NULL REFERENCES revisions(number), generation INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, details BLOB NOT NULL,
        slot TEXT NOT NULL DEFAULT 'interactive', worker TEXT)""",
    _STATE,
    "INSERT INTO state VALUES (1, 0)",
    *_SCHEMA_2,
    "INSERT INTO slots VALUES ('interactive', 'local', NULL, NULL, 0)",
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


def _migrate_1(db) -> None:
    """Schema 1 -> 2: the single active attempt becomes the interactive slot."""
    for statement in (
        "ALTER TABLE requests ADD COLUMN content_id TEXT",
        "ALTER TABLE attempts ADD COLUMN slot TEXT NOT NULL DEFAULT 'interactive'",
        "ALTER TABLE attempts ADD COLUMN worker TEXT",
        *_SCHEMA_2,
        "INSERT INTO slots SELECT 'interactive','local',NULL,active,generation FROM state WHERE singleton=1",
        _STATE.replace("TABLE state", "TABLE state_2"),
        "INSERT INTO state_2 SELECT singleton,generation FROM state",
        "DROP TABLE state",
        "ALTER TABLE state_2 RENAME TO state",
    ):
        db.execute(statement)
    for identity, body in db.execute("SELECT id,body FROM requests").fetchall():
        data = _read(body)
        if data.get("kind") != "inspection":
            db.execute(
                "UPDATE requests SET content_id=? WHERE id=?",
                (content_id(ExecutionRequest.from_dict(data)), identity),
            )


_MIGRATIONS = {1: _migrate_1}


def experiment_run_id(experiment_id: str, content_ids) -> str:
    """Run identity over the definition and its requests' content, not the project revision (D103)."""
    return canonical_hash("experiment-run-v1", {"experiment_id": experiment_id, "content_ids": sorted(content_ids)})


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
    schema_version = 2

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
            if _WINDOWS:
                # The OS owner lock already guarantees one connection. Windows
                # can release a dead owner's locks before its SHM mapping;
                # SQLite then fails to truncate SHM during WAL recovery. Set
                # EXCLUSIVE before any reads to use SQLite's heap WAL index.
                # https://sqlite.org/wal.html#use_of_wal_without_shared_memory
                db.execute("PRAGMA locking_mode=EXCLUSIVE").fetchall()
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, *_MIGRATIONS, cls.schema_version):
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
                    db.execute(f"PRAGMA user_version={cls.schema_version}")
            elif version != cls.schema_version:
                # Forward migrations run in one transaction, including the version bump.
                with store._transaction():
                    for step in range(version, cls.schema_version):
                        _MIGRATIONS[step](db)
                    db.execute(f"PRAGMA user_version={cls.schema_version}")
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
                # Experiment slots run at their pinned revision; only interactive work is superseded.
                active = db.execute("SELECT active FROM slots WHERE name=?", (INTERACTIVE,)).fetchone()[0]
                if active is not None:
                    db.execute(
                        "UPDATE attempts SET status='superseded',updated=?,details=? WHERE id=?",
                        (time.time(), _blob({"message": "Inputs changed"}), active),
                    )
                    db.execute("UPDATE slots SET active=NULL WHERE name=?", (INTERACTIVE,))
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
            "SELECT status,slot FROM attempts WHERE id=? AND request_id=? AND generation=?",
            (key.attempt_id, key.request_id, key.generation),
        ).fetchone()
        if row is None:
            raise StaleAttempt("View assignment does not exist")
        if row[1] != INTERACTIVE:
            raise ValueError("Presentation views bind only interactive attempts")
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
                "JOIN slots s ON s.name=a.slot LEFT JOIN results result ON result.attempt=a.id "
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

    def assign(
        self,
        request: ExecutionRequest,
        revision: Revision,
        *,
        slot: str = INTERACTIVE,
        worker: str | None = None,
        pinned: bool = False,
    ) -> AttemptKey:
        """Make a new attempt its slot's active assignment, superseding the slot's previous one.

        ``pinned`` assignments run at an existing, possibly old, revision (experiments
        and replays); the interactive slot always requires the current revision.
        """
        return self._assign(
            request_id(request),
            request.to_dict(),
            revision,
            slot=slot,
            worker=worker,
            pinned=pinned,
            content=content_id(request),
        )

    def assign_inspection(self, request: InspectionRequest, revision: Revision) -> AttemptKey:
        return self._assign(inspection_id(request), request.to_dict(), revision)

    def _assign(
        self,
        identity: str,
        body: dict,
        revision: Revision,
        *,
        slot: str = INTERACTIVE,
        worker: str | None = None,
        pinned: bool = False,
        content: str | None = None,
    ) -> AttemptKey:
        if type(slot) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", slot) is None:
            raise ValueError("Invalid slot name")
        if slot == INTERACTIVE and pinned:
            raise ValueError("The interactive slot always runs at the current revision")
        if slot == INTERACTIVE and worker is not None:
            raise ValueError("The interactive slot has no remote worker")
        with self._transaction() as db:
            if not pinned:
                if revision != self._current_revision(db):
                    raise RevisionConflict("Cannot assign an obsolete revision")
            elif (
                db.execute(
                    "SELECT 1 FROM revisions WHERE number=? AND digest=?", (revision.number, revision.digest)
                ).fetchone()
                is None
            ):
                raise RevisionConflict("Pinned revision does not exist")
            db.execute(
                "INSERT OR IGNORE INTO slots VALUES (?,?,?,NULL,0)",
                (slot, "local" if worker is None else "remote", worker),
            )
            owner, active = db.execute("SELECT worker,active FROM slots WHERE name=?", (slot,)).fetchone()
            if owner != worker:
                raise ValueError(f"Slot {slot} belongs to another worker")
            # The global counter only allocates unique generations across slots.
            generation = db.execute("SELECT generation FROM state WHERE singleton=1").fetchone()[0] + 1
            key = AttemptKey(identity, secrets.token_hex(16), generation)
            now = time.time()
            if active is not None:
                db.execute("UPDATE attempts SET status='superseded',updated=? WHERE id=?", (now, active))
            db.execute(
                "INSERT OR IGNORE INTO requests(id,body,content_id) VALUES (?,?,?)", (identity, _blob(body), content)
            )
            db.execute(
                "INSERT INTO attempts(id,request_id,revision,generation,status,created,updated,details,slot,worker) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    key.attempt_id,
                    identity,
                    revision.number,
                    key.generation,
                    "queued",
                    now,
                    now,
                    _blob({}),
                    slot,
                    worker,
                ),
            )
            db.execute("UPDATE state SET generation=? WHERE singleton=1", (key.generation,))
            db.execute("UPDATE slots SET generation=?,active=? WHERE name=?", (key.generation, key.attempt_id, slot))
            return key

    def slots(self) -> dict[str, dict]:
        with self._transaction() as db:
            return {
                row["name"]: {
                    "kind": row["kind"],
                    "worker": row["worker"],
                    "active": row["active"],
                    "generation": row["generation"],
                }
                for row in db.execute("SELECT * FROM slots ORDER BY name")
            }

    def _require_active(self, db, key: AttemptKey):
        # An attempt is active only as its own slot's current assignment.
        row = db.execute(
            """SELECT a.* FROM attempts a JOIN slots s ON s.name=a.slot AND s.active=a.id
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
            row = self._require_active(db, key)
            db.execute(
                "UPDATE attempts SET status=?,updated=?,details=? WHERE id=?",
                (reason, time.time(), _blob(details or {}), key.attempt_id),
            )
            db.execute("UPDATE slots SET active=NULL WHERE name=?", (row["slot"],))

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
            db.execute("UPDATE slots SET active=NULL WHERE name=?", (row["slot"],))

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

    def committed_by_content(self, content: str) -> str | None:
        """The earliest committed attempt whose request has this content id (D103)."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT r.attempt FROM results r JOIN attempts a ON a.id=r.attempt "
                "JOIN requests q ON q.id=a.request_id WHERE q.content_id=? ORDER BY r.created,r.attempt LIMIT 1",
                (content,),
            ).fetchone()
            return None if row is None else row[0]

    def put_experiment(self, definition: dict) -> str:
        """Store an immutable definition; editing a definition creates a new experiment."""
        name = definition.get("name")
        if type(name) is not str or not name:
            raise ValueError("Experiment definition needs a name")
        identity = canonical_hash("experiment-v1", definition)
        with self._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO experiments VALUES (?,?,?,?)", (identity, name, _blob(definition), time.time())
            )
        return identity

    def experiment(self, experiment_id: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return {**dict(row), "definition": _read(row["definition"])}

    def create_experiment_run(self, experiment_id: str, preparation: dict, configurations, replications) -> str:
        """Record a run with all replications pending; an existing run id is returned unchanged.

        ``configurations`` are mappings ``label, parameters, layout_hash, layout_ref`` indexed
        by position; ``replications`` are mappings ``configuration, index, seed, content_ids``
        (the content ids of the replication's requests, one per worker kind).
        """
        configurations, replications = list(configurations), list(replications)
        for item in configurations:
            if set(item) != {"label", "parameters", "layout_hash", "layout_ref"}:
                raise ValueError("Invalid experiment configuration")
        seen = set()
        for item in replications:
            if (
                set(item) != {"configuration", "index", "seed", "content_ids"}
                or type(item["configuration"]) is not int
                or not 0 <= item["configuration"] < len(configurations)
                or type(item["index"]) is not int
                or item["index"] < 0
                or type(item["seed"]) is not int
                or not 0 <= item["seed"] < 2**63
                or not item["content_ids"]
                or (item["configuration"], item["index"]) in seen
            ):
                raise ValueError("Invalid experiment replication")
            for identity in item["content_ids"]:
                _hash(identity)
            seen.add((item["configuration"], item["index"]))
        run = experiment_run_id(experiment_id, [identity for item in replications for identity in item["content_ids"]])
        now = time.time()
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM experiments WHERE id=?", (experiment_id,)).fetchone() is None:
                raise KeyError(experiment_id)
            if db.execute("SELECT 1 FROM experiment_runs WHERE id=?", (run,)).fetchone() is not None:
                return run
            db.execute(
                "INSERT INTO experiment_runs VALUES (?,?,?,?,?,?,?)",
                (run, experiment_id, _blob(preparation), "running", now, now, _blob({})),
            )
            for index, item in enumerate(configurations):
                db.execute(
                    "INSERT INTO configurations VALUES (?,?,?,?,?,?)",
                    (
                        run,
                        index,
                        item["label"],
                        _blob(item["parameters"]),
                        item["layout_hash"],
                        _blob(item["layout_ref"]),
                    ),
                )
            db.executemany(
                "INSERT INTO replications VALUES (?,?,?,?,NULL,NULL,'pending',NULL,0)",
                [(run, item["configuration"], item["index"], str(item["seed"])) for item in replications],
            )
        return run

    def experiment_run(self, run_id: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            configurations = [
                {
                    "index": item["idx"],
                    "label": item["label"],
                    "parameters": _read(item["parameters"]),
                    "layout_hash": item["layout_hash"],
                    "layout_ref": _read(item["layout_ref"]),
                }
                for item in db.execute("SELECT * FROM configurations WHERE run=? ORDER BY idx", (run_id,))
            ]
        return {
            **dict(row),
            "preparation": _read(row["preparation"]),
            "details": _read(row["details"]),
            "configurations": configurations,
        }

    def replications(self, run_id: str, *, status: str | None = None) -> list[dict]:
        """Replications in dispatch order: replication-major, then configuration."""
        with self._transaction() as db:
            rows = db.execute(
                "SELECT * FROM replications WHERE run=? AND (? IS NULL OR status=?) ORDER BY idx,configuration",
                (run_id, status, status),
            ).fetchall()
        return [
            {
                "configuration": row["configuration"],
                "index": row["idx"],
                "seed": int(row["seed"]),
                "worker_kind": row["worker_kind"],
                "request_id": row["request_id"],
                "status": row["status"],
                "attempt": row["attempt"],
                "dispatches": row["dispatches"],
            }
            for row in rows
        ]

    def replication_kpis(self, run_id: str) -> dict[tuple[int, int], dict[str, Any]]:
        """KPI scalars of the succeeded and reused replications, by ``(configuration, index)``."""
        with self._transaction() as db:
            rows = db.execute(
                "SELECT r.configuration,r.idx,k.name,k.value FROM replications r "
                "JOIN kpis k ON k.attempt=r.attempt WHERE r.run=? AND r.status IN ('succeeded','reused')",
                (run_id,),
            ).fetchall()
        scalars: dict[tuple[int, int], dict[str, Any]] = {}
        for configuration, index, name, value in rows:
            scalars.setdefault((configuration, index), {})[name] = _read(value)
        return scalars

    def _replication(self, db, run_id, configuration, index):
        row = db.execute(
            "SELECT * FROM replications WHERE run=? AND configuration=? AND idx=?", (run_id, configuration, index)
        ).fetchone()
        if row is None:
            raise KeyError((run_id, configuration, index))
        return row

    def bind_replication(self, run_id: str, configuration: int, index: int, key: AttemptKey) -> None:
        """Record that the slot's active attempt ``key`` executes this pending replication.

        The first dispatch binds the replication to its request (and so to one worker
        kind); later dispatches must reuse that request so results pair with it.
        """
        with self._transaction() as db:
            run = db.execute("SELECT status FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run[0] != "running":
                raise ValueError("Experiment run is not running")
            row = self._replication(db, run_id, configuration, index)
            if row["status"] != "pending":
                raise ValueError("Replication is not pending")
            self._require_active(db, key)
            if row["request_id"] not in (None, key.request_id):
                raise ValueError("Replication is bound to another request")
            body = _read(db.execute("SELECT body FROM requests WHERE id=?", (key.request_id,)).fetchone()[0])
            db.execute(
                "UPDATE replications SET status='dispatched',request_id=?,worker_kind=?,attempt=?,"
                "dispatches=dispatches+1 WHERE run=? AND configuration=? AND idx=?",
                (
                    key.request_id,
                    body["runtime"].get("kind", "local"),
                    key.attempt_id,
                    run_id,
                    configuration,
                    index,
                ),
            )

    def set_replication_status(
        self,
        run_id: str,
        configuration: int,
        index: int,
        status: str,
        *,
        attempt: str | None = None,
        orderly: bool = False,
    ) -> None:
        """Apply one replication transition of design section 4.3.

        ``attempt`` fences terminal updates to the replication's bound attempt; for
        ``reused`` it names the committed attempt whose result is reused. ``orderly``
        is the coordinator's own shutdown: a dispatched replication returns to
        ``pending`` and its dispatch is given back, so pausing never spends the D97
        worker-loss budget (crash recovery still counts the dispatch as lost).
        """
        if status not in _REPLICATION:
            raise ValueError("Invalid replication status")
        if orderly and (status != "pending" or attempt is None):
            raise ValueError("An orderly pause returns one bound replication to pending")
        with self._transaction() as db:
            row = self._replication(db, run_id, configuration, index)
            allowed = ("pending",) if orderly and row["status"] == "dispatched" else _REPLICATION[row["status"]]
            if status not in allowed:
                raise ValueError("Invalid replication transition")
            bound = row["attempt"]
            if status == "reused":
                bound = attempt
            elif attempt is not None and attempt != bound:
                raise StaleAttempt("Replication is bound to another attempt")
            if status in ("succeeded", "reused") and (
                bound is None or db.execute("SELECT 1 FROM results WHERE attempt=?", (bound,)).fetchone() is None
            ):
                raise ValueError("Replication success requires a committed result")
            if status == "pending" and not orderly and row["dispatches"] >= MAX_DISPATCHES:
                raise ValueError("Replication exhausted its dispatch bound")
            db.execute(
                "UPDATE replications SET status=?,attempt=?,dispatches=dispatches-? "
                "WHERE run=? AND configuration=? AND idx=?",
                (status, bound, int(orderly), run_id, configuration, index),
            )

    def set_run_status(self, run_id: str, status: str, details: dict | None = None) -> None:
        """Apply one run transition; ``completed`` also accepts a run whose results were all reused."""
        if status not in _RUN:
            raise ValueError("Invalid experiment run status")
        with self._transaction() as db:
            row = db.execute("SELECT status FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if status not in _RUN[row[0]]:
                raise ValueError("Invalid experiment run transition")
            if status in ("completed", "failed"):
                counts = dict(
                    db.execute(
                        "SELECT CASE WHEN status IN ('pending','dispatched') OR (status='lost' AND dispatches<?) "
                        "THEN 'open' ELSE status END AS state,COUNT(*) FROM replications WHERE run=? GROUP BY state",
                        (MAX_DISPATCHES, run_id),
                    ).fetchall()
                )
                if counts.get("open"):
                    raise ValueError("Experiment run has non-terminal replications")
                succeeded = counts.get("succeeded") or counts.get("reused")
                if status == "completed" and not succeeded:
                    raise ValueError("Experiment run completed without a succeeded replication")
                if status == "failed" and succeeded:
                    raise ValueError("Experiment run with succeeded replications cannot fail")
            db.execute(
                "UPDATE experiment_runs SET status=?,updated=?,details=COALESCE(?,details) WHERE id=?",
                (status, time.time(), None if details is None else _blob(details), run_id),
            )

    def put_replay(self, request_id: str, original: str, *, attempt: str | None = None) -> None:
        """Link a replay request to the original request it re-executes at full level.

        With ``attempt`` (the replay's assigned attempt) the replay is ``running`` and any
        earlier outcome of the same replay request is cleared.
        """
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM requests WHERE id=?", (original,)).fetchone() is None:
                raise KeyError(original)
            db.execute(
                "INSERT OR IGNORE INTO replays VALUES (?,?,NULL,'pending',NULL,?)", (request_id, original, _blob({}))
            )
            if db.execute("SELECT original FROM replays WHERE request_id=?", (request_id,)).fetchone()[0] != original:
                raise ValueError("Replay is linked to another original request")
            if attempt is not None:
                db.execute(
                    "UPDATE replays SET attempt=?,status='running',verified=NULL,details=? WHERE request_id=?",
                    (attempt, _blob({}), request_id),
                )

    def set_replay(
        self, request_id: str, attempt: str, status: str, *, verified: bool | None = None, details: dict | None = None
    ) -> None:
        """Record the outcome of the replay's current attempt: ``succeeded`` with a verification, or a failure."""
        if status not in ("succeeded", *_TERMINAL) or (status == "succeeded") != (verified is not None):
            raise ValueError("Invalid replay outcome")
        with self._transaction() as db:
            row = db.execute("SELECT attempt,status FROM replays WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row["attempt"] != attempt or row["status"] != "running":
                raise StaleAttempt("Replay is bound to another attempt")
            if (
                status == "succeeded"
                and db.execute("SELECT 1 FROM results WHERE attempt=?", (attempt,)).fetchone() is None
            ):
                raise ValueError("Replay success requires a committed result")
            db.execute(
                "UPDATE replays SET status=?,verified=?,details=? WHERE request_id=?",
                (status, None if verified is None else int(verified), _blob(details or {}), request_id),
            )

    def replay(self, request_id: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM replays WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(request_id)
        verified = row["verified"]
        return {**dict(row), "verified": None if verified is None else bool(verified), "details": _read(row["details"])}

    @staticmethod
    def _unreferenced(db, candidates=None) -> list[str]:
        """Results referenced by no replication, replay, view or the newest 20 interactive results."""
        return [
            row[0]
            for row in db.execute(
                """SELECT r.attempt FROM results r JOIN attempts a ON a.id=r.attempt
                WHERE r.attempt NOT IN (SELECT attempt FROM replications WHERE attempt IS NOT NULL)
                AND r.attempt NOT IN (SELECT attempt FROM replays WHERE attempt IS NOT NULL)
                AND a.request_id NOT IN (SELECT original FROM replays UNION SELECT request_id FROM replays)
                AND r.attempt NOT IN (SELECT attempt FROM views)
                AND r.attempt NOT IN (SELECT n.attempt FROM results n JOIN attempts i ON i.id=n.attempt
                    WHERE i.slot=? ORDER BY n.created DESC,n.attempt DESC LIMIT 20)
                ORDER BY r.attempt""",
                (INTERACTIVE,),
            )
            if candidates is None or row[0] in candidates
        ]

    def _drop_results(self, db, attempts) -> None:
        for table in ("kpis", "artifacts", "results"):
            db.executemany(f"DELETE FROM {table} WHERE attempt=?", [(attempt,) for attempt in attempts])

    def _remove_results(self, attempts) -> None:
        for attempt in attempts:
            path = self.root / "results" / attempt
            if path.exists() or path.is_symlink():
                _remove(path)

    def prune(self, dry_run: bool = False) -> dict:
        """Remove unreferenced results (see ``_unreferenced``) and snapshots referenced by no request.

        Snapshot directories are kept when any stored request or captured preparation names
        them as source, inputs or trusted runtime. Only the exclusive owner may prune, and only without a
        live preparation that has not created a request yet (the CLI opens the store alone).
        The index transaction commits first; directories are removed afterwards, so an
        interrupted prune leaves only unindexed directories that ``recover`` removes.
        """
        with self._transaction() as db:
            attempts = self._unreferenced(db)
            referenced = set()
            bodies = [_read(row[0]) for row in db.execute("SELECT body FROM requests")]
            bodies += [
                _read(row[0])["request"]
                for row in db.execute("SELECT manifest FROM snapshots WHERE kind='preparation'")
            ]
            for body in bodies:
                referenced.update(body.get(name) for name in ("source_hash", "input_hash"))
                referenced.add(body["runtime"].get("trusted_runtime_hash"))
            snapshots = sorted(
                path.name
                for path in (self.root / "snapshots").iterdir()
                if _SNAPSHOT.fullmatch(path.name) and path.name not in referenced
            )
            if not dry_run:
                self._drop_results(db, attempts)
        if not dry_run:
            self._remove_results(attempts)
            for name in snapshots:
                _remove(self.root / "snapshots" / name)
        return {"results": attempts, "snapshots": snapshots}

    def delete_experiment_run(self, run_id: str) -> dict:
        """Remove a run's rows, then the results no other run, replay, view or recent history keeps.

        A running run must be cancelled or paused first. Like ``prune``, the index commits
        before directories are removed.
        """
        with self._transaction() as db:
            row = db.execute("SELECT experiment,status FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] == "running":
                raise ValueError("Experiment run is running; cancel it before deleting")
            owned = {
                item[0]
                for item in db.execute(
                    "SELECT attempt FROM replications WHERE run=? AND attempt IS NOT NULL", (run_id,)
                )
            }
            for table, column in (("replications", "run"), ("configurations", "run"), ("experiment_runs", "id")):
                db.execute(f"DELETE FROM {table} WHERE {column}=?", (run_id,))
            db.execute(
                "DELETE FROM experiments WHERE id=? AND id NOT IN (SELECT experiment FROM experiment_runs)",
                (row["experiment"],),
            )
            attempts = self._unreferenced(db, owned)
            self._drop_results(db, attempts)
        self._remove_results(attempts)
        return {"results": attempts}

    def recover(self) -> None:
        """Only called by the exclusive owner before scheduling work.

        Active attempts in every slot become lost. Running experiments pause (D97);
        replications left dispatched take their attempt's outcome, and lost ones are
        queued again within the dispatch bound without counting a new dispatch.
        """
        with self._transaction() as db:
            db.execute(
                "UPDATE attempts SET status='lost',updated=? WHERE status IN (?,?,?,?,?)", (time.time(), *_ACTIVE)
            )
            db.execute("UPDATE slots SET active=NULL")
            db.execute(
                """UPDATE replications SET status=COALESCE((SELECT CASE
                    WHEN a.status='succeeded' AND EXISTS(SELECT 1 FROM results r WHERE r.attempt=a.id) THEN 'succeeded'
                    WHEN a.status IN ('failed','validation_failed','disk_full') THEN 'failed'
                    WHEN a.status='cancelled' THEN 'cancelled' END
                    FROM attempts a WHERE a.id=replications.attempt),'lost') WHERE status='dispatched'"""
            )
            db.execute("UPDATE experiment_runs SET status='paused',updated=? WHERE status='running'", (time.time(),))
            db.execute(
                """UPDATE replications SET status='pending' WHERE status='lost' AND dispatches<?
                AND run IN (SELECT id FROM experiment_runs WHERE status='paused')""",
                (MAX_DISPATCHES,),
            )
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
