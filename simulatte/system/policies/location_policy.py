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
    def __call__(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation | None:
        locations = (location for location in store.locations)

        sorted_locations = sorted(
            locations,
            key=lambda location: location.affinity(product=product),
        )
        if sorted_locations:
            selected_location = sorted_locations[0]
            return selected_location
