"""Saved presentation revisions and export jobs: immutable, reference-holding records in the schema 5 store."""

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

from simulatte.analysis import codec
from simulatte.analysis.definition import AnalysisDefinition
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.project_store import ProjectStore
from simulatte.execution.store import RunStore, _blob
from simulatte.execution.store_reader import StoreReader
from simulatte.model import ParameterSchema
from simulatte.presentations.schema import ManifestError, output_content_hash, snapshot_hash
from simulatte.presentations.store import ExportJob, PresentationStore
from tests.core.test_experiment_results import seeded
from tests.execution.test_store_slots import advance, commit

FIXTURES = Path(__file__).parents[1] / "fixtures"
# Committed full trace with a layout bundle; its finite time range is [0, 2].
TRACE = FIXTURES / "stores/v1/results/e1f70f254a0c77694dc2e5362d7e8168/run.simtrace"
OPTIONS = {"start": 0, "end": 1.5, "fps": 30, "rate": 1, "width": 1920, "height": 1080, "format": "mp4"}


def record(store, seed, trace=TRACE):
    """A committed result outside replications and the interactive slot: only references keep it."""
    revision = store.current_revision()
    key = store.assign(replace(seeded(9, seed), layout_revision=revision.number), revision, slot="local-1", pinned=True)
    advance(store, key, "finalizing")
    commit(store, key, 1.0)
    shutil.copyfile(trace, store.root / "results" / key.attempt_id / "run.simtrace")
    return key


def saved_output(store, attempt, extra=(), generation="env"):
    """A succeeded analysis over ``attempt`` with one number output, then ``extra`` descriptors."""
    analysis = AnalysisStore(store)
    definition = analysis.put_definition(
        AnalysisDefinition("lateness", "report.py:analyze", "a" * 64, ParameterSchema(()), ())
    )
    if generation == "env":
        with store._transaction() as db:
            db.execute("INSERT OR IGNORE INTO environment_generations VALUES (?,?,?)", ("env", _blob({}), "prepared"))
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    execution = analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=generation)
    descriptor = {
        "id": hashlib.sha256(execution.id.encode()).hexdigest(),
        "name": "total",
        "kind": "number",
        "artifacts": [{"path": "number.json", "length": 2, "sha256": "8" * 64}],
        "metadata": {"partial_snapshot": False, "planned": 1, "available": 1, "unit": "h"},
        "renderer": None,
    }
    outputs = [descriptor, *({**descriptor, **changes} for changes in extra)]
    manifest = {"schema": "analysis-output-v1", "execution_id": execution.id, "outputs": outputs, "provenance": {}}
    with store._transaction() as db:
        db.execute("INSERT INTO analysis_outputs VALUES (?,?)", (execution.id, codec.dumps(manifest)))
        db.execute("UPDATE analysis_executions SET state='succeeded' WHERE id=?", (execution.id,))
    return execution, descriptor


def evidence(execution, descriptor, **changes):
    return {
        "id": str(uuid4()),
        "execution_id": execution.id,
        "output_id": descriptor["id"],
        "content_hash": output_content_hash(descriptor),
        "kind": descriptor["kind"],
        "selection": {},
        "labels": {
            "name": descriptor["name"],
            "unit": "h",
            "population": None,
            "sample": {"planned": 1, "available": 1, "partial": False},
            "convention": "descriptive",
            "source": {
                "analysis_name": "Report",
                "snapshot_hash": execution.snapshot_hash,
                "execution_id": execution.id,
            },
        },
        **changes,
    }


def manifest(key, items=(), **changes):
    document = json.loads((FIXTURES / "presentations/valid-minimal.json").read_text())
    document.update(
        {
            "recording": {
                "attempt_id": key.attempt_id,
                "request_id": key.request_id,
                "trace_sha256": hashlib.sha256(TRACE.read_bytes()).hexdigest(),
            },
            "time": {"start": 0, "end": 1.5},
            "evidence": list(items),
            **changes,
        }
    )
    return document


@pytest.fixture
def setup(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        store.create_revision({})
        key = record(store, 1)
        execution, descriptor = saved_output(store, record(store, 2).attempt_id)
        yield store, PresentationStore(store), key, execution, descriptor


def owned(store, kind, identity):
    return sorted(
        tuple(row)
        for row in store._db.execute(
            'SELECT target_kind,target_id FROM "references" WHERE owner_kind=? AND owner_id=?', (kind, identity)
        )
    )


def test_revisions_are_immutable_and_stale_edits_refused(setup):
    store, presentations, key, execution, descriptor = setup
    item = evidence(execution, descriptor)
    identity = presentations.create(manifest(key, [item]))
    first = presentations.get(identity)
    assert (first["id"], first["revision"], first["evidence"]) == (identity, 1, [item])
    assert first["created"] > 0 and first["name"] == "Selected policy"
    assert presentations.revise(identity, 1, manifest(key, name="Second view", rate=2.0)) == 2
    with pytest.raises(ValueError, match="stale revision"):
        presentations.revise(identity, 1, manifest(key, name="Lost edit"))
    assert presentations.get(identity, 1) == first
    second = presentations.get(identity)
    assert (second["revision"], second["name"], second["rate"], second["evidence"]) == (2, "Second view", 2, [])
    assert [r["revision"] for r in presentations.revisions(identity)] == [1, 2]
    assert all(len(r["content_hash"]) == 64 for r in presentations.revisions(identity))
    with pytest.raises(sqlite3.IntegrityError, match="immutable presentation record"):
        with store._transaction() as db:
            db.execute("UPDATE presentation_revisions SET created=0 WHERE id=?", (identity,))
    assert owned(store, "presentation-revision", f"{identity}:1") == sorted(
        [
            ("analysis", execution.id),
            ("analysis-snapshot", execution.snapshot_hash),
            ("request", key.request_id),
            ("result", key.attempt_id),
        ]
    )
    assert owned(store, "presentation-revision", f"{identity}:2") == [
        ("request", key.request_id),
        ("result", key.attempt_id),
    ]
    with pytest.raises(ValueError, match="revision 0"):
        presentations.create({**manifest(key), "revision": 3})
    with pytest.raises(ValueError, match="another presentation"):
        presentations.revise(identity, 2, {**manifest(key), "id": str(uuid4())})
    assert presentations.revise(identity, 2, {**manifest(key), "id": identity, "revision": 2}) == 3
    for bad in (0, "3", True):
        with pytest.raises(ValueError, match="revision"):
            presentations.revise(identity, bad, manifest(key))
    with pytest.raises(KeyError):
        presentations.revise(str(uuid4()), 1, manifest(key))
    with pytest.raises(ManifestError) as caught:
        presentations.create(manifest(key, rate=0))
    assert caught.value.path == "rate"
    for bad in ((identity, 9), (identity, "1"), (str(uuid4()), None)):
        with pytest.raises(KeyError):
            presentations.get(*bad)


def test_list_pages_newest_first_with_export_counts(setup):
    store, presentations, key, _, _ = setup
    ids = [presentations.create(manifest(key, name=f"View {i}")) for i in range(3)]
    presentations.revise(ids[0], 1, manifest(key, name="View 0 again"))
    job = presentations.admit_export(ids[0], 2, "video", OPTIONS, [])
    page = presentations.list(limit=2)
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
    rest = presentations.list(page["next_cursor"], 2)
    assert rest["next_cursor"] is None
    (last,) = rest["items"]
    assert last == {
        "id": ids[0],
        "name": "View 0 again",
        "revision": 2,
        "created": last["created"],
        "recording": {"attempt_id": key.attempt_id},
        "exports": {"active": 1, "total": 1},
    }
    presentations.cancel_export(job.id)
    assert presentations.list(limit=3)["items"][2]["exports"] == {"active": 0, "total": 1}
    for cursor, limit in ((None, 0), (None, 101), ("x", 5), ("01", 5), (None, True)):
        with pytest.raises(ValueError):
            presentations.list(cursor, limit)


def test_missing_or_changed_references_are_refused(setup):
    store, presentations, key, execution, descriptor = setup
    other = record(store, 3)
    trace = hashlib.sha256(TRACE.read_bytes()).hexdigest()
    cases = [
        (
            manifest(key, recording={"attempt_id": "f" * 32, "request_id": key.request_id, "trace_sha256": trace}),
            "no saved result",
        ),
        (
            manifest(
                key, recording={"attempt_id": key.attempt_id, "request_id": other.request_id, "trace_sha256": trace}
            ),
            "another request",
        ),
        (manifest(key, recording={**manifest(key)["recording"], "trace_sha256": "0" * 64}), "trace differs"),
        (manifest(key, time={"start": 0, "end": 2.5}), "outside the recording"),
        (manifest(key, time={"start": -1, "end": 1}), "outside the recording"),
        (manifest(key, [evidence(execution, descriptor, execution_id=str(uuid4()))]), "unavailable"),
        (manifest(key, [evidence(execution, descriptor, output_id="0" * 64)]), "no output"),
        (manifest(key, [evidence(execution, descriptor, content_hash="0" * 64)]), "changed"),
        (manifest(key, [evidence(execution, descriptor, kind="image")]), "is a number"),
    ]
    labels = evidence(execution, descriptor)
    labels["labels"]["source"]["snapshot_hash"] = "0" * 64
    cases.append((manifest(key, [labels]), "another input snapshot"))
    labels = evidence(execution, descriptor)
    labels["labels"]["source"]["execution_id"] = str(uuid4())
    cases.append((manifest(key, [labels]), "another analysis"))
    for document, message in cases:
        with pytest.raises(ValueError, match=message):
            presentations.create(document)
    nonfinite, unhashable = saved_output(
        store, record(store, 4).attempt_id, ({"id": "d" * 64, "metadata": {"x": math.nan}},)
    )
    item = evidence(nonfinite, {**unhashable, "id": "d" * 64})
    with pytest.raises(ValueError, match="changed"):
        presentations.create(manifest(key, [{**item, "content_hash": "0" * 64}]))
    with store._transaction() as db:
        db.execute("UPDATE analysis_executions SET state='failed' WHERE id=?", (execution.id,))
    with pytest.raises(ValueError, match="no saved output"):
        presentations.create(manifest(key, [evidence(execution, descriptor)]))
    damaged = store.root / "results" / key.attempt_id / "run.simtrace"
    damaged.write_bytes(b"not a trace")
    corrupt = manifest(
        key, recording={**manifest(key)["recording"], "trace_sha256": hashlib.sha256(b"not a trace").hexdigest()}
    )
    with pytest.raises(ValueError, match="unreadable"):
        presentations.create(corrupt)
    damaged.unlink()
    with pytest.raises(ValueError, match="trace is missing"):
        presentations.create(manifest(key))
    assert store._db.execute("SELECT COUNT(*) FROM presentations").fetchone()[0] == 0


def test_references_protect_recording_and_evidence_until_delete_and_prune(setup):
    store, presentations, key, execution, descriptor = setup
    identity = presentations.create(manifest(key, [evidence(execution, descriptor)]))
    assert store.prune()["results"] == []
    assert store.result(key.attempt_id).path.is_dir()
    with pytest.raises(ValueError, match="referenced"):
        AnalysisStore(store).delete(execution.id)
    with pytest.raises(ValueError, match="referenced"):
        _delete_referenced(store, presentations, identity)
    presentations.delete(identity)
    with pytest.raises(KeyError):
        presentations.get(identity)
    assert presentations.get(identity, 1)["id"] == identity  # readable for terminal exports until prune
    assert presentations.list()["items"] == []
    with pytest.raises(KeyError):
        presentations.delete(identity)
    with pytest.raises(KeyError):
        presentations.admit_export(identity, 1, "video", OPTIONS, [])
    AnalysisStore(store).delete(execution.id)
    # The analysis input's result goes with its analysis; the recording goes with the presentation.
    assert key.attempt_id in store.prune()["results"]
    assert not (store.root / "results" / key.attempt_id).exists()
    for table in ("presentations", "presentation_revisions"):
        assert store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(KeyError):
        presentations.get(identity, 1)


def _delete_referenced(store, presentations, identity):
    """Another durable owner names the presentation: deletion is refused until that owner goes."""
    with store._transaction() as db:
        db.execute('INSERT INTO "references" VALUES (?,?,?,?)', ("model", "m", "presentation", identity))
    try:
        presentations.delete(identity)
    finally:
        with store._transaction() as db:
            db.execute("DELETE FROM \"references\" WHERE owner_kind='model'")


def test_exports_pin_revision_and_evidence_until_terminal(setup):
    store, presentations, key, execution, descriptor = setup
    item = evidence(execution, descriptor)
    identity = presentations.create(manifest(key, [item]))
    job = presentations.admit_export(identity, 1, "video", OPTIONS, [])
    assert isinstance(job, ExportJob) and job.state == "waiting" and job.revision == 1
    assert job.snapshot_hash == snapshot_hash(presentations.get(identity, 1), "video", OPTIONS, [])
    assert sorted(job.references) == sorted(
        [*owned(store, "presentation-revision", f"{identity}:1"), ("presentation-revision", f"{identity}:1")]
    )
    # Later edits never change an admitted export.
    presentations.revise(identity, 1, manifest(key, name="Edited"))
    assert presentations.export(job.id) == job
    with pytest.raises(ValueError, match="active exports"):
        presentations.delete(identity)
    with pytest.raises(ValueError, match="Start the export"):
        presentations.finish_export(job.id, {"state": "succeeded", "output": _output()})
    running = presentations.start_export(job.id)
    assert running.state == "running"
    with pytest.raises(ValueError, match="running"):
        presentations.start_export(job.id)
    for outcome in (
        {"state": "succeeded"},
        {"state": "lost"},
        {"state": "failed", "output": _output()},
        {"state": "failed", "message": 3},
        {"state": "succeeded", "output": {**_output(), "size": -1}},
        {"state": "succeeded", "output": {**_output(), "extra": 1}},
        {"state": "succeeded", "output": {**_output(), "sha256": "x"}},
        {"state": "succeeded", "output": {**_output(), "name": ""}},
        {"state": "succeeded", "output": {**_output(), "media_type": 1}},
        {"state": "failed", "other": 1},
        "succeeded",
    ):
        with pytest.raises(ValueError):
            presentations.finish_export(job.id, outcome)
    done = presentations.finish_export(job.id, {"state": "succeeded", "message": "Saved", "output": _output()})
    assert (done.state, done.references, done.details["output"], done.details["message"]) == (
        "succeeded",
        (),
        _output(),
        "Saved",
    )
    assert done.finished is not None and owned(store, "export-job", job.id) == []
    with pytest.raises(ValueError, match="succeeded"):
        presentations.finish_export(job.id, {"state": "failed"})
    assert presentations.cancel_export(job.id) == done
    waiting = presentations.admit_export(identity, 2, "studio", {}, [])
    cancelled = presentations.cancel_export(waiting.id)
    assert cancelled.state == "cancelled" and cancelled.references == ()
    failed = presentations.admit_export(identity, 2, "html", {"panel": True}, [])
    assert presentations.finish_export(failed.id, {"state": "failed", "message": "Too large"}).state == "failed"
    assert [j.id for j in presentations.exports(identity)] == [failed.id, waiting.id, job.id]
    assert [j.id for j in presentations.exports()][:1] == [failed.id]
    assert presentations.exports(str(uuid4())) == []
    presentations.delete(identity)
    assert presentations.export(job.id).state == "succeeded"
    store.prune()
    with pytest.raises(KeyError):
        presentations.export(job.id)


def _output():
    return {"name": "presentation.mp4", "size": 1024, "sha256": "9" * 64, "media_type": "video/mp4"}


def test_export_admission_validates_target_options_and_statics(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        store.create_revision({})
        key = record(store, 1)
        extra = ({"id": "f" * 64, "kind": "figure"}, {"id": "e" * 64, "kind": "image"})
        execution, descriptor = saved_output(store, record(store, 2).attempt_id, extra)
        figure, image = ({**descriptor, **changes} for changes in extra)
        presentations = PresentationStore(store)
        items = [
            evidence(execution, figure, selection={"figure": {"width": 960, "height": 540}}),
            evidence(execution, image),
        ]
        identity = presentations.create(manifest(key, items))
        statics = [
            {"evidence_id": items[0]["id"], "media_type": "image/png", "sha256": "1" * 64, "size": 10},
            {"evidence_id": items[1]["id"], "media_type": "image/svg+xml", "sha256": "2" * 64, "size": 20},
        ]
        job = presentations.admit_export(identity, 1, "html", OPTIONS, statics)
        assert job.details == {"statics": statics} and job.options == OPTIONS
        assert presentations.admit_export(identity, 1, "studio", OPTIONS, []).state == "waiting"
        bad = [
            ("print", OPTIONS, statics, "target"),
            ("video", [], statics, "options"),
            ("video", {"x": float("nan")}, statics, "options"),
            ("video", {"x": "y" * 70000}, statics, "options"),
            ("video", OPTIONS, statics[:1], "static view"),
            ("video", OPTIONS, statics + statics[:1], "once"),
            ("video", OPTIONS, "png", "statics"),
            ("video", OPTIONS, [{**statics[0], "evidence_id": str(uuid4())}], "not selected"),
            ("video", OPTIONS, [{**statics[0], "media_type": "image/svg+xml"}, statics[1]], "image/png"),
            ("video", OPTIONS, [statics[0], {**statics[1], "media_type": "text/html"}], "media type"),
            ("video", OPTIONS, [{**statics[0], "sha256": "z"}, statics[1]], "sha256"),
            ("video", OPTIONS, [{**statics[0], "size": -1}, statics[1]], "size"),
            ("video", OPTIONS, [{**statics[0], "extra": 1}, statics[1]], "fields"),
        ]
        for target, options, chosen, message in bad:
            with pytest.raises(ValueError, match=message):
                presentations.admit_export(identity, 1, target, options, chosen)
        value = evidence(execution, descriptor)
        number = presentations.create(manifest(key, [value]))
        with pytest.raises(ValueError, match="number"):
            presentations.admit_export(number, 1, "video", OPTIONS, [{**statics[0], "evidence_id": value["id"]}])
        with pytest.raises(KeyError):
            presentations.admit_export(identity, 7, "video", OPTIONS, statics)
        for call in (presentations.start_export, presentations.cancel_export, presentations.export):
            with pytest.raises(KeyError):
                call(str(uuid4()))
        with pytest.raises(KeyError):
            presentations.finish_export(str(uuid4()), {"state": "failed"})


def test_recovery_marks_active_exports_lost_and_releases_their_leases(setup):
    store, presentations, key, execution, descriptor = setup
    identity = presentations.create(manifest(key, [evidence(execution, descriptor)]))
    waiting = presentations.admit_export(identity, 1, "video", OPTIONS, [])
    running = presentations.start_export(presentations.admit_export(identity, 1, "html", {}, []).id)
    store.recover()
    for job in (waiting, running):
        lost = presentations.export(job.id)
        assert (lost.state, lost.references) == ("lost", ()) and lost.finished is not None
        with pytest.raises(ValueError, match="lost"):
            presentations.finish_export(job.id, {"state": "succeeded", "output": _output()})
    assert store._db.execute("SELECT COUNT(*) FROM \"references\" WHERE owner_kind='export-job'").fetchone()[0] == 0
    # The revision itself still holds its references; deletion is allowed now.
    assert owned(store, "presentation-revision", f"{identity}:1")
    presentations.delete(identity)


def tree(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_saving_a_view_leaves_results_byte_identical(setup):
    store, presentations, key, execution, descriptor = setup
    tables = ("results", "kpis", "artifacts", "attempts", "requests", "analysis_executions", "analysis_outputs")

    def state():
        rows = {
            table: [tuple(r) for r in store._db.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in tables
        }
        return rows, tree(store.root / "results"), tree(store.root / "analysis-results")

    before = state()
    identity = presentations.create(manifest(key, [evidence(execution, descriptor)]))
    presentations.revise(identity, 1, manifest(key, name="Saved again"))
    presentations.finish_export(
        presentations.admit_export(identity, 2, "video", OPTIONS, []).id, {"state": "cancelled"}
    )
    assert state() == before


def test_new_reference_kinds_link_through_the_project_store(setup):
    store, presentations, key, _, _ = setup
    identity = presentations.create(manifest(key))
    job = presentations.admit_export(identity, 1, "video", OPTIONS, [])
    links = ProjectStore(store)
    links.link("export-job", job.id, "presentation", identity)
    links.link("presentation", identity, "presentation-revision", f"{identity}:1")
    for kind, bad in (
        ("presentation-revision", f"{identity}:2"),
        ("presentation-revision", f"{identity}:x"),
        ("presentation-revision", identity),
        ("presentation", str(uuid4())),
        ("export-job", str(uuid4())),
    ):
        with pytest.raises(KeyError):
            links.link("presentation", identity, kind, bad)


def test_readers_list_and_get_but_never_write(setup):
    store, presentations, key, _, _ = setup
    identity = presentations.create(manifest(key))
    job = presentations.admit_export(identity, 1, "video", OPTIONS, [])
    root = store.root
    store.close()
    with closing(StoreReader.open(root)) as reader:
        assert reader.version == 5
        view = PresentationStore(reader)
        assert [item["id"] for item in view.list()["items"]] == [identity]
        assert view.get(identity)["revision"] == 1 and view.export(job.id).state == "waiting"
        with pytest.raises(ValueError, match="read-only"):
            view.create(manifest(key))
        with pytest.raises(KeyError):
            view.revisions(str(uuid4()))
        with pytest.raises(ValueError, match="limit"):
            view.exports(limit=0)
    with closing(sqlite3.connect(root / "index.sqlite", isolation_level=None)) as db:
        for table in ("export_jobs", "presentation_revisions", "presentations"):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=4")
    with closing(StoreReader.open(root)) as reader:
        old = PresentationStore(reader)
        assert old.list() == {"items": [], "next_cursor": None} and old.exports() == []
        for read in (old.get, old.revisions, old.export):
            with pytest.raises(KeyError):
                read(identity)


def test_schema_four_backup_and_migration_preserve_identities(setup):
    store, presentations, key, execution, _ = setup
    root = store.root.parent / "v4"
    with RunStore.open(root) as old:
        old.create_revision({"source": "unchanged"})
        kept = record(old, 1)
        with old._transaction() as db:
            for name in ("export_jobs", "presentation_revisions", "presentations"):
                db.execute(f"DROP TABLE {name}")
            db.execute("PRAGMA user_version=4")
        before = {
            table: [tuple(r) for r in old._db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("revisions", "requests", "attempts", "results", '"references"')
        }
    with RunStore.open(root) as migrated:
        assert migrated.schema_version == 5
        for table, rows in before.items():
            assert [tuple(r) for r in migrated._db.execute(f"SELECT * FROM {table} ORDER BY rowid")] == rows
        identity = PresentationStore(migrated).create(manifest(kept))
        assert PresentationStore(migrated).get(identity)["revision"] == 1
    backup = next((root / "backups").glob("schema-4-*/index.sqlite"))
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT name FROM sqlite_master WHERE name='presentations'").fetchone() is None
    assert json.loads(backup.with_name("manifest.json").read_text())["schema"] == 4
    fresh = sorted(
        map(tuple, store._db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    )
    with closing(sqlite3.connect(root / "index.sqlite")) as db:
        assert sorted(db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")) == fresh
