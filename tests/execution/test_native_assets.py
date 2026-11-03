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
    config = {
        "native_assets_ref": write_document(root, "native-assets", manifest),
        "native_bindings_ref": write_document(root, "native-bindings", lease.bindings()),
    }
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
    entries = tuple(
        Entry(name, (source / name).stat().st_size, hashlib.sha256((source / name).read_bytes()).hexdigest(), False)
        for name in bundle.files
    )
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
    monkeypatch.setattr(
        assets.importlib.util,
        "module_from_spec",
        lambda spec: SimpleNamespace(
            __file__=__file__ if fault == "origin" else str(handle.directory / "extension.so")
        ),
    )
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


@pytest.mark.parametrize("value", [None, "engine", {"name": "engine"}, {"name": "engine", "files": "x.so"}])
def test_bundle_wire_declarations_reject_malformed_data(value):
    with pytest.raises(ValueError):
        assets.NativeAssetBundle.from_dict(value)


@pytest.mark.parametrize("value", ["engine", b"engine", None, [object()]])
def test_bundle_collections_are_bounded_data(value):
    with pytest.raises(ValueError):
        assets.declarations(value)


def test_bundle_limits_and_cross_bundle_source_ownership():
    with pytest.raises(ValueError):
        assets.NativeAssetBundle("bad name", ("x.so",))
    with pytest.raises(ValueError):
        assets.NativeAssetBundle("engine", ("a" * 1025 + ".so",))
    with pytest.raises(ValueError):
        assets.declarations(tuple(assets.NativeAssetBundle(f"b{i}", (f"{i}.so",)) for i in range(17)))
    with pytest.raises(ValueError):
        assets.declarations((assets.NativeAssetBundle("a", ("x.so",)), assets.NativeAssetBundle("b", ("x.so",))))


@pytest.mark.parametrize("part", ["root", "bundles", "bundle", "files", "file", "size", "name", "address"])
def test_manifest_wire_integrity_rejects_malformed_members(captured, part):
    _, _, original = captured
    value = json.loads(json.dumps(original))
    if part == "root":
        value["unexpected"] = True
    elif part == "bundles":
        value["bundles"] = None
    elif part == "bundle":
        value["bundles"][0]["unexpected"] = True
    elif part == "files":
        value["bundles"][0]["files"] = None
    elif part == "file":
        value["bundles"][0]["files"][0]["unexpected"] = True
    elif part == "size":
        value["bundles"][0]["files"][0]["size"] = True
    elif part == "name":
        value["bundles"][0]["files"][0]["name"] = "other.so"
    else:
        value["bundles"][0]["address"] = "b" * 64
    with pytest.raises(ValueError):
        assets.validate_manifest(value)


def test_uncaptured_and_linked_native_source_is_rejected(captured):
    _, bundle, _ = captured
    snapshot = SimpleNamespace(content_hash="a" * 64, entries=())
    with pytest.raises(ValueError, match="captured regular"):
        assets.capture_manifest(snapshot, (bundle,))
    snapshot.entries = (Entry("extension.so", 1, "a" * 64, False, "elsewhere.so"),)
    with pytest.raises(ValueError, match="captured regular"):
        assets.capture_manifest(snapshot, (bundle,))


@pytest.mark.parametrize(
    "fault", ["writable-directory", "extra-member", "symlink", "writable-binary", "same-size-bytes"]
)
def test_cached_entry_integrity_guards(captured, tmp_path, fault):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    path = handle.directory / "engine.so"
    if fault == "writable-directory":
        handle.directory.chmod(0o755)
    elif fault == "extra-member":
        handle.directory.chmod(0o755)
        (handle.directory / "extra").touch()
        handle.directory.chmod(0o555)
    elif fault == "symlink":
        handle.directory.chmod(0o755)
        path.unlink()
        path.symlink_to(source / "engine.so")
        handle.directory.chmod(0o555)
    else:
        path.chmod(0o644)
        if fault == "same-size-bytes":
            path.write_bytes(b"x" * path.stat().st_size)
            path.chmod(0o444)
    with pytest.raises(ValueError):
        handle.verify()
    lease.close()


def test_symlink_store_never_modifies_its_target(captured, tmp_path):
    source, _, manifest = captured
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "cache"
    link.symlink_to(target, target_is_directory=True)
    store = assets.NativeAssetStore(link)
    with pytest.raises(ValueError, match="symlink"):
        store.acquire(source, manifest)
    with pytest.raises(ValueError, match="symlink"):
        store.prune()
    assert list(target.iterdir()) == []


def test_native_binding_documents_must_match_source_and_declarations(captured, tmp_path):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    root = tmp_path / "documents"
    reference = write_document(root, "native-assets", manifest)
    assert not assets.verify_for_execution({}, manifest["source_hash"], [], source)
    with pytest.raises(ValueError, match="Undeclared"):
        assets.verify_for_execution({"native_assets_ref": reference}, manifest["source_hash"], [], source)
    other = assets.NativeAssetBundle("other", bundle.files)
    with pytest.raises(ValueError, match="differs from model"):
        assets.verify_for_execution(
            {"native_assets_ref": reference, "native_bindings_ref": {}},
            manifest["source_hash"],
            [other.to_dict()],
            source,
        )
    for value in (
        {},
        {"schema": True, "source_hash": manifest["source_hash"], "directories": {}},
        {"schema": 1, "source_hash": manifest["source_hash"], "directories": {"engine": "relative"}},
    ):
        config = {"native_assets_ref": reference, "native_bindings_ref": write_document(root, "native-bindings", value)}
        with pytest.raises(ValueError):
            assets.verify_for_execution(config, manifest["source_hash"], [bundle.to_dict()], source)
    lease.close()


def test_native_platform_support_is_explicit(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(assets, "os", SimpleNamespace(name="nt"))
        with pytest.raises(ValueError, match="POSIX"):
            assets.validate_manifest({})
    with monkeypatch.context() as patch:
        patch.setattr(assets, "sys", SimpleNamespace(platform="other"))
        with pytest.raises(ValueError, match="macOS or Linux"):
            assets._images("engine.so")


def test_mapped_image_parsers_preserve_full_paths_and_deduplicate(monkeypatch):
    from pathlib import Path

    expected = Path("/tmp/native images/engine.so").resolve()
    with monkeypatch.context() as patch:
        patch.setattr(assets, "sys", SimpleNamespace(platform="linux"))
        read = Path.read_text
        maps = f"1-2 r-xp 0 00:01 1 {expected}\n2-3 r--p 0 00:01 1 {expected}\n3-4 rw-p 0 00:00 0\n"
        patch.setattr(
            Path, "read_text", lambda path, *a, **kw: maps if str(path) == "/proc/self/maps" else read(path, *a, **kw)
        )
        assert assets._images("engine.so") == {expected}
    with monkeypatch.context() as patch:
        patch.setattr(assets, "sys", SimpleNamespace(platform="darwin"))
        names = [str(expected).encode(), str(expected).encode(), b"/tmp/unrelated.dylib"]

        def count():
            return len(names)

        def name(index):
            return names[index]

        patch.setattr(
            assets.ctypes, "CDLL", lambda _: SimpleNamespace(_dyld_image_count=count, _dyld_get_image_name=name)
        )
        assert assets._images("engine.so") == {expected}


@pytest.mark.parametrize("value", ["engine", b"engine", None])
def test_model_rejects_malformed_native_asset_sequences(value):
    with pytest.raises(ValueError, match="sequence"):
        model(params=Params, horizon=1, native_assets=value)


@pytest.mark.parametrize("failure", ["name", "member", "spec", "origin", "dependency", "changed", "abi"])
def test_extension_loader_rejects_invalid_identity(captured, tmp_path, monkeypatch, failure):
    source, bundle, manifest = captured
    lease = assets.NativeAssetStore(tmp_path / "cache").acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    try:
        if failure == "abi":
            monkeypatch.setattr(assets, "abi", lambda: {})
            with pytest.raises(ValueError, match="ABI"):
                handle.verify()
            return
        if failure in ("name", "member"):
            with pytest.raises((ValueError, KeyError)):
                handle.load_extension("bad-name" if failure == "name" else "engine", "absent.so")
            return
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        monkeypatch.setattr(
            assets.importlib.util, "spec_from_file_location", lambda *args: None if failure == "spec" else spec
        )
        module = SimpleNamespace(
            __file__=str(handle.directory / ("outside.so" if failure == "origin" else "extension.so"))
        )
        monkeypatch.setattr(assets.importlib.util, "module_from_spec", lambda _: module)
        monkeypatch.setattr(assets, "_images", lambda _: set())
        if failure == "changed":
            original = assets.VerifiedNativeBundle.verify
            checks = iter((1, 2))

            def verify(self):
                original(self)
                return next(checks)

            monkeypatch.setattr(assets.VerifiedNativeBundle, "verify", verify)
        with pytest.raises((ValueError, ImportError)):
            handle.load_extension(
                "engine", "extension.so", dependencies=("engine.so",) if failure == "dependency" else ()
            )
    finally:
        lease.close()


@pytest.mark.parametrize("when", ["before", "during"])
def test_checked_binary_rejects_replacement_and_writes_during_read(tmp_path, monkeypatch, when):
    binary = tmp_path / "engine.so"
    binary.write_bytes(b"engine")
    original = assets.os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        value = original(fd)
        calls += 1
        if calls == (1 if when == "before" else 2):
            fields = {key: getattr(value, key) for key in dir(value) if key.startswith("st_")}
            fields["st_ino" if when == "before" else "st_ctime_ns"] += 1
            return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(assets.os, "fstat", changed)
    with pytest.raises(ValueError, match="before read" if when == "before" else "while reading"):
        assets._checked(binary, hashlib.sha256(b"engine").hexdigest(), 6, readonly=False)


def test_verified_extension_accepts_exact_declared_images(captured, tmp_path, monkeypatch):
    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    lease = store.acquire(source, manifest)
    handle = handle_for(tmp_path, source, bundle, manifest, lease)
    assert handle.address == manifest["bundles"][0]["address"]
    module = SimpleNamespace(__file__=str(handle.directory / "extension.so"))
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
    monkeypatch.setattr(assets.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(assets.importlib.util, "module_from_spec", lambda _: module)
    monkeypatch.setattr(assets, "_images", lambda name: {handle.directory / name})
    try:
        assert handle.load_extension("engine", "extension.so", dependencies=("engine.so",)) is module
    finally:
        lease.close()


def test_prune_uninitialized_cache_is_empty(tmp_path):
    store = assets.NativeAssetStore(tmp_path / "absent")
    assert store.prune() == []
    assert not store.root.exists()


def test_incomplete_manifest_publication_is_removed_and_retryable(captured, tmp_path, monkeypatch):
    from pathlib import Path

    source, bundle, manifest = captured
    store = assets.NativeAssetStore(tmp_path / "cache")
    original = Path.open

    class ShortWrite:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, data):
            return self.file.write(data[:-1])

    def opened(path, *args, **kwargs):
        file = original(path, *args, **kwargs)
        return ShortWrite(file) if path.name == "manifest.json" and args == ("xb",) else file

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", opened)
        with pytest.raises(OSError, match="Incomplete"):
            store.acquire(source, manifest)
    assert list(store.root.iterdir()) == []
    assert not any(store.leases.values())
    lease = store.acquire(source, manifest)
    assert handle_for(tmp_path, source, bundle, manifest, lease).verify()
    lease.close()
