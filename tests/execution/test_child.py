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
        if failure is None:
            from simulatte.execution.references import write_document

            body = {"request_ref": write_document(tmp_path / "documents", "request", {})}
        else:
            body = {"request": {}}
        channel.send("run", body, key=key)
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


def test_inspection_metadata_larger_than_control_frame_uses_durable_fragments(tmp_path, monkeypatch):
    from dataclasses import asdict
    from simulatte.execution import schema
    from simulatte.execution.receipts import SegmentReceiver
    from simulatte.execution.store import RunStore
    from simulatte._wire import unpack

    metadata = {"selected": "build", "models": [{"description": "x" * (2 << 20)}]}
    monkeypatch.setattr(bootstrap, "inspect_configuration", lambda config: metadata)
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = FramedChannel(stream, stream)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(child.serve({"attempt_dir": str(tmp_path), "metadata_artifacts": True}, right))
    )
    thread.start()
    with RunStore.open(tmp_path / "store") as store:
        key = store.assign_inspection(
            schema.InspectionRequest(model="plant.py", source_hash="a" * 64, runtime={}), store.create_revision({})
        )
        store.transition(key, "preparing")
        receiver = SegmentReceiver(store, key)
        try:
            channel.send("hello", {})
            assert channel.receive(2).type == "hello"
            channel.send("inspect", {}, key=key)
            while True:
                message = channel.receive(3)
                if message.type != "artifact_fragment":
                    break
                receipt = receiver.receive(
                    _plain(message.body["segment"]), _plain(message.body["fragment_offset"]), message.payload
                )
                channel.send("ack", asdict(receipt), key=key)
            assert message.type == "model_info", message.body
            reference = _plain(message.body["metadata_ref"])
            assert _plain(unpack(receiver.segment_path(reference["id"]).read_bytes())) == metadata
            thread.join(3)
            assert results == [0]
            store.complete_inspection(key, reference, execution_exited=True, descendants_cleaned=True)
        finally:
            try:
                left.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            channel.close()
            stream.close()
            left.close()
            thread.join(3)


@pytest.mark.parametrize("mismatch", [None, "identity", "reference"])
def test_inspection_reference_is_bound_to_assignment_and_runtime(tmp_path, monkeypatch, mismatch):
    from simulatte.execution import runtime
    from simulatte.execution.schema import InspectionRequest, inspection_id
    from simulatte.execution.references import write_document

    request = InspectionRequest(model="plant.py:build", source_hash="a" * 64, runtime={})
    reference = write_document(tmp_path, "request", request.to_dict())
    checked = []
    monkeypatch.setattr(runtime, "verify_runtime", lambda config, value: checked.append(value))
    monkeypatch.setattr(bootstrap, "inspect_configuration", lambda config: {"selected": "build", "models": []})
    config = {"reference": "other.py" if mismatch == "reference" else request.model}
    left, right = socket.socketpair()
    stream = left.makefile("rwb", buffering=0)
    channel = FramedChannel(stream, stream)
    results = []
    thread = threading.Thread(target=lambda: results.append(child.serve(config, right)))
    thread.start()
    key = AttemptKey("b" * 64 if mismatch == "identity" else inspection_id(request), "c" * 32, 1)
    try:
        channel.send("hello", {})
        assert channel.receive(2).type == "hello"
        channel.send("inspect", {"inspection_ref": reference}, key=key)
        message = channel.receive(2)
        assert message.type == ("model_info" if mismatch is None else "error")
        assert len(checked) == (2 if mismatch is None else 0)
        thread.join(2)
        assert results == [0 if mismatch is None else 2]
    finally:
        try:
            left.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        channel.close()
        stream.close()
        left.close()
        thread.join(2)
