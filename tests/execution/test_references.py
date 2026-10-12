"""Local data documents stay immutable and verified outside bounded control frames."""

from pathlib import Path
import importlib
import importlib.util

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.references"), "verified documents missing"
    return importlib.import_module("simulatte.execution.references")


def test_canonical_document_reference_reuse_and_kind_binding(tmp_path):
    m = api()
    value = {"b": [1, "x" * (300 << 10)], "a": True}
    reference = m.write_document(tmp_path, "model-info", value)
    assert m.read_document(reference, "model-info") == value
    assert m.write_document(tmp_path, "model-info", dict(reversed(list(value.items())))) == reference
    with pytest.raises(ValueError, match="kind"):
        m.read_document(reference, "request")
    path = Path(reference["path"])
    path.chmod(0o600)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="length|hash"):
        m.read_document(reference, "model-info")
    with pytest.raises(ValueError):
        m.write_document(tmp_path, "model-info", value)


@pytest.mark.parametrize(
    "change",
    [
        {"length": True},
        {"length": 0},
        {"length": (128 << 20) + 1},
        {"sha256": "bad"},
        {"path": "relative"},
        {"extra": 1},
    ],
)
def test_invalid_reference_is_rejected_before_use(tmp_path, change):
    m = api()
    reference = m.write_document(tmp_path, "request", {"schema": 1})
    with pytest.raises(ValueError):
        m.read_document(reference | change, "request")


def test_symlink_reference_is_refused(tmp_path):
    m = api()
    reference = m.write_document(tmp_path, "layout", {})
    link = tmp_path / "link"
    link.symlink_to(reference["path"])
    with pytest.raises(ValueError, match="regular|symlink"):
        m.read_document(reference | {"path": str(link)}, "layout")


def test_same_length_tampering_and_symlinked_destination_fail(tmp_path):
    m = api()
    reference = m.write_document(tmp_path / "documents", "layout", {"schema": 1})
    path = Path(reference["path"])
    path.chmod(0o600)
    path.write_bytes(b"x" * reference["length"])
    with pytest.raises(ValueError, match="hash"):
        m.read_document(reference, "layout")
    link = tmp_path / "redirect"
    link.symlink_to(tmp_path / "documents", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        m.write_document(link, "layout", {})


@pytest.mark.parametrize("kind,value", [("unknown", {}), ("request", []), ("layout", "text")])
def test_only_known_data_documents_can_be_written(tmp_path, kind, value):
    with pytest.raises(ValueError, match="kind/value"):
        api().write_document(tmp_path, kind, value)


def test_oversize_document_rejected_before_file_creation(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, "MAX_DOCUMENT", 10)
    with pytest.raises(ValueError, match="limit"):
        m.write_document(tmp_path, "request", {"data": "large"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("phase", ["before", "while", "after"])
def test_document_replacement_during_read_is_detected(tmp_path, monkeypatch, phase):
    m = api()
    reference = m.write_document(tmp_path, "request", {})
    original = m._identity
    calls = []
    # The identity guard is read from the opened descriptor twice, then path.
    # A changed tuple models replacement at each distinct race boundary.
    target = {"before": 1, "while": 3, "after": 5}[phase]

    def identity(info):
        calls.append(None)
        result = original(info)
        return (*result[:-1], -1) if len(calls) == target else result

    monkeypatch.setattr(m, "_identity", identity)
    with pytest.raises(ValueError, match=f"changed {phase}"):
        m.read_document(reference, "request")


def test_short_document_write_never_leaves_a_reference_or_temporary(tmp_path, monkeypatch):
    from contextlib import contextmanager

    m = api()
    original = m.tempfile.NamedTemporaryFile

    @contextmanager
    def short_write(**kwargs):
        with original(**kwargs) as file:
            file.write = lambda data: 0
            yield file

    monkeypatch.setattr(m.tempfile, "NamedTemporaryFile", short_write)
    with pytest.raises(OSError, match="Incomplete"):
        m.write_document(tmp_path, "request", {})
    assert not list(tmp_path.iterdir())


def test_failed_temporary_creation_leaves_no_document(tmp_path, monkeypatch):
    m = api()

    def fail(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(m.tempfile, "NamedTemporaryFile", fail)
    with pytest.raises(OSError):
        m.write_document(tmp_path, "request", {})
    assert not list(tmp_path.iterdir())
