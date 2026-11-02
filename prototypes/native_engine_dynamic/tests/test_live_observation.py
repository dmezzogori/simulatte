"""The live contract is checked against real, unmodified production collectors."""

import math

import pytest

from simulatte import Trace
from simulatte.shopfloor import JobFinished
from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.native import NativeStream
from prototypes.native_engine_dynamic.reference import ReferenceStream
from prototypes.native_engine_dynamic.tuned import TunedStream
from prototypes.native_engine_dynamic.live_observation import LiveTraceStream, ProductionLiveStream, values
from prototypes.native_engine_dynamic.validation import fingerprint


def test_boundary_delivery_and_callback_handle_are_event_time(tmp_path):
    stream = LiveTraceStream(CompiledStream, 1, wip_cap=1, raw_path=tmp_path / "source", trace_path=tmp_path / "trace")
    handle = stream.add(Job(0, (0,), (2,), 10))
    seen = []
    stream.projection.env.bus.subscribe(
        lambda event: seen.append((event.t, stream.now, stream.inspect(handle))), (JobFinished,)
    )
    stream.advance(2)
    assert not seen and stream.inspect(handle)["state"] == "active"
    stream.advance(2)
    stream.advance(math.nextafter(2, math.inf))
    assert len(seen) == 1 and seen[0][:2] == (2, 2)
    assert seen[0][2]["state"] == "finished" and seen[0][2]["finished"] == 2
    assert stream.projection.env.now == stream.now == math.nextafter(2, math.inf)
    stream.run()
    stream.result()
    stream.close()


def close_enough(a, b):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            close_enough(a[key], b[key])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            close_enough(x, y)
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=2e-11, abs=1e-8)
    else:
        assert a == b


@pytest.mark.parametrize("level", ["kpi", "full"])
def test_stock_collectors_match_at_intermediate_and_final_boundaries(tmp_path, level):
    native = LiveTraceStream(
        CompiledStream, 2, wip_cap=2, level=level, raw_path=tmp_path / "source", trace_path=tmp_path / "native"
    )
    production = ProductionLiveStream(2, wip_cap=2, mode=level, trace_path=tmp_path / "production")
    try:
        for job in (Job(0, (0, 1), (2, 1), 4), Job(0, (1, 0), (1, 1), 4), Job(2, (0,), (1,), 4)):
            for stream in (native, production):
                stream.advance(job.arrival)
                stream.add(job)
            assert values(native) == values(production)
        for until in (2, 2.5, 3, 4):
            native.advance(until)
            production.advance(until)
            assert values(native) == values(production)
        for stream in (native, production):
            stream.run()
        assert fingerprint(native.result()) == fingerprint(production.result())
        assert values(native) == values(production)
    finally:
        native.close()
        production.close()


@pytest.mark.parametrize("warmup", [0, 1, 2, 4, 20])
@pytest.mark.parametrize("finish", [False, True])
def test_partial_windows_and_close_match_production(tmp_path, warmup, finish):
    native = LiveTraceStream(
        CompiledStream, 1, wip_cap=1, warmup=warmup, raw_path=tmp_path / "source", trace_path=tmp_path / "native"
    )
    production = ProductionLiveStream(1, wip_cap=1, warmup=warmup, mode="full", trace_path=tmp_path / "production")
    for stream in (native, production):
        stream.add(Job(0, (0,), (2,), 3))
        stream.advance(2)
        stream.add(Job(2, (0,), (2,), 3))
    close_enough(values(native), values(production))
    assert not values(native)["window"][2]
    if finish:
        native.run()
        production.run()
        native.result()
        production.result()
    close_enough(values(native), values(production))
    native.close()
    production.close()
    close_enough(values(native), values(production))
    for path in (tmp_path / "native", tmp_path / "production"):
        trace = Trace.open(path)
        trace.check()
        assert trace.verify() is True
        assert trace.outcome == "completed"
    close_enough(Trace.open(tmp_path / "native").kpis(), Trace.open(tmp_path / "production").kpis())


def test_callback_inspection_has_no_lookahead_and_model_control_is_rejected(tmp_path):
    s = LiveTraceStream(CompiledStream, 1, wip_cap=1, raw_path=tmp_path / "source", trace_path=tmp_path / "trace")
    a = s.add(Job(0, (0,), (1,), 10))
    b = s.add(Job(0, (0,), (2,), 10))
    seen = []

    def observe(event):
        seen.append((s.now, s.inspect(a)["state"], s.inspect(b)["state"]))
        for control in (
            lambda: s.add(Job(event.t, (0,), (1,), 10)),
            lambda: s.advance(event.t),
            s.run,
            s.close,
            s.result,
        ):
            with pytest.raises(RuntimeError, match="observational"):
                control()
        with pytest.raises(RuntimeError, match="cannot schedule"):
            s.projection.env.timeout(1)

    s.projection.env.bus.subscribe(observe, (JobFinished,))
    s.advance(10)
    assert seen == [(1, "finished", "pool"), (3, "finished", "finished")]
    assert s.now == s.projection.env.now == 10
    s.run()
    s.result()
    s.close()


def test_views_are_read_only_and_finished_job_is_retired(tmp_path):
    import gc
    import weakref

    from simulatte.entities import EntityCreated

    s = LiveTraceStream(CompiledStream, 1, wip_cap=1, raw_path=tmp_path / "source", trace_path=tmp_path / "trace")
    created = []
    s.projection.env.bus.subscribe(lambda event: created.append(event.entity), (EntityCreated,))
    handle = s.add(Job(0, (0,), (1,), 10))
    assert created == ["job-0"]
    job = s.projection.env.entities.get("job-0")
    ref = weakref.ref(job)
    with pytest.raises(AttributeError):
        job.done = True
    with pytest.raises(RuntimeError, match="live model snapshot"):
        job.snapshot()
    floor = s.projection.env.entities.get("shopfloor")
    with pytest.raises(TypeError):
        list(floor.jobs)
    with pytest.raises(TypeError):
        floor.wip[s.projection.server_views[0]] = 100
    del job
    s.run()
    for _ in range(3):
        gc.collect()
    assert ref() is None and s.inspect(handle)["state"] == "finished"
    s.result()
    s.close()


@pytest.mark.parametrize("factory", [ReferenceStream, TunedStream, NativeStream, CompiledStream])
@pytest.mark.parametrize("mode", ["none", "kpi", "record", "studio"])
def test_flushing_changes_neither_calendar_nor_journal(factory, mode, tmp_path):
    rows, files = [], []
    for flush in (False, True):
        path = tmp_path / str(flush)
        s = factory(2, wip_cap=2, policy="spt", mode=mode, trace_path=path if mode in ("record", "studio") else None)
        for index in range(30):
            s.advance(index // 3)
            s.add(Job(index // 3, (index % 2, 1 - index % 2), (1, 0.5), 10))
            if flush:
                before = s.now
                s.flush()
                assert s.now == before
        s.run()
        if flush:
            s.flush()
        rows.append(s.result())
        s.close()
        if mode in ("record", "studio"):
            files.append(path.read_bytes())
        with pytest.raises(RuntimeError):
            s.flush()
    assert rows[0] == rows[1]
    assert not files or files[0] == files[1]


def test_callback_exception_poisoning_keeps_a_valid_failed_prefix(tmp_path):
    s = LiveTraceStream(CompiledStream, 1, raw_path=tmp_path / "source", trace_path=tmp_path / "trace")
    s.add(Job(0, (0,), (1,), 10))

    def fail(event):
        raise LookupError("observer failure")

    s.projection.env.bus.subscribe(fail, (JobFinished,))
    with pytest.raises(LookupError, match="observer failure"):
        s.advance(5)
    with pytest.raises(RuntimeError):
        s.result()
    s.close()
    trace = Trace.open(tmp_path / "trace")
    trace.check()
    assert trace.outcome == "failed" and trace.verify() is True


@pytest.mark.parametrize("level", ["kpi", "full"])
@pytest.mark.parametrize("delivery", ["boundary", "buffered"])
def test_exhaustion_can_close_without_explicit_result_extraction(tmp_path, level, delivery):
    s = LiveTraceStream(
        CompiledStream, 1, level=level, delivery=delivery, raw_path=tmp_path / "source", trace_path=tmp_path / "trace"
    )
    s.add(Job(0, (0,), (1,), 10))
    s.run()
    expected = values(s)
    s.close()
    s.close()
    trace = Trace.open(tmp_path / "trace")
    trace.check()
    assert trace.outcome == "completed"
    assert trace.verify() == (True if level == "full" else "not_verifiable")
    close_enough(trace.kpis(), expected["scalars"])


def test_buffered_control_is_event_time_but_has_no_boundary_delivery(tmp_path):
    s = LiveTraceStream(
        CompiledStream, 1, wip_cap=1, delivery="buffered", raw_path=tmp_path / "source", trace_path=tmp_path / "trace"
    )
    a = s.add(Job(0, (0,), (1,), 10))
    b = s.add(Job(0, (0,), (2,), 10))
    seen = []
    s.projection.env.bus.subscribe(lambda event: seen.append((s.now, s.inspect(b)["state"])), (JobFinished,))
    s.advance(10)
    assert seen == [] and s.inspect(a)["state"] == "finished" and s.projection.env.now == 0
    s.run()
    assert seen == [(1, "pool"), (3, "finished")]
    s.result()
    s.close()


def test_continuous_observations_and_idle_tail_match(tmp_path):
    from prototypes.native_engine.workloads import generate
    from prototypes.native_engine_dynamic.observation_gate import case

    case(generate(80, 0.95), tmp_path, cap=2, policy="spt", producer="feedback", level="full", warmup=2)
    streams = (
        LiveTraceStream(CompiledStream, 1, raw_path=tmp_path / "tail-source", trace_path=tmp_path / "tail-native"),
        ProductionLiveStream(1, mode="full", trace_path=tmp_path / "tail-production"),
    )
    for s in streams:
        s.add(Job(0, (0,), (1,), 10))
        s.advance(100)
        s.run()
        s.result()
    close_enough(values(streams[0]), values(streams[1]))
    assert values(streams[0])["scalars"]["shopfloor/throughput"] == 0.01
    for s in streams:
        s.close()


def test_transport_flush_failure_poisons_native_boundaries(tmp_path):
    import subprocess
    import sys

    pytest.importorskip("resource")
    script = """
import resource,signal,sys
from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.native import NativeStream
signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE,(1,1))
for cls in (NativeStream,CompiledStream):
    s=cls(1,mode="studio",trace_path=sys.argv[1]+cls.__name__)
    h=s.add(Job(0,(0,),(1,),2))
    try: s.flush()
    except OSError: pass
    else: raise AssertionError("flush must fail")
    for action in (s.result,lambda:s.inspect(h),lambda:s.advance(1)):
        try: action()
        except RuntimeError: pass
        else: raise AssertionError("failed engine escaped")
    s.close()
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "limited")], check=True, capture_output=True)


def test_benchmark_workers_compare_equal_stock_work(tmp_path):
    import copy
    import gzip
    import json
    import subprocess
    import sys

    from prototypes.native_engine.workloads import generate, save
    from prototypes.native_engine_dynamic.observation_benchmark import check_groups, cases
    from prototypes.native_engine_dynamic.observation_gate import equivalent

    save(generate(20, 0.95), tmp_path / "input")
    rows, observed = [], []
    assert len(cases()) == 36
    for backend in ("production", "buffered", "boundary"):
        path = tmp_path / ("production.gz" if backend == "production" else "native.gz")
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.observation_benchmark",
            "worker",
            "--input",
            str(tmp_path / "input"),
            "--observations",
            str(path),
            "--jobs",
            "20",
            "--producer",
            "feedback",
            "--level",
            "kpi",
            "--backend",
            backend,
            "--probe",
        ]
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["peak_rss_bytes"] > 0 and len(row["samples"]) == 1 and row["warmups"] == []
        assert row["trace_signature"]["chunks"] == 0
        rows.append(row)
        observed.append(json.loads(gzip.decompress(path.read_bytes())))
    check_groups(rows)
    equivalent(observed[0], observed[1])
    equivalent(observed[0], observed[2])
    for field in ("trajectory", "scalars", "trace_wire_sha256", "observations_sha256"):
        changed = copy.deepcopy(rows)
        changed[2][field] = "corrupt"
        with pytest.raises((AssertionError, AttributeError)):
            check_groups(changed)
