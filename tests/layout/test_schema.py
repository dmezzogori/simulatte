import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from simulatte.layout.schema import parse_patch, schema, validate_definition
from simulatte.layout import LayoutError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "layout"


def test_schema_valid():
    Draft202012Validator.check_schema(schema())


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_fixtures_agree(path):
    value = json.loads(path.read_text())
    valid = path.stem.startswith("valid")
    assert Draft202012Validator(schema()).is_valid(value) == valid
    if valid:
        assert parse_patch(value) == value
    else:
        with pytest.raises(LayoutError):
            parse_patch(value)


@pytest.mark.parametrize("number", [True, float("nan"), float("inf"), 10**309])
def test_runtime_numeric_constraints(number):
    with pytest.raises(LayoutError):
        parse_patch({"schema_version": "1.0", "grid": {"dx": number}})


def test_parser_copies_input():
    value = {"schema_version": "1.0", "placements": {"a": {"kind": "server", "position": [0, 0, 0]}}}
    copied = parse_patch(value)
    copied["placements"]["a"]["position"][0] = 1
    assert value["placements"]["a"]["position"][0] == 0


def test_typeless_patch_cannot_mix_incompatible_variants():
    value = {"schema_version": "1.0", "network": {"bounds": [0, 0, 1, 1], "lanes": {}}}
    assert not Draft202012Validator(schema()).is_valid(value)
    with pytest.raises(LayoutError):
        parse_patch(value)


def test_required_patch_leaf_unset_is_deferred_until_resolution():
    value = {
        "schema_version": "1.0",
        "grid": {"dx": {"$unset": True}},
        "network": {"type": "lattice", "bounds": {"$unset": True}},
    }
    assert Draft202012Validator(schema()).is_valid(value)
    assert parse_patch(value) == value


def test_connector_requires_node_address():
    value = {
        "schema_version": "1.0",
        "placements": {
            "a": {"kind": "server", "ports": {"p": {"connector": {"target": {"kind": "value", "path": ["grid"]}}}}}
        },
    }
    assert not Draft202012Validator(schema()).is_valid(value)
    with pytest.raises(LayoutError):
        parse_patch(value)


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": "1.0", "grid": {1: 1}},
        {"schema_version": "1.0", "grid": {"dx": object()}},
        {"schema_version": "1.0", "grid": {"dx": []}},
        {"schema_version": "1.0", "grid": {"dx": "1"}},
        {"schema_version": "1.0", "placements": {"": {"kind": "server"}}},
        {"schema_version": "1.0", "placements": {"a\0b": {"kind": "server"}}},
        {"schema_version": "1.0", "placements": {"a": {"kind": "server", "visual": {"$unset": False}}}},
        {"schema_version": "1.0", "grid": {"origin": [0, 0, 0, 0]}},
        {"schema_version": "1.0", "grid": {"origin": [0, 0]}},
        {"schema_version": "1.0", "network": {"neighbors": 4.5}},
    ],
)
def test_bad_structure_is_rejected_by_both_validators(value):
    assert not Draft202012Validator(schema()).is_valid(value)
    with pytest.raises(LayoutError):
        parse_patch(value)


def test_nesting_limit_schema_copy_and_duplicate_incidents():
    from simulatte.layout import Address

    with pytest.raises(LayoutError, match="max_nesting"):
        parse_patch({"schema_version": "1.0", "grid": {"origin": [0, 0, 0]}}, max_nesting=2)
    changed = schema()
    changed["properties"].clear()
    assert "grid" in schema()["properties"]
    incident = {"kind": "lane_vertex", "lane": "a", "vertex": "b"}
    with pytest.raises(LayoutError):
        Address({"kind": "intersection", "incidents": [incident, incident]})


@pytest.mark.parametrize(
    "patch",
    [
        {"network": {"clearance": {"$unset": True}, "neighbors": {"$unset": True}}},
        {
            "placements": {
                "a": {
                    "kind": "warehouse",
                    "footprint": {"width": {"$unset": True}, "depth": {"$unset": True}, "height": {"$unset": True}},
                    "ports": {
                        "p": {
                            "position": {"$unset": True},
                            "approach": {"$unset": True},
                            "connector": {"approach_margin": {"$unset": True}},
                        }
                    },
                }
            }
        },
        {
            "network": {
                "blocked": {
                    "wall": {
                        "position": {"$unset": True},
                        "width": {"$unset": True},
                        "depth": {"$unset": True},
                        "rotation": {"$unset": True},
                    }
                }
            }
        },
        {"network": {"type": "lanes", "lanes": {"road": {"vertices": {"$unset": True}}}}},
        {"network": {"type": "explicit", "nodes": [{"id": "a", "position": {"$unset": True}}]}},
    ],
)
def test_patch_geometry_unset_waits_for_resolved_requirements(patch):
    value = {"schema_version": "1.0", **patch}
    assert Draft202012Validator(schema()).is_valid(value)
    assert parse_patch(value) == value


@pytest.mark.parametrize("value", [2**53, 2**53 + 1, 10**308, float(2**53)])
def test_large_geometry_matches_schema_and_preserves_authored_number_type(value):
    patch = {
        "schema_version": "1.0",
        "grid": {"dx": value},
        "placements": {"a": {"kind": "server", "position": [value, -value, 0]}},
    }
    assert Draft202012Validator(schema()).is_valid(patch)
    copied = parse_patch(patch)
    assert copied == patch
    assert type(copied["grid"]["dx"]) is type(value)
    assert type(copied["placements"]["a"]["position"][0]) is type(value)


@pytest.mark.parametrize("value", [2**53, float(2**53)])
def test_safe_integer_limits_still_apply_to_counts_and_indices(value):
    patches = [
        {"schema_version": "1.0", "network": {"capacity": value}},
        {
            "schema_version": "1.0",
            "overrides": [{"id": "o", "target": {"kind": "lattice", "i": value, "j": 0}, "properties": {}}],
        },
    ]
    for patch in patches:
        assert not Draft202012Validator(schema()).is_valid(patch)
        with pytest.raises(LayoutError):
            parse_patch(patch)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ([1, 1.0], False),
        ([0, -0.0], False),
        ([True, 1], True),
        ([False, 0], True),
        ([{"i": 1, "j": 0}, {"j": -0.0, "i": 1.0}], False),
        ([{"x": [True, {"y": 0}]}, {"x": [1, {"y": -0.0}]}], True),
        ([{"x": [1, {"y": 0}]}, {"x": [1.0, {"y": -0.0}]}], False),
        ([1, "1", None], True),
        ([None, None], False),
    ],
)
def test_unique_items_uses_json_equality(value, valid):
    from simulatte.layout.schema import _validate

    rule = {"type": "array", "uniqueItems": True}
    assert Draft202012Validator(rule).is_valid(value) == valid
    if valid:
        _validate(value, rule, ())
    else:
        with pytest.raises(LayoutError, match="duplicate array item"):
            _validate(value, rule, ())


def _discriminated():
    from simulatte.layout.schema import _schema

    definitions = _schema()["$defs"]
    return {name: definitions[name]["oneOf"] for name in ("address", "nodeAddress", "elementAddress", "selector")}


def test_a_kind_filter_leaves_the_outcome_of_every_discriminated_rule_unchanged():
    """``oneOf`` over branches that fix ``kind`` only evaluates the branches of the value's kind: for values of every
    kind, of a swapped kind, of a boolean or numeric kind, without a kind and with extra fields, the outcome equals
    that of counting the matches of every branch."""
    from simulatte.layout.schema import _matches, _validate

    def probes(branch):
        fixed = {key: rule["const"] for key, rule in branch["properties"].items() if "const" in rule}
        filled = {key: "x" if rule.get("$ref") == "#/$defs/id" else 1 for key, rule in branch["properties"].items()}
        filled.update(fixed)
        yield filled
        yield {**filled, "extra": 1}
        yield {key: value for key, value in filled.items() if key != "kind"}
        for kind in (True, 1, None, "", "unknown", ["explicit"]):
            yield {**filled, "kind": kind}

    for name, branches in _discriminated().items():
        for branch in branches:
            for value in probes(branch):
                expected = sum(_matches(value, other, ()) for other in branches)
                try:
                    _validate(value, {"$ref": f"#/$defs/{name}"}, ())
                except LayoutError as error:
                    assert expected != 1
                    assert str(error) == "$: does not match oneOf schema branches"
                else:
                    assert expected == 1


def test_validation_messages_are_those_of_the_first_failure():
    for definition, value, message, path in [
        ("address", {"kind": "lattice", "i": 1}, "$: does not match oneOf schema branches", ()),
        ("address", {"kind": "lattice", "i": 2**60, "j": 0}, "$: does not match oneOf schema branches", ()),
        ("explicitNode", {"id": "a", "position": [0, 0, 0], "extra": 1}, "$: unknown field extra", ()),
        ("explicitNode", {"id": "", "position": [0, 0, 0]}, "$/id: invalid string", ("id",)),
    ]:
        with pytest.raises(LayoutError) as caught:
            validate_definition(value, definition)
        assert str(caught.value) == message
        assert [(item.code, item.path) for item in caught.value.diagnostics] == [("schema-error", path)]
