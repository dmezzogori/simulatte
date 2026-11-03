"""Declared, captured native binaries and coordinator-owned derived storage.

The RunStore's OS owner lock fences other processes. This manager serializes slot
threads and leases entries until execution/descendant cleanup has finished. Native
code remains trusted; this is byte/provenance enforcement, not a security sandbox.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import sysconfig
import tempfile
import threading
from types import MappingProxyType
from typing import Any, cast

from simulatte._wire import freeze
from simulatte.execution.durability import sync_directory
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import _identity, read_document
from simulatte.execution.schema import _hash, _plain
from simulatte.execution.snapshot import _path

_ADDRESS = re.compile("[0-9a-f]{64}")
_BINARY = re.compile(r".+(?:\.so(?:\.[0-9]+)*|\.dylib|\.dll|\.pyd)$")


@dataclass(frozen=True, slots=True)
class NativeAssetBundle:
    """A named set of exact project-relative native files, kept in source capture."""

    name: str
    files: tuple[str, ...]

    def __post_init__(self):
        if type(self.name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", self.name) is None:
            raise ValueError("Invalid native bundle name")
        if type(self.files) is not tuple or not 1 <= len(self.files) <= 32:
            raise ValueError("Native bundle requires 1–32 file paths")
        names = set()
        for path in self.files:
            _path(path)
            if len(path.encode()) > 1024 or not _BINARY.fullmatch(PurePosixPath(path).name):
                raise ValueError("Native bundle members must be native binary files")
            name = PurePosixPath(path).name
            if name in names:
                raise ValueError("Native bundle file basenames must be unique")
            names.add(name)

    def to_dict(self):
        return {"name": self.name, "files": list(self.files)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"name", "files"}:
            raise ValueError("Invalid native bundle declaration")
        if not isinstance(value["files"], (list, tuple)):
            raise ValueError("Invalid native bundle files")
        return cls(value["name"], tuple(value["files"]))


def declarations(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Native assets require a sequence of bundles")
    try:
        bundles = tuple(values)
    except TypeError as error:
        raise ValueError("Native assets require a sequence of bundles") from error
    if len(bundles) > 16 or any(type(bundle) is not NativeAssetBundle for bundle in bundles):
        raise ValueError("Native assets require at most 16 NativeAssetBundle declarations")
    if len({bundle.name for bundle in bundles}) != len(bundles):
        raise ValueError("Duplicate native bundle name")
    members = [path for bundle in bundles for path in bundle.files]
    if len(set(members)) != len(members):
        raise ValueError("Native files cannot belong to multiple bundles")
    return bundles


def abi():
    return {"implementation": sys.implementation.name, "abi": sysconfig.get_config_var("SOABI"),
            "platform": sys.platform, "cache_tag": sys.implementation.cache_tag}


def _supported():
    if os.name != "posix":
        raise ValueError("Declared native assets currently require local POSIX execution")


def _payload(bundle, runtime_abi):
    return {"schema": 1, "abi": _plain(runtime_abi),
            "files": [{k: file[k] for k in ("name", "sha256", "size")}
                      for file in sorted(bundle["files"], key=lambda f: f["name"])]}


def _address(bundle, runtime_abi):
    return canonical_hash("native-asset-bundle-v1", _payload(bundle, runtime_abi))


def capture_manifest(source, bundles):
    _supported()
    entries = {entry.path: entry for entry in source.entries}
    captured = []
    runtime_abi = abi()
    for bundle in declarations(bundles):
        files = []
        for path in bundle.files:
            if path not in entries or entries[path].link is not None:
                raise ValueError("Declared native asset must be a captured regular source file: " + path)
            entry = entries[path]
            files.append(dict(source=path, name=PurePosixPath(path).name, sha256=entry.sha256, size=entry.size))
        item = dict(name=bundle.name, files=files)
        item["address"] = _address(item, runtime_abi)
        captured.append(item)
    return dict(schema=1, source_hash=source.content_hash, abi=runtime_abi, bundles=captured)


def validate_manifest(value, source_hash=None):
    _supported()
    if not isinstance(value, Mapping) or set(value) != {"schema", "source_hash", "abi", "bundles"}:
        raise ValueError("Invalid native asset manifest")
    if type(value["schema"]) is not int or value["schema"] != 1 or value["abi"] != abi():
        raise ValueError("Native asset schema/interpreter ABI mismatch")
    _hash(value["source_hash"])
    if source_hash is not None and value["source_hash"] != source_hash:
        raise ValueError("Native asset source identity differs from request")
    if not isinstance(value["bundles"], (list, tuple)):
        raise ValueError("Invalid native bundle list")
    declared = []
    for bundle in value["bundles"]:
        if not isinstance(bundle, Mapping) or set(bundle) != {"name", "address", "files"}:
            raise ValueError("Invalid captured native bundle")
        if not isinstance(bundle["files"], (list, tuple)):
            raise ValueError("Invalid captured native files")
        paths = []
        for file in bundle["files"]:
            if not isinstance(file, Mapping) or set(file) != {"source", "name", "sha256", "size"}:
                raise ValueError("Invalid captured native file")
            _hash(file["sha256"])
            _path(file["source"])
            if type(file["size"]) is not int or not 0 <= file["size"] <= 1 << 30:
                raise ValueError("Invalid native file size")
            paths.append(file["source"])
            if file["name"] != PurePosixPath(file["source"]).name:
                raise ValueError("Native filename differs from declaration")
        declared.append(NativeAssetBundle(bundle["name"], tuple(paths)))
        if bundle["address"] != _address(bundle, value["abi"]):
            raise ValueError("Native asset content address mismatch")
    return declarations(declared)


def _checked(path, sha256, size, *, readonly):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.resolve() != path:
        raise ValueError("Native asset must be a regular file without symlink components")
    if readonly and before.st_mode & 0o222:
        raise ValueError("Native asset must be read-only")
    if before.st_size != size:
        raise ValueError("Native asset length differs from captured source")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        opened = os.fstat(file.fileno())
        if _identity(opened) != _identity(before):
            raise ValueError("Native asset changed before read")
        while data := file.read(1 << 20):
            digest.update(data)
        after = os.fstat(file.fileno())
        if _identity(after) != _identity(before) or after.st_ctime_ns != opened.st_ctime_ns:
            raise ValueError("Native asset changed while reading")
    if _identity(path.lstat()) != _identity(before) or digest.hexdigest() != sha256:
        raise ValueError("Native asset content hash/identity mismatch")
    return _identity(before)


def _marker(bundle, runtime_abi):
    return (json.dumps(_payload(bundle, runtime_abi), sort_keys=True, separators=(",", ":")) + "\n").encode()


def _verify_entry(directory, bundle, runtime_abi):
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o222 or directory.resolve() != directory
            or directory.name != bundle["address"]):
        raise ValueError("Invalid immutable native asset directory")
    names = {file["name"] for file in bundle["files"]}
    if {path.name for path in directory.iterdir()} != {*names, "manifest.json"}:
        raise ValueError("Unexpected or missing native asset member")
    marker = _marker(bundle, runtime_abi)
    _checked(directory / "manifest.json", hashlib.sha256(marker).hexdigest(), len(marker), readonly=True)
    return {file["name"]: _checked(directory / file["name"], file["sha256"], file["size"], readonly=True)
            for file in bundle["files"]}


def _verify_source(root, bundle):
    return {file["source"]: _checked(root / file["source"], file["sha256"], file["size"], readonly=False)
            for file in bundle["files"]}


def _images(basename):
    if sys.platform == "darwin":
        library = ctypes.CDLL(None)
        count, name = library._dyld_image_count, library._dyld_get_image_name
        count.restype, count.argtypes = ctypes.c_uint32, []
        name.restype, name.argtypes = ctypes.c_char_p, [ctypes.c_uint32]
        paths = [os.fsdecode(name(index)) for index in range(count())]
    elif sys.platform.startswith("linux"):
        paths = [line.split(maxsplit=5)[-1] for line in Path("/proc/self/maps").read_text().splitlines()
                 if len(line.split(maxsplit=5)) == 6]
    else:
        raise ValueError("Native dependency image checks require macOS or Linux")
    return {Path(path).resolve() for path in paths if Path(path).name == basename}


@dataclass(frozen=True, slots=True)
class VerifiedNativeBundle:
    """Execution-scoped verified paths; never contains simulation state."""

    name: str
    directory: Path
    source_root: Path
    bundle: Mapping[str, Any]
    runtime_abi: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "bundle", freeze(self.bundle))
        object.__setattr__(self, "runtime_abi", freeze(self.runtime_abi))

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(file["name"] for file in self.bundle["files"])

    @property
    def address(self) -> str:
        return self.bundle["address"]

    def verify(self):
        if self.runtime_abi != abi():
            raise ValueError("Native asset interpreter ABI changed")
        return _verify_source(self.source_root, self.bundle), _verify_entry(
            self.directory, self.bundle, self.runtime_abi)

    def path(self, name: str) -> Path:
        if name not in self.files:
            raise KeyError(name)
        self.verify()
        return self.directory / name

    def load_extension(self, module_name: str, filename: str, *, dependencies: tuple[str, ...] = ()):
        if type(module_name) is not str or not all(part.isidentifier() for part in module_name.split(".")):
            raise ValueError("Invalid native module name")
        before = self.verify()
        names = {file["name"] for file in self.bundle["files"]}
        for name in (filename, *dependencies):
            if name not in names:
                raise KeyError(name)
        path = self.directory / filename
        expected = {name: self.directory / name for name in dependencies}
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("Unable to load declared native extension")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if type(module.__file__) is not str or Path(module.__file__).resolve() != path:
            raise ValueError("Native extension loaded from an unexpected origin")
        for name, location in expected.items():
            if _images(name) != {location}:
                raise ValueError("Native dependency loaded from an unexpected origin")
        if self.verify() != before:
            raise ValueError("Native assets changed during loading")
        return module


class NativeAssetLease:
    def __init__(self, owner, manifest, directories):
        self.owner = owner
        self.manifest = cast(Mapping[str, Any], freeze(manifest))
        self.directories = dict(directories)
        self.closed = False

    def bindings(self):
        return dict(schema=1, source_hash=self.manifest["source_hash"], directories=self.directories)

    def close(self):
        with self.owner.lock:
            if not self.closed:
                self.closed = True
                for bundle in self.manifest["bundles"]:
                    address = bundle["address"]
                    self.owner.leases[address] -= 1
                    if not self.owner.leases[address]:
                        del self.owner.leases[address]


class NativeAssetStore:
    """Owned by one fenced RunStore; safe for its concurrent local slot threads."""

    def __init__(self, root):
        self.root = Path(root).absolute()
        self.lock = threading.RLock()
        self.leases = Counter()

    def acquire(self, source_root, manifest):
        validate_manifest(manifest)
        source_root = Path(source_root).resolve()
        with self.lock:
            if self.root.is_symlink():
                raise ValueError("Native asset store cannot be a symlink")
            self.root.mkdir(mode=0o700, exist_ok=True)
            self.root = self.root.resolve()
            sync_directory(self.root.parent)
            directories = {}
            for bundle in manifest["bundles"]:
                _verify_source(source_root, bundle)
                destination = self.root / bundle["address"]
                if not destination.exists() and not destination.is_symlink():
                    self._publish(source_root, destination, bundle, manifest["abi"])
                _verify_entry(destination, bundle, manifest["abi"])
                directories[bundle["name"]] = str(destination)
            for bundle in manifest["bundles"]:
                self.leases[bundle["address"]] += 1
            return NativeAssetLease(self, manifest, directories)

    def _publish(self, source, destination, bundle, runtime_abi):
        temporary = Path(tempfile.mkdtemp(prefix=".publish-", dir=self.root))
        try:
            for file in bundle["files"]:
                path = temporary / file["name"]
                with (source / file["source"]).open("rb") as input_, path.open("xb") as output:
                    shutil.copyfileobj(input_, output, 1 << 20)
                    output.flush()
                    os.fsync(output.fileno())
                path.chmod(0o444)
                _checked(path, file["sha256"], file["size"], readonly=True)
            marker = temporary / "manifest.json"
            data = _marker(bundle, runtime_abi)
            with marker.open("xb") as output:
                if output.write(data) != len(data):
                    raise OSError("Incomplete native asset manifest write")
                output.flush()
                os.fsync(output.fileno())
            marker.chmod(0o444)
            _checked(marker, hashlib.sha256(data).hexdigest(), len(data), readonly=True)
            temporary.chmod(0o555)
            sync_directory(temporary)
            temporary.rename(destination)
            sync_directory(self.root)
        finally:
            if temporary.exists():
                temporary.chmod(0o700)
                shutil.rmtree(temporary)

    def prune(self, dry_run=False):
        with self.lock:
            if not self.root.exists():
                return []
            if self.root.is_symlink():
                raise ValueError("Native asset store cannot be a symlink")
            selected = sorted(path.name for path in self.root.iterdir()
                              if (path.name.startswith(".publish-") or _ADDRESS.fullmatch(path.name))
                              and not self.leases[path.name])
            if not dry_run:
                from simulatte.execution.store import _remove

                for name in selected:
                    _remove(self.root / name)
                sync_directory(self.root)
            return selected


def verify_for_execution(config, source_hash, expected, source_root):
    declarations_ = declarations(NativeAssetBundle.from_dict(item) for item in expected)
    has_manifest = "native_assets_ref" in config
    if not declarations_:
        if has_manifest or "native_bindings_ref" in config:
            raise ValueError("Undeclared native asset bindings")
        return MappingProxyType({})
    if not has_manifest or "native_bindings_ref" not in config:
        raise ValueError("Declared native assets require managed preparation")
    manifest = read_document(config["native_assets_ref"], "native-assets")
    if validate_manifest(manifest, source_hash) != declarations_:
        raise ValueError("Native asset manifest differs from model declaration")
    bound = read_document(config["native_bindings_ref"], "native-bindings")
    if (set(bound) != {"schema", "source_hash", "directories"} or type(bound["schema"]) is not int
            or bound["schema"] != 1 or bound["source_hash"] != source_hash
            or not isinstance(bound["directories"], Mapping)
            or set(bound["directories"]) != {bundle.name for bundle in declarations_}):
        raise ValueError("Invalid native bundle bindings")
    result = {}
    for bundle in manifest["bundles"]:
        directory = Path(bound["directories"][bundle["name"]])
        if not directory.is_absolute():
            raise ValueError("Native bundle directory must be absolute")
        handle = VerifiedNativeBundle(bundle["name"], directory, Path(source_root).resolve(),
                                      bundle, manifest["abi"])
        handle.verify()
        result[bundle["name"]] = handle
    return MappingProxyType(result)
