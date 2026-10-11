"""Segment boundaries concatenate to the existing file format byte for byte."""

import struct

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.builders import build_immediate_release_system
from simulatte.events import LogEvent
from simulatte.execution.control import Control, Cancelled
from simulatte.execution.spool import Spool
from simulatte.provenance import VolatileMetadata
from simulatte.scenario import Scenario
from simulatte.trace import ChunkLimits, RecordType
from simulatte.trace.format import TRAILER_MAGIC
from simulatte.trace.sink import SegmentSink


@pytest.mark.parametrize("level", ["full", "kpi"])
def test_segment_and_file_recorders_produce_identical_bytes(tmp_path, monkeypatch, level):
    env = Environment(seed=42)
    monkeypatch.setattr(env, "volatile_metadata", lambda: VolatileMetadata("host", None, 0.0))
    path = tmp_path / "file.simtrace"
    spool = Spool(tmp_path / "spool")
    sink = SegmentSink(spool)
    limits = ChunkLimits(max_events=2)
    first = TraceRecorder(env, path, level=level, chunk_limits=limits, clock=lambda: 0.0)
    second = None
    try:
        second = TraceRecorder(env, sink=sink, level=level, chunk_limits=limits, clock=lambda: 0.0)
        build_immediate_release_system(env=env, scenario=Scenario(n_servers=2))
        env.emit(LogEvent(level="INFO", message="before activation"))
        env.run(until=10)
        env.emit(LogEvent(level="INFO", message="after activation"))
        first.close()
        second.close()
        segments = spool.ready()
        data = b"".join(segment.path.read_bytes() for segment in segments)
        assert data == path.read_bytes()
        expected_offset = 0
        for segment in segments:
            assert segment.offset == expected_offset
            expected_offset += segment.length
            body = segment.path.read_bytes()
            pos = 12 if segment.offset == 0 else 0
            end = len(body) - 16 if body.endswith(TRAILER_MAGIC) else len(body)
            types = []
            while pos < end:
                size, kind, _ = struct.unpack(">IBI", body[pos : pos + 9])
                types.append(kind)
                pos += 9 + size
            assert pos == end
            assert types == [RecordType.CHUNK, RecordType.INDEX] if RecordType.CHUNK in types else len(types) == 1
        trace = Trace.open(path)
        trace.check()
        assert trace.verify()
    finally:
        first.close()
        if second is not None:
            second.close()


def test_ready_wait_is_cancellable_while_spool_is_full(tmp_path):
    control = Control()
    env = Environment(seed=1)
    spool = Spool(tmp_path / "spool", max_files=1, control=control)
    recorder = TraceRecorder(env, sink=SegmentSink(spool), control=control)
    recorder.wait_committed()
    env.activate()
    control.cancel()
    with pytest.raises(Cancelled):
        recorder.wait_committed()
    with pytest.raises(Cancelled):
        recorder.close()
    assert not recorder._thread.is_alive()


def test_strict_pending_budget_rejects_oversize_header_before_starting_writer(tmp_path):
    env = Environment()
    spool = Spool(tmp_path / "spool")
    with pytest.raises(ValueError, match="pending"):
        TraceRecorder(env, sink=SegmentSink(spool), max_pending_bytes=1)
    assert not env._recorders
    assert spool.stats()["files"] == 0


def test_latency_sealing_respects_strict_pending_limit(tmp_path):
    clock = [0.0]
    env = Environment()
    spool = Spool(tmp_path / "spool")
    recorder = TraceRecorder(
        env,
        sink=SegmentSink(spool),
        clock=lambda: clock[0],
        chunk_limits=ChunkLimits(max_bytes=1 << 20, max_latency_s=1),
    )
    recorder.wait_committed()
    # Lower after HEADER: isolate admission of the latency-flushed observer.
    recorder._max_pending = 100
    env.emit(LogEvent(level="INFO", message="x" * 1000))
    with recorder._cond:
        clock[0] = 2
        recorder._cond.notify_all()
    recorder._thread.join(2)
    try:
        assert not recorder._thread.is_alive()
        with pytest.raises(ValueError, match="pending"):
            recorder.wait_committed()
    finally:
        with pytest.raises(ValueError, match="pending"):
            recorder.close()


def test_output_must_be_unambiguous_and_file_sink_tracks_offset(tmp_path):
    from simulatte.trace.sink import FileSink

    sink = FileSink(tmp_path / "trace")
    try:
        with pytest.raises(ValueError, match="exactly one"):
            TraceRecorder(Environment())
        with pytest.raises(ValueError, match="exactly one"):
            TraceRecorder(Environment(), tmp_path / "other", sink=sink)
        assert sink.write(b"abc") == 3
        assert sink.tell() == 3
        sink.commit()
    finally:
        sink.close()


def test_writer_failure_reaches_ready_waiter_and_close(tmp_path, monkeypatch):
    import threading

    env = Environment()
    spool = Spool(tmp_path / "spool")
    sink = SegmentSink(spool)
    entered, release = threading.Event(), threading.Event()

    def fail_commit():
        entered.set()
        assert release.wait(2)
        raise OSError("failed commit")

    monkeypatch.setattr(sink, "commit", fail_commit)
    recorder = TraceRecorder(env, sink=sink)
    assert entered.wait(2)
    errors = []

    def wait():
        try:
            recorder.wait_committed()
        except OSError as error:
            errors.append(error)

    waiter = threading.Thread(target=wait)
    waiter.start()
    release.set()
    waiter.join(2)
    assert len(errors) == 1 and str(errors[0]) == "failed commit"
    with pytest.raises(OSError, match="failed commit"):
        recorder.close()
    assert spool.stats()["files"] == 0
