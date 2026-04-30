from __future__ import annotations

from typing import TYPE_CHECKING

import simpy
from simpy.events import ProcessGenerator

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import Node


class ChargingStation:
    """Models a battery charging/swapping facility placed on a graph node.

    AGVs navigate to this station when their battery is low. The station
    provides concurrent charging slots (modeled as a ``simpy.Resource``) and
    optionally supports battery swapping via a finite pool of pre-charged
    batteries (modeled as a ``simpy.Container``).
    """

    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        n_slots: int,
        recharge_fn: Callable[[float, float], float] | None = None,
        supports_swap: bool = False,
        swap_pool_size: int = 0,
        swap_time: float = 0.0,
        swap_recharge_time: float = 0.0,
    ) -> None:
        self.env = env
        self.name = name
        self.node = node
        self._recharge_fn = recharge_fn
        self.supports_swap = supports_swap
        self.swap_time = swap_time
        self.swap_recharge_time = swap_recharge_time

        self._slots = simpy.Resource(env, capacity=n_slots)

        self._swap_pool: simpy.Container | None = None
        if supports_swap and swap_pool_size > 0:
            self._swap_pool = simpy.Container(env, capacity=swap_pool_size, init=swap_pool_size)

        # Metrics
        self.total_recharges: int = 0
        self.total_swaps: int = 0
        self.total_occupied_time: float = 0.0

    def recharge(self, agv: AGV, target_pct: float = 1.0) -> ProcessGenerator:
        """Acquire a slot, recharge the AGV battery, and release the slot.

        Uses the station's ``recharge_fn`` if set, otherwise falls back to the
        AGV battery's own ``recharge_time`` method.
        """
        self.env.debug(
            f"[{self.name}] Recharge start for {agv.agv_id}",
            component="ChargingStation",
        )

        req = self._slots.request()
        yield req

        try:
            target_level = target_pct * agv.battery.capacity

            if target_level <= agv.battery.level:
                duration = 0.0
            elif self._recharge_fn is not None:
                duration = self._recharge_fn(agv.battery.level, target_level)
            else:
                duration = agv.battery.recharge_time(target_pct)

            start = self.env.now
            yield self.env.timeout(duration)

            # Restore battery level
            recharge_amount = target_level - agv.battery.level
            if recharge_amount > 0:
                agv.battery.recharge(recharge_amount)

            occupied = self.env.now - start
            self.total_occupied_time += occupied
            self.total_recharges += 1

            self.env.debug(
                f"[{self.name}] Recharge complete for {agv.agv_id} "
                f"(duration={duration:.2f}, level={agv.battery.level_pct:.1%})",
                component="ChargingStation",
            )
        finally:
            self._slots.release(req)

    def swap(self, agv: AGV) -> ProcessGenerator:
        """Swap the AGV's depleted battery with a pre-charged one from the pool.

        Raises ``RuntimeError`` if this station does not support swapping.
        """
        if not self.supports_swap:
            raise RuntimeError("Swap not supported by this station")

        self.env.debug(
            f"[{self.name}] Swap start for {agv.agv_id}",
            component="ChargingStation",
        )

        req = self._slots.request()
        yield req

        try:
            start = self.env.now

            # Wait for a battery to be available in the pool
            if self._swap_pool is not None:
                yield self._swap_pool.get(1)

            # Perform the swap (near-instant, takes swap_time)
            yield self.env.timeout(self.swap_time)

            # Set AGV battery to full
            agv.battery.level = agv.battery.capacity

            occupied = self.env.now - start
            self.total_occupied_time += occupied
            self.total_swaps += 1

            self.env.debug(
                f"[{self.name}] Swap complete for {agv.agv_id} "
                f"(swap_time={self.swap_time:.2f})",
                component="ChargingStation",
            )

            # Kick off background recharge of the depleted battery
            if self._swap_pool is not None:
                self.env.process(self._replenish_pool())
        finally:
            self._slots.release(req)

    def _replenish_pool(self) -> ProcessGenerator:
        """Background process: recharge a depleted battery and return it to the pool."""
        yield self.env.timeout(self.swap_recharge_time)
        if self._swap_pool is not None:
            yield self._swap_pool.put(1)
