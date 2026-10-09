from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

MotionDescription: TypeAlias = Mapping[str, object]
"""A portable description of how a segment is traversed (spec §6.5): a map of wire values with a ``curve`` key."""

LINEAR_MOTION: MotionDescription = {"curve": "linear", "approximate": True}
"""The description of a profile without ``motion``: viewers interpolate linearly and mark the motion approximate."""


@runtime_checkable
class SpeedProfile(Protocol):
    """Travel time of a segment.

    A profile may also define ``motion(distance, load_weight, battery_level, speed_limit) -> MotionDescription``,
    the curve a viewer uses to interpolate the position (spec §6.5); profiles without it are described by
    :data:`LINEAR_MOTION`. The method is optional, so it is not part of this protocol.
    """

    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float: ...


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

    def _effective(
        self, load_weight: float, battery_level: float, speed_limit: float | None
    ) -> tuple[float, float, float, bool]:
        """(v_max, accel, decel, moving): the scaled parameters; `moving` is False when a factor is not positive."""
        battery_factor = self._battery_degradation_fn(battery_level)
        load_factor = self._load_speed_factor_fn(load_weight)
        # Battery scales v_max and acceleration; load scales v_max only; deceleration is unscaled
        v_max = self._max_speed * battery_factor * load_factor
        if speed_limit is not None:
            v_max = min(v_max, speed_limit)
        moving = not (battery_factor <= 0 or load_factor <= 0)
        effective = (v_max, self._acceleration * battery_factor, self._deceleration, moving)
        self._last = ((load_weight, battery_level, speed_limit), effective)
        return effective

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
