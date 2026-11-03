import hashlib
import json
import sys
import sysconfig
from types import SimpleNamespace

import pytest

from prototypes.native_engine_dynamic import immutable_assets as assets


@pytest.fixture
def pair(tmp_path):
    root = tmp_path / "project"
    build = root / "prototypes/native_engine_dynamic/.build"
    build.mkdir(parents=True)
    extension = build / ("_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX"))
    library = build / ("dynamic_engine.dylib" if sys.platform == "darwin" else "dynamic_engine.so")
    extension.write_bytes(b"extension")
    library.write_bytes(b"engine")
    (root / "native-assets.json").write_text(json.dumps(dict(schema=1, files={
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (extension, library)
    })))
    yield extension
    # Let pytest remove deliberately read-only fixture directories afterwards.
    for path in tmp_path.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def test_publish_reuses_same_verified_readonly_pair(pair, tmp_path):
    entry = assets.publish(pair, tmp_path / "store")
    original = assets.verify_entry(entry, assets.descriptor(pair))
    assert assets.publish(pair, tmp_path / "store") == entry
    assert assets.verify_entry(entry, assets.descriptor(pair)) == original
    assert entry.name == assets.address(assets.descriptor(pair))
    assert entry.stat().st_mode & 0o222 == 0
    assert all(p.stat().st_mode & 0o222 == 0 for p in entry.iterdir())


@pytest.mark.parametrize("fault", ["bytes", "missing", "writable", "writable_dir", "manifest", "symlink"])
def test_invalid_entry_rejected_before_import(pair, tmp_path, monkeypatch, fault):
    store = tmp_path / "store"
    entry = assets.publish(pair, store)
    file = entry / pair.name
    if fault == "bytes":
        file.chmod(0o644)
        file.write_bytes(b"corrupt")
        file.chmod(0o444)
    elif fault == "missing":
        entry.chmod(0o755)
        file.unlink()
        entry.chmod(0o555)
    elif fault == "writable":
        file.chmod(0o644)
    elif fault == "writable_dir":
        entry.chmod(0o755)
    elif fault == "manifest":
        marker = entry / "manifest.json"
        marker.chmod(0o644)
        marker.write_text('{}')
        marker.chmod(0o444)
    else:
        entry.chmod(0o755)
        file.unlink()
        file.symlink_to(pair)
        entry.chmod(0o555)
    monkeypatch.setenv(assets.STORE_ENV, str(store))

    def forbidden(path):
        pytest.fail("invalid binary reached import")

    monkeypatch.setattr(assets, "_import_extension", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        assets.load_verified(pair)
    with pytest.raises((ValueError, FileNotFoundError)):
        assets.publish(pair, store)


def test_missing_store_never_falls_back_to_attempt(pair, tmp_path, monkeypatch):
    monkeypatch.setenv(assets.STORE_ENV, str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError):
        assets.load_verified(pair)


def test_changed_snapshot_rejected_even_with_valid_shared_pair(pair, tmp_path, monkeypatch):
    store = tmp_path / "store"
    assets.publish(pair, store)
    pair.write_bytes(b"changed source")
    monkeypatch.setenv(assets.STORE_ENV, str(store))
    with pytest.raises(ValueError, match="integrity"):
        assets.load_verified(pair)


def test_source_file_symlink_rejected(pair):
    saved = pair.with_name("saved-extension")
    pair.rename(saved)
    pair.symlink_to(saved)
    with pytest.raises(ValueError, match="file"):
        assets.load_verified(pair)


@pytest.mark.parametrize("fault", ["mutate", "origin", "engine"])
def test_post_import_validation(pair, tmp_path, monkeypatch, fault):
    store = tmp_path / "store"
    entry = assets.publish(pair, store)
    monkeypatch.setenv(assets.STORE_ENV, str(store))
    library = next(entry / name for name in assets.descriptor(pair)["files"] if name != pair.name)
    monkeypatch.setattr(assets, "engine_images", lambda: [str(library if fault != "engine" else pair)])

    def imported(path):
        if fault == "mutate":
            path.chmod(0o644)
            path.write_bytes(b"changed while loading")
            path.chmod(0o444)
        return SimpleNamespace(__file__=str(path if fault != "origin" else pair))

    monkeypatch.setattr(assets, "_import_extension", imported)
    with pytest.raises(ValueError):
        assets.load_verified(pair)


def test_symlink_directory_and_wrong_address_rejected(pair, tmp_path):
    entry = assets.publish(pair, tmp_path / "store")
    alias = tmp_path / "alias" / entry.name
    alias.parent.mkdir()
    alias.symlink_to(entry, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|directory"):
        assets.verify_entry(alias, assets.descriptor(pair))
    with pytest.raises(ValueError, match="address"):
        assets.verify_entry(entry.with_name("wrong"), assets.descriptor(pair))


def test_diagnostics_excluded_from_returned_module(pair, tmp_path, monkeypatch):
    monkeypatch.delenv(assets.STORE_ENV, raising=False)
    output = tmp_path / "loader.json"
    monkeypatch.setenv(assets.DIAGNOSTIC_ENV, str(output))
    library = next(pair.parent / name for name in assets.descriptor(pair)["files"] if name != pair.name)
    module = SimpleNamespace(__file__=str(pair))
    monkeypatch.setattr(assets, "_import_extension", lambda path: module)
    monkeypatch.setattr(assets, "engine_images", lambda: [str(library)])
    assert assets.load_verified(pair) is module
    document = json.loads(output.read_text())
    assert document["mode"] == "attempt" and document["files"] == assets.descriptor(pair)["files"]
    assert set(vars(module)) == {"__file__"}
