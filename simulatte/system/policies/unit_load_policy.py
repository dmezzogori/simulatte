from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simulatte.exceptions import OutOfStockError
from simulatte.products import Product
from simulatte.stores import WarehouseStore
from simulatte.stores.warehouse_location import PhysicalPosition
from simulatte.unitload import Pallet, Tray

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class UnitLoadPolicy:
    def __call__(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, Pallet],...]:
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
            "magic_oos": 0,
            "magic_poos": 0,
        }

    def __call__(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,  # [cases]
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, Pallet],...]:

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
            and not hasattr(location.second_position.unit_load, 'magic')
        ]
        unit_loads_from_full_locations = [
            location.first_position.unit_load
            if location.first_position.unit_load is not None
            and location.first_position.unit_load not in location.booked_pickups
            else location.second_position.unit_load
            for location in locations
            if location.is_full
            and all(not hasattr(position.unit_load, 'magic') for position in (location.first_position, location.second_position))
        ]

        unit_loads = sorted(
            unit_loads_from_half_empty_locations + unit_loads_from_full_locations,
            key=lambda u: u.n_cases,
        )

        def returner(ul) -> tuple[WarehouseStore, WarehouseLocation, Pallet]:
            if ul in ul.location.booked_pickups:
                raise ValueError("unit load is booked for pickup")
            return (ul.location.store, ul.location, ul)

        # best case: ritorniamo la prima unit load che soddisfa esattamente la quantità richiesta
        for unit_load in unit_loads:
            if unit_load.upper_layer.n_cases == quantity:
                self.counter["best_case"] += 1
                return (returner(unit_load),)

        try:
            try:
                first_unit_load = unit_loads[0]
            except IndexError:
                self.counter['magic_oos'] += 1
                raise OutOfStockError(f"no unit load found for {quantity} cases of {product}")
            if first_unit_load.n_cases >= quantity:
                return (returner(first_unit_load),)

            if len(unit_loads) == 1:
                self.counter["magic_poos"] += 1
                raise OutOfStockError(f"no unit load found for {quantity} cases of {product}")

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

            self.counter["magic_poos"] += 1
            raise OutOfStockError(f"no unit load found for {quantity} cases of {product}")

        except OutOfStockError:
            type_of_store = stores[0].__class__.__name__
            if 'AVSRS' in type_of_store:
                unit_load = Pallet(Tray(product=product, n_cases=quantity))
            else:
                n_layers = quantity // product.cases_per_layer
                unit_load = Pallet(*[Tray(product=product, n_cases=product.cases_per_layer) for _ in range(n_layers)])
            unit_load.magic = True
            store = stores[0]
            location = store.first_available_location()
            position = location.second_position
            unit_load.location = location
            position.put(unit_load=unit_load)
            return (returner(unit_load),)
