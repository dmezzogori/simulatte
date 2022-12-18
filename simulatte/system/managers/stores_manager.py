from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.products import Product
from simulatte.stores import WarehouseStore
from simulatte.system.policies import LocationPolicy, UnitLoadPolicy
from simulatte.unitload import Pallet

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class StoresManager:
    def __init__(self, *, unit_load_policy: UnitLoadPolicy, location_policy: LocationPolicy):
        self._stores: list[WarehouseStore] = []
        self._unit_load_policy = unit_load_policy
        self._location_policy = location_policy

    def __call__(self, store: WarehouseStore) -> None:
        """
        Register a store to be managed by the StoresManager.
        """
        self._stores.append(store)

    def __getattr__(self, item) -> WarehouseStore | None:
        try:
            return next(store for store in self._stores if item in store.name.lower())
        except StopIteration:
            raise AttributeError(f"Store {item} not found.")

    @property
    def stores(self) -> list[WarehouseStore]:
        return self._stores

    @staticmethod
    def freeze(location: WarehouseLocation, unit_load: Pallet) -> None:
        location.freeze(unit_load=unit_load)

    def find_location_for_product(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation:
        return self._location_policy(store=store, product=product)

    def get_location_for_unit_load(self, *, store: WarehouseStore, unit_load: Pallet) -> WarehouseLocation:
        """
        Find a location for a unit load in a store.
        Find the location accordingly to the LocationPolicy set.
        Then freeze the location to prevent other unit loads from
        being placed in the same location.
        """
        location = self.find_location_for_product(store=store, product=unit_load.product)
        location.freeze(unit_load=unit_load)
        return location

    def get_unit_load(
        self,
        *,
        store: WarehouseStore,
        product: Product,
        quantity: int,
        raise_on_none: bool = False,
    ) -> WarehouseLocation | None:
        """
        Get a unit load from a store.
        Get the unit load accordingly to the UnitLoadPolicy set.
        """
        location = self._unit_load_policy(store=store, product=product, quantity=quantity)
        if location is None and raise_on_none:
            raise ValueError(f"Location not found for product {product}.")
        return location

    @staticmethod
    def need_replenishment(*, store: WarehouseStore, product: Product) -> bool:
        on_hand = store.on_hand(product=product)
        on_order = store.on_order(product=product)
        return on_hand.n_layers + on_order.n_layers < product.reorder_level
