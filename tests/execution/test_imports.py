"""Isolation checks execute in a fresh -I -S interpreter, never the pytest host."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import sysconfig

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "src"
SITE = sysconfig.get_path("purelib")


def run(tmp_path, body):
    assert importlib.util.find_spec("simulatte.execution.imports"), "isolated imports missing"
    root = tmp_path / "project"
    snapshot = tmp_path / "snapshot"
    root.mkdir(exist_ok=True)
    snapshot.mkdir(exist_ok=True)
    script = f"""import sys
sys.path[:0] = {repr([str(RUNTIME), SITE])}
from pathlib import Path
from simulatte.execution.imports import install_import_policy
root=Path({str(root)!r}); snapshot=Path({str(snapshot)!r})
{body}
"""
    return subprocess.run([sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, timeout=10)


def test_snapshot_modules_shadow_installed_and_editable_sources(tmp_path):
    result = run(
        tmp_path,
        """(root/'helper.py').write_text('value="working"')
(snapshot/'helper.py').write_text('value="snapshot"')
policy=install_import_policy(root,(snapshot,),tuple(Path(p) for p in sys.path if p))
import helper
assert helper.value=="snapshot"
(root/'helper.py').write_text('value="edited"')
assert helper.value=="snapshot"
assert policy.audit()['helper']=="snapshot:helper.py"
""",
    )
    assert result.returncode == 0, result.stderr


def test_lazy_import_escape_is_blocked_before_execution(tmp_path):
    result = run(
        tmp_path,
        """policy=install_import_policy(root,(snapshot,),tuple(Path(p) for p in sys.path if p))
(root/'leaked.py').write_text('raise AssertionError("executed working file")')
sys.path.insert(0,str(root))
try:
    import leaked
except ImportError as error:
    assert "snapshot" in str(error)
else:
    raise AssertionError("escape accepted")
""",
    )
    assert result.returncode == 0, result.stderr


def test_namespace_and_lazy_submodules_stay_in_snapshot(tmp_path):
    result = run(
        tmp_path,
        """(snapshot/'package').mkdir()
(snapshot/'package'/'late.py').write_text('value=42')
policy=install_import_policy(root,(snapshot,),tuple(Path(p) for p in sys.path if p))
import package
import importlib
late=importlib.import_module('package.late')
assert late.value==42
assert policy.audit()['package.late']=="snapshot:package/late.py"
""",
    )
    assert result.returncode == 0, result.stderr


def test_bootstrap_skips_executable_startup_hooks(tmp_path):
    assert importlib.util.find_spec("simulatte.execution.bootstrap"), "bootstrap is missing"
    root = tmp_path / "project"
    root.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    external = tmp_path / "site"
    external.mkdir()
    marker = tmp_path / "unsafe"
    (external / "hook.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n")
    (external / "sitecustomize.py").write_text('raise AssertionError("sitecustomize ran")')
    (snapshot / "plant.py").write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "@dataclass(frozen=True)\nclass Params:\n    n:int=2\n"
        "@simulatte.model(params=Params,horizon=10)\ndef build(env,layout,params): pass\n"
    )
    config = {
        "project_root": str(root),
        "snapshot_roots": [str(snapshot)],
        "external_roots": [str(RUNTIME), SITE, str(external)],
        "reference": "plant.py:build",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(RUNTIME / "simulatte/execution/bootstrap.py")],
        input=json.dumps(config),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert metadata["models"][0]["name"] == "build"
    assert metadata["models"][0]["parameters"]["fields"][0]["default"] == 2
    assert not marker.exists()


def test_policy_rejects_unverifiable_and_wrong_local_origins(tmp_path):
    from simulatte.execution.imports import ImportPolicy
    from importlib.machinery import ModuleSpec

    root = tmp_path / "project"
    root.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (snapshot / "helper.py").write_text("x=1")
    policy = ImportPolicy(root, (snapshot,), (external,))
    with pytest.raises(ImportError, match="snapshot"):
        policy._check("helper", ModuleSpec("helper", None, origin=str(external / "helper.py")))
    with pytest.raises(ImportError, match="origin"):
        policy._check("custom", ModuleSpec("custom", None))
    with pytest.raises(ImportError, match="outside"):
        policy._check("outside", ModuleSpec("outside", None, origin=str(tmp_path / "out.py")))
    with pytest.raises(ValueError):
        ImportPolicy(root, (), ())
    assert policy._origin("sys", "built-in") == "built-in"


def test_inspect_configuration_data_only_roundtrip_and_errors(tmp_path, monkeypatch):
    from simulatte.execution.bootstrap import inspect_configuration, inspect_model

    root = tmp_path / "project"
    root.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    model = snapshot / "plant.py"
    model.write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "@dataclass(frozen=True)\nclass P: n:int=3\n"
        "@simulatte.model(params=P,horizon=5)\ndef build(env,layout,params): pass\n"
    )
    original_modules = set(sys.modules)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "path", list(sys.path))
        patch.setattr(sys, "meta_path", list(sys.meta_path))
        patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
        patch.chdir(Path.cwd())
        result = inspect_configuration(
            {
                "project_root": str(root),
                "snapshot_roots": [str(snapshot)],
                "external_roots": [str(RUNTIME), SITE],
                "reference": "plant.py",
            }
        )
        assert result["selected"] == "build"
        assert result["models"][0]["parameters"]["fields"][0]["default"] == 3
        for reference in ["../escape.py", "plant.py:missing"]:
            with pytest.raises(ValueError):
                inspect_model(reference, (snapshot,))
        model.write_text("x=1\n")
        with pytest.raises(ValueError, match="No decorated"):
            inspect_model("plant.py", (snapshot,))
    for name in set(sys.modules) - original_modules:
        if name.startswith("_simulatte_model"):
            sys.modules.pop(name, None)


def test_bootstrap_main_real_inspection_and_bad_config(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from simulatte.execution.bootstrap import main

    root = tmp_path / "project"
    root.mkdir()
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "plant.py").write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "@dataclass(frozen=True)\nclass P: n:int=1\n"
        "@simulatte.model(params=P,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    config = {
        "project_root": str(root),
        "snapshot_roots": [str(snap)],
        "external_roots": [str(RUNTIME), SITE],
        "reference": "plant.py:build",
    }
    for raw, expected in [(json.dumps(config).encode(), 0), (b"invalid", 2), (b"x" * (256 * 1024 + 1), 2)]:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "path", list(sys.path))
            patch.setattr(sys, "meta_path", list(sys.meta_path))
            patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
            patch.chdir(Path.cwd())
            patch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
            output = io.StringIO()
            patch.setattr(sys, "stdout", output)
            patch.setattr(sys, "stderr", io.StringIO())
            assert main() == expected
            if expected == 0:
                assert json.loads(output.getvalue())["selected"] == "build"


def test_import_time_declared_data_read_is_rejected(tmp_path, monkeypatch):
    from simulatte.execution.bootstrap import inspect_configuration

    root = tmp_path / "project"
    root.mkdir()
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / "data.csv").write_text("1")
    (snap / "plant.py").write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        'with open("data.csv") as f: DATA=f.read()\n'
        "@dataclass(frozen=True)\nclass P: n:int=1\n"
        '@simulatte.model(params=P,horizon=1,inputs=["data.csv"])\ndef build(env,layout,params): pass\n'
    )
    with monkeypatch.context() as patch:
        patch.setattr(sys, "path", list(sys.path))
        patch.setattr(sys, "meta_path", list(sys.meta_path))
        patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
        patch.chdir(Path.cwd())
        with pytest.raises(ValueError, match="during build"):
            inspect_configuration(
                {
                    "project_root": str(root),
                    "snapshot_roots": [str(snap)],
                    "external_roots": [str(RUNTIME), SITE],
                    "reference": "plant.py",
                }
            )


def test_project_developing_simulatte_loads_its_snapshot_runtime(tmp_path):
    import shutil

    snap = tmp_path / "snapshot"
    snap.mkdir()
    shutil.copytree(RUNTIME / "simulatte", snap / "src" / "simulatte", ignore=shutil.ignore_patterns("__pycache__"))
    (snap / "plant.py").write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "@dataclass(frozen=True)\nclass P: n:int=1\n"
        "@simulatte.model(params=P,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(RUNTIME / "simulatte/execution/bootstrap.py")],
        input=json.dumps(
            {
                "project_root": str(tmp_path),
                "snapshot_roots": [str(snap), str(snap / "src")],
                "external_roots": [str(RUNTIME), SITE],
                "reference": "plant.py",
            }
        ),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["imports"]["simulatte"] == "snapshot:src/simulatte/__init__.py"


def test_relative_package_model_selection_and_policy_origins(tmp_path, monkeypatch):
    from simulatte.execution.bootstrap import inspect_model
    from simulatte.execution.imports import ImportPolicy
    from importlib.machinery import ModuleSpec

    pkg = tmp_path / "sp4_unique_package"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "plant.py").write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "@dataclass(frozen=True)\nclass P: n:int=1\n"
        "@simulatte.model(params=P,stop=simulatte.FinitePopulation(arrivals=0,safety_limit=2))\n"
        "def first(env,layout,params): pass\n"
        "@simulatte.model(params=P,horizon=2)\ndef second(env,layout,params): pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(ValueError, match="Select a model"):
            inspect_model("sp4_unique_package/plant.py", (tmp_path,))
        assert inspect_model("sp4_unique_package/plant.py:second", (tmp_path,))["selected"] == "second"
        policy = ImportPolicy(tmp_path / "working", (tmp_path,), ())
        assert policy.find_spec("sp4_not_installed_name") is None
        spec = ModuleSpec("sp4_unique_package", None, is_package=True)
        spec.submodule_search_locations = [str(pkg)]
        policy._check("sp4_unique_package", spec)
        assert policy.observed["sp4_unique_package"].startswith("namespace:")
        assert policy.find_spec("sp4_unique_package", [str(tmp_path)]) is not None
    finally:
        for name in list(sys.modules):
            if name.startswith("sp4_unique_package"):
                sys.modules.pop(name)


def test_ambiguous_local_roots_and_editable_allowlist_are_rejected(tmp_path):
    from simulatte.execution.imports import ImportPolicy

    working = tmp_path / "working"
    working.mkdir()
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / "shared.py").write_text("VALUE=1")
    with pytest.raises(ValueError, match="Ambiguous"):
        ImportPolicy(working, (first, second), ())
    with pytest.raises(ValueError, match="editable"):
        ImportPolicy(working, (first,), (working,))
