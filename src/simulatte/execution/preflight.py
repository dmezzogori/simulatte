"""Local checks before a project is scheduled on remote workers (design section 9, D101, D105).

Data only: ``pyproject.toml``, ``uv.lock`` and ``.python-version`` are parsed with ``tomllib``
and plain text handling. No uv command runs here and uv is not required on the coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import tomllib
from typing import Any

from simulatte.execution.snapshot import ProjectSpec, _members, non_source_members

DEFAULT_CAP = 64 << 20
_PYPI = "https://pypi.org/simple"
# Lock sources resolved inside the project root: local packages, never installed remotely (D47).
LOCAL_SOURCES = ("editable", "directory", "virtual", "path")
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_CLAUSE = re.compile(r"(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})")


class PreflightError(ValueError):
    """A project that cannot run remotely. ``code`` is one of ``missing_lock``,
    ``missing_simulatte_dependency``, ``incompatible_simulatte``, ``path_dependency_outside_root``,
    ``python_pin_outside_requires``, ``invalid_python_pin`` or ``bundle_too_large``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Preflight:
    spec: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _numbers(text: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in text.split("."))
    return parts + (0,) * (3 - len(parts))  # PEP 440 zero padding: "<4" is "<4.0.0".


def _satisfies(version: str, requires: str) -> bool | None:
    """Whether the request admits a compatible patch; exact pins admit only themselves.

    ``None`` when ``requires`` uses a form other than comma-separated simple comparisons.
    """
    clauses = [clause.strip() for clause in requires.split(",") if clause.strip()]
    if not clauses:
        return None
    bounds = []
    for clause in clauses:
        match = _CLAUSE.fullmatch(clause)
        if match is None:
            return None
        bounds.append((match.group(1), _numbers(match.group(2))))
    candidates = {_numbers(version)}
    if len(version.split(".")) == 2:
        prefix = _numbers(version)[:2]
        # Comparisons change only at a bound or immediately after it. Testing
        # those patches plus zero covers every interval without enumerating it.
        for _, bound in bounds:
            if bound[:2] == prefix:
                candidates.update((bound, (*prefix, bound[2] + 1)))
    return any(all(_compare(actual, operator, bound) for operator, bound in bounds) for actual in candidates)


def _compare(actual: tuple[int, ...], operator: str, bound: tuple[int, ...]) -> bool:
    return {
        ">=": actual >= bound,
        ">": actual > bound,
        "<=": actual <= bound,
        "<": actual < bound,
        "==": actual == bound,
        "!=": actual != bound,
    }[operator]


def _python(root: Path, coordinator_python: str) -> str:
    """The Python request: ``.python-version`` as given (``X.Y`` or ``X.Y.Z``), else the coordinator's
    ``X.Y``. A bare ``X.Y`` lets each host's uv pick an available patch (a coordinator's exact patch
    is often not in a host's uv inventory); the resolved version is recorded as evidence."""
    path = root / ".python-version"
    if not path.is_file():
        return ".".join(coordinator_python.split(".")[:2])
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    pins = [line for line in lines if line and not line.startswith("#")]
    if not pins or _VERSION.fullmatch(pins[0]) is None:
        raise PreflightError("invalid_python_pin", ".python-version must pin a version such as 3.12 or 3.12.4")
    return pins[0]


def _bundle_bytes(project: ProjectSpec) -> int:
    names = {*_members(project), *non_source_members(project)}
    if (project.root / project.layout).is_file():
        names.add(project.layout)
    return sum((project.root / name).stat().st_size for name in names)


def preflight(
    project: ProjectSpec,
    lock_path: Path,
    *,
    cap: int = DEFAULT_CAP,
    coordinator_python: str,
    trusted_runtime_hash: str,
    simulatte_version: str | None = None,
) -> Preflight:
    """Build the uv provisioning spec for ``project`` or raise ``PreflightError``.

    ``simulatte_version`` defaults to the coordinator's installed version; the lock must pin
    ``simulatte`` at the same major.minor. Extras and groups stay empty until experiment files
    can name them.
    """
    root = project.root.resolve()
    lock_path = Path(lock_path)
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file() or not lock_path.is_file():
        raise PreflightError("missing_lock", "Remote workers need pyproject.toml and uv.lock in the project root")
    lock_name = lock_path.resolve().relative_to(root).as_posix() if lock_path.resolve().is_relative_to(root) else None
    if lock_name is None or lock_name not in _members(project):
        raise PreflightError("missing_lock", "uv.lock is not part of the captured source; track it or include it")
    warnings: list[str] = []
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    locked = None
    local_packages = []
    base = lock_path.resolve().parent
    for package in lock.get("package", []):
        name = normalize(str(package.get("name", "")))
        source = package.get("source", {})
        if name == "simulatte":
            locked = str(package.get("version", ""))
        if "registry" in source:
            registry = str(source["registry"])
            if registry.rstrip("/") != _PYPI:
                warnings.append(f"private_index:{registry}")
        for kind in LOCAL_SOURCES:
            if kind not in source:
                continue
            target = (base / os.fsdecode(source[kind])).resolve()
            if not target.is_relative_to(root):
                raise PreflightError(
                    "path_dependency_outside_root", f"Locked package {name} resolves outside the project root: {target}"
                )
            local_packages.append(name)
    if locked is None:
        raise PreflightError("missing_simulatte_dependency", "uv.lock does not contain simulatte")
    if simulatte_version is None:
        from simulatte.provenance import _simulatte_version

        simulatte_version = _simulatte_version()
    if locked.split(".")[:2] != simulatte_version.split(".")[:2]:
        raise PreflightError(
            "incompatible_simulatte",
            f"uv.lock pins simulatte {locked}; this coordinator runs {simulatte_version} (same major.minor required)",
        )
    python = _python(root, coordinator_python)
    requires = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("requires-python")
    if requires is not None:
        verdict = _satisfies(python, str(requires))
        if verdict is None:
            warnings.append("requires_python_unparsed")
        elif not verdict:
            raise PreflightError(
                "python_pin_outside_requires", f"Python {python} does not satisfy requires-python {requires}"
            )
    size = _bundle_bytes(project)
    if size > cap:
        raise PreflightError("bundle_too_large", f"Project bundle is {size} bytes; the remote cap is {cap}")
    spec = {
        "kind": "uv",
        "python": python,
        "lock_hash": _sha256(lock_path),
        "pyproject_hash": _sha256(pyproject),
        "extras": [],
        "groups": [],
        "local_packages": sorted(set(local_packages)),
        "trusted_runtime_hash": trusted_runtime_hash,
    }
    return Preflight(spec, sorted(set(warnings)))
