from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def _default_depletion(distance: float, load_weight: float, speed: float) -> float:  # noqa: ARG001
    return distance * 1.0


def _default_recharge(current_level: float, target_level: float) -> float:
    return (target_level - current_level) * 1.0


class Battery:
    def __init__(
        self,
        capacity: float,
        initial_level: float | None = None,
        depletion_fn: Callable[[float, float, float], float] | None = None,
        recharge_fn: Callable[[float, float], float] | None = None,
        low_threshold: float = 0.2,
        critical_threshold: float = 0.05,
    ) -> None:
        self.capacity = capacity
        self.level = initial_level if initial_level is not None else capacity
        self._depletion_fn = depletion_fn or _default_depletion
        self._recharge_fn = recharge_fn or _default_recharge
        self.low_threshold = low_threshold
        self.critical_threshold = critical_threshold

    @property
    def level_pct(self) -> float:
        return self.level / self.capacity if self.capacity > 0 else 0.0

    @property
    def is_low(self) -> bool:
        return self.level_pct <= self.low_threshold

    @property
    def is_critical(self) -> bool:
        return self.level_pct <= self.critical_threshold

    def deplete(self, distance: float, load_weight: float, speed: float) -> None:
        consumed = self._depletion_fn(distance, load_weight, speed)
        self.level = max(0.0, self.level - consumed)

    def recharge_time(self, target_pct: float = 1.0) -> float:
        target_level = target_pct * self.capacity
        if target_level <= self.level:
            return 0.0
        return self._recharge_fn(self.level, target_level)

    def recharge(self, amount: float) -> None:
        self.level = min(self.capacity, self.level + amount)
