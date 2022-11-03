from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location import WarehouseLocation


class UnitLoadPolicy:
    def __call__(
        self,
        *,
        store: WarehouseStore,
        product: Product,
        quantity: int,
    ) -> WarehouseLocation | None:
        raise NotImplementedError


class ClosestUnitLoadPolicy(UnitLoadPolicy):
    def __call__(
        self,
        *,
        store: WarehouseStore,
        product: Product,
        quantity: int,
    ) -> WarehouseLocation | None:

        locs = sorted(
            (location for location in store.locations if not location.is_empty),
            key=lambda l: (l.n_unit_loads, l.first_available_unit_load.n_cases),
        )

        for location in locs:
            if not location.frozen and location.product == product:
                if location.first_available_unit_load.n_cases >= quantity:
                    return location
        return None
