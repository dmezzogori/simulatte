from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class LocationPolicy:
    def __call__(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation | None:
        raise NotImplementedError


class ClosestLocationPolicy(LocationPolicy):
    @staticmethod
    def sorter(product: Product) -> callable:
        def inner(location: WarehouseLocation) -> tuple[int, int, int]:

            if len(location.future_unit_loads) + location.n_unit_loads == 2:
                return float("inf"), float("inf"), float("inf")

            product_score = float("inf")
            if location.product == product:
                product_score = 0
            elif location.product is None:
                product_score = 1
            score = (
                product_score,
                -len(location.future_unit_loads),
                -location.n_unit_loads,
            )
            return score

        return inner

    def __call__(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation | None:

        locations = (location for location in store.locations)

        sorted_locations = sorted(
            locations,
            # key=self.sorter(product),
            key=lambda location: location.affinity(product=product),
        )
        if sorted_locations:
            selected_location = sorted_locations[0]
            return selected_location
