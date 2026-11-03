"""Declared immutable assets: source identity, publication, leases and failure."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from simulatte import model
from simulatte.execution import native_assets as assets
from simulatte.execution.references import write_document
from simulatte.execution.snapshot import Entry


@dataclass(frozen=True)
class Params:
    pass


@pytest.mark.parametrize("paths", [("../a.so",), ("a.py",), ("a.so", "other/a.so"), (), ("x\\a.so",)])
def test_invalid_declarations(paths):
    with pytest.raises(ValueError):
        assets.NativeAssetBundle("engine", paths)


def test_model_keeps_native_files_in_source():
    bundle = assets.NativeAssetBundle("engine", ("binaries/engine.so",))
    with pytest.raises(ValueError, match="separate"):
        model(params=Params, horizon=1, inputs=bundle.files, native_assets=(bundle,))
    with pytest.raises(ValueError, match="Duplicate"):
        model(params=Params, horizon=1, native_assets=(bundle, bundle))
    build = model(params=Params, horizon=1, native_assets=(bundle,))(lambda env, layout, params: None)
    assert build.__simulatte_model__.native_assets == (bundle,)


@pytest.fixture
def captured(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = ("extension.so", "engine.so")
    for name in files:
        (source / name).write_bytes(name.encode())
    entries = tuple(Entry(name, len(name.encode()), hashlib.sha256(name.encode()).hexdigest(), False) for name in files)
    bundle = assets.NativeAssetBundle("engine", files)
    snapshot = SimpleNamespace(entries=entries, content_hash="a" * 64)
    if os.name != "posix":
        pytest.skip("local POSIX native store")
    manifest = assets.capture_manifest(snapshot, (bundle,))
    yield source, bundle, manifest
    for path in tmp_path.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def handle_for(tmp_path, source, bundle, manifest, lease):
    root = tmp_path / "documents"
    config = {"native_assets_ref": write_document(root, "native-assets", manifest),
              "native_bindings_ref": write_document(root, "native-bindings", lease.bindings())}
    return assets.verify_for_execution(config, manifest["source_hash"], [bundle.to_dict()], source)[bundle.name]


def test_concurrent_publication_reuse_and_active_pruning(captured, tmp_path):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(pool.map(lambda _: store.acquire(source, manifest), range(8)))
    assert len(list(store.root.iterdir())) == 1
    assert store.prune() == []
    handle = handle_for(tmp_path, source, bundle, manifest, leases[0])
    original = handle.verify()
    with pytest.raises(TypeError):
        handle.bundle["files"][0]["sha256"] = "b" * 64
    assert handle.path("engine.so").read_bytes() == b"engine.so"
    with pytest.raises(KeyError):
        handle.path("../engine.so")
    for lease in leases:
        lease.close()
        lease.close()
    assert store.prune(True) == [manifest["bundles"][0]["address"]]
    assert handle.verify() == original
    assert store.prune() == [manifest["bundles"][0]["address"]]
    replacement = store.acquire(source, manifest)
    assert handle_for(tmp_path, source, bundle, manifest, replacement).verify()
    replacement.close()


def test_corruption_fails_without_repair(captured, tmp_path):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    path = handle.directory / "engine.so"
    path.chmod(0o644)
    path.write_bytes(b"corrupt")
    path.chmod(0o444)
    with pytest.raises(ValueError):
        handle.verify()
    with pytest.raises(ValueError):
        store.acquire(source, manifest)
    assert path.read_bytes() == b"corrupt"
    lease.close()


def test_changed_content_invalidates_address(captured):
    source, bundle, manifest = captured
    source.joinpath("engine.so").write_bytes(b"new")
    entries = tuple(Entry(name, (source / name).stat().st_size,
                          hashlib.sha256((source / name).read_bytes()).hexdigest(), False) for name in bundle.files)
    next_manifest = assets.capture_manifest(SimpleNamespace(entries=entries, content_hash="b" * 64), (bundle,))
    assert next_manifest["bundles"][0]["address"] != manifest["bundles"][0]["address"]


def test_failed_publication_is_not_an_entry(captured, tmp_path, monkeypatch):
    source, _, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    original = assets.sync_directory

    def fail(path):
        if path.name.startswith(".publish-"):
            raise OSError("injected directory sync failure")
        return original(path)

    monkeypatch.setattr(assets, "sync_directory", fail)
    with pytest.raises(OSError, match="injected"):
        store.acquire(source, manifest)
    assert list(store.root.iterdir()) == [] and not store.leases
    orphan = store.root / ".publish-interrupted"
    orphan.mkdir()
    (orphan / "partial").write_bytes(b"partial")
    assert store.prune() == [orphan.name]


def test_binding_source_abi_and_declaration_checks(captured, tmp_path):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    with pytest.raises(ValueError, match="source identity"):
        assets.validate_manifest(manifest, "b" * 64)
    changed = json.loads(json.dumps(manifest))
    changed["abi"]["platform"] = "wrong"
    with pytest.raises(ValueError, match="ABI"):
        assets.validate_manifest(changed)
    with pytest.raises(ValueError, match="managed preparation"):
        assets.verify_for_execution({}, manifest["source_hash"], [bundle.to_dict()], source)
    source.joinpath("engine.so").write_bytes(b"modified")
    with pytest.raises(ValueError):
        handle.verify()
    lease.close()


def test_failure_after_rename_leaves_only_verified_reusable_bytes(captured, tmp_path, monkeypatch):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    original = assets.sync_directory

    def fail(path):
        if path == store.root:
            raise OSError("injected post-rename sync failure")
        return original(path)

    monkeypatch.setattr(assets, "sync_directory", fail)
    with pytest.raises(OSError, match="post-rename"):
        store.acquire(source, manifest)
    assert not store.leases and len(list(store.root.iterdir())) == 1
    monkeypatch.setattr(assets, "sync_directory", original)
    lease = store.acquire(source, manifest)
    assert handle_for(tmp_path, source, bundle, manifest, lease).verify()
    lease.close()


@pytest.mark.parametrize("fault", ["origin", "mutation", "dependency"])
def test_checked_loading_rejects_wrong_origin_or_mutation(captured, tmp_path, monkeypatch, fault):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    def execute(module):
        if fault == "mutation":
            path = handle.directory / "engine.so"
            path.chmod(0o644)
            path.write_bytes(b"changed while importing")
            path.chmod(0o444)

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=execute))
    monkeypatch.setattr(assets.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(assets.importlib.util, "module_from_spec", lambda spec: SimpleNamespace(
        __file__=__file__ if fault == "origin" else str(handle.directory / "extension.so")))
    monkeypatch.setattr(assets, "_images", lambda name: {source / name})
    with pytest.raises(ValueError):
        handle.load_extension("_engine", "extension.so", dependencies=("engine.so",) if fault == "dependency" else ())
    lease.close()


def test_origin_allowlist_is_exact_and_retains_actual_path(captured, tmp_path):
    from simulatte.execution.imports import ImportPolicy

    source, _, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    path = store.root / manifest["bundles"][0]["address"] / "extension.so"
    policy = ImportPolicy(source, (source,), (), source_root=source, native_files=(path,))
    assert policy._origin("extension", str(path)) == "external:" + str(path)
    with pytest.raises(ImportError):
        policy._origin("unknown", str(path.with_name("engine.so")))
    lease.close()
