"""Websocket handshake never grants project access on a cookie alone."""

from contextlib import ExitStack

import pytest

pytest.importorskip("starlette")
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from simulatte.studio.auth import SessionAuth
from simulatte.studio.websocket import ConnectionHub


@pytest.fixture
def connected():
    auth = SessionAuth(8123)
    calls = []

    async def dispatch(message, peer):
        calls.append(message)
        return {"ok": True, "count": len(calls)}

    async def state(peer):
        return {"revision": None}

    hub = ConnectionHub(auth, dispatch, state)
    app = Starlette(routes=[Route("/bootstrap", auth.bootstrap), WebSocketRoute("/ws", hub.handle)])
    with TestClient(app, base_url=auth.origin) as client:
        assert client.get(auth.link()).status_code == 200
        yield auth, hub, calls, client


def login(auth, client):
    socket = client.websocket_connect(auth.origin.replace("http:", "ws:") + "/ws", headers={"Origin": auth.origin})
    return socket


def test_cookie_alone_bad_origin_host_and_secret(connected):
    auth, hub, calls, client = connected
    for headers in [{}, {"Origin": "null"}, {"Origin": auth.origin, "Host": "localhost:8123"}]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(auth.origin.replace("http:", "ws:") + "/ws", headers=headers):
                pass
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": "wrong"})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008
    assert not calls and hub.pending == 0 and not hub.connections


def test_auth_then_commands_are_cached_and_conflicting_id_reuse_closes(connected):
    auth, hub, calls, client = connected
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        state = socket.receive_json()
        assert state["type"] == "state" and state["event_seq"] > 0
        command = {"v": 1, "type": "run.start", "id": "one", "revision": None, "body": {}}
        socket.send_json(command)
        first = socket.receive_json()
        socket.send_json(command)
        assert socket.receive_json() == first
        assert len(calls) == 1
        socket.send_json({**command, "type": "params.set"})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1002


def test_auth_deadline_and_frame_limits(connected, monkeypatch):
    auth, hub, calls, client = connected
    monkeypatch.setattr(hub, "auth_timeout", 0.03)
    with login(auth, client) as socket:
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008
    for payload, expected in [(b"binary", 1002), ("x" * ((256 << 10) + 1), 1009), ("{}", 1002)]:
        with login(auth, client) as socket:
            if isinstance(payload, bytes):
                socket.send_bytes(payload)
            else:
                socket.send_text(payload)
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
            assert error.value.code == expected


def test_connection_quotas(connected):
    auth, hub, calls, client = connected
    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(login(auth, client))
        with pytest.raises(WebSocketDisconnect):
            with login(auth, client):
                pass
        assert hub.pending == 4
    with ExitStack() as stack:
        for _ in range(16):
            socket = stack.enter_context(login(auth, client))
            socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
            socket.receive_json()
        with login(auth, client) as socket:
            socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
            assert error.value.code == 1013
        assert len(hub.connections) == 16


def test_slow_tab_closes_for_resync_and_shutdown_uses_going_away(connected):
    auth, hub, calls, client = connected
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        socket.receive_json()

        async def flood():
            for i in range(257):
                hub.notify("run_state", {"index": i})

        client.portal.call(flood)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1013
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        socket.receive_json()
        client.portal.call(hub.close)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1001


def test_coalesced_notifications_remain_ordered_and_authenticated(connected):
    auth, hub, calls, client = connected
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        initial = socket.receive_json()

        async def notify():
            key = {"attempt_id": "a" * 32}
            hub.notify("progress", {"value": 1}, key=key)
            hub.notify("run_state", {"status": "running"}, key=key)
            hub.notify("progress", {"value": 2}, key=key)

        client.portal.call(notify)
        status, progress = socket.receive_json(), socket.receive_json()
        assert status["event_seq"] < progress["event_seq"]
        assert status["event_seq"] > initial["event_seq"]
        assert progress["body"]["value"] == 2


def test_unexpected_transport_callback_failure_closes_with_internal_error(connected):
    auth, hub, calls, client = connected

    async def broken(*args):
        raise RuntimeError("callback failed")

    hub.dispatch = broken
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        socket.receive_json()
        socket.send_json({"v": 1, "type": "state.get", "id": "one", "revision": None, "body": {}})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1011
    assert not hub.connections and not hub.pending


def test_initial_state_precedes_notifications_emitted_while_snapshot_is_read(connected):
    auth, hub, calls, client = connected

    async def state(peer):
        hub.notify("progress", {"value": 1})
        return {"snapshot": True}

    hub.state = state
    with login(auth, client) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": auth.secret})
        first, second = socket.receive_json(), socket.receive_json()
        assert first["type"] == "state" and second["type"] == "progress"
        assert first["event_seq"] < second["event_seq"]


@pytest.mark.parametrize("shutdown", [False, True])
def test_slow_socket_closes_even_when_network_send_is_blocked(shutdown):
    import asyncio
    import json
    from types import SimpleNamespace
    from starlette.datastructures import Headers
    from simulatte.studio.auth import SessionAuth
    from simulatte.studio.websocket import ConnectionHub

    async def run():
        auth = SessionAuth(8123)
        entered = asyncio.Event()

        class Socket:
            headers = Headers({"host": auth.host, "origin": auth.origin})
            cookies = {auth.cookie_name: auth.cookie}
            url = SimpleNamespace(query="")
            authenticated = False
            closed = None

            async def accept(self):
                pass

            async def receive(self):
                if not self.authenticated:
                    self.authenticated = True
                    return {
                        "type": "websocket.receive",
                        "text": json.dumps({"v": 1, "type": "auth", "secret": auth.secret}),
                    }
                await asyncio.Future()

            async def send_text(self, raw):
                entered.set()
                await asyncio.Future()

            async def close(self, *, code):
                self.closed = code

        async def state(peer):
            return {}

        hub = ConnectionHub(auth, state, state)
        socket = Socket()
        task = asyncio.create_task(hub.handle(socket))
        await entered.wait()
        if shutdown:
            await hub.close()
        else:
            for i in range(257):
                hub.notify("run_state", {"index": i})
        await asyncio.wait_for(task, 1)
        assert socket.closed == (1001 if shutdown else 1013) and not hub.connections

    asyncio.run(run())
