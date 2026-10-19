"""Bounded analytical-dataset-v1 MessagePack storage and streaming CSV export."""

from __future__ import annotations
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, TextIO
import msgpack
from . import codec
from .types import DatasetSchema, Observation, plain

MAX_ROWS = 4096
MAX_BYTES = 1 << 20
MAX_ROW_BYTES = 256 << 10
MAX_CSV_RECORD_CHARS = 8 << 20  # tagged JSON/CSV escaping, including non-BMP Unicode


@dataclass(frozen=True, slots=True)
class Chunk:
    path: str
    length: int
    sha256: str
    rows: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "length": self.length, "sha256": self.sha256, "rows": self.rows}


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    schema: DatasetSchema
    window: Mapping[str, Any]
    chunks: tuple[Chunk, ...]
    row_count: int

    def __post_init__(self):
        object.__setattr__(self, "window", Observation(self.window))

    def to_dict(self) -> dict[str, Any]:
        return {
            "codec": "analytical-dataset-v1",
            "schema": self.schema.to_dict(),
            "window": codec.to_json(self.window),
            "chunks": [c.to_dict() for c in self.chunks],
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DatasetManifest:
        if (
            set(data) != {"codec", "schema", "window", "chunks", "row_count"}
            or data["codec"] != "analytical-dataset-v1"
        ):
            raise ValueError("Unsupported analytical dataset manifest")
        return cls(
            DatasetSchema.from_dict(data["schema"]),
            Observation(codec.from_json(data["window"])),
            tuple(Chunk(**c) for c in data["chunks"]),
            data["row_count"],
        )


@dataclass(frozen=True, slots=True)
class DatasetPage:
    rows: tuple[Observation, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"rows": [codec.to_json(row) for row in self.rows], "next_cursor": self.next_cursor}


class DatasetWriter:
    def __init__(self, root: Path, schema: DatasetSchema):
        self.root, self.schema = Path(root), schema
        self.root.mkdir(parents=True, exist_ok=True)
        self._buffer: list[bytes] = []
        self._bytes = 3  # array16 header for at most 4096 rows
        self._chunks: list[Chunk] = []
        self._count = 0
        self._manifest: DatasetManifest | None = None
        self.window: Mapping[str, Any] = {}
        self.peak_buffer_rows = self.peak_buffer_bytes = 0

    def append(self, row: Mapping[str, Any]) -> None:
        if self._manifest is not None:
            raise RuntimeError("Dataset is already finished")
        validated = self.schema.row(row)
        encoded = msgpack.packb([plain(validated[f.name]) for f in self.schema.fields], use_bin_type=True)
        if len(encoded) > MAX_ROW_BYTES:
            raise ValueError(f"{self.schema.name}: encoded row exceeds 256 KiB; reduce retained field data")
        if self._buffer and (len(self._buffer) == MAX_ROWS or self._bytes + len(encoded) > MAX_BYTES):
            self._flush()
        self._buffer.append(encoded)
        self._bytes += len(encoded)
        self._count += 1
        self.peak_buffer_rows = max(self.peak_buffer_rows, len(self._buffer))
        self.peak_buffer_bytes = max(self.peak_buffer_bytes, self._bytes)

    def _flush(self) -> None:
        if not self._buffer:
            return
        path = f"chunk-{len(self._chunks):08d}.msgpack"
        digest = hashlib.sha256()
        length = 0
        # Write fragments directly: no second full-size encoded chunk buffer.
        with (self.root / path).open("xb") as stream:
            for data in (msgpack.Packer().pack_array_header(len(self._buffer)), *self._buffer):
                stream.write(data)
                digest.update(data)
                length += len(data)
            stream.flush()
            os.fsync(stream.fileno())
        self._chunks.append(Chunk(path, length, digest.hexdigest(), len(self._buffer)))
        self._buffer.clear()
        self._bytes = 3

    def finish(self) -> DatasetManifest:
        if self._manifest is None:
            self._flush()
            manifest = DatasetManifest(self.schema, Observation(self.window), tuple(self._chunks), self._count)
            with (self.root / "manifest.json").open("x") as stream:
                json.dump(manifest.to_dict(), stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            self._manifest = manifest
        return self._manifest


class DatasetReader:
    def __init__(self, root: Path, manifest: DatasetManifest):
        self.root, self.manifest = Path(root).resolve(), manifest
        self.peak_buffer_rows = self.peak_buffer_bytes = self.chunks_read = 0
        if type(manifest.row_count) is not int or manifest.row_count < 0:
            raise ValueError("Invalid dataset row count")
        count = 0
        for index, chunk in enumerate(manifest.chunks):
            if (
                chunk.path != f"chunk-{index:08d}.msgpack"
                or type(chunk.length) is not int
                or not 0 < chunk.length <= MAX_BYTES
                or type(chunk.rows) is not int
                or not 0 < chunk.rows <= MAX_ROWS
                or type(chunk.sha256) is not str
                or len(chunk.sha256) != 64
                or any(c not in "0123456789abcdef" for c in chunk.sha256)
            ):
                raise ValueError("Invalid analytical chunk descriptor/path/order")
            count += chunk.rows
        if count != manifest.row_count:
            raise ValueError("Dataset row count differs from chunks")
        self._identity = hashlib.sha256(json.dumps(manifest.to_dict(), sort_keys=True).encode()).hexdigest()

    def _chunk(self, chunk: Chunk) -> list[Any]:
        path = self.root / PurePosixPath(chunk.path)
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("Unsafe analytical chunk path")
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) != chunk.length or hashlib.sha256(data).hexdigest() != chunk.sha256:
            raise ValueError("Dataset chunk integrity failure")
        try:
            rows = msgpack.unpackb(data, raw=False, strict_map_key=True)
        except (ValueError, msgpack.UnpackException) as error:
            raise ValueError("Invalid dataset chunk encoding") from error
        if type(rows) is not list or len(rows) != chunk.rows:
            raise ValueError("Dataset chunk row count mismatch")
        self.chunks_read += 1
        self.peak_buffer_rows = max(self.peak_buffer_rows, len(rows))
        self.peak_buffer_bytes = max(self.peak_buffer_bytes, len(data))
        return rows

    def _row(self, values: Any) -> Observation:
        fields = self.manifest.schema.fields
        if type(values) is not list or len(values) != len(fields):
            raise ValueError("Dataset row width mismatch")
        return self.manifest.schema.row(dict(zip((f.name for f in fields), values)))

    def iter_rows(self) -> Iterator[Observation]:
        for chunk in self.manifest.chunks:
            for row in self._chunk(chunk):
                yield self._row(row)

    def page(self, cursor: str | None = None, *, limit: int = 100) -> DatasetPage:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Page limit must be 1–500")
        offset = 0
        if cursor is not None:
            try:
                digest, raw = cursor.split(":")
                offset = int(raw)
                if digest != self._identity or str(offset) != raw or not 0 <= offset < self.manifest.row_count:
                    raise ValueError()
            except (ValueError, AttributeError) as error:
                raise ValueError("Invalid dataset cursor") from error
        rows: list[Observation] = []
        size = 128  # bounded envelope and next cursor
        skipped = 0
        for chunk in self.manifest.chunks:
            if skipped + chunk.rows <= offset:
                skipped += chunk.rows
                continue
            for values in self._chunk(chunk)[max(0, offset - skipped) :]:
                row = self._row(values)
                row_size = len(json.dumps(codec.to_json(row), ensure_ascii=True).encode()) + 2
                if size + row_size > MAX_BYTES or len(rows) == limit:
                    if not rows:
                        raise ValueError("Single row exceeds JSON page limit; use streaming dataset export")
                    return DatasetPage(tuple(rows), f"{self._identity}:{offset + len(rows)}")
                rows.append(row)
                size += row_size
            skipped += chunk.rows
        return DatasetPage(tuple(rows), None)

    def to_csv(self, destination: TextIO) -> None:
        """Stream every row. Cells are exact tagged JSON, including quoted strings/null."""
        writer = csv.writer(destination)
        writer.writerow(f.name for f in self.manifest.schema.fields)
        for row in self.iter_rows():
            writer.writerow(codec.dumps(row[f.name]).decode() for f in self.manifest.schema.fields)


def _csv_records(source: TextIO) -> Iterator[list[str]]:
    """Read our single-line JSON-cell CSV dialect without changing csv's global limit.

    JSON escapes control characters, so exported cells never contain raw newlines.
    Limit the complete encoded record, including CSV quote escaping, before parsing.
    """
    while line := source.readline(MAX_CSV_RECORD_CHARS + 1):
        if len(line) > MAX_CSV_RECORD_CHARS:
            raise ValueError("CSV record exceeds 8 MiB character limit")
        line = line.removesuffix("\n").removesuffix("\r")
        cells = []
        index = 0
        while True:
            if index < len(line) and line[index] == '"':
                index += 1
                parts = []
                while True:
                    end = line.find('"', index)
                    if end < 0:
                        raise ValueError("Unterminated CSV quoted cell; use JSON-escaped newlines")
                    parts.append(line[index:end])
                    index = end + 1
                    if index < len(line) and line[index] == '"':
                        parts.append('"')
                        index += 1
                    else:
                        break
                cells.append("".join(parts))
            else:
                end = line.find(",", index)
                end = len(line) if end < 0 else end
                value = line[index:end]
                if '"' in value or "\r" in value or "\n" in value:
                    raise ValueError("Invalid CSV cell quoting")
                cells.append(value)
                index = end
            if index == len(line):
                break
            if line[index] != ",":
                raise ValueError("Invalid CSV delimiter after quoted cell")
            index += 1
        yield cells


def read_csv(source: TextIO, schema: DatasetSchema) -> Iterator[Observation]:
    """Stream exported JSON-cell CSV using its companion schema, with an 8 MiB record bound."""
    reader = _csv_records(source)
    names = [f.name for f in schema.fields]
    if next(reader, None) != names:
        raise ValueError("CSV columns differ from companion dataset schema")
    for row in reader:
        if len(row) != len(names):
            raise ValueError("CSV row width mismatch")
        result = schema.row({name: codec.loads(value) for name, value in zip(names, row)})
        if len(msgpack.packb([plain(result[name]) for name in names], use_bin_type=True)) > MAX_ROW_BYTES:
            raise ValueError("CSV decoded row exceeds 256 KiB dataset limit")
        yield result
