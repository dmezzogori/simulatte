"""Selection/preparation cannot acquire or fence another model's simulation slot."""

import threading
import time
from uuid import uuid4

import pytest

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.project_store import ProjectStore
from simulatte.execution.store import RunStore, RevisionConflict
from simulatte.projects import WorkspaceDefinition, ModelRegistration, preview_config, apply_config
from simulatte.projects.environments import EnvironmentPool
from tests.execution.test_coordinator import MODEL


def project(tmp_path, *, hanging=False):
    root = tmp_path
    root.joinpath("pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["*.py"]\n')
    root.joinpath("a.py").write_text(
        MODEL.replace("yield env.timeout(1)", "import time; time.sleep(30)\n        yield env.timeout(1)")
        if hanging
        else MODEL
    )
    root.joinpath("b.py").write_text(MODEL)
    root.joinpath("input.txt").write_text("5")
    models = tuple(
        ModelRegistration(str(uuid4()), key, key.upper(), f"{key}.py:build", f"{key}.json") for key in ("a", "b")
    )
    definition = WorkspaceDefinition(root, str(uuid4()), models, (), None)
    apply_config(preview_config(root, definition))
    return definition


def coordinator(workspace, store):
    assert hasattr(Coordinator, "for_workspace"), "workspace coordinator missing"
    import sys
    from pathlib import Path

    pool = EnvironmentPool(workspace.root, store)
    pool.activate(pool.use_existing(Path(sys.executable)).id)
    return Coordinator.for_workspace(workspace.root, store=store, environments=pool)


def wait_until(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, "execution did not reach expected state"
        time.sleep(0.01)


def test_inspect_b_preserves_active_a(tmp_path, monkeypatch):
    from simulatte.execution import coordinator as module

    workspace = project(tmp_path)
    a, b = [m.id for m in workspace.models]
    entered, release = threading.Event(), threading.Event()
    original = module.publish

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "publish", held)
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        prepared = c.prepare_model(a, timeout=10)
        request = c.make_request(prepared)
        started = c.admit_run(a, request, prepared.revision)
        assert started.status == "started"
        try:
            assert entered.wait(10)
            pb = c.prepare_model(b, timeout=10)
            owner = c.inputs_for(b)
            state = owner.bind(pb)
            state = owner.apply(state.revision, {"seed": "42"}, auto_run=False)
            blocked = c.admit_run(b, state.request, state.revision)
            assert blocked.status == "busy" and blocked.owner == started.key
            assert owner.state.options["seed"] == "42"
            with pytest.raises(RevisionConflict):
                c.contexts[b].prepare(expected=pb.revision)
            for name in ("a.py", "b.py"):
                with (tmp_path / name).open("a") as f:
                    f.write("\n# changed while A was running\n")
            owner.invalidate(state.revision, "source changed")
            a_state = c.inputs_for(a).bind(prepared)
            c.inputs_for(a).invalidate(a_state.revision, "A source changed too")
            store.create_revision({"unrelated": True})
        finally:
            release.set()
        result = c.wait(started.key, timeout=15)
        assert store.result(started.key.attempt_id).key == result.key
        assert ProjectStore(store).history(a, after=None).items[0]["id"] == result.key.attempt_id


def test_stop_and_run_waits_for_owner_cleanup(tmp_path):
    workspace = project(tmp_path, hanging=True)
    a, b = [m.id for m in workspace.models]
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        pa = c.prepare_model(a, timeout=10)
        first = c.admit_run(a, c.make_request(pa), pa.revision)
        wait_until(lambda: store.attempt(first.key)["status"] == "running")
        pb = c.prepare_model(b, timeout=10)
        request = c.make_request(pb)
        replacement = c.stop_and_run(first.key, b, request, pb.revision)
        assert replacement.status == "started"
        assert store.attempt(first.key)["status"] == "cancelled"
        assert c.wait(replacement.key, timeout=15).key == replacement.key
        with pytest.raises(KeyError):
            store.result(first.key.attempt_id)


def test_cancel_failure_and_disabled_auto_never_replace_manual(tmp_path, monkeypatch):
    workspace = project(tmp_path, hanging=True)
    a, b = [m.id for m in workspace.models]
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        pa = c.prepare_model(a, timeout=10)
        first = c.admit_run(a, c.make_request(pa), pa.revision)
        pb = c.prepare_model(b, timeout=10)
        request = c.make_request(pb)
        token = c.enable_auto(b)
        assert c.admit_run(b, request, pb.revision, auto_session=token).status == "busy"
        c.disable_auto(b)
        assert c.admit_run(b, request, pb.revision, auto_session=token).status == "stale"
        with monkeypatch.context() as patch:
            patch.setattr(c, "cancel", lambda owner: False)
            assert c.stop_and_run(first.key, b, request, pb.revision).status == "busy"
        assert store.attempt(first.key)["status"] in ("queued", "preparing", "ready", "running")


def test_auto_session_only_replaces_its_own_attempt(tmp_path):
    workspace = project(tmp_path, hanging=True)
    a = workspace.models[0].id
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        prepared = c.prepare_model(a, timeout=10)
        request = c.make_request(prepared)
        token = c.enable_auto(a)
        first = c.admit_run(a, request, prepared.revision, auto_session=token)
        second = c.admit_run(a, request, prepared.revision, auto_session=token)
        assert second.status == "started" and second.key != first.key
        c.disable_auto(a)
        new_token = c.enable_auto(a)
        assert new_token != token
        assert c.admit_run(a, request, prepared.revision, auto_session=new_token).status == "busy"


def test_intervening_owner_prevents_stop_and_run_admission(tmp_path, monkeypatch):
    workspace = project(tmp_path, hanging=True)
    a, b = [m.id for m in workspace.models]
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        pa = c.prepare_model(a, timeout=10)
        first = c.admit_run(a, c.make_request(pa), pa.revision)
        pb = c.prepare_model(b, timeout=10)
        request = c.make_request(pb)
        original = c.cancel
        intervening = []

        def race(owner):
            cancelled = original(owner)
            snapshot, documents = c._preparation(request)
            intervening.append(c._schedule(request, pb.revision, snapshot, documents, 10, model_id=b))
            return cancelled

        monkeypatch.setattr(c, "cancel", race)
        outcome = c.stop_and_run(first.key, b, request, pb.revision)
        assert outcome.status == "busy"
        assert c.wait(intervening[0], timeout=15).key == intervening[0]
        monkeypatch.setattr(c, "cancel", original)


def test_old_request_cannot_be_admitted_at_new_model_revision(tmp_path):
    workspace = project(tmp_path)
    a = workspace.models[0].id
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        prepared = c.prepare_model(a, timeout=10)
        old = c.make_request(prepared)
        state = c.inputs_for(a).bind(prepared)
        assert c.admit_run(a, old, state.revision).status == "stale"


def test_experiment_captures_model_and_environment_ownership(tmp_path):
    from simulatte.experiments import load_experiment
    from tests.execution.test_scheduler import write_experiment

    workspace = project(tmp_path)
    a = workspace.models[0].id
    path = write_experiment(tmp_path, model="a.py:build", replications={"count": 1})
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as c:
        generation = c.environments.selected
        run_id = c.start_experiment(load_experiment(path), model_id=a)
        report = c.wait_experiment(run_id, timeout=20)
        assert report["status"] == "completed"
        preparation = store.experiment_run(run_id)["preparation"]
        assert preparation["environment"] == generation.id
        assert preparation["model_id"] == a
        assert len(ProjectStore(store).history(a, after=None).items) == 2
        wait_until(lambda: not c.environments.owners(generation.id))
