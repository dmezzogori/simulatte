"""A counter-only emitter must preserve every recorded aligned-bridge value."""

import sys

import pytest

from simulatte import Trace
from simulatte.trace import ChunkLimits
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import generate
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.direct_trace import DirectTraceStream
from prototypes.native_engine_dynamic.feedback import feed, validate_feed
from prototypes.native_engine_dynamic.studio_trace import TraceStream, validate_trace
from prototypes.native_engine_dynamic.trace_benchmark import normalized_wire
from prototypes.native_engine_dynamic.validation import validate_result


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("cap", [None, 2])
@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("producer", ["independent", "feedback"])
@pytest.mark.parametrize("level", ["kpi", "full"])
def test_exact_aligned_direct_output_parity(tmp_path, debug, cap, policy, producer, level):
    templates = generate(60, 0.95)
    expected = None
    for stream_type in (TraceStream, DirectTraceStream):
        raw = tmp_path / f"{stream_type.__name__}.source"
        path = tmp_path / f"{stream_type.__name__}.simtrace"
        stream = stream_type(
            CompiledStream,
            templates.servers,
            wip_cap=cap,
            policy=policy,
            level=level,
            debug=debug,
            raw_path=raw,
            trace_path=path,
            chunk_limits=ChunkLimits(max_events=37, max_latency_s=float("inf")),
        )
        try:
            jobs, decisions = feed(stream, templates, producer)
            stream.run()
            result = stream.result()
            if producer == "feedback":
                validate_feed(templates, jobs, decisions, result)
            assert not any(actor.kind == "job" for actor in stream.projection.env.entities.live())
        finally:
            stream.close()
        workload = Workload(templates.servers, jobs)
        validate_result(workload, result, wip_cap=cap)
        trace = Trace.open(path)
        trace.check()
        signature = validate_trace(path, workload, result, wip_cap=cap) if level == "full" else None
        assert trace.verify() == (True if level == "full" else "not_verifiable")
        actual = (
            result,
            jobs,
            decisions,
            raw.read_bytes(),
            signature,
            trace.fingerprint,
            trace.kpi_series(),
            normalized_wire(path),
        )
        expected = actual if expected is None else expected
        assert actual == expected


def test_publisher_has_no_full_live_state_and_explains_snapshot_limit(tmp_path):
    stream = DirectTraceStream(CompiledStream, 1, raw_path=tmp_path / "source", trace_path=tmp_path / "direct.simtrace")
    assert not hasattr(stream.projection, "state")
    assert stream.projection.env.initial_state["server-0"]["users"] == ()
    with pytest.raises(RuntimeError, match="live model"):
        stream.projection.env.entities.snapshot()
    stream.run()
    stream.result()
    stream.close()
    trace = Trace.open(tmp_path / "direct.simtrace")
    trace.check()
    assert trace.verify() is True


def test_generic_replay_is_removed_from_simulation_thread(tmp_path):
    calls = []
    for cls in (TraceStream, DirectTraceStream):
        replay = []

        def observe(frame, event, arg):
            if event == "call" and frame.f_code.co_name == "apply_deltas":
                replay.append(frame.f_code.co_filename)

        stream = cls(CompiledStream, 10, wip_cap=2, raw_path=tmp_path / "source", trace_path=tmp_path / "out.simtrace")
        previous = sys.getprofile()
        sys.setprofile(observe)
        try:
            feed(stream, generate(20, 0.95), "independent")
            stream.run()
            stream.result()
            stream.close()
        finally:
            sys.setprofile(previous)
        calls.append(len(replay))
    assert calls[0] > 0 and calls[1] == 0
