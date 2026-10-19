"""Data-only local runtime and distribution evidence; no dependency provisioning."""

from __future__ import annotations

import hashlib
from importlib import metadata
import platform
import re
import sys

from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import read_document
from simulatte.execution.schema import _hash, _plain


class RuntimeChanged(ValueError):
    """The child's installed runtime no longer matches its preparation evidence (attempt ``failed``)."""

    code = "runtime_changed"


def provisioning_spec_hash(spec) -> str:
    """The remote environment key: the uv provisioning spec minus ``trusted_runtime_hash`` (D47, D101)."""
    return canonical_hash(
        "provisioning-spec-v1", {name: value for name, value in _plain(spec).items() if name != "trusted_runtime_hash"}
    )


def _python_matches(requested: str, actual: str) -> bool:
    """``3.14`` and ``3.14.1`` both match ``3.14.1``; ``3.1`` does not."""
    parts = requested.split(".")
    return actual.split(".")[: len(parts)] == parts


def describe_runtime(roots) -> dict:
    packages = {}
    warnings = []
    for distribution in metadata.distributions(path=roots):
        name = distribution.metadata["Name"]
        if not name:
            raise ValueError("Installed distribution has no name")
        name = re.sub(r"[-_.]+", "-", name).lower()
        record = distribution.read_text("RECORD")
        item = {
            "version": distribution.version,
            "record_hash": None if record is None else hashlib.sha256(record.encode("utf-8")).hexdigest(),
        }
        if name in packages and packages[name] != item:
            raise ValueError("Ambiguous installed distribution: " + name)
        packages[name] = item
        if record is None:
            warnings.append("distribution_record_unavailable:" + name)
    facts = {
        "implementation": sys.implementation.name,
        "python": platform.python_version(),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "simulatte": packages.get("simulatte", {}).get("version", "unavailable"),
    }
    return {
        "facts": facts,
        "dependencies": {"packages": dict(sorted(packages.items()))},
        "warnings": sorted(set(warnings)),
    }


def verify_runtime(config, requested):
    """Compare actual installed metadata with the captured preflight evidence.

    RECORD hashes identify distribution manifests; they are not a claim to have
    snapshotted or rehashed every external dependency file. For a ``uv`` request the
    evidence is the provisioned environment's own, and the frozen runtime hash and the
    requested Python (``X.Y`` matches any patch, ``X.Y.Z`` only itself) are checked too; the
    environment evidence itself records the resolved version. The resolved runtime carries the spec hash
    with the actual facts, so replay verification and ``mixed_runtime`` compare them.
    """
    expected = read_document(config["runtime_ref"], "runtime")
    if requested.get("kind") == "local-installation-v1" or "format" in expected:
        from simulatte.execution.installation import verify_installation

        return verify_installation(config, requested, expected)
    actual = describe_runtime(config["distribution_roots"])
    if canonical_hash("runtime-evidence-v1", actual) != canonical_hash("runtime-evidence-v1", expected):
        raise RuntimeChanged("Installed runtime changed after preparation")
    dependencies_hash = canonical_hash("dependencies-v1", actual["dependencies"])
    if requested.get("kind") == "uv":
        _hash(requested["trusted_runtime_hash"])
        if requested["trusted_runtime_hash"] != config.get("trusted_runtime_hash") or not _python_matches(
            requested["python"], platform.python_version()
        ):
            raise RuntimeChanged("Installed runtime changed after preparation")
        resolved = {
            "kind": "uv",
            "spec_hash": provisioning_spec_hash(requested),
            "python": requested["python"],
            "facts": actual["facts"],
            "dependencies_hash": dependencies_hash,
            "trusted_runtime_hash": requested["trusted_runtime_hash"],
        }
        return resolved, actual["dependencies"], tuple(actual["warnings"])
    if (
        set(requested) != {"facts", "dependencies_hash", "trusted_runtime_hash"}
        or dict(requested["facts"]) != actual["facts"]
        or requested["dependencies_hash"] != dependencies_hash
    ):
        raise ValueError("Requested runtime differs from actual runtime")
    _hash(requested["trusted_runtime_hash"])
    return dict(requested), actual["dependencies"], tuple(actual["warnings"])
