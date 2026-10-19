"""Versioned installed payload identity for NEW managed environments only.

Legacy runtime.py deliberately keeps raw RECORD hashing. Here RECORD enumerates
actual bytes. Only installer-generated location selectors/cache timestamps are
normalized; the original raw probe is retained by EnvironmentPool separately.
"""

from __future__ import annotations

import csv
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import sys
from urllib.parse import quote, unquote, urlsplit

from simulatte.execution.hashing import canonical_hash
from simulatte.execution.runtime import describe_runtime

FORMAT = "installation-v1"
RUNTIME_KIND = "local-installation-v1"
CAPABILITY = "installation-runtime-v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def captured_artifacts(files) -> dict[str, str]:
    """Content identities for retained files and directory trees (project-relative)."""
    hashes = {name: digest(bytes.fromhex(data)) for name, data in files.items()}
    directories = {str(parent) for name in hashes for parent in PurePosixPath(name).parents if str(parent) != "."}
    return {
        **hashes,
        **{
            directory: canonical_hash(
                "captured-directory-v1",
                {
                    name[len(directory) + 1 :]: value
                    for name, value in hashes.items()
                    if name.startswith(directory + "/")
                },
            )
            for directory in directories
        },
    }


def bind_artifacts(files, source_root) -> dict[str, str]:
    """Bind exact file URLs before provisioning, from verified retained source bytes.

    The installation keeps these bindings after the temporary source root goes away.
    They are never inferred from an installed URL basename or suffix.
    """
    root = Path(source_root).resolve()
    for name, data in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.read_bytes() != bytes.fromhex(data):
            raise ValueError("Provisioning source differs from captured artifact")
    return {(root / name).as_uri(): value for name, value in captured_artifacts(files).items()}


def interpreter_selector(value: str, interpreter: str) -> str:
    """Keep console versus GUI behavior; normalize only the captured interpreter location."""
    windows = PureWindowsPath(interpreter)
    if windows.drive:
        actual = PureWindowsPath(value)
        if actual == windows:
            return "python"
        if actual == windows.with_name(windows.name.replace("python", "pythonw")):
            return "pythonw"
    elif value == interpreter:
        return "python"
    raise ValueError("Unsupported generated interpreter selector")


def script_identity(data: bytes, interpreter: str) -> dict:
    """uv/pip plain shebang or POSIX long-path trampoline, with the complete body."""
    lines = data.split(b"\n", 3)
    if lines[0] == b"#!/bin/sh" and len(lines) == 4 and lines[2] == b"' '''":
        line = lines[1].decode("utf8")
        prefix, suffix = "'''exec' ", ' "$0" "$@"'
        if not line.startswith(prefix) or not line.endswith(suffix):
            raise ValueError("Unsupported generated shell trampoline")
        command = shlex.split(line[len(prefix) : -len(suffix)])
        if len(command) != 1:
            raise ValueError("Unsupported generated shell trampoline")
        selector, body = command[0], lines[3]
    elif data.startswith(b"#!") and b"\n" in data:
        selector, body = data[2:].split(b"\n", 1)
        selector = selector.decode("utf8").strip('"')
    else:
        raise ValueError("Unsupported generated script format")
    return {"interpreter": interpreter_selector(selector, interpreter), "body": digest(body)}


def _direct_url(raw, bindings):
    value = json.loads(raw)
    url = urlsplit(value["url"])
    if url.scheme == "file":
        if url.netloc not in ("", "localhost") or url.query or url.fragment:
            raise ValueError("Unsupported local artifact URL")
        exact_url = url._replace(netloc="", path=quote(unquote(url.path), safe="/:")).geturl()
        if exact_url not in bindings:
            raise ValueError("Local artifact is not bound to verified captured source")
        value["url"] = "captured-sha256:" + bindings[exact_url]
    return value


def _cache(raw):
    value = json.loads(raw)
    timestamp = value.get("timestamp")
    if timestamp is not None:
        if (
            not isinstance(timestamp, dict)
            or set(timestamp) != {"secs_since_epoch", "nanos_since_epoch"}
            or any(type(v) is not int for v in timestamp.values())
        ):
            raise ValueError("Unsupported uv cache timestamp")
        value = {**value, "timestamp": None}
    return value


def describe_installation(roots, *, bindings, prefix=None, interpreter=None) -> dict:
    prefix = Path(sys.prefix if prefix is None else prefix).resolve()
    interpreter = sys.executable if interpreter is None else str(interpreter)
    packages = {}
    for distribution in metadata.distributions(path=roots):
        name = distribution.metadata["Name"]
        if not name:
            raise ValueError("Installed distribution has no name")
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in packages:
            raise ValueError("Ambiguous installed distribution: " + name)
        record = distribution.read_text("RECORD")
        if record is None:
            raise ValueError("Installation identity requires RECORD: " + name)
        root = Path(str(distribution.locate_file(""))).resolve()
        files = {}
        seen = set()
        for row in csv.reader(io.StringIO(record)):
            if len(row) != 3 or not row[0]:
                raise ValueError("Malformed installed RECORD")
            relative, recorded_hash, _ = row
            if PurePosixPath(relative).is_absolute() or "\\" in relative:
                raise ValueError("Installed RECORD path must be relative and canonical")
            path = Path(str(distribution.locate_file(relative))).resolve()
            if not path.is_relative_to(prefix):
                raise ValueError("Installed payload escapes environment")
            if path.is_relative_to(root):
                key = path.relative_to(root).as_posix()
            else:
                key = "@environment/" + path.relative_to(prefix).as_posix()
            if key in seen:
                raise ValueError("Duplicate installed payload path")
            seen.add(key)
            info = path.parent.name.endswith(".dist-info")
            if info and path.name == "RECORD":
                files[key] = {"generated": "RECORD-manifest"}
                continue
            if path.suffix == ".pyc" and "__pycache__" in path.parts and not recorded_hash:
                continue
            raw = path.read_bytes()
            if info and path.name == "direct_url.json":
                content = {"direct_url": _direct_url(raw, bindings)}
            elif info and path.name == "uv_cache.json":
                content = {"uv_cache": _cache(raw)}
            elif key.startswith(("@environment/bin/", "@environment/Scripts/")):
                if raw.startswith(b"MZ"):
                    from simulatte.execution.launchers import windows_identity

                    content = windows_identity(raw, interpreter)
                elif raw.startswith(b"#!"):
                    content = {"script": script_identity(raw, interpreter)}
                else:
                    content = {"sha256": digest(raw)}
            else:
                content = {"sha256": digest(raw)}
            files[key] = content
        packages[name] = {"version": distribution.version, "files": dict(sorted(files.items()))}
    return {
        "format": FORMAT,
        "facts": describe_runtime(roots)["facts"],
        "dependencies": {"packages": dict(sorted(packages.items()))},
        "warnings": [],
    }


def verify_installation(config, requested, expected):
    from simulatte.execution.runtime import RuntimeChanged
    from simulatte.execution.schema import _hash, _plain

    if (
        requested.get("kind") != RUNTIME_KIND
        or expected.get("format") != FORMAT
        or expected.get("identity", {}).get("format") != FORMAT
    ):
        raise ValueError("Runtime request/evidence format mismatch")
    arguments = {"bindings": config.get("installation_bindings", expected["bindings"])}
    if "installation_prefix" in config:
        arguments["prefix"] = config["installation_prefix"]
    actual = describe_installation(config["distribution_roots"], **arguments)
    if canonical_hash("installation-evidence-v1", actual) != canonical_hash(
        "installation-evidence-v1", expected["identity"]
    ):
        raise RuntimeChanged("Installed payload changed after preparation")
    if (
        set(requested) != {"kind", "facts", "dependencies_hash", "trusted_runtime_hash"}
        or _plain(requested["facts"]) != actual["facts"]
        or requested["dependencies_hash"] != canonical_hash("installation-dependencies-v1", actual["dependencies"])
        or requested["trusted_runtime_hash"] != config.get("trusted_runtime_hash")
    ):
        raise ValueError("Requested runtime differs from actual installation")
    _hash(requested["trusted_runtime_hash"])
    return _plain(requested), actual["dependencies"], tuple(actual["warnings"])
