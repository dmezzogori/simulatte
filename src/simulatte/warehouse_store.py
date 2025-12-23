"""WarehouseStore - Server specialization for warehouse operations with inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from simulatte.environment import Environment
from simulatte.server import Server

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class WarehouseStore(Server):
    """A warehouse server with inventory containers for pick/put operations.

    WarehouseStore extends Server to add inventory management. Each product has
    its own simpy.Container that tracks available quantity. Pick operations block
    until sufficient inventory is available, implementing natural backpressure.

    Attributes:
        inventory: Dict mapping product names to simpy.Containers.
        pick_time_fn: Callable returning pick operation time.
        put_time_fn: Callable returning put operation time.
        total_picks: Total number of pick operations completed.
        total_puts: Total number of put operations completed.
    """

    def __init__(
        self,
        *,
        env: Environment,
        n_bays: int,
        products: list[str],
        initial_inventory: dict[str, int] | None = None,
        pick_time_fn: Callable[[], float],
        put_time_fn: Callable[[], float],
        shopfloor: ShopFloor | None = None,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> None:
        """Initialize a WarehouseStore.

        Args:
            env: The simulation environment.
            n_bays: Number of concurrent pick/put operations (capacity).
            products: List of product names to track.
            initial_inventory: Optional initial quantities per product.
            pick_time_fn: Callable returning time for a pick operation.
            put_time_fn: Callable returning time for a put operation.
            shopfloor: Optional shopfloor to register with.
        """
        super().__init__(
            env=env,
            capacity=n_bays,
            shopfloor=shopfloor,
            collect_time_series=collect_time_series,
            retain_job_history=retain_job_history,
        )

        self.pick_time_fn = pick_time_fn
        self.put_time_fn = put_time_fn

        # Create inventory containers for each product
        initial = initial_inventory or {}
        self.inventory: dict[str, simpy.Container] = {
            product: simpy.Container(env, capacity=float("inf"), init=initial.get(product, 0)) for product in products
        }

        # Metrics
        self.total_picks: int = 0
        self.total_puts: int = 0
        self._total_pick_time: float = 0
        self._total_put_time: float = 0

    def __repr__(self) -> str:
        return f"WarehouseStore(id={self._idx}, bays={self.capacity})"

    @property
    def products(self) -> list[str]:
        """List of products tracked by this warehouse."""
        return list(self.inventory.keys())

    def get_inventory_level(self, product: str) -> float:
        """Get current inventory level for a product."""
        if product not in self.inventory:
            raise KeyError(f"Unknown product: {product}")
        return self.inventory[product].level

    def pick_inventory(self, product: str, quantity: int) -> ProcessGenerator:
        """Pick items from inventory.

        This operation blocks until sufficient inventory is available,
        then takes the specified pick time.

        Args:
            product: Product name to pick.
            quantity: Number of items to pick.

        Yields:
            SimPy events for inventory get and timeout.
        """
        if product not in self.inventory:
            raise KeyError(f"Unknown product: {product}")

        # Wait for inventory (blocks if insufficient)
        yield self.inventory[product].get(quantity)

        # Simulate pick time
        pick_time = self.pick_time_fn()
        yield self.env.timeout(pick_time)

        self.total_picks += 1
        self._total_pick_time += pick_time
        self.worked_time += pick_time

    def put_inventory(self, product: str, quantity: int) -> ProcessGenerator:
        """Put items into inventory.

        Args:
            product: Product name to put.
            quantity: Number of items to put.

        Yields:
            SimPy events for inventory put and timeout.
        """
        if product not in self.inventory:
            raise KeyError(f"Unknown product: {product}")

        # Simulate put time
        put_time = self.put_time_fn()
        yield self.env.timeout(put_time)

        # Add to inventory
        yield self.inventory[product].put(quantity)

        self.total_puts += 1
        self._total_put_time += put_time
        self.worked_time += put_time

    @property
    def average_pick_time(self) -> float:
        """Average time per pick operation."""
        if self.total_picks == 0:
            return 0.0
        return self._total_pick_time / self.total_picks

    @property
    def average_put_time(self) -> float:
        """Average time per put operation."""
        if self.total_puts == 0:
            return 0.0
        return self._total_put_time / self.total_puts
