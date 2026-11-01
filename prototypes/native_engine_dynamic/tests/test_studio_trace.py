"""Native-rich records project through the production trace writer and reader."""

import pytest

from simulatte import Trace, Provenance
from simulatte.trace import ChunkLimits
from simulatte.trace.reader import TraceCorrupted
from prototypes.native_engine.model import Job, Workload
from prototypes.native_engine.workloads import generate
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.feedback import feed, validate_feed
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.tuned import TunedStream
from prototypes.native_engine_dynamic.studio_trace import TraceStream, validate_trace
from prototypes.native_engine_dynamic.direct_trace import DirectTraceStream


def test_equal_time_grant_and_departure_states_are_not_inferred_from_start_end(tmp_path):
    path = tmp_path / "native.simtrace"
    stream = TraceStream(
        CompiledStream,
        1,
        raw_path=tmp_path / "source",
        trace_path=path,
        wip_cap=1,
        chunk_limits=ChunkLimits(max_events=3, max_latency_s=float("inf")),
        debug=True,
        provenance=Provenance(model="fixture", source="fixture", inputs="fixture", dependencies="fixture"),
    )
    for duration in (2, 1):
        stream.add(Job(0, (0,), (duration,), 5))
    stream.run()
    result = stream.result()
    stream.close()
    trace = Trace.open(path)
    trace.check()
    assert trace.verify() is True
    events = list(trace.events())
    grant = next(e for e in events if e.type == "job.granted")
    before = trace.state_at((grant.t, grant.seq - 1))
    after = trace.state_at((grant.t, grant.seq))
    assert before["server-0"]["queue"] == ("job-0",)
    assert before["server-0"]["users"] == ()
    assert after["server-0"]["queue"] == ()
    assert after["server-0"]["users"] == ("job-0",)
    assert after["job-0"]["op_index"] is None
    departure = next(e for e in events if e.type == "job.released")
    assert trace.state_at((departure.t, departure.seq))["server-0"]["users"] == ()
    assert result["finished"] == [2, 3]
    assert trace.manifest["complete"] is True


@pytest.mark.parametrize("producer", ["independent", "feedback"])
def test_trace_parity_dynamic_entities_kpis_snapshots_and_replay(tmp_path, producer):
    templates = generate(60, 0.95)
    expected = None
    for cls in (ReferenceStream, TunedStream, CompiledStream):
        path = tmp_path / f"{cls.__name__}.simtrace"
        stream = TraceStream(
            cls,
            templates.servers,
            raw_path=tmp_path / f"{cls.__name__}.source",
            trace_path=path,
            wip_cap=12,
            policy="spt",
            debug=True,
            chunk_limits=ChunkLimits(max_events=53, max_latency_s=float("inf")),
        )
        try:
            jobs, decisions = feed(stream, templates, producer)
            stream.run()
            result = stream.result()
        finally:
            stream.close()
        if producer == "feedback":
            validate_feed(templates, jobs, decisions, result)
        signature = validate_trace(path, Workload(templates.servers, jobs), result, wip_cap=12)
        expected = signature if expected is None else expected
        assert signature == expected
    with path.open("r+b") as f:
        f.seek(100)
        f.write(b"broken")
    with pytest.raises(TraceCorrupted):
        Trace.open(path).check()


@pytest.mark.parametrize("stream_type", [TraceStream, DirectTraceStream])
def test_empty_rejected_future_and_close_before_run(tmp_path, stream_type):
    stream = stream_type(CompiledStream, 1, raw_path=tmp_path / "source", trace_path=tmp_path / "empty.simtrace")
    with pytest.raises(ValueError, match="arrival"):
        stream.add(Job(1, (0,), (1,), 5))
    stream.run()
    result = stream.result()
    stream.close()
    validate_trace(tmp_path / "empty.simtrace", Workload(1, ()), result)
    other = stream_type(CompiledStream, 1, raw_path=tmp_path / "other", trace_path=tmp_path / "incomplete.simtrace")
    other.add(Job(0, (0,), (1,), 5))
    other.close()
    other.close()
    assert Trace.open(tmp_path / "incomplete.simtrace").outcome == "cancelled"


@pytest.mark.parametrize("stream_type", [TraceStream, DirectTraceStream])
def test_invalid_calls_remain_usable_and_native_failure_marks_trace_failed(tmp_path, stream_type):
    stream = stream_type(CompiledStream, 1, raw_path=tmp_path / "raw", trace_path=tmp_path / "failed.simtrace")
    for bad in (None, object()):
        with pytest.raises(TypeError):
            stream.add(bad)
    with pytest.raises(ValueError):
        stream.add(Job(0, (1,), (1,), 1))
    with pytest.raises(ValueError):
        stream.advance(-1)
    stream.advance(1e300)
    stream.add(Job(1e300, (0,), (1,), 1))
    with pytest.raises(ValueError):
        stream.run()
    stream.close()
    assert Trace.open(tmp_path / "failed.simtrace").outcome == "failed"


@pytest.mark.parametrize("stream_type", [TraceStream, DirectTraceStream])
def test_writer_failure_closes_all_owners_without_completed_footer(tmp_path, monkeypatch, stream_type):
    from simulatte.trace.sink import FileSink

    real = FileSink.write

    def fail(self, data):
        if len(data) > 100:
            raise OSError("trace disk full")
        return real(self, data)

    monkeypatch.setattr(FileSink, "write", fail)
    with pytest.raises(OSError, match="trace disk full"):
        stream = stream_type(CompiledStream, 1, raw_path=tmp_path / "raw", trace_path=tmp_path / "disk.simtrace")
        try:
            stream.add(Job(0, (0,), (1,), 1))
            stream.run()
        finally:
            stream.close()
