from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypeGuard, runtime_checkable

from simulatte.intralogistics.kinematics import Kinematics

if TYPE_CHECKING:
    from collections.abc import Callable

MotionDescription: TypeAlias = Mapping[str, object]
"""A portable description of how a segment is traversed (spec §6.5): a map of wire values with a ``curve`` key."""

LINEAR_MOTION: MotionDescription = {"curve": "linear", "approximate": True}
"""The description of a profile without ``motion``: viewers interpolate linearly and mark the motion approximate."""


def motion_fraction(
    description: MotionDescription, duration: float, elapsed: float, *, t_start: float = 0.0
) -> float | None:
    """The position fraction of a ``piecewise`` description `elapsed` seconds into a motion of `duration` seconds.

    `elapsed` is clamped to ``[0, duration]``. Returns ``None`` for any other curve and for an invalid
    description (spec §11.3): no phase, a duration that is not positive and finite, durations that do not sum to
    `duration` (within the tolerance of ``keyframes``, which depends on the event times), a speed below -1e-9 at
    a phase boundary, an integrated distance that differs from ``distance`` by more than
    ``max(1e-9, 1e-9 * distance)``, or a ``distance`` that is not positive and finite. A caller that has the event
    times passes ``duration = t_end - t_start`` and `t_start`, so that the tolerance is the one Studio computes.
    Speeds are floored at zero between phases, as :class:`ArcProfile` does. A number beyond the float range (a huge
    integer read from JSON) makes the description invalid.
    """
    if description.get("curve") != "piecewise" or not _finite(duration) or duration < 0:
        return None
    v0, phases, distance = description.get("v0"), description.get("phases"), description.get("distance")
    if (
        not _is_number(v0)
        or not _is_number(distance)
        or not isinstance(phases, list | tuple)
        or not phases
        or not _finite(v0)
        or not _finite(distance)
        or v0 < -1e-9
        or distance <= 0
    ):
        return None
    parsed: list[tuple[float, float]] = []
    for phase in phases:
        if not isinstance(phase, list | tuple) or len(phase) != 2 or not all(_is_number(x) for x in phase):
            return None
        length, accel = phase
        if not (_finite(length) and length > 0 and _finite(accel)):
            return None
        parsed.append((float(length), float(accel)))
    # The tolerance of ``keyframes`` and of Studio: eight ulps of the larger absolute event time, at least 1e-12.
    try:
        magnitude = max(abs(t_start), abs(t_start + duration), 5e-324)
        tolerance = max(1e-12, 8 * math.ldexp(1.0, math.frexp(magnitude)[1] - 1 - 52))
    except OverflowError:  # a start time beyond the float range
        return None
    if abs(sum(length for length, _ in parsed) - duration) > tolerance:
        return None
    t = min(max(elapsed, 0.0), duration)
    covered, speed, position = 0.0, float(v0), None
    for length, accel in parsed:
        if position is None and t <= length:
            position = covered + speed * t + 0.5 * accel * t * t
        covered += speed * length + 0.5 * accel * length * length
        speed += accel * length
        if speed < -1e-9:
            return None
        speed = max(0.0, speed)
        t -= length
    if abs(covered - distance) > max(1e-9, 1e-9 * distance):
        return None
    return min(1.0, max(0.0, (covered if position is None else position) / distance))


def _is_number(value: object) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _finite(value: float) -> bool:
    """``math.isfinite``, false rather than `OverflowError` for an integer beyond the float range."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@runtime_checkable
class SpeedProfile(Protocol):
    """Travel time of a segment.

    A profile may also define ``motion(distance, load_weight, battery_level, speed_limit) -> MotionDescription``,
    the curve a viewer uses to interpolate the position (spec §6.5). A portable custom curve may use
    ``{"curve": "keyframes", "keyframes": [[elapsed, fraction], ...], "accuracy": meters}``.
    At least two finite points must start at ``[0, 0]``, end at fraction 1, have strictly increasing elapsed
    times and nondecreasing fractions in [0, 1]. Accuracy is finite and nonnegative. The final elapsed time
    must match the finite positive segment duration within eight float64 ULPs of the larger absolute endpoint
    time (minimum tolerance 1e-12). Viewers interpolate between points and never call the profile.
    Profiles without a description are represented by
    :data:`LINEAR_MOTION`. The method is optional, so it is not part of this protocol.
    """

    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float: ...


@runtime_checkable
class KinematicProfile(Protocol):
    """A profile that reports its motion limits for a load and a battery level (spec §9.1).

    A non-positive ``v_max`` or ``accel`` means the AGV cannot move.
    """

    def limits(self, load_weight: float, battery_level: float) -> Kinematics: ...


def describe_motion(
    profile: object, distance: float, load_weight: float, battery_level: float, speed_limit: float | None
) -> MotionDescription:
    """The motion description of `profile` for a segment, or :data:`LINEAR_MOTION` when it has no ``motion``."""
    motion = getattr(profile, "motion", None)
    if motion is None:
        return LINEAR_MOTION
    return motion(distance, load_weight, battery_level, speed_limit)


class TrapezoidalProfile:
    def __init__(
        self,
        max_speed: float,
        acceleration: float,
        deceleration: float,
        battery_degradation_fn: Callable[[float], float] | None = None,
        load_speed_factor_fn: Callable[[float], float] | None = None,
    ) -> None:
        self._max_speed = max_speed
        self._acceleration = acceleration
        self._deceleration = deceleration
        self._battery_degradation_fn = battery_degradation_fn or (lambda level: level)
        self._load_speed_factor_fn = load_speed_factor_fn or (lambda _: 1.0)
        # (load_weight, battery_level, speed_limit) -> effective values of the last travel_time call, so that
        # motion() for the same segment does not call the factor functions again.
        self._last: tuple[tuple[float, float, float | None], tuple[float, float, float, bool]] | None = None

    def _scaled(self, load_weight: float, battery_level: float) -> tuple[float, float, float, bool]:
        """(v_max, accel, decel, moving) before any speed limit; `moving` is False when a factor is not positive."""
        battery_factor = self._battery_degradation_fn(battery_level)
        load_factor = self._load_speed_factor_fn(load_weight)
        # Battery scales v_max and acceleration; load scales v_max only; deceleration is unscaled
        v_max = self._max_speed * battery_factor * load_factor
        moving = not (battery_factor <= 0 or load_factor <= 0)
        return v_max, self._acceleration * battery_factor, self._deceleration, moving

    def _effective(
        self, load_weight: float, battery_level: float, speed_limit: float | None
    ) -> tuple[float, float, float, bool]:
        """(v_max, accel, decel, moving): the scaled parameters; `moving` is False when a factor is not positive."""
        v_max, accel, decel, moving = self._scaled(load_weight, battery_level)
        if speed_limit is not None:
            v_max = min(v_max, speed_limit)
        effective = (v_max, accel, decel, moving)
        self._last = ((load_weight, battery_level, speed_limit), effective)
        return effective

    def limits(self, load_weight: float = 0.0, battery_level: float = 1.0) -> Kinematics:
        """The scaled limits :meth:`travel_time` starts from, before any speed limit; the motion cache is untouched."""
        v_max, accel, decel, _ = self._scaled(load_weight, battery_level)
        return Kinematics(v_max, accel, decel)

    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float:
        if distance <= 0:
            return 0.0

        v_max, accel, decel, moving = self._effective(load_weight, battery_level, speed_limit)
        if not moving:
            return float("inf")

        d_accel = v_max**2 / (2 * accel)
        d_decel = v_max**2 / (2 * decel)

        if d_accel + d_decel <= distance:
            t_accel = v_max / accel
            t_decel = v_max / decel
            t_cruise = (distance - d_accel - d_decel) / v_max
            return t_accel + t_cruise + t_decel
        else:
            v_peak = math.sqrt(2 * distance * accel * decel / (accel + decel))
            return v_peak / accel + v_peak / decel

    def motion(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> MotionDescription:
        """The trapezoidal curve with the effective values :meth:`travel_time` uses for the same arguments.

        Accelerate at ``accel`` up to ``v_max``, cruise, decelerate at ``decel``; when the distance is too short to
        reach ``v_max``, the peak speed is lower (a triangular profile). The values of the last :meth:`travel_time`
        call are reused when the arguments match; a zero distance reports the nominal parameters.
        """
        if distance <= 0:
            v_max, accel, decel = self._max_speed, self._acceleration, self._deceleration
        else:
            last = self._last
            key = (load_weight, battery_level, speed_limit)
            v_max, accel, decel, _ = last[1] if last is not None and last[0] == key else self._effective(*key)
        return {
            "curve": "trapezoidal",
            "v_max": float(v_max),
            "accel": float(accel),
            "decel": float(decel),
            "distance": float(max(distance, 0.0)),
        }
