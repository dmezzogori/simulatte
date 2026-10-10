"""Trace reader: :class:`Trace` (spec §11.1, §11.3).

:meth:`Trace.open` reads the header, the initial state, the chunk index and the footer of a trace file. When
the file ends with a valid trailer, the index comes from the footer and chunks are read only when a cursor
needs them; otherwise the reader scans every record. Damage follows the rules of spec §11.1: a short or
CRC-failing record with no valid record anywhere after it is an incomplete tail (:attr:`Trace.truncated`) and
is ignored, a failing record followed by valid records raises :class:`TraceCorrupted`, and a chunk is visible
only once the ``INDEX`` record that commits it follows it. :class:`ReaderLimits` bounds what an untrusted
file can make the reader allocate.

A *cursor* is ``(t, seq)``. :meth:`Trace.state_at` returns the replay state after every domain event whose
cursor is at or before the given one; the activation cursor ``(t_activation, -1)`` denotes the initial state.
"""

from __future__ import annotations

import os
import zlib
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Literal, NamedTuple, TypeAlias, TypeVar

from simulatte._wire import MAX_SAFE_INT, FrozenMap, Wire, unpack, wire_equal
from simulatte.digest import DigestAccumulator, Fingerprint
from simulatte.entities import StateSchema
from simulatte.events import CatalogEntry, Deltas, Op, apply_deltas
from simulatte.trace.format import (
    FORMAT_MAJOR,
    MAGIC,
    PREAMBLE,
    RECORD_HEADER,
    REQUIRED_FEATURES,
    TRAILER,
    TRAILER_MAGIC,
    RecordType,
)

__all__ = ["ChunkInfo", "Cursor", "KpiPoint", "ReaderLimits", "Trace", "TraceCorrupted", "TraceEvent"]

Cursor: TypeAlias = tuple[float, int]
"""A position in a trace: ``(t, seq)``."""

KpiPoint: TypeAlias = tuple[Cursor, float]
"""A KPI sample: its cursor ``(t, seq)`` and its value."""

State: TypeAlias = dict[str, dict[str, Wire]]
"""Replay state: entity id to field values, each with the entity kind under ``"$kind"``."""

_FRAME = RECORD_HEADER.size
_CACHED_CHUNKS = 4
_MALFORMED = (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError)
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ReaderLimits:
    """Bounds enforced while reading a trace (spec §11.1); raise them for trusted local files.

    `max_record` bounds the payload of any record, `max_chunk` the decompressed size of a chunk, `max_depth`
    the nesting of decoded values and `max_len` the length of any decoded array or map.
    """

    max_record: int = 64 << 20
    max_chunk: int = 256 << 20
    max_depth: int = 64
    max_len: int = 10**7

    def __post_init__(self) -> None:
        for name in ("max_record", "max_chunk", "max_depth", "max_len"):
            if getattr(self, name) < 1:
                raise ValueError(f"ReaderLimits.{name} must be >= 1, got {getattr(self, name)}")


class TraceCorrupted(Exception):
    """The trace file is damaged beyond an incomplete tail, inconsistent, or exceeds the reader limits."""


@dataclass(frozen=True, slots=True)
class ChunkInfo:
    """An entry of the chunk index: where the ``CHUNK`` record is and which events it holds.

    `length` is the size of the record including its framing.
    """

    offset: int
    length: int
    first: Cursor
    last: Cursor
    t_start: float
    t_end: float
    epoch: int


class TraceEvent(NamedTuple):
    """A recorded domain event: ``[seq, ordinal, type, t, payload, deltas]`` as stored in a chunk."""

    seq: int
    ordinal: int
    type: str
    t: float
    payload: Mapping[str, Wire]
    deltas: tuple[Op, ...]


@dataclass(slots=True)
class _Chunk:
    snapshot: Mapping[str, Mapping[str, Wire]]
    events: tuple[TraceEvent, ...]
    cursors: list[Cursor]


@dataclass(slots=True)
class _Scan:
    """What a sequential pass over records found."""

    header: Any = None
    initial: Any = None
    exts: dict[int, Any] = field(default_factory=dict)  # offset -> decoded CATALOG_EXT payload
    index: list[ChunkInfo] = field(default_factory=list)
    kpis: list[Any] = field(default_factory=list)
    footer: Any = None
    truncated: bool = False
    stop: int = 0  # offset where the pass stopped


class _File:
    """Framed record access to an open trace file."""

    __slots__ = ("f", "limits", "size")

    def __init__(self, f: BinaryIO, size: int, limits: ReaderLimits) -> None:
        self.f = f
        self.size = size
        self.limits = limits

    def read(self, offset: int, n: int) -> bytes:
        self.f.seek(offset)
        return self.f.read(n)

    def frame(self, offset: int, end: int) -> tuple[int, int, int] | None:
        """``(type, payload length, crc)`` of the record at `offset`, or None if `end` cuts it short."""
        if offset + _FRAME > end:
            return None
        length, rtype, crc = RECORD_HEADER.unpack(self.read(offset, _FRAME))
        if length > self.limits.max_record:
            raise TraceCorrupted(
                f"record at offset {offset} has a {length}-byte payload, above max_record={self.limits.max_record}"
            )
        if offset + _FRAME + length > end:
            return None
        return rtype, length, crc

    def payload(self, offset: int, length: int, crc: int) -> bytes | None:
        """The payload of the record at `offset`, or None if it fails its CRC check."""
        data = self.read(offset + _FRAME, length)
        return data if zlib.crc32(data) == crc else None

    def valid_record_from(self, offset: int, end: int) -> bool:
        """Whether a CRC-valid record follows at `offset` or after further damaged but well-framed records.

        The walk stops at the first frame that is cut short by `end` or exceeds `max_record`.
        """
        while True:
            try:
                frame = self.frame(offset, end)
            except TraceCorrupted:
                return False
            if frame is None:
                return False
            if self.payload(offset, frame[1], frame[2]) is not None:
                return True
            offset += _FRAME + frame[1]

    def decode(self, data: bytes, what: str) -> Any:
        try:
            return unpack(data, max_depth=self.limits.max_depth, max_len=self.limits.max_len)
        except ValueError as exc:
            raise TraceCorrupted(f"cannot decode {what}: {exc}") from exc


class Trace:
    """A trace file opened for reading; create it with :meth:`open`.

    The reader keeps the file closed between calls and reads chunks on demand, caching the last few.
    """

    def __init__(self, path: str | Path, limits: ReaderLimits) -> None:
        self._path = Path(path)
        self._limits = limits
        self._cache: OrderedDict[int, _Chunk] = OrderedDict()
        self._footer_offset: int | None = None
        self._footer: Any = None
        self._kpi_records: list[Any] = []
        self._exts: dict[int, Any] = {}
        with open(self._path, "rb") as f:
            file = _File(f, os.fstat(f.fileno()).st_size, limits)
            self._size = file.size
            self._open(file)

    @classmethod
    def open(cls, path: str | Path, *, limits: ReaderLimits | None = None) -> Trace:
        """Open the trace at `path`.

        Raises :class:`TraceCorrupted` for damaged or inconsistent files and files above `limits`, and
        `ValueError` for a format version or required feature this reader does not support.
        """
        return cls(path, ReaderLimits() if limits is None else limits)

    # -------------------------------------------------------------------------
    # Opening
    # -------------------------------------------------------------------------

    def _open(self, file: _File) -> None:
        if file.size < PREAMBLE.size:
            raise TraceCorrupted("not a simulatte trace: the file is shorter than the preamble")
        magic, major, minor = PREAMBLE.unpack(file.read(0, PREAMBLE.size))
        if magic != MAGIC:
            raise TraceCorrupted("not a simulatte trace: bad magic bytes")
        if major != FORMAT_MAJOR:
            raise ValueError(f"unsupported trace format {major}.{minor}; this reader reads format {FORMAT_MAJOR}.x")

        footer_offset = self._locate_footer(file)
        if footer_offset is None:
            scan = self._scan(file, PREAMBLE.size, file.size, head_only=False, damage_is_tail=True)
            self._take_head(scan)
            self._index = tuple(scan.index)
            _check_index(self._index, PREAMBLE.size, scan.stop)
            self._exts = scan.exts
            self._kpi_records = scan.kpis
            self._footer = scan.footer
            # A footer without its trailer: the trailer is missing or cut, an incomplete tail.
            self.truncated = scan.truncated or scan.footer is not None
        else:
            self._footer_offset = footer_offset
            head = self._scan(file, PREAMBLE.size, footer_offset, head_only=True, damage_is_tail=False)
            self._take_head(head)
            try:
                index = tuple(_chunk_info(entry) for entry in self._footer["index"])
                epochs = [_wire_int(offset) for offset in self._footer["epochs"]]
            except _MALFORMED as exc:
                raise TraceCorrupted(f"malformed footer: {exc!r}") from exc
            _check_index(index, head.stop, footer_offset)
            self._index = index
            # The tail holds the last chunk, its index, KPI records and late catalog extensions.
            tail_start = index[-1].offset if index else head.stop
            tail = self._scan(file, tail_start, footer_offset, head_only=False, damage_is_tail=False)
            if tail.index != list(index[-1:]):
                raise TraceCorrupted("the footer index disagrees with the INDEX records at the end of the file")
            self._exts = {**head.exts, **tail.exts}
            for offset in epochs[1:]:
                if offset not in self._exts:
                    self._exts[offset] = self._read_ext(file, offset, footer_offset)
            # KPI sample records may also sit between earlier chunks.
            self._kpi_records = self._read_kpis(file, head.stop, tail_start) + tail.kpis
            self.truncated = False
        footer = self._footer
        if footer is not None:
            _decoded(
                lambda: (
                    str(footer["outcome"]),
                    _wire_cursor(footer["cursor"]),
                    footer["manifest"] is None or FrozenMap(footer["manifest"]),
                    dict(footer["fingerprint"]["kpis"]),
                    footer["fingerprint"]["digest"],
                ),
                "footer",
            )
        self._build_catalog()
        self._firsts = [info.first for info in self._index]

    def _locate_footer(self, file: _File) -> int | None:
        """The offset of the footer named by a valid trailer; None if the file has no trailer."""
        size = file.size
        if size < PREAMBLE.size + TRAILER.size or file.read(size - len(TRAILER_MAGIC), 8) != TRAILER_MAGIC:
            return None
        offset, _ = TRAILER.unpack(file.read(size - TRAILER.size, TRAILER.size))
        end = size - TRAILER.size
        frame = file.frame(offset, end) if PREAMBLE.size <= offset < end else None
        if frame is None or frame[0] != RecordType.FOOTER or offset + _FRAME + frame[1] != end:
            raise TraceCorrupted("the trailer does not point at a footer record")
        data = file.payload(offset, frame[1], frame[2])
        if data is None:
            raise TraceCorrupted("the footer record fails its CRC check")
        self._footer = file.decode(data, "the footer")
        return offset

    def _scan(self, file: _File, start: int, end: int, *, head_only: bool, damage_is_tail: bool) -> _Scan:
        """Read records from `start` to `end`, checking CRCs; `head_only` stops at the first chunk-era record.

        With `damage_is_tail`, a short record, or a CRC-failing record with no CRC-valid record anywhere after
        it (walking the frames that follow, damaged or not, until one is cut short or exceeds `max_record`),
        ends the pass as an incomplete tail; otherwise any damage raises.
        """
        scan = _Scan()
        pending: tuple[int, int] | None = None  # (offset, length) of a chunk awaiting its INDEX
        pos = start
        while pos < end:
            frame = file.frame(pos, end)
            if frame is None:
                if not damage_is_tail:
                    raise TraceCorrupted(f"record at offset {pos} is cut short")
                scan.truncated = True
                break
            rtype, length, crc = frame
            if head_only and rtype in (RecordType.CHUNK, RecordType.INDEX, RecordType.KPI, RecordType.FOOTER):
                break
            record_end = pos + _FRAME + length
            data = file.payload(pos, length, crc)
            if data is None:
                if not damage_is_tail or file.valid_record_from(record_end, end):
                    raise TraceCorrupted(f"record at offset {pos} fails its CRC check and valid records follow it")
                scan.truncated = True
                break
            if pos == PREAMBLE.size and rtype != RecordType.HEADER:
                raise TraceCorrupted("the first record is not a HEADER")
            if rtype == RecordType.CHUNK:
                pending = (pos, _FRAME + length)
            elif rtype == RecordType.INDEX:
                info = _decoded(lambda: _chunk_info(file.decode(data, "an INDEX record")), "INDEX record")
                if pending is None or (info.offset, info.length) != pending:
                    raise TraceCorrupted(f"INDEX record at offset {pos} does not commit the chunk before it")
                scan.index.append(info)
                pending = None
            elif rtype == RecordType.HEADER:
                scan.header = file.decode(data, "the header")
            elif rtype == RecordType.INITIAL:
                scan.initial = file.decode(data, "the initial record")
            elif rtype == RecordType.CATALOG_EXT:
                scan.exts[pos] = file.decode(data, "a CATALOG_EXT record")
            elif rtype == RecordType.KPI:
                scan.kpis.append(file.decode(data, "a KPI record"))
            elif rtype == RecordType.PRELUDE:
                file.decode(data, "a PRELUDE record")
            elif rtype == RecordType.FOOTER:
                scan.footer = file.decode(data, "the footer")
                pos = record_end
                break
            pos = record_end  # record types of later minor versions are skipped
        scan.stop = pos
        return scan

    def _take_head(self, scan: _Scan) -> None:
        if scan.header is None:
            raise TraceCorrupted("the trace has no complete header record")
        header = scan.header
        try:
            required = {str(name) for name in header["features"]["required"]}
            self._level = str(header["level"])
            self._header_manifest = FrozenMap(header["manifest"])
            initial = scan.initial
            self._initial: Mapping[str, Mapping[str, Wire]] | None = None
            self._activation: Cursor | None = None
            self._initial_manifest = FrozenMap({})
            if initial is not None:
                self._initial = _state(initial["state"])
                self._activation = _wire_cursor(initial["cursor"])
                self._initial_manifest = FrozenMap(initial["manifest"])
        except _MALFORMED as exc:
            raise TraceCorrupted(f"malformed header or initial record: {exc!r}") from exc
        unknown = required - set(REQUIRED_FEATURES)
        if unknown:
            raise ValueError(f"the trace requires features this reader does not support: {sorted(unknown)}")
        self._header = header

    def _read_ext(self, file: _File, offset: int, end: int) -> Any:
        frame = file.frame(offset, end) if PREAMBLE.size <= offset < end else None
        if frame is None or frame[0] != RecordType.CATALOG_EXT:
            raise TraceCorrupted(f"the footer names offset {offset} as a CATALOG_EXT record, which it is not")
        data = file.payload(offset, frame[1], frame[2])
        if data is None:
            raise TraceCorrupted(f"CATALOG_EXT record at offset {offset} fails its CRC check")
        return file.decode(data, "a CATALOG_EXT record")

    def _read_kpis(self, file: _File, start: int, end: int) -> list[Any]:
        """The decoded ``KPI`` records between `start` and `end`, walking the frames of the other records."""
        records: list[Any] = []
        pos = start
        while pos < end:
            frame = file.frame(pos, end)
            if frame is None:
                raise TraceCorrupted(f"record at offset {pos} is cut short")
            rtype, length, crc = frame
            if rtype == RecordType.KPI:
                data = file.payload(pos, length, crc)
                if data is None:
                    raise TraceCorrupted(f"KPI record at offset {pos} fails its CRC check")
                records.append(file.decode(data, "a KPI record"))
            pos += _FRAME + length
        return records

    def _build_catalog(self) -> None:
        def build() -> tuple[
            dict[str, CatalogEntry],
            dict[str, frozenset[str]],
            dict[str, float],
            dict[str, list[KpiPoint]],
            dict[str, FrozenMap],
        ]:
            catalog = {str(name): CatalogEntry.from_wire(entry) for name, entry in self._header["catalog"].items()}
            kinds = {
                str(kind): StateSchema.from_wire(schema).presentation for kind, schema in self._header["kinds"].items()
            }
            for ext in sorted(self._exts.values(), key=lambda ext: ext["epoch"]):
                catalog.update({str(name): CatalogEntry.from_wire(entry) for name, entry in ext["types"].items()})
                kinds.update(
                    {str(kind): StateSchema.from_wire(schema).presentation for kind, schema in ext["kinds"].items()}
                )
            declarations: dict[str, FrozenMap] = {}
            scalars: dict[str, float] = {}
            series: dict[str, list[KpiPoint]] = {}
            for record in self._kpi_records:
                if type(record) is not FrozenMap:
                    raise TypeError("a KPI record is a map")
                if "declarations" in record:
                    value = record["declarations"]
                    if type(value) is not FrozenMap:
                        raise TypeError("KPI declarations are a map")
                    from simulatte.kpi import KPI

                    for key, declaration in value.items():
                        if key in declarations:
                            raise ValueError(f"duplicate KPI declaration {key!r}")
                        if type(declaration) is not FrozenMap:
                            raise TypeError("a KPI declaration is a map")
                        fields = {
                            "name",
                            "unit",
                            "kind",
                            "observation",
                            "cohort",
                            "aggregation",
                            "clip",
                            "censoring",
                            "ema_reset",
                            "empty",
                            "description",
                        }
                        if set(declaration) != fields:
                            raise ValueError("a KPI declaration must contain every declared metadata field")
                        for field in ("name", "unit", "observation", "aggregation", "description"):
                            if type(declaration[field]) is not str:
                                raise TypeError(f"KPI {field} must be a string")
                        if type(declaration["ema_reset"]) is not bool:
                            raise TypeError("KPI ema_reset must be a bool")
                        if declaration["empty"] is not None:
                            _wire_real(declaration["empty"])
                        if type(declaration["kind"]) is not tuple:
                            raise TypeError("KPI kind must be an array")
                        kpi = KPI(**dict(declaration))
                        if not key.endswith("/" + kpi.name) or key == "/" + kpi.name:
                            raise ValueError("a KPI key must be scope/name matching its declaration")
                        declarations[key] = declaration
                if "scalars" in record:
                    if type(record["scalars"]) is not FrozenMap:
                        raise TypeError("KPI scalars are a map")
                    scalars.update(record["scalars"])
                samples = record.get("samples", ())
                if type(samples) is not tuple:
                    raise TypeError("KPI samples are an array")
                for seq, t, key, value in samples:  # as the TypeScript reader checks them
                    if type(key) is not str:
                        raise TypeError(f"a KPI sample key is not a string: {key!r}")
                    cursor = _wire_cursor((t, seq))
                    series.setdefault(key, []).append((cursor, _wire_real(value)))
            return catalog, kinds, scalars, series, declarations

        built = _decoded(build, "catalog or KPI record")
        self._catalog: dict[str, CatalogEntry] = built[0]
        self._kind_presentation: dict[str, frozenset[str]] = built[1]
        self._kpis: dict[str, float] = built[2]
        self._kpi_series: dict[str, list[KpiPoint]] = built[3]
        self._kpi_declarations = FrozenMap(built[4])

    # -------------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------------

    @property
    def manifest(self) -> FrozenMap:
        """The merged manifest: the requested part and, when the footer exists, the final part."""
        merged: dict[str, Wire] = {**self._header_manifest, **self._initial_manifest}
        if self.manifest_final:
            merged.update(self._footer["manifest"])
        return FrozenMap(merged)

    @property
    def manifest_final(self) -> bool:
        """Whether :attr:`manifest` includes the final part (the footer exists and holds it)."""
        return self._footer is not None and self._footer["manifest"] is not None

    @property
    def catalog(self) -> Mapping[str, CatalogEntry]:
        """Every event type described by the trace: the header catalog plus the catalog extensions."""
        return MappingProxyType(self._catalog)

    @property
    def level(self) -> str:
        """The recording level, ``"full"`` or ``"kpi"``."""
        return self._level

    @property
    def outcome(self) -> str | None:
        """How the run ended (``completed``, ``cancelled``, ``failed``); None without a footer."""
        return None if self._footer is None else str(self._footer["outcome"])

    @property
    def fingerprint(self) -> Fingerprint | None:
        """The digest and KPI scalars stored in the footer; None without a footer."""
        if self._footer is None:
            return None
        stored = self._footer["fingerprint"]
        return Fingerprint(digest=stored["digest"], kpis=dict(stored["kpis"]))

    @property
    def index(self) -> tuple[ChunkInfo, ...]:
        """The visible chunks, in file order."""
        return self._index

    @property
    def cursor_range(self) -> tuple[Cursor, Cursor] | None:
        """The first and last cursors :meth:`state_at` accepts; None if the run was never activated.

        The first is the activation cursor. The last is the footer cursor of a complete ``full`` trace, else
        the last cursor of the last visible chunk (the activation cursor without chunks).
        """
        start = self._activation
        if start is None:
            return None
        if self._footer is not None and self._level == "full":
            end = _wire_cursor(self._footer["cursor"])
        else:
            end = self._index[-1].last if self._index else start
        return start, max(start, end)

    @property
    def kpi_declarations(self) -> FrozenMap:
        """Immutable KPI metadata keyed by ``scope/name``; empty for older traces.

        Each declaration includes its unit, kind, description and observation semantics.
        """
        return self._kpi_declarations

    def kpis(self) -> dict[str, float]:
        """The KPI scalars of the ``KPI`` records, later records overriding earlier ones."""
        return dict(self._kpis)

    def kpi_series(self) -> dict[str, list[KpiPoint]]:
        """The KPI samples of the ``KPI`` records: for each ``"<scope id>/<kpi>"``, ``(cursor, value)`` pairs.

        The cursor ``(t, seq)`` of a sample orders it among the domain events; samples are in emission order.
        """
        return {key: list(points) for key, points in self._kpi_series.items()}

    # -------------------------------------------------------------------------
    # Seeking
    # -------------------------------------------------------------------------

    def state_at(self, cursor: Cursor) -> State:
        """The replay state after every domain event at or before `cursor`.

        Raises `ValueError` for a cursor outside :attr:`cursor_range` and :class:`TraceCorrupted` if the chunk
        it needs is damaged.
        """
        at = _as_cursor(cursor)
        bounds = self.cursor_range
        if bounds is None or self._initial is None:
            raise ValueError("the trace has no initial state: the run was never activated")
        start, end = bounds
        if not start <= at <= end:
            raise ValueError(f"cursor {at} is outside the cursor range {start} .. {end} of the trace")
        i = bisect_right(self._firsts, at) - 1
        if i < 0:
            return _copy(self._initial)
        chunk = self._chunk(i)
        state = _copy(chunk.snapshot)
        _apply(state, chunk.events[: bisect_right(chunk.cursors, at)])
        return state

    def events(self, start: Cursor | None = None, end: Cursor | None = None) -> Iterator[TraceEvent]:
        """The recorded domain events with ``start < (t, seq) <= end``, in order.

        These are exactly the events that lead from ``state_at(start)`` to ``state_at(end)``; a missing bound
        is open.
        """
        low = None if start is None else _as_cursor(start)
        high = None if end is None else _as_cursor(end)
        for i, info in enumerate(self._index):
            if low is not None and info.last <= low:
                continue
            if high is not None and info.first > high:
                return
            for event in self._chunk(i).events:
                at = (event.t, event.seq)
                if low is not None and at <= low:
                    continue
                if high is not None and at > high:
                    return
                yield event

    def _chunk(self, i: int) -> _Chunk:
        cached = self._cache.get(i)
        if cached is not None:
            self._cache.move_to_end(i)
            return cached
        with open(self._path, "rb") as f:
            chunk = self._read_chunk(_File(f, self._size, self._limits), self._index[i])
        self._cache[i] = chunk
        if len(self._cache) > _CACHED_CHUNKS:
            self._cache.popitem(last=False)
        return chunk

    def _read_chunk(self, file: _File, info: ChunkInfo) -> _Chunk:
        offset = info.offset
        frame = file.frame(offset, file.size)
        if frame is None or frame[0] != RecordType.CHUNK:
            raise TraceCorrupted(f"the chunk index names offset {offset} as a CHUNK record, which it is not")
        if _FRAME + frame[1] != info.length:
            raise TraceCorrupted(f"CHUNK record at offset {offset} has length {_FRAME + frame[1]}, not {info.length}")
        data = file.payload(offset, frame[1], frame[2])
        if data is None:
            raise TraceCorrupted(f"CHUNK record at offset {offset} fails its CRC check")
        limit = self._limits.max_chunk
        try:
            inflater = zlib.decompressobj()
            raw = inflater.decompress(data, limit + 1)
        except zlib.error as exc:
            raise TraceCorrupted(f"CHUNK record at offset {offset} is not valid zlib data: {exc}") from exc
        if len(raw) > limit:
            raise TraceCorrupted(f"CHUNK record at offset {offset} decompresses to more than max_chunk={limit} bytes")
        if not inflater.eof or inflater.unused_data:
            raise TraceCorrupted(f"CHUNK record at offset {offset} is not one complete zlib stream")
        body = file.decode(raw, f"the chunk at offset {offset}")

        def build() -> _Chunk:
            events = tuple(map(_trace_event, body["events"]))
            cursors = [_wire_cursor((event.t, event.seq)) for event in events]
            consistent = (
                bool(cursors)
                and cursors[0] == info.first == _wire_cursor(body["first"])
                and cursors[-1] == info.last == _wire_cursor(body["last"])
                and all(a < b for a, b in pairwise(cursors))
            )
            if not consistent:
                raise TraceCorrupted(f"the events of the chunk at offset {offset} disagree with its index entry")
            return _Chunk(snapshot=_state(body["snapshot"]), events=events, cursors=cursors)

        return _decoded(build, f"chunk at offset {offset}")

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def check(self) -> None:
        """Validate the whole container; raise :class:`TraceCorrupted` at the first problem.

        Every record's CRC is checked and every visible chunk is decompressed and decoded within the limits;
        the INDEX records must agree with the footer index, and each chunk's start snapshot must equal the
        initial state with the deltas of all earlier chunks applied, value for value by canonical encoding (ruling
        R31: NaN equals NaN, -0.0 differs from 0.0). An incomplete tail is not a problem: it is reported by
        :attr:`truncated`.
        """
        with open(self._path, "rb") as f:
            file = _File(f, self._size, self._limits)
            if self._footer_offset is not None:
                scan = self._scan(file, PREAMBLE.size, self._footer_offset, head_only=False, damage_is_tail=False)
                if tuple(scan.index) != self._index:
                    raise TraceCorrupted("the footer index disagrees with the INDEX records")
            state = None if self._initial is None else _copy(self._initial)
            for info in self._index:
                chunk = self._read_chunk(file, info)
                if state is not None and not wire_equal(chunk.snapshot, state):
                    raise TraceCorrupted(f"the snapshot of the chunk at offset {info.offset} disagrees with the replay")
                state = _copy(chunk.snapshot)
                _apply(state, chunk.events)

    def verify(self) -> bool | Literal["not_verifiable"]:
        """Recompute the semantic digest from the initial state and the events and compare it with the footer.

        Returns ``"not_verifiable"`` for ``kpi`` traces, which hold no events, and for traces without a footer
        digest.
        """
        stored = self.fingerprint
        if self._level != "full" or stored is None or stored.digest is None:
            return "not_verifiable"
        digest = DigestAccumulator()  # the same framing and rolled kind map as the live SemanticDigest
        presentation_of = self._presentation_of
        if self._initial is not None:
            digest.feed_state(self._initial, presentation_of)
        payload_presentation: dict[str, frozenset[str]] = {}
        for i in range(len(self._index)):
            for event in self._chunk(i).events:
                entry = self._catalog.get(event.type)
                if entry is None:
                    raise TraceCorrupted(f"event type {event.type!r} (seq {event.seq}) is not in the trace catalog")
                hidden = payload_presentation.get(event.type)
                if hidden is None:
                    hidden = payload_presentation[event.type] = frozenset(
                        f.name for f in entry.fields if f.presentation
                    )
                _decoded(
                    lambda event=event, entry=entry, hidden=hidden: digest.feed_event_parts(
                        event.ordinal,
                        event.type,
                        entry.version,
                        event.t,
                        event.payload,
                        event.deltas,
                        payload_presentation=hidden,
                        presentation_of=presentation_of,
                    ),
                    f"event seq {event.seq}",
                )
        return digest.hexdigest() == stored.digest

    def _presentation_of(self, kind: str) -> frozenset[str]:
        return self._kind_presentation.get(kind, frozenset())


# ---------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------


def _decoded(build: Callable[[], _T], what: str) -> _T:
    """Run `build`, turning structural errors in decoded data into :class:`TraceCorrupted`."""
    try:
        return build()
    except _MALFORMED as exc:
        raise TraceCorrupted(f"malformed {what}: {exc!r}") from exc


def _as_cursor(value: Any) -> Cursor:
    """A cursor given by the caller: any ``(t, seq)`` pair that converts to ``(float, int)``; `ValueError` otherwise."""
    try:
        t, seq = value
        return float(t), int(seq)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"a cursor is a (t, seq) pair, got {value!r}") from exc


def _trace_event(entry: Any) -> TraceEvent:
    """A chunk event checked as the TypeScript reader checks it: an integer seq, a null or integer ordinal, a string
    type, a time, any payload and an array of operation arrays; `TypeError` otherwise."""
    event = TraceEvent(*entry)
    if type(event.type) is not str:
        raise TypeError(f"an event type is not a string: {event.type!r}")
    if event.ordinal is not None:
        _wire_int(event.ordinal)
    if type(event.deltas) is not tuple or not all(type(op) is tuple for op in event.deltas):
        raise TypeError("event deltas are an array of operation arrays")
    return event


def _wire_cursor(value: Any) -> Cursor:
    """A cursor read from the file: a ``[t, seq]`` array of a number and an integer, as the TypeScript reader
    requires; `TypeError` otherwise (reported as corruption)."""
    if type(value) is not tuple or len(value) != 2:
        raise TypeError(f"a cursor is a [t, seq] pair of numbers, got {value!r}")
    return _wire_time(value[0]), _wire_int(value[1])


def _wire_time(value: Any) -> float:
    """A time read from the file: a number that is not NaN (ruling R35); `TypeError` otherwise."""
    t = _wire_real(value)
    if t != t:
        raise TypeError("a time is NaN")
    return t


def _wire_real(value: Any) -> float:
    """A number read from the file (not a boolean); `TypeError` otherwise."""
    if type(value) is float or type(value) is int:
        return float(value)
    raise TypeError(f"not a number: {value!r}")


def _wire_int(value: Any) -> int:
    """An integer read from the file: an integer, or a float with a safe integral value (JavaScript cannot tell
    them apart); `TypeError` otherwise."""
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer() and abs(value) <= MAX_SAFE_INT:
        return int(value)
    raise TypeError(f"not an integer: {value!r}")


def _state(value: Any) -> Mapping[str, Mapping[str, Wire]]:
    """A replay state read from the file: a map from entity id to a map holding a string ``"$kind"``; `TypeError`
    otherwise."""
    if type(value) is not FrozenMap:
        raise TypeError("a state is a map of entity maps")
    for entity, fields in value.items():
        if type(fields) is not FrozenMap or type(fields.get("$kind")) is not str:
            raise TypeError(f"entity {entity!r} is not a map with a string '$kind'")
    return value


def _chunk_info(entry: Any) -> ChunkInfo:
    return ChunkInfo(
        offset=_wire_int(entry["offset"]),
        length=_wire_int(entry["length"]),
        first=_wire_cursor(entry["first"]),
        last=_wire_cursor(entry["last"]),
        t_start=_wire_time(entry["t_start"]),
        t_end=_wire_time(entry["t_end"]),
        epoch=_wire_int(entry["epoch"]),
    )


def _check_index(index: tuple[ChunkInfo, ...], start: int, end: int) -> None:
    """Index entries must fit the file between the head records and the footer (or end of scan), in order."""
    if len(index) > (end - start) // (2 * _FRAME):
        raise TraceCorrupted(f"the chunk index has {len(index)} entries, more than the file can hold")
    previous_end = start
    previous_last: Cursor | None = None
    for info in index:
        if info.offset < previous_end or info.length < _FRAME or info.offset + info.length + _FRAME > end:
            raise TraceCorrupted(f"chunk index entry at offset {info.offset} lies outside the records or overlaps")
        if info.first > info.last or (previous_last is not None and info.first <= previous_last):
            raise TraceCorrupted(f"chunk index entry at offset {info.offset} has cursors out of order")
        previous_end = info.offset + info.length + _FRAME
        previous_last = info.last


def _copy(state: Mapping[str, Mapping[str, Wire]]) -> State:
    return {entity: dict(fields) for entity, fields in state.items()}


def _apply(state: State, events: tuple[TraceEvent, ...]) -> None:
    for event in events:
        try:
            apply_deltas(state, Deltas(event.deltas))
        except _MALFORMED as exc:
            raise TraceCorrupted(f"the deltas of event seq {event.seq} do not apply to the replay state") from exc
