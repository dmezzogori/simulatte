"""Attempt fences and private message classification, independent of OS timing."""

from collections import deque
from types import SimpleNamespace

import pytest

from simulatte._wire import freeze, FrozenMap
from simulatte.execution import supervisor as m
from simulatte.execution.protocol import Message
from simulatte.execution.schema import AttemptKey
from simulatte.execution.transport import envelope

KEY = AttemptKey("a" * 64, "b" * 32, 1)
OTHER = AttemptKey("c" * 64, "d" * 32, 2)


def message(kind, body=None, key=KEY, payload=b""):
    value = freeze(envelope(kind, body or {}, 0, key))
    assert isinstance(value, FrozenMap)
    return Message(value, payload)


def actor(monkeypatch, messages=(), code=None):
    output, controls = [], []
    root = m.Supervisor(SimpleNamespace(send=lambda kind, body, **kw: output.append((kind, body, kw))))
    inbox = deque(messages)

    def receive(timeout):
        if not inbox:
            if code is not None:
                raise OSError("child EOF")
            raise TimeoutError()
        return inbox.popleft()

    def wait(timeout):
        if code is None:
            raise TimeoutError()
        return code

    child = SimpleNamespace(
        key=KEY,
        channel=SimpleNamespace(receive=receive, send=lambda *a, **k: controls.append(a)),
        process=SimpleNamespace(wait=wait),
        escalate=lambda: None,
        close=lambda: controls.append(("closed",)),
        cancel=lambda **kw: controls.append(("cancel", kw)),
        progress=m.Progress(),
        diagnostics=m.Diagnostics(),
        proposal=None,
        pending=None,
        hello=False,
        exit_code=None,
        cancel_at=None,
        timeout=False,
        failure=None,
        reported_drops=0,
    )
    monkeypatch.setattr(root, "child", child)
    return root, child, output, controls


def test_busy_stale_control_and_ack_dispatch(monkeypatch):
    root, child, output, controls = actor(monkeypatch)
    root._command(message("inspect", key=OTHER))
    assert output[0][1]["code"] == "worker_busy"
    for kind in ("cancel", "ready_ack", "ack"):
        root._command(message(kind, key=OTHER))
    assert controls == []
    root._command(message("cancel"))
    root._command(message("ready_ack"))
    root._command(message("ack", {"segment": "one"}))
    assert [item[0] for item in controls] == ["cancel", "ready_ack", "ack"]
    with pytest.raises(ValueError, match="Unsupported"):
        root._command(message("kpi"))
    root.child = None
    root._command(message("cancel"))


def test_private_progress_ready_and_payload_forwarding(monkeypatch):
    messages = [
        message("hello", key=None),
        message("progress", {"phase": "running", "event_count": 4}),
        message("ready", {"manifest": {}}),
        message("kpi", {"name": "jobs", "value": 2}),
        message("artifact_fragment", {"payload_bytes": 3}, payload=b"abc"),
    ]
    root, child, output, _ = actor(monkeypatch, messages)
    root._poll_child()
    assert child.hello
    assert child.progress.heartbeat()["event_count"] == 4
    assert [item[0] for item in output] == ["ready", "kpi", "artifact_fragment"]
    assert output[-1][2]["payload"] == b"abc"
    assert all(item[2]["key"] == KEY for item in output)


@pytest.mark.parametrize(
    "messages",
    [
        [message("kpi", key=OTHER)],
        [message("ack")],
        [message("model_info"), message("result")],
    ],
)
def test_wrong_fence_and_duplicate_terminal_fail(monkeypatch, messages):
    root, _, _, _ = actor(monkeypatch, messages)
    with pytest.raises(ValueError):
        root._poll_child()


def test_successful_proposal_with_failed_exit_cannot_publish(monkeypatch):
    root, _, output, controls = actor(monkeypatch, [message("result")], code=7)
    root._poll_child()
    assert output[-1][0] == "error"
    assert output[-1][1]["code"] == "child_crashed"
    assert controls == [("closed",)]


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), "1"])
def test_invalid_deadline_refused_before_child_launch(timeout):
    with pytest.raises(ValueError, match="timeout"):
        m.ChildProcess(message("inspect", {"timeout": timeout}))


def test_oversize_bootstrap_configuration_refused_before_launch():
    with pytest.raises(ValueError, match="configuration"):
        m.ChildProcess(message("inspect", {"config": {"reference": "é" * 50000}}))


def test_preparation_failure_is_fenced(monkeypatch):
    output = []
    root = m.Supervisor(SimpleNamespace(send=lambda kind, body, **kw: output.append((kind, body, kw))))
    root._command(message("inspect", {"timeout": 0}))
    assert output[0][1]["code"] == "preparation_failed"
    assert output[0][2]["key"] == KEY


@pytest.mark.parametrize("noise", [b"", b"x" * 65])
def test_child_private_channel_authentication_failure_cleans_process(monkeypatch, noise):
    import io
    import socket
    import threading

    closed = []
    peers = []
    threads = []

    def start(argv, *, env):
        def peer():
            connection = socket.create_connection(("127.0.0.1", int(argv[-1])), timeout=2)
            peers.append(connection)
            connection.sendall(noise)
            if not noise:
                connection.shutdown(socket.SHUT_WR)

        thread = threading.Thread(target=peer)
        thread.start()
        threads.append(thread)
        return SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO(), close=lambda: closed.append(True))

    monkeypatch.setattr(m.ContainedProcess, "start", start)
    try:
        with pytest.raises(ValueError, match="channel token|disconnected"):
            m.ChildProcess(message("inspect", {"config": {}}))
        assert closed == [True]
    finally:
        for thread in threads:
            thread.join(2)
        for peer in peers:
            peer.close()


def test_cancel_escalation_remains_available_when_private_channel_is_lost(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    process = object.__new__(m.ChildProcess)
    signals = []

    def broken(*args, **kw):
        raise OSError("channel lost")

    process.key = KEY
    process.cancel_at = None
    monkeypatch.setattr(process, "channel", SimpleNamespace(send=broken), raising=False)
    monkeypatch.setattr(
        process,
        "process",
        SimpleNamespace(terminate=lambda: signals.append("TERM"), kill=lambda: signals.append("KILL")),
        raising=False,
    )
    process.deadline = None
    process.started = 0.0
    process.hello = False
    process.terminated = process.killed = False
    clock[0] = 10
    process.escalate()
    assert process.timeout
    clock[0] = 12
    process.escalate()
    clock[0] = 13
    process.escalate()
    process.escalate()
    assert signals == ["TERM", "KILL"]


@pytest.mark.parametrize("broken_output", [False, True])
def test_supervisor_transport_failure_cleans_child_even_when_reporting_fails(monkeypatch, broken_output):
    import threading

    closed = []
    sent = []
    heartbeat_seen = threading.Event()

    def send(kind, body, **kw):
        sent.append(kind)
        if kind == "heartbeat":
            heartbeat_seen.set()
            if broken_output:
                raise BufferError("blocked")
        if broken_output and kind in ("error", "bye"):
            raise OSError("closed")

    def receive(timeout):
        assert heartbeat_seen.wait(2)
        raise OSError("peer EOF")

    def drain(timeout):
        if broken_output:
            raise OSError("closed")

    channel = SimpleNamespace(send=send, receive=receive, drain=drain, close=lambda: closed.append("channel"))
    root = m.Supervisor(channel)
    monkeypatch.setattr(
        root, "child", SimpleNamespace(key=KEY, progress=m.Progress(), close=lambda: closed.append("child"))
    )
    assert root.run_stdio() == 2
    assert closed == ["child", "channel"]
    assert "error" in sent


def test_diagnostic_pipe_close_is_tolerated():
    process = object.__new__(m.ChildProcess)
    process.diagnostics = m.Diagnostics()

    def broken(size):
        raise OSError("descriptor closed")

    process._pump("stdout", SimpleNamespace(read=broken))
    assert process.diagnostics.tail("stdout") == b""


def test_required_capabilities_are_checked_before_scheduling():
    root = m.Supervisor(SimpleNamespace(send=lambda *a, **k: None))
    root._command(message("hello", {"required_capabilities": ["fresh-process"]}, key=None))
    with pytest.raises(ValueError, match="capabilit"):
        root._command(message("hello", {"required_capabilities": ["ssh-worker"]}, key=None))
    with pytest.raises(ValueError, match="capabilit"):
        root._command(message("hello", {"required_capabilities": "wrong"}, key=None))
    assert root.child is None
    hello = m.worker_hello()
    assert hello["limits"]["frame_bytes"] == 2 << 20
    assert hello["limits"]["fragment_bytes"] == 1 << 20
    assert hello["runtime"]["python"]
    assert hello["runtime"]["simulatte_version"]


@pytest.mark.parametrize("control", ["ack", "ready_ack"])
@pytest.mark.parametrize("terminal,code", [("error", 2), ("result", 0)])
def test_late_control_after_private_eof_does_not_mask_terminal_proposal(monkeypatch, control, terminal, code):
    body = {"phase": "preparing", "message": "build refused"} if terminal == "error" else {"proposal": {}}
    root, child, output, controls = actor(monkeypatch, [message(terminal, body)], code=code)

    def closed(*args, **kwargs):
        raise OSError("Worker channel closed")

    child.channel.send = closed
    root._command(message(control))
    root._poll_child()
    assert output == [(terminal, body, {"key": KEY})]
    assert controls == [("closed",)]
