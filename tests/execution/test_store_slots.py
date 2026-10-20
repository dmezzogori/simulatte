"""Store schema 2: per-slot attempt fences, the 1 to 2 migration, experiments, replays and pruning.

The version-1 fixture in ``tests/fixtures/stores/v1`` was produced by Simulatte
0.16.0 through the headless coordinator (one inspection request, one execution
request with a committed result and a bound view). Regenerate it with
``tests/fixtures/stores/make_v1.py``; its docstring gives the command.
"""

from contextlib import closing
from dataclasses import replace
import importlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from simulatte.execution import schema
from simulatte.execution.hashing import canonical_hash
from tests.execution.test_revisions import layout_bundle
from tests.execution.test_schema import request

FIXTURE = Path(__file__).parents[1] / "fixtures" / "stores" / "v1"
FIXTURE_ATTEMPT = next((FIXTURE / "results").iterdir()).name


def api():
    return importlib.import_module("simulatte.execution.store")


def fixture_copy(tmp_path):
    root = tmp_path / ".simulatte"
    shutil.copytree(FIXTURE, root)
    return root


def rows(root, sql):
    """Query a closed store's index, or an open store through its own connection (Windows holds it EXCLUSIVE)."""
    if isinstance(root, api().RunStore):
        with root._mutex:
            return [tuple(row) for row in root._db.execute(sql).fetchall()]
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        return db.execute(sql).fetchall()


def layout(root):
    """Column, foreign key and index definitions of every table, independent of DDL spelling."""
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {
            table: (
                db.execute(f"PRAGMA table_xinfo('{table}')").fetchall(),
                db.execute(f"PRAGMA foreign_key_list('{table}')").fetchall(),
                sorted(
                    (index[1], index[2], tuple(column[2] for column in db.execute(f"PRAGMA index_info({index[1]})")))
                    for index in db.execute(f"PRAGMA index_list('{table}')")
                ),
            )
            for table in tables
        }


def advance(store, key, until="running"):
    for status in ("preparing", "ready", "running", "finalizing"):
        store.transition(key, status)
        if status == until:
            return


def commit(store, key, created):
    """Mirror the index rows and the directory that ``publish`` writes for a result."""
    path = store.root / "results" / key.attempt_id
    path.mkdir()
    (path / "run.simtrace").write_bytes(b"trace")
    with store._transaction() as db:
        db.execute("INSERT INTO results VALUES (?,?,?,?)", (key.attempt_id, api()._blob({}), api()._blob({}), created))
        db.execute("UPDATE attempts SET status='succeeded' WHERE id=?", (key.attempt_id,))
        db.execute("UPDATE slots SET active=NULL WHERE active=?", (key.attempt_id,))


# Migration ------------------------------------------------------------------


def test_migration_of_v1_fixture_keeps_requests_attempts_result_and_view(tmp_path):
    m = api()
    root = fixture_copy(tmp_path)
    assert rows(root, "PRAGMA user_version") == [(1,)]
    requests = rows(root, "SELECT id,body FROM requests ORDER BY id")
    attempts = rows(root, "SELECT * FROM attempts ORDER BY id")
    views = rows(root, "SELECT * FROM views")
    (generation,) = rows(root, "SELECT generation FROM state")[0]
    with m.RunStore.open(root) as store:
        assert store.schema_version == 5
        result = store.result(FIXTURE_ATTEMPT)
        assert result.path.is_dir() and (result.path / "run.simtrace").is_file()
        view = store.presentation_view(committed_only=True)
        assert view is not None and view.key == result.key and view.committed
        assert store.request(result.key.request_id).layout_revision == view.revision.number
        assert store.slots() == {
            "interactive": {"kind": "local", "worker": None, "active": None, "generation": generation}
        }
    assert rows(root, "PRAGMA user_version") == [(5,)]
    assert rows(root, "SELECT id,body FROM requests ORDER BY id") == requests
    assert rows(root, "SELECT * FROM attempts ORDER BY id") == [(*row, "interactive", None) for row in attempts]
    assert rows(root, "SELECT * FROM views") == views
    assert rows(root, "SELECT * FROM state") == [(1, generation)]
    fresh = tmp_path / "fresh"
    with m.RunStore.open(fresh):
        pass
    assert layout(root) == layout(fresh)
    # Opening a migrated store again is a no-op.
    before = rows(root, "SELECT * FROM sqlite_master ORDER BY name")
    with m.RunStore.open(root) as store:
        assert store.result(FIXTURE_ATTEMPT).key == result.key
    assert rows(root, "SELECT * FROM sqlite_master ORDER BY name") == before


def test_migration_moves_active_attempt_into_interactive_slot(tmp_path, monkeypatch):
    m = api()
    root = fixture_copy(tmp_path)
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        attempt, generation = db.execute("SELECT id,generation FROM attempts ORDER BY generation DESC").fetchone()
        db.execute("UPDATE attempts SET status='running' WHERE id=?", (attempt,))
        db.execute("UPDATE state SET active=?", (attempt,))
        db.commit()
    with monkeypatch.context() as patch:
        patch.setattr(m.RunStore, "recover", lambda self: None)
        with m.RunStore.open(root) as store:
            assert store.slots()["interactive"] == {
                "kind": "local",
                "worker": None,
                "active": attempt,
                "generation": generation,
            }
    with m.RunStore.open(root) as store:
        assert store.slots()["interactive"]["active"] is None
        assert rows(store, f"SELECT status FROM attempts WHERE id='{attempt}'") == [("lost",)]


def test_failed_migration_rolls_back_to_version_1(tmp_path, monkeypatch):
    m = api()
    root = fixture_copy(tmp_path)
    before = layout(root)

    def fail(request):
        raise ValueError("injected backfill failure")

    monkeypatch.setattr(m, "content_id", fail)
    with pytest.raises(ValueError, match="injected"):
        m.RunStore.open(root)
    assert rows(root, "PRAGMA user_version") == [(1,)]
    assert layout(root) == before


def test_migration_refuses_newer_schema(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root):
        pass
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        db.execute("PRAGMA user_version=6")
    with pytest.raises(ValueError, match="Unsupported run store schema 6"):
        m.RunStore.open(root)
    assert rows(root, "PRAGMA user_version") == [(6,)]


def test_migration_backfills_content_ids_for_result_reuse(tmp_path):
    m = api()
    root = fixture_copy(tmp_path)
    with m.RunStore.open(root) as store:
        backfilled = dict(rows(store, "SELECT id,content_id FROM requests"))
        result = store.result(FIXTURE_ATTEMPT)
        original = store.request(result.key.request_id)
        assert backfilled.pop(result.key.request_id) == schema.content_id(original)
        assert list(backfilled.values()) == [None]  # the inspection request has no content id
        moved = replace(original, layout_revision=original.layout_revision + 7)
        assert schema.request_id(moved) != result.key.request_id
        assert schema.content_id(moved) == schema.content_id(original)
        assert store.committed_by_content(schema.content_id(moved)) == FIXTURE_ATTEMPT
        assert store.committed_by_content(schema.content_id(replace(original, seed=99))) is None
        # New assignments index their content id too; uncommitted attempts are never reused.
        fresh = replace(original, seed=5, layout_revision=store.current_revision().number)
        store.assign(fresh, store.current_revision())
        assert rows(store, f"SELECT content_id FROM requests WHERE id='{schema.request_id(fresh)}'") == [
            (schema.content_id(fresh),)
        ]
        assert store.committed_by_content(schema.content_id(fresh)) is None


def test_content_id_excludes_only_layout_revision():
    requested = request(schema)
    assert schema.content_id(requested) == canonical_hash(
        "request-content-v1", {k: v for k, v in requested.to_dict().items() if k != "layout_revision"}
    )
    assert schema.content_id(requested) == schema.content_id(replace(requested, layout_revision=9))
    assert schema.content_id(requested) != schema.content_id(replace(requested, seed=1))


# Slots ----------------------------------------------------------------------


def test_concurrent_slots_are_fenced_by_their_own_slot(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({"source": "a"})
        interactive = store.assign(request(schema), revision)
        local = store.assign(request(schema, seed=1), revision, slot="local-1")
        third = store.assign(request(schema, seed=2), revision, slot="local-2")
        assert third.generation > local.generation > interactive.generation
        for key in (interactive, local, third):
            store.require_active(key)
            advance(store, key)
        assert store.slots() == {
            "interactive": {
                "kind": "local",
                "worker": None,
                "active": interactive.attempt_id,
                "generation": interactive.generation,
            },
            "local-1": {"kind": "local", "worker": None, "active": local.attempt_id, "generation": local.generation},
            "local-2": {"kind": "local", "worker": None, "active": third.attempt_id, "generation": third.generation},
        }
        assert store.attempt(local)["slot"] == "local-1" and store.attempt(local)["worker"] is None
        store.revoke(local, "cancelled")
        with pytest.raises(m.StaleAttempt):
            store.require_active(local)
        assert store.slots()["local-1"]["active"] is None
        store.require_active(interactive)
        store.require_active(third)
        # A new assignment supersedes only its own slot's attempt.
        replacement = store.assign(request(schema, seed=3), revision, slot="local-2")
        assert store.attempt(third)["status"] == "superseded"
        store.require_active(interactive)
        store.require_active(replacement)
        # A key must carry its own attempt's generation.
        forged = schema.AttemptKey(interactive.request_id, interactive.attempt_id, replacement.generation)
        with pytest.raises(m.StaleAttempt):
            store.require_active(forged)


def test_revision_revokes_interactive_slot_only(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        old = store.create_revision({"source": "a"})
        interactive = store.assign(request(schema), old)
        experiment = store.assign(request(schema, seed=1), old, slot="local-1", pinned=True)
        advance(store, interactive)
        advance(store, experiment)
        current = store.create_revision({"source": "b"}, expected=old, revoke_active=True)
        assert store.attempt(interactive)["status"] == "superseded"
        with pytest.raises(m.StaleAttempt):
            store.require_active(interactive)
        assert store.slots()["interactive"]["active"] is None
        store.require_active(experiment)
        store.transition(experiment, "finalizing")
        pinned = store.assign(request(schema, seed=2), old, slot="local-2", pinned=True)
        assert store.attempt(pinned)["revision"] == old.number
        store.require_active(pinned)
        with pytest.raises(ValueError, match="interactive"):
            store.assign(request(schema, seed=3), old, pinned=True)
        with pytest.raises(m.RevisionConflict):
            store.assign(request(schema, seed=3), old, slot="local-3")
        with pytest.raises(m.RevisionConflict):
            store.assign(request(schema, seed=3), m.Revision(old.number, current.digest), slot="local-3", pinned=True)
        with pytest.raises(m.RevisionConflict):
            store.assign(request(schema, seed=3), m.Revision(9, old.digest), slot="local-3", pinned=True)
        assert "local-3" not in store.slots()


def test_slot_names_and_workers_are_validated(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        remote = store.assign(request(schema), revision, slot="strangelove-1", worker="strangelove")
        assert store.slots()["strangelove-1"] == {
            "kind": "remote",
            "worker": "strangelove",
            "active": remote.attempt_id,
            "generation": remote.generation,
        }
        assert store.attempt(remote)["worker"] == "strangelove"
        with pytest.raises(ValueError, match="worker"):
            store.assign(request(schema), revision, slot="strangelove-1")
        with pytest.raises(ValueError, match="worker"):
            store.assign(request(schema), revision, worker="strangelove")
        for name in ("", "a/b", "x" * 65, 1):
            with pytest.raises(ValueError, match="slot"):
                store.assign(request(schema), revision, slot=name)
        store.require_active(remote)


def test_views_bind_only_interactive_attempts(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        key = store.assign(request(schema), revision, slot="local-1")
        advance(store, key, "ready")
        bundle = layout_bundle()
        with pytest.raises(ValueError, match="interactive"):
            store.bind_ready_view(key, bundle)
        with pytest.raises(ValueError, match="interactive"):
            store.bind_view(revision, key, bundle)
        with pytest.raises(ValueError, match="interactive"):
            store.create_revision({"layout": "paint"}, expected=revision, view=(key, bundle))
        assert store.presentation_view() is None
        assert store.current_revision() == revision
        store.require_active(key)


# Experiments ----------------------------------------------------------------

DEFINITION = {"schema": 1, "name": "sweep", "model": "plant.py:build", "replications": {"count": 2, "base_seed": 0}}


def configurations():
    return [
        {"label": f"x={x}/base", "parameters": {"x": float(x)}, "layout_hash": "c" * 64, "layout_ref": None}
        for x in (1, 2)
    ]


def replications():
    return [
        {
            "configuration": c,
            "index": i,
            "seed": i,
            "content_ids": [schema.content_id(request(schema, seed=10 * c + i))],
        }
        for c in (0, 1)
        for i in (0, 1)
    ]


def experiment_run(store):
    experiment = store.put_experiment(DEFINITION)
    run = store.create_experiment_run(experiment, {"revision": 1}, configurations(), replications())
    return experiment, run


def test_experiment_rows_round_trip(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root) as store:
        experiment, run = experiment_run(store)
        assert experiment == canonical_hash("experiment-v1", DEFINITION)
        assert store.put_experiment(DEFINITION) == experiment
        with pytest.raises(ValueError, match="name"):
            store.put_experiment({"schema": 1})
        assert run == m.experiment_run_id(experiment, [item["content_ids"][0] for item in replications()])
        assert store.create_experiment_run(experiment, {"revision": 2}, configurations(), replications()) == run
        assert store.experiment_run(run)["status"] == "running"
    with m.RunStore.open(root) as store:  # reopening pauses the run (D97)
        assert store.experiment(experiment)["definition"] == DEFINITION
        assert store.experiment(experiment)["name"] == "sweep"
        stored = store.experiment_run(run)
        assert stored["experiment"] == experiment and stored["status"] == "paused"
        assert stored["preparation"] == {"revision": 1} and stored["details"] == {}
        assert stored["configurations"] == [{"index": i, **item} for i, item in enumerate(configurations())]
        listed = store.replications(run)
        assert [(item["index"], item["configuration"]) for item in listed] == [(0, 0), (0, 1), (1, 0), (1, 1)]
        assert listed[0] == {
            "configuration": 0,
            "index": 0,
            "seed": 0,
            "worker_kind": None,
            "request_id": None,
            "status": "pending",
            "attempt": None,
            "dispatches": 0,
        }
        assert store.replications(run, status="succeeded") == []
        with pytest.raises(KeyError):
            store.experiment("0" * 64)
        with pytest.raises(KeyError):
            store.experiment_run("0" * 64)
        with pytest.raises(KeyError):
            store.create_experiment_run("0" * 64, {}, configurations(), replications())
        with pytest.raises(ValueError, match="replication"):
            store.create_experiment_run(experiment, {}, configurations(), [{**replications()[0], "configuration": 5}])
        with pytest.raises(ValueError, match="configuration"):
            store.create_experiment_run(experiment, {}, [{"label": "x"}], replications())


def test_replication_status_transitions_are_checked(tmp_path):
    m = api()
    with m.RunStore.open(fixture_copy(tmp_path)) as store:
        _, run = experiment_run(store)
        revision = store.current_revision()
        with pytest.raises(ValueError, match="transition"):
            store.set_replication_status(run, 0, 0, "succeeded")
        with pytest.raises(ValueError, match="status"):
            store.set_replication_status(run, 0, 0, "running")
        with pytest.raises(KeyError):
            store.set_replication_status(run, 0, 9, "cancelled")
        requested = replace(request(schema), layout_revision=revision.number)
        first = store.assign(requested, revision, slot="local-1", pinned=True)
        store.bind_replication(run, 0, 0, first)
        (bound,) = [item for item in store.replications(run, status="dispatched")]
        assert (bound["attempt"], bound["request_id"], bound["worker_kind"], bound["dispatches"]) == (
            first.attempt_id,
            first.request_id,
            "local",
            1,
        )
        with pytest.raises(ValueError, match="pending"):
            store.bind_replication(run, 0, 0, first)
        # Only the coordinator's orderly close returns a dispatched replication to pending, giving the dispatch back.
        with pytest.raises(ValueError, match="transition"):
            store.set_replication_status(run, 0, 0, "pending", attempt=first.attempt_id)
        for status, attempt in (("cancelled", first.attempt_id), ("pending", None)):
            with pytest.raises(ValueError, match="orderly"):
                store.set_replication_status(run, 0, 0, status, attempt=attempt, orderly=True)
        store.set_replication_status(run, 0, 0, "pending", attempt=first.attempt_id, orderly=True)
        assert [(item["status"], item["dispatches"]) for item in store.replications(run)][:1] == [("pending", 0)]
        with pytest.raises(ValueError, match="transition"):
            store.set_replication_status(run, 0, 0, "pending", attempt=first.attempt_id, orderly=True)
        first = store.assign(requested, revision, slot="local-1", pinned=True)
        store.bind_replication(run, 0, 0, first)
        with pytest.raises(ValueError, match="result"):
            store.set_replication_status(run, 0, 0, "succeeded")
        store.set_replication_status(run, 0, 0, "lost", attempt=first.attempt_id)
        store.set_replication_status(run, 0, 0, "pending")
        second = store.assign(requested, revision, slot="local-1", pinned=True)
        other = store.assign(replace(requested, seed=1), revision, slot="local-2", pinned=True)
        with pytest.raises(ValueError, match="request"):
            store.bind_replication(run, 0, 0, other)
        store.bind_replication(run, 0, 0, second)
        with pytest.raises(m.StaleAttempt):
            store.set_replication_status(run, 0, 0, "succeeded", attempt=first.attempt_id)
        advance(store, second, "finalizing")
        commit(store, second, 1.0)
        store.set_replication_status(run, 0, 0, "succeeded", attempt=second.attempt_id)
        assert store.replications(run, status="succeeded")[0]["dispatches"] == 2
        # A stale key cannot be bound, and paused runs dispatch nothing.
        with pytest.raises(m.StaleAttempt):
            store.bind_replication(run, 0, 1, first)
        # Reuse requires a committed result.
        with pytest.raises(ValueError, match="result"):
            store.set_replication_status(run, 1, 0, "reused", attempt=other.attempt_id)
        with pytest.raises(ValueError, match="result"):
            store.set_replication_status(run, 1, 0, "reused")
        store.set_replication_status(run, 1, 0, "reused", attempt=FIXTURE_ATTEMPT)
        assert store.replications(run, status="reused")[0]["attempt"] == FIXTURE_ATTEMPT
        # Lost re-dispatch is bounded: three dispatches, then lost is terminal.
        for _ in range(3):
            key = store.assign(replace(requested, seed=2), revision, slot="local-1", pinned=True)
            store.bind_replication(run, 0, 1, key)
            store.set_replication_status(run, 0, 1, "lost")
            if _ < 2:
                store.set_replication_status(run, 0, 1, "pending")
        with pytest.raises(ValueError, match="dispatch"):
            store.set_replication_status(run, 0, 1, "pending")
        store.set_replication_status(run, 1, 1, "cancelled")
        store.set_run_status(run, "paused")
        with pytest.raises(ValueError, match="running"):
            store.bind_replication(run, 1, 1, other)


def test_run_status_transitions_and_completion(tmp_path):
    m = api()
    with m.RunStore.open(fixture_copy(tmp_path)) as store:
        _, run = experiment_run(store)
        with pytest.raises(ValueError, match="status"):
            store.set_run_status(run, "exploded")
        with pytest.raises(ValueError, match="terminal"):
            store.set_run_status(run, "completed")
        store.set_run_status(run, "paused", {"reason": "restart"})
        assert store.experiment_run(run)["details"] == {"reason": "restart"}
        with pytest.raises(ValueError, match="transition"):
            store.set_run_status(run, "completed")
        store.set_run_status(run, "running")
        assert store.experiment_run(run)["details"] == {"reason": "restart"}
        for configuration, index in ((0, 0), (0, 1), (1, 0)):
            store.set_replication_status(run, configuration, index, "cancelled")
        with pytest.raises(ValueError, match="terminal"):
            store.set_run_status(run, "completed")
        store.set_replication_status(run, 1, 1, "cancelled")
        with pytest.raises(ValueError, match="succeeded"):
            store.set_run_status(run, "completed")
        store.set_run_status(run, "cancelled")
        with pytest.raises(ValueError, match="transition"):
            store.set_run_status(run, "running")
        with pytest.raises(KeyError):
            store.set_run_status("0" * 64, "paused")
        experiment = store.put_experiment({**DEFINITION, "name": "done"})
        done = store.create_experiment_run(experiment, {}, configurations()[:1], replications()[:1])
        store.set_replication_status(done, 0, 0, "reused", attempt=FIXTURE_ATTEMPT)
        store.set_run_status(done, "completed", {"reused": 1})
        assert store.experiment_run(done)["status"] == "completed"


def test_recovery_pauses_running_runs_and_requeues_lost_replications(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root) as store:
        revision = store.create_revision({})
        _, run = experiment_run(store)

        def dispatch(configuration, index, slot):
            key = store.assign(request(schema, seed=10 * configuration + index), revision, slot=slot)
            store.bind_replication(run, configuration, index, key)
            return key

        dispatch(0, 0, "local-1")  # in flight at the crash
        for _ in range(2):
            dispatch(0, 1, "local-2")
            store.set_replication_status(run, 0, 1, "lost")
            store.set_replication_status(run, 0, 1, "pending")
        dispatch(0, 1, "local-2")  # third dispatch, in flight at the crash
        committed = dispatch(1, 0, "local-3")
        advance(store, committed, "finalizing")
        commit(store, committed, 1.0)  # published, but the replication was not updated yet
        failed = dispatch(1, 1, "local-4")
        store.revoke(failed, "failed")
    with m.RunStore.open(root) as store:
        assert store.experiment_run(run)["status"] == "paused"
        assert all(slot["active"] is None for slot in store.slots().values())
        state = {(r["configuration"], r["index"]): (r["status"], r["dispatches"]) for r in store.replications(run)}
        assert state == {
            (0, 0): ("pending", 1),
            (0, 1): ("lost", 3),
            (1, 0): ("succeeded", 1),
            (1, 1): ("failed", 1),
        }
    with m.RunStore.open(root) as store:
        assert store.experiment_run(run)["status"] == "paused"
        assert store.replications(run, status="pending")[0]["dispatches"] == 1


# Replays and pruning --------------------------------------------------------


def test_replay_links_round_trip(tmp_path):
    m = api()
    with m.RunStore.open(fixture_copy(tmp_path)) as store:
        original = store.result(FIXTURE_ATTEMPT).key.request_id
        replayed = "d" * 64
        store.put_replay(replayed, original)
        store.put_replay(replayed, original)
        assert store.replay(replayed) == {
            "request_id": replayed,
            "original": original,
            "attempt": None,
            "status": "pending",
            "verified": None,
            "details": {},
        }
        (inspection,) = [identity for (identity,) in rows(store, "SELECT id FROM requests") if identity != original]
        with pytest.raises(ValueError, match="original"):
            store.put_replay(replayed, inspection)
        with pytest.raises(KeyError):
            store.put_replay("e" * 64, "0" * 64)
        with pytest.raises(KeyError):
            store.replay("e" * 64)
        # An assigned attempt makes the replay running; only that attempt records its outcome.
        with pytest.raises(m.StaleAttempt):
            store.set_replay(replayed, FIXTURE_ATTEMPT, "failed")
        store.put_replay(replayed, original, attempt=FIXTURE_ATTEMPT)
        assert store.replay(replayed)["status"] == "running"
        for status, verified in (("succeeded", None), ("failed", True), ("running", None)):
            with pytest.raises(ValueError, match="outcome"):
                store.set_replay(replayed, FIXTURE_ATTEMPT, status, verified=verified)
        with pytest.raises(KeyError):
            store.set_replay("e" * 64, FIXTURE_ATTEMPT, "failed")
        with pytest.raises(m.StaleAttempt):
            store.set_replay(replayed, "f" * 32, "failed")
        store.set_replay(replayed, FIXTURE_ATTEMPT, "succeeded", verified=False, details={"reason": "digest_mismatch"})
        record = store.replay(replayed)
        assert (record["status"], record["verified"], record["details"]) == (
            "succeeded",
            False,
            {"reason": "digest_mismatch"},
        )
        with pytest.raises(m.StaleAttempt):
            store.set_replay(replayed, FIXTURE_ATTEMPT, "failed")
        pending = store.assign(request(schema, seed=7), store.create_revision({}))
        store.put_replay(replayed, original, attempt=pending.attempt_id)
        with pytest.raises(ValueError, match="committed result"):
            store.set_replay(replayed, pending.attempt_id, "succeeded", verified=True)


def test_recovery_marks_running_replays_of_lost_attempts_lost(tmp_path):
    m = api()
    root = fixture_copy(tmp_path)
    with m.RunStore.open(root) as store:
        original = store.result(FIXTURE_ATTEMPT).key.request_id
        revision = store.create_revision({})
        running = store.assign(request(schema, seed=11), revision, slot="local-1")
        advance(store, running)
        store.put_replay(running.request_id, original, attempt=running.attempt_id)  # in flight at the crash
        done = store.assign(request(schema, seed=12), revision, slot="local-2")
        advance(store, done, "finalizing")
        commit(store, done, 1.0)
        store.put_replay(done.request_id, original, attempt=done.attempt_id)
        store.set_replay(done.request_id, done.attempt_id, "succeeded", verified=True)
    with m.RunStore.open(root) as store:
        lost = store.replay(running.request_id)
        assert lost["status"] == "lost" and lost["attempt"] == running.attempt_id
        assert lost["details"]["message"].startswith("The coordinator stopped")
        kept = store.replay(done.request_id)
        assert (kept["status"], kept["verified"], kept["details"]) == ("succeeded", True, {})


def test_prune_keeps_referenced_results_and_survives_interruption(tmp_path, monkeypatch):
    m = api()
    root = fixture_copy(tmp_path)
    with m.RunStore.open(root) as store:
        revision = store.create_revision({})

        def result(seed, created, slot="interactive"):
            key = store.assign(request(schema, seed=seed), revision, slot=slot)
            advance(store, key, "finalizing")
            commit(store, key, created)
            return key

        unreferenced = result(1, 2e9)  # interactive, but older than the newest 20
        newest = [result(100 + i, 2e9 + 1 + i) for i in range(20)]
        loose = result(2, 1.0, "local-1")
        original = result(3, 1.0, "local-1")
        replayed = result(4, 1.0, "local-1")
        store.put_replay(replayed.request_id, original.request_id)
        replication = result(5, 1.0, "local-1")
        experiment = store.put_experiment(DEFINITION)
        run = store.create_experiment_run(experiment, {}, configurations()[:1], replications()[:1])
        store.set_replication_status(run, 0, 0, "reused", attempt=replication.attempt_id)
        expected = sorted([unreferenced.attempt_id, loose.attempt_id])
        assert store.prune(dry_run=True) == {"results": expected, "snapshots": []}
        assert all((root / "results" / attempt).is_dir() for attempt in expected)
        store.result(loose.attempt_id)

        def crash(path):
            raise OSError("interrupted prune")

        with monkeypatch.context() as patch:
            patch.setattr(m, "_remove", crash)
            with pytest.raises(OSError, match="interrupted"):
                store.prune()
        for attempt in expected:
            with pytest.raises(KeyError):
                store.result(attempt)
            assert (root / "results" / attempt).is_dir()
    with m.RunStore.open(root) as store:
        assert not any((root / "results" / attempt).exists() for attempt in expected)
        kept = [FIXTURE_ATTEMPT, original.attempt_id, replayed.attempt_id, replication.attempt_id]
        kept += [key.attempt_id for key in newest]
        for attempt in kept:
            assert store.result(attempt).path.is_dir()
        assert store.prune() == {"results": [], "snapshots": []}
        extra = result(6, 2e9 + 100, "local-1")
        missing = result(7, 2e9 + 101, "local-1")
        shutil.rmtree(root / "results" / missing.attempt_id)  # removed by hand: pruning still succeeds
        assert store.prune() == {"results": sorted([extra.attempt_id, missing.attempt_id]), "snapshots": []}
        assert not (root / "results" / extra.attempt_id).exists()


def test_lost_replications_can_be_cancelled_and_runs_without_success_fail(tmp_path):
    m = api()
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        _, run = experiment_run(store)
        key = store.assign(request(schema, seed=0), revision, slot="local-1", pinned=True)
        store.bind_replication(run, 0, 0, key)
        store.set_replication_status(run, 0, 0, "lost", attempt=key.attempt_id)
        store.set_replication_status(run, 0, 0, "cancelled")
        for configuration, index in ((0, 1), (1, 0), (1, 1)):
            store.set_replication_status(run, configuration, index, "cancelled")
        with pytest.raises(ValueError, match="succeeded"):
            store.set_run_status(run, "completed")
        store.set_run_status(run, "failed")
        with pytest.raises(ValueError, match="transition"):
            store.set_run_status(run, "running")
        experiment = store.put_experiment({**DEFINITION, "name": "open"})
        pending = store.create_experiment_run(experiment, {}, configurations()[:1], replications()[:1])
        with pytest.raises(ValueError, match="terminal"):
            store.set_run_status(pending, "failed")
        store.set_replication_status(pending, 0, 0, "reused", attempt=commit_result(store, revision))
        with pytest.raises(ValueError, match="cannot fail"):
            store.set_run_status(pending, "failed")


def commit_result(store, revision):
    key = store.assign(request(schema, seed=99), revision, slot="local-9", pinned=True)
    advance(store, key, "finalizing")
    commit(store, key, 1.0)
    return key.attempt_id


def test_prune_removes_snapshots_referenced_by_no_request(tmp_path):
    m = api()
    root = tmp_path / ".simulatte"
    with m.RunStore.open(root) as store:
        revision = store.create_revision({})
        requested = request(schema)
        store.assign(requested, revision)
        prepared = replace(requested, seed=9, source_hash="5" * 64, input_hash="6" * 64)
        with store._transaction() as db:
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?)",
                (schema.request_id(prepared), "preparation", m._blob({"request": prepared.to_dict()})),
            )
        requested = replace(requested, runtime={**requested.runtime, "trusted_runtime_hash": "4" * 64})
        store.assign(requested, revision)
        kept = [requested.source_hash, requested.input_hash, "4" * 64, "5" * 64, "6" * 64]
        for identity in [*kept, "7" * 64]:
            (root / "snapshots" / identity).mkdir()
            (root / "snapshots" / identity / "source.tar").write_bytes(b"tar")
        (root / "snapshots" / "not-a-snapshot").mkdir()
        assert store.prune(dry_run=True) == {"results": [], "snapshots": ["7" * 64]}
        assert (root / "snapshots" / ("7" * 64)).is_dir()
        assert store.prune() == {"results": [], "snapshots": ["7" * 64]}
        assert sorted(path.name for path in (root / "snapshots").iterdir()) == sorted([*kept, "not-a-snapshot"])


def test_delete_experiment_run_removes_rows_and_only_its_unreferenced_results(tmp_path):
    m = api()
    root = fixture_copy(tmp_path)
    with m.RunStore.open(root) as store:
        revision = store.create_revision({})
        experiment, run = experiment_run(store)
        other = store.create_experiment_run(
            store.put_experiment({**DEFINITION, "name": "other"}), {}, configurations()[:1], replications()[:1]
        )
        owned = store.assign(request(schema, seed=0), revision, slot="local-1", pinned=True)
        store.bind_replication(run, 0, 0, owned)
        advance(store, owned, "finalizing")
        commit(store, owned, 1.0)
        store.set_replication_status(run, 0, 0, "succeeded", attempt=owned.attempt_id)
        shared = store.assign(request(schema, seed=1), revision, slot="local-1", pinned=True)
        advance(store, shared, "finalizing")
        commit(store, shared, 1.0)
        store.set_replication_status(run, 0, 1, "reused", attempt=shared.attempt_id)
        store.set_replication_status(other, 0, 0, "reused", attempt=shared.attempt_id)
        store.set_replication_status(run, 1, 0, "reused", attempt=FIXTURE_ATTEMPT)
        with pytest.raises(ValueError, match="running"):
            store.delete_experiment_run(run)
        store.set_run_status(run, "paused")
        assert store.delete_experiment_run(run) == {"results": [owned.attempt_id]}
        with pytest.raises(KeyError):
            store.experiment_run(run)
        with pytest.raises(KeyError):
            store.delete_experiment_run(run)
        with pytest.raises(KeyError):
            store.experiment(experiment)
        assert not (root / "results" / owned.attempt_id).exists()
        assert store.result(shared.attempt_id).path.is_dir() and store.result(FIXTURE_ATTEMPT).path.is_dir()
        assert store.experiment_run(other)["status"] == "running"
