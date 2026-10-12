"""Coordinator ownership and terminal state under broken worker peers and storage."""

from types import SimpleNamespace
import time

import pytest

from simulatte.execution import coordinator as m, schema
from simulatte.execution.snapshot import ProjectSpec
from tests.execution.test_schema import request


class Peer:
    def __init__(self, failure):
        self.failure = failure
        self.key = None
        self.closed = False
        self.hello = True
        self.channel = SimpleNamespace(last_heartbeat=time.monotonic() - 10)

    def send(self, kind, body, *, key):
        if self.failure == "send":
            raise BufferError("full")
        self.key = key

    def receive(self, timeout):
        if self.hello:
            self.hello = False
            capabilities = [] if self.failure == "hello" else ["metadata-artifacts"]
            return SimpleNamespace(type="hello", body={"protocol": [1, 0], "capabilities": capabilities})
        if self.failure == "eof":
            raise OSError("closed")
        if self.failure == "heartbeat":
            raise TimeoutError()
        assert self.key is not None
        envelope = {
            "request_id": self.key.request_id,
            "attempt_id": self.key.attempt_id,
            "generation": self.key.generation,
        }
        body = {}
        kind = self.failure
        if kind == "stale":
            envelope["generation"] += 1
        if kind.startswith("error-"):
            phase = kind.removeprefix("error-")
            kind, body = "error", {"phase": phase, "message": "fixture failure"}
            if phase in ("disk_full", "timeout"):
                body["code"] = phase
        return SimpleNamespace(type=kind, body=body, envelope=envelope, payload=b"")

    def close(self):
        self.closed = True


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    project = ProjectSpec(tmp_path, "plant.py", "layout.json", (), (), (".",), False)
    monkeypatch.setattr(m, "materialize", lambda snapshot, path: SimpleNamespace(root=path, python_path=[path]))
    with m.Coordinator(project) as owner:
        yield owner


@pytest.mark.parametrize(
    "failure,status",
    [
        ("hello", "failed"),
        ("send", "lost"),
        ("eof", "lost"),
        ("heartbeat", "lost"),
        ("stale", "failed"),
        ("model_info", "failed"),
        ("ready", "failed"),
        ("result", "failed"),
        ("unknown", "failed"),
        ("error-preparing", "validation_failed"),
        ("error-running", "failed"),
        ("error-disk_full", "disk_full"),
        ("error-timeout", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_broken_peer_never_publishes_or_leaves_attempt_active(coordinator, monkeypatch, failure, status):
    peer = Peer(failure)
    monkeypatch.setattr(m.LocalTransport, "start", lambda *a, **kw: peer)
    revision = coordinator.store.create_revision({})
    key = coordinator._schedule(request(schema), revision, None, {}, 2)
    with pytest.raises(m.ExecutionFailure) as error:
        coordinator.wait(key, timeout=3)
    assert error.value.status == status
    with pytest.raises(KeyError):
        coordinator.store.result(key.attempt_id)


def test_events_are_bounded_and_require_resync_after_overflow(coordinator):
    for i in range(257):
        coordinator._emit("progress", None, {"i": i})
    assert coordinator.events() == [{"type": "resync", "key": None, "body": {}}]
    assert coordinator.events() == []
    coordinator._emit("diagnostics", None, {"text": "x" * (4 << 20)})
    assert coordinator.events() == [{"type": "resync", "key": None, "body": {}}]


@pytest.mark.parametrize("timeout", [True, -1, 0, float("nan"), float("inf")])
def test_invalid_deadline_does_not_assign_attempt(coordinator, timeout):
    with pytest.raises(ValueError, match="Timeout"):
        coordinator._schedule(request(schema), coordinator.store.create_revision({}), None, {}, timeout)


def test_closed_coordinator_cannot_prepare_or_queue(coordinator):
    revision = coordinator.store.create_revision({})
    coordinator.close()
    with pytest.raises(RuntimeError, match="closed"):
        coordinator.prepare()
    with pytest.raises(RuntimeError, match="closed"):
        coordinator._schedule(request(schema), revision, None, {}, None)


def test_metadata_reference_must_match_durable_bytes_and_receipt(coordinator):
    from simulatte.execution.receipts import SegmentReceiver
    from simulatte._wire import canonical_pack, freeze
    from tests.execution.test_receipts import descriptor

    key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))
    receiver = SegmentReceiver(coordinator.store, key)
    data = canonical_pack(freeze({"models": []}))
    reference = descriptor(data, stream="metadata/model-info")
    receiver.receive(reference, 0, data)
    assert coordinator._metadata(receiver, reference, "model-info") == {"models": []}
    with pytest.raises(ValueError, match="stream"):
        coordinator._metadata(receiver, reference, "resolved-manifest")
    with pytest.raises(ValueError, match="descriptor"):
        coordinator._metadata(receiver, reference | {"sha256": "a" * 64}, "model-info")
    path = receiver.segment_path(reference["id"])
    path.write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="durable receipt"):
        coordinator._metadata(receiver, reference, "model-info")


def test_wait_timeout_does_not_revoke_queued_attempt(coordinator):
    key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))
    with pytest.raises(TimeoutError, match="Waiting"):
        coordinator.wait(key, timeout=0.01)
    assert coordinator.store.attempt(key)["status"] == "queued"
    assert coordinator.cancel(key)


def test_missing_cached_request_is_not_silently_recaptured(coordinator):
    with pytest.raises(ValueError, match="unavailable"):
        coordinator.submit(request(schema), coordinator.store.create_revision({}))
    with pytest.raises(ValueError, match="baseline"):
        key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))
        coordinator.reproduce(key.request_id)


@pytest.mark.parametrize(
    "change", ["shape", "fields", "name", "duplicate", "inputs", "input_type", "stop", "selected", "empty"]
)
def test_malformed_inspection_metadata_is_not_visible(change):
    item = {"name": "build", "parameters": {"version": 1, "fields": []}, "inputs": [], "stopping": {}}
    data = {"models": [item], "selected": "build"}
    if change == "shape":
        data = {"models": {}}
    elif change == "fields":
        del item["parameters"]
    elif change == "name":
        item["name"] = "bad-name"
    elif change == "duplicate":
        data["models"].append(item)
    elif change == "inputs":
        item["inputs"] = [1]
    elif change == "input_type":
        item["inputs"] = "file"
    elif change == "stop":
        item["stopping"] = None
    elif change == "selected":
        data["selected"] = "unknown"
    else:
        data["models"] = []
    with pytest.raises(ValueError):
        m.selected_model(data)


def test_runtime_capture_failure_releases_store_owner(tmp_path, monkeypatch):
    project = ProjectSpec(tmp_path, "plant.py", "layout.json", (), (), (".",), False)

    def fail(*args):
        raise OSError("runtime capture failed")

    monkeypatch.setattr(m, "freeze_runtime", fail)
    with pytest.raises(OSError):
        m.Coordinator(project)
    with m.RunStore.open(tmp_path / ".simulatte"):
        pass


@pytest.mark.parametrize("change", ["request", "runtime"])
def test_cached_preparation_cannot_change_request_or_runtime(coordinator, change):
    from simulatte.execution.store import _blob

    requested = request(schema, runtime={"trusted_runtime_hash": "a" * 64})
    cached = requested.to_dict()
    if change == "request":
        cached["seed"] = "1"
    with coordinator.store._transaction() as db:
        db.execute(
            "INSERT INTO snapshots VALUES (?,?,?)",
            (schema.request_id(requested), "preparation", _blob({"request": cached})),
        )
    with pytest.raises(ValueError, match="mismatch|runtime differs"):
        coordinator._preparation(requested)


def test_cancel_with_unwritable_index_reports_undurable_failure(coordinator, monkeypatch):
    import sqlite3

    key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(coordinator.store, "revoke", fail)
    assert coordinator.cancel(key)
    with pytest.raises(m.ExecutionFailure) as error:
        coordinator.wait(key)
    assert error.value.status == "disk_full"
    assert error.value.details["index_terminal_state"] == "not_durable"
    assert coordinator.store.attempt(key)["status"] == "queued"


@pytest.mark.parametrize("failure", ["heartbeat", "eof"])
def test_idle_dead_worker_is_closed_before_next_assignment(coordinator, failure):
    peer = Peer(failure)
    peer.hello = False
    coordinator._worker = peer
    deadline = time.monotonic() + 2
    while not peer.closed:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert coordinator._worker is None


def test_reopening_missing_preparation_does_not_reinspect(tmp_path):
    with m.RunStore.open(tmp_path / ".simulatte") as store:
        key = store.assign(request(schema), store.create_revision({}))
    with pytest.raises(ValueError, match="preparation"):
        m.Coordinator.from_store(tmp_path, key.request_id)


def test_stale_failure_cannot_revoke_new_assignment(coordinator):
    revision = coordinator.store.create_revision({})
    old = coordinator.store.assign(request(schema), revision)
    current = coordinator.store.assign(request(schema), revision)
    coordinator._record_failure(old, "failed", {"message": "late"})
    assert coordinator.store.attempt(current)["status"] == "queued"
    assert coordinator.store.attempt(old)["status"] == "superseded"


def test_cancel_does_not_wait_for_a_full_control_queue(coordinator):
    key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))
    peer = Peer("send")
    with coordinator._condition:
        coordinator._current = SimpleNamespace(key=key)
        coordinator._worker = peer
        assert coordinator.cancel(key)
        coordinator._current = None
        coordinator._worker = None
    assert coordinator.store.attempt(key)["status"] == "cancelled"


def test_revocation_at_dispatch_closes_worker_before_next_slot(coordinator, monkeypatch):
    peer = Peer("unused")

    def start():
        coordinator._worker = peer
        coordinator.store.revoke(coordinator._current.key, "cancelled")
        return peer

    monkeypatch.setattr(coordinator._actors["interactive"], "_start_worker", start)
    key = coordinator._schedule(request(schema), coordinator.store.create_revision({}), None, {}, 2)
    with pytest.raises(m.ExecutionFailure):
        coordinator.wait(key, timeout=3)
    deadline = time.monotonic() + 2
    while not peer.closed:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_cancel_pending_assignment_removes_it_before_dispatch(coordinator):
    with coordinator._condition:
        key = coordinator.store.assign(request(schema), coordinator.store.create_revision({}))
        coordinator._pending = SimpleNamespace(key=key)
        assert coordinator.cancel(key)
        assert coordinator._pending is None
    assert coordinator.store.attempt(key)["status"] == "cancelled"


def test_cleanup_failure_is_reported_without_losing_attempt_failure(coordinator, monkeypatch):
    peer = Peer("error-running")
    monkeypatch.setattr(m.LocalTransport, "start", lambda *a, **kw: peer)

    def materialize(snapshot, path):
        path.mkdir()
        return SimpleNamespace(root=path, python_path=[path])

    def fail(path):
        raise PermissionError("cleanup blocked")

    monkeypatch.setattr(m, "materialize", materialize)
    monkeypatch.setattr(m, "_remove", fail)
    key = coordinator._schedule(request(schema), coordinator.store.create_revision({}), None, {}, 2)
    with pytest.raises(m.ExecutionFailure, match="fixture failure"):
        coordinator.wait(key, timeout=3)
    events = []
    deadline = time.monotonic() + 2
    while not any(event["type"] == "cleanup_error" for event in events):
        assert time.monotonic() < deadline
        events.extend(coordinator.events())
        time.sleep(0.01)
    assert coordinator.store.attempt(key)["status"] == "failed"


def test_shutdown_timeout_retains_owner_until_retry_after_actor_exit(coordinator):
    real_thread = coordinator._thread
    coordinator._thread = SimpleNamespace(join=lambda timeout: real_thread.join(timeout), is_alive=lambda: True)
    try:
        with pytest.raises(RuntimeError, match="shutdown"):
            coordinator.close()
    finally:
        coordinator._thread = real_thread
    coordinator.close()
    with m.RunStore.open(coordinator.store.root):
        pass
