from __future__ import annotations

import abc
import random
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from simulatte.agv import AGV
from simulatte.exceptions.base import SimulationError
from simulatte.products import Product, ProductsGenerator
from simulatte.protocols.warehouse_store import WarehouseStoreProtocol
from simulatte.unitload.case_container import CaseContainer
from simulatte.utils.env_mixin import EnvMixin

if TYPE_CHECKING:
    from simulatte.policies.retrieval_policy import RetrievalPolicy
    from simulatte.policies.storing_policy import StoringPolicy
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class ProductStock(TypedDict):
    on_hand: int
    on_transit: int


Stock = dict[Product, dict[type[WarehouseStoreProtocol], ProductStock]]


class StoresConfig(TypedDict):
    cls: type[WarehouseStoreProtocol]
    n: int
    extra_capacity: float
    config: dict[str, Any]


class StoresControllerConfig(TypedDict):
    retrieval_policy: RetrievalPolicy
    storing_policy: StoringPolicy
    stores_config: list[StoresConfig]


class StoresController(abc.ABC, EnvMixin):
    """
    StoresController is responsible for managing the stores within a simulation.

    It is responsible for:
    - keeping track of the stock of each product
    - triggering replenishment requests when needed
    - finding locations for storing of products
    - loading and unloading of unit loads into the stores
    - keeping track of the on hand and on transit quantities of each product

    Requires:
    - a retrieval policy to be used to find the best unit load to output
    - a storing policy to be used to find the best locations for products' storage
    """

    def __init__(self, *, config: StoresControllerConfig) -> None:
        EnvMixin.__init__(self)

        self._retrieval_policy: RetrievalPolicy = config["retrieval_policy"]
        self._storing_policy: StoringPolicy = config["storing_policy"]
        self._stores: dict[type[WarehouseStoreProtocol], list] = {}
        self._stock: Stock = {}

    @property
    def stores(self) -> dict[type[WarehouseStoreProtocol], list]:
        """
        Return the stores managed by the StoresManager.
        """

        return self._stores

    @staticmethod
    def freeze(location: WarehouseLocation, unit_load: CaseContainer) -> None:
        """
        Freeze a location.

        Used to make sure that a unit load is not picked from another process.
        """

        location.freeze(unit_load=unit_load)

    def register_store(self, store: WarehouseStoreProtocol) -> None:
        """
        Register a store to be managed by the stores' controller.
        """

        stores = self._stores.setdefault(type(store), [])
        if store not in stores:
            stores.append(store)

    def update_stock(
        self,
        *,
        store_type: type[WarehouseStoreProtocol],
        inventory: Literal["on_hand", "on_transit"],
        product: Product,
        n_cases: int,
    ) -> None:
        """
        Keep track of the number of cases (on hand or on transit) of a product.

        Parameters:
            - product: the product to be updated
            - store_type: the type of store to be updated
            - inventory: the inventory to be updated
            - n_cases: the number of cases to be added or removed
        """

        product_stock = self._stock.setdefault(product, {}).setdefault(store_type, {"on_hand": 0, "on_transit": 0})
        product_stock[inventory] += n_cases

    def inventory_position(self, *, product: Product, store_type: type[WarehouseStoreProtocol]) -> int:
        """
        Return the inventory position of a product, filtered in pallets of trays.
        """

        product_stock = self._stock[product][store_type]
        on_hand = product_stock["on_hand"]
        on_transit = product_stock["on_transit"]

        return on_hand + on_transit

    @abc.abstractmethod
    def warmup(self, *, products_generator: ProductsGenerator) -> None:
        """
        Warmup warehouse(s) based on a products' generator.
        """

        ...

    def load(self, *, stores: list[WarehouseStoreProtocol], agv: AGV):
        """
        Orchestrate the loading of a unit load carried by an AGV into a store.

        Update the on hand and on transit quantities, accordingly.
        Finally, trigger the loading process of the store.
        """

        def store_sorter(store):
            product_locations = sum(location.product == agv.unit_load.product for location in store.locations)
            n_remaining_space = sum(
                location.is_empty and len(location.future_unit_loads) == 0 for location in store.locations
            )
            return store.input_agvs_queue, -n_remaining_space, product_locations, random.random()

        possible_locations = tuple(
            (store, location)
            for store in sorted(stores, key=store_sorter)
            if (
                location := self._storing_policy(
                    store=store, product=agv.unit_load.product, n_cases=agv.unit_load.n_cases
                )
            )
            is not None
        )
        if len(possible_locations) == 0:
            raise SimulationError(f"No location found for {agv.unit_load} in {stores}")

        store, location = possible_locations[0]

        # If no location is found, raise an error
        if location is None:
            raise SimulationError(f"No location found for {agv.unit_load} in {store} [n_cases={agv.unit_load.n_cases}]")

        # Book the location to prevent other non-compatible unit loads from being placed in the same location
        store.book_location(location=location, unit_load=agv.unit_load)

        # riduciamo l'on_transit
        self.update_stock(
            store_type=type(store),
            inventory="on_transit",
            product=agv.unit_load.product,
            n_cases=-agv.unit_load.n_cases,
        )

        # alziamo l'on_hand
        self.update_stock(
            store_type=type(store),
            inventory="on_hand",
            product=agv.unit_load.product,
            n_cases=agv.unit_load.n_cases,
        )
        return store, location

    def organize_retrieval(
        self, *, type_of_store: type[WarehouseStoreProtocol], product: Product, n_cases: int
    ) -> tuple[tuple[WarehouseStoreProtocol, WarehouseLocation, CaseContainer], ...]:
        """
        Used to organize the retrieval of unit load(s) from one or more stores.

        Delegates to the retrieval policy to find the best store(s) to retrieve the unit load(s) from.
        Updates the on hand and on transit quantities.

        Parameters:
            - type_of_store: the type of store to be retrieved from
            - product: the product to be retrieved
            - n_cases: the number of cases to be retrieved

        Returns:
            - a tuple of tuples containing the store, the location, and the unit load
        """

        # Get possible stores based on the type of store
        stores = self.stores[type_of_store]

        # Find stores, locations, and unit loads using the RetrievalPolicy
        stores_and_locations = self._retrieval_policy(stores=stores, product=product, quantity=n_cases)

        # Loop over the stores, locations, and unit loads
        for store, location, unit_load in stores_and_locations:
            # Book the unit load at the store's location to prevent other processes from picking it up
            location.book_pickup(unit_load=unit_load)

            # Update "on transit"
            # If the number of cases to be picked up is less than the number of cases in the unit load,
            # we pick up only the number of cases needed
            on_transit = min(unit_load.n_cases, n_cases)
            n_cases -= on_transit
            self.update_stock(store_type=type_of_store, inventory="on_transit", product=product, n_cases=on_transit)

            # Reduce "on hand"
            if not hasattr(unit_load, "magic"):
                self.update_stock(
                    store_type=type_of_store, inventory="on_hand", product=product, n_cases=-unit_load.n_cases
                )

        return stores_and_locations
