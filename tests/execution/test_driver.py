"""Fresh-process execution must await durable readiness and publish verified bytes."""

from dataclasses import asdict
from pathlib import Path
import sysconfig
import time

import pytest

from simulatte._wire import unpack
from simulatte.execution.schema import ExecutionRequest, ResolvedManifest, request_id, _plain
from simulatte.execution.snapshot import freeze_runtime
from simulatte.execution.store import RunStore
from simulatte.execution.receipts import SegmentReceiver
from simulatte.execution.publication import ResultProposal, publish
from simulatte.stopping import Horizon
from tests.execution._worker_harness import WorkerHarness


MODEL = """from dataclasses import dataclass
from pathlib import Path
import simulatte
@dataclass(frozen=True)
class Params:
    n: int = 2
@simulatte.model(params=Params, horizon=2)
def build(env, layout, params):
    def process():
        yield env.timeout(1)
        (env.artifacts_dir / "answer.txt").write_text(str(params.n))
    env.process(process())
"""


def runtime_evidence(tmp_path, trusted_hash):
    from simulatte.execution.runtime import describe_runtime
    from simulatte.execution.references import write_document
    from simulatte.execution.hashing import canonical_hash

    roots = list(dict.fromkeys(sysconfig.get_path(name) for name in ("purelib", "platlib")))
    evidence = describe_runtime(roots)
    requested = {
        "facts": evidence["facts"],
        "dependencies_hash": canonical_hash("dependencies-v1", evidence["dependencies"]),
        "trusted_runtime_hash": trusted_hash,
    }
    config = {
        "runtime_ref": write_document(tmp_path / "runtime-documents", "runtime", evidence),
        "distribution_roots": roots,
    }
    return requested, config


def run_fixture(tmp_path, model=MODEL, *, action="run", measurements=None, level="full"):
    project, snapshot = tmp_path / "project", tmp_path / "snapshot"
    project.mkdir()
    snapshot.mkdir()
    (snapshot / "plant.py").write_text(model)
    runtime = freeze_runtime(Path(__file__).resolve().parents[2] / "src" / "simulatte", project)
    requested_runtime, runtime_config = runtime_evidence(tmp_path, runtime.content_hash)
    config = {
        **runtime_config,
        "project_root": str(project),
        "snapshot_roots": [str(snapshot)],
        "external_roots": [str(runtime.root), sysconfig.get_path("purelib")],
        "reference": "plant.py:build",
    }
    request = ExecutionRequest(
        model="plant.py:build",
        source_hash="a" * 64,
        input_hash="b" * 64,
        layout_hash="c" * 64,
        layout_revision=1,
        parameters={"n": 7},
        seed=42,
        stopping=Horizon(2).to_dict(),
        runtime=requested_runtime,
        measurements=measurements,
        level=level,
    )
    worker = WorkerHarness()
    store = RunStore.open(tmp_path / ".simulatte")
    key = store.assign(request, store.create_revision({"source": "a"}))
    receiver = SegmentReceiver(store, key)
    try:
        assert worker.receive().type == "hello"
        worker.send("inspect", {"config": config}, key=key)
        while (message := worker.receive()).type != "model_info":
            assert message.type != "error", message.body
        config["expected_metadata"] = _plain(message.body["metadata"])
        store.transition(key, "preparing")
        worker.send("run", {"config": config, "request": request.to_dict(), "timeout": 10}, key=key)
        ready = None
        acknowledged = False
        terminal = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = worker.receive(5)
            if message.type in ("trace_chunk", "artifact_fragment"):
                receipt = receiver.receive(
                    _plain(message.body["segment"]), message.body["fragment_offset"], message.payload
                )
                worker.send("ack", asdict(receipt), key=key)
            elif message.type == "ready":
                ready = message.body
                assert ready["simulation_time"] == 0
                assert request_id(request) == message.envelope["request_id"]
            elif message.type in ("result", "error", "cancelled"):
                terminal = message
                break
            if ready is not None and not acknowledged:
                try:
                    resolved_path = receiver.segment_path(ready["resolved"]["id"])
                except KeyError:
                    continue
                prefix = receiver.prefix()
                if prefix is None or prefix.length < ready["trace_length"]:
                    continue
                resolved = _plain(unpack(resolved_path.read_bytes()))
                assert resolved["schema"] == 1
                acknowledged = True
                if action == "cancel_ready":
                    worker.send("cancel", {}, key=key)
                else:
                    store.transition(key, "ready")
                    store.transition(key, "running")
                    worker.send("ready_ack", {}, key=key)
        assert terminal is not None
        if action != "run":
            return terminal, ready
        assert terminal.type == "result", terminal.body
        assert acknowledged
        data = _plain(unpack(receiver.segment_path(terminal.body["proposal"]["id"]).read_bytes()))
        manifest = data.pop("resolved_manifest")
        manifest.pop("schema")
        data.pop("schema")
        assert data["final_imports"]
        proposal = ResultProposal(**data, resolved_manifest=ResolvedManifest(**manifest))
        store.transition(key, "finalizing")
        result = publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        assert (result.path / "artifacts" / "answer.txt").read_text() == "7"
        return result, ready
    finally:
        worker.close()
        store.close()


def test_fresh_child_runs_only_after_durable_ready_and_publishes(tmp_path):
    result, ready = run_fixture(tmp_path)
    assert result.fingerprint["digest"]
    assert ready is not None


def test_cancel_at_ready_does_not_run_processes(tmp_path):
    terminal, ready = run_fixture(tmp_path, action="cancel_ready")
    assert ready is not None
    assert terminal.type == "cancelled"


@pytest.mark.parametrize("body", ["env.run(until=1)", "raise RuntimeError('build failed')"])
def test_invalid_build_never_becomes_ready(tmp_path, body):
    model = MODEL[: MODEL.index("    def process():")] + "    " + body + "\n"
    terminal, ready = run_fixture(tmp_path, model, action="error")
    assert terminal.type == "error"
    assert ready is None


def local_driver(tmp_path, monkeypatch, build, *, change=None, send_hook=None):
    """Keep real environment, recorder and spool; replace only process-local import setup."""
    from types import SimpleNamespace
    from simulatte.execution import driver
    from simulatte.execution.control import Control
    from simulatte.execution.schema import AttemptKey
    from simulatte.execution.spool import Spool
    from simulatte.model import model

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Params:
        n: int = 2

    decorated = model(params=Params, horizon=2)(build)
    metadata = {"models": [{"name": "build"}], "selected": "build"}
    monkeypatch.setattr(
        driver,
        "load_configuration",
        lambda config: (metadata, decorated, SimpleNamespace(audit=lambda: {"fixture": "snapshot:plant.py"})),
    )
    control = Control()
    spool = Spool(tmp_path / "spool", control=control)
    requested_runtime, runtime_config = runtime_evidence(tmp_path, "f" * 64)
    request = ExecutionRequest(
        model="plant.py:build",
        source_hash="a" * 64,
        input_hash="b" * 64,
        layout_hash="c" * 64,
        layout_revision=1,
        parameters={"n": 7},
        seed=42,
        stopping=Horizon(2).to_dict(),
        runtime=requested_runtime,
    )
    key = AttemptKey(request_id(request), "a" * 32, 1)
    config = {**runtime_config, "reference": request.model, "expected_metadata": metadata, "attempt_dir": str(tmp_path)}
    if change:
        request, key, config = change(request, key, config)
    messages = []

    def send(kind, body):
        messages.append((kind, body))
        if send_hook:
            send_hook(kind, body, control)
        elif kind == "ready":
            control.ack_ready()

    result = driver.execute(config, request.to_dict(), key, control, spool, send)
    proposal = next(segment for segment in spool.ready() if segment.id == result["id"])
    return _plain(unpack(proposal.path.read_bytes())), spool, messages


def test_local_driver_artifact_streams_and_final_import_audit(tmp_path, monkeypatch):
    def build(env, layout, params):
        (env.artifacts_dir / "empty").touch()
        (env.artifacts_dir / "answer").write_bytes(b"x" * ((1 << 20) + 3))

    proposal, spool, messages = local_driver(tmp_path, monkeypatch, build)
    assert [item["length"] for item in proposal["artifacts"]] == [(1 << 20) + 3, 0]
    assert proposal["final_imports"] == {"fixture": "snapshot:plant.py"}
    assert [kind for kind, body in messages].count("ready") == 1
    assert len([part for part in spool.ready() if part.stream == "artifact/answer"]) == 2


@pytest.mark.parametrize(
    "failure",
    [
        "identity",
        "model",
        "metadata",
        "kpi",
        "advance",
        "return",
        "generator",
        "coroutine",
        "ready_cancel",
        "process",
        "symlink",
        "directory_symlink",
    ],
)
def test_driver_failures_cannot_propose_a_result(tmp_path, monkeypatch, failure):
    from dataclasses import replace
    from simulatte.execution.control import Cancelled
    from simulatte.execution.schema import AttemptKey

    def build(env, layout, params):
        if failure == "advance":
            env.run(until=1)
        if failure == "return":
            return 1
        if failure == "generator":
            return (value for value in ())
        if failure == "coroutine":

            async def coro():
                pass

            return coro()
        if failure in ("symlink", "directory_symlink"):
            (env.artifacts_dir / "link").symlink_to(
                tmp_path if failure == "directory_symlink" else __file__,
                target_is_directory=failure == "directory_symlink",
            )
        if failure == "process":

            def process():
                yield env.timeout(1)
                raise RuntimeError("process failed")

            env.process(process())

    def change(request, key, config):
        if failure == "identity":
            key = AttemptKey("b" * 64, key.attempt_id, key.generation)
        if failure == "model":
            config["reference"] = "other.py:build"
        if failure == "metadata":
            config["expected_metadata"] = {"models": [], "selected": "build"}
        if failure == "kpi":
            request = replace(request, kpis=("unknown/value",))
            key = AttemptKey(request_id(request), key.attempt_id, key.generation)
        return request, key, config

    def send(kind, body, control):
        if kind == "ready":
            if failure == "ready_cancel":
                control.cancel()
            else:
                control.ack_ready()

    with pytest.raises((ValueError, RuntimeError, Cancelled)):
        local_driver(tmp_path, monkeypatch, build, change=change, send_hook=send)


def test_metadata_segment_rejects_oversize_before_reservation(tmp_path):
    from simulatte.execution.driver import metadata_segment
    from simulatte.execution.spool import Spool

    spool = Spool(tmp_path / "spool", max_segment_bytes=32)
    with pytest.raises(ValueError, match="metadata exceeds"):
        metadata_segment(spool, "result-proposal", {"too_big": "x" * 40})
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


@pytest.mark.parametrize("failure", ["saturated", "disconnected", "recorder"])
def test_progress_is_coalescible_but_transport_or_recorder_failure_is_fatal(tmp_path, monkeypatch, failure):
    from simulatte.execution import driver

    def build(env, layout, params):
        time.sleep(0.25)

    def send(kind, body, control):
        if kind == "progress":
            raise BufferError("full") if failure == "saturated" else OSError("gone")
        if kind == "ready":
            control.ack_ready()

    if failure == "recorder":

        def broken(*args, **kwargs):
            raise ValueError("cannot create recorder")

        monkeypatch.setattr(driver, "TraceRecorder", broken)
    if failure == "saturated":
        proposal, _, _ = local_driver(tmp_path, monkeypatch, build, send_hook=send)
        assert proposal["simulation_manifest"]["stopping_policy"]["horizon"] == 2
    else:
        with pytest.raises((OSError, ValueError)):
            local_driver(tmp_path, monkeypatch, build, send_hook=send)


@pytest.mark.parametrize("when", ["before", "during", "after", "collision"])
def test_artifact_mutation_and_name_collisions_are_refused(tmp_path, monkeypatch, when):
    from simulatte.execution import driver
    from simulatte.execution.spool import Spool

    root = tmp_path / "artifacts"
    root.mkdir()
    path = root / "a"
    path.write_bytes(b"original")
    spool = Spool(tmp_path / "spool")
    original = Path.open
    if when == "collision":
        (root / "A").write_bytes(b"second file on case-sensitive filesystems")
        monkeypatch.setattr(driver.os, "walk", lambda *a, **kw: [(root, [], ["a", "A"])])
    else:

        class Changed:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                return self

            def read(self, size):
                data = self.file.read(size)
                if when == "during" and data:
                    with original(path, "ab") as output:
                        output.write(b"changed")
                    # Once only; otherwise appending extends this read forever.
                    self.read = self.file.read
                return data

            def fileno(self):
                return self.file.fileno()

            def __exit__(self, *args):
                self.file.close()
                if when == "after":
                    path.write_bytes(b"changed")

        def open_file(self, *args, **kwargs):
            if self == path and args == ("rb",) and when == "before":
                # Replace between stat and open; Windows disallows unlinking an open file.
                path.unlink()
                path.write_bytes(b"replacement")
            file = original(self, *args, **kwargs)
            return Changed(file) if self == path and args == ("rb",) else file

        monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError, match="changed|collide"):
        driver._artifacts(root, spool)


def test_driver_selected_kpis_and_finite_empty_population(tmp_path, monkeypatch):
    from dataclasses import replace
    from simulatte.execution.schema import AttemptKey
    from simulatte.stopping import FinitePopulation, Population
    from tests.core.test_kpi import _desk_system

    for finite in (False, True):
        root = tmp_path / str(finite)
        root.mkdir()

        def build(env, layout, params):
            if finite:
                Population(env)
            else:
                _desk_system(env, jobs=[(0, 1)])

        def change(request, key, config):
            request = (
                replace(request, stopping=FinitePopulation(arrivals=0, safety_limit=2).to_dict())
                if finite
                else replace(request, kpis=("desk/flow_time",))
            )
            return request, AttemptKey(request_id(request), key.attempt_id, key.generation), config

        proposal, _, _ = local_driver(root, monkeypatch, build, change=change)
        assert proposal["fingerprint"]["kpis"] == ({} if finite else {"desk/flow_time": 1})


def test_driver_resolves_verified_metadata_and_layout_documents(tmp_path, monkeypatch):
    from dataclasses import replace
    from simulatte.execution.schema import AttemptKey
    from simulatte.execution.references import write_document
    from simulatte.layout.identity import layout_hash

    def build(env, layout, params):
        pass

    def change(request, key, config):
        layout = {"schema_version": "1.0"}
        config["expected_metadata_ref"] = write_document(
            tmp_path / "documents", "model-info", config.pop("expected_metadata")
        )
        config["layout_ref"] = write_document(tmp_path / "documents", "layout", layout)
        request = replace(request, layout_hash=layout_hash("file", layout))
        return request, AttemptKey(request_id(request), key.attempt_id, key.generation), config

    proposal, _, _ = local_driver(tmp_path, monkeypatch, build, change=change)
    assert proposal["resolved_manifest"]["layout"]["schema_version"] == "1.0"


def test_execution_requires_measured_runtime_evidence_before_model_import(tmp_path, monkeypatch):
    built = []

    def build(env, layout, params):
        built.append(True)

    def omit(request, key, config):
        config.pop("runtime_ref")
        return request, key, config

    with pytest.raises(ValueError, match="runtime evidence"):
        local_driver(tmp_path, monkeypatch, build, change=omit)
    assert not built


def test_numerical_run_publishes_declared_dataset_without_domain_animation(tmp_path):
    import json
    from simulatte.analysis import DatasetSchema, Field, MeasurementPlan
    from simulatte.analysis.measurements import select_measurements
    from simulatte.analysis.datasets import DatasetReader, DatasetManifest
    from simulatte.trace import Trace

    plan = MeasurementPlan("numbers", (DatasetSchema("numbers", (Field("x", "int64"),)),))
    source = MODEL.replace(
        "@simulatte.model(params=Params, horizon=2)",
        """from simulatte.analysis import DatasetSchema, Field, MeasurementPlan, ObservationCollector
plan = MeasurementPlan('numbers', (DatasetSchema('numbers', (Field('x', 'int64'),)),))
@simulatte.model(params=Params, horizon=2, measurement_plans=(plan,))""",
    )
    source = source.replace(
        "    def process():",
        """    collector = ObservationCollector(plan.datasets[0], fields={}).attach(env)
    def process():""",
    ).replace(
        "        yield env.timeout(1)",
        """        yield env.timeout(1)
        collector.record(None, observed_at='observation', values={'x': 2**60})""",
    )
    result, _ = run_fixture(tmp_path, source, measurements=select_measurements(plan), level="kpi")
    root = result.path / "artifacts/datasets/numbers"
    manifest = DatasetManifest.from_dict(json.loads((root / "manifest.json").read_text()))
    assert list(DatasetReader(root, manifest).iter_rows()) == [{"x": 2**60}]
    trace = Trace.open(result.path / "run.simtrace")
    assert trace.level == "kpi"
