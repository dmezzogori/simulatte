"""Trace reader (spec §11.1, §11.3): seeking, damage, limits, manifest and verification."""

from __future__ import annotations

import io
import random
import struct
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

import pytest

from simulatte._wire import FrozenMap, pack, unpack
from simulatte.builders import build_immediate_release_system
from simulatte.digest import Fingerprint
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, apply_deltas, event_type
from simulatte.provenance import Provenance
from simulatte.scenario import Scenario
from simulatte.server import Server
from simulatte.trace import ChunkLimits, RecordType, TraceRecorder
from simulatte.trace.format import RECORD_HEADER, TRAILER, write_preamble, write_record
from simulatte.trace.reader import Cursor, ReaderLimits, Trace, TraceCorrupted

# ---------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------


class LiveLog:
    """Collects every domain event of the projection while the simulation runs (independent of the file)."""

    def __init__(self, env: Environment) -> None:
        self.events: list[tuple[Cursor, Deltas]] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        if event.ordinal is not None:  # prelude events are not part of the trajectory
            self.events.append(((float(event.t), event.seq), event.deltas))

    @property
    def cursors(self) -> list[Cursor]:
        return [cursor for cursor, _ in self.events]


def _replay(initial: Any, log: LiveLog, cursor: Cursor) -> dict[str, dict[str, Any]]:
    """Uninterrupted replay: the initial state plus the deltas of every live event at or before `cursor`."""
    state = {entity: dict(fields) for entity, fields in initial.items()}
    for at, deltas in log.events:
        if at > cursor:
            break
        apply_deltas(state, deltas)
    return state


def _record_shop(
    tmp_path: Path,
    *,
    seed: int = 5,
    until: float = 60.0,
    max_events: int = 25,
    level: Literal["full", "kpi"] = "full",
    name: str = "shop.simtrace",
) -> tuple[Path, Environment, LiveLog]:
    path = tmp_path / name
    env = Environment(seed=seed)
    log = LiveLog(env)
    TraceRecorder(env, path, level=level, chunk_limits=ChunkLimits(max_events=max_events))
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
    env.run(until=until)
    env.close()
    return path, env, log


def _bounds(trace: Trace) -> tuple[Cursor, Cursor]:
    bounds = trace.cursor_range
    assert bounds is not None
    return bounds


def _frames(data: bytes) -> list[tuple[int, int, int]]:
    """(offset, type, total size) of every complete record of `data` (the trailer, if any, excluded)."""
    end = len(data) - TRAILER.size if data.endswith(b"SIMTEND\0") else len(data)
    out: list[tuple[int, int, int]] = []
    pos = 12
    while pos + RECORD_HEADER.size <= end:
        length, rtype, _ = RECORD_HEADER.unpack_from(data, pos)
        if pos + RECORD_HEADER.size + length > end:
            break
        out.append((pos, rtype, RECORD_HEADER.size + length))
        pos += RECORD_HEADER.size + length
    return out


def _of(data: bytes, rtype: RecordType) -> list[tuple[int, int, int]]:
    return [f for f in _frames(data) if f[1] == rtype]


def _flip(data: bytes, offset: int) -> bytes:
    return data[:offset] + bytes([data[offset] ^ 0xFF]) + data[offset + 1 :]


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _rewrite_footer(data: bytes, change: Callable[[dict[str, Any]], None]) -> bytes:
    """Re-encode the footer of a complete trace after `change` edited its decoded payload."""
    (offset,) = struct.unpack(">Q", data[-16:-8])
    length = RECORD_HEADER.unpack_from(data, offset)[0]
    footer: Any = unpack(data[offset + 9 : offset + 9 + length])
    body = {k: v for k, v in footer.items()}
    change(body)
    out = io.BytesIO()
    out.write(data[:offset])
    write_record(out, RecordType.FOOTER, pack(FrozenMap(body)))
    out.write(TRAILER.pack(offset, b"SIMTEND\0"))
    return out.getvalue()


def _rebuild_last_chunk(
    data: bytes, change: Callable[[dict[str, Any]], None], *, compressed: bytes | None = None
) -> bytes:
    """A complete trace whose last chunk is re-encoded after `change` edited its body (or is `compressed`)."""
    chunk_offset, _, chunk_size = _of(data, RecordType.CHUNK)[-1]
    index_offset, _, index_size = _of(data, RecordType.INDEX)[-1]
    (footer_offset,) = struct.unpack(">Q", data[-16:-8])
    body: Any = unpack(zlib.decompress(data[chunk_offset + 9 : chunk_offset + chunk_size]))
    edited = {k: v for k, v in body.items()}
    change(edited)
    payload = zlib.compress(pack(FrozenMap(edited))) if compressed is None else compressed
    out = io.BytesIO()
    out.write(data[:chunk_offset])
    length = write_record(out, RecordType.CHUNK, payload)
    entry: Any = unpack(data[index_offset + 9 : index_offset + index_size])
    new_entry = FrozenMap({**entry, "length": length})
    write_record(out, RecordType.INDEX, pack(new_entry))
    out.write(data[index_offset + index_size : footer_offset])
    footer: Any = unpack(data[footer_offset + 9 : len(data) - 16])
    new_footer = FrozenMap({**footer, "index": (*footer["index"][:-1], new_entry)})
    offset = out.tell()
    write_record(out, RecordType.FOOTER, pack(new_footer))
    out.write(TRAILER.pack(offset, b"SIMTEND\0"))
    return out.getvalue()


def _header_only(header: Any) -> bytes:
    out = io.BytesIO()
    write_preamble(out)
    write_record(out, RecordType.HEADER, pack(header))
    return out.getvalue()


class Lamp(Entity, kind="test_reader_lamp"):
    state_schema: ClassVar[StateSchema] = StateSchema(
        {"lit": FieldSpec("bool"), "hue": FieldSpec("str", presentation=True)}
    )

    def __init__(self, env: Environment, *, name: str | None = None) -> None:
        self.lit = False
        self.hue = "white"
        env.entities.attach(self, name=name)


@event_type("test.reader_switched", touches={"test_reader_lamp": ("lit", "hue")}, presentation=frozenset({"why"}))
class Switched(DomainEvent):
    lamp: str
    lit: bool
    why: str = ""


def _switch(env: Environment, lamp: Lamp, lit: bool, *, hue: str | None = None, why: str = "") -> None:
    deltas = Deltas.build().set(lamp.id, "lit", lit)
    lamp.lit = lit
    if hue is not None:
        lamp.hue = hue
        deltas.set(lamp.id, "hue", hue)
    env.emit(Switched(lamp=lamp.id, lit=lit, why=why, deltas=deltas.done()))


# ---------------------------------------------------------------------------------------------------------
# Seeking
# ---------------------------------------------------------------------------------------------------------


def test_state_at_equals_replay_at_chunk_boundaries(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    trace = Trace.open(path)

    assert len(trace.index) >= 5
    assert [e.first for e in trace.index] == sorted(e.first for e in trace.index)
    for entry in trace.index:
        for cursor in (entry.first, entry.last):
            assert trace.state_at(cursor) == _replay(env.initial_state, log, cursor), cursor
    assert trace.cursor_range == ((0.0, -1), (60.0, log.cursors[-1][1]))
    assert trace.state_at(_bounds(trace)[1]) == env.entities.snapshot()
    assert trace.state_at(log.cursors[-1]) == env.entities.snapshot()


def test_state_at_sampled_and_same_time_cursors(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path, seed=11)
    trace = Trace.open(path)
    cursors = log.cursors

    rnd = random.Random(3)
    sampled = rnd.sample(cursors, 40)
    same_time: list[Cursor] = []
    between: list[Cursor] = []
    for before, after in zip(cursors, cursors[1:], strict=False):
        if before[0] == after[0]:
            same_time.append(before)  # a cursor followed by another event at the same time
            if after[1] > before[1] + 1:
                between.append((before[0], before[1] + 1))  # a seq between two same-time events
        else:
            between.append(((before[0] + after[0]) / 2, 0))
    assert len(same_time) >= 20
    for cursor in [*sampled, *rnd.sample(same_time, 20), *rnd.sample(between, 20)]:
        assert trace.state_at(cursor) == _replay(env.initial_state, log, cursor), cursor

    first = trace.state_at(cursors[0])
    first[next(iter(first))]["$kind"] = "mutated"  # results are fresh copies
    assert trace.state_at(cursors[0]) == _replay(env.initial_state, log, cursors[0])


def test_activation_cursor_returns_initial_state(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    trace = Trace.open(path)

    start, end = _bounds(trace)
    assert start == (0.0, -1)
    assert trace.state_at(start) == env.initial_state
    with pytest.raises(ValueError, match="outside"):
        trace.state_at((-1.0, 0))
    with pytest.raises(ValueError, match="outside"):
        trace.state_at((end[0] + 1.0, 0))
    with pytest.raises(ValueError, match="cursor"):
        trace.state_at(cast(Any, (1.0,)))


def test_trace_without_domain_events_is_seekable(tmp_path: Path) -> None:
    path = tmp_path / "quiet.simtrace"
    env = Environment(seed=1)
    TraceRecorder(env, path)
    Server(env=env, capacity=1, name="lathe")
    env.activate()
    env.close()

    trace = Trace.open(path)
    assert trace.cursor_range == ((0.0, -1), (0.0, -1))
    assert trace.state_at((0.0, -1)) == env.initial_state
    assert trace.index == ()
    assert list(trace.events()) == []
    assert trace.outcome == "completed"
    assert trace.verify() is True
    trace.check()


def test_events_between_cursors(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    trace = Trace.open(path)

    everything = list(trace.events())
    assert [(e.t, e.seq) for e in everything] == log.cursors
    assert [e.ordinal for e in everything] == list(range(len(everything)))
    start, end = log.cursors[30], log.cursors[90]
    window = list(trace.events(start, end))
    assert [(e.t, e.seq) for e in window] == log.cursors[31:91]  # (start, end]: from state_at(start) to state_at(end)
    state = trace.state_at(start)
    for event in window:
        apply_deltas(state, Deltas(event.deltas))
    assert state == trace.state_at(end)
    assert [(e.t, e.seq) for e in trace.events(end=log.cursors[2])] == log.cursors[:3]
    boundary = trace.index[0].last
    assert [(e.t, e.seq) for e in trace.events(end=boundary)] == [c for c in log.cursors if c <= boundary]
    assert [(e.t, e.seq) for e in trace.events(start=log.cursors[-2])] == log.cursors[-1:]
    assert isinstance(everything[0].payload, FrozenMap)


# ---------------------------------------------------------------------------------------------------------
# Damage
# ---------------------------------------------------------------------------------------------------------


def test_incomplete_tail_is_truncated(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    data = path.read_bytes()
    full = Trace.open(path)
    assert not full.truncated
    footer_offset = _frames(data)[-1][0]
    chunks = _of(data, RecordType.CHUNK)
    indexes = _of(data, RecordType.INDEX)

    # A partial trailer: the footer record is intact, the remaining bytes are an incomplete tail.
    partial_trailer = Trace.open(_write(tmp_path, "a", data[:-5]))
    assert partial_trailer.truncated
    assert partial_trailer.outcome == "completed"
    assert partial_trailer.index == full.index

    # A cut inside the footer: the footer is lost, every committed chunk stays visible.
    no_footer = Trace.open(_write(tmp_path, "b", data[: footer_offset + 20]))
    assert no_footer.truncated
    assert no_footer.outcome is None
    assert no_footer.fingerprint is None
    assert not no_footer.manifest_final
    assert no_footer.index == full.index
    assert no_footer.cursor_range == ((0.0, -1), full.index[-1].last)
    assert no_footer.verify() == "not_verifiable"

    # A cut inside the last chunk: the chunk is ignored.
    offset = chunks[-1][0]
    cut = Trace.open(_write(tmp_path, "c", data[: offset + 40]))
    assert cut.truncated
    assert cut.index == full.index[:-1]
    last = _bounds(cut)[1]
    assert last == full.index[-2].last
    assert cut.state_at(last) == _replay(env.initial_state, log, last)
    cut.check()

    # A cut inside a record header.
    header_cut = Trace.open(_write(tmp_path, "d", data[: offset + 4]))
    assert header_cut.truncated
    assert header_cut.index == full.index[:-1]

    # A complete last record whose CRC fails: the committing index of the last chunk.
    index_offset, _, index_size = indexes[-1]
    damaged = _flip(data[: index_offset + index_size], index_offset + 12)
    crc_tail = Trace.open(_write(tmp_path, "e", damaged))
    assert crc_tail.truncated
    assert crc_tail.index == full.index[:-1]


def test_interior_corruption_raises(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    data = path.read_bytes()
    chunks = _of(data, RecordType.CHUNK)
    indexes = _of(data, RecordType.INDEX)
    footer_offset = _frames(data)[-1][0]
    middle = chunks[len(chunks) // 2][0]

    # With a footer the chunk is read lazily: seeking into it and check() detect the damage.
    damaged = Trace.open(_write(tmp_path, "a", _flip(data, middle + 30)))
    entry = next(e for e in damaged.index if e.offset == middle)
    with pytest.raises(TraceCorrupted, match="CRC"):
        damaged.state_at(entry.last)
    with pytest.raises(TraceCorrupted, match="CRC"):
        damaged.check()
    with pytest.raises(TraceCorrupted, match="CRC"):
        damaged.verify()
    with pytest.raises(TraceCorrupted, match="CRC"):
        list(damaged.events())

    # Without a footer the reader scans the file: damage followed by valid records is corruption.
    no_footer = data[:footer_offset]
    with pytest.raises(TraceCorrupted, match="CRC"):
        Trace.open(_write(tmp_path, "b", _flip(no_footer, middle + 30)))
    index_offset = indexes[len(indexes) // 2][0]
    with pytest.raises(TraceCorrupted, match="CRC"):
        Trace.open(_write(tmp_path, "c", _flip(no_footer, index_offset + 12)))

    # A damaged footer behind a valid trailer, and a damaged header.
    with pytest.raises(TraceCorrupted, match="footer"):
        Trace.open(_write(tmp_path, "d", _flip(data, footer_offset + 12)))
    with pytest.raises(TraceCorrupted, match="CRC"):
        Trace.open(_write(tmp_path, "e", _flip(data, 12 + 12)))
    with pytest.raises(TraceCorrupted, match="not a simulatte trace"):
        Trace.open(_write(tmp_path, "f", b"NOTATRACE" + data[9:]))
    with pytest.raises(TraceCorrupted, match="header"):
        Trace.open(_write(tmp_path, "g", data[:12]))


def test_inconsistent_index_is_corruption(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    data = path.read_bytes()
    chunks = _of(data, RecordType.CHUNK)
    footer_offset = _frames(data)[-1][0]

    def beyond(body: dict[str, Any]) -> None:
        entry = dict(body["index"][-1])
        entry["offset"] = footer_offset + 100
        body["index"] = (*body["index"], FrozenMap(entry))

    with pytest.raises(TraceCorrupted, match="index"):
        Trace.open(_write(tmp_path, "a", _rewrite_footer(data, beyond)))

    def unsorted(body: dict[str, Any]) -> None:
        body["index"] = tuple(reversed(body["index"]))

    with pytest.raises(TraceCorrupted, match="index"):
        Trace.open(_write(tmp_path, "b", _rewrite_footer(data, unsorted)))

    def bad_cursor(body: dict[str, Any]) -> None:
        entry = dict(body["index"][0])
        entry["first"] = (1e9, 0)
        body["index"] = (FrozenMap(entry), *body["index"][1:])

    with pytest.raises(TraceCorrupted, match="index"):
        Trace.open(_write(tmp_path, "c", _rewrite_footer(data, bad_cursor)))

    def shifted(body: dict[str, Any]) -> None:
        entry = dict(body["index"][0])
        entry["last"] = entry["first"]
        body["index"] = (FrozenMap(entry), *body["index"][1:])

    # The entry passes the open-time checks; loading the chunk finds cursors that disagree with it.
    mismatched = Trace.open(_write(tmp_path, "d", _rewrite_footer(data, shifted)))
    with pytest.raises(TraceCorrupted, match="index"):
        mismatched.state_at(mismatched.index[0].last)
    with pytest.raises(TraceCorrupted):
        mismatched.check()

    # The last entry is checked against the INDEX records at open; earlier ones only when their chunk is read.
    index_offset, _, index_size = _of(data, RecordType.INDEX)[0]

    def not_a_chunk(body: dict[str, Any]) -> None:
        entry = dict(body["index"][0])
        entry["offset"] = index_offset  # the INDEX record that commits it
        entry["length"] = index_size - 9  # short enough to pass the open-time layout checks
        body["index"] = (FrozenMap(entry), *body["index"][1:])

    wrong_type = Trace.open(_write(tmp_path, "e", _rewrite_footer(data, not_a_chunk)))
    with pytest.raises(TraceCorrupted, match="CHUNK"):
        wrong_type.state_at(wrong_type.index[0].last)

    def wrong_length(body: dict[str, Any]) -> None:
        entry = dict(body["index"][0])
        entry["length"] = entry["length"] - 1
        body["index"] = (FrozenMap(entry), *body["index"][1:])

    short = Trace.open(_write(tmp_path, "e2", _rewrite_footer(data, wrong_length)))
    with pytest.raises(TraceCorrupted, match="length"):
        short.state_at(short.index[0].last)

    def last_moved(body: dict[str, Any]) -> None:
        entry = dict(body["index"][-1])
        entry["length"] = entry["length"] - 1
        body["index"] = (*body["index"][:-1], FrozenMap(entry))

    with pytest.raises(TraceCorrupted, match="footer index"):
        Trace.open(_write(tmp_path, "e3", _rewrite_footer(data, last_moved)))

    def malformed(body: dict[str, Any]) -> None:
        body["index"] = "nope"

    with pytest.raises(TraceCorrupted, match="malformed"):
        Trace.open(_write(tmp_path, "f", _rewrite_footer(data, malformed)))

    def bad_epoch(body: dict[str, Any]) -> None:
        body["epochs"] = (12, chunks[0][0])

    with pytest.raises(TraceCorrupted, match="CATALOG_EXT"):
        Trace.open(_write(tmp_path, "g", _rewrite_footer(data, bad_epoch)))

    # A footerless trace whose INDEX does not commit the chunk before it.
    no_footer = bytearray(data[:footer_offset])
    index_offset, _, index_size = _of(data, RecordType.INDEX)[0]
    entry: Any = unpack(bytes(no_footer[index_offset + 9 : index_offset + index_size]))
    forged = pack(FrozenMap({**entry, "offset": entry["offset"] + 1}))
    assert len(forged) == index_size - 9
    no_footer[index_offset : index_offset + index_size] = RECORD_HEADER.pack(
        len(forged), RecordType.INDEX, zlib.crc32(forged)
    ) + bytes(forged)
    with pytest.raises(TraceCorrupted, match="INDEX"):
        Trace.open(_write(tmp_path, "h", bytes(no_footer)))


def test_chunk_without_index_invisible(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    data = path.read_bytes()
    full = Trace.open(path)
    offset, _, size = _of(data, RecordType.CHUNK)[-1]

    trace = Trace.open(_write(tmp_path, "uncommitted", data[: offset + size]))
    assert not trace.truncated  # every record is intact; the last chunk has no committing index yet
    assert trace.outcome is None
    assert trace.index == full.index[:-1]
    assert _bounds(trace)[1] == full.index[-2].last
    assert [(e.t, e.seq) for e in trace.events()] == [c for c in log.cursors if c <= full.index[-2].last]
    trace.check()


# ---------------------------------------------------------------------------------------------------------
# Header, features and limits
# ---------------------------------------------------------------------------------------------------------


def test_unknown_required_feature_refused(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    data = path.read_bytes()
    offset, _, size = _frames(data)[0]
    header: Any = unpack(data[offset + 9 : offset + size])

    def with_features(required: tuple[str, ...], optional: tuple[str, ...]) -> Any:
        return FrozenMap({**header, "features": FrozenMap({"required": required, "optional": optional})})

    required = header["features"]["required"]
    refused = _write(tmp_path, "a", _header_only(with_features((*required, "teleport-v9"), ())))
    with pytest.raises(ValueError, match="teleport-v9"):
        Trace.open(refused)

    accepted = Trace.open(_write(tmp_path, "b", _header_only(with_features(required, ("sparkles-v2",)))))
    assert accepted.outcome is None
    assert accepted.cursor_range is None  # never activated: no initial state
    assert accepted.manifest["seed"] == "5"
    with pytest.raises(ValueError, match="initial state"):
        accepted.state_at((0.0, -1))
    assert accepted.verify() == "not_verifiable"

    unknown_record = io.BytesIO()
    write_record(unknown_record, 99, pack(FrozenMap({})))  # a record type of a later minor version: skipped
    footer_offset = _frames(data)[-1][0]
    skipped = Trace.open(_write(tmp_path, "d", data[:footer_offset] + unknown_record.getvalue()))
    assert skipped.index == Trace.open(path).index
    assert not skipped.truncated

    future = bytearray(data)
    future[8:10] = struct.pack(">H", 2)
    with pytest.raises(ValueError, match="format 2"):
        Trace.open(_write(tmp_path, "c", bytes(future)))


def test_limits_enforced(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    assert Trace.open(path, limits=ReaderLimits()).index

    with pytest.raises(TraceCorrupted, match="max_record"):
        Trace.open(path, limits=ReaderLimits(max_record=100))
    with pytest.raises(TraceCorrupted, match="depth"):
        Trace.open(path, limits=ReaderLimits(max_depth=2))
    with pytest.raises(TraceCorrupted, match="len"):
        Trace.open(path, limits=ReaderLimits(max_len=3))

    small_chunks = Trace.open(path, limits=ReaderLimits(max_chunk=64))
    with pytest.raises(TraceCorrupted, match="max_chunk"):
        small_chunks.state_at(small_chunks.index[0].last)

    data = path.read_bytes()
    footer_offset = _frames(data)[-1][0]

    def too_many(body: dict[str, Any]) -> None:
        body["index"] = body["index"] * (len(data) // 18 + 1)

    with pytest.raises(TraceCorrupted, match="index"):
        Trace.open(_write(tmp_path, "many", _rewrite_footer(data, too_many)))

    garbage = _write(tmp_path, "garbage", data[:footer_offset] + RECORD_HEADER.pack(1 << 30, 5, 0) + b"x" * 64)
    with pytest.raises(TraceCorrupted, match="max_record"):
        Trace.open(garbage)

    for bad in ({"max_record": 0}, {"max_chunk": 0}, {"max_depth": 0}, {"max_len": 0}):
        with pytest.raises(ValueError, match=next(iter(bad))):
            ReaderLimits(**bad)


# ---------------------------------------------------------------------------------------------------------
# Verification, manifest, catalog and KPIs
# ---------------------------------------------------------------------------------------------------------


def test_verify_full_and_kpi_not_verifiable(tmp_path: Path) -> None:
    path, env, _ = _record_shop(tmp_path)
    trace = Trace.open(path)
    assert trace.verify() is True
    assert trace.fingerprint == Fingerprint(digest=env.fingerprint().digest, kpis={})
    trace.check()

    def wrong_digest(body: dict[str, Any]) -> None:
        body["fingerprint"] = FrozenMap({**body["fingerprint"], "digest": "0" * 64})

    tampered = Trace.open(_write(tmp_path, "tampered", _rewrite_footer(path.read_bytes(), wrong_digest)))
    assert tampered.verify() is False

    kpi_path, kpi_env, _ = _record_shop(tmp_path, level="kpi", name="kpi.simtrace")
    kpi = Trace.open(kpi_path)
    assert kpi.verify() == "not_verifiable"
    assert kpi.index == ()
    assert kpi.fingerprint is not None and kpi.fingerprint.digest == kpi_env.fingerprint().digest
    assert kpi.fingerprint.digest == env.fingerprint().digest  # the same run recorded at another level
    kpi.check()


def test_verify_uses_trace_catalog_and_drops_presentation(tmp_path: Path) -> None:
    path = tmp_path / "lamps.simtrace"
    env = Environment(seed=2)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=2))

    @event_type("test.reader_late", presentation=frozenset({"remark"}))
    class Late(DomainEvent):
        value: int
        remark: str = ""

    @event_type("test.reader_early")
    class Early(DomainEvent):
        value: int

    lamp = Lamp(env, name="lamp")
    env.activate()
    env.emit(Early(value=1))  # its catalog extension precedes the first chunk
    _switch(env, lamp, True, hue="red", why="dusk")
    _switch(env, lamp, False)
    _switch(env, lamp, True)
    env.emit(Late(value=4, remark="first use after the header, between two chunks"))
    _switch(env, lamp, False, why="dawn")
    _switch(env, lamp, True, hue="blue")
    Lamp(env, name="spare")
    env.close()

    data = path.read_bytes()
    early_offset, ext_offset = (offset for offset, _, _ in _of(data, RecordType.CATALOG_EXT))
    chunk_offsets = [offset for offset, _, _ in _of(data, RecordType.CHUNK)]
    assert early_offset < chunk_offsets[0] < ext_offset < chunk_offsets[-1]  # read through the epoch offsets
    trace = Trace.open(path)
    assert trace.level == "full"
    assert {"test.reader_early", "test.reader_late"} <= set(trace.catalog)
    assert trace.catalog["test.reader_late"].version == 1
    assert trace.verify() is True
    assert trace.state_at(_bounds(trace)[1]) == env.entities.snapshot()

    no_footer = Trace.open(_write(tmp_path, "nf", data[: _frames(data)[-1][0]]))
    assert "test.reader_late" in no_footer.catalog
    with pytest.raises(TraceCorrupted, match="CATALOG_EXT"):
        Trace.open(_write(tmp_path, "bad-ext", _flip(data, ext_offset + 12)))


def test_never_activated_trace(tmp_path: Path) -> None:
    path = tmp_path / "idle.simtrace"
    env = Environment(seed=1)
    TraceRecorder(env, path)
    env.close()

    trace = Trace.open(path)
    assert trace.outcome == "completed"
    assert trace.cursor_range is None
    assert not trace.manifest_final
    assert trace.verify() is True  # the digest of nothing
    trace.check()


def test_reopened_manifest_after_two_runs(tmp_path: Path) -> None:
    path = tmp_path / "two.simtrace"
    full = Provenance(model="m", source="s", inputs="i", dependencies="d")
    env = Environment(seed=8, time_unit="minute", provenance=full)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=30))
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=2))
    env.run(until=10)
    env.run(until=20)
    env.close()

    trace = Trace.open(path)
    manifest = trace.manifest
    assert trace.manifest_final is True
    assert manifest == env.manifest().merged()
    assert manifest["seed"] == "8"
    assert manifest["simulatte_version"] == env.manifest().requested["simulatte_version"]
    assert manifest["python"] == env.manifest().requested["python"]
    assert manifest["time_unit"] == "minute"
    assert manifest["stopping_policy"] == {"type": "horizon", "horizon": 20.0}
    assert manifest["complete"] is True
    assert trace.outcome == "completed"

    data = path.read_bytes()
    reopened = Trace.open(_write(tmp_path, "nf", data[: _frames(data)[-1][0]]))
    assert reopened.manifest_final is False
    assert reopened.manifest == env.manifest().requested
    assert "stopping_policy" not in reopened.manifest


def test_kpis_from_kpi_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "kpi.simtrace"
    env = Environment(seed=1)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=3))
    lamp = Lamp(env, name="lamp")
    env.activate()
    for i in range(5):
        _switch(env, lamp, i % 2 == 0)
    digest = env.fingerprint().digest
    monkeypatch.setattr(env, "fingerprint", lambda: Fingerprint(digest=digest, kpis={"shop/flow_time": 2.5}))
    env.close()

    trace = Trace.open(path)
    assert trace.kpis() == {"shop/flow_time": 2.5}
    assert trace.fingerprint == Fingerprint(digest=digest, kpis={"shop/flow_time": 2.5})
    assert trace.verify() is True
    data = path.read_bytes()
    assert Trace.open(_write(tmp_path, "nf", data[: _frames(data)[-1][0]])).kpis() == {"shop/flow_time": 2.5}


def test_damaged_chunk_contents(tmp_path: Path) -> None:
    path, env, log = _record_shop(tmp_path)
    data = path.read_bytes()
    assert Trace.open(_write(tmp_path, "same", _rebuild_last_chunk(data, lambda body: None))).verify() is True

    def opened(name: str, rebuilt: bytes) -> Trace:
        trace = Trace.open(_write(tmp_path, name, rebuilt))
        return trace

    garbage = opened("zlib", _rebuild_last_chunk(data, lambda body: None, compressed=b"not zlib data"))
    with pytest.raises(TraceCorrupted, match="zlib"):
        garbage.state_at(garbage.index[-1].last)
    whole = zlib.compress(path.read_bytes()[:64])
    for name, payload in (("cut", whole[:-4]), ("extra", whole + b"xx")):
        bad = opened(name, _rebuild_last_chunk(data, lambda body: None, compressed=payload))
        with pytest.raises(TraceCorrupted, match="complete zlib stream"):
            bad.state_at(bad.index[-1].last)

    def unknown_type(body: dict[str, Any]) -> None:
        events = list(body["events"])
        events[-1] = (*events[-1][:2], "test.never_registered", *events[-1][3:])
        body["events"] = tuple(events)

    with pytest.raises(TraceCorrupted, match="not in the trace catalog"):
        opened("type", _rebuild_last_chunk(data, unknown_type)).verify()

    def ghost_deltas(body: dict[str, Any]) -> None:
        events = list(body["events"])
        events[-1] = (*events[-1][:5], (("set", "ghost", "x", 1),))
        body["events"] = tuple(events)

    ghost = opened("ghost", _rebuild_last_chunk(data, ghost_deltas))
    with pytest.raises(TraceCorrupted, match="do not apply"):
        ghost.state_at(ghost.index[-1].last)

    def short_event(body: dict[str, Any]) -> None:
        body["events"] = (body["events"][0][:5], *body["events"][1:])

    malformed = opened("short", _rebuild_last_chunk(data, short_event))
    with pytest.raises(TraceCorrupted, match="malformed chunk"):
        malformed.state_at(malformed.index[-1].last)

    def stale_snapshot(body: dict[str, Any]) -> None:
        snapshot = dict(body["snapshot"])
        snapshot.pop(next(iter(snapshot)))
        body["snapshot"] = FrozenMap(snapshot)

    stale = opened("stale", _rebuild_last_chunk(data, stale_snapshot))
    with pytest.raises(TraceCorrupted, match="snapshot"):
        stale.check()


def test_damaged_framing(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    data = path.read_bytes()
    footer_offset = _frames(data)[-1][0]
    index_offset, _, index_size = _of(data, RecordType.INDEX)[-1]

    with pytest.raises(TraceCorrupted, match="shorter than the preamble"):
        Trace.open(_write(tmp_path, "tiny", b"SIM"))
    moved = data[:-16] + TRAILER.pack(footer_offset + 1, b"SIMTEND\0")
    with pytest.raises(TraceCorrupted, match="trailer"):
        Trace.open(_write(tmp_path, "moved", moved))

    # Record lengths are outside the CRC: a stretched length before the footer is corruption.
    stretched = bytearray(data)
    stretched[index_offset : index_offset + 4] = struct.pack(">I", index_size + 1000)
    with pytest.raises(TraceCorrupted, match="cut short"):
        Trace.open(_write(tmp_path, "stretched", bytes(stretched)))

    # A CRC-failing last record followed by bytes that are not a record is still an incomplete tail.
    damaged = _flip(data[: index_offset + index_size], index_offset + 12) + b"\xff" * 20
    assert Trace.open(_write(tmp_path, "tail", damaged)).truncated

    initial_first = io.BytesIO()
    write_preamble(initial_first)
    write_record(initial_first, RecordType.INITIAL, pack(FrozenMap({})))
    with pytest.raises(TraceCorrupted, match="first record is not a HEADER"):
        Trace.open(_write(tmp_path, "initial-first", initial_first.getvalue()))

    offset, _, size = _frames(data)[0]
    header: Any = unpack(data[offset + 9 : offset + size])
    no_level = FrozenMap({k: v for k, v in header.items() if k != "level"})
    with pytest.raises(TraceCorrupted, match="malformed header"):
        Trace.open(_write(tmp_path, "no-level", _header_only(no_level)))

    def no_outcome(body: dict[str, Any]) -> None:
        del body["outcome"]

    with pytest.raises(TraceCorrupted, match="malformed footer"):
        Trace.open(_write(tmp_path, "no-outcome", _rewrite_footer(data, no_outcome)))


def test_adjacent_damaged_records_before_valid_ones_are_corruption(tmp_path: Path) -> None:
    path, _, _ = _record_shop(tmp_path)
    data = path.read_bytes()
    no_footer = data[: _frames(data)[-1][0]]
    chunks = _of(no_footer, RecordType.CHUNK)
    indexes = _of(no_footer, RecordType.INDEX)
    k = len(chunks) // 2
    chunk_offset, index_offset = chunks[k][0], indexes[k][0]
    assert index_offset == chunk_offset + chunks[k][2]  # the chunk and its committing index are adjacent

    # One bad block spanning a CHUNK and its INDEX: two damaged records, then valid ones.
    both = _flip(_flip(no_footer, chunk_offset + 30), index_offset + 12)
    with pytest.raises(TraceCorrupted, match="valid records follow"):
        Trace.open(_write(tmp_path, "both", both))

    # Damaged records followed only by well-framed garbage and then a cut frame remain an incomplete tail.
    garbage_record = RECORD_HEADER.pack(4, 99, 0) + b"junk"  # framed, CRC wrong
    tail = _flip(_flip(no_footer[: index_offset + indexes[k][2]], chunk_offset + 30), index_offset + 12)
    trace = Trace.open(_write(tmp_path, "tail", tail + garbage_record + b"\x00\x00\x00"))
    assert trace.truncated
    assert len(trace.index) == k
