"""Bounded strict input and author-preserving atomic persistence."""

import json

import pytest

from simulatte.layout.errors import LayoutError
from simulatte.layout.io import load_layout, save_layout
from simulatte.layout.layers import FileLayer
from simulatte.layout.types import LayoutLimits


def test_parse_exact_bytes_without_reopening_file(tmp_path):
    from simulatte.layout.io import parse_layout

    path = tmp_path / "layout.json"
    raw = b'{"schema_version":"1.0","grid":{"dx":2}}'
    path.write_bytes(raw)
    captured = path.read_bytes()
    path.write_text("invalid external edit")
    assert parse_layout(captured).to_dict()["grid"]["dx"] == 2
    with pytest.raises(LayoutError, match="max_file_bytes"):
        parse_layout(raw, limits=LayoutLimits(max_file_bytes=4))


@pytest.mark.parametrize(
    "text,match",
    [
        ('{"schema_version":"1.0","grid":{"dx":1,"dx":2}}', "duplicate"),
        ('{"schema_version":"2.0"}', "upgrade"),
        ('{"schema_version":"1.0","unknown":1}', "unknown"),
        ('{"schema_version":"1.0","grid":{"dx":{"$unset":false}}}', "schema"),
        ('{"schema_version":"1.0","grid":{"dx":NaN}}', "finite"),
        ("{", "JSON"),
    ],
)
def test_invalid_input(tmp_path, text, match):
    path = tmp_path / "layout.json"
    path.write_text(text)
    with pytest.raises(LayoutError, match=match):
        load_layout(path, limits=LayoutLimits())


def test_file_size_depth_and_utf8_limits(tmp_path):
    path = tmp_path / "layout.json"
    path.write_bytes(b"x" * 101)
    with pytest.raises(LayoutError, match="max_file_bytes"):
        load_layout(path, limits=LayoutLimits(max_file_bytes=100))
    path.write_text('{"schema_version":"1.0","grid":{"origin":[0,0,0]}}')
    with pytest.raises(LayoutError, match="max_nesting"):
        load_layout(path, limits=LayoutLimits(max_nesting=2))
    path.write_text("[" * 1500 + "]" * 1500)
    with pytest.raises(LayoutError, match="max_nesting"):
        load_layout(path, limits=LayoutLimits())
    path.write_bytes(b"\xff")
    with pytest.raises(LayoutError, match="UTF-8"):
        load_layout(path, limits=LayoutLimits())


def test_roundtrip_preserves_orphans_stale_unset_and_float_without_flattening(tmp_path):
    raw = {
        "schema_version": "1.0",
        "placements": {
            "gone": {"kind": "server", "position": [0.10000000000000002, 1e-300, 0]},
            "wrong": {"kind": "warehouse", "rotation": {"$unset": True}, "visual": {"label": '雪[\\"{}]'}},
        },
        "network": {"clearance": 0.4},
        "overrides": [
            {"id": "stale", "target": {"kind": "explicit", "id": "missing"}, "properties": {"enabled": False}}
        ],
    }
    path = tmp_path / "layout.json"
    save_layout(path, FileLayer(raw))
    assert json.loads(path.read_text()) == raw
    assert load_layout(path, limits=LayoutLimits()).to_dict() == raw
    assert "0.10000000000000002" in path.read_text()
    assert path.read_text().index('"network"') < path.read_text().index('"placements"')


def test_atomic_replace_failure_preserves_destination_and_removes_temp(tmp_path, monkeypatch):
    import simulatte.layout.io as io

    path = tmp_path / "layout.json"
    path.write_text("previous")

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(io.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        save_layout(path, FileLayer({"schema_version": "1.0"}))
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_placement_limit_and_schema_invalid_orphans_are_not_ignored(tmp_path):
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps({"schema_version": "1.0", "placements": {"a": {"kind": "server"}, "b": {"kind": "server"}}})
    )
    with pytest.raises(LayoutError, match="max_placements"):
        load_layout(path, limits=LayoutLimits(max_placements=1))
    path.write_text('{"schema_version":"1.0","placements":{"gone":{"kind":"server","bogus":1}}}')
    with pytest.raises(LayoutError, match="unknown field"):
        load_layout(path)


def test_json_escaped_surrogate_rejected_but_valid_pair_accepted(tmp_path):
    path = tmp_path / "layout.json"
    path.write_text(r'{"schema_version":"1.0","placements":{"\ud800":{"kind":"server"}}}')
    with pytest.raises(LayoutError, match="UTF-8"):
        load_layout(path)
    path.write_text(r'{"schema_version":"1.0","placements":{"\ud83d\udce6":{"kind":"server"}}}')
    assert "📦" in load_layout(path).data["placements"]
