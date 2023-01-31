from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class LocationPolicy:
    def __call__(
        self, *, store: WarehouseStore, product: Product
    ) -> WarehouseLocation | None:
        raise NotImplementedError


class ClosestLocationPolicy(LocationPolicy):
    def __call__(
        self, *, store: WarehouseStore, product: Product
    ) -> WarehouseLocation | None:

        possible_locations = [
            location
            for location in store.locations
            if location.product is None  # vanno bene locazioni completamente vuote
            or location.product
            == product  # consideriamo solo locazioni che gestiscono o gestiranno il prodotto
            # consideriamo solo le locazioni che hanno disponibilità (fisica o futura)
            and location.n_unit_loads + len(location.future_unit_loads) < location.depth
        ]

        aff = [location.affinity(product=product) for location in possible_locations]
        if all(a == float("inf") for a in aff):
            raise ValueError

        sorted_locations = sorted(
            possible_locations,
            key=lambda location: location.affinity(product=product),
        )
        if sorted_locations:
            selected_location = sorted_locations[0]
            return selected_location
