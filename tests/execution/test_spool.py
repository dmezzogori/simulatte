"""Reservations include temporary bytes; only a verified durable ACK reclaims them."""

import importlib
import importlib.util
import threading
import time

import pytest

from simulatte.execution.control import Cancelled, Control


def api():
    assert importlib.util.find_spec("simulatte.execution.spool"), "bounded spool missing"
    return importlib.import_module("simulatte.execution.spool")


def sink_for(spool):
    from simulatte.trace.sink import SegmentSink

    return SegmentSink(spool, stream="trace")


def blocked(spool):
    deadline = time.monotonic() + 2
    while not spool.stats()["backpressure"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert spool.stats()["backpressure"]


def test_commits_are_immutable_and_duplicate_ack_does_not_need_history(tmp_path):
    m = api()
    spool = m.Spool(tmp_path / "spool")
    sink = sink_for(spool)
    sink.write(b"abc")
    first = sink.commit()
    assert first.offset == 0 and first.length == 3
    assert first.path.read_bytes() == b"abc"
    assert spool.ready() == (first,)
    assert spool.stats()["bytes"] == 3
    assert spool.ack(first.id, first.sha256)
    for i in range(1100):
        sink.write(b"x")
        segment = sink.commit()
        assert segment.offset == 3 + i
        spool.ack(segment.id, segment.sha256)
    assert not spool.ack(first.id, first.sha256)
    with pytest.raises(ValueError, match="ACK"):
        spool.ack(first.id, "0" * 64)
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0
    assert list(spool.root.iterdir()) == []
    sink.close()


def test_pending_reservation_counts_and_ack_unblocks_writer(tmp_path):
    m = api()
    spool = m.Spool(tmp_path / "spool", max_bytes=12, max_files=2, max_segment_bytes=8)
    sink = sink_for(spool)
    sink.write(b"a" * 8)
    first = sink.commit()
    result = []
    thread = threading.Thread(target=lambda: (sink.write(b"b" * 8), result.append(sink.commit())))
    thread.start()
    blocked(spool)
    assert spool.stats()["files"] == 2
    assert spool.stats()["bytes"] == 8
    spool.ack(first.id, first.sha256)
    thread.join(2)
    assert not thread.is_alive()
    assert result[0].path.read_bytes() == b"b" * 8
    assert spool.stats()["peak_bytes"] <= 12
    assert spool.stats()["peak_files"] <= 2
    spool.ack(result[0].id, result[0].sha256)
    sink.close()


@pytest.mark.parametrize("cause", ["cancel", "no_ack"])
def test_blocked_spool_can_cancel_or_expire_without_more_credit(tmp_path, cause):
    m = api()
    clock = [0.0]
    control = Control()
    spool = m.Spool(
        tmp_path / "spool", max_bytes=4, max_files=1, max_segment_bytes=4, control=control, clock=lambda: clock[0]
    )
    sink = sink_for(spool)
    sink.write(b"full")
    first = sink.commit()
    errors = []

    def write():
        try:
            sink.write(b"next")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=write)
    thread.start()
    blocked(spool)
    if cause == "cancel":
        control.cancel()
    else:
        clock[0] = 61
    thread.join(2)
    assert not thread.is_alive()
    assert isinstance(errors[0], Cancelled if cause == "cancel" else m.SpoolExhausted)
    assert spool.stats()["bytes"] == 4
    spool.ack(first.id, first.sha256)
    sink.close()


def test_oversize_and_out_of_order_ack_fail_without_reclaim(tmp_path):
    m = api()
    spool = m.Spool(tmp_path / "spool", max_bytes=16, max_segment_bytes=8)
    sink = sink_for(spool)
    with pytest.raises(ValueError, match="segment"):
        sink.write(b"x" * 9)
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0
    sink.write(b"one")
    first = sink.commit()
    sink.write(b"two")
    second = sink.commit()
    with pytest.raises(ValueError, match="ACK"):
        spool.ack(second.id, second.sha256)
    assert spool.stats()["bytes"] == 6
    spool.ack(first.id, first.sha256)
    spool.ack(second.id, second.sha256)
    with pytest.raises(ValueError, match="ACK"):
        spool.ack("unknown", "0" * 64)
    sink.close()


def test_preallocated_reservation_cannot_grow_past_segment_limit(tmp_path):
    m = api()
    spool = m.Spool(tmp_path / "spool", max_segment_bytes=3)
    reservation = spool.reserve(3)
    reservation.write(b"a")
    with pytest.raises(ValueError, match="segment"):
        reservation.write(b"bcd")
    reservation.abort()
    assert spool.stats()["bytes"] == 0


@pytest.mark.parametrize("failure", ["write", "fsync", "rename"])
def test_failed_publication_leaves_no_visible_or_unaccounted_segment(tmp_path, monkeypatch, failure):
    import errno

    m = api()
    spool = m.Spool(tmp_path / "spool")
    sink = sink_for(spool)

    def fail(*a, **kw):
        raise OSError(errno.ENOSPC, "full")

    if failure == "write":
        reservation = spool.reserve()
        monkeypatch.setattr(reservation.file, "write", fail)
        monkeypatch.setattr(spool, "reserve", lambda: reservation)
        with pytest.raises(OSError):
            sink.write(b"abc")
    else:
        sink.write(b"abc")
        monkeypatch.setattr(m.os, "fsync", fail) if failure == "fsync" else monkeypatch.setattr(
            m, "replace_durable", fail
        )
        with pytest.raises(OSError):
            sink.commit()
    assert spool.ready() == ()
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0
    assert list(spool.root.iterdir()) == []
    sink.close()


def test_commit_validation_releases_sink_reservation(tmp_path):
    m = api()
    from simulatte.trace.sink import SegmentSink

    spool = m.Spool(tmp_path / "spool")
    sink = SegmentSink(spool, stream="")
    sink.write(b"abc")
    with pytest.raises(ValueError, match="stream"):
        sink.commit()
    sink.close()
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_failed_fdopen_closes_descriptor_and_removes_temporary_file(tmp_path, monkeypatch):
    import os

    m = api()
    spool = m.Spool(tmp_path / "spool")
    descriptors = []

    def fail(descriptor, mode):
        descriptors.append(descriptor)
        raise OSError("fdopen failed")

    monkeypatch.setattr(m.os, "fdopen", fail)
    with pytest.raises(OSError, match="fdopen"):
        spool.reserve(3)
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(spool.root.iterdir()) == []
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_directory_sync_failure_removes_renamed_file(tmp_path, monkeypatch):
    m = api()
    spool = m.Spool(tmp_path / "spool")
    sink = sink_for(spool)
    sink.write(b"abc")

    def renamed_then_fail(source, destination):
        source.replace(destination)
        raise OSError("directory sync failed")

    monkeypatch.setattr(m, "replace_durable", renamed_then_fail)
    with pytest.raises(OSError, match="directory sync"):
        sink.commit()
    assert spool.ready() == ()
    assert list(spool.root.iterdir()) == []
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_failed_cleanup_retains_quota_until_retry(tmp_path, monkeypatch):
    from pathlib import Path

    spool = api().Spool(tmp_path / "spool")
    sink = sink_for(spool)
    sink.write(b"abc")
    unlink = Path.unlink

    def fail(path, **kwargs):
        raise PermissionError("busy file")

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(PermissionError):
        sink.close()
    assert spool.stats()["bytes"] == 3
    assert spool.stats()["files"] == 1
    monkeypatch.setattr(Path, "unlink", unlink)
    sink.close()
    sink.close()
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_ack_delete_failure_does_not_reclaim_or_advance(tmp_path, monkeypatch):
    from pathlib import Path

    spool = api().Spool(tmp_path / "spool")
    sink = sink_for(spool)
    sink.write(b"abc")
    segment = sink.commit()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("busy")))
        with pytest.raises(PermissionError):
            spool.ack(segment.id, segment.sha256)
    assert spool.ready() == (segment,)
    assert spool.stats()["bytes"] == 3
    assert spool.ack(segment.id, segment.sha256)
    assert spool.stats()["bytes"] == 0


@pytest.mark.parametrize(
    "limits",
    [
        {"max_bytes": 0},
        {"max_files": True},
        {"max_segment_bytes": -1},
        {"ack_timeout": float("nan")},
        {"ack_timeout": False},
        {"ack_timeout": 0},
        {"max_bytes": (256 << 20) + 1},
        {"max_files": 1025},
        {"max_segment_bytes": (128 << 20) + 1},
    ],
)
def test_invalid_limits_rejected_before_creating_directory(tmp_path, limits):
    root = tmp_path / "spool"
    with pytest.raises(ValueError, match="limit"):
        api().Spool(root, **limits)
    assert not root.exists()


def test_reservation_validation_and_close(tmp_path, monkeypatch):
    spool = api().Spool(tmp_path / "spool", max_segment_bytes=3)
    for size in (-1, True, 4):
        with pytest.raises(ValueError):
            spool.reserve(size)
    reservation = spool.reserve(3)
    with pytest.raises(ValueError, match="reservation"):
        spool.commit(reservation, stream="trace", offset=0)
    reservation.write(b"abc")
    for offset in (-1, True, 2**53):
        with pytest.raises(ValueError, match="offset"):
            spool.commit(reservation, stream="trace", offset=offset)
    for stream in ("", 1, "x" * 4097):
        with pytest.raises(ValueError, match="stream"):
            spool.commit(reservation, stream=stream, offset=0)
    reservation.abort()
    reservation.abort()
    with pytest.raises(ValueError, match="closed"):
        reservation.write(b"x")
    with pytest.raises(ValueError, match="reservation"):
        spool.commit(reservation, stream="trace", offset=0)
    empty = spool.reserve()
    with pytest.raises(ValueError, match="Empty"):
        spool.commit(empty, stream="trace", offset=0)
    empty.abort()
    sink = sink_for(spool)
    assert sink.write(b"") == 0
    assert sink.commit() is None
    assert sink.tell() == 0
    sink.write(b"abc")
    assert sink.tell() == 3
    segment = sink.commit()
    assert segment.to_dict() == {
        "id": segment.id,
        "stream": "trace",
        "sequence": 1,
        "offset": 0,
        "length": 3,
        "sha256": segment.sha256,
    }
    with pytest.raises(ValueError, match="digest"):
        spool.ack(segment.id, "invalid")
    sink.close()
    with pytest.raises(ValueError, match="closed"):
        sink.write(b"")
    with pytest.raises(ValueError, match="closed"):
        sink.commit()


def test_short_write_and_close_error_still_release_storage(tmp_path, monkeypatch):
    spool = api().Spool(tmp_path / "spool")
    reservation = spool.reserve()
    monkeypatch.setattr(reservation.file, "write", lambda data: 0)
    with pytest.raises(OSError, match="Incomplete"):
        reservation.write(b"abc")
    close = reservation.file.close

    def close_then_fail():
        close()
        raise OSError("close failed")

    monkeypatch.setattr(reservation.file, "close", close_then_fail)
    with pytest.raises(OSError, match="close failed"):
        reservation.abort()
    assert reservation.closed
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_reservation_creation_failure_returns_credit(tmp_path, monkeypatch):
    spool = api().Spool(tmp_path / "spool")
    monkeypatch.setattr(api().tempfile, "mkstemp", lambda **k: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        spool.reserve(3)
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_sequence_exhaustion_is_fatal_and_cleans_temp(tmp_path):
    spool = api().Spool(tmp_path / "spool")
    sink = sink_for(spool)
    sink.write(b"abc")
    spool._sequence = 2**53 - 1
    with pytest.raises(ValueError, match="sequence"):
        sink.commit()
    assert spool.stats()["bytes"] == spool.stats()["files"] == 0


def test_slow_continuing_acks_allow_waits_longer_than_timeout(tmp_path):
    clock = [0.0]
    spool = api().Spool(tmp_path / "spool", max_files=2, clock=lambda: clock[0])
    sink = sink_for(spool)
    sink.write(b"one")
    first = sink.commit()
    sink.write(b"two")
    second = sink.commit()
    result = []
    thread = threading.Thread(target=lambda: (sink.write(b"three"), result.append(sink.commit())))
    thread.start()
    blocked(spool)
    clock[0] = 59
    spool.ack(first.id, first.sha256)
    thread.join(2)
    assert len(result) == 1
    thread = threading.Thread(target=lambda: (sink.write(b"four"), result.append(sink.commit())))
    thread.start()
    blocked(spool)
    clock[0] = 118
    spool.ack(second.id, second.sha256)
    thread.join(2)
    assert len(result) == 2
    sink.close()


def test_latched_failure_prevents_even_unblocked_reservation(tmp_path):
    control = Control()
    spool = api().Spool(tmp_path / "spool", control=control)
    control.fail(OSError("lost coordinator"))
    with pytest.raises(OSError, match="lost coordinator"):
        spool.reserve()
    assert spool.stats()["files"] == 0


def test_publication_never_replaces_existing_segment(tmp_path):
    spool = api().Spool(tmp_path / "spool")
    first_sink = sink_for(spool)
    first_sink.write(b"abc")
    first = first_sink.commit()
    # Simulate damaged sequence state; the existing immutable file must survive.
    spool._sequence = 0
    second_sink = sink_for(spool)
    second_sink.write(b"abc")
    with pytest.raises(ValueError, match="already exists"):
        second_sink.commit()
    assert first.path.read_bytes() == b"abc"
    assert spool.stats()["bytes"] == 3
    assert spool.ready() == (first,)


def test_commit_cleanup_failure_keeps_reservation_for_retry(tmp_path, monkeypatch):
    from pathlib import Path

    spool = api().Spool(tmp_path / "spool")
    from simulatte.trace.sink import SegmentSink

    sink = SegmentSink(spool, stream="")
    sink.write(b"abc")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("busy")))
        with pytest.raises(PermissionError):
            sink.commit()
    assert spool.stats()["bytes"] == 3
    sink.close()
    assert spool.stats()["bytes"] == 0
