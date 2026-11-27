from __future__ import annotations

import pytest

from simulatte.intralogistics.kinematics import Kinematics
from simulatte.intralogistics.speed import KinematicProfile, TrapezoidalProfile


class TestTrapezoidalProfile:
    def test_long_arc_trapezoidal(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        t = profile.travel_time(distance=10.0)
        # Accel phase: 2s to reach 2m/s, covers 2m
        # Decel phase: 2s from 2m/s to 0, covers 2m
        # Cruise phase: 6m at 2m/s = 3s
        # Total: 2 + 3 + 2 = 7s
        assert t == pytest.approx(7.0)

    def test_short_arc_triangular(self) -> None:
        profile = TrapezoidalProfile(max_speed=10.0, acceleration=1.0, deceleration=1.0)
        t = profile.travel_time(distance=2.0)
        # Can't reach max_speed. Triangular profile.
        # v_peak = sqrt(2 * d * a * dec / (a + dec)) = sqrt(2 * 2 * 1 * 1 / 2) = sqrt(2)
        # t = v_peak/a + v_peak/dec = 2*sqrt(2) ≈ 2.828
        assert t == pytest.approx(2 * (2.0**0.5), rel=1e-6)

    def test_zero_distance(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        assert profile.travel_time(distance=0.0) == 0.0

    def test_speed_limit_caps(self) -> None:
        profile = TrapezoidalProfile(max_speed=10.0, acceleration=1.0, deceleration=1.0)
        t_limited = profile.travel_time(distance=20.0, speed_limit=2.0)
        t_unlimited = profile.travel_time(distance=20.0)
        assert t_limited > t_unlimited

    def test_battery_degradation(self) -> None:
        profile = TrapezoidalProfile(
            max_speed=2.0,
            acceleration=1.0,
            deceleration=1.0,
            battery_degradation_fn=lambda lvl: lvl,
        )
        t_full = profile.travel_time(distance=10.0, battery_level=1.0)
        t_half = profile.travel_time(distance=10.0, battery_level=0.5)
        assert t_half > t_full

    def test_load_speed_factor(self) -> None:
        profile = TrapezoidalProfile(
            max_speed=2.0,
            acceleration=1.0,
            deceleration=1.0,
            load_speed_factor_fn=lambda w: max(0.1, 1.0 - w / 100.0),
        )
        t_empty = profile.travel_time(distance=10.0, load_weight=0.0)
        t_loaded = profile.travel_time(distance=10.0, load_weight=50.0)
        assert t_loaded > t_empty

    def test_zero_battery_infinite_time(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        t = profile.travel_time(distance=10.0, battery_level=0.0)
        assert t == float("inf")

    def test_default_battery_degradation_is_linear(self) -> None:
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        t_full = profile.travel_time(distance=10.0, battery_level=1.0)
        t_half = profile.travel_time(distance=10.0, battery_level=0.5)
        assert t_half > t_full

    def test_protocol_conformance(self) -> None:
        from simulatte.intralogistics.speed import SpeedProfile

        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        assert isinstance(profile, SpeedProfile)


class TestKinematicLimits:
    def test_limits_scale_like_effective(self) -> None:
        profile = TrapezoidalProfile(2.0, 1.0, 1.0)
        assert profile.limits(0.0, 0.5) == Kinematics(1.0, 0.5, 1.0)

    def test_limits_without_speed_limit(self) -> None:
        profile = TrapezoidalProfile(2.0, 1.0, 1.5, load_speed_factor_fn=lambda weight: 1.0 / (1.0 + weight))
        assert profile.limits(1.0, 1.0) == Kinematics(1.0, 1.0, 1.5)
        assert profile.limits() == Kinematics(2.0, 1.0, 1.5)

    def test_limits_leave_the_motion_cache_alone(self) -> None:
        profile = TrapezoidalProfile(2.0, 1.0, 1.0)
        profile.travel_time(10.0, 0.0, 1.0, 1.0)
        before = profile._last
        profile.limits(0.0, 0.5)
        assert profile._last is before
        assert profile.motion(10.0, 0.0, 1.0, 1.0)["v_max"] == 1.0

    def test_trapezoidal_profile_is_a_kinematic_profile(self) -> None:
        assert isinstance(TrapezoidalProfile(2.0, 1.0, 1.0), KinematicProfile)
