"""Saved workspace reads do not depend on recent pages or initialized execution."""

from simulatte.execution import schema
from simulatte.execution.project_store import ProjectStore
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
from tests.core.test_project_service_edges import server_for
from tests.execution.test_schema import request


def test_bookmarked_old_annotations_are_authoritative_in_normal_and_recovery_reads(tmp_path):
    service = ProjectService.open(StudioTarget(tmp_path, None))
    keys = []
    for _ in range(51):
        revision = service.store.create_revision({})
        key = service.store.assign(request(schema), revision)
        service.store.revoke(key, "cancelled")
        keys.append(key)
    ProjectStore(service.store).annotate(
        keys[0].attempt_id, name="Keep this study", text="Original note", important=True
    )
    service.close()
    for recovery in (False, True):
        service = ProjectService.open(StudioTarget(tmp_path, None), read_only=recovery)
        static = tmp_path / "static"
        if static.exists():
            (static / "manifest.json").unlink()
            static.rmdir()
        server = server_for(service)
        try:
            assert not any(item["id"] == keys[0].attempt_id for item in service.state()["history"])
            details = server._command({"type": "run.get", "body": {"attempt_id": keys[0].attempt_id}}, None)["run"]
            assert {name: details[name] for name in ("name", "text", "important")} == {
                "name": "Keep this study",
                "text": "Original note",
                "important": True,
            }
            if not recovery:
                server._command(
                    {
                        "type": "history.annotate",
                        "body": {
                            "attempt_id": keys[0].attempt_id,
                            "name": "Keep this study",
                            "text": "Original note",
                            "important": True,
                        },
                    },
                    None,
                )
            assert service.coordinator is None
        finally:
            service.close()


def test_saved_experiment_reports_reopen_without_execution_and_in_recovery(tmp_path):
    import asyncio
    from simulatte.execution.store import RunStore
    from simulatte.experiments import load_experiment
    from tests.execution.test_project_execution import project, coordinator
    from tests.execution.test_scheduler import write_experiment

    workspace = project(tmp_path)
    definition = write_experiment(tmp_path, model="a.py:build", replications={"count": 1})
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        run_id = co.contexts[workspace.models[0].id].start_experiment(load_experiment(definition))
        assert co.wait_experiment(run_id, timeout=20)["status"] == "completed"
    (tmp_path / "a.py").write_text('raise AssertionError("saved experiments must not import model code")')
    for recovery in (False, True):
        service = ProjectService.open(StudioTarget(tmp_path, None), read_only=recovery)
        static = tmp_path / "static"
        if static.exists():
            (static / "manifest.json").unlink()
            static.rmdir()
        server = server_for(service)
        try:
            assert server.snapshot()["experiments"][0]["run_id"] == run_id
            result = asyncio.run(server.command({"type": "comparison.get", "body": {"run_id": run_id}}, None))
            assert result["ok"], result
            report = result["report"]
            assert report["status"] == "completed" and len(report["replications"]) == 2
            attempts = [item["attempt"] for item in report["replications"]]
            result = asyncio.run(
                server.command({"type": "series.get", "body": {"attempts": attempts, "keys": ["missing/key"]}}, None)
            )
            assert result["ok"] and len(result["series"]) == 2
            assert service.coordinator is None
            if recovery:
                for kind in ("experiment.start", "experiment.resume", "experiment.delete", "replay.start"):
                    result = asyncio.run(server.command({"type": kind, "body": {"run_id": run_id}}, None))
                    assert not result["ok"] and "read-only" in result["error"]["message"]
        finally:
            service.close()
