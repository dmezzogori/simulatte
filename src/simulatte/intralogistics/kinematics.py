"""Motion arithmetic for AGVs: arc profiles as constant-acceleration phases (spec §9).

This module is pure: it imports nothing from the simulation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Phase = tuple[float, float]
"""A phase of an arc profile: ``(duration, acceleration)``."""

_FEASIBILITY_SLACK = 1e-9
"""Relative slack on the speed reachability checks, so exit speeds computed by a planner survive rounding."""


@dataclass(frozen=True)
class Kinematics:
    """The motion limits of an AGV: top speed, acceleration and deceleration (all positive to move)."""

    v_max: float
    accel: float
    decel: float


@dataclass(frozen=True)
class ArcProfile:
    """The motion along one arc: entry speed and a sequence of constant-acceleration phases.

    The speed never goes below zero and ``position(duration)`` is ``length``.
    """

    v_in: float
    phases: tuple[Phase, ...]
    length: float

    @property
    def duration(self) -> float:
        return sum(duration for duration, _ in self.phases)

    @property
    def v_out(self) -> float:
        return max(0.0, self.v_in + sum(duration * accel for duration, accel in self.phases))

    def position(self, t: float) -> float:
        """Distance covered at elapsed time `t`, which must lie in ``[0, duration]``."""
        self._check_time(t)
        if t >= self.duration:
            return self.length
        covered, v, accel, tau = self._locate(t)
        return covered + v * tau + 0.5 * accel * tau * tau

    def speed(self, t: float) -> float:
        """Speed at elapsed time `t`, which must lie in ``[0, duration]``."""
        self._check_time(t)
        if t >= self.duration:
            return self.v_out
        _, v, accel, tau = self._locate(t)
        return max(0.0, v + accel * tau)

    def _locate(self, t: float) -> tuple[float, float, float, float]:
        """(distance, speed, acceleration, elapsed) at the start of the phase holding `t`; needs ``t < duration``."""
        covered, v = 0.0, self.v_in
        for duration, accel in self.phases[:-1]:
            if t <= duration:
                return covered, v, accel, t
            covered += v * duration + 0.5 * accel * duration * duration
            v = max(0.0, v + accel * duration)
            t -= duration
        duration, accel = self.phases[-1]
        return covered, v, accel, min(t, duration)  # rounding may leave t a little past the last phase

    def time_at(self, s: float) -> float:
        """Elapsed time at distance `s` in ``[0, length]`` (rounding beyond the end is tolerated).

        Where a position is held over an interval, the earliest time is returned.
        """
        if s < 0 or s > self.length + 1e-9:
            raise ValueError(f"position {s} is outside the arc [0, {self.length}]")
        if s <= 0:
            return 0.0
        elapsed, covered, v = 0.0, 0.0, self.v_in
        for duration, accel in self.phases:
            gain = v * duration + 0.5 * accel * duration * duration
            if s <= covered + gain:
                remaining = s - covered
                root = math.sqrt(max(0.0, v * v + 2.0 * accel * remaining))
                # The earliest root of v*tau + accel*tau^2/2 = remaining, in a form that is stable for accel -> 0.
                return elapsed + min(duration, 2.0 * remaining / (v + root))
            elapsed += duration
            covered += gain
            v = max(0.0, v + accel * duration)
        return elapsed

    def _check_time(self, t: float) -> None:
        if t < 0 or t > self.duration:
            raise ValueError(f"time {t} is outside the profile [0, {self.duration}]")


def arc_profile(v_in: float, v_out: float, cap: float, length: float, kin: Kinematics) -> ArcProfile:
    """The profile that enters an arc at `v_in`, leaves it at `v_out` and runs as fast as `cap` allows (spec §9.2).

    The AGV accelerates to the peak speed ``min(cap, sqrt((2 a d L + d v_in^2 + a v_out^2) / (a + d)))``, cruises
    and decelerates; phases of zero duration are omitted. Raises `ValueError` when an argument is negative, when
    `v_in` or `v_out` exceeds `cap`, when `cap` or an acceleration is not positive on a positive length, or when
    `v_out` cannot be reached from `v_in` within `length`.
    """
    if length < 0 or v_in < 0 or v_out < 0:
        raise ValueError("length and speeds must not be negative")
    if v_in > cap or v_out > cap:
        raise ValueError(f"entry speed {v_in} and exit speed {v_out} must not exceed the cap {cap}")
    if length == 0:
        if v_in != v_out:
            raise ValueError("a zero-length arc cannot change the speed")
        return ArcProfile(v_in, (), 0.0)
    accel, decel = kin.accel, kin.decel
    if cap <= 0 or accel <= 0 or decel <= 0:
        raise ValueError("the cap and the accelerations must be positive")
    if v_out * v_out > (v_in * v_in + 2 * accel * length) * (1 + _FEASIBILITY_SLACK) or v_in * v_in > (
        v_out * v_out + 2 * decel * length
    ) * (1 + _FEASIBILITY_SLACK):
        raise ValueError(f"speed {v_out} cannot be reached from {v_in} within {length}")
    peak_squared = (2 * accel * decel * length + decel * v_in * v_in + accel * v_out * v_out) / (accel + decel)
    cruise = 0.0
    if peak_squared > cap * cap:
        peak = cap
        cruise = max(
            0.0, length - (peak * peak - v_in * v_in) / (2 * accel) - (peak * peak - v_out * v_out) / (2 * decel)
        )
    else:
        peak = max(math.sqrt(peak_squared), v_in, v_out)
    candidates = (((peak - v_in) / accel, accel), (cruise / peak, 0.0), ((peak - v_out) / decel, -decel))
    return ArcProfile(v_in, tuple(phase for phase in candidates if phase[0] > 0), length)


def rest_to_rest(length: float, cap: float, kin: Kinematics) -> ArcProfile:
    """The profile of an arc entered and left at rest."""
    return arc_profile(0.0, 0.0, cap, length, kin)
