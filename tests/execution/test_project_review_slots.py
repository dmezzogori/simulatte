"""Workspace inspection capacity is never an execution/replay overflow slot."""

import pytest

from simulatte.execution.store import RunStore, _read
from simulatte.experiments import load_experiment
from tests.execution.test_project_execution import project, coordinator
from tests.execution.test_scheduler import write_experiment, until


def test_saturated_execution_slots_do_not_borrow_inspection_capacity(tmp_path):
    workspace = project(tmp_path, hanging=True)
    path = write_experiment(tmp_path, model="a.py:build", replications={"count": 2})
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        a, b = (model.id for model in workspace.models)
        prepared = co.prepare_model(b, timeout=10)
        request = co.make_request(prepared, level="kpi")
        key = co.admit_run(b, request, prepared.revision).key
        original = co.wait(key, timeout=10)
        run = co.contexts[a].start_experiment(load_experiment(path))
        until(lambda: all(co._actors[name].current or co._actors[name].pending for name in co.slots.names()))
        with pytest.raises(RuntimeError, match="busy"):
            co.replay(original.key.request_id, timeout=2)
        with pytest.raises(ValueError, match="inspection"):
            co.submit(request, prepared.revision, slot="inspection")
        before = {row["attempt"] for row in store.replications(run)}
        with (tmp_path / "b.py").open("a") as stream:
            stream.write("\n# Uncached inspection while the four execution slots remain occupied.\n")
        inspected = co.prepare_model(b, timeout=10)
        assert inspected.revision != prepared.revision
        assert before == {row["attempt"] for row in store.replications(run)}
        with store._transaction() as db:
            assert all(
                _read(row[0])["kind"] == "inspection"
                for row in db.execute(
                    "SELECT q.body FROM attempts a JOIN requests q ON q.id=a.request_id WHERE a.slot='inspection'"
                )
            )
            assert not db.execute("SELECT 1 FROM attempts WHERE slot LIKE 'local-%' AND status='superseded'").fetchone()
        co.cancel_experiment(run)
