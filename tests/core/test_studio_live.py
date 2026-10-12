"""Studio transport drives the real coordinator without importing user code."""

import json
import time

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import AttemptKey
from simulatte.studio.app import StudioServer
from tests.execution.test_coordinator import MODEL, project


def setup(tmp_path, model=MODEL):
    coordinator = Coordinator(project(tmp_path, model))
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("Studio shell")
    (static / "manifest.json").write_text(json.dumps({"assets": {"index.html": "unused"}}))
    return coordinator, StudioServer(coordinator, 8123, static, timeout=10)


def auth(server, client):
    assert client.get(server.link()).status_code == 200
    return {"X-Simulatte-Session": server.secret}


def wait_state(client, headers, predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get("/api/state", headers=headers)
        assert response.status_code == 200
        state = response.json()
        if predicate(state):
            return state
        if state["startup"]["phase"] == "error":
            pytest.fail(f"Studio startup failed: {state['startup']['error']}")
        time.sleep(0.02)
    pytest.fail("Studio state did not converge")


def response(socket, identity):
    while True:
        message = socket.receive_json()
        if message.get("id") == identity:
            return message["body"]


def test_studio_auto_run_auth_references_and_committed_ranges(tmp_path):
    coordinator, server = setup(tmp_path)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            assert client.get("/").text == "Studio shell"
            assert client.get("/api/state").status_code == 403
            headers = auth(server, client)
            assert client.get("/api/state").status_code == 403
            state = wait_state(client, headers, lambda state: bool(state["results"]))
            metadata = client.get(state["metadata"]["url"], headers=headers)
            assert metadata.status_code == 200 and metadata.json()["selected"] == "build"
            result = state["results"][0]
            assert result["origin"] == state["revision"]
            assert state["latest_attempt"]["key"] == result["key"]
            assert state["latest_attempt"]["status"] == "succeeded"
            trace = client.get(result["trace"]["url"], headers=headers)
            assert trace.status_code == 200 and trace.content.startswith(b"SIMTRACE")
            head = client.head(result["trace"]["url"], headers=headers)
            part = client.get(
                result["trace"]["url"], headers={**headers, "Range": "bytes=0-11", "If-Match": head.headers["etag"]}
            )
            assert part.status_code == 206 and part.content == trace.content[:12]
            assert client.get(result["trace"]["url"], headers={**headers, "If-Match": '"wrong"'}).status_code == 412
            assert (
                client.get(result["trace"]["url"], headers={**headers, "Range": "bytes=999999999-"}).status_code == 416
            )
    finally:
        coordinator.close()


def test_websocket_parameters_are_revision_fenced_and_idempotent(tmp_path):
    coordinator, server = setup(tmp_path, MODEL.replace("n: int = 2", "n: int"))
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda state: state["inputs"] is not None)
            assert not state["results"] and "n" in state["inputs"]["issues"]
            with client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as socket:
                socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
                socket.receive_json()
                socket.send_json(
                    {"v": 1, "type": "run.start", "id": "required", "revision": state["revision"], "body": {}}
                )
                assert not response(socket, "required")["ok"]
                command = {
                    "v": 1,
                    "type": "params.set",
                    "id": "apply",
                    "revision": state["revision"],
                    "body": {"parameters": {"n": 4}},
                }
                socket.send_json(command)
                first = response(socket, "apply")
                assert first["ok"]
                socket.send_json(command)
                assert response(socket, "apply") == first
                socket.send_json({**command, "id": "stale", "body": {"parameters": {"n": 7}}})
                assert response(socket, "stale")["error"]["code"] == "revision_conflict"
            state = wait_state(client, headers, lambda state: bool(state["results"]))
            assert len(state["results"]) == 1
            assert coordinator.inputs.state.options["parameters"]["n"] == 4
    finally:
        coordinator.close()


def test_live_prefix_revokes_on_cancel_and_old_cancel_cannot_target_replacement(tmp_path):
    slow = MODEL.replace(
        "        yield env.timeout(1)", "        yield env.timeout(1)\n        import time; time.sleep(3)"
    )
    coordinator, server = setup(tmp_path, slow)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: value["active"] and value["active"]["prefix"])
            prefix = state["active"]["prefix"]
            assert client.get(prefix["url"], headers=headers).content.startswith(b"SIMTRACE")
            old = state["active"]["key"]
            with client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as socket:
                socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
                socket.receive_json()
                cancel = {
                    "v": 1,
                    "type": "run.cancel",
                    "id": "cancel",
                    "revision": state["revision"],
                    "attempt_id": old["attempt_id"],
                    "generation": old["generation"],
                    "body": {"request_id": old["request_id"]},
                }
                socket.send_json(cancel)
                assert response(socket, "cancel")["ok"]
                assert client.get(prefix["url"], headers=headers).status_code == 410
                socket.send_json({"v": 1, "type": "run.start", "id": "new", "revision": state["revision"], "body": {}})
                new = response(socket, "new")["key"]
                assert new["attempt_id"] != old["attempt_id"]
                socket.send_json({**cancel, "id": "old"})
                assert response(socket, "old")["error"]["code"] == "stale_attempt"
                assert coordinator.store.attempt(AttemptKey(**new))["status"] != "cancelled"
    finally:
        coordinator.close()


def test_layout_put_requires_origin_and_base_and_preserves_presentation_trace(tmp_path):
    from tests.execution.test_inputs import PRESENTATION_MODEL

    coordinator, server = setup(tmp_path, PRESENTATION_MODEL)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: bool(value["results"]))
            original = state["results"][0]["key"]
            layer = {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"color": "red"}}}}
            write_headers = {
                **headers,
                "X-Simulatte-Revision": json.dumps(state["revision"]),
                "X-Simulatte-Command": "layout-one",
            }
            assert client.put("/api/layout", json=layer, headers=write_headers).status_code == 403
            write_headers["Origin"] = server.origin
            written = client.put("/api/layout", json=layer, headers=write_headers)
            assert written.status_code == 200 and written.json()["ok"]
            assert client.put("/api/layout", json=layer, headers=write_headers).json() == written.json()
            stale = client.put(
                "/api/layout", json=layer, headers={**write_headers, "X-Simulatte-Command": "layout-two"}
            )
            assert stale.status_code == 409
            latest = client.get("/api/state", headers=headers).json()
            assert latest["view"]["key"] == original
            assert latest["revision"] != state["revision"]
            assert len(latest["results"]) == 1
            assert json.loads((coordinator.project.root / "layout.json").read_text()) == layer
    finally:
        coordinator.close()


def test_initial_http_routes_and_large_references_remain_authenticated(tmp_path):
    coordinator, server = setup(tmp_path)
    try:
        # No lifespan: exercise the interval before initial inspection exists.
        client = TestClient(server.app, base_url=server.origin)
        headers = auth(server, client)
        assert client.get("/api/state", headers=headers).json()["revision"] is None
        assert client.get("/api/state?secret=no", headers=headers).status_code == 404
        assert client.get("/missing").status_code == 404
        assert client.put("/missing", headers={"Origin": server.origin}).status_code == 404
        assert client.get("/api/missing", headers=headers).status_code == 404
        assert client.get("/api/references/no", headers=headers).status_code == 410
        for suffix in ("bad/1", "a" * 32 + "/x", "a" * 32 + "/1", "too/many/parts"):
            assert client.get("/api/prefix/" + suffix, headers=headers).status_code == 410
        inline = server._body({"value": "x" * (150 << 10)})
        huge = server._body({"value": "x" * (300 << 10)})
        for item in (inline, huge):
            ref = item["ref"]
            result = client.get(ref["url"], headers=headers)
            assert len(result.content) == ref["length"]
            assert client.head(ref["url"], headers=headers).headers["content-length"] == str(ref["length"])
        layout_headers = {
            **headers,
            "Origin": server.origin,
            "X-Simulatte-Command": "save",
            "X-Simulatte-Revision": "null",
        }
        assert client.put("/api/layout", content=b'{"schema_version":"1.0"}', headers=layout_headers).status_code == 409
        assert client.put("/api/layout", content=b"{", headers=layout_headers).status_code == 400
        assert (
            client.put(
                "/api/layout",
                content=b'{"schema_version":"1.0"}',
                headers={**layout_headers, "X-Simulatte-Command": "!"},
            ).status_code
            == 400
        )
        assert (
            client.put(
                "/api/layout", content=b'{"schema_version":"1.0"}', headers=headers | {"Origin": server.origin}
            ).status_code
            == 400
        )
        assert client.put("/api/layout", content=b" " * ((16 << 20) + 1), headers=layout_headers).status_code == 413
        external = tmp_path / "external"
        external.write_text("private")
        (server.static / "escape").symlink_to(external)
        server.allowed.add("escape")
        assert client.get("/escape").status_code == 404
        import asyncio

        result = asyncio.run(server.command({"type": "layout.get", "body": {}}, None))
        assert result["ok"] and result["revision"] is None
        (coordinator.project.root / "layout.json").write_text("{")
        result = asyncio.run(server.command({"type": "layout.get", "body": {}}, None))
        assert not json.loads(server.references.get(result["layout"]["id"]))["valid"]
    finally:
        coordinator.close()


def test_command_outcomes_view_selection_and_original_notification_revision(tmp_path):
    from tests.execution.test_inputs import PRESENTATION_MODEL

    coordinator, server = setup(tmp_path, PRESENTATION_MODEL)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: bool(value["results"]))
            with client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as socket:
                socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
                socket.receive_json()

                def send(kind, body, identity):
                    socket.send_json(
                        {"v": 1, "type": kind, "id": identity, "revision": state["revision"], "body": body}
                    )
                    return response(socket, identity)

                assert send("state.get", {}, "state")["state"]["revision"] == state["revision"]
                assert send("project.rescan", {}, "rescan")["ok"]
                assert send("project.rescan", {"retry": True}, "retry")["ok"]
                assert not send("project.rescan", {"retry": 1}, "invalid_retry")["ok"]
                assert len(client.get("/api/state", headers=headers).json()["results"]) == 1
                assert send("layout.get", {}, "layout")["ok"]
                assert send("layout.validate", {"schema_version": "1.0"}, "validate")["requires_rerun"] is False
                assert send("view.select", {"revision": state["revision"]}, "select")["view"]["committed"]
                assert send("state.get", {}, "selected")["state"]["selected_view"]["committed"]
                coordinator._emit("resync", None, {})
                while (message := socket.receive_json())["type"] != "state":
                    pass
                assert message["body"]["selected_view"]["committed"]
                assert not send("view.select", {"revision": {"number": 999, "digest": "a" * 64}}, "missing")["ok"]
                assert not send("run.start", {"parameters": {}}, "bad_run")["ok"]
                assert not send("run.cancel", {}, "bad_cancel")["ok"]
                assert send("layout.write", {"schema_version": "1.0"}, "write")["ok"]
                key = state["results"][0]["key"]
                newer = coordinator.store.create_revision({"changed": True}, revoke_active=True)
                coordinator._emit("heartbeat", AttemptKey("a" * 64, "a" * 32, 999), {"phase": "obsolete"})
                coordinator._emit("heartbeat", AttemptKey(**key), {"phase": "running"})
                while True:
                    message = socket.receive_json()
                    if message["type"] == "progress":
                        assert message["revision"] == state["revision"]
                        assert message["revision"]["number"] != newer.number
                        assert message["attempt_id"] == key["attempt_id"]
                        assert message["generation"] == key["generation"]
                        break
    finally:
        coordinator.close()


def test_invalid_cli_options_open_reviewable_forms_without_running(tmp_path):
    coordinator, server = setup(tmp_path)

    def invalid(prepared):
        raise ValueError("invalid CLI parameter")

    server.session.configure = invalid
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: value["startup"]["phase"] == "error")
            assert state["inputs"] is not None and not state["results"]
            assert "invalid CLI parameter" in state["startup"]["error"]["message"]
            with client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as socket:
                socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
                socket.receive_json()
                for kind, identity in [("run.start", "run"), ("params.set", "apply")]:
                    socket.send_json({"v": 1, "type": kind, "id": identity, "revision": state["revision"], "body": {}})
                    result = response(socket, identity)
                    assert result["ok"] == (kind == "params.set")
                state = wait_state(client, headers, lambda value: bool(value["results"]))
                assert state["startup"]["phase"] == "ready"
    finally:
        coordinator.close()


def test_server_shutdown_cleans_coordinator_when_background_task_fails(tmp_path, monkeypatch):
    coordinator, server = setup(tmp_path)

    async def fail():
        raise RuntimeError("background task failed")

    monkeypatch.setattr(server, "_pump", fail)
    with TestClient(server.app, base_url=server.origin):
        pass
    assert coordinator._closed
    from simulatte.execution.store import RunStore

    with RunStore.open(coordinator.project.root / ".simulatte"):
        pass


def test_attempt_prefix_gap_and_missing_receipt_are_rejected(tmp_path):
    slow = MODEL.replace("yield env.timeout(1)", 'yield env.timeout(1)\n        __import__("time").sleep(3)')
    coordinator, server = setup(tmp_path, slow)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: value["active"] and value["active"]["prefix"])
            prefix = state["active"]["prefix"]
            assert client.get(prefix["url"].rsplit("/", 1)[0] + "/99999", headers=headers).status_code == 410
            with coordinator.store._transaction() as db:
                db.execute(
                    "UPDATE receipts SET offset=offset+1 WHERE attempt=? AND stream='trace'", (prefix["attempt"],)
                )
            try:
                assert client.get(prefix["url"], headers=headers).status_code == 400
            finally:
                with coordinator.store._transaction() as db:
                    db.execute(
                        "UPDATE receipts SET offset=offset-1 WHERE attempt=? AND stream='trace'", (prefix["attempt"],)
                    )
    finally:
        coordinator.close()


def test_layout_get_returns_the_captured_base_not_unobserved_disk_bytes(tmp_path):
    import asyncio
    from dataclasses import asdict

    coordinator, server = setup(tmp_path)
    try:
        bound = coordinator.inputs.bind(coordinator.prepare(timeout=10))
        captured = coordinator.inputs.captured_inputs()[2].layer().to_dict()
        external = {
            "schema_version": "1.0",
            "placements": {"machine": {"kind": "server", "visual": {"color": "#ff0000"}}},
        }
        (coordinator.project.root / "layout.json").write_text(json.dumps(external))
        result = asyncio.run(server.command({"type": "layout.get", "body": {}}, None))
        shown = json.loads(server.references.get(result["layout"]["id"]))
        assert result["revision"] == asdict(bound.revision)
        assert shown["layer"] == captured
        assert json.loads((coordinator.project.root / "layout.json").read_text()) == external
    finally:
        coordinator.close()


def test_large_layout_validation_uses_bounded_authenticated_http_without_writing(tmp_path):
    from tests.execution.test_inputs import PRESENTATION_MODEL

    coordinator, server = setup(tmp_path, PRESENTATION_MODEL)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            state = wait_state(client, headers, lambda value: bool(value["results"]))
            request_headers = {
                **headers,
                "X-Simulatte-Command": "preview-large",
                "X-Simulatte-Revision": json.dumps(state["revision"]),
            }
            # A large set of legitimate visual fields exceeds a control frame.
            layer = {
                "schema_version": "1.0",
                "placements": {f"orphan-{i}": {"kind": "server", "visual": {"label": "x" * 1024}} for i in range(260)},
            }
            assert len(json.dumps(layer)) > 256 << 10
            assert client.put("/api/layout/validate", json=layer, headers=request_headers).status_code == 403
            request_headers["Origin"] = server.origin
            reply = client.put("/api/layout/validate", json=layer, headers=request_headers)
            assert reply.status_code == 200 and reply.json()["ok"]
            assert coordinator.store.current_revision().number == state["revision"]["number"]
            assert not (coordinator.project.root / coordinator.project.layout).exists()
            assert coordinator.store._db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1
            assert (
                client.put("/api/layout/validate", content=b" " * ((16 << 20) + 1), headers=request_headers).status_code
                == 413
            )
    finally:
        coordinator.close()


def test_initial_invalid_layout_can_be_repaired_with_exact_backup(tmp_path):
    from simulatte.execution.layout_files import backup_layout, LayoutFile
    from simulatte.execution.store import _read

    coordinator, server = setup(tmp_path)
    raw = b"{ broken layout"
    (coordinator.project.root / "layout.json").write_bytes(raw)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            headers = auth(server, client)
            deadline = time.monotonic() + 20
            while True:
                state = client.get("/api/state", headers=headers).json()
                if state["startup"]["phase"] == "error":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert state["inputs"] is None and not state["results"]
            with client.websocket_connect(
                server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
            ) as socket:
                socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
                socket.receive_json()
                socket.send_json({"v": 1, "type": "layout.get", "id": "get", "revision": state["revision"], "body": {}})
                loaded = response(socket, "get")
                assert not client.get(loaded["layout"]["url"], headers=headers).json()["valid"]
                socket.send_json(
                    {
                        "v": 1,
                        "type": "layout.write",
                        "id": "repair",
                        "revision": loaded["revision"],
                        "body": {"schema_version": "1.0"},
                    }
                )
                repaired = response(socket, "repair")
                assert repaired["ok"]
            ready = wait_state(client, headers, lambda value: bool(value["results"]))
            assert ready["startup"]["phase"] == "ready"
            identity = backup_layout(coordinator.store, LayoutFile("layout.json", raw))
            with coordinator.store._transaction() as db:
                saved = _read(db.execute("SELECT manifest FROM snapshots WHERE id=?", (identity,)).fetchone()[0])
            import base64

            assert base64.b64decode(saved["raw_base64"]) == raw
    finally:
        coordinator.close()


def test_unbound_layout_repair_requires_captured_invalid_bytes_and_current_file(tmp_path):
    from simulatte.execution.store import RevisionConflict

    coordinator, server = setup(tmp_path)
    try:
        base = coordinator.store.create_revision({"preparing": True})
        command = {
            "type": "layout.write",
            "revision": {"number": base.number, "digest": base.digest},
            "body": {"schema_version": "1.0"},
        }
        with pytest.raises(RevisionConflict, match="Read the failed layout"):
            server._command(command, None)
        server._command({"type": "layout.get", "body": {}}, None)
        with pytest.raises(ValueError, match="valid layout"):
            server._command(command, None)
        path = coordinator.project.root / "layout.json"
        path.write_bytes(b"{invalid")
        server._unbound_layout = None
        server._command({"type": "layout.get", "body": {}}, None)
        path.write_bytes(b"{changed")
        with pytest.raises(RevisionConflict, match="changed on disk"):
            server._command(command, None)
        assert path.read_bytes() == b"{changed"
    finally:
        coordinator.close()


def test_cli_and_studio_use_equivalent_input_content_and_fingerprints(tmp_path, capsys):
    from simulatte import Trace
    from simulatte.cli import main
    from simulatte.execution.store import RunStore

    cli_root, studio_root = tmp_path / "cli", tmp_path / "studio"
    cli_root.mkdir()
    studio_root.mkdir()
    spec = project(cli_root)
    assert main(["run", str(spec.root / "plant.py"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    cli_trace = Trace.open(result["trace"])
    with RunStore.open(spec.root / ".simulatte") as store:
        cli_request = store.request(result["request_id"]).to_dict()
    coordinator, server = setup(studio_root)
    try:
        with TestClient(server.app, base_url=server.origin) as client:
            state = wait_state(client, auth(server, client), lambda value: bool(value["results"]))
            actual = state["results"][0]["key"]
            studio_request = coordinator.store.request(actual["request_id"]).to_dict()
            # Revision numbers are local fences and remain part of request identity.
            assert cli_request.pop("layout_revision") > 0
            assert studio_request.pop("layout_revision") > 0
            assert studio_request == cli_request
            assert actual["attempt_id"] != result["attempt_id"]
            committed = coordinator.store.result(actual["attempt_id"])
            studio_trace = Trace.open(committed.path / "run.simtrace")
            assert cli_trace.verify() and studio_trace.verify()
            assert cli_trace.fingerprint == studio_trace.fingerprint
            assert (committed.path / "artifacts/answer.txt").read_text() == "7"
    finally:
        coordinator.close()
