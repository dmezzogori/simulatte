"""Headless coordinator owns captured inputs, fresh attempts and published results."""

import importlib
import importlib.util

import pytest

from simulatte import Trace
from simulatte.execution.snapshot import discover_project


MODEL = """from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
import simulatte
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import DomainEvent, Deltas, event_type
class Cell(Entity, kind="coordinator_cell"):
    state_schema: ClassVar[StateSchema] = StateSchema({"count": FieldSpec("int")})
    def __init__(self, env):
        self.count = 0
        env.entities.attach(self, name="cell")
@event_type("coordinator.changed", touches={"coordinator_cell": ("count",)})
class Changed(DomainEvent):
    cell: str
    count: int
@dataclass(frozen=True)
class Params:
    n: int = 2
@simulatte.model(params=Params, horizon=2, inputs=["input.txt"])
def build(env, layout, params):
    cell = Cell(env)
    value = params.n + int(Path("input.txt").read_text())
    def process():
        yield env.timeout(1)
        cell.count = value
        env.emit(Changed(cell=cell.id, count=value, deltas=Deltas.build().set(cell.id,"count",value).done()))
        (env.artifacts_dir / "answer.txt").write_text(str(value))
    env.process(process())
"""


def api():
    assert importlib.util.find_spec("simulatte.execution.coordinator"), "coordinator missing"
    return importlib.import_module("simulatte.execution.coordinator")


def project(tmp_path, model=MODEL):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
    (root / "plant.py").write_text(model)
    (root / "input.txt").write_text("5")
    return discover_project(root / "plant.py", root)


def test_prepare_rejects_stale_revision_before_capture_or_child(tmp_path, monkeypatch):
    m = api()
    with m.Coordinator(project(tmp_path)) as coordinator:
        old = coordinator.store.create_revision({"source": "old"})
        current = coordinator.store.create_revision({"source": "current"})

        def forbidden(*args):
            pytest.fail("stale preparation must not capture or launch")

        monkeypatch.setattr(m, "capture_source", forbidden)
        with pytest.raises(m.RevisionConflict):
            coordinator.prepare(expected=old)
        assert coordinator.store.current_revision() == current


def test_prepare_cannot_replace_revision_changed_during_capture(tmp_path, monkeypatch):
    m = api()
    with m.Coordinator(project(tmp_path)) as coordinator:
        base = coordinator.store.create_revision({"source": "base"})
        original = m.capture_source
        newer = []

        def capture(spec):
            snapshot = original(spec)
            newer.append(coordinator.store.create_revision({"source": "newer"}))
            return snapshot

        monkeypatch.setattr(m, "capture_source", capture)
        with pytest.raises(m.RevisionConflict):
            coordinator.prepare(expected=base)
        assert coordinator.store.current_revision() == newer[0]
        assert coordinator.events() == []


def test_capture_inspect_run_publish_and_reproduce_survive_working_tree_edits(tmp_path, monkeypatch):
    m = api()
    spec = project(tmp_path)
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        request = coordinator.make_request(prepared, parameters={"n": 3})
        (spec.root / "input.txt").write_text("100")
        (spec.root / "plant.py").write_text("raise RuntimeError('working tree must not run')\n")
        key = coordinator.submit(request, prepared.revision, timeout=10)
        result = coordinator.wait(key, timeout=15)
        assert (result.path / "artifacts/answer.txt").read_text() == "8"
        trace = Trace.open(result.path / "run.simtrace")
        assert trace.verify() is True
        view = coordinator.store.presentation_view(revision=prepared.revision)
        assert view is not None and view.committed and view.key == key
        assert view.bundle.full_hash == trace.layout.full_hash
        assert trace.cursor_range is not None
        assert trace.state_at(trace.cursor_range[1])["cell"]["count"] == 8
        repeated = coordinator.reproduce(key.request_id, timeout=15)
        assert repeated.key.attempt_id != key.attempt_id
        assert repeated.key.request_id == key.request_id
        assert repeated.fingerprint == result.fingerprint
        assert any(event["type"] == "result" for event in coordinator.events())
    with m.Coordinator(spec) as reopened:
        repeated = reopened.reproduce(key.request_id, timeout=15)
        assert repeated.fingerprint == result.fingerprint
        from dataclasses import replace
        from simulatte.execution.schema import _plain

        # Exercise the comparison boundary with already-published, verified
        # baseline bytes; execution itself was checked three times above.
        monkeypatch.setattr(reopened, "submit", lambda *a, **kw: repeated.key)
        for change, status in (
            ("incomplete", "unverifiable"),
            ("warning", "unverifiable"),
            ("fingerprint", "mismatch"),
            ("artifact", "mismatch"),
        ):
            manifest = _plain(repeated.manifest)
            fingerprint = repeated.fingerprint
            if change == "incomplete":
                manifest["simulation"]["complete"] = False
            elif change == "warning":
                manifest["resolved"]["warnings"] = ["unmanaged"]
            elif change == "artifact":
                manifest["artifacts"] = []
            else:
                fingerprint = {}
            altered = replace(repeated, manifest=manifest, fingerprint=fingerprint)
            monkeypatch.setattr(reopened, "wait", lambda *a, **kw: altered)
            with pytest.raises(m.ReproductionMismatch) as failure:
                reopened.reproduce(key.request_id)
            assert failure.value.status == status


def test_bad_build_is_terminal_and_cannot_become_a_result(tmp_path):
    m = api()
    spec = project(tmp_path, MODEL[: MODEL.index("    cell = Cell(env)")] + "    env.run(until=1)\n")
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        key = coordinator.submit(coordinator.make_request(prepared), prepared.revision, timeout=10)
        with pytest.raises(m.ExecutionFailure):
            coordinator.wait(key, timeout=15)
        assert coordinator.store.attempt(key)["status"] == "validation_failed"
        with pytest.raises(KeyError):
            coordinator.store.result(key.attempt_id)


def until_running(coordinator, key):
    import time

    deadline = time.monotonic() + 10
    while coordinator.store.attempt(key)["status"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)


@pytest.mark.parametrize("replacement", [False, True])
def test_cancellation_and_supersession_cannot_publish_old_attempt(tmp_path, replacement):
    import time

    m = api()
    model = MODEL.replace(
        "        yield env.timeout(1)",
        "        yield env.timeout(0)\n        if params.n == 1:\n"
        "            while True: pass\n        yield env.timeout(1)",
    )
    spec = project(tmp_path, model)
    started = None
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        old = coordinator.submit(coordinator.make_request(prepared, parameters={"n": 1}), prepared.revision, timeout=20)
        until_running(coordinator, old)
        started = time.monotonic()
        if replacement:
            new = coordinator.submit(
                coordinator.make_request(prepared, parameters={"n": 3}), prepared.revision, timeout=10
            )
            result = coordinator.wait(new, timeout=15)
            assert (result.path / "artifacts/answer.txt").read_text() == "8"
            assert coordinator.store.attempt(old)["status"] == "superseded"
            assert coordinator.cancel(old) is False
        else:
            assert coordinator.cancel(old)
        with pytest.raises(m.ExecutionFailure):
            coordinator.wait(old, timeout=1)
        with pytest.raises(KeyError):
            coordinator.store.result(old.attempt_id)
    if not replacement:
        assert time.monotonic() - started < 5


def test_external_runtime_change_fails_before_build(tmp_path):
    from dataclasses import replace

    m = api()
    spec = project(tmp_path)
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        changed = replace(prepared, runtime={**prepared.runtime, "dependencies_hash": "0" * 64})
        request = coordinator.make_request(changed)
        key = coordinator.submit(request, prepared.revision, timeout=10)
        with pytest.raises(m.ExecutionFailure, match="runtime"):
            coordinator.wait(key, timeout=15)
        assert coordinator.store.attempt(key)["status"] == "validation_failed"


@pytest.mark.parametrize("index_full", [False, True])
def test_publication_storage_failure_never_hangs_or_reports_success(tmp_path, monkeypatch, index_full):
    import errno
    import sqlite3

    m = api()
    spec = project(tmp_path)
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        request = coordinator.make_request(prepared)

        def full(*args, **kwargs):
            raise OSError(errno.ENOSPC, "publication disk full")

        monkeypatch.setattr(m, "publish", full)
        if index_full:

            def index_failure(*args, **kwargs):
                raise sqlite3.OperationalError("database or disk is full")

            monkeypatch.setattr(coordinator.store, "revoke", index_failure)
        key = coordinator.submit(request, prepared.revision, timeout=10)
        with pytest.raises(m.ExecutionFailure) as failure:
            coordinator.wait(key, timeout=15)
        assert failure.value.status == "disk_full"
        with pytest.raises(KeyError):
            coordinator.store.result(key.attempt_id)


def test_timeout_includes_materialization_before_child_launch(tmp_path, monkeypatch):
    import time

    m = api()
    spec = project(tmp_path)
    with m.Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        request = coordinator.make_request(prepared)
        materialize = m.materialize

        def slow(*args, **kwargs):
            time.sleep(0.1)
            return materialize(*args, **kwargs)

        monkeypatch.setattr(m, "materialize", slow)
        starts = []
        actor = coordinator._actors["interactive"]
        start = actor._start_worker

        def start_worker():
            starts.append(True)
            return start()

        monkeypatch.setattr(actor, "_start_worker", start_worker)
        key = coordinator.submit(request, prepared.revision, timeout=0.02)
        with pytest.raises(m.ExecutionFailure, match="deadline|timeout"):
            coordinator.wait(key, timeout=5)
        assert coordinator.store.attempt(key)["status"] == "failed"
        assert not starts


def test_preparation_deadline_includes_source_capture(tmp_path, monkeypatch):
    import time

    m = api()
    spec = project(tmp_path)
    with m.Coordinator(spec) as coordinator:
        capture = m.capture_source

        def slow(project):
            time.sleep(0.05)
            return capture(project)

        monkeypatch.setattr(m, "capture_source", slow)
        with pytest.raises(TimeoutError, match="deadline"):
            coordinator.prepare(timeout=0.01)
        assert coordinator.store.current_revision() is None


def test_prepared_metadata_and_layout_cannot_be_mutated(tmp_path):
    m = api()
    with m.Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        with pytest.raises(TypeError):
            prepared.layout["schema_version"] = "invalid"
        with pytest.raises(TypeError):
            prepared.metadata["models"][0]["name"] = "other"
        with pytest.raises(TypeError):
            prepared.runtime["facts"]["python"] = "invented"


@pytest.mark.parametrize("corrupt", ["initial", "proposal", "resolved", "delayed"])
def test_ready_and_result_metadata_must_match_committed_trace(tmp_path, monkeypatch, corrupt):
    m = api()
    with m.Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        original = coordinator._metadata
        delayed = []

        def metadata(receiver, reference, stream):
            data = original(receiver, reference, stream)
            if stream == "resolved-manifest":
                if corrupt == "initial":
                    data["schema"] = 99
                if corrupt == "delayed" and not delayed:
                    delayed.append(True)
                    raise KeyError("metadata receipt pending")
            if stream == "result-proposal":
                if corrupt == "proposal":
                    data["schema"] = 99
                if corrupt == "resolved":
                    data["resolved_manifest"]["schema"] = 99
            return data

        monkeypatch.setattr(coordinator, "_metadata", metadata)
        key = coordinator.submit(coordinator.make_request(prepared), prepared.revision, timeout=10)
        if corrupt == "delayed":
            assert coordinator.wait(key, timeout=15).key == key
        else:
            with pytest.raises(m.ExecutionFailure, match="INITIAL|schema"):
                coordinator.wait(key, timeout=15)
            with pytest.raises(KeyError):
                coordinator.store.result(key.attempt_id)


def test_obsolete_preparation_cannot_create_request(tmp_path):
    m = api()
    with m.Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        coordinator.store.create_revision({"changed": True})
        with pytest.raises(m.RevisionConflict):
            coordinator.make_request(prepared)
