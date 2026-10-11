"""Bounded framing transport keeps diagnostics out of its private channels."""

from __future__ import annotations

import importlib
import importlib.util
import socket

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.transport"), "transport missing"
    return importlib.import_module("simulatte.execution.transport")


def test_bidirectional_channel_frames_and_shutdown():
    m = api()
    left, right = socket.socketpair()
    a_io, b_io = left.makefile("rwb", buffering=0), right.makefile("rwb", buffering=0)
    a, b = m.FramedChannel(a_io, a_io), m.FramedChannel(b_io, b_io)
    try:
        a.send("hello", {"side": "a"})
        b.send("hello", {"side": "b"})
        assert a.receive(1).body["side"] == "b"
        assert b.receive(1).body["side"] == "a"
        a.send("artifact_fragment", {"payload_bytes": 3}, payload=b"abc")
        assert b.receive(1).payload == b"abc"
        b.send("cancel", {})
        assert a.receive(1).type == "cancel"
        with pytest.raises(TimeoutError):
            a.receive(0.01)
    finally:
        left.shutdown(socket.SHUT_RDWR)
        a.close()
        b.close()
        a_io.close()
        b_io.close()
        left.close()
        right.close()


def test_outbound_queue_is_bounded_without_a_reader():
    m = api()
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = m.FramedChannel(stream, stream)
    try:
        channel.send("hello", {})
        with pytest.raises(BufferError):
            for _ in range(64):
                channel.send("artifact_fragment", {"payload_bytes": 1 << 20}, payload=b"x" * (1 << 20))
        assert channel.pending_bytes <= (20 << 20)
        # The data budget cannot consume the reserved control slots.
        channel.send("cancel", {})
    finally:
        left.shutdown(socket.SHUT_RDWR)
        channel.close()
        stream.close()
        left.close()
        right.close()


def test_inbound_flood_fails_with_bounded_storage():
    import time
    from simulatte.execution.protocol import MAGIC, encode_message
    from simulatte.execution.transport import envelope

    m = api()
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = m.FramedChannel(stream, stream)
    try:
        raw = MAGIC + b"".join(
            encode_message(envelope("hello" if i == 0 else "heartbeat", {}, i, None)) for i in range(140)
        )
        right.sendall(raw)
        deadline = time.monotonic() + 1
        while channel._error is None and time.monotonic() < deadline:
            time.sleep(0.001)
        assert isinstance(channel._error, BufferError)
        assert len(channel._incoming) <= 128
        for _ in range(len(channel._incoming)):
            channel.receive(0.01)
        with pytest.raises(OSError):
            channel.receive(0.01)
    finally:
        left.shutdown(socket.SHUT_RDWR)
        channel.close()
        stream.close()
        left.close()
        right.close()


def test_control_queue_and_drain_deadline_stay_bounded():
    m = api()
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = m.FramedChannel(stream, stream)
    try:
        channel.send("hello", {})
        with pytest.raises(BufferError):
            for _ in range(64):
                channel.send("artifact_fragment", {"payload_bytes": 1 << 20}, payload=b"x" * (1 << 20))
        with pytest.raises(BufferError):
            for _ in range(80):
                channel.send("heartbeat", {})
        with pytest.raises(TimeoutError):
            channel.drain(0.01)
    finally:
        left.shutdown(socket.SHUT_RDWR)
        channel.close()
        stream.close()
        left.close()
        right.close()
    with pytest.raises(OSError):
        channel.send("cancel", {})
    with pytest.raises(OSError):
        channel.drain(0.01)


def test_writer_refuses_zero_progress():
    from types import SimpleNamespace

    m = api()
    channel = object.__new__(m.FramedChannel)
    channel.writer = SimpleNamespace(write=lambda _: 0)
    with pytest.raises(OSError, match="progress"):
        channel._write_all(b"frame")


def test_local_heartbeat_deadline_caps_receive_wait(monkeypatch):
    from types import SimpleNamespace

    m = api()
    clock = [4.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    transport = object.__new__(m.LocalTransport)
    transport.channel = SimpleNamespace(handshake_complete=True, last_heartbeat=0, receive=lambda timeout: timeout)
    assert transport.receive(20) == 1
    clock[0] = 5
    with pytest.raises(TimeoutError, match="heartbeat"):
        transport.receive(20)


def test_failed_worker_start_preserves_bounded_stderr(tmp_path):
    m = api()
    transport = m.LocalTransport.start(tmp_path)
    try:
        with pytest.raises(OSError):
            transport.receive(2)
    finally:
        transport.close()
    transport.close()
    assert b"ModuleNotFoundError" in transport.diagnostics.tail("stderr")
