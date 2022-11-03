from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location import WarehouseLocation


class LocationPolicy:
    def __call__(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation | None:
        raise NotImplementedError


class ClosestLocationPolicy(LocationPolicy):
    @staticmethod
    def sorter(product: Product) -> callable:
        def inner(location: WarehouseLocation) -> tuple[int, int, int]:
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
        sorted_locations = sorted(store.locations, key=self.sorter(product))
        if sorted_locations:
            return sorted_locations[0]
