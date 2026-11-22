from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

from simulatte.intralogistics.kinematics import ArcProfile, Kinematics, arc_profile, plan_run, rest_to_rest
from simulatte.intralogistics.speed import motion_fraction

MOTION = Path(__file__).resolve().parents[1] / "fixtures" / "motion"
KIN = Kinematics(v_max=1.5, accel=0.8, decel=0.6)


def _generate_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("motion_fixture_generate", MOTION / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CASES = json.loads((MOTION / "piecewise.json").read_text(encoding="utf-8"))
VALID = [case for case in CASES if case["samples"] is not None]
INVALID = [case for case in CASES if case["samples"] is None]


def test_committed_fixture_equals_a_fresh_generation() -> None:
    assert (MOTION / "piecewise.json").read_text(encoding="utf-8") == _generate_module().render()


def test_fixture_covers_the_required_cases() -> None:
    names = {case["name"] for case in CASES}
    assert {"one phase", "two phases", "three phases", "four phases", "nonzero entry speed"} <= names
    assert {"zero acceleration only", "duration mismatch", "negative speed", "distance mismatch"} <= names
    assert {"empty phase list", "zero duration", "negative duration"} <= names
    assert sorted(len(case["description"]["phases"]) for case in VALID) == [1, 1, 2, 3, 3, 3, 3, 4]


@pytest.mark.parametrize("case", VALID, ids=lambda case: case["name"])
def test_motion_fraction_reproduces_the_samples(case: dict) -> None:
    duration = case["t_end"] - case["t_start"]
    for time, fraction in case["samples"]:
        value = motion_fraction(case["description"], duration, time - case["t_start"], t_start=case["t_start"])
        assert value == pytest.approx(fraction, abs=1e-12)


@pytest.mark.parametrize("case", INVALID, ids=lambda case: case["name"])
def test_motion_fraction_is_none_for_invalid_descriptions(case: dict) -> None:
    duration = case["t_end"] - case["t_start"]
    assert motion_fraction(case["description"], duration, 0.5 * duration, t_start=case["t_start"]) is None


def test_motion_fraction_clamps_elapsed() -> None:
    description = arc_profile(0.0, 0.0, 1.5, 3.0, KIN).description()
    duration = arc_profile(0.0, 0.0, 1.5, 3.0, KIN).duration
    assert motion_fraction(description, duration, -5.0) == 0.0
    assert motion_fraction(description, duration, duration + 5.0) == 1.0


def test_motion_fraction_rejects_other_curves_and_malformed_values() -> None:
    good = {"curve": "piecewise", "v0": 0.0, "phases": [[2.0, 0.5], [1.0, -1.0]], "distance": 1.5}
    assert motion_fraction(good, 3.0, 3.0) == 1.0
    assert motion_fraction({"curve": "linear"}, 3.0, 1.0) is None
    assert motion_fraction({"curve": "trapezoidal"}, 3.0, 1.0) is None
    assert motion_fraction(good, math.inf, 1.0) is None
    assert motion_fraction(good, -1.0, 1.0) is None
    for key, value in [
        ("v0", "0"),
        ("v0", True),
        ("v0", -1.0),
        ("v0", math.inf),
        ("distance", None),
        ("distance", 0.0),
        ("distance", math.inf),
        ("phases", None),
        ("phases", "ab"),
        ("phases", [[2.0, 0.5], [1.0]]),
        ("phases", [[2.0, 0.5], [1.0, "x"]]),
        ("phases", [[2.0, 0.5], 7]),
        ("phases", [[2.0, 0.5], [math.inf, 0.0]]),
        ("phases", [[2.0, 0.5], [1.0, math.nan]]),
    ]:
        assert motion_fraction({**good, key: value}, 3.0, 1.0) is None, (key, value)


def test_motion_fraction_is_none_for_a_number_beyond_float_range() -> None:
    """A huge integer, as JSON can carry, is not a valid number for the description: None, not `OverflowError`."""
    good = {"curve": "piecewise", "v0": 0.0, "phases": [[2.0, 0.5], [1.0, -1.0]], "distance": 1.5}
    huge = 10**400
    for key, value in [
        ("v0", huge),
        ("distance", huge),
        ("phases", [[huge, 0.5], [1.0, -1.0]]),
        ("phases", [[2.0, 0.5], [1.0, -huge]]),
    ]:
        assert motion_fraction({**good, key: value}, 3.0, 1.0) is None, (key, value)
    assert motion_fraction(good, huge, 1.0) is None
    assert motion_fraction(good, 3.0, 1.0, t_start=huge) is None


def test_motion_fraction_accepts_tuples_and_floors_rounding_negative_speed() -> None:
    # The speed ends at -1e-10, inside the 1e-9 allowance; later phases start from zero.
    description = {"curve": "piecewise", "v0": 1.0, "phases": ((1.0, -1.0 - 1e-10), (1.0, 0.0)), "distance": 0.5}
    value = motion_fraction(description, 2.0, 2.0)
    assert value == pytest.approx(1.0, abs=1e-9)


def test_description_of_a_profile() -> None:
    profile = arc_profile(0.5, 0.0, 1.0, 3.0, KIN)
    description = profile.description()
    assert description["curve"] == "piecewise"
    assert description["v0"] == 0.5
    assert description["phases"] == [[d, a] for d, a in profile.phases]
    assert description["distance"] == 3.0


def test_zero_length_arc_describes_as_linear() -> None:
    profile = arc_profile(0.0, 0.0, 1.5, 0.0, KIN)
    assert profile.phases == ()
    assert profile.description() == {"curve": "linear"}
    # A motion of zero duration is not evaluated as piecewise and does not divide by zero.
    assert motion_fraction(profile.description(), 0.0, 0.0) is None


def test_zero_duration_motion_is_none_for_piecewise() -> None:
    description = {"curve": "piecewise", "v0": 0.0, "phases": [[1.0, 1.0]], "distance": 0.5}
    assert motion_fraction(description, 0.0, 0.0) is None


def test_planned_profiles_never_describe_zero_duration_phases() -> None:
    profiles: list[ArcProfile] = [
        arc_profile(v, w, 1.5, length, KIN) for v in (0.0, 0.7) for w in (0.0, 0.7, 1.0) for length in (2.0, 9.0)
    ]
    profiles += [arc_profile(1.5, 1.5, 1.5, 4.0, KIN), arc_profile(0.0, 1.5, 1.5, 1.5 * 1.5 / 1.6, KIN)]
    profiles += list(plan_run(0.0, [0.0, 2.0, 0.0, 3.0], [1.5] * 4, [0.0, 1.0, 0.0, 0.5, 0.0], KIN))
    for profile in profiles:
        description = profile.description()
        if profile.phases:
            assert all(duration > 0 for duration, _ in description["phases"])  # type: ignore[attr-defined]
        else:
            assert description == {"curve": "linear"}


def test_tolerance_follows_the_event_times() -> None:
    description = {"curve": "piecewise", "v0": 0.0, "phases": [[2.0 + 5e-10, 0.5], [1.0, -1.0]], "distance": 1.5}
    t_end = 1e6 + 3.0 + 5e-10
    assert motion_fraction(description, t_end - 1e6, 1.5, t_start=1e6) is not None
    assert motion_fraction(description, t_end - 1e6, 1.5) is None


def test_planned_arcs_are_valid_at_large_times() -> None:
    rng = random.Random(20261122)
    for _ in range(3000):
        profile = rest_to_rest(rng.uniform(0.5, 40.0), 1.5, KIN)
        t_start = rng.uniform(0.0, 1e6)
        duration = (t_start + profile.duration) - t_start
        elapsed = rng.uniform(0.0, duration)
        value = motion_fraction(profile.description(), duration, elapsed, t_start=t_start)
        assert value is not None
        assert value == pytest.approx(profile.position(min(elapsed, profile.duration)) / profile.length, abs=1e-9)
