from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.intralogistics.battery import Battery

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.environment import Environment
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import Node
    from simulatte.intralogistics.order import TransferOrder
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
    compatibility_fn: Callable[[Any], bool] = field(default=lambda sku: True)
    depletion_fn: Callable[[float, float, float], float] | None = None
    recharge_fn: Callable[[float, float], float] | None = None
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    load_time_fn: Callable[[], float] = field(default=lambda: 0.0)
    unload_time_fn: Callable[[], float] = field(default=lambda: 0.0)


class AGV(Entity, kind="agv"):
    """An automated guided vehicle.

    Its id is ``agv_id`` when given, otherwise ``agv-<n>`` in attachment order; :attr:`agv_id` is an alias of
    :attr:`id`. Setting :attr:`current_node` keeps the ``agvs`` lists of the node bindings up to date.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {
            "node": FieldSpec("str", nullable=True),
            "state": FieldSpec("str"),
            "battery": FieldSpec("float"),
            "load": FieldSpec("int", nullable=True, collection="map"),
            "order": FieldSpec("str", nullable=True),
            "motion": FieldSpec("map", nullable=True),
            "fleet": FieldSpec("str", nullable=True),
        }
    )

    def __init__(
        self,
        *,
        env: Environment,
        agv_type: AGVType,
        agv_id: str | None = None,
        initial_node: Node | None = None,
        label: str | None = None,
    ) -> None:
        self.env = env
        self.agv_type = agv_type
        self._current_node = initial_node
        self.current_load: dict[SKU, int] | None = None
        self.fleet: FleetCoordinator | None = None
        """The coordinator that took this AGV (owner field, spec §5.2)."""
        self.order: TransferOrder | None = None
        """The order of the AGV's current mission."""
        self.motion: dict[str, Any] | None = None
        """The active movement segment (spec §6.5), or None."""

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

        env.entities.attach(self, name=agv_id, label=label)
        if initial_node is not None:
            binding = env.entities.node_binding(initial_node)
            if binding is not None:
                binding.agvs.append(self.id)

    @property
    def agv_id(self) -> str:
        """Alias of :attr:`id`."""
        return self.id

    @property
    def current_node(self) -> Node | None:
        """The node the AGV is at; during a segment, the node it left."""
        return self._current_node

    @current_node.setter
    def current_node(self, node: Node | None) -> None:
        previous = self._current_node
        self._current_node = node
        if node == previous:
            return
        entities = self.env.entities
        if previous is not None:
            binding = entities.node_binding(previous)
            if binding is not None and self.id in binding.agvs:
                binding.agvs.remove(self.id)
        if node is not None:
            binding = entities.node_binding(node)
            if binding is not None:
                binding.agvs.append(self.id)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state; nodes, SKUs, the order and the fleet are referenced by id."""
        node, load, order, fleet = self._current_node, self.current_load, self.order, self.fleet
        return {
            "node": None if node is None else node.id,
            "state": self._state.name,
            "battery": float(self.battery.level),
            "load": None if load is None else {sku.id: quantity for sku, quantity in load.items()},
            "order": None if order is None else order.id,
            "motion": self.motion,
            "fleet": None if fleet is None else fleet.id,
            "label": self.label,
        }

    @property
    def state(self) -> AGVState:
        return self._state

    def transition_to(self, new_state: AGVState) -> None:
        old_state = self._state
        elapsed = self.env.now - self._state_entered_at
        self.state_durations[old_state] += elapsed
        self._state = new_state
        self._state_entered_at = self.env.now
        self.env.debug(
            f"{self.agv_id} {old_state.name} -> {new_state.name}",
            component="AGV",
        )

    def can_carry(self, sku: SKU, quantity: int) -> bool:
        if not self.agv_type.compatibility_fn(sku):
            return False
        total_weight = sku.weight * quantity
        total_volume = sku.volume * quantity
        return total_weight <= self.agv_type.weight_capacity and total_volume <= self.agv_type.volume_capacity

    def utilization(self) -> float:
        self._flush_current_state()
        total = math.fsum(self.state_durations.values())
        if total == 0:
            return 0.0
        utilized = math.fsum(self.state_durations[s] for s in _UTILIZED_STATES)
        return utilized / total

    def state_percentage(self, state: AGVState) -> float:
        self._flush_current_state()
        total = math.fsum(self.state_durations.values())
        if total == 0:
            return 0.0
        return self.state_durations[state] / total

    def time_allocation(self) -> dict[AGVState, float]:
        self._flush_current_state()
        total = math.fsum(self.state_durations.values())
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
