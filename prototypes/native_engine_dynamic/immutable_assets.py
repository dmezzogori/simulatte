"""Research-only verified binary pair store; never caches simulation state.

The store is fixture-local. Native loading is still a trusted operation, not a
security boundary against a malicious process with the same user's permissions.
"""

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import sysconfig
import tempfile
import time

STORE_ENV = "SIMULATTE_NATIVE_ASSET_STORE"
DIAGNOSTIC_ENV = "SIMULATTE_NATIVE_ASSET_DIAGNOSTIC"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def descriptor(extension):
    extension = Path(extension)
    root = extension.parents[3]
    manifest = json.loads((root / "native-assets.json").read_text())
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("invalid native asset manifest")
    expected = "_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX")
    if extension.name != expected:
        raise ValueError("native extension ABI/name mismatch")
    library = extension.with_name("dynamic_engine.dylib" if sys.platform == "darwin" else "dynamic_engine.so")
    files = {}
    for path in (extension, library):
        value = manifest["files"].get(path.relative_to(root).as_posix())
        if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
            raise ValueError("native binary pair missing from manifest")
        files[path.name] = value
    return dict(schema=1, implementation=sys.implementation.name,
                abi=sysconfig.get_config_var("SOABI"), platform=sys.platform, files=files)


def address(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _regular(path, *, readonly):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (readonly and info.st_mode & 0o222):
        raise ValueError("native asset must be a regular read-only file" if readonly else "invalid native asset file")
    return info


def verify_pair(directory, document, *, readonly):
    directory = Path(directory)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or (readonly and info.st_mode & 0o222):
        raise ValueError("invalid immutable native asset directory")
    if directory != directory.resolve():
        raise ValueError("native asset directory contains a symlink")
    identities = {}
    for name, expected in document["files"].items():
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("invalid native binary name")
        path = directory / name
        before = _regular(path, readonly=readonly)
        if _hash(path) != expected or _identity(path.lstat()) != _identity(before):
            raise ValueError("native asset integrity check failed")
        identities[name] = _identity(before)
    return identities


def verify_entry(entry, document):
    entry = Path(entry)
    if entry.name != address(document):
        raise ValueError("immutable native asset address mismatch")
    marker = entry / "manifest.json"
    _regular(marker, readonly=True)
    if json.loads(marker.read_text()) != document:
        raise ValueError("immutable native asset manifest mismatch")
    if {p.name for p in entry.iterdir()} != {*document["files"], "manifest.json"}:
        raise ValueError("unexpected immutable native asset file")
    return verify_pair(entry, document, readonly=True)


def publish(extension, store):
    extension, store = Path(extension).absolute(), Path(store).resolve()
    document = descriptor(extension)
    verify_pair(extension.parent, document, readonly=False)
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = store / address(document)
    if destination.exists() or destination.is_symlink():
        verify_entry(destination, document)
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".publish-", dir=store))
    try:
        for name in document["files"]:
            with (extension.parent / name).open("rb") as source, (temporary / name).open("xb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            (temporary / name).chmod(0o444)
        marker = temporary / "manifest.json"
        with marker.open("x") as output:
            json.dump(document, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        marker.chmod(0o444)
        temporary.chmod(0o555)
        verify_pair(temporary, document, readonly=True)
        try:
            temporary.rename(destination)
        except FileExistsError:
            verify_entry(destination, document)
        verify_entry(destination, document)
        return destination
    finally:
        if temporary.exists():
            temporary.chmod(0o700)
            shutil.rmtree(temporary)


def _import_extension(path):
    spec = importlib.util.spec_from_file_location("_nd_scalar", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load verified native extension")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def engine_images():
    """Inspect actual mapped engine paths on the experiment's local platform."""
    if sys.platform == "darwin":
        loader = ctypes.CDLL(None)
        count, name = loader._dyld_image_count, loader._dyld_get_image_name
        count.restype, count.argtypes = ctypes.c_uint32, []
        name.restype, name.argtypes = ctypes.c_char_p, [ctypes.c_uint32]
        images = [os.fsdecode(name(index)) for index in range(count())]
    elif sys.platform.startswith("linux"):
        images = [line.split(maxsplit=5)[-1] for line in Path("/proc/self/maps").read_text().splitlines()
                  if len(line.split(maxsplit=5)) == 6]
    else:
        raise RuntimeError("native image inspection is unsupported on this experiment platform")
    return sorted({str(Path(path).resolve()) for path in images
                   if Path(path).name in ("dynamic_engine.dylib", "dynamic_engine.so")})


def load_verified(extension):
    started = time.perf_counter_ns()
    extension = Path(extension).absolute()
    document = descriptor(extension)
    source_identity = verify_pair(extension.parent, document, readonly=False)
    store = os.environ.get(STORE_ENV)
    selected = extension.parent if store is None else Path(store) / address(document)
    selected_identity = (verify_pair(selected, document, readonly=False) if store is None
                         else verify_entry(selected, document))
    verified = time.perf_counter_ns()
    module = _import_extension(selected / extension.name)
    imported = time.perf_counter_ns()
    if Path(module.__file__).resolve() != selected / extension.name:
        raise ValueError("unexpected loaded extension path")
    images = engine_images()
    expected_image = str(selected / next(name for name in document["files"] if name != extension.name))
    if images != [expected_image]:
        raise ValueError("unexpected loaded native engine image")
    actual = (verify_pair(selected, document, readonly=False) if store is None
              else verify_entry(selected, document))
    if actual != selected_identity or verify_pair(extension.parent, document, readonly=False) != source_identity:
        raise ValueError("native asset changed during loading")
    checked = time.perf_counter_ns()
    evidence = dict(schema=1, pid=os.getpid(), mode="attempt" if store is None else "immutable",
                    address=address(document), files=document["files"], source=str(extension.parent),
                    selected=str(selected), extension=str(Path(module.__file__).resolve()), images=images,
                    identities={name: list(value) for name, value in selected_identity.items()},
                    verification_s=(verified - started) / 1e9, import_s=(imported - verified) / 1e9,
                    postcheck_s=(checked - imported) / 1e9, total_s=(checked - started) / 1e9)
    diagnostic = os.environ.get(DIAGNOSTIC_ENV)
    if diagnostic is not None:
        with Path(diagnostic).open("x") as output:
            json.dump(evidence, output, separators=(",", ":"))
            output.write("\n")
    return module
