"""The analytical codec must be exact, bounded and independently verifiable."""

from dataclasses import replace
import importlib
import importlib.util
import io
import json
import math

import pytest

from simulatte.analysis import DatasetSchema, Field


def api():
    assert importlib.util.find_spec("simulatte.analysis.datasets") is not None, "dataset codec missing"
    return importlib.import_module("simulatte.analysis.datasets")


def test_missingness_and_exact_numeric_roundtrip(tmp_path):
    d = api()
    from simulatte.analysis.codec import from_json, to_json

    schema = DatasetSchema(
        "numbers",
        (
            Field("n", "int64"),
            Field("x", "float64"),
            Field("label", "string", nullable=True),
            Field("nested", {"record": {"flags": {"list": "bool"}}}),
        ),
    )
    writer = d.DatasetWriter(tmp_path, schema)
    for x in (1.5, float("nan"), float("inf"), -float("inf")):
        writer.append({"n": 2**60, "x": x, "label": None, "nested": {"flags": [True, False]}})
    manifest = writer.finish()
    reader = d.DatasetReader(tmp_path, manifest)
    rows = list(reader.iter_rows())
    assert rows[0] == {"n": 2**60, "x": 1.5, "label": None, "nested": {"flags": (True, False)}}
    assert math.isnan(rows[1]["x"]) and rows[2]["x"] == math.inf and rows[3]["x"] == -math.inf
    restored = from_json(json.loads(json.dumps(to_json(rows[0]), allow_nan=False)))
    assert restored["n"] == 2**60 and restored["label"] is None
    assert math.isnan(from_json(to_json(rows[1]))["x"])
    assert from_json(to_json({"$int64": "literal"})) == {"$int64": "literal"}
    csv = io.StringIO()
    reader.to_csv(csv)
    csv.seek(0)
    restored = list(d.read_csv(csv, schema))
    assert restored[0] == rows[0] and math.isnan(restored[1]["x"])
    assert len(restored) == 4


def test_empty_boundaries_and_page_limits(tmp_path):
    d = api()
    schema = DatasetSchema("rows", (Field("x", "int64"),))
    empty = d.DatasetWriter(tmp_path / "empty", schema).finish()
    assert empty.row_count == 0 and empty.chunks == ()
    assert d.DatasetReader(tmp_path / "empty", empty).page().rows == ()
    writer = d.DatasetWriter(tmp_path / "filled", schema)
    for i in range(4097):
        writer.append({"x": i})
    manifest = writer.finish()
    assert [c.rows for c in manifest.chunks] == [4096, 1]
    assert writer.finish() == manifest
    with pytest.raises(RuntimeError):
        writer.append({"x": 0})
    reader = d.DatasetReader(tmp_path / "filled", manifest)
    first = reader.page(limit=100)
    assert len(first.rows) == 100 and first.rows[-1]["x"] == 99
    assert reader.page(first.next_cursor, limit=100).rows[0]["x"] == 100
    for limit in (0, 501, True):
        with pytest.raises(ValueError):
            reader.page(limit=limit)
    for cursor in ("bad", "-1", "999999999"):
        with pytest.raises(ValueError):
            reader.page(cursor)
    assert d.DatasetManifest.from_dict(manifest.to_dict()) == manifest


def test_chunk_integrity_and_path_refusal(tmp_path):
    d = api()
    schema = DatasetSchema("rows", (Field("x", "int64"),))
    writer = d.DatasetWriter(tmp_path, schema)
    for i in range(4097):
        writer.append({"x": i})
    manifest = writer.finish()
    for chunks in (manifest.chunks[::-1], (manifest.chunks[0],) * 2, (replace(manifest.chunks[0], path="../escape"),)):
        with pytest.raises(ValueError):
            list(d.DatasetReader(tmp_path, replace(manifest, chunks=chunks)).iter_rows())
    path = tmp_path / manifest.chunks[0].path
    original = path.read_bytes()
    for data in (original[:-1], b"x" * len(original)):
        path.write_bytes(data)
        with pytest.raises(ValueError, match="integrity"):
            list(d.DatasetReader(tmp_path, manifest).iter_rows())
    path.unlink()
    outside = tmp_path.parent / "outside.msgpack"
    outside.write_bytes(original)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="path"):
        list(d.DatasetReader(tmp_path, manifest).iter_rows())


def test_byte_bounds_and_single_row_diagnostic(tmp_path):
    d = api()
    schema = DatasetSchema("strings", (Field("text", "string"),))
    writer = d.DatasetWriter(tmp_path, schema)
    for _ in range(10):
        writer.append({"text": "x" * 200_000})
    with pytest.raises(ValueError, match="strings.*256 KiB"):
        writer.append({"text": "x" * 262_144})
    manifest = writer.finish()
    assert len(manifest.chunks) == 2 and all(c.length <= 1 << 20 for c in manifest.chunks)
    reader = d.DatasetReader(tmp_path, manifest)
    page = reader.page(limit=100)
    assert len(json.dumps(page.to_dict()).encode()) <= 1 << 20
    assert page.next_cursor is not None


def test_million_rows_bounded_buffers(tmp_path):
    d = api()
    schema = DatasetSchema("rows", (Field("x", "int64"),))
    writer = d.DatasetWriter(tmp_path, schema)
    for i in range(1_000_000):
        writer.append({"x": i})
    manifest = writer.finish()
    assert manifest.row_count == 1_000_000
    assert writer.peak_buffer_rows <= 4096 and writer.peak_buffer_bytes <= 1 << 20
    reader = d.DatasetReader(tmp_path, manifest)
    assert len(reader.page(limit=100).rows) == 100 and reader.chunks_read == 1
    assert sum(1 for _ in reader.iter_rows()) == 1_000_000
    assert reader.peak_buffer_rows <= 4096 and reader.peak_buffer_bytes <= 1 << 20
    print(
        f"actual buffers: writer={writer.peak_buffer_rows}/{writer.peak_buffer_bytes}, "
        f"reader={reader.peak_buffer_rows}/{reader.peak_buffer_bytes}"
    )


def test_environment_streams_and_reports_measurement_failure(tmp_path):
    d = api()
    from simulatte import Environment
    from simulatte.analysis import MeasurementPlan, ObservationCollector

    schema = DatasetSchema("rows", (Field("x", "int64"),))
    with Environment(seed=1) as env:
        env.configure_measurements(MeasurementPlan("rows", (schema,)), output_dir=tmp_path)
        c = ObservationCollector(schema, fields={}).attach(env)
        for i in range(5000):
            c.record(None, observed_at="observation", values={"x": i})
        assert c._rows is None
    manifest = d.DatasetManifest.from_dict(json.loads((tmp_path / "datasets/rows/manifest.json").read_text()))
    assert manifest.row_count == 5000
    assert list(d.DatasetReader(tmp_path / "datasets/rows", manifest).iter_rows())[-1]["x"] == 4999


@pytest.mark.parametrize(
    "value,dtype",
    [
        ("x" * 150000, "string"),
        ("\U0001f680" * 50000, "string"),
        ({"text": ["x" * 150000]}, {"record": {"text": {"list": "string"}}}),
        ({"text": ["\U0001f680" * 50000]}, {"record": {"text": {"list": "string"}}}),
    ],
    ids=["large-scalar", "unicode-scalar", "large-record", "unicode-record"],
)
def test_csv_supported_large_cells_and_unicode_expansion(tmp_path, value, dtype):
    d = api()
    import csv

    before_limit = csv.field_size_limit()
    schema = DatasetSchema("large", (Field("value", dtype),))
    writer = d.DatasetWriter(tmp_path, schema)
    writer.append({"value": value})
    reader = d.DatasetReader(tmp_path, writer.finish())
    stream = io.StringIO()
    reader.to_csv(stream)
    assert len(stream.getvalue()) > 131072
    stream.seek(0)
    assert list(d.read_csv(stream, schema)) == [schema.row({"value": value})]
    assert csv.field_size_limit() == before_limit


def test_csv_parser_bounds_quoting_and_schema():
    d = api()
    from simulatte.analysis.datasets import _csv_records

    assert list(_csv_records(io.StringIO('a,"b,c","d""e",\r\n'))) == [["a", "b,c", 'd"e', ""]]
    for text in ('"unterminated\n', '"closed"x\n', 'bad"quote\n', "x" * ((8 << 20) + 1)):
        with pytest.raises(ValueError):
            list(_csv_records(io.StringIO(text)))
    schema = DatasetSchema("x", (Field("value", "string"),))
    for text in ("wrong\n", "value\n1,2\n", 'value\n"""' + "x" * 270000 + '"""\n'):
        with pytest.raises(ValueError):
            list(d.read_csv(io.StringIO(text), schema))


def test_persisted_population_memberships_are_not_lost(tmp_path):
    d = api()
    from simulatte import Environment
    from simulatte.analysis import MeasurementPlan, ObservationCollector, Population

    schema = DatasetSchema("jobs", (Field("x", "int64"),))
    populations = (Population("positive", lambda row: row["x"] > 0), Population("all", lambda row: True))
    with Environment(seed=1) as env:
        env.configure_measurements(MeasurementPlan("jobs", (schema,), populations), output_dir=tmp_path)
        c = ObservationCollector(schema, fields={}, populations=populations).attach(env)
        c.record(None, observed_at="observation", values={"x": 1})
        c.record(None, observed_at="observation", values={"x": -1})
    root = tmp_path / "datasets/jobs"
    manifest = d.DatasetManifest.from_dict(json.loads((root / "manifest.json").read_text()))
    rows = list(d.DatasetReader(root, manifest).iter_rows())
    assert rows[0]["populations"] == ("positive", "all")
    assert rows[1]["populations"] == ("all",)


def test_reader_refuses_structurally_invalid_verified_chunks(tmp_path):
    d = api()
    import hashlib
    import msgpack

    schema = DatasetSchema("jobs", (Field("x", "int64"),))
    from simulatte.analysis.datasets import Chunk

    for payload, rows in (
        (b"\xc1", 1),
        (msgpack.packb({}), 1),
        (msgpack.packb([[]]), 1),
        (msgpack.packb([[1], [2]]), 1),
        (msgpack.packb([["wrong"]]), 1),
    ):
        (tmp_path / "chunk-00000000.msgpack").write_bytes(payload)
        manifest = d.DatasetManifest(
            schema,
            {},
            (Chunk("chunk-00000000.msgpack", len(payload), hashlib.sha256(payload).hexdigest(), rows),),
            rows,
        )
        with pytest.raises(ValueError):
            list(d.DatasetReader(tmp_path, manifest).iter_rows())


def test_manifest_and_reader_refuse_inconsistent_counts(tmp_path):
    d = api()
    schema = DatasetSchema("rows", (Field("x", "int64"),))
    writer = d.DatasetWriter(tmp_path, schema)
    writer.append({"x": 1})
    manifest = writer.finish()
    data = manifest.to_dict()
    for tampered in (
        {**data, "codec": "analytical-dataset-v2"},
        {**data, "extra": 1},
        {k: v for k, v in data.items() if k != "window"},
    ):
        with pytest.raises(ValueError, match="Unsupported"):
            d.DatasetManifest.from_dict(tampered)
    for count in (-1, True, 2):
        with pytest.raises(ValueError, match="row count"):
            d.DatasetReader(tmp_path, replace(manifest, row_count=count))


def test_page_cursors_skip_whole_chunks_and_reject_foreign_positions(tmp_path):
    d = api()
    schema = DatasetSchema("rows", (Field("x", "int64"),))
    writer = d.DatasetWriter(tmp_path, schema)
    for i in range(4097):
        writer.append({"x": i})
    reader = d.DatasetReader(tmp_path, writer.finish())
    last = reader.page(f"{reader._identity}:4096", limit=10)
    assert [row["x"] for row in last.rows] == [4096] and last.next_cursor is None
    assert reader.chunks_read == 1
    for cursor in (f"{'0' * 64}:1", f"{reader._identity}:4097", f"{reader._identity}:01", f"{reader._identity}:-1"):
        with pytest.raises(ValueError, match="cursor"):
            reader.page(cursor)


def test_json_page_refuses_row_larger_than_the_page_limit(tmp_path):
    d = api()
    schema = DatasetSchema("strings", (Field("text", "string"),))
    writer = d.DatasetWriter(tmp_path, schema)
    writer.append({"text": "\x00" * 250_000})  # 250 KB as msgpack, 1.5 MB once JSON-escaped
    reader = d.DatasetReader(tmp_path, writer.finish())
    with pytest.raises(ValueError, match="streaming dataset export"):
        reader.page()
    stream = io.StringIO()
    reader.to_csv(stream)
    assert len(stream.getvalue()) > 1 << 20
