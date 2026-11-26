from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar

from simulatte._wire import wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas
from simulatte.layout.binding import node_input, pending
from simulatte.layout.errors import LayoutNotReady
from simulatte.intralogistics.battery import Battery
from simulatte.intralogistics.events import AgvBatteryChanged, AgvPlaced, AgvStateChanged

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.environment import Environment
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte._wire import FrozenMap
    from simulatte.events import DeltaBuilder
    from simulatte.intralogistics.graph import Node, NodeBinding
    from simulatte.intralogistics.order import TransferOrder
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.speed import SpeedProfile
    from simulatte.rng import SamplerDescription
    from simulatte.layout.refs import NodeRef, PortRef


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
    """The settings shared by the AGVs of one kind: speed profile, battery, capacities, load and unload times, the
    vehicle's size (``length``, ``width``, ``min_gap``) and its turning and idle-power models."""

    name: str
    speed_profile: SpeedProfile
    battery_capacity: float
    weight_capacity: float
    volume_capacity: float
    compatibility_fn: Callable[[Any], bool] = field(default=lambda sku: True)
    depletion_fn: Callable[[float, float, float], float] | None = None
    """Energy for ``(distance, load_weight, speed)``; None for one unit per unit of distance. It must be pure (the same
    arguments give the same energy, and nothing else changes): the travel executor may call it fewer times than once
    per arc and reuse the result within a run, and the energy estimates of the planner and of the charger choice call
    it for arcs that are not travelled."""
    recharge_fn: Callable[[float, float], float] | None = None
    low_battery_threshold: float = 0.2
    critical_battery_threshold: float = 0.05
    load_time: SamplerDescription[float] | float | Callable[[], float] = 0.0
    """Load duration: a distribution description or a number (managed), or a zero-argument callable (opaque).

    Each AGV binds it to its own stream ``<agv id>/load`` when it attaches.
    """
    unload_time: SamplerDescription[float] | float | Callable[[], float] = 0.0
    """Unload duration, like :attr:`load_time`, bound to the stream ``<agv id>/unload``."""
    length: float | None = None
    """Length of the vehicle; ``ReservationController`` requires it (spec §6.6). It is also the vehicle's size along
    its heading in the layout (:attr:`size`): ``None`` and ``0`` count as not declared there, and the vehicle keeps
    the default 1 x 1 m placement footprint."""
    width: float | None = field(default=None, kw_only=True)
    """Width of the vehicle, finite and >= 0, keyword-only; descriptive, no component reads it. With a positive
    :attr:`length` it is the depth of the vehicle's placement footprint (:attr:`size`); ``None`` and ``0`` count as not
    declared, and the depth is then half the length. Without a length it is not used."""
    min_gap: float = 0.0
    """Distance kept to the vehicle ahead; the extent of the vehicle is ``length + min_gap``."""
    corner_speed: Callable[[float], float] | None = None
    """The speed cap through a node, from the heading change there in radians (``[0, pi]``, spec §9.4). 0 is a stop
    at the node, infinity no cap; negative and NaN values raise ``ValueError``. It acts on profiles with limits. It must
    be pure: the planner and :class:`~simulatte.intralogistics.routing.FastestRouter` call it for turns that are not
    on the route finally travelled."""
    turn_time: Callable[[float], float] | None = None
    """The time the vehicle waits at rest on a node before it leaves, from the heading change there in radians
    (``[0, pi]``, spec §9.4). A positive value stops the vehicle at the node; negative, NaN and infinite values raise
    ``ValueError``. It must be pure: :class:`~simulatte.intralogistics.routing.FastestRouter` calls it for turns that
    are not on the route finally travelled."""
    idle_power: float = 0.0
    """Energy drained per unit time in every state except ``CHARGING`` (spec §9.5); finite and >= 0, else
    ``ValueError``. It accrues lazily (:meth:`AGV.accrue_idle_power`): no timer wakes an idle AGV. With the default 0
    nothing is accounted, emitted or computed."""

    def __post_init__(self) -> None:
        if self.length is not None and not (math.isfinite(self.length) and self.length >= 0):
            raise ValueError(f"AGVType {self.name!r}: length must be finite and >= 0, got {self.length}")
        if self.width is not None and not (math.isfinite(self.width) and self.width >= 0):
            raise ValueError(f"AGVType {self.name!r}: width must be finite and >= 0, got {self.width}")
        if not (math.isfinite(self.min_gap) and self.min_gap >= 0):
            raise ValueError(f"AGVType {self.name!r}: min_gap must be finite and >= 0, got {self.min_gap}")
        if not (math.isfinite(self.idle_power) and self.idle_power >= 0):
            raise ValueError(f"AGVType {self.name!r}: idle_power must be finite and >= 0, got {self.idle_power}")

    @property
    def size(self) -> tuple[float, float] | None:
        """``(length, width)`` of the vehicle's placement footprint, or None when the type declares no length.

        A length of ``None`` or ``0`` is not declared (no size: the footprint stays 1 x 1 m); a width of ``None`` or
        ``0`` with a positive length gives half the length (studio map spec §6.1), or the length itself when half of
        it rounds to 0. The layout writes it to the vehicle's target in the bundle."""
        if not self.length:
            return None
        length = float(self.length)
        return (length, float(self.width) if self.width else length / 2 or length)


class AGV(Entity, kind="agv"):
    """An automated guided vehicle.

    Its id is ``agv_id`` when given, otherwise ``agv-<n>`` in attachment order; :attr:`agv_id` is an alias of
    :attr:`id`. Setting :attr:`current_node` keeps the ``agvs`` lists of the node bindings up to date and emits
    ``agv.placed``; movement along the graph goes through the fleet coordinator, which emits the ``agv.move_*``
    events. :meth:`transition_to` is the only writer of the state and emits ``agv.state_changed``.
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
            "heading": FieldSpec("float", nullable=True),
        }
    )

    def __init__(
        self,
        *,
        env: Environment,
        agv_type: AGVType,
        agv_id: str | None = None,
        initial_node: NodeRef | PortRef | None = None,
        label: str | None = None,
    ) -> None:
        self.env = env
        self.agv_type = agv_type
        self._current_node, self._pending_node = node_input(
            env, initial_node, component="AGV", parameter="initial_node", optional=True
        )
        node = self._current_node
        self.current_load: dict[SKU, int] | None = None
        self.fleet: FleetCoordinator | None = None
        """The coordinator that took this AGV (owner field, spec §5.2)."""
        self.order: TransferOrder | None = None
        """The order of the AGV's current mission."""
        self.motion: FrozenMap | None = None
        """The active movement segment ``{from, to, t_start, t_end, description}`` (spec §6.5), or None."""
        self.heading: float | None = None
        """The direction of the last arc traversed, in radians (``atan2`` of its chord, spec §9.4); None after
        placement."""

        self.battery = Battery(
            capacity=agv_type.battery_capacity,
            depletion_fn=agv_type.depletion_fn,
            recharge_fn=agv_type.recharge_fn,
            low_threshold=agv_type.low_battery_threshold,
            critical_threshold=agv_type.critical_battery_threshold,
        )

        self._state = AGVState.IDLE
        self._state_entered_at: float = env.now
        self._accrued_at: float = env.now
        """The time up to which idle power has been accounted (:meth:`accrue_idle_power`)."""
        self.state_durations: dict[AGVState, float] = {s: 0.0 for s in AGVState}

        env.entities.attach(self, name=agv_id, label=label)
        self._load_time = env.bind(agv_type.load_time, kind="scalar", stream=f"{self.id}/load", owner=self.id)
        self._unload_time = env.bind(agv_type.unload_time, kind="scalar", stream=f"{self.id}/unload", owner=self.id)
        if node is not None:  # resolved at once: the layout is prepared and bound its nodes before this AGV existed
            binding = env.entities.node_binding(node)
            assert binding is not None
            binding.agvs.append(self.id)  # its create delta did not list it
            if env.wants(AgvPlaced):
                env.emit(
                    AgvPlaced(
                        agv=self.id,
                        node=node.id,
                        previous=None,
                        deltas=Deltas.build()
                        .set(self.id, "node", node.id)
                        .insert(binding.id, "agvs", len(binding.agvs) - 1, self.id)
                        .done(),
                    )
                )

    def sample_load_time(self) -> float:
        """Draw the duration of the next load from the AGV's ``<id>/load`` binding."""
        return self._load_time()

    def sample_unload_time(self) -> float:
        """Draw the duration of the next unload from the AGV's ``<id>/unload`` binding."""
        return self._unload_time()

    @property
    def agv_id(self) -> str:
        """Alias of :attr:`id`."""
        return self.id

    @property
    def current_node(self) -> Node | None:
        """The node the AGV is at; during a segment, the node it left."""
        if self._pending_node is not None:
            pending(self, "current_node")
        return self._current_node

    @current_node.setter
    def current_node(self, node: Node | None) -> None:
        previous = self._current_node
        if node == previous:
            self._validate_node(node)  # a copy equal by value is refused too
            self._current_node = node
            return
        left, entered = self._relocate(node)  # validates the node before changing anything
        headed = self.heading is not None
        self.heading = None  # placed, not moved: the direction is unknown
        env = self.env
        if env.wants(AgvPlaced):
            build = self._node_deltas(node, left, entered)
            if headed:
                build.set(self.id, "heading", None)
            env.emit(
                AgvPlaced(
                    agv=self.id,
                    node=None if node is None else node.id,
                    previous=None if previous is None else previous.id,
                    deltas=build.done(),
                )
            )

    def _relocate(self, node: Node | None) -> tuple[NodeBinding | None, NodeBinding | None]:
        """Set the current node and update the ``agvs`` lists of the bound nodes.

        Returns the binding the AGV was removed from and the binding it was appended to (None when unchanged). Both
        nodes are canonical nodes of the layout's network, which binds every one of them at preparation.
        """
        self._validate_node(node)
        previous = self._current_node
        self._current_node = node
        entities = self.env.entities
        left = entered = None
        if previous is not None:
            left = entities.node_binding(previous)
            assert left is not None
            left.agvs.remove(self.id)
        if node is not None:
            entered = entities.node_binding(node)
            assert entered is not None
            entered.agvs.append(self.id)
        return left, entered

    def _validate_node(self, node: Node | None) -> None:
        if self._pending_node is not None:
            pending(self, "current_node", "assigning")
        if node is None:
            return
        layout = self.env._layout
        # The canonical node of the network passes on one identity check, the common case of every move; anything else
        # (a copy equal by value, a disabled node, a node of another network, a handle, which has no id) is the
        # layout's to refuse.
        try:
            if layout is not None and layout._canonical_nodes.get(node.id) is node:
                return
        except LayoutNotReady:
            pass
        self.env.layout._validate_node(node)

    def _node_deltas(self, node: Node | None, left: NodeBinding | None, entered: NodeBinding | None) -> DeltaBuilder:
        """Deltas of a :meth:`_relocate` to `node`: the AGV's ``node`` and the ``agvs`` lists it changed."""
        build = Deltas.build().set(self.id, "node", None if node is None else node.id)
        if left is not None:
            build.remove(left.id, "agvs", self.id)
        if entered is not None:
            build.insert(entered.id, "agvs", len(entered.agvs) - 1, self.id)
        return build

    def snapshot(self) -> dict[str, Any]:
        """Current entity state; nodes, SKUs, the order and the fleet are referenced by id."""
        node, load, order, fleet = self._current_node, self.current_load, self.order, self.fleet
        return {
            "node": None if node is None else node.id,
            "state": self._state.name,
            "battery": wire_float(self.battery.level),
            "load": None if load is None else {sku.id: quantity for sku, quantity in load.items()},
            "order": None if order is None else order.id,
            "motion": self.motion,
            "fleet": None if fleet is None else fleet.id,
            "heading": None if self.heading is None else wire_float(self.heading),
            "label": self.label,
        }

    @property
    def state(self) -> AGVState:
        return self._state

    def transition_to(self, new_state: AGVState) -> None:
        """Enter `new_state` (the only writer of the state) and emit ``agv.state_changed``."""
        env = self.env
        old_state = self._state
        if self.agv_type.idle_power:  # time spent charging does not drain: account up to the charge, resume after it
            if new_state is AGVState.CHARGING:
                self.accrue_idle_power()
            elif old_state is AGVState.CHARGING:
                self._accrued_at = env.now
        elapsed = env.now - self._state_entered_at
        self.state_durations[old_state] += elapsed
        self._state = new_state
        self._state_entered_at = env.now
        if env.wants(AgvStateChanged):
            env.emit(
                AgvStateChanged(
                    agv=self.id,
                    state=new_state.name,
                    previous=old_state.name,
                    deltas=Deltas.build().set(self.id, "state", new_state.name).done(),
                )
            )

    def accrue_idle_power(self) -> None:
        """Bring the battery level up to date with the idle power drained since it was last accounted (spec §9.5).

        Called at each event of the AGV that reads or changes the level: the energy check of a travel, the start of a
        charge, the check after a mission. It drains ``idle_power * (now - accounted)`` and emits
        ``agv.battery_changed`` when the level changes; the level is updated whether or not anything observes it. Time
        spent ``CHARGING`` does not drain: the accounting moves to now. During a move nothing happens: the move folds
        its drain into ``agv.move_ended`` (:meth:`fold_idle_power`). Without idle power it does nothing.
        """
        power = self.agv_type.idle_power
        if not power or self.motion is not None:
            return
        env = self.env
        now = env.now
        since, self._accrued_at = self._accrued_at, now
        if self._state is AGVState.CHARGING or now == since:
            return
        battery = self.battery
        before = battery.level
        battery.drain(power * (now - since))
        if battery.level != before and env.wants(AgvBatteryChanged):
            level = wire_float(battery.level)
            env.emit(
                AgvBatteryChanged(
                    agv=self.id, battery=level, deltas=Deltas.build().set(self.id, "battery", level).done()
                )
            )

    def battery_level_now(self) -> float:
        """The battery level now: ``battery.level``, the level as last accounted, less the idle power drained since,
        floored at 0, which :meth:`accrue_idle_power` would account now (spec §9.5).

        A pure read: it changes no state and emits nothing, so reports, dispatch strategies and low-battery callbacks
        may use it. While charging or moving it is ``battery.level`` (a move accounts its drain on arrival), and without
        idle power it is ``battery.level`` exactly.
        """
        power, level = self.agv_type.idle_power, self.battery.level
        if not power or self.motion is not None or self._state is AGVState.CHARGING:
            return level
        return max(0.0, level - power * (self.env.now - self._accrued_at))

    def fold_idle_power(self, duration: float) -> None:
        """Drain the idle power of a move of `duration`, which ends now, without an event: the caller emits
        ``agv.move_ended`` with the level after the arc's depletion and this drain (spec §9.5). The accounting was up to
        the start of the move; it moves to now. Without idle power it does nothing."""
        power = self.agv_type.idle_power
        if power:
            self.battery.drain(power * duration)
            self._accrued_at = self.env.now

    def skip_idle_power(self) -> None:
        """Declare the level up to date as of now without draining: the time since it was last accounted was spent
        charging (a station's recharge or swap used outside the ``CHARGING`` state)."""
        self._accrued_at = self.env.now

    def can_carry(self, sku: SKU, quantity: int) -> bool:
        if not self.agv_type.compatibility_fn(sku):
            return False
        total_weight = sku.weight * quantity
        total_volume = sku.volume * quantity
        return total_weight <= self.agv_type.weight_capacity and total_volume <= self.agv_type.volume_capacity

    def _durations_now(self) -> dict[AGVState, float]:
        """``state_durations`` plus the open interval of the current state, as a copy.

        Pure: reads ``env.now`` and never writes; only :meth:`transition_to` writes the accounting.
        """
        durations = dict(self.state_durations)
        durations[self._state] += self.env.now - self._state_entered_at
        return durations

    def utilization(self) -> float:
        durations = self._durations_now()
        total = math.fsum(durations.values())
        if total == 0:
            return 0.0
        utilized = math.fsum(durations[s] for s in _UTILIZED_STATES)
        return utilized / total

    def state_percentage(self, state: AGVState) -> float:
        durations = self._durations_now()
        total = math.fsum(durations.values())
        if total == 0:
            return 0.0
        return durations[state] / total

    def time_allocation(self) -> dict[AGVState, float]:
        durations = self._durations_now()
        total = math.fsum(durations.values())
        if total == 0:
            return {s: 0.0 for s in AGVState}
        return {s: durations[s] / total for s in AGVState}

    def __repr__(self) -> str:
        return f"AGV(id={self.agv_id!r}, state={self._state.name})"
