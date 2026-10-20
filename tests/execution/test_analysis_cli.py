"""Headless selection is explicit; rerun and refresh have different input identities."""

import json
import sys

import pytest

from simulatte.cli import main
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.store import RunStore
from tests.core.test_experiment_results import build
from tests.execution.test_analysis_snapshots import publish


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("""[project]
name = "study"
version = "0.1.0"
[tool.simulatte.project]
schema = 1
id = "c09b902b-ccad-4d13-887b-693356b32f23"
[tool.simulatte.analyses.total]
id = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"
name = "Total"
entrypoint = "report.py:analyze"
[tool.simulatte.bundle]
include = ["report.py"]
""")
    (root / "report.py").write_text("""from simulatte.analysis import analysis
from simulatte.model import ParameterSchema
from simulatte.analysis.outputs import Number
@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    context.emit('total', Number(sum(row['scalars']['lateness'] for row in context.scalars())))
""")
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        run, _ = build(store, {(0, i): "pending" for i in range(2)})
        attempt = publish(store, run, 0, 0, 3)
    return root, run, attempt


def invoke(capsys, root, *args):
    code = main(["analysis", *args, "--project", str(root), "--json"])
    output = json.loads(capsys.readouterr().out)
    return code, output


def test_analysis_cli_same_snapshot_rerun_and_explicit_refresh(project, capsys):
    root, run, attempt = project
    code, first = invoke(capsys, root, "run", "total", "--experiment", run)
    assert code == 0 and first["execution"]["state"] == "succeeded"
    identity = first["execution"]["id"]
    with RunStore.open(root / ".simulatte") as store:
        store.set_run_status(run, "running")
        publish(store, run, 0, 1, 5)
    code, same = invoke(capsys, root, "rerun", identity)
    assert code == 0 and same["execution"]["snapshot_hash"] == first["execution"]["snapshot_hash"]
    code, fresh = invoke(capsys, root, "refresh", identity)
    assert code == 0 and fresh["execution"]["snapshot_hash"] != first["execution"]["snapshot_hash"]
    code, shown = invoke(capsys, root, "show", identity)
    assert code == 0 and shown["execution"] == first["execution"] and shown["output"] == first["output"]
    code, cancelled = invoke(capsys, root, "cancel", identity)
    assert code == 0 and cancelled["execution"]["state"] == "succeeded"


def test_analysis_cli_insufficient_explicit_inputs_and_unknown_registration(project, capsys):
    root, run, attempt = project
    code, failed = invoke(capsys, root, "run", "total", "--experiment", run, "--require-complete")
    assert code == 2 and "insufficient" in failed["message"]
    code, failed = invoke(capsys, root, "run", "missing", "--run", attempt)
    assert code == 2 and "Unknown analysis" in failed["message"] and "details" not in failed
    code, failed = invoke(capsys, root, "run", "total")
    assert code == 2 and "explicit" in failed["message"]


def test_python_and_cli_outputs_match_same_execution_inputs(project, capsys):
    from simulatte.analysis.execution import AnalysisRunner

    root, run, attempt = project
    code, first = invoke(capsys, root, "run", "total", "--run", attempt)
    assert code == 0
    with RunStore.open(root / ".simulatte") as store:
        analysis = AnalysisStore(store)
        item = analysis.rerun(first["execution"]["id"])
        with AnalysisRunner(store) as runner:
            runner.submit(item.id)
            assert runner.wait(item.id).state == "succeeded"
        from simulatte.analysis import codec

        assert codec.to_json(analysis.output(item.id)["outputs"]) == first["output"]["$record"]["outputs"]


def test_analysis_inspection_missing_dependency_message(project, capsys):
    root, run, attempt = project
    (root / "report.py").write_text("import missing_analysis_dependency\n")
    code, failed = invoke(capsys, root, "run", "total", "--run", attempt)
    assert code == 2 and "missing_analysis_dependency" in failed["message"]
    assert "report" not in sys.modules


def test_cancel_uses_owning_writer_and_run_exit_status(project, capsys):
    import subprocess
    import time

    root, run, attempt = project
    (root / "report.py").write_text("""import time
from simulatte.analysis import analysis, Number
from simulatte.model import ParameterSchema
@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    context.emit('ready', Number(1))
    while True: time.sleep(.01)
""")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "simulatte",
            "analysis",
            "run",
            "total",
            "--run",
            attempt,
            "--project",
            str(root),
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    identity = None
    try:
        deadline = time.monotonic() + 15
        while identity is None:
            candidates = list((root / ".simulatte/analysis-staging").glob("*/output/ready/number.json"))
            if candidates:
                identity = candidates[0].parents[2].name
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        code, cancelled = invoke(capsys, root, "cancel", identity)
        assert code == 0 and cancelled["cancellation_requested"] is True
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 130, stderr
        assert json.loads(stdout)["execution"]["state"] == "cancelled"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_inspection_capacity_shared_with_model_preparation(project):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from simulatte.analysis.command import prepare_analysis

    root, run, attempt = project
    with RunStore.open(root / ".simulatte") as store:
        started = threading.Event()

        def inspect():
            started.set()
            return prepare_analysis(store, "total")

        # The exact lock used by Coordinator.prepare_model fences analysis inspection too.
        with ThreadPoolExecutor(1) as pool:
            with store._inspection_lock:
                future = pool.submit(inspect)
                assert started.wait(5) and not future.done()
            definition, generation = future.result(timeout=15)
        assert AnalysisStore(store).definition(definition)["entrypoint"] == "report.py:analyze"


def test_unchanged_registration_reuses_its_inspected_definition(project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from simulatte.analysis import command as module
    from simulatte.execution.process import ContainedProcess

    root, run, attempt = project
    starts = []

    def start(*args, **kwargs):
        starts.append(args)
        return ContainedProcess.start(*args, **kwargs)

    monkeypatch.setattr(module, "ContainedProcess", SimpleNamespace(start=start))
    with RunStore.open(root / ".simulatte") as store:
        first, generation = module.prepare_analysis(store, "total")
        assert len(starts) == 1
        # Same registration and captured source: the stored definition, no child and no inspection capacity.
        with ThreadPoolExecutor(1) as pool, store._inspection_lock:
            assert pool.submit(module.prepare_analysis, store, "total").result(timeout=15) == (first, generation)
        assert len(starts) == 1
        with store._transaction() as db:
            owners = {row[0] for row in db.execute('SELECT owner_kind FROM "references"')}
            assert "analysis-inspection" not in owners and "environment-owner" not in owners
        (root / "report.py").write_text((root / "report.py").read_text() + "# changed\n")
        second, _ = module.prepare_analysis(store, "total")
        assert len(starts) == 2 and second != first
        assert (
            AnalysisStore(store).definition(second)["source_hash"]
            != AnalysisStore(store).definition(first)["source_hash"]
        )
        assert module.prepare_analysis(store, "total")[0] == second and len(starts) == 2


def waiting(root, attempt):
    from simulatte.analysis.command import prepare_analysis
    from simulatte.analysis.selection import Selection

    with RunStore.open(root / ".simulatte") as store:
        analysis = AnalysisStore(store)
        definition, generation = prepare_analysis(store, "total")
        preview = analysis.preview(Selection(run_ids=(attempt,)))
        return analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id=generation).id


@pytest.mark.parametrize("tamper", ["size", "identity"])
def test_inspection_refuses_invalid_metadata_and_releases_capacity(project, monkeypatch, tamper):
    from types import SimpleNamespace
    from simulatte.analysis import codec, command as module
    from simulatte.projects.environments import EnvironmentPool

    root, run, attempt = project
    if tamper == "size":
        monkeypatch.setattr(module, "MAX_MANIFEST_BYTES", 1)
        message = "Invalid analysis metadata"
    else:
        monkeypatch.setattr(module, "codec", SimpleNamespace(loads=lambda raw: {**codec.loads(raw), "id": "other"}))
        message = "identity mismatch"
    with RunStore.open(root / ".simulatte") as store:
        environments = EnvironmentPool(root, store)
        generation = environments.use_existing(sys.executable)
        with pytest.raises(ValueError, match=message):
            module.prepare_analysis(store, "total", environments=environments, generation=generation)
        with store._transaction() as db:
            owners = db.execute('SELECT owner_kind FROM "references"').fetchall()
            assert "analysis-inspection" not in {row[0] for row in owners}
            assert db.execute("SELECT COUNT(*) FROM analysis_definitions").fetchone()[0] == 0
        assert environments.owners(generation.id) == ()
        assert list((store.root / "analysis-staging").iterdir()) == []


def test_request_cancel_guards_identity_state_and_control_file(project, capsys):
    from uuid import uuid4
    from simulatte.analysis.command import request_cancel

    root, run, attempt = project
    identity = waiting(root, attempt)
    store = root / ".simulatte"
    with pytest.raises(ValueError, match="Invalid execution UUID"):
        request_cancel(store, identity.upper())
    with pytest.raises(KeyError):
        request_cancel(store, str(uuid4()))
    control = store / "analysis-control" / (identity + ".cancel")
    for _ in range(2):
        # A repeated request keeps the single pending control file.
        assert request_cancel(store, identity)["cancellation_requested"] is True
        assert control.is_file()
    control.unlink()
    control.symlink_to(root / "elsewhere")
    with pytest.raises(ValueError, match="Unsafe cancellation"):
        request_cancel(store, identity)
    control.unlink()
    # The writer that admitted it has closed: reopening records the unsubmitted execution as lost.
    code, terminal = invoke(capsys, root, "cancel", identity)
    assert code == 0 and terminal["execution"]["state"] == "lost"
    assert request_cancel(store, identity) == terminal
    code, missing = invoke(capsys, root, "show", str(uuid4()))
    assert code == 2 and missing["status"] == "validation_failed"


def test_show_refuses_store_without_analysis_schema(tmp_path, capsys):
    from uuid import uuid4

    with RunStore.open(tmp_path / ".simulatte") as store:
        with store._transaction() as db:
            names = [
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'analysis_%'")
            ]
            for name in reversed(names):
                db.execute(f"DROP TABLE {name}")
            db.execute("PRAGMA user_version=3")
    code, refused = invoke(capsys, tmp_path, "show", str(uuid4()))
    assert code == 2 and "schema4" in refused["message"]


def test_cli_parameters_are_typed_by_captured_schema(project, capsys):
    from simulatte.analysis import codec

    root, run, attempt = project
    (root / "report.py").write_text("""from simulatte.analysis import analysis
from simulatte.model import ParameterField, ParameterSchema
from simulatte.analysis.outputs import Number
@analysis(parameters=ParameterSchema((ParameterField('scale', 'int', False, 1),)))
def analyze(context, parameters):
    total = sum(row['scalars']['lateness'] for row in context.scalars())
    context.emit('total', Number(total * parameters['scale']))
""")
    code, failed = invoke(capsys, root, "run", "total", "--run", attempt, "--param", "scale")
    assert code == 2 and "NAME=VALUE" in failed["message"]
    code, done = invoke(capsys, root, "run", "total", "--run", attempt, "--param", "scale=2")
    assert code == 0 and done["execution"]["state"] == "succeeded"
    output = root / ".simulatte/analysis-results" / done["execution"]["id"] / "total/number.json"
    assert codec.loads(output.read_bytes())["value"] == 6


def test_interrupted_run_cancels_its_execution(project, capsys, monkeypatch):
    from simulatte.analysis.execution import AnalysisRunner

    root, run, attempt = project
    (root / "report.py").write_text("""import time
from simulatte.analysis import analysis
from simulatte.model import ParameterSchema
@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    while True: time.sleep(.01)
""")
    wait, interrupted = AnalysisRunner.wait, []

    def interrupt_once(self, identity, timeout=None):
        if not interrupted:
            interrupted.append(identity)
            raise KeyboardInterrupt
        return wait(self, identity, timeout)

    monkeypatch.setattr(AnalysisRunner, "wait", interrupt_once)
    code, cancelled = invoke(capsys, root, "run", "total", "--run", attempt)
    assert code == 130 and cancelled["execution"]["state"] == "cancelled"
    assert cancelled["execution"]["id"] == interrupted[0]


def test_operational_failure_and_text_output(tmp_path, capsys):
    project = tmp_path / "file"
    project.write_text("not a project directory")
    assert main(["analysis", "rerun", "a", "--project", str(project)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()
    assert main(["analysis", "show", "a", "--project", str(tmp_path)]) == 2
    assert "No readable run store index" in capsys.readouterr().err


def test_dataset_refusal_envelope_carries_structured_details(project, capsys):
    from simulatte.analysis import codec

    root, run, attempt = project
    (root / "report.py").write_text("""from simulatte.analysis import DatasetRequirement, analysis
from simulatte.model import ParameterSchema
@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement('jobs', ('lateness',)),))
def analyze(context, parameters):
    pass
""")
    code, failed = invoke(capsys, root, "run", "total", "--run", attempt)
    assert code == 2 and failed["status"] == "validation_failed"
    assert failed["message"].endswith(
        f"0 of 1 replications valid, 1 required; replication 0 (attempt {attempt}): dataset unavailable"
    )
    details = codec.from_json(failed["details"])
    assert (details["dataset"], details["run"], details["valid"], details["required"]) == ("jobs", attempt, 0, 1)
    assert [(r["attempt"], list(r["missing_fields"])) for r in details["unavailable"]] == [(attempt, ["lateness"])]


def test_request_cancel_does_not_acknowledge_finalizing(project):
    from simulatte.analysis.command import request_cancel

    root, run, attempt = project
    identity = waiting(root, attempt)
    store = root / ".simulatte"
    with RunStore.open(store) as owner:
        with owner._transaction() as db:
            db.execute("UPDATE analysis_executions SET state='finalizing' WHERE id=?", (identity,))
        # The runner no longer polls the control file once publication has started.
        current = request_cancel(store, identity)
    assert current["execution"]["state"] == "finalizing" and "cancellation_requested" not in current
    assert not (store / "analysis-control" / (identity + ".cancel")).exists()
