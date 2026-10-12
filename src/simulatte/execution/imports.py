"""Validate supported import resolution against snapshot and external roots.

This is provenance enforcement for normal imports, not a security sandbox.
Explicit custom loaders/native reads remain the model author's responsibility.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
from pathlib import Path
import sys
from typing import Any


class ImportPolicy(importlib.abc.MetaPathFinder):
    def __init__(
        self,
        project_root: Path,
        snapshot_roots: tuple[Path, ...],
        external_roots: tuple[Path, ...],
        *,
        source_root: Path | None = None,
    ):
        self.project_root = project_root.resolve()
        self.snapshot_roots = tuple(root.resolve() for root in snapshot_roots)
        self.external_roots = tuple(root.resolve() for root in external_roots if root.exists())
        self.source_root = None if source_root is None else source_root.resolve()
        if self.source_root is not None and (
            not self.source_root.is_dir()
            or any(not root.is_relative_to(self.source_root) for root in self.snapshot_roots)
        ):
            raise ValueError("Import roots must be inside the source snapshot")
        if not self.snapshot_roots or any(not root.is_dir() for root in self.snapshot_roots):
            raise ValueError("Snapshot import roots must be existing directories")
        if any(self.project_root.is_relative_to(root) for root in self.external_roots):
            raise ValueError("External import root includes the editable project")
        self.local_names = set()
        origins: dict[tuple[str, ...], Path] = {}
        for root in self.snapshot_roots:
            for path in root.iterdir():
                if path.suffix == ".py":
                    self.local_names.add(path.stem)
                elif path.is_dir() and any(path.rglob("*.py")):
                    self.local_names.add(path.name)
            for path in root.rglob("*.py"):
                parts = path.relative_to(root).with_suffix("").parts
                name = parts[:-1] if parts[-1] == "__init__" else parts
                if not name or not all(part.isidentifier() for part in name):
                    continue
                previous = origins.setdefault(name, path.resolve())
                if previous != path.resolve():
                    raise ValueError("Ambiguous local import roots: " + ".".join(name))
        self.observed: dict[str, str] = {}

    def _origin(self, name: str, origin: str) -> str:
        if origin in ("built-in", "frozen"):
            return origin
        path = Path(origin).resolve()
        for root in (self.source_root,) if self.source_root is not None else self.snapshot_roots:
            if path.is_relative_to(root):
                return "snapshot:" + path.relative_to(root).as_posix()
        if name.partition(".")[0] in self.local_names:
            raise ImportError(f"{name}: local package must load from snapshot")
        for root in self.external_roots:
            if path.is_relative_to(root):
                return "external:" + str(path)
        if path.is_relative_to(self.project_root):
            raise ImportError(f"{name}: editable project import outside snapshot")
        raise ImportError(f"{name}: origin outside declared runtime and snapshot roots")

    def _check(self, name: str, spec: Any) -> None:
        locations = spec.submodule_search_locations
        if locations is not None:
            for location in locations:
                self._origin(name, location)
        if spec.origin is not None:
            self.observed[name] = self._origin(name, spec.origin)
        elif locations is not None:
            self.observed[name] = "namespace:" + ",".join(self._origin(name, str(path)) for path in locations)
        else:
            raise ImportError(f"{name}: custom loader has no verifiable origin")

    def find_spec(self, fullname: str, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None:
            self._check(fullname, spec)
        return spec

    def find_distributions(self, context):
        # Preserve the stdlib's data-only distribution metadata provider. This
        # does not restore editable finders or execute site/startup hooks.
        return importlib.machinery.PathFinder.find_distributions(context)

    def audit(self) -> dict[str, str]:
        for name, module in tuple(sys.modules.items()):
            spec = getattr(module, "__spec__", None)
            if spec is not None:
                self._check(name, spec)
        return dict(sorted(self.observed.items()))


def install_import_policy(
    project_root: Path,
    snapshot_roots: tuple[Path, ...],
    external_roots: tuple[Path, ...],
    *,
    source_root: Path | None = None,
) -> ImportPolicy:
    """Install only in a fresh isolated child, before any model imports."""
    policy = ImportPolicy(project_root, snapshot_roots, external_roots, source_root=source_root)
    # Never add .pth/.start/sitecustomize hooks. This explicitly replaces editable
    # finders; unsupported hook-dependent packages fail through normal imports.
    sys.path[:] = [str(root) for root in (*policy.snapshot_roots, *policy.external_roots)]
    sys.meta_path[:] = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, policy]
    sys.dont_write_bytecode = True
    return policy
