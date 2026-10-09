"""Trace container and threaded recorder (spec §11.1, §11.2)."""

from __future__ import annotations

import contextlib
import struct
import threading
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

import simulatte.trace.writer as writer_mod
from simulatte._wire import FrozenMap, pack, unpack
from simulatte.builders import build_immediate_release_system
from simulatte.digest import Fingerprint
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, LogEvent, apply_deltas, event_type
from simulatte.scenario import Scenario
from simulatte.trace import ChunkLimits, RecordType, TraceRecorder
from simulatte.trace.format import FORMAT_MAJOR, FORMAT_MINOR, MAGIC, TRAILER_MAGIC

# ---------------------------------------------------------------------------------------------------------
# Model and helpers
# ---------------------------------------------------------------------------------------------------------


class Gauge(Entity, kind="test_trace_gauge"):
    state_schema: ClassVar[StateSchema] = StateSchema({"level": FieldSpec("float")})

    def __init__(self, env: Environment, *, name: str | None = None) -> None:
        self.level = 0.0
        env.entities.attach(self, name=name)


@event_type("test.trace_set", touches={"test_trace_gauge": ("level",)})
class GaugeSet(DomainEvent):
    gauge: str
    level: float
    note: str = ""


def _emit_set(env: Environment, gauge: Gauge, level: float, *, note: str = "", live: bool = True) -> None:
    """Emit a level change; with ``live=False`` the live object is left untouched."""
    if live:
        gauge.level = level
    env.emit(
        GaugeSet(gauge=gauge.id, level=level, note=note, deltas=Deltas.build().set(gauge.id, "level", level).done())
    )


class FakeClock:
    """Injected recorder clock; only the test advances it. `read_by_writer` is set once the writer reads it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.read_by_writer = threading.Event()

    def __call__(self) -> float:
        if threading.current_thread() is not threading.main_thread():
            self.read_by_writer.set()
        return self.now


@dataclass
class Rec:
    offset: int
    type: int
    size: int
    body: Any


def _read(path: Path) -> list[Rec]:
    """Parse every record of a trace file, checking CRCs; CHUNK payloads are decompressed."""
    data = path.read_bytes()
    assert data[:8] == MAGIC
    assert struct.unpack(">HH", data[8:12]) == (FORMAT_MAJOR, FORMAT_MINOR)
    end = len(data) - 16 if data.endswith(TRAILER_MAGIC) else len(data)
    records: list[Rec] = []
    pos = 12
    while pos < end:
        length, rtype, crc = struct.unpack(">IBI", data[pos : pos + 9])
        payload = data[pos + 9 : pos + 9 + length]
        assert len(payload) == length
        assert zlib.crc32(payload) == crc
        if rtype == RecordType.CHUNK:
            payload = zlib.decompress(payload)
        records.append(Rec(offset=pos, type=rtype, size=9 + length, body=unpack(payload)))
        pos += 9 + length
    assert pos == end
    return records


def _trailer_offset(path: Path) -> int | None:
    data = path.read_bytes()
    if not data.endswith(TRAILER_MAGIC):
        return None
    return struct.unpack(">Q", data[-16:-8])[0]


def _of(records: list[Rec], rtype: RecordType) -> list[Rec]:
    return [r for r in records if r.type == rtype]


def _replay(records: list[Rec]) -> dict[str, dict[str, Any]]:
    """Initial state plus the deltas of every chunk event, in file order."""
    (initial,) = _of(records, RecordType.INITIAL)
    state = {entity: dict(fields) for entity, fields in initial.body["state"].items()}
    for chunk in _of(records, RecordType.CHUNK):
        for event in chunk.body["events"]:
            apply_deltas(state, Deltas(tuple(event[5])))
    return state


def _drain(rec: TraceRecorder) -> None:
    """Wait until the writer wrote everything enqueued so far."""
    with rec._cond:
        assert rec._cond.wait_for(lambda: rec._pending == 0 or rec._error is not None, timeout=10)


@pytest.fixture
def make_recorder() -> Iterator[Callable[..., TraceRecorder]]:
    created: list[TraceRecorder] = []

    def make(env: Environment, path: Path, **kwargs: Any) -> TraceRecorder:
        rec = TraceRecorder(env, path, **kwargs)
        created.append(rec)
        return rec

    yield make
    for rec in created:
        with contextlib.suppress(Exception):
            rec.close()
        assert not rec._thread.is_alive()


def _system(env: Environment) -> None:
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))


# ---------------------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------------------


def test_chunks_respect_event_limit(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=3), clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    for i in range(10):
        _emit_set(env, gauge, float(i))
    rec.close()

    records = _read(path)
    chunks = _of(records, RecordType.CHUNK)
    assert [len(c.body["events"]) for c in chunks] == [3, 3, 3, 1]
    for i, record in enumerate(records):
        if record.type == RecordType.CHUNK:
            index = records[i + 1]
            assert index.type == RecordType.INDEX
            assert index.body["offset"] == record.offset
            assert index.body["length"] == record.size
            assert index.body["first"] == record.body["first"]
            assert index.body["last"] == record.body["last"]
    seq, ordinal, name, t, payload, deltas = chunks[0].body["events"][0]
    assert (ordinal, name, t) == (0, "test.trace_set", 0.0)
    assert payload == {"gauge": "g", "level": 0.0, "note": ""}
    assert deltas == (("set", "g", "level", 0.0),)
    assert chunks[0].body["first"] == (0.0, seq)
    assert [e[1] for c in chunks for e in c.body["events"]] == list(range(10))


def test_latency_publishes_without_further_events(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    clock = FakeClock()
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=clock)  # default latency limit: 1.0 s
    gauge = Gauge(env, name="g")
    seen: dict[str, list[Rec]] = {}

    def process() -> Any:
        yield env.timeout(1)
        _emit_set(env, gauge, 1.0)
        _emit_set(env, gauge, 2.0)
        yield env.timeout(1)
        # The simulation thread is blocked here, inside a callback, until the writer published the chunk.
        assert clock.read_by_writer.wait(10)  # the writer has timed an open buffer before the clock moves
        clock.now += 1.5
        with rec._cond:  # wake the writer so it re-reads the clock
            rec._cond.notify_all()
            assert rec._cond.wait_for(lambda: rec._chunks_written >= 1, timeout=10)
        seen["records"] = _read(path)
        _emit_set(env, gauge, 3.0)

    env.process(process())
    env.run()
    rec.close()

    during = seen["records"]
    assert [r.type for r in during[-2:]] == [RecordType.CHUNK, RecordType.INDEX]
    assert [e[4]["level"] for e in during[-2].body["events"]] == [1.0, 2.0]
    chunks = _of(_read(path), RecordType.CHUNK)
    assert [len(c.body["events"]) for c in chunks] == [2, 1]


def test_chunk_snapshot_from_replay_state_not_live(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=2), clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    _emit_set(env, gauge, 1.0, live=False)
    _emit_set(env, gauge, 2.0, live=False)
    gauge.level = 999.0  # live state diverges from the recorded deltas
    _emit_set(env, gauge, 3.0, live=False)
    _emit_set(env, gauge, 4.0, live=False)
    rec.close()

    chunks = _of(_read(path), RecordType.CHUNK)
    assert [c.body["snapshot"]["g"]["level"] for c in chunks] == [0.0, 2.0]
    assert chunks[1].body["snapshot"]["g"]["$kind"] == "test_trace_gauge"


def test_initial_record_written_at_activation(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=7)
    rec = make_recorder(env, path, clock=FakeClock())
    Gauge(env, name="g")
    env.time_unit = "minute"  # an activation-only manifest field, configured after attachment
    env.activate()
    _drain(rec)

    records = _read(path)
    assert [r.type for r in records] == [RecordType.HEADER, RecordType.PRELUDE, RecordType.INITIAL]
    header, prelude, initial = (r.body for r in records)
    assert header["level"] == "full"
    assert header["features"]["required"]
    assert header["chunk_limits"]["max_events"] == 10_000
    assert "test.trace_set" in header["catalog"]
    assert header["kinds"]["test_trace_gauge"]["level"]["type"] == "float"
    assert header["manifest"]["seed"] == "7"
    assert "rng_derivation" in header["manifest"]
    assert not {"parameters", "time_unit", "warmup"} & set(header["manifest"])
    assert "host" in header["volatile"]
    assert [e[2] for e in prelude["events"]] == ["entity.created"]
    assert prelude["events"][0][1] is None
    assert initial["cursor"] == (0.0, -1)
    assert initial["state"] == env.initial_state
    assert initial["manifest"] == {"parameters": {}, "time_unit": "minute", "warmup": 0.0}
    rec.close()


def test_footer_trailer_and_final_manifest(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=3)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=50), clock=FakeClock())
    _system(env)
    env.run(until=50)
    rec.close()

    records = _read(path)
    footer = records[-1]
    assert footer.type == RecordType.FOOTER
    assert _trailer_offset(path) == footer.offset
    body = footer.body
    assert body["outcome"] == "completed"
    assert body["manifest"] == env.manifest().final
    assert body["manifest"]["stopping_policy"] == {"type": "horizon", "horizon": 50.0}
    assert body["fingerprint"] == {"digest": env.fingerprint().digest, "kpis": {}}
    assert body["fingerprint"]["digest"] is not None
    assert body["index"] == tuple(r.body for r in _of(records, RecordType.INDEX))
    assert len(body["index"]) >= 2
    assert body["epochs"] == (records[0].offset,)
    last_seq = _of(records, RecordType.CHUNK)[-1].body["events"][-1][0]
    assert body["cursor"] == (50.0, last_seq)
    assert _replay(records) == env.entities.snapshot()


def test_failed_run_footer(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())

    def boom() -> Any:
        yield env.timeout(3)
        raise ValueError("model failure")

    env.process(boom())
    with pytest.raises(ValueError, match="model failure"):
        env.run(until=10)
    rec.close()
    assert _read(path)[-1].body["outcome"] == "failed"


def test_interrupted_run_footer(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())

    def interrupt(_: object) -> None:
        raise KeyboardInterrupt

    env.timeout(5).callbacks.append(interrupt)
    env.run(until=10)  # KeyboardInterrupt becomes StopSimulation
    assert env._interrupted
    assert env.now == 5
    rec.close()
    assert _read(path)[-1].body["outcome"] == "cancelled"


def test_recorder_after_activation_raises(tmp_path: Path) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    env.activate()
    with pytest.raises(RuntimeError, match="activation"):
        TraceRecorder(env, path)
    assert not path.exists()


def test_close_without_run(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())
    rec.close()

    records = _read(path)
    assert [r.type for r in records] == [RecordType.HEADER, RecordType.FOOTER]
    footer = records[-1].body
    assert footer["outcome"] == "completed"
    assert footer["manifest"] is None
    assert footer["cursor"] == (0.0, -1)
    assert footer["index"] == ()
    assert _trailer_offset(path) == records[-1].offset
    env.activate()  # a closed recorder ignores the activation
    assert [r.type for r in _read(path)] == [RecordType.HEADER, RecordType.FOOTER]


def test_continues_across_run_calls(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=4)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=40), clock=FakeClock())
    _system(env)
    env.run(until=10)
    env.run(until=20)
    rec.close()

    records = _read(path)
    assert len(_of(records, RecordType.INITIAL)) == 1
    times = [e[3] for c in _of(records, RecordType.CHUNK) for e in c.body["events"]]
    assert min(times) < 10 < max(times) <= 20
    assert records[-1].body["manifest"]["stopping_policy"] == {"type": "horizon", "horizon": 20.0}
    assert _replay(records) == env.entities.snapshot()


def test_catalog_extension_before_first_use(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())

    @event_type("test.trace_late")
    class Late(DomainEvent):
        value: int

    class LateThing(Entity, kind="test_trace_late_thing"):
        state_schema: ClassVar[StateSchema] = StateSchema({"size": FieldSpec("int")})

        def __init__(self, env: Environment) -> None:
            self.size = 3
            env.entities.attach(self)

    env.activate()
    env.emit(Late(value=1))
    LateThing(env)
    rec.close()

    records = _read(path)
    header = records[0].body
    assert "test.trace_late" not in header["catalog"]
    assert "test_trace_late_thing" not in header["kinds"]
    exts = _of(records, RecordType.CATALOG_EXT)
    assert [e.body["epoch"] for e in exts] == [1, 2]
    assert set(exts[0].body["types"]) == {"test.trace_late"}
    assert exts[0].body["types"]["test.trace_late"]["fields"][0]["name"] == "value"
    assert set(exts[1].body["kinds"]) == {"test_trace_late_thing"}
    (chunk,) = _of(records, RecordType.CHUNK)
    assert all(records.index(e) < records.index(chunk) for e in exts)
    assert chunk.body["epoch"] == 2
    assert [e[2] for e in chunk.body["events"]] == ["test.trace_late", "entity.created"]
    assert records[-1].body["epochs"] == (records[0].offset, exts[0].offset, exts[1].offset)


def test_oversized_event_warns_or_raises_in_debug(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    limits = ChunkLimits(max_event_bytes=128)
    env = Environment(seed=1)
    rec = make_recorder(env, tmp_path / "a.simtrace", chunk_limits=limits, clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    _emit_set(env, gauge, 1.0, note="x" * 300)
    rec.close()
    warnings = env.log_history.query(level="WARNING")
    assert any("max_event_bytes" in w.message for w in warnings)
    (chunk,) = _of(_read(tmp_path / "a.simtrace"), RecordType.CHUNK)
    assert chunk.body["events"][0][4]["note"] == "x" * 300

    debug_env = Environment(seed=1, debug=True)
    make_recorder(debug_env, tmp_path / "b.simtrace", chunk_limits=limits, clock=FakeClock())
    debug_gauge = Gauge(debug_env, name="g")
    debug_env.activate()
    with pytest.raises(ValueError, match="max_event_bytes"):
        _emit_set(debug_env, debug_gauge, 1.0, note="x" * 300)


def _gate_writes(
    monkeypatch: pytest.MonkeyPatch, rec: TraceRecorder, rtype: RecordType, until: Callable[[], bool]
) -> None:
    """Make the writer wait, before each record of `rtype`, until `until()` holds (checked under the lock)."""
    real = writer_mod.write_record

    def gated(f: Any, record_type: int, payload: bytes) -> int:
        if record_type == rtype:
            with rec._cond:
                if not rec._cond.wait_for(until, timeout=10):
                    raise AssertionError("gate timed out")
        return real(f, record_type, payload)

    monkeypatch.setattr(writer_mod, "write_record", gated)


def test_slow_writer_applies_backpressure_with_bounded_memory(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    bound = 16 << 10
    env = Environment(seed=5)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=20), max_pending_bytes=bound, clock=FakeClock())
    _drain(rec)
    rec._peak_pending = 0  # the header is admitted on its own; measure what follows
    # The writer publishes a chunk only while the simulation is blocked on backpressure (or closing).
    _gate_writes(monkeypatch, rec, RecordType.CHUNK, lambda: rec._producer_waiting or rec._closing)
    _system(env)
    env.run(until=200)
    rec.close()

    assert 0 < rec._peak_pending <= bound
    blocked = [w for w in env.log_history.query(level="WARNING") if "blocked" in w.message]
    # the first block is logged, the others only in the summary written when the recorder closes
    assert rec._blocked_count > 2
    assert len(blocked) == 2
    assert "further blocks are summarized" in blocked[0].message
    assert f"blocked {rec._blocked_count} times" in blocked[1].message
    records = _read(path)
    assert records[-1].body["outcome"] == "completed"
    assert len(_of(records, RecordType.CHUNK)) > 10
    assert _replay(records) == env.entities.snapshot()


def test_writer_failure_propagates(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    real = writer_mod.write_record

    def failing(f: Any, rtype: int, payload: bytes) -> int:
        if rtype == RecordType.CHUNK:
            raise OSError("disk full")
        return real(f, rtype, payload)

    monkeypatch.setattr(writer_mod, "write_record", failing)
    env = Environment(seed=1)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=1), clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    _emit_set(env, gauge, 1.0)
    with rec._cond:
        assert rec._cond.wait_for(lambda: rec._error is not None, timeout=10)
    with pytest.raises(OSError, match="disk full"):
        _emit_set(env, gauge, 2.0)
    with pytest.raises(OSError, match="disk full"):
        rec.close()
    rec.close()  # repeated close is a no-op, even after a failure

    records = _read(path)
    assert RecordType.FOOTER not in [r.type for r in records]
    assert _trailer_offset(path) is None


def test_close_during_publication_orders_footer_last(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=2), clock=FakeClock())
    _gate_writes(monkeypatch, rec, RecordType.CHUNK, lambda: rec._closing)
    gauge = Gauge(env, name="g")
    env.activate()
    for i in range(20):
        _emit_set(env, gauge, float(i))
    assert len(rec._queue) >= 5  # the writer holds the first chunk; the rest is queued
    rec.close()

    records = _read(path)
    assert records[-1].type == RecordType.FOOTER
    assert _trailer_offset(path) == records[-1].offset
    chunks = _of(records, RecordType.CHUNK)
    assert len(chunks) == 10
    assert len(records[-1].body["index"]) == 10
    assert [r.type for r in records[-21:-1]] == [RecordType.CHUNK, RecordType.INDEX] * 10


def test_repeated_close_is_noop(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())
    _system(env)
    env.run(until=5)
    rec.close()
    size = path.stat().st_size
    rec.close()
    env.close()
    assert path.stat().st_size == size
    assert [r.type for r in _read(path)].count(RecordType.FOOTER) == 1


def test_env_close_closes_recorders(tmp_path: Path) -> None:
    path = tmp_path / "t.simtrace"
    with Environment(seed=1) as env:
        rec = TraceRecorder(env, path, clock=FakeClock())
        _system(env)
        env.run(until=5)
    assert not rec._thread.is_alive()
    assert _read(path)[-1].body["outcome"] == "completed"


def test_kpi_scalars_precede_footer(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())
    monkeypatch.setattr(env, "fingerprint", lambda: Fingerprint(digest=None, kpis={"shop/throughput": 2.5}))
    rec.close()
    records = _read(path)
    assert [r.type for r in records[-2:]] == [RecordType.KPI, RecordType.FOOTER]
    assert records[-2].body == {"scalars": {"shop/throughput": 2.5}}


def test_kpi_level_records_no_chunks(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, level="kpi", clock=FakeClock())

    class Probe(Entity, kind="test_trace_kpi_probe"):  # registered after the header was written
        state_schema: ClassVar[StateSchema] = StateSchema({"hits": FieldSpec("int")})

        def __init__(self, env: Environment) -> None:
            self.hits = 0
            env.entities.attach(self)

    Probe(env)
    _system(env)
    env.run(until=20)
    rec.close()
    records = _read(path)
    types = [RecordType.HEADER, RecordType.CATALOG_EXT, RecordType.INITIAL, RecordType.FOOTER]
    assert [r.type for r in records] == types
    assert set(records[1].body["kinds"]) == {"test_trace_kpi_probe"}
    assert records[0].body["level"] == "kpi"
    assert records[-1].body["fingerprint"]["digest"] == env.fingerprint().digest
    with pytest.raises(ValueError, match="level"):
        TraceRecorder(Environment(), tmp_path / "x.simtrace", level="everything")  # ty: ignore[invalid-argument-type]


def test_large_prelude_then_warmup_then_activation_readable(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder]
) -> None:
    path = tmp_path / "t.simtrace"
    bound = 2048
    env = Environment(seed=2)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=10), max_pending_bytes=bound, clock=FakeClock())
    _drain(rec)
    rec._peak_pending = 0  # the header is admitted on its own; measure what follows
    gauges = [Gauge(env) for _ in range(200)]  # construction: far more prelude bytes than the bound
    env.time_unit = "minute"  # configured after construction, fixed at activation
    env.activate()
    for i, gauge in enumerate(gauges[:5]):
        _emit_set(env, gauge, float(i))
    rec.close()

    records = _read(path)
    (initial,) = _of(records, RecordType.INITIAL)
    # INITIAL (200 entities) is a single item above the bound, admitted alone (U2); nothing else exceeds it.
    assert initial.size - 9 > bound
    assert rec._peak_pending <= initial.size - 9
    preludes = _of(records, RecordType.PRELUDE)
    assert max(r.size for r in preludes) <= bound
    assert len(preludes) >= 10
    assert sum(r.size for r in preludes) > bound
    assert sum(len(r.body["events"]) for r in preludes) == 200
    kinds = [r.type for r in records]
    assert kinds.index(RecordType.INITIAL) > max(i for i, k in enumerate(kinds) if k == RecordType.PRELUDE)
    assert initial.body["manifest"]["time_unit"] == "minute"
    assert initial.body["manifest"]["warmup"] == 0.0
    assert _replay(records) == env.entities.snapshot()


def test_oversized_batch_admitted_when_drained(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=1), max_pending_bytes=1024, clock=FakeClock())
    gauge = Gauge(env, name="g")
    _drain(rec)
    rec._peak_pending = 0
    # INITIAL stays pending until the producer waits for the 2 KiB batch.
    _gate_writes(monkeypatch, rec, RecordType.INITIAL, lambda: rec._producer_waiting)
    env.activate()
    _emit_set(env, gauge, 1.0, note="y" * 2048)
    rec.close()

    records = _read(path)
    (chunk,) = _of(records, RecordType.CHUNK)
    entry = chunk.body["events"][0]
    batch = len(pack(tuple(entry)))
    assert batch > 1024
    assert rec._peak_pending == batch  # admitted alone, once the queue had drained
    assert any("blocked" in w.message for w in env.log_history.query(level="WARNING"))
    assert records[-1].body["outcome"] == "completed"
    assert isinstance(entry[4], FrozenMap)


def test_chunks_respect_byte_and_window_limits(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    by_bytes = tmp_path / "bytes.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, by_bytes, chunk_limits=ChunkLimits(max_bytes=300), clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    for i in range(5):
        _emit_set(env, gauge, float(i), note="z" * 50)
    _emit_set(env, gauge, 9.0, note="w" * 400)  # larger than max_bytes: a chunk of its own
    rec.close()
    chunks = _of(_read(by_bytes), RecordType.CHUNK)
    sizes = [[len(pack(tuple(e))) for e in c.body["events"]] for c in chunks]
    assert [len(s) for s in sizes] == [2, 2, 1, 1]
    assert all(sum(s) <= 300 for s in sizes[:-1])
    assert sizes[-1][0] > 300

    by_window = tmp_path / "window.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, by_window, chunk_limits=ChunkLimits(max_sim_window=10.0), clock=FakeClock())
    gauge = Gauge(env, name="g")

    def process() -> Any:
        for at in (0.0, 5.0, 9.5, 10.0, 15.0, 25.0):
            yield env.timeout(at - env.now)
            _emit_set(env, gauge, at)

    env.process(process())
    env.run()
    rec.close()
    chunks = _of(_read(by_window), RecordType.CHUNK)
    assert [[e[3] for e in c.body["events"]] for c in chunks] == [[0.0, 5.0, 9.5], [10.0, 15.0], [25.0]]
    assert [(c.body["t_start"], c.body["t_end"]) for c in chunks] == [(0.0, 9.5), (10.0, 15.0), (25.0, 25.0)]


def test_invalid_arguments_raise(tmp_path: Path, make_recorder: Callable[..., TraceRecorder]) -> None:
    for bad in ({"max_events": 0}, {"max_bytes": 0}, {"max_event_bytes": 0}):
        with pytest.raises(ValueError, match=next(iter(bad))):
            ChunkLimits(**bad)
    with pytest.raises(ValueError, match="max_latency_s"):
        ChunkLimits(max_latency_s=0.0)
    with pytest.raises(ValueError, match="max_sim_window"):
        ChunkLimits(max_sim_window=-1.0)
    with pytest.raises(ValueError, match="max_pending_bytes"):
        TraceRecorder(Environment(), tmp_path / "x.simtrace", max_pending_bytes=0)
    assert not (tmp_path / "x.simtrace").exists()

    path = tmp_path / "t.simtrace"
    rec = make_recorder(Environment(seed=1), path, clock=FakeClock())
    with pytest.raises(ValueError, match="outcome"):
        rec.close(outcome="aborted")  # ty: ignore[invalid-argument-type]
    rec.close(outcome="cancelled")
    assert _read(path)[-1].body["outcome"] == "cancelled"


def test_footer_write_failure_raises(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    real = writer_mod.write_record

    def failing(f: Any, rtype: int, payload: bytes) -> int:
        if rtype == RecordType.FOOTER:
            raise OSError("disk full")
        return real(f, rtype, payload)

    monkeypatch.setattr(writer_mod, "write_record", failing)
    rec = make_recorder(Environment(seed=1), path, clock=FakeClock())
    with pytest.raises(OSError, match="disk full"):
        rec.close()
    assert [r.type for r in _read(path)] == [RecordType.HEADER]
    assert _trailer_offset(path) is None


def test_close_failure_stops_writer_without_footer(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    rec = make_recorder(env, path, clock=FakeClock())

    def broken() -> Fingerprint:
        raise RuntimeError("fingerprint failed")

    monkeypatch.setattr(env, "fingerprint", broken)
    with pytest.raises(RuntimeError, match="fingerprint failed"):
        rec.close()
    assert not rec._thread.is_alive()
    assert [r.type for r in _read(path)] == [RecordType.HEADER]


@pytest.mark.parametrize("interrupted", ["ext", "batch"])
def test_interrupt_during_backpressure_keeps_trace_consistent(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch, interrupted: str
) -> None:
    path = tmp_path / "t.simtrace"
    env = Environment(seed=1)
    logged: list[LogEvent] = []  # a backpressure warning, when the simulation blocked, takes a seq too
    env.bus.subscribe(logged.append, (LogEvent,))
    rec = make_recorder(env, path, chunk_limits=ChunkLimits(max_events=2), max_pending_bytes=1, clock=FakeClock())

    @event_type(f"test.trace_rare_{interrupted}")  # first used after the header: needs a CATALOG_EXT
    class Rare(DomainEvent):
        value: int

    gauge = Gauge(env, name="g")
    _drain(rec)
    armed: list[bool] = []  # one KeyboardInterrupt for the next backpressure wait of the simulation thread
    real_wait = rec._cond.wait

    def wait(timeout: float | None = None) -> bool:
        if armed and threading.current_thread() is threading.main_thread() and rec._producer_waiting:
            armed.clear()
            raise KeyboardInterrupt
        return real_wait(timeout)

    monkeypatch.setattr(rec._cond, "wait", wait)
    # Chunks and extensions are written only while the simulation waits unarmed, or at close.
    for rtype in (RecordType.CHUNK, RecordType.CATALOG_EXT):
        _gate_writes(monkeypatch, rec, rtype, lambda: rec._closing or (rec._producer_waiting and not armed))

    def first() -> Any:
        yield env.timeout(1)
        _emit_set(env, gauge, 1.0)
        _emit_set(env, gauge, 2.0)  # sealed; its chunk stays queued
        if interrupted == "ext":
            armed.append(True)
            env.emit(Rare(value=1))  # the CATALOG_EXT wait is interrupted
        else:
            env.emit(Rare(value=1))  # CATALOG_EXT admitted after a regular wait, then held by the gate
            armed.append(True)
            _emit_set(env, gauge, 3.0)  # sealed with Rare; the batch wait is interrupted

    env.process(first())
    env.run()  # the KeyboardInterrupt becomes StopSimulation
    assert env._interrupted
    assert not armed
    if interrupted == "ext":

        def resumed() -> Any:
            env.emit(Rare(value=2))  # its type is known now: its extension must already be queued
            yield env.timeout(0)

        env.process(resumed())
        env.run()
    rec.close()

    records = _read(path)
    footer = records[-1].body
    assert footer["outcome"] == "cancelled"
    chunks = _of(records, RecordType.CHUNK)
    events = [e for c in chunks for e in c.body["events"]]
    assert [e[1] for e in events] == [0, 1, 2, 3]  # every domain event recorded, ordinals without gaps
    seqs = [e[0] for e in events]
    log_seqs = {e.seq for e in logged}
    assert seqs == [seq for seq in range(seqs[0], seqs[-1] + 1) if seq not in log_seqs]
    assert footer["cursor"] == chunks[-1].body["last"]
    index = footer["index"]
    domain_after = {seqs[k]: seqs[k + 1] for k in range(len(seqs) - 1)}  # seqs of consecutive domain events
    assert [i["first"][1] for i in index[1:]] == [domain_after[i["last"][1]] for i in index[:-1]]
    (ext,) = _of(records, RecordType.CATALOG_EXT)
    rare_chunk = next(c for c in chunks if any(e[2] == Rare.type_name for e in c.body["events"]))
    assert records.index(ext) < records.index(rare_chunk)
    assert rare_chunk.body["epoch"] == 1


def test_recorder_reuses_the_digest_encoding(
    tmp_path: Path, make_recorder: Callable[..., TraceRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each event is encoded once: the recorder stores the digest's canonical ``payload, deltas`` tail when the
    event has no presentation field, and encodes the event itself otherwise (here: entity.created's label)."""
    from simulatte.digest import SemanticDigest

    reused: list[str] = []
    real = SemanticDigest.shared_tail

    def spy(self: SemanticDigest, event: DomainEvent) -> bytes | None:
        tail = real(self, event)
        if tail is not None:
            reused.append(type(event).type_name)
        return tail

    monkeypatch.setattr(SemanticDigest, "shared_tail", spy)
    env = Environment(seed=1)
    rec = make_recorder(env, tmp_path / "a.simtrace", clock=FakeClock())
    gauge = Gauge(env, name="g")
    env.activate()
    _emit_set(env, gauge, 1.5, note="n")
    Gauge(env, name="late")
    rec.close()

    assert reused == ["test.trace_set"]
    (chunk,) = _of(_read(tmp_path / "a.simtrace"), RecordType.CHUNK)
    set_event, created = chunk.body["events"]
    assert set_event[2:] == (
        "test.trace_set",
        0.0,
        {"gauge": "g", "level": 1.5, "note": "n"},
        (("set", "g", "level", 1.5),),
    )
    assert created[2] == "entity.created" and created[4]["label"] == "late"
