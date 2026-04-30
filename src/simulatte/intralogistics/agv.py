from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from simulatte.intralogistics.battery import Battery

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import Node
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.speed import SpeedProfile


class AGVState(Enum):
    IDLE = auto()
    TRAVELING_EMPTY = auto()
    WAITING_LOAD = auto()
    TRAVELING_LOADED = auto()
    WAITING_UNLOAD = auto()
    CHARGING = auto()
    STRANDED = auto()


_UTILIZED_STATES = frozenset(
    {
        AGVState.TRAVELING_EMPTY,
        AGVState.WAITING_LOAD,
        AGVState.TRAVELING_LOADED,
        AGVState.WAITING_UNLOAD,
    }
)


@dataclass(frozen=True)
class AGVType:
    name: str
    speed_profile: SpeedProfile
    battery_capacity: float
    weight_capacity: float
    volume_capacity: float
    compatibility_fn: Callable[[Any], bool] = field(default=lambda sku: True)  # noqa: ARG005
    depletion_fn: Callable[[float, float, float], float] | None = None
    recharge_fn: Callable[[float, float], float] | None = None
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    load_time_fn: Callable[[], float] = field(default=lambda: 0.0)
    unload_time_fn: Callable[[], float] = field(default=lambda: 0.0)


class AGV:
    def __init__(
        self,
        *,
        env: Environment,
        agv_type: AGVType,
        agv_id: str | None = None,
        initial_node: Node | None = None,
    ) -> None:
        self.env = env
        self.agv_type = agv_type
        self.agv_id = agv_id or f"agv-{uuid.uuid4().hex[:8]}"
        self.current_node = initial_node
        self.current_load: dict[SKU, int] | None = None

        self.battery = Battery(
            capacity=agv_type.battery_capacity,
            depletion_fn=agv_type.depletion_fn,
            recharge_fn=agv_type.recharge_fn,
            low_threshold=agv_type.low_battery_threshold,
            critical_threshold=agv_type.critical_battery_threshold,
        )

        self._state = AGVState.IDLE
        self._state_entered_at: float = env.now
        self.state_durations: dict[AGVState, float] = {s: 0.0 for s in AGVState}

    @property
    def state(self) -> AGVState:
        return self._state

    def transition_to(self, new_state: AGVState) -> None:
        elapsed = self.env.now - self._state_entered_at
        self.state_durations[self._state] += elapsed
        self._state = new_state
        self._state_entered_at = self.env.now

    def can_carry(self, sku: SKU, quantity: int) -> bool:
        if not self.agv_type.compatibility_fn(sku):
            return False
        total_weight = sku.weight * quantity
        total_volume = sku.volume * quantity
        return total_weight <= self.agv_type.weight_capacity and total_volume <= self.agv_type.volume_capacity

    def utilization(self) -> float:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return 0.0
        utilized = sum(self.state_durations[s] for s in _UTILIZED_STATES)
        return utilized / total

    def state_percentage(self, state: AGVState) -> float:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return 0.0
        return self.state_durations[state] / total

    def time_allocation(self) -> dict[AGVState, float]:
        self._flush_current_state()
        total = sum(self.state_durations.values())
        if total == 0:
            return {s: 0.0 for s in AGVState}
        return {s: self.state_durations[s] / total for s in AGVState}

    def _flush_current_state(self) -> None:
        elapsed = self.env.now - self._state_entered_at
        if elapsed > 0:
            self.state_durations[self._state] += elapsed
            self._state_entered_at = self.env.now

    def __repr__(self) -> str:
        return f"AGV(id={self.agv_id!r}, state={self._state.name})"
