"""Validate prebuilt Studio assets; building an sdist never needs Node."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    excluded = {"node_modules", "dist", "test-results", "playwright-report", ".vite", ".build"}
    inputs = [root / "src/simulatte/layout/schema/layout-v1.schema.json", *(root / "studio").rglob("*")]
    for path in sorted(inputs, key=lambda p: p.as_posix()):
        relative = path.relative_to(root)
        if path.is_file() and not excluded.intersection(relative.parts):
            digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_assets(root: Path, version: str) -> None:
    directory = root / "src/simulatte/studio/static"
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["version"] != version:
            raise ValueError("package version differs")
        assets = manifest["assets"]
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        if actual != set(assets) | {"manifest.json"} or "index.html" not in assets:
            raise ValueError("asset inventory differs")
        for name, expected in assets.items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                raise ValueError("asset hash differs")
        if (root / "studio").is_dir() and manifest["source_fingerprint"] != source_fingerprint(root):
            raise ValueError("frontend source changed")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            "Studio assets missing or stale. Run pnpm --dir studio build, then "
            "uv run python studio/scripts/package_assets.py."
        ) from error


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if version != "editable":
            validate_assets(Path(self.root), self.metadata.version)
