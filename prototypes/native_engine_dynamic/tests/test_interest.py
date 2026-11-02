"""Demand checks must not suppress view updates or requested observations."""

import math

import pytest

from simulatte.psp import PspEntered, PspExited
from simulatte.policies.events import PolicyDecision
from simulatte.server import ServerWorkCredited
from simulatte.shopfloor import OperationStarted, OperationCompleted
from prototypes.native_engine.model import Job
from prototypes.native_engine_dynamic.compiled import CompiledStream
from prototypes.native_engine_dynamic.direct_trace import DirectProjection
from prototypes.native_engine_dynamic.live_observation import LiveTraceStream, values
from prototypes.native_engine_dynamic.observation_gate import equivalent
from prototypes.native_engine_dynamic.trace_benchmark import normalized_wire

UNREQUESTED = (PspEntered, PspExited, PolicyDecision, ServerWorkCredited, OperationStarted)


def stream(tmp_path, name, *, interest=True, stock=True, level="none"):
    return LiveTraceStream(
        CompiledStream,
        2,
        wip_cap=1,
        interest_checks=interest,
        stock=stock,
        level=level,
        raw_path=tmp_path / f"{name}.source",
        trace_path=None if level == "none" else tmp_path / f"{name}.simtrace",
    )


@pytest.mark.parametrize("stock", [False, True])
def test_interest_skips_construction_after_view_updates(tmp_path, monkeypatch, stock):
    original = DirectProjection.emit
    constructed = []

    def construct(self, cls, *args, **kwargs):
        constructed.append(cls)
        return original(self, cls, *args, **kwargs)

    monkeypatch.setattr(DirectProjection, "emit", construct)
    s = stream(tmp_path, "native", stock=stock)
    handle = s.add(Job(0, (0, 1), (2, 1), 10))
    s.advance(1)
    job = s.projection.env.entities.get("job-0")
    assert job.psp_exit_at == 0 and s.inspect(handle)["state"] == "active"
    assert s.projection.inspect(handle.id)["state"] == "active"
    s.advance(math.nextafter(2, math.inf))
    assert s.projection.server_views[0].worked_time == 2
    s.run()
    assert job.done and job.finished_at == 3 and len(s.projection.env.entities.get("shopfloor").jobs_done) == 1
    assert not any(cls in UNREQUESTED for cls in constructed)
    if stock:
        assert OperationCompleted in constructed
    else:
        assert constructed == []
    s.result()
    s.close()


@pytest.mark.parametrize("types", ["*", "**"])
def test_catch_all_observers_preserve_exact_event_stream(tmp_path, types):
    captured = []
    for interest in (False, True):
        s = stream(tmp_path, str(interest), interest=interest)
        events = []
        s.projection.env.bus.subscribe(events.append, types)
        for duration in (1, 2, 1):
            s.add(Job(0, (0, 1), (duration, 1), 10))
        s.run()
        s.result()
        s.close()
        captured.append(events)
    assert captured[0] == captured[1]


@pytest.mark.parametrize("level", ["kpi", "full"])
def test_recording_bypasses_interest_checks_and_preserves_bytes(tmp_path, level):
    captured = []
    for interest in (False, True):
        s = stream(tmp_path, str(interest), interest=interest, level=level)
        assert not s.projection.interest_checks
        s.add(Job(0, (0, 1), (2, 1), 10))
        s.run()
        s.result()
        s.close()
        captured.append(values(s))
    assert captured[0] == captured[1]
    assert (tmp_path / "False.source").read_bytes() == (tmp_path / "True.source").read_bytes()
    assert normalized_wire(tmp_path / "False.simtrace") == normalized_wire(tmp_path / "True.simtrace")


def test_subscriptions_change_immediately_during_delivery(tmp_path, monkeypatch):
    original = DirectProjection.emit
    constructed = []

    def construct(self, cls, *args, **kwargs):
        constructed.append((cls, self.env.now))
        return original(self, cls, *args, **kwargs)

    monkeypatch.setattr(DirectProjection, "emit", construct)
    s = stream(tmp_path, "dynamic")
    starts = []

    def once(event):
        starts.append(event.t)
        subscription.cancel()

    subscription = s.projection.env.bus.subscribe(once, (OperationStarted,))
    s.add(Job(0, (0, 1), (1, 1), 10))
    s.advance(1.5)
    assert starts == [0]
    assert [t for cls, t in constructed if cls is OperationStarted] == [0]
    # The false interest lookup is already cached at the second start.
    s.add(Job(1.5, (0,), (1,), 10))
    second = s.projection.env.bus.subscribe(lambda event: starts.append(event.t), (OperationStarted,))
    s.run()
    assert starts == [0, 2]
    second.cancel()
    s.result()
    s.close()


@pytest.mark.parametrize("types", [UNREQUESTED, (OperationStarted,)])
def test_specific_observers_get_same_payload_and_time(tmp_path, types):
    from dataclasses import fields

    captured = []
    snapshots = []
    for interest in (False, True):
        s = stream(tmp_path, str(interest), interest=interest)
        events = []

        def capture(event):
            events.append({f.name: getattr(event, f.name) for f in fields(event) if f.name not in ("seq", "ordinal")})

        s.projection.env.bus.subscribe(capture, types)
        s.add(Job(0, (0, 1), (1, 1), 10))
        s.run()
        s.result()
        s.close()
        captured.append(events)
        snapshots.append(values(s))
    assert captured[0] == captured[1]
    equivalent(snapshots[0], snapshots[1])


def test_continuous_stock_values_and_boundary_probes_match_production(tmp_path):
    from prototypes.native_engine.workloads import generate
    from prototypes.native_engine_dynamic.observation_gate import case

    workload = generate(80, 0.95)
    a = case(
        workload, tmp_path, cap=2, policy="spt", producer="feedback", level="none", warmup=2, interest_checks=False
    )
    b = case(workload, tmp_path, cap=2, policy="spt", producer="feedback", level="none", warmup=2, interest_checks=True)
    equivalent(a, b)


def test_callback_failure_and_control_guards_survive_filtering(tmp_path):
    from simulatte.shopfloor import JobFinished

    s = stream(tmp_path, "failed")
    first = s.add(Job(0, (0,), (1,), 10))
    second = s.add(Job(0, (0,), (2,), 10))
    seen = []

    def callback(event):
        seen.append((s.now, s.inspect(first)["state"], s.inspect(second)["state"]))
        for fn in (lambda: s.advance(10), s.run, s.result, s.close):
            with pytest.raises(RuntimeError, match="observational"):
                fn()
        raise LookupError("observer failed")

    s.projection.env.bus.subscribe(callback, (JobFinished,))
    with pytest.raises(LookupError, match="observer failed"):
        s.advance(10)
    assert seen == [(1, "finished", "pool")]
    snapshot = values(s)
    s.close()
    equivalent(snapshot, values(s))


def test_interest_benchmark_counts_actual_emissions_and_preserves_exports(tmp_path):
    import copy
    import gzip
    import json
    import subprocess
    import sys

    from prototypes.native_engine.workloads import generate, save
    from prototypes.native_engine_dynamic.interest_benchmark import cases, variants, emission_count, check_groups

    save(generate(20, 0.95), tmp_path / "input")
    assert len(cases()) == 40
    rows = []
    for backend, interest in variants():
        path = tmp_path / ("production.gz" if backend == "production" else "native.gz")
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.interest_benchmark",
            "worker",
            "--input",
            str(tmp_path / "input"),
            "--jobs",
            "20",
            "--observations",
            str(path),
            "--producer",
            "feedback",
            "--backend",
            backend,
            "--probe",
        ]
        if interest:
            command.append("--interest-checks")
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["interest_checks"] == interest and row["python_emissions"] == emission_count(row)
        assert row["no_recording_guard"] and row["trace_bytes"] == 0
        data = json.loads(gzip.decompress(path.read_bytes()))
        assert data["scalars"] == row["scalars"]
        rows.append(row)
    check_groups(rows)
    for field in ("trajectory", "scalars", "observations_sha256", "python_emissions"):
        changed = copy.deepcopy(rows)
        changed[-1][field] = "corrupt"
        with pytest.raises((AssertionError, AttributeError)):
            check_groups(changed)


def test_interest_profile_counts_match_environment_counter(tmp_path):
    import json
    import platform
    import subprocess
    import sys

    from prototypes.native_engine.workloads import generate, save

    if platform.python_implementation() != "CPython":
        pytest.skip("CPython thread-CPU diagnostic only")
    save(generate(20, 0.95), tmp_path / "input")
    counts = []
    for interest in (False, True):
        path = tmp_path / f"{interest}.json"
        command = [
            sys.executable,
            "-m",
            "prototypes.native_engine_dynamic.interest_benchmark",
            "profile",
            "--input",
            str(tmp_path / "input"),
            "--jobs",
            "20",
            "--producer",
            "feedback",
            "--backend",
            "boundary",
            "--output",
            str(path),
        ]
        if interest:
            command.append("--interest-checks")
        completed = subprocess.run(command, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        value = json.loads(path.read_text())
        assert value["interest_checks"] == interest and not value["writers"]
        counts.append(value["python_emissions"])
    assert counts[1] < counts[0]
