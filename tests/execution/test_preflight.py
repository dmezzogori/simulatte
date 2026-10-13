"""Remote preflight: data-only checks of pyproject.toml, uv.lock and .python-version (design 9, D101, D105)."""

import hashlib
from typing import Any

import pytest

from simulatte.execution.preflight import PreflightError, preflight
from simulatte.execution.schema import _runtime
from simulatte.execution.snapshot import discover_project

PYPI = '{ registry = "https://pypi.org/simple" }'
HASH = "c" * 64


def lock_text(*packages):
    text = 'version = 1\nrequires-python = ">=3.11"\n'
    for name, version, source in packages:
        text += f'\n[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {source}\n'
    return text


def project(tmp_path, *packages, requires=">=3.11", include=("plant.py", "uv.lock", "packages/**")):
    root = tmp_path / "project"
    root.mkdir(parents=True)
    patterns = ", ".join(f'"{item}"' for item in include)
    requirement = "" if requires is None else f'requires-python = "{requires}"\n'
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "plant-project"\nversion = "0.1.0"\n{requirement}'
        f"[tool.simulatte.bundle]\ninclude = [{patterns}]\n"
    )
    (root / "plant.py").write_text("x = 1\n")
    packages = packages or (("simulatte", "0.17.2", PYPI), ("plant-project", "0.1.0", '{ virtual = "." }'))
    (root / "uv.lock").write_text(lock_text(*packages))
    return discover_project(root / "plant.py", root)


def check(spec, **changes):
    arguments: dict[str, Any] = {
        "coordinator_python": "3.14.1",
        "trusted_runtime_hash": HASH,
        "simulatte_version": "0.17.0",
    }
    return preflight(spec, spec.root / "uv.lock", **(arguments | changes))


def code(spec, **changes):
    with pytest.raises(PreflightError) as caught:
        check(spec, **changes)
    return caught.value.code


def test_spec_pins_the_lock_python_and_local_packages(tmp_path):
    spec = project(
        tmp_path,
        ("simulatte", "0.17.2", PYPI),
        ("simpy", "4.1.1", PYPI),
        ("plant-project", "0.1.0", '{ editable = "." }'),
        ("Tools_Pkg", "0.2.0", '{ directory = "packages/tools" }'),
        ("wheel", "1.0", '{ path = "packages/wheel-1.0-py3-none-any.whl" }'),
    )
    (spec.root / "packages" / "tools").mkdir(parents=True)
    result = check(spec)
    assert result.warnings == []
    assert result.spec == {
        "kind": "uv",
        "python": "3.14.1",
        "lock_hash": hashlib.sha256((spec.root / "uv.lock").read_bytes()).hexdigest(),
        "pyproject_hash": hashlib.sha256((spec.root / "pyproject.toml").read_bytes()).hexdigest(),
        "extras": [],
        "groups": [],
        "local_packages": ["plant-project", "tools-pkg"],
        "trusted_runtime_hash": HASH,
    }
    _runtime(result.spec)  # An exact ExecutionRequest uv runtime.


def test_path_dependency_outside_the_root_is_rejected(tmp_path):
    spec = project(tmp_path, ("simulatte", "0.17.0", PYPI), ("shared", "1.0", '{ directory = "../shared" }'))
    assert code(spec) == "path_dependency_outside_root"
    spec = project(tmp_path / "wheel", ("simulatte", "0.17.0", PYPI), ("w", "1.0", '{ path = "/tmp/w.whl" }'))
    assert code(spec) == "path_dependency_outside_root"


def test_private_registry_is_a_warning_with_its_url(tmp_path):
    private = '{ registry = "https://pkgs.example.com/simple" }'
    spec = project(tmp_path, ("simulatte", "0.17.0", PYPI), ("inhouse", "2.0", private), ("other", "1.0", private))
    assert check(spec).warnings == ["private_index:https://pkgs.example.com/simple"]


def test_missing_or_uncaptured_lock(tmp_path):
    spec = project(tmp_path)
    (spec.root / "uv.lock").unlink()
    assert code(spec) == "missing_lock"
    spec = project(tmp_path / "untracked", include=("plant.py",))
    assert code(spec) == "missing_lock"


def test_lock_without_simulatte(tmp_path):
    assert code(project(tmp_path, ("simpy", "4.1.1", PYPI))) == "missing_simulatte_dependency"


def test_locked_simulatte_must_match_the_coordinator_major_minor(tmp_path):
    spec = project(tmp_path, ("simulatte", "0.16.0", PYPI))
    assert code(spec) == "incompatible_simulatte"
    from simulatte.provenance import _simulatte_version

    own = project(tmp_path / "own", ("simulatte", _simulatte_version(), PYPI))
    assert check(own, simulatte_version=None).spec["kind"] == "uv"


def test_python_version_pin_against_requires_python(tmp_path):
    spec = project(tmp_path, requires=">=3.12,<4")
    pin = spec.root / ".python-version"
    for text, expected in (("3.12.4\n", "3.12.4"), ("# pinned\n3.14\n", "3.14.1"), ("3.13", "3.13")):
        pin.write_text(text)
        assert check(spec).spec["python"] == expected
    pin.write_text("3.11.9\n")
    assert code(spec) == "python_pin_outside_requires"
    pin.write_text("pypy@3.10\n")
    assert code(spec) == "invalid_python_pin"
    pin.unlink()
    assert code(spec, coordinator_python="3.11.2") == "python_pin_outside_requires"
    for requires in ("~=3.12", " "):
        unparsed = project(tmp_path / f"unparsed{len(requires)}", requires=requires)
        assert check(unparsed).warnings == ["requires_python_unparsed"]
    unpinned = project(tmp_path / "unpinned", requires=None)
    assert check(unpinned).warnings == [] and check(unpinned, coordinator_python="2.7.18").spec["python"] == "2.7.18"


@pytest.mark.parametrize(
    "requires,accepted",
    [(">=3.14", True), ("<3.14", False), ("<=3.14", False), ("==3.14.1", True), ("!=3.14.1", False), (">3.13", True)],
)
def test_requires_python_simple_forms(tmp_path, requires, accepted):
    spec = project(tmp_path, requires=requires)
    if accepted:
        assert check(spec).spec["python"] == "3.14.1"
    else:
        assert code(spec) == "python_pin_outside_requires"


def test_bundle_over_the_cap(tmp_path):
    spec = project(tmp_path)
    (spec.root / "layout.json").write_text("{}")
    size = sum(path.stat().st_size for path in spec.root.iterdir() if path.is_file())
    assert check(spec, cap=size).spec["kind"] == "uv"
    assert code(spec, cap=size - 1) == "bundle_too_large"
