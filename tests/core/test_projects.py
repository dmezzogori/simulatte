"""Project discovery never executes code; edits and starters preserve user files."""

from dataclasses import replace
import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


def api():
    assert importlib.util.find_spec("simulatte.projects"), "project definitions are missing"
    return importlib.import_module("simulatte.projects")


@pytest.fixture(params=[False, True], ids=["bundle", "git"])
def project(tmp_path, request):
    if request.param:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pyproject.toml").write_text(
        '# retained comment\n[project]\nname = "example" # retained inline\nversion = "0.1"\n'
        '\n[tool.simulatte.bundle]\ninclude = ["*.py"]\n'
    )
    (tmp_path / "model.py").write_text('raise AssertionError("do not import")\n')
    return tmp_path


def definition(root):
    m = api()
    return m.WorkspaceDefinition(
        root,
        str(uuid4()),
        (m.ModelRegistration(str(uuid4()), "plant", "Plant", "model.py:build", "layouts/plant.json"),),
        (),
        "plant",
    )


def test_targets(tmp_path):
    m = api()
    assert m.resolve_target(None, cwd=tmp_path).root == tmp_path
    assert m.resolve_target("a/b", cwd=tmp_path).root == tmp_path / "a/b"
    assert (tmp_path / "a/b").is_dir()
    (tmp_path / "folder.py").mkdir()
    assert m.resolve_target("folder.py", cwd=tmp_path).legacy_reference is None
    (tmp_path / "model.py").write_text("raise RuntimeError()")
    target = m.resolve_target("model.py:alternate", cwd=tmp_path)
    assert target.root == tmp_path
    assert target.legacy_reference == str(tmp_path / "model.py") + ":alternate"
    assert m.resolve_target("model.py", cwd=tmp_path).legacy_reference == str(tmp_path / "model.py")
    (tmp_path / "data.txt").write_text("data")
    with pytest.raises(ValueError, match="Python"):
        m.resolve_target("data.txt", cwd=tmp_path)


@pytest.mark.parametrize(
    "reference", ["missing.py:build", "missing.py:bad-name", "missing.py:", "nosuch/folder/model.py", "model.py"]
)
def test_legacy_reference_never_creates_directory(tmp_path, reference):
    with pytest.raises(ValueError):
        api().resolve_target(reference, cwd=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_config_roundtrip_preserves_unrelated_toml_and_rejects_stale(project):
    m = api()
    original = (project / "pyproject.toml").read_bytes()
    workspace = definition(project)
    edit = m.preview_config(project, workspace)
    assert (project / "pyproject.toml").read_bytes() == original
    m.apply_config(edit)
    assert (project / "pyproject.toml").read_bytes().startswith(original)
    assert m.load_workspace(project) == workspace
    assert m.load_workspace(project).models[0].id == workspace.models[0].id
    edit = m.preview_config(project, replace(workspace, default_model=None))
    (project / "pyproject.toml").write_bytes((project / "pyproject.toml").read_bytes() + b"\n# user edit\n")
    with pytest.raises(ValueError, match="changed"):
        m.apply_config(edit)
    assert (project / "pyproject.toml").read_bytes().endswith(b"# user edit\n")


def test_invalid_definitions_are_rejected(project):
    m = api()
    workspace = definition(project)
    model = workspace.models[0]
    for bad in [
        replace(workspace, models=(model, replace(model, key="other"))),
        replace(workspace, default_model="missing"),
        replace(workspace, id="bad"),
        replace(workspace, models=(replace(model, entrypoint="../escape.py:build"),)),
        replace(workspace, models=(replace(model, layout="../escape.json"),)),
    ]:
        with pytest.raises(ValueError):
            m.preview_config(project, bad)
    m.apply_config(m.preview_config(project, workspace))
    path = project / "pyproject.toml"
    path.write_text(path.read_text().replace("schema = 1", "schema = 99"))
    with pytest.raises(ValueError, match="schema"):
        m.load_workspace(project)


def test_symlinks_and_config_creation(tmp_path):
    m = api()
    root = tmp_path / "actual"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert m.resolve_target("link", cwd=tmp_path).root == root
    assert m.load_workspace(root) is None
    workspace = definition(root)
    outside = tmp_path / "outside.py"
    outside.write_text("pass")
    (root / "model.py").symlink_to(outside)
    with pytest.raises(ValueError, match="escape"):
        m.preview_config(root, workspace)
    (root / "model.py").unlink()
    edit = m.preview_config(root, workspace)
    (root / "pyproject.toml").write_text("# user file")
    with pytest.raises(ValueError, match="changed"):
        m.apply_config(edit)


@pytest.mark.parametrize("kind", ["production", "logistics"])
def test_starters_run_as_bare_python_and_have_explicit_membership(tmp_path, kind):
    m = api()
    files = m.starter_files(kind)
    m.install_starter(tmp_path, files)
    workspace = m.load_workspace(tmp_path)
    registration = workspace.models[0]
    from simulatte.execution.snapshot import capture_source, discover_project

    spec = discover_project(tmp_path / registration.entrypoint.split(":")[0], layout=registration.layout)
    assert spec.include
    assert not (tmp_path / ".git").exists()
    assert {e.path for e in capture_source(spec).entries} == {"models/" + kind + ".py", "pyproject.toml"}
    result = subprocess.run(
        [sys.executable, str(tmp_path / registration.entrypoint.split(":")[0])], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    before = {name: (tmp_path / name).read_bytes() for name in files}
    with pytest.raises(FileExistsError):
        m.install_starter(tmp_path, files)
    assert before == {name: (tmp_path / name).read_bytes() for name in files}


def test_starter_rolls_back_only_created_files(tmp_path, monkeypatch):
    m = api()
    (tmp_path / "keep").write_text("mine")
    original = Path.open

    def fail(path, *args, **kwargs):
        if path.name == "second.py":
            raise OSError("disk failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="disk failure"):
        m.install_starter(tmp_path, {"nested/first.py": b"pass", "second.py": b"pass"})
    assert list(tmp_path.iterdir()) == [tmp_path / "keep"]
    with pytest.raises(ValueError):
        m.install_starter(tmp_path, {"../escape": b"bad"})
