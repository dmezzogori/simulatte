"""Project history survives configuration changes and versioned store upgrades."""

from contextlib import closing
from dataclasses import replace
import importlib
import importlib.util
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

from simulatte.execution import schema
from simulatte.execution.store import RunStore
from simulatte.projects import ModelRegistration, WorkspaceDefinition
from tests.execution.test_schema import request
from tests.execution.test_store_slots import commit

FIXTURE = Path(__file__).parents[1] / "fixtures/stores/project-v2"


def api():
    assert importlib.util.find_spec("simulatte.execution.project_store"), "project store missing"
    return importlib.import_module("simulatte.execution.project_store")


def workspace(root):
    return WorkspaceDefinition(
        root, str(uuid4()), (ModelRegistration(str(uuid4()), "a", "A", "plant.py:build", "a.json"),), (), "a"
    )


def saved(store):
    key = store.assign(request(schema), store.create_revision({"source": "a"}))
    commit(store, key, key.generation)
    return key


def test_model_identity_survives_rename_removal_and_copy(tmp_path):
    m = api()
    definition = workspace(tmp_path)
    model = definition.models[0]
    with RunStore.open(tmp_path / ".simulatte") as store:
        projects = m.ProjectStore(store)
        projects.register(definition)
        key = saved(store)
        before = store.request(key.request_id).to_dict()
        projects.associate_attempt(key, model.id)
        projects.register(
            replace(definition, models=(replace(model, key="renamed", name="Renamed"),), default_model="renamed")
        )
        projects.register(replace(definition, models=(), default_model=None))
        assert projects.history(model.id, after=None).items[0]["id"] == key.attempt_id
        assert store.request(key.request_id).to_dict() == before
    copied = tmp_path / "copy"
    shutil.copytree(tmp_path / ".simulatte", copied / ".simulatte")
    with RunStore.open(copied / ".simulatte") as store:
        projects = m.ProjectStore(store)
        replacement = replace(model, id=str(uuid4()))
        projects.register(replace(definition, root=copied, models=(replacement,)))
        assert projects.history(model.id, after=None).items[0]["id"] == key.attempt_id
        assert projects.history(replacement.id, after=None).items == ()
        with pytest.raises(ValueError, match="associated"):
            projects.associate_attempt(key, replacement.id)


def test_ambiguous_legacy_history_remains_unassigned(tmp_path):
    m = api()
    shutil.copytree(FIXTURE, tmp_path / ".simulatte")
    definition = workspace(tmp_path)
    definition = replace(
        definition, models=(*definition.models, replace(definition.models[0], id=str(uuid4()), key="b"))
    )
    with RunStore.open(tmp_path / ".simulatte") as store:
        projects = m.ProjectStore(store)
        projects.register(definition)
        history = projects.history(None, after=None)
        assert history.items and all(item["model_id"] is None for item in history.items)


def test_annotations_references_and_stable_pagination(tmp_path):
    m = api()
    definition = workspace(tmp_path)
    with RunStore.open(tmp_path / ".simulatte") as store:
        projects = m.ProjectStore(store)
        projects.register(definition)
        keys = []
        for _ in range(25):
            key = saved(store)
            projects.associate_attempt(key, definition.models[0].id)
            keys.append(key)
        projects.annotate(keys[0].attempt_id, name="Important", text="Keep this evidence", important=True)
        projects.link("model", definition.models[0].id, "result", keys[1].attempt_id)
        first = projects.history(None, after=None, limit=10)
        saved(store)
        second = projects.history(None, after=first.next_cursor, limit=20)
        assert [item["id"] for item in first.items + second.items] == [k.attempt_id for k in reversed(keys)]
        item = second.items[-1]
        assert (item["name"], item["text"], item["important"]) == ("Important", "Keep this evidence", True)
        pruned = store.prune(dry_run=True)["results"]
        assert keys[0].attempt_id not in pruned and keys[1].attempt_id not in pruned
        assert keys[2].attempt_id in pruned
        with pytest.raises(ValueError):
            projects.link("model", definition.models[0].id, "invalid", "bad")
        with pytest.raises(KeyError):
            projects.link("model", definition.models[0].id, "result", "missing")
        with pytest.raises(ValueError):
            projects.history(None, after="invalid", limit=10)


def file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_schema2_reader_is_read_only_and_preserves_results(tmp_path):
    api()
    from simulatte.execution.store_reader import StoreReader

    shutil.copytree(FIXTURE, tmp_path / "store")
    root = tmp_path / "store"
    before = file_bytes(root)
    reader = StoreReader.open(root)
    try:
        history = reader.history(None, after=None)
        result = reader.result(history.items[0]["id"])
        assert (result.path / "run.simtrace").is_file()
        assert reader.artifacts(result.key.attempt_id)
    finally:
        reader.close()
    assert file_bytes(root) == before
    assert not (root / "coordinator.lock").exists()


def test_schema2_migration_backup_preserves_identities(tmp_path):
    api()
    root = tmp_path / ".simulatte"
    shutil.copytree(FIXTURE, root)
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        before = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("requests", "attempts", "results", "views")
        }
    with RunStore.open(root) as store:
        assert store.schema_version == 5
        for table, rows in before.items():
            assert [tuple(r) for r in store._db.execute(f"SELECT * FROM {table}")] == rows
    backup = next((root / "backups").glob("*/index.sqlite"))
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
    assert backup.with_name("manifest.json").is_file()


def test_backup_failure_and_interrupted_migration_leave_original(tmp_path, monkeypatch):
    api()
    from simulatte.execution import store_migrations

    root = tmp_path / ".simulatte"
    shutil.copytree(FIXTURE, root)
    before = (root / "index.sqlite").read_bytes()

    def fail(*args):
        raise OSError("backup failed")

    with monkeypatch.context() as patch:
        patch.setattr(store_migrations, "backup_store", fail)
        with pytest.raises(OSError, match="backup failed"):
            RunStore.open(root)
    assert (root / "index.sqlite").read_bytes() == before

    def interrupted(db):
        db.execute("CREATE TABLE unfinished (value TEXT)")
        raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setitem(importlib.import_module("simulatte.execution.store")._MIGRATIONS, 2, interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            RunStore.open(root)
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='unfinished'").fetchall()
