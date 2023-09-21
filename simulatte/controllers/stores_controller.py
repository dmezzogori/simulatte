from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from simulatte.exceptions.store import OutOfStockError
from simulatte.products import Product
from simulatte.unitload.case_container import CaseContainer
from simulatte.utils.utils import as_process

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV
    from simulatte.controllers import SystemController
    from simulatte.policies.retrieval_policy import RetrievalPolicy
    from simulatte.policies.storing_policy import StoringPolicy
    from simulatte.products import ProductsGenerator
    from simulatte.stores.warehouse_location import PhysicalPosition
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.stores.warehouse_store import WarehouseStore
    from simulatte.unitload.pallet import Pallet


class ProductStock(TypedDict):
    on_hand: int
    on_transit: int


Stock = dict[Product, dict[type[CaseContainer], ProductStock]]


class StoresConfig(TypedDict):
    cls: type[WarehouseStore]
    n: int
    extra_capacity: float
    config: dict[str, Any]


class StoresControllerConfig(TypedDict):
    retrieval_policy: RetrievalPolicy
    storing_policy: StoringPolicy
    stores_config: list[StoresConfig]


class BaseStoresController:
    """
    Base class for the StoresController.

    The StoresController is responsible for managing the stores of the controllers.
    It is responsible for:
    - keeping track of the stock of each product
    - triggering replenishment requests when needed
    - finding locations for storing of products
    - loading and unloading of unit loads into the stores
    - keeping track of the on hand and on transit quantities of each product

    Requires:
    - a unit load policy to be used to find the best unit load to be loaded into the store
    - a location policy to be used to find locations for storing of products
    """

    def __init__(self, *, config: StoresControllerConfig) -> None:
        self._retrieval_policy = config["retrieval_policy"]
        self._storing_policy = config["storing_policy"]

        self._stores: dict[type[WarehouseStore], list] = {}
        self._stock: Stock = {}
        self.system: SystemController | None = None

    def register_system(self, system: SystemController):
        """
        Register the controllers to which the stores' controller belongs.
        """

        self.system = system

    def register_store(self, store: WarehouseStore):
        """
        Register a store to be managed by the stores' controller.
        """

        stores = self._stores.setdefault(type(store), [])
        if store not in stores:
            stores.append(store)

    @property
    def stores(self):
        """
        Return the stores managed by the StoresManager.
        """

        return self._stores

    @staticmethod
    def freeze(location: WarehouseLocation, unit_load: Pallet):
        """
        Freeze a location.

        This method is used to make sure that a unit load is not picked from another process.
        """

        return location.freeze(unit_load=unit_load)

    def update_stock(
        self,
        *,
        product: Product,
        case_container: type[CaseContainer],
        inventory: Literal["on_hand", "on_transit"],
        n_cases: int,
    ) -> None:
        """
        Keep track of the number of cases (on hand or on transit) of a product.
        """

        product_stock = self._stock.setdefault(product, {}).setdefault(case_container, {"on_hand": 0, "on_transit": 0})
        product_stock[inventory] += n_cases

    def inventory_position(self, *, product: Product, case_container: type[CaseContainer]) -> int:
        """
        Return the inventory position of a product, filtered in pallets of trays.
        """

        product_stock = self._stock[product][case_container]
        on_hand = product_stock["on_hand"]
        on_transit = product_stock["on_transit"]

        return on_hand + on_transit

    def load(self, *, store: WarehouseStore, agv: AGV) -> None:
        """
        Used to centralize the loading of unit loads into the stores.
        Needed to keep trace of the on hand quantity of each product,
        to trigger replenishment when needed.

        This method must be called when the agv is in front of the store, waiting to be
        unloaded by the store.

        It triggers the loading process of the store.
        """

        raise NotImplementedError

    def unload(
        self, *, type_of_stores: type[WarehouseStore], product: Product, n_cases: int
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, PhysicalPosition], ...]:
        """
        Used to centralize the unloading of unitloads from the stores.
        Needed to keep trace of the on hand quantity of each product.
        It does not trigger replenishment operations.

        This method should be called when the controllers is organizing the feeding operation.
        It does NOT trigger the unloading process of the store.
        """

        raise NotImplementedError

    def check_replenishment(
        self,
        *,
        product: Product,
        case_container: type[CaseContainer],
        periodic_check=False,
    ):
        """
        Checks if there is need for replenishment operations.
        Used both in the unload method and in the
        periodic replenishment process.
        """

        inventory_position = self.inventory_position(product=product, case_container=case_container)
        s_max = product.s_max[case_container]
        s_min = product.s_min[case_container]

        if periodic_check or inventory_position <= s_min:
            # calcoliamo quanti cases ci servono per arrivare a S_max
            n_cases = s_max - inventory_position
            n_cases = max(0, n_cases)
            n_pallet = math.ceil(n_cases / product.case_per_pallet)

            # aumentiamo l'on_transit
            self.update_stock(
                product=product,
                case_container=case_container,
                inventory="on_transit",
                n_cases=n_pallet * product.case_per_pallet,
            )

            for _ in range(n_pallet):
                self.system.store_replenishment(
                    product=product,
                    case_container=case_container,
                )

    @as_process
    def periodic_store_replenishment(self):
        """
        Periodically checks if there is need for replenishment operations.
        """
        while True:
            yield self.system.env.timeout(60 * 60 * 8)  # TODO: mettere come parametro

            for product in self.system.products:
                for case_container in ("pallet", "tray"):
                    self.check_replenishment(
                        product=product,
                        case_container=case_container,
                        periodic_check=True,
                    )

    def find_location_for_product(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation:
        return self._storing_policy(store=store, product=product)

    def get_location_for_unit_load(self, *, store: WarehouseStore, unit_load: Pallet) -> WarehouseLocation:
        """
        FOR INPUT.

        Find a location for a unit load in a store.
        Find the location accordingly to the LocationPolicy set.
        Then freeze the location to prevent other unit loads from
        being placed in the same location.
        """

        location = self.find_location_for_product(store=store, product=unit_load.product)
        store.book_location(location=location, unit_load=unit_load)
        return location

    def find_stores_locations_for_output(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,
        raise_on_none: bool = False,
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, Pallet], ...]:
        """
        FOR OUTPUT.

        Get a tuple of stores and locations from which to pickup a product.
        """

        try:
            stores_and_locations = self._retrieval_policy(stores=stores, product=product, quantity=quantity)
            return stores_and_locations
        except OutOfStockError as e:
            if raise_on_none:
                raise e

    def warmup(
        self,
        *,
        products_generator: ProductsGenerator,
        **kwargs,
    ):
        """
        Warmup the warehouse with a given products' generator.
        """

        raise NotImplementedError
