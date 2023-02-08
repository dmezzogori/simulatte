from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Literal, cast, Type

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


def location_sorter(location: WarehouseLocation):
    try:
        return min(location.first_position.unit_load.n_cases, location.second_position.unit_load.n_cases)
    except AttributeError:
        return location.n_cases


def defrag(stores: list[WarehouseStore], product: Product, quantity: int):

    store_locations = []
    for store in stores:
        n_cases = 0
        locations = sorted(
            (
                location
                for location in store.locations
                if location.physically_available_product == product and len(location.booked_pickups) == 0
            ),
            key=location_sorter,
        )
        for i in range(len(locations)):
            location = locations[i]
            n_cases += location_sorter(location)
            if n_cases >= quantity:
                return (store, locations[: i + 1], n_cases), True

        store_locations.append((store, locations, n_cases))

    return sorted(store_locations, key=lambda x: x[2], reverse=True)[0], False


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

        for product_id in [0]:
            pallet_on_hand += self._stock[product_id]["pallet"]["on_hand"]
            pallet_on_transit += self._stock[product_id]["pallet"]["on_transit"]
            tray_on_hand += self._stock[product_id]["tray"]["on_hand"]
            tray_on_transit += self._stock[product_id]["tray"]["on_transit"]

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

        if ant.unit_load.n_cases == 0 or hasattr(ant.unit_load, "magic"):
            raise ValueError(f"Unit load {ant.unit_load} has no cases.")

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
    ) -> tuple[WarehouseStore, WarehouseLocation, PhysicalPosition]:
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
        store, location, position = self.get_unit_load(
            stores=stores,
            product=picking_request.product,
            quantity=picking_request.n_cases,
            raise_on_none=False,
        )
        if location is None:

            # se non troviamo un singolo pallet/vassoio utile

            if type_of_stores is ASRS:
                # cerchiamo il pallet più scarico
                store, location, position = self.get_unit_load(
                    stores=stores,
                    product=picking_request.product,
                    quantity=0,
                    raise_on_none=False,
                )
                print(
                    f"[{self.system.env.now}] MAGIA ASRS {picking_request.product} {picking_request.product.family} {picking_request.n_cases}"
                )

                if location is None:
                    unit_load = Pallet.by_product(product=picking_request.product)
                    delta_n_cases = unit_load.n_cases

                    # prendiamo il magazzino con più spazio libero
                    store = max(stores, key=lambda s: sum(location.is_empty for location in s.locations))
                    location = store.first_available_location()
                    store.book_location(location=location, unit_load=unit_load)
                    location.put(unit_load=unit_load)
                    position = location.second_position
                    self._magic["pallet"][0] += 1
                else:
                    # carichiamo il numero di case che ci serve
                    new_pallet = Pallet.by_product(product=picking_request.product)
                    delta_n_cases = new_pallet.n_cases - position.unit_load.n_cases

                    # svuotiamo il pallet
                    for _ in range(position.unit_load.n_layers):
                        position.unit_load.remove_layer()

                    # riempiamo il pallet
                    for _ in new_pallet.layers:
                        position.unit_load.layers.append(
                            Tray(product=picking_request.product, n_cases=picking_request.n_cases)
                        )
                    self._magic["pallet"][1] += 1

                position.unit_load.magic = True
                self.update_stock(
                    product=picking_request.product,
                    case_container="pallet",
                    inventory="on_hand",
                    n_cases=delta_n_cases,
                )
            else:
                print(
                    f"[{self.system.env.now}] MAGIA AVSRS {picking_request.product} {picking_request.product.family} {picking_request.n_cases}"
                )

                (store, locations, n_cases_tot), enough = defrag(
                    stores, product=picking_request.product, quantity=picking_request.n_cases
                )

                delta = picking_request.n_cases - n_cases_tot

                if locations:
                    self._magic["tray"][1] += 1
                    location = locations[0]
                else:
                    self._magic["tray"][0] += 1
                    location = store.first_available_location()

                if enough:
                    n_cases = n_cases_tot
                else:
                    n_cases = n_cases_tot + delta
                    self.update_stock(
                        product=picking_request.product,
                        case_container="tray",
                        inventory="on_hand",
                        n_cases=delta,
                    )

                new_tray = Pallet(Tray(product=picking_request.product, n_cases=n_cases, exceed=True))
                if not enough:
                    new_tray.magic = True

                for loc in locations:
                    n = location_sorter(loc)
                    if loc.second_position.n_cases == n:
                        position = loc.second_position
                    else:
                        position = loc.first_position
                    position.unit_load = None

                    if loc.first_position.unit_load is not None:
                        loc.second_position.unit_load, loc.first_position.unit_load = (
                            loc.first_position.unit_load,
                            loc.second_position.unit_load,
                        )

                location.future_unit_loads.append(new_tray)
                location.put(unit_load=new_tray)
                position = (
                    location.first_position
                    if location.first_position.unit_load is not None
                    else location.second_position
                )

                # if location is None:
                #     unit_load = Pallet(
                #         Tray(
                #             product=picking_request.product,
                #             n_cases=picking_request.product.cases_per_layer,
                #         )
                #     )
                #     delta_n_cases = unit_load.n_cases
                #
                #     # prendiamo il magazzino con più spazio libero
                #     store = max(stores, key=lambda s: sum(location.is_empty for location in s.locations))
                #     location = store.first_available_location()
                #     store.book_location(location=location, unit_load=unit_load)
                #     location.put(unit_load=unit_load)
                #     position = location.second_position
                #     self._magic["tray"][0] += 1
                # else:
                #     # carichiamo il numero di case che ci serve
                #     new_tray = Pallet(
                #         Tray(
                #             product=picking_request.product,
                #             n_cases=picking_request.product.cases_per_layer,
                #         )
                #     )
                #     delta_n_cases = new_tray.n_cases - position.unit_load.n_cases
                #
                #     # svuotiamo il tray
                #     position.unit_load.remove_layer()
                #
                #     # riempiamo il tray
                #     position.unit_load.layers.append(
                #         Tray(product=picking_request.product, n_cases=picking_request.n_cases)
                #     )
                #     self._magic["tray"][1] += 1

        location.book_pickup(position.unit_load)

        if not hasattr(position.unit_load, "magic"):
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

        return store, location, position

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

    def get_unit_load(
        self,
        *,
        stores: list[WarehouseStore],
        product: Product,
        quantity: int,
        raise_on_none: bool = False,
    ) -> tuple[WarehouseStore, WarehouseLocation, PhysicalPosition] | tuple[None, None, None]:
        """
        FOR OUTPUT.

        Get a unit load from a store.
        Get the unit load accordingly to the UnitLoadPolicy set.
        """
        store, location, position = self._unit_load_policy(stores=stores, product=product, quantity=quantity)
        if location is None and raise_on_none:
            raise ValueError(f"Location not found for product {product}.")
        return store, location, position

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

                    def iter_stores():
                        i = 0
                        while True:
                            try:
                                yield stores[i]
                                i += 1
                            except IndexError:
                                i = 0

                    if case_container == "pallet":
                        for _, store in zip(range(n_pallet), iter_stores()):
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
                        for _ in range(n_pallet):
                            for _, store in zip(range(product.layers_per_pallet), iter_stores()):
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
