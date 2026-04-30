from __future__ import annotations

import pytest

from simulatte.intralogistics.speed import TrapezoidalProfile


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
