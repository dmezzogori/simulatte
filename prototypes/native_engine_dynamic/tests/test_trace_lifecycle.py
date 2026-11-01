"""Production retirement must affect replay, never native results or handles."""

import gc
import weakref

import pytest

from simulatte import Trace
from simulatte.entities import EntityCreated
from simulatte.trace import ChunkLimits
from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.studio_trace import TraceStream
from prototypes.native_engine_dynamic.direct_trace import DirectTraceStream


@pytest.mark.parametrize("factory", [CompiledStream, ReferenceStream])
@pytest.mark.parametrize("stream_type", [TraceStream, DirectTraceStream])
@pytest.mark.parametrize("cap", [None, 1])
@pytest.mark.parametrize("level", ["kpi", "full"])
def test_retirement_preserves_handles_results_and_historical_state(tmp_path, factory, stream_type, cap, level):
    path = tmp_path / "lifecycle.simtrace"
    stream = stream_type(
        factory,
        1,
        wip_cap=cap,
        policy="spt",
        level=level,
        raw_path=tmp_path / "source",
        trace_path=path,
        debug=True,
        chunk_limits=ChunkLimits(max_events=3, max_latency_s=float("inf")),
    )
    references = []

    def created(event):
        if event.kind == "job":
            references.append(weakref.ref(stream.projection.env.entities.get(event.entity)))

    stream.projection.env.bus.subscribe(created, (EntityCreated,))
    handles = [stream.add(Job(0, (0,), (duration,), 10)) for duration in (2, 1)]
    stream.run()
    first = stream.result()
    for index, handle in enumerate(handles):
        observed = stream.inspect(handle)
        assert observed["state"] == "finished"
        assert observed["released"] == first["released"][index]
        assert observed["finished"] == first["finished"][index]
    assert stream.result() == first
    if stream_type is TraceStream:
        assert not any(key.startswith("job-") for key in stream.projection.state)
    else:
        assert not hasattr(stream.projection, "state")
    assert not any(actor.kind == "job" for actor in stream.projection.env.entities.live())
    gc.collect()
    assert len(references) == 2 and all(ref() is None for ref in references)
    stream.close()
    trace = Trace.open(path)
    trace.check()
    if level == "kpi":
        assert trace.verify() == "not_verifiable"
        assert trace.kpis()["shopfloor/completed"] == 2
        return
    assert trace.verify() is True
    events = list(trace.events())
    retired = [e for e in events if e.type == "entity.retired"]
    decisions = [e for e in events if e.type == "policy.decision"]
    assert len(retired) == 2 and len(decisions) == (2 if cap else 0)
    for event in retired:
        identity = event.payload["entity"]
        index = int(identity.split("-")[1])
        before = trace.state_at((event.t, event.seq - 1))
        after = trace.state_at((event.t, event.seq))
        assert before[identity]["location"] == "done"
        assert before[identity]["finished_at"] == first["finished"][index]
        assert identity not in after
        assert event.deltas == (("retire", identity),)
    for decision in decisions:
        assert dict(decision.payload) == dict(policy="ConWIP", job=decision.payload["job"], action="release")
        exit_event = next(e for e in events if e.type == "psp.exited" and e.payload["job"] == decision.payload["job"])
        assert decision.t == exit_event.t and decision.seq < exit_event.seq
    assert not any(key.startswith("job-") for key in trace.state_at(trace.cursor_range[1]))
