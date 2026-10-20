"""Immutable candidate admission and retention share the publication transaction."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
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
            for name in ("export_jobs", "presentation_revisions", "presentations"):
                db.execute(f"DROP TABLE {name}")
            names = [
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'analysis_%'")
            ]
            for name in reversed(names):
                db.execute(f"DROP TABLE {name}")
            db.execute("PRAGMA user_version=3")
    with RunStore.open(root) as migrated:
        assert migrated.schema_version == 5 and migrated.current_revision().number == 1
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
    with pytest.raises(ValueError, match="insufficient") as refused:
        admit(analysis, absent, preview)
    assert f"0 of 1 replications valid, 1 required; replication 0 (attempt {attempt}) lacks fields: family" in str(
        refused.value
    )
    assert refused.value.details["unavailable"] == [
        {"index": 0, "attempt": attempt, "missing_fields": ["family"], "reason": "fields not retained"}
    ]
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
        assert reader.version == 5
    with sqlite3.connect(root / "index.sqlite") as db:
        db.execute("PRAGMA user_version=6")
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
    attempts = []
    for i in range(2):
        attempt = publish(store, run, 0, i, 3)
        attempts.append(attempt)
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
    with pytest.raises(ValueError, match="insufficient input for dataset jobs") as refused:
        admit(analysis, definition, preview)
    # The refusal names the counts and the fields each invalid replication lacks.
    assert (
        f"1 of 2 replications valid, 2 required; replication 1 (attempt {attempts[1]}) lacks fields: lateness"
        in str(refused.value)
    )
    assert refused.value.details == {
        "dataset": "jobs",
        "run": run,
        "configuration": 0,
        "valid": 1,
        "replications": 2,
        "required": 2,
        "require_complete": "require_complete" in policy,
        "unavailable": [
            {"index": 1, "attempt": attempts[1], "missing_fields": ["lateness"], "reason": "fields not retained"}
        ],
    }
    # The relaxed policy can use one valid replication; no evidence is silently removed.
    relaxed = admit(analysis, definition, analysis.preview(Selection(experiment_run_ids=(run,), configurations=(0,))))
    assert len(analysis.snapshot(relaxed.snapshot_hash)["replications"]) == 2


def index(store, attempt, files):
    """Register result files as published artifacts, optionally with an overridden descriptor."""
    import hashlib

    root = store.root / "results" / attempt / "artifacts"
    with store._transaction() as db:
        for path, override in files.items():
            raw = (root / path).read_bytes() if (root / path).is_file() else b""
            descriptor = {"length": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), **override}
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?)", (attempt, path, descriptor["length"], descriptor["sha256"])
            )


def dataset(root, name, schema="jobs"):
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.types import DatasetSchema, Field

    writer = DatasetWriter(root / "datasets" / name, DatasetSchema(schema, (Field("lateness", "float64"),)))
    writer.append({"lateness": 1.5})
    writer.finish()
    return sorted(p.relative_to(root).as_posix() for p in (root / "datasets" / name).iterdir())


@pytest.mark.parametrize(
    "case, message",
    [
        ("removed", "Evidence files removed"),
        ("escape", "Unsafe evidence path"),
        ("absent", "Evidence files removed"),
        ("large", "exceeds 4 MiB"),
        ("changed", "Dataset manifest changed"),
        ("duplicate", "Duplicate dataset name"),
        ("unpublished", "chunk is not published"),
    ],
)
def test_preview_marks_unsafe_or_changed_evidence_unavailable(setup, case, message):
    import shutil

    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    root = store.root / "results" / attempt / "artifacts"
    root.mkdir(exist_ok=True)
    if case == "removed":
        shutil.rmtree(store.root / "results" / attempt)
    elif case == "escape":
        index(store, attempt, {"../outside": {"length": 0}})
    elif case == "absent":
        index(store, attempt, {"absent.bin": {"length": 1}})
    elif case == "large":
        (root / "datasets/big").mkdir(parents=True)
        with (root / "datasets/big/manifest.json").open("wb") as stream:
            stream.truncate((4 << 20) + 1)
        index(store, attempt, {"datasets/big/manifest.json": {}})
    elif case == "changed":
        index(store, attempt, {path: {"sha256": "0" * 64} for path in dataset(root, "jobs")})
    elif case == "duplicate":
        index(store, attempt, {path: {} for path in dataset(root, "a") + dataset(root, "b")})
    else:
        index(store, attempt, {path: {} for path in dataset(root, "jobs") if path.endswith("manifest.json")})
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    assert (preview.available, preview.missing) == (0, 1)
    assert message in analysis.snapshot(preview.snapshot_hash)["replications"][0]["reason"]
    with pytest.raises(ValueError, match="insufficient input"):
        admit(analysis, definition, preview)


def test_unknown_identities_selection_and_source_are_refused(setup):
    from types import SimpleNamespace

    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending", (1, 0): "pending"})
    publish(store, run, 0, 0, 3)
    publish(store, run, 1, 0, 4)
    preview = analysis.preview(Selection(experiment_run_ids=(run,), configurations=(1,)))
    snapshot = analysis.snapshot(preview.snapshot_hash)
    assert [r["configuration"] for r in snapshot["replications"]] == [1] and preview.available == 1
    with pytest.raises(ValueError, match="Unknown configuration"):
        analysis.preview(Selection(experiment_run_ids=(run,), configurations=(2,)))
    for call in (
        lambda: analysis.preview(Selection(experiment_run_ids=("missing",))),
        lambda: analysis.admit("missing", definition_id=definition, parameters={}, generation_id="env"),
        lambda: analysis.definition("missing"),
        lambda: analysis.execution("missing"),
        lambda: analysis.annotate("missing", "note"),
    ):
        with pytest.raises(KeyError):
            call()
    source = SimpleNamespace(content_hash="b" * 64, project=SimpleNamespace(root=store.root.parent))
    with pytest.raises(ValueError, match="source identity/project mismatch"):
        analysis.put_definition(
            AnalysisDefinition("lateness", "report.py:analyze", "a" * 64, ParameterSchema(()), ()), source=source
        )


def test_admission_refuses_short_groups_and_removed_experiment(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending", (0, 1): "pending"})
    publish(store, run, 0, 0, 3)
    short = analysis.preview(Selection(experiment_run_ids=(run,), min_replications=2))
    assert (short.available, short.partial) == (1, True)
    with pytest.raises(ValueError, match="insufficient replication count"):
        admit(analysis, definition, short)
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    store.set_run_status(run, "paused")
    store.delete_experiment_run(run)
    with pytest.raises(ValueError, match="Experiment removed"):
        admit(analysis, definition, preview)


def test_referenced_analysis_is_kept_and_annotations_are_bounded(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    execution = admit(analysis, definition, analysis.preview(Selection(run_ids=(attempt,))))
    for text in (None, "x" * 65537):
        with pytest.raises(ValueError, match="Annotation"):
            analysis.annotate(execution.id, text)
    first, second = analysis.annotate(execution.id, "first"), analysis.annotate(execution.id, "")
    assert first != second
    assert analysis.cancel(execution.id).state == "cancelled"
    # Cancellation is idempotent on terminal executions.
    assert analysis.cancel(execution.id).state == "cancelled"
    with store._transaction() as db:
        db.execute('INSERT INTO "references" VALUES (?,?,?,?)', ("report", "r-1", "analysis", execution.id))
    with pytest.raises(ValueError, match="Analysis is referenced"):
        analysis.delete(execution.id)
    with store._transaction() as db:
        db.execute('DELETE FROM "references" WHERE owner_kind=?', ("report",))
    analysis.delete(execution.id)
    with store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM analysis_annotations").fetchone()[0] == 0
    with pytest.raises(KeyError):
        analysis.execution(execution.id)


def test_measurement_failure_reaches_admission_refusal(setup, monkeypatch):
    import json
    from simulatte.analysis import store as module
    from simulatte.analysis.definition import DatasetRequirement

    store, analysis, _ = setup
    run, _ = build(store, {(0, i): "pending" for i in range(3)})
    failed, bare = publish(store, run, 0, 0, 3), publish(store, run, 0, 1, 4)
    root = store.root / "results" / failed / "artifacts"
    root.mkdir(exist_ok=True)
    paths = dataset(root, "jobs")
    # A manifest beside the failure record does not make the failed measurement valid.
    (root / "datasets/jobs/failure.json").write_text(json.dumps({"failure": "jobs: RuntimeError: boom"}))
    index(store, failed, {path: {} for path in [*paths, "datasets/jobs/failure.json"]})
    # The fixture's configuration 1 has no replications; select the populated one only.
    preview = analysis.preview(Selection(experiment_run_ids=(run,), configurations=(0,)))
    first = analysis.snapshot(preview.snapshot_hash)["replications"][0]
    assert first["available"] and "jobs" not in first["datasets"]
    assert first["dataset_failures"] == {"jobs": "jobs: RuntimeError: boom"}
    definition = analysis.put_definition(
        AnalysisDefinition(
            "jobs", "report.py:run", "a" * 64, ParameterSchema(()), (DatasetRequirement("jobs", ("lateness",)),)
        )
    )
    with pytest.raises(ValueError, match="insufficient input for dataset jobs") as refused:
        admit(analysis, definition, preview)
    assert str(refused.value).endswith(
        f"0 of 3 replications valid, 1 required; replication 0 (attempt {failed}): jobs: RuntimeError: boom; "
        f"replication 1 (attempt {bare}): dataset unavailable; replication 2: pending"
    )
    assert [(r["index"], r["missing_fields"], r["reason"]) for r in refused.value.details["unavailable"]] == [
        (0, ["lateness"], "jobs: RuntimeError: boom"),
        (1, ["lateness"], "dataset unavailable"),
        (2, ["lateness"], "pending"),
    ]
    # Long refusals list a bounded prefix in the message; details keep every replication.
    monkeypatch.setattr(module, "LISTED_REPLICATIONS", 2)
    with pytest.raises(ValueError, match="dataset unavailable; 1 more$") as refused:
        admit(analysis, definition, preview)
    assert len(refused.value.details["unavailable"]) == 3
    # With the empty configuration 1 selected too, groups are checked in sorted order.
    with pytest.raises(ValueError, match="in configuration 0 of run") as refused:
        admit(analysis, definition, analysis.preview(Selection(experiment_run_ids=(run,))))
    assert refused.value.details["configuration"] == 0


@pytest.mark.parametrize("tamper", ["extra", "missing"])
def test_malformed_dataset_manifest_marks_only_its_replication_unavailable(setup, tamper):
    import json

    store, analysis, definition = setup
    run, _ = build(store, {(0, i): "pending" for i in range(2)})
    bad = publish(store, run, 0, 0, 3)
    publish(store, run, 0, 1, 4)
    root = store.root / "results" / bad / "artifacts"
    root.mkdir(exist_ok=True)
    paths = dataset(root, "jobs")
    manifest = root / "datasets/jobs/manifest.json"
    body = json.loads(manifest.read_text())
    if tamper == "extra":
        body["chunks"][0]["extra"] = 1
    else:
        del body["schema"]["name"]
    manifest.write_text(json.dumps(body))
    index(store, bad, {path: {} for path in paths})
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    assert (preview.available, preview.missing) == (1, 1)
    assert analysis.snapshot(preview.snapshot_hash)["replications"][0]["reason"] == "invalid dataset manifest"


def test_delete_and_cancel_remove_output_tree_and_control_file(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    first, second = admit(analysis, definition, preview), admit(analysis, definition, preview)
    control = store.root / "analysis-control"
    (control / (first.id + ".cancel")).touch()
    analysis.cancel(first.id)
    assert not (control / (first.id + ".cancel")).exists()
    # A planted symlink is removed without following it; a missing file is ignored.
    outside = store.root.parent / "outside"
    outside.write_text("keep")
    (control / (second.id + ".cancel")).symlink_to(outside)
    output = store.root / "analysis-results" / second.id
    output.mkdir()
    (output / "number.json").write_text("{}")
    analysis.cancel(second.id)
    assert outside.read_text() == "keep"
    (control / (second.id + ".cancel")).touch()
    analysis.delete(second.id)
    assert not output.exists() and not (control / (second.id + ".cancel")).exists()
    analysis.delete(first.id)
    assert list(control.iterdir()) == []


def test_prune_removes_unadmitted_previews_and_keeps_admitted_snapshots(setup):
    store, analysis, definition = setup
    run, _ = build(store, {(0, i): "pending" for i in range(2)})
    attempt = publish(store, run, 0, 0, 3)
    unadmitted = analysis.preview(Selection(experiment_run_ids=(run,)))
    admitted = analysis.preview(Selection(run_ids=(attempt,)))
    execution = admit(analysis, definition, admitted)
    assert store.prune(dry_run=True) == {"results": [], "snapshots": []}
    with store._transaction() as db:
        assert {r[0] for r in db.execute("SELECT id FROM analysis_previews")} == {unadmitted.id, admitted.id}
    store.prune()
    with store._transaction() as db:
        assert [r[0] for r in db.execute("SELECT id FROM analysis_previews")] == [admitted.id]
    with pytest.raises(KeyError):
        analysis.snapshot(unadmitted.snapshot_hash)
    with pytest.raises(KeyError):
        admit(analysis, definition, unadmitted)
    assert analysis.snapshot(execution.snapshot_hash)["replications"][0]["attempt"] == attempt
    assert analysis.rerun(execution.id).snapshot_hash == execution.snapshot_hash


def test_failure_published_after_snapshot_is_changed_evidence(setup):
    import json

    store, analysis, definition = setup
    run, _ = build(store, {(0, 0): "pending"})
    attempt = publish(store, run, 0, 0, 3)
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    assert "dataset_failures" not in analysis.snapshot(preview.snapshot_hash)["replications"][0]
    root = store.root / "results" / attempt / "artifacts"
    (root / "datasets/jobs").mkdir(parents=True)
    (root / "datasets/jobs/failure.json").write_text(json.dumps({"failure": "jobs: RuntimeError: late"}))
    index(store, attempt, {"datasets/jobs/failure.json": {}})
    with pytest.raises(ValueError, match="Evidence changed"):
        admit(analysis, definition, preview)


def window_execution(store, analysis, definition, replications):
    from simulatte.analysis import codec

    body = {
        "replications": replications,
        "selection": {},
        "available": sum(r["available"] for r in replications),
        "planned": len(replications),
        "partial": False,
        "experiments": [{"unrelated_preparation": "do not decode"}],
    }
    identity = codec.identity(body)
    raw = codec.dumps(body)
    with store._transaction() as db:
        db.execute("INSERT INTO analysis_input_snapshots VALUES (?,?)", (identity, raw))
        db.execute(
            "INSERT INTO analysis_executions VALUES (?,?,?,?,?,?,?,?)",
            ("window-test", definition, identity, "env", codec.dumps({}), "waiting", codec.dumps({}), 1),
        )
    return identity, raw


def test_legacy_window_summary_projects_only_windows_and_caches(setup, monkeypatch):
    from simulatte.analysis import codec, datasets
    from simulatte.analysis import store as module

    store, analysis, definition = setup
    manifest = {
        "window": codec.to_json({"start": 5.0, "end": 10.0}),
        "chunks": [{"unrelated_chunk": str(i)} for i in range(100)],
        "schema": {"unrelated_schema": "do not decode"},
    }
    identity, raw = window_execution(
        store,
        analysis,
        definition,
        [
            {
                "available": True,
                "request": {"stopping": {"warmup": 0.0, "horizon": 10.0}},
                "datasets": {"jobs": {"manifest": manifest}},
            }
            for _ in range(25)
        ],
    )
    original = codec.loads

    def bounded_loads(raw):
        assert "unrelated_" not in str(raw)
        return original(raw)

    monkeypatch.setattr(codec, "loads", bounded_loads)
    monkeypatch.setattr(datasets, "Chunk", lambda **kw: pytest.fail("chunk materialized"))
    result = analysis.get("window-test")["snapshot"]["windows"]
    assert result == {
        "items": [
            {"dataset": None, "start": 0.0, "end": 10.0, "replications": 25},
            {"dataset": "jobs", "start": 5.0, "end": 10.0, "replications": 25},
        ],
        "total": 2,
    }
    monkeypatch.setattr(module, "_WINDOW_SUMMARY", "not SQL: cache must serve this read")
    assert AnalysisStore(store).get("window-test")["snapshot"]["windows"] == result
    with store._transaction() as db:
        assert db.execute("SELECT body FROM analysis_input_snapshots WHERE id=?", (identity,)).fetchone()[0] == raw


@pytest.mark.parametrize("failure", ["none", "replace", "mkdir"])
def test_window_cache_is_disposable_and_keeps_bounded_order(setup, monkeypatch, failure):
    from pathlib import Path
    from simulatte.analysis import codec
    from simulatte.analysis import store as module

    store, analysis, definition = setup
    replications = [
        {"available": True, "request": {"stopping": {"warmup": i, "horizon": 10.0}}, "datasets": {}} for i in range(23)
    ] + [
        {"available": False},
        {
            "available": True,
            "request": {"stopping": {"warmup": float("nan"), "horizon": "unknown"}},
            "datasets": {"jobs": {"manifest": {"window": codec.to_json({"start": 2**60, "end": float("inf")})}}},
        },
        {"available": True, "datasets": {}},
    ]
    identity, _ = window_execution(store, analysis, definition, replications)
    cache = store.root / "analysis-cache" / "windows-v1" / f"{identity}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("invalid cache")

    def fail(*args, **kwargs):
        raise OSError("read-only or full disk")

    if failure == "replace":
        monkeypatch.setattr(module.os, "replace", fail)
    elif failure == "mkdir":
        monkeypatch.setattr(Path, "mkdir", fail)
    result = analysis.get("window-test")["snapshot"]["windows"]
    assert result["total"] == 25
    assert [r["start"] for r in result["items"]] == list(range(20))
    assert len(list(cache.parent.iterdir())) == 1  # no abandoned temporary file
    if failure == "none":
        cache.unlink()
        # Check the tagged/nonfinite values as well as the bounded slice.
        with store._transaction() as db:
            rows = db.execute(module._WINDOW_SUMMARY.replace("LIMIT 20", "LIMIT 100"), (identity,)).fetchall()
        assert dict(rows[-1]) == {"dataset": "jobs", "start": 2**60, "end": None, "replications": 1, "total": 25}
        assert dict(rows[-2]) == {"dataset": None, "start": None, "end": None, "replications": 2, "total": 25}


@pytest.mark.parametrize(
    "cached",
    [
        "[]",
        '{"items": 5, "total": 1}',
        '{"items": [{"dataset": null, "start": true, "end": 1, "replications": 1}], "total": 1}',
        '{"items": [], "total": 0, "extra": 1}',
        '{"items": [], "total": -1}',
        " " * 70000,
    ],
)
def test_window_cache_of_the_wrong_shape_is_recomputed(setup, cached):
    store, analysis, definition = setup
    replications = [{"available": True, "request": {"stopping": {"warmup": 3, "horizon": 10.0}}, "datasets": {}}]
    identity, _ = window_execution(store, analysis, definition, replications)
    cache = store.root / "analysis-cache" / "windows-v1" / f"{identity}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(cached)
    expected = {"items": [{"dataset": None, "start": 3, "end": 10.0, "replications": 1}], "total": 1}
    assert analysis.get("window-test")["snapshot"]["windows"] == expected
    assert json.loads(cache.read_text()) == expected


def test_window_summary_without_available_inputs(setup):
    store, analysis, definition = setup
    window_execution(store, analysis, definition, [{"available": False}])
    assert analysis.get("window-test")["snapshot"]["windows"] == {"items": [], "total": 0}


def test_legacy_window_summary_read_only_leaves_no_cache(setup, monkeypatch):
    from simulatte.execution.store_reader import StoreReader

    store, analysis, definition = setup
    window_execution(store, analysis, definition, [{"available": True}])
    with closing(StoreReader.open(store.root)) as reader:
        assert AnalysisStore(reader).get("window-test")["snapshot"]["windows"] == {
            "items": [{"dataset": None, "start": None, "end": None, "replications": 1}],
            "total": 1,
        }
        from simulatte.analysis import store as module

        monkeypatch.setattr(module, "_WINDOW_SUMMARY", "not SQL: the reader must reuse its aggregate")
        assert AnalysisStore(reader).get("window-test")["snapshot"]["windows"]["total"] == 1
    assert not (store.root / "analysis-cache").exists()


@pytest.mark.parametrize("value, expected", [(True, None), ("5", None), ([], None), (2**60, 2**60), (-5.5, -5.5)])
def test_window_summary_retains_numeric_semantics(setup, value, expected):
    from simulatte.analysis import codec

    store, analysis, definition = setup
    window_execution(
        store,
        analysis,
        definition,
        [
            {
                "available": True,
                "request": {"stopping": {"warmup": value, "horizon": value}},
                "datasets": {"jobs": {"manifest": {"window": codec.to_json({"start": value, "end": value})}}},
            }
        ],
    )
    assert analysis.get("window-test")["snapshot"]["windows"]["items"] == [
        {"dataset": None, "start": expected, "end": expected, "replications": 1},
        {"dataset": "jobs", "start": expected, "end": expected, "replications": 1},
    ]


def test_read_only_window_memory_cache_evicts_old_entries(setup):
    from simulatte.analysis import codec
    from simulatte.execution.store_reader import StoreReader

    store, analysis, definition = setup
    with store._transaction() as db:
        db.executemany(
            "INSERT INTO analysis_input_snapshots VALUES (?,?)",
            [(str(i), codec.dumps({"replications": []})) for i in range(129)],
        )
    with closing(StoreReader.open(store.root)) as reader:
        with reader._transaction() as db:
            for i in range(129):
                assert AnalysisStore(reader)._window_summary(db, str(i)) == {"items": [], "total": 0}
        assert len(reader._analysis_windows) == 128 and "0" not in reader._analysis_windows
