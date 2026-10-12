"""Working bytes, membership and materialization define immutable run inputs."""

from __future__ import annotations

import importlib
import importlib.util
import subprocess

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.snapshot"), "snapshot capture is missing"
    return importlib.import_module("simulatte.execution.snapshot")


def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["*.py", "data/*.csv"]\n')
    (tmp_path / "plant.py").write_text("VALUE=1\n")
    (tmp_path / "helper.py").write_text("HELP=2\n")
    (tmp_path / "layout.json").write_text("{}")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/cal.csv").write_text("rate\n1\n")
    return tmp_path / "plant.py"


def test_capture_inputs_dirty_source_and_detached_materialization(tmp_path):
    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    source = m.capture_source(spec)
    snapshot = m.capture_inputs(source, ("data/cal.csv",))
    paths = m.materialize(snapshot, tmp_path / ".simulatte/attempt")
    model.write_text("VALUE=changed\n")
    (tmp_path / "data/cal.csv").write_text("rate\nchanged\n")
    assert (paths.root / "plant.py").read_text() == "VALUE=1\n"
    assert (paths.root / "data/cal.csv").read_text() == "rate\n1\n"
    assert not (paths.root / "layout.json").exists()
    assert (paths.root / "plant.py").stat().st_ino != model.stat().st_ino
    assert "data/cal.csv" not in {e.path for e in snapshot.source.entries}
    assert snapshot.inputs.entries[0].path == "data/cal.csv"
    assert len(snapshot.source.content_hash) == 64


def test_archive_and_identity_ignore_mtime_and_listing_order(tmp_path):
    m = api()
    model = project(tmp_path)
    first = m.capture_source(m.discover_project(model))
    (tmp_path / "helper.py").touch()
    second = m.capture_source(m.discover_project(model))
    assert first.content_hash == second.content_hash
    assert first.archive.read_bytes() == second.archive.read_bytes()
    model.write_text("VALUE=2\n")
    assert m.capture_source(m.discover_project(model)).content_hash != first.content_hash


def test_git_reads_working_bytes_and_only_explicit_membership(tmp_path):
    m = api()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "plant.py").write_text("a=1")
    (tmp_path / "ignored.py").write_text("secret")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "plant.py", ".gitignore"], check=True)
    (tmp_path / "plant.py").write_text("a=2")
    spec = m.discover_project(tmp_path / "plant.py")
    source = m.capture_source(spec)
    assert {e.path for e in source.entries} == {"plant.py", ".gitignore"}
    paths = m.materialize(m.capture_inputs(source, ()), tmp_path / ".simulatte/attempt")
    assert (paths.root / "plant.py").read_text() == "a=2"
    (tmp_path / "plant.py").unlink()
    with pytest.raises(ValueError, match="model"):
        m.capture_source(spec)


def test_non_git_requires_explicit_include(tmp_path):
    m = api()
    model = tmp_path / "plant.py"
    model.write_text("pass")
    with pytest.raises(ValueError, match="include"):
        m.discover_project(model)


def test_symlink_escape_fifo_and_case_collision_rejected(tmp_path):
    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    outside = tmp_path.parent / "outside-sp4.py"
    outside.write_text("outside")
    link = tmp_path / "escape.py"
    link.symlink_to(outside)
    try:
        with pytest.raises(ValueError, match="escap"):
            m.capture_source(spec)
    finally:
        link.unlink()
        outside.unlink()
    if hasattr(__import__("os"), "mkfifo"):
        __import__("os").mkfifo(tmp_path / "fifo.py")
        with pytest.raises(ValueError, match="regular"):
            m.capture_source(spec)


def test_internal_symlink_is_materialized_and_tamper_detected(tmp_path):
    m = api()
    model = project(tmp_path)
    (tmp_path / "alias.py").symlink_to("helper.py")
    source = m.capture_source(m.discover_project(model))
    snapshot = m.capture_inputs(source, ())
    paths = m.materialize(snapshot, tmp_path / ".simulatte/attempt")
    assert not (paths.root / "alias.py").is_symlink()
    assert (paths.root / "alias.py").read_text() == "HELP=2\n"
    source.archive.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        m.materialize(snapshot, tmp_path / ".simulatte/other")


def test_limits_and_layout_input_conflict(tmp_path):
    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    source = m.capture_source(spec)
    with pytest.raises(ValueError, match="layout"):
        m.capture_inputs(source, ("layout.json",))
    with pytest.raises(ValueError, match="missing"):
        m.capture_inputs(source, ("absent.csv",))
    from dataclasses import replace

    with pytest.raises(ValueError, match="limit"):
        m.capture_source(replace(spec, max_bytes=1))
    with pytest.raises(ValueError, match="limit"):
        m.capture_source(replace(spec, max_files=1))


def test_snapshot_reloads_from_verified_manifest(tmp_path):
    m = api()
    assert hasattr(m, "load_source"), "durable snapshot loading is missing"
    model = project(tmp_path)
    spec = m.discover_project(model)
    original = m.capture_source(spec)
    loaded = m.load_source(spec, original.content_hash)
    assert loaded.entries == original.entries
    assert loaded.archive_hash == original.archive_hash
    manifest = original.archive.with_suffix(".msgpack")
    manifest.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        m.load_source(spec, original.content_hash)


def test_capture_retries_observed_concurrent_edits(tmp_path, monkeypatch):
    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    copy = m._copy
    count = 0

    def unstable(root, name, destination, remaining):
        nonlocal count
        result = copy(root, name, destination, remaining)
        if name == "plant.py" and destination is not None:
            count += 1
            model.write_text(f"VALUE={count + 1}")
        return result

    monkeypatch.setattr(m, "_copy", unstable)
    with pytest.raises(m.SnapshotChanged, match="three"):
        m.capture_source(spec)
    assert count == 3


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "CON.py", "bad:stream.py", "x\\y.py", "x./file.py"])
def test_invalid_manifest_paths_fail_without_escape(tmp_path, name):
    m = api()
    model = project(tmp_path)
    source = m.capture_source(m.discover_project(model))
    with pytest.raises(ValueError):
        m.capture_inputs(source, (name,))


def test_editable_runtime_is_frozen_separately(tmp_path):
    m = api()
    assert hasattr(m, "freeze_runtime"), "trusted runtime freezing is missing"
    package = tmp_path / "editable" / "simulatte"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION=1")
    store = tmp_path / "project"
    store.mkdir()
    runtime = m.freeze_runtime(package, store)
    (package / "__init__.py").write_text("VERSION=2")
    assert (runtime.root / "simulatte/__init__.py").read_text() == "VERSION=1"
    assert len(runtime.content_hash) == 64


@pytest.mark.parametrize(
    "config",
    [
        '[tool.simulatte.bundle]\ninclude="*.py"',
        '[tool.simulatte.bundle]\ninclude=["*.py"]\npython-path=[]',
        '[tool.simulatte.bundle]\ninclude=["*.py"]\nmax_bytes=0',
        '[tool.simulatte.bundle]\ninclude=["*.py"]\n[tool.uv.sources]\nx={path="../outside"}',
        '[tool.simulatte.bundle]\ninclude=["*.py"]\n[tool.uv.workspace]\nmembers=["../outside"]',
    ],
)
def test_invalid_project_configuration_fails_preflight(tmp_path, config):
    m = api()
    model = project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(config)
    with pytest.raises(ValueError):
        m.discover_project(model)


def test_explicit_root_and_src_import_roots(tmp_path):
    m = api()
    model = project(tmp_path)
    (tmp_path / "src").mkdir()
    assert m.discover_project(model, tmp_path).python_path == (".", "src")
    with pytest.raises(ValueError, match="escape"):
        m.discover_project(model, tmp_path / "src")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.simulatte.bundle]\ninclude=["*.py"]\n'
        'python-path=[".","src"]\n[tool.uv.sources]\nx={path="src"}\n[tool.uv.workspace]\nmembers=["src/*"]\n'
    )
    assert m.discover_project(model).python_path == (".", "src")


def test_missing_source_and_changed_inputs_do_not_publish(tmp_path):
    from dataclasses import replace

    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    source = m.capture_source(spec)
    (tmp_path / "helper.py").write_text("changed")
    with pytest.raises(m.SnapshotChanged):
        m.capture_inputs(source, ("data/cal.csv",))
    source = m.capture_source(spec)
    with pytest.raises(ValueError):
        m.capture_inputs(source, ("plant.py",))
    with pytest.raises(ValueError):
        m.capture_inputs(source, ("wild*.csv",))
    with pytest.raises(ValueError):
        m.capture_inputs(replace(source, project=replace(spec, exclude=("data/*",))), ("data/cal.csv",))
    with pytest.raises(ValueError):
        m.capture_inputs(replace(source, project=replace(spec, max_files=1)), ("a", "b"))
    with pytest.raises(ValueError):
        m.load_source(spec, "a" * 64)
    with pytest.raises(ValueError):
        m.load_source(spec, source.content_hash, kind="other")
    source.archive.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        m.capture_source(spec)


def test_deleted_tracked_helper_is_removed_from_membership(tmp_path):
    m = api()
    model = project(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    # No include pattern recaptures a deleted path.
    (tmp_path / "helper.py").unlink()
    source = m.capture_source(m.discover_project(model))
    assert "helper.py" not in {entry.path for entry in source.entries}


def test_loader_rejects_wrong_manifest_schema_and_hash(tmp_path):
    from simulatte._wire import freeze, pack, unpack

    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    source = m.capture_source(spec)
    path = source.archive.with_suffix(".msgpack")
    decoded = unpack(path.read_bytes())
    assert isinstance(decoded, m.Mapping)
    original = dict(decoded)
    variants = [
        {},
        original | {"schema": 2},
        original | {"entries": "wrong"},
        original | {"archive_hash": "a" * 64},
        original | {"entries": ()},
        original | {"entries": ({"path": "x", "size": -1, "sha256": "a" * 64, "executable": False, "link": None},)},
    ]
    for data in variants:
        path.write_bytes(pack(freeze(data)))
        with pytest.raises(ValueError):
            m.load_source(spec, source.content_hash)


def test_runtime_cache_verification_and_executable_mode(tmp_path):
    m = api()
    package = tmp_path / "editable" / "simulatte"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION=1")
    (package / "run.py").write_text("pass")
    (package / "run.py").chmod(0o755)
    store = tmp_path / "project"
    store.mkdir()
    first = m.freeze_runtime(package, store)
    assert m.freeze_runtime(package, store) == first
    target = first.root / "simulatte/run.py"
    assert target.stat().st_mode & 0o111
    target.chmod(0o755)
    target.write_text("modified")
    with pytest.raises(ValueError, match="modified"):
        m.freeze_runtime(package, store)


def test_local_package_roots_use_metadata_without_importing(tmp_path):
    import json
    from types import SimpleNamespace

    m = api()
    root = tmp_path / "project"
    root.mkdir()
    pkg = root / "packages" / "local"
    (pkg / "src" / "library").mkdir(parents=True)
    (pkg / "src" / "library" / "__init__.py").write_text('raise AssertionError("imported")')

    def dist(name, path):
        return SimpleNamespace(
            metadata={"Name": name},
            read_text=lambda _: json.dumps({"url": path.as_uri(), "dir_info": {"editable": True}}),
        )

    roots = m.local_package_roots(
        root, {"tool": {"uv": {"workspace": {"members": ["packages/*"]}}}}, [dist("local", pkg)]
    )
    assert roots == ("packages/local/src",)
    with pytest.raises(ValueError, match="escapes"):
        m.local_package_roots(root, {}, [dist("other", tmp_path)])
    assert m.local_package_roots(root, {}, [dist("simulatte", tmp_path)]) == ()
    with pytest.raises(ValueError, match="metadata"):
        m.local_package_roots(root, {}, [SimpleNamespace(metadata={"Name": "broken"}, read_text=lambda _: "{")])


def test_input_capture_rechecks_source_after_copy_and_combined_file_bound(tmp_path, monkeypatch):
    from dataclasses import replace

    m = api()
    source = m.capture_source(m.discover_project(project(tmp_path)))
    (tmp_path / "extra.csv").write_text("2")
    limited = replace(source, project=replace(source.project, max_files=len(source.entries)))
    with pytest.raises(ValueError, match="file limit"):
        m.capture_inputs(limited, ("extra.csv",))
    original = m._copy

    def mutate(root, name, destination, remaining):
        result = original(root, name, destination, remaining)
        if name == "extra.csv" and destination is not None:
            (tmp_path / "helper.py").write_text("CHANGED=1")
        return result

    monkeypatch.setattr(m, "_copy", mutate)
    with pytest.raises(m.SnapshotChanged):
        m.capture_inputs(source, ("extra.csv",))


@pytest.mark.parametrize("damage", ["duplicate", "traversal", "symlink", "size", "bytes", "missing"])
def test_untrusted_archive_members_are_checked_before_materialization(tmp_path, damage):
    from dataclasses import replace
    import hashlib
    import io
    import tarfile

    m = api()
    source = m.capture_source(m.discover_project(project(tmp_path)))
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as target:
        for entry in source.entries:
            if damage == "missing":
                continue
            info = tarfile.TarInfo("../escape" if damage == "traversal" else entry.path)
            data = (tmp_path / entry.path).read_bytes()
            if damage == "size":
                data += b"extra"
            if damage == "bytes":
                data = b"x" * len(data)
            info.size = len(data)
            if damage == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "../escape"
            target.addfile(info, io.BytesIO(data))
            if damage == "duplicate":
                target.addfile(info, io.BytesIO(data))
    bad = replace(source, archive=archive, archive_hash=hashlib.sha256(archive.read_bytes()).hexdigest())
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(ValueError):
        m._extract(bad, destination)
    assert not (tmp_path / "escape").exists()


def test_membership_change_retries_and_entry_collision_rejected(tmp_path, monkeypatch):
    m = api()
    source_spec = m.discover_project(project(tmp_path))
    original = m._copy
    changed = False

    def adding(root, name, destination, remaining):
        nonlocal changed
        result = original(root, name, destination, remaining)
        if destination is not None and not changed:
            (root / "added.py").write_text("NEW=1")
            changed = True
        return result

    monkeypatch.setattr(m, "_copy", adding)
    source = m.capture_source(source_spec)
    assert "added.py" in {e.path for e in source.entries}
    with pytest.raises(ValueError, match="collision"):
        m._validate_names(["Plant.py", "plant.py"])
    with pytest.raises(ValueError):
        m.Entry("ok.py", -1, "a" * 64, False)
    with pytest.raises(ValueError):
        m.Entry("ok.py", 1, "a" * 64, False, "../escape")


def test_local_dependency_missing_or_nonlocal_metadata(tmp_path):
    import json
    from types import SimpleNamespace

    m = api()

    def dist(data):
        return SimpleNamespace(metadata={"Name": "dependency"}, read_text=lambda _: json.dumps(data))

    assert (
        m.local_package_roots(tmp_path, {}, [dist({"url": "https://example.invalid/a.whl", "archive_info": {}})]) == ()
    )
    with pytest.raises(ValueError, match="metadata"):
        m.local_package_roots(tmp_path, {}, [dist({"url": "https://example.invalid/source", "dir_info": {}})])
    with pytest.raises(ValueError, match="missing"):
        m.local_package_roots(tmp_path, {"tool": {"uv": {"sources": {"missing": {"path": "absent"}}}}}, [])


def test_atomic_replace_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "plant.py"
    path.write_text("OLD")
    real_open = m.os.open

    def racing_open(name, flags, *args, **kwargs):
        replacement = tmp_path / "replacement"
        replacement.write_text("NEW")
        replacement.replace(path)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(m.os, "open", racing_open)
    with pytest.raises(m.SnapshotChanged):
        m._copy(tmp_path, "plant.py", None, 100)


@pytest.mark.parametrize("remaining", [10, 100])
def test_source_growth_during_read_is_not_silently_captured(tmp_path, monkeypatch, remaining):
    m = api()
    path = tmp_path / "plant.py"
    path.write_bytes(b"123456")
    real_hash = m.hashlib.sha256
    changed = False

    class Digest:
        def __init__(self):
            self.actual = real_hash()

        def update(self, data):
            nonlocal changed
            self.actual.update(data)
            if not changed:
                with path.open("ab") as stream:
                    stream.write(b"12345")
                changed = True

        def hexdigest(self):
            return self.actual.hexdigest()

    monkeypatch.setattr(m.hashlib, "sha256", Digest)
    with pytest.raises(ValueError, match="limit|snapshot_changed"):
        m._copy(tmp_path, "plant.py", None, remaining)


def test_inputs_share_byte_quota_and_edits_after_copy_fail(tmp_path, monkeypatch):
    from dataclasses import replace

    m = api()
    source = m.capture_source(m.discover_project(project(tmp_path)))
    extra = tmp_path / "extra.csv"
    extra.write_text("more data")
    limited = replace(source, project=replace(source.project, max_bytes=sum(e.size for e in source.entries)))
    with pytest.raises(ValueError, match="Combined snapshot byte"):
        m.capture_inputs(limited, ("extra.csv",))
    copy = m._copy

    def changed(root, name, destination, remaining):
        entry = copy(root, name, destination, remaining)
        if name == "extra.csv" and destination is not None:
            extra.write_text("changed")
        return entry

    monkeypatch.setattr(m, "_copy", changed)
    with pytest.raises(m.SnapshotChanged, match="inputs"):
        m.capture_inputs(source, ("extra.csv",))


def test_manifest_reopen_obeys_size_caps(tmp_path):
    from dataclasses import replace

    m = api()
    source = m.capture_source(m.discover_project(project(tmp_path)))
    with pytest.raises(ValueError, match="byte limit"):
        m.load_source(replace(source.project, max_bytes=1), source.content_hash)
    with pytest.raises(ValueError, match="file limit"):
        m.load_source(replace(source.project, max_files=1), source.content_hash)
    with source.archive.with_suffix(".msgpack").open("wb") as stream:
        stream.truncate((64 << 20) + 1)
    with pytest.raises(ValueError, match="manifest exceeds"):
        m.load_source(source.project, source.content_hash)


@pytest.mark.parametrize("mode", ["conflict", "submodule", "removed_git"])
def test_git_membership_inconsistency_refuses_capture(tmp_path, mode):
    import shutil

    m = api()
    model = project(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    spec = m.discover_project(model)
    digest = subprocess.check_output(["git", "-C", str(tmp_path), "hash-object", "plant.py"]).decode().strip()
    if mode == "conflict":
        subprocess.run(
            ["git", "-C", str(tmp_path), "update-index", "--index-info"],
            input=f"100644 {digest} 1\thelper.py\n".encode(),
            check=True,
        )
    elif mode == "submodule":
        subprocess.run(
            ["git", "-C", str(tmp_path), "update-index", "--add", "--cacheinfo", f"160000,{digest},vendor"], check=True
        )
    else:
        shutil.rmtree(tmp_path / ".git")
    with pytest.raises(ValueError, match="conflicts|Submodule|Git source"):
        m.capture_source(spec)


def test_project_discovery_normalizes_symlinked_parent_without_losing_model_name(tmp_path):
    from simulatte.execution.snapshot import discover_project

    real = tmp_path / "real"
    real.mkdir()
    (real / "plant.py").write_text("pass\n")
    (real / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    project = discover_project(alias / "plant.py")
    assert project.root == real.resolve()
    assert project.model == "plant.py"


def test_capture_rejects_configuration_changed_after_project_discovery(tmp_path):
    m = api()
    model = project(tmp_path)
    spec = m.discover_project(model)
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    with pytest.raises(m.SnapshotChanged, match="configuration"):
        m.capture_source(spec)
    current = m.capture_source(m.discover_project(model))
    assert "helper.py" not in {entry.path for entry in current.entries}


def test_configuration_creation_and_removal_are_fenced_against_discovery(tmp_path):
    m = api()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    model = tmp_path / "plant.py"
    model.write_text("pass\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "plant.py"], check=True)
    absent = m.discover_project(model)
    assert absent.config_hash is not None
    original = m.capture_source(absent)
    (tmp_path / "pyproject.toml").write_text("")
    with pytest.raises(m.SnapshotChanged, match="configuration"):
        m.capture_source(absent)
    present = m.discover_project(model)
    assert present.config_hash != absent.config_hash
    m.capture_source(present)
    (tmp_path / "pyproject.toml").unlink()
    with pytest.raises(m.SnapshotChanged, match="configuration"):
        m.capture_source(present)
    assert m.capture_source(m.discover_project(model)).content_hash == original.content_hash
