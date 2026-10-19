"""The pending UI socket never queues its own environment cancellation behind uv."""

import sys
import threading

from starlette.testclient import TestClient

from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
from tests.core.test_project_service_edges import server_for
from tests.core.test_project_review_lifecycle import authenticate, response


def test_pending_authenticated_socket_preparation_is_cancelled_by_same_tab(tmp_path, monkeypatch):
    install_starter(tmp_path, starter_files("production"))
    (tmp_path / "uv.lock").write_text("version = 1\n")
    service = ProjectService.open(StudioTarget(tmp_path, None))
    server = server_for(service)
    pool = service.environments
    entered = threading.Event()
    real_run = pool._run

    def slow_uv(command, *, cwd, environment=None):
        entered.set()
        real_run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=cwd)

    monkeypatch.setattr(pool, "_run", slow_uv)
    monkeypatch.setattr(pool, "_uv", lambda: "test-uv")
    client = TestClient(server.app, base_url=server.origin)
    client.get(server.link())
    headers = {"Origin": server.origin, "X-Simulatte-Session": server.secret}
    url = "/api/environment/cancel/preparation-1"
    try:
        with client.websocket_connect(
            server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
        ) as socket:
            authenticate(socket, server)
            socket.send_json(
                {"v": 1, "type": "environment.prepare", "id": "prepare", "body": {"operation_id": "preparation-1"}}
            )
            assert entered.wait(10)
            assert client.put(url).status_code == 403
            assert client.put(url, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
            cancelled = client.put(url, headers=headers)
            assert cancelled.status_code == 200
            assert cancelled.json()["ok"]
            reply = response(socket, "prepare")
            assert not reply["ok"] and "cancel" in reply["error"]["message"].lower()
            assert client.put(url, headers=headers).json() == cancelled.json()
            assert pool.selected is None
            assert not list(pool.cache.glob("*/published"))
            # Cancellation arriving before dispatch must also be remembered for that ID.
            assert client.put("/api/environment/cancel/preparation-2", headers=headers).status_code == 200
            socket.send_json(
                {"v": 1, "type": "environment.prepare", "id": "precancelled", "body": {"operation_id": "preparation-2"}}
            )
            reply = response(socket, "precancelled")
            assert not reply["ok"] and "cancel" in reply["error"]["message"].lower()
            entered.clear()
            socket.send_json(
                {"v": 1, "type": "environment.prepare", "id": "next", "body": {"operation_id": "preparation-3"}}
            )
            assert entered.wait(10)
            assert client.put(url, headers=headers).status_code == 200
            assert not pool._cancelled.is_set(), "late cancellation affected a different operation"
            assert client.put("/api/environment/cancel/preparation-3", headers=headers).status_code == 200
            assert not response(socket, "next")["ok"]
    finally:
        pool.cancel()
        service.close()
        client.close()


def test_cancel_validation_and_parallel_prepare_leave_current_operation_owned(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import pytest

    install_starter(tmp_path, starter_files("production"))
    service = ProjectService.open(StudioTarget(tmp_path, None))
    server = server_for(service)
    client = TestClient(server.app, base_url=server.origin)
    client.get(server.link())
    headers = {"Origin": server.origin, "X-Simulatte-Session": server.secret}
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        raise ValueError("prepared operation stopped")

    monkeypatch.setattr(service.environments, "prepare", held)
    try:
        assert client.put("/api/environment/cancel/valid", headers=headers, content=b"unexpected").status_code == 413
        bad = client.put("/api/environment/cancel/invalid!", headers=headers)
        assert bad.status_code == 400 and "operation id" in bad.json()["error"]["message"]
        with pytest.raises(ValueError, match="operation id"):
            service.prepare_environment({"operation_id": []})
        with ThreadPoolExecutor(1) as executor:
            first = executor.submit(service.prepare_environment, {"operation_id": "owned"})
            assert entered.wait(10)
            try:
                with pytest.raises(ValueError, match="busy"):
                    service.prepare_environment({"operation_id": "other"})
                assert service._preparation_id == "owned"
                assert client.put("/api/environment/cancel/other", headers=headers).status_code == 200
                assert not service.environments._cancelled.is_set()
            finally:
                release.set()
            with pytest.raises(ValueError, match="stopped"):
                first.result(10)
        assert service._preparation_id is None
    finally:
        release.set()
        service.close()
        client.close()
