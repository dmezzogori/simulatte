"""Verified installed native images. Public model API: simulatte.experimental.native."""

import base64
import hashlib
from importlib import metadata, util
import json
from pathlib import Path
import platform
import struct
import sys
import sysconfig
from threading import RLock

from simulatte.execution.native_assets import _checked, _images

_lock = RLock()
_loaded = None
_pin = None


def _abi():
    return dict(
        implementation=sys.implementation.name,
        soabi=sysconfig.get_config_var("SOABI"),
        suffix=sysconfig.get_config_var("EXT_SUFFIX"),
        platform=sys.platform,
        machine=platform.machine(),
        bits=struct.calcsize("P") * 8,
        python=list(sys.version_info[:2]),
        free_threaded=bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    )


def _read():
    directory = (Path(__file__).parent / "_bin").resolve()
    marker = directory / "manifest.json"
    try:
        dist = metadata.distribution("simulatte-native")
        entry = next(file for file in dist.files or () if str(file) == "simulatte_native/_bin/manifest.json")
    except (metadata.PackageNotFoundError, StopIteration) as error:
        raise ImportError("Install a built simulatte-native wheel; editable native installs are unsupported") from error
    if Path(dist.locate_file(entry)).resolve() != marker or entry.hash is None or entry.hash.mode != "sha256":
        raise ImportError("Native manifest does not belong to the installed wheel RECORD")
    raw = marker.read_bytes()
    expected = base64.urlsafe_b64decode(entry.hash.value + "=" * (-len(entry.hash.value) % 4)).hex()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ImportError("Native manifest differs from the installed wheel RECORD")
    _checked(marker, expected, len(raw), readonly=False)
    value = json.loads(raw)
    if (
        set(value) != {"schema", "provider_api", "version", "abi", "sources", "files"}
        or value["schema"] != 1
        or value["provider_api"] != 1
    ):
        raise ImportError("Unsupported native provider API/manifest")
    if value["version"] != dist.version or value["version"] != metadata.version("simulatte"):
        raise ImportError("Install matching simulatte and simulatte-native versions")
    runtime = _abi()
    if (
        runtime["implementation"] != "cpython"
        or runtime["platform"] not in ("darwin", "linux")
        or runtime["free_threaded"]
        or value["abi"] != runtime
    ):
        raise ImportError("Native wheel ABI mismatch: install a wheel built for this CPython/platform")
    extension = "_nd_scalar" + runtime["suffix"]
    library = "simulatte_native_engine.dylib" if sys.platform == "darwin" else "simulatte_native_engine.so"
    if set(value["files"]) != {extension, library}:
        raise ImportError("Unexpected native wheel binary members")
    return directory, raw, value, extension, library


def _verify():
    global _pin
    directory, raw, value, extension, library = _read()
    pin = (str(directory), raw)
    if _pin is not None and pin != _pin:
        raise ImportError("Installed native manifest changed after loading")
    identities = {
        name: _checked(directory / name, file["sha256"], file["size"], readonly=False)
        for name, file in value["files"].items()
    }
    if _loaded is not None:
        if Path(_loaded.__file__).resolve() != directory / extension:
            raise ImportError("Native extension loaded from an unexpected origin")
        if _images(library) != {directory / library}:
            raise ImportError("Native engine dependency loaded from an unexpected origin")
    _pin = pin
    return directory, value, extension, library, identities


def load():
    """Verify before/after loading; cache only a module, never simulation state."""
    global _loaded
    with _lock:
        directory, _, extension, library, identities = _verify()
        if _loaded is None:
            name = "simulatte_native._nd_scalar"
            spec = util.spec_from_file_location(name, directory / extension)
            if spec is None or spec.loader is None:
                raise ImportError("Unable to load native wheel extension")
            module = util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if Path(module.__file__).resolve() != directory / extension or _images(library) != {directory / library}:
                raise ImportError("Native images loaded from an unexpected origin")
            _loaded = module
            sys.modules[name] = module
        if _verify()[-1] != identities:
            raise ImportError("Native images changed during loading")
        return _loaded


def verify():
    with _lock:
        _verify()


def info():
    """Detached deterministic build/ABI evidence; contains no installation paths."""
    with _lock:
        _, value, _, _, _ = _verify()
        return {key: value[key] for key in ("schema", "provider_api", "version", "abi", "sources", "files")}
