from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from simpy.events import ProcessGenerator
from simpy.resources.resource import Request

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas
from simulatte.intralogistics._resources import NotifyingContainer, NotifyingResource
from simulatte.intralogistics.events import AgvBatteryChanged, ChargingEnded, ChargingPoolChanged, ChargingStarted

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.resources.container import ContainerAmount

    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import Node
    from simulatte.rng import SamplerDescription


class _SlotRequest(Request):
    """A slot request of a charging station that names the AGV and the mode (``"recharge"`` or ``"swap"``).

    Both are set before SimPy's constructor runs, because a free slot is granted inside it.
    """

    def __init__(self, resource: NotifyingResource, agv: AGV, mode: str) -> None:
        self.agv = agv
        self.mode = mode
        super().__init__(resource)


class ChargingStation(Entity, kind="charging_station"):
    """Models a battery charging/swapping facility placed on a graph node.

    AGVs navigate to this station when their battery is low. The station
    provides concurrent charging slots (modeled as a ``simpy.Resource``) and
    optionally supports battery swapping via a finite pool of pre-charged
    batteries (modeled as a ``simpy.Container``). Its id is ``name``.

    Events (spec §6.4): ``charging.started`` when an AGV is granted a slot and ``charging.ended`` when it releases
    it, ``charging.pool_changed`` whenever the swap pool changes, and ``agv.battery_changed`` after a recharge or a
    swap.

    ``recharge_time`` is a distribution description or a number (managed) or a callable
    ``(current_level, target_level) -> float`` (opaque), bound to the stream ``<name>/recharge``; without it the
    recharge time is computed from the AGV battery's charging rate.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {"slots_in_use": FieldSpec("int"), "swap_pool": FieldSpec("float", nullable=True)}
    )

    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        n_slots: int,
        recharge_time: SamplerDescription[float] | float | Callable[[float, float], float] | None = None,
        supports_swap: bool = False,
        swap_pool_size: int = 0,
        swap_time: float = 0.0,
        swap_recharge_time: float = 0.0,
        label: str | None = None,
    ) -> None:
        self.env = env
        self.name = name
        self.node = node
        self.supports_swap = supports_swap
        self.swap_time = swap_time
        self.swap_recharge_time = swap_recharge_time

        self._slots = NotifyingResource(env, capacity=n_slots, on_change=self._slot_changed)

        self._swap_pool: NotifyingContainer | None = None
        if supports_swap:
            self._swap_pool = NotifyingContainer(
                env, capacity=max(swap_pool_size, 1), init=swap_pool_size, on_change=self._pool_changed
            )

        # Metrics
        self.total_recharges: int = 0
        self.total_swaps: int = 0
        self.total_occupied_time: float = 0.0
        env.entities.attach(self, name=name, label=label)
        self._recharge_time: Callable[..., float] | None = (
            None
            if recharge_time is None
            else env.bind(recharge_time, kind="contextual", stream=f"{self.id}/recharge", owner=self.id)
        )

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: slots in use and the swap-pool level (None without battery swapping)."""
        pool = self._swap_pool
        return {
            "slots_in_use": self._slots.count,
            "swap_pool": None if pool is None else float(pool.level),
            "label": self.label,
        }

    def recharge(self, agv: AGV, target_pct: float = 1.0) -> ProcessGenerator:
        """Acquire a slot, recharge the AGV battery, and release the slot.

        Uses the station's ``recharge_time`` if set, otherwise falls back to the
        AGV battery's own ``recharge_time`` method.
        """
        req = _SlotRequest(self._slots, agv, "recharge")
        yield req

        try:
            target_level = target_pct * agv.battery.capacity

            if target_level <= agv.battery.level:
                duration = 0.0
            elif self._recharge_time is not None:
                duration = self._recharge_time(agv.battery.level, target_level)
            else:
                duration = agv.battery.recharge_time(target_pct)

            start = self.env.now
            yield self.env.timeout(duration)

            # Restore battery level
            recharge_amount = target_level - agv.battery.level
            if recharge_amount > 0:
                agv.battery.recharge(recharge_amount)
                self._battery_changed(agv)

            occupied = self.env.now - start
            self.total_occupied_time += occupied
            self.total_recharges += 1
        finally:
            self._slots.release(req)

    def swap(self, agv: AGV) -> ProcessGenerator:
        """Swap the AGV's depleted battery with a pre-charged one from the pool.

        Raises ``RuntimeError`` if this station does not support swapping.
        """
        if not self.supports_swap:
            raise RuntimeError("Swap not supported by this station")

        req = _SlotRequest(self._slots, agv, "swap")
        yield req

        try:
            start = self.env.now

            # Always wait for a battery to be available in the pool
            assert self._swap_pool is not None
            yield self._swap_pool.get(1)

            # Perform the swap (near-instant, takes swap_time)
            yield self.env.timeout(self.swap_time)

            # Set AGV battery to full
            agv.battery.level = agv.battery.capacity
            self._battery_changed(agv)

            occupied = self.env.now - start
            self.total_occupied_time += occupied
            self.total_swaps += 1

            # Kick off background recharge of the depleted battery
            self.env.process(self._replenish_pool())
        finally:
            self._slots.release(req)

    def _battery_changed(self, agv: AGV) -> None:
        """Emit ``agv.battery_changed`` after a recharge or a swap changed the AGV's battery level."""
        env = self.env
        if env.wants(AgvBatteryChanged):
            level = float(agv.battery.level)
            env.emit(
                AgvBatteryChanged(agv=agv.id, battery=level, deltas=Deltas.build().set(agv.id, "battery", level).done())
            )

    def _slot_changed(self, request: Request, granted: bool) -> None:
        """Emit ``charging.started`` after a slot was granted and ``charging.ended`` after it was released."""
        env = self.env
        cls = ChargingStarted if granted else ChargingEnded
        if env.wants(cls):
            slot = cast("_SlotRequest", request)
            in_use = self._slots.count
            env.emit(
                cls(
                    station=self.id,
                    agv=slot.agv.id,
                    mode=slot.mode,
                    deltas=Deltas.build().set(self.id, "slots_in_use", in_use).done(),
                )
            )

    def _pool_changed(self, pool: NotifyingContainer, amount: ContainerAmount) -> None:
        """Emit ``charging.pool_changed`` after a swap took a battery or ``_replenish_pool`` returned one."""
        env = self.env
        if env.wants(ChargingPoolChanged):
            level = float(pool.level)
            env.emit(
                ChargingPoolChanged(
                    station=self.id, swap_pool=level, deltas=Deltas.build().set(self.id, "swap_pool", level).done()
                )
            )

    def _replenish_pool(self) -> ProcessGenerator:
        """Background process: recharge a depleted battery and return it to the pool."""
        yield self.env.timeout(self.swap_recharge_time)
        assert self._swap_pool is not None
        yield self._swap_pool.put(1)
