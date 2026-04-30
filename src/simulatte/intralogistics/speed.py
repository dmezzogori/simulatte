from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class SpeedProfile(Protocol):
    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float: ...


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

    def travel_time(
        self,
        distance: float,
        load_weight: float = 0.0,
        battery_level: float = 1.0,
        speed_limit: float | None = None,
    ) -> float:
        if distance <= 0:
            return 0.0

        battery_factor = self._battery_degradation_fn(battery_level)
        load_factor = self._load_speed_factor_fn(load_weight)

        if battery_factor <= 0 or load_factor <= 0:
            return float("inf")

        # Battery scales v_max and acceleration; load scales v_max only; deceleration is unscaled
        v_max = self._max_speed * battery_factor * load_factor
        if speed_limit is not None:
            v_max = min(v_max, speed_limit)

        accel = self._acceleration * battery_factor
        decel = self._deceleration

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
