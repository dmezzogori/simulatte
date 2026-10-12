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

    # CPython deliberately skips tracing audit hooks unless they opt in. Keep
    # instrumentation in the test; production inspection need not trace hooks.
    add_hook = sys.addaudithook

    def traced_hook(hook):
        hook.__cantrace__ = True
        add_hook(hook)

    monkeypatch.setattr(sys, "addaudithook", traced_hook)

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


@pytest.mark.parametrize(
    ("prefix", "import_paths", "regular_packages", "filename"),
    [
        ("", (".",), (), "model.py"),
        ("", (".",), ("plant",), "model.py"),
        ("", (".",), ("plant/section",), "model.py"),
        ("src/", ("src",), (), "model.py"),
        ("src/", (".", "src"), (), "model.py"),
        ("src/", (".", "src"), ("plant",), "model.py"),
        ("src/", ("src", "."), ("plant/section",), "model.py"),
        ("src/", (".", "src"), ("plant",), "__init__.py"),
    ],
)
def test_model_relative_imports_use_declared_namespace_roots(
    tmp_path, prefix, import_paths, regular_packages, filename
):
    snapshot = tmp_path / "snapshot"
    package = snapshot / prefix / "plant"
    section = package / "section"
    section.mkdir(parents=True)
    for name in regular_packages:
        (snapshot / prefix / name / "__init__.py").write_text("")
    (package / "outer.py").write_text("OFFSET=3\n")
    (section / "helpers.py").write_text("VALUE=9\n")
    (section / filename).write_text(
        "from dataclasses import dataclass\nfrom .helpers import VALUE\nfrom ..outer import OFFSET\n"
        "import simulatte\nassert __package__ == 'plant.section'\n"
        "@dataclass(frozen=True)\nclass Params: n:int=VALUE+OFFSET\n"
        "@simulatte.model(params=Params,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    config = {
        "project_root": str(tmp_path / "project"),
        "snapshot_root": str(snapshot),
        "snapshot_roots": [str(snapshot / name) for name in import_paths],
        "external_roots": [str(RUNTIME), SITE],
        "reference": prefix + "plant/section/" + filename,
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
    assert metadata["models"][0]["parameters"]["fields"][0]["default"] == 12
    module_name = "plant.section" if filename == "__init__.py" else "plant.section.model"
    assert metadata["imports"][module_name] == "snapshot:" + prefix + "plant/section/" + filename
    assert metadata["imports"]["plant.section.helpers"] == "snapshot:" + prefix + "plant/section/helpers.py"


@pytest.mark.parametrize("reference", ["plant.py", "examples/demo-model.py", "examples-with-dash/model.py"])
def test_standalone_models_keep_private_module_names(tmp_path, reference, monkeypatch):
    from simulatte.execution.bootstrap import inspect_model

    path = tmp_path / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from dataclasses import dataclass\nimport simulatte\n"
        "assert __name__ == '_simulatte_model'\n"
        "@dataclass(frozen=True)\nclass Params: n:int=2\n"
        "@simulatte.model(params=Params,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    monkeypatch.setitem(sys.modules, "_simulatte_model", None)
    assert inspect_model(reference, (tmp_path,))["selected"] == "build"


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


@pytest.mark.parametrize("damage", ["valid", "oversize", "header_eof", "body_eof"])
def test_private_bootstrap_configuration_is_bounded(tmp_path, monkeypatch, damage):
    import socket
    import struct
    import threading
    from simulatte.execution import bootstrap, child

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    config = {"snapshot_roots": [str(tmp_path)], "external_roots": [str(RUNTIME), SITE]}
    data = json.dumps(config).encode()
    payload = struct.pack(">I", len(data)) + data
    if damage == "oversize":
        payload = struct.pack(">I", (256 << 10) + 1)
    if damage == "header_eof":
        payload = b"\0"
    if damage == "body_eof":
        payload = payload[:-1]

    def server():
        connection, _ = listener.accept()
        with connection:
            assert connection.recv(6) == b"token\n"
            for offset in range(0, len(payload), 3):
                connection.sendall(payload[offset : offset + 3])
            connection.shutdown(socket.SHUT_WR)

    thread = threading.Thread(target=server)
    thread.start()

    def serve(received, connection):
        assert received == config
        assert connection.gettimeout() is None
        return 7

    monkeypatch.setattr(child, "serve", serve)
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        if damage == "valid":
            monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--child", str(listener.getsockname()[1])])
            monkeypatch.setenv("SIMULATTE_CHILD_TOKEN", "token")
            assert bootstrap.main() == 7
        else:
            with pytest.raises(ValueError):
                bootstrap._worker_entry(listener.getsockname()[1], "token")
    finally:
        thread.join(2)
        listener.close()


def test_model_root_is_separate_from_configured_import_roots(tmp_path):
    import json
    import subprocess

    root = tmp_path / "project"
    root.mkdir()
    snapshot = tmp_path / "snapshot"
    source = snapshot / "src"
    source.mkdir(parents=True)
    (source / "helper.py").write_text("VALUE=9\n")
    (snapshot / "hidden.py").write_text("raise RuntimeError('root must not become an import path')\n")
    (snapshot / "plant.py").write_text(
        "from dataclasses import dataclass\nimport importlib.util\nimport helper\nimport simulatte\n"
        "assert importlib.util.find_spec('hidden') is None\n"
        "@dataclass(frozen=True)\nclass Params: n:int=helper.VALUE\n"
        "@simulatte.model(params=Params,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    config = {
        "project_root": str(root),
        "snapshot_root": str(snapshot),
        "snapshot_roots": [str(source)],
        "external_roots": [str(RUNTIME), SITE],
        "reference": "plant.py",
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
    assert metadata["models"][0]["parameters"]["fields"][0]["default"] == 9
    assert metadata["imports"]["helper"] == "snapshot:src/helper.py"


def test_guarded_imports_preserve_standard_distribution_metadata(tmp_path, monkeypatch):
    from importlib import metadata
    from simulatte.execution.imports import install_import_policy

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def versions():
        return sorted((dist.metadata["Name"], dist.version) for dist in metadata.distributions(path=[SITE]))

    expected = versions()
    assert expected
    with monkeypatch.context() as patch:
        patch.setattr(sys, "path", list(sys.path))
        patch.setattr(sys, "meta_path", list(sys.meta_path))
        patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
        install_import_policy(tmp_path / "project", (snapshot,), (RUNTIME, Path(SITE)))
        assert versions() == expected


def test_explicit_source_root_cannot_contain_foreign_import_roots(tmp_path):
    from simulatte.execution.imports import ImportPolicy

    source, foreign = tmp_path / "source", tmp_path / "foreign"
    source.mkdir()
    foreign.mkdir()
    with pytest.raises(ValueError, match="source snapshot"):
        ImportPolicy(tmp_path / "working", (foreign,), (), source_root=source)
    with pytest.raises(ValueError, match="source snapshot"):
        ImportPolicy(tmp_path / "working", (source,), (), source_root=tmp_path / "missing")
