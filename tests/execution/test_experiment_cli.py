"""Headless `simulatte experiment` and `store prune` commands: one JSON envelope, SP4 exit codes, core install."""

import json
import sys
from unittest.mock import patch

from simulatte.cli import main
from simulatte.execution import coordinator as m
from simulatte.execution.slots import SlotConfig
from simulatte.execution.store import RunStore, _blob
from simulatte.experiments import load_experiment
from tests.execution.test_coordinator import project
from tests.execution.test_scheduler import SLOW, attempts, until, write_experiment
from tests.execution.test_store_slots import DEFINITION, configurations, replications

NO_WEB = {"starlette": None, "uvicorn": None, "watchfiles": None, "websockets": None}


def envelope(capsys):
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return json.loads(out)


def test_experiment_run_status_delete_and_prune_without_web(tmp_path, capsys):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 2})
    root = str(spec.root)
    with patch.dict(sys.modules, NO_WEB):
        assert main(["experiment", "run", str(path), "--project", root, "--local-slots", "2", "--json"]) == 0
        result = envelope(capsys)
        assert result["status"] == "completed" and result["counts"]["succeeded"] == 4
        run_id = result["run_id"]
        assert main(["experiment", "status", run_id, "--project", root, "--json"]) == 0
        status = envelope(capsys)
        assert status["run_id"] == run_id and [c["counts"]["succeeded"] for c in status["configurations"]] == [2, 2]
        assert main(["experiment", "status", "--project", root, "--json"]) == 0
        (listed,) = envelope(capsys)["runs"]
        assert listed["run_id"] == run_id and listed["status"] == "completed"
        assert main(["experiment", "status", run_id, "--project", root]) == 0
        assert "n=3/base" in capsys.readouterr().out
        # The tiny model reports no KPIs: the table is empty. Then store scalars by hand: n=3 and n=4 differ by 1.
        assert main(["experiment", "results", run_id, "--project", root, "--json"]) == 0
        assert envelope(capsys)["rows"] == []
        with RunStore.open(spec.root / ".simulatte") as store:
            items = store.replications(run_id)
            with store._transaction() as db:
                for item in items:
                    value = item["index"] * 2.5 + item["configuration"]
                    db.execute("INSERT INTO kpis VALUES (?,?,?)", (item["attempt"], "shop/wip", _blob(value)))
        assert main(["experiment", "results", run_id, "--project", root, "--baseline", "n=3/base"]) == 0
        table = capsys.readouterr().out
        assert "pointwise 95 % t intervals" in table and "paired differences, configuration - n=3/base" in table
        assert main(["experiment", "results", run_id, "--project", root, "--json"] + ["--baseline", "n=3/base"]) == 0
        report = envelope(capsys)
        assert report["pointwise"] is True and report["rows"][0]["estimate"]["state"] == "ok"
        assert (
            report["paired"][0]["estimate"]["mean"] == 1.0 and report["paired"][0]["estimate"]["state"] == "degenerate"
        )
        assert main(["experiment", "results", run_id, "--project", root, "--bonferroni", "--level", "0.9"]) == 0
        assert "Bonferroni-adjusted t intervals, 90 % family level (95 % each, 2 intervals)" in capsys.readouterr().out
        for extra in (["--baseline", "nowhere"], ["--level", "1.5"]):
            assert main(["experiment", "results", run_id, "--project", root, "--json", *extra]) == 2
            assert envelope(capsys)["status"] == "validation_failed"
        assert main(["experiment", "results", "0" * 64, "--project", root]) == 2
        assert "0" * 64 in capsys.readouterr().err
        assert main(["experiment", "cancel", run_id, "--project", root, "--json"]) == 2
        assert envelope(capsys)["status"] == "validation_failed"
        assert main(["experiment", "resume", run_id, "--project", root, "--json"]) == 2
        assert envelope(capsys)["status"] == "validation_failed"
        assert main(["store", "prune", "--dry-run", "--project", root, "--json"]) == 0
        pruned = envelope(capsys)
        assert pruned["dry_run"] is True and pruned["results"] == []
        with RunStore.open(spec.root / ".simulatte") as store:
            request = store.request(store.replications(run_id)[0]["request_id"])
        kept = {request.source_hash, request.input_hash, request.runtime["trusted_runtime_hash"]}
        assert not kept & set(pruned["snapshots"])
        assert main(["store", "prune", "--project", root]) == 0
        assert capsys.readouterr().out.startswith("removed 0 results")
        # Pruning leaves the store usable: the same file is the same, completed run.
        assert main(["experiment", "run", str(path), "--project", root, "--json"]) == 0
        assert envelope(capsys)["run_id"] == run_id
        assert main(["experiment", "delete", run_id, "--project", root, "--json"]) == 0
        deleted = envelope(capsys)
        assert deleted["status"] == "succeeded" and len(deleted["results"]) == 4
        assert main(["experiment", "status", run_id, "--project", root, "--json"]) == 2
        assert envelope(capsys)["status"] == "validation_failed"
        with RunStore.open(spec.root / ".simulatte") as store:
            with store._transaction() as db:
                assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0] == 0


def test_unknown_worker_exits_2_before_scheduling(tmp_path, capsys):
    spec = project(tmp_path)
    path = write_experiment(spec.root)
    command = ["experiment", "run", str(path), "--project", str(spec.root), "--workers", "local,nowhere", "--json"]
    assert main(command) == 2
    failure = envelope(capsys)
    assert failure["status"] == "validation_failed" and "nowhere" in failure["message"]
    with RunStore.open(spec.root / ".simulatte") as store:
        assert store.current_revision() is None
        with store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0] == 0


def test_cancel_and_resume_paused_runs(tmp_path, capsys):
    spec = project(tmp_path, SLOW)
    root = str(spec.root)
    with RunStore.open(spec.root / ".simulatte") as store:
        stored = store.create_experiment_run(store.put_experiment(DEFINITION), {}, configurations(), replications())
    assert main(["experiment", "cancel", stored, "--project", root, "--json"]) == 0
    assert envelope(capsys) == {"status": "cancelled", "run_id": stored}
    with RunStore.open(spec.root / ".simulatte") as store:
        assert {item["status"] for item in store.replications(stored)} == {"cancelled"}
    path = write_experiment(spec.root, replications={"count": 2}, configurations={"grid": {"delay": [0.5]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        until(lambda: attempts(coordinator.store) != [])
    assert main(["experiment", "resume", run_id, "--project", root, "--local-slots", "1", "--json"]) == 0
    resumed = envelope(capsys)
    assert resumed["status"] == "completed" and resumed["counts"]["succeeded"] == 2


def test_interrupt_and_invalid_invocations(tmp_path, capsys):
    spec = project(tmp_path)
    path = write_experiment(spec.root)
    root = str(spec.root)

    def interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt

    with patch.object(m.Coordinator, "start_experiment", interrupt):
        assert main(["experiment", "run", str(path), "--project", root, "--json"]) == 130
    assert envelope(capsys)["status"] == "cancelled"
    assert main(["experiment", "run", str(spec.root / "missing.experiment.json"), "--project", root, "--json"]) == 2
    assert envelope(capsys)["status"] == "validation_failed"
    assert main(["experiment", "run", str(path), "--project", root, "--local-slots", "0", "--json"]) == 2
    assert envelope(capsys)["status"] == "validation_failed"
    assert main(["experiment", "delete", "0" * 64, "--project", root]) == 2
    assert "0" * 64 in capsys.readouterr().err
    with RunStore.open(spec.root / ".simulatte"):
        assert main(["experiment", "status", "--project", root, "--json"]) == 1
        assert envelope(capsys)["status"] == "failed"


def test_failure_envelopes(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["experiment", "status", "--project", str(empty), "--json"]) == 2
    assert "No run store" in envelope(capsys)["message"]
    assert main(["store", "prune", "--project", str(empty), "--json"]) == 2
    assert envelope(capsys)["status"] == "validation_failed"
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    root = str(spec.root)

    def fatal(self, run_id, timeout=None):
        raise m.ExecutionFailure(None, "disk_full", {"message": "index full"})

    with patch.object(m.Coordinator, "wait_experiment", fatal):
        assert main(["experiment", "run", str(path), "--project", root, "--json"]) == 1
    failure = envelope(capsys)
    assert failure["status"] == "disk_full" and failure["run_id"]
    close = m.Coordinator.close

    def broken_close(self):
        close(self)
        raise RuntimeError("shutdown failed")

    with patch.object(m.Coordinator, "close", broken_close):
        assert main(["experiment", "run", str(path), "--project", root, "--json"]) == 1
    assert envelope(capsys) == {"status": "failed", "message": "shutdown failed"}
    with RunStore.open(spec.root / ".simulatte"):
        assert main(["store", "prune", "--project", root]) == 1
    assert "Another coordinator" in capsys.readouterr().err
