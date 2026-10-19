"""Workspace configuration and authenticated command errors remain reviewable."""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
import sys

import pytest
from starlette.testclient import TestClient

from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
from simulatte.execution.schema import AttemptKey
from simulatte.studio.project_commands import ProjectServer
from tests.execution.test_project_environments import fake_pool


@pytest.fixture
def service(tmp_path):
    install_starter(tmp_path, starter_files("production"))
    instance = ProjectService.open(StudioTarget(tmp_path, None))
    try:
        yield instance
    finally:
        instance.close()


def server_for(service):
    static = service.root / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    return ProjectServer(service, 8123, static)


def test_environment_preview_selection_busy_and_restart(service, monkeypatch):
    from simulatte.projects import bootstrap

    service.environments = pool = fake_pool(service.workspace, service.store, monkeypatch)
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda interpreter: {})
    server = server_for(service)
    assert server.coordinator.store is service.store
    preview = server._command({"type": "environment.prepare", "body": {}}, None)
    assert "uv.lock" in preview["files"]
    server._command({"type": "project.configure", "body": {"apply": preview["preview_id"]}}, None)
    first = service.prepare_environment({})["generation"]
    assert service.select_environment({"id": first["id"]})["status"] == "restart_required"
    assert service.restart_generation is None
    service.select_environment({"id": first["id"], "restart": True})
    assert service.restart_generation == first["id"]
    pool.activate(first["id"])
    pool.acquire(first["id"], "run")
    (service.root / "uv.lock").write_text("version=3\n")
    second = service.prepare_environment({})["generation"]
    assert service.select_environment({"id": second["id"]})["status"] == "busy"
    pool.release("run")
    existing = service.prepare_environment({"existing": sys.executable})["generation"]
    assert service.select_environment({"id": existing["id"]})["status"] == "selected"
    assert service.prepare_environment({"cancel": True})["ok"]


def test_configuration_preserves_context_only_for_same_entrypoint(service):
    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    co = service.ensure_coordinator()
    old = service.workspace.models[0]
    context = co.contexts[old.id]
    preview = service.configure({"models": [asdict(replace(old, name="Renamed"))]})
    service.configure({"apply": preview["preview_id"]})
    assert co.contexts[old.id] is context
    assert context.registration.name == "Renamed"
    preview = service.configure({"models": [asdict(replace(old, entrypoint="models/production.py:another"))]})
    service.configure({"apply": preview["preview_id"]})
    assert co.contexts[old.id] is not context
    for _ in range(17):
        service.configure({"models": [asdict(old)]})
    assert len(service._previews) <= 16
    with pytest.raises(ValueError, match="list"):
        service.configure({"models": [asdict(old)], "include": "all-files"})
    preview = service.configure({"models": [asdict(old)], "include": ["models/production.py"]})
    service.configure({"apply": preview["preview_id"]})
    assert len(service.workspace.models) == 1


def test_empty_setup_and_unknown_recovery_fail_actionably(tmp_path):
    service = ProjectService.open(StudioTarget(tmp_path, str(tmp_path / "model.py")))
    try:
        assert service.state()["legacy_reference"] == "model.py:build"
        with pytest.raises(ValueError, match="Configure"):
            service.prepare_environment({})
        service.close()
        service.close()
    finally:
        service.close()
    (tmp_path / "pyproject.toml").write_text("invalid toml !!!")
    import sqlite3

    with sqlite3.connect(tmp_path / ".simulatte/index.sqlite") as db:
        db.execute("PRAGMA user_version=999")
    broken = ProjectService.open(StudioTarget(tmp_path, None))
    try:
        assert broken.read_only and broken.reader is None
        server = server_for(broken)
        assert server.snapshot()["project"]["phase"] == "recovery"
        for kind, body in (("history.get", {}), ("run.get", {"attempt_id": "missing"})):
            reply = asyncio.run(server.command({"type": kind, "body": body}, None))
            assert not reply["ok"] and "unavailable" in reply["error"]["message"]
    finally:
        broken.close()


def test_model_commands_preserve_revision_and_history(service):
    from simulatte.execution.store import RevisionConflict

    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    server = server_for(service)
    model = service.workspace.models[0].id

    def command(kind, body=None, revision=None):
        return server._command({"type": kind, "body": body or {"model_id": model}, "revision": revision}, None)

    command("model.prepare")
    context = service.coordinator.contexts[model]
    revision = asdict(context.revision)
    assert command("project.get")["state"]["project"]["models"]
    assert command("history.get")["items"] == ()
    assert command("model.auto", {"model_id": model, "enabled": True})["auto_session"]
    assert command("model.auto", {"model_id": model, "enabled": False})["ok"]
    assert command("layout.get", revision=revision)["layout"]
    with pytest.raises(RevisionConflict):
        command("params.set", {"model_id": model, "changes": {"seed": "4"}})
    changed = command("params.set", {"model_id": model, "changes": {"seed": "4"}}, revision)
    started = command("run.start", revision=changed["revision"])
    key = AttemptKey(**started["key"])
    service.coordinator.wait(key, timeout=10)
    assert command("history.get")["items"][0]["id"] == key.attempt_id
    command("history.annotate", {"attempt_id": key.attempt_id, "name": "Reviewed", "important": True})
    assert command("run.get", {"attempt_id": key.attempt_id})["run"]["trace"]
    assert command("run.cancel", {"owner": asdict(key)})["cancelled"] is False
    assert command("workers.get")["ok"]
    definition = {
        "schema": 1,
        "name": "Study",
        "model": context.reference,
        "replications": {"count": 1},
        "configurations": {"grid": {"servers": [2]}},
    }
    assert command("experiment.define", {"model_id": model, "path": "study.experiment.json", "definition": definition})[
        "ok"
    ]
    experiment = command("experiment.start", {"model_id": model, "path": "study.experiment.json"})
    assert service.coordinator.wait_experiment(experiment["run_id"], timeout=20)["status"] == "completed"

    with pytest.raises(KeyError):
        command("run.get", {"attempt_id": "missing"})
    with pytest.raises(ValueError):
        command("unknown.command", revision=asdict(context.revision))
    # Captured invalid layout remains an explicit error document, never an empty repair.
    path = service.root / context.project.layout
    path.parent.mkdir(exist_ok=True)
    path.write_text("{invalid")
    loaded = command("layout.get", revision=asdict(context.revision))
    document = json.loads(server.references.get(loaded["layout"]["id"]))
    assert document["valid"] is False


def test_layout_http_rejects_bad_envelopes_before_mutation(service):
    server = server_for(service)
    with TestClient(server.app, base_url=server.origin) as client:
        bootstrap = client.get(server.link())
        assert 'rel="icon" href="data:' in bootstrap.text
        assert "img-src data:" in bootstrap.headers["content-security-policy"]
        headers = {"Origin": server.origin, "X-Simulatte-Session": server.secret}
        assert client.put("/api/layout", json={}, headers=headers).status_code == 400
        headers.update(
            {"X-Simulatte-Revision": "null", "X-Simulatte-Model": "bad", "X-Simulatte-Command": "bad command"}
        )
        assert client.put("/api/layout", json={}, headers=headers).status_code == 400
        headers["X-Simulatte-Command"] = "valid-id"
        assert client.put("/api/layout", content=b"x" * ((16 << 20) + 1), headers=headers).status_code == 413
        assert client.put("/api/unknown", json={}, headers=headers).status_code == 405
        assert client.get("/api/artifacts/missing/file", headers=headers).status_code == 404


def test_saved_history_reader_rejects_mutation_but_lists_evidence(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/stores/project-v2"
    shutil.copytree(fixture, tmp_path / ".simulatte")
    service = ProjectService.open(StudioTarget(tmp_path, None), read_only=True)
    try:
        server = server_for(service)
        page = server._command({"type": "history.get", "body": {}}, None)
        assert page["items"]
        reply = asyncio.run(server.command({"type": "project.configure", "body": {}}, None))
        assert not reply["ok"] and "read-only" in reply["error"]["message"]
    finally:
        service.close()


def test_changed_project_identity_opens_recovery_and_releases_writer(service):
    from uuid import uuid4
    from simulatte.execution.store import RunStore

    root = service.root
    identity = service.workspace.id
    service.close()
    path = root / "pyproject.toml"
    path.write_text(path.read_text().replace(identity, str(uuid4())))
    recovery = ProjectService.open(StudioTarget(root, None))
    try:
        assert recovery.read_only and "UUID" in recovery.error
        assert recovery.reader is not None and recovery.store is None
        with RunStore.open(root / ".simulatte"):
            pass
    finally:
        recovery.close()


def test_wrong_launcher_interpreter_is_read_only(service, monkeypatch):
    from simulatte.projects import bootstrap
    from simulatte.projects.environments import EnvironmentPool
    from simulatte.execution.store import RunStore

    root = service.root
    service.close()
    other = root / "another-python"
    other.write_bytes(b"test interpreter")
    with RunStore.open(root / ".simulatte") as store:
        pool = EnvironmentPool(root, store)
        monkeypatch.setattr(pool, "_probe", lambda path: {"runtime": {}, "distribution_roots": [], "studio": True})
        pool.activate(pool.use_existing(other).id)
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda path: {})
    recovery = ProjectService.open(StudioTarget(root, None))
    try:
        assert recovery.read_only and "handoff" in recovery.error
    finally:
        recovery.close()


def test_monitor_replacement_and_restart_do_not_keep_stale_model_context(service, monkeypatch):
    from simulatte.execution import workers, snapshot

    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    monkeypatch.setattr(workers, "load_workers", lambda: (_ for _ in ()).throw(ValueError("invalid worker config")))
    service.prepare_model(service.workspace.models[0].id)
    assert "invalid worker" in service.error
    server = server_for(service)
    monkeypatch.setattr(
        snapshot, "non_source_members", lambda project: (_ for _ in ()).throw(ValueError("changed membership"))
    )
    assert server.snapshot()["project"]["models"][0]["experiment_files"] == []

    async def exercise():
        task = asyncio.create_task(server._pump_project())
        try:
            await asyncio.sleep(0.05)
            assert len(server._monitors) == 1
            preview = service.configure({"models": []})
            service.configure({"apply": preview["preview_id"]})
            await asyncio.sleep(0.3)
            assert not server._monitors
            restarted = []
            server.on_restart = lambda: restarted.append(True)
            service.restart_generation = generation.id
            await asyncio.wait_for(task, 2)
            assert restarted == [True]
        finally:
            task.cancel()
            for child in server._tasks:
                child.cancel()
            await asyncio.gather(task, *server._tasks, return_exceptions=True)

    asyncio.run(exercise())


def test_required_inputs_still_resynchronize_without_admitting_work(service):
    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    source = service.root / "models/production.py"
    source.write_text(source.read_text().replace("servers: int = 3", "servers: int"))
    model = service.workspace.models[0].id
    server = server_for(service)
    server._command({"type": "model.prepare", "body": {"model_id": model}}, None)
    context = service.coordinator.contexts[model]
    with pytest.raises(ValueError, match="valid inputs"):
        server._command({"type": "run.start", "revision": asdict(context.revision), "body": {"model_id": model}}, None)
    assert not service.state()["history"]
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        with client.websocket_connect(
            server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
        ) as socket:
            socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
            first = socket.receive_json()
            second = socket.receive_json()
            assert first["type"] == second["type"] == "state"
            assert second["body"]["project"]["active_run"] is None
