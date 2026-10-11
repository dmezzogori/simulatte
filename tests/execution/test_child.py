"""Private child dispatch and terminal messages, with data-only inspection fixtures."""

import socket
import threading

import pytest

from simulatte.execution import bootstrap, child
from simulatte.execution.control import Control
from simulatte.execution.schema import AttemptKey, _plain
from simulatte.execution.transport import FramedChannel


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "error",
        "cancel",
        "stale_cancel",
        "ready",
        "unsupported",
        "bad_control",
        "ack",
        "deadline",
        "bad_timeout",
        "bad_initial",
        "disconnect",
        "malformed_control",
        "idle",
    ],
)
def test_private_child_terminal_and_control_fences(monkeypatch, mode):
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = FramedChannel(stream, stream)
    entered, release = threading.Event(), threading.Event()
    control = Control()
    monkeypatch.setattr(child, "Control", lambda: control)

    def inspect(config):
        entered.set()
        if mode in ("cancel", "stale_cancel", "bad_control", "ack", "disconnect", "malformed_control"):
            assert release.wait(2)
        if mode in ("ready", "idle"):
            control.wait_ready()
        if mode == "error":
            raise ValueError("💥" * 20000)
        return {"selected": "build", "models": []}

    monkeypatch.setattr(bootstrap, "inspect_configuration", inspect)
    result = []
    thread = threading.Thread(target=lambda: result.append(child.serve({}, right)))
    thread.start()
    key = AttemptKey("a" * 64, "b" * 32, 1)
    try:
        channel.send("hello", {})
        assert channel.receive(2).type == "hello"
        kind = "run" if mode == "unsupported" else "cancel" if mode == "bad_initial" else "inspect"
        body = {"timeout": 1e-100} if mode == "deadline" else {"timeout": "bad"} if mode == "bad_timeout" else {}
        channel.send(kind, body, key=key)
        if mode in ("cancel", "stale_cancel", "ready", "bad_control", "ack", "disconnect", "malformed_control", "idle"):
            assert entered.wait(1)
            control_key = key if mode != "stale_cancel" else AttemptKey("c" * 64, "d" * 32, 2)
            if mode == "malformed_control":
                control_key = None
            if mode == "idle":
                assert not control.ready.wait(0.15)
            if mode == "disconnect":
                left.shutdown(socket.SHUT_RDWR)
                assert control.cancelled.wait(1)
                release.set()
                thread.join(2)
                assert result == [2]
                return
            command_kind = (
                "ready_ack"
                if mode in ("ready", "idle")
                else "kpi"
                if mode == "bad_control"
                else "ack"
                if mode == "ack"
                else "cancel"
            )
            channel.send(command_kind, {}, key=control_key)
            channel.drain(1)
            if mode in ("cancel", "bad_control", "malformed_control"):
                assert control.cancelled.wait(1)
            if mode in ("stale_cancel", "ack"):
                channel.send("ready_ack", {}, key=key)
                assert control.ready.wait(1)
                assert not control.cancelled.is_set()
            release.set()
        message = channel.receive(2)
        assert message.type == {
            "cancel": "cancelled",
            "error": "error",
            "unsupported": "error",
            "bad_control": "error",
            "deadline": "error",
            "bad_timeout": "error",
            "bad_initial": "error",
            "malformed_control": "error",
        }.get(mode, "model_info")
        if message.type == "error":
            traceback_text = message.body["traceback"]
            assert isinstance(traceback_text, str)
            assert len(traceback_text.encode("utf-8")) <= 65536
        assert message.envelope["attempt_id"] == key.attempt_id
        thread.join(2)
        assert result == [0 if message.type == "model_info" else 2]
    finally:
        release.set()
        control.cancel()
        thread.join(2)
        channel.close()
        stream.close()
        left.close()


@pytest.mark.parametrize("failure", [None, "cancel", "bad_ack", "disk_full"])
def test_private_run_transfers_metadata_with_ack_and_terminal_cleanup(tmp_path, monkeypatch, failure):
    from dataclasses import asdict
    import errno
    from simulatte.execution import driver
    from simulatte.execution.receipts import FragmentReceipt

    def execute(config, request, key, control, spool, send):
        control.phase = "finalizing"
        if failure == "disk_full":
            raise OSError(errno.ENOSPC, "full")
        return driver.metadata_segment(spool, "result-proposal", {"padding": "x" * ((1 << 20) + 9)})

    monkeypatch.setattr(driver, "execute", execute)
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = FramedChannel(stream, stream)
    results = []
    thread = threading.Thread(target=lambda: results.append(child.serve({"attempt_dir": str(tmp_path)}, right)))
    thread.start()
    key = AttemptKey("a" * 64, "b" * 32, 1)
    try:
        channel.send("hello", {})
        assert channel.receive(2).type == "hello"
        channel.send("run", {"request": {}}, key=key)
        transferred = 0
        while True:
            message = channel.receive(3)
            if message.type != "artifact_fragment":
                break
            transferred += len(message.payload)
            if failure == "cancel":
                channel.send("cancel", {}, key=key)
            elif failure == "bad_ack":
                channel.send("ack", {}, key=key)
            else:
                segment = _plain(message.body["segment"])
                offset = _plain(message.body["fragment_offset"])
                receipt = FragmentReceipt(
                    segment["id"],
                    offset,
                    len(message.payload),
                    segment["sha256"],
                    offset + len(message.payload) == segment["length"],
                )
                channel.send("ack", asdict(receipt), key=key)
        assert message.type == ("result" if failure is None else "cancelled" if failure == "cancel" else "error")
        if failure == "disk_full":
            assert message.body["code"] == "disk_full"
            assert message.body["phase"] == "finalizing"
        if failure is None:
            assert transferred > 1 << 20
            assert not list((tmp_path / "spool").glob("*.segment"))
        thread.join(3)
        assert results == [0 if failure is None else 2]
    finally:
        try:
            left.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        channel.close()
        stream.close()
        left.close()
        thread.join(3)
