"""Server-side sort/filter over retained analytical tables: exact order, bounded pages, disposable indexes."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import random
import runpy
import sys
import warnings

import pytest

from simulatte.analysis import DatasetSchema, Field
from simulatte.analysis import codec
from simulatte.analysis.datasets import DatasetReader, DatasetWriter

BIG = 2**53 + 1  # first integer a float64/JavaScript number cannot hold


def api():
    assert importlib.util.find_spec("simulatte.analysis.table_query") is not None, "table query missing"
    return importlib.import_module("simulatte.analysis.table_query")


SCHEMA = DatasetSchema(
    "mixed",
    (
        Field("id", "int64"),
        Field("i", "int64", nullable=True),
        Field("x", "float64", nullable=True),
        Field("s", "string", nullable=True),
        Field("b", "bool", nullable=True),
        Field("tags", {"list": "int64"}),
    ),
)
NAN, INF = float("nan"), float("inf")
ROWS = [
    {"id": 0, "i": BIG, "x": NAN, "s": "a", "b": True, "tags": [1, 2]},
    {"id": 1, "i": 2**53, "x": INF, "s": "Z", "b": False, "tags": []},
    {"id": 2, "i": -(2**63), "x": -INF, "s": "é", "b": None, "tags": [1]},
    {"id": 3, "i": 2**63 - 1, "x": 0.0, "s": "😀", "b": True, "tags": [2]},
    {"id": 4, "i": 0, "x": -0.0, "s": "", "b": False, "tags": [1, 2]},
    {"id": 5, "i": None, "x": 1.5, "s": "￿", "b": True, "tags": []},
    {"id": 6, "i": BIG, "x": None, "s": None, "b": False, "tags": [10]},
]


def dataset(root, schema, rows):
    writer = DatasetWriter(root, schema)
    for row in rows:
        writer.append(row)
    return DatasetReader(root, writer.finish())


def canon(rows):
    """NaN-safe exact comparison: canonical tagged JSON of each row."""
    return [codec.dumps(row) for row in rows]


def collect(reader, query, cache, *, limit=500, **options):
    tq = api()
    rows, cursor = [], None
    while True:
        page = tq.query_table(reader, query, cursor, limit=limit, cache_dir=cache, **options)
        assert len(page.rows) <= limit
        rows.extend(page.rows)
        if page.next_cursor is None:
            return rows
        cursor = page.next_cursor


@pytest.fixture(params=["child", "inline"])
def mode(request, monkeypatch):
    """Run every semantic case through the real contained child and in-process (for coverage)."""
    tq = api()
    if request.param == "inline":
        monkeypatch.setattr(tq, "_run_child", lambda config, directory, **_: tq.main(str(config)))
    return request.param


@pytest.fixture
def launches(monkeypatch):
    tq = api()
    started = []
    original = tq.ContainedProcess.start

    def start(*args, **kwargs):
        started.append(original(*args, **kwargs))
        return started[-1]

    monkeypatch.setattr(tq.ContainedProcess, "start", start)
    return started


def files(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_query_validation_refuses_unknown_fields_bad_ops_and_mistyped_values():
    tq = api()
    TableQuery, Filter = tq.TableQuery, tq.Filter
    for build in (
        lambda: Filter(1, "eq", 1),
        lambda: Filter("i", "like", 1),
        lambda: Filter("i", "in", 1),
        lambda: Filter("i", "in", ()),
        lambda: Filter("i", "in", tuple(range(tq.MAX_IN + 1))),
        lambda: Filter("i", "eq", object()),
        lambda: TableQuery(((1, "asc"),)),
        lambda: TableQuery((("i", "up"),)),
        lambda: TableQuery((("i",),)),
        lambda: TableQuery((("i", "asc"), ("i", "desc"))),
        lambda: TableQuery(tuple((f"f{n}", "asc") for n in range(tq.MAX_SORT + 1))),
        lambda: TableQuery((), (("i", "eq", 1),)),
        lambda: TableQuery((), tuple(Filter("i", "eq", n) for n in range(tq.MAX_FILTERS + 1))),
    ):
        with pytest.raises(ValueError):
            build()
    for query in (
        TableQuery((("missing", "asc"),)),
        TableQuery((), (Filter("missing", "eq", 1),)),
        TableQuery((), (Filter("i", "eq", "1"),)),  # string for int64
        TableQuery((), (Filter("i", "eq", 1.0),)),  # float for int64
        TableQuery((), (Filter("i", "eq", True),)),  # bool is not an int64
        TableQuery((), (Filter("x", "eq", BIG),)),  # not exactly representable as float64
        TableQuery((), (Filter("x", "eq", "1"),)),
        TableQuery((), (Filter("id", "eq", None),)),  # not nullable
        TableQuery((), (Filter("i", "lt", None),)),  # null has no range order
        TableQuery((), (Filter("x", "gt", NAN),)),  # NaN has no range order
        TableQuery((), (Filter("tags", "lt", (1,)),)),  # nested values are equality-only
        TableQuery((), (Filter("tags", "eq", (1.5,)),)),
        TableQuery((), (Filter("s", "eq", "\ud800"),)),  # not encodable as UTF-8
        TableQuery((), (Filter("i", "in", (1, "2")),)),
    ):
        with pytest.raises(ValueError):
            query.validate(SCHEMA)
    query = TableQuery([["x", "desc"]], [Filter("x", "in", [0, 1.5]), Filter("i", "eq", None)])
    validated = query.validate(SCHEMA)
    assert validated.sort == (("x", "desc"),) and validated.filters[0].value == (0.0, 1.5)
    assert type(validated.filters[0].value[0]) is float and validated.validate(SCHEMA) == validated
    assert validated.fields() == ("x", "i") and not validated.empty and TableQuery().empty
    assert TableQuery.from_dict(json.loads(json.dumps(validated.to_dict()))) == validated


def test_identity_is_stable_and_specific_to_query_and_artifact():
    tq = api()
    TableQuery, Filter = tq.TableQuery, tq.Filter
    query = TableQuery((("i", "asc"),), (Filter("i", "ne", BIG), Filter("x", "eq", NAN))).validate(SCHEMA)
    identity = query.identity("a" * 64)
    assert len(identity) == 64 and set(identity) <= set("0123456789abcdef")
    assert identity == TableQuery.from_dict(query.to_dict()).identity("a" * 64)
    assert identity != query.identity("b" * 64)
    assert identity != TableQuery((("i", "desc"),), query.filters).identity("a" * 64)
    assert identity != TableQuery(query.sort, (Filter("i", "ne", BIG - 1), query.filters[1])).identity("a" * 64)


def test_parse_query_reads_http_forms_with_tagged_json_values():
    tq = api()
    query = tq.parse_query(
        "x:desc,i:asc",
        'i:ge:{"$int64":"9007199254740993"},s:in:["a:b,c","\\u00e9"],x:ne:{"$float64":"nan"},'
        "tags:eq:[1,2],b:eq:true,i:ne:null,x:lt:2",
        SCHEMA,
    )
    assert query.sort == (("x", "desc"), ("i", "asc"))
    assert [(f.field, f.op) for f in query.filters] == [
        ("i", "ge"),
        ("s", "in"),
        ("x", "ne"),
        ("tags", "eq"),
        ("b", "eq"),
        ("i", "ne"),
        ("x", "lt"),
    ]
    values = [f.value for f in query.filters]
    assert values[0] == BIG and values[1] == ("a:b,c", "é") and math.isnan(values[2])
    assert values[3:] == [(1, 2), True, None, 2.0] and type(values[6]) is float
    assert tq.parse_query(None, None, SCHEMA).empty and tq.parse_query("", "", SCHEMA).empty
    for sort, filter in (
        ("i", None),
        ("i:up", None),
        ("i:asc,", None),
        ("i:asc,i:desc", None),
        ("nope:asc", None),
        (",".join(["i:asc"] * 12000), None),
        (None, "i"),
        (None, "i:eq"),
        (None, "i:eq:"),
        (None, "i:eq: 1"),
        (None, "i:eq:1x"),
        (None, "i:eq:1,"),
        (None, "i:eq:1;x:eq:2"),
        (None, "x:eq:NaN"),
        (None, "x:eq:Infinity"),
        (None, "i:eq:9007199254740993"),  # big integers need the $int64 tag
        (None, 'i:eq:{"$int64":"1"}'),
        (None, "i:eq:" + "[" * 50000),
        (None, "i:eq:" + "1" * 70000),
        (None, "i:between:1"),
        (None, 'i:eq:"1"'),
        (None, "nope:eq:1"),
    ):
        with pytest.raises(ValueError):
            tq.parse_query(sort, filter, SCHEMA)


def order_reference(rows, field, direction):
    """Documented order: -inf < finite < +inf < NaN; strings by code point; nulls last both ways; stable."""

    def key(row):
        value = row[field]
        if type(value) is float:
            return (1, 0.0) if math.isnan(value) else (0, value)
        if isinstance(value, tuple):
            return codec.dumps(value)
        return value

    present = sorted((r for r in rows if r[field] is not None), key=key, reverse=direction == "desc")
    return present + [r for r in rows if r[field] is None]


def test_sort_orders_int64_non_finite_strings_bools_and_nulls_exactly(tmp_path, mode):
    tq = api()
    reader = dataset(tmp_path / "artifact", SCHEMA, ROWS)
    original = list(reader.iter_rows())
    before = files(tmp_path / "artifact")
    for field in ("i", "x", "s", "b", "tags"):
        for direction in ("asc", "desc"):
            result = collect(reader, tq.TableQuery(((field, direction),)), tmp_path / "cache", limit=3)
            assert canon(result) == canon(order_reference(original, field, direction)), (field, direction)

    def ids(field, direction):
        return [r["id"] for r in collect(reader, tq.TableQuery(((field, direction),)), tmp_path / "cache")]

    assert ids("i", "asc") == [2, 4, 1, 0, 6, 3, 5]  # 2**53 < 2**53+1 (twice, stable) < 2**63-1, null last
    assert ids("i", "desc") == [3, 0, 6, 1, 4, 2, 5]
    assert ids("x", "asc") == [2, 3, 4, 5, 1, 0, 6]  # -inf, 0.0 == -0.0 (stable), 1.5, +inf, NaN, null
    assert ids("x", "desc") == [0, 1, 5, 3, 4, 2, 6]  # NaN, +inf, ..., -inf, null still last
    assert ids("s", "asc") == [4, 1, 0, 2, 5, 3, 6]  # "" < "Z" < "a" < "é" < U+FFFF < U+1F600 (not UTF-16)
    assert ids("b", "desc") == [0, 3, 5, 1, 4, 6, 2]
    page = tq.query_table(reader, tq.TableQuery((("x", "asc"),)), None, cache_dir=tmp_path / "cache")
    assert page.indexed and page.row_count == 7 and page.next_cursor is None
    assert page.to_dict() == {
        "rows": [codec.to_json(r) for r in page.rows],
        "next_cursor": None,
        "row_count": 7,
        "indexed": True,
    }
    exact = {r["id"]: r for r in page.rows}
    assert exact[0]["i"] == BIG and type(exact[3]["i"]) is int and exact[3]["i"] == 2**63 - 1
    assert sorted(canon(page.rows)) == sorted(canon(original)) and files(tmp_path / "artifact") == before


def test_filters_use_exact_comparisons_and_three_valued_nulls(tmp_path, mode):
    tq = api()
    Filter = tq.Filter
    reader = dataset(tmp_path / "artifact", SCHEMA, ROWS)
    cases = [
        (Filter("i", "eq", BIG), [0, 6]),  # not 1: 2**53 would equal 2**53+1 as a float
        (Filter("i", "ne", BIG), [1, 2, 3, 4]),  # null ≠ x is unknown: row 5 excluded
        (Filter("i", "ne", None), [0, 1, 2, 3, 4, 6]),  # IS NOT NULL
        (Filter("i", "eq", None), [5]),  # IS NULL
        (Filter("i", "gt", 2**53), [0, 3, 6]),
        (Filter("i", "le", -(2**63)), [2]),
        (Filter("i", "in", (2**53, None)), [1, 5]),
        (Filter("x", "eq", NAN), [0]),
        (Filter("x", "ne", NAN), [1, 2, 3, 4, 5]),
        (Filter("x", "gt", 1.0), [1, 5]),  # NaN is unordered for range filters
        (Filter("x", "lt", 0.0), [2]),  # -0.0 == 0.0
        (Filter("x", "ge", -0.0), [1, 3, 4, 5]),
        (Filter("x", "eq", 0), [3, 4]),
        (Filter("x", "in", (-INF, INF)), [1, 2]),
        (Filter("s", "lt", "a"), [1, 4]),
        (Filter("s", "gt", "￿"), [3]),  # code point order, not UTF-16 code units
        (Filter("b", "eq", True), [0, 3, 5]),
        (Filter("b", "ne", True), [1, 4, 6]),
        (Filter("b", "lt", True), [1, 4, 6]),
        (Filter("tags", "eq", (1, 2)), [0, 4]),
        (Filter("tags", "in", ((), (10,))), [1, 5, 6]),
    ]
    for item, expected in cases:
        rows = collect(reader, tq.TableQuery((), (item,)), tmp_path / "cache")
        assert [r["id"] for r in rows] == expected, item
    combined = tq.TableQuery((("x", "desc"),), (Filter("i", "ne", None), Filter("b", "eq", False)))
    assert [r["id"] for r in collect(reader, combined, tmp_path / "cache")] == [1, 4, 6]
    nothing = tq.query_table(reader, tq.TableQuery((), (Filter("s", "eq", "absent"),)), None, cache_dir=tmp_path / "c")
    assert nothing.rows == () and nothing.next_cursor is None and nothing.row_count == 0


def test_pages_are_stable_bounded_and_cursors_bound_to_their_index(tmp_path):
    tq = api()
    schema = DatasetSchema("rows", (Field("n", "int64"),))
    reader = dataset(tmp_path / "artifact", schema, [{"n": n % 7} for n in range(1200)])
    query = tq.TableQuery((("n", "desc"),))
    cache = tmp_path / "cache"
    first = tq.query_table(reader, query, None, limit=500, cache_dir=cache)
    second = tq.query_table(reader, query, first.next_cursor, limit=500, cache_dir=cache)
    third = tq.query_table(reader, query, second.next_cursor, limit=500, cache_dir=cache)
    assert [len(p.rows) for p in (first, second, third)] == [500, 500, 200] and third.next_cursor is None
    assert tq.query_table(reader, query, first.next_cursor, limit=500, cache_dir=cache) == second
    identity = query.validate(schema).identity(reader._identity)
    assert first.next_cursor == f"{identity}:500"
    other = tq.TableQuery((("n", "asc"),))
    for cursor in (
        tq.query_table(reader, other, None, limit=10, cache_dir=cache).next_cursor,  # another query's index
        reader.page(limit=10).next_cursor,  # the unindexed artifact's cursor
        "bad",
        f"{identity}:x",
        f"{identity}:-1",
        f"{identity}:01",
        f"{identity}:1200",
        f"{identity}:1:2",
        7,
    ):
        with pytest.raises(ValueError):
            tq.query_table(reader, query, cursor, cache_dir=cache)
    for limit in (0, 501, True, 1.0):
        with pytest.raises(ValueError):
            tq.query_table(reader, query, None, limit=limit, cache_dir=cache)
        with pytest.raises(ValueError):
            tq.query_table(reader, tq.TableQuery(), None, limit=limit, cache_dir=cache)
    # ~600 KB of JSON per row: the 1 MiB response bound caps the page before the row limit.
    wide = DatasetSchema("wide", (Field("k", "int64"), Field("text", "string")))
    big = dataset(tmp_path / "wide", wide, [{"k": k, "text": "\x01" * 100_000} for k in range(3)])
    pages = []
    cursor = None
    while True:
        page = tq.query_table(big, tq.TableQuery((("k", "desc"),)), cursor, limit=500, cache_dir=cache)
        pages.append([r["k"] for r in page.rows])
        if (cursor := page.next_cursor) is None:
            break
    assert pages == [[2], [1], [0]]
    huge = dataset(tmp_path / "huge", wide, [{"k": 0, "text": "\x01" * 200_000}])
    with pytest.raises(ValueError, match="exceeds JSON page limit"):
        tq.query_table(huge, tq.TableQuery((("k", "asc"),)), None, cache_dir=cache)


def test_empty_query_pages_the_artifact_and_empty_datasets_have_empty_results(tmp_path):
    tq = api()
    schema = DatasetSchema("rows", (Field("n", "int64"),))
    reader = dataset(tmp_path / "artifact", schema, [{"n": n} for n in range(150)])
    cache = tmp_path / "cache"
    page = tq.query_table(reader, tq.TableQuery(), None, cache_dir=cache)
    direct = reader.page(limit=100)
    assert page.rows == direct.rows and page.next_cursor == direct.next_cursor
    assert not page.indexed and page.row_count == 150
    assert (
        tq.query_table(reader, tq.TableQuery(), page.next_cursor, cache_dir=cache).rows
        == reader.page(direct.next_cursor).rows
    )
    assert not cache.exists()
    empty = dataset(tmp_path / "empty", schema, [])
    result = tq.query_table(empty, tq.TableQuery((("n", "asc"),)), None, cache_dir=cache)
    assert result.rows == () and result.next_cursor is None and result.row_count == 0
    identity = tq.TableQuery((("n", "asc"),)).identity(empty._identity)
    with pytest.raises(ValueError):
        tq.query_table(empty, tq.TableQuery((("n", "asc"),)), f"{identity}:0", cache_dir=cache)


def test_ready_index_is_reused_and_a_damaged_index_is_rebuilt(tmp_path, launches):
    tq = api()
    schema = DatasetSchema("rows", (Field("n", "int64"),))
    reader = dataset(tmp_path / "artifact", schema, [{"n": -n} for n in range(300)])
    before = files(tmp_path / "artifact")
    query = tq.TableQuery((("n", "asc"),))
    cache = tmp_path / "cache"
    first = tq.query_table(reader, query, None, cache_dir=cache)
    identity = query.identity(reader._identity)
    index = cache / reader._identity / f"{identity}.sqlite"
    assert sorted(p.name for p in index.parent.iterdir()) == [f"{identity}.ready", f"{identity}.sqlite"]
    stamp = index.stat().st_mtime_ns
    assert tq.query_table(reader, query, first.next_cursor, cache_dir=cache).rows[0]["n"] == -199
    assert len(launches) == 1 and index.stat().st_mtime_ns == stamp
    assert launches[0].returncode == 0
    index.write_bytes(b"not a database")
    assert tq.query_table(reader, query, None, cache_dir=cache) == first
    assert len(launches) == 2 and files(tmp_path / "artifact") == before
    (index.parent / f"{identity}.ready").unlink()
    assert tq.query_table(reader, query, None, cache_dir=cache) == first and len(launches) == 3


def test_tampered_chunk_fails_the_build_and_publishes_nothing(tmp_path):
    tq = api()
    schema = DatasetSchema("rows", (Field("n", "int64"),))
    reader = dataset(tmp_path / "artifact", schema, [{"n": n} for n in range(10)])
    chunk = tmp_path / "artifact" / "chunk-00000000.msgpack"
    data = bytearray(chunk.read_bytes())
    data[-1] ^= 1
    chunk.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="integrity failure"):
        tq.query_table(reader, tq.TableQuery((("n", "desc"),)), None, cache_dir=tmp_path / "cache")
    assert list((tmp_path / "cache" / reader._identity).iterdir()) == []


def test_cancel_and_timeout_kill_the_child_and_leave_no_index(tmp_path, launches):
    tq = api()
    schema = DatasetSchema("rows", (Field("n", "int64"),))
    reader = dataset(tmp_path / "artifact", schema, [{"n": n} for n in range(10)])
    query = tq.TableQuery((("n", "desc"),))
    directory = tmp_path / "cache" / reader._identity
    with pytest.raises(tq.TableQueryCancelled):
        tq.query_table(reader, query, None, cache_dir=tmp_path / "cache", cancel=lambda: True)
    assert launches[-1].returncode != 0 and list(directory.iterdir()) == []
    with pytest.raises(TimeoutError):
        tq.query_table(reader, query, None, cache_dir=tmp_path / "cache", timeout=0)
    assert launches[-1].returncode != 0 and list(directory.iterdir()) == []
    assert len(tq.query_table(reader, query, None, cache_dir=tmp_path / "cache", cancel=lambda: False).rows) == 10


def test_large_float_sort_spills_with_bounded_pages(tmp_path, monkeypatch):
    tq = api()
    monkeypatch.setattr(tq, "CACHE_KIB", 64)  # forces the external sorter to spill to temp files
    generator = random.Random(7)
    specials = (NAN, INF, -INF, None, 0.0, -0.0)
    rows = [
        {"id": n, "x": generator.choice(specials) if generator.random() < 0.02 else generator.uniform(-1e6, 1e6)}
        for n in range(50_000)
    ]
    schema = DatasetSchema("large", (Field("id", "int64"), Field("x", "float64", nullable=True)))
    reader = dataset(tmp_path / "artifact", schema, rows)
    for direction in ("asc", "desc"):
        result = collect(reader, api().TableQuery((("x", direction),)), tmp_path / "cache", limit=500)
        assert canon(result) == canon(order_reference(list(reader.iter_rows()), "x", direction))


def test_child_entrypoint_runs_in_process(tmp_path, monkeypatch):
    tq = api()
    reader = dataset(tmp_path / "artifact", SCHEMA, ROWS)
    query = tq.TableQuery((("s", "desc"),), (tq.Filter("i", "ne", None),)).validate(SCHEMA)
    identity = query.identity(reader._identity)
    config = tmp_path / "config.json"
    destination = tmp_path / "index.sqlite"
    config.write_text(
        json.dumps(
            {
                "root": str(reader.root),
                "manifest": reader.manifest.to_dict(),
                "query": query.to_dict(),
                "destination": str(destination),
                "work": str(tmp_path / "work.sqlite"),
                "identity": identity,
                "cache_kib": 64,
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["table_query", str(config)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exited:
            runpy.run_module("simulatte.analysis.table_query", run_name="__main__")
    assert exited.value.code == 0 and not (tmp_path / "work.sqlite").exists()
    page = tq._page(destination, identity, SCHEMA, 0, 100, 6)
    assert [r["id"] for r in page.rows] == [3, 2, 0, 1, 4, 6]
