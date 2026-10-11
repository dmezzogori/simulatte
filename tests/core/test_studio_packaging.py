"""Packaging preserves the build fingerprint and previously published assets."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import subprocess

import pytest


@pytest.fixture
def packaging(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    script = tmp_path / "studio/scripts/package_assets.py"
    script.parent.mkdir(parents=True)
    shutil.copy(repository / "studio/scripts/package_assets.py", script)
    shutil.copy(repository / "studio/scripts/stamp.mjs", script.with_name("stamp.mjs"))
    schema = tmp_path / "src/simulatte/layout/schema/layout-v1.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copy(repository / "src/simulatte/layout/schema/layout-v1.schema.json", schema)
    shutil.copy(repository / "hatch_build.py", tmp_path / "hatch_build.py")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.14.0"\n')
    (tmp_path / "studio/THIRD_PARTY_NOTICES.md").write_text("notices")
    source = tmp_path / "studio/source.ts"
    dist = tmp_path / "studio/apps/viewer/dist"
    dist.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("packaging_test_hook", tmp_path / "hatch_build.py")
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    def build(value):
        source.write_text(value)
        (dist / "index.html").write_text(value)
        fingerprint = hook.source_fingerprint(tmp_path)
        (dist / "build-stamp.json").write_text(json.dumps({"source_fingerprint": fingerprint}))
        return fingerprint

    build("previous build")
    runpy.run_path(str(script), run_name="__main__")
    target = tmp_path / "src/simulatte/studio/static"
    previous = {path.name: path.read_bytes() for path in target.iterdir()}
    fingerprint = build("next build")
    return script, source, dist, target, previous, fingerprint


def test_packaging_keeps_build_fingerprint(packaging):
    script, _, _, target, _, fingerprint = packaging
    runpy.run_path(str(script), run_name="__main__")
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["source_fingerprint"] == fingerprint
    assert (target / "index.html").read_text() == "next build"
    assert list(target.parent.iterdir()) == [target]


def test_source_edit_during_copy_preserves_previous_assets(packaging, monkeypatch):
    script, source, dist, target, previous, _ = packaging
    original = shutil.copytree

    def copy_and_edit(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if Path(src) == dist:
            source.write_text("edited while copying")
        return result

    monkeypatch.setattr(shutil, "copytree", copy_and_edit)
    with pytest.raises(RuntimeError, match="sources changed|assets missing or stale"):
        runpy.run_path(str(script), run_name="__main__")
    assert {path.name: path.read_bytes() for path in target.iterdir()} == previous
    assert list(target.parent.iterdir()) == [target]


def test_source_edit_at_publication_restores_previous_assets(packaging, monkeypatch):
    script, source, _, target, previous, _ = packaging
    original = Path.replace
    edited = False

    def replace_and_edit(path, destination):
        nonlocal edited
        result = original(path, destination)
        if Path(destination) == target and path.name == "static" and not edited:
            edited = True
            source.write_text("edited at publication")
        return result

    monkeypatch.setattr(Path, "replace", replace_and_edit)
    with pytest.raises(RuntimeError, match="sources changed|assets missing or stale"):
        runpy.run_path(str(script), run_name="__main__")
    assert edited
    assert {path.name: path.read_bytes() for path in target.iterdir()} == previous
    assert list(target.parent.iterdir()) == [target]


def test_imported_schema_edit_rejects_stale_assets(packaging):
    script, _, _, target, _, _ = packaging
    runpy.run_path(str(script), run_name="__main__")
    previous = {path.name: path.read_bytes() for path in target.iterdir()}
    root = script.parents[2]
    schema = root / "src/simulatte/layout/schema/layout-v1.schema.json"
    schema.write_text(schema.read_text() + "\n")
    hook = runpy.run_path(str(root / "hatch_build.py"))
    with pytest.raises(RuntimeError, match="assets missing or stale"):
        hook["validate_assets"](root, "0.14.0")
    with pytest.raises(RuntimeError, match="sources changed"):
        runpy.run_path(str(script), run_name="__main__")
    assert {path.name: path.read_bytes() for path in target.iterdir()} == previous


def test_node_and_python_fingerprints_include_imported_schema(packaging):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not available")
    script, _, dist, _, _, _ = packaging
    root = script.parents[2]
    stamp_script = script.with_name("stamp.mjs")
    subprocess.run([node, str(stamp_script), "begin"], check=True, capture_output=True)
    before = json.loads((root / "studio/.build/source.json").read_text())
    hook = runpy.run_path(str(root / "hatch_build.py"))
    assert before["source_fingerprint"] == hook["source_fingerprint"](root)
    previous_stamp = (dist / "build-stamp.json").read_bytes()
    schema = root / "src/simulatte/layout/schema/layout-v1.schema.json"
    schema.write_text(schema.read_text() + "\n")
    result = subprocess.run([node, str(stamp_script)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Frontend inputs changed during the build" in result.stderr
    assert (dist / "build-stamp.json").read_bytes() == previous_stamp
