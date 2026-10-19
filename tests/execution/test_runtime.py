"""Local runtime evidence is data-only and verified again in execution children."""

import importlib
import importlib.util
from types import SimpleNamespace

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.runtime"), "runtime evidence missing"
    return importlib.import_module("simulatte.execution.runtime")


def test_runtime_identity_changes_with_external_distribution_evidence(monkeypatch):
    m = api()
    records = ["first"]
    dist = SimpleNamespace(metadata={"Name": "Example_Package"}, version="1.2", read_text=lambda name: records[0])
    monkeypatch.setattr(m.metadata, "distributions", lambda **kw: [dist])
    first = m.describe_runtime([])
    assert first["dependencies"]["packages"]["example-package"]["version"] == "1.2"
    records[0] = "second"
    assert m.describe_runtime([]) != first
    records[0] = None
    assert m.describe_runtime([])["warnings"] == ["distribution_record_unavailable:example-package"]


def test_runtime_verification_refuses_changed_environment(tmp_path, monkeypatch):
    m = api()
    from simulatte.execution.references import write_document
    from simulatte.execution.hashing import canonical_hash

    evidence = {"facts": {"implementation": "fixture"}, "dependencies": {"packages": {}}, "warnings": []}
    monkeypatch.setattr(m, "describe_runtime", lambda roots: evidence)
    config = {"runtime_ref": write_document(tmp_path, "runtime", evidence), "distribution_roots": []}
    requested = {
        "facts": evidence["facts"],
        "dependencies_hash": canonical_hash("dependencies-v1", evidence["dependencies"]),
        "trusted_runtime_hash": "a" * 64,
    }
    actual, dependencies, warnings = m.verify_runtime(config, requested)
    assert actual["facts"] == evidence["facts"] and dependencies == evidence["dependencies"] and not warnings
    evidence["facts"]["implementation"] = "changed"
    with pytest.raises(ValueError, match="runtime"):
        m.verify_runtime(config, requested)


@pytest.mark.parametrize("names", [[None], ["example", "Example"]])
def test_missing_or_ambiguous_distribution_metadata_fails(monkeypatch, names):
    m = api()
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version=str(i), read_text=lambda key: "record")
        for i, name in enumerate(names)
    ]
    monkeypatch.setattr(m.metadata, "distributions", lambda **kw: distributions)
    with pytest.raises(ValueError, match="name|Ambiguous"):
        m.describe_runtime([])


@pytest.mark.parametrize("changed", [{"extra": 1}, {"facts": {}}, {"dependencies_hash": "a" * 64}])
def test_request_must_match_actual_runtime_identity(tmp_path, monkeypatch, changed):
    from simulatte.execution.references import write_document
    from simulatte.execution.hashing import canonical_hash

    m = api()
    evidence = {"facts": {"python": "fixture"}, "dependencies": {}, "warnings": []}
    monkeypatch.setattr(m, "describe_runtime", lambda roots: evidence)
    config = {"runtime_ref": write_document(tmp_path, "runtime", evidence), "distribution_roots": []}
    requested = {
        "facts": evidence["facts"],
        "dependencies_hash": canonical_hash("dependencies-v1", {}),
        "trusted_runtime_hash": "a" * 64,
    }
    with pytest.raises(ValueError, match="actual runtime"):
        m.verify_runtime(config, requested | changed)


def test_installation_runtime_requires_matching_tags_and_independent_payload(tmp_path, monkeypatch):
    from simulatte.execution.installation import FORMAT, RUNTIME_KIND
    from simulatte.execution import installation
    from simulatte.execution.hashing import canonical_hash
    from simulatte.execution.references import write_document

    payload = {"format": FORMAT, "facts": {"python": "3.14.3"}, "dependencies": {"packages": {}}, "warnings": []}
    document = {
        "format": FORMAT,
        "identity": payload,
        "bindings": {"file:///captured.whl": "c" * 64},
        "raw": {"record": "old"},
    }
    config = {
        "runtime_ref": write_document(tmp_path, "runtime", document),
        "distribution_roots": [],
        "trusted_runtime_hash": "a" * 64,
    }
    requested = {
        "kind": RUNTIME_KIND,
        "facts": payload["facts"],
        "dependencies_hash": canonical_hash("installation-dependencies-v1", payload["dependencies"]),
        "trusted_runtime_hash": "a" * 64,
    }
    monkeypatch.setattr(installation, "describe_installation", lambda roots, bindings: payload)
    assert api().verify_runtime(config, requested) == (requested, payload["dependencies"], ())
    for change in (
        {"kind": "local"},
        {"trusted_runtime_hash": "b" * 64},
        {"dependencies_hash": "d" * 64},
        {"facts": {}},
        {"unexpected": True},
    ):
        with pytest.raises(ValueError):
            api().verify_runtime(config, requested | change)
    for wrong in (
        {"facts": {}, "dependencies": {}, "warnings": []},
        {**document, "format": "future"},
        {**document, "identity": {**payload, "format": "future"}},
    ):
        config["runtime_ref"] = write_document(tmp_path, "runtime", wrong)
        with pytest.raises(ValueError):
            api().verify_runtime(config, requested)
    config["runtime_ref"] = write_document(tmp_path, "runtime", document)
    changed = {**payload, "dependencies": {"changed": True}}
    monkeypatch.setattr(installation, "describe_installation", lambda roots, bindings: changed)
    with pytest.raises(ValueError, match="changed"):
        api().verify_runtime(config, requested)
