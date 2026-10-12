"""Layout saves preserve exact prior bytes and reject concurrent disk edits."""

import base64

import pytest

from simulatte.execution.store import RunStore, RevisionConflict, _read
from simulatte.layout.layers import FileLayer


def api():
    from simulatte.execution import layout_files

    return layout_files


def test_save_keeps_invalid_original_bytes_in_durable_backup(tmp_path):
    m = api()
    path = tmp_path / "layout.json"
    path.write_bytes(b"invalid original\xff")
    with RunStore.open(tmp_path / ".simulatte") as store:
        before = m.read_layout_file(tmp_path, "layout.json")
        backup = m.backup_layout(store, before)
        with m.stage_layout(tmp_path, "layout.json", FileLayer({"schema_version": "1.0"})) as staged:
            staged.replace(expected=before)
            assert staged.replaced
        with store._transaction() as db:
            row = db.execute("SELECT kind,manifest FROM snapshots WHERE id=?", (backup,)).fetchone()
        assert row[0] == "layout-backup"
        assert base64.b64decode(_read(row[1])["raw_base64"], validate=True) == b"invalid original\xff"
        assert m.read_layout_file(tmp_path, "layout.json").layer().to_dict() == {"schema_version": "1.0"}
        assert m.backup_layout(store, before) == backup
    assert not list(tmp_path.glob(".layout.json.*.tmp"))


def test_external_edit_prevents_replace_and_temp_is_removed(tmp_path):
    m = api()
    before = m.read_layout_file(tmp_path, "layout.json")
    assert before.raw is None
    assert before.layer().to_dict() == {"schema_version": "1.0"}
    with m.stage_layout(tmp_path, "layout.json", before.layer()) as staged:
        (tmp_path / "layout.json").write_bytes(b"external")
        with pytest.raises(RevisionConflict, match="disk"):
            staged.replace(expected=before)
        assert not staged.replaced
    assert (tmp_path / "layout.json").read_bytes() == b"external"
    assert not list(tmp_path.glob(".layout.json.*.tmp"))


def test_layout_file_rejects_escape_symlink_nonregular_and_oversize(tmp_path):
    m = api()
    from simulatte.layout.types import LayoutLimits

    for name in ("../layout.json", str(tmp_path / "layout.json")):
        with pytest.raises(ValueError):
            m.read_layout_file(tmp_path, name)
    (tmp_path / "real.json").write_bytes(b"12345")
    (tmp_path / "layout.json").symlink_to("real.json")
    with pytest.raises(ValueError, match="symlink"):
        m.read_layout_file(tmp_path, "layout.json")
    with pytest.raises(ValueError, match="limit"):
        m.read_layout_file(tmp_path, "real.json", limits=LayoutLimits(max_file_bytes=4))
    (tmp_path / "directory").mkdir()
    with pytest.raises(ValueError, match="regular"):
        m.read_layout_file(tmp_path, "directory")


def test_post_rename_sync_failure_reports_replaced_and_retains_backup(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "layout.json"
    path.write_bytes(b"old")
    with RunStore.open(tmp_path / ".simulatte") as store:
        before = m.read_layout_file(tmp_path, "layout.json")
        backup = m.backup_layout(store, before)
        with m.stage_layout(tmp_path, "layout.json", FileLayer({"schema_version": "1.0"})) as staged:

            def fail(path):
                raise OSError("sync failed")

            monkeypatch.setattr(m, "sync_directory", fail)
            with pytest.raises(OSError, match="sync failed"):
                staged.replace(expected=before)
            assert staged.replaced
        with store._transaction() as db:
            assert db.execute("SELECT 1 FROM snapshots WHERE id=?", (backup,)).fetchone()
    assert path.read_bytes() != b"old"


def test_missing_and_empty_layout_backups_are_distinct_and_corruption_rejected(tmp_path):
    m = api()
    from simulatte.execution.store import _blob

    with RunStore.open(tmp_path / ".simulatte") as store:
        absent = m.read_layout_file(tmp_path, "layout.json")
        absent_id = m.backup_layout(store, absent)
        (tmp_path / "layout.json").write_bytes(b"")
        empty = m.read_layout_file(tmp_path, "layout.json")
        empty_id = m.backup_layout(store, empty)
        assert absent_id != empty_id and absent.digest is None and empty.digest is not None
        with store._transaction() as db:
            db.execute("UPDATE snapshots SET manifest=? WHERE id=?", (_blob({"tampered": True}), empty_id))
        with pytest.raises(ValueError, match="identity mismatch"):
            m.backup_layout(store, empty)


def test_staged_layout_is_bounded_and_can_only_be_replaced_once(tmp_path):
    m = api()
    from simulatte.layout.types import LayoutLimits
    from simulatte.layout.errors import LayoutError

    before = m.read_layout_file(tmp_path, "layout.json")
    with pytest.raises(LayoutError, match="max_file_bytes"):
        with m.stage_layout(tmp_path, "layout.json", before.layer(), limits=LayoutLimits(max_file_bytes=4)):
            pytest.fail("oversize stage must fail before yielding")
    assert not list(tmp_path.iterdir())
    with m.stage_layout(tmp_path, "layout.json", before.layer()) as staged:
        with pytest.raises(RevisionConflict):
            staged.replace(expected=m.LayoutFile("other.json", None))
        staged.replace(expected=before)
        with pytest.raises(ValueError, match="already replaced"):
            staged.replace(expected=before)


def test_layout_read_detects_mutation_during_read_and_closes_descriptor(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import os

    m = api()
    path = tmp_path / "layout.json"
    path.write_bytes(b"before")
    original = os.fdopen
    descriptors = []

    @contextmanager
    def changing(descriptor, *args, **kwargs):
        descriptors.append(descriptor)
        with original(descriptor, *args, **kwargs) as stream:

            class Reader:
                def read(self, length):
                    raw = stream.read(length)
                    path.write_bytes(b"after edit")
                    return raw

            yield Reader()

    monkeypatch.setattr(m.os, "fdopen", changing)
    with pytest.raises(RevisionConflict, match="while reading"):
        m.read_layout_file(tmp_path, "layout.json")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_layout_replacement_between_stat_and_open_is_detected(tmp_path, monkeypatch):
    import os

    m = api()
    path = tmp_path / "layout.json"
    path.write_bytes(b"old")
    replacement = tmp_path / "new.json"
    replacement.write_bytes(b"new")
    original = os.open
    descriptors = []

    def changed(name, *args, **kwargs):
        if name == path:
            os.replace(replacement, path)
        descriptor = original(name, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(m.os, "open", changed)
    with pytest.raises(RevisionConflict, match="before reading"):
        m.read_layout_file(tmp_path, "layout.json")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_staging_short_write_and_temp_creation_failure_leave_no_output(tmp_path, monkeypatch):
    from contextlib import contextmanager

    m = api()
    original = m.tempfile.NamedTemporaryFile

    @contextmanager
    def short(**kwargs):
        with original(**kwargs) as file:

            class Writer:
                name = file.name

                def write(self, data):
                    return file.write(data[:-1])

            yield Writer()

    monkeypatch.setattr(m.tempfile, "NamedTemporaryFile", short)
    with pytest.raises(OSError, match="Incomplete"):
        with m.stage_layout(tmp_path, "layout.json", FileLayer({"schema_version": "1.0"})):
            pytest.fail("short write must not yield")
    assert list(tmp_path.iterdir()) == []

    def fail(**kwargs):
        raise OSError("cannot create temporary")

    monkeypatch.setattr(m.tempfile, "NamedTemporaryFile", fail)
    with pytest.raises(OSError, match="cannot create"):
        with m.stage_layout(tmp_path, "layout.json", FileLayer({"schema_version": "1.0"})):
            pytest.fail("failed temp must not yield")
    assert list(tmp_path.iterdir()) == []
