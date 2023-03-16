from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal, cast, Type

import simulatte
from simulatte.ant import Ant
from simulatte.exceptions import OutOfStockError
from simulatte.products import Product, ProductsGenerator
from simulatte.requests import Request
from simulatte.stores import WarehouseStore
from simulatte.stores.warehouse_location import PhysicalPosition
from simulatte.system.policies import LocationPolicy, UnitLoadPolicy
from simulatte.unitload import Pallet, Tray

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte import System


class StoresManager:
    def __init__(self, *, unit_load_policy: UnitLoadPolicy, location_policy: LocationPolicy):
        self._stores: list[WarehouseStore] = []
        self._stores: dict[Type[WarehouseStore], list] = {}

        self._unit_load_policy = unit_load_policy
        self._location_policy = location_policy  # to be used to find locations for storing of products
        self._stock: dict[int, dict[str, dict[str, int]]] = {}
        self.system: System | None = None

        self._magic = {"pallet": [0, 0], "tray": [0, 0]}
        self._ip_history = []

    def __call__(self, store: WarehouseStore) -> None:
        """
        Register a store to be managed by the StoresManager.
        """
        self._stores.setdefault(type(store), []).append(store)

    def register_system(self, system: System) -> None:
        self.system = system

    @property
    def stores(self) -> dict[Type[WarehouseStore], list]:
        return self._stores

    @staticmethod
    def freeze(location: WarehouseLocation, unit_load: Pallet) -> None:
        location.freeze(unit_load=unit_load)

    def update_stock(
        self,
        *,
        product: Product,
        case_container: Literal["pallet", "tray"],
        inventory: Literal["on_hand", "on_transit"],
        n_cases: int,
    ) -> None:
        """
        Modify the stock (on hand and on transit) quantities of a product.
        """

        if product.id not in self._stock:
            self._stock[product.id] = {
                "pallet": {"on_hand": 0, "on_transit": 0},
                "tray": {"on_hand": 0, "on_transit": 0},
            }

        self._stock[product.id][case_container][inventory] += n_cases

        pallet_on_hand = 0
        pallet_on_transit = 0
        tray_on_hand = 0
        tray_on_transit = 0

        for product_id in [0, 1, 2, 3, 4]:
            try:
                pallet_on_hand += self._stock[product_id]["pallet"]["on_hand"]
                pallet_on_transit += self._stock[product_id]["pallet"]["on_transit"]
                tray_on_hand += self._stock[product_id]["tray"]["on_hand"]
                tray_on_transit += self._stock[product_id]["tray"]["on_transit"]
            except KeyError:
                pass
        self._ip_history.append(
            {
                "time": self.system.env.now,
                "pallet_on_hand": pallet_on_hand,
                "pallet_on_transit": pallet_on_transit,
                "tray_on_hand": tray_on_hand,
                "tray_on_transit": tray_on_transit,
            }
        )

    def inventory_position(self, *, product: Product, case_container: Literal["pallet", "tray"]) -> int:
        """
        Return the inventory position of a product, filtered in pallets of trays.
        """
        on_hand = self._stock[product.id][case_container]["on_hand"]
        on_transit = self._stock[product.id][case_container]["on_transit"]
        return on_hand + on_transit

    @simulatte.as_process
    def load(self, *, store: WarehouseStore, ant: Ant) -> None:
        """
        Used to centralize the loading of unitloads into the stores.
        Needed to keep trace of the on hand quantity of each product,
        to trigger replenishment when needed.

        This method must be called when the ant is in front of the store, waiting to be
        unloaded by the store.

        It triggers the loading process of the store.
        """

        from eagle_trays.asrs import ASRS
        from eagle_trays.avsrs import AVSRS

        # troviamo la locazione per il pallet/vassoio
        location = self.get_location_for_unit_load(store=store, unit_load=ant.unit_load)

        if isinstance(store, ASRS):
            case_container = cast(Literal, "pallet")
        elif isinstance(store, AVSRS):
            case_container = cast(Literal, "tray")
        else:
            raise ValueError(f"Case container {type(ant.unit_load)} not supported.")

        # riduciamo l'on_transit
        self.update_stock(
            product=ant.unit_load.product,
            case_container=case_container,
            inventory="on_transit",
            n_cases=-ant.unit_load.n_cases,
        )

        # alziamo l'on_hand
        self.update_stock(
            product=ant.unit_load.product,
            case_container=case_container,
            inventory="on_hand",
            n_cases=ant.unit_load.n_cases,
        )

        yield store.load(unit_load=ant.unit_load, location=location, ant=ant, priority=10)

    def unload(
        self, *, type_of_stores: Type[WarehouseStore], picking_request: Request
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, PhysicalPosition],...]:
        """
        Used to centralize the unloading of unitloads from the stores.
        Needed to keep trace of the on hand quantity of each product.
        It does not trigger replenishment operations.

        This method should be called when the system is organizing the feeding operation.
        It does NOT trigger the unloading process of the store.
        """

        from eagle_trays.asrs import ASRS
        from eagle_trays.avsrs import AVSRS

        if type_of_stores is ASRS:
            case_container = cast(Literal, "pallet")
        elif type_of_stores is AVSRS:
            case_container = cast(Literal, "tray")
        else:
            raise ValueError

        # Get location
        stores = self.stores[type_of_stores]
        stores_and_locations = self.find_stores_locations_for_output(
            stores=stores,
            product=picking_request.product,
            quantity=picking_request.n_cases,
            raise_on_none=False,
        )

        for (store, location, unit_load) in stores_and_locations:
            location.book_pickup(unit_load)

        n_cases_tot = sum(unit_load.n_cases for (_, _, unit_load) in stores_and_locations)

        # aumentiamo l'on_transit
        self.update_stock(
            product=picking_request.product,
            case_container=case_container,
            inventory="on_transit",
            n_cases=n_cases_tot - picking_request.n_cases,
        )

        # riduciamo l'on_hand
        self.update_stock(
            product=picking_request.product,
            case_container=case_container,
            inventory="on_hand",
            n_cases=-n_cases_tot,
        )

        # controlliamo necessità di replenishment
        self.check_replenishment(product=picking_request.product, case_container=case_container)

        return stores_and_locations

    def check_replenishment(
        self,
        *,
        product: Product,
        case_container: Literal["pallet", "tray"],
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

    @simulatte.as_process
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
        return self._location_policy(store=store, product=product)

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
    ) -> tuple[tuple[WarehouseStore, WarehouseLocation, Pallet],...]:
        """
        FOR OUTPUT.

        Get a tuple of stores and locations from which to pickup a product.
        """
        try:
            stores_and_locations = self._unit_load_policy(stores=stores, product=product, quantity=quantity)
            return stores_and_locations
        except OutOfStockError as e:
            if raise_on_none:
                raise e

    def warmup(
        self,
        *,
        products_generator: ProductsGenerator,
        locations: Literal["products", "random"],
    ):
        from eagle_trays.asrs import ASRS
        from eagle_trays.avsrs import AVSRS

        if locations == "products":
            for product in products_generator.products:
                for type_of_store, stores in self.stores.items():
                    if type_of_store is ASRS:
                        case_container = "pallet"
                    elif type_of_store is AVSRS:
                        case_container = "tray"
                    else:
                        raise ValueError

                    s_max = product.s_max[case_container]  # [cases]
                    n_pallet = math.ceil(s_max / product.case_per_pallet)

                    # if product.family in ("B", "C"):
                    #    n_pallet -= 1
                    n_pallet = max(1, n_pallet)

                    def iter_stores(product: Product):
                        i = 0
                        while True:
                            try:
                                store = stores[i]
                                n_available = sum(
                                    1
                                    for location in store.locations
                                    if location.is_empty or (location.is_half_full and location.product == product)
                                )
                                if n_available > 0:
                                    yield store
                                i += 1
                            except IndexError:
                                i = 0

                    if case_container == "pallet":
                        for _, store in zip(range(n_pallet), iter_stores(product)):
                            unit_load = Pallet.by_product(product=product)
                            location = store.first_available_location_for_warmup(unit_load=unit_load)
                            try:
                                store.book_location(location=location, unit_load=unit_load)
                            except:
                                raise
                            location.put(unit_load=unit_load)

                            # aumentiamo l'on_hand
                            self.update_stock(
                                product=product,
                                case_container=case_container,
                                inventory="on_hand",
                                n_cases=unit_load.n_cases,
                            )
                    else:
                        for _ in range(n_pallet):
                            for _, store in zip(range(product.layers_per_pallet), iter_stores(product)):
                                unit_load = Pallet(
                                    Tray(
                                        product=product,
                                        n_cases=product.cases_per_layer,
                                    )
                                )
                                location = store.first_available_location_for_warmup(unit_load=unit_load)
                                store.book_location(location=location, unit_load=unit_load)
                                location.put(unit_load=unit_load)

                                # aumentiamo l'on_hand
                                self.update_stock(
                                    product=product,
                                    case_container=case_container,
                                    inventory="on_hand",
                                    n_cases=unit_load.n_cases,
                                )
        else:
            raise ValueError(f"Unknown locations warmup policy {locations}")
