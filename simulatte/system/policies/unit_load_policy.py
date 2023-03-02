from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions import OutOfStockError
from simulatte.products import Product
from simulatte.stores import WarehouseStore
from simulatte.stores.warehouse_location import PhysicalPosition

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class UnitLoadPolicy:
    def __call__(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, PhysicalPosition],...]:
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


class MultiStoreLocationPolicy(UnitLoadPolicy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = {
            "best_case": 0,
            "first_aggregation": 0,
            "second_aggregation": 0,
            "magic": 0,
        }

    def __call__(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,  # [cases]
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, PhysicalPosition],...]:

        # individuiamo le location di tutti gli store che contengono il prodotto richiesto
        # e che non sono completamente prenotate
        locations = [
            location
            for store in stores
            for location in store.locations
            if not location.is_empty and not location.fully_booked and location.product == product
        ]

        # separiamo le unit load che sono in posizione seconda e quelle che sono in posizione prima
        unit_loads_from_half_empty_locations = [
            location.second_position.unit_load
            for location in locations
            if location.is_half_full
            and location.second_position.unit_load is not None
            and location.second_position.unit_load not in location.booked_pickups
        ]
        unit_loads_from_full_locations = [
            location.first_position.unit_load
            if location.first_position.unit_load is not None
            and location.first_position.unit_load not in location.booked_pickups
            else location.second_position.unit_load
            for location in locations
            if location.is_full
        ]

        unit_loads = sorted(
            unit_loads_from_half_empty_locations + unit_loads_from_full_locations,
            key=lambda u: u.n_cases,
        )

        def returner(unit_load) -> tuple[WarehouseStore, WarehouseLocation, PhysicalPosition]:
            position = (
                unit_load.location.first_position
                if unit_load == unit_load.location.first_position.unit_load
                else unit_load.location.second_position
            )
            if unit_load in unit_load.location.booked_pickups:
                raise ValueError("unit load is booked for pickup")
            return (unit_load.location.store, unit_load.location, position)

        # best case: ritorniamo la prima unit load che soddisfa esattamente la quantità richiesta
        for unit_load in unit_loads:
            if unit_load.upper_layer.n_cases == quantity:
                self.counter["best_case"] += 1
                return (returner(unit_load),)

        first_unit_load = unit_loads[0]
        if first_unit_load.n_cases >= quantity:
            return (returner(first_unit_load),)

        second_unit_load = None
        for other_unit_load in unit_loads[1:]:
            if first_unit_load.n_cases + other_unit_load.n_cases == quantity:
                second_unit_load = other_unit_load
                self.counter["first_aggregation"] += 1
                break
        else:
            last_unit_load = unit_loads[-1]
            if first_unit_load.n_cases + last_unit_load.n_cases > quantity:
                self.counter["second_aggregation"] += 1
                second_unit_load = last_unit_load

        if second_unit_load is not None:
            return returner(first_unit_load), returner(second_unit_load)

        raise OutOfStockError(f"no unit load found for {quantity} cases of {product}")
