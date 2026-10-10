"""Deferred targets, deterministic diagnostics and Unicode boundary validation."""

import pytest
from jsonschema import Draft202012Validator

from simulatte.layout.errors import LayoutError
from simulatte.layout.layers import FileLayer, resolve_layers, validate_targets
from simulatte.layout.refs import Address
from simulatte.layout.schema import parse_patch, schema


def test_wrong_kind_is_quarantined_and_unknown_remains_pending_until_final():
    file = FileLayer(
        {
            "schema_version": "1.0",
            "placements": {
                "a": {"kind": "warehouse", "position": [9, 9, 0]},
                "later": {"kind": "server", "position": [1, 2, 0]},
            },
        }
    )
    code = {"schema_version": "1.0", "placements": {"a": {"kind": "server", "position": [0, 0, 0]}}}
    values = resolve_layers((("declare", code),), file)
    assert values.data["placements"]["a"]["position"] == (0, 0, 0)
    assert "later" not in values.data["placements"]
    assert all(d.code != "orphan-placement" for d in values.diagnostics)
    assert [d.code for d in validate_targets(values, {})] == ["kind-mismatch", "orphan-placement"]
    final = resolve_layers((("declare", code),), file, targets={"later": "server"})
    assert final.data["placements"]["later"]["position"] == (1, 2, 0)
    assert [d.code for d in validate_targets(final, {"later": "server"})] == ["kind-mismatch"]
    assert file.to_dict()["placements"]["a"]["position"] == [9, 9, 0]


def test_non_placeable_targets_and_sorted_diagnostics():
    file = FileLayer(
        {
            "schema_version": "1.0",
            "placements": {"z": {"kind": "job"}, "b": {"kind": "server"}, "a": {"kind": "server"}},
        }
    )
    values = resolve_layers((), file, targets={"z": "job"})
    assert "z" not in values.data.get("placements", {})
    assert [(d.path, d.code) for d in validate_targets(values, {"z": "job"})] == [
        (("placements", "a"), "orphan-placement"),
        (("placements", "b"), "orphan-placement"),
        (("placements", "z"), "non-placeable-target"),
    ]


@pytest.mark.parametrize("bad", ["\ud800", "\udfff", "a\ud800b"])
def test_non_utf8_ids_rejected_at_address_and_schema_boundary(bad):
    raw = {"schema_version": "1.0", "placements": {bad: {"kind": "server"}}}
    assert not Draft202012Validator(schema()).is_valid(raw)
    with pytest.raises(LayoutError):
        parse_patch(raw)
    with pytest.raises(LayoutError):
        Address({"kind": "explicit", "id": bad})


def test_non_utf8_visual_strings_have_schema_runtime_parity():
    raw = {"schema_version": "1.0", "placements": {"a": {"kind": "server", "visual": {"label": "\ud800"}}}}
    assert not Draft202012Validator(schema()).is_valid(raw)
    with pytest.raises(LayoutError):
        parse_patch(raw)


def test_supplementary_unicode_is_valid_and_encoded_in_addresses():
    raw = {"schema_version": "1.0", "placements": {"📦": {"kind": "server", "visual": {"label": "雪📦"}}}}
    assert Draft202012Validator(schema()).is_valid(raw)
    assert parse_patch(raw) == raw
    address = Address({"kind": "explicit", "id": "📦"})
    assert bytes.fromhex(address.encoded_id.removeprefix("layout:")).decode("utf-8") == '{"id":"📦","kind":"explicit"}'


def test_code_diagnostic_order_is_stage_order_before_paths():
    stages = (
        ("first", {"schema_version": "1.0", "placements": {"z": {"kind": "job"}}}),
        ("second", {"schema_version": "1.0", "placements": {"a": {"kind": "order"}}}),
    )
    values = resolve_layers(stages, FileLayer({"schema_version": "1.0", "placements": {"0": {"kind": "server"}}}))
    assert [(d.stage, d.target) for d in validate_targets(values, {})] == [("first", "z"), ("second", "a"), (None, "0")]
