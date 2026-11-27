"""The frame fixtures of the Studio map (``studio/fixtures/map``) are what ``generate_map_frames.py`` writes.

The TypeScript tests and the render harness load these files; this test regenerates them into a temporary directory
and compares each with the committed one. The fixtures were written on one machine and this test runs on every
interpreter lane (PyPy included) and platform, where a float computed through the platform's math library (a heading
from ``atan2``, a position from ``cos``) may differ in its last digit. So the files are compared as parsed JSON: the
same keys, lists and types, every value other than a float exactly equal, floats to a relative tolerance of 1e-12.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tests" / "fixtures" / "traces" / "generate_map_frames.py"
COMMITTED = ROOT / "studio" / "fixtures" / "map"
REL_TOL = 1e-12
ABS_TOL = 1e-12
"""Floats agree within this relative tolerance, or this absolute one near zero (a ``cos`` of a right angle)."""


def differences(new: Any, old: Any, path: str = "$") -> list[str]:
    """Where `new` and `old` differ, as JSON paths; floats within the tolerance are equal."""
    if type(new) is not type(old):
        return [f"{path}: {type(new).__name__} {new!r} != {type(old).__name__} {old!r}"]
    if isinstance(new, dict):
        if new.keys() != old.keys():
            return [f"{path}: keys {sorted(new.keys() ^ old.keys())} differ"]
        return [d for key in new for d in differences(new[key], old[key], f"{path}.{key}")]
    if isinstance(new, list):
        if len(new) != len(old):
            return [f"{path}: length {len(new)} != {len(old)}"]
        return [d for i, (a, b) in enumerate(zip(new, old, strict=True)) for d in differences(a, b, f"{path}[{i}]")]
    if isinstance(new, float):
        return [] if math.isclose(new, old, rel_tol=REL_TOL, abs_tol=ABS_TOL) else [f"{path}: {new!r} != {old!r}"]
    return [] if new == old else [f"{path}: {new!r} != {old!r}"]


def test_differences_compare_floats_to_a_tolerance_and_everything_else_exactly() -> None:
    assert differences({"a": [1, 2.0, "x", None]}, {"a": [1, 2.0, "x", None]}) == []
    heading = -2.356194490192345
    assert differences({"a": math.nextafter(heading, 0.0)}, {"a": heading}) == []  # one ulp apart
    assert differences({"a": 1e-17}, {"a": 0.0}) == []
    assert differences({"a": 2.0}, {"a": 2.0001}) == ["$.a: 2.0 != 2.0001"]
    assert differences({"a": 1}, {"a": 1.0}) == ["$.a: int 1 != float 1.0"]
    assert differences({"a": True}, {"a": 1}) == ["$.a: bool True != int 1"]
    assert differences({"a": [1]}, {"a": [1, 2]}) == ["$.a: length 1 != 2"]
    assert differences({"a": 1, "b": 2}, {"a": 1, "c": 2}) == ["$: keys ['b', 'c'] differ"]
    assert differences({"a": "C"}, {"a": "D"}) == ["$.a: 'C' != 'D'"]


def test_map_frame_fixtures_up_to_date(tmp_path: Path) -> None:
    # A fresh interpreter, as for generate.py: the recordings carry the registered entity kinds, which other tests
    # extend.
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(tmp_path)], check=True, timeout=120, capture_output=True
    )
    committed = sorted(p.name for p in COMMITTED.glob("*.json"))
    assert committed, "no map frame fixtures are committed"
    assert sorted(p.name for p in tmp_path.glob("*.json")) == committed
    for name in committed:
        found = differences(
            json.loads((tmp_path / name).read_text(encoding="utf-8")),
            json.loads((COMMITTED / name).read_text(encoding="utf-8")),
        )
        assert not found, (name, found[:10])
