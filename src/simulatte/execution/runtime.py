"""Data-only local runtime and distribution evidence; no dependency provisioning."""

from __future__ import annotations

import hashlib
from importlib import metadata
import platform
import re
import sys

from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import read_document
from simulatte.execution.schema import _hash


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
    snapshotted or rehashed every external dependency file.
    """
    expected = read_document(config["runtime_ref"], "runtime")
    actual = describe_runtime(config["distribution_roots"])
    if canonical_hash("runtime-evidence-v1", actual) != canonical_hash("runtime-evidence-v1", expected):
        raise ValueError("Installed runtime changed after preparation")
    if (
        set(requested) != {"facts", "dependencies_hash", "trusted_runtime_hash"}
        or dict(requested["facts"]) != actual["facts"]
        or requested["dependencies_hash"] != canonical_hash("dependencies-v1", actual["dependencies"])
    ):
        raise ValueError("Requested runtime differs from actual runtime")
    _hash(requested["trusted_runtime_hash"])
    return dict(requested), actual["dependencies"], tuple(actual["warnings"])
