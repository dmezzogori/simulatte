"""Immutable candidate admission and retention share the publication transaction."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import sqlite3
from threading import Event

import pytest

from simulatte.analysis.definition import AnalysisDefinition
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.store import RunStore, _blob
from simulatte.experiments.results import ExperimentReport
from simulatte.model import ParameterSchema
from tests.core.test_experiment_results import build, seeded
from tests.execution.test_store_slots import advance, commit


@pytest.fixture
def setup(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        store.create_revision({})
        analysis = AnalysisStore(store)
        definition = analysis.put_definition(
            AnalysisDefinition("lateness", "report.py:analyze", "a" * 64, ParameterSchema(()), ())
        )
        with store._transaction() as db:
            db.execute("INSERT INTO environment_generations VALUES (?,?,?)", ("env", _blob({}), "selected"))
        yield store, analysis, definition


def admit(analysis, definition, preview):
    return analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id="env")


def publish(store, run, c, i, value):
    revision = store.current_revision()
    key = store.assign(replace(seeded(c, i), layout_revision=revision.number), revision, slot="local-1", pinned=True)
    store.bind_replication(run, c, i, key)
    advance(store, key, "finalizing")
    commit(store, key, 1.0)
    with store._transaction() as db:
        db.execute("INSERT INTO kpis VALUES (?,?,?)", (key.attempt_id, "lateness", _blob(value)))
    store.set_replication_status(run, c, i, "succeeded", attempt=key.attempt_id)
    return key.attempt_id


def test_snapshot_remains_eight_after_resume(setup, monkeypatch):
    store, analysis, definition = setup
    run, _ = build(store, {(0, i): "pending" for i in range(24)})
    ids = [publish(store, run, 0, i, i + 0.5) for i in range(8)]
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    assert (preview.available, preview.planned, preview.missing, preview.partial) == (8, 24, 16, True)
    execution = admit(analysis, definition, preview)
    before = analysis.snapshot(execution.snapshot_hash)
    for i in range(8, 24):
        publish(store, run, 0, i, i + 0.5)
    assert analysis.snapshot(execution.snapshot_hash) == before
    assert [r["attempt"] for r in before["replications"] if r["available"]] == ids
    rerun = analysis.rerun(execution.id)
    assert rerun.id != execution.id and rerun.snapshot_hash == execution.snapshot_hash
    refreshed = analysis.refresh(execution.id)
    assert refreshed.snapshot_hash != execution.snapshot_hash and refreshed.available == 24
    monkeypatch.setattr(store, "replication_kpis", lambda *a: pytest.fail("latest query"))
    report = ExperimentReport.from_snapshot(before)
    assert report.table(0.95)[0].estimate.n == 8
    assert report.table(0.95)[0].estimate.mean == 4
    assert len(report.missing()) == 16


def test_snapshot_admission_races_publication_and_prune(setup, monkeypatch):
    store, analysis, definition = setup
    run, _ = build(store, {(0, i): "pending" for i in range(2)})
    first = publish(store, run, 0, 0, 2**53 - 1)
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    # Publication commits after the preview: admission must retain its unavailable status.
    publish(store, run, 0, 1, 7)
    execution = admit(analysis, definition, preview)
    snap = analysis.snapshot(execution.snapshot_hash)
    assert snap["replications"][1]["status"] == "pending"
    assert snap["replications"][1]["scalars"] == {}
    store.set_run_status(run, "paused")
    with pytest.raises(ValueError, match="referenced"):
        store.delete_experiment_run(run)
    analysis.cancel(execution.id)
    assert store.prune()["results"] == []
    analysis.delete(execution.id)
    # Candidate has no lease. A transaction barrier orders prune before admission.
    standalone = analysis.preview(Selection(run_ids=(first,)))
    locked, release = Event(), Event()
    original = store._drop_results

    def blocked(db, attempts):
        original(db, attempts)
        locked.set()
        assert release.wait(5)

    monkeypatch.setattr(store, "_drop_results", blocked)
    with ThreadPoolExecutor(2) as pool:
        deleting = pool.submit(store.delete_experiment_run, run)
        assert locked.wait(5)
        admitting = pool.submit(admit, analysis, definition, standalone)
        release.set()
        deleting.result()
        with pytest.raises(ValueError, match="fresh preview"):
            admitting.result()


def test_admitted_revisions_and_shared_result_keep_final_owner(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 9)
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    one = admit(analysis, definition, preview)
    two = analysis.rerun(one.id)
    assert (
        analysis.put_definition(AnalysisDefinition("lateness", "other.py:analyze", "b" * 64, ParameterSchema(()), ()))
        != definition
    )
    for item in (one, two):
        analysis.cancel(item.id)
    store.set_run_status(run, "paused")
    store.delete_experiment_run(run)
    analysis.delete(one.id)
    assert store.prune()["results"] == []
    analysis.delete(two.id)
    assert store.prune()["results"] == [attempt]


def test_requirements_and_parameter_validation(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    for selection in (
        Selection(experiment_run_ids=(run,), require_complete=True),
        Selection(experiment_run_ids=(run,), min_replications=2),
    ):
        with pytest.raises(ValueError, match="insufficient"):
            admit(analysis, definition, analysis.preview(selection))
    for args in (
        {},
        {"run_ids": ("a", "a")},
        {"min_replications": -1, "run_ids": ("a",)},
        {"configurations": (True,), "run_ids": ("a",)},
    ):
        with pytest.raises(ValueError):
            Selection(**args)
    with pytest.raises(KeyError):
        analysis.preview(Selection(run_ids=("missing",)))


def test_schema_three_backup_and_recovery_preserve_identities(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    execution = admit(analysis, definition, analysis.preview(Selection(run_ids=(attempt,))))
    with store._transaction() as db:
        db.execute("UPDATE analysis_executions SET state='running' WHERE id=?", (execution.id,))
    store.recover()
    assert analysis.execution(execution.id).state == "lost"
    assert analysis.snapshot(execution.snapshot_hash)["replications"][0]["attempt"] == attempt
    assert store.result(attempt).path.is_dir()
    # Downgrade only an empty independent fixture to genuine v3 DDL.
    root = store.root.parent / "v3"
    with RunStore.open(root) as old:
        old.create_revision({"source": "unchanged"})
        with old._transaction() as db:
            names = [
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'analysis_%'")
            ]
            for name in reversed(names):
                db.execute(f"DROP TABLE {name}")
            db.execute("PRAGMA user_version=3")
    with RunStore.open(root) as migrated:
        assert migrated.schema_version == 4 and migrated.current_revision().number == 1
    backup = next((root / "backups").glob("schema-3-*/index.sqlite"))
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT number FROM revisions").fetchone()[0] == 1


def test_dataset_requirements_and_changed_evidence_rejected(setup):
    import hashlib
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.definition import DatasetRequirement
    from simulatte.analysis.types import DatasetSchema, Field

    store, analysis, _ = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    root = store.root / "results" / attempt
    writer = DatasetWriter(
        root / "artifacts" / "datasets" / "jobs", DatasetSchema("jobs", (Field("lateness", "float64"),))
    )
    writer.append({"lateness": 1.5})
    writer.finish()
    with store._transaction() as db:
        for path in sorted((root / "artifacts").rglob("*")):
            if path.is_file():
                raw = path.read_bytes()
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?)",
                    (
                        attempt,
                        path.relative_to(root / "artifacts").as_posix(),
                        len(raw),
                        hashlib.sha256(raw).hexdigest(),
                    ),
                )
    definition = analysis.put_definition(
        AnalysisDefinition(
            "jobs",
            "report.py:run",
            "a" * 64,
            ParameterSchema(()),
            (DatasetRequirement("jobs", ("lateness",), 1, True),),
        )
    )
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    execution = admit(analysis, definition, preview)
    assert (
        analysis.snapshot(execution.snapshot_hash)["replications"][0]["datasets"]["jobs"]["manifest"]["row_count"] == 1
    )
    absent = analysis.put_definition(
        AnalysisDefinition(
            "missing", "report.py:run", "a" * 64, ParameterSchema(()), (DatasetRequirement("jobs", ("family",)),)
        )
    )
    with pytest.raises(ValueError, match="insufficient"):
        admit(analysis, absent, preview)
    with store._transaction() as db:
        db.execute("UPDATE kpis SET value=? WHERE attempt=?", (_blob(4), attempt))
    with pytest.raises(ValueError, match="fresh preview"):
        admit(analysis, definition, preview)
    with store._transaction() as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE analysis_input_snapshots SET body=? WHERE id=?", (b"{}", preview.snapshot_hash))


def test_admission_wins_deletion_barrier_and_reader_versions(setup, monkeypatch):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    publish(store, run, 0, 0, 4)
    store.set_run_status(run, "paused")
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    locked, release = Event(), Event()
    original = analysis._result

    def blocked(db, attempt):
        result = original(db, attempt)
        locked.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(analysis, "_result", blocked)
    with ThreadPoolExecutor(2) as pool:
        admitted = pool.submit(admit, analysis, definition, preview)
        assert locked.wait(5)
        deleted = pool.submit(store.delete_experiment_run, run)
        release.set()
        execution = admitted.result()
        with pytest.raises(ValueError, match="referenced"):
            deleted.result()
    with pytest.raises(ValueError, match="active"):
        analysis.delete(execution.id)
    assert analysis.execution(execution.id).state == "waiting"


def test_readers_refuse_unknown_schema(tmp_path):
    from simulatte.execution.store_reader import StoreReader

    root = tmp_path / "store"
    with RunStore.open(root):
        pass
    with closing(StoreReader.open(root)) as reader:
        assert reader.version == 4
    with sqlite3.connect(root / "index.sqlite") as db:
        db.execute("PRAGMA user_version=5")
    with pytest.raises(ValueError, match="Unsupported"):
        StoreReader.open(root)


@pytest.mark.parametrize("policy", [{"min_replications": 2}, {"require_complete": True}])
def test_user_policy_applies_to_dataset_valid_inputs(setup, policy):
    import hashlib
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.definition import DatasetRequirement
    from simulatte.analysis.types import DatasetSchema, Field

    store, analysis, _ = setup
    run, _ = build(store, {(0, i): "pending" for i in range(2)})
    for i in range(2):
        attempt = publish(store, run, 0, i, 3)
        root = store.root / "results" / attempt
        # Both results have jobs, but only one retained the required lateness field.
        field = "lateness" if i == 0 else "duration"
        writer = DatasetWriter(
            root / "artifacts" / "datasets" / "jobs", DatasetSchema("jobs", (Field(field, "float64"),))
        )
        writer.append({field: 1.5})
        writer.finish()
        with store._transaction() as db:
            for path in (root / "artifacts").rglob("*"):
                if path.is_file():
                    raw = path.read_bytes()
                    db.execute(
                        "INSERT INTO artifacts VALUES (?,?,?,?)",
                        (
                            attempt,
                            path.relative_to(root / "artifacts").as_posix(),
                            len(raw),
                            hashlib.sha256(raw).hexdigest(),
                        ),
                    )
    definition = analysis.put_definition(
        AnalysisDefinition(
            "jobs", "report.py:run", "a" * 64, ParameterSchema(()), (DatasetRequirement("jobs", ("lateness",)),)
        )
    )
    preview = analysis.preview(Selection(experiment_run_ids=(run,), configurations=(0,), **policy))
    assert preview.available == preview.planned == 2
    snapshot = analysis.snapshot(preview.snapshot_hash)
    assert len(snapshot["replications"]) == 2
    assert snapshot["replications"][1]["datasets"]["jobs"]["manifest"]["schema"]["fields"][0]["name"] == "duration"
    with pytest.raises(ValueError, match="insufficient input for dataset jobs"):
        admit(analysis, definition, preview)
    # The relaxed policy can use one valid replication; no evidence is silently removed.
    relaxed = admit(analysis, definition, analysis.preview(Selection(experiment_run_ids=(run,), configurations=(0,))))
    assert len(analysis.snapshot(relaxed.snapshot_hash)["replications"]) == 2
