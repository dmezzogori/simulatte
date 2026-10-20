"""Server-side sort and filter over a retained analytical table, through a disposable SQLite index.

An empty query pages the artifact's chunks directly. Any other query is answered from an index at
``<cache_dir>/<manifest identity>/<query identity>.sqlite`` that a contained child process
(``python -I -m simulatte.analysis.table_query <config.json>``) builds from the verified chunks. The
index holds the filtered rows in their final order; the original artifact is only read. An index is
visible under its final name only after a durable rename, and is used only once its ``.ready``
marker exists and its stored identity matches; a damaged or unmarked index is rebuilt. Concurrent
builders of one index in one process share a single build; builders in different processes each
write a private temporary file, and the last rename wins with identical content.

Ordering, for each sort key:

- int64 values compare exactly (SQLite INTEGER, never a float), including values beyond 2**53.
- float64: ``-inf < finite < +inf < NaN``; ``0.0`` and ``-0.0`` are equal.
- strings compare by Unicode code point (UTF-8 bytes under memcmp), not by UTF-16 code unit.
- bools: ``False < True``.
- nested lists and records compare as their canonical tagged-JSON text: a stable, text-only order.
- null is last in both directions. ``desc`` reverses the order of non-null values only, so a
  descending float sort is ``NaN, +inf, ..., -inf, null``.
- ties keep the artifact's row order, so the order is total and pages are stable.

Filters (all ANDed together), with exact comparisons in the same value domains:

- ``eq``/``ne``/``in`` test equality. NaN equals NaN; ``0.0`` equals ``-0.0``. A null value means
  missingness: ``eq null`` is IS NULL and ``ne null`` is IS NOT NULL; ``in`` may include null.
- Nulls follow SQL three-valued logic otherwise: comparing a null cell with a non-null value is
  unknown, so the row is excluded. In particular ``ne 5`` does not return null cells.
- ``lt``/``le``/``gt``/``ge`` order numbers, strings and bools as above. NaN cells never satisfy
  a range filter, and null, NaN and nested values are refused as range operands.
- Values must match the field type: int64 takes a Python int (bool is not an int), float64 a float
  or an int that is exactly representable as float64, string a UTF-8-encodable str, nested types
  a value matching the declared structure. Over HTTP values are tagged JSON (``simulatte.analysis.codec``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing, suppress
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any, Literal

import msgpack

from simulatte.execution.durability import replace_durable
from simulatte.execution.process import ContainedProcess
from . import codec
from .datasets import MAX_BYTES, MAX_ROWS, DatasetManifest, DatasetPage, DatasetReader
from .types import DatasetSchema, Field, copied, matches, plain

OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in")
RANGE = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}
MAX_SORT = 16
MAX_FILTERS = 32
MAX_IN = 1000  # 32 filters x 1000 values stays below SQLite's 32766 bound parameters
MAX_QUERY_CHARS = 64 << 10
CACHE_KIB = 8192  # per-connection page cache; larger sorts spill to temporary files
VERSION = "table-query-v1"


class TableQueryCancelled(RuntimeError):
    """The caller cancelled an index build; nothing was published."""


class _SharedBuild:
    """One in-process index build and the callers waiting for it, keyed by the index path."""

    def __init__(self):
        self.done = threading.Event()
        self.count: int | None = None
        self.error: BaseException | None = None
        # One entry per waiting caller; the build stops only when every caller has cancelled.
        self.cancels: dict[object, Callable[[], bool] | None] = {}

    def cancelled(self) -> bool:
        with _BUILDS_LOCK:
            cancels = list(self.cancels.values())
        return all(cancel is not None and cancel() for cancel in cancels)


_BUILDS: dict[str, _SharedBuild] = {}
_BUILDS_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class Filter:
    field: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in"]
    value: Any

    def __post_init__(self):
        if type(self.field) is not str or self.op not in OPS:
            raise ValueError("A filter needs a field name and one of eq/ne/lt/le/gt/ge/in")
        value = copied(self.value)
        if self.op == "in" and (type(value) is not tuple or not 1 <= len(value) <= MAX_IN):
            raise ValueError(f"Filter 'in' takes a tuple of 1–{MAX_IN} values")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class TableQuery:
    sort: tuple[tuple[str, Literal["asc", "desc"]], ...] = ()
    filters: tuple[Filter, ...] = ()

    def __post_init__(self):
        try:
            sort = tuple((field, direction) for field, direction in self.sort)
        except (TypeError, ValueError) as error:
            raise ValueError("Sort keys are (field, direction) pairs") from error
        filters = tuple(self.filters)
        if any(type(field) is not str or direction not in ("asc", "desc") for field, direction in sort):
            raise ValueError("Sort direction must be asc or desc")
        if len({field for field, _ in sort}) != len(sort) or len(sort) > MAX_SORT:
            raise ValueError(f"Sort on at most {MAX_SORT} distinct fields")
        if any(type(item) is not Filter for item in filters) or len(filters) > MAX_FILTERS:
            raise ValueError(f"Filters must be at most {MAX_FILTERS} Filter values")
        object.__setattr__(self, "sort", sort)
        object.__setattr__(self, "filters", filters)

    @property
    def empty(self) -> bool:
        return not self.sort and not self.filters

    def fields(self) -> tuple[str, ...]:
        """Referenced field names, sort keys first, without duplicates."""
        return tuple(dict.fromkeys([*(name for name, _ in self.sort), *(item.field for item in self.filters)]))

    def validate(self, schema: DatasetSchema) -> TableQuery:
        """Check names and values against the schema; returns the normalized query (int → float64)."""
        fields = {f.name: f for f in schema.fields}
        for name in self.fields():
            if name not in fields:
                raise ValueError(f"Unknown table field: {name!r}")
        return TableQuery(
            self.sort,
            tuple(Filter(f.field, f.op, _filter_value(fields[f.field], f.op, f.value)) for f in self.filters),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sort": [list(key) for key in self.sort],
            "filters": [[f.field, f.op, codec.to_json(f.value)] for f in self.filters],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TableQuery:
        return cls(
            tuple(tuple(key) for key in data["sort"]),
            tuple(Filter(field, op, codec.from_json(value)) for field, op, value in data["filters"]),
        )

    def identity(self, manifest_identity: str) -> str:
        """sha256 naming the derived index of this query over one artifact (validate first)."""
        body = {"version": VERSION, "manifest": manifest_identity, **self.to_dict()}
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class TablePage(DatasetPage):
    """A ``DatasetPage`` plus the size of the (filtered) result and whether an index served it."""

    row_count: int
    indexed: bool

    def to_dict(self) -> dict[str, Any]:
        return {**DatasetPage.to_dict(self), "row_count": self.row_count, "indexed": self.indexed}


def _filter_value(field: Field, op: str, value: Any) -> Any:
    if op == "in":
        return tuple(_typed(field, op, item) for item in value)
    return _typed(field, op, value)


def _typed(field: Field, op: str, value: Any) -> Any:
    if value is None:
        if op in RANGE:
            raise ValueError(f"{field.name}: null has no order; use eq/ne/in null")
        if not field.nullable and field.dtype != "null":
            raise ValueError(f"{field.name} is not nullable")
        return None
    if op in RANGE and (not isinstance(field.dtype, str) or type(value) is float and math.isnan(value)):
        raise ValueError(f"{field.name}: NaN and nested values support only eq/ne/in")
    if field.dtype == "float64" and type(value) is int:
        converted = float(value)
        if int(converted) != value:
            raise ValueError(f"{field.name}: {value} is not exactly representable as float64")
        value = converted
    if not matches(value, field.dtype):
        raise ValueError(f"{field.name}: filter value must be {plain(field.dtype)}")
    if type(value) is str:
        value.encode("utf-8")  # UnicodeEncodeError (a ValueError) for lone surrogates
    return value


def _constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not allowed; use a $float64 tag")


_DECODER = json.JSONDecoder(parse_constant=_constant)


def parse_query(sort: str | None, filter: str | None, schema: DatasetSchema) -> TableQuery:
    """Parse ``sort=<field>:<asc|desc>[,...]`` and ``filter=<field>:<op>:<tagged-json>[,...]``."""
    if len(sort or "") > MAX_QUERY_CHARS or len(filter or "") > MAX_QUERY_CHARS:
        raise ValueError("Table query exceeds 64 KiB")
    keys: list[tuple[str, Any]] = []  # TableQuery checks the direction
    for item in sort.split(",") if sort else ():
        field, separator, direction = item.partition(":")
        if not separator:
            raise ValueError("Sort must be <field>:<asc|desc>")
        keys.append((field, direction))
    filters = []
    index = 0
    while filter and index <= len(filter):
        first = filter.find(":", index)
        second = filter.find(":", first + 1) if first >= 0 else -1
        if second < 0:
            raise ValueError("Filter must be <field>:<op>:<json-value>")
        field, op = filter[index:first], filter[first + 1 : second]
        operator: Any = op  # Filter checks the operator
        try:
            raw, index = _DECODER.raw_decode(filter, second + 1)
            value = codec.from_json(raw)
        except (ValueError, RecursionError) as error:
            raise ValueError(f"Invalid JSON value for filter on {field!r}") from error
        filters.append(Filter(field, operator, value))
        if index < len(filter) and filter[index] != ",":
            raise ValueError("Filters are separated by commas")
        index += 1
        if index == len(filter):
            raise ValueError("Trailing comma in filter")
    return TableQuery(tuple(keys), tuple(filters)).validate(schema)


def query_table(
    reader: DatasetReader,
    query: TableQuery,
    cursor: str | None,
    *,
    limit: int = 100,
    cache_dir: Path,
    timeout: float = 60.0,
    cancel: Callable[[], bool] | None = None,
) -> TablePage:
    """One page (limit 1–500, at most 1 MiB of JSON) of ``query`` over ``reader``'s table.

    A missing index is built in a contained child; ``timeout`` (seconds) and ``cancel`` (polled)
    kill it, raising ``TimeoutError`` or ``TableQueryCancelled``. Cursors are
    ``<index identity>:<offset>`` and are rejected for any other query or artifact.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Page limit must be 1–500")
    if query.empty:
        page = reader.page(cursor, limit=limit)
        return TablePage(page.rows, page.next_cursor, reader.manifest.row_count, False)
    schema = reader.manifest.schema
    query = query.validate(schema)
    manifest_identity = reader._identity
    identity = query.identity(manifest_identity)
    offset = _offset(cursor, identity)
    directory = Path(cache_dir) / manifest_identity
    index = directory / f"{identity}.sqlite"
    count = _ready(index, directory / f"{identity}.ready", identity)
    if count is None:
        count = _shared(reader, query, identity, directory, timeout=timeout, cancel=cancel)
    if cursor is not None and offset >= count:
        raise ValueError("Invalid table cursor for this query")
    return _page(index, identity, schema, offset, limit, count)


def _shared(
    reader: DatasetReader,
    query: TableQuery,
    identity: str,
    directory: Path,
    *,
    timeout: float,
    cancel: Callable[[], bool] | None,
) -> int:
    """Build the index once for every concurrent caller in this process and return its row count.

    The first caller builds; later callers wait for it, polling their own ``cancel`` and
    ``timeout``. A failed build raises its error in every waiter. A caller that cancels leaves
    with ``TableQueryCancelled``; the child is killed only once every waiting caller has cancelled.
    """
    key = str((directory / f"{identity}.sqlite").resolve())
    while True:
        token = object()
        with _BUILDS_LOCK:
            shared = _BUILDS.get(key)
            leader = shared is None
            if shared is None:
                shared = _BUILDS[key] = _SharedBuild()
            shared.cancels[token] = cancel
        if leader:
            try:
                # Another build may have finished between the caller's check and this one.
                count = _ready(directory / f"{identity}.sqlite", directory / f"{identity}.ready", identity)
                if count is None:
                    count = _build(reader, query, identity, directory, timeout=timeout, cancel=shared.cancelled)
                shared.count = count
            except BaseException as error:
                shared.error = error
                raise
            finally:
                with _BUILDS_LOCK:
                    del _BUILDS[key]
                shared.done.set()
        else:
            deadline = time.monotonic() + timeout
            try:
                while not shared.done.wait(0.05):
                    if cancel is not None and cancel():
                        raise TableQueryCancelled("Table index build cancelled")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Table index build exceeded {timeout} s")
            finally:
                with _BUILDS_LOCK:
                    shared.cancels.pop(token, None)
            if isinstance(shared.error, TableQueryCancelled) and not (cancel is not None and cancel()):
                continue  # the others cancelled after this caller's last poll: build again
            if shared.error is not None:
                raise copy.copy(shared.error) from shared.error
        if cancel is not None and cancel():
            raise TableQueryCancelled("Table index build cancelled")
        assert shared.count is not None
        return shared.count


def _offset(cursor: str | None, identity: str) -> int:
    if cursor is None:
        return 0
    try:
        digest, raw = cursor.split(":")
        offset = int(raw)
        if digest != identity or str(offset) != raw or offset < 0:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise ValueError("Invalid table cursor for this query") from error
    return offset


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _ready(index: Path, marker: Path, identity: str) -> int | None:
    """Row count of a published index, or None if it must be (re)built."""
    if not marker.is_file():
        return None
    try:
        with closing(_connect(index)) as db:
            stored, count = db.execute("SELECT identity, rows FROM meta").fetchone()
    except (sqlite3.Error, TypeError):
        return None
    return count if stored == identity else None


def _build(
    reader: DatasetReader,
    query: TableQuery,
    identity: str,
    directory: Path,
    *,
    timeout: float,
    cancel: Callable[[], bool] | None,
) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f".{identity}.{secrets.token_hex(8)}"
    temporary, work, config, marker = (directory / (stem + suffix) for suffix in (".tmp", ".work", ".json", ".ready"))
    try:
        body = {
            "root": str(reader.root),
            "manifest": reader.manifest.to_dict(),
            "query": query.to_dict(),
            "destination": str(temporary),
            "work": str(work),
            "identity": identity,
            "cache_kib": CACHE_KIB,
        }
        config.write_text(json.dumps(body, allow_nan=False))
        _run_child(config, directory, timeout=timeout, cancel=cancel)
        with closing(_connect(temporary)) as db:
            (count,) = db.execute("SELECT rows FROM meta").fetchone()
        replace_durable(temporary, directory / f"{identity}.sqlite")
        with marker.open("xb") as stream:
            stream.write(identity.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        replace_durable(marker, directory / f"{identity}.ready")
        return count
    finally:
        for path in (temporary, work, config, marker):
            path.unlink(missing_ok=True)


def _run_child(config: Path, directory: Path, *, timeout: float, cancel: Callable[[], bool] | None) -> None:
    child = ContainedProcess.start(
        [sys.executable, "-I", "-m", "simulatte.analysis.table_query", str(config)],
        cwd=directory,
        env={"PYTHONDONTWRITEBYTECODE": "1", "SQLITE_TMPDIR": str(directory)},
    )
    diagnostics, lock = bytearray(), threading.Lock()

    def drain(stream):
        while block := stream.read(65536):
            with lock:
                diagnostics.extend(block)
                del diagnostics[:-8192]

    drains = [threading.Thread(target=drain, args=(stream,), daemon=True) for stream in (child.stdout, child.stderr)]
    for thread in drains:
        thread.start()
    deadline, stopped = time.monotonic() + timeout, None
    try:
        while child.returncode is None:
            if stopped is None and cancel is not None and cancel():
                stopped = "cancelled"
                child.kill()
            elif stopped is None and time.monotonic() >= deadline:
                stopped = "timed out"
                child.kill()
            with suppress(TimeoutError):
                child.wait(timeout=0.05)
    finally:
        child.close()
        for thread in drains:
            thread.join(5)
    if stopped == "cancelled":
        raise TableQueryCancelled("Table index build cancelled")
    if stopped is not None:
        raise TimeoutError(f"Table index build exceeded {timeout} s")
    if child.returncode != 0:
        raise ValueError("Table index build failed: " + diagnostics.decode(errors="replace").strip()[-2048:])


def _page(index: Path, identity: str, schema: DatasetSchema, offset: int, limit: int, count: int) -> TablePage:
    names = [f.name for f in schema.fields]
    rows = []
    size = 128  # bounded envelope and next cursor, as DatasetReader.page
    with closing(_connect(index)) as db:
        for (blob,) in db.execute("SELECT row FROM result WHERE pos > ? ORDER BY pos LIMIT ?", (offset, limit)):
            row = schema.row(dict(zip(names, msgpack.unpackb(blob, raw=False))))
            row_size = len(json.dumps(codec.to_json(row), ensure_ascii=True).encode()) + 2
            if size + row_size > MAX_BYTES:
                if not rows:
                    raise ValueError("Single row exceeds JSON page limit; use streaming dataset export")
                break
            rows.append(row)
            size += row_size
    end = offset + len(rows)
    return TablePage(tuple(rows), f"{identity}:{end}" if end < count else None, count, True)


# Child side: everything below runs in the contained process (and in-process in tests).


def _key(value: Any, dtype: Any) -> tuple[int, int, Any]:
    """(null flag, NaN flag, comparable value) for one cell or filter operand."""
    if value is None:
        return 1, 0, None
    if dtype == "float64":
        return (0, 1, None) if math.isnan(value) else (0, 0, value)
    if dtype == "string":
        return 0, 0, value.encode("utf-8")
    if isinstance(dtype, str):
        return 0, 0, value  # int64 and bool are SQLite INTEGERs
    return 0, 0, codec.dumps(value)


def _equal(position: int, key: tuple[int, int, Any]) -> tuple[str, list[Any]]:
    null, nan, value = key
    if null:
        return f"n{position} = 1", []
    if nan:
        return f"c{position} = 1", []
    return f"v{position} IS ?", [value]


def _where(query: TableQuery, positions: Mapping[str, int], schema: DatasetSchema) -> tuple[str, list[Any]]:
    dtypes = {f.name: f.dtype for f in schema.fields}
    clauses, parameters = ["1"], []
    for item in query.filters:
        position, dtype = positions[item.field], dtypes[item.field]
        if item.op == "in":
            parts = [_equal(position, _key(value, dtype)) for value in item.value]
            clauses.append("(" + " OR ".join(sql for sql, _ in parts) + ")")
            parameters.extend(value for _, values in parts for value in values)
        elif item.op in RANGE:
            clauses.append(f"v{position} {RANGE[item.op]} ?")
            parameters.append(_key(item.value, dtype)[2])
        else:
            sql, values = _equal(position, _key(item.value, dtype))
            clauses.append(sql if item.op == "eq" else f"(n{position} = 0 AND NOT ({sql}))")
            parameters.extend(values)
    return " AND ".join(clauses), parameters


def _order(query: TableQuery, positions: Mapping[str, int]) -> str:
    terms = []
    for name, direction in query.sort:
        position, order = positions[name], direction.upper()
        terms += [f"n{position} ASC", f"c{position} {order}", f"v{position} {order}"]
    return ", ".join([*terms, "ord ASC"])


def _write_index(reader: DatasetReader, query: TableQuery, destination: Path, work: Path, identity: str, kib: int):
    schema = reader.manifest.schema
    dtypes = {f.name: f.dtype for f in schema.fields}
    names = query.fields()
    positions = {name: position for position, name in enumerate(names)}
    db = sqlite3.connect(destination, isolation_level=None)
    try:
        db.execute("ATTACH DATABASE ? AS work", (str(work),))
        for database in ("main", "work"):
            db.execute(f"PRAGMA {database}.cache_size = -{int(kib)}")
            db.execute(f"PRAGMA {database}.journal_mode = OFF")
        db.execute("PRAGMA temp_store = FILE")
        columns = "".join(f", n{p} INTEGER, c{p} INTEGER, v{p}" for p in range(len(names)))
        db.execute(f"CREATE TABLE work.source (ord INTEGER PRIMARY KEY, row BLOB NOT NULL{columns})")
        insert = f"INSERT INTO work.source VALUES ({', '.join('?' * (2 + 3 * len(names)))})"
        batch: list[list[Any]] = []
        for number, row in enumerate(reader.iter_rows()):  # verifies every chunk's sha256
            values = [number, msgpack.packb([plain(row[f.name]) for f in schema.fields], use_bin_type=True)]
            for name in names:
                values.extend(_key(row[name], dtypes[name]))
            batch.append(values)
            if len(batch) == MAX_ROWS or number + 1 == reader.manifest.row_count:
                db.execute("BEGIN")
                db.executemany(insert, batch)
                db.execute("COMMIT")
                batch.clear()
        where, parameters = _where(query, positions, schema)
        db.execute("CREATE TABLE result (pos INTEGER PRIMARY KEY, row BLOB NOT NULL)")
        db.execute(
            f"INSERT INTO result (row) SELECT row FROM work.source WHERE {where} ORDER BY {_order(query, positions)}",
            parameters,
        )
        db.execute("CREATE TABLE meta (identity TEXT NOT NULL, rows INTEGER NOT NULL)")
        db.execute("INSERT INTO meta SELECT ?, count(*) FROM result", (identity,))
        db.execute("DETACH DATABASE work")
    finally:
        db.close()
        work.unlink(missing_ok=True)
    with destination.open("rb+") as stream:
        os.fsync(stream.fileno())


def main(config_path: str) -> int:
    config = json.loads(Path(config_path).read_text())
    manifest = DatasetManifest.from_dict(config["manifest"])
    query = TableQuery.from_dict(config["query"]).validate(manifest.schema)
    reader = DatasetReader(Path(config["root"]), manifest)
    _write_index(
        reader, query, Path(config["destination"]), Path(config["work"]), config["identity"], config["cache_kib"]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
