"""Protocol 1.1 staging: coordinator ``stage_blob`` against a real supervisor with a remote cache."""

import hashlib
import os
import sys

import pytest

from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.schema import AttemptKey
from simulatte.execution.staging import WINDOW, RemoteStepError, stage_blob
from tests.execution._worker_harness import WorkerHarness

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows is never a remote host")

KEY = AttemptKey("a" * 64, "b" * 32, 1)


class Recording:
    """The harness as a coordinator transport, recording fragments and the worker's acks."""

    def __init__(self, harness):
        self.harness, self.channel = harness, harness.channel
        self.fragments: list[int] = []
        self.acked = 0
        self.outstanding: list[int] = []

    def send(self, kind, body, **kwargs):
        self.harness.send(kind, body, **kwargs)
        if kind == "stage_fragment":
            self.fragments.append(len(kwargs["payload"]))
            self.outstanding.append(body["fragment_offset"] + len(kwargs["payload"]) - self.acked)

    def receive(self, timeout=5):
        message = self.harness.receive(timeout)
        if message.type == "ack":
            self.acked = message.body["received"]
        return message


@pytest.fixture
def worker(tmp_path):
    harness = WorkerHarness(trusted_runtime_hash="c" * 64, cache=str(tmp_path / "cache"))
    hello = harness.receive()
    assert {"staging-v1", "uv-provisioning-v1"} <= set(hello.body["capabilities"])
    yield harness
    harness.close()


def reply(harness):
    """The next message that is not a heartbeat."""
    while (message := harness.receive()).type == "heartbeat":
        pass
    return message


def blob(tmp_path, size, name="blob"):
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_fragments_stay_under_the_window_and_complete_blobs_are_published(worker, tmp_path):
    path, sha = blob(tmp_path, 20 * MAX_FRAGMENT + 123)
    transport = Recording(worker)
    assert stage_blob(transport, "source", path, sha, KEY) is False
    assert max(transport.fragments) == MAX_FRAGMENT and sum(transport.fragments) == path.stat().st_size
    assert len(transport.fragments) == 21 and max(transport.outstanding) <= WINDOW
    staged = tmp_path / "cache" / "blobs" / sha
    assert staged.read_bytes() == path.read_bytes() and not staged.stat().st_mode & 0o222
    assert [item.name for item in staged.parent.iterdir()] == [sha]
    # The second stage of a held blob ships nothing.
    again = Recording(worker)
    assert stage_blob(again, "source", path, sha, KEY) is True
    assert again.fragments == []


def test_hash_mismatch_is_stage_mismatch_and_leaves_nothing(worker, tmp_path):
    path, _ = blob(tmp_path, MAX_FRAGMENT + 1)
    with pytest.raises(RemoteStepError) as caught:
        stage_blob(worker, "document", path, "0" * 64, KEY)
    assert caught.value.code == "stage_mismatch" and caught.value.details["sha256"] == "0" * 64
    assert list((tmp_path / "cache" / "blobs").iterdir()) == []
    # The supervisor stays usable for the next blob.
    path, sha = blob(tmp_path, 10, "small")
    assert stage_blob(worker, "inputs", path, sha, KEY) is False


def test_a_truncated_cached_entry_is_staged_again(worker, tmp_path):
    path, sha = blob(tmp_path, 100)
    (tmp_path / "cache" / "blobs" / sha).write_bytes(b"short")
    assert stage_blob(worker, "inputs", path, sha, KEY) is False
    assert (tmp_path / "cache" / "blobs" / sha).read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "body,code",
    [
        ({"kind": "runtime", "sha256": "1" * 64, "length": 1}, "staging_failed"),
        ({"kind": "source", "sha256": "1" * 64, "length": 0}, "staging_failed"),
        ({"kind": "source", "sha256": "not-a-hash", "length": 1}, "staging_failed"),
    ],
)
def test_invalid_stage_requests_are_refused(worker, body, code):
    worker.send("stage", body, key=KEY)
    message = reply(worker)
    assert message.type == "error" and message.body["code"] == code


def test_stray_fragments_are_ignored_and_a_second_stage_is_busy(worker, tmp_path):
    worker.send("stage_fragment", {"sha256": "1" * 64, "fragment_offset": 0, "payload_bytes": 1}, key=KEY, payload=b"x")
    worker.send("stage", {"kind": "source", "sha256": "2" * 64, "length": 10}, key=KEY)
    message = reply(worker)
    assert message.type == "ack" and message.body["received"] == 0
    worker.send("stage", {"kind": "source", "sha256": "3" * 64, "length": 10}, key=KEY)
    message = reply(worker)
    assert message.type == "error" and message.body["code"] == "worker_busy"
    # An out-of-order fragment fails the upload and removes its partial file.
    payload = {"sha256": "2" * 64, "fragment_offset": 5, "payload_bytes": 1}
    worker.send("stage_fragment", payload, key=KEY, payload=b"x")
    message = reply(worker)
    assert message.type == "error" and message.body["code"] == "staging_failed"
    assert list((tmp_path / "cache" / "blobs").iterdir()) == []


def test_a_supervisor_without_cache_refuses_staging_and_provisioning():
    harness = WorkerHarness()
    try:
        hello = harness.receive()
        assert "staging-v1" not in hello.body["capabilities"]
        harness.send("stage", {"kind": "source", "sha256": "1" * 64, "length": 1}, key=KEY)
        harness.send("provision", {"spec": {}, "source_sha256": "1" * 64, "timeout": 1}, key=KEY)
        assert [reply(harness).body["code"] for _ in range(2)] == ["staging_unavailable", "staging_unavailable"]
    finally:
        harness.close()


def test_stage_blob_reports_heartbeats_and_maps_transport_loss(tmp_path):
    from types import SimpleNamespace

    from simulatte._wire import freeze
    from simulatte.execution.protocol import Message

    def message(kind, body, key=KEY):
        identity = (key.request_id, key.attempt_id, key.generation) if key else (None, None, None)
        envelope = dict(zip(("request_id", "attempt_id", "generation"), identity))
        frozen = freeze({**envelope, "type": kind, "body": body, "v": [1, 1], "seq": 0})
        return Message(frozen)  # ty: ignore[invalid-argument-type]

    class Scripted:
        def __init__(self, *items):
            self.items = list(items)
            self.channel = SimpleNamespace(last_heartbeat=0.0)

        def send(self, *args, **kwargs):
            if self.items and self.items[0] == "send-fails":
                raise OSError("pipe closed")

        def receive(self, timeout):
            item = self.items.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    path, sha = blob(tmp_path, 3)
    seen = []
    other = AttemptKey("d" * 64, "e" * 32, 2)
    staged = message("staged", {"sha256": sha, "cached": True})
    scripted = Scripted(message("heartbeat", {"phase": "idle"}, None), message("heartbeat", {}, other), staged)
    assert stage_blob(scripted, "inputs", path, sha, KEY, notify=lambda *item: seen.append(item)) is True
    scripted = Scripted(message("heartbeat", {"phase": "staging"}), message("diagnostics", {"stderr": ""}), staged)
    assert stage_blob(scripted, "inputs", path, sha, KEY, notify=lambda *item: seen.append(item)) is True
    assert seen == [("heartbeat", {"phase": "staging"}), ("diagnostics", {"stderr": ""})]
    for items, error in (
        ([TimeoutError("quiet")], ConnectionError),
        ([OSError("closed")], ConnectionError),
        (["send-fails"], ConnectionError),
        ([message("staged", {"sha256": sha, "cached": True}, other)], ValueError),
        ([message("staged", {"sha256": sha, "cached": False})], ValueError),
        ([message("ack", {"sha256": sha, "received": 1})], ValueError),
        ([message("ack", {"sha256": sha, "received": 0}), message("ack", {"sha256": sha, "received": 9})], ValueError),
        (
            [
                message("ack", {"sha256": sha, "received": 0}),
                message("ack", {"sha256": sha, "received": 3}),
                message("staged", {"sha256": sha, "cached": True}),
            ],
            ValueError,
        ),
    ):
        with pytest.raises(error):
            stage_blob(Scripted(*items), "inputs", path, sha, KEY)
    shrinking = Scripted(message("ack", {"sha256": sha, "received": 0}))
    shrinking.send = lambda *args, **kwargs: path.write_bytes(b"")  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="changed"):
        stage_blob(shrinking, "inputs", path, sha, KEY)


def test_provisioning_answers_are_bounded_and_checked(monkeypatch):
    from types import SimpleNamespace

    from simulatte._wire import freeze
    from simulatte.execution import staging
    from simulatte.execution.protocol import Message

    envelope = {"request_id": KEY.request_id, "attempt_id": KEY.attempt_id, "generation": KEY.generation}
    frozen = freeze({**envelope, "type": "staged", "body": {}, "v": [1, 1], "seq": 0})
    staged = Message(frozen)  # ty: ignore[invalid-argument-type]
    transport = SimpleNamespace(
        send=lambda *args, **kwargs: None, receive=lambda timeout: staged, channel=SimpleNamespace(last_heartbeat=0)
    )
    with pytest.raises(ValueError, match="provisioning answer"):
        staging.provision(transport, {"kind": "uv"}, "1" * 64, KEY, timeout=5)
    monkeypatch.setattr(staging, "ANSWER_GRACE", -10)
    with pytest.raises(RemoteStepError) as caught:
        staging.provision(transport, {"kind": "uv"}, "1" * 64, KEY, timeout=5)
    assert caught.value.code == "provisioning_failed" and caught.value.details["reason"] == "timeout"
