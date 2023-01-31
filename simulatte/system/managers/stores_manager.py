from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Literal, cast

import simulatte
from simulatte.ant import Ant
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
        self._unit_load_policy = unit_load_policy
        self._location_policy = location_policy  # to be used to find locations for storing of products

        # {
        #     product_id: {
        #         'pallet': {
        #             'on_transit': 0,
        #             'on_hand': 0
        #         },
        #         'vassoi': {
        #             'on_transit': 0,
        #             'on_hand': 0
        #         },
        #     }
        self._stock: dict[int, dict[str, dict[str, int]]] = {}
        self.system: System | None = None

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

    def register_system(self, system: System) -> None:
        self.system = system

    @property
    def stores(self) -> list[WarehouseStore]:
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

    def inventory_position(self, *, product: Product, case_container: Literal["pallet", "tray"]) -> int:
        """
        Return the inventory position of a product.
        """
        return (
            self._stock[product.id][case_container]["on_hand"] + self._stock[product.id][case_container]["on_transit"]
        )

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

        # troviamo la locazione per il pallet/vassoio
        location = self.get_location_for_unit_load(store=store, unit_load=ant.unit_load)

        if isinstance(ant.unit_load, Pallet):
            case_container = cast(Literal, "pallet")
        elif isinstance(ant.unit_load, Tray):
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

    def unload(self, *, store: WarehouseStore, picking_request: Request) -> tuple[WarehouseLocation, PhysicalPosition]:
        """
        Used to centralize the unloading of unitloads from the stores.
        Needed to keep trace of the on hand quantity of each product.
        It does not trigger replenishment operations.

        This method should be called when the system is organizing the feeding operation.
        It does NOT trigger the unloading process of the store.
        """
        if store is self.asrs:
            case_container = cast(Literal, "pallet")
        elif store is self.avsrs:
            case_container = cast(Literal, "tray")
        else:
            raise ValueError

        # Get location
        location, position = self.get_unit_load(
            store=store,
            product=picking_request.product,
            quantity=picking_request.n_cases,
            raise_on_none=False,
        )
        if location is None:

            # se non troviamo nulla, magicamente facciamo apparire materiale

            if store is self.asrs:
                print(
                    f"[{self.system.env.now}] MAGIA ASRS {picking_request.product} {picking_request.product.family} {picking_request.n_cases}"
                )
                unit_load = Pallet.by_product(product=picking_request.product)
            else:
                print(
                    f"[{self.system.env.now}] MAGIA AVSRS {picking_request.product} {picking_request.product.family} {picking_request.n_cases}"
                )
                unit_load = Pallet(
                    Tray(
                        product=picking_request.product,
                        n_cases=picking_request.product.cases_per_layer,
                    )
                )

            location = store.first_available_location()
            store.book_location(location=location, unit_load=unit_load)
            location.put(unit_load=unit_load)

            assert location.second_position.busy
            position = location.second_position

            self.update_stock(
                product=picking_request.product,
                case_container=case_container,
                inventory="on_hand",
                n_cases=position.unit_load.n_cases,
            )

        location.book_pickup(position.unit_load)

        # aumentiamo l'on_transit
        self.update_stock(
            product=picking_request.product,
            case_container=case_container,
            inventory="on_transit",
            n_cases=position.unit_load.n_cases - picking_request.n_cases,
        )

        # riduciamo l'on_hand
        self.update_stock(
            product=picking_request.product,
            case_container=case_container,
            inventory="on_hand",
            n_cases=-position.unit_load.n_cases,
        )

        # controlliamo necessità di replenishment
        self.check_replenishment(product=picking_request.product, case_container=case_container)

        return location, position

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

            case_per_pallet = (
                product.cases_per_layer * product.layers_per_pallet
            )  # TODO: spostare come property della classe Product

            n_pallet = math.ceil(n_cases / case_per_pallet)

            # aumentiamo l'on_transit
            self.update_stock(
                product=product,
                case_container=case_container,
                inventory="on_transit",
                n_cases=n_pallet * case_per_pallet,
            )

            if case_container == "pallet":
                store = self.asrs
            elif case_container == "tray":
                store = self.avsrs
            else:
                raise ValueError(f"Case container {case_container} not supported.")

            for _ in range(n_pallet):
                self.system.store_replenishment(
                    product=product,
                    store=store,
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
        try:
            store.book_location(location=location, unit_load=unit_load)
        except:
            print("Location already booked")
            raise
        return location

    def get_unit_load(
        self,
        *,
        store: WarehouseStore,
        product: Product,
        quantity: int,
        raise_on_none: bool = False,
    ) -> tuple[WarehouseLocation, PhysicalPosition] | tuple[None, None]:
        """
        FOR OUTPUT.

        Get a unit load from a store.
        Get the unit load accordingly to the UnitLoadPolicy set.
        """
        location, position = self._unit_load_policy(store=store, product=product, quantity=quantity)
        if location is None and raise_on_none:
            raise ValueError(f"Location not found for product {product}.")
        return location, position

    def warmup(
        self,
        *,
        store: WarehouseStore,
        products_generator: ProductsGenerator,
        filling: float | None = 0.5,
        locations: Literal["products", "random"],
        products: Literal["linear", "random"] | None,
    ):
        if locations == "products":
            for product in products_generator.products:
                if store is self.asrs:
                    case_container = "pallet"
                elif store is self.avsrs:
                    case_container = "tray"
                else:
                    raise ValueError

                s_max = product.s_max[case_container]  # [cases]
                n_pallet = math.ceil(s_max / product.case_per_pallet)

                if product.family in ("A", "B", "C"):
                    n_pallet -= 1
                n_pallet = max(1, n_pallet)

                for _ in range(n_pallet):
                    if case_container == "pallet":
                        unit_load = Pallet.by_product(product=product)
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
                        for _ in range(product.layers_per_pallet):
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

        elif locations == "random":
            i = 0
            for location in store.locations:
                if random.random() < filling and i < len(products_generator.products):
                    if products == "random":
                        product = products_generator.choose_one()
                    elif products == "linear":
                        product = products_generator.products[i]
                        i += 1
                    else:
                        raise ValueError(f"Unknown products warmup policy: {products}")

                    for _ in range(location.depth):
                        unit_load = Pallet(
                            Tray(
                                product=product,
                                n_cases=product.cases_per_layer,
                            )
                        )
                        location.freeze(unit_load=unit_load)
                        location.put(unit_load=unit_load)
        else:
            raise ValueError(f"Unknown locations warmup policy {locations}")
