"""Content-addressed snapshots of explicitly selected working-tree bytes.

Archives are captured locally without importing model code. The exact captured
bytes define identity; observed concurrent edits cause a bounded whole-copy retry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from dataclasses import dataclass
import fnmatch
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unicodedata
from urllib.parse import urlsplit
from urllib.request import url2pathname

from simulatte.execution.hashing import canonical_hash
from simulatte._wire import pack, unpack, freeze
from simulatte.execution.schema import _hash
from simulatte.execution.durability import replace_durable, sync_directory

_RESERVED = {".git", ".simulatte", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    root: Path
    model: str
    layout: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    python_path: tuple[str, ...]
    git: bool
    max_bytes: int = 1 << 30
    max_files: int = 100_000
    config_hash: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    path: str
    size: int
    sha256: str
    executable: bool
    link: str | None = None

    def __post_init__(self):
        _path(self.path)
        _hash(self.sha256)
        if type(self.size) is not int or self.size < 0 or type(self.executable) is not bool:
            raise ValueError("Invalid snapshot entry")
        if self.link is not None:
            _path(self.link)

    def to_dict(self):
        return {
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "executable": self.executable,
            "link": self.link,
        }


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    project: ProjectSpec
    entries: tuple[Entry, ...]
    archive: Path
    content_hash: str
    archive_hash: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    source: SourceSnapshot
    inputs: SourceSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotPaths:
    root: Path
    python_path: tuple[Path, ...]


class SnapshotChanged(ValueError):
    """Working membership or bytes changed while copying."""


def _path(name: str, *, pattern: bool = False) -> str:
    if (
        type(name) is not str
        or not name
        or "\\" in name
        or any(c in name for c in '<>:"|')
        or "\0" in name
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
        or PurePosixPath(name).as_posix() != name
        or name == "."
        or any(ord(c) < 32 for c in name)
        or any(part.endswith((" ", ".")) for part in name.split("/"))
    ):
        raise ValueError("Expected a normalized portable relative path")
    if not pattern and any(c in name for c in "*?[]"):
        raise ValueError("Wildcards are only allowed in bundle include/exclude")
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part.split(".")[0].lower() in devices for part in name.split("/")):
        raise ValueError("Nonportable reserved filename")
    return name


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Cannot enumerate Git source membership")
    return result.stdout


def discover_project(
    model_path: str | Path, explicit_root: str | Path | None = None, *, layout: str = "layout.json"
) -> ProjectSpec:
    model = Path(model_path).absolute()
    model = model.parent.resolve() / model.name
    if explicit_root is not None:
        root = Path(explicit_root).resolve()
    else:
        root = next((p for p in model.parent.parents if (p / "pyproject.toml").is_file()), model.parent)
        if (model.parent / "pyproject.toml").is_file():
            root = model.parent
        if not (root / "pyproject.toml").is_file():
            result = subprocess.run(
                ["git", "-C", str(model.parent), "rev-parse", "--show-toplevel"], capture_output=True
            )
            root = Path(os.fsdecode(result.stdout).strip()) if result.returncode == 0 else model.parent
    root = root.resolve()
    if not model.resolve().is_relative_to(root):
        raise ValueError("model escapes project root")
    relative = _path(model.relative_to(root).as_posix())
    layout = _path(layout)
    config_bytes = (root / "pyproject.toml").read_bytes() if (root / "pyproject.toml").is_file() else None
    config = tomllib.loads(config_bytes.decode("utf-8")) if config_bytes is not None else {}
    bundle = config.get("tool", {}).get("simulatte", {}).get("bundle", {})
    patterns = {}
    for key in ("include", "exclude"):
        values = bundle.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"bundle.{key} must be a list")
        patterns[key] = tuple(_path(item, pattern=True) for item in values)
    git = (
        subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode
        == 0
    )
    if not git and not patterns["include"]:
        raise ValueError("Non-Git projects require bundle.include")
    roots = bundle.get("python-path", [".", *(["src"] if (root / "src").is_dir() else [])])
    if not isinstance(roots, list) or not roots:
        raise ValueError("bundle.python-path must be a nonempty list")
    for name in roots:
        if name != ".":
            _path(name)
        if not (root / name).resolve().is_relative_to(root):
            raise ValueError("Import root escapes project")
    roots = list(dict.fromkeys([*roots, *local_package_roots(root, config)]))
    limits = {name: bundle.get(name, default) for name, default in [("max_bytes", 1 << 30), ("max_files", 100_000)]}
    if any(type(value) is not int or value < 1 for value in limits.values()):
        raise ValueError("Invalid capture limit")
    config_hash = _configuration_hash(None if config_bytes is None else hashlib.sha256(config_bytes).hexdigest())
    return ProjectSpec(
        root,
        relative,
        layout,
        patterns["include"],
        patterns["exclude"],
        tuple(roots),
        git,
        **limits,
        config_hash=config_hash,
    )


def _configuration_hash(pyproject_hash):
    return canonical_hash("project-config-v1", {"pyproject": pyproject_hash})


def local_package_roots(root: Path, config: Mapping, distributions=None) -> tuple[str, ...]:
    """Discover local roots from data only; never execute build/editable hooks.

    Flat and src layouts are supported automatically. Other layouts require an
    explicit bundle.python-path. The separately frozen trusted Simulatte runtime
    is the only external editable-package exception.
    """
    packages: set[Path] = set()
    uv = config.get("tool", {}).get("uv", {})
    for declaration in uv.get("sources", {}).values():
        for variant in declaration if isinstance(declaration, list) else [declaration]:
            if isinstance(variant, dict) and "path" in variant:
                packages.add((root / variant["path"]).resolve())
    for name in uv.get("workspace", {}).get("members", []):
        _path(name, pattern=True)
        packages.update(path.resolve() for path in root.glob(name) if path.is_dir())
    for distribution in importlib.metadata.distributions() if distributions is None else distributions:
        raw = distribution.read_text("direct_url.json")
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if "dir_info" not in data:
                continue
            url = urlsplit(data["url"])
            if url.scheme != "file" or url.netloc not in ("", "localhost"):
                raise ValueError("Unsupported local package URL")
            path = Path(url2pathname(url.path)).resolve()
            if distribution.metadata["Name"].lower().replace("_", "-") == "simulatte" and not path.is_relative_to(root):
                continue
            packages.add(path)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("Invalid installed local package metadata") from error
    roots = set()
    for package in packages:
        if not package.is_relative_to(root):
            raise ValueError("Local path dependency escapes project root")
        if not package.is_dir():
            raise ValueError("Local package directory is missing")
        candidate = package / "src" if (package / "src").is_dir() else package
        relative = candidate.relative_to(root).as_posix()
        if relative != ".":
            _path(relative)
        roots.add(relative)
    return tuple(sorted(roots))


def _members(project: ProjectSpec) -> tuple[str, ...]:
    names = set()
    if project.git:
        raw = _git(project.root, "ls-files", "-z", "--stage", "--", ".")
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, _, stage = header.split()
            if stage != b"0":
                raise ValueError("Resolve Git merge conflicts before capture")
            if mode == b"160000":
                raise ValueError("Submodule source needs explicit supported in-root inclusion")
            name = os.fsdecode(path)
            if (project.root / name).exists() or (project.root / name).is_symlink():
                names.add(name)
    for pattern in project.include:
        for path in project.root.glob(pattern):
            if not path.is_dir():
                names.add(path.relative_to(project.root).as_posix())
    if (project.root / "pyproject.toml").is_file():
        names.add("pyproject.toml")
    names = {
        name
        for name in names
        if not set(PurePosixPath(name).parts) & _RESERVED
        and name != project.layout
        and not any(fnmatch.fnmatchcase(name, pattern) for pattern in project.exclude)
    }
    if project.model not in names:
        raise ValueError("Selected model is missing from source membership")
    _validate_names(names)
    return tuple(sorted(names, key=lambda n: n.encode("utf-8")))


def _validate_names(names) -> None:
    folded = set()
    for name in names:
        _path(name)
        normalized = unicodedata.normalize("NFC", name).casefold()
        if normalized in folded:
            raise ValueError("Source path normalization/case collision")
        folded.add(normalized)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _copy(root: Path, name: str, destination: Path | None, remaining: int) -> Entry:
    original = root / name
    try:
        resolved = original.resolve(strict=True)
    except FileNotFoundError as error:
        raise SnapshotChanged(f"missing source/input: {name}") from error
    if not resolved.is_relative_to(root):
        raise ValueError("Source symlink escapes project root")
    link = resolved.relative_to(root).as_posix() if original.is_symlink() else None
    before = resolved.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Source/input must be a regular file")
    if before.st_size > remaining:
        raise ValueError("Snapshot byte limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(resolved, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise SnapshotChanged("snapshot_changed")
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            sink = destination.open("wb") if destination is not None else None
            try:
                while data := source.read(1 << 20):
                    size += len(data)
                    if size > remaining:
                        raise ValueError("Snapshot byte limit exceeded")
                    digest.update(data)
                    if sink is not None:
                        sink.write(data)
                if sink is not None:
                    sink.flush()
                    os.fsync(sink.fileno())
            finally:
                if sink is not None:
                    sink.close()
        if (
            _identity(os.fstat(descriptor)) != _identity(before)
            or original.resolve() != resolved
            or _identity(resolved.stat()) != _identity(before)
        ):
            raise SnapshotChanged("snapshot_changed")
    finally:
        os.close(descriptor)
    return Entry(name, size, digest.hexdigest(), bool(before.st_mode & 0o111), link)


def _digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _publish(project: ProjectSpec, entries: tuple[Entry, ...], tree: Path, kind: str) -> SourceSnapshot:
    identity = canonical_hash(f"{kind}-manifest-v1", [entry.to_dict() for entry in entries])
    directory = project.root / ".simulatte" / "snapshots" / identity
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{kind}.tar"
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    os.close(fd)
    try:
        with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as target:
            for entry in entries:
                info = tarfile.TarInfo(entry.path)
                info.size = entry.size
                info.mode = 0o755 if entry.executable else 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with (tree / entry.path).open("rb") as content:
                    target.addfile(info, content)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        digest = _digest_file(Path(temporary))
        if archive.exists():
            if _digest_file(archive) != digest:
                raise ValueError("Stored snapshot archive hash mismatch")
        else:
            replace_durable(Path(temporary), archive)
    finally:
        Path(temporary).unlink(missing_ok=True)
    manifest = archive.with_suffix(".msgpack")
    payload = pack(
        freeze({"schema": 1, "kind": kind, "archive_hash": digest, "entries": [entry.to_dict() for entry in entries]})
    )
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        manifest_temp = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        replace_durable(manifest_temp, manifest)
    finally:
        manifest_temp.unlink(missing_ok=True)
    sync_directory(directory.parent)
    sync_directory(directory.parent.parent)
    return SourceSnapshot(project, entries, archive, identity, digest)


def capture_source(project: ProjectSpec) -> SourceSnapshot:
    cache = project.root / ".simulatte"
    cache.mkdir(exist_ok=True)
    for retry in range(3):
        names = _members(project)
        if len(names) > project.max_files:
            raise ValueError("Snapshot file limit exceeded")
        try:
            with tempfile.TemporaryDirectory(dir=cache, prefix="capture-") as temporary:
                tree = Path(temporary)
                entries = []
                remaining = project.max_bytes
                for name in names:
                    entry = _copy(project.root, name, tree / name, remaining)
                    entries.append(entry)
                    remaining -= entry.size
                if _members(project) != names:
                    raise SnapshotChanged("snapshot_changed: membership")
                for entry in entries:
                    if _copy(project.root, entry.path, None, project.max_bytes) != entry:
                        raise SnapshotChanged("snapshot_changed: bytes")
                if project.config_hash is not None:
                    config_hash = next((entry.sha256 for entry in entries if entry.path == "pyproject.toml"), None)
                    if _configuration_hash(config_hash) != project.config_hash:
                        raise SnapshotChanged("snapshot_changed: project configuration; rediscover project")
                return _publish(project, tuple(entries), tree, "source")
        except (SnapshotChanged, FileNotFoundError) as error:
            if retry == 2:
                raise SnapshotChanged(f"snapshot_changed after three capture attempts: {error}") from None
    raise AssertionError("unreachable")  # pragma: no cover


def _extract(source: SourceSnapshot, destination: Path) -> None:
    if _digest_file(source.archive) != source.archive_hash:
        raise ValueError("Snapshot archive hash mismatch")
    expected = {entry.path: entry for entry in source.entries}
    seen = set()
    _validate_names(expected)
    with tarfile.open(source.archive, "r:") as archive:
        for member in archive:
            if member.name not in expected or member.name in seen or not member.isreg():
                raise ValueError("Invalid archive member")
            entry = expected[member.name]
            if member.size != entry.size:
                raise ValueError("Archive member size mismatch")
            seen.add(member.name)
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = archive.extractfile(member)
            if content is None:
                raise ValueError("Missing regular archive payload")
            with content, target.open("xb") as sink:
                shutil.copyfileobj(content, sink, 1 << 20)
            if _digest_file(target) != entry.sha256:
                raise ValueError("Snapshot member hash mismatch")
            target.chmod(0o755 if entry.executable else 0o644)
    if seen != set(expected):
        raise ValueError("Snapshot archive is missing entries")


def capture_inputs(source: SourceSnapshot, declarations: tuple[str, ...]) -> Snapshot:
    project = source.project
    names = tuple(sorted(declarations, key=lambda n: n.encode("utf-8")))
    _validate_names(names)
    if project.layout in names:
        raise ValueError("Configured layout cannot also be a declared input")
    if len(names) > project.max_files:
        raise ValueError("Snapshot file limit exceeded")
    if any(
        any(fnmatch.fnmatchcase(name, pattern) for pattern in project.exclude)
        or set(PurePosixPath(name).parts) & _RESERVED
        for name in names
    ):
        raise ValueError("Declared input conflicts with bundle excludes")
    with tempfile.TemporaryDirectory(dir=project.root / ".simulatte", prefix="inputs-") as temporary:
        tree = Path(temporary)
        input_tree = tree / "input"
        source_tree = tree / "source"
        input_tree.mkdir()
        source_tree.mkdir()
        _extract(source, source_tree)
        # Require the captured source still matches working bytes before adding inputs.
        for entry in source.entries:
            if _copy(project.root, entry.path, None, project.max_bytes) != entry:
                raise SnapshotChanged("snapshot_changed before input capture")
        entries = []
        remaining = project.max_bytes
        for name in names:
            entry = _copy(project.root, name, input_tree / name, remaining)
            entries.append(entry)
            remaining -= entry.size
        for entry in entries:
            if _copy(project.root, entry.path, None, project.max_bytes) != entry:
                raise SnapshotChanged("snapshot_changed: inputs")
        kept = tuple(entry for entry in source.entries if entry.path not in names)
        if len(kept) + len(entries) > project.max_files:
            raise ValueError("Combined snapshot file limit exceeded")
        for entry in source.entries:
            if _copy(project.root, entry.path, None, project.max_bytes) != entry:
                raise SnapshotChanged("snapshot_changed during input capture")
        if project.model in names:
            raise ValueError("Selected model cannot be a data input")
        if sum(e.size for e in kept) + sum(e.size for e in entries) > project.max_bytes:
            raise ValueError("Combined snapshot byte limit exceeded")
        return Snapshot(
            _publish(project, kept, source_tree, "source"), _publish(project, tuple(entries), input_tree, "inputs")
        )


def materialize(snapshot: Snapshot, destination: Path) -> SnapshotPaths:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        _extract(snapshot.source, destination)
        _extract(snapshot.inputs, destination)
        for path in destination.rglob("*"):
            if path.is_file():
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        for path in sorted((p for p in destination.rglob("*") if p.is_dir()), reverse=True):
            path.chmod(0o555)
        destination.chmod(0o555)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return SnapshotPaths(destination, tuple(destination / name for name in snapshot.source.project.python_path))


def load_source(project: ProjectSpec, identity: str, *, kind: str = "source") -> SourceSnapshot:
    """Read a stored manifest and verify both its identity and archive bytes."""
    _hash(identity)
    if kind not in ("source", "inputs"):
        raise ValueError("Unknown snapshot kind")
    archive = project.root / ".simulatte" / "snapshots" / identity / f"{kind}.tar"
    try:
        with archive.with_suffix(".msgpack").open("rb") as stream:
            raw = stream.read((64 << 20) + 1)
        if len(raw) > 64 << 20:
            raise ValueError("Snapshot manifest exceeds limit")
        data: Any = unpack(raw)
        if not isinstance(data, Mapping) or set(data) != {"schema", "kind", "archive_hash", "entries"}:
            raise ValueError("Invalid snapshot manifest")
        if type(data["schema"]) is not int or data["schema"] != 1 or data["kind"] != kind:
            raise ValueError("Unsupported snapshot manifest")
        if not isinstance(data["entries"], (tuple, list)) or len(data["entries"]) > project.max_files:
            raise ValueError("Snapshot file limit exceeded")
        entries = tuple(Entry(**entry) for entry in data["entries"])
        _validate_names(entry.path for entry in entries)
        if sum(entry.size for entry in entries) > project.max_bytes:
            raise ValueError("Snapshot byte limit exceeded")
        if canonical_hash(f"{kind}-manifest-v1", [e.to_dict() for e in entries]) != identity:
            raise ValueError("Snapshot manifest hash mismatch")
        _hash(data["archive_hash"])
        if _digest_file(archive) != data["archive_hash"]:
            raise ValueError("Snapshot archive hash mismatch")
        return SourceSnapshot(project, entries, archive, identity, data["archive_hash"])
    except (OSError, TypeError, KeyError) as error:
        raise ValueError("Invalid or unavailable snapshot") from error


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    root: Path
    content_hash: str


def freeze_runtime(package: Path, project_root: Path) -> RuntimeSnapshot:
    """Freeze an editable trusted package without writing into its installation."""
    package = package.resolve()
    root = project_root.resolve()
    cache = root / ".simulatte"
    cache.mkdir(exist_ok=True)
    names = tuple(
        sorted(
            path.relative_to(package.parent).as_posix()
            for path in package.rglob("*")
            if not path.is_dir() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    )
    _validate_names(names)
    if len(names) > 100_000:
        raise ValueError("Runtime file limit exceeded")
    project = ProjectSpec(root, f"{package.name}/__init__.py", "layout.json", (), (), (".",), False)
    with tempfile.TemporaryDirectory(dir=cache, prefix="runtime-") as temporary:
        tree = Path(temporary)
        entries = []
        remaining = project.max_bytes
        for name in names:
            entry = _copy(package.parent, name, tree / name, remaining)
            entries.append(entry)
            remaining -= entry.size
        for entry in entries:
            if _copy(package.parent, entry.path, None, project.max_bytes) != entry:
                raise SnapshotChanged("Trusted runtime changed during capture")
        captured = _publish(project, tuple(entries), tree, "runtime")
    destination = cache / "tools" / captured.content_hash
    if not destination.exists():
        destination.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            staged = Path(temporary) / "tree"
            staged.mkdir()
            _extract(captured, staged)
            for path in staged.rglob("*"):
                if path.is_file():
                    path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
            os.replace(staged, destination)
    else:
        for entry in entries:
            if _copy(destination, entry.path, None, project.max_bytes) != entry:
                raise ValueError("Trusted runtime cache was modified")
    return RuntimeSnapshot(destination, captured.content_hash)
