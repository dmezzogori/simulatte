"""Bounded layout bytes, durable backups and conflict-checked replacement.

The input owner serializes revision checks around replacement. Filesystem rename
and the run index cannot form one transaction: callers must invalidate and rescan
on any replacement/commit error, retaining the separately committed backup.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import _identity
from simulatte.execution.store import RevisionConflict, _blob, _read
from simulatte.layout.io import parse_layout
from simulatte.layout.layers import FileLayer
from simulatte.layout.types import LayoutLimits


def _path(root, name):
    root = Path(root).resolve()
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or "\\" in name:
        raise ValueError("Layout path must be normalized and project-relative")
    path = root / name
    if path.resolve() != path:
        raise ValueError("Layout path must not contain symlink components")
    return path


@dataclass(frozen=True, slots=True)
class LayoutFile:
    name: str
    raw: bytes | None

    @property
    def digest(self):
        return None if self.raw is None else hashlib.sha256(self.raw).hexdigest()

    def layer(self, *, limits=LayoutLimits()):
        return FileLayer({"schema_version": "1.0"}) if self.raw is None else parse_layout(self.raw, limits=limits)


def read_layout_file(root, name, *, limits=LayoutLimits()):
    path = _path(root, name)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return LayoutFile(name, None)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Layout must be a regular file")
    if before.st_size > limits.max_file_bytes:
        raise ValueError("Layout byte limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise RevisionConflict("Layout changed on disk before reading")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limits.max_file_bytes + 1)
        if (
            len(raw) > limits.max_file_bytes
            or _identity(os.fstat(descriptor)) != _identity(before)
            or _identity(path.lstat()) != _identity(before)
            or _path(root, name) != path
        ):
            raise RevisionConflict("Layout changed on disk while reading")
    finally:
        os.close(descriptor)
    return LayoutFile(name, raw)


def backup_layout(store, captured: LayoutFile):
    """Commit exact prior bytes separately, before any replacement transaction."""
    data = {
        "schema": 1,
        "path": captured.name,
        "raw_base64": None if captured.raw is None else base64.b64encode(captured.raw).decode("ascii"),
    }
    identity = canonical_hash("layout-backup-v1", data)
    with store._transaction() as db:
        row = db.execute("SELECT kind,manifest FROM snapshots WHERE id=?", (identity,)).fetchone()
        if row is not None:
            if row[0] != "layout-backup" or _read(row[1]) != data:
                raise ValueError("Layout backup identity mismatch")
        else:
            db.execute("INSERT INTO snapshots VALUES (?,?,?)", (identity, "layout-backup", _blob(data)))
    return identity


@dataclass(slots=True)
class StagedLayout:
    root: Path
    name: str
    temporary: Path
    limits: LayoutLimits
    replacement: LayoutFile
    replaced: bool = False

    def replace(self, *, expected: LayoutFile):
        if self.replaced:
            raise ValueError("Staged layout was already replaced")
        path = _path(self.root, self.name)
        if expected.name != self.name or read_layout_file(self.root, self.name, limits=self.limits) != expected:
            raise RevisionConflict("Layout changed on disk; reload before saving")
        # An uncooperative external editor can still race this final check and
        # rename. The durable backup preserves what this owner observed.
        if os.name == "nt":
            replace_durable(self.temporary, path)
        else:
            os.replace(self.temporary, path)
        self.replaced = True
        sync_directory(path.parent)


@contextmanager
def stage_layout(root, name, layer: FileLayer, *, limits=LayoutLimits()):
    path = _path(root, name)
    raw = (json.dumps(layer.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    parse_layout(raw, limits=limits)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            if file.write(raw) != len(raw):
                raise OSError("Incomplete layout write")
            file.flush()
            os.fsync(file.fileno())
        yield StagedLayout(Path(root).resolve(), name, temporary, limits, LayoutFile(name, raw))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
