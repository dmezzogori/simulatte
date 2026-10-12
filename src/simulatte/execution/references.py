"""Verified local data documents referenced by bounded worker control messages."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from simulatte._wire import canonical_pack, freeze, unpack
from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.schema import _hash, _plain

MAX_DOCUMENT = 128 << 20
_KINDS = frozenset(("request", "model-info", "layout", "runtime"))


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_document(reference, kind):
    if not isinstance(reference, Mapping) or set(reference) != {"path", "length", "sha256"}:
        raise ValueError("Invalid document reference fields")
    length = reference["length"]
    if type(length) is not int or not 0 < length <= MAX_DOCUMENT:
        raise ValueError("Invalid document reference length")
    _hash(reference["sha256"])
    if type(reference["path"]) is not str or not Path(reference["path"]).is_absolute():
        raise ValueError("Document reference path must be absolute")
    path = Path(reference["path"])
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.resolve() != path:
        raise ValueError("Document must be a regular file without symlink components")
    if before.st_size != length:
        raise ValueError("Document length mismatch")
    with path.open("rb") as file:
        if _identity(os.fstat(file.fileno())) != _identity(before):
            raise ValueError("Document changed before reading")
        data = file.read(length + 1)
        if _identity(os.fstat(file.fileno())) != _identity(before):
            raise ValueError("Document changed while reading")
    if _identity(path.lstat()) != _identity(before):
        raise ValueError("Document changed after reading")
    if len(data) != length or hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError("Document content hash/length mismatch")
    document = unpack(data)
    if (
        not isinstance(document, Mapping)
        or set(document) != {"schema", "kind", "value"}
        or type(document["schema"]) is not int
        or document["schema"] != 1
        or kind not in _KINDS
        or document["kind"] != kind
        or not isinstance(document["value"], Mapping)
    ):
        raise ValueError("Invalid document schema/kind/value")
    return _plain(document["value"])


def write_document(root: Path, kind: str, value: Mapping) -> dict:
    if kind not in _KINDS or not isinstance(value, Mapping):
        raise ValueError("Invalid document kind/value")
    data = canonical_pack(freeze({"schema": 1, "kind": kind, "value": value}))
    if len(data) > MAX_DOCUMENT:
        raise ValueError("Document exceeds segment limit")
    if root.is_symlink():
        raise ValueError("Document directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    sync_directory(root.parent)
    digest = hashlib.sha256(data).hexdigest()
    path = root / (digest + ".msgpack")
    reference = {"path": str(path), "length": len(data), "sha256": digest}
    if path.exists() or path.is_symlink():
        read_document(reference, kind)
        return reference
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as file:
            temporary = Path(file.name)
            if file.write(data) != len(data):
                raise OSError("Incomplete document write")
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o444)
        replace_durable(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return reference
