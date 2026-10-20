"""presentation-v1 manifests: one schema, shared fixtures, identical paths and hashes in Python and TypeScript."""

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from simulatte.execution.hashing import canonical_hash
from simulatte.presentations.schema import (
    MANIFEST_SCHEMA_PATH,
    ManifestError,
    json_value,
    output_content_hash,
    snapshot_hash,
    validate_manifest,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "presentations"
CASES = sorted(FIXTURES.glob("valid-*.json")) + sorted(FIXTURES.glob("invalid-*.json"))


def fixture(name="valid-full"):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def schema_document():
    return json.loads(MANIFEST_SCHEMA_PATH.read_text())


def failure(document):
    with pytest.raises(ManifestError) as caught:
        validate_manifest(document)
    return caught.value.path


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_shared_fixtures_accept_and_reject_like_typescript(path):
    document = json.loads(path.read_text())
    if path.stem.startswith("valid-"):
        assert validate_manifest(document) == json_value(document)
        return
    expect = path.parent / f"{path.stem}.expect.txt"
    reported = failure(document)
    if expect.exists():
        assert reported == expect.read_text().strip()


def test_fixture_directory_has_both_outcomes():
    names = [path.stem for path in CASES]
    assert "valid-minimal" in names and "valid-full" in names
    assert sum(name.startswith("invalid-") for name in names) >= 10


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda d: d.pop("notes"), "notes"),
        (lambda d: d.update(schema=True), "schema"),
        (lambda d: d.update(id="not-a-uuid"), "id"),
        (lambda d: d.update(revision=1.5), "revision"),
        (lambda d: d.update(revision=True), "revision"),
        (lambda d: d.update(name="x" * 201), "name"),
        (lambda d: d.update(name="tab\u0001"), "name"),
        (lambda d: d.update(name="lone \ud800"), "name"),
        (lambda d: d.update(notes="n" * 4001), "notes"),
        (lambda d: d.update(recording=[]), "recording"),
        (lambda d: d["recording"].update(request_id="B" * 64), "recording.request_id"),
        (lambda d: d["recording"].update(trace_sha256="d" * 63), "recording.trace_sha256"),
        (lambda d: d["recording"].pop("trace_sha256"), "recording.trace_sha256"),
        (lambda d: d.update(layout={"schema_version": "1.0"}), "layout"),
        (lambda d: d.update(layout=[]), "layout"),
        (lambda d: d["camera"].update(bounds=[0, 0, 1]), "camera.bounds"),
        (lambda d: d["camera"].update(bounds=[0, 1, 1, 1]), "camera.bounds"),
        (lambda d: d["camera"].update(bounds=[0, 0, math.inf, 1]), "camera.bounds"),
        (lambda d: d["camera"].update(bounds=[False, 0, 1, 1]), "camera.bounds"),
        (lambda d: d["visibility"].update(entities="all"), "visibility.entities"),
        (lambda d: d["visibility"].update(labels=[""]), "visibility.labels[0]"),
        (lambda d: d["visibility"].update(labels=["x"] * 10001), "visibility.labels"),
        (lambda d: d.update(measurements=["a", "a"]), "measurements"),
        (lambda d: d.update(measurements=[3]), "measurements[0]"),
        (lambda d: d["time"].update(start=math.nan), "time.start"),
        (lambda d: d["time"].update(end="1"), "time.end"),
        (lambda d: d.update(rate=1025), "rate"),
        (lambda d: d.update(rate=-1), "rate"),
        (lambda d: d.update(annotations=[{}] * 51), "annotations"),
        (lambda d: d["annotations"][0].update(id="x"), "annotations[0].id"),
        (lambda d: d["annotations"][0].update(text="t" * 2001), "annotations[0].text"),
        (lambda d: d["annotations"][1].update(time=math.inf), "annotations[1].time"),
        (lambda d: d["annotations"].append("note"), "annotations[2]"),
        (lambda d: d.update(evidence=d["evidence"] * 5), "evidence"),
        (lambda d: d["evidence"].append(copy.deepcopy(d["evidence"][0])), "evidence"),
        (lambda d: d["evidence"][0].update(selection=[]), "evidence[0].selection"),
        (lambda d: d["evidence"][0].update(selection={"rows": {}}), "evidence[0].selection"),
        (lambda d: d["evidence"][1].update(selection={}), "evidence[1].selection"),
        (lambda d: d["evidence"][1]["selection"]["rows"].update(offset=-1), "evidence[1].selection.rows.offset"),
        (
            lambda d: d["evidence"][1]["selection"]["rows"].update(columns=["a", "a"]),
            "evidence[1].selection.rows.columns",
        ),
        (
            lambda d: d["evidence"][1]["selection"]["rows"].update(columns=["c"] * 13),
            "evidence[1].selection.rows.columns",
        ),
        (lambda d: d["evidence"][1]["selection"]["rows"].update(columns=[""]), "evidence[1].selection.rows.columns[0]"),
        (lambda d: d["evidence"][2].update(selection={"rows": {}}), "evidence[2].selection"),
        (lambda d: d["evidence"][2]["selection"]["figure"].update(height=2161), "evidence[2].selection.figure.height"),
        (lambda d: d["evidence"][0].update(execution_id="E" * 36), "evidence[0].execution_id"),
        (lambda d: d["evidence"][0].update(output_id="o"), "evidence[0].output_id"),
        (lambda d: d["evidence"][0].update(id=1), "evidence[0].id"),
        (lambda d: d["evidence"][0]["labels"].update(unit=3), "evidence[0].labels.unit"),
        (lambda d: d["evidence"][0]["labels"].update(name="n" * 1025), "evidence[0].labels.name"),
        (lambda d: d["evidence"][0]["labels"]["sample"].update(partial=1), "evidence[0].labels.sample.partial"),
        (lambda d: d["evidence"][0]["labels"]["sample"].update(planned=-1), "evidence[0].labels.sample.planned"),
        (
            lambda d: d["evidence"][0]["labels"]["source"].update(snapshot_hash="c"),
            "evidence[0].labels.source.snapshot_hash",
        ),
        (lambda d: d["evidence"][0]["labels"]["source"].update(extra=1), "evidence[0].labels.source.extra"),
        (lambda d: d.update(created=-1), "created"),
        (lambda d: d.update(created=None), "created"),
    ],
)
def test_each_rule_names_its_path(change, path):
    document = fixture()
    change(document)
    assert failure(document) == path


def test_manifest_must_be_an_object_and_messages_name_the_path():
    with pytest.raises(ManifestError, match="must be an object") as caught:
        validate_manifest([])
    assert caught.value.path == ""
    with pytest.raises(ValueError, match="^name: must be plain text"):
        validate_manifest({**fixture(), "name": ""})


def test_valid_documents_are_copied_and_integral_numbers_normalized():
    document = fixture()
    document["rate"] = 2.0
    document["camera"]["bounds"] = [-0.0, 0.0, 1.5, 1e300]
    copied = validate_manifest(document)
    assert copied is not document and copied["layout"] is not document["layout"]
    assert type(copied["rate"]) is int and type(copied["created"]) is int
    assert [type(v) for v in copied["camera"]["bounds"]] == [float, int, float, float]
    assert math.copysign(1, copied["camera"]["bounds"][0]) == -1
    copied["name"] = "changed"
    assert document["name"] == "Selected release policy"
    minimal = fixture("valid-minimal")
    assert "id" not in validate_manifest(minimal) and "revision" not in validate_manifest(minimal)
    assert validate_manifest({**minimal, "revision": 0})["revision"] == 0


def test_json_value_rejects_non_json_and_matches_javascript_numbers():
    assert json_value({"a": (1.0, 2.5, True, None, "x")}) == {"a": [1, 2.5, True, None, "x"]}
    assert type(json_value(9007199254740991.0)) is int and type(json_value(9007199254740992.0)) is float
    for bad in ({1: 2}, {"a": {1, 2}}, math.nan, b"x", 2**53, "\ud800"):
        with pytest.raises(ValueError):
            json_value(bad)
    nested = []
    for _ in range(70):
        nested = [nested]
    with pytest.raises(ValueError, match="nests"):
        json_value(nested)


def test_published_schema_matches_the_validator_on_shared_fixtures():
    schema = schema_document()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert schema["$id"].endswith("presentation-v1.schema.json")
    for path in CASES:
        document = json.loads(path.read_text())
        errors = list(validator.iter_errors(document))
        if path.stem.startswith("valid-"):
            assert errors == [], (path.stem, errors[:1])
    for name in ("invalid-schema", "invalid-unknown-key", "invalid-recording-attempt", "invalid-rate-zero"):
        assert list(validator.iter_errors(fixture(name))), name
    for change in (
        lambda d: d["evidence"][0]["labels"].update(extra=1),
        lambda d: d["evidence"][1]["selection"]["rows"].update(limit=51),
        lambda d: d["evidence"][2].update(selection={}),
        lambda d: d["annotations"][0].update(text=""),
        lambda d: d["layout"].update(extra=1),
    ):
        document = fixture()
        change(document)
        assert list(validator.iter_errors(document))


def objects_close_their_keys(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False, path
        for key, value in node.items():
            objects_close_their_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            objects_close_their_keys(value, f"{path}[{index}]")


def test_published_schema_closes_every_described_object():
    objects_close_their_keys(schema_document())


def test_snapshot_hash_fixture_is_the_canonical_wire_hash():
    published = json.loads((FIXTURES / "export-snapshot-hash.json").read_text())
    assert set(published) == {"input", "hash"}
    value = published["input"]
    assert set(value) == {"manifest", "target", "options", "statics"}
    validate_manifest(value["manifest"])
    assert snapshot_hash(**value) == published["hash"]
    assert canonical_hash("export-snapshot-v1", value) == published["hash"]
    # Python reads 2.0 as a float, JavaScript as 2: the hash normalizes both to the integer.
    options = {**value["options"], "rate": float(value["options"]["rate"])}
    assert snapshot_hash(value["manifest"], value["target"], options, value["statics"]) == published["hash"]
    assert snapshot_hash(value["manifest"], "html", value["options"], value["statics"]) != published["hash"]


def test_output_content_hash_fixture():
    published = json.loads((FIXTURES / "output-content-hash.json").read_text())
    assert output_content_hash(published["input"]) == published["hash"]
    assert published["hash"] == canonical_hash("presentation-evidence-output-v1", published["input"])
