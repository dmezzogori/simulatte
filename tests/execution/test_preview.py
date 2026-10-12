"""A physical preview builds captured inputs but never steps or publishes a run."""

from dataclasses import replace

import pytest

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.store import RevisionConflict
from tests.execution.test_coordinator import project

MODEL = """from dataclasses import dataclass
from pathlib import Path
import os
from simulatte import model
from simulatte.server import Server
from simulatte.layout import Point
@dataclass(frozen=True)
class Params:
    fail: bool = False
@model(params=Params, horizon=2)
def build(env, layout, params):
    if params.fail:
        raise ValueError("preview build failed")
    server = Server(env=env, capacity=1, name="machine")
    layout.place(server, position=Point(1, 2))
    def process():
        raise RuntimeError("preview stepped the simulation")
        yield env.timeout(1)
    env.process(process())
"""


def test_preview_builds_in_fresh_child_without_mutation_or_publication(tmp_path):
    with Coordinator(project(tmp_path, MODEL)) as coordinator:
        prepared = coordinator.prepare()
        coordinator.inputs.bind(prepared)
        before = coordinator.store.current_revision()
        attempts = coordinator.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        layer = {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "position": [5, 6, 0]}}}
        preview = coordinator.inputs.preview_layout(before, layer, timeout=10)
        assert preview.requires_rerun and preview.bundle.to_dict()["resolved"]["placements"]["machine"]["position"] == [
            5,
            6,
            0,
        ]
        assert coordinator.store.current_revision() == before
        assert coordinator.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == attempts
        assert coordinator.store._db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
        assert not (coordinator.project.root / coordinator.project.layout).exists()


def test_preview_failure_preserves_revision_and_cleans_temporary_store(tmp_path):
    with Coordinator(project(tmp_path, MODEL)) as coordinator:
        prepared = coordinator.prepare()
        state = coordinator.inputs.bind(prepared)
        state = coordinator.inputs.apply(state.revision, {"parameters": {"fail": True}}, auto_run=False)
        with pytest.raises(ExecutionFailure, match="preview build failed"):
            coordinator.inputs.preview_layout(state.revision, {"schema_version": "1.0"}, timeout=10)
        assert coordinator.store.current_revision() == state.revision
        assert coordinator._preview is None


def test_preview_rejects_stale_and_invalid_input_before_launch(tmp_path):
    with Coordinator(project(tmp_path, MODEL)) as coordinator:
        prepared = coordinator.prepare()
        state = coordinator.inputs.bind(prepared)
        with pytest.raises(RevisionConflict):
            coordinator.inputs.preview_layout(replace(state.revision, number=0), {"schema_version": "1.0"})
        with pytest.raises(ValueError):
            coordinator.inputs.preview_layout(state.revision, {"schema_version": "wrong"})
        assert coordinator._preview is None


def test_preview_uses_pure_resolution_when_physics_is_unchanged(tmp_path, monkeypatch):
    from tests.execution.test_coordinator import MODEL as RUNNABLE

    with Coordinator(project(tmp_path, RUNNABLE)) as coordinator:
        prepared = coordinator.prepare()
        state = coordinator.inputs.bind(prepared)
        key = coordinator.submit(state.request, state.revision)
        coordinator.wait(key)
        monkeypatch.setattr(coordinator, "preview", lambda *args, **kwargs: pytest.fail("No preview child needed"))
        result = coordinator.inputs.preview_layout(state.revision, {"schema_version": "1.0"})
        assert not result.requires_rerun and result.bundle is not None


def test_preview_refuses_invalid_forms_and_closed_owner(tmp_path):
    model = MODEL.replace("fail: bool = False", "fail: bool")
    with Coordinator(project(tmp_path, model)) as coordinator:
        prepared = coordinator.prepare()
        state = coordinator.inputs.bind(prepared)
        with pytest.raises(ValueError, match="input issues"):
            coordinator.inputs.preview_layout(state.revision, {"schema_version": "1.0"})
        request = coordinator._build_request(prepared, parameters={"fail": False})
    with pytest.raises(RuntimeError, match="closed"):
        coordinator.preview(prepared, request, {"schema_version": "1.0"})


@pytest.mark.parametrize("action", ["revision", "shutdown", "timeout"])
def test_blocked_preview_fences_changes_and_cleans_worker(tmp_path, action):
    import threading
    import time

    marker = tmp_path / "entered"
    model = MODEL.replace(
        "    server =",
        "    child = __import__('subprocess').Popen([__import__('sys').executable, "
        "'-c', 'import time; time.sleep(30)'])\n"
        f"    Path({str(marker)!r}).write_text(str(os.getpid()) + ',' + str(child.pid))\n"
        "    __import__('time').sleep(30)\n    server =",
    )
    with Coordinator(project(tmp_path, model)) as coordinator:
        prepared = coordinator.prepare()
        state = coordinator.inputs.bind(prepared)
        errors = []

        def preview():
            try:
                coordinator.inputs.preview_layout(
                    state.revision, {"schema_version": "1.0"}, timeout=2 if action == "timeout" else 20
                )
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=preview)
        thread.start()
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.02)
        assert marker.exists()
        child_pids = [int(pid) for pid in marker.read_text().split(",")]
        with pytest.raises(RuntimeError, match="already running"):
            coordinator.inputs.preview_layout(state.revision, {"schema_version": "1.0"})
        if action == "revision":
            coordinator.store.create_revision({"changed": True}, expected=state.revision)
        elif action == "shutdown":
            coordinator.close()
        thread.join(10)
        assert not thread.is_alive() and errors
        assert isinstance(errors[0], RevisionConflict if action != "timeout" else (TimeoutError, ExecutionFailure))
        assert coordinator._preview is None
        if __import__("os").name != "nt":
            for child_pid in child_pids:
                with pytest.raises(ProcessLookupError):
                    __import__("os").kill(child_pid, 0)


def test_invalid_physical_preview_preserves_committed_view(tmp_path):
    from tests.execution.test_inputs import PRESENTATION_MODEL

    with Coordinator(project(tmp_path, PRESENTATION_MODEL)) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare())
        key = coordinator.submit(state.request, state.revision)
        coordinator.wait(key)
        before = coordinator.store.presentation_view(revision=state.revision)
        patch = {
            "schema_version": "1.0",
            "network": {
                "type": "explicit",
                "nodes": [{"id": "start", "position": [0, 0, 0]}],
                "arcs": [{"source": "start", "target": "missing"}],
            },
        }
        with pytest.raises(ExecutionFailure):
            coordinator.inputs.preview_layout(state.revision, patch)
        after = coordinator.store.presentation_view(revision=state.revision)
        assert after.key == before.key == key
        assert after.bundle.full_hash == before.bundle.full_hash
        assert coordinator.store.current_revision() == state.revision
        assert coordinator.store._db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_physical_preview_does_not_cancel_active_run(tmp_path):
    from tests.execution.test_inputs import PRESENTATION_MODEL

    model = PRESENTATION_MODEL.replace(
        "        yield env.timeout(1)", "        __import__('time').sleep(3)\n        yield env.timeout(1)"
    )
    with Coordinator(project(tmp_path, model)) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare())
        key = coordinator.submit(state.request, state.revision)
        result = coordinator.inputs.preview_layout(state.revision, {"schema_version": "1.0", "grid": {"dx": 2}})
        assert result.requires_rerun
        assert coordinator.store.attempt(key)["status"] not in ("cancelled", "lost", "failed")
        assert coordinator.wait(key).key == key
        assert coordinator.store.current_revision() == state.revision
