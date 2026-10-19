"""Restart recovery releases transient leases while retaining captured provenance."""

from pathlib import Path
import sys

from simulatte.execution import schema
from simulatte.execution.project_store import ProjectStore
from simulatte.execution.store import RunStore, StaleAttempt
from simulatte.projects.environments import EnvironmentPool
from tests.execution.test_schema import request
from tests.execution.test_store_slots import DEFINITION, configurations, replications
import pytest


def test_exclusive_restart_fences_attempts_and_releases_transient_generation_owners(tmp_path):
    root = tmp_path / ".simulatte"
    with RunStore.open(root) as store:
        pool = EnvironmentPool(tmp_path, store)
        old = pool.use_existing(Path(sys.executable))
        pool.activate(old.id)
        new = pool.use_existing(Path(sys.executable).with_name("python3" if sys.platform != "win32" else "python.exe"))
        revision = store.create_revision({})
        attempt = store.assign(request(schema), revision)
        run = store.create_experiment_run(
            store.put_experiment(DEFINITION),
            {"environment": old.id, "environment_evidence": dict(old.evidence)},
            configurations(),
            replications(),
        )
        pool.acquire(old.id, attempt.attempt_id)
        pool.acquire(old.id, "experiment:" + run)
        ProjectStore(store).link("attempt", attempt.attempt_id, "environment", old.id)
        captured = store.experiment_run(run)["preparation"]
        assert len(pool.owners(old.id)) == 2
        # Writer interruption leaves these durable rows; no Coordinator.close release runs.
    with RunStore.open(root) as store:
        pool = EnvironmentPool(tmp_path, store)
        assert store.attempt(attempt)["status"] == "lost"
        assert store.slots()["interactive"]["active"] is None
        with pytest.raises(StaleAttempt):
            store.transition(attempt, "running")
        assert store.experiment_run(run)["status"] == "paused"
        assert store.experiment_run(run)["preparation"] == captured
        assert pool.get(old.id).evidence == old.evidence
        assert pool.owners(old.id) == ()
        assert pool.select(new.id) in ("selected", "restart_required")
        with store._transaction() as db:
            assert db.execute("SELECT target_id FROM \"references\" WHERE owner_kind='attempt'").fetchone()[0] == old.id
