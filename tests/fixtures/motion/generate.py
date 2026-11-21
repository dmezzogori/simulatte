"""Shared fixture for the ``piecewise`` motion curve (spec §11.3, §13).

``python tests/fixtures/motion/generate.py [--out FILE]`` writes ``piecewise.json``: a list of cases
``{"name", "t_start", "t_end", "description", "samples"}`` where ``samples`` is a list of ``[time, fraction]``
pairs, or ``null`` for a description that is invalid and must fall back to linear. Valid cases come from
profiles built by :func:`arc_profile` (the four-phase case is built by hand, since one arc profile has at most
three phases); the sample fractions are computed from :meth:`ArcProfile.position`, independently of the
evaluators. Both ``motion_fraction`` (Python) and ``motionProgress`` (TypeScript) are tested against the file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from simulatte.intralogistics.kinematics import ArcProfile, Kinematics, arc_profile

PATH = Path(__file__).resolve().parent / "piecewise.json"
KIN = Kinematics(v_max=1.5, accel=0.8, decel=0.6)
STEPS = 16


def _valid(name: str, profile: ArcProfile, t_start: float) -> dict[str, Any]:
    duration = profile.duration
    elapsed = [duration * k / STEPS for k in range(STEPS + 1)]
    return {
        "name": name,
        "t_start": t_start,
        "t_end": t_start + duration,
        "description": profile.description(),
        "samples": [[t_start + e, profile.position(e) / profile.length] for e in elapsed],
    }


def _invalid(name: str, t_end: float, description: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "t_start": 0.0, "t_end": t_end, "description": description, "samples": None}


def cases() -> list[dict[str, Any]]:
    four = ArcProfile(0.0, ((1.0, 0.5), (1.0, 0.0), (1.0, -0.25), (1.0, -0.25)), 1.25, 0.0)
    out = [
        _valid("one phase", arc_profile(0.0, 1.0, 1.5, 0.625, KIN), 0.0),
        _valid("two phases", arc_profile(0.0, 0.0, 1.5, 1.0, KIN), 0.0),
        _valid("three phases", arc_profile(0.0, 0.0, 1.5, 10.0, KIN), 0.0),
        _valid("four phases", four, 0.0),
        _valid("nonzero entry speed", arc_profile(0.6, 0.3, 1.0, 3.0, KIN), 100.0),
        _valid("zero acceleration only", arc_profile(1.0, 1.0, 1.0, 5.0, KIN), 7.5),
    ]
    good: dict[str, Any] = {"curve": "piecewise", "v0": 0.0, "phases": [[2.0, 0.5], [1.0, -1.0]], "distance": 1.5}
    out += [
        _invalid("duration mismatch", 3.5, good),
        _invalid(
            "negative speed",
            2.0,
            {"curve": "piecewise", "v0": 0.5, "phases": [[1.0, 0.0], [1.0, -1.0]], "distance": 0.5},
        ),
        _invalid("distance mismatch", 3.0, {**good, "distance": 1.6}),
        _invalid("empty phase list", 3.0, {**good, "phases": []}),
        _invalid("zero duration", 3.0, {**good, "phases": [[2.0, 0.5], [0.0, 0.0], [1.0, -1.0]]}),
        _invalid("negative duration", 3.0, {**good, "phases": [[4.0, 0.5], [-1.0, -1.0]]}),
    ]
    return out


def render() -> str:
    return json.dumps(cases(), indent=1) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=PATH)
    args = parser.parse_args()
    args.out.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
