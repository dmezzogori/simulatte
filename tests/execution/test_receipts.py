"""Durable fragment credit is distinct from segment visibility and spool ACK."""

import hashlib
import importlib
import importlib.util

import pytest

from simulatte.execution import schema
from simulatte.execution.protocol import MAX_FRAGMENT
from tests.execution.test_schema import request
from tests.execution.test_store import api as store_api


def api():
    assert importlib.util.find_spec("simulatte.execution.receipts"), "durable receiver missing"
    return importlib.import_module("simulatte.execution.receipts")


def descriptor(data, **changes):
    return {
        "id": "segment-1",
        "stream": "trace",
        "sequence": 1,
        "offset": 0,
        "length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    } | changes


def setup(tmp_path):
    store = store_api().RunStore.open(tmp_path / ".simulatte")
    key = store.assign(request(schema), store.create_revision({"source": "a"}))
    return store, key


def test_large_segment_has_durable_fragment_credit_but_no_partial_visibility(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        data = b"x" * (17 * MAX_FRAGMENT + 3)
        desc = descriptor(data)
        for offset in range(0, len(data), MAX_FRAGMENT):
            receipt = receiver.receive(desc, offset, data[offset : offset + MAX_FRAGMENT])
            assert receipt.offset == offset
            assert receipt.length == len(data[offset : offset + MAX_FRAGMENT])
            assert receipt.segment_complete == (offset + receipt.length == len(data))
            if not receipt.segment_complete:
                assert receiver.prefix() is None
                with store._transaction() as db:
                    assert db.execute("SELECT written FROM uploads").fetchone()[0] == offset + receipt.length
        prefix = receiver.prefix()
        assert prefix.length == len(data) and prefix.sha256 == desc["sha256"]
        assert prefix.path.read_bytes() == data
        assert receiver.segment_path(desc["id"]).read_bytes() == data
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1


def test_duplicate_fragments_require_identical_descriptor_offset_length_and_bytes(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        data = b"a" * MAX_FRAGMENT + b"b"
        desc = descriptor(data)
        first = receiver.receive(desc, 0, data[:MAX_FRAGMENT])
        assert not first.duplicate and not first.segment_complete
        assert receiver.receive(desc, 0, data[:MAX_FRAGMENT]).duplicate
        with pytest.raises(ValueError, match="duplicate"):
            receiver.receive(desc, 0, b"c" * MAX_FRAGMENT)
        receiver.receive(desc, MAX_FRAGMENT, b"b")
        assert receiver.receive(desc, MAX_FRAGMENT, b"b").duplicate
        assert receiver.receive(desc, 0, data[:MAX_FRAGMENT]).segment_complete
        with pytest.raises(ValueError, match="descriptor"):
            receiver.receive(desc | {"stream": "artifact/output.txt"}, 0, data[:MAX_FRAGMENT])
        assert receiver.prefix().length == len(data)


def test_gaps_short_fragments_and_wrong_final_hash_never_publish(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        data = b"a" * MAX_FRAGMENT + b"b"
        desc = descriptor(data)
        with pytest.raises(ValueError):
            receiver.receive(desc, MAX_FRAGMENT, b"b")
        with pytest.raises(ValueError):
            receiver.receive(desc, 0, b"short")
        receiver.receive(desc, 0, data[:MAX_FRAGMENT])
        with pytest.raises(ValueError, match="hash"):
            receiver.receive(desc, MAX_FRAGMENT, b"c")
        assert receiver.prefix() is None


def test_cancel_during_disk_write_cannot_ack_or_publish(tmp_path, monkeypatch):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        real_sync = m.os.fsync

        def sync_then_cancel(fd):
            real_sync(fd)
            store.revoke(key, "cancelled")

        monkeypatch.setattr(m.os, "fsync", sync_then_cancel)
        with pytest.raises(store_api().StaleAttempt):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0


def test_failed_fsync_cannot_ack(tmp_path, monkeypatch):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        monkeypatch.setattr(m.os, "fsync", lambda *a: (_ for _ in ()).throw(OSError("full")))
        with pytest.raises(OSError, match="full"):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        assert receiver.prefix() is None


def test_multiple_streams_keep_trace_prefix_offsets_and_digest(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        with pytest.raises(ValueError, match="already"):
            m.SegmentReceiver(store, key)
        receiver.receive(descriptor(b"abc"), 0, b"abc")
        old = receiver.prefix()
        receiver.receive(descriptor(b"out", id="artifact", sequence=2, stream="artifact/results/data.txt"), 0, b"out")
        assert receiver.prefix() == old
        receiver.receive(descriptor(b"def", id="segment-3", sequence=3, offset=3), 0, b"def")
        new = receiver.prefix()
        assert old.length == 3 and new.length == 6
        assert new.sha256 == hashlib.sha256(b"abcdef").hexdigest()
        assert new.path.read_bytes()[: old.length] == b"abc"
        with pytest.raises(KeyError):
            receiver.segment_path("missing")
        store.revoke(key, "cancelled")
        with pytest.raises(store_api().StaleAttempt):
            receiver.prefix()
        with pytest.raises(store_api().StaleAttempt):
            receiver.receive(descriptor(b"abc"), 0, b"abc")


@pytest.mark.parametrize(
    "changes",
    [
        {"length": 0},
        {"length": (128 << 20) + 1},
        {"offset": 2**53 - 1},
        {"sequence": 0},
        {"sequence": True},
        {"id": ""},
        {"id": "x" * 4097},
        {"sha256": "wrong"},
        {"stream": "../escape"},
        {"stream": "artifact/../escape"},
        {"stream": "artifact/CON.txt"},
        {"stream": "artifact/a."},
        {"stream": "other"},
        {"unknown": 1},
    ],
)
def test_invalid_descriptor_is_rejected_before_any_upload(tmp_path, changes):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        with pytest.raises(ValueError):
            receiver.receive(descriptor(b"abc", **changes), 0, b"abc")
        assert list(receiver.root.iterdir()) == [receiver.root / "segments"]


def test_replacement_attempt_fences_receiver_during_prefix_fsync(tmp_path, monkeypatch):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        original = m.os.fsync
        replaced = []

        def sync(fd):
            original(fd)
            if (receiver.root / "prefix.new").exists() and not replaced:
                replaced.append(store.assign(request(schema), store.current_revision()))

        monkeypatch.setattr(m.os, "fsync", sync)
        with pytest.raises(store_api().StaleAttempt):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        assert replaced
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
        new = m.SegmentReceiver(store, replaced[0])
        assert new.prefix() is None


@pytest.mark.parametrize("name", ["upload.new", "trace-prefix", "prefix.new"])
def test_receiver_rejects_symlink_targets(tmp_path, name):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        outside = tmp_path / "outside"
        outside.write_bytes(b"precious")
        (receiver.root / name).symlink_to(outside)
        with pytest.raises(ValueError, match="symlink"):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        assert outside.read_bytes() == b"precious"
        assert receiver.prefix() is None


def test_active_upload_rejects_other_descriptor_and_noncontiguous_fragment(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        data = b"a" * (2 * MAX_FRAGMENT) + b"b"
        desc = descriptor(data)
        receiver.receive(desc, 0, data[:MAX_FRAGMENT])
        with pytest.raises(ValueError, match="descriptor"):
            receiver.receive(desc | {"id": "other"}, MAX_FRAGMENT, data[:MAX_FRAGMENT])
        with pytest.raises(ValueError, match="gap"):
            receiver.receive(desc, 2 * MAX_FRAGMENT, b"b")
        assert receiver.prefix() is None


def test_existing_unindexed_segment_is_not_overwritten(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        destination = receiver._path("segment-1")
        destination.write_bytes(b"precious")
        with pytest.raises(ValueError, match="already exists"):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        assert destination.read_bytes() == b"precious"
        assert receiver.prefix() is None


@pytest.mark.parametrize("name", ["upload.new", "prefix.new"])
def test_short_disk_write_cannot_ack_fragment_or_segment(tmp_path, monkeypatch, name):
    from pathlib import Path

    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        original = Path.open

        def open_file(path, *args, **kwargs):
            file = original(path, *args, **kwargs)
            if path.name == name:
                monkeypatch.setattr(file, "write", lambda data: 0)
            return file

        monkeypatch.setattr(Path, "open", open_file)
        with pytest.raises(OSError, match="Incomplete"):
            receiver.receive(descriptor(b"abc"), 0, b"abc")
        assert receiver.prefix() is None


def test_finalizing_attempt_no_longer_accepts_segments(tmp_path):
    m = api()
    store, key = setup(tmp_path)
    with store:
        receiver = m.SegmentReceiver(store, key)
        receiver.receive(descriptor(b"abc"), 0, b"abc")
        prefix = receiver.prefix()
        for phase in ("preparing", "ready", "running", "finalizing"):
            store.transition(key, phase)
        with pytest.raises(ValueError, match="finalizing"):
            receiver.receive(descriptor(b"def", id="second", sequence=2, offset=3), 0, b"def")
        assert receiver.prefix() == prefix
