"""SP3 display extensions preserve the domain trace and digest contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.events import DomainEvent, ObserverEvent, event_type
from simulatte.kpi import Collector, KPI
from simulatte.trace import ChunkLimits
from simulatte.server import Server


@event_type("test.viewer.domain")
class Change(DomainEvent):
    value: int


@event_type("test.viewer.observer")
class Observation(ObserverEvent):
    value: int


class Values(Collector):
    kpis = (KPI("value", unit="jobs", kind=("series",), interpolation="linear", missing="hold"),)


def test_display_metadata_is_separate_from_legacy_declarations(tmp_path: Path) -> None:
    env = Environment()
    recorder = TraceRecorder(env, tmp_path / "display.simtrace")
    collector = Values(Server(env=env, capacity=1)).attach(env)
    env.activate()
    collector.sample("value", 4)
    recorder.close()
    trace = Trace.open(tmp_path / "display.simtrace")
    key = next(iter(trace.kpi_declarations))
    assert "interpolation" not in cast(dict, trace.kpi_declarations[key])
    assert dict(cast(dict, trace.kpi_series_display[key])) == {"interpolation": "linear", "missing": "hold"}
    assert len(trace.kpi_series()[key]) == 1
    assert trace.verify() is True


def test_observer_history_keeps_domain_replay_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "observers.simtrace"
    env = Environment()
    recorder = TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=1))
    env.emit(Observation(value=-1))
    env.activate()
    env.emit(Change(value=1))
    env.emit(Observation(value=2))
    env.info("a label </script>")
    recorder.close()
    trace = Trace.open(path)
    trace.check()
    assert trace.verify() is True
    assert [event.type for event in trace.events()] == ["test.viewer.domain"]
    assert [event.type for event in trace.observer_events()] == ["test.viewer.observer", "log"]
    assert [event.payload["value"] for event in trace.observer_events(prelude=True)] == [-1]
    history = list(trace.inspect_events())
    assert [event.seq for event in history] == sorted(event.seq for event in history)
    assert len(history) == 3
    assert trace.cursor_range is not None
    assert history[-1].seq > trace.cursor_range[1][1]
    assert all(not event.deltas and event.ordinal is None for event in trace.observer_events())


def test_recorder_diagnostics_cannot_recurse_across_recorders(tmp_path: Path) -> None:
    env = Environment()
    observed: list[object] = []
    env.bus.subscribe(observed.append, "**")
    recorders = [
        TraceRecorder(env, tmp_path / f"{i}.simtrace", chunk_limits=ChunkLimits(max_event_bytes=1)) for i in range(2)
    ]
    env.activate()
    env.emit(Observation(value=2))
    for recorder in recorders:
        recorder.close()
    assert len(observed) == 1
    assert len(env.log_history.query(level="WARNING")) == 2
    for i in range(2):
        trace = Trace.open(tmp_path / f"{i}.simtrace")
        assert len(list(trace.observer_events())) == 1
        assert trace.verify() is True


@pytest.mark.parametrize("field,value", [("interpolation", "cubic"), ("missing", "zero")])
def test_invalid_series_display_rule(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        KPI("value", unit="jobs", **cast(Any, {field: value}))


@pytest.mark.parametrize(
    "change",
    [
        {"phase": "prelude"},
        {"epoch": 99},
        {"first": (9.0, 0)},
        {"last": (9.0, 0)},
        {"extra": True},
        {"events": ()},
        {"events": ((0, None, "test.viewer.observer", 0.0, {"value": 1}, ()),) * 2},
        {"events": ((0, 1, "test.viewer.observer", 0.0, {"value": 1}, ()),)},
        {"events": ((0, None, "test.viewer.domain", 0.0, {"value": 1}, ()),)},
        {"events": ((0, None, "test.viewer.observer", 0.0, {"value": 1}, (("retire", "x"),)),)},
    ],
)
def test_invalid_observer_records_are_rejected(tmp_path: Path, change: dict[str, Any]) -> None:
    import io

    from simulatte._wire import pack, unpack
    from simulatte.trace.format import PREAMBLE, RECORD_HEADER, RecordType, write_record
    from simulatte.trace.reader import TraceCorrupted

    path = tmp_path / "bad.simtrace"
    env = Environment()
    recorder = TraceRecorder(env, path)
    env.activate()
    env.emit(Observation(value=1))
    recorder.close()
    data = path.read_bytes()
    result = io.BytesIO(data[: PREAMBLE.size])
    result.seek(PREAMBLE.size)
    offset = PREAMBLE.size
    while offset < len(data):
        length, kind, _ = RECORD_HEADER.unpack_from(data, offset)
        if kind == RecordType.FOOTER:
            break
        payload = data[offset + RECORD_HEADER.size : offset + RECORD_HEADER.size + length]
        if kind == RecordType.OBSERVERS:
            original = cast(Any, unpack(payload))
            payload = pack(cast(Any, {**original, **change}))
        write_record(result, kind, payload)
        offset += RECORD_HEADER.size + length
    path.write_bytes(result.getvalue())
    trace = Trace.open(path)
    with pytest.raises(TraceCorrupted):
        trace.check()


def test_late_observer_catalog_and_reentrant_emission(tmp_path: Path) -> None:
    env = Environment()
    path = tmp_path / "late.simtrace"
    recorder = TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=1))

    @event_type("test.viewer.late_observer")
    class Late(ObserverEvent):
        value: int

    def derived(event: Observation) -> None:
        env.emit(Late(value=event.value + 1))

    env.bus.subscribe(derived, (Observation,))
    env.activate()
    env.emit(Observation(value=1))
    recorder.close()
    trace = Trace.open(path)
    trace.check()
    assert [e.payload["value"] for e in trace.observer_events()] == [1, 2]
    assert trace.verify()


def test_observer_latency_flushes_without_a_following_event(tmp_path: Path) -> None:
    now = [0.0]
    path = tmp_path / "latency.simtrace"
    env = Environment()
    recorder = TraceRecorder(env, path, clock=lambda: now[0])
    env.activate()
    env.emit(Observation(value=1))
    with recorder._cond:
        now[0] = 2.0
        recorder._cond.notify_all()
        assert recorder._cond.wait_for(lambda: not recorder._observers and recorder._pending == 0, timeout=5)
    assert [e.payload["value"] for e in Trace.open(path).observer_events()] == [1]
    recorder.close()


@pytest.mark.parametrize("tail", [False, True])
def test_observer_crc_damage_rejects_interior_records_but_ignores_incomplete_tail(tmp_path: Path, tail: bool) -> None:
    from simulatte.trace.format import PREAMBLE, RECORD_HEADER, RecordType
    from simulatte.trace.reader import TraceCorrupted

    path = tmp_path / "observer-crc.simtrace"
    env = Environment()
    recorder = TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=1))
    env.activate()
    env.emit(Change(value=0))
    env.emit(Observation(value=1))
    env.emit(Observation(value=2))
    env.emit(Change(value=3))
    recorder.close()
    data = bytearray(path.read_bytes())
    observers: list[tuple[int, int]] = []
    offset = PREAMBLE.size
    while offset < len(data):
        length, kind, _ = RECORD_HEADER.unpack_from(data, offset)
        if kind == RecordType.FOOTER:
            break
        end = offset + RECORD_HEADER.size + length
        if kind == RecordType.OBSERVERS:
            observers.append((offset, end))
        offset = end
    assert len(observers) == 2
    damaged, end = observers[-1]
    data[damaged + RECORD_HEADER.size] ^= 0xFF
    path.write_bytes(data[:end] if tail else data)
    trace = Trace.open(path)
    if tail:
        assert trace.truncated
        assert [event.payload["value"] for event in trace.observer_events()] == [1]
        trace.check()
    else:
        assert not trace.truncated
        with pytest.raises(TraceCorrupted, match=f"record at {damaged} fails its CRC check"):
            list(trace.observer_events())
