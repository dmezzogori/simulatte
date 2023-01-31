from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore
from simulatte.stores.warehouse_location import PhysicalPosition

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


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
        quantity: int,  # [cases]
    ) -> tuple[WarehouseLocation, PhysicalPosition] | tuple[None, None]:

        locs = sorted(
            (
                location
                for location in store.locations
                if not location.is_empty and not location.fully_booked and location.product == product
            ),
            key=lambda l: (l.n_unit_loads, l.first_available_unit_load.n_cases),
        )

        for location in locs:
            first_position = location.first_position
            unit_load = first_position.unit_load
            if unit_load is not None:  # c'è qualcosa in prima locazione
                if unit_load not in location.booked_pickups:
                    if first_position.n_cases >= quantity:
                        return location, first_position
                else:
                    # se la unit_load nella prima posizione è stata prenotata
                    # non si può accedere alla seconda posizione
                    # quindi la locazione non viene più considerata
                    continue
            else:
                second_position = location.second_position
                unit_load = second_position.unit_load
                if unit_load is not None and unit_load not in location.booked_pickups:
                    if second_position.n_cases >= quantity:
                        return location, second_position

        return None, None
