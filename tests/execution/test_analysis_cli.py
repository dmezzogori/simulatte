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
    assert code == 2 and "Unknown analysis" in failed["message"]
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
