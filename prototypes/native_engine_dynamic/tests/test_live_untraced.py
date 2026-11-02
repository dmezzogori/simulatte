"""No final recorder, with the same live collectors and observation contract."""

import pytest

from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.live_observation import LiveTraceStream, ProductionLiveStream, values
from prototypes.native_engine_dynamic.observation_gate import equivalent
from prototypes.native_engine_dynamic.validation import fingerprint


@pytest.mark.parametrize("delivery", ["boundary", "buffered"])
def test_no_recorder_or_digest_and_equal_stock_outputs(tmp_path, monkeypatch, delivery):
    import prototypes.native_engine_dynamic.direct_trace as direct
    import prototypes.native_engine_dynamic.production as production

    def forbidden(*args, **kwargs):
        raise AssertionError("TraceRecorder must not be constructed")

    monkeypatch.setattr(direct, "TraceRecorder", forbidden)
    monkeypatch.setattr(production, "TraceRecorder", forbidden)
    native = LiveTraceStream(
        CompiledStream,
        2,
        wip_cap=2,
        level="none",
        delivery=delivery,
        raw_path=tmp_path / "source",
        trace_path=None,
    )
    actual = ProductionLiveStream(2, wip_cap=2, mode="none")
    for stream, env, recorder in (
        (native, native.projection.env, native.projection.recorder),
        (actual, actual.env, actual.recorder),
    ):
        assert recorder is None and env._digest is None and not env._projection_active
        assert not env._projection_listeners
        assert len(env.collectors) == 7  # four floor classes + two servers + WIP-series collector
        for job in (Job(0, (0, 1), (2, 1), 4), Job(0, (1, 0), (1, 1), 4)):
            stream.add(job)
        stream.advance(2)
    if delivery == "boundary":
        equivalent(values(native), values(actual))
    for stream in (native, actual):
        stream.run()
    equivalent(values(native), values(actual))
    a, b = native.result(), actual.result()
    assert fingerprint(a) == fingerprint(b)
    assert a["kpis"] is not None and b["kpis"] is not None
    native.close()
    actual.close()
    native.close()
    equivalent(values(native), values(actual))
    assert not list(tmp_path.glob("*.simtrace"))


@pytest.mark.parametrize("warmup", [0, 2, 20])
@pytest.mark.parametrize("horizon", [None, 4])
def test_untraced_and_recorded_boundary_values_and_probes_match(tmp_path, warmup, horizon):
    from prototypes.native_engine_dynamic.observation_gate import case
    from prototypes.native_engine_dynamic.semantics_gate import workloads

    a = case(
        workloads()["boundary"],
        tmp_path,
        cap=2,
        policy="spt",
        producer="feedback",
        level="none",
        warmup=warmup,
        horizon=horizon,
    )
    b = case(
        workloads()["boundary"],
        tmp_path,
        cap=2,
        policy="spt",
        producer="feedback",
        level="full",
        warmup=warmup,
        horizon=horizon,
    )
    equivalent(a, b)


@pytest.mark.parametrize("delivery", ["boundary", "buffered"])
def test_untraced_close_can_extract_implicitly_and_preserves_idle_window(tmp_path, delivery):
    s = LiveTraceStream(
        CompiledStream, 1, level="none", delivery=delivery, raw_path=tmp_path / "source", trace_path=None
    )
    s.add(Job(0, (0,), (1,), 10))
    s.advance(100)
    s.run()
    expected = values(s)
    s.close()
    s.close()
    equivalent(expected, values(s))
    assert expected["scalars"]["shopfloor/throughput"] == 0.01
    assert expected["window"] == (0, 100, True)


def test_untraced_callback_failure_and_control_guards(tmp_path):
    from simulatte.shopfloor import JobFinished

    s = LiveTraceStream(CompiledStream, 1, wip_cap=1, level="none", raw_path=tmp_path / "source", trace_path=None)
    a = s.add(Job(0, (0,), (1,), 10))
    b = s.add(Job(0, (0,), (2,), 10))
    seen = []

    def callback(event):
        seen.append((s.now, s.inspect(a)["state"], s.inspect(b)["state"]))
        for fn in (lambda: s.advance(10), s.run, s.result, s.close):
            with pytest.raises(RuntimeError, match="observational"):
                fn()
        with pytest.raises(RuntimeError, match="cannot schedule"):
            s.projection.env.timeout(1)
        raise LookupError("observer failed")

    s.projection.env.bus.subscribe(callback, (JobFinished,))
    with pytest.raises(LookupError, match="observer failed"):
        s.advance(10)
    assert seen == [(1, "finished", "pool")]
    prefix = values(s)
    with pytest.raises(RuntimeError):
        s.result()
    s.close()
    equivalent(prefix, values(s))


def test_untraced_does_not_silently_ignore_a_trace_path(tmp_path):
    with pytest.raises(ValueError, match="trace_path=None"):
        LiveTraceStream(CompiledStream, 1, level="none", raw_path=tmp_path / "source", trace_path=tmp_path / "trace")
    assert not (tmp_path / "trace").exists()


def test_untraced_benchmark_compares_equal_stock_work_and_rejects_drift(tmp_path):
    import copy
    import gzip
    import json
    import subprocess
    import sys

    from prototypes.native_engine.workloads import generate, save
    from prototypes.native_engine_dynamic.untraced_benchmark import cases, check_groups

    save(generate(20, 0.95), tmp_path / "input")
    assert len(cases()) == 48
    rows, observed = [], []
    for level in ("none", "full"):
        for backend in ("production", "buffered", "boundary"):
            path = tmp_path / ("production.gz" if backend == "production" else "native.gz")
            command = [
                sys.executable,
                "-m",
                "prototypes.native_engine_dynamic.untraced_benchmark",
                "worker",
                "--input",
                str(tmp_path / "input"),
                "--jobs",
                "20",
                "--observations",
                str(path),
                "--producer",
                "feedback",
                "--level",
                level,
                "--backend",
                backend,
                "--probe",
            ]
            row = json.loads(subprocess.check_output(command, text=True))
            assert row["no_recording_guard"] == (level == "none")
            assert (row["trace_bytes"] == 0) == (level == "none")
            assert row["peak_rss_bytes"] > 0 and row["model_kpis"] is not None
            rows.append(row)
            observed.append(json.loads(gzip.decompress(path.read_bytes())))
    check_groups(rows)
    for value in observed:
        equivalent(value, observed[0])
    for field in ("trajectory", "scalars", "observations_sha256", "trace_wire_sha256"):
        changed = copy.deepcopy(rows)
        changed[-1][field] = "corrupt"
        with pytest.raises((AssertionError, AttributeError)):
            check_groups(changed)


def test_untraced_cpu_profile_has_no_writer_and_keeps_stock_output(tmp_path):
    import json
    import platform
    import subprocess
    import sys

    from prototypes.native_engine.workloads import generate, save

    if platform.python_implementation() != "CPython":
        pytest.skip("CPython thread-CPU profiler only")
    save(generate(20, 0.95), tmp_path / "input")
    results = []
    for level in ("none", "full"):
        path = tmp_path / f"{level}.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "prototypes.native_engine_dynamic.untraced_benchmark",
                "profile",
                "--input",
                str(tmp_path / "input"),
                "--jobs",
                "20",
                "--producer",
                "feedback",
                "--level",
                level,
                "--backend",
                "boundary",
                "--output",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        value = json.loads(path.read_text())
        assert len(value["writers"]) == (0 if level == "none" else 1)
        assert value["no_recording_guard"] == (level == "none")
        assert value["simulation"]["total_self_cpu"] > 0 and value["separate_thread_passes"]
        results.append(value)
    assert results[0]["observations_sha256"] == results[1]["observations_sha256"]
