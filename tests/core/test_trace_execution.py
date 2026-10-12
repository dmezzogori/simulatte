"""Optional execution provenance must not change semantic trace verification."""

import struct
from typing import Any

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.execution.schema import ExecutionRequest, ResolvedManifest, request_id, _plain
from simulatte.stopping import Horizon, execute_until


def metadata():
    request = ExecutionRequest(
        model="plant.py:build",
        source_hash="a" * 64,
        input_hash="b" * 64,
        layout_hash="c" * 64,
        layout_revision=1,
        parameters={},
        seed=42,
        stopping=Horizon(1).to_dict(),
        runtime={},
    )
    resolved = ResolvedManifest(runtime={}, dependencies={}, imports={}, layout={}, capabilities={})
    return {"schema": 1, "request_id": request_id(request), "request": request.to_dict()}, resolved.to_dict()


def test_execution_metadata_roundtrip_does_not_change_fingerprint(tmp_path):
    header, resolved = metadata()
    fingerprints = []
    for managed in (False, True):
        env = Environment(seed=42)
        path = tmp_path / f"{managed}.simtrace"
        recorder = TraceRecorder(
            env, path, **({"execution": header, "resolve_execution": lambda: resolved} if managed else {})
        )
        stop = execute_until(env, Horizon(1))
        recorder.close(termination=stop.reason)
        trace = Trace.open(path)
        trace.check()
        assert trace.verify() is True
        assert struct.unpack(">HH", path.read_bytes()[8:12]) == (1, 3)
        fingerprints.append(trace.fingerprint)
        if managed:
            execution = _plain(trace.execution)
            assert execution["header"]["request_id"] == header["request_id"]
            assert execution["initial"]["resolved"]["runtime"] == {}
            assert execution["footer"] == {"schema": 1, "termination": "horizon", "actual_end": 1.0}
        else:
            assert trace.execution is None
    assert fingerprints[0] == fingerprints[1]


def test_execution_metadata_validated_before_writer_and_at_activation(tmp_path):
    header, resolved = metadata()
    invalid: tuple[dict[str, Any], ...] = (
        {"execution": header},
        {"resolve_execution": lambda: resolved},
        {"execution": header | {"request_id": "0" * 64}, "resolve_execution": lambda: resolved},
    )
    for kwargs in invalid:
        with pytest.raises(ValueError):
            TraceRecorder(Environment(), tmp_path / "invalid", **kwargs)
    assert not (tmp_path / "invalid").exists()
    env = Environment(seed=42)
    recorder = TraceRecorder(env, tmp_path / "initial", execution=header, resolve_execution=lambda: {"schema": 2})
    try:
        with pytest.raises(ValueError, match="resolved"):
            env.activate()
    finally:
        recorder.close(outcome="failed")


@pytest.mark.parametrize(
    "value", [None, [], {}, {"schema": True}, {"schema": 1}, {"schema": 1, "request": [], "request_id": "a" * 64}]
)
def test_invalid_requested_metadata(value):
    from simulatte.trace.execution import header

    with pytest.raises(ValueError):
        header(value)


@pytest.mark.parametrize("change", [{"runtime": []}, {"dependencies": []}, {"warnings": [1]}, {"warnings": "invalid"}])
def test_invalid_resolved_metadata(change):
    from simulatte.trace.execution import initial

    _, resolved = metadata()
    with pytest.raises(ValueError, match="resolved"):
        initial(resolved | change)


@pytest.mark.parametrize(
    "reason,end", [(None, 1), ("", 1), ("x" * 4097, 1), ("horizon", True), ("horizon", -1), ("horizon", float("nan"))]
)
def test_invalid_termination_metadata(reason, end):
    from simulatte.trace.execution import footer

    with pytest.raises(ValueError):
        footer(reason, end)


def test_partial_and_unknown_optional_execution_metadata():
    from simulatte.trace.execution import read

    header, resolved = metadata()
    record = {"features": {"optional": ["execution-v1"]}, "execution": header}
    assert read(record, None, None, has_initial=False)["initial"] is None
    assert read(record, {"schema": 1, "resolved": resolved}, None, has_initial=True)["footer"] is None
    for optional in (["future-execution-v2"], {}, None):
        assert read({"features": {"optional": optional}}, None, None, has_initial=False) is None
    with pytest.raises(ValueError, match="differs"):
        read(
            record,
            None,
            {"execution": {"schema": 1, "termination": "horizon", "actual_end": 1}, "cursor": [2, 0]},
            has_initial=False,
        )


def test_selected_kpis_filter_recording_without_disabling_collectors(tmp_path):
    from tests.core.test_kpi import _desk_system

    values = []
    digests = []
    for selection in (None, ("desk/flow_time",), ()):
        env = Environment(seed=42)
        desk, collector = _desk_system(env)
        path = tmp_path / str(len(values))
        recorder = TraceRecorder(env, path, kpis=selection)
        env.run(until=10)
        recorder.close()
        trace = Trace.open(path)
        assert trace.verify() is True
        assert trace.fingerprint is not None
        values.append(env.fingerprint().kpis)
        digests.append(trace.fingerprint.digest)
        assert desk.done == 4 and len(collector.flow_times) == 4
        expected = set(values[-1]) if selection is None else set(selection)
        assert set(trace.fingerprint.kpis) == expected
        assert set(trace.kpi_declarations) == expected
        assert set(trace.kpi_series()) == ({"desk/flow_time"} if selection != () else set())
    assert values[0] == values[1] == values[2]
    assert digests[0] == digests[1] == digests[2]
    invalid_selections: tuple[Any, ...] = (["desk/flow_time"], ("",), (1,))
    for selection in invalid_selections:
        with pytest.raises(ValueError, match="KPI selection"):
            TraceRecorder(Environment(), tmp_path / "invalid-selection", kpis=selection)
    assert not (tmp_path / "invalid-selection").exists()


def test_unknown_optional_provenance_fields_are_retained_without_affecting_verification(tmp_path):
    header, resolved = metadata()
    resolved["future"] = {"information": 7}
    header["future"] = True
    env = Environment(seed=42)
    path = tmp_path / "future.simtrace"
    recorder = TraceRecorder(env, path, execution=header, resolve_execution=lambda: resolved)
    execute_until(env, Horizon(1))
    recorder.close(termination="horizon")
    trace = Trace.open(path)
    assert trace.verify() is True
    execution = _plain(trace.execution)
    assert execution["header"]["future"] is True
    assert execution["initial"]["resolved"]["future"]["information"] == 7
