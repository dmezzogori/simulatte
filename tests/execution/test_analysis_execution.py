"""Analysis child isolation and durable output visibility."""

from pathlib import Path
import sys
import threading

import pytest

from simulatte.analysis import codec
from simulatte.analysis.definition import AnalysisDefinition
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.snapshot import ProjectSpec, capture_source
from simulatte.execution.store import RunStore
from simulatte.model import ParameterSchema
from simulatte.projects.environments import EnvironmentPool
from tests.core.test_experiment_results import build
from tests.execution.test_analysis_snapshots import publish


def admit_total(root, store, environments, run):
    """Admit the captured ``total`` report over every replication of ``run``."""
    (root / "report.py").write_text("""from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    context.emit('total', Number(sum(row['scalars']['lateness'] for row in context.scalars())))
""")
    generation = environments.use_existing(sys.executable)
    analysis = AnalysisStore(store)
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("total", "report.py:analyze", source.content_hash, ParameterSchema(()), ()),
        source=source,
    )
    preview = analysis.preview(Selection(experiment_run_ids=(run,)))
    return analysis, analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=generation.id)


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        run, _ = build(store, {(0, 0): "pending"})
        publish(store, run, 0, 0, 3)
        environments = EnvironmentPool(root, store)
        analysis, execution = admit_total(root, store, environments, run)
        yield root, store, analysis, environments, execution


def test_captured_analysis_publishes_equal_numerical_artifacts(prepared):
    root, store, analysis, environments, execution = prepared
    # Working source edit after capture cannot change either execution.
    (root / "report.py").write_text("raise RuntimeError('live source imported')")
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "succeeded"
        second = analysis.rerun(execution.id)
        runner.submit(second.id)
        assert runner.wait(second.id).state == "succeeded"
    one, two = analysis.output(execution.id), analysis.output(second.id)
    assert one["outputs"] == two["outputs"]
    assert one["provenance"]["source_hash"] == analysis.definition(execution.definition_id)["source_hash"]
    assert one["provenance"]["input_snapshot"] == execution.snapshot_hash
    assert one["provenance"]["environment_generation"] == execution.generation_id
    artifact = one["outputs"][0]["artifacts"][0]["path"]
    assert codec.loads((store.root / "analysis-results" / execution.id / artifact).read_bytes())["value"] == 3
    assert environments.owners(execution.generation_id) == ()


@pytest.mark.parametrize("body", ["raise RuntimeError('broken')", "import os; os._exit(7)"])
def test_failed_analysis_never_publishes_success(prepared, body):
    root, store, analysis, environments, original = prepared
    (root / "report.py").write_text(
        "def analyze(context, parameters):\n    from simulatte.analysis.outputs import Number\n"
        '    context.emit("early", Number(1))\n    ' + body + "\n"
    )
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("fail", "report.py:analyze", source.content_hash, ParameterSchema(()), ()), source=source
    )
    execution = analysis.admit(
        analysis.refresh(original.id).id, definition_id=definition, parameters={}, generation_id=original.generation_id
    )
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "failed"
    with pytest.raises(KeyError):
        analysis.output(execution.id)
    assert not (store.root / "analysis-results" / execution.id).exists()
    assert store.result(analysis.snapshot(execution.snapshot_hash)["replications"][0]["attempt"]).path.is_dir()


def test_cancel_waiting_and_running_analysis_never_adopts_late_output(prepared, monkeypatch):
    root, store, analysis, environments, execution = prepared
    started, release = threading.Event(), threading.Event()
    original = environments.acquire

    def held(*args):
        result = original(*args)
        started.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(environments, "acquire", held)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert started.wait(10)
        second = analysis.rerun(execution.id)
        runner.submit(second.id)
        runner.cancel(second.id)
        runner.cancel(execution.id)
        release.set()
        assert runner.wait(second.id).state == "cancelled"
        assert runner.wait(execution.id).state == "cancelled"
    for item in (execution, second):
        with pytest.raises(KeyError):
            analysis.output(item.id)
    assert environments.owners(execution.generation_id) == ()


def test_publication_disk_failure_and_recovery_keep_inputs(prepared, monkeypatch):
    from simulatte.analysis import publication

    root, store, analysis, environments, execution = prepared

    def fail(*args):
        raise OSError(28, "injected disk full")

    monkeypatch.setattr(publication, "replace_durable", fail)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "failed"
    loose = store.root / "analysis-results" / "unindexed"
    loose.mkdir()
    (loose / "partial").write_text("partial")
    store.recover()
    assert not loose.exists()
    assert analysis.snapshot(execution.snapshot_hash)["available"] == 1


@pytest.mark.parametrize("stage", ["verified", "renamed", "before_commit", "committed"])
def test_analysis_crash_visibility_is_atomic(prepared, stage):
    import subprocess

    root, store, analysis, environments, execution = prepared
    identity, snapshot_hash = execution.id, execution.snapshot_hash
    store.close()
    script = """
import os, sys
from pathlib import Path
from simulatte.analysis.context import AnalysisContext
from simulatte.analysis.outputs import Number
from simulatte.analysis.store import AnalysisStore
from simulatte.analysis.publication import publish
from simulatte.execution.store import RunStore
root=Path(sys.argv[1]); identity=sys.argv[2]; stage=sys.argv[3]
with RunStore.open(root) as store:
    analysis=AnalysisStore(store)
    item=analysis.execution(identity)
    output=root/'analysis-staging'/identity
    context=AnalysisContext(analysis.snapshot(item.snapshot_hash), input_dir=root/'unused', output_dir=output)
    context.emit('value', Number(3))
    context.finish(identity, {})
    with store._transaction() as db:
        db.execute("UPDATE analysis_executions SET state='finalizing' WHERE id=?", (identity,))
    def crash(at):
        if at == stage: os._exit(83)
    publish(store, identity, output, {}, checkpoint=crash)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / ".simulatte"), identity, stage], capture_output=True, timeout=20
    )
    assert result.returncode == 83, result.stderr
    with RunStore.open(root / ".simulatte") as reopened:
        saved = AnalysisStore(reopened)
        assert saved.snapshot(snapshot_hash)["available"] == 1
        if stage == "committed":
            assert saved.execution(identity).state == "succeeded"
            assert saved.output(identity)["outputs"][0]["name"] == "value"
        else:
            assert saved.execution(identity).state == "lost"
            with pytest.raises(KeyError):
                saved.output(identity)
            assert not (reopened.root / "analysis-results" / identity).exists()


def test_dataset_inputs_are_private_verified_copies(prepared):
    import hashlib
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.definition import DatasetRequirement
    from simulatte.analysis.types import DatasetSchema, Field

    root, store, analysis, environments, original = prepared
    attempt = analysis.snapshot(original.snapshot_hash)["replications"][0]["attempt"]
    result = store.root / "results" / attempt
    writer = DatasetWriter(result / "artifacts/datasets/jobs", DatasetSchema("jobs", (Field("lateness", "float64"),)))
    writer.append({"lateness": 5.0})
    writer.finish()
    with store._transaction() as db:
        for path in (result / "artifacts").rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?)",
                    (
                        attempt,
                        path.relative_to(result / "artifacts").as_posix(),
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                    ),
                )
    (root / "report.py").write_text("""from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    context.emit('lateness', Number(sum(row['lateness'] for row in context.rows('jobs'))))
""")
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition(
            "dataset",
            "report.py:analyze",
            source.content_hash,
            ParameterSchema(()),
            (DatasetRequirement("jobs", ("lateness",)),),
        ),
        source=source,
    )
    preview = analysis.preview(Selection(run_ids=(attempt,)))
    item = analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=original.generation_id)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        assert runner.wait(item.id).state == "succeeded"
    payload = codec.loads((store.root / "analysis-results" / item.id / "lateness/number.json").read_bytes())
    assert payload["value"] == 5.0
    assert (result / "artifacts/datasets/jobs/chunk-00000000.msgpack").is_file()


def test_descendant_cleanup_failure_prevents_publication(prepared, monkeypatch):
    from simulatte.execution.process import ContainedProcess

    root, store, analysis, environments, execution = prepared
    close = ContainedProcess.close

    def failed_once(process):
        if process._closed:
            return
        close(process)
        raise OSError("descendant cleanup failed")

    monkeypatch.setattr(ContainedProcess, "close", failed_once)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "failed"
    with pytest.raises(KeyError):
        analysis.output(execution.id)


def test_running_child_late_output_after_cancel_is_not_success(prepared):
    import time

    root, store, analysis, environments, original = prepared
    (root / "report.py").write_text("""import signal, time
from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    def cancelled(*args):
        context.emit('late', Number(2))
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, cancelled)
    context.emit('ready', Number(1))
    while True:
        time.sleep(.01)
""")
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("cancel", "report.py:analyze", source.content_hash, ParameterSchema(()), ()), source=source
    )
    item = analysis.admit(
        analysis.refresh(original.id).id, definition_id=definition, parameters={}, generation_id=original.generation_id
    )
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        ready = store.root / "analysis-staging" / item.id / "output/ready/number.json"
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        runner.cancel(item.id)
        assert runner.wait(item.id).state == "cancelled"
    with pytest.raises(KeyError):
        analysis.output(item.id)
    assert "cleanup_error" not in details(store, item.id)


def test_real_publisher_dataset_admission_and_child_execution(tmp_path):
    from dataclasses import replace
    import hashlib
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.definition import DatasetRequirement
    from simulatte.analysis.types import DatasetSchema, Field
    from tests.execution.test_publication import prepared as publication_fixture
    from tests.execution.test_receipts import descriptor
    from simulatte.execution.publication import publish as publish_result

    store, key, proposal, receiver = publication_fixture(tmp_path, level="kpi", finalize=False)
    with store:
        dataset = tmp_path / "dataset"
        writer = DatasetWriter(dataset, DatasetSchema("values", (Field("value", "int64"),)))
        writer.append({"value": 2**60})
        writer.finish()
        artifacts = []
        for sequence, file in enumerate(sorted(dataset.iterdir()), start=2):
            data = file.read_bytes()
            path = "datasets/values/" + file.name
            receiver.receive(
                descriptor(data, id=f"data-{sequence}", sequence=sequence, stream="artifact/" + path), 0, data
            )
            artifacts.append({"path": path, "length": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        store.transition(key, "finalizing")
        result = publish_result(
            store, key, replace(proposal, artifacts=tuple(artifacts)), execution_exited=True, descendants_cleaned=True
        )
        assert (result.path / "artifacts/datasets/values/manifest.json").is_file()
        analysis = AnalysisStore(store)
        preview = analysis.preview(Selection(run_ids=(key.attempt_id,)))
        assert preview.available == 1, analysis.snapshot(preview.snapshot_hash)
        (tmp_path / "report.py").write_text("""from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    context.emit('value', Number(next(context.rows('values'))['value']))
""")
        source = capture_source(ProjectSpec(tmp_path, "report.py", "layout.json", ("report.py",), (), (".",), False))
        definition = analysis.put_definition(
            AnalysisDefinition(
                "published",
                "report.py:analyze",
                source.content_hash,
                ParameterSchema(()),
                (DatasetRequirement("values", ("value",)),),
            ),
            source=source,
        )
        environments = EnvironmentPool(tmp_path, store)
        generation = environments.use_existing(sys.executable)
        item = analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=generation.id)
        with AnalysisRunner(store, environments=environments) as runner:
            runner.submit(item.id)
            assert runner.wait(item.id).state == "succeeded"
        value = codec.loads((store.root / "analysis-results" / item.id / "value/number.json").read_bytes())
        assert value["value"] == 2**60


def details(store, identity):
    with store._transaction() as db:
        row = db.execute("SELECT details FROM analysis_executions WHERE id=?", (identity,)).fetchone()
    return codec.loads(row[0])


def test_runner_owns_one_slot_and_guards_submission(prepared):
    root, store, analysis, environments, execution = prepared
    waiting = analysis.rerun(execution.id)
    with AnalysisRunner(store, environments=environments) as runner:
        with pytest.raises(ValueError, match="already owns"):
            AnalysisRunner(store, environments=environments)
        # Waiting on an execution this runner never accepted reports its saved state.
        assert runner.wait(waiting.id).state == "waiting"
        runner.submit(execution.id)
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "succeeded"
        analysis.cancel(waiting.id)
        with pytest.raises(ValueError, match="Only a waiting"):
            runner.submit(waiting.id)
        runner.close()
        with pytest.raises(ValueError, match="closed"):
            runner.submit(waiting.id)
    # Closing twice is harmless and releases the slot for a new runner.
    with AnalysisRunner(store, environments=environments):
        pass


def test_pending_control_file_cancels_before_start(prepared):
    root, store, analysis, environments, execution = prepared
    control = store.root / "analysis-control" / (execution.id + ".cancel")
    control.touch()
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "cancelled"
    assert not control.exists()
    assert not (store.root / "analysis-staging" / execution.id).exists()
    assert environments.owners(execution.generation_id) == ()


def test_captured_input_mismatch_fails_before_child(prepared, monkeypatch):
    import hashlib
    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.types import DatasetSchema, Field
    from simulatte.execution.process import ContainedProcess

    root, store, analysis, environments, original = prepared
    attempt = analysis.snapshot(original.snapshot_hash)["replications"][0]["attempt"]
    result = store.root / "results" / attempt
    writer = DatasetWriter(result / "artifacts/datasets/jobs", DatasetSchema("jobs", (Field("lateness", "float64"),)))
    writer.append({"lateness": 5.0})
    writer.finish()
    with store._transaction() as db:
        for path in (result / "artifacts").rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?)",
                    (
                        attempt,
                        path.relative_to(result / "artifacts").as_posix(),
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                    ),
                )
    item = analysis.admit(
        analysis.preview(Selection(run_ids=(attempt,))).id,
        definition_id=original.definition_id,
        parameters={},
        generation_id=original.generation_id,
    )
    # Admission checks chunk lengths; the private copy verifies their bytes.
    chunk = result / "artifacts/datasets/jobs/chunk-00000000.msgpack"
    chunk.write_bytes(bytes(len(chunk.read_bytes())))
    monkeypatch.setattr(ContainedProcess, "start", lambda *a, **k: pytest.fail("child started"))
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        assert runner.wait(item.id).state == "failed"
    assert details(store, item.id)["message"] == "Captured input hash/length mismatch"
    assert not (store.root / "analysis-staging" / item.id).exists()


def test_cancelled_child_ignoring_terminate_is_killed(prepared):
    import time

    root, store, analysis, environments, original = prepared
    (root / "report.py").write_text("""import signal, time
from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    context.emit('ready', Number(1))
    while True:
        time.sleep(.01)
""")
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("stubborn", "report.py:analyze", source.content_hash, ParameterSchema(()), ()),
        source=source,
    )
    item = analysis.admit(
        analysis.refresh(original.id).id, definition_id=definition, parameters={}, generation_id=original.generation_id
    )
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        ready = store.root / "analysis-staging" / item.id / "output/ready/number.json"
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        # A store-level cancellation reaches the child only through the runner loop,
        # which escalates from terminate to kill.
        analysis.cancel(item.id)
        assert runner.wait(item.id, timeout=15).state == "cancelled"
    with pytest.raises(KeyError):
        analysis.output(item.id)


def test_cleanup_failure_is_recorded_after_failed_run(prepared, monkeypatch):
    from simulatte.execution.process import ContainedProcess

    root, store, analysis, environments, execution = prepared
    close = ContainedProcess.close

    def always_failed(process):
        close(process)
        raise OSError("descendant cleanup failed")

    monkeypatch.setattr(ContainedProcess, "close", always_failed)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "failed"
    # The cleanup error joins the failure message instead of replacing it.
    assert details(store, execution.id) == {
        "message": "descendant cleanup failed",
        "cleanup_error": "descendant cleanup failed",
    }
    assert environments.owners(execution.generation_id) == ()


def test_primary_cleanup_failure_is_recorded_for_cancelled_execution(prepared, monkeypatch):
    from simulatte.execution.process import ContainedProcess

    root, store, analysis, environments, execution = prepared
    close = ContainedProcess.close

    def cancelled_then_failed(process):
        # A real second close() is a no-op once the first one ran.
        if process._closed:
            return
        close(process)
        analysis.cancel(execution.id)
        raise OSError("descendant cleanup failed")

    monkeypatch.setattr(ContainedProcess, "close", cancelled_then_failed)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "cancelled"
    assert details(store, execution.id) == {"cleanup_error": "descendant cleanup failed"}


def test_unindexed_output_after_publication_failure_is_removed(prepared, monkeypatch):
    from simulatte.analysis import execution as module

    root, store, analysis, environments, execution = prepared
    output = store.root / "analysis-results" / execution.id

    def renamed_then_failed(store, identity, staged, provenance):
        staged.rename(output)
        raise OSError("index failed after rename")

    monkeypatch.setattr(module, "publish", renamed_then_failed)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "failed"
    assert details(store, execution.id)["message"] == "index failed after rename"
    assert not output.exists()


def test_cancel_after_clean_child_exit_never_finalizes_partial_snapshot(prepared, monkeypatch):
    from simulatte.execution.process import ContainedProcess

    root, store, analysis, environments, original = prepared
    first = analysis.snapshot(original.snapshot_hash)["replications"][0]["run"]
    # A second run with no published replication contributes an unavailable row.
    second, _ = build(store, {(0, 1): "pending"})
    item = analysis.admit(
        analysis.preview(Selection(experiment_run_ids=(first, second))).id,
        definition_id=original.definition_id,
        parameters={},
        generation_id=original.generation_id,
    )
    assert analysis.snapshot(item.snapshot_hash)["partial"] is True
    close = ContainedProcess.close

    def cancelled_after_exit(process):
        # The child has exited cleanly; cancellation arrives before finalization.
        close(process)
        analysis.cancel(item.id)

    monkeypatch.setattr(ContainedProcess, "close", cancelled_after_exit)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        assert runner.wait(item.id).state == "cancelled"
    with pytest.raises(KeyError):
        analysis.output(item.id)
    assert not (store.root / "analysis-results" / item.id).exists()


def test_experiment_scale_snapshot_reaches_the_child_without_resolved_manifests(tmp_path, monkeypatch):
    from simulatte.execution import schema
    from simulatte.execution.process import ContainedProcess
    from simulatte.execution.store import _blob
    from tests.core.test_experiment_results import seeded
    from tests.execution.test_store_slots import DEFINITION

    root = tmp_path / "project"
    root.mkdir()
    documents = []
    start = ContainedProcess.start

    def measured(argv, **kwargs):
        config = Path(argv[-1]).read_bytes()
        documents.append((len(config), codec.loads(config)["inputs"]))
        return start(argv, **kwargs)

    monkeypatch.setattr(ContainedProcess, "start", measured)
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        configurations = [
            {"label": f"x={x}", "parameters": {"x": float(x)}, "layout_hash": "c" * 64, "layout_ref": None}
            for x in range(3)
        ]
        replications = [
            {"configuration": c, "index": i, "seed": i, "content_ids": [schema.content_id(seeded(c, i))]}
            for c in range(3)
            for i in range(24)
        ]
        run = store.create_experiment_run(store.put_experiment(DEFINITION), {}, configurations, replications)
        for c in range(3):
            for i in range(24):
                publish(store, run, c, i, 1)
        # The example model resolves to about 90 KB of manifest per replication.
        with store._transaction() as db:
            db.execute("UPDATE results SET manifest=?", (_blob({"resolved": "x" * 90_000}),))
        environments = EnvironmentPool(root, store)
        analysis, execution = admit_total(root, store, environments, run)
        snapshot = analysis.snapshot(execution.snapshot_hash)
        assert snapshot["available"] == 72 and len(codec.dumps(snapshot)) > 6 << 20
        with AnalysisRunner(store, environments=environments) as runner:
            runner.submit(execution.id)
            assert runner.wait(execution.id).state == "succeeded", details(store, execution.id)
        value = codec.loads((store.root / "analysis-results" / execution.id / "total/number.json").read_bytes())
        assert value["value"] == 72
        assert analysis.output(execution.id)["provenance"]["input_snapshot"] == execution.snapshot_hash
    [(size, inputs)] = documents
    assert size < 1 << 20
    assert len(inputs["replications"]) == 72
    assert not any({"manifest", "request", "fingerprint"} & set(row) for row in inputs["replications"])
    assert all(row["scalars"] == {"lateness": 1} and row["request_id"] for row in inputs["replications"])


def test_runner_close_after_deleting_finished_execution(prepared):
    root, store, analysis, environments, execution = prepared
    runner = AnalysisRunner(store, environments=environments)
    runner.submit(execution.id)
    assert runner.wait(execution.id).state == "succeeded"
    analysis.delete(execution.id)
    runner.close()
    assert runner._futures == {}
    # The slot is released for a new runner.
    with AnalysisRunner(store, environments=environments):
        pass


def test_runner_close_tolerates_execution_deleted_while_finishing(prepared, monkeypatch):
    root, store, analysis, environments, execution = prepared
    runner = AnalysisRunner(store, environments=environments)
    deleted, cancelling = threading.Event(), threading.Event()
    release, cancel = environments.release, runner.analysis.cancel

    def recorded(identity):
        cancelling.set()
        return cancel(identity)

    def deleted_before_release(owner):
        # The execution is terminal but its future is not done yet.
        analysis.delete(execution.id)
        deleted.set()
        assert cancelling.wait(10)
        release(owner)

    monkeypatch.setattr(runner.analysis, "cancel", recorded)
    monkeypatch.setattr(environments, "release", deleted_before_release)
    runner.submit(execution.id)
    assert deleted.wait(10)
    runner.close()
    assert cancelling.is_set() and store._analysis_runner is None
    assert environments.owners(execution.generation_id) == ()


def test_service_close_after_deleting_finished_analysis_closes_the_store(tmp_path):
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService

    service = ProjectService.open(StudioTarget(tmp_path, None))
    store = service.store
    store.create_revision({})
    run, _ = build(store, {(0, 0): "pending"})
    publish(store, run, 0, 0, 3)
    analysis, execution = admit_total(tmp_path, store, service.environments, run)
    runner = service.analyses()
    assert service.analyses() is runner
    runner.submit(execution.id)
    assert runner.wait(execution.id).state == "succeeded"
    analysis.delete(execution.id)
    service.close()
    assert store._analysis_runner is None and store._closed


def test_service_close_closes_every_component_after_a_failure(tmp_path):
    from types import SimpleNamespace
    from simulatte.projects import StudioTarget
    from simulatte.projects.service import ProjectService

    service = ProjectService.open(StudioTarget(tmp_path, None))
    store, closed = service.store, []

    def component(name, error=None):
        def close():
            closed.append(name)
            if error is not None:
                raise error

        return SimpleNamespace(close=close)

    service.analysis_runner = component("runner", RuntimeError("runner close failed"))
    service.coordinator = component("coordinator")
    service.reader = component("reader")
    with pytest.raises(RuntimeError, match="runner close failed"):
        service.close()
    assert closed == ["runner", "coordinator", "reader"] and store._closed


@pytest.mark.parametrize("entry", ["directory", "dangling symlink", "symlink to file"])
def test_non_regular_control_entry_is_not_a_request_and_is_removed(prepared, entry):
    import os

    root, store, analysis, environments, execution = prepared
    control = store.root / "analysis-control" / (execution.id + ".cancel")
    target = root / "elsewhere"
    if entry == "directory":
        control.mkdir()
    else:
        if entry == "symlink to file":
            target.touch()
        control.symlink_to(target)
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(execution.id)
        assert runner.wait(execution.id).state == "succeeded"
    assert not os.path.lexists(control)
    assert target.exists() == (entry == "symlink to file")


def test_control_file_while_running_terminates_the_child(prepared):
    import time

    root, store, analysis, environments, original = prepared
    marker = root / "terminated"
    (root / "report.py").write_text(f"""import pathlib, signal, time
from simulatte.analysis.outputs import Number

def analyze(context, parameters):
    def terminated(*args):
        pathlib.Path({str(marker)!r}).touch()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminated)
    context.emit('ready', Number(1))
    while True:
        time.sleep(.01)
""")
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("control", "report.py:analyze", source.content_hash, ParameterSchema(()), ()),
        source=source,
    )
    item = analysis.admit(
        analysis.refresh(original.id).id, definition_id=definition, parameters={}, generation_id=original.generation_id
    )
    control = store.root / "analysis-control" / (item.id + ".cancel")
    with AnalysisRunner(store, environments=environments) as runner:
        runner.submit(item.id)
        ready = store.root / "analysis-staging" / item.id / "output/ready/number.json"
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        # The store discards the control file while cancelling; the loop still terminates the child.
        control.touch()
        assert runner.wait(item.id, timeout=15).state == "cancelled"
    assert marker.exists()
    assert not control.exists()
    assert details(store, item.id) == {}


def test_cancel_then_delete_while_terminating_finishes_cleanly(prepared, monkeypatch):
    root, store, analysis, environments, original = prepared
    (root / "report.py").write_text("""import time

def analyze(context, parameters):
    while True:
        time.sleep(.01)
""")
    source = capture_source(ProjectSpec(root, "report.py", "layout.json", ("report.py",), (), (".",), False))
    definition = analysis.put_definition(
        AnalysisDefinition("deleted", "report.py:analyze", source.content_hash, ParameterSchema(()), ()), source=source
    )
    item = analysis.admit(
        analysis.refresh(original.id).id, definition_id=definition, parameters={}, generation_id=original.generation_id
    )
    deleted = threading.Event()
    with AnalysisRunner(store, environments=environments) as runner:
        execution = runner.analysis.execution

        def deleted_while_running(identity):
            # Delete only checks the saved state, so a cancelled execution goes while its child still runs.
            if identity in runner._processes and not deleted.is_set():
                analysis.cancel(identity)
                analysis.delete(identity)
                deleted.set()
            return execution(identity)

        monkeypatch.setattr(runner.analysis, "execution", deleted_while_running)
        runner.submit(item.id)
        future = runner._futures[item.id]
        assert future.exception(timeout=15) is None
        assert deleted.is_set()
        # A deleted execution is unknown to wait(), like any other identity.
        with pytest.raises(KeyError):
            runner.wait(item.id)
    assert not (store.root / "analysis-staging" / item.id).exists()
    assert environments.owners(original.generation_id) == ()
