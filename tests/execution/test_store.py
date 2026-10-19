"""One owner, monotonic revisions and durable attempt fencing."""

from contextlib import closing
import importlib
import importlib.util
import mmap
import shutil
import sqlite3

import pytest

from simulatte.execution import schema
from tests.execution.test_schema import request


def api():
    assert importlib.util.find_spec("simulatte.execution.store"), "run store missing"
    return importlib.import_module("simulatte.execution.store")


def test_store_initialization_ownership_and_reopen(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root) as store:
        assert store.schema_version == 3
        assert store.settings() == {"journal_mode": "wal", "synchronous": 2, "foreign_keys": 1}
        with pytest.raises(m.StoreBusy):
            m.RunStore.open(root)
    with closing(sqlite3.connect(root / "index.sqlite")) as reader:
        assert reader.execute("PRAGMA user_version").fetchone() == (3,)
    with m.RunStore.open(root):
        pass
    assert (root / "coordinator.lock").exists()


def test_revisions_are_monotonic_even_for_identical_inputs(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        first = store.create_revision({"source": "a"})
        second = store.create_revision({"source": "b"}, expected=first)
        third = store.create_revision({"source": "a"}, expected=second)
        assert third.number == first.number + 2
        assert third.digest == first.digest
        with pytest.raises(m.RevisionConflict):
            store.create_revision({"source": "c"}, expected=first)
        assert store.current_revision() == third
    with m.RunStore.open(tmp_path / ".simulatte") as reopened:
        assert reopened.current_revision() == third


def test_new_assignment_atomically_supersedes_old_and_preserves_requests(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({"source": "a"})
        requested = request(schema)
        first = store.assign(requested, revision)
        second = store.assign(requested, revision)
        assert first.request_id == second.request_id == schema.request_id(requested)
        assert first.attempt_id != second.attempt_id
        assert second.generation == first.generation + 1
        assert store.attempt(first)["status"] == "superseded"
        assert store.request(second.request_id) == requested
        with pytest.raises(m.StaleAttempt):
            store.transition(first, "ready")
        for state in ("preparing", "ready", "running", "finalizing"):
            store.transition(second, state)
        assert store.attempt(second)["status"] == "finalizing"
        store.revoke(second, "cancelled")
        assert store.attempt(second)["status"] == "cancelled"
        with pytest.raises(m.StaleAttempt):
            store.require_active(second)


def test_recovery_marks_orphaned_attempt_lost_and_removes_unindexed_data(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root) as store:
        key = store.assign(request(schema), store.create_revision({"source": "a"}))
        orphan = root / "results" / key.attempt_id
        orphan.mkdir()
        (orphan / "run.simtrace").write_bytes(b"incomplete")
        stage = root / "staging" / key.attempt_id
        stage.mkdir(exist_ok=True)
        (stage / "partial").write_bytes(b"incomplete")
    with m.RunStore.open(root) as store:
        assert store.attempt(key)["status"] == "lost"
        with pytest.raises(m.StaleAttempt):
            store.require_active(key)
        assert not orphan.exists() and not stage.exists()


def test_unknown_schema_is_refused_without_recovery_or_modification(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root):
        pass
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        db.execute("PRAGMA user_version=4")
    orphan = root / "staging" / "unindexed"
    orphan.mkdir()
    with pytest.raises(ValueError, match="schema"):
        m.RunStore.open(root)
    assert orphan.exists()
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (4,)


def test_actual_owner_death_releases_lock(tmp_path):
    import subprocess
    import sys

    m = api()
    root = tmp_path / ".simulatte"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from simulatte.execution.store import RunStore; "
            "import sys,time; store=RunStore.open(Path(sys.argv[1])); "
            "store.create_revision({'source':'committed'}); print('ready',flush=True); time.sleep(60)",
            str(root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(m.StoreBusy):
            m.RunStore.open(root)
        process.kill()
        process.wait(5)
        try:
            with m.RunStore.open(root) as store:
                assert store.current_revision().number == 1
                assert store.create_revision({"source": "after crash"}).number == 2
        except sqlite3.Error as error:
            error.add_note(
                f"SQLite {sqlite3.sqlite_version}: {error.sqlite_errorname} ({error.sqlite_errorcode}); "
                f"files={[(path.name, path.stat().st_size) for path in root.iterdir() if path.is_file()]}"
            )
            raise
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(5)
        process.stdout.close()


def test_windows_recovery_ignores_stale_mapped_shm_and_preserves_wal(tmp_path, monkeypatch):
    m = api()
    source = tmp_path / "source"
    root = tmp_path / ".simulatte"
    root.mkdir()
    # Simulate a crash with a stale SHM snapshot and a newer committed WAL.
    monkeypatch.setattr(m, "_WINDOWS", False)
    with m.RunStore.open(source) as store:
        store.create_revision({"source": "old"})
        stale_shm = (source / "index.sqlite-shm").read_bytes()
        committed = store.create_revision({"source": "committed only in WAL"})
        for name in ("index.sqlite", "index.sqlite-wal"):
            shutil.copyfile(source / name, root / name)
    shm = root / "index.sqlite-shm"
    shm.write_bytes(stale_shm)
    monkeypatch.setattr(m, "_WINDOWS", True)
    with shm.open("r+b") as file, mmap.mmap(file.fileno(), 0) as mapping:
        with m.RunStore.open(root) as store:
            assert store.current_revision() == committed
            assert store.create_revision({"source": "after recovery"}).number == committed.number + 1
            # Recovery must use the WAL, without trusting or truncating SHM.
            assert mapping[:] == stale_shm
    with m.RunStore.open(root) as store:
        assert store.current_revision().number == committed.number + 1


def test_failed_schema_creation_rolls_back_atomically(tmp_path, monkeypatch):
    m = api()
    root = tmp_path / ".simulatte"
    with monkeypatch.context() as patch:
        patch.setattr(m, "_DDL", (*m._DDL, "this is invalid SQL"))
        with pytest.raises(sqlite3.OperationalError):
            m.RunStore.open(root)
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    with m.RunStore.open(root):
        pass


def test_invalid_transitions_and_keys_do_not_mutate_current_attempt(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        assert store.current_revision() is None
        old = store.create_revision({"value": 1})
        revision = store.create_revision({"value": 2})
        with pytest.raises(m.RevisionConflict):
            store.assign(request(schema), old)
        key = store.assign(request(schema), revision)
        wrong = schema.AttemptKey("0" * 64, key.attempt_id, key.generation)
        with pytest.raises(m.StaleAttempt):
            store.revoke(wrong, "cancelled")
        with pytest.raises(ValueError, match="transition"):
            store.transition(key, "running")
        with pytest.raises(ValueError, match="terminal"):
            store.revoke(key, "succeeded")
        with pytest.raises(KeyError):
            store.attempt(wrong)
        with pytest.raises(KeyError):
            store.request("0" * 64)
        assert store.attempt(key)["status"] == "queued"
        store.transition(key, "preparing", details={"pid": 1})
        assert store.attempt(key)["details"] == {"pid": 1}
        store.revoke(key, "failed", details={"code": "build_failed"})
        assert store.attempt(key)["details"] == {"code": "build_failed"}
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store.current_revision()


@pytest.mark.parametrize("name", ["coordinator.lock", "index.sqlite", "staging", "results", "snapshots"])
def test_store_does_not_follow_reserved_symlinks(tmp_path, name):
    m = api()
    root = tmp_path / ".simulatte"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        m.RunStore.open(root)
    assert list(outside.iterdir()) == []


def test_windows_lock_api_locks_and_unlocks_same_byte(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    m = api()
    calls = []
    with (tmp_path / "lock").open("w+b") as file:
        file.write(b"abc")
        monkeypatch.setitem(
            sys.modules,
            "msvcrt",
            SimpleNamespace(
                LK_NBLCK=2,
                LK_UNLCK=0,
                locking=lambda fd, mode, count: calls.append((fd, mode, count, file.tell())),
            ),
        )
        monkeypatch.setattr(m, "os", SimpleNamespace(name="nt"))
        m._acquire(file)
        file.seek(2)
        m._release(file)
        assert calls == [(file.fileno(), 2, 1, 0), (file.fileno(), 0, 1, 0)]


def test_lock_fdopen_failure_does_not_leak_descriptor(tmp_path, monkeypatch):
    import os

    m = api()
    descriptors = []

    def fail(fd, *args, **kwargs):
        descriptors.append(fd)
        raise OSError("fdopen failed")

    monkeypatch.setattr(m.os, "fdopen", fail)
    with pytest.raises(OSError, match="fdopen"):
        m.RunStore.open(tmp_path / ".simulatte")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_recovery_unlinks_orphan_symlink_without_following_it(tmp_path):
    m = api()
    root = tmp_path / "nested" / ".simulatte"
    with m.RunStore.open(root):
        pass
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "data").write_bytes(b"keep")
    (root / "results" / "orphan").symlink_to(outside, target_is_directory=True)
    with m.RunStore.open(root):
        assert (outside / "data").read_bytes() == b"keep"
        assert not (root / "results" / "orphan").exists()
    (root / "staging").rmdir()
    (root / "staging").write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="not a directory"):
        m.RunStore.open(root)


def test_failed_assignment_transaction_does_not_supersede_previous(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        first = store.assign(request(schema), revision)
        with store._transaction() as db:
            db.execute("""CREATE TRIGGER fail_attempt BEFORE INSERT ON attempts
                BEGIN SELECT RAISE(ABORT, 'injected index failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="index failure"):
            store.assign(request(schema, seed=1), revision)
        store.require_active(first)
        assert store.attempt(first)["status"] == "queued"
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 1


def test_recovery_removes_read_only_detached_snapshot_directories(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root):
        snapshot = root / "staging" / "orphan" / "snapshot"
        snapshot.mkdir(parents=True)
        path = snapshot / "plant.py"
        path.write_text("# captured source\n")
        path.chmod(0o444)
        snapshot.chmod(0o555)
    with m.RunStore.open(root):
        assert not list((root / "staging").iterdir())
