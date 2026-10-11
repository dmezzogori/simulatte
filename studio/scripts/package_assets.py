"""Copy the production frontend and publish a reproducible asset manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import tomllib

root = Path(__file__).resolve().parents[2]
# The hook is also the source fingerprint authority. Its build dependency is provisioned by uv.
spec = importlib.util.spec_from_file_location("studio_build", root / "hatch_build.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
stamp = json.loads((root / "studio/apps/viewer/dist/build-stamp.json").read_text())
if stamp["source_fingerprint"] != module.source_fingerprint(root):
    raise RuntimeError("Frontend sources changed since the build. Run pnpm --dir studio build first.")
target = root / "src/simulatte/studio/static"
target.parent.mkdir(parents=True, exist_ok=True)
with TemporaryDirectory(prefix=".static-build-", dir=target.parent) as temporary:
    staged_root = Path(temporary)
    staged = staged_root / "src/simulatte/studio/static"
    # Keep the backup outside automatic cleanup so a failed rollback cannot delete it.
    backup = staged_root.with_name(staged_root.name + "-previous")
    shutil.copytree(root / "studio/apps/viewer/dist", staged)
    shutil.copy(root / "studio/THIRD_PARTY_NOTICES.md", staged)
    manifest = {
        "version": tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"],
        "source_fingerprint": stamp["source_fingerprint"],
        "assets": {
            p.relative_to(staged).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(staged.rglob("*"))
            if p.is_file()
        },
    }
    (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    module.validate_assets(staged_root, manifest["version"])
    if stamp["source_fingerprint"] != module.source_fingerprint(root):
        raise RuntimeError("Frontend sources changed during packaging. Run pnpm --dir studio build first.")
    try:
        if target.exists() or target.is_symlink():
            target.replace(backup)
        staged.replace(target)
        module.validate_assets(root, manifest["version"])
    except BaseException:
        if backup.exists() or backup.is_symlink():
            if target.exists():
                shutil.rmtree(target)
            backup.replace(target)
        elif not staged.exists() and target.exists():
            shutil.rmtree(target)
        raise
    if backup.is_symlink():
        backup.unlink()
    elif backup.exists():
        shutil.rmtree(backup)
print(f"Packaged {len(manifest['assets'])} Studio assets")
