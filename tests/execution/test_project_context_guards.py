"""Model-context adapters preserve preparation, ownership and shared-store lifetime."""

from pathlib import Path
import sys

import pytest

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.store import RunStore
from simulatte.projects.environments import EnvironmentPool
from simulatte.experiments import load_experiment
from tests.execution.test_project_execution import project, coordinator
from tests.execution.test_scheduler import write_experiment


def test_workspace_requires_configuration_selected_runtime_and_valid_models(tmp_path, monkeypatch):
    with RunStore.open(tmp_path / ".simulatte") as store:
        pool = EnvironmentPool(tmp_path, store)
        with pytest.raises(ValueError, match="Configure"):
            Coordinator.for_workspace(tmp_path, store=store, environments=pool)
        project(tmp_path)
        with pytest.raises(ValueError, match="select"):
            Coordinator.for_workspace(tmp_path, store=store, environments=pool)
        pool.activate(pool.use_existing(Path(sys.executable)).id)
        with monkeypatch.context() as patch:
            patch.setattr(sys, "executable", str(tmp_path / "other-python"))
            with pytest.raises(ValueError, match="Restart"):
                Coordinator.for_workspace(tmp_path, store=store, environments=pool)
        path = tmp_path / "pyproject.toml"
        path.write_text(path.read_text().replace('include=["*.py"]', "include=[]"))
        with pytest.raises(ValueError, match="Non-Git"):
            Coordinator.for_workspace(tmp_path, store=store, environments=pool)
        # Failed context creation closes actors, not the shared writer.
        assert store._db.execute("SELECT 1").fetchone()[0] == 1


def test_repeated_inspection_uses_cache_and_context_autorun_is_opt_in(tmp_path):
    workspace = project(tmp_path)
    model = workspace.models[0].id
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        co._inspection_cache.update({f"older-{i}": {} for i in range(64)})
        prepared = co.prepare_model(model, timeout=10)
        assert len(co._inspection_cache) == 1
        before = store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        prepared = co.prepare_model(model, timeout=10)
        assert store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == before
        context = co.contexts[model]
        state = context.inputs.bind(prepared)
        assert context.submit(state.request, state.revision) is None
        co.enable_auto(model)
        state = context.inputs.apply(state.revision, {"seed": "23"}, auto_run=True)
        key = co._owner()
        assert key is not None
        assert co.wait(key, timeout=10).key == key
        assert any(event["body"].get("model_id") == model for event in co.events())


def test_environment_acquisition_failure_revokes_new_attempt(tmp_path, monkeypatch):
    workspace = project(tmp_path)
    model = workspace.models[0].id
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        prepared = co.prepare_model(model, timeout=10)
        monkeypatch.setattr(
            co.environments, "acquire", lambda *args: (_ for _ in ()).throw(ValueError("runtime drift"))
        )
        with pytest.raises(ValueError, match="drift"):
            co.admit_run(model, co.make_request(prepared), prepared.revision)
        assert co._owner() is None
        assert (
            store._db.execute("SELECT status FROM attempts ORDER BY generation DESC LIMIT 1").fetchone()[0] == "failed"
        )


def test_context_experiment_requires_its_captured_environment_on_resume(tmp_path, monkeypatch):
    workspace = project(tmp_path)
    model = workspace.models[0].id
    path = write_experiment(tmp_path, model="a.py:build", replications={"count": 1})
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        context = co.contexts[model]
        run_id = context.start_experiment(load_experiment(path))
        assert co.wait_experiment(run_id, timeout=20)["status"] == "completed"
        # Simulate a durable paused run after restart, then enforce its retained runtime.
        with store._transaction() as db:
            db.execute("UPDATE experiment_runs SET status='paused' WHERE id=?", (run_id,))
        pool = co.environments
        co.environments = None
        with pytest.raises(ValueError, match="captured environment"):
            co.resume_experiment(run_id)
        co.environments = pool
        monkeypatch.setattr(
            co._scheduler, "activate", lambda *a, **kw: (_ for _ in ()).throw(ValueError("dispatch unavailable"))
        )
        with pytest.raises(ValueError, match="dispatch unavailable"):
            co.resume_experiment(run_id)
        assert "experiment:" + run_id not in pool.owners(pool.selected.id)
