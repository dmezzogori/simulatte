"""Durable schema additions and pre-migration SQLite backups."""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from simulatte.execution.durability import sync_directory

SCHEMA_3 = (
    """CREATE TABLE project_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        id TEXT NOT NULL, settings BLOB NOT NULL)""",
    """CREATE TABLE models (id TEXT PRIMARY KEY, key TEXT NOT NULL, name TEXT NOT NULL,
        entrypoint TEXT NOT NULL, layout TEXT NOT NULL, tombstone INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE model_revisions (revision INTEGER PRIMARY KEY REFERENCES revisions(number),
        model TEXT NOT NULL REFERENCES models(id))""",
    "CREATE INDEX model_revision_latest ON model_revisions(model,revision)",
    """CREATE TABLE attempt_models (attempt TEXT PRIMARY KEY REFERENCES attempts(id),
        model TEXT NOT NULL REFERENCES models(id))""",
    "CREATE INDEX model_attempts ON attempt_models(model,attempt)",
    """CREATE TABLE history_annotations (attempt TEXT PRIMARY KEY REFERENCES attempts(id),
        name TEXT NOT NULL, text TEXT NOT NULL, important INTEGER NOT NULL CHECK(important IN (0,1)))""",
    """CREATE TABLE environment_generations (id TEXT PRIMARY KEY, evidence BLOB NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('prepared','selected','retired')))""",
    """CREATE TABLE "references" (owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
        target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
        PRIMARY KEY(owner_kind,owner_id,target_kind,target_id))""",
    'CREATE INDEX reference_target ON "references"(target_kind,target_id)',
)


def migrate_2(db):
    for statement in SCHEMA_3:
        db.execute(statement)


def backup_store(db: sqlite3.Connection, root: Path, version: int) -> Path:
    """Called with the writer OS lock, before any mutation of the source database."""
    from simulatte.execution.store import _directory

    backups = root / "backups"
    _directory(backups)
    target = backups / f"schema-{version}-{uuid4().hex}"
    target.mkdir()
    try:
        path = target / "index.sqlite"
        with sqlite3.connect(path) as backup:
            db.backup(backup)
        backup.close()
        manifest = {
            "schema": version,
            "sqlite_version": sqlite3.sqlite_version,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        with path.open("r+b") as stream:
            import os

            os.fsync(stream.fileno())
        with (target / "manifest.json").open("x") as stream:
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(target)
        sync_directory(backups)
        return target
    except BaseException:
        shutil.rmtree(target)
        raise
