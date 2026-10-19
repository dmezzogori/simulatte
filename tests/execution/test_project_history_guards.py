"""History identity and read-only containment reject ambiguous or unsafe changes."""

from contextlib import closing
from dataclasses import replace
import shutil
import sqlite3
from uuid import uuid4

import pytest

from simulatte.execution.project_store import ProjectStore
from simulatte.execution.store import RunStore, StaleAttempt
from simulatte.execution.store_reader import StoreReader
from simulatte.execution.store_migrations import backup_store
from tests.execution.test_project_store import FIXTURE, workspace, saved


def test_history_rejects_invalid_paging_and_identity_reassignment(tmp_path):
    definition = workspace(tmp_path)
    with RunStore.open(tmp_path / ".simulatte") as store:
        projects = ProjectStore(store)
        projects.register(definition)
        key = saved(store)
        for limit in (0, 201, True, "10"):
            with pytest.raises(ValueError, match="limit"):
                projects.history(limit=limit)
        for cursor in ("-1:1", "1:3", "1:0"):
            with pytest.raises(ValueError, match="cursor"):
                projects.history(after=cursor)
        with pytest.raises(ValueError, match="UUID"):
            projects.register(replace(definition, id=str(uuid4())))
        with pytest.raises(KeyError):
            projects.associate_attempt(replace(key, generation=key.generation + 1), definition.models[0].id)
        with pytest.raises(KeyError):
            projects.associate_attempt(key, str(uuid4()))
        for change in ({"name": 3}, {"text": "x" * 10001}, {"important": 1}):
            with pytest.raises(ValueError, match="annotation"):
                projects.annotate(key.attempt_id, **{"name": "", "text": "", "important": False, **change})


def test_reader_refuses_symlink_unrecovered_wal_and_escaping_artifacts(tmp_path):
    root = tmp_path / "store"
    shutil.copytree(FIXTURE, root)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        StoreReader.open(linked)
    with pytest.raises(ValueError, match="index"):
        StoreReader.open(tmp_path / "absent")
    (root / "index.sqlite-wal").write_bytes(b"unrecovered")
    with pytest.raises(ValueError, match="WAL recovery"):
        StoreReader.open(root)
    (root / "index.sqlite-wal").unlink()
    with closing(StoreReader.open(root)) as reader:
        assert reader.history("unassigned").items == ()
        with pytest.raises(KeyError):
            reader.result("missing")
        item = reader.history().items[0]
        with pytest.raises(KeyError):
            reader.artifact(item["id"], "unknown.csv")
        artifact = reader.artifacts(item["id"])[0]["path"]
        path = reader.artifact(item["id"], artifact)
        path.unlink()
        path.symlink_to(tmp_path / "outside")
        with pytest.raises(ValueError, match="escapes"):
            reader.artifact(item["id"], artifact)
        with pytest.raises(StaleAttempt, match="committed"):
            reader._require_active(reader._db, None)


def test_backup_failure_closes_database_and_removes_partial_directory(tmp_path, monkeypatch):
    from simulatte.execution import store_migrations

    root = tmp_path / "store"
    root.mkdir()
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        db.execute("CREATE TABLE original (id)")
        db.commit()
        before = (root / "index.sqlite").read_bytes()
        monkeypatch.setattr(
            store_migrations, "sync_directory", lambda path: (_ for _ in ()).throw(OSError("disk failure"))
        )
        with pytest.raises(OSError, match="disk failure"):
            backup_store(db, root, 2)
        assert (root / "index.sqlite").read_bytes() == before
        assert list((root / "backups").iterdir()) == []


def test_interrupted_sqlite_backup_closes_destination_before_cleanup(tmp_path, monkeypatch):
    from simulatte.execution import store_migrations

    opened = []
    original = sqlite3.connect

    class Destination(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(path):
        connection = original(path, factory=Destination)
        opened.append(connection)
        return connection

    class Interrupted:
        def backup(self, destination):
            raise sqlite3.OperationalError("interrupted backup")

    monkeypatch.setattr(store_migrations.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        backup_store(Interrupted(), tmp_path, 2)
    assert opened[0].closed
    assert not list((tmp_path / "backups").iterdir())
