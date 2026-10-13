"""Verification of a full-level replay against the original result (design 6, D31, D100).

A replay re-executes a committed request at ``full`` level. It is ``verified`` only
when its trace verifies and it reproduces the original's semantic digest, every KPI
scalar both fingerprints report, and the original's resolved runtime facts,
dependencies and physical layout. Otherwise it is ``unverified`` with the first
differing field as the reason; the replay stays published either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulatte._wire import canonical_pack, freeze
from simulatte.execution.publication import PublishedResult
from simulatte.execution.schema import _plain


@dataclass(frozen=True, slots=True)
class Verification:
    verified: bool
    reason: str | None  # ``None`` when verified, else the first failing check.


def _same(a: Any, b: Any) -> bool:
    # Canonical bytes: NaN equals NaN and nested mappings compare by content.
    return canonical_pack(freeze(_plain(a))) == canonical_pack(freeze(_plain(b)))


def verify_replay(original: PublishedResult, replay: PublishedResult, replay_trace_verified) -> Verification:
    """Compare in a fixed order and report the first failing check.

    ``replay_trace_verified`` is the replay trace's ``Trace.verify()`` result; anything
    but ``True`` (including ``"not_verifiable"``) is ``trace_unverified``. KPI reasons
    name the first differing key in sorted order as ``kpi_mismatch:<key>``.
    """
    if replay_trace_verified is not True:
        return Verification(False, "trace_unverified")
    if original.fingerprint["digest"] != replay.fingerprint["digest"]:
        return Verification(False, "digest_mismatch")
    before, after = original.fingerprint["kpis"], replay.fingerprint["kpis"]
    for name in sorted(set(before) & set(after)):
        if not _same(before[name], after[name]):
            return Verification(False, f"kpi_mismatch:{name}")
    old, new = original.manifest["resolved"], replay.manifest["resolved"]
    if not _same(old["runtime"]["facts"], new["runtime"]["facts"]):
        return Verification(False, "runtime_mismatch")
    if not _same(old["runtime"]["dependencies_hash"], new["runtime"]["dependencies_hash"]) or not _same(
        old["dependencies"], new["dependencies"]
    ):
        return Verification(False, "dependencies_mismatch")
    if not _same(old["layout"].get("physical_hash"), new["layout"].get("physical_hash")):
        return Verification(False, "layout_mismatch")
    return Verification(True, None)
