"""Independent-review regressions at the authenticated workspace lifecycle boundary."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
import sys
import threading

import pytest
from starlette.testclient import TestClient

from simulatte.execution.coordinator import Coordinator
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
from tests.core.test_project_service_edges import server_for
from tests.execution.test_project_execution import project
from tests.execution.test_scheduler import write_experiment, until


def ready_service(root):
    workspace = project(root, hanging=True)
    service = ProjectService.open(StudioTarget(root, None))
    pool = service.environments
    pool.activate(pool.use_existing(Path(sys.executable)).id)
    return service, workspace


def authenticate(socket, server):
    socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
    while socket.receive_json()["type"] != "state":
        pass


def response(socket, identity):
    while True:
        item = socket.receive_json()
        if item["type"] == "response" and item["id"] == identity:
            return item["body"]


def test_authenticated_concurrent_lazy_starts_share_slot_ownership(tmp_path, monkeypatch):
    service, workspace = ready_service(tmp_path)
    for key in ("a", "b"):
        write_experiment(tmp_path, name=key, model=f"{key}.py:build", replications={"count": 1})
    # Warm the tool cache; the regression concerns shared actor ownership, not cache creation.
    service.ensure_coordinator().close()
    service.coordinator = None
    server = server_for(service)
    entered, second_dispatched, first_admitted = (threading.Event() for _ in range(3))
    made = []
    factory = Coordinator.for_workspace.__func__
    dispatch = server._command

    def held_factory(cls, *args, **kwargs):
        first = not entered.is_set()
        entered.set()
        assert second_dispatched.wait(10)
        if not first:
            assert first_admitted.wait(10)
        co = factory(cls, *args, **kwargs)
        made.append(co)
        return co

    def observed_dispatch(message, peer):
        if message["id"] == "second":
            second_dispatched.set()
        result = dispatch(message, peer)
        if message["id"] == "first":
            first_admitted.set()
        return result

    monkeypatch.setattr(Coordinator, "for_workspace", classmethod(held_factory))
    monkeypatch.setattr(server, "_command", observed_dispatch)
    # No lifespan pump: two real authenticated socket dispatch loops control this interleaving.
    client = TestClient(server.app, base_url=server.origin)
    client.get(server.link())
    try:
        with (
            client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as a,
            client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as b,
        ):
            authenticate(a, server)
            authenticate(b, server)
            a.send_json(
                {
                    "v": 1,
                    "type": "experiment.start",
                    "id": "first",
                    "body": {"model_id": workspace.models[0].id, "path": "a.experiment.json"},
                }
            )
            assert entered.wait(10)
            b.send_json(
                {
                    "v": 1,
                    "type": "experiment.start",
                    "id": "second",
                    "body": {"model_id": workspace.models[1].id, "path": "b.experiment.json"},
                }
            )
            assert response(a, "first")["ok"]
            assert response(b, "second")["ok"]
            until(
                lambda: bool(service.store._db.execute("SELECT 1 FROM attempts WHERE slot LIKE 'local-%'").fetchone())
            )
            with service.store._transaction() as db:
                statuses = [r[0] for r in db.execute("SELECT status FROM attempts WHERE slot LIKE 'local-%'")]
            assert "superseded" not in statuses
            assert len(made) == 1
            assert service.coordinator is made[0]
    finally:
        first_admitted.set()
        second_dispatched.set()
        for co in made:
            co.close()
        service.close()
        client.close()


@pytest.mark.parametrize("action", ["close", "configure"])
def test_initialization_is_serialized_with_lifecycle_changes(tmp_path, monkeypatch, action):
    service, workspace = ready_service(tmp_path)
    preview = service.configure({"models": [asdict(replace(workspace.models[0], name="Updated"))]})
    entered, release, changing = (threading.Event() for _ in range(3))
    factory = Coordinator.for_workspace.__func__
    made = []

    def held(cls, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        co = factory(cls, *args, **kwargs)
        made.append(co)
        return co

    def change():
        changing.set()
        return service.close() if action == "close" else service.configure({"apply": preview["preview_id"]})

    monkeypatch.setattr(Coordinator, "for_workspace", classmethod(held))
    try:
        with ThreadPoolExecutor(2) as executor:
            constructing = executor.submit(service.ensure_coordinator)
            assert entered.wait(10)
            changed = executor.submit(change)
            assert changing.wait(10)
            try:
                assert not changed.done(), "lifecycle change bypassed initialization ownership"
            finally:
                release.set()
            co = constructing.result(10)
            changed.result(10)
            if action == "close":
                assert co._closed
                with pytest.raises(ValueError, match="closed"):
                    service.ensure_coordinator()
            else:
                assert co.contexts[workspace.models[0].id].registration.name == "Updated"
    finally:
        release.set()
        for co in made:
            co.close()
        service.close()


@pytest.mark.skipif(sys.platform == "win32", reason="native alternate venv interpreter path is POSIX")
def test_restart_fences_late_authenticated_admission_and_direct_context_work(tmp_path):
    service, workspace = ready_service(tmp_path)
    server = server_for(service)
    pool = service.environments
    target = pool.use_existing(Path(sys.executable).with_name("python3"))
    model = workspace.models[0].id
    service.prepare_model(model)
    co = service.coordinator
    state = co.inputs_for(model).state
    until(lambda: not pool.owners(pool.selected.id))
    client = TestClient(server.app, base_url=server.origin)
    client.get(server.link())
    try:
        with (
            client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as a,
            client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as b,
        ):
            authenticate(a, server)
            authenticate(b, server)
            a.send_json(
                {"v": 1, "type": "environment.select", "id": "restart", "body": {"id": target.id, "restart": True}}
            )
            assert response(a, "restart")["status"] == "restart_required"
            b.send_json(
                {
                    "v": 1,
                    "type": "run.start",
                    "id": "late",
                    "revision": asdict(state.revision),
                    "body": {"model_id": model},
                }
            )
            reply = response(b, "late")
            assert not reply["ok"] and "restart" in reply["error"]["message"].lower()
            with pytest.raises(RuntimeError, match="restart"):
                co.admit_run(model, state.request, state.revision)
            assert co._owner() is None
    finally:
        service.close()
        client.close()


@pytest.mark.skipif(sys.platform == "win32", reason="native alternate venv interpreter path is POSIX")
def test_environment_change_cannot_cancel_work_admitted_during_validation(tmp_path, monkeypatch):
    from simulatte.projects import bootstrap

    service, workspace = ready_service(tmp_path)
    pool = service.environments
    target = pool.use_existing(Path(sys.executable).with_name("python3"))
    model = workspace.models[0].id
    service.prepare_model(model)
    co = service.coordinator
    state = co.inputs_for(model).state
    until(lambda: not pool.owners(pool.selected.id))
    admitted = []
    validate = bootstrap.validate_runtime

    def concurrent_admission(interpreter):
        result = validate(interpreter)
        admitted.append(co.admit_run(model, state.request, state.revision))
        return result

    monkeypatch.setattr(bootstrap, "validate_runtime", concurrent_admission)
    try:
        assert service.select_environment({"id": target.id, "restart": True})["status"] == "busy"
        assert admitted[0].status == "started"
        assert service.restart_generation is None
        assert co._owner() == admitted[0].key
        assert service.store.attempt(admitted[0].key)["status"] not in ("cancelled", "superseded")
        co.cancel(admitted[0].key)  # Only the explicit test-owner stop cancels it.
    finally:
        service.close()
